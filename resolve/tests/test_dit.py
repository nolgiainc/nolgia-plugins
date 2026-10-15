# SPDX-License-Identifier: GPL-3.0-or-later
"""The `dit` kind in DaVinci Resolve: argument checks, the exact argv,
finding the CLI, the approval for writes and jobs, against a fake CLI."""
import json
import os
import sys
import tempfile
import time
import unittest

import support  # noqa: F401  (puts the plugin on sys.path)

from nolgia_resolve import dit, ops as ops_module
from nolgia_resolve.core.commands import CommandError, validate

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fake_nolgia.py")


class FakeCommand:
    caller_label = "The NOLGIA Agent"

    def __init__(self, args):
        self.args = validate("dit", args)


class Env:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.values}
        for k, v in self.values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class DitTest(unittest.TestCase):
    def ops(self):
        return ops_module.Ops.__new__(ops_module.Ops)

    def test_args_are_checked(self):
        ok = validate("dit", {"subcommand": "offload", "sources": ["E:\\"], "destinations": ["/Volumes/RAID"]})
        self.assertEqual(ok["action"], "start")
        for bad in (
            {"subcommand": "offload", "sources": ["rel"], "destinations": ["/d"]},
            {"subcommand": "offload", "sources": ["/a"], "destinations": ["/1", "/2", "/3", "/4"]},
            {"subcommand": "offload", "sources": ["/a\nx"], "destinations": ["/d"]},
            {"subcommand": "erase", "dir": "/a"},
            {"action": "status"},
        ):
            with self.assertRaises(CommandError, msg=bad):
                validate("dit", bad)

    def test_argv_is_one_argument_per_value(self):
        a = dit.validate_args({"subcommand": "rename", "dir": "/f", "shoot": "x; rm -rf /", "apply": True})
        self.assertEqual(dit.argv(a), ["--json", "dit", "rename", "/f", "--shoot", "x; rm -rf /", "--apply"])
        self.assertNotIn("--apply", dit.argv(a, dry_run=True))

    def test_cli_is_found_in_npm_package_folder_on_windows(self):
        want = "C:\\Users\\x\\AppData\\Roaming\\npm\\node_modules\\@nolgia\\cli\\vendor\\nolgia.exe"
        found = dit.locate_cli({"APPDATA": "C:\\Users\\x\\AppData\\Roaming", "USERPROFILE": "C:\\Users\\x",
                                "PATH": "C:\\Windows"}, "win32", exists=lambda p: p == want)
        self.assertEqual(found, want)

    def test_offload_is_planned_and_needs_approval(self):
        with Env(NOLGIA_CLI=FAKE, FAKE_CONFLICT=None):
            cmd = FakeCommand({"subcommand": "offload", "sources": ["/cards/A001"], "destinations": ["/drives/one"]})
            prepared = ops_module.prepare_dit(cmd)
            request = self.ops().dit_approval(cmd, prepared)
            self.assertIsNotNone(request)
            self.assertIn("copy 2 files", request.title)
            self.assertTrue(any("Nothing at a destination is replaced" in l for l in request.lines))

    def test_read_only_work_needs_no_approval(self):
        with Env(NOLGIA_CLI=FAKE):
            cmd = FakeCommand({"subcommand": "verify", "dir": "/drives/one/SHOOT/A001"})
            self.assertIsNone(self.ops().dit_approval(cmd, ops_module.prepare_dit(cmd)))
            res = self.ops().do_dit(cmd.args, ops_module.prepare_dit(cmd), cmd)
            if res["running"]:
                time.sleep(2)
            self.assertEqual(res.get("exit_code", 68), 68)

    def test_a_conflict_is_refused_before_asking(self):
        with Env(NOLGIA_CLI=FAKE, FAKE_CONFLICT="1"):
            cmd = FakeCommand({"subcommand": "offload", "sources": ["/c"], "destinations": ["/d"]})
            prepared = ops_module.prepare_dit(cmd)
            self.assertIsNone(self.ops().dit_approval(cmd, prepared))
            with self.assertRaises(CommandError) as ctx:
                self.ops().do_dit(cmd.args, prepared, cmd)
            self.assertIn("never replaces a file", str(ctx.exception))

    def test_long_work_comes_back_as_a_job(self):
        log = os.path.join(tempfile.mkdtemp(), "argv.log")
        with Env(NOLGIA_CLI=FAKE, FAKE_SLOW="3", FAKE_LOG=log):
            o = self.ops()
            cmd = FakeCommand({"subcommand": "inventory", "dir": "/drives/one/SHOOT"})
            res = o.do_dit(cmd.args, ops_module.prepare_dit(cmd), cmd)
            self.assertTrue(res["running"])
            deadline = time.time() + 10
            while True:
                done = o.do_dit({"action": "status", "job_id": res["job_id"]}, None, cmd)
                if not done["running"] or time.time() > deadline:
                    break
                time.sleep(0.2)
            self.assertEqual(done["result"], {"status": "ok"})
            with open(log) as fh:
                self.assertEqual(json.loads(fh.readline())[:3], ["--json", "dit", "inventory"])


if __name__ == "__main__":
    unittest.main()
