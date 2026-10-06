# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Where things live on each system. No Resolve calls here.

- scripts_dir(): the per-user Fusion Scripts folder Resolve lists under
  Workspace > Scripts (the plugin goes in its Utility folder).
- config_dir(): NOLGIA's own per-user folder for the settings, the sign in,
  the session id and the log.
- lut_dirs(): the LUT folders Resolve reads LUTs from.
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


def lut_dirs(platform=None, env=None):
    """The LUT folders Resolve reads, in the order the plugin tries them.

    NOLGIA_LUT_DIR, then BMD_RESOLVE_LUT_DIR (Resolve's own setting for its
    LUT folder), then Resolve's LUT folder for the system, from Resolve's
    "User Configuration folders" notes: on Windows
    %PROGRAMDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\LUT (the only
    one Resolve 21.1 on Windows was seen to read; its installer lets every
    user write there). On macOS and Linux the per-user folder comes after
    the system one, for when the system one is not writable.
    """
    env = os.environ if env is None else env
    out = [env[k] for k in ("NOLGIA_LUT_DIR", "BMD_RESOLVE_LUT_DIR") if env.get(k)]
    name = platform_name(platform)
    if name == "windows":
        programdata = env.get("PROGRAMDATA") or env.get("ALLUSERSPROFILE") or os.path.join(
            os.path.splitdrive(_home())[0] + os.sep, "ProgramData")
        out.append(os.path.join(programdata, "Blackmagic Design", "DaVinci Resolve", "Support", "LUT"))
    elif name == "macos":
        out.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")
        out.append(os.path.join(_home(), "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve",
                                "LUT"))
    else:
        out += ["/opt/resolve/LUT", "/home/resolve/LUT", os.path.join(_home(), ".local", "share", "DaVinciResolve",
                                                                     "LUT")]
    seen, unique = set(), []
    for folder in out:
        if folder not in seen:
            seen.add(folder)
            unique.append(folder)
    return unique


def lut_dir(platform=None, env=None):
    """The first of lut_dirs()."""
    return lut_dirs(platform, env)[0]


def documents_dir():
    home = _home()
    docs = os.path.join(home, "Documents")
    return docs if os.path.isdir(docs) else home


def import_dir(env=None):
    """NOLGIA_IMPORT_DIR, else Documents/NOLGIA imports."""
    env = os.environ if env is None else env
    return env.get("NOLGIA_IMPORT_DIR") or os.path.join(documents_dir(), "NOLGIA imports")
