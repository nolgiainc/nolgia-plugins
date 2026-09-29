// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const util = require("../core/util.js");
const pathutil = require("../core/pathutil.js");

test("parseTime reads Go's RFC 3339 with up to nine fraction digits and offsets", () => {
  assert.equal(util.parseTime("2026-09-29T20:05:45Z"), Date.UTC(2026, 8, 29, 20, 5, 45));
  assert.equal(util.parseTime("2026-09-29T20:05:45.123456789Z"), Date.UTC(2026, 8, 29, 20, 5, 45, 123));
  assert.equal(util.parseTime("2026-09-29T22:05:45+02:00"), Date.UTC(2026, 8, 29, 20, 5, 45));
  assert.equal(util.parseTime("2026-09-29T15:05:45-0500"), Date.UTC(2026, 8, 29, 20, 5, 45));
  assert.equal(util.parseTime("yesterday"), null);
  assert.equal(util.parseTime(null), null);
});

test("Backoff doubles up to the cap with jitter in [d/2, d]", () => {
  const b = new util.Backoff(1, 8, () => 1);
  assert.deepEqual([b.next(), b.next(), b.next(), b.next(), b.next()], [1, 2, 4, 8, 8]);
  b.reset();
  assert.equal(b.next(), 1);
  const low = new util.Backoff(1, 8, () => 0);
  assert.equal(low.next(), 0.5);
});

test("cutText keeps the tail or the head and says how much it cut", () => {
  assert.equal(util.cutText("abcdef", 10), "abcdef");
  assert.equal(util.cutText("abcdef", 2), "[4 characters cut]\nef");
  assert.equal(util.cutText("abcdef", 2, "head"), "ab\n[4 more characters cut]");
  assert.equal(util.cutText(null, 2), "");
});

test("goJsonSize counts UTF-8 bytes and Go's escaping of < > &", () => {
  assert.equal(util.goJsonSize({ a: "x" }), '{"a":"x"}'.length);
  assert.equal(util.goJsonSize({ a: "<&>" }), '{"a":"<&>"}'.length + 15);
  assert.equal(util.goJsonSize({ a: "é" }), '{"a":""}'.length + 2);
  assert.equal(util.goJsonSize({ a: "😀" }), '{"a":""}'.length + 4);
});

test("sniffExtension and importExtension pick what Photoshop can open", () => {
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0]);
  const jpg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
  const psd = new Uint8Array([0x38, 0x42, 0x50, 0x53, 0, 1]);
  const glb = new Uint8Array([0x67, 0x6c, 0x54, 0x46, 2, 0, 0, 0]);
  assert.equal(util.sniffExtension(png), ".png");
  assert.equal(util.sniffExtension(jpg), ".jpg");
  assert.equal(util.sniffExtension(psd), ".psd");
  assert.equal(util.importExtension("plate.jpg", "image/jpeg", png), ".png", "the bytes win over the name");
  assert.equal(util.importExtension("plate", "image/webp", new Uint8Array([])), ".webp");
  assert.equal(util.importExtension("model.glb", "model/gltf-binary", glb), "");
  assert.equal(util.otherKind("model.glb", "model/gltf-binary"), "model");
  assert.equal(util.otherKind("clip", "video/mp4"), "video");
  assert.equal(util.otherKind("plate.png", "image/png"), "");
});

test("safeFilename and stem", () => {
  assert.equal(util.safeFilename("C:\\x\\shot: final?.psd"), "shot_ final_.psd");
  assert.equal(util.safeFilename("..."), "file");
  assert.equal(util.stem("street plate.png"), "street plate");
  assert.equal(util.stem("no-extension"), "no-extension");
  assert.equal(util.extname("A.PSD"), ".psd");
});

test("Signal wakes waiters and times out", async () => {
  const s = new util.Signal();
  assert.equal(await s.wait(10), false);
  setTimeout(() => s.set(), 10);
  assert.equal(await s.wait(1000), true);
  assert.equal(await s.wait(0), true, "already set");
  s.clear();
  assert.equal(s.isSet, false);
});

test("pathutil handles Windows and macOS paths", () => {
  assert.equal(pathutil.normalize("C:/Users/me/../you/./a.psd"), "C:\\Users\\you\\a.psd");
  assert.equal(pathutil.normalize("/Users/me/../you/a.psd"), "/Users/you/a.psd");
  assert.equal(pathutil.dirname("C:\\Proj\\shot.psd"), "C:\\Proj");
  assert.equal(pathutil.dirname("C:\\shot.psd"), "C:\\");
  assert.equal(pathutil.basename("/Users/me/shot.psd"), "shot.psd");
  assert.equal(pathutil.join("C:\\Users\\me\\Documents", "NOLGIA exports"), "C:\\Users\\me\\Documents\\NOLGIA exports");
  assert.equal(pathutil.resolve("sub/x.psd", "C:\\Proj\\shot.psd"), "C:\\Proj\\sub\\x.psd");
  assert.equal(pathutil.resolve("~/Desktop/x.psd", null, "/Users/me"), "/Users/me/Desktop/x.psd");
  assert.throws(() => pathutil.resolve("x.psd", null), /full path/);
  assert.equal(pathutil.toFileUrl("C:\\Users\\me\\a b.psd"), "file:/C:/Users/me/a b.psd");
  assert.equal(pathutil.toFileUrl("/Users/me/a.psd"), "file:/Users/me/a.psd");
});
