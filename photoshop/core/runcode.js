// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Run a piece of JavaScript the way the `run` command needs it.
//
// - The code is the body of an async function, so it can `await`.
// - `result` is the value handed back (made JSON safe); a top-level `return`
//   works too.
// - console.log/info/debug go to stdout and console.warn/error to stderr.
// - A failure returns the error with the code's own line numbers and lines.
// - The timeout is best effort: JavaScript cannot be stopped from outside,
//   so after the timeout NOLGIA stops waiting and says so. A loop that never
//   awaits blocks Photoshop until it ends.
"use strict";

const { cutText } = require("./util.js");

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const MAX_STREAM_CHARS = 256 * 1024;
const MAX_LIST_ITEMS = 10000;
const MAX_DEPTH = 32;
const FRAME_RE = /<anonymous>:(\d+):(\d+)/;

class Outcome {
  constructor({ ok, value = null, stdout = "", stderr = "", error = null, timedOut = false }) {
    this.ok = ok;
    this.value = value;
    this.stdout = stdout;
    this.stderr = stderr;
    this.error = error;
    this.timedOut = timedOut;
  }

  result() {
    return { value: this.value, stdout: this.stdout, stderr: this.stderr };
  }
}

// How many lines the Function constructor puts before the body.
let lineOffset = null;
function bodyLineOffset() {
  if (lineOffset !== null) return lineOffset;
  lineOffset = 2;
  try {
    new Function("a", "b", "throw new Error('probe')")();
  } catch (err) {
    const m = FRAME_RE.exec(String(err.stack || ""));
    if (m) lineOffset = Number(m[1]) - 1;
  }
  return lineOffset;
}

class Stream {
  constructor(limit) {
    this.limit = limit;
    this.parts = [];
    this.size = 0;
    this.dropped = 0;
  }

  write(text) {
    const room = this.limit - this.size;
    if (room > 0) {
      const piece = text.slice(0, room);
      this.parts.push(piece);
      this.size += piece.length;
    }
    this.dropped += Math.max(0, text.length - Math.max(room, 0));
  }

  value() {
    let text = this.parts.join("");
    if (this.dropped) text += "\n[" + this.dropped + " more characters cut]";
    return text;
  }
}

function show(value) {
  if (typeof value === "string") return value;
  if (value instanceof Error) return value.stack || value.name + ": " + value.message;
  if (value === undefined) return "undefined";
  if (typeof value === "function") return "[function " + (value.name || "anonymous") + "]";
  try {
    const json = JSON.stringify(toJsonable(value));
    return json === undefined ? String(value) : json;
  } catch (err) {
    return String(value);
  }
}

/** A console for the code: its output is captured, and still reaches the
 *  real console (UXP Developer Tool) when there is one. */
function captureConsole(real, out, err) {
  const line = (args) => args.map(show).join(" ") + "\n";
  const forward = (name, args) => {
    if (real && typeof real[name] === "function") {
      try {
        real[name].apply(real, args);
      } catch (e) {
        // ignore
      }
    }
  };
  const make = (stream, name) =>
    function (...args) {
      stream.write(line(args));
      forward(name, args);
    };
  return {
    log: make(out, "log"),
    info: make(out, "info"),
    debug: make(out, "debug"),
    dir: make(out, "log"),
    table: make(out, "log"),
    warn: make(err, "warn"),
    error: make(err, "error"),
    trace: make(err, "warn"),
    assert(cond, ...args) {
      if (!cond) err.write("Assertion failed" + (args.length ? ": " + line(args) : "\n"));
    },
  };
}

/** Turn what the code left in `result` into plain JSON data. Photoshop
 *  objects (documents, layers, ...) become {typename, id, name}; Bounds
 *  become {typename, left, top, right, bottom, width, height}. */
function toJsonable(value, depth = 0, seen = null) {
  if (depth > MAX_DEPTH) return "[nested too deep]";
  if (value === null || value === undefined) return null;
  const type = typeof value;
  if (type === "boolean" || type === "string") return value;
  if (type === "number") return Number.isFinite(value) ? value : String(value);
  if (type === "bigint") return value.toString();
  if (type === "function") return "[function " + (value.name || "anonymous") + "]";
  if (type === "symbol") return value.toString();
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value.toISOString();
  seen = seen || new Set();
  if (seen.has(value)) return "[circular]";
  seen.add(value);
  try {
    if (value instanceof Error) return { name: value.name, message: value.message };
    if (typeof value.typename === "string" && !Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype) {
      const out = { typename: value.typename };
      // Enough to act on: which object it is, and for Bounds the numbers.
      for (const key of ["id", "name", "left", "top", "right", "bottom", "width", "height"]) {
        try {
          const v = value[key];
          if (typeof v === "number" || typeof v === "string") out[key] = v;
        } catch (err) {
          // some Photoshop objects throw for missing properties
        }
      }
      return out;
    }
    if (Array.isArray(value) || ArrayBuffer.isView(value)) {
      return Array.from(value).slice(0, MAX_LIST_ITEMS).map((v) => toJsonable(v, depth + 1, seen));
    }
    if (value instanceof Map) {
      const out = {};
      for (const [k, v] of value) out[String(k)] = toJsonable(v, depth + 1, seen);
      return out;
    }
    if (value instanceof Set) return Array.from(value).slice(0, MAX_LIST_ITEMS).map((v) => toJsonable(v, depth + 1, seen));
    if (typeof value.toJSON === "function") {
      try {
        return toJsonable(value.toJSON(), depth + 1, seen);
      } catch (err) {
        // fall through
      }
    }
    const keys = Object.keys(value);
    if (!keys.length && Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) {
      return String(value).slice(0, 1000);
    }
    const out = {};
    for (const key of keys.slice(0, MAX_LIST_ITEMS)) {
      let v;
      try {
        v = value[key];
      } catch (err) {
        v = "[unreadable]";
      }
      if (v === undefined || typeof v === "function") continue;
      out[key] = toJsonable(v, depth + 1, seen);
    }
    return out;
  } finally {
    seen.delete(value);
  }
}

