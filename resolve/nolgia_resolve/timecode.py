# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Timecode and frame numbers, the way DaVinci Resolve counts them.

Resolve numbers a timeline's frames from its start timecode (01:00:00:00 at
24 fps is frame 86400). Non-integer rates count at their nominal rate
(23.976 counts like 24); 29.97 and 59.94 can use drop-frame timecode, which
Resolve writes with ";" before the frames.
"""


def nominal(fps):
    return max(1, int(round(float(fps))))


def is_drop_frame(fps, text=None):
    if text is not None and ";" in text:
        return True
    return False


def _drop_count(fps):
    return 2 if nominal(fps) == 30 else 4 if nominal(fps) == 60 else 0


def to_frames(text, fps):
    """'01:00:00:00' -> 86400 at 24 fps. Drop-frame when the text has ';'."""
    parts = [int(p) for p in text.strip().replace(";", ":").replace(".", ":").split(":")]
    if len(parts) != 4:
        raise ValueError("timecode must be HH:MM:SS:FF")
    hours, minutes, seconds, frames = parts
    base = nominal(fps)
    total = ((hours * 60 + minutes) * 60 + seconds) * base + frames
    drop = _drop_count(fps) if is_drop_frame(fps, text) else 0
    if drop:
        total_minutes = hours * 60 + minutes
        total -= drop * (total_minutes - total_minutes // 10)
    return total


def from_frames(frame, fps, drop_frame=False):
    """86400 at 24 fps -> '01:00:00:00'."""
    base = nominal(fps)
    drop = _drop_count(fps) if drop_frame else 0
    frame = int(frame)
    if drop:
        per_ten = base * 600 - drop * 9
        per_minute = base * 60 - drop
        tens, rest = divmod(frame, per_ten)
        if rest > drop:
            frame += drop * 9 * tens + drop * ((rest - drop) // per_minute)
        else:
            frame += drop * 9 * tens
    frames = frame % base
    seconds = (frame // base) % 60
    minutes = (frame // (base * 60)) % 60
    hours = frame // (base * 3600)
    sep = ";" if drop else ":"
    return "%02d:%02d:%02d%s%02d" % (hours, minutes, seconds, sep, frames)
