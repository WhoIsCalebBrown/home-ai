#!/usr/bin/env python3
"""Small, allowlisted read-only relay for the Hermes container.

This module intentionally uses only the Python standard library.  It is a
separate trust boundary: callers can select three fixed read operations, but
cannot select arbitrary Tools capabilities, URLs, headers, or arguments.
"""
from __future__ import annotations

import argparse
import hmac
import http.client
import ipaddress
import json
import os
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

MAX_BODY = 8192
MAX_RESPONSE = 256 * 1024
UPSTREAM_TIMEOUT = 8
DEFAULT_BIND = "172.17.0.1"
DEFAULT_UPSTREAM = "http://172.23.0.9:8090"
RELAY_TOKEN_HEADER = "X-Hermes-Relay-Token"
UPSTREAM_TOKEN_HEADER = "X-Home-AI-Tools-Token"
ROUTES = {
    "/media/status": ("media_status", {"workflow_id", "query", "title", "media_type"}),
    "/media/diagnose": ("media_diagnose", {"workflow_id"}),
    "/media/library": ("plex_library_lookup", {"query", "library"}),
    # Reads the Tower-local Overseerr database (/config/overseerr), not VPS Seerr.
    "/media/requests": ("overseerr_recent_requests", set()),
}


def _read_secret(value: str, path: str) -> str:
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return value.strip()


def _scalar(value: Any) -> Any:
    return value[:1000] if isinstance(value, str) else value if isinstance(value, (str, int, float, bool)) else None


def _project(name: str, value: Any) -> dict[str, Any]:
    """Project each capability through its own reviewed output schema."""
    if not isinstance(value, dict):
        raise ValueError("invalid upstream response")
    if name == "media_status":
        allowed = ("found", "status", "query", "workflow_id", "canonical_state", "storage_class", "status_reason", "last_checked")
        out = {k: _scalar(value[k]) for k in allowed if k in value}
        identity = value.get("canonical_identity")
        if isinstance(identity, dict):
            out["canonical_identity"] = {k: _scalar(identity[k]) for k in ("title", "year", "media_type") if k in identity}
        return out
    if name == "media_diagnose":
        allowed = ("found", "workflow_id", "title", "canonical_state", "diagnosis",
                   "blocking_boundary", "next_read_only_action")
        out = {k: _scalar(value[k]) for k in allowed if k in value}
        identity = value.get("canonical_identity")
        if isinstance(identity, dict):
            out["canonical_identity"] = {k: _scalar(identity[k]) for k in ("title", "year", "media_type") if k in identity}
        return out
    if name == "plex_library_lookup":
        out = {k: _scalar(value[k]) for k in ("available", "source") if k in value}
        matches = value.get("matches", [])
        if not isinstance(matches, list):
            raise ValueError("invalid upstream response")
        out["matches"] = [{k: _scalar(row[k]) for k in ("title", "year", "media_type", "library", "parent_title") if k in row}
                          for row in matches[:20] if isinstance(row, dict)]
        return out
    if name == "overseerr_recent_requests":
        requests = value.get("requests", [])
        if not isinstance(requests, list):
            raise ValueError("invalid upstream response")
        return {"count": _scalar(value.get("count", 0)), "requests": [
            {k: _scalar(row[k]) for k in ("id", "status", "createdAt", "updatedAt", "type", "mediaType", "media_status", "tmdbId", "tvdbId", "imdbId") if k in row}
            for row in requests[:50] if isinstance(row, dict)]}
    raise ValueError("unsupported capability")


