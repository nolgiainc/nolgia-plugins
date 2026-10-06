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
        self.assertEqual(self.project.jobs, {}, "render job left in the queue")

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
        with self.assertRaisesRegex(CommandError, "no project named Nope"):
            self.do("open", {"project": "Nope"})

    def test_open_in_a_folder(self):
        self.resolve.pm.folders["Clients"] = []
        self.resolve.pm.add("Ad", folder="Clients")
        res = self.do("open", {"project": "Ad", "folder": "Clients"})
        self.assertEqual((res["project"], res["folder"]), ("Ad", "Clients"))
        with self.assertRaisesRegex(CommandError, "no project folder"):
            self.do("open", {"project": "Ad", "folder": "Missing"})

    def test_open_asks_with_a_window_and_without_one_only_after_changes(self):
        cmd = command("open", {"project": "Other"})
        windowed = ops_module.Ops(self.resolve, can_ask=lambda: True)
        req = windowed.open_approval(cmd)
        self.assertEqual(req.approve_label, "Open project")
        self.assertIn("Rooftop Story", req.lines[1])
        headless = ops_module.Ops(self.resolve, can_ask=lambda: False)
        self.assertIsNone(headless.open_approval(cmd))
        headless.changed = True
        self.assertIn("Save first", headless.open_approval(cmd).headless_error)
        self.assertIsNone(windowed.open_approval(command("open", {"project": "Rooftop Story"})))


class ImportMedia(Base):
    def media(self, name="clip.mov"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(b"data")
        return path

    def test_imports_into_the_nolgia_bin(self):
        path = self.media()
        res = self.do("import_asset", {"asset_id": "a1"}, {"kind": "video", "path": path})
        self.assertEqual(res, {"imported": ["clip.mov"], "kind": "video", "bin": "NOLGIA imports", "path": path})
        bins = [f.name for f in self.project.pool.root.folders]
        self.assertIn("NOLGIA imports", bins)
        self.assertIs(self.project.pool.current, self.project.pool.root, "current bin not put back")
        self.assertTrue(self.ops.changed)
        again = self.do("import_asset", {"asset_id": "a1"}, {"kind": "video", "path": path})
        self.assertTrue(again["already_in_bin"])
        self.assertEqual(bins.count("NOLGIA imports"), 1)

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
        with self.assertRaisesRegex(CommandError, "for LUTs"):
            self.do("import_asset", {"asset_id": "a1", "apply_to": "all"}, {"kind": "video", "path": self.media()})


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
        first = self.do("import_asset", {"asset_id": "a"}, self.cube())
        second = self.do("import_asset", {"asset_id": "b"}, self.cube(text=b"LUT_3D_SIZE 2\n" + b"1 0 0\n" * 8))
        self.assertTrue(second["path"].endswith("look 2.cube"), second["path"])
        same = self.do("import_asset", {"asset_id": "a"}, self.cube())
        self.assertEqual(same["path"], first["path"])

    def test_apply_to_each_target(self):
        tl = self.tl
        tl.add(fake.TimelineItem("Top", tl.start, tl.start + 20, "video", 2))
        tl.playhead = tl.start + 5
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

    def api(self, asset, data):
        class Api:
            base_url = "http://mock/v1"

            def get_asset(self, asset_id):
                return asset

            def download(self, url, dest):
                with open(dest, "wb") as handle:
                    handle.write(data)
                return data[:64]

        return Api()

    def test_media_goes_to_the_import_folder(self):
        asset = {"signed_url": "u", "display_name": "Rooftop/clip.mp4", "mime_type": "video/mp4", "size_bytes": 12}
        cmd = command("import_asset", {"asset_id": "a-1"})
        out = ops_module.prepare_import(cmd, self.api(asset, b"\x00\x00\x00\x18ftypmp42"), {"document": {"name": "My: Film"}})
        self.assertEqual(out["kind"], "video")
        self.assertEqual(out["path"], os.path.join(self.tmp, "imports", "My_ Film", "a-1", "clip.mp4"))
        self.assertTrue(os.path.isfile(out["path"]))

    def test_cube_by_name_or_content(self):
        cube = b'TITLE "Warm"\nLUT_3D_SIZE 2\n' + b"0 0 0\n" * 8
        for name in ("Warm look.cube", "warm.txt"):
            asset = {"signed_url": "u", "display_name": name, "mime_type": "text/plain", "size_bytes": len(cube)}
            out = ops_module.prepare_import(command("import_asset", {"asset_id": "a"}), self.api(asset, cube), {})
            self.assertEqual(out["kind"], "lut")
            self.assertTrue(out["filename"].endswith(".cube"))
            shutil.rmtree(out["temp_dir"])

    def test_models_are_refused(self):
        asset = {"signed_url": "u", "display_name": "chair.glb", "mime_type": "model/gltf-binary"}
        with self.assertRaisesRegex(CommandError, "cannot import this file type"):
            ops_module.prepare_import(command("import_asset", {"asset_id": "a"}), self.api(asset, b"glTF...."), {})


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
