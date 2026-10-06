# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""A small stand-in for DaVinci Resolve's scripting objects, with the
methods the plugin calls and the behaviour DaVinciResolveScript.pyi
documents (and what the real app was seen to do). Plain Python, for the
unit tests."""

import os
import struct

import support  # noqa: F401

from nolgia_resolve import still, timecode


class PyRemoteObject:
    """Named like Resolve's own proxy class, which the plugin recognises."""

    calls = None

    def _log(self, name, *args):
        if PyRemoteObject.calls is not None:
            PyRemoteObject.calls.append((type(self).__name__, name) + args)


class Graph(PyRemoteObject):
    def __init__(self, nodes=1):
        self.nodes = nodes
        self.luts = {}
        self.known_luts = None  # set of paths Resolve "discovered"; None = any existing file

    def GetNumNodes(self):
        return self.nodes

    def SetLUT(self, node, path):
        self._log("SetLUT", node, path)
        if not 1 <= node <= self.nodes:
            return False
        if self.known_luts is not None and path not in self.known_luts:
            return False
        if self.known_luts is None and not os.path.isabs(path):
            return False
        self.luts[node] = path
        return True

    def GetLUT(self, node):
        return self.luts.get(node, "")


class TimelineItem(PyRemoteObject):
    def __init__(self, name, start, end, track_type="video", track=1, clip=None):
        self.name, self.start, self.end = name, start, end
        self.track_type, self.track = track_type, track
        self.clip = clip
        self.graph = Graph()

    def GetName(self):
        return self.name

    def GetStart(self, subframe=False):
        return self.start

    def GetEnd(self, subframe=False):
        return self.end

    def GetDuration(self, subframe=False):
        return self.end - self.start

    def GetTrackTypeAndIndex(self):
        return [self.track_type, self.track]

    def GetNodeGraph(self, layer=None):
        return self.graph

    def GetMediaPoolItem(self):
        return self.clip


class MediaPoolItem(PyRemoteObject):
    def __init__(self, path, kind="video", frames=48):
        self.path = path
        self.name = os.path.basename(path)
        self.kind = kind
        self.frames = frames

    def GetName(self):
        return self.name

    def GetClipProperty(self, key=None):
        props = {"File Path": self.path, "Clip Name": self.name, "Type": self.kind}
        return props if key is None else props.get(key, "")


class Folder(PyRemoteObject):
    def __init__(self, name):
        self.name = name
        self.clips = []
        self.folders = []

    def GetName(self):
        return self.name

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.folders)

    def GetUniqueId(self):
        return "folder-" + self.name


class Timeline(PyRemoteObject):
    def __init__(self, name, fps=24, width=1920, height=1080, start_tc="01:00:00:00", uid=None):
        self.name = name
        self.fps, self.width, self.height = fps, width, height
        self.start_tc = start_tc
        self.start = timecode.to_frames(start_tc, fps)
        self.tracks = {"video": [[]], "audio": [[]], "subtitle": []}
        self.track_names = {}
        self.selected = []
        self.playhead = self.start
        self.uid = uid or "tl-" + name
        self.markers = {}

    # contents
    def add(self, item):
        tracks = self.tracks[item.track_type]
        while len(tracks) < item.track:
            tracks.append([])
        tracks[item.track - 1].append(item)
        return item

    def append_clip(self, clip):
        # Resolve appends to the end of the first track of the clip's kind.
        kind = "audio" if clip.kind == "audio" else "video"
        track = self.tracks[kind][0] if self.tracks[kind] else []
        end = max([i.end for i in track] or [self.start])
        item = TimelineItem(clip.name, end, end + clip.frames, kind, 1, clip)
        return self.add(item)

    # API
    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def GetSettings(self):
        return {"timelineFrameRate": float(self.fps), "timelineResolutionWidth": str(self.width),
                "timelineResolutionHeight": str(self.height), "timelineDropFrameTimecode": "0"}

    def GetStartFrame(self):
        return self.start

    def GetEndFrame(self):
        ends = [i.end for track in self.tracks["video"] + self.tracks["audio"] for i in track]
        return max(ends) if ends else self.start

    def GetStartTimecode(self):
        return self.start_tc

    def GetCurrentTimecode(self):
        return timecode.from_frames(self.playhead, self.fps)

    def SetCurrentTimecode(self, text):
        self._log("SetCurrentTimecode", text)
        try:
            self.playhead = timecode.to_frames(text, self.fps)
        except ValueError:
            return False
        return True

    def GetTrackCount(self, kind):
        return len(self.tracks.get(kind, []))

    def GetItemListInTrack(self, kind, index):
        tracks = self.tracks.get(kind, [])
        return list(tracks[index - 1]) if 1 <= index <= len(tracks) else None

    def GetTrackName(self, kind, index):
        return self.track_names.get((kind, index), "%s %d" % (kind.title(), index))

    def GetIsTrackEnabled(self, kind, index):
        return True

    def GetIsTrackLocked(self, kind, index):
        return False

    def GetSelectedClips(self):
        return list(self.selected)

    def GetCurrentVideoItem(self):
        for track in reversed(self.tracks["video"]):
            for item in track:
                if item.start <= self.playhead < item.end:
                    return item
        return None

    def GetMarkers(self):
        return dict(self.markers)


