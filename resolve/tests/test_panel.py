# SPDX-License-Identifier: GPL-3.0-or-later
"""The NOLGIA window, against a stand-in UIManager: every state draws, the
buttons and switches do what they say, and code waits for Approve."""

import shutil
import tempfile
import time
import unittest

import support
import fake_resolve as fake
import fake_ui

from nolgia_resolve import panel as panel_module
from nolgia_resolve.runtime import Controller
from nolgia_resolve.settings import Settings


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.server = support.start_mock(poll_wait_seconds=2, device_interval=1, auto_approve_after=1)
        self.tmp = tempfile.mkdtemp(prefix="nolgia-panel-")
        self.env = {"NOLGIA_API_URL": self.server.base_url, "NOLGIA_CONFIG_DIR": self.tmp}
        self.fusion, self.bmd = fake_ui.Fusion(), fake_ui.Bmd()
        self.opened = []
        self.lines = []
        self.controller = Controller(fake.sample(), has_window=True, env=self.env, log=self.lines.append,
                                     open_url=self.opened.append)
        self.panel = panel_module.Panel(None, self.fusion, self.bmd, controller=self.controller)
        self.win = self.panel.build()
        self.disp = self.bmd.dispatchers[0]

    def tearDown(self):
        self.controller.shutdown(wait=5)
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def item(self, element_id):
        return self.win.Find(element_id)

    def visible(self, element_id):
        return not self.item(element_id).Hidden

    def tick_until(self, predicate, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            self.disp.tick(panel_module.TIMER_ID)
            if predicate():
                return
            time.sleep(0.02)
        self.fail("timed out; status: %s" % self.controller.status_line())

    def sign_in(self):
        self.win.fire("SignIn")
        self.tick_until(lambda: self.controller.connected and self.controller.worker.state == "connected"
                        and self.item("Account").Text == "Signed in as test@nolgia.ai")

    def command(self, kind, args=None):
        status, data = support.call(self.server, "POST", "/v1/bridge/commands",
                                    {"app": "resolve", "kind": kind, "args": args or {}, "timeout_seconds": 60})
        self.assertEqual(status, 201, data)
        return data["id"]

    def status_of(self, command_id):
        return support.call(self.server, "GET", "/v1/bridge/commands/" + command_id)[1]

    def test_layout_and_signed_out_state(self):
        self.assertEqual(self.win.props["WindowTitle"], "NOLGIA")
        for element_id in ("Status", "SignIn", "SignOut", "Connected", "AllowAgent", "AskBeforeRun", "Activity",
                           "Pause", "Footer", "Review"):
            self.assertIsNotNone(self.item(element_id), element_id)
        self.assertTrue(self.fusion.UIManager.timers[0].started)
        self.assertEqual(self.item("Status").Text, "Not signed in.")
        self.assertTrue(self.visible("SignIn"))
        for element_id in ("SignOut", "Connected", "AllowAgent", "AskBeforeRun", "Review", "Pause"):
            self.assertFalse(self.visible(element_id), element_id)
        self.assertEqual(self.item("Activity").header, ["Time", "Command", "Status", "Detail"])
        self.assertEqual(self.item("Activity").rows[0].Text[1], "Nothing yet.")
        self.assertIn("this window is open", self.item("Footer").Text)
        # The window cannot be squeezed below the width its labels need, and the
        # two wrapping labels have two lines of height (a long retry message
        # once spilled over the title bar in a 230 px wide window).
        self.assertGreaterEqual(self.win.props["MinimumSize"][0], 400)
        for element_id in ("Status", "Footer"):
            self.assertTrue(self.item(element_id).props["WordWrap"], element_id)
            self.assertGreaterEqual(self.item(element_id).props["MinimumSize"][1], 32, element_id)
            self.assertTrue(self.item(element_id).props["Alignment"]["AlignTop"], element_id)

    def test_sign_in_connect_pause_and_sign_out(self):
        self.win.fire("SignIn")
        self.tick_until(lambda: self.item("Code").Text != "")
        self.assertIn("Enter code", self.item("Status").Text)
        self.assertTrue(self.visible("OpenPage") and self.visible("CancelSignIn"))
        self.assertEqual(len(self.opened), 1)
        self.tick_until(lambda: self.controller.connected and self.item("Account").Text.startswith("Signed in as"))
        self.tick_until(lambda: self.item("Status").Text == "Connected. NOLGIA can work in this DaVinci Resolve.")
        self.assertTrue(self.item("Connected").Checked)
        self.assertTrue(self.item("AllowAgent").Checked)
        self.assertFalse(self.item("AskBeforeRun").Checked)
        self.assertEqual(self.item("Pause").Text, "Pause")
        self.assertFalse(self.visible("Code"))
        self.win.fire("Pause")
        self.tick_until(lambda: self.controller.worker is None)
        self.assertEqual(self.item("Pause").Text, "Resume")
        self.assertFalse(self.item("Connected").Checked)
        self.assertFalse(Settings(self.tmp)["connected"])
        self.item("Connected").Checked = True
        self.win.fire("Connected")
        self.tick_until(lambda: self.controller.connected)
        self.assertTrue(Settings(self.tmp)["connected"])
        self.win.fire("SignOut")
        self.tick_until(lambda: self.controller.worker is None)
        self.assertEqual(self.item("Status").Text, "Signed out.")
        self.assertTrue(self.visible("SignIn"))
        self.assertEqual(Settings(self.tmp)["token"], "")

    def test_ask_before_running_code(self):
        self.sign_in()
        self.item("AskBeforeRun").Checked = True
        self.win.fire("AskBeforeRun")
        self.assertTrue(Settings(self.tmp)["ask_before_run"])
        first = self.command("run", {"code": "result = 6 * 7"})
        self.tick_until(lambda: self.panel.request_win is not None)
        request = self.panel.request_win
        self.assertEqual(request.props["WindowTitle"], "NOLGIA request")
        self.assertEqual(request.Find("RequestTitle").Text, "Your agent wants to run Python in DaVinci Resolve")
        self.assertEqual(request.Find("RequestCode").PlainText, "result = 6 * 7")
        self.assertEqual(request.Find("Approve").Text, "Run code")
        self.assertTrue(self.visible("Review"))
        self.assertEqual(self.item("Waiting").Text, "1 request waiting for you.")
        self.assertEqual(self.item("Status").Text, "Waiting for you to approve a request.")
        self.assertTrue(request.shown)
        request.fire("Approve")
        self.tick_until(lambda: self.status_of(first)["status"] == "succeeded")
        self.assertEqual(self.status_of(first)["result"]["value"], 42)
        self.assertFalse(request.shown)

        second = self.command("run", {"code": "result = 1"})
        self.tick_until(lambda: request.shown)
        self.assertEqual(request.Find("RequestCode").PlainText, "result = 1")
        request.fire(panel_module.REQUEST_ID, "Close")  # closing leaves it waiting
        self.assertFalse(request.shown)
        self.disp.tick(panel_module.TIMER_ID)
        self.assertFalse(request.shown, "a closed request must not pop up again by itself")
        self.assertTrue(self.visible("Review"))
        self.win.fire("Review")
        self.assertTrue(request.shown)
        request.fire("Deny")
        self.tick_until(lambda: self.status_of(second)["status"] == "failed")
        self.assertEqual(self.status_of(second)["error"], "The person at DaVinci Resolve clicked Deny, so this did not run.")
        self.tick_until(lambda: [r.Text[2] for r in self.item("Activity").rows][:2] == ["Failed", "Done"])
        self.assertEqual([r.Text[1] for r in self.item("Activity").rows][:2], ["run", "run"])

    def test_allow_agent_switch(self):
        self.sign_in()
        self.item("AllowAgent").Checked = False
        self.win.fire("AllowAgent")
        self.assertFalse(Settings(self.tmp)["allow_agent"])
        self.assertFalse(self.controller.snapshot["allow_agent"])

    def test_close_switches_off(self):
        self.sign_in()
        sid = support.call(self.server, "GET", "/v1/bridge/sessions")[1]["sessions"][0]["id"]
        self.win.fire(panel_module.WINDOW_ID, "Close")
        self.assertTrue(self.disp.exited)
        self.assertIsNone(self.controller.worker)
        deleted = support.call(self.server, "GET", "/mock/state", token=None)[1]["deleted_sessions"]
        self.assertIn(sid, [d["session_id"] for d in deleted])
        self.assertTrue(Settings(self.tmp)["connected"], "closing the window keeps Connected for next time")

    def test_reconnects_when_left_on(self):
        Settings(self.tmp).update(token="unit-token", token_api_url=self.server.base_url, connected=True)
        self.server.state.add_token("unit-token")
        ctl = Controller(fake.sample(), has_window=True, env=self.env, log=self.lines.append)
        fusion, bmd = fake_ui.Fusion(), fake_ui.Bmd()
        panel = panel_module.Panel(None, fusion, bmd, controller=ctl)
        bmd_disp_run = []

        def run_loop():
            bmd_disp_run.append(True)
            win = fusion.UIManager.windows[panel_module.WINDOW_ID]
            self.assertTrue(win.shown)
            self.assertTrue(ctl.connected)
            win.fire(panel_module.WINDOW_ID, "Close")

        panel.build = (lambda build: (lambda: (build(), setattr(bmd.dispatchers[0], "RunLoop", run_loop))[0]))(panel.build)
        self.assertTrue(panel.run())
        self.assertEqual(bmd_disp_run, [True])
        self.assertIsNone(ctl.worker)

    def test_open_window_brings_an_open_one_forward(self):
        import nolgia_resolve

        self.win.Show()
        self.assertFalse(nolgia_resolve.open_window(None, self.fusion, self.bmd))
        self.assertEqual(len(self.bmd.dispatchers), 1, "a second window was made")

    def test_close_window_from_run_code(self):
        self.sign_in()
        cid = self.command("run", {"code": "import nolgia_resolve\nresult = nolgia_resolve.close_window()"})
        self.tick_until(lambda: self.disp.exited)
        self.assertEqual(self.status_of(cid)["status"], "succeeded")
        self.assertIs(self.status_of(cid)["result"]["value"], True)
        self.assertIsNone(self.controller.worker)


if __name__ == "__main__":
    unittest.main()
