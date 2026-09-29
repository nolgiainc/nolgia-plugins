// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// What each command does inside Photoshop.
//
// Each runner `(args, prepared, command)` returns the result object or
// throws CommandError. Network work happens around it: `prepare` downloads
// the asset for import_asset first, and `finish` uploads a file a command
// made (a result that carries `_upload`) afterwards.
//
// Photoshop only lets a plugin change a document inside
// core.executeAsModal, so every runner that changes something goes through
// `modal()`.
"use strict";

const photoshop = require("photoshop");
const uxp = require("uxp");
const { app, core, action, constants } = photoshop;
const fs = uxp.storage.localFileSystem;
const formats = uxp.storage.formats;

const util = require("../core/util.js");
const { CommandError } = require("../core/commands.js");
const { runCode } = require("../core/runcode.js");
const paths = require("./paths.js");

// The MCP preview tool shows the still inline only up to 3,750,000 bytes.
const PREVIEW_MAX_BYTES = 3600000;
const PREVIEW_JPEG_QUALITIES = [10, 8, 6, 4];
const UPLOAD_MAX_BYTES = 100 * 1024 * 1024; // NOLGIA takes images up to 100 MB
const IMPORT_MAX_BYTES = 500 * 1024 * 1024;
const MAX_INFO_LAYERS = 1000;
const UPLOAD_TAGS = ["photoshop"];
const KEEP_SELECTION = "NOLGIA kept selection";
const PSD_NOTE = "Photoshop files stay on this computer; NOLGIA stores previews and exported images.";

// Settings the developer file (see ps/host.js) can change for tests.
const tuning = { previewMaxBytes: PREVIEW_MAX_BYTES, exportDir: null };

// ------------------------------------------------------------------ basics

function appVersion() {
  try {
    return String(uxp.host.version || app.version || "");
  } catch (err) {
    return "";
  }
}

function activeDocument() {
  try {
    return app.activeDocument || null;
  } catch (err) {
    return null;
  }
}

function documents() {
  const out = [];
  try {
    const docs = app.documents;
    for (let i = 0; i < docs.length; i++) out.push(docs[i]);
  } catch (err) {
    // no documents
  }
  return out;
}

/** The file a document was opened from or saved to, or "" while it has
 *  never been saved. */
function docPath(doc) {
  try {
    const path = doc.path;
    return typeof path === "string" ? path : "";
  } catch (err) {
    return "";
  }
}

function isDirty(doc) {
  try {
    return !doc.saved;
  } catch (err) {
    return true;
  }
}

/** {name, path?} for the session's `document`. */
function documentSnapshot() {
  const doc = activeDocument();
  if (!doc) return { name: "" };
  const out = { name: String(doc.name || "") };
  const path = docPath(doc);
  if (path) out.path = path;
  return out;
}

function findDocument(ref) {
  if (ref === null || ref === undefined) {
    const doc = activeDocument();
    if (!doc) throw new CommandError("No document is open in Photoshop. Open one first (the open command) or import an image.");
    return doc;
  }
  const docs = documents();
  const doc = docs.find((d) => d.id === ref || d.name === ref);
  if (!doc) {
    throw new CommandError(
      "There is no open document " + JSON.stringify(ref) + ". Open documents: " + (docs.map((d) => d.name).join(", ") || "none") + ".",
    );
  }
  return doc;
}

function modalBusy(err) {
  const text = String((err && err.message) || err || "");
  return /modal/i.test(text) && /(state|busy|another|progress)/i.test(text);
}

function asError(err) {
  if (err instanceof Error) return err;
  const text = String((err && err.message) || err || "unknown error");
  return new Error(text.replace(/^Error: /, ""));
}

/** Run fn inside Photoshop's modal scope (needed for every change).
 *
 *  executeAsModal turns an error thrown inside it into a plain string and
 *  drops its stack, so fn's outcome is carried out as a value instead and
 *  the original error is thrown here. */
