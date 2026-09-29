// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// The network side of the plugin: two loops that run side by side.
//
// - heartbeat: POST /bridge/sessions every 20 s (and at once when asked, for
//   example after a document is opened or saved), which registers the
//   session the first time and keeps it live after that.
// - poll: GET /bridge/sessions/{id}/next long poll; each command goes to the
//   executor (which runs it in Photoshop), and its result goes back with
//   POST /bridge/commands/{id}/result.
//
// What the loops need to know about Photoshop (open document, Allow NOLGIA
// Agent) comes from `snapshot()`, a plain object the plugin keeps up to date.
"use strict";

const { APP, PLUGIN_VERSION } = require("./constants.js");
const { ApiError, NetworkError, Unauthorized } = require("./api.js");
const { CAPABILITIES, Command, CommandError, resultBody, validate } = require("./commands.js");
const { Backoff, Signal, oneLine } = require("./util.js");

// The API's limits on POST /bridge/sessions fields (characters).
const MAX_VERSION = 64;
const MAX_MACHINE_NAME = 128;
const MAX_DOCUMENT_NAME = 512;
const MAX_DOCUMENT_PATH = 4096;

const Status = {
  OFF: "off",
  CONNECTING: "connecting",
  CONNECTED: "connected",
  RETRYING: "retrying",
  SIGNED_OUT: "signed_out",
};

const CONNECTED_TEXT = "Connected. NOLGIA can work in this Photoshop.";

/** 404 (unknown session) or 409 session_disconnected: register again. */
function sessionGone(err) {
  return err.status === 404 || (err.status === 409 && (err.code === "session_disconnected" || err.code === ""));
}

class BridgeWorker {
  constructor({
    api,
    executor,
    activity,
    instanceId,
    snapshot,
    prepare = null,
    finish = null,
    onStatus = null,
    onAuthFailed = null,
    log = null,
    heartbeatInterval = 20,
    machineName = "",
    clock = () => Date.now() / 1000,
    startDelay = 0,
    onPoll = null,
  }) {
    this.api = api;
    this.executor = executor;
    this.activity = activity;
    this.instanceId = instanceId;
    this.snapshot = snapshot;
    this.prepare = prepare;
    this.finish = finish;
    this.onStatus = onStatus || (() => {});
    this.onAuthFailed = onAuthFailed || (() => {});
    this.log = log || (() => {});
    this.heartbeatInterval = heartbeatInterval;
    this.machineName = machineName;
    this.clock = clock;
    this.startDelay = startDelay;
    this.onPoll = onPoll || (() => {});
    // When the server stops waiting on the last long poll. Aborting a poll
    // here does not always end it on the server (a proxy can keep it open),
    // and a poll the server still holds would take the next command for this
    // session and lose it. See Controller.connect.
    this.pollBusyUntil = 0;

    this.sessionId = null;
    this.pollWait = 25;
    this.state = Status.OFF;
    this.statusText = "Switched off.";

    this._stop = new Signal();
    this._kill = new Signal();
    this._wake = new Signal();
    this._sessionReady = new Signal();
    this.finished = new Signal();
    this._inFlight = null;
    this._deleted = false;
    this._authDead = false;
    this._heartbeatDone = false;
    this._loops = [];
  }

  // ------------------------------------------------------------------ control

  start() {
    this._setStatus(Status.CONNECTING, "Connecting to NOLGIA...");
    this._loops = [this._guard(this._heartbeatLoop()), this._guard(this._pollLoop())];
  }

  _guard(promise) {
    return promise.catch((err) => this.log("NOLGIA plugin bug: " + (err && (err.stack || err))));
  }

  /** Stop taking commands. A command already running still reports its
   *  result; then the session is closed (DELETE) and `finished` is set. */
  stop() {
    this._stop.set();
    this._wake.set();
    this._sessionReady.set();
    // End a long poll in progress: left waiting, it could take a command
    // queued after the person switched on again.
    if (this._pollAbort) {
      try {
        this._pollAbort.abort();
      } catch (err) {
        // ignore
      }
    }
  }

