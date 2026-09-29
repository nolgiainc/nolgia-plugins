// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Native paths to UXP file entries (the plugin has full file access, which
// the manifest declares, so save, open and export can use the paths the
// agent gives).
"use strict";

const uxp = require("uxp");
const fs = uxp.storage.localFileSystem;
const types = uxp.storage.types;
const pathutil = require("../core/pathutil.js");
const { CommandError } = require("../core/commands.js");

let homeCache = null;

/** The person's home folder. */
function home() {
  if (homeCache) return homeCache;
  try {
    const os = require("os");
    if (typeof os.homedir === "function" && os.homedir()) homeCache = String(os.homedir());
  } catch (err) {
    // older UXP has no os.homedir
  }
  return homeCache;
}

/** Called at startup with the plugin's data folder, which lives under the
 *  home folder (C:\Users\<name>\AppData\... or /Users/<name>/Library/...). */
function learnHome(dataFolderPath) {
  if (home() || !dataFolderPath) return;
  const m = /^(.*?)[\\/](AppData|Library)[\\/]/.exec(dataFolderPath);
  if (m) homeCache = m[1];
}

function defaultExportDir() {
  const h = home();
  if (!h) throw new CommandError("Could not find your Documents folder. Save the document first, or give a filename in a saved folder.");
  return pathutil.join(pathutil.join(h, "Documents"), "NOLGIA exports");
}

function resolve(p, relativeTo) {
  try {
    return pathutil.resolve(p, relativeTo || null, home());
  } catch (err) {
    throw new CommandError(err.message);
  }
}

async function entry(p) {
  try {
    return await fs.getEntryWithUrl(pathutil.toFileUrl(p));
  } catch (err) {
    return null;
  }
}

async function exists(p) {
  return (await entry(p)) !== null;
}

async function isFolder(p) {
  const e = await entry(p);
  return Boolean(e && e.isFolder);
}

async function getFile(p) {
  const e = await entry(p);
  return e && e.isFile ? e : null;
}

/** The folder at `p`, made (with its parents) when missing. */
async function ensureFolder(p) {
  const existing = await entry(p);
  if (existing) {
    if (!existing.isFolder) throw new CommandError(p + " is a file, not a folder.");
    return existing;
  }
  const parent = pathutil.dirname(p);
  if (parent && parent !== p) await ensureFolder(parent);
  try {
    return await fs.createEntryWithUrl(pathutil.toFileUrl(p), { type: types.folder });
  } catch (err) {
    const again = await entry(p);
    if (again && again.isFolder) return again;
    throw new CommandError("Could not make the folder " + p + ": " + ((err && err.message) || err));
  }
}

module.exports = {
  home,
  learnHome,
  defaultExportDir,
  resolve,
  exists,
  isFolder,
  getFile,
  ensureFolder,
  dirname: pathutil.dirname,
  basename: pathutil.basename,
  join: pathutil.join,
};