async function modal(name, fn, options = {}) {
  let outcome;
  try {
    outcome = await core.executeAsModal(
      async (context) => {
        try {
          return { ok: true, value: await fn(context) };
        } catch (error) {
          return { ok: false, error };
        }
      },
      Object.assign({ commandName: "NOLGIA: " + name }, options),
    );
  } catch (err) {
    if (modalBusy(err)) {
      const busy = new CommandError(
        "Photoshop is busy with something else (an open dialog, a tool in use, or another plugin), so NOLGIA cannot change the document right now. Ask the person to finish or close it, then try again. (Photoshop said: " +
          String((err && err.message) || err).slice(0, 200) + ")",
      );
      busy.busy = true;
      throw busy;
    }
    throw asError(err);
  }
  if (!outcome || !outcome.ok) throw outcome ? outcome.error : new Error("Photoshop gave no result.");
  return outcome.value;
}

async function batchPlay(descriptors, options = {}) {
  return action.batchPlay(descriptors, options);
}

/** batchPlay that throws when a step fails. Photoshop's own batchPlay does
 *  not throw: a failed step comes back as {_obj: "error", message}. */
async function play(descriptors, options = {}) {
  const answers = await action.batchPlay(descriptors, options);
  (answers || []).forEach((answer, i) => {
    if (answer && answer._obj === "error") {
      const what = descriptors[i] && descriptors[i]._obj ? descriptors[i]._obj : "step " + (i + 1);
      throw new Error("batchPlay " + what + " failed: " + String(answer.message || "").replace(/^NOLGIA: /, "") + " (" + answer.result + ")");
    }
  });
  return answers;
}

// ------------------------------------------------------------------- info

function safe(fn, fallback = null) {
  try {
    const value = fn();
    return value === undefined ? fallback : value;
  } catch (err) {
    return fallback;
  }
}

function bounds(b) {
  if (!b) return null;
  const pick = (v) => (typeof v === "number" ? Math.round(v * 100) / 100 : v && typeof v._value === "number" ? v._value : null);
  const out = { left: pick(b.left), top: pick(b.top), right: pick(b.right), bottom: pick(b.bottom) };
  return Object.values(out).every((v) => typeof v === "number") ? out : null;
}

function walkLayers(layers, visit, depth = 0) {
  for (let i = 0; i < layers.length; i++) {
    const layer = layers[i];
    visit(layer, depth);
    const kids = safe(() => layer.layers);
    if (kids && kids.length) walkLayers(kids, visit, depth + 1);
  }
}

async function maskFlags(doc, ids) {
  // One batchPlay call asks for both mask flags of every layer.
  const props = ["hasUserMask", "hasVectorMask", "userMaskEnabled"];
  const descriptors = [];
  for (const id of ids) {
    for (const prop of props) {
      descriptors.push({ _obj: "get", _target: [{ _property: prop }, { _ref: "layer", _id: id }, { _ref: "document", _id: doc.id }] });
    }
  }
  const flags = {};
  if (!descriptors.length) return flags;
  let answers = [];
  try {
    answers = await batchPlay(descriptors, {});
  } catch (err) {
    return flags;
  }
  ids.forEach((id, i) => {
    const out = {};
    props.forEach((prop, j) => {
      const answer = answers[i * props.length + j] || {};
      out[prop] = typeof answer[prop] === "boolean" ? answer[prop] : false;
    });
    flags[id] = out;
  });
  return flags;
}

async function selectionBounds(doc) {
  try {
    const answer = await batchPlay([{ _obj: "get", _target: [{ _property: "selection" }, { _ref: "document", _id: doc.id }] }], {});
    const sel = answer && answer[0] && answer[0].selection;
    if (!sel) return null;
    return bounds(sel);
  } catch (err) {
    return null;
  }
}

