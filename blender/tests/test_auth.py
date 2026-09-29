# SPDX-License-Identifier: GPL-3.0-or-later
"""Device login: the same polling rules as the NOLGIA CLI."""

import threading
import unittest

import support

from core import DEVICE_CLIENT_ID
from core.api import ApiClient, ApiError, NetworkError
from core.auth import DeviceLogin, LoginCancelled, LoginError


class FakeApi:
    """Answers polls from a script; records nothing else."""

    def __init__(self, answers, interval=5, expires_in=900):
        self.answers = list(answers)
        self.interval = interval
        self.expires_in = expires_in
        self.polls = 0

    def start_device_auth(self, client_id, scope):
        assert client_id == DEVICE_CLIENT_ID
        return {"device_code": "dev-1", "user_code": "ABCD-EFGH",
                "verification_uri": "https://nolgia.ai/device",
                "verification_uri_complete": "https://nolgia.ai/device?user_code=ABCD-EFGH",
                "expires_in": self.expires_in, "interval": self.interval}

    def poll_device_token(self, client_id, device_code):
        assert device_code == "dev-1"
        self.polls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def oauth(status, code):
    return ApiError(status, title=code, code=code)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []


class RecordingLogin(DeviceLogin):
    """Sleeps are recorded instead of waited, and move a fake clock."""

    def __init__(self, api, clock):
        super().__init__(api, clock=lambda: clock.now)
        self._clock_obj = clock

    def _sleep(self, seconds):
        if self.cancel_event.is_set():
            raise LoginCancelled("Sign in cancelled.")
        self._clock_obj.slept.append(seconds)
        self._clock_obj.now += seconds


TOKEN = {"access_token": "nol_abc", "token_type": "Bearer", "expires_in": 2592000,
         "email": "ada@nolgia.ai", "user_id": "u-1"}


class DeviceLoginTest(unittest.TestCase):
    def run_login(self, answers, **kwargs):
        clock = FakeClock()
        login = RecordingLogin(FakeApi(answers, **kwargs), clock)
        prompt = login.start()
        return login, prompt, clock

    def test_pending_then_token(self):
        login, prompt, clock = self.run_login([oauth(400, "authorization_pending"), dict(TOKEN)])
        self.assertEqual(prompt.user_code, "ABCD-EFGH")
        self.assertEqual(prompt.open_url, "https://nolgia.ai/device?user_code=ABCD-EFGH")
        token = login.wait_for_token(prompt)
        self.assertEqual((token.access_token, token.email), ("nol_abc", "ada@nolgia.ai"))
        self.assertEqual(token.expires_at, clock.now + 2592000)
        self.assertEqual(clock.slept, [5, 5], "sleeps the interval before every poll, like the CLI")

    def test_slow_down_adds_five_seconds(self):
        login, prompt, clock = self.run_login(
            [oauth(400, "slow_down"), oauth(400, "authorization_pending"), dict(TOKEN)])
        login.wait_for_token(prompt)
        self.assertEqual(clock.slept, [5, 10, 10])

    def test_expired_and_denied(self):
        login, prompt, _ = self.run_login([oauth(400, "expired_token")])
        with self.assertRaisesRegex(LoginError, "expired"):
            login.wait_for_token(prompt)
        login, prompt, _ = self.run_login([oauth(403, "access_denied")])
        with self.assertRaisesRegex(LoginError, "declined"):
            login.wait_for_token(prompt)

    def test_other_403_keeps_polling_like_the_cli(self):
        login, prompt, _ = self.run_login([oauth(403, "forbidden"), dict(TOKEN)])
        self.assertEqual(login.wait_for_token(prompt).access_token, "nol_abc")

    def test_network_blips_retry(self):
        login, prompt, _ = self.run_login([NetworkError("down"), dict(TOKEN)])
        self.assertEqual(login.wait_for_token(prompt).access_token, "nol_abc")

    def test_gives_up_when_the_code_runs_out(self):
        answers = [oauth(400, "authorization_pending")] * 10
        login, prompt, _ = self.run_login(answers, interval=5, expires_in=12)
        with self.assertRaisesRegex(LoginError, "expired"):
            login.wait_for_token(prompt)

    def test_cancel(self):
        login, prompt, _ = self.run_login([oauth(400, "authorization_pending")] * 5)
        login.cancel()
        with self.assertRaises(LoginCancelled):
            login.wait_for_token(prompt)

    def test_start_failure_is_plain_words(self):
        class Down(FakeApi):
            def start_device_auth(self, client_id, scope):
                raise NetworkError("connection refused")
        with self.assertRaisesRegex(LoginError, "Could not reach NOLGIA"):
            DeviceLogin(Down([])).start()


class DeviceLoginAgainstMock(unittest.TestCase):
    def test_full_flow_with_real_sleeps(self):
        server = support.start_mock(device_interval=1)
        try:
            login = DeviceLogin(ApiClient(server.base_url))
            prompt = login.start()
            self.assertIn("/device?user_code=", prompt.open_url)
            # The person approves in the browser a moment later.
            threading.Timer(0.3, lambda: support.call(
                server, "POST", "/mock/device/approve", {"user_code": prompt.user_code}, token=None)).start()
            token = login.wait_for_token(prompt)
            self.assertTrue(token.access_token.startswith("nol_"))
            # The new token works for the bridge.
            status, _ = support.call(server, "GET", "/v1/bridge/sessions", token=token.access_token)
            self.assertEqual(status, 200)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