class RelayConfig:
    def __init__(self, *, token: str, allowed_source: str = "172.17.0.2",
                 upstream: str = DEFAULT_UPSTREAM, upstream_token: str = "",
                 upstream_token_file: str = "", max_body: int = MAX_BODY, allow_test_upstream: bool = False):
        self.token = token
        self.allowed_source = ipaddress.ip_network(allowed_source, strict=False)
        parsed = urlsplit(upstream)
        if parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query:
            raise ValueError("upstream must be a plain http origin")
        if not allow_test_upstream and parsed.hostname != "172.23.0.9":
            raise ValueError("invalid upstream")
        self.upstream = parsed
        self.upstream_token = _read_secret(upstream_token, upstream_token_file)
        self.max_body = max(1, min(max_body, MAX_BODY))


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "HermesMediaRelay/1"

    def _cfg(self) -> RelayConfig:
        return self.server.relay_config  # type: ignore[attr-defined]

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        cfg = self._cfg()
        try:
            source_ok = ipaddress.ip_address(self.client_address[0]) in cfg.allowed_source
        except ValueError:
            source_ok = False
        token_ok = bool(cfg.token) and hmac.compare_digest(self.headers.get(RELAY_TOKEN_HEADER, ""), cfg.token)
        if not (source_ok and token_ok):
            self._send(403, {"error": "forbidden"})
            return False
        return True

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path != "/media/requests":
            self._send(404, {"error": "not found"})
            return
        self._invoke("overseerr_recent_requests", {})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        route = ROUTES.get(self.path)
        if not route or self.path == "/media/requests":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > self._cfg().max_body:
                raise ValueError
            if self.headers.get_content_type() != "application/json":
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or set(body) - route[1]:
                raise ValueError
            if self.path == "/media/status" and not any(body.get(k) for k in ("workflow_id", "query", "title")):
                raise ValueError
            if self.path == "/media/library" and not body.get("query"):
                raise ValueError
            if self.path == "/media/diagnose" and not body.get("workflow_id"):
                raise ValueError
            if any(not isinstance(v, str) or len(v) > 512 for v in body.values()):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "invalid request"})
            return
        self._invoke(route[0], body)

    def _invoke(self, name: str, arguments: dict[str, Any]) -> None:
        cfg = self._cfg()
        payload = json.dumps({"name": name, "arguments": arguments, "client_id": "hermes-media-relay", "confirmed": False}).encode()
        conn = None
        try:
            conn = http.client.HTTPConnection(cfg.upstream.hostname, cfg.upstream.port or 80, timeout=UPSTREAM_TIMEOUT)
            conn.request("POST", "/invoke", payload, {"Content-Type": "application/json", UPSTREAM_TOKEN_HEADER: cfg.upstream_token})
            response = conn.getresponse()
            if response.status < 200 or response.status >= 300:
                self._send(502, {"error": "upstream unavailable"})
                return
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                self._send(502, {"error": "upstream response too large"})
                return
            decoded = json.loads(data)
            if (not isinstance(decoded, dict) or decoded.get("status") != "ok" or
                    decoded.get("operation_ok") is not True or not isinstance(decoded.get("result"), dict)):
                self._send(502, {"error": "invalid upstream response"})
                return
            self._send(200, {"ok": True, "tool": name, "result": _project(name, decoded["result"])})
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._send(502, {"error": "upstream unavailable"})
        finally:
            if conn is not None:
                conn.close()

    def log_message(self, *_: Any) -> None:
        return


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, handler, max_workers=8):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max_workers)
    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, client_address)
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(config: RelayConfig, bind: str = DEFAULT_BIND, port: int = 8091) -> ThreadingHTTPServer:
    server = BoundedHTTPServer((bind, port), RelayHandler)
    server.relay_config = config  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default=os.getenv("HERMES_RELAY_BIND", DEFAULT_BIND))
    parser.add_argument("--port", type=int, default=int(os.getenv("HERMES_RELAY_PORT", "8091")))
    args = parser.parse_args()
    config = RelayConfig(
        token=_read_secret(os.getenv("HERMES_MEDIA_RELAY_TOKEN", ""), os.getenv("HERMES_MEDIA_RELAY_TOKEN_FILE", "")),
        allowed_source=os.getenv("HERMES_RELAY_ALLOWED_SOURCE", "172.17.0.2"),
        upstream=os.getenv("HOME_AI_TOOLS_URL", DEFAULT_UPSTREAM),
        upstream_token_file=os.getenv("HOME_AI_TOOLS_TOKEN_FILE", ""),
    )
    if not config.token or not config.upstream_token:
        raise SystemExit("relay and upstream tokens are required")
    make_server(config, args.bind, args.port).serve_forever()


if __name__ == "__main__":
    main()
