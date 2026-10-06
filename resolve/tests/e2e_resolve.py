#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""End-to-end test: the real plugin next to a real DaVinci Resolve Studio,
against the mock API.

1. Builds the download zip and unzips it into a scratch folder.
2. Starts DaVinci Resolve without its window (`Resolve -nogui`), or, with
   --use-running, uses the one already open. External scripting must be set
   to Local (Preferences > System > General).
3. Makes a throwaway project ("NOLGIA e2e ...") and opens it, so your own
   projects are never touched.
4. Starts tools/mock_bridge_server.py in this process and runs
   `ResolvePython NOLGIA.py --serve` with NOLGIA_TOKEN and NOLGIA_API_URL
   pointing at it (settings, imports and logs go to the scratch folder).
5. Sends every command through the mock's caller API (as the MCP server
   would) and checks the results, the uploaded files and Resolve itself.
6. Starts serve twice more to check it refuses to connect with "Ask before
   running code" on, and stops when the token is refused; signs in with the
   device flow inside ResolvePython.
7. Deletes the throwaway project, the LUTs it installed and the scratch
   folder, opens your previous project again (--use-running) or quits the
   Resolve it started.

Usage:
    python3 resolve/tests/e2e_resolve.py
    python3 resolve/tests/e2e_resolve.py --use-running --keep

