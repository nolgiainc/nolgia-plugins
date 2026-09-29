# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Ties the pieces together inside Blender.

Threads: the worker's two network threads and a sign-in thread never call
bpy. They talk to the main thread through the executor (commands) and
`events` (anything else), both drained by `tick()`, which runs from a
bpy.app.timers callback, or from `serve()` when Blender has no window.
"""

import atexit
import os
import queue
import threading
import time
import traceback
import webbrowser

import bpy
from bpy.app.handlers import persistent

from . import ops
from .core import PLUGIN_VERSION
from .core.api import ApiClient, base_url_from_env
from .core.auth import DeviceLogin, LoginCancelled, LoginError
from .core.commands import ActivityLog, CommandError
from .core.instance import lease_instance_id
from .core.mainthread import MainThreadExecutor
from .core.worker import BridgeWorker, Status

PACKAGE = __package__
_TRUE = ("1", "true", "yes", "on")


def env_flag(name):
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value.strip().lower() in _TRUE


def log(message):
    print("NOLGIA: %s" % message, flush=True)


class LoginState:
    def __init__(self, login):
        self.login = login
        self.prompt = None
        self.error = None


class Controller:
    def __init__(self):
        self.activity = ActivityLog()
        self.executor = MainThreadExecutor(
            run=self._run_ticket,
            needs_approval=self._needs_approval,
            can_ask=lambda: not bpy.app.background,
            on_status=self.activity.update,
            on_change=self._on_approvals_changed,
        )
        self.events = queue.Queue()
        self.worker = None
        self.lease = None
        self.login = None
        self.snapshot = {}
        self.env_token = os.environ.get("NOLGIA_TOKEN") or None
        self.message = ""
        self.version = 0
        self._drawn = (-1, -1)
        self._want_connect = False
        self._syncing = False
        self._last_snapshot = 0.0
        self._popup_shown = set()
        self.clean_exit = True

    # ---------------------------------------------------------- settings

    def prefs(self):
        addon = bpy.context.preferences.addons.get(PACKAGE)
        return addon.preferences if addon is not None else None

    def token(self):
        if self.env_token:
            return self.env_token
        prefs = self.prefs()
        return prefs.token if prefs is not None and prefs.token else None

    @property
    def signed_in(self):
        return bool(self.token())

    @property
    def connected(self):
        return self.worker is not None and not self.worker.stopping

    def set_pref(self, name, value):
        prefs = self.prefs()
        if prefs is None or getattr(prefs, name) == value:
            return
        self._syncing = True
        try:
            setattr(prefs, name, value)
        finally:
            self._syncing = False

    def apply_env(self):
        """Headless knobs: NOLGIA_ASK_BEFORE_RUN and NOLGIA_ALLOW_AGENT."""
        for env, pref in (("NOLGIA_ASK_BEFORE_RUN", "ask_before_run"), ("NOLGIA_ALLOW_AGENT", "allow_agent")):
            flag = env_flag(env)
            if flag is not None:
                self.set_pref(pref, flag)

    def bump(self):
        self.version += 1

    # ---------------------------------------------------------- status

    def status_line(self):
        if self.login is not None:
            if self.login.prompt is not None:
                return "Enter code %s in your browser to sign in." % self.login.prompt.user_code
            return "Starting sign in..."
        if self.executor.approvals:
            return "Waiting for you to approve a request."
        if self.worker is not None:
            busy = self.worker.busy_with
            if busy and self.worker.state == Status.CONNECTED:
                item = next((i for i in self.activity.items() if i["id"] == busy), None)
                if item:
                    return "Working on %s..." % item["kind"]
            if self.worker.stopping:
                return "Switching off..."
            return self.worker.status_text
        if self.message:
            return self.message
        if not self.signed_in:
            return "Not signed in."
        return "Switched off. Turn on Connected to let NOLGIA work in this Blender."

    # ------------------------------------------------------ connect/disconnect

    def connect(self):
        """Start talking to NOLGIA. Returns True when started."""
        self.message = ""
        if self.worker is not None:
            if self.worker.stopping:
                self._want_connect = True  # start again once the old one is done
                return True
            return True
        token = self.token()
        if not token:
            self.message = "Sign in to NOLGIA first."
            self.set_pref("connected", False)
            self.bump()
            return False
        prefs = self.prefs()
        if not self.env_token and prefs is not None and 0 < prefs.token_expires_at < time.time():
            self.set_pref("token", "")
            self.set_pref("connected", False)
            _mark_prefs_dirty()
            self.message = "Your NOLGIA sign in expired. Sign in again."
            self.bump()
            return False
        if bpy.app.background and prefs is not None and prefs.ask_before_run:
            self.message = (
                "Ask before running code is on, but Blender is running without a window, so nobody "
                "could approve code. Turn that setting off (NOLGIA_ASK_BEFORE_RUN=0) to connect."
            )
            log(self.message)
            self.set_pref("connected", False)
            self.clean_exit = False
            self.bump()
            return False
        user_dir = bpy.utils.extension_path_user(PACKAGE, create=True)
        self.lease = lease_instance_id(user_dir, os.environ.get("NOLGIA_INSTANCE_ID"))
        self.fallback_dir = os.path.join(user_dir, "downloads")
        api = ApiClient(
            base_url_from_env(),
            token,
            user_agent="nolgia-blender/%s (Blender %s; %s)"
            % (PLUGIN_VERSION, ops.app_version(), bpy.app.build_platform.decode("utf-8", "replace")),
        )
        self.update_snapshot()
        self.executor.reopen()
        self.worker = BridgeWorker(
            api,
            self.executor,
            self.activity,
            self.lease.instance_id,
            lambda: self.snapshot,
            prepare=lambda command: self._prepare(command, api),
            finish=lambda command, result: ops.finish_upload(command, result, api),
            on_status=lambda state, text: self.bump(),
            on_auth_failed=lambda text: self.events.put(lambda: self._auth_failed(text)),
            log=log,
        )
        self.worker.start()
        self.set_pref("connected", True)
        self.clean_exit = True
        log("Connecting to %s as instance %s." % (api.base_url, self.lease.instance_id))
        self.bump()
        return True

    def disconnect(self, reason="NOLGIA was switched off in Blender before this ran."):
        self._want_connect = False
        self.set_pref("connected", False)
        if self.worker is not None and not self.worker.stopping:
            self.executor.close(reason)
            self.worker.stop()
            log("Switching off.")
        self.bump()

    def _worker_done(self):
        if self.lease is not None:
            self.lease.release()
            self.lease = None
        self.worker = None
        self.bump()
        if self._want_connect:
            self._want_connect = False
            self.connect()

    def _auth_failed(self, text):
        self.message = text
        self.clean_exit = False
        if self.env_token:
            self.env_token = None
        else:
            self.set_pref("token", "")
            self.set_pref("account_email", "")
            _mark_prefs_dirty()
        self.disconnect()
        self.message = text

    # ------------------------------------------------------------ sign in

    def sign_in(self):
        if self.login is not None:
            return
        self.message = ""
        login = DeviceLogin(ApiClient(base_url_from_env()))
        state = LoginState(login)
        self.login = state

        def flow():
            try:
                prompt = login.start()
                state.prompt = prompt
                self.events.put(self.bump)
                try:
                    webbrowser.open(prompt.open_url)
                except Exception:
                    pass
                token = login.wait_for_token(prompt)
                self.events.put(lambda: self._signed_in(state, token))
            except LoginCancelled:
                self.events.put(lambda: self._login_over(state, ""))
            except LoginError as err:
                message = str(err)
                self.events.put(lambda: self._login_over(state, message))
            except Exception as err:
                message = "Sign in failed: %s" % err
                self.events.put(lambda: self._login_over(state, message))

        threading.Thread(target=flow, name="nolgia-sign-in", daemon=True).start()
        self.bump()

    def cancel_sign_in(self):
        if self.login is not None:
            self.login.login.cancel()
            self.login = None
            self.bump()

    def _login_over(self, state, message):
        if self.login is state:
            self.login = None
        self.message = message
        self.bump()

    def _signed_in(self, state, token):
        if self.login is not state:
            return  # cancelled meanwhile
        self.login = None
        self.env_token = None
        self.set_pref("token", token.access_token)
        self.set_pref("account_email", token.email or "")
        self.set_pref("token_expires_at", float(token.expires_at or 0))
        _mark_prefs_dirty()
        log("Signed in%s." % (" as %s" % token.email if token.email else ""))
        self.connect()

    def sign_out(self):
        self.cancel_sign_in()
        self.disconnect("You signed out of NOLGIA in Blender before this ran.")
        self.env_token = None
        self.set_pref("token", "")
        self.set_pref("account_email", "")
        self.set_pref("token_expires_at", 0.0)
        _mark_prefs_dirty()
        self.message = "Signed out."
        self.bump()

    # ------------------------------------------------------------ commands

    def _needs_approval(self, ticket):
        command = ticket.command
        if command.kind == "run":
            prefs = self.prefs()
            return ops.run_approval(command, bool(prefs and prefs.ask_before_run))
        if command.kind == "open":
            return ops.open_approval(command)
        return None

    def _run_ticket(self, ticket):
        command = ticket.command
        runner = ops.RUNNERS.get(command.kind)
        if runner is None:
            return False, None, "This plugin cannot do %s." % command.kind
        self.bump()
        try:
            return True, runner(command.args, ticket.prepared, command), None
        except CommandError as err:
            return False, err.result, str(err)
        except Exception:
            return False, None, "Blender raised an error:\n" + traceback.format_exc()
        finally:
            self.update_snapshot()

    def _prepare(self, command, api):
        """Worker thread: network work a command needs before Blender runs it."""
        if command.kind == "import_asset":
            return ops.prepare_import(command, api, self.fallback_dir, self.snapshot)
        return None

    def _on_approvals_changed(self):
        self.bump()
        if bpy.app.background:
            return
        for pending in self.executor.approvals:
            if pending.id in self._popup_shown:
                continue
            self._popup_shown.add(pending.id)
            win = ops.active_window()
            if win is None:
                continue
            try:
                with bpy.context.temp_override(window=win, screen=win.screen):
                    bpy.ops.nolgia.review_request("INVOKE_DEFAULT", command_id=pending.id)
            except Exception as err:
                log("Could not show the approval window (%s); use the NOLGIA panel." % err)

    # ------------------------------------------------------------ main thread

    def update_snapshot(self):
        prefs = self.prefs()
        snap = {
            "document": ops.document(),
            "allow_agent": bool(prefs.allow_agent) if prefs is not None else True,
            "app_version": ops.app_version(),
            "download_dir": ops.download_dir(getattr(self, "fallback_dir", "")),
        }
        changed = (snap["document"], snap["allow_agent"]) != (
            self.snapshot.get("document"),
            self.snapshot.get("allow_agent"),
        )
        self.snapshot = snap
        self._last_snapshot = time.monotonic()
        if changed and self.worker is not None:
            self.worker.request_heartbeat()

    def tick(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                event()
            except Exception:
                traceback.print_exc()
        if self.executor.pump():
            self.bump()
        if self.worker is not None:
            if time.monotonic() - self._last_snapshot > 1.0:
                self.update_snapshot()
            if self.worker.finished.is_set():
                self._worker_done()
        state = (self.version, self.activity.version)
        if state != self._drawn:
            self._drawn = state
            _redraw()

    def serve(self):
        """Block and run commands until disconnected. For Blender without a
        window. Returns True after a normal switch off, False when it could
        not connect or NOLGIA stopped accepting the sign in."""
        self.apply_env()
        if self.worker is None and not self.connect():
            return False
        log("Serving NOLGIA commands. Press Ctrl+C to stop.")
        try:
            while self.worker is not None:
                self.tick()
                time.sleep(0.02)
        except KeyboardInterrupt:
            self.disconnect()
            end = time.monotonic() + 10
            while self.worker is not None and time.monotonic() < end:
                self.tick()
                time.sleep(0.02)
        return self.clean_exit

    def shutdown(self, wait=0.0):
        self.cancel_sign_in()
        worker = self.worker
        if worker is not None:
            self.executor.close("Blender is closing.")
            worker.stop()
            if wait:
                worker.finished.wait(wait)
            if not worker.finished.is_set():
                worker.kill()
                worker.finished.wait(min(wait, 2.0) if wait else 0)
        if self.lease is not None:
            self.lease.release()
            self.lease = None
        self.worker = None


def _mark_prefs_dirty():
    try:
        bpy.context.preferences.is_dirty = True
    except Exception:
        pass


def _redraw():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in ("VIEW_3D", "PREFERENCES"):
                area.tag_redraw()


controller = None


def _timer():
    if controller is None:
        return None
    try:
        controller.tick()
    except Exception:
        traceback.print_exc()
    busy = controller.worker is not None or controller.login is not None or controller.executor.approvals
    return 0.1 if busy else 0.5


def _startup():
    """First tick after Blender starts: reconnect when the person left it on."""
    if controller is None or bpy.app.background:
        return None
    controller.apply_env()
    prefs = controller.prefs()
    wanted = env_flag("NOLGIA_BRIDGE_AUTOCONNECT") or (prefs is not None and prefs.connected)
    if wanted and controller.signed_in:
        controller.connect()
    elif prefs is not None and prefs.connected:
        controller.set_pref("connected", False)
    return None


@persistent
def _on_file_event(*_args):
    ops.mark_clean()
    if controller is not None:
        controller.update_snapshot()
        if controller.worker is not None:
            controller.worker.request_heartbeat()


def _on_exit():
    if controller is not None:
        controller.shutdown(wait=3.0)


def register():
    global controller
    controller = Controller()
    bpy.app.timers.register(_timer, first_interval=0.2, persistent=True)
    bpy.app.timers.register(_startup, first_interval=0.1, persistent=False)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.save_post):
        if _on_file_event not in handlers:
            handlers.append(_on_file_event)
    atexit.register(_on_exit)


def unregister():
    global controller
    atexit.unregister(_on_exit)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.save_post):
        if _on_file_event in handlers:
            handlers.remove(_on_file_event)
    for fn in (_timer, _startup):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    if controller is not None:
        controller.shutdown(wait=1.0)
    controller = None
