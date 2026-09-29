# SPDX-License-Identifier: GPL-3.0-or-later
import datetime
import random
import unittest

import support  # noqa: F401

from core import util


class ParseTime(unittest.TestCase):
    def test_go_nanoseconds_and_z(self):
        t = util.parse_time("2026-09-29T06:24:52.527745123Z")
        self.assertEqual(t, datetime.datetime(2026, 9, 29, 6, 24, 52, 527745, datetime.timezone.utc))

    def test_offsets_and_no_fraction(self):
        t = util.parse_time("2026-09-29T08:00:00+02:00")
        self.assertEqual(t.astimezone(datetime.timezone.utc).hour, 6)
        self.assertIsNotNone(util.parse_time("2026-09-29T06:00:00"))

    def test_garbage(self):
        for value in (None, "", "yesterday", 12, "2026-13-40T99:00:00Z"):
            self.assertIsNone(util.parse_time(value))


class BackoffTest(unittest.TestCase):
    def test_grows_with_jitter_and_caps(self):
        b = util.Backoff(1.0, 8.0, rng=random.Random(1))
        delays = [b.next() for _ in range(8)]
        for i, d in enumerate(delays):
            top = min(8.0, 2 ** i)
            self.assertGreaterEqual(d, top / 2)
            self.assertLessEqual(d, top)
        b.reset()
        self.assertLessEqual(b.next(), 1.0)


class Files(unittest.TestCase):
    def test_import_extension_prefers_name_then_mime_then_bytes(self):
        self.assertEqual(util.import_extension("hero.FBX", "application/octet-stream"), ".fbx")
        self.assertEqual(util.import_extension("Hero shot v2", "model/gltf-binary"), ".glb")
        self.assertEqual(util.import_extension("", "video/quicktime"), ".mov")
        self.assertEqual(util.import_extension("thing", "application/octet-stream", b"glTF\x02\x00"), ".glb")
        self.assertEqual(util.import_extension("x", "", b"\x89PNG\r\n\x1a\n...."), ".png")
        self.assertEqual(util.import_extension("x", "", b"\x00\x00\x00\x18ftypisom"), ".mp4")
        self.assertEqual(util.import_extension("notes.txt", "text/plain", b"hello"), "")

    def test_safe_filename(self):
        self.assertEqual(util.safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(util.safe_filename("C:\\x\\shot: final?.blend"), "shot_ final_.blend")
        self.assertEqual(util.safe_filename("   ", "fallback"), "fallback")

    def test_cut_text(self):
        self.assertEqual(util.cut_text("abc", 5), "abc")
        self.assertTrue(util.cut_text("a" * 10, 4).endswith("aaaa"))
        self.assertIn("6 characters cut", util.cut_text("a" * 10, 4))
        self.assertTrue(util.cut_text("abcdef", 2, keep="head").startswith("ab\n"))


if __name__ == "__main__":
    unittest.main()
