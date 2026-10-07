# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Pure Python parts of the NOLGIA plugin for Blender.

Nothing in this package imports bpy, so it runs (and is tested) with a plain
Python interpreter. The Blender side lives one level up.
"""

PLUGIN_VERSION = "0.1.1"
APP = "blender"
DEFAULT_API_URL = "https://api.nolgia.ai/v1"
DEVICE_CLIENT_ID = "nolgia-blender"
DEVICE_SCOPE = "bridge assets:read assets:write"
