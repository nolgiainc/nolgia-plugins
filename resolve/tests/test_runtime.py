# SPDX-License-Identifier: GPL-3.0-or-later
"""The whole plugin without Resolve: the controller with the fake Resolve
objects, serving commands from the mock API the way serve() does next to a
real Resolve."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import support
import fake_resolve as fake

from nolgia_resolve import still
from nolgia_resolve.core import PLUGIN_VERSION
from nolgia_resolve.core.commands import CAPABILITIES
from nolgia_resolve.runtime import Controller
from nolgia_resolve.settings import Settings


def until(predicate, timeout=15.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("condition not met within %.1fs" % timeout)


class Harness:
    """Runs Controller.serve() on its own thread, which plays Resolve's
    script thread (the only one that touches the fake Resolve)."""

    def __init__(self, server, tmp, resolve=None, env=None, settings=None):
        self.resolve = resolve or fake.sample()
        self.env = {
            "NOLGIA_TOKEN": support.TOKEN,
            "NOLGIA_API_URL": server.base_url,
            "NOLGIA_CONFIG_DIR": os.path.join(tmp, "config"),
            "NOLGIA_ASK_BEFORE_RUN": "0",
        }
        self.env.update(env or {})
        self.settings = settings
        self.lines = []
        self.controller = None
        self.result = None
        self.main_thread = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._main, daemon=True)

    def _main(self):
        self.main_thread = threading.get_ident()
        self.controller = Controller(self.resolve, settings=self.settings, has_window=False, env=self.env,
                                     log=self.lines.append)
        self.ready.set()
        self.result = self.controller.serve(poll=0.005)

    def start(self):
        self.thread.start()
        self.ready.wait(5)
        return self

    def stop(self):
        if self.controller is not None and self.thread.is_alive():
            self.controller.events.put(self.controller.disconnect)
        self.thread.join(15)


class ServeTest(unittest.TestCase):
    def setUp(self):
        self.server = support.start_mock(poll_wait_seconds=2)
        self.tmp = tempfile.mkdtemp(prefix="nolgia-runtime-")
        self.env = mock.patch.dict(os.environ, {
            "NOLGIA_LUT_DIR": os.path.join(self.tmp, "LUT"),
            "NOLGIA_IMPORT_DIR": os.path.join(self.tmp, "imports"),
        })
        self.env.start()
        self.h = None

    def tearDown(self):
        if self.h is not None:
            self.h.stop()
        self.env.stop()
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, **kwargs):
        self.h = Harness(self.server, self.tmp, **kwargs).start()
        until(lambda: support.call(self.server, "GET", "/v1/bridge/sessions")[1]["sessions"])
        return self.h

    def command(self, kind, args=None, timeout=60, caller=None):
        headers = {"X-Nolgia-Surface": "hermes"} if caller == "agent" else None
        status, data = support.call(self.server, "POST", "/v1/bridge/commands",
                                    {"app": "resolve", "kind": kind, "args": args or {}, "timeout_seconds": timeout},
                                    headers=headers)
        self.assertEqual(status, 201, data)
        end = time.time() + timeout + 10
        while time.time() < end:
            status, cmd = support.call(self.server, "GET", "/v1/bridge/commands/%s?wait=10" % data["id"])
            if cmd["status"] not in ("queued", "running"):
                return cmd
        self.fail("command %s did not finish" % kind)

    def ok(self, kind, args=None, **kwargs):
        cmd = self.command(kind, args, **kwargs)
        self.assertEqual(cmd["status"], "succeeded", cmd.get("error"))
        return cmd["result"]

    def asset_bytes(self, asset_id):
        status, data = support.call(self.server, "GET", "/mock/assets/%s/bytes" % asset_id, token=None)
        self.assertEqual(status, 200)
        return data

    def state(self):
        return support.call(self.server, "GET", "/mock/state", token=None)[1]

    def test_session_and_every_command(self):
        self.start()
        session = support.call(self.server, "GET", "/v1/bridge/sessions")[1]["sessions"][0]
        self.assertEqual(session["app"], "resolve")
        self.assertEqual(session["capabilities"], list(CAPABILITIES))
        self.assertEqual(session["plugin_version"], PLUGIN_VERSION)
        self.assertEqual(session["app_version"], "21.1.1.7")
        self.assertEqual(session["document"], {"name": "Rooftop Story"})

        info = self.ok("info")
        self.assertEqual(info["timeline"]["name"], "Edit 1")

        run = self.ok("run", {"language": "python", "code": "print('x')\nresult = timeline.GetName()"})
        self.assertEqual((run["value"], run["stdout"]), ("Edit 1", "x\n"))

        prev = self.ok("preview", {"width": 320, "frame": 3})
        self.assertEqual(still.png_size(self.asset_bytes(prev["asset_id"])), (320, 180))
        self.assertEqual(prev["mime_type"], "image/png")

        png_id = support.call(self.server, "POST", "/mock/assets?filename=red.png&content_type=image/png",
                              fake.make_bmp(1, 1), token=None)[1]["id"]
        imp = self.ok("import_asset", {"asset_id": png_id, "append": True})
        self.assertEqual(imp["kind"], "image")
        self.assertEqual(imp["appended"]["timeline"], "Edit 1")
        self.assertTrue(imp["path"].startswith(os.path.join(self.tmp, "imports", "Rooftop Story")))

        lut = self.ok("import_asset", {"color_preset": "kodak-portra-400", "apply_to": "all"})
        self.assertEqual(lut["path"], os.path.join(self.tmp, "LUT", "NOLGIA", "Kodak Portra 400.cube"))
        with open(lut["path"], "rb") as handle:
            self.assertTrue(handle.read().startswith(b'TITLE "Nolgia Kodak Portra 400"'))
        self.assertEqual(len(lut["applied_to"]), 3)

        missing = self.command("import_asset", {"color_preset": "no-such-look"})
        self.assertEqual(missing["status"], "failed")
        self.assertIn("no color preset named no-such-look", missing["error"])

        mp4 = self.ok("export", {"format": "mp4", "frames": "0-47", "filename": "cut"})
        self.assertEqual(self.asset_bytes(mp4["asset_id"])[4:8], b"ftyp")
        self.assertEqual(mp4["filename"], "cut.mp4")
        png = self.ok("export", {"format": "png", "frames": "5"})
        self.assertEqual(still.png_size(self.asset_bytes(png["asset_id"])), (1920, 1080))

        self.assertEqual(self.ok("save"), {"project": "Rooftop Story", "saved": True})
        self.h.resolve.pm.add("Second Film")
        self.assertEqual(self.ok("open", {"project": "Second Film"})["project"], "Second Film")
        until(lambda: self.state()["last_heartbeat"]["document"] == {"name": "Second Film"})

        storage = [r for r in self.state()["requests"] if r["path"].startswith("/storage/")]
        self.assertTrue(storage)
        self.assertFalse(any(r["auth"] for r in storage), "token sent to a signed URL")

    def test_open_refused_headless_after_unsaved_changes(self):
        self.start()
        self.h.resolve.pm.add("Other")
        self.ok("run", {"code": "x = 1"})
        cmd = self.command("open", {"project": "Other"})
        self.assertEqual(cmd["status"], "failed")
        self.assertIn("Save first", cmd["error"])

    def test_allow_agent_off(self):
        self.start()
        ctl = self.h.controller
        self.assertEqual(self.command("info", caller="agent")["status"], "succeeded")
        ctl.events.put(lambda: ctl.set_setting("allow_agent", False))
        until(lambda: self.state()["last_heartbeat"]["allow_agent"] is False)
        status, data = support.call(self.server, "POST", "/v1/bridge/commands", {"app": "resolve", "kind": "info"},
                                    headers={"X-Nolgia-Surface": "hermes"})
        self.assertEqual((status, data["code"]), (403, "agent_not_allowed"))
        with open(os.path.join(self.tmp, "config", "settings.json")) as handle:
            self.assertIs(json.load(handle)["allow_agent"], False)

    def test_switch_off_closes_the_session(self):
        self.start()
        sid = support.call(self.server, "GET", "/v1/bridge/sessions")[1]["sessions"][0]["id"]
        self.h.stop()
        self.assertIs(self.h.result, True)
        self.assertIn(sid, [d["session_id"] for d in self.state()["deleted_sessions"]])

    def test_resolve_closing_stops_serving(self):
        self.start()
        self.h.controller._last_alive = 0
        self.h.resolve.alive = False
        self.h.thread.join(20)
        self.assertFalse(self.h.thread.is_alive())
        self.assertIs(self.h.result, True)
        self.assertIn("DaVinci Resolve closed; switching off.", self.h.lines)

    def test_refuses_with_ask_before_running_code(self):
        h = Harness(self.server, self.tmp, env={"NOLGIA_ASK_BEFORE_RUN": "1"}).start()
        h.thread.join(10)
        self.assertIs(h.result, False)
        self.assertTrue(any("Ask before running code is on" in line for line in h.lines))
        self.assertEqual(self.state()["heartbeats"], 0)

    def test_refused_token_stops(self):
        h = Harness(self.server, self.tmp, env={"NOLGIA_TOKEN": "not-a-token"}).start()
        h.thread.join(20)
        self.assertIs(h.result, False)
        self.assertTrue(any("did not accept your sign in" in line for line in h.lines))

    def test_cancel_while_running(self):
        self.start()
        status, data = support.call(self.server, "POST", "/v1/bridge/commands",
                                    {"app": "resolve", "kind": "run", "args": {"code": "import time\ntime.sleep(2)"}})
        until(lambda: support.call(self.server, "GET", "/v1/bridge/commands/" + data["id"])[1]["status"] == "running")
        support.call(self.server, "POST", "/v1/bridge/commands/%s/cancel" % data["id"])
        time.sleep(2.5)
        self.assertEqual(support.call(self.server, "GET", "/v1/bridge/commands/" + data["id"])[1]["status"], "cancelled")
        self.ok("info")


class SignIn(unittest.TestCase):
    def setUp(self):
        self.server = support.start_mock(poll_wait_seconds=2, device_interval=1, auto_approve_after=1)
        self.tmp = tempfile.mkdtemp(prefix="nolgia-signin-")
        self.env = {"NOLGIA_API_URL": self.server.base_url, "NOLGIA_CONFIG_DIR": self.tmp}

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tick_until(self, ctl, predicate, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            ctl.tick()
            if predicate():
                return
            time.sleep(0.02)
        self.fail("timed out: %s" % ctl.status_line())

    def test_device_flow_saves_the_sign_in_then_sign_out(self):
        opened = []
        ctl = Controller(fake.sample(), has_window=True, env=self.env, log=lambda m: None, open_url=opened.append)
        self.assertFalse(ctl.signed_in)
        self.assertEqual(ctl.status_line(), "Not signed in.")
        ctl.sign_in()
        self.tick_until(ctl, lambda: ctl.worker is not None and ctl.worker.state == "connected")
        self.assertEqual(len(opened), 1)
        self.assertIn("/device?user_code=", opened[0])
        saved = Settings(self.tmp)
        self.assertTrue(saved["token"].startswith("nol_mock_"))
        self.assertEqual(saved["token_api_url"], self.server.base_url)
        self.assertTrue(saved["connected"])
        self.tick_until(ctl, lambda: ctl.account_email() == "test@nolgia.ai")
        if os.name != "nt":
            self.assertEqual(os.stat(saved.path).st_mode & 0o777, 0o600)
        ctl.sign_out()
        self.tick_until(ctl, lambda: ctl.worker is None)
        saved.load()
        self.assertEqual((saved["token"], saved["connected"]), ("", False))
        self.assertEqual(ctl.status_line(), "Signed out.")

    def test_a_saved_sign_in_only_goes_to_its_own_api(self):
        Settings(self.tmp).update(token="nol_saved", token_api_url="https://api.nolgia.ai/v1")
        ctl = Controller(fake.sample(), has_window=True, env=self.env, log=lambda m: None)
        self.assertIsNone(ctl.token())
        Settings(self.tmp).update(token_api_url=self.server.base_url)
        ctl = Controller(fake.sample(), has_window=True, env=self.env, log=lambda m: None)
        self.assertEqual(ctl.token(), "nol_saved")

    def test_expired_sign_in(self):
        Settings(self.tmp).update(token="nol_old", token_api_url=self.server.base_url, token_expires_at=1.0)
        ctl = Controller(fake.sample(), has_window=True, env=self.env, log=lambda m: None)
        self.assertFalse(ctl.connect())
        self.assertEqual(ctl.status_line(), "Your NOLGIA sign in expired. Sign in again.")


if __name__ == "__main__":
    unittest.main()
