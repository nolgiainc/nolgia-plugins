# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""NOLGIA for DaVinci Resolve.

In DaVinci Resolve Studio: Workspace > Scripts > NOLGIA opens the NOLGIA
window, where you sign in and switch NOLGIA on.

Next to a running Resolve (External scripting set to Local), for render
machines, scripts and tests:

    ResolvePython NOLGIA.py --serve

The plugin itself is in nolgia_resolve.zip next to this file.
"""

import os
import sys

LIBRARY = "nolgia_resolve.zip"
PACKAGE = "nolgia_resolve"


def _folders():
    here = globals().get("__file__")
    if isinstance(here, str) and here:
        yield os.path.dirname(os.path.abspath(here))
    # Resolve runs menu scripts in its fuscript program without __file__,
    # with the script's path in sys.argv[0].
    argv = list(getattr(sys, "argv", None) or [])
    if argv and isinstance(argv[0], str) and os.path.isfile(argv[0]):
        yield os.path.dirname(os.path.abspath(argv[0]))
    # Else look where the installer puts it.
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA") or os.path.expanduser(os.path.join("~", "AppData", "Roaming"))
        programdata = os.environ.get("PROGRAMDATA") or "C:\\ProgramData"
        yield os.path.join(appdata, "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility")
        yield os.path.join(programdata, "Blackmagic Design", "DaVinci Resolve", "Fusion", "Scripts", "Utility")
    elif sys.platform == "darwin":
        tail = os.path.join("Blackmagic Design", "DaVinci Resolve", "Fusion", "Scripts", "Utility")
        yield os.path.join(os.path.expanduser("~/Library/Application Support"), tail)
        yield os.path.join("/Library/Application Support", tail)
    else:
        yield os.path.expanduser("~/.local/share/DaVinciResolve/Fusion/Scripts/Utility")
        yield "/opt/resolve/Fusion/Scripts/Utility"


def load():
    """Import the plugin from nolgia_resolve.zip (or a nolgia_resolve folder,
    when running from a copy of the source)."""
    for folder in _folders():
        for entry in (os.path.join(folder, LIBRARY), folder):
            found = os.path.isfile(entry) if entry.endswith(".zip") else \
                os.path.isfile(os.path.join(entry, PACKAGE, "__init__.py"))
            if found:
                if entry not in sys.path:
                    sys.path.insert(0, entry)
                import nolgia_resolve

                return nolgia_resolve
    raise ImportError("NOLGIA: %s is missing. Put it next to NOLGIA.py in Resolve's Scripts/Utility folder." % LIBRARY)


def main(scope):
    if sys.version_info < (3, 8):
        print("NOLGIA needs Python 3.8 or newer; DaVinci Resolve is using Python %s." % sys.version.split()[0])
        return
    args = list(getattr(sys, "argv", None) or [])[1:]
    if "--serve" in args:
        sys.exit(0 if load().serve() else 3)
    resolve_app = scope.get("resolve")
    fusion_app = scope.get("fusion") or scope.get("fu")
    bmd_module = scope.get("bmd")
    if resolve_app is None or fusion_app is None or bmd_module is None:
        print("NOLGIA: open this from DaVinci Resolve (Workspace > Scripts > NOLGIA), "
              "or run it with --serve next to a running Resolve.")
        sys.exit(2)
    load().open_window(resolve_app, fusion_app, bmd_module)


main(globals())
