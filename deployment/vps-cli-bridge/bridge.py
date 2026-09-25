#!/usr/bin/env python3
"""Private, narrow Home-AI adapter for the protected VPS cli_debrid instance."""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

DB_PATH = os.getenv("CLIDEBRID_DB_PATH", "/opt/family-media/data/cli_debrid/db_content/media_items.db")
WEBHOOK_URL = os.getenv("CLIDEBRID_WEBHOOK_URL", "http://127.0.0.1:5000/webhook/")
TOKEN_FILE = os.getenv("HOME_AI_BRIDGE_TOKEN_FILE", "/opt/family-media/secrets/home-ai-cli-bridge-token")
MAX_BODY = 16_384


def read_token() -> bytes:
    return Path(TOKEN_FILE).read_bytes().strip()


def exact_movie_status(tmdb_id: int) -> dict:
    # mode=ro lets SQLite consume the live WAL correctly. immutable=1 is
    # intentionally avoided because it can ignore uncheckpointed WAL pages.
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=1)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=1000")
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT state, location_on_disk FROM media_items "
            "WHERE tmdb_id = ? AND type = ? ORDER BY id DESC LIMIT 100",
            (str(tmdb_id), "movie"),
        ).fetchall()
    finally:
        con.close()
    normalized = []
    replica_paths = []
    for row in rows:
        state = str(row["state"] or "")
        path = row["location_on_disk"]
        relative = None
        if isinstance(path, str) and path.startswith("/data/symlinked/"):
            relative = path[len("/data/symlinked"):]
            if ".." not in Path(relative).parts:
                replica_paths.append(relative)
        normalized.append({"state": state, "vps_collected": "collect" in state.casefold(),
                           "replica_path": relative})
    return {"tmdb_id": tmdb_id, "media_type": "movie",
            "status": "present" if rows else "absent",
            "vps_collected": any(item["vps_collected"] for item in normalized),
            "replica_paths": replica_paths, "rows": normalized}


def validate_request(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"notification_type", "subject", "request", "media"}:
        raise ValueError("invalid request shape")
    if payload.get("notification_type") != "MEDIA_PENDING":
        raise ValueError("unsupported operation")
    subject = payload.get("subject")
    request = payload.get("request")
    media = payload.get("media")
    if not isinstance(subject, str) or len(subject) > 160 or not isinstance(request, dict) or not isinstance(media, dict):
        raise ValueError("invalid request fields")
    if set(request) != {"request_id", "requestedBy_username", "requestedBy_email"}:
        raise ValueError("invalid request fields")
    if not re.fullmatch(r"home_ai_[A-Za-z0-9_-]{1,128}", str(request.get("request_id", ""))):
        raise ValueError("invalid request id")
    if set(media) - {"media_type", "tmdbId", "from_overseerr"}:
        raise ValueError("unsupported media fields")
    if media.get("media_type") != "movie" or media.get("from_overseerr") is not True:
        raise ValueError("movie requests only")
    tmdb_id = media.get("tmdbId")
    if isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int) or not 1 <= tmdb_id <= 2_147_483_647:
        raise ValueError("invalid TMDB id")
    if request.get("requestedBy_username") != "Home-AI" or request.get("requestedBy_email") != "home-ai@system":
        raise ValueError("invalid request source")
    return payload


def forward_movie_request(payload: dict) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(WEBHOOK_URL, data=body,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError("webhook rejected request")
    return {"accepted": True, "authority": "vps_cli_debrid"}


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeAICliBridge/1"
    sys_version = ""

    def log_message(self, _fmt, *_args):
        # Never log request bodies, headers, query identifiers or credentials.
        return

    def authorized(self) -> bool:
        expected = read_token()
        supplied = self.headers.get("X-Home-AI-Bridge-Token", "").encode()
        return bool(expected) and hmac.compare_digest(expected, supplied)

    def send_json(self, status: int, value: dict):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        match = re.fullmatch(r"/v1/movies/([1-9][0-9]{0,9})", urlsplit(self.path).path)
        if not match or int(match.group(1)) > 2_147_483_647:
            self.send_json(404, {"error": "not_found"})
            return
        try:
            status = exact_movie_status(int(match.group(1)))
            # Tools needs only normalized provider state plus relative replica
            # paths; never return arbitrary database fields or SQL results.
            self.send_json(200, {k: status[k] for k in
                                 ("tmdb_id", "media_type", "status", "vps_collected", "replica_paths", "rows")})
        except sqlite3.Error as exc:
            logging.warning("read-only VPS status query unavailable (%s)", type(exc).__name__)
            self.send_json(503, {"error": "status_unavailable"})

    def do_POST(self):
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        if urlsplit(self.path).path != "/v1/requests":
            self.send_json(404, {"error": "not_found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > MAX_BODY:
                raise ValueError("invalid body size")
            payload = validate_request(json.loads(self.rfile.read(size)))
            self.send_json(202, forward_movie_request(payload))
        except ValueError:
            self.send_json(400, {"error": "invalid_request"})
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            logging.warning("VPS webhook forwarding unavailable: %s", type(exc).__name__)
            self.send_json(503, {"error": "upstream_unavailable"})


def main():
    host = os.getenv("HOME_AI_BRIDGE_BIND", "100.118.61.115")
    port = int(os.getenv("HOME_AI_BRIDGE_PORT", "9301"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
