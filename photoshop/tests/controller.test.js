// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { Controller } = require("../core/controller.js");
const { CAPABILITIES } = require("../core/commands.js");
const { PLUGIN_VERSION } = require("../core/constants.js");
const { TOKEN, startMock, call, enqueue, waitCommand, mockState, until, fakeHost } = require("./support.js");

let mock;
test.before(async () => {
  mock = await startMock({ deviceInterval: 1, autoApproveAfter: 1 });
});
test.after(async () => {
  await mock.stop();
});
test.beforeEach(async () => {
  await call(mock, "POST", "/mock/reset", null, { token: null });
});

async function stopped(ctl) {
  const worker = ctl.worker;
  ctl.disconnect();
  if (worker) await worker.finished.wait(10000);
}

test("the developer file connects at startup with its own token", async () => {
  const host = fakeHost({ dev: { token: TOKEN, api_url: mock.baseUrl, autoconnect: true }, document: { name: "street.psd", path: "C:\\x\\street.psd" } });
  const ctl = new Controller(host);
  await ctl.init();
  try {
    const { data } = await until(async () => {
      const res = await call(mock, "GET", "/v1/bridge/sessions");
      return res.data.sessions.length ? res : null;
    }, { what: "a session" });
    const session = data.sessions[0];
    assert.equal(session.app, "photoshop");
    assert.deepEqual(session.capabilities, CAPABILITIES);
    assert.equal(session.plugin_version, PLUGIN_VERSION);
    assert.equal(session.app_version, "27.5.0");
    assert.equal(session.machine_name, "Unit Test PC");
    assert.equal(session.instance_id, "unit-instance");
    assert.deepEqual(session.document, { name: "street.psd", path: "C:\\x\\street.psd" });
    assert.equal(ctl.usingDevToken, true);
    await until(() => ctl.accountEmail() === "test@nolgia.ai", { what: "the email" });
    assert.equal(host.savedToken, null, "a developer token is never saved");
    assert.equal(host.savedSettings.connected, true);
    const id = await enqueue(mock, "info");
    const cmd = await waitCommand(mock, id);
    assert.equal(cmd.status, "succeeded");
    assert.equal(ctl.activity.items()[0].status, "succeeded");
  } finally {
    await stopped(ctl);
  }
  const state = await mockState(mock);
  assert.equal(state.deleted_sessions.length, 1, "switching off closes the session");
  assert.equal(host.savedSettings.connected, false);
});

test("sign in with the device flow, then sign out", async () => {
  const host = fakeHost({ dev: { api_url: mock.baseUrl } });
  const ctl = new Controller(host);
  await ctl.init();
  assert.equal(ctl.signedIn, false);
  assert.equal(ctl.statusLine(), "Not signed in.");
  const signing = ctl.signIn();
  await until(() => ctl.login && ctl.login.prompt, { what: "the code" });
  assert.match(ctl.statusLine(), /^Enter code [A-Z]{4}-[A-Z]{4} in your browser to sign in\.$/);
  await signing;
  assert.equal(host.opened.length, 1);
  assert.match(host.opened[0], /\/device\?user_code=/);
  assert.match(host.savedToken.token, /^nol_mock_/);
  assert.equal(host.savedToken.apiUrl, mock.baseUrl);
  assert.equal(host.savedToken.email, "test@nolgia.ai");
  assert.ok(host.savedToken.expiresAt > Date.now() / 1000);
  await until(() => ctl.worker && ctl.worker.state === "connected", { what: "connected" });
  assert.equal(ctl.accountEmail(), "test@nolgia.ai");
  const worker = ctl.worker;
  await ctl.signOut();
  await worker.finished.wait(10000);
  assert.equal(host.savedToken, null);
  assert.equal(ctl.signedIn, false);
  assert.equal(ctl.statusLine(), "Signed out.");
  assert.equal((await mockState(mock)).deleted_sessions.length, 1);
});

test("a saved sign in is only sent to the API it came from", async () => {
  const host = fakeHost({ savedToken: { token: TOKEN, email: "a@b.c", expiresAt: 0, apiUrl: "https://api.nolgia.ai/v1" }, dev: { api_url: mock.baseUrl } });
  const ctl = new Controller(host);
  await ctl.init();
  assert.equal(ctl.signedIn, false, "a production token must not go to another API");
  host.dev = null;
  const ctl2 = new Controller(host);
  await ctl2.init();
  assert.equal(ctl2.signedIn, true);
});

test("an expired saved sign in is forgotten", async () => {
  const host = fakeHost({ savedToken: { token: TOKEN, email: "a@b.c", expiresAt: 1000, apiUrl: mock.baseUrl }, dev: { api_url: mock.baseUrl }, savedSettings: { connected: true } });
  const ctl = new Controller(host);
  await ctl.init();
  assert.equal(ctl.signedIn, false);
  assert.equal(host.savedToken, null);
  assert.equal(ctl.statusLine(), "Your NOLGIA sign in expired. Sign in again.");
  assert.equal(ctl.worker, null);
});

test("reconnects at startup when Connected was left on", async () => {
  const host = fakeHost({ savedToken: { token: TOKEN, email: "", expiresAt: 0, apiUrl: mock.baseUrl }, dev: { api_url: mock.baseUrl }, savedSettings: { connected: true } });
  const ctl = new Controller(host);
  await ctl.init();
  try {
    await until(() => ctl.worker && ctl.worker.state === "connected", { what: "connected" });
  } finally {
    await stopped(ctl);
  }
});

