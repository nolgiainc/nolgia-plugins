# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Pure Python parts of the NOLGIA plugin for DaVinci Resolve.

A copy of blender/core: the same files, kept the same by
resolve/tests/test_core_copy.py, which lists the few places they may differ
(this file's constants, the per-app argument checks in commands.py, and the
app's name in messages). Nothing in this package talks to Resolve.
"""

PLUGIN_VERSION = "0.1.0"
APP = "resolve"
DEFAULT_API_URL = "https://api.nolgia.ai/v1"
DEVICE_CLIENT_ID = "nolgia-resolve"
DEVICE_SCOPE = "bridge assets:read assets:write"
