// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { ApiClient, ApiError, NetworkError } = require("../core/api.js");
const { DeviceLogin, LoginError, LoginCancelled } = require("../core/auth.js");
const { startMock, call } = require("./support.js");

let mock;
test.before(async () => {
  mock = await startMock({ deviceInterval: 1 });
});
test.after(async () => {
  await mock.stop();
});

test("approve in the browser: a token comes back", async () => {
  const login = new DeviceLogin(new ApiClient({ baseUrl: mock.baseUrl }));
  const prompt = await login.start();
  assert.match(prompt.openUrl, /\/device\?user_code=/);
  assert.equal(prompt.interval, 1);
  setTimeout(() => call(mock, "POST", "/mock/device/approve", { user_code: prompt.userCode }, { token: null }), 1500);
  const token = await login.waitForToken(prompt);
  assert.match(token.accessToken, /^nol_mock_/);
  assert.ok(token.expiresAt > Date.now() / 1000 + 29 * 24 * 3600);
  const me = await new ApiClient({ baseUrl: mock.baseUrl, token: token.accessToken }).getMe();
  assert.equal(me.email, "test@nolgia.ai");
});

test("declined in the browser", async () => {
  const login = new DeviceLogin(new ApiClient({ baseUrl: mock.baseUrl }));
  const prompt = await login.start();
  setTimeout(() => call(mock, "POST", "/mock/device/deny", { user_code: prompt.userCode }, { token: null }), 1200);
  await assert.rejects(login.waitForToken(prompt), (err) => err instanceof LoginError && /declined/.test(err.message));
});

test("cancel stops the wait", async () => {
  const login = new DeviceLogin(new ApiClient({ baseUrl: mock.baseUrl }));
  const prompt = await login.start();
  setTimeout(() => login.cancel(), 200);
  await assert.rejects(login.waitForToken(prompt), (err) => err instanceof LoginCancelled);
});

test("slow_down adds 5 s; expired codes and network trouble", async () => {
  const answers = [
    new ApiError(400, { code: "slow_down" }),
    new NetworkError("offline"),
    new ApiError(400, { code: "expired_token" }),
  ];
  const fake = {
    startDeviceAuth: async () => ({ device_code: "d", user_code: "ABCD-EFGH", verification_uri: "https://nolgia.ai/device", expires_in: 900, interval: 1 }),
    pollDeviceToken: async () => {
      throw answers.shift();
    },
  };
  let now = 0;
  const login = new DeviceLogin(fake, { clock: () => now });
  login._sleep = async (s) => {
    now += s;
  };
  const prompt = await login.start();
  await assert.rejects(login.waitForToken(prompt), (err) => err instanceof LoginError && /expired/.test(err.message));
  assert.equal(login.interval, 6);
});

test("start fails clearly when NOLGIA cannot be reached", async () => {
  const login = new DeviceLogin(new ApiClient({ baseUrl: "http://127.0.0.1:9/v1", timeout: 2 }));
  await assert.rejects(login.start(), (err) => err instanceof LoginError && /Could not reach NOLGIA/.test(err.message));
});
