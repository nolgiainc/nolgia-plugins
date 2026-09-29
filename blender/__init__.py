# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""NOLGIA for Blender: let your NOLGIA agent work in the open scene.

The plugin signs in to NOLGIA, connects out to it, and runs the commands your
agent sends (look at the scene, run Python, render a preview, import and
export files, save, open) while you have it switched on.

Scripts and render machines without a window can use it too:

    NOLGIA_TOKEN=... blender -b shot.blend --python-expr \\
      "import addon_utils; addon_utils.enable('bl_ext.user_default.nolgia', default_set=True); \\
       import bl_ext.user_default.nolgia as nolgia; nolgia.serve()"
"""

from . import runtime, ui


def register():
    ui.register()
    runtime.register()


def unregister():
    runtime.unregister()
    ui.unregister()


def serve():
    """Connect and run NOLGIA commands until switched off (blocks).

    For Blender without a window (`blender --background`). Uses NOLGIA_TOKEN
    and NOLGIA_API_URL from the environment. Returns True after a normal
    switch off, False when it could not connect or lost the sign in.
    """
    return runtime.controller.serve()


def connect():
    """Switch on, as the Connected toggle does. Returns True when started."""
    return runtime.controller.connect()


def disconnect():
    """Switch off, as the Connected toggle does."""
    runtime.controller.disconnect()


def status():
    """The status line shown in the NOLGIA panel."""
    return runtime.controller.status_line()
