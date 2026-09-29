// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// The approval window: when "Ask before running code" is on and code comes
// in, a window shows the code with Run code and Deny, even while the NOLGIA
// panel is closed. The same request also waits in the panel; answering in
// either place closes the window. Closing the window without choosing leaves
// the request waiting in the panel.
//
// One <dialog> element is kept for the plugin's lifetime and filled for each
// request: UXP complains when a dialog node is removed while it closes.
"use strict";

const MAX_CODE_CHARS = 20000;

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function createApprovalWindow(doc, log = () => {}) {
  let parts = null;
  let current = null; // id of the request the window shows
  const shown = new Set();

  function build() {
    const dialog = el(doc, "dialog");
    const body = el(doc, "div", "dialog-body");
    body.appendChild(el(doc, "div", "brand", "NOLGIA"));
    const title = el(doc, "div", "approval-title");
    const code = el(doc, "div", "code-view");
    body.appendChild(title);
    body.appendChild(code);
    body.appendChild(el(doc, "div", "hint", "Code runs on this computer with your permissions. Closing this window leaves the request waiting in the NOLGIA panel."));
    const row = el(doc, "div", "row");
    const approve = el(doc, "div", "btn primary", "Run code");
    const deny = el(doc, "div", "btn", "Deny");
    approve.addEventListener("click", () => dialog.close("approve"));
    deny.addEventListener("click", () => dialog.close("deny"));
    row.appendChild(approve);
    row.appendChild(deny);
    body.appendChild(row);
    dialog.appendChild(body);
    doc.body.appendChild(dialog);
    parts = { dialog, title, code, approve };
  }

  function close() {
    if (current === null || !parts) return;
    try {
      parts.dialog.close("gone");
    } catch (err) {
      // already closed
    }
  }

  function showFor(pending, controller) {
    if (!parts) build();
    const code = pending.request.code || pending.request.lines.join("\n");
    parts.title.textContent = pending.request.title;
    parts.code.textContent = code.length > MAX_CODE_CHARS ? code.slice(0, MAX_CODE_CHARS) + "\n..." : code;
    parts.approve.textContent = pending.request.approveLabel;
    const id = pending.id;
    current = id;
    const settle = (answer) => {
      log("The approval window closed (" + answer + ").");
      if (current === id) current = null;
      if (answer === "approve") controller.approve(id);
      else if (answer === "deny") controller.deny(id);
      update(controller.executor.approvals, controller); // the next request, if one waits
    };
    let shownModal;
    try {
      shownModal = parts.dialog.uxpShowModal({ title: "NOLGIA", resize: "both", size: { width: 600, height: 480 } });
    } catch (err) {
      current = null;
      throw err;
    }
    log("The approval window is open.");
    Promise.resolve(shownModal).then(settle, (err) => {
      log("The approval window failed: " + ((err && err.message) || err));
      settle(null);
    });
  }

  /** Called whenever the waiting requests change. */
  function update(approvals, controller) {
    if (current !== null && !approvals.some((p) => p.id === current)) close(); // answered in the panel, or timed out
    if (current !== null) return;
    const next = approvals.find((p) => !shown.has(p.id));
    if (!next) return;
    shown.add(next.id);
    showFor(next, controller);
  }

  return { update, close };
}

module.exports = { createApprovalWindow };