class MediaPool(PyRemoteObject):
    def __init__(self, project):
        self.project = project
        self.root = Folder("Master")
        self.current = self.root

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self.current

    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = Folder(name)
        parent.folders.append(folder)
        return folder

    def ImportMedia(self, infos):
        self._log("ImportMedia", infos)
        out = []
        for info in infos:
            path = info["FilePath"] if isinstance(info, dict) else info
            if not os.path.isfile(path):
                continue
            if any(c.path == path for c in self.current.clips):
                continue  # Resolve skips a file already in the bin
            ext = os.path.splitext(path)[1].lower()
            kind = "audio" if ext in (".wav", ".mp3") else "still" if ext in (".png", ".jpg") else "video"
            clip = MediaPoolItem(path, kind, 120 if kind == "still" else 48)
            self.current.clips.append(clip)
            out.append(clip)
        return out

    def AppendToTimeline(self, infos):
        tl = self.project.current
        if tl is None:
            return []
        return [tl.append_clip(info["mediaPoolItem"]) for info in infos]

    def CreateTimelineFromClips(self, name, infos):
        if any(t.name == name for t in self.project.timelines):
            return None
        tl = Timeline(name, self.project.fps, self.project.width, self.project.height)
        for info in infos:
            tl.append_clip(info["mediaPoolItem"])
        self.project.timelines.append(tl)
        self.current.clips.append(MediaPoolItem(name, "timeline"))
        return tl


def make_bmp(width, height, rgb=(200, 120, 40)):
    row = bytes([rgb[2], rgb[1], rgb[0]]) * width
    pad = (4 - len(row) % 4) % 4
    body = (row + b"\x00" * pad) * height
    header = struct.pack("<2sIHHI", b"BM", 54 + len(body), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(body), 2835, 2835, 0, 0)
    return header + info + body


def make_jpeg_stub(width, height, size=2000):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    data = b"\xff\xd8" + sof + b"\x00" * max(0, size - 40) + b"\xff\xd9"
    return data


class Project(PyRemoteObject):
    def __init__(self, name, manager):
        self.name = name
        self.manager = manager
        self.fps, self.width, self.height = 24, 1920, 1080
        self.timelines = []
        self.current = None
        self.pool = MediaPool(self)
        self.render_presets = ["H.264 Master", "YouTube 1080p"]
        self.format_codec = {"format": "mov", "codec": "H264"}
        self.render_mode = 1
        self.render_settings = {"TargetDir": "C:/Users/me/Videos"}
        self.jobs = {}
        self.job_order = []
        self.rendering = None
        self.lut_refreshes = 0
        self.still_formats = {".bmp", ".ppm", ".png", ".jpg"}
        self.still_noise = False
        self.render_polls = 2
        self.fail_render = False
        self.saved_presets = {}

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return "project-" + self.name

    def GetMediaPool(self):
        return self.pool

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, tl):
        self._log("SetCurrentTimeline", tl.name)
        if tl not in self.timelines:
            return False
        self.current = tl
        return True

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1] if 1 <= index <= len(self.timelines) else None

    def GetSettings(self):
        return {"timelineFrameRate": float(self.fps), "timelineResolutionWidth": str(self.width),
                "timelineResolutionHeight": str(self.height)}

    def GetRenderPresetList(self):
        return list(self.render_presets)

    def GetCurrentRenderFormatAndCodec(self):
        return dict(self.format_codec)

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self._log("SetCurrentRenderFormatAndCodec", fmt, codec)
        self.format_codec = {"format": fmt, "codec": codec}
        return True

    def GetCurrentRenderMode(self):
        return self.render_mode

    def SetCurrentRenderMode(self, mode):
        self.render_mode = mode
        return True

    def GetRenderCodecs(self, fmt):
        return {"H.264": "H264", "H.265": "H265"} if fmt == "mp4" else {}

    def SaveAsNewRenderPreset(self, name):
        if name in self.render_presets:
            return False
        self.render_presets.append(name)
        self.saved_presets[name] = dict(self.render_settings)
        return True

    def LoadRenderPreset(self, name):
        if name not in self.saved_presets:
            return False
        self.render_settings = dict(self.saved_presets[name])
        return True

    def DeleteRenderPreset(self, name):
        if name not in self.render_presets:
            return False
        self.render_presets.remove(name)
        self.saved_presets.pop(name, None)
        return True

    def SetRenderSettings(self, settings):
        self._log("SetRenderSettings", dict(settings))
        self.render_settings.update(settings)
        return True

    def AddRenderJob(self):
        job = "job-%d" % (len(self.job_order) + 1)
        self.jobs[job] = {"settings": dict(self.render_settings), "status": "Ready", "polls": 0,
                          "timeline": self.current.name if self.current else None}
        self.job_order.append(job)
        return job

    def GetRenderJobList(self):
        return [{"JobId": j} for j in self.job_order if j in self.jobs]

    def StartRendering(self, jobs, interactive=False):
        for job in jobs:
            if job not in self.jobs:
                return False
            self.jobs[job]["status"] = "Rendering"
        self.rendering = jobs[0]
        return True

    def IsRenderingInProgress(self):
        return self.rendering is not None

    def GetRenderJobStatus(self, job):
        info = self.jobs.get(job)
        if info is None:
            return {}
        if info["status"] == "Rendering":
            info["polls"] += 1
            if info["polls"] >= self.render_polls:
                if self.fail_render:
                    info["status"] = "Failed"
                    info["error"] = "Disk full"
                else:
                    settings = info["settings"]
                    path = os.path.join(settings["TargetDir"], settings["CustomName"] + ".mp4")
                    with open(path, "wb") as handle:
                        handle.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
                    info["status"] = "Complete"
                self.rendering = None
        out = {"JobStatus": info["status"], "CompletionPercentage": 100 if info["status"] == "Complete" else 50}
        if info.get("error"):
            out["Error"] = info["error"]
        return out

    def StopRendering(self):
        if self.rendering:
            self.jobs[self.rendering]["status"] = "Cancelled"
        self.rendering = None

    def DeleteRenderJob(self, job):
        return self.jobs.pop(job, None) is not None

    def RefreshLUTList(self):
        self.lut_refreshes += 1
        return True

    def ExportCurrentFrameAsStill(self, path):
        self._log("ExportCurrentFrameAsStill", path)
        ext = os.path.splitext(path)[1].lower()
        tl = self.current
        if tl is None or ext not in self.still_formats:
            return False
        w, h = tl.width, tl.height
        shade = (tl.playhead - tl.start) % 256
        if ext == ".bmp":
            data = make_bmp(w, h, (shade, 120, 40))
            if self.still_noise:
                import random

                rnd = random.Random(shade)
                data = data[:54] + bytes(rnd.randrange(256) for _ in range(len(data) - 54))
            with open(path, "wb") as handle:
                handle.write(data)
        elif ext == ".ppm":
            with open(path, "wb") as handle:
                handle.write(b"P6\n%d %d\n255\n" % (w, h) + bytes([shade, 120, 40]) * (w * h))
        elif ext == ".png":
            still.write_png(still.Image(w, h, [bytes([shade, 120, 40]) * w] * h), path)
        elif ext == ".jpg":
            with open(path, "wb") as handle:
                handle.write(make_jpeg_stub(w, h))
        return True


