# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for the NOLGIA CLI: records argv and answers like `nolgia --json dit ...`."""
import json
import os
import sys
import time

args = sys.argv[1:]
if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "a") as fh:
        fh.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_SLOW"):
    time.sleep(float(os.environ["FAKE_SLOW"]))


def out(obj, code=0):
    sys.stdout.write(json.dumps(obj))
    sys.exit(code)


if "offload" in args and "--dry-run" in args:
    if os.environ.get("FAKE_CONFLICT"):
        out({"files": 2, "bytes": 2000, "cards": [], "destinations": [],
             "conflicts": [{"path": "/d/S/A/C0001.MP4", "reason": "already exists with different content"}]}, 68)
    out({"files": 2, "bytes": 2000000000, "cards": [{"card": "A001", "source": "/cards/A001", "files": 2,
        "bytes": 2000000000}], "destinations": [{"folder": "/drives/one/SHOOT", "free_bytes": 5e12}], "conflicts": []})
if "offload" in args:
    out({"status": "complete", "files_verified": 2})
if "rename" in args and "--apply" not in args:
    out({"status": "dry-run", "to_rename": 1, "changes": [{"from": "C0001.MP4", "to": "S_C0001.MP4"}]})
if "verify" in args:
    out({"status": "failed"}, 68)
out({"status": "ok"})
