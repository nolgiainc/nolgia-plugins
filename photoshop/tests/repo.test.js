// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Manifest, license, package and wording rules.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { PLUGIN_VERSION, PLUGIN_ID } = require("../core/constants.js");
const { REPO } = require("./support.js");

const PLUGIN = path.join(REPO, "photoshop");
const manifest = JSON.parse(fs.readFileSync(path.join(PLUGIN, "manifest.json"), "utf8"));

function files(dir, exts, skip = ["node_modules", "dist", ".git"]) {
  const out = [];
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (skip.includes(name)) continue;
    if (fs.statSync(full).isDirectory()) out.push(...files(full, exts, skip));
    else if (exts.some((e) => name.endsWith(e))) out.push(full);
  }
  return out;
}

test("manifest: a v5 Photoshop plugin with a NOLGIA panel", () => {
  assert.equal(manifest.manifestVersion, 5);
  assert.equal(manifest.id, PLUGIN_ID);
  assert.equal(manifest.name, "NOLGIA for Photoshop");
  assert.equal(manifest.version, PLUGIN_VERSION);
  assert.equal(manifest.main, "index.html");
  assert.deepEqual(manifest.host, { app: "PS", minVersion: "25.0.0", data: { apiVersion: 2, loadEvent: "startup" } });
  const panel = manifest.entrypoints.find((e) => e.type === "panel");
  assert.equal(panel.id, "nolgia");
  assert.equal(panel.label.default, "NOLGIA");
  for (const icon of panel.icons.concat(manifest.icons)) {
    for (const scale of icon.scale) {
      const file = icon.path.replace(/\.png$/, "@" + scale + "x.png");
      assert.ok(fs.existsSync(path.join(PLUGIN, file)), file + " is missing");
    }
  }
});

test("manifest: only the permissions the plugin needs", () => {
  const p = manifest.requiredPermissions;
  assert.deepEqual(p.network.domains, ["https://api.nolgia.ai", "https://storage.googleapis.com", "https://*.storage.googleapis.com"]);
  assert.equal(p.localFileSystem, "fullAccess");
  assert.deepEqual(p.launchProcess, { schemes: ["https"], extensions: [] });
  assert.equal(p.clipboard, "readAndWrite");
  assert.equal(p.allowCodeGenerationFromStrings, true);
  assert.equal(p.ipc, undefined, "the release build does not talk to other plugins");
  assert.deepEqual(Object.keys(p).sort(), ["allowCodeGenerationFromStrings", "clipboard", "launchProcess", "localFileSystem", "network"]);
});

test("the license is the full GPL 3", () => {
  const text = fs.readFileSync(path.join(PLUGIN, "LICENSE"), "utf8");
  assert.match(text, /GNU GENERAL PUBLIC LICENSE/);
  assert.match(text, /Version 3, 29 June 2007/);
  assert.ok(text.length > 30000);
  for (const file of files(PLUGIN, [".js", ".py", ".css", ".html"])) {
    assert.match(fs.readFileSync(file, "utf8").slice(0, 400), /SPDX-License-Identifier: GPL-3\.0-or-later/, file);
  }
});

test("the build makes the .ccx the installer takes", () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), "nolgia-build-"));
  try {
    const release = execFileSync("python3", [path.join(PLUGIN, "build.py"), "--out", out], { encoding: "utf8" }).trim();
    assert.equal(path.basename(release), "nolgia-photoshop-" + PLUGIN_VERSION + ".ccx");
    const listing = execFileSync("python3", ["-c", "import sys, zipfile; print('\\n'.join(zipfile.ZipFile(sys.argv[1]).namelist()))", release], { encoding: "utf8" })
      .trim()
      .split("\n");
    for (const needed of ["manifest.json", "index.html", "main.js", "styles.css", "LICENSE", "core/worker.js", "ps/ops.js", "ui/panel.js", "icons/plugin@1x.png"]) {
      assert.ok(listing.includes(needed), needed + " is not in the package");
    }
    assert.ok(!listing.some((n) => n.startsWith("tests/") || n === "build.py"), "tests or the build script were packaged");
    const packed = JSON.parse(execFileSync("python3", ["-c", "import sys, zipfile; print(zipfile.ZipFile(sys.argv[1]).read('manifest.json').decode())", release], { encoding: "utf8" }));
    assert.deepEqual(packed, manifest);
    const dev = execFileSync("python3", [path.join(PLUGIN, "build.py"), "--out", out, "--dev-domain", "http://localhost:8791"], { encoding: "utf8" }).trim();
    assert.equal(path.basename(dev), "nolgia-photoshop-" + PLUGIN_VERSION + "-dev.ccx");
    const devManifest = JSON.parse(execFileSync("python3", ["-c", "import sys, zipfile; print(zipfile.ZipFile(sys.argv[1]).read('manifest.json').decode())", dev], { encoding: "utf8" }));
    assert.ok(devManifest.requiredPermissions.network.domains.includes("http://localhost:8791"));
    assert.deepEqual(devManifest.requiredPermissions.ipc, { enablePluginCommunication: true });
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }
});