async function layerTree(doc) {
  const flat = [];
  walkLayers(safe(() => doc.layers, []), (layer) => flat.push(layer));
  const listed = flat.slice(0, MAX_INFO_LAYERS);
  // Photoshop hands out a new object for a layer on every read, so layers
  // are matched by id, never by identity.
  const listedIds = new Set(listed.map((l) => l.id));
  const flags = await maskFlags(doc, listed.map((l) => l.id));
  const activeIds = new Set(safe(() => Array.from(doc.activeLayers).map((l) => l.id), []));
  const describe = (layer) => {
    const f = flags[layer.id] || {};
    const out = {
      id: layer.id,
      name: safe(() => layer.name, ""),
      kind: String(safe(() => layer.kind, "")),
      visible: safe(() => layer.visible, true),
      opacity: safe(() => Math.round(layer.opacity * 10) / 10, 100),
      blend_mode: String(safe(() => layer.blendMode, "")),
      mask: Boolean(f.hasUserMask),
      vector_mask: Boolean(f.hasVectorMask),
    };
    if (f.hasUserMask && f.userMaskEnabled === false) out.mask_enabled = false;
    if (safe(() => layer.isBackgroundLayer, false)) out.background = true;
    if (safe(() => layer.isClippingMask, false)) out.clipped = true;
    if (safe(() => layer.allLocked, false)) out.locked = true;
    if (activeIds.has(layer.id)) out.selected = true;
    const b = bounds(safe(() => layer.bounds));
    if (b) out.bounds = b;
    const kids = safe(() => layer.layers);
    if (kids && kids.length !== undefined && String(out.kind) === "group") {
      out.layers = [];
      for (let i = 0; i < kids.length; i++) if (listedIds.has(kids[i].id)) out.layers.push(describe(kids[i]));
    }
    return out;
  };
  const top = safe(() => doc.layers, []);
  const tree = [];
  for (let i = 0; i < top.length; i++) if (listedIds.has(top[i].id)) tree.push(describe(top[i]));
  return { tree, count: flat.length, listed: listed.length, active: flat.filter((l) => activeIds.has(l.id)).map((l) => l.name) };
}

async function describeDocument(doc) {
  const layers = await layerTree(doc);
  const out = {
    id: doc.id,
    name: safe(() => doc.name, ""),
    path: docPath(doc) || null,
    dirty: isDirty(doc),
    width: safe(() => Math.round(doc.width)),
    height: safe(() => Math.round(doc.height)),
    resolution: safe(() => Math.round(doc.resolution * 100) / 100),
    mode: String(safe(() => doc.mode, "")),
    bits_per_channel: String(safe(() => doc.bitsPerChannel, "")),
    color_profile: safe(() => doc.colorProfileName, null),
    layer_count: layers.count,
    active_layers: layers.active,
    selection: await selectionBounds(doc),
    layers: layers.tree,
  };
  if (layers.listed < layers.count) out.layers_note = "Only the first " + layers.listed + " of " + layers.count + " layers are listed.";
  const history = safe(() => doc.activeHistoryState && doc.activeHistoryState.name, null);
  if (history) out.history_state = history;
  return out;
}

async function doInfo() {
  const active = activeDocument();
  const docs = documents();
  return {
    app: "photoshop",
    app_version: appVersion(),
    documents: docs.map((d) => ({
      id: d.id,
      name: safe(() => d.name, ""),
      path: docPath(d) || null,
      dirty: isDirty(d),
      active: Boolean(active && d.id === active.id),
    })),
    active_document: active ? await describeDocument(active) : null,
  };
}

// -------------------------------------------------------------------- run

function runGlobals() {
  return {
    require,
    photoshop,
    uxp,
    app,
    core,
    action,
    batchPlay: (descriptors, options = {}) => action.batchPlay(descriptors, options),
    play,
    constants,
    imaging: photoshop.imaging,
    fs,
    formats,
    doc: activeDocument(),
    modal: (fn, name = "run code") => modal(name, fn),
    sleep: util.sleep,
  };
}

/** Every run is one history step ("NOLGIA: run code"), so one Undo takes
 *  it back; when the code fails, its changes to the document are undone. */
