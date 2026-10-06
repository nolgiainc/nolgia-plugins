# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""What each command does inside DaVinci Resolve.

`Ops` methods named do_<kind>(args, prepared, command) run on the script's
main thread, the only thread that calls Resolve. Each returns the result
dict or raises CommandError. Network work happens on the worker thread:
before (downloads, see `prepare_import`) and after (scaling a still,
waiting for a render, uploading, see `Finisher`). A result that carries
`_still`, `_render` or `_upload` is finished there.
"""

import contextlib
import glob
import hashlib
import os
import shutil
import tempfile
import time

from . import paths, still, timecode
from .core import util
from .core.api import ApiError, NetworkError
from .core.commands import CommandError
from .core.mainthread import ApprovalRequest
from .core.pyexec import run_python, to_jsonable

# The MCP preview tool shows the still inline only up to 3,750,000 bytes.
PREVIEW_MAX_BYTES = 3_600_000
PREVIEW_DEFAULT_WIDTH = 1280
UPLOAD_TAGS = ["resolve"]
IMPORT_BIN = "NOLGIA imports"
LUT_SUBFOLDER = "NOLGIA"
NEW_TIMELINE_NAME = "NOLGIA timeline"
# Uncompressed stills the plugin can read and scale, in the order to try.
RAW_STILL_EXTENSIONS = (".bmp", ".ppm")
MAX_LIST = 200
RENDER_DONE = ("Complete", "Failed", "Cancelled", "Background Render Cancelled", "Remote Render Cancelled")
MEDIA_KINDS = ("image", "video", "audio")
# Pages where Resolve 21.1.1 has no timeline playhead for scripts
# (GetCurrentTimecode answers None, SetCurrentTimecode fails).
NO_PLAYHEAD_PAGES = ("media", "fusion")


def call(obj, method, *args, default=None):
    """obj.method(*args), or `default` when obj is None or the call fails."""
    if obj is None:
        return default
    try:
        value = getattr(obj, method)(*args)
    except Exception:
        return default
    return default if value is None else value


REMOTE_TYPES = ("PyRemoteObject",)


def is_resolve_object(value):
    return any(cls.__name__ in REMOTE_TYPES for cls in type(value).__mro__)


def resolve_jsonable(value, _depth=0):
    """Like core.pyexec.to_jsonable, but Resolve objects (timelines, clips,
    folders) become their names."""
    if _depth > 32:
        return "[nested too deep]"
    if isinstance(value, dict):
        return {str(k): resolve_jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [resolve_jsonable(v, _depth + 1) for v in list(value)[:10000]]
    if is_resolve_object(value):
        name = call(value, "GetName")
        if isinstance(name, str):
            return name
        return repr(value)[:1000]
    return to_jsonable(value)


class Ops:
    """Everything here runs on the main thread."""

    def __init__(self, resolve, log=None, can_ask=lambda: True):
        self.resolve = resolve
        self.log = log or (lambda message: None)
        self.can_ask = can_ask
        # Whether NOLGIA's own commands changed the project since it was last
        # saved or opened (Resolve's API cannot say whether it has unsaved
        # changes, so this is what the plugin knows).
        self.changed = False
        self._raw_still_ext = None
        # (job id, settings to put back) while NOLGIA's render runs.
        self.active_render = None

    # ------------------------------------------------------------ context

    def project_manager(self):
        pm = call(self.resolve, "GetProjectManager")
        if pm is None:
            raise CommandError("DaVinci Resolve did not answer. Is it still open?")
        return pm

    def project(self):
        project = call(self.project_manager(), "GetCurrentProject")
        if project is None:
            raise CommandError("No project is open in DaVinci Resolve.")
        return project

    def timeline(self, project, name=None, required=True):
        if name:
            for index in range(1, int(call(project, "GetTimelineCount", default=0)) + 1):
                tl = call(project, "GetTimelineByIndex", index)
                if tl is not None and call(tl, "GetName") == name:
                    return tl
            names = self.timeline_names(project)
            raise CommandError("There is no timeline named %s. Timelines in this project: %s."
                               % (name, ", ".join(names[:50]) or "none"))
        tl = call(project, "GetCurrentTimeline")
        if tl is None and required:
            raise CommandError("This project has no timeline open. Open or make a timeline first.")
        return tl

    def timeline_names(self, project):
        names = []
        for index in range(1, int(call(project, "GetTimelineCount", default=0)) + 1):
            name = call(call(project, "GetTimelineByIndex", index), "GetName")
            if name:
                names.append(name)
        return names

    def app_version(self):
        return str(call(self.resolve, "GetVersionString", default="") or "")

    def document(self):
        """The session's `document`: the open project's name."""
        project = call(call(self.resolve, "GetProjectManager"), "GetCurrentProject")
        return {"name": str(call(project, "GetName", default="") or "")}

    def alive(self):
        return bool(call(self.resolve, "GetVersionString"))

    # -------------------------------------------------------- timeline math

    def timeline_facts(self, tl):
        settings = call(tl, "GetSettings", default={}) or {}
        fps = _number(settings.get("timelineFrameRate")) or 24.0
        start = int(call(tl, "GetStartFrame", default=0) or 0)
        end = int(call(tl, "GetEndFrame", default=start) or start)
        start_tc = str(call(tl, "GetStartTimecode", default="") or "")
        drop = ";" in start_tc or str(settings.get("timelineDropFrameTimecode", "0")) == "1"
        return {
            "fps": fps,
            "start": start,
            "end": end,
            "duration": max(0, end - start),
            "start_timecode": start_tc,
            "drop_frame": drop,
            "width": _int_or_none(settings.get("timelineResolutionWidth")),
            "height": _int_or_none(settings.get("timelineResolutionHeight")),
        }

    def current_offset(self, tl, facts):
        text = call(tl, "GetCurrentTimecode", default="")
        if not text:
            return None
        try:
            return timecode.to_frames(text, facts["fps"]) - facts["start"]
        except ValueError:
            return None

    def offset_timecode(self, facts, offset):
        return timecode.from_frames(facts["start"] + offset, facts["fps"], facts["drop_frame"])

    def _target_offset(self, tl, facts, frame=None, text=None):
        if text:
            try:
                offset = timecode.to_frames(text, facts["fps"]) - facts["start"]
            except ValueError:
                raise CommandError("`timecode` must look like 01:00:05:12.") from None
        elif frame is not None:
            offset = frame
        else:
            offset = self.current_offset(tl, facts)
            if offset is None:
                offset = 0
        if facts["duration"] and not 0 <= offset < facts["duration"]:
            raise CommandError(
                "Frame %d is outside the timeline, which has %d frames (0 to %d, starting at %s)."
                % (offset, facts["duration"], facts["duration"] - 1, facts["start_timecode"] or "its start"))
        return offset

    # ---------------------------------------------------------------- info

    def do_info(self, args, prepared, command):
        resolve = self.resolve
        pm = self.project_manager()
        project = call(pm, "GetCurrentProject")
        out = {
            "app": "resolve",
            "app_version": self.app_version(),
            "product": call(resolve, "GetProductName"),
            "studio": call(resolve, "IsStudio"),
            "page": call(resolve, "GetCurrentPage"),
            "document": self.document(),
        }
        if project is None:
            out["project"] = None
            return out
        settings = call(project, "GetSettings", default={}) or {}
        db = call(pm, "GetCurrentDatabase", default={}) or {}
        out["project"] = {
            "name": call(project, "GetName"),
            "folder": call(pm, "GetCurrentFolder"),
            "database": db.get("DbName"),
            "unsaved_changes": None,
            "changed_by_nolgia_since_save": self.changed,
            "note": "DaVinci Resolve's scripting cannot tell whether the project has unsaved changes. "
                    "changed_by_nolgia_since_save says whether NOLGIA changed it since it was last saved or opened.",
            "width": _int_or_none(settings.get("timelineResolutionWidth")),
            "height": _int_or_none(settings.get("timelineResolutionHeight")),
            "fps": _number(settings.get("timelineFrameRate")),
        }
        current = call(project, "GetCurrentTimeline")
        current_name = call(current, "GetName")
        out["timeline"] = self.timeline_info(current) if current is not None else None
        out["current_timecode"] = out["timeline"]["current_timecode"] if out["timeline"] else None
        timelines = []
        for index in range(1, int(call(project, "GetTimelineCount", default=0)) + 1):
            name = call(call(project, "GetTimelineByIndex", index), "GetName")
            if name is not None and len(timelines) < MAX_LIST:
                timelines.append({"name": name, "current": name == current_name})
        out["timelines"] = timelines
        out["media_pool"] = self.media_pool_info(project)
        out["render"] = {
            "presets": list(call(project, "GetRenderPresetList", default=[]) or [])[:MAX_LIST],
            "format_and_codec": call(project, "GetCurrentRenderFormatAndCodec", default={}),
            "jobs": len(call(project, "GetRenderJobList", default=[]) or []),
            "rendering": bool(call(project, "IsRenderingInProgress", default=False)),
        }
        return out

    def timeline_info(self, tl):
        facts = self.timeline_facts(tl)
        offset = self.current_offset(tl, facts)
        tracks = {}
        for kind in ("video", "audio", "subtitle"):
            items = []
            for index in range(1, int(call(tl, "GetTrackCount", kind, default=0)) + 1):
                clips = call(tl, "GetItemListInTrack", kind, index, default=[]) or []
                items.append({
                    "index": index,
                    "name": call(tl, "GetTrackName", kind, index),
                    "items": len(clips),
                    "enabled": call(tl, "GetIsTrackEnabled", kind, index),
                    "locked": call(tl, "GetIsTrackLocked", kind, index),
                })
            tracks[kind] = items
        selected = []
        for item in call(tl, "GetSelectedClips", default=[]) or []:
            if len(selected) >= MAX_LIST:
                break
            selected.append(self.item_info(item, facts))
        current_item = self.current_item(tl) if facts["duration"] else None
        return {
            "name": call(tl, "GetName"),
            "fps": facts["fps"],
            "width": facts["width"],
            "height": facts["height"],
            "start_timecode": facts["start_timecode"],
            "start_frame": facts["start"],
            "end_frame": facts["end"],
            "duration_frames": facts["duration"],
            "current_timecode": call(tl, "GetCurrentTimecode"),
            "current_frame": offset,
            "tracks": tracks,
            "selected_clips": selected,
            "clip_under_playhead": self.item_info(current_item, facts) if current_item is not None else None,
            "markers": len(call(tl, "GetMarkers", default={}) or {}),
        }

    def item_info(self, item, facts):
        kind_index = call(item, "GetTrackTypeAndIndex", default=[]) or []
        start = call(item, "GetStart")
        end = call(item, "GetEnd")
        return {
            "name": call(item, "GetName"),
            "track": "%s%s" % ({"video": "V", "audio": "A", "subtitle": "S"}.get(kind_index[0], "?"), kind_index[1])
            if len(kind_index) == 2 else None,
            "start_frame": int(start) - facts["start"] if isinstance(start, (int, float)) else None,
            "end_frame": int(end) - facts["start"] if isinstance(end, (int, float)) else None,
        }

    def media_pool_info(self, project):
        pool = call(project, "GetMediaPool")
        root = call(pool, "GetRootFolder")
        bins = []
        for folder in call(root, "GetSubFolderList", default=[]) or []:
            if len(bins) >= MAX_LIST:
                break
            bins.append({
                "name": call(folder, "GetName"),
                "clips": len(call(folder, "GetClipList", default=[]) or []),
                "bins": len(call(folder, "GetSubFolderList", default=[]) or []),
            })
        return {
            "root": call(root, "GetName"),
            "root_clips": len(call(root, "GetClipList", default=[]) or []),
            "current_bin": call(call(pool, "GetCurrentFolder"), "GetName"),
            "bins": bins,
        }

    # ----------------------------------------------------------------- run

    def run_approval(self, command, ask):
        if not ask:
            return None
        code = command.args.get("code", "")
        return ApprovalRequest(
            "%s wants to run Python in DaVinci Resolve" % command.caller_label,
            code.splitlines() or [""],
            headless_error="Ask before running code is on, and DaVinci Resolve has no NOLGIA window to ask in, "
            "so the code did not run.",
            approve_label="Run code",
        )

    def do_run(self, args, prepared, command):
        pm = call(self.resolve, "GetProjectManager")
        project = call(pm, "GetCurrentProject")
        namespace = {
            "resolve": self.resolve,
            "project_manager": pm,
            "project": project,
            "media_pool": call(project, "GetMediaPool"),
            "timeline": call(project, "GetCurrentTimeline"),
            "fusion": call(self.resolve, "Fusion"),
            "result": None,
        }
        timeout = max(1.0, command.remaining())
        if args.get("timeout_seconds"):
            timeout = min(timeout, args["timeout_seconds"])
        self.changed = True
        outcome = run_python(args["code"], namespace, timeout=timeout)
        if outcome.ok:
            outcome.value = resolve_jsonable(namespace.get("result"))
        if not outcome.ok:
            raise CommandError(outcome.error, result=outcome.result())
        return outcome.result()

    # -------------------------------------------------------- stills

    @contextlib.contextmanager
    def playhead_page(self):
        """On the Media and Fusion pages Resolve has no playhead for scripts:
        go to the Edit page for the time it takes, then back."""
        page = call(self.resolve, "GetCurrentPage")
        moved = page in NO_PLAYHEAD_PAGES and bool(call(self.resolve, "OpenPage", "edit", default=False))
        try:
            yield
        finally:
            if moved:
                call(self.resolve, "OpenPage", page)

    def _at_frame(self, project, tl, offset, action):
        """Move the playhead to `offset`, run action(), put it back."""
        with self.playhead_page():
            return self._at_frame_here(project, tl, offset, action)

    def _at_frame_here(self, project, tl, offset, action):
        facts = self.timeline_facts(tl)
        before_tl = call(project, "GetCurrentTimeline")
        switched = before_tl is None or call(before_tl, "GetUniqueId") != call(tl, "GetUniqueId")
        if switched and not call(project, "SetCurrentTimeline", tl):
            raise CommandError("DaVinci Resolve did not switch to the timeline %s." % call(tl, "GetName"))
        before_tc = call(tl, "GetCurrentTimecode")
        target = self.offset_timecode(facts, offset)
        try:
            if target != before_tc and not call(tl, "SetCurrentTimecode", target):
                raise CommandError("DaVinci Resolve did not move the playhead to %s." % target)
            return action(target)
        finally:
            if before_tc and target != before_tc:
                call(tl, "SetCurrentTimecode", before_tc)
            if switched and before_tl is not None:
                call(project, "SetCurrentTimeline", before_tl)

    def _export_still(self, project, path):
        ok = call(project, "ExportCurrentFrameAsStill", path, default=False)
        return bool(ok) and os.path.isfile(path) and os.path.getsize(path) > 0

    def do_preview(self, args, prepared, command):
        project = self.project()
        tl = self.timeline(project, args.get("timeline"))
        facts = self.timeline_facts(tl)
        if not facts["duration"]:
            raise CommandError("The timeline %s is empty, so there is no frame to show." % call(tl, "GetName"))
        offset = self._target_offset(tl, facts, args.get("frame"), args.get("timecode"))
        folder = tempfile.mkdtemp(prefix="nolgia-preview-")
        try:
            def grab(tc):
                exts = (self._raw_still_ext,) if self._raw_still_ext else RAW_STILL_EXTENSIONS
                for ext in exts:
                    path = os.path.join(folder, "frame" + ext)
                    if self._export_still(project, path):
                        try:
                            image = still.read_image(path)
                        except still.StillError as err:
                            self.log("Could not read the %s still (%s)." % (ext, err))
                            continue
                        self._raw_still_ext = ext
                        return path, image.size
                # Resolve wrote no still we can read: take its PNG as it is.
                path = os.path.join(folder, "frame.png")
                if self._export_still(project, path):
                    return path, still.image_size(path)
                raise CommandError("DaVinci Resolve did not export the frame as a still.")

            raw, size = self._at_frame(project, tl, offset, grab)
            jpeg = os.path.join(folder, "frame.jpg")
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        width = args.get("width") or min(PREVIEW_DEFAULT_WIDTH, size[0])
        width, height = still.fit_width(size, width)
        name = call(tl, "GetName") or "timeline"
        return {
            "_still": {
                "path": raw,
                "folder": folder,
                "width": width,
                "height": height,
                "jpeg": jpeg,
                "jpeg_at": (tl, offset),
                "stem": "%s-preview-%04d" % (util.safe_filename(name, "timeline"), offset),
                "display_name": "DaVinci Resolve preview, %s frame %d" % (name, offset),
            },
            "width": width,
            "height": height,
            "timeline": name,
            "frame": offset,
            "timecode": self.offset_timecode(facts, offset),
        }

    def export_jpeg(self, tl_offset, path):
        """Main thread, from the finisher: Resolve's own JPEG of the frame."""
        tl, offset = tl_offset
        project = self.project()
        return self._at_frame(project, tl, offset, lambda tc: self._export_still(project, path))

    # ---------------------------------------------------------------- export

    def do_export(self, args, prepared, command):
        project = self.project()
        tl = self.timeline(project, args.get("timeline"))
        facts = self.timeline_facts(tl)
        name = call(tl, "GetName") or "timeline"
        stem = _stem(args.get("filename"), call(project, "GetName") or name)
        if not facts["duration"]:
            raise CommandError("The timeline %s is empty, so there is nothing to export." % name)
        frames = args.get("frames")
        if frames and frames[1] >= facts["duration"]:
            raise CommandError("`frames` ends at %d, past the end of the timeline (%d frames, 0 to %d)."
                               % (frames[1], facts["duration"], facts["duration"] - 1))
        folder = tempfile.mkdtemp(prefix="nolgia-export-")
        try:
            if args["format"] == "png":
                offset = frames[0] if frames else self._target_offset(tl, facts)
                path = os.path.join(folder, "%s-%04d.png" % (stem, offset))
                if not self._at_frame(project, tl, offset, lambda tc: self._export_still(project, path)):
                    raise CommandError("DaVinci Resolve did not export the frame as a PNG.")
                size = still.image_size(path)
                return {
                    "_upload": {"path": path, "content_type": "image/png", "filename": os.path.basename(path),
                                "display_name": os.path.basename(path), "cleanup": folder},
                    "format": "png",
                    "filename": os.path.basename(path),
                    "frames": [offset, offset],
                    "timecode": self.offset_timecode(facts, offset),
                    "width": size[0],
                    "height": size[1],
                    "timeline": name,
                }
            return self.start_render(project, tl, facts, frames, folder, stem)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def start_render(self, project, tl, facts, frames, folder, stem):
        if call(project, "IsRenderingInProgress", default=False):
            raise CommandError("DaVinci Resolve is already rendering. Try again when that render is done.")
        restore = {
            "page": call(self.resolve, "GetCurrentPage"),  # rendering opens the Deliver page
            "timeline": call(project, "GetCurrentTimeline"),
            "render_timeline": tl,
            "timecode": call(tl, "GetCurrentTimecode"),  # and moves the playhead
            "format": call(project, "GetCurrentRenderFormatAndCodec", default={}) or {},
            "mode": call(project, "GetCurrentRenderMode"),
            "preset": None,
        }
        # Resolve has no way to read the render settings back, so keep them
        # in a preset and load it again afterwards.
        preset = "NOLGIA saved settings %s" % hashlib.sha1(folder.encode("utf-8")).hexdigest()[:8]
        if call(project, "SaveAsNewRenderPreset", preset, default=False):
            restore["preset"] = preset
        job = None
        try:
            switched = restore["timeline"] is None or call(restore["timeline"], "GetUniqueId") != call(tl, "GetUniqueId")
            if switched and not call(project, "SetCurrentTimeline", tl):
                raise CommandError("DaVinci Resolve did not switch to the timeline %s." % call(tl, "GetName"))
            codec = self.h264_codec(project)
            if not call(project, "SetCurrentRenderFormatAndCodec", "mp4", codec, default=False):
                raise CommandError("DaVinci Resolve did not take MP4 with %s as the render format." % codec)
            call(project, "SetCurrentRenderMode", 1)
            width = facts["width"] or 1920
            height = facts["height"] or 1080
            settings = {
                "TargetDir": folder,
                "CustomName": stem,
                "ExportVideo": True,
                "ExportAudio": self.has_audio(tl),
                "FormatWidth": width - width % 2,
                "FormatHeight": height - height % 2,
            }
            if frames:
                settings.update(SelectAllFrames=False, MarkIn=facts["start"] + frames[0],
                                MarkOut=facts["start"] + frames[1])
            else:
                settings["SelectAllFrames"] = True
            if not call(project, "SetRenderSettings", settings, default=False):
                raise CommandError("DaVinci Resolve did not take the render settings.")
            job = call(project, "AddRenderJob")
            if not job:
                raise CommandError("DaVinci Resolve did not add the render job.")
            if not call(project, "StartRendering", [job], False, default=False):
                raise CommandError("DaVinci Resolve did not start rendering.")
        except BaseException:
            self.finish_render(job, restore)
            raise
        self.active_render = (job, restore)
        start, end = frames if frames else (0, facts["duration"] - 1)
        return {
            "_render": {"job": job, "folder": folder, "stem": stem, "restore": restore},
            "format": "mp4",
            "filename": stem + ".mp4",
            "frames": [start, end],
            "timeline": call(tl, "GetName"),
            "width": settings["FormatWidth"],
            "height": settings["FormatHeight"],
        }

    def h264_codec(self, project):
        codecs = call(project, "GetRenderCodecs", "mp4", default={}) or {}
        names = list(codecs.values())
        for want in ("H264", "H.264"):
            if want in names:
                return want
        for name in names:
            if str(name).upper().replace(".", "").startswith("H264"):
                return name
        if names:
            raise CommandError("DaVinci Resolve offers no H.264 for MP4 here (it offers %s)." % ", ".join(map(str, names)))
        return "H264"

    def has_audio(self, tl):
        for index in range(1, int(call(tl, "GetTrackCount", "audio", default=0)) + 1):
            if call(tl, "GetItemListInTrack", "audio", index, default=[]):
                return True
        return False

    def render_status(self, job):
        project = self.project()
        status = call(project, "GetRenderJobStatus", job, default={}) or {}
        return {
            "in_progress": bool(call(project, "IsRenderingInProgress", default=False)),
            "status": status.get("JobStatus"),
            "percent": status.get("CompletionPercentage"),
            "error": status.get("Error"),
        }

    def stop_render(self):
        call(self.project(), "StopRendering")

    def abandon_render(self):
        """The window is closing mid-render: stop it and tidy up now."""
        if self.active_render is None:
            return
        job, restore = self.active_render
        project = call(call(self.resolve, "GetProjectManager"), "GetCurrentProject")
        if call(project, "IsRenderingInProgress", default=False):
            call(project, "StopRendering")
        self.finish_render(job, restore)

    def finish_render(self, job, restore):
        """Remove the job and put the person's render settings back."""
        if self.active_render and self.active_render[0] == job:
            self.active_render = None
        project = call(call(self.resolve, "GetProjectManager"), "GetCurrentProject")
        if project is None:
            return
        if job:
            call(project, "DeleteRenderJob", job)
        fmt = restore.get("format") or {}
        if restore.get("preset"):
            call(project, "LoadRenderPreset", restore["preset"])
            call(project, "DeleteRenderPreset", restore["preset"])
        if fmt.get("format") and fmt.get("codec"):
            call(project, "SetCurrentRenderFormatAndCodec", fmt["format"], fmt["codec"])
        if restore.get("mode") is not None:
            call(project, "SetCurrentRenderMode", restore["mode"])
        rendered = restore.get("render_timeline")
        if rendered is not None and restore.get("timecode") and call(rendered, "GetCurrentTimecode") != restore["timecode"]:
            call(rendered, "SetCurrentTimecode", restore["timecode"])
        if restore.get("timeline") is not None:
            call(project, "SetCurrentTimeline", restore["timeline"])
        if restore.get("page") and call(self.resolve, "GetCurrentPage") != restore["page"]:
            call(self.resolve, "OpenPage", restore["page"])

    # ------------------------------------------------------------ save, open

    def do_save(self, args, prepared, command):
        pm = self.project_manager()
        project = self.project()
        if not call(pm, "SaveProject", default=False):
            raise CommandError("DaVinci Resolve did not save the project %s." % call(project, "GetName"))
        self.changed = False
        return {"project": call(project, "GetName"), "saved": True}

    def open_approval(self, command):
        """Resolve cannot say whether the open project has unsaved changes,
        so with a window open the person is always asked. Without one, the
        plugin refuses only when its own commands changed the project."""
        project = call(call(self.resolve, "GetProjectManager"), "GetCurrentProject")
        current = call(project, "GetName", default="")
        target = command.args.get("project", "")
        if project is None or current == target:
            return None
        if not self.can_ask() and not self.changed:
            return None
        return ApprovalRequest(
            "%s wants to open another project" % command.caller_label,
            ["Open: %s" % target,
             "Changes to %s that are not saved are lost. Save first if you want to keep them." % current],
            headless_error="NOLGIA changed this project since it was last saved, and DaVinci Resolve has no "
            "NOLGIA window to ask the person in. Save first with the save command, then open.",
            approve_label="Open project",
        )

    def do_open(self, args, prepared, command):
        pm = self.project_manager()
        name = args["project"]
        folder = args.get("folder")
        if folder:
            call(pm, "GotoRootFolder")
            for part in [p for p in folder.replace("\\", "/").split("/") if p]:
                if not call(pm, "OpenFolder", part, default=False):
                    raise CommandError("There is no project folder %s." % folder)
        elif call(pm, "GetCurrentFolder") is None:
            # Right after Resolve starts, its project manager has no current
            # folder (and lists no projects) until a script picks one.
            call(pm, "GotoRootFolder")
        current = call(call(pm, "GetCurrentProject"), "GetName")
        if current == name:
            return {"project": name, "already_open": True}
        names = list(call(pm, "GetProjectListInCurrentFolder", default=[]) or [])
        if name not in names:
            raise CommandError("There is no project named %s in the project folder %s. Projects there: %s."
                               % (name, call(pm, "GetCurrentFolder") or "(top)", ", ".join(names[:50]) or "none"))
        project = call(pm, "LoadProject", name)
        if project is None:
            raise CommandError("DaVinci Resolve did not open the project %s." % name)
        self.changed = False
        return {"project": call(project, "GetName"), "folder": call(pm, "GetCurrentFolder")}

    # ---------------------------------------------------------------- import

    def import_bin(self, pool, name):
        root = call(pool, "GetRootFolder")
        if root is None:
            raise CommandError("DaVinci Resolve did not give the media pool.")
        for folder in call(root, "GetSubFolderList", default=[]) or []:
            if call(folder, "GetName") == name:
                return folder
        folder = call(pool, "AddSubFolder", root, name)
        if folder is None:
            raise CommandError("DaVinci Resolve did not make the bin %s." % name)
        return folder

    def do_import_asset(self, args, prepared, command):
        items = prepared["items"] if "items" in prepared else [prepared]
        luts = [i for i in items if i["kind"] == "lut"]
        if args.get("apply_to") and not (len(items) == 1 and luts):
            raise CommandError("`apply_to` is for a single LUT (a color_preset or one .cube file); "
                               "this import has %s." % _describe(items))
        if len(items) == 1 and luts:
            result = self.install_lut(args, luts[0])
            if luts[0].get("asset_id"):
                result["asset_ids"] = [luts[0]["asset_id"]]
            return result
        project = self.project()
        pool = call(project, "GetMediaPool")
        bin_name = args.get("bin") or IMPORT_BIN
        folder = self.import_bin(pool, bin_name) if len(luts) < len(items) else None
        entries, clips = [], []
        for item in items:
            if item["kind"] == "lut":
                lut = self.install_lut({}, item)
                entries.append({"asset_id": item.get("asset_id"), "name": lut["imported"][0], "kind": "lut",
                                "path": lut["path"], "lut": lut["lut"]})
                continue
            found, already = self.import_file(pool, folder, item["path"])
            entry = {"asset_id": item.get("asset_id"), "name": call(found[0], "GetName"), "kind": item["kind"],
                     "path": item["path"]}
            if already:
                entry["already_in_bin"] = True
            entries.append(entry)
            clips += found
        self.changed = True
        kinds = sorted({e["kind"] for e in entries})
        result = {
            "imported": [e["name"] for e in entries],
            "asset_ids": [e["asset_id"] for e in entries],
            "assets": entries,
            "kind": kinds[0] if len(kinds) == 1 else "mixed",
            "bin": bin_name,
        }
        if len(entries) == 1:
            result["path"] = entries[0]["path"]
            if entries[0].get("already_in_bin"):
                result["already_in_bin"] = True
        if prepared.get("skipped"):
            result["skipped"] = prepared["skipped"]
        if args.get("append") and clips:
            result["appended"] = self.append(project, pool, clips)
        return result

    def import_file(self, pool, folder, path):
        """Import one file into `folder`. Returns (clips, was_already_there)."""
        before = call(pool, "GetCurrentFolder")
        try:
            call(pool, "SetCurrentFolder", folder)
            # Resolve 21.1.1 imports with a list of paths and returns None for
            # the [{"FilePath": ...}] form its README calls the new one.
            found = call(pool, "ImportMedia", [path], default=[]) or []
            if not found:
                found = call(pool, "ImportMedia", [{"FilePath": path}], default=[]) or []
        finally:
            if before is not None:
                call(pool, "SetCurrentFolder", before)
        if found:
            return found, False
        found = [c for c in call(folder, "GetClipList", default=[]) or []
                 if _same_path(call(c, "GetClipProperty", "File Path"), path)]
        if not found:
            raise CommandError("DaVinci Resolve did not import %s." % os.path.basename(path))
        return found[:1], True

    def append(self, project, pool, items):
        tl = call(project, "GetCurrentTimeline")
        if tl is None:
            names = set(self.timeline_names(project))
            name, n = NEW_TIMELINE_NAME, 2
            while name in names:
                name, n = "%s %d" % (NEW_TIMELINE_NAME, n), n + 1
            tl = call(pool, "CreateTimelineFromClips", name, [{"mediaPoolItem": i} for i in items])
            if tl is None:
                raise CommandError("DaVinci Resolve did not make a timeline from the clips.")
            call(project, "SetCurrentTimeline", tl)
            facts = self.timeline_facts(tl)
            added = []
            for kind in ("video", "audio"):
                for index in range(1, int(call(tl, "GetTrackCount", kind, default=0)) + 1):
                    added += call(tl, "GetItemListInTrack", kind, index, default=[]) or []
            return {"timeline": name, "new_timeline": True, "items": [self.item_info(i, facts) for i in added]}
        added = call(pool, "AppendToTimeline", [{"mediaPoolItem": i} for i in items], default=[]) or []
        if not added:
            raise CommandError("DaVinci Resolve did not add the clips to the timeline %s." % call(tl, "GetName"))
        facts = self.timeline_facts(tl)
        return {"timeline": call(tl, "GetName"), "new_timeline": False,
                "items": [self.item_info(i, facts) for i in added]}

    # ------------------------------------------------------------------ LUTs

    def lut_folder(self):
        """The NOLGIA folder in the first LUT folder Resolve reads that can
        be written to."""
        tried = []
        for base in paths.lut_dirs():
            folder = os.path.join(base, LUT_SUBFOLDER)
            try:
                os.makedirs(folder, exist_ok=True)
                probe = os.path.join(folder, ".nolgia-write-test")
                with open(probe, "wb"):
                    pass
                os.remove(probe)
                return folder
            except OSError as err:
                tried.append("%s (%s)" % (base, err.strerror or err))
        raise CommandError("Could not write to DaVinci Resolve's LUT folder: %s. Set NOLGIA_LUT_DIR to a LUT "
                           "folder Resolve reads." % "; ".join(tried))

    def install_lut(self, args, prepared):
        project = self.project()
        folder = self.lut_folder()
        target = os.path.join(folder, prepared["filename"])
        with open(prepared["path"], "rb") as handle:
            data = handle.read()
        if os.path.exists(target) and not prepared.get("replace"):
            with open(target, "rb") as handle:
                same = handle.read() == data
            if not same:
                stem, ext = os.path.splitext(target)
                n = 2
                while os.path.exists("%s %d%s" % (stem, n, ext)):
                    n += 1
                target = "%s %d%s" % (stem, n, ext)
        tmp = target + ".part"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
        shutil.rmtree(prepared.get("temp_dir") or "", ignore_errors=True)
        call(project, "RefreshLUTList")
        relative = "%s/%s" % (LUT_SUBFOLDER, os.path.basename(target))
        result = {
            "imported": [os.path.splitext(os.path.basename(target))[0]],
            "kind": "lut",
            "path": target,
            "lut": relative,
        }
        if prepared.get("color_preset"):
            result["color_preset"] = prepared["color_preset"]
        target_items = args.get("apply_to")
        if target_items:
            applied, failed = self.apply_lut(project, target, relative, target_items, args.get("node") or 1)
            result.update(applied_to=applied, node=args.get("node") or 1)
            if failed:
                result["not_applied"] = failed
            if not applied:
                raise CommandError("The LUT is installed (%s) but DaVinci Resolve did not apply it: %s"
                                   % (relative, "; ".join("%s: %s" % (f["name"], f["why"]) for f in failed[:5])),
                                   result=result)
            self.changed = True
        return result

    def current_item(self, tl):
        item = call(tl, "GetCurrentVideoItem")
        if item is None:
            # Resolve 21.1.1 answers None until the playhead has been set from
            # a script: set it where it already is, then ask again.
            here = call(tl, "GetCurrentTimecode")
            if here and call(tl, "SetCurrentTimecode", here):
                item = call(tl, "GetCurrentVideoItem")
        return item

    def lut_targets(self, tl, which):
        if which == "selected":
            items = [i for i in call(tl, "GetSelectedClips", default=[]) or [] if _is_video(i)]
            if not items:
                raise CommandError("No clips are selected in the timeline. Select clips, or use "
                                   "apply_to: \"current\" or \"all\".")
            return items
        if which in ("current", "current_track"):
            item = self.current_item(tl)
            if item is None:
                raise CommandError("There is no video clip under the playhead (at %s, on the %s page) in the "
                                   "timeline %s." % (call(tl, "GetCurrentTimecode") or "an unknown time",
                                                     call(self.resolve, "GetCurrentPage") or "current",
                                                     call(tl, "GetName")))
            if which == "current":
                return [item]
            kind_index = call(item, "GetTrackTypeAndIndex", default=[]) or []
            index = kind_index[1] if len(kind_index) == 2 else 1
            return list(call(tl, "GetItemListInTrack", "video", index, default=[]) or [])
        items = []
        for index in range(1, int(call(tl, "GetTrackCount", "video", default=0)) + 1):
            items += call(tl, "GetItemListInTrack", "video", index, default=[]) or []
        if not items:
            raise CommandError("The timeline %s has no video clips." % call(tl, "GetName"))
        return items

    def apply_lut(self, project, absolute, relative, which, node):
        with self.playhead_page():
            return self._apply_lut(project, absolute, relative, which, node)

    def _apply_lut(self, project, absolute, relative, which, node):
        tl = self.timeline(project)
        facts = self.timeline_facts(tl)
        applied, failed = [], []
        for item in self.lut_targets(tl, which):
            info = self.item_info(item, facts)
            graph = call(item, "GetNodeGraph")
            count = call(graph, "GetNumNodes")
            if isinstance(count, int) and node > count:
                info["why"] = "it has %d node%s" % (count, "" if count == 1 else "s")
                failed.append(info)
                continue
            ok = False
            for path in (absolute, relative):
                if call(graph, "SetLUT", node, path, default=False) or call(item, "SetLUT", node, path, default=False):
                    ok = True
                    break
            if ok:
                applied.append(info)
            else:
                info["why"] = "SetLUT returned false"
                failed.append(info)
        return applied, failed


