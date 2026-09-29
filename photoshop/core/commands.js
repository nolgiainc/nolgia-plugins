// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Command kinds, argument checks, result shaping and the activity list.
// The wire format is the one in nolgia-api docs/design/bridge.md.
"use strict";

const { parseTime, cutText, goJsonSize } = require("./util.js");

// Every kind this plugin implements, in the order the spec lists them. The
// plugin advertises exactly these in `capabilities`.
const CAPABILITIES = ["info", "run", "preview", "import_asset", "export", "save", "open"];

const EXPORT_FORMATS = ["png", "jpg", "psd"];
const IMPORT_AS = ["layer", "pixels", "document"];
const RUN_LANGUAGES = ["uxp", "javascript", "js"];

const DEFAULT_TIMEOUT = 120;
// The API takes at most 1 MB (2**20 bytes) of result JSON, measured after it
// re-encodes the result. Stay a little under so a result never lands at 413.
const MAX_RESULT_BYTES = 1000 * 1000;
const MAX_ERROR_CHARS = 64 * 1024;
// The API refuses a result that arrives at or after expires_at, so the
// plugin stops waiting (for approval, or for code to finish) a little before.
const RESULT_MARGIN = 3;

/** A command failed. The message is shown to the agent and the person. */
class CommandError extends Error {
  constructor(message, result) {
    super(message);
    this.name = "CommandError";
    this.result = result === undefined ? null : result;
  }
}

function nowSeconds() {
  return Date.now() / 1000;
}

class Command {
  constructor(data, receivedAt, clock = nowSeconds) {
    this.raw = data;
    this.id = String(data.id || "");
    this.kind = String(data.kind || "");
    this.args = data.args && typeof data.args === "object" && !Array.isArray(data.args) ? data.args : {};
    this.caller = String(data.caller || "");
    this.clock = clock;
    this.receivedAt = receivedAt === undefined ? clock() : receivedAt;
    this.timeout = Command.timeoutOf(data);
  }

  /** Seconds this command may take, measured from when we got it. Uses the
   *  server's own clock (expires_at minus claimed_at or created_at) so a
   *  skewed clock on this computer does not matter. */
  static timeoutOf(data) {
    const expires = parseTime(data.expires_at);
    const start = parseTime(data.claimed_at) || parseTime(data.created_at);
    if (expires && start && expires > start) return (expires - start) / 1000;
    const seconds = data.timeout_seconds;
    if (typeof seconds === "number" && seconds > 0) return seconds;
    return DEFAULT_TIMEOUT;
  }

  /** When the API stops accepting a result, on our clock. */
  get expires() {
    return this.receivedAt + this.timeout;
  }

  /** When to give up so the failure still reaches the API in time. */
  get deadline() {
    return this.expires - Math.min(RESULT_MARGIN, this.timeout * 0.25);
  }

  remaining(now) {
    return this.deadline - (now === undefined ? this.clock() : now);
  }

  get callerLabel() {
    return { agent: "NOLGIA Agent", user: "Your agent" }[this.caller] || this.caller || "NOLGIA";
  }
}

function intArg(args, key, { low, high, required = false } = {}) {
  const value = args[key];
  if (value === undefined || value === null) {
    if (required) throw new CommandError("`" + key + "` is required.");
    return null;
  }
  if (typeof value !== "number" || !Number.isInteger(value)) throw new CommandError("`" + key + "` must be a whole number.");
  if (low !== undefined && value < low) throw new CommandError("`" + key + "` must be at least " + low + ".");
  if (high !== undefined && value > high) throw new CommandError("`" + key + "` must be at most " + high + ".");
  return value;
}

function strArg(args, key, { required = false, choices } = {}) {
  const value = args[key];
  if (value === undefined || value === null || value === "") {
    if (required) throw new CommandError("`" + key + "` is required.");
    return null;
  }
  if (typeof value !== "string") throw new CommandError("`" + key + "` must be text.");
  if (choices && !choices.includes(value)) throw new CommandError("`" + key + "` must be one of: " + choices.join(", ") + ".");
  return value;
}

/** `document`: an open document's name, or its id as a number. */
function documentArg(args) {
  const value = args.document;
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value === "string") return value;
  throw new CommandError("`document` must be an open document's name or id.");
}

/** `region`: {left, top, right, bottom} or [left, top, right, bottom], in
 *  document pixels. */
function regionArg(args) {
  const value = args.region;
  if (value === undefined || value === null) return null;
  let box = null;
  if (Array.isArray(value) && value.length === 4) box = { left: value[0], top: value[1], right: value[2], bottom: value[3] };
  else if (value && typeof value === "object") box = { left: value.left, top: value.top, right: value.right, bottom: value.bottom };
  const bad = "`region` must be {left, top, right, bottom} in pixels, with right > left and bottom > top.";
  if (!box) throw new CommandError(bad);
  for (const key of ["left", "top", "right", "bottom"]) {
    if (typeof box[key] !== "number" || !Number.isFinite(box[key]) || box[key] < 0) throw new CommandError(bad);
    box[key] = Math.round(box[key]);
  }
  if (box.right <= box.left || box.bottom <= box.top) throw new CommandError(bad);
  return box;
}

