# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""The network side of the plugin: two background threads.

- heartbeat: POST /bridge/sessions every 20 s (and at once when asked, e.g.
  after a file is opened or saved), which registers the session the first
  time and keeps it live after that.
- poll: GET /bridge/sessions/{id}/next long poll; each command goes to the
  main thread through the executor, and its result goes back with
  POST /bridge/commands/{id}/result.

Neither thread touches bpy. What they need to know about Blender (open file,
Allow NOLGIA Agent) comes from `snapshot()`, a plain dict the main thread
keeps up to date.
"""

import platform
import threading
import time
import traceback

from . import APP, PLUGIN_VERSION
from .api import ApiError, NetworkError, Unauthorized
from .commands import CAPABILITIES, Command, CommandError, result_body, validate
from .util import Backoff


# The API's limits on POST /bridge/sessions fields (characters).
MAX_VERSION = 64
MAX_MACHINE_NAME = 128
MAX_DOCUMENT_NAME = 512
MAX_DOCUMENT_PATH = 4096


def _one_line(value, limit):
    text = "".join(ch for ch in str(value or "") if ch.isprintable())
    return text.strip()[:limit]


def _session_gone(err):
    """404 (unknown session) or 409 session_disconnected: register again."""
    return err.status == 404 or (err.status == 409 and err.code in ("session_disconnected", ""))


class Status:
    OFF = "off"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RETRYING = "retrying"
    SIGNED_OUT = "signed_out"


class BridgeWorker:
    def __init__(self, api, executor, activity, instance_id, snapshot,
                 prepare=None, finish=None, on_status=None, on_auth_failed=None,
                 log=None, heartbeat_interval=20.0, machine_name=None):
        self.api = api
        self.executor = executor
        self.activity = activity
        self.instance_id = instance_id
        self.snapshot = snapshot
        self.prepare = prepare
        self.finish = finish
        self.on_status = on_status or (lambda state, text: None)
        self.on_auth_failed = on_auth_failed or (lambda message: None)
        self.log = log or (lambda message: None)
        self.heartbeat_interval = heartbeat_interval
        self.machine_name = machine_name if machine_name is not None else platform.node()

        self.session_id = None
        self.poll_wait = 25
        self.state = Status.OFF
        self.status_text = "Switched off."

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._kill = threading.Event()
        self._wake = threading.Event()
        self._session_ready = threading.Event()
        self.finished = threading.Event()
        self._in_flight = None
        self._deleted = False
        self._auth_dead = False
        self._threads = []
        self._heartbeat_done = threading.Event()

    # ------------------------------------------------------------ control

    def start(self):
        self._set_status(Status.CONNECTING, "Connecting to NOLGIA...")
        for name, target in (("heartbeat", self._heartbeat_loop), ("poll", self._poll_loop)):
            thread = threading.Thread(target=target, name="nolgia-" + name, daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self):
        """Stop taking commands. A command already running still reports its
        result; then the session is closed (DELETE) and `finished` is set."""
        self._stop.set()
        self._wake.set()
        self._session_ready.set()

    def kill(self):
        """Stop at once, e.g. when Blender quits. No more retries."""
        self.stop()
        self._kill.set()

    def request_heartbeat(self):
        self._wake.set()

    @property
    def stopping(self):
        return self._stop.is_set()

    @property
    def busy_with(self):
        return self._in_flight

    def is_alive(self):
        return any(t.is_alive() for t in self._threads)

    def join(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            left = None if end is None else max(0.0, end - time.monotonic())
            thread.join(left)

    # ---------------------------------------------------------- internals

    def _set_status(self, state, text):
        self.state = state
        self.status_text = text
        try:
            self.on_status(state, text)
        except Exception:
            pass

    def _wait(self, seconds):
        """Sleep that ends early on stop. Returns True when stopping."""
        return self._stop.wait(seconds)

    def payload(self):
        """The full state, every time: the API resets fields left out."""
        snap = self.snapshot() or {}
        doc = snap.get("document") or {}
        document = {"name": str(doc.get("name") or "")[:MAX_DOCUMENT_NAME]}
        path = doc.get("path")
        if path and len(path) <= MAX_DOCUMENT_PATH:
            document["path"] = path
        return {
            "instance_id": self.instance_id,
            "app": APP,
            "app_version": _one_line(snap.get("app_version"), MAX_VERSION),
            "plugin_version": PLUGIN_VERSION,
            "machine_name": _one_line(self.machine_name, MAX_MACHINE_NAME),
            "document": document,
            "capabilities": list(CAPABILITIES),
            "allow_agent": bool(snap.get("allow_agent", True)),
        }

    def _auth_failed(self, err):
        with self._lock:
            self._auth_dead = True
        text = "NOLGIA did not accept your sign in. Sign in again."
        self._set_status(Status.SIGNED_OUT, text)
        self.log("%s (%s)" % (text, err))
        try:
            self.on_auth_failed(text)
        except Exception:
            pass
        self.stop()

    def _retry_text(self, err, delay):
        if isinstance(err, NetworkError):
            why = "Cannot reach NOLGIA (%s)." % err
        elif err.status == 429:
            why = "NOLGIA asked us to slow down."
        elif err.status >= 500:
            why = "NOLGIA is having trouble (%d)." % err.status
        else:
            why = "NOLGIA refused the connection: %s" % (err.detail or err.title or err.status)
        return "%s Trying again in %d s." % (why, max(1, round(delay)))

    def _heartbeat_loop(self):
        backoff = Backoff(1.0, 60.0)
        try:
            while not self._stop.is_set():
                try:
                    data = self.api.register_session(self.payload())
                except Unauthorized as err:
                    self._auth_failed(err)
                    break
                except (ApiError, NetworkError) as err:
                    delay = getattr(err, "retry_after", None) or backoff.next()
                    self._set_status(Status.RETRYING, self._retry_text(err, delay))
                    self.log(self.status_text)
                    self._wait(delay)
                    continue
                # The API answers the session itself; accept {"session": {...}} too.
                session = data if data.get("id") else data.get("session")
                session_id = session.get("id") if isinstance(session, dict) else None
                if not session_id:
                    delay = backoff.next()
                    self._set_status(Status.RETRYING, "NOLGIA answered without a session. Trying again in %d s." % round(delay))
                    self._wait(delay)
                    continue
                backoff.reset()
                wait = data.get("poll_wait_seconds") or session.get("poll_wait_seconds") or 25
                try:
                    wait = max(1, min(25, int(wait)))
                except (TypeError, ValueError):
                    wait = 25
                first = self.session_id != session_id
                with self._lock:
                    self.session_id = session_id
                    self.poll_wait = wait
                self._session_ready.set()
                if first or self.state != Status.CONNECTED:
                    self._set_status(Status.CONNECTED, "Connected. NOLGIA can work in this DaVinci Resolve.")
                    self.log("Connected to NOLGIA (session %s)." % session_id)
                self._wake.wait(self.heartbeat_interval)
                self._wake.clear()
        finally:
            self._heartbeat_done.set()
            self._close_if_idle()

    def _session_lost(self, session_id):
        with self._lock:
            if self.session_id == session_id:
                self.session_id = None
                self._session_ready.clear()
        self._wake.set()

    def _poll_loop(self):
        backoff = Backoff(1.0, 60.0)
        try:
            while not self._stop.is_set():
                if not self._session_ready.wait(0.5):
                    continue
                with self._lock:
                    session_id, wait = self.session_id, self.poll_wait
                if self._stop.is_set() or not session_id:
                    continue
                try:
                    data = self.api.next_command(session_id, wait)
                except Unauthorized as err:
                    self._auth_failed(err)
                    break
                except ApiError as err:
                    if _session_gone(err):
                        self.log("NOLGIA closed this session; registering again.")
                        self._session_lost(session_id)
                        continue
                    delay = err.retry_after or backoff.next()
                    self._set_status(Status.RETRYING, self._retry_text(err, delay))
                    self._wait(delay)
                    continue
                except NetworkError as err:
                    delay = backoff.next()
                    self._set_status(Status.RETRYING, self._retry_text(err, delay))
                    self._wait(delay)
                    self._wake.set()  # re-check the session as soon as we are back
                    continue
                backoff.reset()
                if self.state == Status.RETRYING:
                    self._set_status(Status.CONNECTED, "Connected. NOLGIA can work in this DaVinci Resolve.")
                if data:
                    self._handle(Command(data))
        finally:
            self._close_if_idle()

    def _handle(self, command):
        with self._lock:
            self._in_flight = command.id
        self.activity.add(command.id, command.kind, command.caller)
        self.log("Command %s (%s) from %s." % (command.kind, command.id, command.caller_label))
        ok, result, error = False, None, None
        try:
            if self._stop.is_set():
                error = "NOLGIA was switched off in DaVinci Resolve before this command ran."
            else:
                ok, result, error = self._execute(command)
            self._post(command, ok, result, error)
        finally:
            with self._lock:
                self._in_flight = None
            self._close_if_idle()

    def _execute(self, command):
        try:
            command.args = validate(command.kind, command.args)
            prepared = self.prepare(command) if self.prepare else None
            ticket = self.executor.submit(command, prepared)
            while not ticket.wait(0.5):
                if self._kill.is_set():
                    return False, None, "DaVinci Resolve closed before this command finished."
            if ticket.ok and self.finish:
                return True, self.finish(command, ticket.result), None
            return bool(ticket.ok), ticket.result, ticket.error
        except CommandError as err:
            return False, err.result, str(err)
        except Unauthorized:
            return False, None, "NOLGIA did not accept the sign in while moving files."
        except NetworkError as err:
            return False, None, "Could not reach NOLGIA while moving files (%s)." % err
        except ApiError as err:
            return False, None, err.message()
        except Exception:
            return False, None, "The NOLGIA plugin hit a bug:\n" + traceback.format_exc()

    def _post(self, command, ok, result, error):
        body = result_body(ok, result, error)
        backoff = Backoff(1.0, 30.0)
        # The API refuses a result at or after expires_at, so retrying past
        # that is pointless (a few seconds of slack for the clocks).
        give_up_at = command.expires + 5.0
        while True:
            try:
                self.api.post_result(command.id, body)
                self.activity.update(command.id, body["status"], body.get("error", ""))
                self.log("Command %s %s." % (command.kind, body["status"]))
                return
            except Unauthorized as err:
                self.activity.update(command.id, "failed", "Sign in no longer accepted")
                self._auth_failed(err)
                return
            except ApiError as err:
                if err.status == 409:  # command_not_running: drop the outcome
                    status = "expired" if "expired" in (err.detail or "") else "cancelled"
                    self.activity.update(command.id, status, err.detail or "Stopped on NOLGIA")
                    self.log("NOLGIA no longer wanted the result of %s (%s)." % (command.kind, err.detail or err.code))
                    return
                if err.status == 413:  # the API already marked it failed
                    self.activity.update(command.id, "failed", "Result over 1 MB")
                    self.log("The result of %s was over 1 MB; NOLGIA marked it failed." % command.kind)
                    return
                if err.status == 404:
                    self.activity.update(command.id, "expired", "NOLGIA no longer has this command")
                    return
                if err.status != 429 and err.status < 500:
                    self.activity.update(command.id, "failed", err.message())
                    self.log("NOLGIA refused the result of %s: %s" % (command.kind, err.message()))
                    return
                delay = err.retry_after or backoff.next()
            except NetworkError:
                delay = backoff.next()
            if time.monotonic() + delay > give_up_at:
                self.activity.update(command.id, "failed", "Could not send the result to NOLGIA")
                self.log("Gave up sending the result of %s." % command.kind)
                return
            if self._kill.wait(delay):
                return

    def _close_if_idle(self):
        """After stop: close the session once nothing is running and the
        heartbeat thread is done, then set `finished`."""
        if not self._stop.is_set() or not self._heartbeat_done.is_set():
            return
        with self._lock:
            if self._in_flight or self.finished.is_set():
                return
            session_id = None if (self._deleted or self._auth_dead) else self.session_id
            self._deleted = True
        if session_id:
            try:
                self.api.delete_session(session_id, timeout=5.0)
                self.log("Disconnected from NOLGIA.")
            except Exception as err:
                self.log("Could not tell NOLGIA we disconnected (%s)." % err)
        if self.state != Status.SIGNED_OUT:
            self._set_status(Status.OFF, "Switched off.")
        self.finished.set()