  /** Stop at once, for example when Photoshop quits. No more retries. */
  kill() {
    this.stop();
    this._kill.set();
  }

  /** Photoshop is quitting: tell NOLGIA this session is gone right away,
   *  without waiting for the loops, so callers get "not connected" instead
   *  of a command nobody will run. Resolves when NOLGIA answered (or after
   *  `timeout` seconds). */
  async closeNow(timeout = 2) {
    this.kill();
    const sessionId = this._deleted || this._authDead ? null : this.sessionId;
    this._deleted = true;
    if (!sessionId) return;
    try {
      await this.api.deleteSession(sessionId, timeout);
      this.log("Disconnected from NOLGIA.");
    } catch (err) {
      this.log("Could not tell NOLGIA we disconnected (" + (err && err.message) + ").");
    }
  }

  requestHeartbeat() {
    this._wake.set();
  }

  get stopping() {
    return this._stop.isSet;
  }

  get busyWith() {
    return this._inFlight;
  }

  // ---------------------------------------------------------------- internals

  _setStatus(state, text) {
    this.state = state;
    this.statusText = text;
    try {
      this.onStatus(state, text);
    } catch (err) {
      // the panel must not break the worker
    }
  }

  _wait(seconds) {
    return this._stop.wait(seconds * 1000);
  }

  /** The full state, every time: the API resets fields left out. */
  payload() {
    const snap = this.snapshot() || {};
    const doc = snap.document || {};
    const document = { name: String(doc.name || "").slice(0, MAX_DOCUMENT_NAME) };
    if (doc.path && String(doc.path).length <= MAX_DOCUMENT_PATH) document.path = String(doc.path);
    return {
      instance_id: this.instanceId,
      app: APP,
      app_version: oneLine(snap.app_version, MAX_VERSION),
      plugin_version: PLUGIN_VERSION,
      machine_name: oneLine(this.machineName, MAX_MACHINE_NAME),
      document,
      capabilities: CAPABILITIES.slice(),
      allow_agent: snap.allow_agent === undefined ? true : Boolean(snap.allow_agent),
    };
  }

  _authFailed(err) {
    this._authDead = true;
    const text = "NOLGIA did not accept your sign in. Sign in again.";
    this._setStatus(Status.SIGNED_OUT, text);
    this.log(text + " (" + (err && err.message) + ")");
    try {
      this.onAuthFailed(text);
    } catch (e) {
      // ignore
    }
    this.stop();
  }

  _retryText(err, delay) {
    let why;
    if (err instanceof NetworkError) why = "Cannot reach NOLGIA (" + err.message + ").";
    else if (err.status === 429) why = "NOLGIA asked us to slow down.";
    else if (err.status >= 500) why = "NOLGIA is having trouble (" + err.status + ").";
    else why = "NOLGIA refused the connection: " + (err.detail || err.title || err.status);
    return why + " Trying again in " + Math.max(1, Math.round(delay)) + " s.";
  }

