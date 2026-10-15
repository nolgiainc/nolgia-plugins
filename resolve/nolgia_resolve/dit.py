# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""The `dit` command kind: the NOLGIA CLI's `nolgia dit` (copy camera cards
with verified checksums, verify a copy, inventory, rename, undo, report) run
on this computer, the same contract as the Adobe plugin's.

Safety:
- Only the NOLGIA CLI is ever started, as `nolgia --json dit <subcommand>`
  with arguments built here from validated fields. No shell.
- Anything that writes media (offload, rename --apply, undo) is planned with
  the CLI's own dry run on the worker thread, then shown to the person in the
  NOLGIA window for Approve, whatever "Ask before running code" says. With
  no window open it does not run.
- Long work runs as a job: `start` hands back a job id, `status` and
  `cancel` follow it. The CLI journals every file, so a cancelled offload
  resumes when started again.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

SUBCOMMANDS = ("offload", "verify", "manifest", "inventory", "rename", "undo", "report")
ACTIONS = ("start", "status", "cancel")
HASHES = re.compile(r"^(xxh64|md5|sha1)(,(xxh64|md5|sha1))*$")
PAIR = re.compile(r"^[^=]{1,200}=[^=]{1,200}$")
OFFSET = re.compile(r"^[^=]{1,200}=[+-]?(\d+[hHmM])+$")
JOB_ID = re.compile(r"^[a-z0-9-]{1,64}$")
MAX_PATH = 4096
MAX_LIST = 32
MAX_RESULT_CHARS = 900 * 1000

INSTALL_HINT = (
    "The NOLGIA CLI is not installed on this computer, and DIT runs through it. Install it once, then ask "
    "again: npm install -g @nolgia/cli (any computer with Node), or brew install nolgiainc/nolgia/nolgia "
    "(Mac), or the installer at https://github.com/nolgiainc/nolgia-cli#installation."
)


class DitError(Exception):
    pass


def _text(value, key, required=False, max_len=MAX_PATH, pattern=None):
    if value is None or value == "":
        if required:
            raise DitError("`%s` is required." % key)
        return None
    if not isinstance(value, str):
        raise DitError("`%s` must be a string." % key)
    if len(value) > max_len or any(ord(ch) < 32 for ch in value):
        raise DitError("`%s` is not a usable value." % key)
    if pattern is not None and not pattern.match(value):
        raise DitError("`%s` has an unexpected format." % key)
    return value


def _list(args, key, required=False, most=MAX_LIST, pattern=None):
    value = args.get(key)
    if value is None:
        if required:
            raise DitError("`%s` is required: a list." % key)
        return []
    if not isinstance(value, list) or len(value) > most:
        raise DitError("`%s` must be a list of at most %d." % (key, most))
    if required and not value:
        raise DitError("`%s` needs at least one value." % key)
    return [_text(v, key, required=True, pattern=pattern) for v in value]


def _flag(args, key):
    value = args.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise DitError("`%s` must be true or false." % key)
    return value


def _absolute(path, key):
    windows = re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith("\\\\")
    if not (path.startswith("/") or windows):
        raise DitError("`%s` must be a full path (like E:\\ or /Volumes/A001), not %r." % (key, path))
    return path


