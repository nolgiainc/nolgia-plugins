#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""End-to-end test: the real extension in a real Blender against the mock API.

1. Builds the extension zip with `blender --command extension build`.
2. Installs it into a throwaway Blender user folder (BLENDER_USER_RESOURCES),
   so your own Blender settings and add-ons are not touched.
3. Starts tools/mock_bridge_server.py in this process.
4. Runs `blender --background --factory-startup` with NOLGIA_TOKEN,
   NOLGIA_API_URL and NOLGIA_BRIDGE_AUTOCONNECT=1, enabling the extension and
   calling serve().
5. Sends commands through the mock's caller API (as the MCP server would)
   and checks every result.
6. Starts Blender twice more to check it refuses to connect headless with
   "Ask before running code" on, and stops when the token is refused.

Usage:
    python3 blender/tests/e2e_blender.py --blender /path/to/blender[.exe]

Works from WSL with a Windows blender.exe: paths are translated with wslpath
and variables passed through WSLENV. BLENDER env var works instead of
--blender.
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
EXT_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(EXT_DIR)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, EXT_DIR)

from mock_bridge_server import MockBridgeServer  # noqa: E402
from core import PLUGIN_VERSION  # noqa: E402
from core.commands import CAPABILITIES  # noqa: E402

TOKEN = "e2e-token"
MODULE = "bl_ext.user_default.nolgia"
BOOTSTRAP = (
    "import addon_utils, sys; "
    "addon_utils.enable('%s', default_set=True, handle_error=None); "
    "import %s as nolgia; "
    "sys.exit(0 if nolgia.serve() else 3)" % (MODULE, MODULE)
)

# Draws the NOLGIA panel into a stand-in layout in every state, checking that
# each button is a registered operator and each toggle a real property.
UI_PROBE = """
import bpy, importlib
ui = importlib.import_module('%s.ui')
runtime = importlib.import_module('%s.runtime')
from %s.core.mainthread import ApprovalRequest, PendingApproval, Ticket
from %s.core.commands import Command
ctl = runtime.controller
labels = []

class Props:
    def __init__(self, idname):
        mod, name = idname.split('.')
        self.__dict__['_rna'] = getattr(getattr(bpy.ops, mod), name).get_rna_type()
    def __setattr__(self, key, value):
        assert key in self._rna.properties.keys(), key

class Layout:
    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name == 'label':
                labels.append(kwargs.get('text', ''))
            if name == 'operator':
                return Props(args[0])
            if name == 'prop':
                assert args[1] in args[0].bl_rna.properties.keys(), args[1]
            if name in ('row', 'column', 'box', 'split'):
                return Layout()
        return call

states = []
ui.draw_nolgia(Layout(), bpy.context); states.append('connected')
ui.draw_nolgia(Layout(), bpy.context, in_prefs=True); states.append('preferences')
cmd = Command({'id': 'probe', 'kind': 'run', 'caller': 'agent', 'args': {'code': 'x = 1\\n' * 10}})
ctl.executor.approvals.append(PendingApproval(Ticket(cmd), ApprovalRequest('NOLGIA Agent wants to run Python in Blender', ['x = 1'] * 10, 'n/a')))
try:
    ui.draw_nolgia(Layout(), bpy.context); states.append('approval')
    assert ctl.status_line() == 'Waiting for you to approve a request.', ctl.status_line()
finally:
    ctl.executor.approvals.clear()
saved = ctl.env_token
ctl.env_token = None
try:
    ui.draw_nolgia(Layout(), bpy.context); states.append('signed out')
    assert 'Not signed in.' in labels or ctl.worker is not None
finally:
    ctl.env_token = saved
wm = bpy.types.WindowManager.bl_rna.functions['invoke_props_dialog'].parameters.keys()
result = {'states': states, 'labels': len(labels), 'dialog_has_title': 'title' in wm and 'confirm_text' in wm}
""" % ((MODULE,) * 4)

