# SPDX-License-Identifier: GPL-3.0-or-later
"""resolve/nolgia_resolve/core is a copy of blender/core. This test fails as
soon as the two drift apart, so a fix made in one is made in the other.

The only differences allowed, and why:

1. The app's name in text the person or the agent reads: the Resolve copy
   says "DaVinci Resolve" where the Blender one says "Blender".
2. __init__.py: the docstring and the per-app constants (APP,
   DEVICE_CLIENT_ID, PLUGIN_VERSION). The API address and the sign in scope
   stay the same.
3. commands.py: the argument checks, because each app takes its own
   arguments: `validate()` and the names listed in PER_APP_COMMANDS.

Everything else must be the same, byte for byte.
"""

import ast
import os
import re
import unittest

import support

BLENDER_CORE = os.path.join(support.REPO, "blender", "core")
RESOLVE_CORE = os.path.join(support.PACKAGE_DIR, "core")

PER_APP_INIT = {"APP", "DEVICE_CLIENT_ID", "PLUGIN_VERSION"}
PER_APP_COMMANDS = {
    "validate",
    # Blender
    "PREVIEW_ENGINES", "IMPORT_AS",
    # both, with different values
    "EXPORT_FORMATS",
    # DaVinci Resolve
    "LUT_TARGETS", "_bool", "_timecode", "_slug", "_ids",
}


def read(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as handle:
        return handle.read()


def py_files(folder):
    return sorted(n for n in os.listdir(folder) if n.endswith(".py"))


def defined_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def without(source, names):
    """The source with the top-level definitions of `names` cut out, and runs
    of blank lines made one, so only the rest is compared."""
    lines = source.splitlines(keepends=True)
    drop = set()
    for node in ast.parse(source).body:
        found = defined_names(node)
        if found and found <= names:
            start = node.lineno - 1
            if getattr(node, "decorator_list", None):
                start = min(d.lineno for d in node.decorator_list) - 1
            drop.update(range(start, node.end_lineno))
    kept = "".join(line for i, line in enumerate(lines) if i not in drop)
    return re.sub(r"\n{3,}", "\n\n", kept)


def as_blender(text):
    return text.replace("DaVinci Resolve", "Blender")


class CoreCopy(unittest.TestCase):
    maxDiff = 4000

    def test_same_files(self):
        self.assertEqual(py_files(BLENDER_CORE), py_files(RESOLVE_CORE))

    def test_files_are_the_same_but_for_the_app_name(self):
        for name in py_files(BLENDER_CORE):
            if name in ("__init__.py", "commands.py"):
                continue
            with self.subTest(file=name):
                self.assertEqual(
                    as_blender(read(RESOLVE_CORE, name)), read(BLENDER_CORE, name),
                    "resolve/nolgia_resolve/core/%s drifted from blender/core/%s; copy the change over" % (name, name),
                )

    def test_commands_differ_only_in_the_argument_checks(self):
        blender = without(read(BLENDER_CORE, "commands.py"), PER_APP_COMMANDS)
        resolve = without(as_blender(read(RESOLVE_CORE, "commands.py")), PER_APP_COMMANDS)
        self.assertEqual(resolve, blender)

    def test_init_differs_only_in_the_app_constants(self):
        def constants(folder):
            tree = ast.parse(read(folder, "__init__.py"))
            out = {}
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        out[target.id] = ast.literal_eval(node.value)
            return out

        blender, resolve = constants(BLENDER_CORE), constants(RESOLVE_CORE)
        self.assertEqual(set(blender), set(resolve))
        for key in set(blender) - PER_APP_INIT:
            self.assertEqual(resolve[key], blender[key], key)
        self.assertEqual(resolve["APP"], "resolve")
        self.assertEqual(resolve["DEVICE_CLIENT_ID"], "nolgia-resolve")

    def test_the_rule_catches_a_drift(self):
        # A change outside the allowed places shows up.
        source = read(RESOLVE_CORE, "commands.py").replace("MAX_ERROR_CHARS = 64 * 1024", "MAX_ERROR_CHARS = 1")
        self.assertNotEqual(without(as_blender(source), PER_APP_COMMANDS),
                            without(read(BLENDER_CORE, "commands.py"), PER_APP_COMMANDS))


if __name__ == "__main__":
    unittest.main()
