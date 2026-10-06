# SPDX-License-Identifier: GPL-3.0-or-later
"""License and wording rules for NOLGIA for DaVinci Resolve."""

import ast
import os
import re
import unittest

import support

EM_DASH = chr(0x2014)
PLUGIN = support.PLUGIN_DIR
CUSTOMER_FILES = ["NOLGIA.py", "nolgia_resolve/__init__.py", "nolgia_resolve/panel.py", "nolgia_resolve/runtime.py",
                  "nolgia_resolve/ops.py", "nolgia_resolve/core/commands.py"]


def plugin_files():
    for root, dirs, files in os.walk(PLUGIN):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "dist")]
        for name in files:
            yield os.path.join(root, name)


def string_literals(path):
    """String constants in a Python file, leaving out docstrings."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def resolve_readme():
    with open(os.path.join(support.REPO, "README.md"), encoding="utf-8") as handle:
        text = handle.read()
    start = text.index("## NOLGIA for DaVinci Resolve")
    end = text.find("\n## ", start + 10)
    return text[start:end if end > 0 else None]


class License(unittest.TestCase):
    def test_full_gpl3(self):
        with open(os.path.join(PLUGIN, "LICENSE"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 29 June 2007", text)
        self.assertIn("END OF TERMS AND CONDITIONS", text)

    def test_every_source_file_says_gpl(self):
        for path in plugin_files():
            if path.endswith((".py", ".sh", ".cmd")):
                with open(path, encoding="utf-8") as handle:
                    head = handle.read(400)
                if path.endswith(".py"):
                    self.assertIn("SPDX-License-Identifier: GPL-3.0-or-later", head, path)

    def test_listed_in_the_readme(self):
        with open(os.path.join(support.REPO, "README.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("| [`resolve/`](resolve/) | DaVinci Resolve Studio 21.1 and newer | GPL-3.0-or-later "
                      "([`resolve/LICENSE`](resolve/LICENSE)) |", text)


class Wording(unittest.TestCase):
    def test_no_em_dashes(self):
        paths = [p for p in plugin_files() if p.endswith((".py", ".txt", ".sh", ".cmd", ".md"))]
        paths.append(os.path.join(support.REPO, "tools", "mock_bridge_server.py"))
        texts = []
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                texts.append((path, handle.read()))
        texts.append(("README.md (Resolve section)", resolve_readme()))
        for name, text in texts:
            self.assertNotIn(EM_DASH, text, "%s has an em dash" % name)

    def test_nolgia_in_caps_in_customer_text(self):
        texts = []
        for name in CUSTOMER_FILES:
            texts += [(name, t) for t in string_literals(os.path.join(PLUGIN, name))]
        for name in ("INSTALL.txt", "install-windows.cmd", "install-unix.sh"):
            with open(os.path.join(PLUGIN, "install", name), encoding="utf-8") as handle:
                texts += [(name, line) for line in handle if not line.startswith(("rem ", "#"))]
        texts.append(("README", resolve_readme()))
        for name, text in texts:
            for match in re.finditer("nolgia", text, re.IGNORECASE):
                if match.group(0) == "NOLGIA":
                    continue
                before = text[max(0, match.start() - 1):match.start()]
                after = text[match.end():match.end() + 1]
                # Lower case is fine in names: nolgia_resolve, nolgia.ai, nolgia-preview-, NOLGIA_TOKEN ...
                if before in (".", "_", "-", "/", "'", "`", "\\", "\"") or after in (".", "_", "-", "/", "'", "`"):
                    continue
                if after.isalnum():  # an identifier such as NolgiaTick
                    continue
                if match.group(0) == "Nolgia" and after == " " and before == "\"":
                    continue
                self.fail("%s: %r should say NOLGIA" % (name, text[max(0, match.start() - 40):match.end() + 40]))

    def test_windows_installer_has_no_unix_only_bits(self):
        with open(os.path.join(PLUGIN, "install", "install-windows.cmd"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("%APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility", text)
        self.assertNotIn("$HOME", text)


if __name__ == "__main__":
    unittest.main()