const DASHES = new RegExp("[" + String.fromCharCode(0x2013) + String.fromCharCode(0x2014) + "]");

test("no em or en dashes in the plugin or its workflows", () => {
  const all = files(PLUGIN, [".js", ".py", ".css", ".html", ".json", ".md"]).concat(files(path.join(REPO, "presets", "photoshop"), [".md"]));
  for (const file of all) {
    fs.readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => assert.doesNotMatch(line, DASHES, file + ":" + (i + 1) + " has an em or en dash"));
  }
});

test("NOLGIA is in capitals in text people read", () => {
  // Lower case is fine inside identifiers and file names: __nolgia,
  // com.nolgia.photoshop, nolgia-dev.json, nolgia.settings, NOLGIA_...
  const texts = [path.join(PLUGIN, "index.html"), path.join(PLUGIN, "manifest.json")]
    .concat(files(path.join(PLUGIN, "ui"), [".js"]))
    .concat(files(path.join(PLUGIN, "ps"), [".js"]))
    .concat(files(path.join(PLUGIN, "core"), [".js"]))
    .concat([path.join(PLUGIN, "main.js")])
    .concat(files(path.join(REPO, "presets", "photoshop"), [".md"]));
  for (const file of texts) {
    let text = fs.readFileSync(file, "utf8");
    if (file.endsWith(".js")) {
      // In code only the strings are text people read; comments and names are not.
      text = (text.match(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g) || []).join("\n");
    }
    for (const m of text.matchAll(/nolgia/gi)) {
      if (m[0] === "NOLGIA") continue;
      const before = text[m.index - 1] || " ";
      const after = text[m.index + m[0].length] || " ";
      const identifier = /[._\-/\\a-zA-Z0-9$`"']/;
      if (m[0] === "nolgia" && (identifier.test(before) || identifier.test(after))) continue;
      assert.fail(file + ": " + JSON.stringify(text.slice(Math.max(0, m.index - 20), m.index + 26)) + " should say NOLGIA");
    }
  }
});

test("every Photoshop workflow has the Film assistant front matter", () => {
  const dir = path.join(REPO, "presets", "photoshop");
  const workflows = fs.readdirSync(dir).filter((n) => n.endsWith(".md") && !n.startsWith("_"));
  assert.ok(workflows.length >= 1);
  for (const name of workflows) {
    const text = fs.readFileSync(path.join(dir, name), "utf8");
    const m = /^---\n([\s\S]*?)\n---\n/.exec(text);
    assert.ok(m, name + " has no front matter");
    const fields = Object.fromEntries(m[1].split("\n").map((l) => [l.slice(0, l.indexOf(":")), l.slice(l.indexOf(":") + 1).trim()]));
    assert.equal(fields.slug, "photoshop-" + name.replace(/\.md$/, ""));
    assert.equal(fields.category, "Film assistant");
    assert.equal(fields.app, "photoshop");
    assert.match(fields.min_app_version, /^"\d+(\.\d+)*"$/);
    assert.match(fields.starter_prompt, /^"Use NOLGIA to /);
    assert.ok(fields.name.length > 5);
    for (const section of ["## Ask", "## Build", "## Check", "## Deliver"]) assert.ok(text.includes("\n" + section + "\n"), name + " lacks " + section);
    assert.ok(Buffer.byteLength(text) < 32 * 1024, name + " is over the 32 KB the API takes");
  }
  const common = fs.readFileSync(path.join(dir, "_common.md"), "utf8");
  assert.ok(Buffer.byteLength(common) < 16 * 1024);
});
