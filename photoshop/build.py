#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Build the installable NOLGIA for Photoshop plugin (.ccx).

A .ccx is a zip of the plugin folder with manifest.json at its root; Adobe's
installer (double click, or UnifiedPluginInstallerAgent /install) takes it.

    python3 photoshop/build.py                 # dist/nolgia-photoshop-<version>.ccx
    python3 photoshop/build.py --dev-domain http://localhost:8765
        # a test build that may also reach a local mock API and may open its
        # own panel (for screenshots): dist/nolgia-photoshop-<version>-dev.ccx

Standard library only. Checks that the manifest's version matches
core/constants.js, that every file the plugin loads is included, and (when
Node is on PATH) that every JavaScript file parses.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# What goes into the package, relative to photoshop/.
INCLUDE_FILES = ("manifest.json", "index.html", "styles.css", "main.js", "LICENSE")
INCLUDE_DIRS = {"core": (".js",), "ps": (".js",), "ui": (".js",), "icons": (".png",)}


def plugin_version():
    with open(os.path.join(HERE, "core", "constants.js"), encoding="utf-8") as handle:
        match = re.search(r'PLUGIN_VERSION:\s*"([^"]+)"', handle.read())
    if not match:
        raise SystemExit("core/constants.js has no PLUGIN_VERSION")
    return match.group(1)


def package_files():
    files = [name for name in INCLUDE_FILES]
    for folder, exts in INCLUDE_DIRS.items():
        for name in sorted(os.listdir(os.path.join(HERE, folder))):
            if name.endswith(exts):
                files.append(folder + "/" + name)
    return files


def check_requires(files):
    """Every relative require() in the package points at a packaged file."""
    missing = []
    for rel in files:
        if not rel.endswith(".js"):
            continue
        with open(os.path.join(HERE, rel), encoding="utf-8") as handle:
            text = handle.read()
        for target in re.findall(r'require\("(\.[^"]+)"\)', text):
            resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target)).replace(os.sep, "/")
            if resolved not in files:
                missing.append("%s requires %s" % (rel, target))
    if missing:
        raise SystemExit("missing from the package:\n  " + "\n  ".join(missing))


def check_syntax(files):
    node = shutil.which("node")
    if not node:
        print("node not found: skipping the JavaScript syntax check")
        return
    for rel in files:
        if rel.endswith(".js"):
            out = subprocess.run([node, "--check", os.path.join(HERE, rel)], capture_output=True, text=True)
            if out.returncode != 0:
                raise SystemExit("%s does not parse:\n%s" % (rel, out.stderr))


def build(out_dir, dev_domains=()):
    version = plugin_version()
    with open(os.path.join(HERE, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest["version"] != version:
        raise SystemExit("manifest.json version %s != core/constants.js %s" % (manifest["version"], version))
    files = package_files()
    check_requires(files)
    check_syntax(files)
    name = "nolgia-photoshop-%s%s.ccx" % (version, "-dev" if dev_domains else "")
    if dev_domains:
        domains = manifest["requiredPermissions"]["network"]["domains"]
        for domain in dev_domains:
            if domain not in domains:
                domains.append(domain)
        # Lets the developer file's show_panel open the NOLGIA panel (UXP's
        # pluginManager needs it) so tests can look at the panel.
        manifest["requiredPermissions"]["ipc"] = {"enablePluginCommunication": True}
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, name)
    tmp = target + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as package:
        for rel in files:
            if rel == "manifest.json":
                package.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            else:
                package.write(os.path.join(HERE, rel), rel)
    os.replace(tmp, target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=os.path.join(HERE, "dist"), help="output folder (default photoshop/dist)")
    parser.add_argument("--dev-domain", action="append", default=[],
                        help="extra network domain for a test build, e.g. http://localhost:8765 (repeatable)")
    opts = parser.parse_args()
    print(build(opts.out, opts.dev_domain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