/** Check and normalise a command's args. Throws CommandError. */
function validate(kind, args) {
  if (!CAPABILITIES.includes(kind)) {
    throw new CommandError(
      'This NOLGIA plugin for Photoshop cannot do "' + kind + '". It can do: ' + CAPABILITIES.join(", ") +
        ". Updating the plugin may add it.",
    );
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) throw new CommandError("`args` must be an object.");
  const out = {};
  if (kind === "run") {
    const language = (strArg(args, "language") || "uxp").toLowerCase();
    if (!RUN_LANGUAGES.includes(language)) {
      throw new CommandError("Photoshop runs UXP JavaScript only (language \"uxp\"); this command asked for " + language + ".");
    }
    const code = args.code;
    if (typeof code !== "string" || !code.trim()) throw new CommandError("`code` is required: the UXP JavaScript to run in Photoshop.");
    out.language = "uxp";
    out.code = code;
    const timeout = args.timeout_seconds;
    if (timeout !== undefined && timeout !== null) {
      if (typeof timeout !== "number" || !(timeout > 0)) throw new CommandError("`timeout_seconds` must be a positive number.");
      out.timeout_seconds = timeout;
    }
  } else if (kind === "preview") {
    out.width = intArg(args, "width", { low: 16, high: 1920 });
    out.region = regionArg(args);
    out.document = documentArg(args);
  } else if (kind === "import_asset") {
    out.asset_id = strArg(args, "asset_id", { required: true });
    out.as = strArg(args, "as", { choices: IMPORT_AS });
    out.name = strArg(args, "name");
  } else if (kind === "export") {
    let format = (strArg(args, "format", { required: true }) || "").toLowerCase();
    if (format === "jpeg") format = "jpg";
    if (!EXPORT_FORMATS.includes(format)) throw new CommandError("`format` must be one of: " + EXPORT_FORMATS.join(", ") + ".");
    out.format = format;
    out.filename = strArg(args, "filename");
    out.document = documentArg(args);
    out.quality = intArg(args, "quality", { low: 1, high: 12 });
  } else if (kind === "save") {
    out.path = strArg(args, "path");
    out.document = documentArg(args);
  } else if (kind === "open") {
    out.path = strArg(args, "path", { required: true });
  }
  return out;
}

function size(body) {
  return goJsonSize(body);
}

/** The body for POST /bridge/commands/{id}/result, kept under 1 MB. */
function resultBody(ok, result, error) {
  let body;
  if (ok) {
    body = { status: "succeeded", result: result === undefined || result === null ? {} : result };
  } else {
    body = { status: "failed", error: cutText(error || "The command failed.", MAX_ERROR_CHARS) };
    if (result !== undefined && result !== null) body.result = result;
  }
  if (size(body) <= MAX_RESULT_BYTES) return body;
  // Too big: shorten captured output first, then give up on the value.
  const res = body.result;
  if (res && typeof res === "object" && !Array.isArray(res)) {
    const copy = Object.assign({}, res);
    for (const key of ["stdout", "stderr"]) {
      if (typeof copy[key] === "string") copy[key] = cutText(copy[key], 16 * 1024);
    }
    body = Object.assign({}, body, { result: copy });
    if (size(body) <= MAX_RESULT_BYTES) return body;
  }
  return {
    status: "failed",
    error: "The result is larger than 1 MB, the most NOLGIA accepts. Return less data, or write it to a file and export that.",
  };
}

/** The last 20 commands for the panel. */
class ActivityLog {
  constructor(limit = 20, clock = nowSeconds) {
    this.items_ = [];
    this.limit = limit;
    this.clock = clock;
    this.version = 0;
    this.listeners = [];
  }

  _changed() {
    this.version += 1;
    for (const fn of this.listeners) {
      try {
        fn();
      } catch (err) {
        // a broken listener must not stop the log
      }
    }
  }

  onChange(fn) {
    this.listeners.push(fn);
  }

  add(commandId, kind, caller = "") {
    this.items_ = this.items_.filter((item) => item.id !== commandId);
    this.items_.push({ id: commandId, kind, caller, status: "received", time: this.clock(), detail: "" });
    while (this.items_.length > this.limit) this.items_.shift();
    this._changed();
  }

  update(commandId, status, detail = "") {
    const item = this.items_.find((i) => i.id === commandId);
    if (!item) return;
    item.status = status;
    if (detail) {
      const lines = String(detail).trim().split(/\r?\n/);
      item.detail = lines[lines.length - 1].slice(0, 200);
    }
    this._changed();
  }

  /** Newest first. */
  items() {
    return this.items_.slice().reverse().map((item) => Object.assign({}, item));
  }

  clear() {
    this.items_ = [];
    this._changed();
  }
}

ActivityLog.STATUS_LABELS = {
  received: "Received",
  running: "Running",
  approval: "Waiting for you",
  succeeded: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  expired: "Expired",
};

module.exports = {
  CAPABILITIES,
  EXPORT_FORMATS,
  IMPORT_AS,
  DEFAULT_TIMEOUT,
  MAX_RESULT_BYTES,
  MAX_ERROR_CHARS,
  RESULT_MARGIN,
  CommandError,
  Command,
  validate,
  resultBody,
  ActivityLog,
};