function modalWrap(label, state = {}) {
  return (body) =>
    modal(label, async (context) => {
      const doc = activeDocument();
      let suspension = null;
      if (doc) {
        try {
          suspension = await context.hostControl.suspendHistory({ documentID: doc.id, name: "NOLGIA: " + label });
          state.suspended = true;
        } catch (err) {
          suspension = null;
        }
      }
      let ok = false;
      try {
        const value = await body(context);
        ok = true;
        return value;
      } finally {
        if (suspension !== null) {
          try {
            await context.hostControl.resumeHistory(suspension, ok);
          } catch (err) {
            // the document was closed by the code
          }
        }
      }
    });
}

async function doRun(args, prepared, command) {
  // Stop in time for the failure to reach NOLGIA before the command expires.
  let timeout = Math.max(1, command.remaining());
  if (args.timeout_seconds) timeout = Math.min(timeout, args.timeout_seconds);
  const state = { suspended: false, started: false };
  const wrapped = modalWrap("run code", state);
  // When Photoshop refuses the modal scope (a dialog is open), the code still
  // runs, outside it: it can read the documents but not change them.
  let outsideModal = false;
  const wrap = async (body) => {
    try {
      return await wrapped((context) => {
        state.started = true;
        return body(context);
      });
    } catch (err) {
      if (!(err && err.busy) || state.started) throw err;
      outsideModal = true;
      return body(undefined);
    }
  };
  const outcome = await runCode(args.code, runGlobals(), {
    timeout,
    console: typeof console !== "undefined" ? console : null,
    wrap,
  });
  if (outsideModal) {
    const note = "NOLGIA: Photoshop was busy (an open dialog or a tool in use), so this code ran outside executeAsModal: it could read the documents but not change them. Ask the person to close the dialog, then run the changes again.\n";
    outcome.stderr = note + outcome.stderr;
    if (!outcome.ok) outcome.error += "\n" + note.trim();
  }
  if (!outcome.ok) {
    let error = outcome.error;
    if (!outcome.timedOut && state.suspended) error += "\nPhotoshop undid what this run changed in the document that was active when it started.";
    throw new CommandError(error, outcome.result());
  }
  return outcome.result();
}

// ------------------------------------------------------------- files

