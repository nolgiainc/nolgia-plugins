# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings and the sign in on disk, the log, and where files go."""

import json
import os
import shutil
import tempfile
import unittest

import support  # noqa: F401

from nolgia_resolve import paths
from nolgia_resolve.settings import DEFAULTS, Log, Settings


class SettingsFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nolgia-settings-")
        self.folder = os.path.join(self.tmp, "NOLGIA", "resolve")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_without_a_file(self):
        s = Settings(self.folder)
        self.assertEqual({k: s[k] for k in DEFAULTS}, DEFAULTS)
        self.assertFalse(os.path.exists(s.path), "nothing written until something changes")
        self.assertTrue(s["allow_agent"])
        self.assertFalse(s["ask_before_run"])

    def test_saved_and_read_back(self):
        s = Settings(self.folder)
        self.assertTrue(s.update(token="nol_secret", connected=True, ask_before_run=True))
        self.assertFalse(s.update(connected=True), "no change, no write")
        again = Settings(self.folder)
        self.assertEqual((again["token"], again["connected"], again["ask_before_run"]), ("nol_secret", True, True))
        with open(s.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["token"], "nol_secret")
        self.assertEqual([n for n in os.listdir(self.folder) if n.startswith(".settings-")], [], "temp file left")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_only_you_can_read_it(self):
        s = Settings(self.folder)
        s.update(token="nol_secret")
        self.assertEqual(os.stat(s.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.folder).st_mode & 0o777, 0o700)

    def test_public_hides_the_token(self):
        s = Settings(self.folder)
        s.update(token="nol_secret")
        self.assertEqual(s.public()["token"], "***")
        self.assertNotIn("nol_secret", json.dumps(s.public()))

    def test_bad_file_and_bad_values(self):
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "settings.json"), "w") as handle:
            handle.write("{not json")
        s = Settings(self.folder)
        self.assertEqual(s["token"], "")
        self.assertTrue(s.load_error)
        with open(s.path, "w") as handle:
            json.dump({"connected": "yes", "token": 5, "token_expires_at": "soon", "allow_agent": False}, handle)
        s.load()
        self.assertEqual((s["connected"], s["token"], s["token_expires_at"], s["allow_agent"]), (False, "", 0.0, False))
        with self.assertRaises(KeyError):
            s.update(nonsense=1)


class LogFile(unittest.TestCase):
    def test_appends_and_cuts_back(self):
        tmp = tempfile.mkdtemp(prefix="nolgia-log-")
        try:
            log = Log(tmp, limit=2000, echo=False)
            for n in range(200):
                log("line %d" % n)
            with open(log.path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("line 199", text)
            self.assertNotIn("line 0\n", text)
            self.assertLess(os.path.getsize(log.path), 2600)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Paths(unittest.TestCase):
    def test_per_os_folders(self):
        env = {"APPDATA": r"C:\Users\me\AppData\Roaming"}
        self.assertTrue(paths.utility_dir("win32", env).endswith(
            os.path.join("Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility")))
        self.assertTrue(paths.utility_dir("win32", env).startswith(r"C:\Users\me\AppData\Roaming"))
        self.assertTrue(paths.utility_dir("darwin", {}).endswith(os.path.join(
            "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve", "Fusion", "Scripts", "Utility")))
        self.assertTrue(paths.utility_dir("linux", {}).endswith(os.path.join(
            ".local", "share", "DaVinciResolve", "Fusion", "Scripts", "Utility")))
        self.assertTrue(paths.config_dir("win32", env).endswith(os.path.join("NOLGIA", "resolve")))
        self.assertTrue(paths.config_dir("linux", {"XDG_CONFIG_HOME": "/x"}).startswith("/x"))
        self.assertEqual(paths.config_dir("linux", {"NOLGIA_CONFIG_DIR": "/c"}), "/c")
        self.assertTrue(paths.lut_dir("win32", env).endswith(os.path.join("DaVinci Resolve", "Support", "LUT")))
        self.assertEqual(paths.lut_dir("darwin", {"NOLGIA_LUT_DIR": "/l"}), "/l")
        self.assertEqual(paths.import_dir({"NOLGIA_IMPORT_DIR": "/i"}), "/i")
        self.assertTrue(paths.import_dir({}).endswith("NOLGIA imports"))


if __name__ == "__main__":
    unittest.main()
