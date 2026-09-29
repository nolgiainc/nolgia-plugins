// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Shared setup for the unit tests: the mock API (tools/mock_bridge_server.py,
// the same one the Blender tests use) and a stand-in for the Photoshop side.
"use strict";

const path = require("node:path");
const { spawn } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..");
const TOKEN = "unit-token";

/** Start the mock API on a free port. Resolves to {baseUrl, rootUrl, stop}. */
function startMock({ tokens = [TOKEN], deviceInterval = null, autoApproveAfter = null } = {}) {
  const args = [path.join(REPO, "tools", "mock_bridge_server.py"), "--port", "0"];
  for (const token of tokens) args.push("--token", token);
  if (deviceInterval !== null) args.push("--device-interval", String(deviceInterval));
  if (autoApproveAfter !== null) args.push("--auto-approve-after", String(autoApproveAfter));
  const proc = spawn(process.env.PYTHON || "python3", args, { stdio: ["ignore", "pipe", "inherit"] });
  return new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("the mock API did not start")), 15000);
    proc.on("error", reject);
    proc.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      const m = /listening on (http:\/\/[^\s]+)/.exec(buffer);
      if (m) {
        clearTimeout(timer);
        const baseUrl = m[1];
        resolve({
          baseUrl,
          rootUrl: baseUrl.replace(/\/v1$/, ""),
          stop: () =>
            new Promise((done) => {
              proc.once("exit", () => done());
              proc.kill();
            }),
        });
      }
    });
  });
}

async function call(mock, method, urlPath, body, { token = TOKEN, headers = {} } = {}) {
  const init = { method, headers: Object.assign({}, headers) };
  if (token) init.headers.Authorization = "Bearer " + token;
  if (body !== undefined && body !== null) {
    if (body instanceof Uint8Array) init.body = body;
    else {
      init.body = JSON.stringify(body);
      init.headers["Content-Type"] = "application/json";
    }
  }
  const resp = await fetch(mock.rootUrl + urlPath, init);
  const buf = new Uint8Array(await resp.arrayBuffer());
  const type = resp.headers.get("content-type") || "";
  let data = buf;
  if (type.includes("json")) data = buf.length ? JSON.parse(Buffer.from(buf).toString("utf8")) : null;
  return { status: resp.status, data };
}

async function enqueue(mock, kind, args = {}, { timeout = 120, caller = null } = {}) {
  const headers = caller === "agent" ? { "X-Nolgia-Surface": "hermes" } : {};
  const { status, data } = await call(mock, "POST", "/v1/bridge/commands", { app: "photoshop", kind, args, timeout_seconds: timeout }, { headers });
  if (status !== 201) throw new Error("enqueue " + kind + ": " + status + " " + JSON.stringify(data));
  return data.id;
}

async function waitCommand(mock, id, wait = 15) {
  const { status, data } = await call(mock, "GET", "/v1/bridge/commands/" + id + "?wait=" + wait);
  if (status !== 200) throw new Error("wait: " + status + " " + JSON.stringify(data));
  return data;
}

async function mockState(mock) {
  return (await call(mock, "GET", "/mock/state", null, { token: null })).data;
}

async function until(fn, { timeout = 10000, every = 50, what = "condition" } = {}) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > end) throw new Error("timed out waiting for " + what);
    await new Promise((r) => setTimeout(r, every));
  }
}

/** A stand-in for ps/host.js: memory storage and simple runners. */
function fakeHost(overrides = {}) {
  const host = {
    savedSettings: {},
    savedToken: null,
    dev: null,
    opened: [],
    copied: [],
    logs: [],
    approvalsSeen: [],
    fetch: (url, init) => fetch(url, init),
    log(message) {
      host.logs.push(message);
    },
    loadSettings() {
      return Object.assign({}, host.savedSettings);
    },
    saveSettings(settings) {
      host.savedSettings = Object.assign({}, settings);
    },
    async loadToken() {
      return host.savedToken ? Object.assign({}, host.savedToken) : null;
    },
    async saveToken(record) {
      host.savedToken = record ? Object.assign({}, record) : null;
    },
    async readDevFile() {
      return host.dev ? Object.assign({}, host.dev) : null;
    },
    async instanceId(override) {
      return override || "unit-instance";
    },
    machineName: () => "Unit Test PC",
    appVersion: () => "27.5.0",
    documentSnapshot: () => host.document || { name: "" },
    async openUrl(url) {
      host.opened.push(url);
    },
    async copyText(text) {
      host.copied.push(text);
    },
    runners: {
      info: async () => ({ app: "photoshop", documents: [] }),
      run: async (args) => ({ value: args.code.length, stdout: "", stderr: "" }),
      preview: async () => ({ width: 16, height: 8 }),
      import_asset: async (args, prepared) => ({ imported: [prepared && prepared.name] }),
      export: async (args) => ({ format: args.format }),
      save: async (args) => ({ path: args.path || "C:/x.psd" }),
      open: async (args) => ({ path: args.path }),
    },
    prepare: async () => null,
    finish: async (command, result) => result,
    approvalsChanged(approvals) {
      host.approvalsSeen.push(approvals.map((p) => p.id));
    },
    remembered: {},
    remember(key, value) {
      host.remembered[key] = value;
    },
    recall(key) {
      return host.remembered[key];
    },
  };
  return Object.assign(host, overrides);
}

module.exports = { REPO, TOKEN, startMock, call, enqueue, waitCommand, mockState, until, fakeHost };