async function tempFolder(prefix) {
  const tmp = await fs.getTemporaryFolder();
  return tmp.createFolder(prefix + "-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6));
}

async function removeEntry(entry) {
  if (!entry) return;
  try {
    if (entry.isFolder) {
      const kids = await entry.getEntries();
      for (const kid of kids) await removeEntry(kid);
    }
    await entry.delete();
  } catch (err) {
    // temp files go away with the temp folder anyway
  }
}

async function readBytes(file) {
  return new Uint8Array(await file.read({ format: formats.binary }));
}

async function fileSize(file) {
  try {
    const meta = await file.getMetadata();
    if (meta && typeof meta.size === "number") return meta.size;
  } catch (err) {
    // fall back to reading it
  }
  return (await readBytes(file)).byteLength;
}

// ---------------------------------------------------------- flat copies

function isRgb8(doc) {
  const mode = String(safe(() => doc.mode, ""));
  const bits = String(safe(() => doc.bitsPerChannel, ""));
  return /rgb/i.test(mode) && /8/.test(bits);
}

/** A flattened copy of `doc` (visible layers merged), cropped to `region`,
 *  in 8-bit RGB and at most `width` pixels wide. Call inside modal(). */
async function flatCopy(doc, { width = null, region = null } = {}) {
  const dup = await doc.duplicate("NOLGIA copy", true);
  try {
    if (region) {
      const w = Math.round(dup.width);
      const h = Math.round(dup.height);
      const box = {
        left: Math.min(region.left, w - 1),
        top: Math.min(region.top, h - 1),
        right: Math.min(region.right, w),
        bottom: Math.min(region.bottom, h),
      };
      await dup.crop(box);
    }
    if (!isRgb8(dup)) {
      const mode = String(safe(() => dup.mode, ""));
      if (!/rgb/i.test(mode)) await dup.changeMode(constants.ChangeMode.RGB);
      if (!/8/.test(String(safe(() => dup.bitsPerChannel, "")))) dup.bitsPerChannel = constants.BitsPerChannelType.EIGHT;
    }
    const w = Math.round(dup.width);
    const h = Math.round(dup.height);
    if (width && width < w) {
      let tw = width;
      let th = Math.max(1, Math.round((width * h) / w));
      if (th > 1920) {
        th = 1920;
        tw = Math.max(1, Math.round((1920 * w) / h));
      }
      const method = constants.ResampleMethod && (constants.ResampleMethod.BICUBICSHARPER || constants.ResampleMethod.BICUBIC);
      await dup.resizeImage(tw, th, undefined, method);
    }
    return dup;
  } catch (err) {
    await closeCopy(dup);
    throw err;
  }
}

async function closeCopy(dup) {
  try {
    await dup.closeWithoutSaving();
  } catch (err) {
    // already closed
  }
}

async function savePng(dup, file) {
  await dup.saveAs.png(file, { compression: 6, interlaced: false }, true);
}

async function saveJpeg(dup, file, quality) {
  if (safe(() => dup.layers.length, 1) > 1 || !safe(() => dup.layers[0].isBackgroundLayer, true)) await dup.flatten();
  await dup.saveAs.jpg(file, { quality, embedColorProfile: true }, true);
}

function stemOf(doc, filename) {
  if (filename) return util.safeFilename(util.stem(filename), "untitled");
  return util.safeFilename(util.stem(safe(() => doc.name, "untitled")), "untitled");
}

// ----------------------------------------------------------------- preview

async function doPreview(args) {
  const doc = findDocument(args.document);
  const folder = await tempFolder("nolgia-preview");
  const stem = stemOf(doc);
  const maxBytes = tuning.previewMaxBytes || PREVIEW_MAX_BYTES;
  let file;
  let mime = "image/png";
  let size = { width: 0, height: 0 };
  try {
    await modal("preview", async () => {
      const original = doc;
      const docWidth = Math.round(doc.width);
      const regionWidth = args.region ? Math.min(args.region.right, docWidth) - args.region.left : docWidth;
      const width = Math.min(args.width || 1280, regionWidth);
      const dup = await flatCopy(doc, { width, region: args.region });
      try {
        size = { width: Math.round(dup.width), height: Math.round(dup.height) };
        file = await folder.createFile(stem + "-preview.png", { overwrite: true });
        await savePng(dup, file);
        if ((await fileSize(file)) > maxBytes) {
          mime = "image/jpeg";
          let fitted = false;
          for (const quality of PREVIEW_JPEG_QUALITIES) {
            file = await folder.createFile(stem + "-preview.jpg", { overwrite: true });
            await saveJpeg(dup, file, quality);
            if ((await fileSize(file)) <= maxBytes) {
              fitted = true;
              break;
            }
          }
          if (!fitted) throw new CommandError("The preview image is too large even as a JPEG. Ask for a smaller width.");
        }
      } finally {
        await closeCopy(dup);
        try {
          app.activeDocument = original;
        } catch (err) {
          // it stays active in most cases anyway
        }
      }
    });
  } catch (err) {
    await removeEntry(folder);
    throw err;
  }
  const out = {
    _upload: {
      file,
      content_type: mime,
      filename: file.name,
      display_name: "Photoshop preview, " + safe(() => doc.name, "untitled"),
      cleanup: folder,
    },
    width: size.width,
    height: size.height,
    mime_type: mime,
    document: safe(() => doc.name, ""),
  };
  if (args.region) out.region = args.region;
  return out;
}

// ------------------------------------------------------------------ export

async function exportPsdCopy(doc, filename) {
  const current = docPath(doc);
  const folderPath = current ? paths.dirname(current) : tuning.exportDir || paths.defaultExportDir();
  const base = filename ? stemOf(doc, filename) : stemOf(doc) + "-copy";
  let target = paths.join(folderPath, base + ".psd");
  if (await paths.exists(target)) {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const stamp = d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + "-" + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
    target = paths.join(folderPath, base + "-" + stamp + ".psd");
    let n = 2;
    while (await paths.exists(target)) {
      target = paths.join(folderPath, base + "-" + stamp + "-" + n + ".psd");
      n += 1;
    }
  }
  const folder = await paths.ensureFolder(folderPath);
  const file = await folder.createFile(paths.basename(target), { overwrite: false });
  await modal("export PSD", async () => {
    await doc.saveAs.psd(file, { embedColorProfile: true, maximizeCompatibility: true }, true);
  });
  if (!(await paths.exists(target))) throw new CommandError("Photoshop did not write the copy to " + target + ".");
  return { asset_id: null, path: target, note: PSD_NOTE, format: "psd", filename: paths.basename(target) };
}

async function doExport(args) {
  const doc = findDocument(args.document);
  if (args.format === "psd") return exportPsdCopy(doc, args.filename);
  const folder = await tempFolder("nolgia-export");
  const stem = stemOf(doc, args.filename);
  const ext = args.format === "jpg" ? ".jpg" : ".png";
  const mime = args.format === "jpg" ? "image/jpeg" : "image/png";
  let file;
  let size = { width: 0, height: 0 };
  try {
    await modal("export " + args.format.toUpperCase(), async () => {
      const dup = await flatCopy(doc, {});
      try {
        size = { width: Math.round(dup.width), height: Math.round(dup.height) };
        file = await folder.createFile(stem + ext, { overwrite: true });
        if (args.format === "jpg") await saveJpeg(dup, file, args.quality || 11);
        else await savePng(dup, file);
      } finally {
        await closeCopy(dup);
        try {
          app.activeDocument = doc;
        } catch (err) {
          // ignore
        }
      }
    });
    if ((await fileSize(file)) > UPLOAD_MAX_BYTES) {
      throw new CommandError("The " + args.format.toUpperCase() + " is over 100 MB, the most NOLGIA takes for an image. Export a jpg instead, or make the image smaller.");
    }
  } catch (err) {
    await removeEntry(folder);
    throw err;
  }
  return {
    _upload: { file, content_type: mime, filename: file.name, display_name: file.name, cleanup: folder },
    format: args.format,
    filename: file.name,
    width: size.width,
    height: size.height,
  };
}

// ------------------------------------------------------------- save, open

async function doSave(args) {
  const doc = findDocument(args.document);
  if (!args.path) {
    const current = docPath(doc);
    if (!current) {
      throw new CommandError(
        "This document has never been saved, so there is no file to save to. Pass `path`, for example C:/Projects/shot.psd.",
      );
    }
    await modal("save", async () => {
      await doc.save();
    });
    return { path: docPath(doc) || current, document: safe(() => doc.name, "") };
  }
  let target = paths.resolve(args.path, docPath(doc));
  const ext = util.extname(target);
  if (ext !== ".psd" && ext !== ".psb") {
    if (ext && ext !== ".") {
      throw new CommandError(
        "save writes Photoshop files (.psd or .psb). To make a " + ext + " image, use the export command (png or jpg).",
      );
    }
    target += ".psd";
  }
  if (await paths.isFolder(target)) throw new CommandError(target + " is a folder. Give a file name ending in .psd.");
  const folder = await paths.ensureFolder(paths.dirname(target));
  const file = await folder.createFile(paths.basename(target), { overwrite: true });
  await modal("save", async () => {
    if (util.extname(target) === ".psb") await doc.saveAs.psb(file, {}, false);
    else await doc.saveAs.psd(file, { embedColorProfile: true, maximizeCompatibility: true }, false);
  });
  return { path: docPath(doc) || target, document: safe(() => doc.name, "") };
}

async function doOpen(args) {
  const target = paths.resolve(args.path, docPath(activeDocument() || {}));
  const entry = await paths.getFile(target);
  if (!entry) throw new CommandError("There is no file at " + target + ".");
  let doc;
  try {
    doc = await modal("open", async () => app.open(entry));
  } catch (err) {
    if (err instanceof CommandError) throw err;
    throw new CommandError("Photoshop could not open " + target + ": " + ((err && err.message) || err));
  }
  doc = doc || activeDocument();
  return { path: (doc && docPath(doc)) || target, document: doc ? safe(() => doc.name, "") : "", id: doc ? doc.id : null };
}

// ------------------------------------------------------------------ import

/** Worker side: fetch the asset's file before Photoshop runs the command. */
async function prepareImport(command, api) {
  const assetId = command.args.asset_id;
  let asset;
  try {
    asset = await api.getAsset(assetId);
  } catch (err) {
    if (err && err.status === 404) throw new CommandError("NOLGIA has no asset " + assetId + " in this account.");
    throw err;
  }
  const url = asset && asset.signed_url;
  if (!url) throw new CommandError("NOLGIA did not give a download link for asset " + assetId + ".");
  const name = asset.display_name || assetId;
  const kind = util.otherKind(name, asset.mime_type);
  if (kind) {
    throw new CommandError(
      "Photoshop brings in images; asset " + assetId + " is " + (kind === "model" ? "a 3D model" : "a " + kind + " file") +
        ". Use it in an app that takes " + kind + " (Blender, Premiere Pro).",
    );
  }
  let bytes;
  try {
    bytes = await api.download(url, IMPORT_MAX_BYTES);
  } catch (err) {
    if (err && err.name === "NetworkError") throw new CommandError("Could not download the asset from NOLGIA (" + err.message + ").");
    throw err;
  }
  const ext = util.importExtension(name, asset.mime_type, bytes.subarray(0, 64));
  if (!ext) {
    throw new CommandError(
      "Photoshop cannot open this file type (" + (asset.mime_type || "unknown") + "). It imports PNG, JPEG, WebP, TIFF and PSD images from NOLGIA.",
    );
  }
  const folder = await tempFolder("nolgia-import");
  const filename = util.safeFilename(util.stem(name), assetId) + ext;
  const file = await folder.createFile(filename, { overwrite: true });
  await file.write(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), { format: formats.binary });
  return { file, folder, ext, assetId, name: util.stem(name) || assetId };
}

