# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Where things live on each system. No Resolve calls here.

- scripts_dir(): the per-user Fusion Scripts folder Resolve lists under
  Workspace > Scripts (the plugin goes in its Utility folder).
- config_dir(): NOLGIA's own per-user folder for the settings, the sign in,
  the session id and the log.
- lut_dir(): the per-user LUT folder Resolve reads LUTs from.
- import_dir(): where downloaded media is kept, since Resolve's media pool
  points at files on disk.
"""

import os
import sys

APP_DIR_NAME = "resolve"


def platform_name(platform=None):
    platform = platform or sys.platform
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    return "linux"


def _home():
    return os.path.expanduser("~")


def _appdata(env):
    return env.get("APPDATA") or os.path.join(_home(), "AppData", "Roaming")


def scripts_dir(platform=None, env=None):
    """The per-user Scripts folder from Resolve's scripting README."""
    env = os.environ if env is None else env
    name = platform_name(platform)
    if name == "windows":
        return os.path.join(_appdata(env), "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts")
    if name == "macos":
        return os.path.join(_home(), "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve",
                            "Fusion", "Scripts")
    return os.path.join(_home(), ".local", "share", "DaVinciResolve", "Fusion", "Scripts")


def utility_dir(platform=None, env=None):
    return os.path.join(scripts_dir(platform, env), "Utility")


def config_dir(platform=None, env=None):
    """NOLGIA_CONFIG_DIR, else %APPDATA%\\NOLGIA\\resolve on Windows,
    ~/Library/Application Support/NOLGIA/resolve on macOS and
    $XDG_CONFIG_HOME/nolgia/resolve (~/.config/nolgia/resolve) on Linux."""
    env = os.environ if env is None else env
    if env.get("NOLGIA_CONFIG_DIR"):
        return env["NOLGIA_CONFIG_DIR"]
    name = platform_name(platform)
    if name == "windows":
        return os.path.join(_appdata(env), "NOLGIA", APP_DIR_NAME)
    if name == "macos":
        return os.path.join(_home(), "Library", "Application Support", "NOLGIA", APP_DIR_NAME)
    base = env.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")
    return os.path.join(base, "nolgia", APP_DIR_NAME)


def lut_dir(platform=None, env=None):
    """The per-user LUT folder (Project Settings > Color Management >
    Open User LUT Folder). NOLGIA_LUT_DIR overrides it."""
    env = os.environ if env is None else env
    if env.get("NOLGIA_LUT_DIR"):
        return env["NOLGIA_LUT_DIR"]
    name = platform_name(platform)
    if name == "windows":
        return os.path.join(_appdata(env), "Blackmagic Design", "DaVinci Resolve", "Support", "LUT")
    if name == "macos":
        return os.path.join(_home(), "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve", "LUT")
    return os.path.join(_home(), ".local", "share", "DaVinciResolve", "LUT")


def documents_dir():
    home = _home()
    docs = os.path.join(home, "Documents")
    return docs if os.path.isdir(docs) else home


def import_dir(env=None):
    """NOLGIA_IMPORT_DIR, else Documents/NOLGIA imports."""
    env = os.environ if env is None else env
    return env.get("NOLGIA_IMPORT_DIR") or os.path.join(documents_dir(), "NOLGIA imports")