# ------------------------------------------------------------ worker side


MAX_PROJECT_ASSETS = 200


def prepare_import(command, api, snapshot, log=lambda m: None):
    """Worker thread: fetch the files or the color preset, in order. No
    Resolve here. Returns {"items": [...], "skipped": [...]}."""
    args = command.args
    if args.get("color_preset"):
        return {"items": [_prepare_color_preset(args["color_preset"], api)], "skipped": []}
    skipped = []
    if args.get("project_id"):
        listed, skipped = _project_assets(args["project_id"], api)
        wanted = [(a["id"], a) for a in listed]
    else:
        wanted = [(i, None) for i in (args.get("asset_ids") or [args["asset_id"]])]
    items = []
    try:
        for asset_id, asset in wanted:
            items.append(_prepare_asset(asset_id, api, snapshot, asset))
    except BaseException:
        for item in items:
            if item.get("temp_dir"):
                shutil.rmtree(item["temp_dir"], ignore_errors=True)
        raise
    return {"items": items, "skipped": skipped}


def _project_assets(project_id, api):
    """The project's ready assets, oldest first: (media, skipped)."""
    media, skipped, cursor = [], [], None
    while True:
        query = {"project_id": project_id, "sort": "created_at_asc", "limit": 100}
        if cursor:
            query["cursor"] = cursor
        try:
            page = api.request("GET", "/assets", query=query, timeout=60).json() or {}
        except ApiError as err:
            if err.status in (400, 404):
                raise CommandError("NOLGIA has no project %s in this account (%s)."
                                   % (project_id, err.detail or err.title or err.status)) from None
            raise
        if not cursor and (page.get("total") or 0) > MAX_PROJECT_ASSETS:
            raise CommandError("The project has %d assets; import at most %d at a time (use asset_ids)."
                               % (page["total"], MAX_PROJECT_ASSETS))
        for asset in page.get("items") or []:
            if asset.get("status", "ready") != "ready":
                continue
            if asset.get("modality") in MEDIA_KINDS:
                media.append(asset)
            else:
                skipped.append({"asset_id": asset.get("id"), "name": asset.get("display_name") or "",
                                "why": "DaVinci Resolve does not import %s assets" % (asset.get("modality") or "these")})
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if len(media) + len(skipped) > MAX_PROJECT_ASSETS:
            raise CommandError("The project has more than %d assets; import at most %d at a time (use asset_ids)."
                               % (MAX_PROJECT_ASSETS, MAX_PROJECT_ASSETS))
    if not media:
        raise CommandError("The NOLGIA project %s has no ready video, image or audio assets." % project_id)
    return media, skipped


