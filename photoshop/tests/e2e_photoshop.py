#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""End-to-end test: the real plugin in the real Photoshop, driven through the
NOLGIA Bridge exactly as an agent drives it.

1. Builds the .ccx (with --prod the release build; against the mock a test
   build that may also reach the mock on localhost).
2. Installs it with Adobe's own installer (UnifiedPluginInstallerAgent),
   removing an older copy first.
3. Writes the developer file (nolgia-dev.json) into the plugin's data folder:
   the token, the API, connect at startup, a fixed instance id.
4. Starts Photoshop (it must not be running already) and waits for the
   plugin to register its session. Photoshop's "Graphics Processor
   Compatibility Check" notice, when it shows, is closed with its OK button.
5. Sends every command kind through POST /bridge/commands, as the MCP server
   does, and checks the results (and the files and assets they made).
6. Signs in with the device flow (the approval is done through the API),
   restarts Photoshop to check the saved sign in reconnects by itself, then
   signs out.
7. Removes the developer file and closes Photoshop.

Usage (Windows, from WSL or from Windows Python 3.8+, or macOS):

    python3 photoshop/tests/e2e_photoshop.py                  # against tools/mock_bridge_server.py
    python3 photoshop/tests/e2e_photoshop.py --prod --token-file ~/.config/nolgia/tokens.json

--prod talks to https://api.nolgia.ai with a real token (a JSON file with
`access_token`, or the token itself in NOLGIA_TOKEN). It uploads a few small
test images to that account and signs in one more device session for it.
Nothing it does costs credits.
"""

import argparse
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(PLUGIN_DIR)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, PLUGIN_DIR)

from mock_bridge_server import MockBridgeServer  # noqa: E402
import build  # noqa: E402

PLUGIN_ID = "com.nolgia.photoshop"
PHOTOSHOP = r"C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe"
UPIA = (r"C:\Program Files\Common Files\Adobe\Adobe Desktop Common\RemoteComponents\UPI"
        r"\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe")
MAC_PHOTOSHOP = "/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app"
MAC_UPIA = ("/Library/Application Support/Adobe/Adobe Desktop Common/RemoteComponents/UPI"
            "/UnifiedPluginInstallerAgent/UnifiedPluginInstallerAgent.app/Contents/MacOS/UnifiedPluginInstallerAgent")
CAPABILITIES = ["info", "run", "preview", "import_asset", "export", "save", "open"]
MOCK_TOKEN = "e2e-token"

# UXP snippets the checks share.
ALL_LAYERS = "const all = (ls) => Array.from(ls).flatMap((l) => [l].concat(l.layers ? all(l.layers) : []));\n"
SELECT_RED_BOX = "await play([{ _obj: 'select', _target: [{ _ref: 'layer', _name: 'Red box' }], makeVisible: false }]);\n"

GPU_NOTICE_PS = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$p = Get-Process Photoshop -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { exit 0 }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.AndCondition(
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $p.Id)),
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "Graphics Processor Compatibility Check")))
$dlg = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $dlg) { exit 0 }
$ok = $dlg.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "OK")))
if ($ok) { $ok.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); "pressed OK" }
"""

# Answers "No" in Photoshop's "Save changes ... before quitting?" prompt. The
# test only runs when Photoshop was closed before it started, so every open
# document is one of its own.
DONT_SAVE_PS = r"""
Add-Type @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class DS {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  public static string Text(IntPtr h) { var sb = new StringBuilder(256); GetWindowText(h, sb, 256); return sb.ToString(); }
  public static string Cls(IntPtr h) { var sb = new StringBuilder(256); GetClassName(h, sb, 256); return sb.ToString(); }
  public static List<IntPtr> Top(uint pid) { var l = new List<IntPtr>(); EnumWindows((h, x) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid && IsWindowVisible(h)) l.Add(h); return true; }, IntPtr.Zero); return l; }
  public static List<IntPtr> Kids(IntPtr w) { var l = new List<IntPtr>(); EnumChildWindows(w, (h, x) => { l.Add(h); return true; }, IntPtr.Zero); return l; }
}
"@
$p = Get-Process Photoshop -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { "no photoshop"; exit 0 }
foreach ($w in [DS]::Top([uint32]$p.Id)) {
  if ([DS]::Text($w) -ne "Adobe Photoshop") { continue }
  foreach ($k in [DS]::Kids($w)) {
    $t = [DS]::Text($k)
    "child [" + $t + "] " + [DS]::Cls($k)
    if ($t -eq "No" -or $t -eq "&No" -or $t -eq "Don't Save" -or $t -eq "Do&n't Save") { [DS]::SendMessage($k, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null; "clicked " + $t; exit 0 }
  }
}
"""

# Captures one Photoshop window (PrintWindow draws only that window, never
# what else is on the screen). UXP dialogs come out blank this way, so the
# approval window is checked through its DOM instead.
CAPTURE_PS = r"""
param([string]$Out, [string]$Title, [int]$MinWidth = 0)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class NW {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static List<IntPtr> For(uint pid) {
    var list = new List<IntPtr>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid && IsWindowVisible(h)) list.Add(h); return true; }, IntPtr.Zero);
    return list;
  }
}
"@
[NW]::SetProcessDPIAware() | Out-Null
$p = Get-Process Photoshop -ErrorAction Stop | Select-Object -First 1
foreach ($h in [NW]::For([uint32]$p.Id)) {
  $sb = New-Object System.Text.StringBuilder 256
  [NW]::GetWindowText($h, $sb, 256) | Out-Null
  if ($sb.ToString() -ne $Title) { continue }
  $r = New-Object NW+RECT
  [NW]::GetWindowRect($h, [ref]$r) | Out-Null
  $w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
  if ($w -lt $MinWidth) { continue }
  $bmp = New-Object System.Drawing.Bitmap $w, $ht
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $hdc = $g.GetHdc(); [NW]::PrintWindow($h, $hdc, 2) | Out-Null; $g.ReleaseHdc($hdc)
  $bmp.Save($Out)
  "saved $w x $ht"
  break
}
"""


# ------------------------------------------------------------------ host


