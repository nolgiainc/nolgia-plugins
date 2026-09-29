// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { CAPABILITIES, Command, CommandError, validate, resultBody, ActivityLog, MAX_RESULT_BYTES } = require("../core/commands.js");
const { goJsonSize } = require("../core/util.js");

test("the plugin advertises the bridge's command kinds", () => {
  assert.deepEqual(CAPABILITIES, ["info", "run", "preview", "import_asset", "export", "save", "open"]);
});

test("run takes UXP JavaScript only", () => {
  assert.deepEqual(validate("run", { code: "result = 1" }), { language: "uxp", code: "result = 1" });
  assert.deepEqual(validate("run", { language: "javascript", code: "x", timeout_seconds: 5 }), { language: "uxp", code: "x", timeout_seconds: 5 });
  assert.throws(() => validate("run", { language: "python", code: "x" }), /UXP JavaScript only/);
  assert.throws(() => validate("run", { code: "  " }), /`code` is required/);
  assert.throws(() => validate("run", { code: "x", timeout_seconds: -1 }), /positive number/);
});

test("preview checks width and region", () => {
  assert.deepEqual(validate("preview", {}), { width: null, region: null, document: null });
  assert.deepEqual(validate("preview", { width: 320, region: [1, 2, 30.4, 40], camera: "ignored", frame: 3 }), {
    width: 320,
    region: { left: 1, top: 2, right: 30, bottom: 40 },
    document: null,
  });
  assert.throws(() => validate("preview", { width: 5000 }), /at most 1920/);
  assert.throws(() => validate("preview", { width: 12.5 }), /whole number/);
  assert.throws(() => validate("preview", { region: { left: 10, top: 0, right: 5, bottom: 5 } }), /right > left/);
  assert.equal(validate("preview", { document: "street.psd" }).document, "street.psd");
  assert.throws(() => validate("preview", { document: { id: 1 } }), /document/);
});

test("export, import_asset, save and open args", () => {
  assert.equal(validate("export", { format: "JPEG" }).format, "jpg");
  assert.throws(() => validate("export", { format: "mp4" }), /png, jpg, psd/);
  assert.throws(() => validate("export", {}), /`format` is required/);
  assert.throws(() => validate("export", { format: "jpg", quality: 13 }), /at most 12/);
  assert.deepEqual(validate("import_asset", { asset_id: "a", as: "pixels" }), { asset_id: "a", as: "pixels", name: null });
  assert.throws(() => validate("import_asset", { asset_id: "a", as: "plane" }), /layer, pixels, document/);
  assert.throws(() => validate("import_asset", {}), /`asset_id` is required/);
  assert.deepEqual(validate("save", {}), { path: null, document: null });
  assert.throws(() => validate("open", {}), /`path` is required/);
  assert.throws(() => validate("render", {}), /cannot do "render"/);
  assert.throws(() => validate("info", []), /must be an object/);
});

test("Command times itself by the server's clock", () => {
  const cmd = new Command(
    { id: "c1", kind: "run", args: { code: "x" }, caller: "agent", created_at: "2026-09-29T20:00:00Z", claimed_at: "2026-09-29T20:00:05Z", expires_at: "2026-09-29T20:02:05Z" },
    1000,
  );
  assert.equal(cmd.timeout, 120);
  assert.equal(cmd.expires, 1120);
  assert.equal(cmd.deadline, 1117);
  assert.equal(cmd.remaining(1100), 17);
  assert.equal(cmd.callerLabel, "NOLGIA Agent");
  assert.equal(new Command({ id: "x", caller: "user" }).callerLabel, "Your agent");
  assert.equal(new Command({ id: "x", timeout_seconds: 9 }).timeout, 9);
  assert.equal(new Command({ id: "x" }).timeout, 120);
});

test("resultBody keeps results under 1 MB", () => {
  assert.deepEqual(resultBody(true, null), { status: "succeeded", result: {} });
  assert.deepEqual(resultBody(false, null, "boom"), { status: "failed", error: "boom" });
  assert.deepEqual(resultBody(false, { value: null }, ""), { status: "failed", error: "The command failed.", result: { value: null } });
  const big = "x".repeat(900 * 1000);
  const trimmed = resultBody(true, { value: null, stdout: big, stderr: big });
  assert.equal(trimmed.status, "succeeded");
  assert.ok(trimmed.result.stdout.length < 20 * 1024);
  assert.ok(goJsonSize(trimmed) <= MAX_RESULT_BYTES);
  const escaped = resultBody(true, { value: "<".repeat(200 * 1000) });
  assert.equal(escaped.status, "failed", "Go's escaping of < makes this over 1 MB");
  assert.match(escaped.error, /larger than 1 MB/);
});

test("ActivityLog keeps the last 20, newest first", () => {
  let t = 0;
  const log = new ActivityLog(20, () => ++t);
  let changes = 0;
  log.onChange(() => changes++);
  for (let i = 0; i < 25; i++) log.add("c" + i, "info");
  log.update("c24", "failed", "line one\nline two");
  log.update("missing", "succeeded");
  const items = log.items();
  assert.equal(items.length, 20);
  assert.equal(items[0].id, "c24");
  assert.equal(items[0].detail, "line two");
  assert.equal(items[19].id, "c5");
  assert.equal(changes, 26);
  assert.equal(ActivityLog.STATUS_LABELS.approval, "Waiting for you");
});

test("CommandError carries a result", () => {
  const err = new CommandError("nope", { value: 1 });
  assert.equal(err.name, "CommandError");
  assert.deepEqual(err.result, { value: 1 });
});
