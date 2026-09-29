# SPDX-License-Identifier: GPL-3.0-or-later
"""Command parsing, argument checks, result shaping and the activity list."""

import json
import unittest

import support  # noqa: F401

from core.commands import (
    ActivityLog, CAPABILITIES, Command, CommandError, MAX_RESULT_BYTES, parse_frames, result_body, validate,
)


class Kinds(unittest.TestCase):
    def test_capabilities_are_the_spec_kinds(self):
        self.assertEqual(CAPABILITIES, ("info", "run", "preview", "import_asset", "export", "save", "open"))

    def test_unknown_kind(self):
        with self.assertRaisesRegex(CommandError, "cannot do \"render_everything\""):
            validate("render_everything", {})

    def test_run(self):
        self.assertEqual(validate("run", {"language": "python", "code": "x=1"}),
                         {"language": "python", "code": "x=1"})
        self.assertEqual(validate("run", {"code": "x=1", "timeout_seconds": 5})["timeout_seconds"], 5.0)
        with self.assertRaisesRegex(CommandError, "Python only"):
            validate("run", {"language": "extendscript", "code": "x"})
        with self.assertRaisesRegex(CommandError, "`code` is required"):
            validate("run", {"language": "python", "code": "  "})
        with self.assertRaises(CommandError):
            validate("run", {"code": "x", "timeout_seconds": -1})

    def test_preview(self):
        self.assertEqual(validate("preview", {}), {"camera": None, "frame": None, "width": None, "engine": "current"})
        self.assertEqual(validate("preview", {"camera": "Cam", "frame": 12, "width": 640})["width"], 640)
        for bad in ({"width": 1921}, {"width": 8}, {"width": 1.5}, {"frame": "ten"}, {"engine": "vray"}):
            with self.assertRaises(CommandError):
                validate("preview", bad)

    def test_import_export_save_open(self):
        self.assertEqual(validate("import_asset", {"asset_id": "a"})["asset_id"], "a")
        with self.assertRaises(CommandError):
            validate("import_asset", {})
        with self.assertRaises(CommandError):
            validate("import_asset", {"asset_id": "a", "as": "hologram"})
        out = validate("export", {"format": "MP4", "frames": "1-24", "filename": "shot"})
        self.assertEqual((out["format"], out["frames"], out["filename"]), ("mp4", (1, 24), "shot"))
        with self.assertRaisesRegex(CommandError, "blend, png, mp4, glb"):
            validate("export", {"format": "usd"})
        self.assertEqual(validate("save", {}), {"path": None})
        with self.assertRaises(CommandError):
            validate("open", {})

    def test_frames(self):
        self.assertIsNone(parse_frames(None))
        self.assertEqual(parse_frames(7), (7, 7))
        self.assertEqual(parse_frames("7"), (7, 7))
        self.assertEqual(parse_frames("24"), (24, 24))  # what the MCP tool sends
        self.assertEqual(parse_frames("1-120"), (1, 120))
        self.assertEqual(parse_frames([10, 20]), (10, 20))
        self.assertEqual(parse_frames({"start": 3, "end": 9}), (3, 9))
        self.assertEqual(parse_frames("-5"), (-5, -5))
        for bad in ("x", "9-1", [1, 2, 3], True, 1.5):
            with self.assertRaises(CommandError):
                parse_frames(bad)


class CommandParsing(unittest.TestCase):
    def test_timeout_from_server_clock(self):
        cmd = Command({"id": "c1", "kind": "info", "args": {},
                       "created_at": "2026-09-29T06:00:00.000000001Z",
                       "claimed_at": "2026-09-29T06:00:10Z",
                       "expires_at": "2026-09-29T06:02:00Z"}, received_at=100.0)
        self.assertEqual(cmd.timeout, 110.0)
        self.assertEqual(cmd.expires, 210.0)
        # stops 3 s early, so a failure still reaches the API before expires_at
        self.assertEqual(cmd.deadline, 207.0)
        self.assertEqual(cmd.remaining(now=200.0), 7.0)
        short = Command({"id": "c", "kind": "info", "timeout_seconds": 5}, received_at=0.0)
        self.assertEqual(short.deadline, 3.75)

    def test_timeout_fallbacks(self):
        self.assertEqual(Command({"id": "c", "kind": "info", "timeout_seconds": 30}).timeout, 30.0)
        self.assertEqual(Command({"id": "c", "kind": "info"}).timeout, 120.0)
        self.assertEqual(Command({"id": "c", "kind": "info", "args": None}).args, {})

    def test_caller_label(self):
        self.assertEqual(Command({"id": "c", "kind": "run", "caller": "agent"}).caller_label, "NOLGIA Agent")
        self.assertEqual(Command({"id": "c", "kind": "run", "caller": "user"}).caller_label, "you")


class ResultShaping(unittest.TestCase):
    def test_success_and_failure_bodies(self):
        self.assertEqual(result_body(True, {"a": 1}), {"status": "succeeded", "result": {"a": 1}})
        self.assertEqual(result_body(True, None), {"status": "succeeded", "result": {}})
        self.assertEqual(result_body(False, None, "boom"), {"status": "failed", "error": "boom"})
        body = result_body(False, {"value": None, "stdout": "x", "stderr": ""}, "tb")
        self.assertEqual(body["result"]["stdout"], "x")

    def test_big_output_is_trimmed_under_1mb(self):
        body = result_body(True, {"value": 1, "stdout": "x" * 2_000_000, "stderr": ""})
        self.assertEqual(body["status"], "succeeded")
        self.assertLessEqual(len(json.dumps(body)), MAX_RESULT_BYTES)
        self.assertEqual(body["result"]["value"], 1)

    def test_size_counts_go_html_escaping(self):
        # Go writes each < as \u003c: 6 bytes. A result under 1 MB in Python can
        # be over it on the API side, so the plugin measures it the Go way.
        body = result_body(True, {"value": "<" * 200_000, "stdout": "", "stderr": ""})
        self.assertEqual(body["status"], "failed")

    def test_big_value_fails_with_advice(self):
        body = result_body(True, {"value": ["y" * 1000] * 2000, "stdout": "", "stderr": ""})
        self.assertEqual(body["status"], "failed")
        self.assertIn("larger than 1 MB", body["error"])


class Activity(unittest.TestCase):
    def test_keeps_the_last_20_newest_first(self):
        log = ActivityLog(clock=lambda: 5.0)
        for i in range(25):
            log.add("c%d" % i, "info", "agent")
        items = log.items()
        self.assertEqual(len(items), 20)
        self.assertEqual(items[0]["id"], "c24")
        self.assertEqual(items[-1]["id"], "c5")
        log.update("c24", "failed", "line one\nValueError: boom")
        self.assertEqual((log.items()[0]["status"], log.items()[0]["detail"]), ("failed", "ValueError: boom"))
        log.update("gone", "failed")  # unknown ids are ignored
        self.assertEqual(ActivityLog.STATUS_LABELS["approval"], "Waiting for you")


if __name__ == "__main__":
    unittest.main()