test("a refused token signs out and says so", async () => {
  const host = fakeHost({ savedToken: { token: "not-a-token", email: "x@y.z", expiresAt: 0, apiUrl: mock.baseUrl }, dev: { api_url: mock.baseUrl }, savedSettings: { connected: true } });
  const ctl = new Controller(host);
  await ctl.init();
  await until(() => ctl.worker === null, { what: "the worker to stop" });
  assert.equal(host.savedToken, null);
  assert.equal(ctl.statusLine(), "NOLGIA did not accept your sign in. Sign in again.");
  assert.equal(host.savedSettings.connected, false);
});

test("Allow NOLGIA Agent off refuses the agent; Pause stops commands", async () => {
  const host = fakeHost({ dev: { token: TOKEN, api_url: mock.baseUrl, autoconnect: true } });
  const ctl = new Controller(host);
  await ctl.init();
  try {
    await until(() => ctl.worker && ctl.worker.state === "connected", { what: "connected" });
    const cmd = await waitCommand(mock, await enqueue(mock, "info", {}, { caller: "agent" }));
    assert.equal(cmd.status, "succeeded");
    assert.equal(ctl.activity.items()[0].caller, "agent");
    ctl.setSetting("allow_agent", false);
    await until(async () => (await mockState(mock)).last_heartbeat.allow_agent === false, { what: "the heartbeat" });
    const res = await call(mock, "POST", "/v1/bridge/commands", { app: "photoshop", kind: "info" }, { headers: { "X-Nolgia-Surface": "hermes" } });
    assert.equal(res.status, 403);
    assert.equal(res.data.code, "agent_not_allowed");
    const worker = ctl.worker;
    await ctl.togglePause();
    await worker.finished.wait(10000);
    assert.equal(ctl.connected, false);
    const res2 = await call(mock, "POST", "/v1/bridge/commands", { app: "photoshop", kind: "info" });
    assert.equal(res2.status, 409);
    // Resume waits until the server has let go of the long poll that was
    // running when Pause was clicked (up to 25 s), then registers again.
    await ctl.togglePause();
    assert.ok(ctl.worker.startDelay > 20 && ctl.worker.startDelay <= 27, "delay " + ctl.worker.startDelay);
    assert.equal(ctl.statusLine(), "Reconnecting to NOLGIA...");
    assert.equal(host.remembered.poll_busy_until > Date.now() / 1000, true);
  } finally {
    await stopped(ctl);
  }
});

test("Ask before running code: approve, deny, and time out", async () => {
  const host = fakeHost({ dev: { token: TOKEN, api_url: mock.baseUrl, autoconnect: true, ask_before_run: true } });
  const ctl = new Controller(host);
  await ctl.init();
  try {
    await until(() => ctl.worker && ctl.worker.state === "connected", { what: "connected" });
    const first = await enqueue(mock, "run", { language: "uxp", code: "result = 1" });
    await until(() => ctl.executor.approvals.length === 1, { what: "an approval" });
    const pending = ctl.executor.approvals[0];
    assert.equal(pending.request.title, "Your agent wants to run code in Photoshop");
    assert.equal(pending.request.code, "result = 1");
    assert.equal(ctl.statusLine(), "Waiting for you to approve a request.");
    assert.equal(ctl.statusState(), "approval");
    assert.deepEqual(host.approvalsSeen[host.approvalsSeen.length - 1], [first]);
    ctl.approve(first);
    assert.equal((await waitCommand(mock, first)).status, "succeeded");

    const second = await enqueue(mock, "run", { code: "result = 2" });
    await until(() => ctl.executor.approvals.length === 1, { what: "an approval" });
    ctl.deny(second);
    const denied = await waitCommand(mock, second);
    assert.equal(denied.status, "failed");
    assert.match(denied.error, /clicked Deny/);

    // info never needs approval
    assert.equal((await waitCommand(mock, await enqueue(mock, "info"))).status, "succeeded");

    const third = await enqueue(mock, "run", { code: "result = 3" }, { timeout: 5 });
    const late = await waitCommand(mock, third, 20);
    assert.equal(late.status, "failed");
    assert.match(late.error, /Nobody approved this in Photoshop in time/);
    assert.equal(ctl.executor.approvals.length, 0);
  } finally {
    await stopped(ctl);
  }
});

test("switching off while a request waits fails it with the reason", async () => {
  const host = fakeHost({ dev: { token: TOKEN, api_url: mock.baseUrl, autoconnect: true, ask_before_run: true } });
  const ctl = new Controller(host);
  await ctl.init();
  await until(() => ctl.worker && ctl.worker.state === "connected", { what: "connected" });
  const id = await enqueue(mock, "run", { code: "result = 1" });
  await until(() => ctl.executor.approvals.length === 1, { what: "an approval" });
  const worker = ctl.worker;
  ctl.disconnect();
  await worker.finished.wait(10000);
  const cmd = await waitCommand(mock, id);
  assert.equal(cmd.status, "failed");
  assert.match(cmd.error, /switched off in Photoshop/);
});

test("a long poll left waiting by the last Photoshop session delays the first connection", async () => {
  const host = fakeHost({ dev: { token: TOKEN, api_url: mock.baseUrl, autoconnect: true } });
  host.remembered.poll_busy_until = Date.now() / 1000 + 1.5;
  const ctl = new Controller(host);
  const started = Date.now();
  await ctl.init();
  try {
    assert.equal(ctl.statusLine(), "Reconnecting to NOLGIA...");
    await until(() => ctl.worker && ctl.worker.state === "connected", { what: "connected", timeout: 10000 });
    assert.ok(Date.now() - started >= 1200, "connected before the old poll ran out");
  } finally {
    await stopped(ctl);
  }
});
