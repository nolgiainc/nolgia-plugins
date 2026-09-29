// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// Plain JavaScript parts of the NOLGIA plugin for Photoshop.
//
// Nothing in core/ touches Photoshop or UXP, so it runs (and is tested) with
// plain Node. The Photoshop side lives in ps/ and ui/.
"use strict";

module.exports = {
  PLUGIN_VERSION: "0.1.0",
  PLUGIN_ID: "com.nolgia.photoshop",
  APP: "photoshop",
  DEFAULT_API_URL: "https://api.nolgia.ai/v1",
  DEVICE_CLIENT_ID: "nolgia-photoshop",
  DEVICE_SCOPE: "bridge assets:read assets:write",
};
