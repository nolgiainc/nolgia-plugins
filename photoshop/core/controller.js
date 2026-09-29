// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Ties the pieces together: settings, sign in, the worker, approvals and
// the activity list. Everything Photoshop or UXP specific comes in through
// `host` (see ps/host.js), so this runs under Node in the tests too.
"use strict";

const { PLUGIN_VERSION, DEFAULT_API_URL } = require("./constants.js");
const { ApiClient, normalizeBaseUrl } = require("./api.js");
const { DeviceLogin, LoginCancelled, LoginError } = require("./auth.js");
const { ActivityLog } = require("./commands.js");
const { Executor, ApprovalRequest } = require("./executor.js");
const { BridgeWorker, Status } = require("./worker.js");

const DEFAULT_SETTINGS = { connected: false, allow_agent: true, ask_before_run: false };

class LoginState {
  constructor(login) {
    this.login = login;
    this.prompt = null;
  }
}

class Controller {
  constructor(host) {
    this.host = host;
    this.activity = new ActivityLog();
    this.executor = new Executor({
      run: (command, prepared) => this._run(command, prepared),
      needsApproval: (command) => this._needsApproval(command),
      canAsk: () => true,
      onStatus: (id, status) => this.activity.update(id, status),
      onChange: () => this._approvalsChanged(),
    });
    this.activity.onChange(() => this.bump());
    this.settings = Object.assign({}, DEFAULT_SETTINGS);
    this.tokenRecord = null; // {token, email, expiresAt, apiUrl} from secure storage
    this.dev = null; // the developer file, when there is one
    this.worker = null;
    this.login = null;
    this.message = "";
    this.snapshot = {};
    this.version = 0;
    this.instanceId = null;
    this.devEmail = "";
    this._listeners = [];
    this._wantConnect = false;
    this._pollBusyUntil = 0;
  }

  // ---------------------------------------------------------------- settings

  get apiUrl() {
    return normalizeBaseUrl((this.dev && this.dev.api_url) || DEFAULT_API_URL);
  }

  /** The token to use: the developer file's, else the saved sign in when it
   *  was made with this same API. */
  token() {
    if (this.dev && this.dev.token) return this.dev.token;
    const rec = this.tokenRecord;
    if (rec && rec.token && normalizeBaseUrl(rec.apiUrl || DEFAULT_API_URL) === this.apiUrl) return rec.token;
    return null;
  }

  get usingDevToken() {
    return Boolean(this.dev && this.dev.token);
  }

  get signedIn() {
    return Boolean(this.token());
  }

  get connected() {
    return this.worker !== null && !this.worker.stopping;
  }

  accountEmail() {
    if (this.usingDevToken) return this.devEmail;
    return (this.tokenRecord && this.tokenRecord.email) || "";
  }

  /** Small facts kept between Photoshop sessions (see ps/host.js). */
  _remember(key, value) {
    if (key === "poll_busy_until") this._pollBusyUntil = value || 0;
    try {
      if (this.host.remember) this.host.remember(key, value);
    } catch (err) {
      // a hint only
    }
  }

  _saveSettings() {
    try {
      this.host.saveSettings(Object.assign({}, this.settings));
    } catch (err) {
      this.log("Could not save the settings (" + (err && err.message) + ").");
    }
  }

  setSetting(name, value) {
    if (this.settings[name] === value) return;
    this.settings[name] = value;
    this._saveSettings();
    if (name === "allow_agent") this.updateSnapshot();
    this.bump();
  }

  // ----------------------------------------------------------------- change

  onChange(fn) {
    this._listeners.push(fn);
  }

  bump() {
    this.version += 1;
    for (const fn of this._listeners) {
      try {
        fn();
      } catch (err) {
        // the panel must not break the plugin
      }
    }
  }

  log(message) {
    try {
      this.host.log("NOLGIA: " + message);
    } catch (err) {
      // ignore
    }
  }

  // ---------------------------------------------------------------- startup

  /** Load settings, the saved sign in and the developer file, then connect
   *  when the person left NOLGIA on (or the developer file says so). */
  async init() {
    try {
      this.settings = Object.assign({}, DEFAULT_SETTINGS, this.host.loadSettings() || {});
    } catch (err) {
      this.settings = Object.assign({}, DEFAULT_SETTINGS);
    }
    try {
      this.tokenRecord = await this.host.loadToken();
    } catch (err) {
      this.tokenRecord = null;
    }
    try {
      // A poll the last Photoshop session left waiting on the server.
      this._pollBusyUntil = Number((this.host.recall && this.host.recall("poll_busy_until")) || 0) || 0;
    } catch (err) {
      this._pollBusyUntil = 0;
    }
    try {
      this.dev = await this.host.readDevFile();
    } catch (err) {
      this.dev = null;
      this.log("Could not read the developer file (" + (err && err.message) + ").");
    }
    if (this.dev) {
      for (const key of ["allow_agent", "ask_before_run"]) {
        if (typeof this.dev[key] === "boolean") this.settings[key] = this.dev[key];
      }
      this.log("Using the developer file: API " + this.apiUrl + (this.dev.token ? ", its own token" : "") + ".");
    }
    const rec = this.tokenRecord;
    if (!this.usingDevToken && rec && rec.expiresAt && rec.expiresAt < Date.now() / 1000) {
      await this._forgetToken();
      this.message = "Your NOLGIA sign in expired. Sign in again.";
    }
    const wanted = (this.dev && this.dev.autoconnect) || this.settings.connected;
    if (wanted && this.signedIn) await this.connect();
    else if (this.settings.connected) this.setSetting("connected", false);
    this.bump();
  }