class ProjectManager(PyRemoteObject):
    def __init__(self):
        self.projects = {}
        self.folders = {"": ["Untitled Project"]}
        self.current = None
        self.saves = 0
        self.folder = ""

    def add(self, name, folder=""):
        project = Project(name, self)
        self.projects[name] = project
        self.folders.setdefault(folder, [])
        if name not in self.folders[folder]:
            self.folders[folder].append(name)
        return project

    def GetCurrentProject(self):
        return self.current

    def SaveProject(self):
        if self.current is None:
            return False
        self.saves += 1
        return True

    def LoadProject(self, name):
        if name not in self.folders.get(self.folder, []):
            return None
        self.current = self.projects.get(name) or self.add(name, self.folder)
        return self.current

    def GetProjectListInCurrentFolder(self):
        return list(self.folders.get(self.folder, []))

    def GetCurrentFolder(self):
        return self.folder.split("/")[-1] if self.folder else ""

    def GotoRootFolder(self):
        self.folder = ""
        return True

    def OpenFolder(self, name):
        path = (self.folder + "/" + name).strip("/")
        if path not in self.folders:
            return False
        self.folder = path
        return True

    def GetCurrentDatabase(self):
        return {"DbType": "Disk", "DbName": "Local Database"}


class Resolve(PyRemoteObject):
    def __init__(self):
        self.pm = ProjectManager()
        self.page = "edit"
        self.alive = True

    def GetProjectManager(self):
        return self.pm if self.alive else None

    def GetVersionString(self):
        return "21.1.1.7" if self.alive else None

    def GetProductName(self):
        return "DaVinci Resolve Studio"

    def IsStudio(self):
        return True

    def GetCurrentPage(self):
        return self.page

    def Fusion(self):
        return None


def sample(clips=2):
    """A Resolve with one project and a timeline holding `clips` clips."""
    resolve = Resolve()
    project = resolve.pm.add("Rooftop Story")
    resolve.pm.current = project
    tl = Timeline("Edit 1")
    project.timelines.append(tl)
    project.current = tl
    for n in range(clips):
        tl.add(TimelineItem("Shot %d" % (n + 1), tl.start + 48 * n, tl.start + 48 * (n + 1)))
    tl.add(TimelineItem("Music", tl.start, tl.start + 96, "audio", 1))
    project.pool.root.folders.append(Folder("Footage"))
    project.pool.root.folders[0].clips.append(MediaPoolItem("C:/media/a.mov"))
    return resolve
