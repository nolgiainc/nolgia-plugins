// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 NOLGIA Inc
//
// HTTP client for the NOLGIA API, on fetch (UXP's or Node's).
//
// The bearer token is sent only to the API itself, never to the signed
// storage URLs that uploads and downloads go through.
"use strict";

const { PLUGIN_VERSION, DEFAULT_API_URL } = require("./constants.js");

/** The API answered with an error status. */
class ApiError extends Error {
  constructor(status, { title = "", detail = "", code = "", retryAfter = null, body = null } = {}) {
    const text = detail || title || "no details";
    super("NOLGIA answered " + status + ": " + text);
    this.name = "ApiError";
    this.status = status;
    this.title = title || "";
    this.detail = detail || "";
    this.code = code || "";
    this.retryAfter = retryAfter;
    this.body = body;
  }
}

/** The token is missing, expired or revoked (401). */
class Unauthorized extends ApiError {
  constructor(status, fields) {
    super(status, fields);
    this.name = "Unauthorized";
  }
}

/** NOLGIA could not be reached (DNS, TCP, TLS or a timeout). */
class NetworkError extends Error {
  constructor(message) {
    super(message);
    this.name = "NetworkError";
  }
}

function normalizeBaseUrl(url) {
  let out = String(url || DEFAULT_API_URL).trim().replace(/\/+$/, "");
  if (!out.endsWith("/v1")) out += "/v1";
  return out;
}

function retryAfter(headers) {
  if (!headers || typeof headers.get !== "function") return null;
  const value = Number(headers.get("Retry-After"));
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function query(params) {
  const parts = [];
  for (const key of Object.keys(params || {})) {
    parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(params[key])));
  }
  return parts.length ? "?" + parts.join("&") : "";
}

function decodeText(buffer) {
  if (!buffer) return "";
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (typeof TextDecoder !== "undefined") return new TextDecoder("utf-8").decode(bytes);
  let out = "";
  for (let i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
  try {
    return decodeURIComponent(escape(out));
  } catch (err) {
    return out;
  }
}

class Response {
  constructor(status, headers, body) {
    this.status = status;
    this.headers = headers;
    this.body = body; // text
  }

  json() {
    if (!this.body) return null;
    return JSON.parse(this.body);
  }
}

function apiError(status, text, headers) {
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    data = {};
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) data = {};
  const title = String(data.title || "");
  // The device token poll carries its OAuth error in `title` (RFC 7807);
  // other endpoints use `code`. Accept `error` too, like the CLI.
  let code = String(data.code || data.error || "");
  if (!code && title && !title.includes(" ") && title.toLowerCase() === title) code = title;
  const Cls = status === 401 ? Unauthorized : ApiError;
  return new Cls(status, {
    title,
    detail: String(data.detail || data.error_description || ""),
    code,
    retryAfter: retryAfter(headers),
    body: data,
  });
}

/** The body as an ArrayBuffer. UXP's fetch throws "Already read" when asked
 *  for the body of an empty response (a 204, or a PUT to storage), so an
 *  empty body is never read. */
async function readBody(resp) {
  const length = resp.headers && typeof resp.headers.get === "function" ? resp.headers.get("content-length") : null;
  if (resp.status === 204 || resp.status === 205 || length === "0") return new ArrayBuffer(0);
  try {
    return await resp.arrayBuffer();
  } catch (err) {
    if (/already read/i.test(String((err && err.message) || err))) return new ArrayBuffer(0);
    throw err;
  }
}

function networkReason(err) {
  if (!err) return "unknown error";
  if (err.name === "AbortError") return "timed out";
  return String(err.message || err) || err.name || "unknown error";
}

class ApiClient {
  constructor({ baseUrl, token = null, fetch: fetchImpl, userAgent, timeout = 30 } = {}) {
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.token = token;
    this.timeout = timeout; // seconds
    this.userAgent = userAgent || "nolgia-photoshop/" + PLUGIN_VERSION;
    this._fetch = fetchImpl || (typeof fetch !== "undefined" ? fetch.bind(globalThis) : null);
    if (!this._fetch) throw new Error("fetch is not available");
  }

  /** fetch with a time limit. Throws NetworkError on any transport failure.
   *  `signal` (an AbortSignal) stops the request early. */
  async _send(url, init, seconds, signal) {
    const hasAbort = typeof AbortController !== "undefined";
    const controller = hasAbort ? new AbortController() : null;
    let timer = null;
    let stopped = null;
    const onAbort = () => {
      if (controller) {
        try {
          controller.abort();
        } catch (err) {
          // ignore
        }
      }
      if (stopped) stopped(new NetworkError("stopped"));
    };
    const cancelled = new Promise((resolve, reject) => {
      stopped = reject;
    });
    cancelled.catch(() => {});
    if (signal) {
      if (signal.aborted) throw new NetworkError("stopped");
      signal.addEventListener("abort", onAbort);
    }
    const limit = new Promise((resolve, reject) => {
      timer = setTimeout(() => {
        if (controller) {
          try {
            controller.abort();
          } catch (err) {
            // ignore
          }
        }
        reject(new NetworkError("timed out after " + Math.round(seconds) + " s"));
      }, seconds * 1000);
    });
    try {
      const req = this._fetch(url, controller ? Object.assign({}, init, { signal: controller.signal }) : init);
      const resp = await Promise.race([req, limit, cancelled]);
      const buffer = await Promise.race([readBody(resp), limit, cancelled]);
      return { resp, buffer };
    } catch (err) {
      if (err instanceof NetworkError) throw err;
      if (signal && signal.aborted) throw new NetworkError("stopped");
      throw new NetworkError(networkReason(err));
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", onAbort);
    }
  }