class Windows:
    """Runs Windows programs and maps paths, from WSL or from Windows."""

    sep = "\\"
    photoshop = PHOTOSHOP
    upia = UPIA
    upia_flag = "/"

    def __init__(self):
        self.wsl = os.name != "nt" and os.path.exists("/proc/version") and \
            "microsoft" in open("/proc/version").read().lower()
        if os.name != "nt" and not self.wsl:
            raise SystemExit("This test drives Photoshop on Windows or macOS: run it there or in WSL.")

    def documents(self):
        return self.env("USERPROFILE") + r"\Documents"

    def uxp_root(self):
        return self.env("APPDATA") + r"\Adobe\UXP"

    def major_version(self, app):
        return self.powershell("(Get-Item '%s').VersionInfo.ProductMajorPart" % app).stdout.strip()

    def launch(self, app):
        subprocess.Popen(["cmd.exe", "/c", "start", "", app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def quit_photoshop(self):
        self.run(["taskkill.exe", "/IM", "Photoshop.exe"], timeout=60)

    def answer_dont_save(self):
        self.powershell(DONT_SAVE_PS, timeout=60)

    def close_gpu_notice(self):
        self.powershell(GPU_NOTICE_PS, timeout=60)

    def capture(self, out, title, min_width):
        res = self.powershell(CAPTURE_PS, ["-Out", out, "-Title", title, "-MinWidth", str(min_width)])
        return "saved" in res.stdout

    def to_app(self, path):
        if not self.wsl:
            return path
        return subprocess.check_output(["wslpath", "-w", path], text=True).strip()

    def to_local(self, path):
        if not self.wsl:
            return path
        return subprocess.check_output(["wslpath", "-u", path], text=True).strip()

    def run(self, args, timeout=120):
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              errors="replace", timeout=timeout)

    def env(self, name):
        out = self.run(["cmd.exe", "/c", "echo %" + name + "%"], timeout=30).stdout
        return out.strip().splitlines()[-1].strip()

    def powershell(self, script, args=(), timeout=120):
        folder = tempfile.mkdtemp(prefix="nolgia-ps-", dir=self.scratch)
        path = os.path.join(folder, "script.ps1")
        with open(path, "w", encoding="utf-8-sig") as handle:
            handle.write(script)
        try:
            return self.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             self.to_app(path)] + list(args), timeout=timeout)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def photoshop_running(self):
        out = self.run(["tasklist.exe", "/FI", "IMAGENAME eq Photoshop.exe"], timeout=30).stdout
        return "photoshop.exe" in out.lower()


class Mac:
    """Runs Photoshop on macOS. Paths are the same for the test and for
    Photoshop. The GPU notice and the window captures are Windows only."""

    sep = "/"
    photoshop = MAC_PHOTOSHOP
    upia = MAC_UPIA
    upia_flag = "--"
    wsl = False

    def to_app(self, path):
        return path

    def to_local(self, path):
        return path

    def run(self, args, timeout=120):
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              errors="replace", timeout=timeout)

    def documents(self):
        return os.path.expanduser("~/Documents")

    def uxp_root(self):
        return os.path.expanduser("~/Library/Application Support/Adobe/UXP")

    def major_version(self, app):
        plist = os.path.join(app, "Contents", "Info.plist")
        out = self.run(["defaults", "read", plist, "CFBundleShortVersionString"], timeout=30).stdout
        return out.strip().split(".")[0]

    def launch(self, app):
        subprocess.Popen(["open", app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def quit_photoshop(self):
        # The test only runs when Photoshop was closed before it started, so
        # every open document is one of its own: close them without saving.
        self.run(["osascript", "-e", 'tell application id "com.adobe.Photoshop"\n'
                  "close every document saving no\nquit\nend tell"], timeout=60)

    def answer_dont_save(self):
        self.quit_photoshop()

    def close_gpu_notice(self):
        pass

    def capture(self, out, title, min_width):
        return False

    def photoshop_running(self):
        out = self.run(["pgrep", "-f", "/Adobe Photoshop [0-9]+\\.app/Contents/MacOS/"], timeout=30)
        return out.returncode == 0


def make_host():
    return Mac() if sys.platform == "darwin" else Windows()


# ---------------------------------------------------------------- fixtures


def make_png(width, height, rgb=(230, 40, 40)):
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG: %r" % data[:8]
    return struct.unpack(">II", data[16:24])


def jpeg_size(data):
    assert data[:3] == b"\xff\xd8\xff", "not a JPEG: %r" % data[:4]
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    raise AssertionError("no JPEG size")


def tiny_glb():
    doc = json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0}).encode()
    doc += b" " * ((4 - len(doc) % 4) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(doc)) + struct.pack("<I4s", len(doc), b"JSON") + doc


# ------------------------------------------------------------------ caller


class Caller:
    """What the MCP server does: queue a command and wait for it."""

    def __init__(self, root, token, mock=None):
        self.root = root.rstrip("/")
        self.token = token
        self.mock = mock
        self.session_id = None

    def http(self, method, path, body=None, auth=True, raw=False, headers=None, url=None, timeout=60):
        # A TLS handshake that times out never sent the request, so it is
        # safe to try again (it happens now and then on the way to the API).
        for attempt in range(3):
            try:
                return self._http(method, path, body, auth, raw, headers, url, timeout)
            except urllib.error.URLError as err:
                if attempt == 2 or "handshake" not in str(err.reason):
                    raise
                time.sleep(2)

    def _http(self, method, path, body, auth, raw, headers, url, timeout):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(url or (self.root + path), data=data, method=method, headers=dict(headers or {}))
        if auth:
            req.add_header("Authorization", "Bearer " + self.token)
        if body is not None and not isinstance(body, bytes):
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                return resp.status, (payload if raw else (json.loads(payload) if payload else None))
        except urllib.error.HTTPError as err:
            payload = err.read()
            try:
                return err.code, json.loads(payload) if payload else None
            except ValueError:
                return err.code, payload

    def sessions(self):
        status, data = self.http("GET", "/v1/bridge/sessions")
        assert status == 200, data
        return data["sessions"]

    def enqueue(self, kind, args=None, timeout=120, agent=False):
        body = {"app": "photoshop", "kind": kind, "args": args or {}, "timeout_seconds": timeout}
        if self.session_id:
            body["session_id"] = self.session_id
        headers = {"X-Nolgia-Surface": "hermes"} if agent else None
        status, data = self.http("POST", "/v1/bridge/commands", body, headers=headers)
        assert status == 201, "enqueue %s: %s %s" % (kind, status, data)
        return data["id"]

    def wait(self, command_id, limit=300):
        end = time.time() + limit
        while True:
            status, data = self.http("GET", "/v1/bridge/commands/%s?wait=25" % command_id)
            assert status == 200, data
            if data["status"] not in ("queued", "running") or time.time() > end:
                return data

    def command(self, kind, args=None, timeout=120):
        return self.wait(self.enqueue(kind, args, timeout))

    def run(self, code, timeout=120, **extra):
        args = {"language": "uxp", "code": code}
        args.update(extra)
        return self.command("run", args, timeout)

    def asset_bytes(self, asset_id):
        if self.mock:
            status, data = self.http("GET", "/mock/assets/%s/bytes" % asset_id, auth=False, raw=True)
            assert status == 200, status
            return data
        status, asset = self.http("GET", "/v1/assets/%s" % asset_id)
        assert status == 200, asset
        status, data = self.http("GET", None, url=asset["signed_url"], auth=False, raw=True, timeout=120)
        assert status == 200, status
        return data

    def asset(self, asset_id):
        status, asset = self.http("GET", "/v1/assets/%s" % asset_id)
        assert status == 200, asset
        return asset

    def seed_asset(self, filename, content_type, data):
        if self.mock:
            query = urllib.parse.urlencode({"filename": filename, "content_type": content_type})
            status, asset = self.http("POST", "/mock/assets?" + query, data, auth=False)
            assert status == 201, asset
            return asset["id"]
        status, slot = self.http("POST", "/v1/assets/uploads", {
            "filename": filename, "content_type": content_type, "size_bytes": len(data),
            "display_name": filename, "tags": ["photoshop-e2e"]})
        assert status == 201, slot
        status, _ = self.http("PUT", None, data, auth=False, raw=True, url=slot["upload_url"],
                              headers={"Content-Type": content_type})
        assert status in (200, 201), status
        status, asset = self.http("POST", "/v1/assets/uploads/%s/complete" % slot["upload_id"])
        assert status == 200, asset
        return asset["id"]

    def approve_device(self, user_code):
        if self.mock:
            status, data = self.http("POST", "/mock/device/approve", {"user_code": user_code}, auth=False)
            assert status == 200 and data["approved"] == 1, data
            return
        status, data = self.http("POST", "/v1/auth/device/approve", {"user_code": user_code})
        assert status == 204, (status, data)