# Signs in with the device flow inside Blender, with the browser stubbed out.
SIGN_IN_SCRIPT = """
import addon_utils, json, sys, time, webbrowser
opened = []
webbrowser.open = lambda url, *a, **k: opened.append(url) or True
addon_utils.enable('%s', default_set=True, handle_error=None)
import importlib
runtime = importlib.import_module('%s.runtime')
ctl = runtime.controller
before = ctl.signed_in
ctl.sign_in()
end = time.time() + 40
while time.time() < end and not (ctl.worker is not None and ctl.worker.state == 'connected'):
    ctl.tick()
    time.sleep(0.05)
prefs = ctl.prefs()
report = {'before': before, 'opened': opened, 'token': prefs.token[:9], 'email': prefs.account_email,
          'connected': prefs.connected, 'state': ctl.worker.state if ctl.worker else None,
          'status': ctl.status_line()}
ctl.sign_out()
while ctl.worker is not None and time.time() < end + 10:
    ctl.tick()
    time.sleep(0.05)
report['after_sign_out'] = {'token': prefs.token, 'connected': prefs.connected, 'status': ctl.status_line()}
print('E2E_SIGN_IN ' + json.dumps(report), flush=True)
sys.exit(0)
""" % (MODULE, MODULE)


# ------------------------------------------------------------------ paths


class Host:
    """Runs Blender, translating paths when a Windows blender.exe runs from WSL."""

    def __init__(self, blender):
        self.blender = blender
        self.wsl = blender.lower().endswith(".exe") and os.path.exists("/proc/version") \
            and "microsoft" in open("/proc/version").read().lower()

    def to_blender(self, path):
        if not self.wsl:
            return path
        return subprocess.check_output(["wslpath", "-w", path], text=True).strip()

    def from_blender(self, path):
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

    def run(self, args, extra_env=None, timeout=300, **kwargs):
        return subprocess.run([self.blender] + args, env=self.env(extra_env or {}), timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              errors="replace", **kwargs)

    def popen(self, args, extra_env, log_path):
        log = open(log_path, "w", encoding="utf-8", errors="replace")
        return subprocess.Popen([self.blender] + args, env=self.env(extra_env), stdout=log,
                                stderr=subprocess.STDOUT)


# --------------------------------------------------------------- fixtures


