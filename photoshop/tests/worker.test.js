// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { ApiClient } = require("../core/api.js");
const { ActivityLog, CAPABILITIES } = require("../core/commands.js");
const { Executor } = require("../core/executor.js");
const { BridgeWorker } = require("../core/worker.js");
const { TOKEN, startMock, call, enqueue, waitCommand, mockState, until } = require("./support.js");

let mock;
test.before(async () => {
  mock = await startMock();
});
test.after(async () => {
  await mock.stop();
});
test.beforeEach(async () => {
  await call(mock, "POST", "/mock/reset", null, { token: null });
});

function setup({ token = TOKEN, run, prepare, finish, snapshot } = {}) {
  const activity = new ActivityLog();
  const executor = new Executor({
    run: run || (async (cmd) => ({ ok: true, result: { kind: cmd.kind, args: cmd.args }, error: null })),
    onStatus: (id, status) => activity.update(id, status),
  });
  const snap = snapshot || { document: { name: "a.psd", path: "C:\\a.psd" }, allow_agent: true, app_version: "27.5.0" };
  const events = { authFailed: [] };
  const worker = new BridgeWorker({
    api: new ApiClient({ baseUrl: mock.baseUrl, token }),
    executor,
    activity,
    instanceId: "worker-test",
    snapshot: () => snap,
    prepare,
    finish,
    onAuthFailed: (text) => events.authFailed.push(text),
    machineName: "Test\u0000 PC\n",
    heartbeatInterval: 0.3,
  });
  return { worker, activity, snap, events };
}

async function connected(worker) {
  await until(() => worker.state === "connected", { what: "connected" });
}

async function stop(worker) {
  worker.stop();
  assert.equal(await worker.finished.wait(10000), true, "the worker did not finish");
}

test("registers the full state and keeps heartbeating", async () => {
  const { worker, snap } = setup();
  worker.start();
  await connected(worker);
  const first = (await mockState(mock)).last_heartbeat;
  assert.deepEqual(first, {
    instance_id: "worker-test",
    app: "photoshop",
    app_version: "27.5.0",
    plugin_version: "0.1.0",
    machine_name: "Test PC",
    document: { name: "a.psd", path: "C:\\a.psd" },
    capabilities: CAPABILITIES,
    allow_agent: true,
  });
  snap.document = { name: "b.psd" };
  worker.requestHeartbeat();
  await until(async () => (await mockState(mock)).last_heartbeat.document.name === "b.psd", { what: "the new document" });
  assert.ok((await mockState(mock)).heartbeats >= 2);
  await stop(worker);
  assert.equal((await mockState(mock)).deleted_sessions.length, 1);
  assert.equal(worker.state, "off");
});

test("runs commands, with prepare and finish around them", async () => {
  const seen = [];
  const { worker, activity } = setup({
    prepare: async (cmd) => {
      seen.push("prepare " + cmd.kind);
      return { downloaded: true };
    },
    run: async (cmd, prepared) => ({ ok: true, result: { prepared, _upload: 1 }, error: null }),
    finish: async (cmd, result) => {
      seen.push("finish");
      return { asset_id: "x", prepared: result.prepared };
    },
  });
  worker.start();
  await connected(worker);
  const id = await enqueue(mock, "import_asset", { asset_id: "a1" });
  const cmd = await waitCommand(mock, id);
  assert.equal(cmd.status, "succeeded");
  assert.deepEqual(cmd.result, { asset_id: "x", prepared: { downloaded: true } });
  assert.deepEqual(seen, ["prepare import_asset", "finish"]);
  assert.equal(activity.items()[0].status, "succeeded");
  const bad = await waitCommand(mock, await enqueue(mock, "preview", { width: 99999 }));
  assert.equal(bad.status, "failed");
  assert.match(bad.error, /at most 1920/);
  await stop(worker);
});

test("a failure keeps its partial result; plugin bugs are reported", async () => {
  let calls = 0;
  const { worker } = setup({
    run: async () => {
      calls += 1;
      if (calls === 1) return { ok: false, result: { value: null, stdout: "x", stderr: "" }, error: "Error: boom" };
      throw new Error("bug in the plugin");
    },
  });
  worker.start();
  await connected(worker);
  const one = await waitCommand(mock, await enqueue(mock, "run", { code: "x" }));
  assert.equal(one.status, "failed");
  assert.equal(one.error, "Error: boom");
  assert.deepEqual(one.result, { value: null, stdout: "x", stderr: "" });
  const two = await waitCommand(mock, await enqueue(mock, "run", { code: "x" }));
  assert.equal(two.status, "failed");
  assert.match(two.error, /bug in the plugin/);
  await stop(worker);
});

test("a command cancelled while it runs is dropped quietly", async () => {
  const { worker, activity } = setup({
    run: async () => {
      await new Promise((r) => setTimeout(r, 800));
      return { ok: true, result: {}, error: null };
    },
  });
  worker.start();
  await connected(worker);
  const id = await enqueue(mock, "run", { code: "x" });
  await until(async () => (await call(mock, "GET", "/v1/bridge/commands/" + id)).data.status === "running", { what: "running" });
  await call(mock, "POST", "/v1/bridge/commands/" + id + "/cancel");
  await until(() => activity.items()[0] && activity.items()[0].status === "cancelled", { what: "cancelled in the activity list" });
  assert.equal((await waitCommand(mock, await enqueue(mock, "info"))).status, "succeeded");
  await stop(worker);
});

test("a closed session registers again", async () => {
  const { worker } = setup();
  worker.start();
  await connected(worker);
  const sid = worker.sessionId;
  await new ApiClient({ baseUrl: mock.baseUrl, token: TOKEN }).deleteSession(sid);
  await until(async () => {
    const res = await call(mock, "GET", "/v1/bridge/sessions");
    return res.data.sessions.some((s) => s.id === sid);
  }, { what: "the session to come back" });
  assert.equal((await waitCommand(mock, await enqueue(mock, "info"))).status, "succeeded");
  await stop(worker);
});

test("a refused token stops the worker and says so", async () => {
  const { worker, events } = setup({ token: "wrong" });
  worker.start();
  assert.equal(await worker.finished.wait(10000), true);
  assert.equal(worker.state, "signed_out");
  assert.deepEqual(events.authFailed, ["NOLGIA did not accept your sign in. Sign in again."]);
  assert.equal((await mockState(mock)).deleted_sessions.length, 0);
});

test("stop waits for the running command, sends its result, then closes", async () => {
  const { worker } = setup({
    run: async () => {
      await new Promise((r) => setTimeout(r, 600));
      return { ok: true, result: { late: true }, error: null };
    },
  });
  worker.start();
  await connected(worker);
  const id = await enqueue(mock, "run", { code: "x" });
  await until(() => worker.busyWith === id, { what: "busy" });
  worker.stop();
  assert.equal(await worker.finished.wait(10000), true);
  const cmd = await waitCommand(mock, id);
  assert.deepEqual(cmd.result, { late: true });
  assert.equal((await mockState(mock)).deleted_sessions.length, 1);
});

test("switching off ends the long poll, so switching on again gets the next command", async () => {
  const first = setup();
  first.worker.start();
  await connected(first.worker);
  await new Promise((r) => setTimeout(r, 300)); // the poll is now waiting on the server
  await stop(first.worker);
  const second = setup();
  second.worker.start();
  await connected(second.worker);
  const cmd = await waitCommand(mock, await enqueue(mock, "info"));
  assert.equal(cmd.status, "succeeded", cmd.error);
  await stop(second.worker);
});
