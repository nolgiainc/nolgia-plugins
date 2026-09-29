// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { ApiClient, ApiError, Unauthorized, NetworkError, normalizeBaseUrl } = require("../core/api.js");
const { TOKEN, startMock, call, mockState } = require("./support.js");

let mock;
test.before(async () => {
  mock = await startMock();
});
test.after(async () => {
  await mock.stop();
});

const payload = (extra = {}) =>
  Object.assign(
    { instance_id: "api-test", app: "photoshop", app_version: "27.5.0", plugin_version: "0.1.0", machine_name: "PC", document: { name: "" }, capabilities: ["info", "run"], allow_agent: true },
    extra,
  );

test("normalizeBaseUrl", () => {
  assert.equal(normalizeBaseUrl("https://api.nolgia.ai"), "https://api.nolgia.ai/v1");
  assert.equal(normalizeBaseUrl("https://api.nolgia.ai/v1/"), "https://api.nolgia.ai/v1");
  assert.equal(normalizeBaseUrl(""), "https://api.nolgia.ai/v1");
});

test("register, long poll (204), command, result, delete", async () => {
  const api = new ApiClient({ baseUrl: mock.baseUrl, token: TOKEN });
  const session = await api.registerSession(payload());
  assert.ok(session.id);
  assert.equal(session.poll_wait_seconds, 25);
  assert.equal(await api.nextCommand(session.id, 0), null, "204 means nothing to do");
  const queued = await call(mock, "POST", "/v1/bridge/commands", { app: "photoshop", kind: "info", args: {} });
  assert.equal(queued.status, 201);
  const cmd = await api.nextCommand(session.id, 1);
  assert.equal(cmd.id, queued.data.id);
  assert.equal(cmd.status, "running");
  await api.postResult(cmd.id, { status: "succeeded", result: { ok: 1 } });
  const done = await call(mock, "GET", "/v1/bridge/commands/" + cmd.id);
  assert.deepEqual(done.data.result, { ok: 1 });
  await assert.rejects(api.postResult(cmd.id, { status: "succeeded" }), (err) => err instanceof ApiError && err.status === 409 && err.code === "command_not_running");
  await api.deleteSession(session.id);
  await assert.rejects(api.nextCommand(session.id, 0), (err) => err.status === 409 && err.code === "session_disconnected");
});

test("errors: 401 is Unauthorized, 400 carries the detail, no server is a NetworkError", async () => {
  const bad = new ApiClient({ baseUrl: mock.baseUrl, token: "nope" });
  await assert.rejects(bad.registerSession(payload()), (err) => err instanceof Unauthorized && err.status === 401);
  const api = new ApiClient({ baseUrl: mock.baseUrl, token: TOKEN });
  await assert.rejects(api.registerSession(payload({ app: "gimp" })), (err) => err instanceof ApiError && /app must be one of/.test(err.detail));
  const gone = new ApiClient({ baseUrl: "http://127.0.0.1:9/v1", token: TOKEN, timeout: 3 });
  await assert.rejects(gone.getMe(), (err) => err instanceof NetworkError);
});

test("a request that takes too long is a NetworkError", async () => {
  const slow = (url, init) =>
    new Promise((resolve, reject) => {
      if (init.signal) init.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
    });
  const api = new ApiClient({ baseUrl: mock.baseUrl, token: TOKEN, fetch: slow, timeout: 0.2 });
  await assert.rejects(api.getMe(), (err) => err instanceof NetworkError && /timed out/.test(err.message));
});

test("empty bodies are never read (UXP's fetch throws 'Already read' for them)", async () => {
  const strict = async (url, init) => {
    const resp = await fetch(url, init);
    return {
      status: resp.status,
      headers: resp.headers,
      arrayBuffer: async () => {
        const buf = await resp.arrayBuffer();
        if (!buf.byteLength) throw new TypeError("Already read");
        return buf;
      },
    };
  };
  const api = new ApiClient({ baseUrl: mock.baseUrl, token: TOKEN, fetch: strict });
  const session = await api.registerSession(payload({ instance_id: "empty-bodies" }));
  assert.equal(await api.nextCommand(session.id, 0), null);
  const asset = await api.uploadBytes(new Uint8Array([1, 2, 3]), "image/png", { filename: "x.png" });
  assert.equal(asset.status, "ready");
  await api.deleteSession(session.id);
});

test("upload and download go to storage without the token", async () => {
  const api = new ApiClient({ baseUrl: mock.baseUrl, token: TOKEN });
  const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);
  const asset = await api.uploadBytes(bytes, "image/png", { filename: "p.png", displayName: "Preview", tags: ["photoshop"] });
  assert.equal(asset.display_name, "Preview");
  assert.deepEqual(asset.tags, ["photoshop"]);
  const again = await api.getAsset(asset.id);
  const back = await api.download(again.signed_url);
  assert.deepEqual(Array.from(back), Array.from(bytes));
  await assert.rejects(api.download(again.signed_url, 3), (err) => err.status === 413);
  const storage = (await mockState(mock)).requests.filter((r) => r.path.startsWith("/storage/"));
  assert.ok(storage.length >= 2);
  assert.ok(storage.every((r) => !r.auth), "the token went to a signed URL");
});

test("device login endpoints", async () => {
  const api = new ApiClient({ baseUrl: mock.baseUrl });
  const start = await api.startDeviceAuth("nolgia-photoshop", "bridge");
  assert.match(start.user_code, /^[A-Z]{4}-[A-Z]{4}$/);
  await assert.rejects(api.pollDeviceToken("nolgia-photoshop", start.device_code), (err) => err.code === "authorization_pending");
});