def _prepare_asset(asset_id, api, snapshot, asset=None):
    if not (asset or {}).get("signed_url"):
        try:
            asset = api.get_asset(asset_id)
        except ApiError as err:
            if err.status == 404:
                raise CommandError("NOLGIA has no asset %s in this account." % asset_id) from None
            raise
    url = (asset or {}).get("signed_url")
    if not url:
        raise CommandError("NOLGIA did not give a download link for asset %s." % asset_id)
    name = asset.get("display_name") or asset_id
    temp_dir = tempfile.mkdtemp(prefix="nolgia-import-")
    temp = os.path.join(temp_dir, "download")
    keep_temp = False
    try:
        try:
            head = api.download(url, temp)
        except NetworkError as err:
            raise CommandError("Could not download the asset from NOLGIA (%s)." % err) from None
        if _is_cube(name, asset.get("mime_type"), head):
            keep_temp = True  # install_lut copies it into the LUT folder, then removes it
            filename = _lut_filename(os.path.splitext(name)[0], asset_id)
            return {"kind": "lut", "path": temp, "filename": filename, "temp_dir": temp_dir, "asset_id": asset_id}
        ext = util.import_extension(name, asset.get("mime_type"), head)
        kind = util.IMPORT_KINDS.get(ext)
        if kind not in MEDIA_KINDS:
            raise CommandError(
                "DaVinci Resolve cannot import %s (%s). It imports images, video, audio and .cube LUTs from "
                "NOLGIA." % (name, asset.get("mime_type") or ext or "unknown type"))
        filename = util.safe_filename(os.path.splitext(name)[0], asset_id) + ext
        project = util.safe_filename((snapshot or {}).get("document", {}).get("name") or "", "Untitled Project")
        folder = os.path.join(paths.import_dir(), project, util.safe_filename(asset_id))
        os.makedirs(folder, exist_ok=True)
        final = os.path.join(folder, filename)
        size = asset.get("size_bytes")
        if not (os.path.isfile(final) and (not size or os.path.getsize(final) == size)):
            # (The same asset imported before is kept: Resolve may be using the file.)
            shutil.move(temp, final)
    finally:
        if not keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return {"kind": kind, "path": final, "ext": ext, "asset_id": asset_id}


