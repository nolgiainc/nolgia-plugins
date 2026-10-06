# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""The NOLGIA window inside DaVinci Resolve (Workspace > Scripts > NOLGIA),
built with Resolve's UIManager.

The window lives as long as the script: closing it switches NOLGIA off for
this Resolve. A UIManager timer calls Controller.tick() every 100 ms on the
script's thread, which runs the commands, and redraws what changed.
"""

import time
import traceback

from .core.commands import ActivityLog
from .core.worker import Status
from .runtime import Controller, env_flag

WINDOW_ID = "com.nolgia.resolve.panel"
REQUEST_ID = "com.nolgia.resolve.request"
TIMER_ID = "NolgiaTick"
TICK_MS = 100
MAX_CODE_CHARS = 200000
FOOTER = "NOLGIA works in this DaVinci Resolve while this window is open and Connected is on."

# The open window, for nolgia_resolve.close_window().
current = None


def find_open_window(fusion):
    """The NOLGIA window when it is already open (the script runs again)."""
    try:
        ui = fusion.UIManager
        win = ui.FindWindow(WINDOW_ID)
    except Exception:
        return None
    return win or None


class Panel:
    def __init__(self, resolve, fusion, bmd, controller=None, log=None):
        self.resolve = resolve
        self.fusion = fusion
        self.ui = fusion.UIManager
        self.disp = bmd.UIDispatcher(self.ui)
        self.controller = controller or Controller(resolve, has_window=True, log=log)
        self.controller.on_approval = self._approvals_changed
        self.log = self.controller.log
        self.win = None
        self.request_win = None
        self.request_shown = None
        self.timer = None
        self._drawn = None
        self._activity_drawn = -1
        self._closed = False
        self._ticks = 0
        global current
        current = self

    # ------------------------------------------------------------- layout

    def build(self):
        ui = self.ui
        big = ui.Font({"PointSize": 18, "Bold": True})
        bold = ui.Font({"Bold": True})
        layout = ui.VGroup({"Spacing": 6}, [
            ui.Label({"ID": "Status", "Text": "", "WordWrap": True, "Weight": 0, "Font": bold}),
            ui.Label({"ID": "Account", "Text": "", "WordWrap": True, "Weight": 0}),
            ui.Label({"ID": "Code", "Text": "", "Weight": 0, "Font": big, "Alignment": {"AlignHCenter": True}}),
            ui.Label({"ID": "CodeHint", "Text": "", "WordWrap": True, "Weight": 0}),
            ui.HGroup({"Weight": 0}, [
                ui.Button({"ID": "SignIn", "Text": "Sign in"}),
                ui.Button({"ID": "OpenPage", "Text": "Open page again"}),
                ui.Button({"ID": "CancelSignIn", "Text": "Cancel"}),
                ui.Button({"ID": "SignOut", "Text": "Sign out"}),
            ]),
            ui.CheckBox({"ID": "Connected", "Text": "Connected", "Weight": 0,
                         "ToolTip": "Let NOLGIA send commands to this DaVinci Resolve. Turn off to pause."}),
            ui.CheckBox({"ID": "AllowAgent", "Text": "Allow NOLGIA Agent", "Weight": 0,
                         "ToolTip": "Let your NOLGIA Agent in the cloud work here too, not only the agent apps "
                                    "you run yourself (Claude, ChatGPT and others)."}),
            ui.CheckBox({"ID": "AskBeforeRun", "Text": "Ask before running code", "Weight": 0,
                         "ToolTip": "Show each piece of Python NOLGIA wants to run and wait for you to approve it."}),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "Waiting", "Text": "", "WordWrap": True}),
                ui.Button({"ID": "Review", "Text": "Show request", "Weight": 0}),
            ]),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"Text": "Activity", "Font": bold}),
                ui.HGap(0, 1),
                ui.Button({"ID": "Pause", "Text": "Pause", "Weight": 0}),
            ]),
            ui.Tree({"ID": "Activity", "Weight": 1, "RootIsDecorated": False, "AlternatingRowColors": True,
                     "SelectionMode": "NoSelection"}),
            ui.Label({"ID": "Footer", "Text": FOOTER, "WordWrap": True, "Weight": 0}),
        ])
        self.win = self.disp.AddWindow({"ID": WINDOW_ID, "WindowTitle": "NOLGIA", "Geometry": [120, 120, 440, 600]},
                                       layout)
        self.items = self.win.GetItems()
        tree = self.items["Activity"]
        tree.ColumnCount = 4
        tree.SetHeaderLabels(["Time", "Command", "Status", "Detail"])
        for column, width in enumerate((70, 90, 110, 160)):
            tree.ColumnWidth[column] = width
        on = self.win.On
        on[WINDOW_ID].Close = self._on_close
        on.SignIn.Clicked = self._guard(lambda ev: self.controller.sign_in())
        on.OpenPage.Clicked = self._guard(lambda ev: self.controller.open_sign_in_page())
        on.CancelSignIn.Clicked = self._guard(lambda ev: self.controller.cancel_sign_in())
        on.SignOut.Clicked = self._guard(lambda ev: self.controller.sign_out())
        on.Connected.Clicked = self._guard(self._on_connected)
        on.AllowAgent.Clicked = self._guard(
            lambda ev: self.controller.set_setting("allow_agent", bool(self.items["AllowAgent"].Checked)))
        on.AskBeforeRun.Clicked = self._guard(
            lambda ev: self.controller.set_setting("ask_before_run", bool(self.items["AskBeforeRun"].Checked)))
        on.Pause.Clicked = self._guard(self._on_pause)
        on.Review.Clicked = self._guard(lambda ev: self.show_request(force=True))
        self._start_timer()
        self.refresh(force=True)
        return self.win

    def _start_timer(self):
        self.timer = self.ui.Timer({"ID": TIMER_ID, "Interval": TICK_MS, "SingleShot": False})
        handler = self._guard(self._on_tick)
        # UIManager delivers a timer's Timeout to the dispatcher.
        self.disp.On[TIMER_ID].Timeout = handler
        self.timer.Start()

    def _guard(self, fn):
        def handler(ev):
            try:
                fn(ev)
            except Exception:
                self.log("The NOLGIA window hit a bug:\n" + traceback.format_exc())
            self.refresh()
        return handler

    # ------------------------------------------------------------- events

    def _on_tick(self, ev):
        self._ticks += 1
        if self._ticks == 1:
            self.log("The NOLGIA window is running (timer ticks).")
        self.controller.tick()

    def _on_connected(self, ev):
        if self.items["Connected"].Checked:
            self.controller.connect()
        else:
            self.controller.disconnect()

    def _on_pause(self, ev):
        if self.controller.connected:
            self.controller.disconnect()
        else:
            self.controller.connect()

    def close(self):
        """Close the window, as its close button does."""
        self._on_close(None)

    def _on_close(self, ev):
        if self._closed:
            return
        self._closed = True
        self.log("The NOLGIA window closed; switching off.")
        try:
            if self.timer is not None:
                self.timer.Stop()
        except Exception:
            pass
        self.controller.shutdown(wait=3.0)
        self._close_request()
        self.disp.ExitLoop()

    def _approvals_changed(self):
        self.show_request()

    # ------------------------------------------------------------- request

    def show_request(self, force=False):
        pending = list(self.controller.executor.approvals)
        if not pending:
            self._close_request()
            return
        first = pending[0]
        if self.request_shown == first.id and not force:
            return
        win = self.request_win or self._build_request()
        request = first.request
        items = win.GetItems()
        items["RequestTitle"].Text = request.title
        items["RequestCode"].PlainText = "\n".join(request.lines)[:MAX_CODE_CHARS]
        items["Approve"].Text = request.approve_label
        win.Show()
        try:
            win.Raise()
        except Exception:
            pass
        self.request_shown = first.id
        self.log("Asked the person: %s." % request.title)

    def _build_request(self):
        """The "NOLGIA request" window, made once and reused."""
        ui = self.ui
        mono = ui.Font({"Family": "Consolas", "PointSize": 10, "MonoSpaced": True})
        layout = ui.VGroup({"Spacing": 6}, [
            ui.Label({"ID": "RequestTitle", "Text": "", "WordWrap": True, "Weight": 0,
                      "Font": ui.Font({"Bold": True})}),
            ui.TextEdit({"ID": "RequestCode", "PlainText": "", "ReadOnly": True, "Font": mono,
                         "LineWrapMode": "NoWrap", "Lexer": "python", "Weight": 1}),
            ui.Label({"Text": "Code runs on this computer with your permissions.", "Weight": 0}),
            ui.HGroup({"Weight": 0}, [
                ui.HGap(0, 1),
                ui.Button({"ID": "Deny", "Text": "Deny"}),
                ui.Button({"ID": "Approve", "Text": "Approve"}),
            ]),
        ])
        win = self.disp.AddWindow({"ID": REQUEST_ID, "WindowTitle": "NOLGIA request",
                                   "Geometry": [180, 160, 680, 460]}, layout)

        def decide(approve):
            def handler(ev):
                command_id = self.request_shown
                if command_id is not None:
                    if approve:
                        self.controller.executor.approve(command_id)
                    else:
                        self.controller.executor.deny(command_id)
                self.controller.bump()
                self._close_request()
            return self._guard(handler)

        win.On.Approve.Clicked = decide(True)
        win.On.Deny.Clicked = decide(False)
        # Closing the request window leaves the request waiting in the NOLGIA window.
        win.On[REQUEST_ID].Close = self._guard(lambda ev: self._close_request())
        self.request_win = win
        return win

    def _close_request(self):
        win = self.request_win
        if win is not None:
            try:
                win.Hide()
            except Exception:
                pass
        self.request_shown = None

    # ------------------------------------------------------------- drawing

    def refresh(self, force=False):
        if self.win is None or self._closed:
            return
        ctl = self.controller
        state = (ctl.version, ctl.activity.version, len(ctl.executor.approvals))
        if state == self._drawn and not force:
            return
        self._drawn = state
        it = self.items
        it["Status"].Text = ctl.status_line()
        signing_in = ctl.login is not None
        signed_in = ctl.signed_in
        email = ctl.account_email()
        if signed_in and not signing_in:
            it["Account"].Text = ("Signed in as %s" % email) if email else (
                "Signed in with NOLGIA_TOKEN" if ctl.env_token else "Signed in")
        else:
            it["Account"].Text = ""
        prompt = ctl.login.prompt if signing_in else None
        it["Code"].Text = prompt.user_code if prompt else ""
        it["CodeHint"].Text = "Approve this code in the browser page that opened." if prompt else ""
        _show(it["Code"], bool(prompt))
        _show(it["CodeHint"], bool(prompt))
        _show(it["Account"], bool(it["Account"].Text))
        _show(it["SignIn"], not signed_in and not signing_in)
        _show(it["OpenPage"], bool(prompt))
        _show(it["CancelSignIn"], signing_in)
        _show(it["SignOut"], signed_in and not signing_in)
        for key in ("Connected", "AllowAgent", "AskBeforeRun"):
            _show(it[key], signed_in and not signing_in)
        it["Connected"].Checked = bool(ctl.connected)
        it["AllowAgent"].Checked = bool(ctl.setting("allow_agent"))
        it["AskBeforeRun"].Checked = bool(ctl.setting("ask_before_run"))
        waiting = len(ctl.executor.approvals)
        it["Waiting"].Text = ("%d request%s waiting for you." % (waiting, "" if waiting == 1 else "s")) if waiting else ""
        _show(it["Waiting"], bool(waiting))
        _show(it["Review"], bool(waiting))
        it["Pause"].Text = "Pause" if ctl.connected else "Resume"
        _show(it["Pause"], signed_in)
        if ctl.activity.version != self._activity_drawn:
            self._activity_drawn = ctl.activity.version
            self._draw_activity()

    def _draw_activity(self):
        tree = self.items["Activity"]
        tree.Clear()
        items = self.controller.activity.items()
        if not items:
            row = tree.NewItem()
            row.Text[0] = ""
            row.Text[1] = "Nothing yet."
            tree.AddTopLevelItem(row)
            return
        for item in items:
            row = tree.NewItem()
            row.Text[0] = time.strftime("%H:%M:%S", time.localtime(item["time"]))
            row.Text[1] = item["kind"]
            row.Text[2] = ActivityLog.STATUS_LABELS.get(item["status"], item["status"])
            row.Text[3] = item.get("detail") or ""
            tree.AddTopLevelItem(row)

    # ------------------------------------------------------------- run

    def run(self):
        """Show the window and process its events until it closes."""
        self.build()
        ctl = self.controller
        ctl.apply_env()
        wanted = env_flag("NOLGIA_BRIDGE_AUTOCONNECT", ctl.env) or ctl.settings.get("connected")
        if wanted and ctl.signed_in:
            ctl.connect()
        elif ctl.settings.get("connected") and not ctl.signed_in:
            ctl.settings.update(connected=False)
        self.refresh(force=True)
        self.win.Show()
        self.log("NOLGIA window open (plugin %s, DaVinci Resolve %s)." % (_plugin_version(), ctl.ops.app_version()))
        try:
            self.disp.RunLoop()
        finally:
            if not self._closed:
                self._closed = True
                ctl.shutdown(wait=3.0)
            try:
                self.win.Hide()
            except Exception:
                pass
            self._close_request()
        return True


def _show(element, visible):
    try:
        element.Hidden = not visible
    except Exception:
        pass


def _plugin_version():
    from .core import PLUGIN_VERSION

    return PLUGIN_VERSION


def describe(controller):
    """A one-line state for logs and tests."""
    worker = controller.worker
    return "%s; %s" % (controller.status_line(), worker.state if worker is not None else Status.OFF)
