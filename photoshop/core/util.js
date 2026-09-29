// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Small helpers with no Photoshop dependency.
"use strict";

const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:?\d{2})?$/;

/** Parse an RFC 3339 timestamp (Go sends up to 9 fraction digits).
 *  Returns milliseconds since the epoch, or null when missing or odd. */
function parseTime(value) {
  if (typeof value !== "string") return null;
  const m = ISO_RE.exec(value.trim());
  if (!m) return null;
  const [, y, mo, d, h, mi, s, frac, zone] = m;
  const ms = Number(((frac || "0") + "000").slice(0, 3));
  let t = Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s), ms);
  if (Number.isNaN(t)) return null;
  if (zone && zone !== "Z" && zone !== "z") {
    const sign = zone[0] === "+" ? 1 : -1;
    const digits = zone.slice(1).replace(":", "");
    t -= sign * (Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2))) * 60000;
  }
  return t;
}

/** Exponential backoff with jitter: base, 2*base, 4*base ... up to cap
 *  (seconds). Each delay is drawn from [delay/2, delay] so plugins that lost
 *  the network together do not all come back in the same second. */
class Backoff {
  constructor(base = 1, cap = 60, rng = Math.random) {
    this.base = base;
    this.cap = cap;
    this.attempt = 0;
    this.rng = rng;
  }

  next() {
    const delay = Math.min(this.cap, this.base * 2 ** this.attempt);
    this.attempt += 1;
    return delay / 2 + this.rng() * (delay / 2);
  }

  reset() {
    this.attempt = 0;
  }
}

/** Keep at most `limit` characters, saying how much was cut. */
function cutText(text, limit, keep = "tail") {
  if (text === null || text === undefined) return "";
  text = String(text);
  if (text.length <= limit) return text;
  const dropped = text.length - limit;
  if (keep === "head") return text.slice(0, limit) + "\n[" + dropped + " more characters cut]";
  return "[" + dropped + " characters cut]\n" + text.slice(text.length - limit);
}

/** Bytes of a string as UTF-8. */
function utf8Length(text) {
  let n = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length) {
      const next = text.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        n += 4;
        i += 1;
      } else n += 3;
    } else n += 3;
  }
  return n;
}

/** len(json.Marshal(value)) in Go: compact UTF-8, with <, > and & written
 *  as < and friends (five bytes more each). The API measures results
 *  this way. */
function goJsonSize(value) {
  const text = JSON.stringify(value);
  if (text === undefined) return 4;
  let extra = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "<" || c === ">" || c === "&") extra += 5;
  }
  return utf8Length(text) + extra;
}

// File types NOLGIA accepts through POST /assets/uploads that Photoshop makes.
const UPLOAD_TYPES = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
};

// What an asset's MIME type means as a file on disk, for import.
const MIME_EXTENSIONS = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/tiff": ".tif",
  "image/bmp": ".bmp",
  "image/vnd.adobe.photoshop": ".psd",
  "image/x-photoshop": ".psd",
  "video/mp4": ".mp4",
  "video/quicktime": ".mov",
  "video/webm": ".webm",
  "audio/mpeg": ".mp3",
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "audio/ogg": ".ogg",
  "audio/mp4": ".m4a",
  "model/gltf-binary": ".glb",
};

// What Photoshop can bring in, by extension. Anything else is refused with
// a clear message.
const IMPORT_KINDS = {
  ".png": "image",
  ".jpg": "image",
  ".jpeg": "image",
  ".webp": "image",
  ".gif": "image",
  ".tif": "image",
  ".tiff": "image",
  ".bmp": "image",
  ".psd": "image",
  ".psb": "image",
};

const OTHER_KINDS = {
  ".mp4": "video",
  ".mov": "video",
  ".webm": "video",
  ".mp3": "audio",
  ".wav": "audio",
  ".ogg": "audio",
  ".m4a": "audio",
  ".glb": "model",
  ".gltf": "model",
  ".fbx": "model",
  ".obj": "model",
};

function startsWith(bytes, prefix) {
  if (!bytes || bytes.length < prefix.length) return false;
  for (let i = 0; i < prefix.length; i++) if (bytes[i] !== prefix[i]) return false;
  return true;
}

function ascii(bytes, start, end) {
  let out = "";
  for (let i = start; i < Math.min(end, bytes.length); i++) out += String.fromCharCode(bytes[i]);
  return out;
}

