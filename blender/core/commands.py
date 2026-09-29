# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Command kinds, argument checks, result shaping and the activity list.

The wire format is the one in nolgia-api-bridge docs/design/bridge.md.
"""

import collections
import json
import threading
import time

from .util import parse_time, cut_text

# Every kind this plugin implements, in the order the spec lists them. The
# plugin advertises exactly these in `capabilities`.
CAPABILITIES = ("info", "run", "preview", "import_asset", "export", "save", "open")

EXPORT_FORMATS = ("blend", "png", "mp4", "glb")
PREVIEW_ENGINES = ("current", "eevee", "workbench", "cycles")
IMPORT_AS = ("plane", "texture", "clip")

DEFAULT_TIMEOUT = 120.0
# The API takes at most 1 MB (2**20 bytes) of result JSON, measured after it
# re-encodes the result. Stay a little under so a result never lands at 413.
MAX_RESULT_BYTES = 1000 * 1000
MAX_ERROR_CHARS = 64 * 1024
# The API refuses a result that arrives at or after expires_at, so the plugin
# stops waiting (for approval, or for code to finish) a little before that.
RESULT_MARGIN = 3.0


class CommandError(Exception):
    """A command failed. The message is shown to the agent and the person."""

    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


class Command:
    def __init__(self, data, received_at=None):
        self.raw = data
        self.id = str(data.get("id") or "")
        self.kind = str(data.get("kind") or "")
        args = data.get("args")
        self.args = args if isinstance(args, dict) else {}
        self.caller = str(data.get("caller") or "")
        self.received_at = received_at if received_at is not None else time.monotonic()
        self.timeout = self._timeout(data)

    def _timeout(self, data):
        """Seconds this command may take, measured from when we got it.

        Uses the server's own clock (expires_at minus claimed_at or created_at)
        so a skewed clock on this computer does not matter.
        """
        expires = parse_time(data.get("expires_at"))
        start = parse_time(data.get("claimed_at")) or parse_time(data.get("created_at"))
        if expires and start:
            seconds = (expires - start).total_seconds()
            if seconds > 0:
                return seconds
        seconds = data.get("timeout_seconds")
        if isinstance(seconds, (int, float)) and seconds > 0:
            return float(seconds)
        return DEFAULT_TIMEOUT

    @property
    def expires(self):
        """When the API stops accepting a result, on our monotonic clock."""
        return self.received_at + self.timeout

    @property
    def deadline(self):
        """When to give up so the failure still reaches the API in time."""
        return self.expires - min(RESULT_MARGIN, self.timeout * 0.25)

    def remaining(self, now=None):
        return self.deadline - (time.monotonic() if now is None else now)

    @property
    def caller_label(self):
        return {"agent": "Your NOLGIA Agent", "user": "Your agent"}.get(self.caller, self.caller or "NOLGIA")


def _int(args, key, low=None, high=None, required=False):
    value = args.get(key)
    if value is None:
        if required:
            raise CommandError("`%s` is required." % key)
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise CommandError("`%s` must be a whole number." % key)
    value = int(value)
    if low is not None and value < low:
        raise CommandError("`%s` must be at least %d." % (key, low))
    if high is not None and value > high:
        raise CommandError("`%s` must be at most %d." % (key, high))
    return value


def _str(args, key, required=False, choices=None):
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise CommandError("`%s` is required." % key)
        return None
    if not isinstance(value, str):
        raise CommandError("`%s` must be text." % key)
    if choices and value not in choices:
        raise CommandError("`%s` must be one of: %s." % (key, ", ".join(choices)))
    return value


def parse_frames(value):
    """`frames` for export: 12, "12", "1-120", [1, 120] or {"start":1,"end":120}.

    Returns (start, end) or None.
    """
    if value is None or value == "":
        return None
    start = end = None
    if isinstance(value, bool):
        raise CommandError("`frames` must be a frame number or a range like \"1-120\".")
    if isinstance(value, (int, float)) and int(value) == value:
        start = end = int(value)
    elif isinstance(value, str):
        text = value.strip()
        parts = text.split("-", 1) if not text.startswith("-") else [text]
        try:
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                start, end = int(parts[0]), int(parts[1])
            else:
                start = end = int(text)
        except ValueError:
            raise CommandError("`frames` must be a frame number or a range like \"1-120\".") from None
    elif isinstance(value, (list, tuple)) and len(value) in (1, 2):
        try:
            start, end = int(value[0]), int(value[-1])
        except (TypeError, ValueError):
            raise CommandError("`frames` must be a frame number or a range like \"1-120\".") from None
    elif isinstance(value, dict) and "start" in value:
        try:
            start = int(value["start"])
            end = int(value.get("end", start))
        except (TypeError, ValueError):
            raise CommandError("`frames` must be a frame number or a range like \"1-120\".") from None
    else:
        raise CommandError("`frames` must be a frame number or a range like \"1-120\".")
    if end < start:
        raise CommandError("`frames` ends before it starts.")
    return start, end


def validate(kind, args):
    """Check and normalise a command's args. Raises CommandError."""
    if kind not in CAPABILITIES:
        raise CommandError(
            "This NOLGIA plugin for Blender cannot do \"%s\". It can do: %s. "
            "Updating the plugin may add it." % (kind, ", ".join(CAPABILITIES))
        )
    if not isinstance(args, dict):
        raise CommandError("`args` must be an object.")
    out = {}
    if kind == "run":
        language = (_str(args, "language") or "python").lower()
        if language != "python":
            raise CommandError("Blender runs Python only; this command asked for %s." % language)
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            raise CommandError("`code` is required: the Python to run in Blender.")
        out["language"] = "python"
        out["code"] = code
        timeout = args.get("timeout_seconds")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                raise CommandError("`timeout_seconds` must be a positive number.")
            out["timeout_seconds"] = float(timeout)
    elif kind == "preview":
        out["camera"] = _str(args, "camera")
        out["frame"] = _int(args, "frame")
        out["width"] = _int(args, "width", 16, 1920)
        out["engine"] = _str(args, "engine", choices=PREVIEW_ENGINES) or "current"
    elif kind == "import_asset":
        out["asset_id"] = _str(args, "asset_id", required=True)
        out["as"] = _str(args, "as", choices=IMPORT_AS)
    elif kind == "export":
        out["format"] = (_str(args, "format", required=True) or "").lower()
        if out["format"] not in EXPORT_FORMATS:
            raise CommandError("`format` must be one of: %s." % ", ".join(EXPORT_FORMATS))
        out["frames"] = parse_frames(args.get("frames"))
        out["filename"] = _str(args, "filename")
        selected = args.get("selected_only", False)
        if not isinstance(selected, bool):
            raise CommandError("`selected_only` must be true or false.")
        out["selected_only"] = selected
    elif kind == "save":
        out["path"] = _str(args, "path")
    elif kind == "open":
        out["path"] = _str(args, "path", required=True)
    return out


