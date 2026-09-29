// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { runCode, toJsonable, formatError } = require("../core/runcode.js");

test("result, return and a code's own `const result`", async () => {
  assert.equal((await runCode("result = 1 + 1")).value, 2);
  assert.equal((await runCode("return 'early';\nresult = 'late'")).value, "early");
  assert.equal((await runCode("const result = { a: 1 };")).value.a, 1);
  assert.equal((await runCode("let x = 1")).value, null);
  assert.equal((await runCode("result = await Promise.resolve(5)")).value, 5);
});

test("globals are in scope and console output is captured", async () => {
  const real = [];
  const out = await runCode("console.log('a', 1, {b: 2});\nconsole.warn('w');\nconsole.error(new Error('e'));\nresult = app.name", {
    app: { name: "Photoshop" },
  }, { console: { log: (...a) => real.push(a), warn: () => {}, error: () => {} } });
  assert.equal(out.ok, true);
  assert.equal(out.value, "Photoshop");
  assert.equal(out.stdout, 'a 1 {"b":2}\n');
  assert.match(out.stderr, /^w\nError: e/);
  assert.equal(real.length, 1, "output still reaches the real console");
});

test("errors name the code's own line and source", async () => {
  const out = await runCode("const a = 1;\nfunction f() { return missing.x; }\nf();");
  assert.equal(out.ok, false);
  assert.equal(out.error, "ReferenceError: missing is not defined\n  at line 2, column 16: function f() { return missing.x; }\n  at line 3, column 1: f();");
  assert.doesNotMatch(out.error, /runcode\.js/);
  const syntax = await runCode("let a = 1;\nlet b = ;");
  assert.match(syntax.error, /^SyntaxError: .*\n  at line 2: let b = ;\n  \(the code did not compile/);
});

test("errors turned into text by executeAsModal are explained", async () => {
  const out = await runCode("throw 'Error: x'");
  assert.match(out.error, /^Error: x\n  \(no line number: core\.executeAsModal turned the error into text/);
  assert.equal(formatError({ name: "TypeError", message: "m" }, ""), "TypeError: m");
  assert.equal(formatError(42, ""), "The code threw a value that is not an Error: 42");
});

test("the timeout stops waiting and says so", async () => {
  const t0 = Date.now();
  const out = await runCode("await new Promise((r) => setTimeout(r, 2000)); result = 1", {}, { timeout: 0.2 });
  assert.equal(out.ok, false);
  assert.equal(out.timedOut, true);
  assert.match(out.error, /still running after 0\.2 seconds/);
  assert.ok(Date.now() - t0 < 1500);
});

test("wrap runs the code inside a scope that gets a context", async () => {
  let wrapped = 0;
  const wrap = async (body) => {
    wrapped += 1;
    return body({ hostControl: "ctx" });
  };
  const out = await runCode("result = executionContext.hostControl", {}, { wrap });
  assert.equal(out.value, "ctx");
  assert.equal(wrapped, 1);
  assert.equal((await runCode("result = executionContext")).value, null);
});

test("toJsonable makes Photoshop objects and odd values plain data", () => {
  class Layer {
    get typename() {
      return "Layer";
    }
    get id() {
      return 7;
    }
    get name() {
      return "Sky";
    }
  }
  const cyclic = { a: 1 };
  cyclic.self = cyclic;
  assert.deepEqual(toJsonable({ layer: new Layer(), n: NaN, big: 10n, d: new Date(0), s: new Set([1]), m: new Map([["k", 2]]), f() {}, u: undefined }), {
    layer: { typename: "Layer", id: 7, name: "Sky" },
    n: "NaN",
    big: "10",
    d: "1970-01-01T00:00:00.000Z",
    s: [1],
    m: { k: 2 },
  });
  assert.deepEqual(toJsonable(cyclic), { a: 1, self: "[circular]" });
  class Bounds {
    get typename() {
      return "Bounds";
    }
    get left() {
      return 1;
    }
    get top() {
      return 2;
    }
    get right() {
      return 11;
    }
    get bottom() {
      return 22;
    }
    get width() {
      return 10;
    }
    get height() {
      return 20;
    }
  }
  assert.deepEqual(toJsonable(new Bounds()), { typename: "Bounds", left: 1, top: 2, right: 11, bottom: 22, width: 10, height: 20 });
  assert.deepEqual(toJsonable(new Uint8Array([1, 2])), [1, 2]);
  assert.deepEqual(toJsonable(new Error("x")), { name: "Error", message: "x" });
});

test("the code's own declarations win over the globals it is given", async () => {
  const out = await runCode("const { app, core } = require('photoshop');\nresult = [app, core];", {
    app: "given",
    core: "given",
    require: () => ({ app: "own", core: "own" }),
  });
  assert.equal(out.ok, true, out.error);
  assert.deepEqual(out.value, ["own", "own"]);
  assert.match((await runCode("let x = 1;\nlet x = 2;")).error, /Identifier 'x' has already been declared\n  at line 2/);
});