async function placeFile(file) {
  const token = await fs.createSessionToken(file);
  await play(
    [
      {
        _obj: "placeEvent",
        null: { _path: token, _kind: "local" },
        freeTransformCenterState: { _enum: "quadCenterState", _value: "QCSAverage" },
        offset: { _obj: "offset", horizontal: { _unit: "pixelsUnit", _value: 0 }, vertical: { _unit: "pixelsUnit", _value: 0 } },
        _options: { dialogOptions: "dontDisplay" },
      },
    ],
    {},
  );
}

/** Photoshop places an image centred on the selection (when there is one)
 *  and may or may not scale it to the canvas (a preference). Line it up the
 *  same way every time: an image with the canvas's shape covers the canvas
 *  exactly (a plate or a full-frame edit lines up pixel for pixel), anything
 *  else sits in the middle of the canvas. Returns "canvas" or "centered". */
async function alignPlaced(doc, layer) {
  const cw = Number(doc.width);
  const ch = Number(doc.height);
  let b = bounds(safe(() => layer.boundsNoEffects)) || bounds(safe(() => layer.bounds));
  if (!b) return "as placed";
  let lw = b.right - b.left;
  let lh = b.bottom - b.top;
  if (lw <= 0 || lh <= 0) return "as placed";
  const sameShape = Math.abs(lw / lh - cw / ch) <= 0.01 * (cw / ch);
  if (sameShape && (Math.abs(lw - cw) > 0.5 || Math.abs(lh - ch) > 0.5)) {
    await layer.scale((cw / lw) * 100, (ch / lh) * 100);
    b = bounds(safe(() => layer.boundsNoEffects)) || b;
    lw = b.right - b.left;
    lh = b.bottom - b.top;
  }
  const dx = sameShape ? -b.left : cw / 2 - (b.left + b.right) / 2;
  const dy = sameShape ? -b.top : ch / 2 - (b.top + b.bottom) / 2;
  if (Math.abs(dx) > 0.01 || Math.abs(dy) > 0.01) await layer.translate(dx, dy);
  return sameShape ? "canvas" : "centered";
}

