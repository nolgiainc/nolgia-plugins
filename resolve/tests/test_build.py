# SPDX-License-Identifier: GPL-3.0-or-later
"""The download zip, the install scripts and the NOLGIA.py entry."""

import io
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import support

sys.path.insert(0, support.PLUGIN_DIR)
import build  # noqa: E402

from nolgia_resolve.core import PLUGIN_VERSION  # noqa: E402


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nolgia-build-")
        cls.path = build.build(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_name_and_layout(self):
        self.assertEqual(os.path.basename(self.path), "nolgia-resolve-%s.zip" % PLUGIN_VERSION)
        with zipfile.ZipFile(self.path) as archive:
            self.assertEqual(sorted(archive.namelist()), [
                "INSTALL.txt", "LICENSE", "Utility/NOLGIA.py", "Utility/nolgia_resolve.zip",
                "install.cmd", "install.sh",
            ])
            notes = archive.read("INSTALL.txt").decode("utf-8")
            self.assertIn("NOLGIA for DaVinci Resolve %s" % PLUGIN_VERSION, notes)
            self.assertNotIn("\r", notes)
            self.assertNotIn(b"\r", archive.read("install.sh"))
            self.assertNotIn(b"\r", archive.read("Utility/NOLGIA.py"))
            self.assertIn(b"\r\n", archive.read("install.cmd"))
            mode = archive.getinfo("install.sh").external_attr >> 16
            self.assertTrue(mode & 0o111, "install.sh must be executable")
            library = zipfile.ZipFile(io.BytesIO(archive.read("Utility/nolgia_resolve.zip")))
            names = library.namelist()
        self.assertIn("nolgia_resolve/__init__.py", names)
        self.assertIn("nolgia_resolve/core/worker.py", names)
        self.assertFalse([n for n in names if "__pycache__" in n or "tests" in n or not n.endswith(".py")], names)

    def test_reproducible(self):
        again = build.build(os.path.join(self.tmp, "again"))
        with open(self.path, "rb") as a, open(again, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_the_plugin_imports_from_the_zip(self):
        folder = tempfile.mkdtemp(dir=self.tmp)
        with zipfile.ZipFile(self.path) as archive:
            archive.extractall(folder)
        code = ("import sys; sys.path.insert(0, %r); import nolgia_resolve, nolgia_resolve.panel, "
                "nolgia_resolve.runtime; print(nolgia_resolve.__file__, nolgia_resolve.PLUGIN_VERSION)"
                % os.path.join(folder, "Utility", "nolgia_resolve.zip"))
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("nolgia_resolve.zip", out.stdout)
        self.assertIn(PLUGIN_VERSION, out.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX shell")
    def test_unix_installer(self):
        folder = tempfile.mkdtemp(dir=self.tmp)
        home = os.path.join(folder, "home")
        with zipfile.ZipFile(self.path) as archive:
            archive.extractall(folder)
        out = subprocess.run(["sh", os.path.join(folder, "install.sh")], capture_output=True, text=True,
                             env=dict(os.environ, HOME=home), timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        target = os.path.join(home, ".local", "share", "DaVinciResolve", "Fusion", "Scripts", "Utility")
        self.assertEqual(sorted(os.listdir(target)), ["NOLGIA.py", "nolgia_resolve.zip"])


class Entry(unittest.TestCase):
    """NOLGIA.py, the script Resolve lists in its Scripts menu."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nolgia-entry-")
        path = build.build(self.tmp)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(self.tmp)
        self.script = os.path.join(self.tmp, "Utility", "NOLGIA.py")
        self.saved_path = list(sys.path)
        self.saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("nolgia_resolve")}
        for name in self.saved_modules:
            del sys.modules[name]

    def tearDown(self):
        sys.path[:] = self.saved_path
        for name in [k for k in sys.modules if k.startswith("nolgia_resolve")]:
            del sys.modules[name]
        sys.modules.update(self.saved_modules)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_without_resolve_it_explains(self):
        with mock.patch.object(sys, "argv", ["NOLGIA.py"]), self.assertRaises(SystemExit) as ctx:
            runpy.run_path(self.script, run_name="__main__")
        self.assertEqual(ctx.exception.code, 2)

    def test_in_resolve_it_opens_the_window_from_the_zip(self):
        # Resolve runs the script with resolve, fusion and bmd in its globals.
        opened = []

        def fake_open(resolve, fusion, bmd):
            import nolgia_resolve

            opened.append((nolgia_resolve.__file__, resolve, fusion, bmd))

        sys.path.insert(0, os.path.join(self.tmp, "Utility", "nolgia_resolve.zip"))
        import nolgia_resolve

        scope = {"resolve": "R", "fusion": "F", "bmd": "B"}
        with mock.patch.object(sys, "argv", [""]), mock.patch.object(nolgia_resolve, "open_window", fake_open):
            runpy.run_path(self.script, init_globals=scope, run_name="__main__")
        self.assertEqual(len(opened), 1)
        self.assertIn("nolgia_resolve.zip", opened[0][0])
        self.assertEqual(opened[0][1:], ("R", "F", "B"))

    def test_as_resolve_runs_it_without_file(self):
        # Resolve's fuscript runs a menu script with no __file__ and its path in sys.argv[0].
        opened = []
        sys.path.insert(0, os.path.join(self.tmp, "Utility", "nolgia_resolve.zip"))
        import nolgia_resolve

        with open(self.script, encoding="utf-8") as handle:
            code = compile(handle.read(), "<NOLGIA>", "exec")
        scope = {"resolve": "R", "fusion": "F", "bmd": "B", "__name__": "__main__"}
        sys.path.remove(os.path.join(self.tmp, "Utility", "nolgia_resolve.zip"))
        with mock.patch.object(sys, "argv", [self.script]), \
                mock.patch.object(nolgia_resolve, "open_window", lambda *a: opened.append(a)):
            exec(code, scope)
        self.assertEqual(opened, [("R", "F", "B")])

    def test_serve_flag(self):
        sys.path.insert(0, os.path.join(self.tmp, "Utility", "nolgia_resolve.zip"))
        import nolgia_resolve

        with mock.patch.object(sys, "argv", ["NOLGIA.py", "--serve"]), \
                mock.patch.object(nolgia_resolve, "serve", return_value=False), \
                self.assertRaises(SystemExit) as ctx:
            runpy.run_path(self.script, run_name="__main__")
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()


class OtherSystems(unittest.TestCase):
    """macOS and Linux logic, run here with the platform patched (there is no
    Mac to run the plugin on; see the README)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nolgia-os-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entry_folders(self, platform, env):
        namespace = {"__name__": "nolgia_entry_probe"}
        with open(os.path.join(support.PLUGIN_DIR, "NOLGIA.py"), encoding="utf-8") as handle:
            source = handle.read().replace("\nmain(globals())\n", "\n")
        exec(compile(source, "NOLGIA.py", "exec"), namespace)
        with mock.patch.object(sys, "platform", platform), mock.patch.dict(os.environ, env, clear=False), \
                mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", env.get("HOME", "~"), 1)):
            return list(namespace["_folders"]())

    def test_entry_looks_in_each_systems_scripts_folder(self):
        mac = self.entry_folders("darwin", {"HOME": "/Users/ana"})
        self.assertIn("/Users/ana/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
                      [f.replace(os.sep, "/") for f in mac])
        linux = self.entry_folders("linux", {"HOME": "/home/ana"})
        self.assertIn("/home/ana/.local/share/DaVinciResolve/Fusion/Scripts/Utility",
                      [f.replace(os.sep, "/") for f in linux])
        win = self.entry_folders("win32", {"APPDATA": "C:\\Users\\Ana\\AppData\\Roaming", "HOME": "/x"})
        self.assertTrue(any(f.replace("\\", "/").startswith("C:/Users/Ana/AppData/Roaming/Blackmagic Design/DaVinci "
                                                            "Resolve/Support/Fusion/Scripts/Utility") for f in win), win)

    @unittest.skipIf(os.name == "nt", "POSIX shell")
    def test_install_sh_on_macos(self):
        folder = tempfile.mkdtemp(dir=self.tmp)
        with zipfile.ZipFile(build.build(self.tmp)) as archive:
            archive.extractall(folder)
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, "uname"), "w") as handle:
            handle.write("#!/bin/sh\necho Darwin\n")
        os.chmod(os.path.join(bin_dir, "uname"), 0o755)
        home = os.path.join(self.tmp, "Users", "ana")
        env = dict(os.environ, HOME=home, PATH=bin_dir + os.pathsep + os.environ["PATH"])
        out = subprocess.run(["sh", os.path.join(folder, "install.sh")], capture_output=True, text=True, env=env,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        target = os.path.join(home, "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve",
                              "Fusion", "Scripts", "Utility")
        self.assertEqual(sorted(os.listdir(target)), ["NOLGIA.py", "nolgia_resolve.zip"])
        self.assertIn("Restart DaVinci Resolve", out.stdout)

    def test_sign_in_page_opens_with_webbrowser(self):
        from nolgia_resolve import runtime

        with mock.patch("webbrowser.open", return_value=True) as opened:
            runtime._open_url("https://nolgia.ai/device?user_code=ABCD-EFGH")
        opened.assert_called_once_with("https://nolgia.ai/device?user_code=ABCD-EFGH")

    def test_no_windows_only_calls(self):
        for root, _dirs, files in os.walk(support.PACKAGE_DIR):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(root, name), encoding="utf-8") as handle:
                        text = handle.read()
                    for bad in ("startfile", "C:\\\\", "winreg", "msvcrt.getch"):
                        self.assertFalse(bad in text, "%s uses %s" % (name, bad))