class Checks:
    def __init__(self):
        self.results = []

    def check(self, name, fn):
        started = time.time()
        try:
            note = fn()
        except Skip as skip:
            self.results.append((name, "SKIP", str(skip)))
            print("SKIP %-58s %s" % (name, skip), flush=True)
            return True
        except Exception as err:  # report and keep going
            self.results.append((name, "FAIL", "%s: %s" % (type(err).__name__, err)))
            print("FAIL %-58s %.1fs  %s: %s" % (name, time.time() - started, type(err).__name__, str(err)[:2000]), flush=True)
            return False
        self.results.append((name, "PASS", note or ""))
        print("PASS %-58s %.1fs%s" % (name, time.time() - started, ("  " + note) if note else ""), flush=True)
        return True

    @property
    def failed(self):
        return [r for r in self.results if r[1] == "FAIL"]


class Skip(Exception):
    pass


def seconds_between(a, b):
    """Seconds from RFC 3339 time a to time b (the server's clock)."""
    import datetime
    import re

    def parse(text):
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$", text)
        assert m, text
        stamp = datetime.datetime.fromisoformat(m.group(1)).replace(tzinfo=datetime.timezone.utc)
        return stamp.timestamp() + float("0." + (m.group(2) or "0"))

    return parse(b) - parse(a)


def ok(cmd):
    assert cmd["status"] == "succeeded", "status %s, error: %s" % (cmd["status"], cmd.get("error"))
    return cmd["result"]


def value(cmd):
    return ok(cmd)["value"]