async function doImportAsset(args, prepared) {
  const { file, folder, name } = prepared;
  try {
    const target = activeDocument();
    if (args.as === "document" || !target) {
      const doc = await modal("import", async () => app.open(file));
      const opened = doc || activeDocument();
      return { imported: [opened ? opened.name : name], kind: "document", document: opened ? opened.name : name, id: opened ? opened.id : null };
    }
    let layer = null;
    let placement = "";
    await modal("import", async (context) => {
      let suspension = null;
      try {
        suspension = await context.hostControl.suspendHistory({ documentID: target.id, name: "NOLGIA: import " + name });
      } catch (err) {
        suspension = null;
      }
      let ok = false;
      try {
        // Placing drops the selection; keep it in a channel and put it back.
        const selection = await selectionBounds(target);
        if (selection) await play([{ _obj: "duplicate", _target: [{ _ref: "channel", _property: "selection" }], name: KEEP_SELECTION }]);
        await placeFile(file);
        layer = target.activeLayers[0];
        if (!layer) throw new CommandError("Photoshop placed the image but no new layer came in.");
        placement = await alignPlaced(target, layer);
        if (args.as === "pixels") await layer.rasterize(constants.RasterizeType.ENTIRELAYER);
        layer = target.activeLayers[0];
        layer.name = args.name || name;
        if (selection) {
          await play([
            { _obj: "set", _target: [{ _ref: "channel", _property: "selection" }], to: { _ref: "channel", _name: KEEP_SELECTION } },
            { _obj: "delete", _target: [{ _ref: "channel", _name: KEEP_SELECTION }] },
          ]);
        }
        ok = true;
      } finally {
        if (suspension !== null) {
          try {
            await context.hostControl.resumeHistory(suspension, ok);
          } catch (err) {
            // ignore
          }
        }
      }
    });
    const parent = safe(() => layer.parent, null);
    return {
      imported: [layer.name],
      kind: args.as === "pixels" ? "layer" : "smart_object",
      layer_id: layer.id,
      document: safe(() => target.name, ""),
      // The image lands right above the active layer, inside its group.
      group: parent && parent.typename === "Layer" ? safe(() => parent.name, null) : null,
      placement,
      bounds: bounds(safe(() => layer.boundsNoEffects)),
    };
  } finally {
    await removeEntry(folder);
  }
}

