// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// NOLGIA for Photoshop: let your NOLGIA agent work in your open documents.
//
// The plugin signs in to NOLGIA, connects out to it, and runs the commands
// your agent sends (look at the documents, run UXP JavaScript, preview,
// import and export images, save, open) while you have it switched on.
// Photoshop loads it at startup (manifest loadEvent "startup"), so it
// reconnects on its own when you left Connected on.
"use strict";

const uxp = require("uxp");
const { action } = require("photoshop");
const { PLUGIN_ID, PLUGIN_VERSION } = require("./core/constants.js");
const { Controller } = require("./core/controller.js");
const { createHost } = require("./ps/host.js");
const { createPanel } = require("./ui/panel.js");
const { createApprovalWindow } = require("./ui/approval.js");
const ops = require("./ps/ops.js");

let approvalWindow = null;
let panel = null;

const host = createHost({
  onApprovals(approvals, controller) {
    if (approvalWindow) approvalWindow.update(approvals, controller);
  },
});
const controller = new Controller(host);

function showPanel() {
  try {
    for (const plugin of uxp.pluginManager.plugins) {
      if (plugin.id === PLUGIN_ID) plugin.showPanel("nolgia");
    }
  } catch (err) {
    controller.log("Could not open the NOLGIA panel (" + (err && err.message) + ").");
  }
}

function start() {
  try {
    panel = createPanel(controller, document);
  } catch (err) {
    controller.log("Could not build the panel: " + (err && (err.stack || err)));
  }
  approvalWindow = createApprovalWindow(document, (m) => controller.log(m));
  let snapshotTimer = null;
  const refresh = () => {
    if (controller.worker) controller.updateSnapshot();
  };
  try {
    action.addNotificationListener(["open", "close", "save", "select", "make", "delete", "duplicate"], () => {
      if (snapshotTimer) return;
      snapshotTimer = setTimeout(() => {
        snapshotTimer = null;
        refresh();
      }, 300);
    });
  } catch (err) {
    controller.log("Could not follow document changes (" + (err && err.message) + ").");
  }
  setInterval(refresh, 2000);
  controller
    .init()
    .then(async () => {
      controller.log("NOLGIA for Photoshop " + PLUGIN_VERSION + " is ready.");
      try {
        controller.log("data folder: " + (await host.dataFolderPath()));
      } catch (err) {
        // only for the log
      }
      if (controller.dev && controller.dev.show_panel) showPanel();
    })
    .catch((err) => controller.log("NOLGIA could not start: " + (err && (err.stack || err))));
}

try {
  uxp.entrypoints.setup({
    plugin: {
      // UXP wants both create and destroy.
      create() {},
      destroy() {
        controller.log("Photoshop is closing the plugin.");
        return controller.shutdown();
      },
    },
    panels: {
      nolgia: {
        show() {
          if (panel) panel.render();
        },
      },
    },
  });
} catch (err) {
  controller.log("Could not set up the entry points: " + (err && (err.stack || err)));
}

// For the end-to-end tests and for debugging from the UXP Developer Tool.
Object.defineProperty(globalThis, "__nolgia", { value: { controller, ops, host, showPanel }, enumerable: false });

start();
