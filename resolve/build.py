#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Build the NOLGIA for DaVinci Resolve download (Python 3, standard library).

    python3 resolve/build.py            # resolve/dist/nolgia-resolve-<version>.zip

The zip holds:

    Utility/NOLGIA.py              the Workspace > Scripts entry
    Utility/nolgia_resolve.zip     the plugin (the nolgia_resolve package)
    install.cmd                    Windows: copy both into Resolve's Scripts/Utility folder
    install.sh                     macOS and Linux: the same
    INSTALL.txt                    the install steps
    LICENSE

The plugin's modules are zipped so the only script in the Utility folder is
NOLGIA.py (Resolve lists the scripts it finds in its Scripts folders, and
their sub folders, in Workspace > Scripts). Builds are reproducible: entries
are sorted and dated 2026-01-01.
"""

import argparse
import ast
import io
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "nolgia_resolve"
LIBRARY = "nolgia_resolve.zip"
STAMP = (2026, 1, 1, 0, 0, 0)


def version():
    with open(os.path.join(HERE, PACKAGE, "core", "__init__.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PLUGIN_VERSION" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("PLUGIN_VERSION not found")


def package_files():
    root = os.path.join(HERE, PACKAGE)
    out = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                out.append((os.path.relpath(path, HERE).replace(os.sep, "/"), path))
    return out


def _add(archive, name, data, mode=0o644):
    info = zipfile.ZipInfo(name, STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100000 | mode) << 16
    archive.writestr(info, data)


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


def _crlf(data):
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def library_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, path in package_files():
            _add(archive, name, _read(path))
    return buffer.getvalue()


def build(out_dir=None):
    ver = version()
    out_dir = out_dir or os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "nolgia-resolve-%s.zip" % ver)
    install = os.path.join(HERE, "install")
    notes = _read(os.path.join(install, "INSTALL.txt")).replace(b"{version}", ver.encode("ascii"))
    entries = [
        ("INSTALL.txt", notes, 0o644),
        ("LICENSE", _read(os.path.join(HERE, "LICENSE")), 0o644),
        ("Utility/NOLGIA.py", _read(os.path.join(HERE, "NOLGIA.py")), 0o644),
        ("Utility/" + LIBRARY, library_bytes(), 0o644),
        ("install.sh", _read(os.path.join(install, "install.sh")), 0o755),
        # cmd.exe reads batch files most reliably with Windows line ends.
        ("install.cmd", _crlf(_read(os.path.join(install, "install.cmd"))), 0o644),
    ]
    tmp = target + ".part"
    with zipfile.ZipFile(tmp, "w") as archive:
        for name, data, mode in sorted(entries):
            _add(archive, name, data, mode)
    os.replace(tmp, target)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default=None, help="where to write the zip (default resolve/dist)")
    opts = parser.parse_args(argv)
    path = build(opts.out_dir)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
