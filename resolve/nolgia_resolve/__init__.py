# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""NOLGIA for DaVinci Resolve: let your NOLGIA agent work in the open project.

The plugin signs in to NOLGIA, connects out to it, and runs the commands your
agent sends (look at the project, run Python, export a preview still, import
and export files, save, open) while its window is open and Connected is on.

Inside Resolve: Workspace > Scripts > NOLGIA opens the window (`open_window`).
Outside it, with External scripting set to Local, `serve()` connects and
runs commands until switched off:

    ResolvePython NOLGIA.py --serve
"""

from .core import PLUGIN_VERSION

__all__ = ["PLUGIN_VERSION", "open_window", "serve"]


def open_window(resolve, fusion, bmd):
    """Show the NOLGIA window and run until it closes (blocks). When it is
    already open, bring it to the front instead."""
    from . import panel

    existing = panel.find_open_window(fusion)
    if existing is not None:
        existing.Show()
        try:
            existing.Raise()
        except Exception:
            pass
        return False
    return panel.Panel(resolve, fusion, bmd).run()


def serve(resolve=None):
    """Connect and run NOLGIA commands until switched off (blocks).

    For a script running next to DaVinci Resolve (External scripting set to
    Local, Resolve open with or without its window: `Resolve -nogui`). Uses
    NOLGIA_TOKEN and NOLGIA_API_URL from the environment. Returns True after a
    normal stop, False when it could not connect or lost the sign in.
    """
    from . import runtime

    return runtime.serve(resolve)
