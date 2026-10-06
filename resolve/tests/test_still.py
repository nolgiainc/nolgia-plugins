# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading, scaling and writing stills with the standard library."""

import os
import shutil
import struct
import tempfile
import unittest
import zlib

import support  # noqa: F401
import fake_resolve as fake

from nolgia_resolve import still, timecode


def decode_png(data):
    """A small PNG reader for the tests (8-bit RGB, filters 0 and 2)."""
    width, height = still.png_size(data)
    pos, idat = 8, b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    rows, prev, stride = [], bytes(width * 3), width * 3 + 1
    for y in range(height):
        kind, line = raw[y * stride], bytearray(raw[y * stride + 1:(y + 1) * stride])
        if kind == 2:
            line = bytearray((a + b) & 0xFF for a, b in zip(line, prev))
        else:
            assert kind == 0, kind
        rows.append(bytes(line))
        prev = bytes(line)
    return width, height, rows


class Stills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nolgia-still-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bmp_bottom_up_with_padding(self):
        image = still.read_bmp(fake.make_bmp(5, 3, (10, 20, 30)))
        self.assertEqual(image.size, (5, 3))
        self.assertEqual(image.rows[0][:3], bytes([10, 20, 30]))

    def test_bmp_32_bit_top_down(self):
        rows = [bytes([1, 2, 3, 255]) * 2, bytes([4, 5, 6, 255]) * 2]
        body = b"".join(rows)
        data = struct.pack("<2sIHHI", b"BM", 54 + len(body), 0, 0, 54) + \
            struct.pack("<IiiHHIIiiII", 40, 2, -2, 1, 32, 0, len(body), 0, 0, 0, 0) + body
        image = still.read_bmp(data)
        self.assertEqual(image.rows, [bytes([3, 2, 1]) * 2, bytes([6, 5, 4]) * 2])

    def test_ppm_8_and_16_bit(self):
        image = still.read_ppm(b"P6\n# made by a test\n2 1\n255\n" + bytes([9, 8, 7, 6, 5, 4]))
        self.assertEqual(image.rows, [bytes([9, 8, 7, 6, 5, 4])])
        image = still.read_ppm(b"P6 1 1 65535\n" + struct.pack(">3H", 65535, 32768, 0))
        self.assertEqual(image.rows, [bytes([255, 128, 0])])

    def test_scale_keeps_colours_and_averages(self):
        image = still.Image(4, 2, [bytes([0, 0, 0, 200, 200, 200] * 2)] * 2)
        small = still.scale(image, 2, 1)
        self.assertEqual(small.rows, [bytes([100, 100, 100] * 2)])
        flat = still.Image(1920, 1080, [bytes([40, 80, 120]) * 1920] * 1080)
        out = still.scale(flat, 1280, 720)
        self.assertEqual(out.size, (1280, 720))
        self.assertEqual(set(out.rows), {bytes([40, 80, 120]) * 1280})

    def test_png_round_trip(self):
        rows = [bytes((x * 7 + y * 3 + c) % 256 for x in range(33) for c in range(3)) for y in range(17)]
        path = os.path.join(self.tmp, "x.png")
        still.write_png(still.Image(33, 17, rows), path)
        with open(path, "rb") as handle:
            self.assertEqual(decode_png(handle.read()), (33, 17, rows))
        self.assertEqual(still.image_size(path), (33, 17))

    def test_fit_width(self):
        self.assertEqual(still.fit_width((1920, 1080), 1280), (1280, 720))
        self.assertEqual(still.fit_width((640, 480), 1920), (640, 480))
        self.assertEqual(still.fit_width((1080, 3840), 1080), (540, 1920))

    def test_jpeg_size(self):
        self.assertEqual(still.jpeg_size(fake.make_jpeg_stub(1280, 720)), (1280, 720))


class Timecodes(unittest.TestCase):
    def test_round_trips(self):
        for fps in (23.976, 24, 25, 30, 50, 60):
            for frame in (0, 1, 86400, 90061, 1234567):
                self.assertEqual(timecode.to_frames(timecode.from_frames(frame, fps), fps), frame)
        for fps in (29.97, 59.94):
            for frame in (0, 1799, 1800, 17982, 107892, 999999):
                text = timecode.from_frames(frame, fps, drop_frame=True)
                self.assertIn(";", text)
                self.assertEqual(timecode.to_frames(text, fps), frame, text)

    def test_known_values(self):
        self.assertEqual(timecode.to_frames("01:00:00:00", 24), 86400)
        self.assertEqual(timecode.to_frames("01:00:00:00", 23.976), 86400)
        self.assertEqual(timecode.from_frames(86450, 24), "01:00:02:02")
        self.assertEqual(timecode.to_frames("00:01:00;02", 29.97), 1800)
        self.assertEqual(timecode.to_frames("00:10:00;00", 29.97), 17982)
        self.assertEqual(timecode.from_frames(1800, 29.97, True), "00:01:00;02")


if __name__ == "__main__":
    unittest.main()
