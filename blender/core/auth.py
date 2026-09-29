# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Device login, the same flow the NOLGIA CLI uses.

1. POST /auth/device gives a user code and a page to approve it on.
2. The person approves the code in their browser.
3. We poll POST /auth/device/token every `interval` seconds; the API answers
   400 authorization_pending until then, 400 slow_down when we poll too fast
   (we add 5 s to the interval, like the CLI), 400 expired_token when the
   code ran out, and 403 access_denied when the person said no.

Device tokens last 30 days and come without a refresh token, so there is no
refresh step: when the API stops accepting the token, the plugin asks the
person to sign in again.
"""

import threading
import time

from . import DEVICE_CLIENT_ID, DEVICE_SCOPE
from .api import ApiError, NetworkError


class LoginError(Exception):
    """Sign in did not finish. The message is written for the person."""


class LoginCancelled(LoginError):
    pass


class DevicePrompt:
    def __init__(self, data):
        self.device_code = data["device_code"]
        self.user_code = data["user_code"]
        self.verification_uri = data["verification_uri"]
        self.verification_uri_complete = data.get("verification_uri_complete") or None
        self.expires_in = int(data.get("expires_in") or 900)
        self.interval = max(1, int(data.get("interval") or 5))

    @property
    def open_url(self):
        return self.verification_uri_complete or self.verification_uri


class Token:
    def __init__(self, access_token, expires_at=None, email=None, user_id=None):
        self.access_token = access_token
        self.expires_at = expires_at  # unix seconds, or None
        self.email = email
        self.user_id = user_id


class DeviceLogin:
    def __init__(self, api, client_id=DEVICE_CLIENT_ID, scope=DEVICE_SCOPE,
                 clock=time.time, cancel_event=None):
        self.api = api
        self.client_id = client_id
        self.scope = scope
        self.clock = clock
        self.cancel_event = cancel_event or threading.Event()
        self.interval = None

    def start(self):
        try:
            data = self.api.start_device_auth(self.client_id, self.scope)
        except NetworkError as err:
            raise LoginError("Could not reach NOLGIA to sign in (%s)." % err) from None
        except ApiError as err:
            raise LoginError("NOLGIA did not start the sign in: %s" % (err.detail or err.title)) from None
        prompt = DevicePrompt(data)
        self.interval = prompt.interval
        return prompt

    def cancel(self):
        self.cancel_event.set()

    def _sleep(self, seconds):
        if self.cancel_event.wait(seconds):
            raise LoginCancelled("Sign in cancelled.")

    def wait_for_token(self, prompt):
        """Poll until the person approves. Blocks; run it off the main thread."""
        deadline = self.clock() + prompt.expires_in
        self.interval = prompt.interval
        network_failures = 0
        while True:
            if self.clock() >= deadline:
                raise LoginError("The sign in code expired. Click Sign in to get a new one.")
            self._sleep(self.interval)
            try:
                data = self.api.poll_device_token(self.client_id, prompt.device_code)
            except NetworkError:
                network_failures += 1
                self._sleep(min(30, 2 * network_failures))
                continue
            except ApiError as err:
                network_failures = 0
                code = err.code or err.title
                if code == "authorization_pending":
                    continue
                if code == "slow_down":
                    self.interval += 5
                    continue
                if code == "expired_token":
                    raise LoginError("The sign in code expired. Click Sign in to get a new one.") from None
                if code == "access_denied":
                    raise LoginError("Sign in was declined in the browser.") from None
                if err.status == 403:
                    continue
                raise LoginError("Sign in failed: %s" % (err.detail or err.title or err.status)) from None
            token = (data or {}).get("access_token")
            if not token:
                raise LoginError("NOLGIA answered without a token. Try signing in again.")
            expires_in = (data or {}).get("expires_in")
            expires_at = self.clock() + int(expires_in) if expires_in else None
            # The documented token response has no email; the plugin reads it
            # from GET /me afterwards. Use it if a server sends it anyway.
            return Token(token, expires_at, data.get("email"), data.get("user_id"))
