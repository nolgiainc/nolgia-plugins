// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// The NOLGIA panel: shows the controller's state and turns clicks into
// controller calls. Plain DOM, so it works in UXP without a framework.
"use strict";

const { ActivityLog } = require("../core/commands.js");

const CHIP_TEXT = {
  off: "Off",
  connecting: "Connecting",
  connected: "Connected",
  retrying: "Retrying",
  signed_out: "Signed out",
  approval: "Needs you",
};

const MAX_CODE_CHARS = 20000;

function pad(n) {
  return String(n).padStart(2, "0");
}

function clock(seconds) {
  const d = new Date(seconds * 1000);
  return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}

/** classList.toggle with a force flag, spelled out for older UXP. */
function setClass(el, name, on) {
  if (!el) return;
  if (on) el.classList.add(name);
  else el.classList.remove(name);
}

function createPanel(controller, doc) {
  const $ = (id) => doc.getElementById(id);
  const show = (id, visible) => {
    const el = $(id);
    setClass(el, "hidden", !visible);
  };
  const on = (id, fn) => {
    const el = $(id);
    if (el) {
      el.addEventListener("click", () => {
        try {
          const out = fn();
          if (out && typeof out.catch === "function") out.catch((err) => controller.log("Panel action failed: " + err));
        } catch (err) {
          controller.log("Panel action failed: " + err);
        }
      });
    }
  };

  on("btn-sign-in", () => controller.signIn());
  on("btn-cancel-sign-in", () => controller.cancelSignIn());
  on("btn-open-page", () => controller.login && controller.login.prompt && controller.host.openUrl(controller.login.prompt.openUrl));
  on("btn-copy-code", () => controller.login && controller.login.prompt && controller.host.copyText(controller.login.prompt.userCode));
  on("btn-sign-out", () => controller.signOut());
  on("btn-pause", () => controller.togglePause());
  on("tg-connected", () => (controller.connected ? controller.disconnect() : controller.connect()));
  on("tg-allow-agent", () => controller.setSetting("allow_agent", !controller.settings.allow_agent));
  on("tg-ask", () => controller.setSetting("ask_before_run", !controller.settings.ask_before_run));
  on("btn-approve", () => {
    const pending = controller.executor.approvals[0];
    if (pending) controller.approve(pending.id);
  });
  on("btn-deny", () => {
    const pending = controller.executor.approvals[0];
    if (pending) controller.deny(pending.id);
  });

  let shownCode = null;

  function render() {
    const state = controller.statusState();
    const dot = $("chip-dot");
    dot.className = "dot " + state;
    $("chip-text").textContent = CHIP_TEXT[state] || state;
    $("status").textContent = controller.statusLine();

    const login = controller.login;
    const signedIn = controller.signedIn;
    show("signin", !login && !signedIn);
    show("login", Boolean(login));
    if (login) $("login-code").textContent = login.prompt ? login.prompt.userCode : "....-....";
    show("btn-open-page", Boolean(login && login.prompt));
    show("btn-copy-code", Boolean(login && login.prompt));

    show("account", !login && signedIn);
    if (signedIn) {
      const email = controller.accountEmail();
      $("account-line").textContent = email
        ? "Signed in as " + email
        : controller.usingDevToken
          ? "Signed in with a developer token file"
          : "Signed in";
    }
    setClass($("tg-connected"), "on", controller.connected);
    setClass($("tg-allow-agent"), "on", Boolean(controller.settings.allow_agent));
    setClass($("tg-ask"), "on", Boolean(controller.settings.ask_before_run));

    const pending = controller.executor.approvals[0];
    show("approval", Boolean(pending));
    if (pending) {
      $("approval-title").textContent = pending.request.title;
      $("btn-approve").textContent = pending.request.approveLabel;
      if (shownCode !== pending.id) {
        const code = pending.request.code || pending.request.lines.join("\n");
        $("approval-code").textContent = code.length > MAX_CODE_CHARS ? code.slice(0, MAX_CODE_CHARS) + "\n..." : code;
        shownCode = pending.id;
      }
    } else {
      shownCode = null;
    }

    show("btn-pause", signedIn);
    $("btn-pause").textContent = controller.connected ? "Pause" : "Resume";
    const list = $("activity-list");
    while (list.firstChild) list.removeChild(list.firstChild);
    const items = controller.activity.items();
    if (!items.length) {
      const empty = doc.createElement("div");
      empty.className = "empty";
      empty.textContent = "Nothing yet.";
      list.appendChild(empty);
    }
    for (const item of items) {
      const row = doc.createElement("div");
      row.className = "item";
      if (item.detail && item.status !== "succeeded") row.title = item.detail;
      const time = doc.createElement("div");
      time.className = "time";
      time.textContent = clock(item.time);
      const kind = doc.createElement("div");
      kind.className = "kind";
      kind.textContent = item.kind + (item.caller === "agent" ? "  (NOLGIA Agent)" : "");
      const status = doc.createElement("div");
      status.className = "state " + item.status;
      status.textContent = ActivityLog.STATUS_LABELS[item.status] || item.status;
      row.appendChild(time);
      row.appendChild(kind);
      row.appendChild(status);
      list.appendChild(row);
    }

    const dev = controller.dev;
    $("dev-line").textContent = dev ? "Developer file in use: " + controller.apiUrl : "";
    show("dev-line", Boolean(dev));
  }

  let queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    setTimeout(() => {
      queued = false;
      try {
        render();
      } catch (err) {
        controller.log("Could not draw the panel: " + err);
      }
    }, 30);
  }

  controller.onChange(schedule);
  render();
  return { render, schedule };
}

module.exports = { createPanel, CHIP_TEXT };
