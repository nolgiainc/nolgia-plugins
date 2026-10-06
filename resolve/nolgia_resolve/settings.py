# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""The panel's switches and the sign in, kept in settings.json in the
per-user NOLGIA folder (paths.config_dir()).

The file is written whole (to a temp file, then renamed), readable only by
you: mode 0600 on macOS and Linux; on Windows the folder sits in your own
%APPDATA%, which other users cannot read. The token is never logged.
"""

import json
import os
import tempfile
import threading

DEFAULTS = {
    "token": "",
    "token_api_url": "",
    "token_expires_at": 0.0,
    "account_email": "",
    "connected": False,
    "allow_agent": True,
    "ask_before_run": False,
}

SECRET_KEYS = ("token",)


class Settings:
    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "settings.json")
        self._lock = threading.Lock()
        self._values = dict(DEFAULTS)
        self.load_error = None
        self.load()

    def load(self):
        values = dict(DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                for key, default in DEFAULTS.items():
                    value = data.get(key, default)
                    if isinstance(default, bool):
                        value = bool(value) if isinstance(value, bool) else default
                    elif isinstance(default, float):
                        value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
                    elif isinstance(default, str):
                        value = value if isinstance(value, str) else default
                    values[key] = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as err:
            self.load_error = str(err)
        with self._lock:
            self._values = values

    def get(self, key):
        with self._lock:
            return self._values[key]

    def __getitem__(self, key):
        return self.get(key)

    def update(self, **changes):
        """Change and save. Returns True when something changed."""
        for key in changes:
            if key not in DEFAULTS:
                raise KeyError(key)
        with self._lock:
            before = dict(self._values)
            self._values.update(changes)
            changed = before != self._values
            values = dict(self._values)
        if changed:
            self._write(values)
        return changed

    def public(self):
        """The values without the token, for logs and tests."""
        with self._lock:
            return {k: ("***" if k in SECRET_KEYS and v else v) for k, v in self._values.items()}

    def _write(self, values):
        os.makedirs(self.folder, exist_ok=True)
        _private_folder(self.folder)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=self.folder)
        try:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(values, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def _private_folder(folder):
    if os.name != "nt":
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass


class Log:
    """NOLGIA's log: printed (to the console of the program running it) and appended to
    nolgia.log in the config folder, cut back when it passes `limit` bytes.
    Callers never pass the token."""

    def __init__(self, folder, limit=512 * 1024, echo=True):
        self.path = os.path.join(folder, "nolgia.log") if folder else None
        self.limit = limit
        self.echo = echo
        self._lock = threading.Lock()

    def __call__(self, message):
        import time

        line = "NOLGIA: %s" % message
        if self.echo:
            try:
                print(line, flush=True)
            except Exception:
                pass
        if not self.path:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                if os.path.exists(self.path) and os.path.getsize(self.path) > self.limit:
                    with open(self.path, "rb") as handle:
                        handle.seek(-self.limit // 2, os.SEEK_END)
                        tail = handle.read()
                    with open(self.path, "wb") as handle:
                        handle.write(tail[tail.find(b"\n") + 1:])
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write("%s %s\n" % (stamp, message))
            except OSError:
                pass
