# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Ties the pieces together for DaVinci Resolve.

Threads: the worker's two network threads and a sign in thread never call
Resolve. They talk to the main thread (the script's own thread, the one that
loaded Resolve's scripting module) through the executor (commands) and
`events` (anything else), both drained by `tick()`. In the NOLGIA window a
UIManager timer calls `tick()`; without a window `serve()` does.

A command that waits for Resolve (a render) waits on the worker thread and
asks the main thread for each status check with `on_main()`, so the window
keeps answering clicks while Resolve renders.
"""

import os
import platform
import queue
import sys
import threading
import time
import traceback
import webbrowser

from . import ops as ops_module
from . import paths
from .core import DEFAULT_API_URL, PLUGIN_VERSION
from .core.api import ApiClient, normalize_base_url
from .core.auth import DeviceLogin, LoginCancelled, LoginError
from .core.commands import ActivityLog, CommandError
from .core.instance import lease_instance_id
from .core.mainthread import MainThreadExecutor
from .core.worker import BridgeWorker, Status
from .settings import Log, Settings

_TRUE = ("1", "true", "yes", "on")


CA_BUNDLES = (
    "/etc/ssl/cert.pem",                      # macOS, Alpine
    "/etc/ssl/certs/ca-certificates.crt",     # Debian, Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",       # Fedora, RHEL, Rocky
    "/etc/ssl/ca-bundle.pem",                 # openSUSE
)


def ensure_ca_certificates(env=None):
    """Resolve's own Python has no certifi, and on macOS and Linux its
    OpenSSL may look for certificates where there are none. Point it at the
    system's bundle then (Windows uses its own certificate store)."""
    env = os.environ if env is None else env
    if sys.platform.startswith("win") or env.get("SSL_CERT_FILE") or env.get("SSL_CERT_DIR"):
        return None
    try:
        import ssl

        paths_ = ssl.get_default_verify_paths()
        if (paths_.cafile and os.path.isfile(paths_.cafile)) or (paths_.capath and os.path.isdir(paths_.capath)
                                                                    and os.listdir(paths_.capath)):
            return None
    except Exception:
        pass
    for bundle in CA_BUNDLES:
        if os.path.isfile(bundle):
            env["SSL_CERT_FILE"] = bundle
            return bundle
    return None


def env_flag(name, env=None):
    env = os.environ if env is None else env
    value = env.get(name)
    if value is None or value == "":
        return None
    return value.strip().lower() in _TRUE


class LoginState:
    def __init__(self, login):
        self.login = login
        self.prompt = None
        self.error = None


# The controller of this Resolve, for nolgia_resolve.connect()/disconnect().
current = None


class Controller:
    def __init__(self, resolve, settings=None, log=None, has_window=False, env=None, open_url=None):
        self.env = os.environ if env is None else env
        ensure_ca_certificates()
        self.config_dir = paths.config_dir(env=self.env)
        self.settings = settings or Settings(self.config_dir)
        self.log = log or Log(self.config_dir)
        self.has_window = has_window
        self.open_url = open_url or _open_url
        self.ops = ops_module.Ops(resolve, self.log, can_ask=self.can_ask)
        self.activity = ActivityLog()
        self.executor = MainThreadExecutor(
            run=self._run_ticket,
            needs_approval=self._needs_approval,
            can_ask=self.can_ask,
            on_status=self.activity.update,
            on_change=self._on_approvals_changed,
        )
        self.events = queue.Queue()
        self.worker = None
        self.lease = None
        self.login = None
        self.api = None
        self.snapshot = {}
        self.env_token = self.env.get("NOLGIA_TOKEN") or None
        self.env_email = ""
        self.overrides = {}
        self.message = ""
        self.version = 0
        self.main_ident = threading.get_ident()
        self.closing = False
        self.clean_exit = True
        self._want_connect = False
        self._last_snapshot = 0.0
        self._last_alive = time.monotonic()
        self.on_approval = None  # set by the window: called with each new request
        self.resolve_gone = False
        global current
        current = self

    # ---------------------------------------------------------- settings

    def setting(self, key):
        if key in self.overrides:
            return self.overrides[key]
        return self.settings.get(key)

    def set_setting(self, key, value):
        """A change the person made: saved, and it replaces an env override."""
        self.overrides.pop(key, None)
        self.settings.update(**{key: value})
        if key == "allow_agent":
            self.update_snapshot()
        self.bump()

    def apply_env(self):
        """NOLGIA_ASK_BEFORE_RUN and NOLGIA_ALLOW_AGENT, for this run only."""
        for env, key in (("NOLGIA_ASK_BEFORE_RUN", "ask_before_run"), ("NOLGIA_ALLOW_AGENT", "allow_agent")):
            flag = env_flag(env, self.env)
            if flag is not None:
                self.overrides[key] = flag

    def can_ask(self):
        return self.has_window and not self.closing

    def base_url(self):
        return _base_url(self.env)

    def token(self):
        if self.env_token:
            return self.env_token
        token = self.settings.get("token")
        if not token:
            return None
        # A saved sign in is only ever sent to the API it came from.
        saved_for = self.settings.get("token_api_url")
        if saved_for and saved_for != self.base_url():
            return None
        return token

    @property
    def signed_in(self):
        return bool(self.token())

    @property
    def connected(self):
        return self.worker is not None and not self.worker.stopping

    def bump(self):
        self.version += 1

    def account_email(self):
        if self.env_token:
            return self.env_email
        return self.settings.get("account_email")

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
        return "Switched off. Turn on Connected to let NOLGIA work in this DaVinci Resolve."

    # ------------------------------------------------------ connect/disconnect

    def connect(self):
        """Start talking to NOLGIA. Returns True when started."""
        self.message = ""
        if self.worker is not None:
            if self.worker.stopping:
                self._want_connect = True  # start again once the old one is done
            return True
        token = self.token()
        if not token:
            self.message = "Sign in to NOLGIA first."
            self.settings.update(connected=False)
            self.bump()
            return False
        expires = self.settings.get("token_expires_at")
        if not self.env_token and 0 < expires < time.time():
            self.settings.update(token="", connected=False)
            self.message = "Your NOLGIA sign in expired. Sign in again."
            self.bump()
            return False
        if not self.has_window and self.setting("ask_before_run"):
            self.message = (
                "Ask before running code is on, but DaVinci Resolve has no NOLGIA window here, so nobody "
                "could approve code. Turn that setting off (NOLGIA_ASK_BEFORE_RUN=0) to connect."
            )
            self.log(self.message)
            self.clean_exit = False
            self.bump()
            return False
        self.lease = lease_instance_id(self.config_dir, self.env.get("NOLGIA_INSTANCE_ID"))
        version = self.ops.app_version() or "unknown"
        api = ApiClient(
            self.base_url(),
            token,
            user_agent="nolgia-resolve/%s (DaVinci Resolve %s; %s)" % (PLUGIN_VERSION, version, platform.system()),
        )
        self.api = api
        self.update_snapshot()
        self.executor.reopen()
        finisher = ops_module.Finisher(self.ops, api, self.on_main, log=self.log)
        self.worker = BridgeWorker(
            api,
            self.executor,
            self.activity,
            self.lease.instance_id,
            lambda: self.snapshot,
            prepare=lambda command: self._prepare(command, api),
            finish=finisher,
            on_status=lambda state, text: self.bump(),
            on_auth_failed=lambda text: self.events.put(lambda: self._auth_failed(text)),
            log=self.log,
        )
        self.worker.start()
        if self.has_window:
            self.settings.update(connected=True)
        self.clean_exit = True
        if not self.account_email():
            self._fetch_email(api)
        self.log("Connecting to %s as instance %s." % (api.base_url, self.lease.instance_id))
        self.bump()
        return True

    def _fetch_email(self, api):
        token = api.token

        def fetch():
            try:
                email = (api.get_me() or {}).get("email") or ""
            except Exception:
                return
            if email:
                self.events.put(lambda: self._set_email(token, email))

        threading.Thread(target=fetch, name="nolgia-me", daemon=True).start()

    def _set_email(self, token, email):
        if token != self.token():
            return  # signed out or in again meanwhile
        if self.env_token:
            self.env_email = email
        else:
            self.settings.update(account_email=email)
        self.log("Signed in as %s." % email)
        self.bump()

    def disconnect(self, reason="NOLGIA was switched off in DaVinci Resolve before this ran.", remember=True):
        self._want_connect = False
        if remember and self.has_window:
            self.settings.update(connected=False)
        if self.worker is not None and not self.worker.stopping:
            self.executor.close(reason)
            self.worker.stop()
            self.log("Switching off.")
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
        self.clean_exit = False
        if self.env_token:
            self.env_token = None
        else:
            self.settings.update(token="", account_email="", token_expires_at=0.0)
        self.disconnect()
        self.message = text

    # ------------------------------------------------------------ sign in

    def sign_in(self):
        if self.login is not None:
            return
        self.message = ""
        login = DeviceLogin(ApiClient(self.base_url(), user_agent="nolgia-resolve/%s" % PLUGIN_VERSION))
        state = LoginState(login)
        self.login = state
        base_url = self.base_url()

        def flow():
            try:
                prompt = login.start()
                state.prompt = prompt
                self.events.put(self.bump)
                try:
                    self.open_url(prompt.open_url)
                except Exception:
                    pass
                token = login.wait_for_token(prompt)
                if not token.email:  # the token response has no email: ask GET /me
                    try:
                        me = ApiClient(base_url, token.access_token).get_me()
                        token.email = me.get("email") or None
                    except Exception:
                        pass
                self.events.put(lambda: self._signed_in(state, token, base_url))
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

    def open_sign_in_page(self):
        if self.login is not None and self.login.prompt is not None:
            self.open_url(self.login.prompt.open_url)

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

    def _signed_in(self, state, token, base_url):
        if self.login is not state:
            return  # cancelled meanwhile
        self.login = None
        self.env_token = None
        self.env_email = ""
        self.settings.update(token=token.access_token, token_api_url=base_url, account_email=token.email or "",
                             token_expires_at=float(token.expires_at or 0))
        self.log("Signed in%s." % (" as %s" % token.email if token.email else ""))
        self.connect()

    def sign_out(self):
        self.cancel_sign_in()
        self.disconnect("You signed out of NOLGIA in DaVinci Resolve before this ran.")
        self.env_token = None
        self.settings.update(token="", token_api_url="", account_email="", token_expires_at=0.0, connected=False)
        self.message = "Signed out."
        self.bump()

    # ------------------------------------------------------------ commands

    def _needs_approval(self, ticket):
        command = ticket.command
        if command.kind == "run":
            return self.ops.run_approval(command, bool(self.setting("ask_before_run")))
        if command.kind == "open":
            return self.ops.open_approval(command)
        return None

    def _run_ticket(self, ticket):
        command = ticket.command
        runner = getattr(self.ops, "do_" + command.kind, None)
        if runner is None:
            return False, None, "This plugin cannot do %s." % command.kind
        self.bump()
        try:
            return True, runner(command.args, ticket.prepared, command), None
        except CommandError as err:
            return False, err.result, str(err)
        except Exception:
            return False, None, "DaVinci Resolve raised an error:\n" + traceback.format_exc()
        finally:
            self.update_snapshot()

    def _prepare(self, command, api):
        """Worker thread: network work a command needs before Resolve runs it."""
        if command.kind == "import_asset":
            return ops_module.prepare_import(command, api, self.snapshot, self.log)
        return None

    def _on_approvals_changed(self):
        self.bump()
        if self.on_approval is not None:
            try:
                self.on_approval()
            except Exception as err:
                self.log("Could not show the request window (%s); use the NOLGIA window." % err)

    def on_main(self, fn, timeout=120.0):
        """Run fn on the main thread and return its value. From a worker
        thread; on the main thread itself it just calls fn."""
        if threading.get_ident() == self.main_ident:
            return fn()
        box = {}
        done = threading.Event()

        def job():
            try:
                box["value"] = fn()
            except BaseException as err:  # handed back to the waiting thread
                box["error"] = err
            finally:
                done.set()

        self.events.put(job)
        end = time.monotonic() + timeout
        while not done.wait(0.25):
            if self.closing or time.monotonic() > end:
                raise CommandError("DaVinci Resolve did not answer in time.")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # ------------------------------------------------------------ main thread

    def update_snapshot(self):
        try:
            document = self.ops.document()
            version = self.ops.app_version()
        except Exception:
            document, version = {"name": ""}, ""
        snap = {
            "document": document,
            "allow_agent": bool(self.setting("allow_agent")),
            "app_version": version or self.snapshot.get("app_version", ""),
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
                self.log("A NOLGIA task failed:\n" + traceback.format_exc())
        if self.executor.pump():
            self.bump()
        if self.worker is not None:
            if time.monotonic() - self._last_snapshot > 1.0:
                self.update_snapshot()
            if self.worker.finished.is_set():
                self._worker_done()

    def check_resolve(self, every=5.0):
        """Headless: notice when Resolve went away. Returns False then."""
        now = time.monotonic()
        if now - self._last_alive < every:
            return True
        self._last_alive = now
        if self.ops.alive():
            return True
        self.resolve_gone = True
        return False

    def serve(self, poll=0.02):
        """Block and run commands until switched off. For DaVinci Resolve
        without the NOLGIA window. Returns True after a normal stop (switch
        off, Ctrl+C or Resolve closing), False when it could not connect or
        NOLGIA stopped accepting the sign in."""
        self.apply_env()
        if self.worker is None and not self.connect():
            return False
        self.log("Serving NOLGIA commands. Press Ctrl+C to stop.")
        try:
            while self.worker is not None:
                self.tick()
                if not self.check_resolve():
                    self.log("DaVinci Resolve closed; switching off.")
                    self.disconnect("DaVinci Resolve closed before this ran.")
                    self._drain(10)
                    break
                time.sleep(poll)
        except KeyboardInterrupt:
            self.disconnect()
            self._drain(10)
        return self.clean_exit

    def _drain(self, seconds):
        end = time.monotonic() + seconds
        while self.worker is not None and time.monotonic() < end:
            self.tick()
            time.sleep(0.02)

    def shutdown(self, wait=0.0):
        self.closing = True
        self.cancel_sign_in()
        worker = self.worker
        if worker is not None:
            self.executor.close("DaVinci Resolve's NOLGIA window closed.")
            worker.stop()
            end = time.monotonic() + wait
            while not worker.finished.is_set() and time.monotonic() < end:
                self.tick()  # let a finishing command reach the main thread
                time.sleep(0.02)
            if not worker.finished.is_set():
                worker.kill()
                worker.finished.wait(min(wait, 2.0) if wait else 0)
        try:
            self.ops.abandon_render()
        except Exception as err:
            self.log("Could not stop NOLGIA's render (%s)." % err)
        if self.lease is not None:
            self.lease.release()
            self.lease = None
        self.worker = None


def _base_url(env):
    return normalize_base_url(env.get("NOLGIA_API_URL") or DEFAULT_API_URL)


def _open_url(url):
    """The sign in page, in the default browser (Python's webbrowser module,
    which works the same on Windows, macOS and Linux)."""
    webbrowser.open(url)


def connect_resolve():
    """The Resolve object for a script running outside Resolve (External
    scripting set to Local), or None."""
    try:
        import DaVinciResolveScript as dvr  # noqa: N813 - Resolve's own module name
    except ImportError:
        return None
    try:
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def serve(resolve=None, env=None):
    """Connect and run NOLGIA commands until switched off (blocks)."""
    resolve = resolve or connect_resolve()
    env = os.environ if env is None else env
    log = Log(paths.config_dir(env=env))
    if resolve is None or not ops_module.call(resolve, "GetVersionString"):
        log("Could not reach DaVinci Resolve. Start DaVinci Resolve Studio and set Preferences > System > "
            "General > External scripting using to Local, then try again.")
        return False
    controller = Controller(resolve, log=log, has_window=False, env=env)
    try:
        return controller.serve()
    finally:
        controller.shutdown(wait=5.0)