// ------------------------------------------------------------------ upload

/** Worker side: upload a file a command made, then drop the temp copy. */
async function finishUpload(command, result, api) {
  if (!result || typeof result !== "object" || !result._upload) return result;
  const out = Object.assign({}, result);
  const upload = out._upload;
  delete out._upload;
  let asset;
  try {
    const bytes = await readBytes(upload.file);
    asset = await api.uploadBytes(bytes, upload.content_type, {
      filename: upload.filename,
      displayName: upload.display_name,
      tags: UPLOAD_TAGS,
    });
  } catch (err) {
    if (err && err.name === "ApiError") throw new CommandError("NOLGIA did not take the file: " + (err.detail || err.title || err.status));
    if (err && err.name === "NetworkError") throw new CommandError("Could not upload the file to NOLGIA (" + err.message + ").");
    throw err;
  } finally {
    await removeEntry(upload.cleanup);
  }
  return Object.assign({ asset_id: asset && asset.id }, out);
}

const RUNNERS = {
  info: doInfo,
  run: doRun,
  preview: doPreview,
  import_asset: doImportAsset,
  export: doExport,
  save: doSave,
  open: doOpen,
};

async function prepare(command, api) {
  if (command.kind === "import_asset") return prepareImport(command, api);
  return null;
}

module.exports = {
  RUNNERS,
  prepare,
  finishUpload,
  appVersion,
  documentSnapshot,
  activeDocument,
  tuning,
  modal,
};