def _describe(items):
    kinds = [i["kind"] for i in items]
    if len(kinds) == 1:
        return "one %s" % kinds[0]
    return "%d files" % len(kinds)


def _prepare_color_preset(slug, api):
    try:
        resp = api.request("GET", "/color-presets/%s/cube" % slug, timeout=60)
    except ApiError as err:
        if err.status == 404:
            raise CommandError("NOLGIA has no color preset named %s. The list is at "
                               "%s/color-presets." % (slug, api.base_url)) from None
        raise
    data = resp.body or b""
    if not _looks_like_cube(data[:4096]):
        raise CommandError("NOLGIA's answer for the color preset %s is not a .cube LUT." % slug)
    name = None
    try:
        listing = api.request("GET", "/color-presets", timeout=30).json() or {}
        for preset in listing.get("presets") or []:
            if preset.get("slug") == slug:
                name = preset.get("name")
    except (ApiError, NetworkError, ValueError):
        pass
    name = name or _cube_title(data) or slug
    temp_dir = tempfile.mkdtemp(prefix="nolgia-lut-")
    path = os.path.join(temp_dir, "preset.cube")
    with open(path, "wb") as handle:
        handle.write(data)
    filename = _lut_filename(name, slug)
    return {"kind": "lut", "path": path, "filename": filename, "temp_dir": temp_dir,
            "color_preset": slug, "replace": True}


