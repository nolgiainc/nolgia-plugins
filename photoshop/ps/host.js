// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// What the controller needs from UXP: settings, the saved sign in, the
// developer file, the instance id, the browser and the clipboard.
//
// - Settings (Connected, Allow NOLGIA Agent, Ask before running code) live
//   in the plugin's localStorage.
// - The token lives in UXP secure storage (encrypted by the system); the
//   account email, the token's expiry and the API it belongs to sit next to
//   it in localStorage.
// - The instance id is a file in the plugin's data folder, so the API keeps
//   one session per Photoshop install across restarts.
// - The developer file, nolgia-dev.json in the data folder, is for tests
//   and scripts only: see the README.
"use strict";

const uxp = require("uxp");
const ops = require("./ops.js");
const paths = require("./paths.js");
const { PLUGIN_VERSION } = require("../core/constants.js");

const fs = uxp.storage.localFileSystem;
const formats = uxp.storage.formats;
const SETTINGS_KEY = "nolgia.settings";
const TOKEN_META_KEY = "nolgia.sign_in";
const TOKEN_KEY = "nolgia.token";
const DEV_FILE = "nolgia-dev.json";

function randomUuid() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const hex = [];
  for (let i = 0; i < 16; i++) hex.push(Math.floor(Math.random() * 256));
  hex[6] = (hex[6] & 0x0f) | 0x40;
  hex[8] = (hex[8] & 0x3f) | 0x80;
  const h = hex.map((b) => b.toString(16).padStart(2, "0")).join("");
  return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
}

function bytesToText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let out = "";
  for (let i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
  return out;
}

let dataFolder = null;
async function data() {
  if (!dataFolder) {
    dataFolder = await fs.getDataFolder();
    paths.learnHome(dataFolder.nativePath);
  }
  return dataFolder;
}

async function readDataFile(name) {
  const folder = await data();
  let entry;
  try {
    entry = await folder.getEntry(name);
  } catch (err) {
    return null;
  }
  return String(await entry.read({ format: formats.utf8 }));
}

async function writeDataFile(name, text) {
  const folder = await data();
  const file = await folder.createFile(name, { overwrite: true });
  await file.write(text, { format: formats.utf8 });
}

function machineName() {
  try {
    const os = require("os");
    for (const fn of ["hostname", "computerName"]) {
      if (typeof os[fn] === "function") {
        const name = os[fn]();
        if (name) return String(name);
      }
    }
  } catch (err) {
    // no os module details
  }
  const home = paths.home();
  const user = home ? home.split(/[\\/]/).filter(Boolean).pop() : "";
  let platform = "";
  try {
    platform = require("os").platform() === "darwin" ? "Mac" : "Windows PC";
  } catch (err) {
    platform = "computer";
  }
  return user ? user + "'s " + platform : platform;
}

function createHost({ onApprovals } = {}) {
  return {
    fetch: (url, init) => fetch(url, init),

    log(message) {
      console.log(message);
    },

    loadSettings() {
      try {
        return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
      } catch (err) {
        return {};
      }
    },

    saveSettings(settings) {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    },

    remember(key, value) {
      localStorage.setItem("nolgia.state." + key, JSON.stringify(value));
    },

    recall(key) {
      try {
        return JSON.parse(localStorage.getItem("nolgia.state." + key) || "null");
      } catch (err) {
        return null;
      }
    },

    async loadToken() {
      let token = "";
      try {
        token = bytesToText(await uxp.storage.secureStorage.getItem(TOKEN_KEY));
      } catch (err) {
        token = "";
      }
      if (!token) return null;
      let meta = {};
      try {
        meta = JSON.parse(localStorage.getItem(TOKEN_META_KEY) || "{}");
      } catch (err) {
        meta = {};
      }
      return { token, email: meta.email || "", expiresAt: meta.expiresAt || 0, apiUrl: meta.apiUrl || "" };
    },

    async saveToken(record) {
      if (!record) {
        try {
          await uxp.storage.secureStorage.removeItem(TOKEN_KEY);
        } catch (err) {
          // nothing saved
        }
        localStorage.removeItem(TOKEN_META_KEY);
        return;
      }
      await uxp.storage.secureStorage.setItem(TOKEN_KEY, record.token);
      localStorage.setItem(
        TOKEN_META_KEY,
        JSON.stringify({ email: record.email || "", expiresAt: record.expiresAt || 0, apiUrl: record.apiUrl || "" }),
      );
    },

    /** nolgia-dev.json in the data folder, or null. */
    async readDevFile() {
      const text = await readDataFile(DEV_FILE);
      if (text === null) return null;
      const dev = JSON.parse(text);
      if (!dev || typeof dev !== "object") return null;
      if (dev.preview_max_bytes) ops.tuning.previewMaxBytes = Number(dev.preview_max_bytes);
      if (dev.export_dir) ops.tuning.exportDir = String(dev.export_dir);
      return dev;
    },

    async instanceId(override) {
      if (override) return String(override);
      let id = "";
      try {
        id = ((await readDataFile("instance_id")) || "").trim();
      } catch (err) {
        id = "";
      }
      if (!id) {
        id = randomUuid();
        try {
          await writeDataFile("instance_id", id + "\n");
        } catch (err) {
          // a new id next time is not a problem
        }
      }
      return id;
    },

    async dataFolderPath() {
      return (await data()).nativePath;
    },

    machineName,

    appVersion: ops.appVersion,

    documentSnapshot: ops.documentSnapshot,

    async openUrl(url) {
      await uxp.shell.openExternal(url, "Sign in to NOLGIA to connect Photoshop.");
    },

    async copyText(text) {
      await navigator.clipboard.setContent({ "text/plain": text });
    },

    runners: ops.RUNNERS,

    prepare: (command, api) => ops.prepare(command, api),

    finish: (command, result, api) => ops.finishUpload(command, result, api),

    approvalsChanged(approvals, controller) {
      if (onApprovals) onApprovals(approvals, controller);
    },

    pluginVersion: PLUGIN_VERSION,
  };
}

module.exports = { createHost, data };