/** Guess a file extension from its first bytes (a Uint8Array). */
function sniffExtension(head) {
  if (!head || !head.length) return "";
  if (startsWith(head, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return ".png";
  if (startsWith(head, [0xff, 0xd8, 0xff])) return ".jpg";
  if (ascii(head, 0, 4) === "RIFF" && ascii(head, 8, 12) === "WEBP") return ".webp";
  if (ascii(head, 0, 4) === "8BPS") return ".psd";
  if (ascii(head, 0, 4) === "GIF8") return ".gif";
  if (ascii(head, 0, 2) === "BM") return ".bmp";
  if (ascii(head, 0, 4) === "II*\u0000" || ascii(head, 0, 4) === "MM\u0000*") return ".tif";
  if (ascii(head, 0, 4) === "glTF") return ".glb";
  if (ascii(head, 4, 8) === "ftyp") return ".mp4";
  if (ascii(head, 0, 4) === "RIFF" && ascii(head, 8, 12) === "WAVE") return ".wav";
  if (ascii(head, 0, 3) === "ID3") return ".mp3";
  return "";
}

function extname(name) {
  const base = String(name || "").replace(/\\/g, "/").split("/").pop();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

function stem(name) {
  const base = String(name || "").replace(/\\/g, "/").split("/").pop();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

/** Pick the extension to save a downloaded asset under. The file's first
 *  bytes win (Photoshop places by extension, so a PNG named .jpg would
 *  fail), then the name's own extension, then the MIME type. Returns ""
 *  when Photoshop cannot open it. */
function importExtension(displayName, mimeType, head) {
  const sniffed = sniffExtension(head);
  if (IMPORT_KINDS[sniffed]) return sniffed;
  if (sniffed) return "";
  const ext = extname(displayName);
  if (IMPORT_KINDS[ext]) return ext;
  const byMime = MIME_EXTENSIONS[String(mimeType || "").split(";")[0].trim().toLowerCase()] || "";
  return IMPORT_KINDS[byMime] ? byMime : "";
}

/** What kind of file an asset is when Photoshop cannot open it. */
function otherKind(displayName, mimeType) {
  const byName = OTHER_KINDS[extname(displayName)];
  if (byName) return byName;
  const mime = String(mimeType || "").toLowerCase();
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (mime.startsWith("model/")) return "model";
  return "";
}

/** A file name that is safe on Windows and macOS. */
function safeFilename(name, fallback = "file") {
  let base = String(name || "").replace(/\\/g, "/").split("/").pop();
  base = base.replace(/[^A-Za-z0-9._ -]+/g, "_").replace(/^[ .]+|[ .]+$/g, "");
  return base.slice(0, 120) || fallback;
}

/** An event you can wait on with a timeout, like Python's threading.Event. */
class Signal {
  constructor() {
    this._set = false;
    this._waiters = [];
  }

  get isSet() {
    return this._set;
  }

  set() {
    this._set = true;
    const waiters = this._waiters;
    this._waiters = [];
    for (const w of waiters) w(true);
  }

  clear() {
    this._set = false;
  }

  /** Resolves true when set (at once if already set), false after `ms`. */
  wait(ms) {
    if (this._set) return Promise.resolve(true);
    return new Promise((resolve) => {
      let timer = null;
      const waiter = (value) => {
        if (timer !== null) clearTimeout(timer);
        resolve(value);
      };
      this._waiters.push(waiter);
      if (ms !== undefined && ms !== null && ms !== Infinity) {
        timer = setTimeout(() => {
          const i = this._waiters.indexOf(waiter);
          if (i >= 0) this._waiters.splice(i, 1);
          resolve(false);
        }, Math.max(0, ms));
      }
    });
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));
}

/** One line of printable text, at most `limit` characters. */
function oneLine(value, limit) {
  const text = String(value === null || value === undefined ? "" : value).replace(/[\u0000-\u001f\u007f-\u009f]/g, "");
  return text.trim().slice(0, limit);
}

module.exports = {
  parseTime,
  Backoff,
  cutText,
  utf8Length,
  goJsonSize,
  UPLOAD_TYPES,
  MIME_EXTENSIONS,
  IMPORT_KINDS,
  sniffExtension,
  importExtension,
  otherKind,
  extname,
  stem,
  safeFilename,
  Signal,
  sleep,
  oneLine,
};
