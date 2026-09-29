# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""The stable `instance_id` the API keys sessions by.

One id per install, kept in a file. When a second Blender is open on the
same install it would otherwise share the first one's session, so each
running Blender holds a lock on a numbered slot: the first uses the id as
is, the second uses "<id>-2", and so on. The ids stay the same across
restarts, so the API does not collect a new session every launch.
"""

import errno
import os
import uuid

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # macOS and Linux
    msvcrt = None

MAX_SLOTS = 16


def _read_or_create(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
            if value:
                return value
    except OSError:
        pass
    value = str(uuid.uuid4())
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
    except OSError:
        pass
    return value


# Errors that mean "another process holds this lock". Anything else (EINVAL
# on a network drive, ENOLCK, EOPNOTSUPP) means locks do not work here.
_HELD = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
HELD = "held"
UNSUPPORTED = "unsupported"


def _try_lock(path):
    """Returns (handle, None) when locked, else (None, HELD or UNSUPPORTED)."""
    try:
        handle = open(path, "a+b")
    except OSError:
        return None, UNSUPPORTED
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle, None
    except OSError as err:
        handle.close()
        return None, HELD if err.errno in _HELD else UNSUPPORTED


class InstanceLease:
    def __init__(self, instance_id, handle):
        self.instance_id = instance_id
        self._handle = handle

    def release(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def lease_instance_id(directory, override=None):
    """Return an InstanceLease; call release() when disconnecting."""
    if override:
        return InstanceLease(override, None)
    os.makedirs(directory, exist_ok=True)
    base = _read_or_create(os.path.join(directory, "instance_id"))
    for slot in range(1, MAX_SLOTS + 1):
        handle, why = _try_lock(os.path.join(directory, "slot-%d.lock" % slot))
        if handle is not None:
            return InstanceLease(base if slot == 1 else "%s-%d" % (base, slot), handle)
        if why == UNSUPPORTED:
            # No file locks on this drive: keep the plain id so it stays stable.
            return InstanceLease(base if slot == 1 else "%s-%d" % (base, slot), None)
    return InstanceLease("%s-%s" % (base, uuid.uuid4().hex[:8]), None)
