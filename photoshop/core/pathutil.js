// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Native path strings for Windows and macOS, without Node's path module
// (UXP has none). ps/paths.js turns them into UXP file entries.
"use strict";

function isWindowsPath(p) {
  return /^[A-Za-z]:([\\/]|$)/.test(p) || p.startsWith("\\\\");
}

function sepOf(p) {
  return isWindowsPath(p) || (p.includes("\\") && !p.includes("/")) ? "\\" : "/";
}

function isAbsolute(p) {
  return isWindowsPath(p) || p.startsWith("/");
}

/** Clean up separators and . / .. parts, keeping the path's own style. */
function normalize(p) {
  p = String(p || "").trim();
  if (!p) return p;
  const sep = sepOf(p);
  const unc = p.startsWith("\\\\") || (sep === "\\" && p.startsWith("//"));
  let prefix = "";
  let rest = p.replace(/[\\/]+/g, "/");
  const drive = /^([A-Za-z]:)\/?/.exec(rest);
  if (drive) {
    prefix = drive[1].toUpperCase() + "/";
    rest = rest.slice(drive[0].length);
  } else if (unc) {
    prefix = "//";
    rest = rest.replace(/^\/+/, "");
  } else if (rest.startsWith("/")) {
    prefix = "/";
    rest = rest.slice(1);
  }
  const parts = [];
  for (const part of rest.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (parts.length && parts[parts.length - 1] !== "..") parts.pop();
      else if (!prefix) parts.push("..");
      continue;
    }
    parts.push(part);
  }
  const out = prefix + parts.join("/");
  return sep === "\\" ? out.replace(/\//g, "\\") : out;
}

function dirname(p) {
  const n = normalize(p);
  const sep = sepOf(n);
  const i = n.lastIndexOf(sep);
  if (i < 0) return "";
  if (i === 0) return sep;
  if (/^[A-Za-z]:$/.test(n.slice(0, i))) return n.slice(0, i + 1);
  return n.slice(0, i);
}

function basename(p) {
  const n = String(p || "").replace(/[\\/]+$/, "");
  return n.split(/[\\/]/).pop();
}

function join(dir, name) {
  const sep = sepOf(dir);
  if (!dir) return name;
  return normalize(dir.replace(/[\\/]+$/, "") + sep + name);
}

/** An absolute path for `p`. `~` is the home folder; a relative path is
 *  taken next to `relativeTo` (the open document) when there is one. */
function resolve(p, relativeTo, home) {
  p = String(p || "").trim();
  if (!p) throw new Error("empty path");
  if (p === "~" || p.startsWith("~/") || p.startsWith("~\\")) {
    if (!home) throw new Error("Use a full path, for example C:/Projects/shot.psd or /Users/me/shot.psd.");
    p = join(home, p.slice(2) || ".");
  }
  if (isAbsolute(p)) return normalize(p);
  if (!relativeTo) throw new Error("Use a full path, for example C:/Projects/shot.psd or /Users/me/shot.psd.");
  return join(dirname(relativeTo), p);
}

/** The file: URL UXP's getEntryWithUrl wants for a native path. */
function toFileUrl(p) {
  const n = normalize(p).replace(/\\/g, "/");
  if (/^[A-Za-z]:\//.test(n)) return "file:/" + n;
  if (n.startsWith("//")) return "file:" + n;
  return "file:" + n;
}

module.exports = { isAbsolute, normalize, dirname, basename, join, resolve, toFileUrl, sepOf };