  async _heartbeatLoop() {
    const backoff = new Backoff(1, 60);
    try {
      if (this.startDelay > 0) {
        this._setStatus(Status.CONNECTING, "Reconnecting to NOLGIA...");
        await this._wait(this.startDelay);
      }
      while (!this._stop.isSet) {
        let data;
        try {
          data = await this.api.registerSession(this.payload());
        } catch (err) {
          if (err instanceof Unauthorized) {
            this._authFailed(err);
            break;
          }
          if (err instanceof ApiError || err instanceof NetworkError) {
            const delay = err.retryAfter || backoff.next();
            this._setStatus(Status.RETRYING, this._retryText(err, delay));
            this.log(this.statusText);
            await this._wait(delay);
            continue;
          }
          throw err;
        }
        // The API answers the session itself; accept {"session": {...}} too.
        const session = data && data.id ? data : data && data.session;
        const sessionId = session && typeof session === "object" ? session.id : null;
        if (!sessionId) {
          const delay = backoff.next();
          this._setStatus(Status.RETRYING, "NOLGIA answered without a session. Trying again in " + Math.round(delay) + " s.");
          await this._wait(delay);
          continue;
        }
        backoff.reset();
        let wait = Number(data.poll_wait_seconds || session.poll_wait_seconds || 25);
        wait = Number.isFinite(wait) ? Math.max(1, Math.min(25, Math.floor(wait))) : 25;
        const first = this.sessionId !== sessionId;
        this.sessionId = sessionId;
        this.pollWait = wait;
        this._sessionReady.set();
        if (first || this.state !== Status.CONNECTED) {
          this._setStatus(Status.CONNECTED, CONNECTED_TEXT);
          this.log("Connected to NOLGIA (session " + sessionId + ").");
        }
        await this._wake.wait(this.heartbeatInterval * 1000);
        this._wake.clear();
      }
    } finally {
      this._heartbeatDone = true;
      await this._closeIfIdle();
    }
  }

  _notePoll(until) {
    this.pollBusyUntil = until;
    try {
      this.onPoll(until);
    } catch (err) {
      // only a hint for the next connection
    }
  }

  _sessionLost(sessionId) {
    if (this.sessionId === sessionId) {
      this.sessionId = null;
      this._sessionReady.clear();
    }
    this._wake.set();
  }

  async _pollLoop() {
    const backoff = new Backoff(1, 60);
    try {
      while (!this._stop.isSet) {
        if (!(await this._sessionReady.wait(500))) continue;
        const sessionId = this.sessionId;
        const wait = this.pollWait;
        if (this._stop.isSet || !sessionId) {
          if (!sessionId) this._sessionReady.clear();
          continue;
        }
        let data;
        this._pollAbort = typeof AbortController !== "undefined" ? new AbortController() : null;
        this._notePoll(this.clock() + wait + 1);
        try {
          data = await this.api.nextCommand(sessionId, wait, this._pollAbort ? this._pollAbort.signal : null);
          this._notePoll(0);
        } catch (err) {
          if (this._stop.isSet) break;
          if (err instanceof Unauthorized) {
            this._authFailed(err);
            break;
          }
          if (err instanceof ApiError) {
            if (sessionGone(err)) {
              this.log("NOLGIA closed this session; registering again.");
              this._sessionLost(sessionId);
              continue;
            }
            const delay = err.retryAfter || backoff.next();
            this._setStatus(Status.RETRYING, this._retryText(err, delay));
            await this._wait(delay);
            continue;
          }
          if (err instanceof NetworkError) {
            const delay = backoff.next();
            this._setStatus(Status.RETRYING, this._retryText(err, delay));
            await this._wait(delay);
            this._wake.set(); // re-check the session as soon as we are back
            continue;
          }
          throw err;
        }
        backoff.reset();
        if (this.state === Status.RETRYING) this._setStatus(Status.CONNECTED, CONNECTED_TEXT);
        if (data) await this._handle(new Command(data, undefined, this.clock));
      }
    } finally {
      await this._closeIfIdle();
    }
  }

  async _handle(command) {
    this._inFlight = command.id;
    this.activity.add(command.id, command.kind, command.caller);
    this.log("Command " + command.kind + " (" + command.id + ") from " + command.callerLabel + ".");
    try {
      let outcome;
      if (this._stop.isSet) outcome = [false, null, "NOLGIA was switched off in Photoshop before this command ran."];
      else outcome = await this._execute(command);
      await this._post(command, outcome[0], outcome[1], outcome[2]);
    } finally {
      this._inFlight = null;
      await this._closeIfIdle();
    }
  }

