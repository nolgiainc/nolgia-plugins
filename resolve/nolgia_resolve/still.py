# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Stills for `preview`, with the standard library only (Resolve's Python
has no Pillow).

Resolve writes the frame at the timeline's full size. For a preview the
plugin asks Resolve for an uncompressed still (BMP or PPM, which are plain
rows of pixels), scales it down here with a box filter, and writes a PNG.
The work is done with C-level loops (map, zip, zlib), so a 4K frame takes
about a second.
"""

import itertools
import operator
import struct
import zlib


class StillError(Exception):
    pass


class Image:
    """8-bit RGB, rows top to bottom, each `width * 3` bytes."""

    def __init__(self, width, height, rows):
        self.width = width
        self.height = height
        self.rows = rows

    @property
    def size(self):
        return self.width, self.height


# ------------------------------------------------------------------ reading


def read_image(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:2] == b"BM":
        return read_bmp(data)
    if data[:2] in (b"P6", b"P3"):
        return read_ppm(data)
    raise StillError("Unknown still format (%r)." % data[:4])


def read_bmp(data):
    if len(data) < 54 or data[:2] != b"BM":
        raise StillError("Not a BMP file.")
    offset = struct.unpack_from("<I", data, 10)[0]
    header_size = struct.unpack_from("<I", data, 14)[0]
    if header_size < 40:
        raise StillError("Old BMP headers are not supported.")
    width, height, _planes, bits, compression = struct.unpack_from("<iiHHI", data, 18)
    if compression not in (0, 3) or bits not in (24, 32):
        raise StillError("Only uncompressed 24 or 32 bit BMP stills are supported (got %d bit, mode %d)."
                         % (bits, compression))
    top_down = height < 0
    height = abs(height)
    step = bits // 8
    stride = (width * step + 3) & ~3
    if offset + stride * height > len(data):
        raise StillError("The BMP still is cut short.")
    rows = []
    for y in range(height):
        start = offset + stride * y
        raw = data[start:start + width * step]
        # BGR(A) to RGB
        rgb = bytearray(width * 3)
        rgb[0::3] = raw[2::step]
        rgb[1::3] = raw[1::step]
        rgb[2::3] = raw[0::step]
        rows.append(bytes(rgb))
    if not top_down:
        rows.reverse()
    return Image(width, height, rows)


def _ppm_tokens(data, count):
    tokens, pos = [], 2
    while len(tokens) < count:
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] not in (b"\n", b"\r"):
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        tokens.append(int(data[start:pos]))
    return tokens, pos + 1


def read_ppm(data):
    if data[:2] != b"P6":
        raise StillError("Only binary PPM (P6) stills are supported.")
    (width, height, maxval), pos = _ppm_tokens(data, 3)
    if maxval < 256:
        need = width * height * 3
        body = data[pos:pos + need]
        if len(body) < need:
            raise StillError("The PPM still is cut short.")
        if maxval != 255:
            table = bytes(min(255, round(v * 255 / maxval)) for v in range(256))
            body = body.translate(table)
        rows = [body[y * width * 3:(y + 1) * width * 3] for y in range(height)]
        return Image(width, height, rows)
    # 16 bit: keep the high byte (big endian), scaled when maxval is not 65535
    need = width * height * 6
    body = data[pos:pos + need]
    if len(body) < need:
        raise StillError("The PPM still is cut short.")
    high = body[0::2]
    if maxval != 65535:
        values = struct.unpack(">%dH" % (width * height * 3), body)
        high = bytes(min(255, v * 255 // maxval) for v in values)
    rows = [high[y * width * 3:(y + 1) * width * 3] for y in range(height)]
    return Image(width, height, rows)


# ------------------------------------------------------------------ scaling


def _taps(src, dst, count):
    """For each of `dst` output positions, `count` source positions spread
    evenly over the part of the source it covers."""
    scale = src / dst
    out = []
    for t in range(count):
        frac = (t + 0.5) / count
        out.append([min(src - 1, int((x + frac) * scale)) for x in range(dst)])
    return out


def scale(image, width, height):
    """Box-filter the image to width x height (smaller or equal sizes)."""
    if (width, height) == image.size:
        return image
    if width < 1 or height < 1:
        raise StillError("Bad target size.")
    ratio_x = image.width / width
    ratio_y = image.height / height
    taps_x = max(1, min(4, int(round(ratio_x))))
    taps_y = max(1, min(4, int(round(ratio_y))))
    cols = _taps(image.width, width, taps_x)
    # for each horizontal tap, the source byte behind each output byte
    offsets = [[3 * tap[i // 3] + i % 3 for i in range(width * 3)] for tap in cols]
    rows_for = _taps(image.height, height, taps_y)
    count = taps_x * taps_y
    half = count // 2
    rows = []
    for y in range(height):
        gathered = []
        for tap_y in rows_for:
            src = image.rows[tap_y[y]]
            get = src.__getitem__
            for offs in offsets:
                gathered.append(map(get, offs))
        if count == 1:
            rows.append(bytes(gathered[0]))
            continue
        sums = map(sum, zip(*gathered))
        rows.append(bytes(map(operator.floordiv, map(operator.add, sums, itertools.repeat(half)),
                              itertools.repeat(count))))
    return Image(width, height, rows)


def fit_width(size, width):
    """(w, h) scaled to `width` (never larger than the source), keeping the
    long side at most 1920."""
    src_w, src_h = size
    width = max(16, min(width, src_w))
    height = max(16, int(round(src_h * width / src_w)))
    if height > 1920:
        height = 1920
        width = max(16, int(round(src_w * height / src_h)))
    return width, height


# ------------------------------------------------------------------ writing


_MOD = itertools.repeat(256)


def _up_filter(row, prev):
    """PNG filter 2 (Up): each byte minus the byte above, mod 256."""
    if prev is None:
        return row
    return bytes(map(operator.mod, map(operator.sub, row, prev), _MOD))


def write_png(image, path, level=6):
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    comp = zlib.compressobj(level)
    parts = []
    prev = None
    for row in image.rows:
        if prev is None:
            parts.append(comp.compress(b"\x00" + row))
        else:
            parts.append(comp.compress(b"\x02" + _up_filter(row, prev)))
        prev = row
    parts.append(comp.flush())
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", header))
        handle.write(chunk(b"IDAT", b"".join(parts)))
        handle.write(chunk(b"IEND", b""))
    return path


# ------------------------------------------------------------------ sizes


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        raise StillError("Not a PNG file.")
    return struct.unpack(">II", data[16:24])


def jpeg_size(data):
    if data[:2] != b"\xff\xd8":
        raise StillError("Not a JPEG file.")
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[pos + 5:pos + 9])
            return width, height
        pos += 2 + length
    raise StillError("No size in the JPEG file.")


def image_size(path):
    with open(path, "rb") as handle:
        head = handle.read(1 << 16)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return png_size(head)
    if head[:2] == b"\xff\xd8":
        with open(path, "rb") as handle:
            return jpeg_size(handle.read())
    return read_image(path).size