# ------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prod", action="store_true", help="test against the real NOLGIA API")
    parser.add_argument("--api-url", default="https://api.nolgia.ai")
    parser.add_argument("--token-file", default=None, help="JSON file with access_token (prod)")
    parser.add_argument("--photoshop", default=None, help="Photoshop.exe (Windows) or its .app (macOS)")
    parser.add_argument("--port", type=int, default=8791, help="port for the mock API (default 8791)")
    parser.add_argument("--workdir", default=None, help="folder for test files (default Documents/NOLGIA test/photoshop/e2e)")
    parser.add_argument("--skip-install", action="store_true", help="use the plugin already installed")
    parser.add_argument("--skip-sign-in", action="store_true", help="leave out the device sign in and restart")
    parser.add_argument("--keep-open", action="store_true", help="leave Photoshop open at the end")
    opts = parser.parse_args()

    win = make_host()
    win.scratch = tempfile.mkdtemp(prefix="nolgia-e2e-")
    opts.photoshop = opts.photoshop or win.photoshop
    started = time.time()
    checks = Checks()

    if win.photoshop_running():
        raise SystemExit("Photoshop is running. Close it first: the test starts its own Photoshop.")

    sep = win.sep
    uxp_root = win.uxp_root()
    work_win = opts.workdir or sep.join([win.documents(), "NOLGIA test", "photoshop", "e2e"])
    work = win.to_local(work_win)
    if os.path.exists(work):
        shutil.rmtree(work)
    for sub in ("files", "exports", "shots"):
        os.makedirs(os.path.join(work, sub))
    files_win = work_win + sep + "files"
    exports_win = work_win + sep + "exports"
    print("test folder: %s" % work_win, flush=True)

    # --------------------------------------------------------- API and token
    server = None
    if opts.prod:
        token = os.environ.get("NOLGIA_TOKEN")
        if not token:
            if not opts.token_file:
                raise SystemExit("--prod needs --token-file (or NOLGIA_TOKEN)")
            with open(os.path.expanduser(opts.token_file), encoding="utf-8") as handle:
                token = json.load(handle)["access_token"]
        api_root = opts.api_url.rstrip("/")
        caller = Caller(api_root, token)
    else:
        server = MockBridgeServer(host="127.0.0.1", port=opts.port, tokens=(MOCK_TOKEN,), device_interval=1).start()
        token = MOCK_TOKEN
        api_root = "http://localhost:%d" % opts.port
        caller = Caller(server.root_url, token, mock=server)

    # ------------------------------------------------------- build, install
    dist = os.path.join(work, "dist")
    ccx = {}

    def build_it():
        domains = [] if opts.prod else ["http://localhost:%d" % opts.port, "http://127.0.0.1:%d" % opts.port]
        ccx["path"] = build.build(dist, domains)
        ccx["name"] = os.path.basename(ccx["path"])
        return ccx["name"]

    if not checks.check("build the .ccx", build_it):
        return finish(checks, started, opts, win, None)

    major = win.major_version(opts.photoshop)
    data_dir_win = sep.join([uxp_root, "PluginsStorage", "PHSP", major, "External", PLUGIN_ID, "PluginData"])
    data_dir = win.to_local(data_dir_win)
    plugins_info = win.to_local(sep.join([uxp_root, "PluginsInfo", "v1", "PS.json"]))

    def install():
        if opts.skip_install:
            raise Skip("--skip-install")
        upia = win.to_local(win.upia)
        # By name: "NOLGIA for Photoshop" (plain "NOLGIA" would also remove the
        # NOLGIA extension for After Effects, Premiere Pro and Illustrator).
        win.run([upia, win.upia_flag + "remove", "NOLGIA for Photoshop"], timeout=300)
        out = win.run([upia, win.upia_flag + "install", win.to_app(ccx["path"])], timeout=300).stdout
        assert "Installation Successful" in out, out[-1500:]
        with open(plugins_info, encoding="utf-8") as handle:
            entry = [p for p in json.load(handle)["plugins"] if p["pluginId"] == PLUGIN_ID]
        assert entry and entry[0]["status"] == "enabled", entry
        folder = win.to_local(entry[0]["path"].replace("$localPlugins", sep.join([uxp_root, "Plugins"])))
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        # The installer keeps an older copy of the same version, so check
        # that what is installed is this build.
        with zipfile.ZipFile(ccx["path"]) as package:
            for name in ("main.js", "ps/ops.js", "core/worker.js"):
                with open(os.path.join(folder, name), "rb") as handle:
                    assert handle.read() == package.read(name), "the installed %s is not this build" % name
        domains = manifest["requiredPermissions"]["network"]["domains"]
        assert ("http://localhost:%d" % opts.port in domains) != opts.prod, domains
        return "%s %s" % (entry[0]["name"], entry[0]["versionString"])

    if not checks.check("install with Adobe's installer", install) and not opts.skip_install:
        return finish(checks, started, opts, win, server)

    instance = "e2e-photoshop-%06d" % random.randrange(10 ** 6)
    dev = {
        "token": token,
        "api_url": api_root + "/v1",
        "autoconnect": True,
        "ask_before_run": False,
        "allow_agent": True,
        "instance_id": instance,
        "show_panel": True,
        "export_dir": exports_win,
    }
    os.makedirs(data_dir, exist_ok=True)
    dev_file = os.path.join(data_dir, "nolgia-dev.json")

    def write_dev(values):
        with open(dev_file, "w", encoding="utf-8") as handle:
            json.dump(values, handle)

    write_dev(dev)
    shots = {}

    def shot(name, title="", min_width=0):
        out_win = sep.join([work_win, "shots", name + ".png"])
        if win.capture(out_win, title, min_width):
            shots[name] = out_win
        return out_win

    def launch():
        win.launch(opts.photoshop)

    def wait_session(limit=240, want_dev_token=True):
        end = time.time() + limit
        last_gpu = 0
        while time.time() < end:
            mine = [s for s in caller.sessions() if s["instance_id"] == instance]
            if mine:
                caller.session_id = mine[0]["id"]
                return mine[0]
            if time.time() - last_gpu > 5 and win.photoshop_running():
                win.close_gpu_notice()
                last_gpu = time.time()
            time.sleep(2)
        raise AssertionError("no session from Photoshop within %d s" % limit)

    def close_photoshop(limit=90):
        win.quit_photoshop()
        end = time.time() + limit
        prompt_checked = time.time()
        while time.time() < end and win.photoshop_running():
            time.sleep(1)
            if time.time() - prompt_checked > 8:
                win.answer_dont_save()
                prompt_checked = time.time()
        return not win.photoshop_running()

    session = {}
    launch()
    try:
        def connects():
            session.update(wait_session())
            time.sleep(3)
            win.close_gpu_notice()
            assert session["app"] == "photoshop", session
            assert session["capabilities"] == CAPABILITIES, session["capabilities"]
            assert session["plugin_version"] == build.plugin_version(), session
            assert session["app_version"].startswith(major + "."), session["app_version"]
            assert session["document"] == {"name": ""}, session["document"]
            assert session["allow_agent"] is True
            assert session["machine_name"], session
            return "Photoshop %s, %s" % (session["app_version"], session["machine_name"])

        if not checks.check("connects and registers a session", connects):
            raise SystemExit

        def info_empty():
            res = ok(caller.command("info"))
            assert res["app"] == "photoshop" and res["documents"] == [] and res["active_document"] is None, res

        checks.check("info: no document open", info_empty)

        make_doc = """
// documents.add() sometimes resolves to null (seen right after Photoshop
// started); the new document is the active one then.
const d = (await app.documents.add({ width: 800, height: 450, resolution: 72, mode: "RGBColorMode", fill: "white", name: "NOLGIA e2e" })) || app.activeDocument;
const box = await d.layers.add({ name: "Red box" });
await d.selection.selectRectangle({ left: 100, top: 100, right: 300, bottom: 250 });
await play([{ _obj: "fill", using: { _enum: "fillContents", _value: "color" }, color: { _obj: "RGBColor", red: 230, grain: 40, blue: 40 }, opacity: { _unit: "percentUnit", _value: 100 }, mode: { _enum: "blendMode", _value: "normal" } }]);
await d.selection.selectRectangle({ left: 150, top: 120, right: 250, bottom: 200 });
await play([{ _obj: "make", new: { _class: "channel" }, at: { _ref: "channel", _enum: "channel", _value: "mask" }, using: { _enum: "userMaskEnabled", _value: "revealSelection" } }]);
await d.selection.selectRectangle({ left: 10, top: 20, right: 110, bottom: 70 });
const group = await d.createLayerGroup({ name: "NOLGIA Cleanup" });
// New layers go inside the active group, so make the red box active again.
await play([{ _obj: "select", _target: [{ _ref: "layer", _name: "Red box" }], makeVisible: false }]);
console.log("made", d.name);
result = { id: d.id, name: d.name, layers: d.layers.map(l => [l.name, l.kind]) };
"""

        def run_make():
            res = ok(caller.run(make_doc))
            assert res["value"]["name"] == "NOLGIA e2e", res
            assert res["value"]["layers"] == [["NOLGIA Cleanup", "group"], ["Red box", "pixel"], ["Background", "pixel"]], res
            assert res["stdout"] == "made NOLGIA e2e\n", res
            assert res["stderr"] == "", res

        checks.check("run: make a document with a layer, mask, group, selection", run_make)

        def info_doc():
            res = ok(caller.command("info"))
            assert [d["name"] for d in res["documents"]] == ["NOLGIA e2e"], res["documents"]
            doc = res["active_document"]
            assert (doc["width"], doc["height"], doc["resolution"]) == (800, 450, 72), doc
            assert doc["mode"] == "RGBColorMode" and doc["bits_per_channel"] == "bitDepth8", doc
            assert doc["path"] is None and doc["dirty"] is True, doc
            assert doc["selection"] == {"left": 10, "top": 20, "right": 110, "bottom": 70}, doc["selection"]
            names = [(l["name"], l["kind"], l["mask"]) for l in doc["layers"]]
            assert names == [("NOLGIA Cleanup", "group", False), ("Red box", "pixel", True), ("Background", "pixel", False)], names
            assert doc["layers"][2].get("background") is True, doc["layers"][2]
            # A layer's bounds are what its mask lets through.
            assert doc["layers"][1]["bounds"] == {"left": 150, "top": 120, "right": 250, "bottom": 200}, doc["layers"][1]
            assert doc["active_layers"] == ["Red box"], doc["active_layers"]
            assert doc["layer_count"] == 3
            hb_doc = caller.sessions()
            mine = [s for s in hb_doc if s["instance_id"] == instance][0]
            assert mine["document"] == {"name": "NOLGIA e2e"}, mine["document"]

        checks.check("info: document, layers, masks, selection; heartbeat names it", info_doc)

        def run_value():
            res = ok(caller.run("console.log('hello', {a: 1});\nconsole.warn('careful');\n"
                                "return { doc: doc.name, layer: doc.layers[0], n: 1.5, when: new Date(0) };"))
            assert res["value"] == {"doc": "NOLGIA e2e", "layer": {"typename": "Layer", "id": res["value"]["layer"]["id"],
                                    "name": "NOLGIA Cleanup"}, "n": 1.5, "when": "1970-01-01T00:00:00.000Z"}, res
            assert res["stdout"] == 'hello {"a":1}\n' and res["stderr"] == "careful\n", res

        checks.check("run: return value, console output", run_value)

        def run_undo_step():
            # (doc.layers.add puts a layer at the top of the stack, here inside
            # the group, so count every layer.)
            res = value(caller.run("await doc.layers.add({ name: 'Step A' });\nawait doc.layers.add({ name: 'Step B' });\n"
                                   "result = doc.activeHistoryState.name;"))
            state = value(caller.run(ALL_LAYERS + "result = [doc.activeHistoryState.name, all(doc.layers).length]"))
            assert state == ["NOLGIA: run code", 5], (res, state)
            value(caller.run(ALL_LAYERS + "for (const l of all(doc.layers).filter(l => l.name.startsWith('Step '))) await l.delete();\n" + SELECT_RED_BOX + "result = 1"))

        checks.check("run: one history step per run", run_undo_step)

        def run_fails():
            before = value(caller.run("result = doc.layers.length"))
            cmd = caller.run("await doc.layers.add({ name: 'Doomed' });\nconst x = 1;\nthrow new Error('boom from the test');\n")
            assert cmd["status"] == "failed", cmd
            err = cmd["error"]
            assert err.startswith("Error: boom from the test"), err
            assert "at line 3, column 7: throw new Error('boom from the test');" in err, err
            assert "undid what this run changed" in err, err
            after = value(caller.run("result = doc.layers.length"))
            assert after == before, (before, after)

        checks.check("run: failure gives the line and undoes the run", run_fails)

        def run_syntax():
            cmd = caller.run("const a = 1;\nconst b = ;\n")
            assert cmd["status"] == "failed" and "SyntaxError" in cmd["error"] and "at line 2: const b = ;" in cmd["error"], cmd

        checks.check("run: syntax error names the line", run_syntax)

        def run_timeout():
            t0 = time.time()
            cmd = caller.run("await sleep(6000);\nresult = 1;", timeout_seconds=2)
            assert cmd["status"] == "failed" and "after 2 seconds" in cmd["error"], cmd
            assert time.time() - t0 < 20
            time.sleep(5)

        checks.check("run: timeout stops waiting", run_timeout)

        def run_language():
            # The real API refuses it before it reaches the plugin; the mock
            # passes it on, so the plugin's own refusal is checked there.
            body = {"app": "photoshop", "kind": "run", "args": {"language": "python", "code": "print(1)"}}
            if caller.session_id:
                body["session_id"] = caller.session_id
            status, data = caller.http("POST", "/v1/bridge/commands", body)
            if status == 422:
                assert data["code"] == "language_not_supported", data
                return "refused by the API"
            assert status == 201, (status, data)
            cmd = caller.wait(data["id"])
            assert cmd["status"] == "failed" and "UXP JavaScript only" in cmd["error"], cmd

        checks.check("run: refuses other languages", run_language)

        def preview():
            res = ok(caller.command("preview", {"width": 320}))
            assert (res["width"], res["height"], res["mime_type"]) == (320, 180, "image/png"), res
            data = caller.asset_bytes(res["asset_id"])
            assert png_size(data) == (320, 180), png_size(data)
            with open(os.path.join(work, "shots", "preview.png"), "wb") as handle:
                handle.write(data)
            return "asset %s" % res["asset_id"]

        checks.check("preview: PNG at the asked width, uploaded", preview)

        def preview_region():
            res = ok(caller.command("preview", {"width": 100, "region": {"left": 100, "top": 100, "right": 300, "bottom": 250}}))
            assert (res["width"], res["height"]) == (100, 75), res
            data = caller.asset_bytes(res["asset_id"])
            assert png_size(data) == (100, 75)

        checks.check("preview: a region", preview_region)

        def preview_jpeg():
            value(caller.run(
                "globalThis.__nolgia.ops.tuning.previewMaxBytes = 400000;\n"
                "await doc.selection.deselect();\n"
                "const l = await doc.layers.add({ name: 'Noise' });\n"
                "await play([{ _obj: 'fill', using: { _enum: 'fillContents', _value: 'gray' }, opacity: { _unit: 'percentUnit', _value: 100 }, mode: { _enum: 'blendMode', _value: 'normal' } }]);\n"
                "await play([{ _obj: 'addNoise', distort: { _enum: 'distort', _value: 'uniformDistribution' }, noise: { _unit: 'percentUnit', _value: 60 }, monochromatic: false }]);\n"
                "result = 1;"))
            try:
                res = ok(caller.command("preview", {"width": 800}))
            finally:
                value(caller.run(ALL_LAYERS + "globalThis.__nolgia.ops.tuning.previewMaxBytes = 3600000;\n"
                                 "for (const l of all(doc.layers).filter(l => l.name === 'Noise')) await l.delete();\n"
                                 "await doc.selection.selectRectangle({ left: 10, top: 20, right: 110, bottom: 70 });\n"
                                 + SELECT_RED_BOX + "result = 1;"))
            assert res["mime_type"] == "image/jpeg" and (res["width"], res["height"]) == (800, 450), res
            data = caller.asset_bytes(res["asset_id"])
            assert jpeg_size(data) == (800, 450) and len(data) <= 400000, (jpeg_size(data), len(data))
            return "%d bytes" % len(data)

        checks.check("preview: JPEG when the PNG is too big to show", preview_jpeg)

        def preview_args():
            cmd = caller.command("preview", {"width": 5000})
            assert cmd["status"] == "failed" and "at most 1920" in cmd["error"], cmd

        checks.check("preview: checks its arguments", preview_args)

        def storage_without_token():
            if not server:
                raise Skip("the mock shows every request; production does not")
            reqs = server.state.requests
            storage = [r for r in reqs if r["path"].startswith("/storage/")]
            assert storage and not any(r["auth"] for r in storage), storage

        checks.check("uploads never send the token to storage", storage_without_token)

        assets = {}

        def seed():
            assets["plate"] = caller.seed_asset("street plate.png", "image/png", make_png(1600, 900, (40, 120, 220)))
            assets["patch"] = caller.seed_asset("patch.png", "image/png", make_png(120, 90, (40, 200, 90)))
            assets["glb"] = caller.seed_asset("model.glb", "model/gltf-binary", tiny_glb())

        checks.check("seed test assets", seed)

        def import_layer():
            res = ok(caller.command("import_asset", {"asset_id": assets["plate"]}))
            assert res["imported"] == ["street plate"] and res["kind"] == "smart_object" and res["group"] is None, res
            assert res["placement"] == "canvas" and res["bounds"] == {"left": 0, "top": 0, "right": 800, "bottom": 450}, res
            doc = ok(caller.command("info"))["active_document"]
            # A placed image lands right above the active layer (the red box).
            names = [l["name"] for l in doc["layers"]]
            assert names == ["NOLGIA Cleanup", "street plate", "Red box", "Background"], names
            assert doc["layers"][1]["kind"] == "smartObject" and doc["active_layers"] == ["street plate"], doc["layers"][1]
            assert doc["selection"] == {"left": 10, "top": 20, "right": 110, "bottom": 70}, doc["selection"]
            assert doc["history_state"] == "NOLGIA: import street plate", doc["history_state"]

        checks.check("import_asset: Smart Object lined up with the canvas, selection kept", import_layer)

        def import_patch():
            res = ok(caller.command("import_asset", {"asset_id": assets["patch"], "as": "pixels", "name": "Patch"}))
            assert res["kind"] == "layer" and res["imported"] == ["Patch"] and res["placement"] == "centered", res
            assert res["bounds"] == {"left": 340, "top": 180, "right": 460, "bottom": 270}, res
            kind = value(caller.run("result = [doc.activeLayers[0].name, doc.activeLayers[0].kind]"))
            assert kind == ["Patch", "pixel"], kind

        checks.check("import_asset: a small image as pixels, centered", import_patch)

        def import_document():
            res = ok(caller.command("import_asset", {"asset_id": assets["patch"], "as": "document"}))
            assert res["kind"] == "document" and res["document"].startswith("patch"), res
            names = value(caller.run("const out = app.activeDocument.name; await app.activeDocument.closeWithoutSaving(); result = [out, app.activeDocument.name]"))
            assert names[1] == "NOLGIA e2e", names

        checks.check("import_asset: as a new document", import_document)

        def import_refusals():
            cmd = caller.command("import_asset", {"asset_id": "00000000-0000-4000-8000-000000000000"})
            assert cmd["status"] == "failed" and "no asset" in cmd["error"], cmd
            cmd = caller.command("import_asset", {"asset_id": assets["glb"]})
            assert cmd["status"] == "failed" and "3D model" in cmd["error"], cmd

        checks.check("import_asset: unknown and non-image assets fail clearly", import_refusals)

        def export_png_jpg():
            res = ok(caller.command("export", {"format": "png"}))
            assert res["filename"] == "NOLGIA e2e.png" and (res["width"], res["height"]) == (800, 450), res
            assert png_size(caller.asset_bytes(res["asset_id"])) == (800, 450)
            res = ok(caller.command("export", {"format": "jpg", "filename": "e2e final"}))
            assert res["filename"] == "e2e final.jpg", res
            assert jpeg_size(caller.asset_bytes(res["asset_id"])) == (800, 450)
            return "png %s" % res["asset_id"]

        checks.check("export: png and jpg, full size, uploaded", export_png_jpg)

        def export_psd():
            before = len(server.state.uploads) if server else None
            res = ok(caller.command("export", {"format": "psd", "filename": "e2e"}))
            assert res["asset_id"] is None and res["note"].startswith("Photoshop files stay on this computer"), res
            assert res["path"] == exports_win + sep + "e2e.psd", res
            assert os.path.isfile(os.path.join(work, "exports", "e2e.psd"))
            with open(os.path.join(work, "exports", "e2e.psd"), "rb") as handle:
                assert handle.read(4) == b"8BPS"
            again = ok(caller.command("export", {"format": "psd", "filename": "e2e"}))
            assert again["path"] != res["path"] and os.path.isfile(win.to_local(again["path"])), again
            if server:
                assert len(server.state.uploads) == before, "a PSD was uploaded"

        checks.check("export: psd stays local and never overwrites", export_psd)

        saved = files_win + sep + "e2e-saved.psd"

        def save_needs_path():
            cmd = caller.command("save")
            assert cmd["status"] == "failed" and "never been saved" in cmd["error"], cmd
            cmd = caller.command("save", {"path": files_win + sep + "x.png"})
            assert cmd["status"] == "failed" and "export command" in cmd["error"], cmd

        checks.check("save: unsaved needs a path; only .psd/.psb", save_needs_path)

        def save_as():
            res = ok(caller.command("save", {"path": saved}))
            assert res["path"] == saved and res["document"] == "e2e-saved.psd", res
            assert os.path.isfile(os.path.join(work, "files", "e2e-saved.psd"))
            end = time.time() + 10
            while time.time() < end:
                mine = [s for s in caller.sessions() if s["instance_id"] == instance][0]
                if mine["document"].get("name") == "e2e-saved.psd":
                    break
                time.sleep(0.5)
            assert mine["document"] == {"name": "e2e-saved.psd", "path": saved}, mine["document"]

        checks.check("save: to a path; heartbeat names the file", save_as)

        def save_again():
            value(caller.run("await doc.layers.add({ name: 'After save' }); result = 1"))
            assert ok(caller.command("info"))["active_document"]["dirty"] is True
            res = ok(caller.command("save"))
            assert res["path"] == saved, res
            assert ok(caller.command("info"))["active_document"]["dirty"] is False

        checks.check("save: again to the same file", save_again)

        def open_file():
            value(caller.run("await doc.closeWithoutSaving(); result = app.documents.length"))
            res = ok(caller.command("open", {"path": saved}))
            assert res["path"] == saved and res["document"] == "e2e-saved.psd", res
            doc = ok(caller.command("info"))["active_document"]
            def walk(layers):
                for layer in layers:
                    yield layer["name"]
                    yield from walk(layer.get("layers") or [])
            names = set(walk(doc["layers"]))
            assert {"NOLGIA Cleanup", "After save", "Patch", "street plate", "Red box", "Background"} <= names, names
            cmd = caller.command("open", {"path": files_win + sep + "missing.psd"})
            assert cmd["status"] == "failed" and "There is no file" in cmd["error"], cmd

        checks.check("open: a PSD; a missing file fails clearly", open_file)

        def allow_agent():
            status, data = caller.http("POST", "/v1/bridge/commands", {"app": "photoshop", "kind": "info", "session_id": caller.session_id},
                                       headers={"X-Nolgia-Surface": "hermes"})
            assert status == 201, data
            if data["caller"] != "agent":
                caller.wait(data["id"])
                raise Skip("this API does not treat the X-Nolgia-Surface header as the NOLGIA Agent")
            assert caller.wait(data["id"])["status"] == "succeeded"
            value(caller.run("globalThis.__nolgia.controller.setSetting('allow_agent', false); result = 1"))
            try:
                end = time.time() + 10
                while time.time() < end:
                    mine = [s for s in caller.sessions() if s["instance_id"] == instance][0]
                    if mine["allow_agent"] is False:
                        break
                    time.sleep(0.3)
                status, data = caller.http("POST", "/v1/bridge/commands", {"app": "photoshop", "kind": "info", "session_id": caller.session_id},
                                           headers={"X-Nolgia-Surface": "hermes"})
                assert (status, (data or {}).get("code")) == (403, "agent_not_allowed"), (status, data)
            finally:
                value(caller.run("globalThis.__nolgia.controller.setSetting('allow_agent', true); result = 1"))

        checks.check("Allow NOLGIA Agent off: the NOLGIA Agent is refused", allow_agent)

        # Ask before running code. Nothing queued can click a button while a
        # request waits, so one run (made while Ask is off) leaves a listener
        # that answers the next four requests the way a person would: Run
        # code in the panel, Deny in the approval window, no answer, and Run
        # code in the panel again (that last run turns Ask off).
        answers = """
const c = globalThis.__nolgia.controller;
const plan = ['panel-approve', 'window-deny', 'none', 'panel-approve'];
const seen = new Set();
const answer = () => {
  const pending = c.executor.approvals[0];
  if (!pending || seen.has(pending.id) || !plan.length) return;
  seen.add(pending.id);
  const step = plan.shift();
  if (step === 'panel-approve') setTimeout(() => document.getElementById('btn-approve').click(), 1500);
  if (step === 'window-deny') setTimeout(() => {
    const d = document.querySelector('dialog');
    const buttons = d ? Array.from(d.querySelectorAll('.btn')) : [];
    globalThis.__e2eWindow = d ? {
      title: d.querySelector('.approval-title').textContent,
      code: d.querySelector('.code-view').textContent,
      buttons: buttons.map((b) => b.textContent),
    } : null;
    const deny = buttons.find((b) => b.textContent === 'Deny');
    if (deny) deny.click();
  }, 4000);
};
c.onChange(answer);
c.setSetting('ask_before_run', true);
// Safety net: whatever happens, Ask is off again after 90 s.
setTimeout(() => c.setSetting('ask_before_run', false), 90000);
result = c.settings.ask_before_run;
"""

        window = {}

        def approval_panel():
            assert value(caller.run(answers)) is True
            cmd = caller.run("result = 'approved in the panel'")
            assert ok(cmd)["value"] == "approved in the panel", cmd

        checks.check("Ask before running code: Run code in the panel", approval_panel)

        def approval_deny():
            code = "await doc.layers.add({ name: 'Denied' }); result = 1"
            cid = caller.enqueue("run", {"language": "uxp", "code": code}, timeout=25)
            time.sleep(2)
            shot("panel-approval", "", 300)
            cmd = caller.wait(cid)
            assert cmd["status"] == "failed" and "clicked Deny" in cmd["error"], cmd
            window["code"] = code

        checks.check("Ask before running code: Deny in the approval window", approval_deny)

        def approval_timeout():
            cmd = caller.command("run", {"language": "uxp", "code": "result = 1"}, timeout=8)
            assert cmd["status"] == "failed" and "Nobody approved this in Photoshop in time" in cmd["error"], cmd

        checks.check("Ask before running code: nobody answers in time", approval_timeout)

        def approval_off():
            res = value(caller.run("globalThis.__nolgia.controller.setSetting('ask_before_run', false); result = 'off'"))
            assert res == "off", res
            # What the approval window showed when Deny was clicked in it.
            seen = value(caller.run("result = globalThis.__e2eWindow"))
            assert seen == {"title": "Your agent wants to run code in Photoshop", "code": window.get("code"),
                            "buttons": ["Run code", "Deny"]}, seen

        checks.check("Ask before running code: turned off again; the window showed the code", approval_off)

        def panel_draws():
            probe = """
const $ = (id) => document.getElementById(id);
const hidden = (id) => $(id).classList.contains('hidden');
const c = globalThis.__nolgia.controller;
result = {
  chip: $('chip-text').textContent,
  status: $('status').textContent,
  signin: hidden('signin'), login: hidden('login'), account: hidden('account'), approval: hidden('approval'),
  connected: $('tg-connected').classList.contains('on'),
  allow: $('tg-allow-agent').classList.contains('on'),
  ask: $('tg-ask').classList.contains('on'),
  pause: $('btn-pause').textContent,
  rows: document.querySelectorAll('#activity-list .item').length,
  account_line: $('account-line').textContent,
  dev: $('dev-line').textContent,
};
"""
            res = value(caller.run(probe))
            assert res["chip"] == "Connected", res
            assert res["signin"] and res["login"] and not res["account"] and res["approval"], res
            assert res["connected"] and res["allow"] and not res["ask"], res
            assert res["pause"] == "Pause" and res["rows"] == 20, res
            assert res["account_line"].startswith("Signed in as ") or res["account_line"] == "Signed in with a developer token file", res
            assert res["dev"].startswith("Developer file in use: "), res
            shot("panel", "", 300)
            return res["account_line"]

        checks.check("panel: shows the connected state and the activity", panel_draws)

        def panel_buttons():
            # UXP delivers click() a moment later, so wait after each one.
            res = value(caller.run(
                "const c = globalThis.__nolgia.controller;\n"
                "const click = async (id) => { document.getElementById(id).click(); await sleep(300); };\n"
                "await click('tg-allow-agent');\n"
                "const off = c.settings.allow_agent;\n"
                "await click('tg-allow-agent');\n"
                "await click('tg-ask');\n"
                "const ask = c.settings.ask_before_run;\n"
                "await click('tg-ask');\n"
                "result = [off, c.settings.allow_agent, ask, c.settings.ask_before_run];"))
            assert res == [False, True, True, False], res

        checks.check("panel: the toggles change the settings", panel_buttons)

        def cancel_running():
            cid = caller.enqueue("run", {"language": "uxp", "code": "await sleep(4000); result = 1"})
            end = time.time() + 30
            while time.time() < end:
                data = caller.http("GET", "/v1/bridge/commands/%s" % cid)[1]
                if data["status"] == "running":
                    break
                time.sleep(0.1)
            status, data = caller.http("POST", "/v1/bridge/commands/%s/cancel" % cid)
            assert status == 200 and data["status"] == "cancelled", data
            time.sleep(5)
            assert caller.wait(cid)["status"] == "cancelled"
            ok(caller.command("info"))

        checks.check("cancel: a running command, then carry on", cancel_running)

        def close_docs():
            n = value(caller.run("for (const d of Array.from(app.documents)) await d.closeWithoutSaving(); result = app.documents.length"))
            assert n == 0, n

        checks.check("close the test documents", close_docs)

        def pause_and_resume():
            # Pause in the panel, then Resume 8 s later (timers, since nothing
            # reaches the plugin while it is paused).
            value(caller.run("setTimeout(() => document.getElementById('btn-pause').click(), 300);\n"
                             "setTimeout(() => document.getElementById('btn-pause').click(), 8000);\n"
                             "result = 1"))
            end = time.time() + 7
            gone = False
            while time.time() < end:
                if not any(s["instance_id"] == instance for s in caller.sessions()):
                    gone = True
                    break
                time.sleep(0.3)
            assert gone, "still connected after Pause"
            status, data = caller.http("POST", "/v1/bridge/commands", {"app": "photoshop", "kind": "info", "session_id": caller.session_id})
            assert status == 409 and data.get("code") == "app_not_connected", (status, data)
            if server:
                assert caller.session_id in [d["session_id"] for d in server.state.deleted_sessions], "Pause did not close the session"
            # Resume waits until the server lets go of the last long poll
            # (up to 25 s after it began), then registers again.
            wait_session(limit=60)
            ok(caller.command("info"))

        checks.check("Pause and Resume in the panel", pause_and_resume)

        if not opts.skip_sign_in:
            def sign_in_flow():
                url_file = "sign-in-url.txt"
                if os.path.exists(os.path.join(data_dir, url_file)):
                    os.remove(os.path.join(data_dir, url_file))
                code = """
const c = globalThis.__nolgia.controller;
const uxp = require('uxp');
c.host.openUrl = async (url) => {
  const folder = await uxp.storage.localFileSystem.getDataFolder();
  const file = await folder.createFile('%s', { overwrite: true });
  await file.write(url);
};
setTimeout(async () => {
  const worker = c.worker;
  c.disconnect();
  if (worker) await worker.finished.wait(20000);
  c.dev.token = null;
  c.signIn();
}, 500);
result = 'signing in';
""" % url_file
                assert value(caller.run(code)) == "signing in"
                end = time.time() + 60
                url = None
                while time.time() < end:
                    path = os.path.join(data_dir, url_file)
                    if os.path.exists(path):
                        url = open(path, encoding="utf-8").read().strip()
                        if url:
                            break
                    time.sleep(0.5)
                # The mock's page takes ?user_code=, nolgia.ai/device takes ?code=.
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query)
                user_code = (query.get("user_code") or query.get("code") or [None])[0]
                assert user_code, url
                caller.approve_device(user_code)
                old = caller.session_id
                caller.session_id = None
                session2 = wait_session(limit=90)
                res = value(caller.run(
                    "const c = globalThis.__nolgia.controller;\n"
                    "const saved = await require('uxp').storage.secureStorage.getItem('nolgia.token');\n"
                    "result = { dev: c.usingDevToken, email: c.accountEmail(), saved: saved ? saved.length : 0, same: String.fromCharCode.apply(null, Array.from(saved || [])) === c.token(), connected: c.settings.connected };"))
                assert res["dev"] is False and res["saved"] > 20 and res["same"] is True and res["connected"] is True, res
                assert res["email"], res
                os.remove(os.path.join(data_dir, url_file))
                return "code %s, session %s (was %s), signed in as %s" % (user_code, session2["id"][:8], old[:8], res["email"])

            signed_in = checks.check("sign in with the device flow (token in secure storage)", sign_in_flow)

            if signed_in:
                def restart_reconnects():
                    # No token in the developer file now: the saved sign in must do it.
                    write_dev(dict(dev, token=None, autoconnect=False))
                    before = [x for x in caller.sessions() if x["instance_id"] == instance][0]["last_seen_at"]
                    assert close_photoshop(), "Photoshop did not close"
                    caller.session_id = None
                    launch()
                    # Photoshop gives a quitting plugin no time to tell NOLGIA,
                    # so the session looks live for up to a minute, and its last
                    # long poll may still wait on the server for up to 25 s and
                    # take a command meant for the new Photoshop. Wait for the
                    # new Photoshop to check in after that.
                    end = time.time() + 240
                    while time.time() < end:
                        s = wait_session(limit=240)
                        if seconds_between(before, s["last_seen_at"]) > 27:
                            break
                        time.sleep(3)
                    time.sleep(3)
                    win.close_gpu_notice()
                    res = value(caller.run("const c = globalThis.__nolgia.controller; result = [c.usingDevToken, c.signedIn, c.settings.connected]"))
                    assert res == [False, True, True], res
                    return "session %s" % s["id"][:8]

                checks.check("restart: the saved sign in reconnects by itself", restart_reconnects)

                def sign_out():
                    sid = caller.session_id
                    value(caller.run("setTimeout(() => document.getElementById('btn-sign-out').click(), 300); result = 1"))
                    end = time.time() + 20
                    while time.time() < end and any(s["id"] == sid for s in caller.sessions()):
                        time.sleep(0.5)
                    assert not any(s["id"] == sid for s in caller.sessions()), "still connected after Sign out"
                    shot("panel-signed-out", "", 300)
                    time.sleep(8)
                    assert not any(s["instance_id"] == instance for s in caller.sessions()), "reconnected after Sign out"

                checks.check("Sign out: disconnects and forgets the sign in", sign_out)
    except SystemExit:
        pass
    finally:
        if os.path.exists(dev_file):
            os.remove(dev_file)
        if not opts.keep_open and win.photoshop_running():
            if not close_photoshop():
                print("Photoshop did not close by itself; close it by hand.", flush=True)
        if server:
            server.stop()
        shutil.rmtree(win.scratch, ignore_errors=True)
    if shots:
        print("screenshots: " + ", ".join("%s=%s" % kv for kv in sorted(shots.items())))
    with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as handle:
        json.dump({"target": "prod" if opts.prod else "mock", "results": checks.results, "shots": shots}, handle, indent=1)
    return finish(checks, started, opts, win, None)


def finish(checks, started, opts, win, server):
    if server:
        server.stop()
    passed = sum(1 for r in checks.results if r[1] == "PASS")
    skipped = sum(1 for r in checks.results if r[1] == "SKIP")
    print("\n%d passed, %d failed, %d skipped in %.0f s" % (passed, len(checks.failed), skipped, time.time() - started))
    for name, _, why in checks.failed:
        print("failed: %s: %s" % (name, why[:300]))
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