Works on Windows, macOS and Linux, and from WSL with the Windows Resolve
(paths through wslpath, variables through WSLENV).
"""

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(PLUGIN_DIR)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, PLUGIN_DIR)

import build  # noqa: E402
from mock_bridge_server import COLOR_PRESETS, MockBridgeServer  # noqa: E402
from nolgia_resolve.core import PLUGIN_VERSION  # noqa: E402
from nolgia_resolve.core.commands import CAPABILITIES  # noqa: E402

TOKEN = "e2e-token"
WSL = os.path.exists("/proc/version") and "microsoft" in open("/proc/version").read().lower()

DEFAULTS = {
    "win": (r"C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolvePython\ResolvePython.exe",
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"),
    "darwin": ("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolvePython",
               "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/Resolve"),
    "linux": ("/opt/resolve/bin/ResolvePython", "/opt/resolve/bin/resolve"),
}

# Run inside ResolvePython against the running Resolve; prints one JSON line.
HELPER = r'''
import json, os, sys, time
import DaVinciResolveScript as dvr
action = sys.argv[1]
arg = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
out = {"ok": False}
resolve = dvr.scriptapp("Resolve")
if resolve is None:
    out["error"] = "no resolve"
else:
    pm = resolve.GetProjectManager()
    current = pm.GetCurrentProject()
    out["version"] = resolve.GetVersionString()
    out["product"] = resolve.GetProductName()
    out["studio"] = resolve.IsStudio()
    out["project"] = current.GetName() if current else None
    if action == "probe":
        out["ok"] = True
    elif action == "setup":
        pm.GotoRootFolder()
        project = pm.CreateProject(arg["name"])
        if project is None:
            out["error"] = "CreateProject failed"
        else:
            if pm.GetCurrentProject().GetName() != arg["name"]:
                project = pm.LoadProject(arg["name"])
            out["ok"] = pm.GetCurrentProject().GetName() == arg["name"]
            out["project"] = pm.GetCurrentProject().GetName()
            out["user_lut_dir_hint"] = None
    elif action == "inspect":
        project = pm.GetCurrentProject()
        tl = project.GetCurrentTimeline()
        out["timelines"] = [project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)]
        out["timeline"] = tl.GetName() if tl else None
        out["presets"] = project.GetRenderPresetList()
        out["jobs"] = len(project.GetRenderJobList() or [])
        out["format"] = project.GetCurrentRenderFormatAndCodec()
        out["playhead"] = tl.GetCurrentTimecode() if tl else None
        items = []
        if tl:
            for i in range(1, tl.GetTrackCount("video") + 1):
                for item in tl.GetItemListInTrack("video", i) or []:
                    graph = item.GetNodeGraph()
                    items.append({"name": item.GetName(), "lut": graph.GetLUT(1) if graph else None})
        out["video_items"] = items
        out["ok"] = True
    elif action == "teardown":
        name = arg["name"]
        project = pm.GetCurrentProject()
        if project is not None and project.GetName() == name:
            pm.CloseProject(project)
        back = arg.get("back")
        if back and back != name:
            pm.LoadProject(back)
        pm.GotoRootFolder()
        out["deleted"] = pm.DeleteProject(name)
        out["left"] = [p for p in pm.GetProjectListInCurrentFolder() if p.startswith("NOLGIA e2e")]
        out["project"] = pm.GetCurrentProject().GetName() if pm.GetCurrentProject() else None
        out["ok"] = bool(out["deleted"])
    elif action == "quit":
        out["ok"] = bool(resolve.Quit())
print("HELPER " + json.dumps(out, default=str), flush=True)
'''

# Signs in with the device flow inside ResolvePython, with the browser stubbed.
SIGN_IN = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from nolgia_resolve.runtime import Controller, connect_resolve
opened = []
ctl = Controller(connect_resolve(), has_window=True, open_url=opened.append)
before = ctl.signed_in
ctl.sign_in()
end = time.time() + 60
while time.time() < end and not (ctl.worker is not None and ctl.worker.state == "connected"):
    ctl.tick()
    time.sleep(0.05)
report = {"before": before, "opened": opened, "token": ctl.settings.get("token")[:9],
          "connected": ctl.settings.get("connected"), "state": ctl.worker.state if ctl.worker else None}
end = time.time() + 10
while time.time() < end and not ctl.account_email():
    ctl.tick()
    time.sleep(0.05)
report["email"] = ctl.account_email()
ctl.sign_out()
end = time.time() + 15
while ctl.worker is not None and time.time() < end:
    ctl.tick()
    time.sleep(0.05)
report["after"] = {"token": ctl.settings.get("token"), "connected": ctl.settings.get("connected"),
                   "status": ctl.status_line()}
print("E2E_SIGN_IN " + json.dumps(report), flush=True)
'''


# ------------------------------------------------------------------ paths


class Host:
    """Runs Resolve's programs, translating paths when they are Windows
    programs started from WSL."""

    def __init__(self, python, resolve):
        self.python = python
        self.resolve = resolve
        self.wsl = WSL and python.lower().endswith(".exe")

    def native(self, path):
        if not self.wsl:
            return path
        return subprocess.check_output(["wslpath", "-w", path], text=True).strip()

    def local(self, path):
        if not self.wsl:
            return path
        return subprocess.check_output(["wslpath", "-u", path], text=True).strip()

    def env(self, extra):
        env = dict(os.environ)
        env.update(extra)
        if self.wsl:
            names = [n for n in (env.get("WSLENV") or "").split(":") if n]
            names += [k for k in extra if k not in names]
            env["WSLENV"] = ":".join(names)
        return env

    def py(self, args, extra_env=None, timeout=300):
        return subprocess.run([self.python] + args, env=self.env(extra_env or {}), timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")

    def py_popen(self, args, extra_env, log_path):
        log = open(log_path, "w", encoding="utf-8", errors="replace")
        return subprocess.Popen([self.python] + args, env=self.env(extra_env), stdout=log, stderr=subprocess.STDOUT)

    def windows_env(self, name):
        if not self.wsl:
            return os.environ.get(name)
        out = subprocess.run(["cmd.exe", "/c", "echo %" + name + "%"], capture_output=True, text=True,
                             cwd="/mnt/c", timeout=30)
        return out.stdout.strip()

    def resolve_running(self):
        if self.wsl or sys.platform.startswith("win"):
            out = subprocess.run(["tasklist.exe" if self.wsl else "tasklist", "/FI", "IMAGENAME eq Resolve.exe"],
                                 capture_output=True, text=True, timeout=30)
            return "Resolve.exe" in out.stdout
        out = subprocess.run(["pgrep", "-f", "Resolve"], capture_output=True, text=True)
        return bool(out.stdout.strip())

    def start_resolve(self, log_path):
        log = open(log_path, "w", encoding="utf-8", errors="replace")
        return subprocess.Popen([self.resolve, "-nogui"], stdout=log, stderr=subprocess.STDOUT,
                                cwd="/mnt/c" if self.wsl else None)


def helper(host, folder, action, arg=None, timeout=180):
    path = os.path.join(folder, "helper.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(HELPER)
    out = host.py([host.native(path), action, json.dumps(arg or {})], timeout=timeout)
    line = next((l for l in out.stdout.splitlines() if l.startswith("HELPER ")), None)
    if line is None:
        return {"ok": False, "error": out.stdout[-2000:]}
    return json.loads(line[len("HELPER "):])


# --------------------------------------------------------------- fixtures


def make_png(width=320, height=180):
    """A warm gradient: sky to sand, like late sun on a wall."""
    rows = []
    for y in range(height):
        t = y / max(1, height - 1)
        row = bytearray()
        for x in range(width):
            s = x / max(1, width - 1)
            row += bytes((int(200 + 40 * t), int(150 + 50 * t - 20 * s), int(110 + 30 * s - 40 * t)))
        rows.append(b"\x00" + bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def make_wav(path, seconds=2.0, rate=48000):
    with wave.open(path, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for n in range(int(seconds * rate)):
            v = int(6000 * math.sin(2 * math.pi * 440 * n / rate))
            frames += struct.pack("<hh", v, v)
        out.writeframes(bytes(frames))


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG: %r" % data[:8]
    return struct.unpack(">II", data[16:24])


# ----------------------------------------------------------------- caller


class Caller:
    """What the MCP server does: enqueue a command and wait for it."""

    def __init__(self, root_url, token):
        self.root = root_url
        self.token = token

    def http(self, method, path, body=None, auth=True, raw=False, headers=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.root + path, data=data, method=method, headers=headers or {})
        if auth:
            req.add_header("Authorization", "Bearer " + self.token)
        if body is not None and not isinstance(body, bytes):
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
                return resp.status, (payload if raw else (json.loads(payload) if payload else None))
        except urllib.error.HTTPError as err:
            payload = err.read()
            return err.code, (json.loads(payload) if payload else None)

    def enqueue(self, kind, args=None, timeout=120, headers=None):
        status, data = self.http("POST", "/v1/bridge/commands",
                                 {"app": "resolve", "kind": kind, "args": args or {}, "timeout_seconds": timeout},
                                 headers=headers)
        assert status == 201, "enqueue %s: %s %s" % (kind, status, data)
        return data["id"]

    def wait(self, command_id, limit=900):
        end = time.time() + limit
        while True:
            status, data = self.http("GET", "/v1/bridge/commands/%s?wait=25" % command_id)
            assert status == 200, data
            if data["status"] not in ("queued", "running") or time.time() > end:
                return data

    def command(self, kind, args=None, timeout=120):
        return self.wait(self.enqueue(kind, args, timeout))

    def state(self):
        return self.http("GET", "/mock/state", auth=False)[1]

    def asset_bytes(self, asset_id):
        status, data = self.http("GET", "/mock/assets/%s/bytes" % asset_id, auth=False, raw=True)
        assert status == 200, status
        return data

    def seed_asset(self, filename, content_type, data, project_id=None):
        url = "/mock/assets?filename=%s&content_type=%s" % (filename, content_type)
        if project_id:
            url += "&project_id=" + project_id
        status, asset = self.http("POST", url, data, auth=False)
        assert status == 201, asset
        return asset["id"]


# ------------------------------------------------------------------ runner


class Checks:
    def __init__(self):
        self.passed = []
        self.failed = []

    def check(self, name, fn):
        started = time.time()
        try:
            fn()
        except Exception as err:  # report and keep going
            self.failed.append(name)
            print("FAIL %-52s %.1fs  %s: %s" % (name, time.time() - started, type(err).__name__, err), flush=True)
            return False
        self.passed.append(name)
        print("PASS %-52s %.1fs" % (name, time.time() - started), flush=True)
        return True


def expect_ok(cmd):
    assert cmd["status"] == "succeeded", "status %s, error: %s" % (cmd["status"], cmd.get("error"))
    return cmd["result"]


def scratch_root(host):
    """A folder both this script and Resolve can reach."""
    if host.wsl:
        temp = host.windows_env("TEMP")
        return host.local(temp)
    return tempfile.gettempdir()


def lut_dir(host):
    if host.wsl or sys.platform.startswith("win"):
        appdata = host.windows_env("APPDATA") if host.wsl else os.environ["APPDATA"]
        return os.path.join(host.local(appdata), "Blackmagic Design", "DaVinci Resolve", "Support", "LUT")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")
    return os.path.expanduser("~/.local/share/DaVinciResolve/LUT")


def main():
    key = "win" if (WSL or sys.platform.startswith("win")) else ("darwin" if sys.platform == "darwin" else "linux")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resolve-python", default=os.environ.get("RESOLVE_PYTHON", DEFAULTS[key][0]))
    parser.add_argument("--resolve", default=os.environ.get("RESOLVE_APP", DEFAULTS[key][1]))
    parser.add_argument("--use-running", action="store_true",
                        help="use the Resolve already open (its open project is switched to a throwaway one and back)")
    parser.add_argument("--keep", action="store_true", help="keep the scratch folder")
    opts = parser.parse_args()
    if WSL and opts.resolve_python[1:3] == ":\\":
        opts.resolve_python = subprocess.check_output(["wslpath", "-u", opts.resolve_python], text=True).strip()
        opts.resolve = subprocess.check_output(["wslpath", "-u", opts.resolve], text=True).strip()

    host = Host(opts.resolve_python, opts.resolve)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = os.path.join(scratch_root(host), "nolgia-resolve-e2e-" + stamp)
    os.makedirs(work)
    print("scratch folder: %s" % work, flush=True)
    started = time.time()
    checks = Checks()
    project_name = "NOLGIA e2e " + stamp
    state = {"resolve_proc": None, "previous": None, "luts_before": None}
    luts = os.path.join(lut_dir(host), "NOLGIA")
    state["luts_before"] = set(os.listdir(luts)) if os.path.isdir(luts) else None

    def build_and_unzip():
        path = build.build(os.path.join(work, "dist"))
        with zipfile.ZipFile(path) as archive:
            archive.extractall(os.path.join(work, "plugin"))
        assert os.path.isfile(os.path.join(work, "plugin", "Utility", "NOLGIA.py"))

    if not checks.check("build and unzip the download", build_and_unzip):
        return finish(checks, work, opts, started)

    def resolve_ready():
        probe = helper(host, work, "probe")
        if probe["ok"]:
            if not opts.use_running:
                raise AssertionError(
                    "DaVinci Resolve is already open (project %r). Close it first, or pass --use-running "
                    "to use it (its open project is switched to a throwaway one and back)." % probe.get("project"))
        else:
            if host.resolve_running():
                raise AssertionError(
                    "DaVinci Resolve is running but does not answer scripts. Set Preferences > System > General > "
                    "External scripting to Local, then try again. (%s)" % probe.get("error", "")[-300:])
            state["resolve_proc"] = host.start_resolve(os.path.join(work, "resolve.log"))
            end = time.time() + 240
            while time.time() < end:
                probe = helper(host, work, "probe")
                if probe["ok"]:
                    break
                assert state["resolve_proc"].poll() is None, "Resolve exited early"
                time.sleep(3)
            assert probe["ok"], "Resolve did not answer scripts within 240 s: %s" % probe.get("error", "")[-300:]
        assert probe["studio"] is True, "this is not DaVinci Resolve Studio: %s" % probe
        state["previous"] = probe.get("project")
        print("  %s %s, open project %r" % (probe["product"], probe["version"], probe["project"]), flush=True)

    if not checks.check("DaVinci Resolve Studio answers scripts", resolve_ready):
        return finish(checks, work, opts, started)

    def setup():
        res = helper(host, work, "setup", {"name": project_name})
        assert res["ok"], res
        state["project_open"] = True

    if not checks.check("make and open a throwaway project", setup):
        return cleanup_and_finish(host, checks, work, opts, started, project_name, state, luts)

    server = MockBridgeServer(tokens=(TOKEN,), poll_wait_seconds=10).start()
    caller = Caller(server.root_url, TOKEN)
    native = host.native
    env = {
        "NOLGIA_TOKEN": TOKEN,
        "NOLGIA_API_URL": server.base_url,
        "NOLGIA_ASK_BEFORE_RUN": "0",
        "NOLGIA_CONFIG_DIR": native(os.path.join(work, "config")),
        "NOLGIA_IMPORT_DIR": native(os.path.join(work, "imports")),
        "NOLGIA_INSTANCE_ID": "e2e-" + stamp,
    }
    entry = native(os.path.join(work, "plugin", "Utility", "NOLGIA.py"))
    log_path = os.path.join(work, "serve.log")
    proc = host.py_popen([entry, "--serve"], env, log_path)
    made = {}

    def serve_log():
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            return handle.read()

    try:
        def connects():
            end = time.time() + 60
            session = None
            while time.time() < end:
                status, data = caller.http("GET", "/v1/bridge/sessions")
                if status == 200 and data["sessions"]:
                    session = data["sessions"][0]
                    break
                assert proc.poll() is None, "serve exited early:\n" + serve_log()[-3000:]
                time.sleep(0.5)
            assert session, "no session within 60 s:\n" + serve_log()[-2000:]
            assert session["app"] == "resolve", session
            assert session["capabilities"] == list(CAPABILITIES), session["capabilities"]
            assert session["plugin_version"] == PLUGIN_VERSION
            assert session["app_version"].startswith("21."), session["app_version"]
            assert session["document"] == {"name": project_name}, session["document"]
            assert session["allow_agent"] is True and session["machine_name"]

        if not checks.check("serve connects and registers a session", connects):
            raise SystemExit

        def info_empty():
            res = expect_ok(caller.command("info"))
            assert res["project"]["name"] == project_name, res["project"]
            assert res["studio"] is True and res["app_version"].startswith("21."), res
            assert res["timeline"] is None and res["timelines"] == [], res
            assert res["page"] in ("media", "cut", "edit", "fusion", "color", "fairlight", "deliver", "photo"), res
            assert res["render"]["presets"], res["render"]
            made["info"] = res

        checks.check("info: empty project", info_empty)

        work_files = os.path.join(work, "fixtures")
        os.makedirs(work_files)
        png_id = caller.seed_asset("sunset-wall.png", "image/png", make_png())
        wav_path = os.path.join(work_files, "tone.wav")
        make_wav(wav_path)
        with open(wav_path, "rb") as handle:
            wav_id = caller.seed_asset("tone.wav", "audio/wav", handle.read())

        def import_image_new_timeline():
            res = expect_ok(caller.command("import_asset", {"asset_id": png_id, "append": True}))
            assert res["kind"] == "image" and res["bin"] == "NOLGIA imports", res
            assert res["imported"] == ["sunset-wall.png"], res
            assert res["appended"]["new_timeline"] is True, res
            assert res["appended"]["timeline"] == "NOLGIA timeline", res
            assert os.path.isfile(host.local(res["path"])), res["path"]
            made["image"] = res

        checks.check("import_asset: image, new timeline from it", import_image_new_timeline)

        def import_audio_append():
            res = expect_ok(caller.command("import_asset", {"asset_id": wav_id, "append": True}))
            assert res["kind"] == "audio", res
            assert res["appended"]["new_timeline"] is False and res["appended"]["timeline"] == "NOLGIA timeline", res
            assert any(i["track"].startswith("A") for i in res["appended"]["items"]), res

        checks.check("import_asset: audio appended to the timeline", import_audio_append)

        def info_timeline():
            res = expect_ok(caller.command("info"))
            tl = res["timeline"]
            assert tl["name"] == "NOLGIA timeline", tl
            assert tl["duration_frames"] > 0 and tl["width"] and tl["height"] and tl["fps"], tl
            assert tl["tracks"]["video"][0]["items"] >= 1, tl["tracks"]
            assert res["media_pool"]["bins"] and any(b["name"] == "NOLGIA imports" for b in res["media_pool"]["bins"]), res
            made["timeline"] = tl

        checks.check("info: timeline, tracks, bins", info_timeline)

        def run_ok():
            code = "\n".join([
                "tl = timeline",
                "print('hello from resolve')",
                "result = {'project': project, 'timeline': tl, 'fps': tl.GetSetting('timelineFrameRate'),",
                "          'clips': [i for i in tl.GetItemListInTrack('video', 1)], 'pool': media_pool is not None,",
                "          'version': resolve.GetVersionString(), 'pm': project_manager is not None}",
            ])
            res = expect_ok(caller.command("run", {"language": "python", "code": code}))
            value = res["value"]
            assert value["project"] == project_name and value["timeline"] == "NOLGIA timeline", value
            assert value["clips"] == ["sunset-wall.png"], value
            assert value["pool"] and value["pm"] and value["version"].startswith("21."), value
            assert "hello from resolve" in res["stdout"], res

        checks.check("run: Resolve objects, return value, stdout", run_ok)

        def run_fails():
            cmd = caller.command("run", {"code": "x = 1\nraise ValueError('boom from the test')\n"})
            assert cmd["status"] == "failed", cmd
            assert "ValueError: boom from the test" in cmd["error"] and "line 2" in cmd["error"], cmd["error"]
            assert "pyexec" not in cmd["error"], cmd["error"]

        checks.check("run: failure returns the traceback", run_fails)

        def run_timeout():
            t0 = time.time()
            cmd = caller.command("run", {"timeout_seconds": 2, "code": "import time\nwhile True:\n    time.sleep(0.01)\n"})
            assert cmd["status"] == "failed" and "longer than 2 seconds" in cmd["error"], cmd
            assert time.time() - t0 < 30

        checks.check("run: timeout stops a runaway loop", run_timeout)

        def preview():
            res = expect_ok(caller.command("preview", {"width": 640, "frame": 0}))
            data = caller.asset_bytes(res["asset_id"])
            assert res["mime_type"] == "image/png", res
            assert png_size(data) == (res["width"], res["height"]), (png_size(data), res)
            assert res["width"] == 640, res
            assert res["frame"] == 0 and res["timecode"] == made["timeline"]["start_timecode"], res
            made["preview"] = res

        checks.check("preview: a frame, scaled, uploaded as PNG", preview)

        def preview_default_and_timecode():
            res = expect_ok(caller.command("preview", {"timecode": made["timeline"]["start_timecode"]}))
            assert res["width"] == min(1280, made["timeline"]["width"]), res
            bad = caller.command("preview", {"frame": made["timeline"]["duration_frames"] + 10})
            assert bad["status"] == "failed" and "outside the timeline" in bad["error"], bad

        checks.check("preview: default width, timecode, range check", preview_default_and_timecode)

        def preview_jpeg_when_too_big():
            code = "import os\nos.environ['NOLGIA_PREVIEW_MAX_BYTES'] = '%s'"
            expect_ok(caller.command("run", {"code": code % "20000"}))
            try:
                res = expect_ok(caller.command("preview", {"width": made["timeline"]["width"]}))
            finally:
                expect_ok(caller.command("run", {"code": code % ""}))
            data = caller.asset_bytes(res["asset_id"])
            assert len(data) <= 20000 or res["mime_type"] == "image/png", (len(data), res)
            assert res["mime_type"] in ("image/jpeg", "image/png"), res
            made["big_preview"] = res["mime_type"]

        checks.check("preview: smaller file when too big to show", preview_jpeg_when_too_big)

        def export_png():
            res = expect_ok(caller.command("export", {"format": "png", "frames": "0", "filename": "still"}))
            data = caller.asset_bytes(res["asset_id"])
            assert png_size(data) == (made["timeline"]["width"], made["timeline"]["height"]), png_size(data)
            assert res["filename"] == "still-0000.png", res

        checks.check("export: png at the timeline's size", export_png)

        def export_mp4():
            before = helper(host, work, "inspect")
            res = expect_ok(caller.command("export", {"format": "mp4", "frames": "0-23", "filename": "cut"}, 600))
            data = caller.asset_bytes(res["asset_id"])
            assert data[4:8] == b"ftyp", data[:16]
            assert b"avc1" in data[:65536] + data[-65536:], "not H.264"
            assert res["filename"] == "cut.mp4" and res["frames"] == [0, 23], res
            made["mp4"] = res["asset_id"]
            after = helper(host, work, "inspect")
            assert after["jobs"] == before["jobs"], "render job left in the queue: %s" % after
            assert after["presets"] == before["presets"], "render presets changed: %s" % after["presets"]
            assert after["format"] == before["format"], "render format not put back: %s" % after["format"]
            assert after["playhead"] == before["playhead"], (before["playhead"], after["playhead"])

        checks.check("export: mp4 (H.264), Deliver settings put back", export_mp4)

        def import_video():
            res = expect_ok(caller.command("import_asset", {"asset_id": made["mp4"]}))
            assert res["kind"] == "video" and res["imported"] == ["cut.mp4"], res

        checks.check("import_asset: the rendered video into the bin", import_video)

        def import_several_and_a_project():
            project = "0d6c7a52-5f1e-4c55-9a52-0f6e4c3b2a10"
            with open(wav_path, "rb") as handle:
                wav = handle.read()
            clip = caller.asset_bytes(made["mp4"])
            first = caller.seed_asset("shot-1.mp4", "video/mp4", clip, project)
            second = caller.seed_asset("shot-2.png", "image/png", make_png(160, 90), project)
            third = caller.seed_asset("room-tone.wav", "audio/wav", wav, project)
            res = expect_ok(caller.command("import_asset", {"asset_ids": [second, first], "bin": "Selects"}))
            assert res["asset_ids"] == [second, first] and res["imported"] == ["shot-2.png", "shot-1.mp4"], res
            res = expect_ok(caller.command("import_asset", {"project_id": project, "bin": "Assembly",
                                                            "append": True}, 300))
            assert res["asset_ids"] == [first, second, third], res
            items = res["appended"]["items"]
            video = [i["name"] for i in items if i["track"].startswith("V")]
            audio = [i["name"] for i in items if i["track"].startswith("A")]
            assert video == ["shot-1.mp4", "shot-2.png"], res["appended"]  # the MP4's own sound is on A too
            assert audio[-1] == "room-tone.wav", res["appended"]

        checks.check("import_asset: asset_ids and project_id, in order", import_several_and_a_project)

        def color_preset_lut():
            slug, name = COLOR_PRESETS[0][0], COLOR_PRESETS[0][1]
            res = expect_ok(caller.command("import_asset", {"color_preset": slug, "apply_to": "all"}))
            assert res["kind"] == "lut" and res["color_preset"] == slug, res
            assert res["lut"] == "NOLGIA/%s.cube" % name, res
            local = host.local(res["path"])
            assert os.path.isfile(local), local
            with open(local, "rb") as handle:
                assert handle.read().startswith(('TITLE "Nolgia %s"' % name).encode()), "not the preset's cube"
            assert [i["name"] for i in res["applied_to"]] == ["sunset-wall.png"], res
            look = helper(host, work, "inspect")
            luts = [i["lut"] for i in look["video_items"]]
            assert luts and all(l and "NOLGIA" in l for l in luts), look
            made["lut"] = res

        checks.check("import_asset: color preset LUT, applied with SetLUT", color_preset_lut)

        def lut_targets():
            res = expect_ok(caller.command("import_asset", {"color_preset": COLOR_PRESETS[1][0], "apply_to": "current"}))
            assert len(res["applied_to"]) == 1, res
            res = caller.command("import_asset", {"color_preset": COLOR_PRESETS[2][0], "apply_to": "selected"})
            assert res["status"] == "failed" and "No clips are selected" in res["error"], res
            res = caller.command("import_asset", {"color_preset": "no-such-look"})
            assert res["status"] == "failed" and "no color preset named" in res["error"], res
            res = caller.command("import_asset", {"color_preset": COLOR_PRESETS[0][0], "apply_to": "all", "node": 9})
            assert res["status"] == "failed" and "node" in res["error"], res

        checks.check("import_asset: LUT targets and errors", lut_targets)

        def lut_from_library():
            cube = (b'TITLE "Library look"\nLUT_3D_SIZE 2\n' + b"0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n")
            asset = caller.seed_asset("Library look.cube", "text/plain", cube)
            res = expect_ok(caller.command("import_asset", {"asset_id": asset, "apply_to": "current_track"}))
            assert res["kind"] == "lut" and res["lut"] == "NOLGIA/Library look.cube", res
            assert res["applied_to"], res

        checks.check("import_asset: .cube from the library", lut_from_library)

        def save_and_open():
            res = expect_ok(caller.command("save"))
            assert res == {"project": project_name, "saved": True}, res
            res = caller.command("open", {"project": "NOLGIA no such project"})
            assert res["status"] == "failed" and "no project named" in res["error"], res
            res = expect_ok(caller.command("open", {"project": project_name}))
            assert res.get("already_open") is True, res
            info_ = expect_ok(caller.command("info"))
            assert info_["project"]["changed_by_nolgia_since_save"] is False, info_["project"]

        checks.check("save, then open checks", save_and_open)

        def open_refuses_after_changes():
            expect_ok(caller.command("run", {"code": "result = 1"}))
            other = state.get("previous") or "Untitled Project"
            res = caller.command("open", {"project": other})
            assert res["status"] == "failed" and "Save first" in res["error"], res

        checks.check("open: refuses headless after unsaved changes", open_refuses_after_changes)

        def allow_agent():
            status, data = caller.http("POST", "/v1/bridge/commands", {"app": "resolve", "kind": "info"},
                                       headers={"X-Nolgia-Surface": "hermes"})
            assert status == 201 and data["caller"] == "agent", data
            assert caller.wait(data["id"])["status"] == "succeeded"

        checks.check("NOLGIA Agent can send commands", allow_agent)

        def storage_without_token():
            storage = [r for r in caller.state()["requests"] if r["path"].startswith("/storage/")]
            assert storage and not any(r["auth"] for r in storage), "bearer token sent to a signed URL"

        checks.check("uploads never send the token to storage", storage_without_token)

        def cancel_running():
            cid = caller.enqueue("run", {"code": "import time\ntime.sleep(3)\nresult = 1"})
            end = time.time() + 30
            while time.time() < end and caller.http("GET", "/v1/bridge/commands/" + cid)[1]["status"] != "running":
                time.sleep(0.1)
            assert caller.http("POST", "/v1/bridge/commands/%s/cancel" % cid)[0] == 200
            time.sleep(4)
            assert caller.wait(cid)["status"] == "cancelled"
            expect_ok(caller.command("info"))

        checks.check("cancel: running command, plugin carries on", cancel_running)

        def switch_off():
            sid = caller.http("GET", "/v1/bridge/sessions")[1]["sessions"][0]["id"]
            res = expect_ok(caller.command("run", {"code":
                "import nolgia_resolve\nnolgia_resolve.disconnect()\nresult = 'bye'"}))
            assert res["value"] == "bye", res
            code = proc.wait(timeout=60)
            assert code == 0, "serve exit code %s\n%s" % (code, serve_log()[-2000:])
            deleted = [d["session_id"] for d in caller.state()["deleted_sessions"]]
            assert sid in deleted, deleted

        checks.check("switch off: session closed, clean exit", switch_off)
    except SystemExit:
        pass
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    def refuses_headless_ask():
        beats = caller.state()["heartbeats"]
        out = host.py([entry, "--serve"], dict(env, NOLGIA_ASK_BEFORE_RUN="1"), timeout=120)
        assert out.returncode == 3, "exit %s\n%s" % (out.returncode, out.stdout[-2000:])
        assert "Ask before running code is on" in out.stdout, out.stdout[-2000:]
        assert caller.state()["heartbeats"] == beats, "it registered anyway"

    checks.check("serve with Ask before running code: refuses", refuses_headless_ask)

    def bad_token():
        out = host.py([entry, "--serve"], dict(env, NOLGIA_TOKEN="not-a-real-token"), timeout=120)
        assert out.returncode == 3, "exit %s\n%s" % (out.returncode, out.stdout[-2000:])
        assert "did not accept your sign in" in out.stdout, out.stdout[-2000:]

    checks.check("refused token: stops and says so", bad_token)

    def device_sign_in():
        script = os.path.join(work, "sign_in.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(SIGN_IN)
        server.state.device_interval = 1
        server.state.auto_approve_after = 1
        signin_env = dict(env, NOLGIA_TOKEN="", NOLGIA_CONFIG_DIR=native(os.path.join(work, "signin-config")))
        lib = native(os.path.join(work, "plugin", "Utility", "nolgia_resolve.zip"))
        out = host.py([native(script), lib], signin_env, timeout=180)
        line = next((l for l in out.stdout.splitlines() if l.startswith("E2E_SIGN_IN ")), None)
        assert out.returncode == 0 and line, "exit %s\n%s" % (out.returncode, out.stdout[-3000:])
        report = json.loads(line[len("E2E_SIGN_IN "):])
        assert report["before"] is False, report
        assert len(report["opened"]) == 1 and "/device?user_code=" in report["opened"][0], report
        assert report["token"].startswith("nol_mock_") and report["connected"] is True, report
        assert report["state"] == "connected" and report["email"] == "test@nolgia.ai", report
        assert report["after"] == {"token": "", "connected": False, "status": "Signed out."}, report

    checks.check("sign in with the device flow, then sign out", device_sign_in)

    server.stop()
    return cleanup_and_finish(host, checks, work, opts, started, project_name, state, luts)


def cleanup_and_finish(host, checks, work, opts, started, project_name, state, luts):
    def cleanup():
        res = helper(host, work, "teardown", {"name": project_name, "back": state.get("previous")})
        assert res["ok"], res
        assert res["left"] == [], "throwaway projects left: %s" % res["left"]
        if os.path.isdir(luts):
            for name in os.listdir(luts):
                if state["luts_before"] is None or name not in state["luts_before"]:
                    os.remove(os.path.join(luts, name))
            if not os.listdir(luts) and state["luts_before"] is None:
                os.rmdir(luts)
        if state.get("resolve_proc") is not None:
            helper(host, work, "quit")
            try:
                state["resolve_proc"].wait(timeout=120)
            except subprocess.TimeoutExpired:
                state["resolve_proc"].kill()

    checks.check("clean up: project, LUTs, Resolve", cleanup)
    return finish(checks, work, opts, started)


def finish(checks, work, opts, started):
    total = len(checks.passed) + len(checks.failed)
    print("\n%d/%d checks passed in %.0f s" % (len(checks.passed), total, time.time() - started))
    if checks.failed:
        print("failed: " + ", ".join(checks.failed))
        print("logs kept in %s" % work)
        return 1
    if not opts.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
