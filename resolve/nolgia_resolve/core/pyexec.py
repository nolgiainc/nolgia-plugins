# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Run a piece of Python the way the `run` command needs it.

- `result` in the namespace is the value handed back (made JSON safe).
- print() output of the code is captured per thread, so our own background
  threads writing to the console do not leak into the command's stdout.
- A failure returns the traceback with the offending source lines.
- The timeout is best effort: a watchdog raises RunTimeout inside the running
  code at its next Python bytecode. A single long C call (one render, one
  sleep) finishes first.
"""

import ctypes
import io
import linecache
import math
import sys
import threading
import traceback
import uuid

from .util import cut_text

MAX_STREAM_CHARS = 256 * 1024
MAX_LIST_ITEMS = 10000
MAX_DEPTH = 32


class RunTimeout(BaseException):
    """Raised inside user code when its time is up.

    A BaseException so a bare `except Exception` in the code does not swallow it.
    """


class Outcome:
    def __init__(self, ok, value=None, stdout="", stderr="", error=None, timed_out=False):
        self.ok = ok
        self.value = value
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.timed_out = timed_out

    def result(self):
        return {"value": self.value, "stdout": self.stdout, "stderr": self.stderr}


class _Capture(io.TextIOBase):
    """Stands in for sys.stdout/sys.stderr: writes from `thread_id` go to the
    buffer, everything else goes to the original stream."""

    def __init__(self, original, thread_id, limit):
        self.original = original
        self.thread_id = thread_id
        self.limit = limit
        self.parts = []
        self.size = 0
        self.dropped = 0

    def writable(self):
        return True

    def write(self, text):
        if threading.get_ident() != self.thread_id:
            if self.original is not None:
                return self.original.write(text)
            return len(text)
        room = self.limit - self.size
        if room > 0:
            piece = text[:room]
            self.parts.append(piece)
            self.size += len(piece)
        self.dropped += max(0, len(text) - max(room, 0))
        return len(text)

    def flush(self):
        if self.original is not None and threading.get_ident() != self.thread_id:
            try:
                self.original.flush()
            except Exception:
                pass

    def getvalue(self):
        text = "".join(self.parts)
        if self.dropped:
            text += "\n[%d more characters cut]" % self.dropped
        return text

    @property
    def encoding(self):
        return "utf-8"


def _async_raise(thread_id, exc_type):
    return ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread_id), ctypes.py_object(exc_type) if exc_type else None
    )


class _Watchdog:
    def __init__(self, thread_id, timeout):
        self.thread_id = thread_id
        self.lock = threading.Lock()
        self.done = False
        self.fired = False
        self.timer = threading.Timer(timeout, self._fire)
        self.timer.daemon = True

    def _fire(self):
        with self.lock:
            if not self.done:
                self.fired = True
                _async_raise(self.thread_id, RunTimeout)

    def start(self):
        self.timer.start()

    def finish(self):
        self.timer.cancel()
        with self.lock:
            self.done = True
            if self.fired:
                # Clear a timeout that was set but has not landed yet, so it
                # can never go off later in Blender's own code.
                _async_raise(self.thread_id, None)


def run_python(code, namespace, timeout=None, filename=None):
    """Execute `code` in `namespace` on the calling thread."""
    filename = filename or "<nolgia run %s>" % uuid.uuid4().hex[:8]
    namespace.setdefault("result", None)
    namespace.setdefault("__name__", "__nolgia__")
    try:
        compiled = compile(code, filename, "exec")
    except SyntaxError:
        return Outcome(False, error=_format_exception(skip=0))

    me = threading.get_ident()
    out = _Capture(sys.stdout, me, MAX_STREAM_CHARS)
    err = _Capture(sys.stderr, me, MAX_STREAM_CHARS)
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    watchdog = _Watchdog(me, timeout) if timeout and timeout > 0 else None
    old_out, old_err = sys.stdout, sys.stderr
    ok, error, timed_out = True, None, False
    try:
        try:
            sys.stdout, sys.stderr = out, err
            if watchdog:
                watchdog.start()
            try:
                exec(compiled, namespace)
            finally:
                if watchdog:
                    watchdog.finish()
        except RunTimeout:
            ok, timed_out = False, True
            error = "The code took longer than %s seconds and was stopped." % _seconds(timeout)
        except SystemExit as exit_:
            ok = False
            error = "The code called exit(%s). Set `result` and return instead." % (exit_.code,)
        except BaseException:  # noqa: B902 - report everything the code raised
            ok = False
            error = _format_exception(skip=1)
    except RunTimeout:
        # The timeout landed in our own bookkeeping right after the code ended.
        ok, timed_out = False, True
        error = "The code took longer than %s seconds and was stopped." % _seconds(timeout)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        linecache.cache.pop(filename, None)

    value = to_jsonable(namespace.get("result")) if ok else None
    return Outcome(ok, value, out.getvalue(), err.getvalue(), cut_text(error, 64 * 1024), timed_out)


def _seconds(value):
    return ("%g" % value) if isinstance(value, (int, float)) else str(value)


def _format_exception(skip):
    etype, evalue, tb = sys.exc_info()
    for _ in range(skip):
        if tb is not None:
            tb = tb.tb_next
    return "".join(traceback.format_exception(etype, evalue, tb)).rstrip()


def to_jsonable(value, _depth=0):
    """Turn what the code left in `result` into plain JSON data.

    Blender and mathutils values become lists (vectors, colors, matrices) or
    their names (objects, materials and other data blocks); anything else
    falls back to its repr.
    """
    if _depth > MAX_DEPTH:
        return "[nested too deep]"
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)[:MAX_LIST_ITEMS]
        return [to_jsonable(v, _depth + 1) for v in items]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    # Blender data blocks and RNA structs: use the name when there is one.
    if hasattr(value, "bl_rna"):
        name = getattr(value, "name", None)
        if isinstance(name, str) and not _is_sequence(value):
            return name
        if _is_sequence(value):
            return [to_jsonable(v, _depth + 1) for v in _take(value)]
        return repr(value)[:1000]
    # mathutils Vector, Color, Euler, Quaternion, Matrix and friends.
    if _is_sequence(value):
        return [to_jsonable(v, _depth + 1) for v in _take(value)]
    for attr in ("item", "tolist"):  # numpy-style scalars and arrays
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return to_jsonable(fn(), _depth + 1)
            except Exception:
                pass
    return repr(value)[:1000]


def _is_sequence(value):
    if isinstance(value, (str, bytes, bytearray, dict)):
        return False
    try:
        iter(value)
        len(value)
        return True
    except Exception:
        return False


def _take(value):
    out = []
    for i, item in enumerate(value):
        if i >= MAX_LIST_ITEMS:
            break
        out.append(item)
    return out