def make_glb():
    """A valid glTF binary with one triangle named NolgiaTriangle."""
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"
    blob = positions + indices
    doc = {
        "asset": {"version": "2.0", "generator": "nolgia e2e"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "NolgiaTriangle"}],
        "meshes": [{"name": "NolgiaTriangleMesh",
                    "primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36, "target": 34962},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }
    js = json.dumps(doc).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\x00") + blob)


def make_png(width=16, height=8):
    raw = b"".join(b"\x00" + bytes([230, 40, 40]) * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
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

    def enqueue(self, kind, args=None, timeout=120):
        status, data = self.http("POST", "/v1/bridge/commands",
                                 {"app": "blender", "kind": kind, "args": args or {}, "timeout_seconds": timeout})
        assert status == 201, "enqueue %s: %s %s" % (kind, status, data)
        return data["id"]

    def wait(self, command_id, limit=300):
        end = time.time() + limit
        while True:
            status, data = self.http("GET", "/v1/bridge/commands/%s?wait=25" % command_id)
            assert status == 200, data
            cmd = data
            if cmd["status"] not in ("queued", "running") or time.time() > end:
                return cmd

    def command(self, kind, args=None, timeout=120):
        return self.wait(self.enqueue(kind, args, timeout))

    def state(self):
        return self.http("GET", "/mock/state", auth=False)[1]

    def asset_bytes(self, asset_id):
        status, data = self.http("GET", "/mock/assets/%s/bytes" % asset_id, auth=False, raw=True)
        assert status == 200, status
        return data

    def seed_asset(self, filename, content_type, data):
        status, asset = self.http(
            "POST", "/mock/assets?filename=%s&content_type=%s" % (filename, content_type), data, auth=False)
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
            print("FAIL %-44s %.1fs  %s: %s" % (name, time.time() - started, type(err).__name__, err), flush=True)
            return False
        self.passed.append(name)
        print("PASS %-44s %.1fs" % (name, time.time() - started), flush=True)
        return True


def expect_ok(cmd):
    assert cmd["status"] == "succeeded", "status %s, error: %s" % (cmd["status"], cmd.get("error"))
    return cmd["result"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    parser.add_argument("--workdir", default=None, help="scratch folder Blender can reach (default: a temp dir)")
    parser.add_argument("--keep", action="store_true", help="keep the scratch folder")
    opts = parser.parse_args()

    host = Host(opts.blender)
    work = opts.workdir or tempfile.mkdtemp(prefix="nolgia-e2e-")
    work = os.path.abspath(work)
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    user_dir = os.path.join(work, "blender-user")
    dist = os.path.join(work, "dist")
    files = os.path.join(work, "files")
    exports = os.path.join(work, "exports")
    # Blender ignores BLENDER_USER_RESOURCES (and loads your own settings and
    # add-ons) when the folder does not exist yet, so create it first.
    for folder in (user_dir, dist, files):
        os.makedirs(folder)
    started = time.time()
    checks = Checks()
    print("scratch folder: %s" % work, flush=True)

    base_env = {"BLENDER_USER_RESOURCES": host.to_blender(user_dir)}

    # 1-2: build and install
    def build_and_install():
        out = host.run(["--command", "extension", "build", "--source-dir", host.to_blender(EXT_DIR),
                        "--output-dir", host.to_blender(dist)], base_env)
        assert out.returncode == 0, out.stdout[-2000:]
        zips = [f for f in os.listdir(dist) if f.endswith(".zip")]
        assert zips == ["nolgia-%s.zip" % PLUGIN_VERSION], zips
        out = host.run(["--command", "extension", "install-file", "-r", "user_default", "-e",
                        host.to_blender(os.path.join(dist, zips[0]))], base_env)
        assert out.returncode == 0, out.stdout[-2000:]
        installed = os.path.join(user_dir, "extensions", "user_default", "nolgia", "blender_manifest.toml")
        assert os.path.isfile(installed), "not installed in the scratch Blender folder: " + out.stdout[-2000:]

    if not checks.check("build and install the extension", build_and_install):
        return finish(checks, work, opts, started)

    server = MockBridgeServer(tokens=(TOKEN,)).start()
    caller = Caller(server.root_url, TOKEN)
    env = dict(base_env, NOLGIA_TOKEN=TOKEN, NOLGIA_API_URL=server.base_url,
               NOLGIA_BRIDGE_AUTOCONNECT="1", NOLGIA_ASK_BEFORE_RUN="0",
               NOLGIA_EXPORT_DIR=host.to_blender(exports))
    log_path = os.path.join(work, "blender.log")
    proc = host.popen(["--background", "--factory-startup", "--python-exit-code", "4",
                       "--python-expr", BOOTSTRAP], env, log_path)
    session = {}
    made = {}

    try:
        def connects():
            end = time.time() + 90
            while time.time() < end:
                status, data = caller.http("GET", "/v1/bridge/sessions")
                if status == 200 and data["sessions"]:
                    session.update(data["sessions"][0])
                    break
                assert proc.poll() is None, "Blender exited early:\n" + open(log_path).read()[-3000:]
                time.sleep(0.5)
            assert session, "no session within 90 s"
            assert session["app"] == "blender"
            assert session["capabilities"] == list(CAPABILITIES), session["capabilities"]
            assert session["plugin_version"] == PLUGIN_VERSION
            assert session["app_version"].startswith("4."), session["app_version"]
            assert session["document"] == {"name": ""}, session["document"]  # unsaved: empty name
            assert session["allow_agent"] is True
            assert session["machine_name"]
            assert session["instance_id"]

        if not checks.check("connects and registers a session", connects):
            raise SystemExit

        def info():
            res = expect_ok(caller.command("info"))
            assert res["document"]["name"] == ""
            assert res["scene"]["name"] == "Scene"
            assert (res["scene"]["frame_start"], res["scene"]["frame_end"]) == (1, 250)
            assert res["scene"]["fps"] == 24
            assert res["render"]["resolution_x"] == 1920
            assert {"name": "Camera", "active": True} in res["cameras"]
            assert {"name": "Collection", "objects": 3} in res["collections"]
            assert res["selected_objects"] == ["Cube"]

        checks.check("info", info)

        def panel_draws():
            res = expect_ok(caller.command("run", {"language": "python", "code": UI_PROBE}))
            value = res["value"]
            assert value["states"] == ["connected", "preferences", "approval", "signed out"], value
            assert value["labels"] > 10 and value["dialog_has_title"], value

        checks.check("panel draws in every state", panel_draws)

        def run_ok():
            code = "\n".join([
                "import bpy",
                "bpy.ops.mesh.primitive_cube_add(size=1.5, location=(3, 0, 0))",
                "cube = bpy.context.active_object",
                "cube.name = 'NolgiaCube'",
                "mat = bpy.data.materials.new('NolgiaRed')",
                "mat.use_nodes = True",
                "mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (1, 0, 0, 1)",
                "cube.data.materials.append(mat)",
                "print('hello from blender')",
                "result = {'name': cube.name, 'material': cube.active_material.name,",
                "          'location': cube.location, 'objects': len(bpy.data.objects)}",
            ])
            res = expect_ok(caller.command("run", {"language": "python", "code": code}))
            assert res["value"] == {"name": "NolgiaCube", "material": "NolgiaRed",
                                    "location": [3.0, 0.0, 0.0], "objects": 4}, res["value"]
            assert "hello from blender" in res["stdout"], res
            assert res["stderr"] == "", res["stderr"]

        checks.check("run: cube, material, return value", run_ok)

        def run_fails():
            cmd = caller.command("run", {"language": "python",
                                         "code": "x = 1\nraise ValueError('boom from the test')\n"})
            assert cmd["status"] == "failed", cmd
            err = cmd["error"]
            assert "Traceback (most recent call last)" in err, err
            assert "ValueError: boom from the test" in err, err
            assert "line 2" in err and "raise ValueError('boom from the test')" in err, err
            assert "pyexec" not in err, "plugin frames leak into the traceback:\n" + err
            assert cmd["result"] == {"value": None, "stdout": "", "stderr": ""}, cmd["result"]

        checks.check("run: failure returns the traceback", run_fails)

        def run_timeout():
            t0 = time.time()
            cmd = caller.command("run", {"language": "python", "timeout_seconds": 2,
                                         "code": "import time\nwhile True:\n    time.sleep(0.01)\n"})
            assert cmd["status"] == "failed", cmd
            assert "longer than 2 seconds" in cmd["error"], cmd["error"]
            assert time.time() - t0 < 20

        checks.check("run: timeout stops a runaway loop", run_timeout)

        def run_other_language():
            cmd = caller.command("run", {"language": "extendscript", "code": "alert(1)"})
            assert cmd["status"] == "failed" and "Python only" in cmd["error"], cmd

        checks.check("run: refuses other languages", run_other_language)

        def preview():
            res = expect_ok(caller.command("preview", {"width": 320, "frame": 1}))
            assert (res["width"], res["height"]) == (320, 180), res
            assert res["camera"] == "Camera" and res["frame"] == 1, res
            data = caller.asset_bytes(res["asset_id"])
            assert png_size(data) == (320, 180), png_size(data)

        checks.check("preview: renders, uploads a PNG", preview)

        def preview_too_big_for_inline():
            # Lower the inline limit for this one preview: the PNG is over it,
            # so the plugin sends a JPEG instead.
            code = "import os\nos.environ['NOLGIA_PREVIEW_MAX_BYTES'] = '%s'"
            expect_ok(caller.command("run", {"language": "python", "code": code % "200000"}))
            try:
                res = expect_ok(caller.command("preview", {"width": 1920}))
            finally:
                expect_ok(caller.command("run", {"language": "python", "code": code % ""}))
            assert res["mime_type"] == "image/jpeg" and (res["width"], res["height"]) == (1920, 1080), res
            data = caller.asset_bytes(res["asset_id"])
            assert data[:3] == b"\xff\xd8\xff" and len(data) <= 200000, (data[:4], len(data))
            up = [u for u in caller.state()["uploads"] if u["asset_id"] == res["asset_id"]][0]
            assert up["content_type"] == "image/jpeg" and up["filename"].endswith(".jpg"), up

        checks.check("preview: JPEG when the PNG is too big to show", preview_too_big_for_inline)

        def preview_bad_width():
            cmd = caller.command("preview", {"width": 5000})
            assert cmd["status"] == "failed" and "at most 1920" in cmd["error"], cmd

        checks.check("preview: checks its arguments", preview_bad_width)

        def storage_without_token():
            reqs = caller.state()["requests"]
            storage = [r for r in reqs if r["path"].startswith("/storage/")]
            assert storage, "no storage requests seen"
            assert not any(r["auth"] for r in storage), "bearer token sent to a signed URL"
            assert all(r["status"] == 200 for r in storage), storage

        checks.check("uploads never send the token to storage", storage_without_token)

        glb_id = caller.seed_asset("triangle.glb", "model/gltf-binary", make_glb())
        png_id = caller.seed_asset("red.png", "image/png", make_png())

        def import_glb():
            res = expect_ok(caller.command("import_asset", {"asset_id": glb_id}))
            assert res["kind"] == "model", res
            assert "NolgiaTriangle" in res["imported"], res

        checks.check("import_asset: GLB as objects", import_glb)

        def import_png():
            res = expect_ok(caller.command("import_asset", {"asset_id": png_id}))
            assert res["kind"] == "image" and len(res["imported"]) == 1, res
            plane = res["imported"][0]
            check = caller.command("run", {"language": "python", "code":
                "import bpy\no = bpy.data.objects[%r]\n"
                "img = o.active_material.node_tree.nodes['Image Texture'].image\n"
                "result = [o.type, img.size[:], img.packed_file is not None]" % plane})
            assert expect_ok(check)["value"] == ["MESH", [16, 8], True], check

        checks.check("import_asset: image as a plane", import_png)

        def import_texture():
            res = expect_ok(caller.command("import_asset", {"asset_id": png_id, "as": "texture"}))
            assert res["kind"] == "image" and res["imported"][0].startswith("red"), res

        checks.check("import_asset: image as a texture", import_texture)

        def import_missing():
            cmd = caller.command("import_asset", {"asset_id": "00000000-0000-4000-8000-000000000000"})
            assert cmd["status"] == "failed" and "no asset" in cmd["error"], cmd

        checks.check("import_asset: unknown asset fails clearly", import_missing)

        def export_glb():
            res = expect_ok(caller.command("export", {"format": "glb"}))
            data = caller.asset_bytes(res["asset_id"])
            assert data[:4] == b"glTF", data[:16]
            assert b"NolgiaCube" in data, "cube missing from the GLB"
            up = [u for u in caller.state()["uploads"] if u["asset_id"] == res["asset_id"]][0]
            assert up["content_type"] == "model/gltf-binary", up

        checks.check("export: glb", export_glb)

        def export_blend_unsaved():
            uploads = len(caller.state()["uploads"])
            res = expect_ok(caller.command("export", {"format": "blend", "filename": "shot"}))
            assert res["asset_id"] is None and res["note"].startswith("Blender files stay on this computer"), res
            local = host.from_blender(res["path"])
            assert local == os.path.join(exports, "shot.blend"), local
            with open(local, "rb") as handle:
                head = handle.read(7)
            assert head == b"BLENDER" or head[:4] == b"\x28\xb5\x2f\xfd", head
            again = expect_ok(caller.command("export", {"format": "blend", "filename": "shot"}))
            assert again["path"] != res["path"] and os.path.isfile(host.from_blender(again["path"])), again
            assert len(caller.state()["uploads"]) == uploads, "a .blend was uploaded"

        checks.check("export: blend stays local, never overwrites", export_blend_unsaved)

        def export_png_and_mp4():
            expect_ok(caller.command("run", {"language": "python", "code":
                "import bpy\ns = bpy.context.scene\ns.render.resolution_percentage = 10\n"
                "s.render.engine = 'BLENDER_WORKBENCH'"}))
            res = expect_ok(caller.command("export", {"format": "png", "frames": "1"}))
            assert png_size(caller.asset_bytes(res["asset_id"])) == (192, 108)
            res = expect_ok(caller.command("export", {"format": "mp4", "frames": "1-6"}))
            made["mp4"] = res["asset_id"]
            data = caller.asset_bytes(res["asset_id"])
            assert data[4:8] == b"ftyp" and b"avc1" in data[:4096] + data[-65536:], data[:32]
            after = expect_ok(caller.command("run", {"language": "python", "code":
                "import bpy\ns = bpy.context.scene\n"
                "result = [s.render.image_settings.file_format, s.frame_start, s.frame_end, s.render.filepath]"}))
            assert after["value"][:3] == ["PNG", 1, 250], "settings not restored: %s" % after["value"]

        checks.check("export: png and mp4 (H.264), settings restored", export_png_and_mp4)

        def save_needs_path():
            cmd = caller.command("save")
            assert cmd["status"] == "failed" and "never been saved" in cmd["error"], cmd

        checks.check("save: untitled file needs a path", save_needs_path)

        saved_path = os.path.join(files, "saved.blend")

        def save_as():
            res = expect_ok(caller.command("save", {"path": host.to_blender(saved_path)}))
            assert os.path.normcase(res["path"]) == os.path.normcase(host.to_blender(saved_path)), res
            assert os.path.isfile(saved_path)
            end = time.time() + 10
            while time.time() < end:  # save_post sends a heartbeat at once
                hb = caller.state()["last_heartbeat"]
                if hb["document"].get("name") == "saved.blend":
                    break
                time.sleep(0.2)
            assert hb["document"] == {"name": "saved.blend", "path": res["path"]}, hb["document"]

        checks.check("save: to a path, heartbeat names the file", save_as)

        def open_refuses_unsaved():
            expect_ok(caller.command("run", {"language": "python", "code":
                "import bpy\nbpy.data.objects.new('NolgiaEmpty', None)\n"
                "bpy.context.scene.collection.objects.link(bpy.data.objects['NolgiaEmpty'])"}))
            cmd = caller.command("open", {"path": host.to_blender(saved_path)})
            assert cmd["status"] == "failed" and "unsaved changes" in cmd["error"], cmd

        checks.check("open: refuses over unsaved changes headless", open_refuses_unsaved)

        def save_and_open():
            res = expect_ok(caller.command("save"))
            assert os.path.normcase(res["path"]) == os.path.normcase(host.to_blender(saved_path)), res
            res = expect_ok(caller.command("open", {"path": host.to_blender(saved_path)}))
            info_ = expect_ok(caller.command("info"))
            assert info_["document"]["name"] == "saved.blend" and info_["document"]["dirty"] is False, info_
            names = expect_ok(caller.command("run", {"language": "python", "code":
                "import bpy\nresult = sorted(o.name for o in bpy.data.objects)"}))["value"]
            assert "NolgiaCube" in names and "NolgiaEmpty" in names and "NolgiaTriangle" in names, names

        checks.check("save, then open the saved file", save_and_open)

        def export_blend_next_to_file():
            res = expect_ok(caller.command("export", {"format": "blend"}))
            assert host.from_blender(res["path"]) == os.path.join(files, "saved-copy.blend"), res
            info_ = expect_ok(caller.command("info"))
            assert info_["document"]["name"] == "saved.blend", "the copy replaced the open file"

        checks.check("export: blend copy next to the saved file", export_blend_next_to_file)

        def allow_agent_toggle():
            prefs = "bpy.context.preferences.addons[%r].preferences" % MODULE
            status, data = caller.http("POST", "/v1/bridge/commands", {"app": "blender", "kind": "info"},
                                       headers={"X-Nolgia-Surface": "hermes"})
            assert status == 201 and data["caller"] == "agent", data
            assert caller.wait(data["id"])["status"] == "succeeded"
            expect_ok(caller.command("run", {"language": "python", "code":
                "import bpy\n%s.allow_agent = False" % prefs}))
            end = time.time() + 10
            while time.time() < end and caller.state()["last_heartbeat"]["allow_agent"] is not False:
                time.sleep(0.2)
            status, data = caller.http("POST", "/v1/bridge/commands", {"app": "blender", "kind": "info"},
                                       headers={"X-Nolgia-Surface": "hermes"})
            assert (status, data.get("code")) == (403, "agent_not_allowed"), (status, data)
            expect_ok(caller.command("run", {"language": "python", "code":
                "import bpy\n%s.allow_agent = True" % prefs}))

        checks.check("Allow NOLGIA Agent off: agent refused", allow_agent_toggle)

        def import_video():
            res = expect_ok(caller.command("import_asset", {"asset_id": made["mp4"]}))
            assert res["kind"] == "video" and len(res["imported"]) == 1, res
            local = host.from_blender(res["path"])
            assert os.path.isfile(local), local
            assert os.path.dirname(os.path.dirname(local)) == os.path.join(files, "nolgia_assets"), local
            clip = expect_ok(caller.command("run", {"language": "python", "code":
                "import bpy\nc = bpy.data.movieclips[%r]\nresult = [c.frame_duration, c.size[:]]" % res["imported"][0]}))
            assert clip["value"] == [6, [192, 108]], clip

        checks.check("import_asset: video as a clip, kept by the .blend", import_video)

        def cancel_running():
            cid = caller.enqueue("run", {"language": "python", "code": "import time\ntime.sleep(3)\nresult = 1"})
            end = time.time() + 30
            while time.time() < end:
                status, data = caller.http("GET", "/v1/bridge/commands/%s" % cid)
                if data["status"] == "running":
                    break
                time.sleep(0.1)
            status, data = caller.http("POST", "/v1/bridge/commands/%s/cancel" % cid)
            assert status == 200, data
            time.sleep(4)
            assert caller.wait(cid)["status"] == "cancelled"
            expect_ok(caller.command("info"))  # still working afterwards

        checks.check("cancel: running command, plugin carries on", cancel_running)

        def disconnects():
            sid = caller.http("GET", "/v1/bridge/sessions")[1]["sessions"][0]["id"]
            res = expect_ok(caller.command("run", {"language": "python", "code":
                "import %s as nolgia\nnolgia.disconnect()\nresult = 'bye'" % MODULE}))
            assert res["value"] == "bye"
            code = proc.wait(timeout=40)
            assert code == 0, "Blender exit code %s" % code
            deleted = [d["session_id"] for d in caller.state()["deleted_sessions"]]
            assert sid in deleted, deleted
            assert caller.http("GET", "/v1/bridge/sessions")[1]["sessions"] == []

        checks.check("switch off: result sent, DELETE, clean exit", disconnects)
    except SystemExit:
        pass
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    def refuses_headless_ask():
        beats = caller.state()["heartbeats"]
        out = host.run(["--background", "--factory-startup", "--python-expr", BOOTSTRAP],
                       dict(env, NOLGIA_ASK_BEFORE_RUN="1"), timeout=120)
        assert out.returncode == 3, "exit %s\n%s" % (out.returncode, out.stdout[-2000:])
        assert "Ask before running code is on" in out.stdout, out.stdout[-2000:]
        assert caller.state()["heartbeats"] == beats, "it registered anyway"

    checks.check("headless with Ask before running code: refuses", refuses_headless_ask)

    def bad_token():
        out = host.run(["--background", "--factory-startup", "--python-expr", BOOTSTRAP],
                       dict(env, NOLGIA_TOKEN="not-a-real-token"), timeout=120)
        assert out.returncode == 3, "exit %s\n%s" % (out.returncode, out.stdout[-2000:])
        assert "did not accept your sign in" in out.stdout, out.stdout[-2000:]

    checks.check("refused token: stops and says so", bad_token)

    def device_sign_in():
        script = os.path.join(work, "sign_in.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(SIGN_IN_SCRIPT)
        server.state.device_interval = 1
        server.state.auto_approve_after = 1
        beats_before = len(server.state.heartbeats)
        deletes_before = len(server.state.deleted_sessions)
        signin_env = {k: v for k, v in env.items() if k != "NOLGIA_TOKEN"}
        signin_env["NOLGIA_TOKEN"] = ""
        out = host.run(["--background", "--factory-startup", "--python", host.to_blender(script)],
                       signin_env, timeout=120)
        line = next((l for l in out.stdout.splitlines() if l.startswith("E2E_SIGN_IN ")), None)
        assert out.returncode == 0 and line, "exit %s\n%s" % (out.returncode, out.stdout[-3000:])
        report = json.loads(line[len("E2E_SIGN_IN "):])
        assert report["before"] is False, report
        assert len(report["opened"]) == 1 and "/device?user_code=" in report["opened"][0], report
        assert report["token"].startswith("nol_mock_"), report
        assert report["email"] == "test@nolgia.ai" and report["connected"] is True, report
        assert report["state"] == "connected", report
        assert report["after_sign_out"] == {"token": "", "connected": False, "status": "Signed out."}, report
        # Same user and same install, so the API upserts the same session.
        assert len(server.state.heartbeats) > beats_before, "no heartbeat after signing in"
        assert len(server.state.deleted_sessions) == deletes_before + 1, "sign out did not close the session"

    checks.check("sign in with the device flow, then sign out", device_sign_in)

    server.stop()
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