def validate_args(args):
    """Check a `dit` command's args and return the normalised form."""
    out = {"action": _text(args.get("action"), "action") or "start"}
    if out["action"] not in ACTIONS:
        raise DitError("`action` must be one of: %s." % ", ".join(ACTIONS))
    if out["action"] != "start":
        out["job_id"] = _text(args.get("job_id"), "job_id", required=True, max_len=64, pattern=JOB_ID)
        return out
    sub = _text(args.get("subcommand"), "subcommand", required=True, max_len=16)
    if sub not in SUBCOMMANDS:
        raise DitError("`subcommand` must be one of: %s." % ", ".join(SUBCOMMANDS))
    out["subcommand"] = sub
    if sub == "offload":
        out["sources"] = [_absolute(p, "sources") for p in _list(args, "sources", required=True, most=16)]
        out["destinations"] = [_absolute(p, "destinations") for p in _list(args, "destinations", required=True, most=3)]
        out["shoot"] = _text(args.get("shoot"), "shoot", max_len=200)
        out["cards"] = _list(args, "cards", most=16)
        out["hash"] = _text(args.get("hash"), "hash", max_len=32, pattern=HASHES)
        out["dry_run"] = _flag(args, "dry_run")
        mp = args.get("max_parallel")
        if mp is not None:
            if isinstance(mp, bool) or not isinstance(mp, int) or not 1 <= mp <= 4:
                raise DitError("`max_parallel` must be 1 to 4.")
            out["max_parallel"] = mp
    else:
        out["dir"] = _absolute(_text(args.get("dir"), "dir", required=True), "dir")
    if sub in ("verify", "report"):
        out["manifest"] = _text(args.get("manifest"), "manifest")
        if out["manifest"]:
            _absolute(out["manifest"], "manifest")
        out["only"] = _list(args, "only")
    if sub == "verify":
        out["record"] = _flag(args, "record")
    if sub == "report":
        out["verify"] = _flag(args, "verify")
        out["out"] = _text(args.get("out"), "out")
        if out["out"]:
            _absolute(out["out"], "out")
    if sub == "manifest":
        out["hash"] = _text(args.get("hash"), "hash", max_len=32, pattern=HASHES)
    if sub in ("inventory", "rename", "report"):
        out["clock_offsets"] = _list(args, "clock_offsets", pattern=OFFSET)
    if sub == "inventory":
        out["csv"] = _text(args.get("csv"), "csv")
        if out["csv"]:
            _absolute(out["csv"], "csv")
        out["files"] = _flag(args, "files")
    if sub == "rename":
        out["pattern"] = _text(args.get("pattern"), "pattern", max_len=300)
        out["shoot"] = _text(args.get("shoot"), "shoot", max_len=200)
        out["cameras"] = _list(args, "cameras", pattern=PAIR)
        out["apply"] = _flag(args, "apply")
    if sub in ("rename", "undo"):
        out["map"] = _text(args.get("map"), "map")
        if out["map"]:
            _absolute(out["map"], "map")
    return out


def writes_media(a):
    return (
        (a.get("subcommand") == "offload" and not a.get("dry_run"))
        or (a.get("subcommand") == "rename" and a.get("apply"))
        or a.get("subcommand") == "undo"
    )


def argv(a, dry_run=False):
    """The exact argv after `nolgia`; every value is its own argument."""
    v = ["--json", "dit", a["subcommand"]]

    def repeat(name, values):
        for x in values or []:
            v.extend([name, x])

    def opt(name, value):
        if value not in (None, ""):
            v.extend([name, str(value)])

    sub = a["subcommand"]
    if sub == "offload":
        v.extend(a["sources"])
        repeat("--to", a["destinations"])
        opt("--shoot", a.get("shoot"))
        repeat("--card", a.get("cards"))
        opt("--hash", a.get("hash"))
        opt("--max-parallel", a.get("max_parallel"))
        if a.get("dry_run") or dry_run:
            v.append("--dry-run")
    elif sub == "verify":
        v.append(a["dir"])
        opt("--manifest", a.get("manifest"))
        repeat("--only", a.get("only"))
        if a.get("record"):
            v.append("--record")
    elif sub == "manifest":
        v.append(a["dir"])
        opt("--hash", a.get("hash"))
    elif sub == "inventory":
        v.append(a["dir"])
        repeat("--clock-offset", a.get("clock_offsets"))
        opt("--csv", a.get("csv"))
        if a.get("files"):
            v.append("--files")
    elif sub == "rename":
        v.append(a["dir"])
        opt("--pattern", a.get("pattern"))
        opt("--shoot", a.get("shoot"))
        repeat("--camera", a.get("cameras"))
        repeat("--clock-offset", a.get("clock_offsets"))
        opt("--map", a.get("map"))
        if a.get("apply") and not dry_run:
            v.append("--apply")
    elif sub == "undo":
        v.append(a["dir"])
        opt("--map", a.get("map"))
    elif sub == "report":
        v.append(a["dir"])
        opt("--manifest", a.get("manifest"))
        if a.get("verify"):
            v.append("--verify")
        repeat("--only", a.get("only"))
        repeat("--clock-offset", a.get("clock_offsets"))
        opt("--out", a.get("out"))
    return v