/** The error as the person wrote the code: frames in the code get their
 *  own line numbers and source lines; the plugin's frames are left out. */
function formatError(err, code) {
  if (typeof err === "string" && /^[A-Za-z]*Error\b/.test(err)) {
    // Photoshop's core.executeAsModal hands back errors as bare text.
    return err + "\n  (no line number: core.executeAsModal turned the error into text. Use modal(fn) instead, or let the code run as is: it already runs inside executeAsModal)";
  }
  if (!(err instanceof Error)) {
    if (err && typeof err === "object" && typeof err.message === "string") {
      return (err.name || "Error") + ": " + err.message;
    }
    return "The code threw a value that is not an Error: " + show(err);
  }
  const lines = code.split(/\r?\n/);
  const head = (err.name || "Error") + ": " + err.message;
  const offset = bodyLineOffset();
  const out = [head];
  for (const frame of String(err.stack || "").split("\n").slice(1)) {
    const m = FRAME_RE.exec(frame);
    if (!m) continue;
    const line = Number(m[1]) - offset;
    const column = Number(m[2]);
    if (line < 1 || line > lines.length) continue;
    out.push("  at line " + line + ", column " + column + ": " + lines[line - 1].trim());
  }
  if (out.length === 1 && err.name === "SyntaxError") {
    const line = syntaxErrorLine(lines, err.message);
    if (line) out.push("  at line " + line + ": " + lines[line - 1].trim());
    out.push("  (the code did not compile, so nothing ran)");
  }
  return out.join("\n");
}

/** The Function constructor gives no line for a syntax error. Find it: the
 *  first prefix of the code that fails with the same message ends there. */
function syntaxErrorLine(lines, message) {
  if (lines.length > 2000) return 0;
  for (let n = 1; n <= lines.length; n++) {
    try {
      new AsyncFunction(lines.slice(0, n).join("\n"));
    } catch (err) {
      if (err instanceof SyntaxError && err.message === message) return n;
    }
  }
  return 0;
}

/** Compile the code as the body of an async function whose parameters are
 *  the globals. When the code declares a name itself (`const { app } =
 *  require("photoshop")`, `const result = ...`), its own declaration wins:
 *  that global is left out and the code is compiled again. Returns the
 *  function and the parameter names it takes. */
function compile(code, names) {
  const body = code + "\n;return result;";
  let params = names.concat(["result"]);
  for (let tries = 0; tries <= params.length; tries++) {
    try {
      return { fn: new AsyncFunction(...params, body), params };
    } catch (err) {
      const m = err instanceof SyntaxError && /Identifier '([^']+)' has already been declared/.exec(err.message);
      if (!m || !params.includes(m[1])) throw err;
      params = params.filter((name) => name !== m[1]);
    }
  }
  throw new Error("could not compile the code");
}

/**
 * Run `code` with `globals` in scope. Resolves to an Outcome; never rejects.
 *
 * options.timeout: seconds to wait before giving up (best effort).
 * options.console: the real console to forward output to.
 * options.wrap: async (body) => body(context), for example a function that
 *   runs body inside Photoshop's executeAsModal; `context` is offered to the
 *   code as `executionContext`.
 */
async function runCode(code, globals = {}, { timeout = null, console: real = null, wrap = null } = {}) {
  const out = new Stream(MAX_STREAM_CHARS);
  const err = new Stream(MAX_STREAM_CHARS);
  const scope = Object.assign({}, globals, { console: captureConsole(real, out, err) });
  if (!("executionContext" in scope)) scope.executionContext = null;
  let compiled;
  try {
    compiled = compile(code, Object.keys(scope));
  } catch (e) {
    return new Outcome({ ok: false, error: formatError(e, code) });
  }
  const { fn, params } = compiled;
  const call = (context) => {
    const values = params.map((name) => {
      if (name === "result") return undefined;
      if (name === "executionContext" && context !== undefined) return context;
      return scope[name];
    });
    return fn(...values);
  };
  let timer = null;
  const running = (async () => (wrap ? wrap(call) : call(undefined)))();
  const racers = [running.then((value) => ({ value }), (error) => ({ error }))];
  if (timeout && timeout > 0) {
    racers.push(new Promise((resolve) => (timer = setTimeout(() => resolve({ timedOut: true }), timeout * 1000))));
  }
  const settled = await Promise.race(racers);
  if (timer !== null) clearTimeout(timer);
  if (settled.timedOut) {
    return new Outcome({
      ok: false,
      stdout: out.value(),
      stderr: err.value(),
      timedOut: true,
      error:
        "The code was still running after " + timeout + " seconds, so NOLGIA stopped waiting for it. " +
        "It may still finish in Photoshop. Split long work into smaller runs.",
    });
  }
  if ("error" in settled) {
    return new Outcome({ ok: false, stdout: out.value(), stderr: err.value(), error: cutText(formatError(settled.error, code), 64 * 1024) });
  }
  let value;
  try {
    value = toJsonable(settled.value);
  } catch (e) {
    value = String(settled.value);
  }
  return new Outcome({ ok: true, value, stdout: out.value(), stderr: err.value() });
}

module.exports = { runCode, toJsonable, formatError, Outcome, bodyLineOffset };
