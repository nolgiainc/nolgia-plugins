# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Small helpers with no Blender dependency."""

import datetime
import os
import random
import re

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(Z|z|[+-]\d{2}:?\d{2})?$"
)


def parse_time(value):
    """Parse an RFC 3339 timestamp (Go emits up to 9 fraction digits).

    Returns an aware datetime, or None when the value is missing or odd.
    """
    if not isinstance(value, str):
        return None
    match = _ISO_RE.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, frac, zone = match.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    if zone in (None, "Z", "z"):
        tz = datetime.timezone.utc
    else:
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        tz = datetime.timezone(
            sign * datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        )
    try:
        return datetime.datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), micro, tz
        )
    except ValueError:
        return None


class Backoff:
    """Exponential backoff with jitter: base, 2*base, 4*base ... up to cap.

    Each delay is drawn from [delay/2, delay] so many plugins that lost the
    network at the same moment do not all come back in the same second.
    """

    def __init__(self, base=1.0, cap=60.0, rng=None):
        self.base = base
        self.cap = cap
        self.attempt = 0
        self._rng = rng or random.Random()

    def next(self):
        delay = min(self.cap, self.base * (2 ** self.attempt))
        self.attempt += 1
        return self._rng.uniform(delay / 2.0, delay)

    def reset(self):
        self.attempt = 0


def cut_text(text, limit, keep="tail"):
    """Keep at most `limit` characters, saying how much was cut."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    if keep == "head":
        return text[:limit] + "\n[%d more characters cut]" % dropped
    return "[%d characters cut]\n" % dropped + text[-limit:]


# File types NOLGIA accepts through POST /assets/uploads, by extension.
UPLOAD_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".glb": "model/gltf-binary",
}

# What an asset's MIME type means as a file on disk, for import.
MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/tiff": ".tif",
    "image/bmp": ".bmp",
    "image/x-exr": ".exr",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/webm": ".webm",
    "model/gltf-binary": ".glb",
    "model/gltf+json": ".gltf",
    "model/obj": ".obj",
    "model/fbx": ".fbx",
    "application/x-blender": ".blend",
}

IMPORT_KINDS = {
    ".glb": "model",
    ".gltf": "model",
    ".fbx": "model",
    ".obj": "model",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".exr": "image",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mkv": "video",
    ".avi": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".ogg": "audio",
    ".m4a": "audio",
    ".flac": "audio",
}


def sniff_extension(head):
    """Guess a file extension from its first bytes."""
    if not head:
        return ""
    if head.startswith(b"glTF"):
        return ".glb"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head.startswith(b"Kaydara FBX Binary"):
        return ".fbx"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand.startswith(b"qt"):
            return ".mov"
        if brand in (b"M4A ", b"M4B "):
            return ".m4a"
        return ".mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return ".mp3"
    if head.startswith(b"OggS"):
        return ".ogg"
    if head.lstrip().startswith(b"{") and b'"asset"' in head:
        return ".gltf"
    return ""


def import_extension(display_name, mime_type, head=b""):
    """Pick the extension to save a downloaded asset under.

    The name's own extension wins when Blender can import it, then the MIME
    type, then the file's first bytes.
    """
    ext = os.path.splitext(display_name or "")[1].lower()
    if ext in IMPORT_KINDS:
        return ext
    ext = MIME_EXTENSIONS.get((mime_type or "").split(";")[0].strip().lower(), "")
    if ext in IMPORT_KINDS:
        return ext
    return sniff_extension(head)


_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_filename(name, fallback="file"):
    """A file name that is safe on Windows, macOS and Linux."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = _UNSAFE.sub("_", base).strip(" .")
    return base[:120] or fallback
