# SPDX-License-Identifier: GPL-3.0-or-later
"""Manifest, license and wording rules."""

import ast
import os
import re
import unittest

import support

from core import PLUGIN_VERSION

EXT = support.EXT_DIR
EM_DASH = chr(0x2014)


def manifest():
    """blender_manifest.toml, read without tomllib (Python 3.10 has none)."""
    out, section = {}, ""
    with open(os.path.join(EXT, "blender_manifest.toml"), encoding="utf-8") as handle:
        text = handle.read()
    for line in re.sub(r"\[\s*\n(.*?)\]", lambda m: "[" + m.group(1).replace("\n", " ") + "]", text, flags=re.S).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and "=" not in line:
            section = line.strip("[]") + "."
            continue
        key, value = line.split("=", 1)
        out[section + key.strip()] = ast.literal_eval(value.strip())
    return out


def source_files():
    for root, dirs, files in os.walk(support.REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "dist")]
        for name in files:
            if name.endswith((".py", ".toml", ".md")):
                yield os.path.join(root, name)


def string_literals(path):
    """String constants in a Python file, leaving out docstrings (those are
    for developers and may show code)."""
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
            yield node.lineno, node.value


class Manifest(unittest.TestCase):
    def test_fields(self):
        m = manifest()
        self.assertEqual(m["id"], "nolgia")
        self.assertEqual(m["name"], "NOLGIA")
        self.assertEqual(m["maintainer"], "NOLGIA Inc")
        self.assertEqual(m["type"], "add-on")
        self.assertEqual(m["website"], "https://nolgia.ai/plugins/blender")
        self.assertEqual(m["license"], ["SPDX:GPL-3.0-or-later"])
        self.assertEqual(m["blender_version_min"], "4.2.0")
        self.assertEqual(m["version"], PLUGIN_VERSION)
        self.assertTrue(m["permissions.network"])
        self.assertTrue(m["permissions.files"])

    def test_license_is_the_full_gpl3(self):
        with open(os.path.join(EXT, "LICENSE"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 29 June 2007", text)
        self.assertIn("END OF TERMS AND CONDITIONS", text)
        self.assertGreater(len(text), 30000)


class Wording(unittest.TestCase):
    def test_no_em_dashes(self):
        for path in source_files():
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    self.assertNotIn(EM_DASH, line, "%s:%d has an em dash" % (path, number))

    def test_nolgia_in_caps_in_customer_text(self):
        m = manifest()
        texts = [("blender_manifest.toml", m[key]) for key in
                 ("name", "tagline", "maintainer", "permissions.network", "permissions.files")]
        texts += [("blender_manifest.toml", c) for c in m["copyright"]]
        for name in ("ui.py", "runtime.py", "ops.py", "__init__.py"):
            texts += [(name, t) for _, t in string_literals(os.path.join(EXT, name))]
        for name, text in texts:
            for match in re.finditer("nolgia", text, re.IGNORECASE):
                word = match.group(0)
                if word == "NOLGIA":
                    continue
                before = text[max(0, match.start() - 1):match.start()]
                after = text[match.end():match.end() + 1]
                # Lower case is fine inside identifiers: nolgia.sign_in,
                # nolgia-preview-, nolgia_assets, bl_ext.user_default.nolgia.
                if word == "nolgia" and (before in (".", "_", "-", "/", "'") or after in (".", "_", "-", "/", "'")):
                    continue
                self.fail("%s: %r should say NOLGIA" % (name, text))

if __name__ == "__main__":
    unittest.main()