def locate_cli(env=None, platform=None, exists=os.path.isfile):
    """NOLGIA_CLI, PATH, the installers' default folders, npm's package folder."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if env.get("NOLGIA_CLI") and exists(env["NOLGIA_CLI"]):
        return env["NOLGIA_CLI"]
    win = platform.startswith("win")
    sep = "\\" if win else "/"
    exe = "nolgia.exe" if win else "nolgia"
    home = env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")
    dirs = [d for d in (env.get("PATH") or env.get("Path") or "").split(";" if win else ":") if d]
    npm_pkg = sep.join(["node_modules", "@nolgia", "cli", "vendor"])
    if win:
        if env.get("APPDATA"):
            dirs.append(sep.join([env["APPDATA"], "npm", npm_pkg]))
        if env.get("LOCALAPPDATA"):
            dirs.append(sep.join([env["LOCALAPPDATA"], "Programs", "nolgia"]))
        dirs += [sep.join([home, ".cargo", "bin"]), sep.join([home, ".local", "bin"])]
    else:
        dirs += [home + "/.local/bin", home + "/bin", home + "/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin",
                 "/opt/homebrew/lib/" + npm_pkg, "/usr/local/lib/" + npm_pkg]
    for d in dirs:
        p = d.rstrip("\\/") + sep + exe
        if exists(p):
            return p
    return None


def _command(cli, args):
    if cli.endswith(".py"):
        return [sys.executable, cli] + list(args)
    return [cli] + list(args)


def _popen(cli, args):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(_command(cli, args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            shell=False, creationflags=flags)


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _size(b):
    b = float(b or 0)
    if b >= 1e9:
        return "%.2f GB" % (b / 1e9)
    if b >= 1e6:
        return "%.1f MB" % (b / 1e6)
    return "%d bytes" % b


def plan_for(cli, a):
    """Worker thread: the CLI's own dry run for a write, as lines to approve.
    Returns {"lines", "title", "approve"}, {"refused", "result"} or {"nothing"}."""
    if a["subcommand"] == "undo":
        sheet = a.get("map") or os.path.join(a["dir"], "_DIT", "rename-map.csv")
        try:
            with open(sheet, encoding="utf-8") as fh:
                rows = sum(1 for line in fh if line.startswith("file,"))
        except OSError:
            return {"refused": "There is no rename lookup sheet at %s, so there is nothing to undo." % sheet}
        return {"title": "restore the original names of %d files" % rows,
                "lines": ["Restore the original names of %d files in %s," % (rows, a["dir"]), "from %s." % sheet,
                          "Nothing is overwritten."],
                "approve": "Restore names"}
    proc = _popen(cli, argv(a, dry_run=True))
    with proc:
        out, err = proc.communicate()
    plan = _json(out.decode("utf-8", "replace"))
    last = (err.decode("utf-8", "replace").strip().splitlines() or ["exit %s" % proc.returncode])[-1]
    if plan is None:
        return {"refused": "The NOLGIA CLI could not plan this: %s" % last}
    if a["subcommand"] == "offload":
        if plan.get("conflicts"):
            first = ["%s: %s" % (c["path"], c["reason"]) for c in plan["conflicts"][:5]]
            return {"refused": "Nothing was copied. These files already exist at a destination with different "
                               "content, and NOLGIA never replaces a file: " + "; ".join(first), "result": plan}
        lines = ["Copy %s files (%s) and check every copy:" % (plan.get("files"), _size(plan.get("bytes")))]
        for c in plan.get("cards", []):
            lines.append("  card %s: %s (%s files, %s)" % (c["card"], c["source"], c["files"], _size(c["bytes"])))
        for d in plan.get("destinations", []):
            free = " (%s free)" % _size(d["free_bytes"]) if d.get("free_bytes") else ""
            lines.append("  to %s%s" % (d["folder"], free))
        lines.append("The cards are only read. Nothing at a destination is replaced.")
        return {"title": "copy %s files (%s) from camera cards" % (plan.get("files"), _size(plan.get("bytes"))),
                "lines": lines, "approve": "Copy and verify"}
    if proc.returncode != 0:
        return {"refused": "The rename cannot go ahead: %s" % last}
    if not plan.get("to_rename"):
        return {"nothing": plan}
    lines = ["Rename %d files in %s, for example:" % (plan["to_rename"], a["dir"])]
    for c in plan.get("changes", [])[:8]:
        lines.append("  %s -> %s" % (c["from"], c["to"]))
    lines.append("A lookup sheet and undo scripts are written to _DIT first; card-structure files keep their names.")
    return {"title": "rename %d files" % plan["to_rename"], "lines": lines, "approve": "Rename files"}


def trim_result(value):
    text = json.dumps(value)
    if len(text) <= MAX_RESULT_CHARS:
        return value
    copy = json.loads(text)

    def trim(o):
        if isinstance(o, dict):
            for k in list(o):
                if isinstance(o[k], list) and len(o[k]) > 50:
                    o[k + "_total"] = len(o[k])
                    o[k] = o[k][:50]
                else:
                    trim(o[k])

    trim(copy)
    copy["truncated"] = ("Long lists were cut to their first 50 items; run the same nolgia dit command with "
                         "--json for all of them.")
    return copy


MEANINGS = {
    0: "finished: everything matched",
    68: "finished with a problem: a copy or file does not match, is missing, or a destination file was in the "
        "way (listed in result)",
    130: "stopped before the end; start the same command again to resume",
}


class Jobs:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, cli, args):
        job = {"id": "dit-" + uuid.uuid4().hex[:12], "args": list(args), "started": time.time(),
               "stdout": b"", "stderr": b"", "code": None, "proc": None}
        try:
            job["proc"] = _popen(cli, args)
        except OSError as err:
            job["code"] = -1
            job["stderr"] = str(err).encode()
        else:
            threading.Thread(target=self._collect, args=(job,), daemon=True).start()
        with self.lock:
            self.jobs[job["id"]] = job
        return job

    def _collect(self, job):
        proc = job["proc"]

        def read_err():
            for line in proc.stderr:
                job["stderr"] = (job["stderr"] + line)[-8000:]

        t = threading.Thread(target=read_err, daemon=True)
        t.start()
        job["stdout"] = proc.stdout.read()
        proc.wait()
        t.join(timeout=2)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        job["code"] = proc.returncode

    def view(self, job):
        running = job["code"] is None
        err = job["stderr"].decode("utf-8", "replace").strip().splitlines()
        out = {"job_id": job["id"], "command": "nolgia " + " ".join(job["args"]), "running": running,
               "seconds": int(time.time() - job["started"])}
        if running:
            out["progress"] = err[-1] if err else ""
        else:
            out["exit_code"] = job["code"]
            out["meaning"] = MEANINGS.get(job["code"], "failed")
            out["result"] = _json(job["stdout"].decode("utf-8", "replace"))
            if out["result"] is None and err:
                out["error"] = " ".join(err[-3:])
        return trim_result(out)

    def wait(self, job, seconds):
        end = time.time() + seconds
        while job["code"] is None and time.time() < end:
            time.sleep(0.05)
        return self.view(job)

    def status(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            raise DitError("No DIT job %s on this computer (jobs are kept while Resolve stays open)." % job_id)
        return self.view(job)

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            raise DitError("No DIT job %s on this computer." % job_id)
        running = job["code"] is None and job["proc"] is not None
        if running:
            job["proc"].terminate()
        return {"job_id": job_id, "cancelled": running,
                "note": "An offload resumes where it stopped when the same command is started again."}
