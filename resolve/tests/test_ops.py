# SPDX-License-Identifier: GPL-3.0-or-later
"""What each command does in Resolve, against the fake Resolve objects."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import support  # noqa: F401
import fake_resolve as fake

from nolgia_resolve import ops as ops_module
from nolgia_resolve import still
from nolgia_resolve.core.commands import Command, CommandError, validate


def command(kind, args=None, timeout=120, caller="user"):
    cmd = Command({"id": "c-" + kind, "kind": kind, "args": args or {}, "timeout_seconds": timeout,
                   "caller": caller})
    cmd.args = validate(kind, cmd.args)
    return cmd


class Base(unittest.TestCase):
    def setUp(self):
        self.resolve = fake.sample()
        self.project = self.resolve.pm.current
        self.tl = self.project.current
        self.ops = ops_module.Ops(self.resolve)
        self.tmp = tempfile.mkdtemp(prefix="nolgia-test-")
        self.env = mock.patch.dict(os.environ, {
            "NOLGIA_LUT_DIR": os.path.join(self.tmp, "LUT"),
            "NOLGIA_IMPORT_DIR": os.path.join(self.tmp, "imports"),
        })
        self.env.start()
        fake.PyRemoteObject.calls = []

    def tearDown(self):
        fake.PyRemoteObject.calls = None
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def do(self, kind, args=None, prepared=None, **kwargs):
        cmd = command(kind, args, **kwargs)
        return getattr(self.ops, "do_" + kind)(cmd.args, prepared, cmd)

    def finisher(self, uploads=None):
        uploads = [] if uploads is None else uploads

        class Api:
            def upload_file(self, path, content_type, display_name=None, tags=None, filename=None):
                with open(path, "rb") as handle:
                    uploads.append({"data": handle.read(), "content_type": content_type,
                                    "filename": filename, "display_name": display_name, "tags": tags})
                return {"id": "asset-%d" % len(uploads)}

        return ops_module.Finisher(self.ops, Api(), on_main=lambda fn: fn(), sleep=lambda s: None), uploads


class Info(Base):
    def test_info(self):
        self.tl.playhead = self.tl.start + 50
        self.tl.selected = [self.tl.tracks["video"][0][1]]
        res = self.do("info")
        self.assertEqual(res["app"], "resolve")
        self.assertEqual(res["app_version"], "21.1.1.7")
        self.assertTrue(res["studio"])
        self.assertEqual(res["page"], "edit")
        self.assertEqual(res["document"], {"name": "Rooftop Story"})
        self.assertEqual(res["project"]["name"], "Rooftop Story")
        self.assertTrue(res["project"]["in_library"])
        self.assertIsNone(res["project"]["unsaved_changes"])
        self.assertIs(res["project"]["changed_by_nolgia_since_save"], False)
        tl = res["timeline"]
        self.assertEqual((tl["name"], tl["fps"], tl["width"], tl["height"]), ("Edit 1", 24.0, 1920, 1080))
        self.assertEqual((tl["start_timecode"], tl["start_frame"], tl["duration_frames"]), ("01:00:00:00", 86400, 96))
        self.assertEqual((tl["current_timecode"], tl["current_frame"]), ("01:00:02:02", 50))
        self.assertEqual(res["current_timecode"], "01:00:02:02")
        self.assertEqual(tl["tracks"]["video"][0]["items"], 2)
        self.assertEqual(tl["tracks"]["audio"][0]["items"], 1)
        self.assertEqual(tl["selected_clips"], [{"name": "Shot 2", "track": "V1", "start_frame": 48, "end_frame": 96}])
        self.assertEqual(tl["clip_under_playhead"]["name"], "Shot 2")
        self.assertEqual(res["timelines"], [{"name": "Edit 1", "current": True}])
        self.assertEqual(res["media_pool"]["bins"], [{"name": "Footage", "clips": 1, "bins": 0}])
        self.assertIn("H.264 Master", res["render"]["presets"])
        self.assertIs(res["render"]["rendering"], False)

    def test_info_without_project_or_timeline(self):
        self.project.current = None
        res = self.do("info")
        self.assertIsNone(res["timeline"])
        self.resolve.pm.current = None
        res = self.do("info")
        self.assertIsNone(res["project"])
        self.assertEqual(res["document"], {"name": ""})


class Run(Base):
    def test_namespace_and_result(self):
        code = ("print('hi')\n"
                "result = {'project': project, 'timeline': timeline, 'pool': media_pool is not None,\n"
                "          'version': resolve.GetVersionString(), 'pm': project_manager is not None,\n"
                "          'clips': timeline.GetItemListInTrack('video', 1)}")
        res = self.do("run", {"code": code})
        self.assertEqual(res["value"], {"project": "Rooftop Story", "timeline": "Edit 1", "pool": True,
                                        "version": "21.1.1.7", "pm": True, "clips": ["Shot 1", "Shot 2"]})
        self.assertEqual(res["stdout"], "hi\n")
        self.assertTrue(self.ops.changed)

    def test_failure_returns_the_traceback(self):
        with self.assertRaises(CommandError) as ctx:
            self.do("run", {"code": "x = 1\nraise ValueError('boom')\n"})
        self.assertIn("ValueError: boom", str(ctx.exception))
        self.assertIn("line 2", str(ctx.exception))
        self.assertEqual(ctx.exception.result, {"value": None, "stdout": "", "stderr": ""})

    def test_approval_only_when_asked(self):
        cmd = command("run", {"code": "x = 1\ny = 2"}, caller="agent")
        self.assertIsNone(self.ops.run_approval(cmd, False))
        req = self.ops.run_approval(cmd, True)
        self.assertEqual(req.title, "Your NOLGIA Agent wants to run Python in DaVinci Resolve")
        self.assertEqual(req.lines, ["x = 1", "y = 2"])
        self.assertEqual(req.approve_label, "Run code")


class Preview(Base):
    def test_scales_the_frame_and_puts_the_playhead_back(self):
        self.tl.playhead = self.tl.start + 10
        res = self.do("preview", {"frame": 60, "width": 640})
        self.assertEqual((res["width"], res["height"], res["frame"], res["timecode"]), (640, 360, 60, "01:00:02:12"))
        self.assertEqual(self.tl.playhead, self.tl.start + 10)
        finisher, uploads = self.finisher()
        out = finisher(command("preview"), res)
        self.assertEqual(out["asset_id"], "asset-1")
        self.assertEqual(out["mime_type"], "image/png")
        self.assertNotIn("_still", out)
        up = uploads[0]
        self.assertEqual(still.png_size(up["data"]), (640, 360))
        self.assertEqual(up["filename"], "Edit 1-preview-0060.png")
        self.assertEqual(up["tags"], ["resolve"])
        self.assertFalse(os.path.exists(res["_still"]["folder"]), "temp files left behind")

    def test_playhead_set_again_when_resolve_answers_true_without_moving(self):
        self.tl.ignore_sets = 1
        with mock.patch("time.sleep"):
            res = self.do("preview", {"frame": 60, "width": 320})
        self.assertEqual(res["frame"], 60)
        sets = [c for c in fake.PyRemoteObject.calls if c[1] == "SetCurrentTimecode"]
        self.assertEqual([c[2] for c in sets][:2], ["01:00:02:12", "01:00:02:12"], sets)
        self.assertEqual(self.tl.playhead, self.tl.start, "playhead not put back")
        shutil.rmtree(res["_still"]["folder"])

    def test_default_width_and_timecode(self):
        res = self.do("preview", {"timecode": "01:00:01:00"})
        self.assertEqual((res["width"], res["frame"]), (1280, 24))
        shutil.rmtree(res["_still"]["folder"])

    def test_never_larger_than_the_timeline(self):
        self.tl.width, self.tl.height = 640, 480
        res = self.do("preview", {"width": 1920})
        self.assertEqual((res["width"], res["height"]), (640, 480))
        shutil.rmtree(res["_still"]["folder"])

    def test_named_timeline_switches_back(self):
        other = fake.Timeline("Alt")
        other.add(fake.TimelineItem("X", other.start, other.start + 10))
        self.project.timelines.append(other)
        res = self.do("preview", {"camera": "Alt", "frame": 3})
        self.assertEqual(res["timeline"], "Alt")
        self.assertIs(self.project.current, self.tl)
        shutil.rmtree(res["_still"]["folder"])
        with self.assertRaisesRegex(CommandError, "no timeline named Nope"):
            self.do("preview", {"timeline": "Nope"})

    def test_frame_outside_the_timeline(self):
        with self.assertRaisesRegex(CommandError, "outside the timeline, which has 96 frames"):
            self.do("preview", {"frame": 96})

    def test_ppm_when_bmp_is_not_offered(self):
        self.project.still_formats = {".ppm", ".png", ".jpg"}
        res = self.do("preview", {"width": 320})
        self.assertTrue(res["_still"]["path"].endswith(".ppm"))
        finisher, uploads = self.finisher()
        finisher(command("preview"), res)
        self.assertEqual(still.png_size(uploads[0]["data"]), (320, 180))

    def test_png_as_is_when_nothing_scalable(self):
        self.project.still_formats = {".png", ".jpg"}
        res = self.do("preview", {"width": 320})
        finisher, uploads = self.finisher()
        out = finisher(command("preview"), res)
        self.assertEqual((out["width"], out["height"]), (1920, 1080))
        self.assertEqual(still.png_size(uploads[0]["data"]), (1920, 1080))

    def test_too_big_uses_resolves_jpeg_at_the_same_size(self):
        self.project.still_noise = True
        self.tl.width, self.tl.height = 320, 180
        res = self.do("preview", {"width": 320})
        finisher, uploads = self.finisher()
        with mock.patch.dict(os.environ, {"NOLGIA_PREVIEW_MAX_BYTES": "100000"}):
            out = finisher(command("preview"), res)
        self.assertEqual(out["mime_type"], "image/jpeg")
        self.assertEqual(uploads[0]["content_type"], "image/jpeg")
        self.assertTrue(uploads[0]["filename"].endswith(".jpg"))

    def test_too_big_at_another_size_keeps_png_with_fewer_levels(self):
        self.project.still_noise = True
        self.tl.width, self.tl.height = 640, 360
        res = self.do("preview", {"width": 320})
        finisher, uploads = self.finisher()
        with mock.patch.dict(os.environ, {"NOLGIA_PREVIEW_MAX_BYTES": "150000"}):
            out = finisher(command("preview"), res)
        self.assertEqual(out["mime_type"], "image/png")
        self.assertEqual(still.png_size(uploads[0]["data"]), (320, 180))
        self.assertLessEqual(len(uploads[0]["data"]), 150000)

    def test_empty_timeline(self):
        empty = fake.Timeline("Empty")
        self.project.timelines.append(empty)
        self.project.current = empty
        with self.assertRaisesRegex(CommandError, "is empty"):
            self.do("preview")


class Export(Base):
    def test_png_at_timeline_size(self):
        res = self.do("export", {"format": "png", "frames": "12", "filename": "shot.png"})
        finisher, uploads = self.finisher()
        out = finisher(command("export", {"format": "mp4"}), res)
        self.assertEqual(out["filename"], "shot-0012.png")
        self.assertEqual((out["width"], out["height"], out["frames"]), (1920, 1080, [12, 12]))
        self.assertEqual(still.png_size(uploads[0]["data"]), (1920, 1080))
        self.assertEqual(self.tl.playhead, self.tl.start)

    def test_mp4_renders_and_restores_settings(self):
        before = (dict(self.project.render_settings), dict(self.project.format_codec), list(self.project.render_presets))
        res = self.do("export", {"format": "mp4", "frames": "10-20"})
        self.assertEqual(res["frames"], [10, 20])
        sets = [c for c in fake.PyRemoteObject.calls if c[1] == "SetRenderSettings"][0][2]
        self.assertEqual((sets["MarkIn"], sets["MarkOut"], sets["SelectAllFrames"]), (86410, 86420, False))
        self.assertEqual((sets["FormatWidth"], sets["FormatHeight"], sets["CustomName"]), (1920, 1080, "Rooftop Story"))
        self.assertTrue(sets["ExportAudio"])
        self.assertEqual(self.project.format_codec, {"format": "mp4", "codec": "H264"})
        finisher, uploads = self.finisher()
        out = finisher(command("export", {"format": "mp4"}), res)
        self.assertEqual(out["filename"], "Rooftop Story.mp4")
        self.assertEqual(uploads[0]["content_type"], "video/mp4")
        self.assertEqual(uploads[0]["data"][4:8], b"ftyp")
        after = (dict(self.project.render_settings), dict(self.project.format_codec), list(self.project.render_presets))
        self.assertEqual(after, before, "render settings, format or presets not put back")
        self.assertEqual(self.resolve.page, "edit", "the page rendering opened was not put back")
        self.assertEqual(self.project.jobs, {}, "render job left in the queue")

    def test_mp4_puts_the_playhead_back(self):
        self.tl.playhead = self.tl.start + 30
        res = self.do("export", {"format": "mp4"})
        finisher, _ = self.finisher()
        finisher(command("export", {"format": "mp4"}), res)
        self.assertEqual(self.tl.playhead, self.tl.start + 30)

    def test_fresh_session_shows_the_color_page_once(self):
        # Resolve 21.1.1 exports no still until the Color page has been shown.
        res = self.do("preview", {"frame": 10, "width": 320})
        pages = [c[2] for c in fake.PyRemoteObject.calls if c[1] == "OpenPage"]
        self.assertEqual(pages, ["color", "edit"])
        self.assertEqual(self.resolve.page, "edit")
        shutil.rmtree(res["_still"]["folder"])
        res = self.do("preview", {"frame": 20, "width": 320})  # second time: no hop
        self.assertEqual([c[2] for c in fake.PyRemoteObject.calls if c[1] == "OpenPage"], ["color", "edit"])
        shutil.rmtree(res["_still"]["folder"])

    def test_color_page_already_shown_means_no_hop(self):
        self.resolve.color_shown = True
        res = self.do("preview", {"frame": 10, "width": 320})
        self.assertEqual([c for c in fake.PyRemoteObject.calls if c[1] == "OpenPage"], [])
        shutil.rmtree(res["_still"]["folder"])

    def test_media_page_has_no_playhead(self):
        self.resolve.color_shown = True
        self.resolve.page = "media"
        res = self.do("preview", {"frame": 10})
        self.assertEqual(self.resolve.page, "media", "page not put back")
        self.assertEqual([c[2] for c in fake.PyRemoteObject.calls if c[1] == "OpenPage"], ["edit", "media"])
        shutil.rmtree(res["_still"]["folder"])
        self.tl.playhead = self.tl.start + 60
        cube = Luts.cube(self)
        out = self.do("import_asset", {"asset_id": "a", "apply_to": "current"}, cube)
        self.assertEqual([i["name"] for i in out["applied_to"]], ["Shot 2"])
        self.assertEqual(self.resolve.page, "media")

    def test_mp4_whole_timeline(self):
        res = self.do("export", {"format": "mp4"})
        self.assertEqual(res["frames"], [0, 95])
        sets = [c for c in fake.PyRemoteObject.calls if c[1] == "SetRenderSettings"][0][2]
        self.assertTrue(sets["SelectAllFrames"])
        finisher, _ = self.finisher()
        finisher(command("export", {"format": "mp4"}), res)

    def test_failed_render(self):
        self.project.fail_render = True
        res = self.do("export", {"format": "mp4"})
        finisher, uploads = self.finisher()
        with self.assertRaisesRegex(CommandError, "did not finish the render \\(Disk full\\)"):
            finisher(command("export", {"format": "mp4"}), res)
        self.assertEqual(uploads, [])
        self.assertFalse(os.path.exists(res["_render"]["folder"]))
        self.assertEqual(self.project.jobs, {})

    def test_render_out_of_time_is_stopped(self):
        self.project.render_polls = 10 ** 9
        res = self.do("export", {"format": "mp4"})
        finisher, _ = self.finisher()
        cmd = command("export", {"format": "mp4"}, timeout=5)
        with self.assertRaisesRegex(CommandError, "took longer"):
            finisher(cmd, res)
        self.assertIsNone(self.project.rendering)
        self.assertEqual(self.project.jobs, {})

    def test_closing_mid_render_stops_and_tidies(self):
        before = dict(self.project.format_codec)
        res = self.do("export", {"format": "mp4"})
        self.assertIsNotNone(self.ops.active_render)
        self.ops.abandon_render()
        self.assertIsNone(self.project.rendering)
        self.assertEqual(self.project.jobs, {})
        self.assertEqual(self.project.format_codec, before)
        self.assertIsNone(self.ops.active_render)
        shutil.rmtree(res["_render"]["folder"], ignore_errors=True)

    def test_already_rendering(self):
        self.project.rendering = "someone-else"
        with self.assertRaisesRegex(CommandError, "already rendering"):
            self.do("export", {"format": "mp4"})

    def test_range_past_the_end(self):
        with self.assertRaisesRegex(CommandError, "past the end of the timeline"):
            self.do("export", {"format": "mp4", "frames": "90-100"})


class SaveOpen(Base):
    def test_save(self):
        self.ops.changed = True
        self.assertEqual(self.do("save"), {"project": "Rooftop Story", "saved": True})
        self.assertEqual(self.resolve.pm.saves, 1)
        self.assertFalse(self.ops.changed)
        with self.assertRaisesRegex(CommandError, "takes no `path`"):
            validate("save", {"path": "C:/x.drp"})

    def test_open(self):
        self.resolve.pm.add("Other")
        res = self.do("open", {"project": "Other"})
        self.assertEqual(res["project"], "Other")
        self.assertEqual(self.resolve.pm.current.name, "Other")
        self.assertEqual(self.resolve.pm.dialogs, 0)

    def test_save_refuses_a_project_that_was_never_saved(self):
        pm = self.resolve.pm
        pm.CloseProject(self.project)  # Resolve shows an unsaved Untitled Project
        self.assertFalse(self.do("info")["project"]["in_library"])
        with self.assertRaisesRegex(CommandError, "never been saved.*File > Save Project"):
            self.do("save")
        self.assertEqual(pm.dialogs, 0, "the Save dialog would have opened")
        self.assertEqual(pm.saves, 0)
        self.assertTrue(self.do("info")["project"]["in_library"] is False)

    def test_open_over_an_empty_untitled_project_closes_it_first(self):
        pm = self.resolve.pm
        pm.CloseProject(self.project)
        cmd = command("open", {"project": "Rooftop Story"})
        self.assertIsNone(ops_module.Ops(self.resolve, can_ask=lambda: False).open_approval(cmd))
        res = self.do("open", {"project": "Rooftop Story"})
        self.assertEqual(res["project"], "Rooftop Story")
        self.assertEqual(pm.dialogs, 0)

    def test_open_over_an_untitled_project_with_content_is_refused(self):
        pm = self.resolve.pm
        pm.CloseProject(self.project)
        pm.current.CreateEmptyTimeline("Scratch")
        cmd = command("open", {"project": "Rooftop Story"})
        for can_ask in (False, True):
            ops = ops_module.Ops(self.resolve, can_ask=lambda: can_ask)
            self.assertIsNone(ops.open_approval(cmd), "nothing to ask: it is refused outright")
            with self.assertRaisesRegex(CommandError, "never been saved and is not empty.*File > Save Project"):
                ops.do_open(cmd.args, None, cmd)
        self.assertEqual(pm.dialogs, 0)
        self.assertEqual(pm.current.name, "Untitled Project", "the plugin must not have called CloseProject on it")

    def test_project_manager_state_is_explained(self):
        # At startup Resolve shows only its Project Manager: no page, and media calls answer None.
        self.resolve.page = None
        for kind, args, prepared in (("preview", {}, None), ("export", {"format": "png"}, None),
                                     ("import_asset", {"asset_id": "a"},
                                      {"items": [{"kind": "video", "path": __file__, "asset_id": "a"}]})):
            with self.assertRaisesRegex(CommandError, "Project Manager"):
                self.do(kind, args, prepared)
        self.assertIsNone(self.do("info")["page"])


class ImportMedia(Base):
    def media(self, name="clip.mov"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(b"data")
        return path

    def test_imports_into_the_nolgia_bin(self):
        path = self.media()
        res = self.do("import_asset", {"asset_id": "a1"}, {"items": [{"kind": "video", "path": path, "asset_id": "a1"}]})
        self.assertEqual(res, {"imported": ["clip.mov"], "asset_ids": ["a1"], "kind": "video", "bin": "NOLGIA imports",
                               "path": path, "assets": [{"asset_id": "a1", "name": "clip.mov", "kind": "video",
                                                         "path": path}]})
        bins = [f.name for f in self.project.pool.root.folders]
        self.assertIn("NOLGIA imports", bins)
        self.assertIs(self.project.pool.current, self.project.pool.root, "current bin not put back")
        self.assertTrue(self.ops.changed)
        again = self.do("import_asset", {"asset_id": "a1"}, {"kind": "video", "path": path, "asset_id": "a1"})
        self.assertTrue(again["already_in_bin"])
        self.assertTrue(again["assets"][0]["already_in_bin"])
        self.assertEqual(bins.count("NOLGIA imports"), 1)

    def test_import_puts_the_pool_back_on_the_root_when_no_folder_was_current(self):
        # Resolve 21.1.1 answers None for GetCurrentFolder right after CreateProject.
        self.project.pool.current = None
        self.do("import_asset", {"asset_id": "a1"}, {"kind": "video", "path": self.media(), "asset_id": "a1"})
        self.assertIs(self.project.pool.current, self.project.pool.root)

    def test_append_to_the_current_timeline(self):
        path = self.media("still.png")
        res = self.do("import_asset", {"asset_id": "a1", "append": True}, {"kind": "image", "path": path})
        self.assertEqual(res["appended"]["timeline"], "Edit 1")
        self.assertEqual(res["appended"]["items"], [{"name": "still.png", "track": "V1", "start_frame": 96,
                                                     "end_frame": 216}])

    def test_append_makes_a_timeline_when_none_is_open(self):
        self.project.current = None
        self.project.timelines.append(fake.Timeline("NOLGIA timeline"))
        path = self.media("tone.wav")
        res = self.do("import_asset", {"asset_id": "a1", "append": True, "bin": "Sound"}, {"kind": "audio", "path": path})
        self.assertEqual(res["bin"], "Sound")
        self.assertEqual(res["appended"]["timeline"], "NOLGIA timeline 2")
        self.assertTrue(res["appended"]["new_timeline"])
        self.assertEqual(self.project.current.name, "NOLGIA timeline 2")
        self.assertEqual(res["appended"]["items"][0]["track"], "A1")

    def test_apply_to_is_for_luts(self):
        with self.assertRaisesRegex(CommandError, "for a single LUT"):
            self.do("import_asset", {"asset_id": "a1", "apply_to": "all"}, {"kind": "video", "path": self.media()})

    def test_several_in_order_then_appended_in_order(self):
        paths = [self.media(n) for n in ("c.mov", "a.png", "b.wav", "d.mov")]
        kinds = ["video", "image", "audio", "video"]
        prepared = {"items": [{"kind": k, "path": p, "asset_id": "id-%d" % n}
                              for n, (k, p) in enumerate(zip(kinds, paths))]}
        res = self.do("import_asset", {"asset_ids": ["id-0", "id-1", "id-2", "id-3"], "append": True,
                                       "bin": "Cut"}, prepared)
        self.assertEqual(res["imported"], ["c.mov", "a.png", "b.wav", "d.mov"])
        self.assertEqual(res["asset_ids"], ["id-0", "id-1", "id-2", "id-3"])
        self.assertEqual(res["kind"], "mixed")
        self.assertEqual(res["bin"], "Cut")
        self.assertNotIn("path", res)
        bin_ = [f for f in self.project.pool.root.folders if f.name == "Cut"][0]
        self.assertEqual([c.name for c in bin_.clips], ["c.mov", "a.png", "b.wav", "d.mov"])
        items = res["appended"]["items"]
        self.assertEqual([i["name"] for i in items], ["c.mov", "a.png", "b.wav", "d.mov"])
        # Resolve 21.1.1 appends each clip after the timeline's last clip, whatever its track.
        self.assertEqual([(i["start_frame"], i["end_frame"]) for i in items],
                         [(96, 144), (144, 264), (264, 312), (312, 360)])
        self.assertEqual([i["track"] for i in items], ["V1", "V1", "A1", "V1"])

    def test_skipped_and_luts_in_a_list(self):
        cube_dir = tempfile.mkdtemp(dir=self.tmp)
        cube = os.path.join(cube_dir, "download")
        with open(cube, "wb") as handle:
            handle.write(b"LUT_3D_SIZE 2\n" + b"0 0 0\n" * 8)
        prepared = {"items": [{"kind": "video", "path": self.media(), "asset_id": "v"},
                              {"kind": "lut", "path": cube, "filename": "Look.cube", "temp_dir": cube_dir,
                               "asset_id": "l"}],
                    "skipped": [{"asset_id": "m", "name": "chair", "why": "DaVinci Resolve does not import 3d assets"}]}
        res = self.do("import_asset", {"project_id": "p", "append": True}, prepared)
        self.assertEqual([a["kind"] for a in res["assets"]], ["video", "lut"])
        self.assertEqual(res["assets"][1]["lut"], "NOLGIA/Look.cube")
        self.assertEqual(res["skipped"][0]["asset_id"], "m")
        self.assertEqual([i["name"] for i in res["appended"]["items"]], ["clip.mov"])
        with self.assertRaisesRegex(CommandError, "single LUT"):
            self.do("import_asset", {"project_id": "p", "apply_to": "all"}, prepared)


class Luts(Base):
    def cube(self, name="look.cube", text=b'TITLE "x"\nLUT_3D_SIZE 2\n' + b"0 0 0\n" * 8):
        folder = tempfile.mkdtemp(dir=self.tmp)
        path = os.path.join(folder, "download")
        with open(path, "wb") as handle:
            handle.write(text)
        return {"kind": "lut", "path": path, "filename": name, "temp_dir": folder}

    def test_installs_into_the_nolgia_lut_folder(self):
        prepared = self.cube("Kodak Portra 400.cube")
        prepared.update(color_preset="kodak-portra-400", replace=True)
        res = self.do("import_asset", {"color_preset": "kodak-portra-400"}, prepared)
        target = os.path.join(self.tmp, "LUT", "NOLGIA", "Kodak Portra 400.cube")
        self.assertEqual(res, {"imported": ["Kodak Portra 400"], "kind": "lut", "path": target,
                               "lut": "NOLGIA/Kodak Portra 400.cube", "color_preset": "kodak-portra-400"})
        self.assertTrue(os.path.isfile(target))
        self.assertEqual(self.project.lut_refreshes, 1)
        self.assertFalse(os.path.exists(prepared["temp_dir"]))
        # a preset installs over its own older copy
        prepared = self.cube("Kodak Portra 400.cube", b"LUT_3D_SIZE 2\n" + b"1 1 1\n" * 8)
        prepared.update(color_preset="kodak-portra-400", replace=True)
        self.assertEqual(self.do("import_asset", {"color_preset": "kodak-portra-400"}, prepared)["path"], target)

    def test_a_library_lut_never_replaces_another(self):
        first = self.do("import_asset", {"asset_id": "a"}, dict(self.cube(), asset_id="a"))
        self.assertEqual(first["asset_ids"], ["a"])
        second = self.do("import_asset", {"asset_id": "b"}, self.cube(text=b"LUT_3D_SIZE 2\n" + b"1 0 0\n" * 8))
        self.assertTrue(second["path"].endswith("look 2.cube"), second["path"])
        same = self.do("import_asset", {"asset_id": "a"}, self.cube())
        self.assertEqual(same["path"], first["path"])

    def test_apply_to_each_target(self):
        tl = self.tl
        tl.add(fake.TimelineItem("Top", tl.start, tl.start + 20, "video", 2))
        tl.playhead = tl.start + 5
        tl.playhead_set = False  # as after Resolve starts: GetCurrentVideoItem answers None until the playhead is set
        res = self.do("import_asset", {"asset_id": "a", "apply_to": "current"}, self.cube())
        self.assertEqual([i["name"] for i in res["applied_to"]], ["Top"])
        self.assertEqual(res["node"], 1)
        self.assertEqual(tl.tracks["video"][1][0].graph.luts[1], res["path"])
        tl.selected = [tl.tracks["video"][0][0], tl.tracks["audio"][0][0]]
        res = self.do("import_asset", {"asset_id": "a", "apply_to": "selected"}, self.cube())
        self.assertEqual([i["name"] for i in res["applied_to"]], ["Shot 1"])
        tl.playhead = tl.start + 60
        res = self.do("import_asset", {"asset_id": "a", "apply_to": "current_track"}, self.cube())
        self.assertEqual([i["name"] for i in res["applied_to"]], ["Shot 1", "Shot 2"])
        res = self.do("import_asset", {"asset_id": "a", "apply_to": "all"}, self.cube())
        self.assertEqual([i["name"] for i in res["applied_to"]], ["Shot 1", "Shot 2", "Top"])

    def test_relative_path_when_resolve_wants_it(self):
        for item in self.tl.tracks["video"][0]:
            item.graph.known_luts = {"NOLGIA/look.cube"}
        res = self.do("import_asset", {"asset_id": "a", "apply_to": "all"}, self.cube())
        self.assertEqual(self.tl.tracks["video"][0][0].graph.luts[1], "NOLGIA/look.cube")
        self.assertEqual(len(res["applied_to"]), 2)

    def test_node_out_of_range_and_nothing_applied(self):
        with self.assertRaises(CommandError) as ctx:
            self.do("import_asset", {"asset_id": "a", "apply_to": "all", "node": 3}, self.cube())
        self.assertIn("did not apply it", str(ctx.exception))
        self.assertIn("it has 1 node", str(ctx.exception))
        self.assertEqual(ctx.exception.result["kind"], "lut")
        self.assertTrue(os.path.isfile(ctx.exception.result["path"]), "LUT should stay installed")

    def test_nothing_selected(self):
        with self.assertRaisesRegex(CommandError, "No clips are selected"):
            self.do("import_asset", {"asset_id": "a", "apply_to": "selected"}, self.cube())


class PrepareImport(unittest.TestCase):
    """The worker-thread half of import_asset, against a stub API."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nolgia-test-")
        self.env = mock.patch.dict(os.environ, {"NOLGIA_IMPORT_DIR": os.path.join(self.tmp, "imports")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def api(self, asset, data, pages=None):
        class Response:
            def __init__(self, body):
                self.body = body

            def json(self):
                return self.body

        class Api:
            base_url = "http://mock/v1"
            queries = []

            def get_asset(self, asset_id):
                return dict(asset, id=asset_id)

            def download(self, url, dest):
                with open(dest, "wb") as handle:
                    handle.write(data(url) if callable(data) else data)
                with open(dest, "rb") as handle:
                    return handle.read(64)

            def request(self, method, path, query=None, timeout=None):
                Api.queries.append((path, dict(query or {})))
                return Response(pages[(query or {}).get("cursor")])

        return Api()

    def test_media_goes_to_the_import_folder(self):
        asset = {"signed_url": "u", "display_name": "Rooftop/clip.mp4", "mime_type": "video/mp4", "size_bytes": 12}
        cmd = command("import_asset", {"asset_id": "a-1"})
        out = ops_module.prepare_import(cmd, self.api(asset, b"\x00\x00\x00\x18ftypmp42"), {"document": {"name": "My: Film"}})
        item = out["items"][0]
        self.assertEqual(item["kind"], "video")
        self.assertEqual(item["path"], os.path.join(self.tmp, "imports", "My_ Film", "a-1", "clip.mp4"))
        self.assertTrue(os.path.isfile(item["path"]))

    def test_cube_by_name_or_content(self):
        cube = b'TITLE "Warm"\nLUT_3D_SIZE 2\n' + b"0 0 0\n" * 8
        for name in ("Warm look.cube", "warm.txt"):
            asset = {"signed_url": "u", "display_name": name, "mime_type": "text/plain", "size_bytes": len(cube)}
            out = ops_module.prepare_import(command("import_asset", {"asset_id": "a"}), self.api(asset, cube), {})
            item = out["items"][0]
            self.assertEqual(item["kind"], "lut")
            self.assertTrue(item["filename"].endswith(".cube"))
            shutil.rmtree(item["temp_dir"])

    def test_models_are_refused(self):
        asset = {"signed_url": "u", "display_name": "chair.glb", "mime_type": "model/gltf-binary"}
        with self.assertRaisesRegex(CommandError, "cannot import chair.glb"):
            ops_module.prepare_import(command("import_asset", {"asset_id": "a"}), self.api(asset, b"glTF...."), {})

    def test_asset_ids_in_order(self):
        asset = {"signed_url": "u", "mime_type": "image/png", "display_name": "x.png"}
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        out = ops_module.prepare_import(command("import_asset", {"asset_ids": ["z", "a", "m"]}),
                                        self.api(asset, png), {})
        self.assertEqual([i["asset_id"] for i in out["items"]], ["z", "a", "m"])
        self.assertEqual(out["skipped"], [])

    def test_project_assets_oldest_first_across_pages(self):
        def asset(i, modality, mime, name):
            return {"id": "p%d" % i, "modality": modality, "mime_type": mime, "display_name": name,
                    "signed_url": "url-%d" % i, "status": "ready"}

        pages = {
            None: {"items": [asset(1, "video", "video/mp4", "first.mp4"), asset(2, "3d", "model/gltf-binary", "chair.glb")],
                   "next_cursor": "c2", "total": 4},
            "c2": {"items": [asset(3, "image", "image/png", "third.png"), asset(4, "audio", "audio/wav", "fourth.wav")],
                   "next_cursor": None, "total": 4},
        }
        heads = {"url-1": b"\x00\x00\x00\x18ftypmp42", "url-3": b"\x89PNG\r\n\x1a\n", "url-4": b"RIFF\x00\x00\x00\x00WAVE"}
        api = self.api({}, lambda url: heads[url], pages)
        out = ops_module.prepare_import(command("import_asset", {"project_id": "proj-1"}), api, {})
        self.assertEqual([i["asset_id"] for i in out["items"]], ["p1", "p3", "p4"])
        self.assertEqual([i["kind"] for i in out["items"]], ["video", "image", "audio"])
        self.assertEqual(out["skipped"], [{"asset_id": "p2", "name": "chair.glb",
                                           "why": "DaVinci Resolve does not import 3d assets"}])
        self.assertEqual(api.queries[0], ("/assets", {"project_id": "proj-1", "sort": "created_at_asc", "limit": 100}))
        self.assertEqual(api.queries[1][1]["cursor"], "c2")

    def test_empty_or_huge_project(self):
        api = self.api({}, b"", {None: {"items": [], "next_cursor": None, "total": 0}})
        with self.assertRaisesRegex(CommandError, "no ready video, image or audio"):
            ops_module.prepare_import(command("import_asset", {"project_id": "p"}), api, {})
        api = self.api({}, b"", {None: {"items": [], "next_cursor": "x", "total": 500}})
        with self.assertRaisesRegex(CommandError, "has 500 assets"):
            ops_module.prepare_import(command("import_asset", {"project_id": "p"}), api, {})


class Helpers(unittest.TestCase):
    def test_resolve_objects_become_names(self):
        value = {"t": fake.Timeline("Edit"), "l": [fake.MediaPoolItem("C:/a.mov")], "n": 1}
        self.assertEqual(ops_module.resolve_jsonable(value), {"t": "Edit", "l": ["a.mov"], "n": 1})

    def test_lut_file_names(self):
        self.assertEqual(ops_module._lut_filename("Teal & Orange", "x"), "Teal & Orange.cube")
        self.assertEqual(ops_module._lut_filename("A/B: C?", "x"), "A B C.cube")
        self.assertEqual(ops_module._lut_filename("", "slug"), "slug.cube")
        self.assertEqual(ops_module._cube_title(b'TITLE "Nolgia Kodak Portra 400"\n'), "Kodak Portra 400")


if __name__ == "__main__":
    unittest.main()
