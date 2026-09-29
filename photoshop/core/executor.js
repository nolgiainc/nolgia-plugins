// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Runs commands in Photoshop one at a time, asking the person first when a
// command needs it (code while "Ask before running code" is on). A command
// waiting for approval sits in `approvals` until the person clicks Approve
// or Deny, the plugin is switched off, or the command runs out of time.
"use strict";

class ApprovalRequest {
  /** What to ask the person. `headlessError` is the failure to report when
   *  there is nobody to ask. */
  constructor(title, lines, { headlessError, approveLabel = "Approve", code = "" } = {}) {
    this.title = title;
    this.lines = lines.slice();
    this.headlessError = headlessError || "Nobody could approve this in Photoshop, so it did not run.";
    this.approveLabel = approveLabel;
    this.code = code;
  }
}

class PendingApproval {
  constructor(command, request, settle) {
    this.command = command;
    this.request = request;
    this.decision = null;
    this._settle = settle;
  }

  get id() {
    return this.command.id;
  }
}

class Executor {
  /**
   * run(command, prepared) -> Promise<{ok, result, error}> does the work.
   * needsApproval(command) -> ApprovalRequest | null.
   * canAsk() -> whether anyone can answer an approval.
   * onStatus(commandId, status), onChange() report to the panel.
   */
  constructor({ run, needsApproval, canAsk, onStatus, onChange, clock = () => Date.now() / 1000 } = {}) {
    this._run = run;
    this._needsApproval = needsApproval || (() => null);
    this._canAsk = canAsk || (() => true);
    this._onStatus = onStatus || (() => {});
    this._onChange = onChange || (() => {});
    this._clock = clock;
    this._closedReason = null;
    this._chain = Promise.resolve();
    this.approvals = [];
  }

  /** Stop starting new commands; queued and waiting ones fail with `reason`. */
  close(reason) {
    this._closedReason = reason;
    for (const pending of this.approvals.slice()) this._decide(pending, false, reason);
  }

  reopen() {
    this._closedReason = null;
  }

  approve(commandId) {
    const pending = this.approvals.find((p) => p.id === commandId && p.decision === null);
    return pending ? this._decide(pending, true) : false;
  }

  deny(commandId) {
    const pending = this.approvals.find((p) => p.id === commandId && p.decision === null);
    return pending ? this._decide(pending, false, "The person at Photoshop clicked Deny, so this did not run.") : false;
  }

  _decide(pending, decision, reason) {
    if (pending.decision !== null) return false;
    pending.decision = decision;
    const i = this.approvals.indexOf(pending);
    if (i >= 0) this.approvals.splice(i, 1);
    pending._settle(decision, reason);
    this._onChange();
    return true;
  }

  /** Run a command after the ones already submitted. Resolves to
   *  {ok, result, error}; never rejects. */
  submit(command, prepared) {
    const next = this._chain.then(() => this._one(command, prepared));
    this._chain = next.catch(() => {});
    return next;
  }

  async _one(command, prepared) {
    if (this._closedReason) return { ok: false, result: null, error: this._closedReason };
    let request = null;
    try {
      request = this._needsApproval(command);
    } catch (err) {
      return { ok: false, result: null, error: "Could not check this command: " + (err && err.message) };
    }
    if (request) {
      if (!this._canAsk()) return { ok: false, result: null, error: request.headlessError };
      const verdict = await this._ask(command, request);
      if (!verdict.ok) return { ok: false, result: null, error: verdict.reason };
    }
    this._onStatus(command.id, "running");
    try {
      const out = await this._run(command, prepared);
      return out || { ok: false, result: null, error: "The command gave no result." };
    } catch (err) {
      return { ok: false, result: null, error: "The plugin failed to run this command: " + (err && (err.message || err)) };
    }
  }

  _ask(command, request) {
    return new Promise((resolve) => {
      let timer = null;
      const pending = new PendingApproval(command, request, (decision, reason) => {
        if (timer !== null) clearTimeout(timer);
        resolve({ ok: decision, reason });
      });
      const left = Math.max(0, command.deadline - this._clock());
      timer = setTimeout(() => {
        this._decide(pending, false, "Nobody approved this in Photoshop in time, so it did not run.");
      }, left * 1000);
      this.approvals.push(pending);
      this._onStatus(command.id, "approval");
      this._onChange();
    });
  }
}

module.exports = { Executor, ApprovalRequest, PendingApproval };
