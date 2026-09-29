# SPDX-License-Identifier: GPL-3.0-or-later
"""The protocol client against the mock API."""

import os
import tempfile
import time
import unittest

import support

from core import PLUGIN_VERSION
from core.api import ApiClient, ApiError, NetworkError, Unauthorized, normalize_base_url
from core.commands import CAPABILITIES


def register_body(instance_id="inst-1", **extra):
    body = {
        "instance_id": instance_id,
        "app": "blender",
        "app_version": "4.5.8",
        "plugin_version": PLUGIN_VERSION,
        "machine_name": "test-machine",
        "document": {"name": ""},
        "capabilities": list(CAPABILITIES),
        "allow_agent": True,
    }
    body.update(extra)
    return body


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = support.start_mock()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.state.reset()
        self.api = ApiClient(self.server.base_url, support.TOKEN)

    def test_base_url(self):
        self.assertEqual(normalize_base_url("https://api.nolgia.ai"), "https://api.nolgia.ai/v1")
        self.assertEqual(normalize_base_url("https://api.nolgia.ai/v1/"), "https://api.nolgia.ai/v1")
        self.assertEqual(normalize_base_url(None), "https://api.nolgia.ai/v1")

    def test_register_answers_the_session_at_top_level(self):
        first = self.api.register_session(register_body())
        self.assertEqual(first["poll_wait_seconds"], 25)
        self.assertEqual(set(first), {"id", "instance_id", "app", "app_version", "plugin_version", "machine_name",
                                      "document", "capabilities", "allow_agent", "created_at", "last_seen_at",
                                      "poll_wait_seconds"})
        again = self.api.register_session(register_body(document={"name": "shot.blend", "path": "/x/shot.blend"}))
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(again["document"], {"name": "shot.blend", "path": "/x/shot.blend"})
        other = self.api.register_session(register_body("inst-2"))
        self.assertNotEqual(first["id"], other["id"])

    def test_register_resets_what_is_left_out(self):
        self.api.register_session(register_body(allow_agent=False))
        bare = self.api.register_session({"instance_id": "inst-1", "app": "blender"})
        self.assertEqual((bare["allow_agent"], bare["capabilities"], bare["app_version"], bare["document"]),
                         (True, [], "", {}))

    def test_register_validation(self):
        for body, detail in (
            (register_body("x" * 129), "instance_id must be one line"),
            (register_body("a\nb"), "instance_id must be one line"),
            (register_body(capabilities=["Preview"]), "capabilities"),
            (register_body(capabilities=["k%d" % i for i in range(33)]), "at most 32"),
            (register_body(app="maya"), "app must be one of"),
            (register_body(document={"name": "a", "size": 1}), "unknown field"),
        ):
            with self.assertRaises(ApiError) as ctx:
                self.api.register_session(body)
            self.assertEqual(ctx.exception.status, 400, body)
            self.assertIn(detail, ctx.exception.detail)

    def test_me(self):
        self.assertEqual(self.api.get_me()["email"], "test@nolgia.ai")

    def test_long_poll_empty_then_command_then_result(self):
        sid = self.api.register_session(register_body())["id"]
        self.assertIsNone(self.api.next_command(sid, wait=0))
        cid = support.enqueue(self.server, "info")
        cmd = self.api.next_command(sid, wait=5)
        self.assertEqual((cmd["id"], cmd["kind"], cmd["status"]), (cid, "info", "running"))
        self.assertTrue(cmd["expires_at"] and cmd["claimed_at"])
        self.assertNotIn("result", cmd)  # absent, not null
        self.assertNotIn("finished_at", cmd)
        self.api.post_result(cid, {"status": "succeeded", "result": {"ok": True}})
        done = support.wait_command(self.server, cid)
        self.assertEqual((done["status"], done["result"]), ("succeeded", {"ok": True}))

    def test_long_poll_wakes_when_a_command_arrives(self):
        import threading, time
        sid = self.api.register_session(register_body())["id"]
        threading.Timer(0.3, lambda: support.enqueue(self.server, "info")).start()
        t0 = time.time()
        cmd = self.api.next_command(sid, wait=10)
        self.assertIsNotNone(cmd)
        self.assertLess(time.time() - t0, 5)

    def test_result_after_cancel_is_409(self):
        sid = self.api.register_session(register_body())["id"]
        cid = support.enqueue(self.server, "info")
        self.api.next_command(sid, wait=1)
        support.call(self.server, "POST", "/v1/bridge/commands/%s/cancel" % cid)
        with self.assertRaises(ApiError) as ctx:
            self.api.post_result(cid, {"status": "succeeded", "result": {}})
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "command_not_running"))

    def test_result_at_expiry_is_refused(self):
        sid = self.api.register_session(register_body())["id"]
        cid = support.enqueue(self.server, "info")
        self.api.next_command(sid, wait=1)
        self.server.state.commands[cid]["expires_at"] = time.time() - 0.01
        with self.assertRaises(ApiError) as ctx:
            self.api.post_result(cid, {"status": "succeeded", "result": {}})
        self.assertEqual(ctx.exception.code, "command_not_running")
        self.assertIn("expired", ctx.exception.detail)

    def test_result_over_1mb_is_413_and_marks_it_failed(self):
        sid = self.api.register_session(register_body())["id"]
        cid = support.enqueue(self.server, "info")
        self.api.next_command(sid, wait=1)
        with self.assertRaises(ApiError) as ctx:
            self.api.post_result(cid, {"status": "succeeded", "result": {"value": "x" * (1 << 20)}})
        self.assertEqual(ctx.exception.status, 413)
        done = support.wait_command(self.server, cid)
        self.assertEqual(done["status"], "failed")
        self.assertIn("larger than 1 MB", done["error"])

    def test_failed_without_error_gets_the_default_text(self):
        sid = self.api.register_session(register_body())["id"]
        cid = support.enqueue(self.server, "info")
        self.api.next_command(sid, wait=1)
        self.api.post_result(cid, {"status": "failed"})
        done = support.wait_command(self.server, cid)
        self.assertEqual(done["error"], "The app reported a failure without details.")
        self.assertNotIn("result", done)

    def test_delete_session_stops_it_being_live(self):
        sid = self.api.register_session(register_body())["id"]
        cid = support.enqueue(self.server, "info")
        self.assertEqual(len(support.call(self.server, "GET", "/v1/bridge/sessions")[1]["sessions"]), 1)
        self.api.delete_session(sid)
        self.assertEqual(support.call(self.server, "GET", "/v1/bridge/sessions")[1]["sessions"], [])
        dropped = support.wait_command(self.server, cid)
        self.assertEqual((dropped["status"], dropped["error"]),
                         ("expired", "The app disconnected before this command finished."))
        with self.assertRaises(ApiError) as ctx:
            self.api.next_command(sid, wait=0)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "session_disconnected"))
        # registering again reconnects the same session
        self.assertEqual(self.api.register_session(register_body())["id"], sid)
        self.assertIsNone(self.api.next_command(sid, wait=0))
        with self.assertRaises(ApiError) as ctx:
            self.api.next_command("00000000-0000-4000-8000-000000000000", wait=0)
        self.assertEqual(ctx.exception.status, 404)

    def test_unknown_field_is_refused_like_the_go_api(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.register_session(register_body(extra_field=1))
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("unknown field", ctx.exception.detail)

    def test_bad_token_is_unauthorized(self):
        with self.assertRaises(Unauthorized):
            ApiClient(self.server.base_url, "nope").register_session(register_body())

    def test_server_down_is_network_error(self):
        api = ApiClient("http://127.0.0.1:9/v1", support.TOKEN, timeout=2)
        with self.assertRaises(NetworkError):
            api.register_session(register_body())

    def test_upload_then_download_without_leaking_the_token(self):
        folder = tempfile.mkdtemp()
        src = os.path.join(folder, "frame.png")
        payload = b"\x89PNG\r\n\x1a\n" + os.urandom(2000)
        with open(src, "wb") as handle:
            handle.write(payload)
        asset = self.api.upload_file(src, "image/png", display_name="Frame", tags=["blender"])
        self.assertEqual(asset["status"], "ready")
        self.assertEqual(asset["size_bytes"], len(payload))
        fetched = self.api.get_asset(asset["id"])
        dest = os.path.join(folder, "copy.png")
        head = self.api.download(fetched["signed_url"], dest)
        self.assertEqual(head, payload[:64])
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), payload)
        storage = [r for r in self.server.state.requests if r["path"].startswith("/storage/")]
        self.assertEqual(len(storage), 2)
        self.assertFalse(any(r["auth"] for r in storage))

    def test_upload_type_not_accepted(self):
        folder = tempfile.mkdtemp()
        src = os.path.join(folder, "shot.blend")
        with open(src, "wb") as handle:
            handle.write(b"BLENDER")
        for ctype in ("text/plain", "application/x-blender", "model/fbx"):
            with self.assertRaises(ApiError) as ctx:
                self.api.upload_file(src, ctype)
            self.assertEqual(ctx.exception.status, 400)

    def test_device_errors_parse_the_oauth_code_from_title(self):
        start = self.api.start_device_auth("nolgia-blender", "bridge")
        with self.assertRaises(ApiError) as ctx:
            self.api.poll_device_token("nolgia-blender", start["device_code"])
        self.assertEqual((ctx.exception.status, ctx.exception.code), (400, "authorization_pending"))
        with self.assertRaises(ApiError) as ctx:
            self.api.poll_device_token("nolgia-blender", start["device_code"])
        self.assertEqual(ctx.exception.code, "slow_down")
        with self.assertRaises(ApiError) as ctx:
            self.api.poll_device_token("nolgia-blender", "unknown")
        self.assertEqual(ctx.exception.code, "expired_token")


if __name__ == "__main__":
    unittest.main()