def _lut_filename(name, fallback):
    text = "".join(ch if (ch.isalnum() or ch in " -_.&+'()") else " " for ch in name)
    text = " ".join(text.split()).strip(" .")[:100]
    return (text or fallback) + ".cube"


def _cube_title(data):
    for line in data[:4096].decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.upper().startswith("TITLE"):
            title = line[5:].strip().strip('"').strip()
            if title.upper().startswith("NOLGIA "):  # the API titles its cubes "Nolgia <name>"
                title = title[7:]
            return title or None
    return None


def _looks_like_cube(head):
    text = head.decode("utf-8", "replace")
    return any(key in text for key in ("LUT_3D_SIZE", "LUT_1D_SIZE"))


def _is_cube(name, mime, head):
    if os.path.splitext(name or "")[1].lower() == ".cube":
        return True
    if (mime or "").split(";")[0].strip().lower() in ("application/x-cube", "text/x-cube"):
        return True
    return _looks_like_cube(head or b"")


def _same_path(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _is_video(item):
    kind_index = call(item, "GetTrackTypeAndIndex", default=[]) or []
    return bool(kind_index) and kind_index[0] == "video"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stem(filename, fallback):
    if filename:
        return util.safe_filename(os.path.splitext(os.path.basename(filename))[0], "untitled")
    return util.safe_filename(fallback, "untitled")


class Finisher:
    """Worker thread: the part of a command that comes after Resolve's turn.

    `on_main(fn)` runs fn on the main thread and returns its value (for the
    few Resolve calls needed while waiting for a render).
    """

    def __init__(self, ops, api, on_main, log=lambda m: None, sleep=time.sleep):
        self.ops = ops
        self.api = api
        self.on_main = on_main
        self.log = log
        self.sleep = sleep

    def __call__(self, command, result):
        if not isinstance(result, dict):
            return result
        if "_still" in result:
            result = self.finish_still(command, dict(result))
        if "_render" in result:
            result = self.finish_render(command, dict(result))
        if "_upload" in result:
            result = self.upload(dict(result))
        return result

    def finish_still(self, command, result):
        job = result.pop("_still")
        folder = job["folder"]
        try:
            max_bytes = int(os.environ.get("NOLGIA_PREVIEW_MAX_BYTES") or PREVIEW_MAX_BYTES)
            width, height = job["width"], job["height"]
            path, mime = self._small_still(job, width, height, max_bytes)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        ext = os.path.splitext(path)[1]
        result["mime_type"] = mime
        result["width"], result["height"] = still.image_size(path)
        result["_upload"] = {"path": path, "content_type": mime, "filename": job["stem"] + ext,
                             "display_name": job["display_name"], "cleanup": folder}
        return result

    def _small_still(self, job, width, height, max_bytes):
        source = job["path"]
        jpeg = job["jpeg"]

        def resolve_jpeg(exact):
            """Resolve's own JPEG of the frame (at the timeline's size)."""
            if not self.on_main(lambda: self.ops.export_jpeg(job["jpeg_at"], jpeg)) or not os.path.isfile(jpeg):
                return False
            if exact and still.image_size(jpeg) != (width, height):
                return False
            return os.path.getsize(jpeg) <= max_bytes

        if source.endswith(".png"):
            # Resolve wrote no still the plugin can scale: send its PNG (or
            # JPEG) at the timeline's own size.
            if os.path.getsize(source) <= max_bytes:
                return source, "image/png"
            if resolve_jpeg(exact=False):
                return jpeg, "image/jpeg"
            raise CommandError("The frame is too large to show inline, and this DaVinci Resolve cannot export "
                               "it smaller. Use export with format png instead.")
        out = os.path.join(job["folder"], "preview.png")
        image = still.scale(still.read_image(source), width, height)
        still.write_png(image, out)
        if os.path.getsize(out) <= max_bytes:
            return out, "image/png"
        # Too big to show inline: a JPEG when Resolve's own one has the size
        # asked for, else keep the size and drop the lowest bits of each
        # colour, which PNG compresses far better.
        if resolve_jpeg(exact=True):
            return jpeg, "image/jpeg"
        for bits in (6, 5, 4):
            mask = 0xFF & ~((1 << (8 - bits)) - 1)
            table = bytes(v & mask for v in range(256))
            still.write_png(still.Image(width, height, [r.translate(table) for r in image.rows]), out)
            if os.path.getsize(out) <= max_bytes:
                return out, "image/png"
        raise CommandError("The preview image is too large to show inline. Ask for a smaller width.")

    def finish_render(self, command, result):
        job = result.pop("_render")
        folder = job["folder"]
        try:
            status = self._wait_render(command, job["job"])
        finally:
            try:
                self.on_main(lambda: self.ops.finish_render(job["job"], job["restore"]))
            except Exception as err:
                self.log("Could not put the render settings back (%s)." % err)
        try:
            if status.get("status") != "Complete":
                why = status.get("error") or status.get("status") or "unknown"
                raise CommandError("DaVinci Resolve did not finish the render (%s)." % why)
            movies = sorted(glob.glob(os.path.join(folder, "*.mp4")), key=os.path.getmtime)
            if not movies:
                raise CommandError("DaVinci Resolve finished rendering but wrote no MP4 file.")
            final = os.path.join(folder, job["stem"] + ".mp4")
            if movies[-1] != final:
                os.replace(movies[-1], final)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        result["_upload"] = {"path": final, "content_type": "video/mp4", "filename": os.path.basename(final),
                             "display_name": os.path.basename(final), "cleanup": folder}
        result["filename"] = os.path.basename(final)
        return result

    def _wait_render(self, command, job):
        # Leave time to stop the render and report back before the command expires.
        margin = 8.0
        last = {}
        seen_running = False
        while True:
            last = self.on_main(lambda: self.ops.render_status(job))
            if last.get("status") in RENDER_DONE:
                return last
            if last.get("in_progress") or last.get("status") == "Rendering":
                seen_running = True
            elif seen_running and not last.get("in_progress"):
                # Rendering stopped without a final status: ask once more.
                self.sleep(0.5)
                last = self.on_main(lambda: self.ops.render_status(job))
                return last
            if command.remaining() < margin:
                self.on_main(self.ops.stop_render)
                raise CommandError("The render took longer than this command may take, so NOLGIA stopped it. "
                                   "Export a shorter range with `frames`.")
            self.sleep(0.5)

    def upload(self, result):
        upload = result.pop("_upload")
        try:
            asset = self.api.upload_file(
                upload["path"],
                upload["content_type"],
                display_name=upload.get("display_name"),
                tags=UPLOAD_TAGS,
                filename=upload.get("filename"),
            )
        except ApiError as err:
            raise CommandError("NOLGIA did not take the file: %s" % (err.detail or err.title or err.status)) from None
        except NetworkError as err:
            raise CommandError("Could not upload the file to NOLGIA (%s)." % err) from None
        finally:
            shutil.rmtree(upload.get("cleanup") or "", ignore_errors=True)
        out = {"asset_id": asset.get("id")}
        out.update(result)
        return out