  // ------------------------------------------------------------------- status

  statusLine() {
    if (this.login) {
      if (this.login.prompt) return "Enter code " + this.login.prompt.userCode + " in your browser to sign in.";
      return "Starting sign in...";
    }
    if (this.executor.approvals.length) return "Waiting for you to approve a request.";
    if (this.worker) {
      const busy = this.worker.busyWith;
      if (busy && this.worker.state === Status.CONNECTED) {
        const item = this.activity.items().find((i) => i.id === busy);
        if (item) return "Working on " + item.kind + "...";
      }
      if (this.worker.stopping) return "Switching off...";
      return this.worker.statusText;
    }
    if (this.message) return this.message;
    if (!this.signedIn) return "Not signed in.";
    return "Switched off. Turn on Connected to let NOLGIA work in this Photoshop.";
  }

  /** "off", "connecting", "connected", "retrying", "signed_out" or "approval",
   *  for the status dot. */
  statusState() {
    if (this.executor.approvals.length) return "approval";
    if (this.worker) return this.worker.stopping ? "connecting" : this.worker.state;
    return this.signedIn ? "off" : "signed_out";
  }

  // --------------------------------------------------------- connect/disconnect

  api(token) {
    return new ApiClient({
      baseUrl: this.apiUrl,
      token: token === undefined ? this.token() : token,
      fetch: this.host.fetch,
      userAgent: "nolgia-photoshop/" + PLUGIN_VERSION,
    });
  }

  /** Start talking to NOLGIA. Resolves true when started. */
  async connect() {
    this.message = "";
    if (this.worker) {
      if (this.worker.stopping) this._wantConnect = true; // start again once the old one is done
      return true;
    }
    const token = this.token();
    if (!token) {
      this.message = "Sign in to NOLGIA first.";
      this.setSetting("connected", false);
      this.bump();
      return false;
    }
    if (!this.instanceId) this.instanceId = await this.host.instanceId((this.dev && this.dev.instance_id) || null);
    const api = this.api(token);
    this.updateSnapshot();
    this.executor.reopen();
    const worker = new BridgeWorker({
      api,
      executor: this.executor,
      activity: this.activity,
      instanceId: this.instanceId,
      snapshot: () => this.snapshot,
      prepare: (command) => this.host.prepare(command, api, this.snapshot),
      finish: (command, result) => this.host.finish(command, result, api),
      onStatus: () => this.bump(),
      onAuthFailed: (text) => this._authFailed(text),
      log: (m) => this.log(m),
      machineName: this.host.machineName(),
      heartbeatInterval: (this.dev && this.dev.heartbeat_seconds) || 20,
      // A long poll from the last connection may still be waiting on the
      // server for this same session; let it run out before registering.
      startDelay: Math.min(30, Math.max(0, this._pollBusyUntil - Date.now() / 1000)),
      onPoll: (until) => this._remember("poll_busy_until", until),
    });
    this.worker = worker;
    worker.finished.wait().then(() => this._workerDone(worker));
    worker.start();
    this.setSetting("connected", true);
    if (!this.accountEmail()) this._fetchEmail(api);
    this.log("Connecting to " + api.baseUrl + " as instance " + this.instanceId + ".");
    this.bump();
    return true;
  }

  async _fetchEmail(api) {
    const token = api.token;
    try {
      const me = await api.getMe();
      const email = (me && me.email) || "";
      if (!email || token !== this.token()) return;
      if (this.usingDevToken) this.devEmail = email;
      else if (this.tokenRecord) {
        this.tokenRecord.email = email;
        await this.host.saveToken(this.tokenRecord);
      }
      this.log("Signed in as " + email + ".");
      this.bump();
    } catch (err) {
      // a refused token shows up through the worker anyway
    }
  }

  disconnect(reason = "NOLGIA was switched off in Photoshop before this ran.") {
    this._wantConnect = false;
    this.setSetting("connected", false);
    if (this.worker && !this.worker.stopping) {
      this.executor.close(reason);
      this.worker.stop();
      this.log("Switching off.");
    }
    this.bump();
  }

  togglePause() {
    if (this.connected) this.disconnect();
    else return this.connect();
    return Promise.resolve(false);
  }

  _workerDone(worker) {
    this._pollBusyUntil = Math.max(this._pollBusyUntil, worker.pollBusyUntil || 0);
    if (this.worker !== worker) return;
    this.worker = null;
    this.bump();
    if (this._wantConnect) {
      this._wantConnect = false;
      this.connect();
    }
  }

