# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Shared setup for the unit tests: import paths and a mock API server."""

import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)          # resolve/
REPO = os.path.dirname(PLUGIN_DIR)
PACKAGE_DIR = os.path.join(PLUGIN_DIR, "nolgia_resolve")
for path in (PLUGIN_DIR, HERE, os.path.join(REPO, "tools")):
    if path not in sys.path:
        sys.path.insert(0, path)

from mock_bridge_server import MockBridgeServer  # noqa: E402

TOKEN = "unit-token"


def start_mock(**kwargs):
    kwargs.setdefault("tokens", (TOKEN,))
    return MockBridgeServer(**kwargs).start()


def call(server, method, path, body=None, token=TOKEN, headers=None):
    """Talk to the mock as the caller side (the MCP server) would."""
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(server.root_url + path, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if body is not None and not isinstance(body, bytes):
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            kind = resp.headers.get_content_type()
            return resp.status, (json.loads(raw) if raw and kind.endswith("json") else raw)
    except urllib.error.HTTPError as err:
        raw = err.read()
        return err.code, (json.loads(raw) if raw else None)
