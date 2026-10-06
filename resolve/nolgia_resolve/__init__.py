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

__all__ = ["PLUGIN_VERSION", "open_window", "close_window", "serve", "connect", "disconnect", "status"]


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


def close_window():
    """Close the NOLGIA window (which switches NOLGIA off), as its close
    button does. From `run` code the window closes once the command is done."""
    from . import panel

    if panel.current is None or panel.current._closed:
        return False
    win = panel.current
    win.controller.events.put(win.close)
    return True


def serve(resolve=None):
    """Connect and run NOLGIA commands until switched off (blocks).

    For a script running next to DaVinci Resolve (External scripting set to
    Local, Resolve open with or without its window: `Resolve -nogui`). Uses
    NOLGIA_TOKEN and NOLGIA_API_URL from the environment. Returns True after a
    normal stop, False when it could not connect or lost the sign in.
    """
    from . import runtime

    return runtime.serve(resolve)


def _controller():
    from . import runtime

    if runtime.current is None:
        raise RuntimeError("NOLGIA is not running in this DaVinci Resolve.")
    return runtime.current


def connect():
    """Switch on, as the Connected switch does. Returns True when started."""
    return _controller().connect()


def disconnect():
    """Switch off, as the Connected switch does. Without the window, serve()
    then returns."""
    _controller().disconnect()


def status():
    """The status line shown in the NOLGIA window."""
    return _controller().status_line()
