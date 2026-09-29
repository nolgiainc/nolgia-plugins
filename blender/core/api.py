# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""HTTP client for the NOLGIA API (urllib only, safe to use from any thread).

The bearer token is sent only to the API itself, never to the signed storage
URLs that uploads and downloads go through.
"""

import json
import os
import shutil
import ssl
import urllib.error
import urllib.parse
import urllib.request

from . import PLUGIN_VERSION, DEFAULT_API_URL


class ApiError(Exception):
    """The API answered with an error status."""

    def __init__(self, status, title="", detail="", code="", retry_after=None, body=None):
        self.status = status
        self.title = title or ""
        self.detail = detail or ""
        self.code = code or ""
        self.retry_after = retry_after
        self.body = body
        super().__init__(self.message())

    def message(self):
        text = self.detail or self.title or "no details"
        return "NOLGIA answered %d: %s" % (self.status, text)


class Unauthorized(ApiError):
    """The token is missing, expired or revoked (401)."""


class NetworkError(Exception):
    """NOLGIA could not be reached (DNS, TCP, TLS or a timeout)."""


def normalize_base_url(url):
    url = (url or DEFAULT_API_URL).strip().rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def base_url_from_env():
    return normalize_base_url(os.environ.get("NOLGIA_API_URL") or DEFAULT_API_URL)


def _ssl_context():
    try:
        import certifi  # Blender bundles it; plain Python may not.

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _retry_after(headers):
    value = headers.get("Retry-After") if headers is not None else None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


class ApiClient:
    def __init__(self, base_url=None, token=None, user_agent=None, timeout=30.0):
        self.base_url = normalize_base_url(base_url) if base_url else base_url_from_env()
        self.token = token
        self.timeout = timeout
        self.user_agent = user_agent or "nolgia-blender/%s" % PLUGIN_VERSION
        handlers = []
        if self.base_url.startswith("https:"):
            handlers.append(urllib.request.HTTPSHandler(context=_ssl_context()))
        self._opener = urllib.request.build_opener(*handlers)
        self._storage_opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context())
        )

    # ------------------------------------------------------------------ core

    def request(self, method, path, body=None, query=None, timeout=None, auth=True):
        """Send a JSON request to the API and return a Response.

        Raises Unauthorized on 401, ApiError on other 4xx/5xx and
        NetworkError when the API cannot be reached.
        """
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif method in ("POST", "PUT", "PATCH"):
            data = b""  # Content-Length: 0; Google's front end refuses a POST without it
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                return Response(resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as err:
            raise _api_error(err) from None
        except (urllib.error.URLError, OSError, ValueError) as err:
            raise NetworkError(_network_reason(err)) from None

    # --------------------------------------------------------- device login

    def start_device_auth(self, client_id, scope=None):
        body = {"client_id": client_id}
        if scope:
            body["scope"] = scope
        return self.request("POST", "/auth/device", body, auth=False).json()

    def poll_device_token(self, client_id, device_code):
        """One poll. Returns the token response, or raises ApiError whose
        `code` is the OAuth error (authorization_pending, slow_down, ...)."""
        return self.request(
            "POST",
            "/auth/device/token",
            {"client_id": client_id, "device_code": device_code},
            auth=False,
        ).json()

    def get_me(self):
        """GET /me: the signed-in user ({id, email, name?, ...})."""
        return self.request("GET", "/me").json() or {}

    # --------------------------------------------------------------- bridge

    def register_session(self, payload):
        return self.request("POST", "/bridge/sessions", payload).json() or {}

    def delete_session(self, session_id, timeout=5.0):
        self.request(
            "DELETE", "/bridge/sessions/%s" % urllib.parse.quote(session_id), timeout=timeout
        )

    def next_command(self, session_id, wait=25):
        """Long poll. Returns the command (the API answers it bare), or None
        on 204. 404 (unknown session) and 409 session_disconnected raise
        ApiError: both mean register again."""
        wait = max(0, min(25, int(wait)))
        resp = self.request(
            "GET",
            "/bridge/sessions/%s/next" % urllib.parse.quote(session_id),
            query={"wait": wait},
            timeout=wait + 20,
        )
        if resp.status == 204 or not resp.body:
            return None
        data = resp.json() or {}
        command = data.get("command") if isinstance(data.get("command"), dict) else data
        return command if command.get("id") else None

    def post_result(self, command_id, body):
        return self.request(
            "POST", "/bridge/commands/%s/result" % urllib.parse.quote(command_id), body
        )

    # --------------------------------------------------------------- assets

    def get_asset(self, asset_id):
        return self.request("GET", "/assets/%s" % urllib.parse.quote(asset_id)).json()

    def upload_file(self, path, content_type, display_name=None, tags=None, filename=None):
        """Upload a file as a NOLGIA asset. Returns the completed asset."""
        size = os.path.getsize(path)
        body = {
            "filename": filename or os.path.basename(path),
            "content_type": content_type,
            "size_bytes": size,
        }
        if display_name:
            body["display_name"] = display_name[:200]
        if tags:
            body["tags"] = tags
        slot = self.request("POST", "/assets/uploads", body).json()
        self._put_file(slot["upload_url"], path, content_type, size)
        return self.request(
            "POST", "/assets/uploads/%s/complete" % urllib.parse.quote(slot["upload_id"])
        ).json()

    def _put_file(self, url, path, content_type, size):
        headers = {"Content-Type": content_type, "Content-Length": str(size)}
        with open(path, "rb") as handle:
            req = urllib.request.Request(url, data=handle, method="PUT", headers=headers)
            try:
                with self._storage_opener.open(req, timeout=max(self.timeout, 300)) as resp:
                    resp.read()
            except urllib.error.HTTPError as err:
                raise ApiError(err.code, "Upload failed", _read_text(err)) from None
            except (urllib.error.URLError, OSError, ValueError) as err:
                raise NetworkError(_network_reason(err)) from None

    def download(self, url, dest_path, max_bytes=None):
        """Download a signed URL to dest_path. Returns the first bytes."""
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        tmp = dest_path + ".part"
        try:
            with self._storage_opener.open(req, timeout=max(self.timeout, 300)) as resp:
                with open(tmp, "wb") as out:
                    copied = 0
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if max_bytes and copied > max_bytes:
                            raise ApiError(413, "Too large", "The file is too large to import.")
                        out.write(chunk)
            shutil.move(tmp, dest_path)
        except urllib.error.HTTPError as err:
            raise ApiError(err.code, "Download failed", _read_text(err)) from None
        except (urllib.error.URLError, OSError, ValueError) as err:
            raise NetworkError(_network_reason(err)) from None
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        with open(dest_path, "rb") as handle:
            return handle.read(64)


def _read_text(err):
    try:
        return err.read().decode("utf-8", "replace")[:500]
    except Exception:
        return ""


def _network_reason(err):
    reason = getattr(err, "reason", None) or err
    return str(reason) or err.__class__.__name__


def _api_error(err):
    raw = b""
    try:
        raw = err.read()
    except Exception:
        pass
    data = {}
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    title = str(data.get("title") or "")
    # The device token poll carries its OAuth error in `title` (RFC 7807);
    # other endpoints use `code`. Accept `error` too, like the CLI.
    code = str(data.get("code") or data.get("error") or "")
    if not code and title and " " not in title and title.lower() == title:
        code = title
    cls = Unauthorized if err.code == 401 else ApiError
    return cls(
        err.code,
        title=title,
        detail=str(data.get("detail") or data.get("error_description") or ""),
        code=code,
        retry_after=_retry_after(err.headers),
        body=data,
    )