  async _execute(command) {
    try {
      command.args = validate(command.kind, command.args);
      const prepared = this.prepare ? await this.prepare(command) : null;
      if (!this._killed) this._killed = this._kill.wait().then(() => null);
      const outcome = await Promise.race([this.executor.submit(command, prepared), this._killed]);
      if (outcome === null) return [false, null, "Photoshop closed before this command finished."];
      if (outcome.ok && this.finish) return [true, await this.finish(command, outcome.result), null];
      return [Boolean(outcome.ok), outcome.result === undefined ? null : outcome.result, outcome.error || null];
    } catch (err) {
      if (err instanceof CommandError) return [false, err.result, err.message];
      if (err instanceof Unauthorized) return [false, null, "NOLGIA did not accept the sign in while moving files."];
      if (err instanceof NetworkError) return [false, null, "Could not reach NOLGIA while moving files (" + err.message + ")."];
      if (err instanceof ApiError) return [false, null, err.message];
      return [false, null, "The NOLGIA plugin hit a bug:\n" + ((err && (err.stack || err.message)) || String(err))];
    }
  }

  async _post(command, ok, result, error) {
    const body = resultBody(ok, result, error);
    const backoff = new Backoff(1, 30);
    // The API refuses a result at or after expires_at, so retrying past that
    // is pointless (a few seconds of slack for the clocks).
    const giveUpAt = command.expires + 5;
    for (;;) {
      let delay;
      try {
        await this.api.postResult(command.id, body);
        this.activity.update(command.id, body.status, body.error || "");
        this.log("Command " + command.kind + " " + body.status + ".");
        return;
      } catch (err) {
        if (err instanceof Unauthorized) {
          this.activity.update(command.id, "failed", "Sign in no longer accepted");
          this._authFailed(err);
          return;
        }
        if (err instanceof ApiError) {
          if (err.status === 409) {
            // command_not_running: drop the outcome
            const status = (err.detail || "").includes("expired") ? "expired" : "cancelled";
            this.activity.update(command.id, status, err.detail || "Stopped on NOLGIA");
            this.log("NOLGIA no longer wanted the result of " + command.kind + " (" + (err.detail || err.code) + ").");
            return;
          }
          if (err.status === 413) {
            this.activity.update(command.id, "failed", "Result over 1 MB");
            this.log("The result of " + command.kind + " was over 1 MB; NOLGIA marked it failed.");
            return;
          }
          if (err.status === 404) {
            this.activity.update(command.id, "expired", "NOLGIA no longer has this command");
            return;
          }
          if (err.status !== 429 && err.status < 500) {
            this.activity.update(command.id, "failed", err.message);
            this.log("NOLGIA refused the result of " + command.kind + ": " + err.message);
            return;
          }
          delay = err.retryAfter || backoff.next();
        } else if (err instanceof NetworkError) {
          delay = backoff.next();
        } else {
          throw err;
        }
      }
      if (this.clock() + delay > giveUpAt) {
        this.activity.update(command.id, "failed", "Could not send the result to NOLGIA");
        this.log("Gave up sending the result of " + command.kind + ".");
        return;
      }
      if (await this._kill.wait(delay * 1000)) return;
    }
  }

  /** After stop: close the session once nothing is running and the heartbeat
   *  loop is done, then set `finished`. */
  async _closeIfIdle() {
    if (!this._stop.isSet || !this._heartbeatDone) return;
    if (this._inFlight || this.finished.isSet || this._closing) return;
    this._closing = true;
    const sessionId = this._deleted || this._authDead ? null : this.sessionId;
    this._deleted = true;
    if (sessionId) {
      try {
        await this.api.deleteSession(sessionId, 5);
        this.log("Disconnected from NOLGIA.");
      } catch (err) {
        this.log("Could not tell NOLGIA we disconnected (" + (err && err.message) + ").");
      }
    }
    if (this.state !== Status.SIGNED_OUT) this._setStatus(Status.OFF, "Switched off.");
    this.finished.set();
  }
}

module.exports = { BridgeWorker, Status, sessionGone, CONNECTED_TEXT };
