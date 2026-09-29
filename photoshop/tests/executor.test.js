// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { Executor, ApprovalRequest } = require("../core/executor.js");

function command(id, seconds = 60) {
  const now = Date.now() / 1000;
  return { id, kind: "run", args: {}, deadline: now + seconds };
}

function make(ask = false) {
  const statuses = [];
  const ran = [];
  const exec = new Executor({
    run: async (cmd) => {
      ran.push(cmd.id);
      await new Promise((r) => setTimeout(r, 20));
      return { ok: true, result: { id: cmd.id }, error: null };
    },
    needsApproval: () => (ask ? new ApprovalRequest("Run?", ["x"], { approveLabel: "Run code", code: "x" }) : null),
    onStatus: (id, status) => statuses.push([id, status]),
  });
  return { exec, statuses, ran };
}

test("commands run one at a time, in order", async () => {
  const { exec, ran } = make();
  const results = await Promise.all([exec.submit(command("a")), exec.submit(command("b")), exec.submit(command("c"))]);
  assert.deepEqual(ran, ["a", "b", "c"]);
  assert.deepEqual(results.map((r) => r.result.id), ["a", "b", "c"]);
});

test("approve, deny, time out", async () => {
  const { exec, statuses } = make(true);
  const first = exec.submit(command("a"));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(exec.approvals.length, 1);
  assert.equal(exec.approvals[0].request.approveLabel, "Run code");
  exec.approve("a");
  assert.equal((await first).ok, true);
  assert.deepEqual(statuses.slice(0, 2), [["a", "approval"], ["a", "running"]]);

  const second = exec.submit(command("b"));
  await new Promise((r) => setTimeout(r, 10));
  exec.deny("b");
  const denied = await second;
  assert.equal(denied.ok, false);
  assert.match(denied.error, /clicked Deny/);

  const late = await exec.submit(command("c", 0.2));
  assert.match(late.error, /Nobody approved this in Photoshop in time/);
  assert.equal(exec.approvals.length, 0);
  assert.equal(exec.approve("zzz"), false);
});

test("close fails waiting and queued commands with the reason", async () => {
  const { exec, ran } = make(true);
  const waiting = exec.submit(command("a"));
  const queued = exec.submit(command("b"));
  await new Promise((r) => setTimeout(r, 10));
  exec.close("switched off");
  assert.deepEqual([(await waiting).error, (await queued).error], ["switched off", "switched off"]);
  assert.deepEqual(ran, []);
  exec.reopen();
  const again = exec.submit(command("c"));
  await new Promise((r) => setTimeout(r, 10));
  exec.approve("c");
  assert.equal((await again).ok, true);
});

test("a runner that throws is reported, not fatal", async () => {
  const exec = new Executor({ run: async () => { throw new Error("kaput"); } });
  const out = await exec.submit(command("a"));
  assert.equal(out.ok, false);
  assert.match(out.error, /kaput/);
  const nobody = new Executor({ run: async () => ({ ok: true }), needsApproval: () => new ApprovalRequest("t", [], { headlessError: "no one here" }), canAsk: () => false });
  assert.equal((await nobody.submit(command("b"))).error, "no one here");
});