  /** Send a JSON request to the API. Throws Unauthorized on 401, ApiError on
   *  other 4xx/5xx and NetworkError when the API cannot be reached. */
  async request(method, path, { body, query: params, timeout, auth = true, signal = null } = {}) {
    const url = this.baseUrl + path + query(params);
    const headers = { Accept: "application/json" };
    let data;
    if (body !== undefined && body !== null) {
      data = JSON.stringify(body);
      headers["Content-Type"] = "application/json";
    } else if (method === "POST" || method === "PUT" || method === "PATCH") {
      data = ""; // Content-Length: 0; Google's front end refuses a POST without it
    }
    if (auth && this.token) headers.Authorization = "Bearer " + this.token;
    const { resp, buffer } = await this._send(url, { method, headers, body: data }, timeout || this.timeout, signal);
    const text = decodeText(buffer);
    if (resp.status >= 400) throw apiError(resp.status, text, resp.headers);
    return new Response(resp.status, resp.headers, text);
  }

  // ------------------------------------------------------------ device login

  async startDeviceAuth(clientId, scope) {
    const body = { client_id: clientId };
    if (scope) body.scope = scope;
    return (await this.request("POST", "/auth/device", { body, auth: false })).json();
  }

  /** One poll. Returns the token response, or throws ApiError whose `code`
   *  is the OAuth error (authorization_pending, slow_down, ...). */
  async pollDeviceToken(clientId, deviceCode) {
    const body = { client_id: clientId, device_code: deviceCode };
    return (await this.request("POST", "/auth/device/token", { body, auth: false })).json();
  }

  /** GET /me: the signed-in user ({id, email, ...}). */
  async getMe() {
    return (await this.request("GET", "/me")).json() || {};
  }

  // ------------------------------------------------------------------ bridge

  async registerSession(payload) {
    return (await this.request("POST", "/bridge/sessions", { body: payload })).json() || {};
  }

  async deleteSession(sessionId, timeout = 5) {
    await this.request("DELETE", "/bridge/sessions/" + encodeURIComponent(sessionId), { timeout });
  }

  /** Long poll. Returns the command, or null on 204. 404 (unknown session)
   *  and 409 session_disconnected throw ApiError: both mean register again.
   *  `signal` ends the wait early (switching off), so a poll left waiting
   *  cannot take a command meant for the next session. */
  async nextCommand(sessionId, wait = 25, signal = null) {
    wait = Math.max(0, Math.min(25, Math.floor(Number(wait) || 0)));
    const resp = await this.request("GET", "/bridge/sessions/" + encodeURIComponent(sessionId) + "/next", {
      query: { wait },
      timeout: wait + 20,
      signal,
    });
    if (resp.status === 204 || !resp.body) return null;
    const data = resp.json() || {};
    const command = data.command && typeof data.command === "object" ? data.command : data;
    return command && command.id ? command : null;
  }

  async postResult(commandId, body) {
    return this.request("POST", "/bridge/commands/" + encodeURIComponent(commandId) + "/result", { body });
  }

  // ------------------------------------------------------------------ assets

  async getAsset(assetId) {
    return (await this.request("GET", "/assets/" + encodeURIComponent(assetId))).json();
  }

  /** Upload bytes (Uint8Array or ArrayBuffer) as a NOLGIA asset. Returns the
   *  completed asset. */
  async uploadBytes(bytes, contentType, { filename, displayName, tags } = {}) {
    const size = bytes.byteLength;
    const body = { filename: filename || "upload", content_type: contentType, size_bytes: size };
    if (displayName) body.display_name = String(displayName).slice(0, 200);
    if (tags && tags.length) body.tags = tags;
    const slot = (await this.request("POST", "/assets/uploads", { body })).json();
    await this._put(slot.upload_url, bytes, contentType);
    return (await this.request("POST", "/assets/uploads/" + encodeURIComponent(slot.upload_id) + "/complete")).json();
  }

  async _put(url, bytes, contentType) {
    // No Authorization header: signed URLs refuse one.
    const { resp, buffer } = await this._send(
      url,
      { method: "PUT", headers: { "Content-Type": contentType }, body: bytes },
      Math.max(this.timeout, 300),
    );
    if (resp.status >= 400) {
      throw new ApiError(resp.status, { title: "Upload failed", detail: decodeText(buffer).slice(0, 500) });
    }
  }

  /** Download a signed URL. Returns a Uint8Array. */
  async download(url, maxBytes) {
    const { resp, buffer } = await this._send(url, { method: "GET" }, Math.max(this.timeout, 300));
    if (resp.status >= 400) {
      throw new ApiError(resp.status, { title: "Download failed", detail: decodeText(buffer).slice(0, 500) });
    }
    if (maxBytes && buffer.byteLength > maxBytes) {
      throw new ApiError(413, { title: "Too large", detail: "The file is too large to import." });
    }
    return new Uint8Array(buffer);
  }
}

module.exports = { ApiClient, ApiError, Unauthorized, NetworkError, normalizeBaseUrl, decodeText };