  async _authFailed(text) {
    if (this.usingDevToken) this.dev.token = null;
    else await this._forgetToken();
    this.disconnect();
    this.message = text;
    this.bump();
  }

  async _forgetToken() {
    this.tokenRecord = null;
    try {
      await this.host.saveToken(null);
    } catch (err) {
      this.log("Could not clear the saved sign in (" + (err && err.message) + ").");
    }
  }

  // ------------------------------------------------------------------- sign in

  async signIn() {
    if (this.login) return;
    this.message = "";
    const login = new DeviceLogin(this.api(null));
    const state = new LoginState(login);
    this.login = state;
    this.bump();
    try {
      const prompt = await login.start();
      state.prompt = prompt;
      this.bump();
      try {
        await this.host.openUrl(prompt.openUrl);
      } catch (err) {
        this.log("Could not open the browser (" + (err && err.message) + "). Open " + prompt.openUrl + " yourself.");
      }
      const token = await login.waitForToken(prompt);
      if (!token.email) {
        try {
          token.email = (await this.api(token.accessToken).getMe()).email || null;
        } catch (err) {
          // shown later through GET /me in connect()
        }
      }
      if (this.login !== state) return; // cancelled meanwhile
      this.login = null;
      if (this.dev) this.dev.token = null; // a real sign in replaces the developer token
      this.tokenRecord = { token: token.accessToken, email: token.email || "", expiresAt: token.expiresAt || 0, apiUrl: this.apiUrl };
      await this.host.saveToken(this.tokenRecord);
      this.log("Signed in" + (token.email ? " as " + token.email : "") + ".");
      await this.connect();
    } catch (err) {
      if (this.login === state) this.login = null;
      if (err instanceof LoginCancelled) this.message = "";
      else if (err instanceof LoginError) this.message = err.message;
      else this.message = "Sign in failed: " + (err && err.message);
      this.bump();
    }
  }

  cancelSignIn() {
    if (this.login) {
      this.login.login.cancel();
      this.login = null;
      this.bump();
    }
  }

  async signOut() {
    this.cancelSignIn();
    this.disconnect("You signed out of NOLGIA in Photoshop before this ran.");
    if (this.dev) this.dev.token = null;
    this.devEmail = "";
    await this._forgetToken();
    this.message = "Signed out.";
    this.bump();
  }

  // ----------------------------------------------------------------- commands

  _needsApproval(command) {
    if (command.kind === "run" && this.settings.ask_before_run) {
      const code = String(command.args.code || "");
      return new ApprovalRequest(command.callerLabel + " wants to run code in Photoshop", code.split(/\r?\n/), {
        approveLabel: "Run code",
        code,
      });
    }
    return null;
  }

  approve(commandId) {
    this.executor.approve(commandId);
    this.bump();
  }

  deny(commandId) {
    this.executor.deny(commandId);
    this.bump();
  }

  _approvalsChanged() {
    this.bump();
    if (this.host.approvalsChanged) {
      try {
        this.host.approvalsChanged(this.executor.approvals.slice(), this);
      } catch (err) {
        this.log("Could not show the approval window (" + (err && err.message) + "); use the NOLGIA panel.");
      }
    }
  }

  async _run(command, prepared) {
    const runner = this.host.runners[command.kind];
    if (!runner) return { ok: false, result: null, error: "This plugin cannot do " + command.kind + "." };
    this.bump();
    try {
      const result = await runner(command.args, prepared, command);
      return { ok: true, result, error: null };
    } catch (err) {
      if (err && err.name === "CommandError") return { ok: false, result: err.result, error: err.message };
      return { ok: false, result: null, error: "Photoshop raised an error:\n" + ((err && (err.stack || err.message)) || String(err)) };
    } finally {
      this.updateSnapshot();
    }
  }

  // ---------------------------------------------------------------- snapshot

  updateSnapshot() {
    let document = { name: "" };
    try {
      document = this.host.documentSnapshot() || { name: "" };
    } catch (err) {
      document = { name: "" };
    }
    let appVersion = "";
    try {
      appVersion = this.host.appVersion();
    } catch (err) {
      appVersion = "";
    }
    const snap = { document, allow_agent: Boolean(this.settings.allow_agent), app_version: appVersion };
    const before = this.snapshot || {};
    const changed =
      JSON.stringify([snap.document, snap.allow_agent]) !== JSON.stringify([before.document, before.allow_agent]);
    this.snapshot = snap;
    if (changed && this.worker) this.worker.requestHeartbeat();
    return changed;
  }

  /** Photoshop is closing or the plugin unloads. The Connected setting is
   *  kept, so NOLGIA reconnects next time. Resolves once NOLGIA knows. */
  shutdown() {
    this.cancelSignIn();
    const worker = this.worker;
    if (!worker) return Promise.resolve();
    this.executor.close("Photoshop is closing.");
    return worker.closeNow(2);
  }
}

module.exports = { Controller, DEFAULT_SETTINGS };