def result_body(ok, result=None, error=None):
    """The body for POST /bridge/commands/{id}/result, kept under 1 MB."""
    if ok:
        body = {"status": "succeeded", "result": result if result is not None else {}}
    else:
        body = {"status": "failed", "error": cut_text(error or "The command failed.", MAX_ERROR_CHARS)}
        if result is not None:
            body["result"] = result
    size = _size(body)
    if size <= MAX_RESULT_BYTES:
        return body
    # Too big: shorten captured output first, then give up on the value.
    res = body.get("result")
    if isinstance(res, dict):
        res = dict(res)
        for key in ("stdout", "stderr"):
            if isinstance(res.get(key), str):
                res[key] = cut_text(res[key], 16 * 1024)
        body["result"] = res
        if _size(body) <= MAX_RESULT_BYTES:
            return body
    return {
        "status": "failed",
        "error": "The result is larger than 1 MB, the most NOLGIA accepts. "
        "Return less data, or write it to a file and export that.",
    }


def _size(body):
    """Bytes of the body as the API measures it. Go's encoder writes <, > and &
    as \\u003c and friends, five bytes more each."""
    text = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    return len(text.encode("utf-8")) + 5 * (text.count("<") + text.count(">") + text.count("&"))


class ActivityLog:
    """The last 20 commands for the panel. Safe to use from any thread."""

    STATUS_LABELS = {
        "received": "Received",
        "running": "Running",
        "approval": "Waiting for you",
        "succeeded": "Done",
        "failed": "Failed",
        "cancelled": "Cancelled",
        "expired": "Expired",
    }

    def __init__(self, size=20, clock=time.time):
        self._items = collections.OrderedDict()
        self._size = size
        self._lock = threading.Lock()
        self._clock = clock
        self.version = 0

    def add(self, command_id, kind, caller=""):
        with self._lock:
            self._items[command_id] = {
                "id": command_id,
                "kind": kind,
                "caller": caller,
                "status": "received",
                "time": self._clock(),
                "detail": "",
            }
            while len(self._items) > self._size:
                self._items.popitem(last=False)
            self.version += 1

    def update(self, command_id, status, detail=""):
        with self._lock:
            item = self._items.get(command_id)
            if item is None:
                return
            item["status"] = status
            if detail:
                item["detail"] = detail.strip().splitlines()[-1][:200]
            self.version += 1

    def items(self):
        """Newest first."""
        with self._lock:
            return [dict(item) for item in reversed(self._items.values())]

    def clear(self):
        with self._lock:
            self._items.clear()
            self.version += 1
