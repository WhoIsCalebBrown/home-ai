import asyncio
import ast
import base64
import contextvars
import html
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import socket
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Local Server Tools", version="2026.09.13")

TOWER = os.getenv("TOWER_URL", "http://192.168.40.44").rstrip("/")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://SearXNG:8080").rstrip("/")
DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
CLIDEBRID_BASE = os.getenv("CLIDEBRID_BASE", f"{TOWER}:5000/webhook").rstrip("/")
CLIDEBRID_BRIDGE_TOKEN = os.getenv("CLIDEBRID_BRIDGE_TOKEN", "")
# Home-AI-Tools mounts the Unraid appdata root at /config; cli_debrid's
# token lives below its mounted config directory.  Keep the path server-side
# and fail closed if it is absent or unreadable.
CLIDEBRID_BRIDGE_TOKEN_FILE = os.getenv(
    "CLIDEBRID_BRIDGE_TOKEN_FILE",
    "/config/cli_debrid/config/cli_debrid_bridge_token",
)
STANDARD_MEDIA_WRITES_ENABLED = os.getenv("STANDARD_MEDIA_WRITES_ENABLED", "false").casefold() == "true"
STANDARD_MOVIE_WRITES_ENABLED = os.getenv("STANDARD_MOVIE_WRITES_ENABLED", "false").casefold() == "true"
STANDARD_SEASON_WRITES_ENABLED = os.getenv("STANDARD_SEASON_WRITES_ENABLED", "false").casefold() == "true"
STANDARD_EPISODE_WRITES_ENABLED = os.getenv("STANDARD_EPISODE_WRITES_ENABLED", "false").casefold() == "true"
AUDIT = Path(os.getenv("AUDIT_LOG", "/data/audit.jsonl"))
LISTS_PATH = Path(os.getenv("LISTS_PATH", "/data/home-ai-lists.json"))
MEDIA_WORKFLOWS_PATH = Path(os.getenv("MEDIA_WORKFLOWS_PATH", "/data/media-workflows.json"))
USER_PROFILE_PATH = Path(os.getenv("USER_PROFILE_PATH", "/config/home-ai-user-profile.json"))
WEATHER_LOCATION_HINTS = {}
for _hint in os.getenv("WEATHER_LOCATION_HINTS", "").split(";"):
    if "=" in _hint:
        _name, _qualified = _hint.split("=", 1)
        if _name.strip() and _qualified.strip():
            WEATHER_LOCATION_HINTS[_name.strip().casefold()] = _qualified.strip()
PROTECTED = {x.strip().lower() for x in os.getenv(
    "PROTECTED_CONTAINERS",
    "voice-api,voice-ollama,voice-whisper,voice-kokoro,voice-piper,Nginx-Proxy-Manager-Official,adguardhome,cloudflare-tunnel,mariadb,postgres,redis"
).split(",") if x.strip()}

SERVICES = {
    "plex": (f"{TOWER}:32400", "Plex-Media-Server/Library/Application Support/Plex Media Server/Preferences.xml"),
    "sonarr": (f"{TOWER}:8989", "sonarr/config.xml"),
    "radarr": (f"{TOWER}:7878", "radarr/config.xml"),
    "lidarr": (f"{TOWER}:8686", "lidarr/config.xml"),
    "qbittorrent": (f"{TOWER}:8080", ""),
    "frigate": (f"{TOWER}:6060", ""),
    "netdata": (f"{TOWER}:19999", ""),
    "overseerr": (f"{TOWER}:5055", ""),
    "slskd": (f"{TOWER}:5030", "slskdn/access.json"),
    "music_enricher": (f"{TOWER}:8723", ""),
    "torbox": (f"{TOWER}:8081", ""),
}
QBIT_BASE = f"{TOWER}:8080"
QBIT_SECRET_FILE = Path(os.getenv("QBIT_SECRET_FILE", "/data/qbittorrent.env"))
OVERSEERR_DB = Path("/config/overseerr/db/db.sqlite3")
BEETS_DB = Path("/config/beets/music.db")
MUSIC_ENRICHER_DB = Path("/config/music-enricher/state.sqlite3")
AUDIT_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("audit_context", default={})


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def user_profile() -> dict[str, Any]:
    try:
        value = json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def event_time(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def safe_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: ("[redacted]" if any(s in k.lower() for s in ("key", "token", "password", "secret")) else v) for k, v in args.items()}


def audit_result(value: Any, limit: int = 1200) -> Any:
    """Keep bounded, non-secret result evidence in the local audit trail."""
    if isinstance(value, dict):
        return {str(k): audit_result(v, limit) for k, v in list(value.items())[:40]
                if not any(secret in str(k).lower() for secret in ("key", "token", "password", "secret"))}
    if isinstance(value, list):
        return [audit_result(item, limit) for item in value[:20]]
    if isinstance(value, str):
        return value[:limit]
    return value


def audit(entry: dict[str, Any]) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": now(), **entry}, default=str) + "\n")


def xml_value(path: Path, key: str) -> str | None:
    try:
        root = ET.parse(path).getroot()
        return root.attrib.get(key) or next((child.text for child in root if child.tag.lower() == key.lower()), None)
    except Exception:
        return None


def api_key(service: str) -> str | None:
    _, rel = SERVICES[service]
    if not rel:
        return None
    return xml_value(Path("/config") / rel, "ApiKey")


async def get_json(service: str, path: str, params: dict[str, Any] | None = None, timeout: float = 5) -> Any:
    base, _ = SERVICES[service]
    headers = {}
    key = api_key(service)
    if key:
        headers["X-Api-Key"] = key
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(base + path, params=params, headers=headers)
        r.raise_for_status()
        return r.json()


async def post_json(service: str, path: str, body: dict[str, Any] | None = None, timeout: float = 8) -> Any:
    base, _ = SERVICES[service]
    headers = {}
    key = api_key(service)
    if key:
        headers["X-Api-Key"] = key
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(base + path, json=body or {}, headers=headers)
        r.raise_for_status()
        return r.json() if r.content else {"ok": True}


def slskd_api_key() -> str | None:
    try:
        return json.loads((Path("/config") / SERVICES["slskd"][1]).read_text()).get("api_key")
    except Exception:
        return None


async def qbit_request(path: str, params: dict[str, Any] | None = None) -> Any:
    username, password = qbit_credentials()
    async with httpx.AsyncClient(timeout=8) as client:
        login = await client.post(f"{QBIT_BASE}/api/v2/auth/login", data={"username": username, "password": password})
        if login.status_code not in (200, 204) or (login.text.strip() and login.text.strip() != "Ok."):
            raise RuntimeError("qBittorrent authentication failed")
        response = await client.get(f"{QBIT_BASE}{path}", params=params)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}


def qbit_credentials() -> tuple[str, str]:
    username, password = os.getenv("QBIT_USERNAME", ""), os.getenv("QBIT_PASSWORD", "")
    if (not username or not password) and QBIT_SECRET_FILE.exists():
        values = {}
        for line in QBIT_SECRET_FILE.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        username, password = values.get("QBIT_USERNAME", username), values.get("QBIT_PASSWORD", password)
    return username, password


async def torbox_request(path: str, params: dict[str, Any] | None = None) -> Any:
    username, password = qbit_credentials()
    async with httpx.AsyncClient(timeout=8) as client:
        login = await client.post(f"{SERVICES['torbox'][0]}/api/v2/auth/login", data={"username": username, "password": password})
        if login.status_code not in (200, 204) or (login.text.strip() and login.text.strip() != "Ok."):
            raise RuntimeError("Torbox client authentication failed")
        response = await client.get(f"{SERVICES['torbox'][0]}{path}", params=params)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}


async def slskd_get(path: str, params: dict[str, Any] | None = None) -> Any:
    key = slskd_api_key()
    if not key:
        raise RuntimeError("Slskd API credential unavailable")
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(f"{SERVICES['slskd'][0]}{path}", params=params, headers={"X-API-Key": key})
        response.raise_for_status()
        return response.json()


def sqlite_rows(path: Path, query: str, params: tuple = ()) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query, params).fetchall()]


async def docker_get(path: str, params: dict[str, Any] | None = None) -> Any:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    async with httpx.AsyncClient(transport=transport, timeout=5) as client:
        r = await client.get("http://docker" + path, params=params)
        r.raise_for_status()
        return r.json()


async def service_bytes(service: str, path: str, params: dict[str, Any] | None = None) -> tuple[bytes, str]:
    base, _ = SERVICES[service]
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(base + path, params=params)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]


def normalize_container(row: dict[str, Any]) -> dict[str, Any]:
    names = [n.lstrip("/") for n in row.get("Names", [])]
    return {"name": names[0] if names else row.get("Id", "")[:12], "id": row.get("Id", "")[:12],
            "image": row.get("Image", ""), "state": row.get("State", ""), "status": row.get("Status", ""),
            "labels": {k: v for k, v in row.get("Labels", {}).items() if k in ("com.docker.compose.service", "net.unraid.docker.managed")}}


async def server_overview(_: dict[str, Any]) -> dict[str, Any]:
    usage = shutil.disk_usage("/mnt/user")
    cache = shutil.disk_usage("/mnt/cache")
    return {"hostname": "Tower", "uptime_seconds": _uptime(),
            "storage": {"user_total_bytes": usage.total, "user_free_bytes": usage.free,
                         "cache_total_bytes": cache.total, "cache_free_bytes": cache.free}}


def _uptime() -> int | None:
    try:
        return int(float(Path("/host/proc/uptime").read_text().split()[0]))
    except Exception:
        return None


async def storage_status(_: dict[str, Any]) -> dict[str, Any]:
    result = await server_overview({})
    s, c = result["storage"], result["storage"]
    return {"user_share": {"total_bytes": s["user_total_bytes"], "free_bytes": s["user_free_bytes"],
                            "used_percent": round((1 - s["user_free_bytes"] / s["user_total_bytes"]) * 100, 1)},
            "cache": {"total_bytes": c["cache_total_bytes"], "free_bytes": c["cache_free_bytes"],
                      "used_percent": round((1 - c["cache_free_bytes"] / c["cache_total_bytes"]) * 100, 1)}}


async def gpu_status(_: dict[str, Any]) -> dict[str, Any]:
    try:
        p = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=name,uuid,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw", "--format=csv,noheader,nounits", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await p.communicate()
        if p.returncode:
            raise RuntimeError(err.decode(errors="ignore"))
        gpus = []
        for line in out.decode().splitlines():
            name, uid, mem, total, util, temp, power = [x.strip() for x in line.split(",")]
            gpus.append({"model": name, "uuid": uid, "vram_used_mib": int(float(mem)), "vram_total_mib": int(float(total)), "utilization_percent": int(float(util)), "temperature_c": int(float(temp)), "power_w": float(power)})
        return {"gpus": gpus}
    except Exception as exc:
        return {"error": "GPU telemetry unavailable", "detail": type(exc).__name__}


async def list_containers(args: dict[str, Any]) -> dict[str, Any]:
    rows = await docker_get("/containers/json", {"all": "1"})
    status = args.get("status")
    all_items = [normalize_container(r) for r in rows]
    summary = {
        "total": len(all_items),
        "running": sum(item["state"] == "running" for item in all_items),
        "stopped": sum(item["state"] in {"exited", "created"} for item in all_items),
        "paused": sum(item["state"] == "paused" for item in all_items),
        "restarting": sum(item["state"] == "restarting" for item in all_items),
        "dead": sum(item["state"] == "dead" for item in all_items),
    }
    items = all_items
    if status:
        items = [x for x in items if x["state"] == status or status.lower() in x["status"].lower()]
    return {"count": len(items), "summary": summary, "status_filter": status or None, "containers": items[:200]}


async def container_status(args: dict[str, Any]) -> dict[str, Any]:
    name = args["name"]
    rows = await docker_get("/containers/json", {"all": "1"})
    match = next((r for r in rows if name in [n.lstrip("/") for n in r.get("Names", [])]), None)
    if not match:
        return {"found": False, "name": name}
    return {"found": True, **normalize_container(match)}


async def container_logs(args: dict[str, Any]) -> dict[str, Any]:
    name, lines = args["name"], min(int(args.get("lines", 50)), 200)
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    async with httpx.AsyncClient(transport=transport, timeout=5) as client:
        r = await client.get(f"http://docker/containers/{name}/logs", params={"stdout": 1, "stderr": 1, "tail": lines})
        r.raise_for_status()
    return {"name": name, "lines": r.content.decode(errors="replace")[-20000:]}


async def restart_container(args: dict[str, Any]) -> dict[str, Any]:
    name = args["name"].lower()
    if name in PROTECTED:
        return {"blocked": True, "reason": "protected infrastructure requires elevated confirmation", "name": name}
    before = await docker_get(f"/containers/{name}/json")
    before_started = before.get("State", {}).get("StartedAt")
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    async with httpx.AsyncClient(transport=transport, timeout=10) as client:
        r = await client.post(f"http://docker/containers/{name}/restart", params={"t": 10})
        r.raise_for_status()
    after = await docker_get(f"/containers/{name}/json")
    state = after.get("State", {})
    after_started = state.get("StartedAt")
    health = state.get("Health", {}).get("Status") if isinstance(state.get("Health"), dict) else None
    verified = bool(state.get("Running")) and bool(after_started) and after_started != before_started
    return {"ok": verified, "name": name, "action": "restarted" if verified else "restart_unverified",
            "before_started_at": before_started, "after_started_at": after_started,
            "started_at_changed": after_started != before_started, "running": bool(state.get("Running")),
            "health": health, "verified": verified}


def public_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only public http(s) URLs are allowed")
    host = parsed.hostname.rstrip(".").lower()
    blocked_names = {"localhost", "unraid", "tower", "host.docker.internal", "metadata.google.internal"}
    if host in blocked_names or host.endswith((".local", ".lan", ".internal", ".docker", ".home")):
        raise ValueError("internal hostnames are not allowed")
    try:
        addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)]
    except socket.gaierror as exc:
        raise ValueError("hostname could not be resolved") from exc
    if not addresses or any(address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified for address in addresses):
        raise ValueError("private or link-local targets are not allowed")
    return value


async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"].strip()
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"})
        response.raise_for_status()
    results = []
    for item in response.json().get("results", [])[:8]:
        if item.get("url"):
            results.append({"title": item.get("title", ""), "url": item["url"],
                            "domain": urlparse(item["url"]).hostname or "",
                            "snippet": item.get("content", ""), "date": item.get("publishedDate"),
                            "rank": len(results) + 1})
    return {"query": query, "results": results, "source": "SearXNG", "untrusted": True}


async def web_fetch(args: dict[str, Any]) -> dict[str, Any]:
    target = public_url(args["url"])
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": "Home-AI-Tools/1.0"}) as client:
        for _ in range(4):
            response = await client.get(target)
            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                target = public_url(urljoin(target, response.headers["location"]))
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not any(kind in content_type for kind in ("text/", "application/json", "application/xml")):
                raise ValueError("only text web pages can be fetched")
            text = response.text[:200000]
            text = html.unescape(re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", text, flags=re.I))
            return {"url": target, "content": re.sub(r"\s+", " ", text).strip(), "untrusted": True}
    raise ValueError("too many redirects")


def _safe_calculate(expression: str) -> float | int:
    """Evaluate only arithmetic literals/operators; never execute arbitrary Python."""
    tree = ast.parse(expression, mode="eval")
    def visit(node):
        if isinstance(node, ast.Expression): return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool): return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)): return (+1 if isinstance(node.op, ast.UAdd) else -1) * visit(node.operand)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div): return left / right
            if isinstance(node.op, ast.FloorDiv): return left // right
            if isinstance(node.op, ast.Mod): return left % right
            if abs(right) > 1000: raise ValueError("exponent too large")
            return left ** right
        raise ValueError("only numeric arithmetic is supported")
    value = visit(tree)
    if abs(value) > 10**18: raise ValueError("result out of range")
    return value


async def calculator(args: dict[str, Any]) -> dict[str, Any]:
    expression = str(args["expression"]).strip()
    return {"expression": expression, "value": _safe_calculate(expression), "deterministic": True}


async def unit_convert(args: dict[str, Any]) -> dict[str, Any]:
    value, source, target = float(args["value"]), args["from_unit"].casefold(), args["to_unit"].casefold()
    factors = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
               "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}
    if source in factors and target in factors:
        result = value * factors[source] / factors[target]
    elif source in {"c", "°c", "celsius"} and target in {"f", "°f", "fahrenheit"}:
        result = value * 9 / 5 + 32
    elif source in {"f", "°f", "fahrenheit"} and target in {"c", "°c", "celsius"}:
        result = (value - 32) * 5 / 9
    else:
        raise ValueError("unsupported unit pair")
    return {"value": value, "from_unit": source, "to_unit": target, "result": result, "deterministic": True}


async def current_datetime(args: dict[str, Any]) -> dict[str, Any]:
    zone = str(args.get("timezone") or os.getenv("DEFAULT_TIMEZONE", "America/Toronto"))
    current = datetime.now(ZoneInfo(zone))
    return {"timezone": zone, "iso": current.isoformat(), "date": current.date().isoformat(),
            "time": current.strftime("%H:%M"), "weekday": current.strftime("%A"), "source": "system_clock"}


async def weather_forecast(args: dict[str, Any]) -> dict[str, Any]:
    profile = user_profile()
    location = str(args.get("location") or os.getenv("WEATHER_DEFAULT_LOCATION", "") or profile.get("home_location", "")).strip()
    if not location:
        return {"location_required": True, "message": "A city or location is required; no default home location is configured."}
    offset = max(0, min(7, int(args.get("days_from_now", 0))))
    geocoder_location = WEATHER_LOCATION_HINTS.get(location.casefold(), location)
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        geo = await client.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": geocoder_location, "count": 1, "language": "en", "format": "json"})
        geo.raise_for_status(); places = geo.json().get("results") or []
        if not places: return {"location": location, "found": False}
        place = places[0]
        forecast = await client.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "celsius", "wind_speed_unit": "kmh", "forecast_days": max(2, offset + 1), "timezone": "auto"})
        forecast.raise_for_status(); data = forecast.json()
    daily = {k: (v[offset] if isinstance(v, list) and len(v) > offset else None) for k, v in data.get("daily", {}).items()}
    resolved = {"name": place.get("name"), "admin1": place.get("admin1"), "country": place.get("country")}
    unit = str(profile.get("temperature_unit") or os.getenv("WEATHER_TEMPERATURE_UNIT", "C")).upper()
    if unit not in {"C", "F"}:
        unit = "C"
    if unit == "F":
        def convert(value):
            return round(float(value) * 9 / 5 + 32, 1) if value is not None else None
        for key in ("temperature_2m", "apparent_temperature"):
            if key in data.get("current", {}):
                data["current"][key] = convert(data["current"][key])
        for key in ("temperature_2m_max", "temperature_2m_min"):
            daily[key] = [convert(value) for value in daily.get(key, [])] if isinstance(daily.get(key), list) else convert(daily.get(key))
    return {"requested_location": location, "resolved_location": ", ".join(str(x) for x in (resolved.get("name"), resolved.get("admin1"), resolved.get("country")) if x),
            "location": resolved, "temperature_unit": unit,
            "days_from_now": offset, "current": data.get("current", {}), "day": daily,
            "timezone": data.get("timezone"), "source": "Open-Meteo", "retrieved_at": now()}


async def wikipedia_search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args["query"]).strip()
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Home-AI-Tools/1.0"}) as client:
        response = await client.get("https://en.wikipedia.org/w/rest.php/v1/search/page", params={"q": query, "limit": 5})
        response.raise_for_status()
    pages = []
    for page in response.json().get("pages", []):
        pages.append({"title": page.get("title"), "description": page.get("description"), "excerpt": re.sub(r"<[^>]+>", "", page.get("excerpt", "")), "url": "https://en.wikipedia.org/wiki/" + (page.get("key") or "").replace(" ", "_")})
    return {"query": query, "results": pages, "source": "Wikipedia"}


async def plex_search(args: dict[str, Any]) -> dict[str, Any]:
    token = xml_value(Path("/config") / SERVICES["plex"][1], "PlexOnlineToken")
    if not token:
        return {"error": "Plex credential unavailable"}
    base, _ = SERVICES["plex"]
    async with httpx.AsyncClient(timeout=8) as client:
        sections_response = await client.get(base + "/library/sections", params={"X-Plex-Token": token})
        sections_response.raise_for_status()
        section_root = ET.fromstring(sections_response.text)
        sections_by_id = {}
        for d in section_root:
            value = {"title": d.attrib.get("title"), "id": d.attrib.get("uuid") or d.attrib.get("key"), "key": d.attrib.get("key")}
            if d.attrib.get("key"):
                sections_by_id[str(d.attrib["key"])] = value
            if d.attrib.get("uuid"):
                sections_by_id[str(d.attrib["uuid"])] = value
        r = await client.get(base + "/search", params={"query": args["query"], "X-Plex-Token": token})
        r.raise_for_status()
        root = ET.fromstring(r.text)
    results = []
    for item in root:
        attrs = item.attrib
        if not attrs.get("title") or not attrs.get("ratingKey"):
            continue
        section_id = attrs.get("librarySectionID") or attrs.get("librarySectionKey")
        section = sections_by_id.get(str(section_id), {})
        library_title = attrs.get("librarySectionTitle") or section.get("title")
        match = {"title": attrs.get("title"), "year": int(attrs["year"]) if attrs.get("year", "").isdigit() else attrs.get("year"),
                 "media_type": attrs.get("type"), "rating_key": attrs.get("ratingKey"),
                 "library_title": library_title, "library_key": section.get("key") or attrs.get("librarySectionKey"),
                 "library_section_id": section_id, "edition": attrs.get("editionTitle")}
        rating_key = attrs.get("ratingKey")
        if rating_key:
            try:
                async with httpx.AsyncClient(timeout=8) as detail_client:
                    detail = await detail_client.get(base + f"/library/metadata/{rating_key}", params={"X-Plex-Token": token})
                    detail.raise_for_status()
                    detail_root = ET.fromstring(detail.text)
                media_rows = []
                for media in detail_root.findall(".//Media"):
                    media_rows.append({"video_resolution": media.attrib.get("videoResolution"), "video_codec": media.attrib.get("videoCodec"), "container": media.attrib.get("container"), "duration_ms": media.attrib.get("duration"), "bitrate": media.attrib.get("bitrate"), "parts": [{"file": Path(part.attrib["file"]).name if part.attrib.get("file") else None, "size": part.attrib.get("size"), "duration_ms": part.attrib.get("duration")} for part in media.findall("Part")]})
                match["media"] = media_rows
                if media_rows:
                    raw_resolution = media_rows[0].get("video_resolution")
                    match["resolution"] = f"{raw_resolution}p" if raw_resolution and raw_resolution.isdigit() else raw_resolution
                    match["version"] = match.get("edition") or " ".join(x for x in (match.get("resolution"), media_rows[0].get("video_codec"), media_rows[0].get("container")) if x)
            except Exception:
                match["media"] = []
        results.append({k: v for k, v in match.items() if v is not None})
    library = args.get("library")
    if library:
        results = [x for x in results if x.get("library_title") == library]
    unique_titles = len({(x.get("title"), x.get("year")) for x in results})
    return {"query": args["query"], "unique_titles": unique_titles, "matches": results[:20]}


async def plex_library_lookup(args: dict[str, Any]) -> dict[str, Any]:
    """Fast metadata-only Plex lookup used by semantic planning.

    Keep this separate from plex_search: the latter enriches every match with
    media details and is intentionally slower for user-facing investigations.
    """
    token = xml_value(Path("/config") / SERVICES["plex"][1], "PlexOnlineToken")
    base, _ = SERVICES["plex"]
    query = str(args.get("query", "")).strip()
    if not token or not query:
        return {"query": query, "matches": [], "available": False, "error": "Plex lookup unavailable"}
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(base + "/search", params={"query": query, "X-Plex-Token": token})
        response.raise_for_status()
        root = ET.fromstring(response.text)
    wanted_library = str(args.get("library", "")).casefold().strip()
    matches = []
    for item in root:
        attrs = item.attrib
        library = attrs.get("librarySectionTitle") or attrs.get("librarySectionName") or ""
        if wanted_library and wanted_library not in library.casefold() and not (wanted_library == "music" and attrs.get("type") in {"artist", "album", "track"}):
            continue
        matches.append({k: v for k, v in {
            "title": attrs.get("title"), "year": int(attrs["year"]) if attrs.get("year", "").isdigit() else attrs.get("year"),
            "media_type": attrs.get("type"), "rating_key": attrs.get("ratingKey"),
            "library": library, "parent_title": attrs.get("parentTitle"),
        }.items() if v is not None})
    return {"query": query, "available": bool(matches), "matches": matches[:20], "source": "Plex"}


def _normalize_identity_title(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _plex_external_ids(*sources: Any) -> dict[str, str]:
    """Extract stable IDs from Plex GUID variants without exposing raw GUIDs."""
    ids: dict[str, str] = {}
    for source in sources:
        values = []
        if isinstance(source, dict):
            values.extend(source.get(key) for key in ("guid", "guid_id"))
            values.extend(source.get("guid_ids", []))
        elif isinstance(source, str):
            values.append(source)
        for value in values:
            for kind, identifier in re.findall(r"(?:^|[^a-z])(tmdb|imdb|tvdb)://([^?&#/]+)", str(value), re.I):
                ids[kind.casefold()] = identifier
    return ids


def _evaluate_plex_candidate(candidate: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one discovered item; search similarity never implies identity."""
    requested_ids = requested.get("external_ids", {})
    candidate_ids = candidate.get("external_ids", {})
    stable_requested = {k: v for k, v in requested_ids.items() if k in {"tmdb", "imdb", "tvdb"}}
    stable_candidate = {k: v for k, v in candidate_ids.items() if k in {"tmdb", "imdb", "tvdb"}}
    matched_key = next((k for k, v in stable_requested.items() if stable_candidate.get(k) == v), None)
    conflicting_key = next((k for k, v in stable_requested.items()
                            if k in stable_candidate and stable_candidate[k] != v), None)
    if matched_key:
        return {**candidate, "match_method": matched_key, "confidence": 1.0}
    if conflicting_key or (stable_candidate and stable_requested):
        return {**candidate, "rejected_reason": "canonical_identity_mismatch"}
    if _normalize_identity_title(candidate.get("title")) != _normalize_identity_title(requested.get("title")):
        return {**candidate, "rejected_reason": "title_mismatch"}
    if requested.get("year") is not None and candidate.get("year") != requested.get("year"):
        return {**candidate, "rejected_reason": "year_mismatch"}
    return {**candidate, "match_method": "title_year", "confidence": 0.85}


async def plex_match_canonical_media(args: dict[str, Any]) -> dict[str, Any]:
    """Canonical Plex matcher: search discovers candidates; IDs decide identity."""
    token = xml_value(Path("/config") / SERVICES["plex"][1], "PlexOnlineToken")
    base, _ = SERVICES["plex"]
    media_type = str(args.get("media_type", "movie")).casefold()
    title = str(args.get("title", "")).strip()
    year = args.get("year")
    requested_ids = {key.replace("_id", "").casefold(): str(value)
                     for key, value in (args.get("canonical_external_ids") or {}).items()
                     if value not in (None, "")}
    requested = {"media_type": media_type, "title": title, "year": year, "external_ids": requested_ids}
    if not token or not title:
        return {"matched": False, "match_method": None, "confidence": 0, "requested": requested,
                "candidates": [], "error": "Plex lookup unavailable"}

    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(base + "/search", params={"query": title, "X-Plex-Token": token})
        response.raise_for_status()
        search_root = ET.fromstring(response.text)
        sections = await client.get(base + "/library/sections", params={"X-Plex-Token": token})
        sections.raise_for_status()
        section_root = ET.fromstring(sections.text)
        section_names = {str(x.attrib.get("key")): x.attrib.get("title", "") for x in section_root}
        wanted_library = str(args.get("library", "")).casefold()
        candidates = []
        for item in search_root:
            attrs = dict(item.attrib)
            if attrs.get("type") != media_type or not attrs.get("ratingKey"):
                continue
            library = attrs.get("librarySectionTitle") or section_names.get(str(attrs.get("librarySectionKey")), "")
            if wanted_library and library.casefold() != wanted_library:
                continue
            detail_attrs = attrs
            detail_guid_ids = []
            try:
                detail = await client.get(base + f"/library/metadata/{attrs['ratingKey']}", params={"X-Plex-Token": token})
                detail.raise_for_status()
                detail_root = ET.fromstring(detail.text)
                detail_item = next(iter(detail_root), None)
                if detail_item is not None:
                    detail_attrs = {**attrs, **detail_item.attrib}
                    detail_guid_ids = [g.attrib.get("id") for g in detail_item.findall(".//Guid") if g.attrib.get("id")]
            except Exception:
                detail_guid_ids = []
            external_ids = _plex_external_ids(detail_attrs, {"guid_ids": detail_guid_ids})
            candidate = {
                "title": detail_attrs.get("title"),
                "year": int(detail_attrs["year"]) if str(detail_attrs.get("year", "")).isdigit() else detail_attrs.get("year"),
                "media_type": detail_attrs.get("type"),
                "plex_rating_key": detail_attrs.get("ratingKey"),
                "library": library,
                "external_ids": external_ids,
            }
            candidates.append(_evaluate_plex_candidate(candidate, requested))

    positives = [x for x in candidates if x.get("match_method")]
    if len(positives) == 1:
        match = positives[0]
        return {"matched": True, "match_method": match["match_method"], "confidence": match["confidence"],
                "requested": requested, "match": match, "candidates": candidates}
    if len(positives) > 1:
        for item in positives:
            item["rejected_reason"] = "ambiguous_library_match"
            item.pop("match_method", None)
        return {"matched": False, "match_method": None, "confidence": 0, "reason": "AMBIGUOUS_LIBRARY_MATCH",
                "requested": requested, "candidates": candidates}
    return {"matched": False, "match_method": None, "confidence": 0, "requested": requested,
            "candidates": candidates}


async def plex_artist_library(args: dict[str, Any]) -> dict[str, Any]:
    """Return the actual albums/tracks already present under an exact Plex Music artist."""
    token = xml_value(Path("/config") / SERVICES["plex"][1], "PlexOnlineToken")
    base, _ = SERVICES["plex"]
    if not token:
        return {"query": args["query"], "found": False, "error": "Plex credential unavailable"}
    async with httpx.AsyncClient(timeout=8) as client:
        sections = await client.get(base + "/library/sections", params={"X-Plex-Token": token})
        sections.raise_for_status()
        section_root = ET.fromstring(sections.text)
        music_sections = {}
        for d in section_root:
            if d.attrib.get("type") == "artist":
                music_sections[str(d.attrib.get("key"))] = d.attrib.get("title")
                if d.attrib.get("uuid"):
                    music_sections[str(d.attrib.get("uuid"))] = d.attrib.get("title")
        search = await client.get(base + "/search", params={"query": args["query"], "X-Plex-Token": token})
        search.raise_for_status()
        root = ET.fromstring(search.text)
        artists = [x for x in root if x.attrib.get("type") == "artist" and music_sections.get(str(x.attrib.get("librarySectionKey") or x.attrib.get("librarySectionID"))) and x.attrib.get("title", "").casefold() == args["query"].casefold()]
        if not artists:
            return {"query": args["query"], "found": False, "library": "Music", "albums": [], "tracks": []}
        artist = artists[0]
        artist_key = artist.attrib.get("ratingKey")
        children = await client.get(base + f"/library/metadata/{artist_key}/children", params={"X-Plex-Token": token})
        children.raise_for_status()
        album_root = ET.fromstring(children.text)
        albums, tracks = [], []
        for album in album_root:
            if album.attrib.get("type") != "album":
                continue
            album_row = {"rating_key": album.attrib.get("ratingKey"), "title": album.attrib.get("title"), "year": int(album.attrib["year"]) if album.attrib.get("year", "").isdigit() else album.attrib.get("year"), "library_title": music_sections.get(str(album.attrib.get("librarySectionKey") or album.attrib.get("librarySectionID"))) or "Music"}
            album_tracks = await client.get(base + f"/library/metadata/{album.attrib.get('ratingKey')}/children", params={"X-Plex-Token": token})
            album_tracks.raise_for_status()
            track_root = ET.fromstring(album_tracks.text)
            album_row["track_count"] = len([x for x in track_root if x.attrib.get("type") == "track"])
            for track in track_root:
                if track.attrib.get("type") == "track":
                    tracks.append({"title": track.attrib.get("title"), "index": track.attrib.get("index"), "album": album_row["title"], "rating_key": track.attrib.get("ratingKey"), "library_title": album_row["library_title"]})
            albums.append(album_row)
    return {"query": args["query"], "found": True, "artist": artist.attrib.get("title"), "library_title": music_sections.get(str(artist.attrib.get("librarySectionKey") or artist.attrib.get("librarySectionID"))) or "Music", "artist_rating_key": artist_key, "album_count": len(albums), "track_count": len(tracks), "albums": albums[:100], "tracks": tracks[:300]}


async def plex_counts(_: dict[str, Any]) -> dict[str, Any]:
    token = xml_value(Path("/config") / SERVICES["plex"][1], "PlexOnlineToken")
    base, _ = SERVICES["plex"]
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(base + "/library/sections", params={"X-Plex-Token": token})
        r.raise_for_status()
        root = ET.fromstring(r.text)
        counts = []
        for d in root:
            key, title, typ = d.attrib.get("key"), d.attrib.get("title"), d.attrib.get("type")
            rr = await client.get(base + f"/library/sections/{key}/all", params={"X-Plex-Token": token, "X-Plex-Container-Size": 1})
            rr.raise_for_status()
            media = ET.fromstring(rr.text).attrib
            counts.append({"library": title, "type": typ, "items": int(media.get("totalSize", media.get("size", "0")))})
    return {"libraries": counts}


async def plex_recently_added(args: dict[str, Any]) -> dict[str, Any]:
    """Return Plex library metadata, kept separate from acquisition state."""
    token = xml_value(Path("/config") / SERVICES["plex"][1], "PlexOnlineToken")
    base, _ = SERVICES["plex"]
    limit = max(1, min(int(args.get("limit", 10)), 50))
    requested_type = str(args.get("media_type") or "").casefold()
    async with httpx.AsyncClient(timeout=8) as client:
        sections = await client.get(base + "/library/sections", params={"X-Plex-Token": token})
        sections.raise_for_status()
        root = ET.fromstring(sections.text)
        rows = []
        for section in root:
            section_key = section.attrib.get("key")
            if not section_key or (requested_type and section.attrib.get("type", "").casefold() != requested_type):
                continue
            response = await client.get(base + f"/library/sections/{section_key}/all", params={
                "X-Plex-Token": token, "sort": "addedAt:desc", "X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit})
            response.raise_for_status()
            media = ET.fromstring(response.text)
            for item in media:
                attrs = item.attrib
                if not attrs.get("ratingKey"):
                    continue
                rows.append({"title": attrs.get("title"), "year": int(attrs["year"]) if attrs.get("year", "").isdigit() else attrs.get("year"),
                             "media_type": attrs.get("type"), "library": section.attrib.get("title"),
                             "added_at": attrs.get("addedAt"), "rating_key": attrs.get("ratingKey"),
                             "show": attrs.get("grandparentTitle"), "season": attrs.get("parentIndex"), "episode": attrs.get("index")})
    rows.sort(key=lambda row: int(row.get("added_at") or 0), reverse=True)
    return {"items": rows[:limit], "count": min(len(rows), limit), "source": "Plex", "metadata_only": True}


async def plex_sessions(_: dict[str, Any]) -> dict[str, Any]:
    token = xml_value(Path("/config") / SERVICES["plex"][1], "PlexOnlineToken")
    base, _ = SERVICES["plex"]
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(base + "/status/sessions", params={"X-Plex-Token": token})
        r.raise_for_status()
        root = ET.fromstring(r.text)
    sessions = []
    for video in root.findall("Video"):
        user = video.find("User")
        sessions.append({"title": video.attrib.get("grandparentTitle", video.attrib.get("title")), "user": user.attrib.get("title") if user is not None else None, "video_decision": video.attrib.get("videoDecision"), "transcode": video.find("TranscodeSession") is not None})
    return {"active_sessions": len(sessions), "sessions": sessions[:20]}


async def arr_get(service: str, path: str, args: dict[str, Any] | None = None) -> Any:
    return await get_json(service, path, args)


async def arr_health(service: str, _: dict[str, Any]) -> dict[str, Any]:
    api_version = "v1" if service == "lidarr" else "v3"
    rows = await arr_get(service, f"/api/{api_version}/health")
    return {"service": service, "issues": [{"level": x.get("level"), "message": x.get("message")} for x in rows[:20]], "healthy": not any(x.get("level") == "error" for x in rows)}


async def arr_queue(service: str, _: dict[str, Any]) -> dict[str, Any]:
    data = await arr_get(service, "/api/v3/queue", {"page": 1, "pageSize": 50})
    records = data.get("records", data if isinstance(data, list) else [])
    return {"service": service, "total": data.get("totalRecords", len(records)) if isinstance(data, dict) else len(records), "items": [{"title": x.get("title"), "status": x.get("status"), "sizeleft": x.get("sizeleft"), "protocol": x.get("protocol")} for x in records[:50]]}


async def sonarr_search(args):
    rows = await arr_get("sonarr", "/api/v3/series/lookup", {"term": args["query"]})
    return {"matches": [{"title": x.get("title"), "year": x.get("year"), "tvdbId": x.get("tvdbId"),
                          "tmdbId": x.get("tmdbId"),
                          "seriesType": x.get("seriesType"), "genres": x.get("genres") or [],
                          "overview": x.get("overview", "")[:240]} for x in rows[:20]]}


async def radarr_search(args):
    rows = await arr_get("radarr", "/api/v3/movie/lookup", {"term": args["query"]})
    return {"matches": [{"title": x.get("title"), "year": x.get("year"), "tmdbId": x.get("tmdbId"), "overview": x.get("overview", "")[:240]} for x in rows[:20]]}


async def lidarr_search(args):
    rows = await arr_get("lidarr", "/api/v1/artist/lookup", {"term": args["query"]})
    return {"matches": [{"artistName": x.get("artistName"), "sortName": x.get("sortName"), "foreignArtistId": x.get("foreignArtistId"), "overview": x.get("overview", "")[:240]} for x in rows[:20]]}


async def lidarr_search_album(args):
    rows = await arr_get("lidarr", "/api/v1/album/lookup", {"term": args["query"]})
    return {"matches": [{"title": x.get("title"), "artist": x.get("artist", {}).get("artistName") if isinstance(x.get("artist"), dict) else x.get("artistName"), "release_date": x.get("releaseDate"), "foreign_album_id": x.get("foreignAlbumId"), "album_type": x.get("albumType")} for x in rows[:20]]}


async def lidarr_artist_status(args):
    query = args["query"].casefold().strip()
    rows = await arr_get("lidarr", "/api/v1/artist")
    matches = []
    for x in rows if isinstance(rows, list) else []:
        name = str(x.get("artistName") or "")
        if query in name.casefold():
            stats = x.get("statistics") or {}
            matches.append({"id": x.get("id"), "artist_name": name, "monitored": x.get("monitored"),
                            "status": x.get("status"), "album_count": stats.get("albumCount"),
                            "track_count": stats.get("trackCount"), "track_file_count": stats.get("trackFileCount"),
                            "total_track_count": stats.get("totalTrackCount"),
                            "missing_track_count": (stats.get("totalTrackCount") or 0) - (stats.get("trackFileCount") or 0) if stats.get("totalTrackCount") is not None and stats.get("trackFileCount") is not None else None})
    return {"query": args["query"], "known": bool(matches), "matches": matches[:20]}


async def lidarr_import_status(args):
    ids = [int(value) for value in (args.get("album_ids") or []) if str(value).isdigit()]
    rows = []
    for album_id in ids[:50]:
        data = await arr_get("lidarr", f"/api/v1/album/{album_id}")
        stats = data.get("statistics") or {}
        rows.append({"album_id": album_id, "title": data.get("title"), "artist": (data.get("artist") or {}).get("artistName"),
                     "track_count": stats.get("trackCount"), "track_file_count": stats.get("trackFileCount"),
                     "imported": stats.get("trackCount") is not None and stats.get("trackFileCount") == stats.get("trackCount")})
    return {"album_ids": ids, "items": rows, "imported_count": sum(1 for row in rows if row["imported"]), "source": "Lidarr"}


async def frigate_status(_: dict[str, Any]) -> dict[str, Any]:
    data = await get_json("frigate", "/api/version")
    return {"reachable": True, "version": data.get("version") if isinstance(data, dict) else data}


async def frigate_stats(_: dict[str, Any]) -> dict[str, Any]:
    data = await get_json("frigate", "/api/stats")
    cameras = data.get("cameras", {}) if isinstance(data, dict) else {}
    return {"cameras": {name: {"camera_fps": value.get("camera_fps"), "detection_fps": value.get("detection_fps"), "process_fps": value.get("process_fps"), "detection_enabled": value.get("detection_enabled")} for name, value in cameras.items()}, "detector": data.get("detectors", {}) if isinstance(data, dict) else {}}


async def frigate_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    camera = str(args.get("camera", "")).strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", camera):
        return {"ok": False, "error": "camera name is required"}
    image, content_type = await service_bytes("frigate", f"/api/{camera}/latest.jpg")
    if content_type not in {"image/jpeg", "image/png"}:
        raise RuntimeError("Frigate snapshot was not an image")
    if len(image) > 8_000_000:
        raise RuntimeError("Frigate snapshot is too large")
    return {"ok": True, "camera": camera, "content_type": content_type,
            "image_base64": base64.b64encode(image).decode("ascii"), "vision_ready": True}


async def frigate_event_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    event_id = str(args.get("event_id", "")).strip()
    # Frigate event IDs commonly contain a fractional timestamp, e.g.
    # 1789405759.934032-ppqizx. Keep this bounded to identifier characters.
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", event_id):
        return {"ok": False, "error": "event_id is required"}
    image, content_type = await service_bytes("frigate", f"/api/events/{event_id}/snapshot.jpg")
    if content_type not in {"image/jpeg", "image/png"}:
        raise RuntimeError("Frigate event snapshot was not an image")
    if len(image) > 8_000_000:
        raise RuntimeError("Frigate event snapshot is too large")
    return {"ok": True, "event_id": event_id, "content_type": content_type,
            "image_base64": base64.b64encode(image).decode("ascii"), "vision_ready": True}


async def frigate_events(args: dict[str, Any]) -> dict[str, Any]:
    params = {"limit": min(int(args.get("limit", 10)), 50)}
    if args.get("camera"): params["camera"] = args["camera"]
    if args.get("label"): params["label"] = args["label"]
    rows = await get_json("frigate", "/api/events", params)
    retrieved = time.time()
    events = []
    for x in rows[:50]:
        start = event_time(x.get("start_time"))
        end = event_time(x.get("end_time"))
        age = max(0, retrieved - start) if start is not None else None
        events.append({"id": x.get("id"), "camera": x.get("camera"), "label": x.get("label"),
                       "start_time": x.get("start_time"), "end_time": x.get("end_time"),
                       "age_seconds": round(age, 1) if age is not None else None,
                       "active": end is None, "has_clip": x.get("has_clip"),
                       "has_snapshot": x.get("has_snapshot")})
    return {"events": events, "retrieved_at": datetime.fromtimestamp(retrieved, timezone.utc).isoformat()}


async def arr_missing(service: str, _: dict[str, Any]) -> dict[str, Any]:
    path = {"sonarr": "/api/v3/wanted/missing", "radarr": "/api/v3/wanted/missing", "lidarr": "/api/v1/wanted/missing"}[service]
    data = await arr_get(service, path, {"page": 1, "pageSize": 50})
    records = data.get("records", []) if isinstance(data, dict) else []
    return {"service": service, "count": data.get("totalRecords", len(records)), "items": [{"id": x.get("id"), "album_id": x.get("albumId") or x.get("id"), "title": x.get("title"), "series": x.get("series", {}).get("title") if isinstance(x.get("series"), dict) else None, "artist": x.get("artist", {}).get("artistName") if isinstance(x.get("artist"), dict) else None, "season": x.get("seasonNumber"), "episode": x.get("episodeNumber")} for x in records[:50]]}


async def netdata_summary(_: dict[str, Any]) -> dict[str, Any]:
    data = await get_json("netdata", "/api/v1/info")
    return {"reachable": True, "version": data.get("version"), "hostname": data.get("hostname"), "memory_mode": data.get("memory_mode")}


def normalize_qbit(row: dict[str, Any]) -> dict[str, Any]:
    return {"hash": row.get("hash"), "name": row.get("name"), "state": row.get("state"),
            "progress_percent": round(float(row.get("progress", 0)) * 100, 1),
            "size_bytes": row.get("size"), "completed_bytes": row.get("completed"),
            "download_speed_bytes_s": row.get("dlspeed"), "upload_speed_bytes_s": row.get("upspeed"),
            "eta_seconds": row.get("eta"), "category": row.get("category"), "save_path": row.get("save_path")}


async def qbittorrent_summary(_: dict[str, Any]) -> dict[str, Any]:
    transfer = await qbit_request("/api/v2/transfer/info")
    rows = await qbit_request("/api/v2/torrents/info", {"filter": "all"})
    items = [normalize_qbit(x) for x in rows]
    download_states = {"downloading", "stalledDL", "metaDL", "forcedDL", "queuedDL", "checkingDL", "allocating", "checkingResumeData", "stoppedDL"}
    active = [x for x in items if x["state"] in download_states and x["state"] != "stoppedDL"]
    stalled = [x for x in items if x["state"] == "stalledDL"]
    return {"torrent_count": len(items), "active_count": len(active), "stalled_count": len(stalled),
            "download_speed_bytes_s": transfer.get("dl_info_speed"), "upload_speed_bytes_s": transfer.get("up_info_speed"),
            "free_space_bytes": transfer.get("free_space_on_disk"), "items": [x for x in items if x in active or x in stalled][:50],
            "stopped_downloads": [x for x in items if x["state"] == "stoppedDL"][:20]}


async def qbittorrent_list(args: dict[str, Any]) -> dict[str, Any]:
    rows = await qbit_request("/api/v2/torrents/info", {"filter": args.get("filter", "all")})
    return {"count": len(rows), "items": [normalize_qbit(x) for x in rows[:100]]}


async def qbittorrent_get(args: dict[str, Any]) -> dict[str, Any]:
    rows = await qbit_request("/api/v2/torrents/info", {"hashes": args["hash"]})
    return {"items": [normalize_qbit(x) for x in rows[:5]]}


async def slskd_downloads(args: dict[str, Any]) -> dict[str, Any]:
    data = await slskd_get("/api/v0/transfers/downloads")
    items = []
    for user in data if isinstance(data, list) else []:
        for directory in user.get("directories", []):
            for file in directory.get("files", []):
                items.append({"id": file.get("id"), "username": file.get("username") or user.get("username"), "filename": file.get("filename"), "state": file.get("state"), "bytes_transferred": file.get("bytesTransferred"), "size_bytes": file.get("size"), "directory": directory.get("directory")})
    completed_count = sum(1 for x in items if "completed" in (x.get("state") or "").casefold() or "succeeded" in (x.get("state") or "").casefold())
    completed_items = [x for x in items if "completed" in (x.get("state") or "").casefold() or "succeeded" in (x.get("state") or "").casefold()]
    items = [x for x in items if x not in completed_items]
    query = (args.get("query") or "").casefold()
    if query:
        items = [x for x in items if query in (x.get("filename") or "").casefold() or query in (x.get("directory") or "").casefold()]
    if args.get("include_completed"):
        completed_items = [x for x in completed_items if not query or query in (x.get("filename") or "").casefold() or query in (x.get("directory") or "").casefold()]
    return {"active_count": len(items), "completed_count": len(completed_items) if query else completed_count,
            "items": items[:100], "completed_items": completed_items[:50] if args.get("include_completed") else []}


async def slskd_search_status(_: dict[str, Any]) -> dict[str, Any]:
    data = await slskd_get("/api/v0/searches")
    rows = data if isinstance(data, list) else []
    return {"count": len(rows), "searches": [{"id": x.get("id"), "search_text": x.get("searchText"), "state": x.get("state"), "is_complete": x.get("isComplete"), "response_count": x.get("responseCount"), "started_at": x.get("startedAt")} for x in rows[:50]]}


async def music_enricher_status(_: dict[str, Any]) -> dict[str, Any]:
    data = await get_json("music_enricher", "/status")
    return {"running": data.get("running"), "last": data.get("last"), "summary": data.get("summary")}


async def music_enricher_quarantine(args: dict[str, Any]) -> dict[str, Any]:
    rows = sqlite_rows(MUSIC_ENRICHER_DB, "select path, kind, status, detail, updated_at from item_state where lower(status) like '%quarantine%' or lower(kind) like '%quarantine%' order by updated_at desc limit 100")
    query = (args.get("query") or "").casefold()
    if query:
        rows = [x for x in rows if query in json.dumps(x).casefold()]
    return {"count": len(rows), "items": rows}


async def beets_status(_: dict[str, Any]) -> dict[str, Any]:
    rows = sqlite_rows(BEETS_DB, "select count(*) as tracks, count(distinct album_id) as albums, max(added) as last_added from items")
    return rows[0] if rows else {"tracks": 0, "albums": 0, "last_added": None}


async def beets_recent_imports(_: dict[str, Any]) -> dict[str, Any]:
    rows = sqlite_rows(BEETS_DB, "select id, artist, album, title, path, added from items order by added desc limit 50")
    return {"count": len(rows), "items": rows}


async def torbox_status(args: dict[str, Any]) -> dict[str, Any]:
    try:
        state = await torbox_request("/ui/api/state")
        torrents = state.get("torrents", []) if isinstance(state, dict) else []
        summary = state.get("summary", {}) if isinstance(state, dict) else {}
        query = (args.get("query") or "").casefold()
        if query:
            torrents = [x for x in torrents if query in json.dumps(x).casefold()]
        return {"reachable": True, "subscription": state.get("subscription") if isinstance(state, dict) else None,
                "summary": summary, "items": [{"hash": x.get("hash"), "name": x.get("name"), "category": x.get("category"), "state": x.get("state"), "progress_percent": round(float(x.get("progress", 0)) * 100, 1), "cloud_progress_percent": round(float(x.get("cloud_progress", 0)) * 100, 1), "local_progress_percent": round(float(x.get("local_progress", 0)) * 100, 1), "error": x.get("error")} for x in torrents[:50]]}
    except Exception as exc:
        return {"reachable": False, "error": "Torbox client unavailable", "detail": type(exc).__name__}


async def overseerr_status(_: dict[str, Any]) -> dict[str, Any]:
    data = await get_json("overseerr", "/api/v1/status")
    return {"version": data.get("version"), "commit": data.get("commitTag"), "update_available": data.get("updateAvailable")}


async def overseerr_recent_requests(_: dict[str, Any]) -> dict[str, Any]:
    rows = sqlite_rows(OVERSEERR_DB, "select r.id, r.status, r.createdAt, r.updatedAt, r.type, r.mediaId, r.requestedById, m.mediaType, m.tmdbId, m.tvdbId, m.imdbId, m.status as media_status, m.ratingKey from media_request r left join media m on m.id=r.mediaId order by r.createdAt desc limit 50")
    return {"count": len(rows), "requests": rows}


def load_lists() -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(LISTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_lists(data: dict[str, list[dict[str, Any]]]) -> None:
    LISTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = LISTS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(LISTS_PATH)


async def list_items(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("list", "grocery")).strip().casefold() or "grocery"
    items = load_lists().get(name, [])
    return {"list": name, "items": items, "count": len([item for item in items if not item.get("completed")])}


async def add_list_items(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("list", "grocery")).strip().casefold() or "grocery"
    values = args.get("items") or ([args.get("item")] if args.get("item") else [])
    data = load_lists()
    current = data.setdefault(name, [])
    added = []
    for value in values:
        text = str(value).strip()
        if text and not any(item.get("text", "").casefold() == text.casefold() and not item.get("completed") for item in current):
            entry = {"text": text, "completed": False, "created_at": now()}
            current.append(entry)
            added.append(entry)
    save_lists(data)
    audit({**AUDIT_CONTEXT.get(), "tool": "list_add", "permission": "write_low", "status": "ok", "arguments": safe_args(args), "result_summary": {"list": name, "added": added}})
    return {"list": name, "added": added, "items": current}


async def remove_list_items(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("list", "grocery")).strip().casefold() or "grocery"
    query = str(args.get("item", "")).strip().casefold()
    data = load_lists()
    current = data.setdefault(name, [])
    removed = [item for item in current if query and query in item.get("text", "").casefold()]
    data[name] = [item for item in current if item not in removed]
    save_lists(data)
    audit({**AUDIT_CONTEXT.get(), "tool": "list_remove", "permission": "write_low", "status": "ok", "arguments": safe_args(args), "result_summary": {"list": name, "removed": removed}})
    return {"list": name, "removed": removed, "items": data[name]}


async def clear_completed_list_items(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("list", "grocery")).strip().casefold() or "grocery"
    data = load_lists()
    current = data.setdefault(name, [])
    removed = [item for item in current if item.get("completed")]
    data[name] = [item for item in current if not item.get("completed")]
    save_lists(data)
    return {"list": name, "removed": removed, "items": data[name]}


async def investigate_downloads(_: dict[str, Any]) -> dict[str, Any]:
    result = {}
    calls = [("qbittorrent", qbittorrent_summary), ("sonarr", lambda a: arr_queue("sonarr", a)), ("radarr", lambda a: arr_queue("radarr", a)), ("radarr_missing_movies", lambda a: arr_missing("radarr", a)), ("lidarr", lambda a: arr_queue("lidarr", a)), ("slskd", slskd_downloads), ("torbox", torbox_status)]

    async def run(name, fn):
        try:
            return name, await fn({})
        except Exception as exc:
            return name, {"error": f"{name} unavailable", "detail": type(exc).__name__}

    values = dict(await asyncio.gather(*(run(name, fn) for name, fn in calls)))
    for name, _ in calls:
        value = values[name]
        try:
            if name == "qbittorrent":
                result[name] = {k: value.get(k) for k in ("torrent_count", "active_count", "stalled_count", "download_speed_bytes_s", "upload_speed_bytes_s", "stopped_downloads")}
                result[name]["stopped_downloads"] = result[name].get("stopped_downloads", [])[:5]
            elif name == "torbox":
                result[name] = {"reachable": value.get("reachable"), "summary": value.get("summary"), "items": value.get("items", [])[:5]}
            elif isinstance(value, dict):
                result[name] = {k: value.get(k) for k in ("service", "total", "count", "active_count", "completed_count", "stalled_count") if k in value}
                if isinstance(value.get("items"), list):
                    result[name]["items"] = value["items"][:5]
                if isinstance(value.get("searches"), list):
                    result[name]["searches"] = value["searches"][:5]
            else:
                result[name] = value
        except Exception as exc:
            result[name] = {"error": f"{name} unavailable", "detail": type(exc).__name__}
    return {"investigation": "downloads", "sources_checked": [name for name, _ in calls], "sources": result}


async def investigation_step(parent: str, name: str, fn, args: dict[str, Any]):
    started = time.monotonic()
    ctx = AUDIT_CONTEXT.get()
    try:
        value = await fn(args)
        audit({**ctx, "tool": name, "parent_tool": parent, "service": name.split("_")[0],
               "permission": "read", "arguments": safe_args(args), "status": "ok",
               "duration_ms": round((time.monotonic() - started) * 1000),
               "result_summary": audit_result(value)})
        return value
    except Exception as exc:
        audit({**ctx, "tool": name, "parent_tool": parent, "service": name.split("_")[0],
               "permission": "read", "arguments": safe_args(args), "status": "error",
               "error": type(exc).__name__, "duration_ms": round((time.monotonic() - started) * 1000)})
        raise


async def investigate_media_pipeline(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"].strip()
    entity_type = args.get("entity_type", "auto")
    result = {"query": query, "entity_type": entity_type, "sources_checked": ["plex_music", "lidarr", "qbittorrent", "slskd", "torbox", "music_enricher", "beets"]}
    calls = [("plex", "plex_search", plex_search, {"query": query, "library": "Music"}),
             ("lidarr_artist_status", "lidarr_artist_status", lidarr_artist_status, {"query": query}),
             ("lidarr_albums", "lidarr_search_album", lidarr_search_album, {"query": query}),
             ("slskd", "slskd_downloads", slskd_downloads, {"query": query, "include_completed": True}),
             ("music_enricher", "music_enricher_quarantine", music_enricher_quarantine, {"query": query}),
             ("beets", "beets_recent_imports", beets_recent_imports, {}),
             ("torbox", "torbox_status", torbox_status, {"query": query}),
             ("qbittorrent", "qbittorrent_list", lambda _: qbit_request("/api/v2/torrents/info", {"filter": "all"}), {})]
    async def run_call(name, fn_name, fn, fn_args):
        try:
            return name, await investigation_step("investigate_media_pipeline", fn_name, fn, fn_args)
        except Exception as exc:
            return name, {"error": f"{name} unavailable", "detail": type(exc).__name__}

    values = dict(await asyncio.gather(*(run_call(name, fn_name, fn, fn_args) for name, fn_name, fn, fn_args in calls)))
    for name, fn_name, fn, fn_args in calls:
        try:
            value = values[name]
            if name == "beets":
                value["items"] = [x for x in value.get("items", []) if query.casefold() in json.dumps(x).casefold()][:20]
                value["count"] = len(value["items"])
            elif name == "qbittorrent":
                result[name] = {"items": [normalize_qbit(row) for row in value if query.casefold() in (row.get("name", "") + " " + row.get("category", "")).casefold()][:5]} if isinstance(value, list) else value
                continue
            elif isinstance(value, dict) and isinstance(value.get("items"), list):
                value["items"] = value["items"][:5]
            result[name] = value
        except Exception as exc:
            result[name] = {"error": f"{name} unavailable", "detail": type(exc).__name__}
    return {"investigation": "music_pipeline", **result}


async def investigate_plex_missing(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"].strip()
    result = {"query": query}
    calls = [("plex", "plex_search", plex_search, {"query": query}), ("sonarr_series", "sonarr_search_series", sonarr_search, {"query": query}), ("sonarr_queue", "sonarr_queue", lambda a: arr_queue("sonarr", a), {}), ("qbittorrent", "qbittorrent_summary", qbittorrent_summary, {}), ("docker", "get_container_status", lambda a: asyncio.gather(container_status({"name": "sonarr"}), container_status({"name": "plex"})), {})]
    for name, fn_name, fn, fn_args in calls:
        try:
            value = await investigation_step("investigate_plex_missing", fn_name, fn, fn_args)
            result[name] = value
        except Exception as exc:
            result[name] = {"error": f"{name} unavailable", "detail": type(exc).__name__}
    return {"investigation": "plex_missing_episode", **result}


# Semantic media orchestration -------------------------------------------------
# These records describe what each backend can own.  The planner consumes this
# registry; Qwen never receives raw service credentials or arbitrary API calls.
MEDIA_CAPABILITY_REGISTRY = {
    "plex": {"owner": "plex", "media_types": ["movie", "tv", "anime", "album", "track"],
              "capabilities": {"media.library.check", "media.library.recent", "media.verify"}, "risk": "READ_ONLY"},
    "radarr": {"owner": "radarr", "media_types": ["movie"],
               "capabilities": {"media.identify", "media.wanted.read", "media.queue.read", "media.request", "media.import.read"}, "risk": "CONFIRMATION_REQUIRED"},
    "sonarr": {"owner": "sonarr", "media_types": ["tv", "anime"],
               "capabilities": {"media.identify", "media.wanted.read", "media.queue.read", "media.request", "media.import.read"}, "risk": "CONFIRMATION_REQUIRED"},
    "cli_debrid": {"owner": "cli_debrid", "media_types": ["movie", "tv", "anime"],
                   "capabilities": {"media.request", "media.search", "media.acquire", "media.queue.read", "media.verify"}, "risk": "CONFIRMATION_REQUIRED"},
    "lidarr": {"owner": "lidarr", "media_types": ["music_artist", "album", "track"],
                "capabilities": {"media.identify", "media.wanted.read", "media.queue.read", "media.import.read", "media.request"}, "risk": "CONFIRMATION_REQUIRED"},
    "torbox-client": {"owner": "torbox-client", "media_types": ["movie", "tv", "anime", "album"],
                      "capabilities": {"media.acquire", "media.queue.read", "media.download.read"}, "risk": "READ_ONLY"},
    "qBittorrent": {"owner": "qBittorrent", "media_types": ["movie", "tv", "anime", "album"],
                    "capabilities": {"media.queue.read", "media.download.read"}, "risk": "READ_ONLY"},
    "slskd": {"owner": "slskd", "media_types": ["album", "track"],
              "capabilities": {"media.search", "media.download.read"}, "risk": "READ_ONLY"},
    "music-enricher": {"owner": "music-enricher", "media_types": ["album", "track"],
                        "capabilities": {"media.enrich", "media.import.read"}, "risk": "READ_ONLY"},
    "beets": {"owner": "beets", "media_types": ["album", "track"],
              "capabilities": {"media.enrich", "media.import.read"}, "risk": "READ_ONLY"},
}

MEDIA_LIFECYCLE = ["UNKNOWN", "IDENTIFIED", "ALREADY_AVAILABLE", "WANTED", "REQUESTED", "SEARCHING",
                   "CANDIDATE_FOUND", "QUEUED", "ACQUIRING", "DOWNLOADED", "PENDING_IMPORT", "IMPORTED",
                   "ENRICHING", "AVAILABLE_IN_PLEX", "FAILED", "BLOCKED", "NOT_FOUND"]

# Central, planner-owned policy.  Provider IDs and paths are never selected by
# Qwen.  A null policy is intentional: planning must fail closed until the
# operator chooses a deterministic convention for that media class.
MEDIA_POLICY = {
    "music": {
        "manager": "lidarr",
        "root_folder": "/data/media/music",
        "quality_profile_id": 2,
        "metadata_profile_id": 1,
        "new_artist_monitor": "none",
        "target_monitor": "explicit_album_only",
        "series_type": None,
    },
    "movies": {
        "manager": "radarr",
        "root_folder": "/data/media/movies",
        "quality_profile_id": 11,
        "minimum_availability": "released",
    },
    "tv": {
        "manager": "sonarr",
        "root_folder": "/data/media/tv",
        "quality_profile_id": 9,
        "series_type": "standard",
        "season_folder": False,
    },
    "anime": {
        "manager": "sonarr",
        "root_folder": "/data/media/anime",
        "quality_profile_id": 17,
        "series_type": "anime",
        "season_folder": True,
    },
}

# Storage is deliberately split between the watch-first DB libraries and the
# permanent manager-owned libraries.  These are planner/executor invariants;
# they are never inputs supplied by Qwen.
MEDIA_STORAGE_POLICY = {
    "movie": {
        "standard": {"library": "Movies-DB", "path": "/data/symlinked/Movies"},
        "permanent": {"library": "Movies", "path": "/data/media/movies"},
    },
    "tv": {
        "standard": {"library": "TV Shows-DB", "path": "/data/symlinked/TV Shows"},
        "permanent": {"library": "TV Shows", "path": "/data/media/tv"},
    },
    "anime": {
        "standard": {"library": "Anime-DB", "path": "/data/symlinked/Anime TV Shows"},
        "permanent": {"library": "Anime", "path": "/data/media/anime"},
    },
}
STANDARD_FORBIDDEN_PATHS = {"/data/media/movies", "/data/media/tv", "/data/media/anime"}


def media_storage_policy_for(media_type: str) -> dict[str, Any]:
    return dict(MEDIA_STORAGE_POLICY.get(media_type, {}))


def _validate_standard_storage_contract(media_type: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """Fail closed if a standard request could select permanent storage."""
    policy = media_storage_policy_for(media_type)
    if not policy or "standard" not in policy:
        return False, "STANDARD_STORAGE_POLICY_MISSING"
    if payload.get("rootFolder") != "/":
        return False, "STANDARD_ROOT_MUST_BE_CLI_AUTHORITATIVE"
    if any(str(value) in STANDARD_FORBIDDEN_PATHS for value in payload.values()):
        return False, "STANDARD_PERMANENT_PATH_FORBIDDEN"
    return True, "VALID"


def media_policy_for(media_type: str) -> dict[str, Any]:
    """Return a copy so callers cannot mutate the process-wide policy."""
    key = {"album": "music", "music_artist": "music", "track": "music",
           "movie": "movies", "series": "tv"}.get(media_type, media_type)
    return dict(MEDIA_POLICY.get(key, {}))


def media_confirmation_record(*, workflow_id: str, plan: dict[str, Any], session_id: str,
                              operation: str, arguments: dict[str, Any], ttl_seconds: int = 120) -> dict[str, Any]:
    """Build a confirmation that is bound to one exact media operation."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    args_hash = hashlib.sha256(canonical.encode()).hexdigest()
    created = datetime.now(timezone.utc)
    return {
        "confirmation_id": str(uuid.uuid4()),
        "workflow_id": workflow_id,
        "plan_version_hash": plan_hash,
        "session_id": session_id,
        "canonical_media_type": plan.get("canonical_identity", {}).get("media_type"),
        "canonical_external_id": (plan.get("canonical_identity", {}).get("foreign_album_id")
                                   or plan.get("canonical_identity", {}).get("tmdb_id")
                                   or plan.get("canonical_identity", {}).get("tvdb_id")),
        "title": plan.get("canonical_identity", {}).get("title"),
        "manager": operation.split(".", 1)[0],
        "operation": operation,
        "arguments": arguments,
        "arguments_hash": args_hash,
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(seconds=ttl_seconds)).isoformat(),
        "status": "PENDING",
    }


def validate_media_confirmation(record: dict[str, Any], *, session_id: str,
                                current_plan: dict[str, Any], now_value: datetime | None = None) -> tuple[bool, str]:
    """Validate identity, session, plan, and expiry without executing anything."""
    if record.get("status") != "PENDING":
        return False, "NOT_PENDING"
    if record.get("session_id") != session_id:
        return False, "SESSION_MISMATCH"
    current_hash = hashlib.sha256(json.dumps(current_plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if record.get("plan_version_hash") != current_hash:
        return False, "PLAN_CHANGED"
    current = now_value or datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(str(record["expires_at"]))
    except (KeyError, TypeError, ValueError):
        return False, "INVALID_EXPIRY"
    if current >= expires:
        return False, "EXPIRED"
    return True, "VALID"


async def validate_media_policy(media_type: str) -> dict[str, Any]:
    """Read-only validation against the live manager configuration."""
    policy = media_policy_for(media_type)
    if not policy:
        return {"valid": False, "reason": "NO_POLICY", "media_type": media_type}
    manager = policy["manager"]
    prefix = "api/v1" if manager == "lidarr" else "api/v3"
    try:
        root_rows = await arr_get(manager, f"/{prefix}/rootfolder", {})
    except Exception as exc:
        return {"valid": False, "reason": "MANAGER_UNAVAILABLE", "media_type": media_type,
                "manager": manager, "error_type": type(exc).__name__}
    roots = {row.get("path"): row for row in root_rows if isinstance(row, dict)}
    if policy.get("root_folder") not in roots:
        return {"valid": False, "reason": "ROOT_FOLDER_MISSING", "media_type": media_type,
                "policy": policy, "available_roots": sorted(roots)}
    profile_id = policy.get("quality_profile_id")
    if profile_id is None:
        return {"valid": False, "reason": "PROFILE_POLICY_UNSET", "media_type": media_type,
                "policy": policy}
    try:
        profiles = await arr_get(manager, f"/{prefix}/qualityprofile", {})
    except Exception as exc:
        return {"valid": False, "reason": "MANAGER_UNAVAILABLE", "media_type": media_type,
                "manager": manager, "error_type": type(exc).__name__}
    profile = next((row for row in profiles if row.get("id") == profile_id), None)
    if profile is None:
        return {"valid": False, "reason": "QUALITY_PROFILE_MISSING", "media_type": media_type,
                "policy": policy, "available_profiles": [row.get("id") for row in profiles]}
    if manager == "lidarr":
        try:
            metadata = await arr_get(manager, "/api/v1/metadataprofile", {})
        except Exception as exc:
            return {"valid": False, "reason": "MANAGER_UNAVAILABLE", "media_type": media_type,
                    "manager": manager, "error_type": type(exc).__name__}
        if not any(row.get("id") == policy.get("metadata_profile_id") for row in metadata):
            return {"valid": False, "reason": "METADATA_PROFILE_MISSING", "media_type": media_type,
                    "policy": policy}
    return {"valid": True, "media_type": media_type, "policy": policy,
            "profile": {"id": profile.get("id"), "name": profile.get("name")},
            "root_folder": roots[policy["root_folder"]].get("path")}


def _media_workflows() -> list[dict[str, Any]]:
    try:
        value = json.loads(MEDIA_WORKFLOWS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_media_workflows(rows: list[dict[str, Any]]) -> None:
    MEDIA_WORKFLOWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEDIA_WORKFLOWS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[-100:], indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEDIA_WORKFLOWS_PATH)


def _media_goal_parts(goal: str, media_type: str | None = None) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", str(goal or "").strip())
    lowered = text.casefold()
    mode = "permanent" if re.search(r"\b(?:permanent(?:ly)?|keep)\b", lowered) else "standard"
    kind = media_type
    if not kind:
        if re.search(r"\b(album|music|song|artist|record|release)\b|\bby\s+[^.]+", lowered):
            kind = "album"
        elif re.search(r"\b(anime|series|show|season|episode|kai)\b", lowered):
            kind = "anime" if "anime" in lowered else "tv"
        elif re.search(r"\b(movie|film|hobbit)\b", lowered):
            kind = "movie"
        else:
            kind = "unknown"
    artist = None
    title = text
    by_match = re.search(r"\b(.+?)\s+by\s+(.+?)(?:[.!?]|$)", text, re.I)
    if by_match:
        title, artist = by_match.group(1), by_match.group(2)
    title = re.sub(r"^\s*(?:get|find|add|request|do i have|is there)\s+", "", title, flags=re.I)
    title = re.sub(r"\b(?:and )?(?:get|add) (?:it|that)\b", "", title, flags=re.I).strip(" .?!")
    season_match = re.search(r"\bseason\s+(\d+)\s+(?:of\s+)?(.+?)(?:[.!?]|$)", text, re.I)
    episode_match = re.search(r"\bepisode\s+(\d+)\s+(?:of\s+)?(.+?)(?:[.!?]|$)", text, re.I)
    season_scope = [int(season_match.group(1))] if season_match else []
    episode_scope = [int(episode_match.group(1))] if episode_match else []
    if season_match:
        title = season_match.group(2).strip(" .?!")
    elif episode_match:
        title = episode_match.group(2).strip(" .?!")
    title = re.sub(r"\s+and\s+keep(?:\s+it)?\s+permanently\s*$", "", title, flags=re.I).strip(" .?!")
    if kind in {"movie", "tv", "anime"}:
        title = re.sub(r"\b(?:the|original|animated|version|movie|film|series|show|whole|entire|all)\b", " ", title, flags=re.I)
        title = re.sub(r"\bof\b", " ", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip(" .?!") or text
    return {"raw_goal": text, "media_type": kind, "title_query": title, "artist_query": artist,
            "action": "ensure_available" if re.search(r"\b(get|find|add|request)\b", lowered) else "inspect",
            "mode": mode, "season_scope": season_scope, "episode_scope": episode_scope}


def _pick_match(matches: list[dict[str, Any]], title: str, artist: str | None = None) -> tuple[dict[str, Any] | None, bool]:
    if not matches:
        return None, False
    title_cf = title.casefold()
    artist_cf = (artist or "").casefold()
    exact = [m for m in matches if str(m.get("title") or m.get("artistName") or "").casefold() == title_cf
              and (not artist_cf or artist_cf in json.dumps(m).casefold())]
    if len(exact) == 1:
        return exact[0], False
    wanted = set(re.findall(r"[a-z0-9]+", title_cf))
    if "kai" in wanted:
        kai_matches = [row for row in matches if "kai" in set(re.findall(r"[a-z0-9]+", str(row.get("title") or "").casefold()))]
        if len(kai_matches) == 1:
            return kai_matches[0], False
    scored = []
    for row in matches:
        candidate = str(row.get("title") or row.get("artistName") or "").casefold()
        tokens = set(re.findall(r"[a-z0-9]+", candidate))
        score = len(wanted & tokens) / max(len(wanted), 1)
        if artist_cf and artist_cf in json.dumps(row).casefold():
            score += 1.0
        scored.append((score, row))
    scored.sort(key=lambda pair: -pair[0])
    if len(scored) == 1:
        return scored[0][1], False
    # A strong top match is safe; a close tie remains a clarification case.
    margin = scored[0][0] - scored[1][0]
    return (scored[0][1], False) if scored[0][0] >= 0.75 and margin >= 0.25 else (None, True)


async def media_plan_goal(args: dict[str, Any]) -> dict[str, Any]:
    """Read/plan only. It never adds, searches, downloads, imports, or mutates a provider."""
    parts = _media_goal_parts(args.get("goal", ""), args.get("media_type"))
    kind, title, artist = parts["media_type"], parts["title_query"], parts["artist_query"]
    plan: dict[str, Any] = {"plan_only": True, "goal": parts, "mode": parts.get("mode", "standard"), "writes_required": [], "confirmation_required": False,
                            "canonical_identity": None, "current_state": "UNKNOWN", "steps": [], "providers": {}}
    matches: list[dict[str, Any]] = []
    if kind == "album":
        lookup = await lidarr_search_album({"query": " ".join(x for x in (title, artist) if x)})
        matches = lookup.get("matches", [])
        identity, ambiguous = _pick_match(matches, title, artist)
        plan["steps"].append({"capability": "media.identify", "owner": "lidarr", "reason": "canonical album identity required"})
        if identity:
            plan["canonical_identity"] = {"media_type": "album", "title": identity.get("title"), "artist": identity.get("artist"),
                                           "year": str(identity.get("release_date", ""))[:4] or None, "foreign_album_id": identity.get("foreign_album_id"),
                                           "album_type": identity.get("album_type")}
        plan["ambiguous"] = ambiguous
        plex = await plex_library_lookup({"query": title, "library": "Music"})
        plan["providers"]["plex_music"] = plex
        plan["steps"].append({"capability": "media.library.check", "owner": "plex", "reason": "avoid duplicate acquisition"})
        if plan["canonical_identity"] and not ambiguous:
            managed = await arr_get("lidarr", "/api/v1/album", {})
            fid = plan["canonical_identity"].get("foreign_album_id")
            owned = next((row for row in managed if str(row.get("foreignAlbumId")) == str(fid)), None) if isinstance(managed, list) else None
            plan["providers"]["lidarr"] = {"managed": bool(owned), "album_id": owned.get("id") if owned else None,
                                             "monitored": owned.get("monitored") if owned else None,
                                             "track_file_count": (owned.get("statistics") or {}).get("trackFileCount") if owned else None,
                                             "track_count": (owned.get("statistics") or {}).get("trackCount") if owned else None}
            plan["steps"].append({"capability": "media.wanted.read", "owner": "lidarr", "reason": "determine whether the album is already managed"})
    elif kind == "movie":
        lookup = await radarr_search({"query": title})
        matches = lookup.get("matches", [])
        identity, ambiguous = _pick_match(matches, title)
        if re.search(r"\boriginal\b", parts["raw_goal"], re.I) and re.search(r"\banimated\b", parts["raw_goal"], re.I):
            preferred = next((row for row in matches
                              if str(row.get("title", "")).casefold().strip() in {title.casefold(), f"the {title.casefold()}"}
                              and str(row.get("year", "")).isdigit() and int(row.get("year")) <= 1985), None)
            if preferred:
                identity, ambiguous = preferred, False
        plan["steps"].append({"capability": "media.identify", "owner": "radarr", "reason": "canonical movie identity required"})
        if identity:
            plan["canonical_identity"] = {"media_type": "movie", "title": identity.get("title"), "year": identity.get("year"), "tmdb_id": identity.get("tmdbId")}
        plan["ambiguous"] = ambiguous
        plex_identity = {"tmdb_id": identity.get("tmdbId")} if identity else {}
        permanent_match = await plex_match_canonical_media({"media_type": "movie", "title": identity.get("title", title),
                                                            "year": identity.get("year"), "canonical_external_ids": plex_identity,
                                                            "library": "Movies"}) if identity else {"matched": False, "candidates": []}
        standard_match = await plex_match_canonical_media({"media_type": "movie", "title": identity.get("title", title),
                                                           "year": identity.get("year"), "canonical_external_ids": plex_identity,
                                                           "library": "Movies-DB"}) if identity else {"matched": False, "candidates": []}
        plan["providers"]["plex"] = {"permanent": permanent_match, "standard": standard_match,
                                      "available": bool(permanent_match.get("matched") or standard_match.get("matched"))}
        plan["steps"].append({"capability": "media.library.check", "owner": "plex", "reason": "avoid duplicate acquisition"})
        if identity:
            managed = await arr_get("radarr", "/api/v3/movie", {})
            owned = next((row for row in managed if str(row.get("tmdbId")) == str(identity.get("tmdbId"))), None) if isinstance(managed, list) else None
            plan["providers"]["radarr"] = {"managed": bool(owned), "movie_id": owned.get("id") if owned else None, "has_file": owned.get("hasFile") if owned else None}
    elif kind in {"tv", "anime"}:
        lookup = await sonarr_search({"query": title})
        matches = lookup.get("matches", [])
        identity, ambiguous = _pick_match(matches, title)
        # Sonarr's canonical lookup classification outranks the loose language
        # heuristic (e.g. "Dragon Ball Z Kai" is anime even when the user does
        # not say the word "anime"). This selects the policy for a new item;
        # existing managed series retain their current profile.
        if identity and (str(identity.get("seriesType") or "").casefold() == "anime"
                         or any(str(genre).casefold() == "anime" for genre in (identity.get("genres") or []))):
            kind = "anime"
            parts["media_type"] = "anime"
        plan["steps"].append({"capability": "media.identify", "owner": "sonarr", "reason": "canonical series identity required"})
        if identity:
            plan["canonical_identity"] = {"media_type": kind, "title": identity.get("title"), "year": identity.get("year"),
                                           "tvdb_id": identity.get("tvdbId"), "tmdb_id": identity.get("tmdbId"), "series_type": identity.get("seriesType"),
                                           "genres": identity.get("genres") or []}
        plan["ambiguous"] = ambiguous
        if identity:
            plex_identity = {key: identity.get(key) for key in ("tmdb_id", "tvdb_id") if identity.get(key)}
            plan["providers"]["plex"] = await plex_match_canonical_media({"media_type": "show", "title": identity.get("title", title),
                                                                             "year": identity.get("year"), "canonical_external_ids": plex_identity,
                                                                             "library": "TV Shows"})
            managed = await arr_get("sonarr", "/api/v3/series", {})
            owned = next((row for row in managed if str(row.get("tvdbId")) == str(identity.get("tvdb_id"))), None) if isinstance(managed, list) else None
            plan["providers"]["sonarr"] = {"managed": bool(owned), "series_id": owned.get("id") if owned else None,
                                             "monitored": owned.get("monitored") if owned else None,
                                             "episode_file_count": (owned.get("statistics") or {}).get("episodeFileCount") if owned else None,
                                             "episode_count": (owned.get("statistics") or {}).get("episodeCount") if owned else None}
            plan["steps"].append({"capability": "media.library.check", "owner": "plex", "reason": "avoid duplicate acquisition"})
            plan["steps"].append({"capability": "media.wanted.read", "owner": "sonarr", "reason": "determine whether the series is already managed"})
    else:
        plan["ambiguous"] = True
    identity = plan.get("canonical_identity") or {}
    plex_provider = plan.get("providers", {}).get("plex") or {}
    if kind == "movie" and "permanent" in plex_provider:
        plex_available = bool(plex_provider.get("permanent", {}).get("matched") or plex_provider.get("standard", {}).get("matched"))
        plex_matches = []
    else:
        plex_available = bool(plex_provider.get("matched"))
        plex_matches = plex_provider.get("matches", [])
    has_requested_scope = bool(parts.get("season_scope") or parts.get("episode_scope"))
    if identity and not has_requested_scope and plex_available:
        plan["current_state"] = "AVAILABLE_IN_PLEX"
    elif plan.get("ambiguous"):
        plan["current_state"] = "AMBIGUOUS_IDENTITY"
    elif identity:
        owner = "lidarr" if kind == "album" else "radarr" if kind == "movie" else "sonarr"
        provider = plan.get("providers", {}).get(owner, {})
        if provider.get("managed"):
            plan["current_state"] = "WANTED" if not provider.get("has_file") and not (provider.get("track_file_count") == provider.get("track_count") and provider.get("track_count") is not None) else "IMPORTED"
        else:
            plan["current_state"] = "IDENTIFIED"
            if parts["action"] == "ensure_available":
                plan["writes_required"] = [{"owner": owner, "capability": "media.request", "risk": "CONFIRMATION_REQUIRED", "status": "NOT_EXECUTED"}]
                plan["confirmation_required"] = True
    # Standard/watch-first movie and TV goals never create managed *arr
    # records.  They bridge only the canonical TMDB identity and exact season
    # scope to cli_debrid.  Music remains manager-owned for now.
    if identity and parts.get("mode") == "standard" and kind in {"movie", "tv", "anime"} and parts.get("action") == "ensure_available":
        if parts.get("episode_scope"):
            plan["current_state"] = "BLOCKED"
            plan["blocked_reason"] = "STANDARD_EPISODE_SCOPE_UNSUPPORTED"
            plan["writes_required"] = []
            plan["confirmation_required"] = False
        else:
            plan["writes_required"] = [{"owner": "cli_debrid", "capability": "media.standard_request",
                                         "risk": "CONFIRMATION_REQUIRED", "status": "NOT_EXECUTED"}]
            plan["confirmation_required"] = True
            plan["bounded_write_plan"] = [{
                "operation": "POST /webhook/api/v1/request",
                "arguments": {
                    "mediaType": "movie" if kind == "movie" else "tv",
                    "mediaId": identity.get("tmdb_id"),
                    "seasons": parts.get("season_scope", []) if kind != "movie" else [],
                    "is4k": False, "serverId": 0, "profileId": 0, "rootFolder": "/", "userId": 1,
                },
                "ownership": "cli_debrid chooses scrapers, candidates, and acquisition",
            }]
            plan["steps"].append({"capability": "media.standard_request", "owner": "cli_debrid",
                                   "reason": "watch-first acquisition without creating a managed *arr record"})

    if plan.get("writes_required") and not (parts.get("mode") == "standard" and kind in {"movie", "tv", "anime"}):
        policy_type = "music" if kind == "album" else "movies" if kind == "movie" else kind
        policy_status = await validate_media_policy(policy_type)
        plan["policy"] = policy_status
        if not policy_status.get("valid"):
            plan["writes_required"][0]["status"] = "BLOCKED_POLICY"
            plan["confirmation_required"] = False
            plan["blocked_reason"] = policy_status.get("reason")
        elif kind == "movie":
            plan["bounded_write_plan"] = [
                {"operation": "POST /api/v3/movie", "arguments": {
                    "base": "canonical Radarr lookup resource",
                    "rootFolderPath": policy_status["root_folder"],
                    "qualityProfileId": policy_status["profile"]["id"],
                    "minimumAvailability": policy_status["policy"]["minimum_availability"],
                    "monitored": True, "addOptions": {"searchForMovie": False}}},
                {"operation": "POST /api/v3/command", "arguments": {
                    "name": "MoviesSearch", "movieIds": ["newly-created-radarr-movie-id"]}},
            ]
        elif kind == "anime":
            plan["bounded_write_plan"] = [
                {"operation": "POST /api/v3/series", "arguments": {
                    "base": "canonical Sonarr lookup resource",
                    "rootFolderPath": policy_status["root_folder"],
                    "qualityProfileId": policy_status["profile"]["id"],
                    "seriesType": policy_status["policy"]["series_type"],
                    "seasonFolder": policy_status["policy"]["season_folder"],
                    "monitored": True,
                    "addOptions": {"searchForMissingEpisodes": False, "searchForCutoffUnmetEpisodes": False}}},
                {"operation": "POST /api/v3/command", "arguments": {
                    "name": "SeriesSearch", "seriesId": "newly-created-sonarr-series-id"}},
            ]
        elif kind == "album":
            plan["bounded_write_plan"] = [
                {"operation": "POST /api/v1/artist (only if artist is absent)", "arguments": {
                    "rootFolderPath": policy_status["root_folder"],
                    "qualityProfileId": policy_status["profile"]["id"],
                    "metadataProfileId": policy_status["policy"]["metadata_profile_id"],
                    "monitorNewItems": "none", "addOptions": {"monitor": "none"}}},
                {"operation": "PUT /api/v1/album/monitor", "arguments": {
                    "albumIds": ["exact-Lidarr-album-id"], "monitored": True}},
                {"operation": "POST /api/v1/command", "arguments": {
                    "name": "AlbumSearch", "albumIds": ["exact-Lidarr-album-id"]}},
            ]
        plan["steps"].append({"capability": "media.request", "owner": plan["writes_required"][0]["owner"],
                               "reason": "item is identified but not yet managed; execution is disabled in plan mode",
                               "policy_status": policy_status.get("reason", "VALID")})
    plan["lifecycle_states"] = MEDIA_LIFECYCLE
    plan["recommended_workflow"] = " / ".join(step["capability"] for step in plan["steps"])
    rows = _media_workflows()
    canonical_id = identity.get("foreign_album_id") or identity.get("tmdb_id") or identity.get("tvdb_id") or identity.get("title")
    key_data = {"type": kind, "id": canonical_id, "mode": parts.get("mode", "standard"),
                "season_scope": sorted(set(parts.get("season_scope") or [])),
                "episode_scope": sorted(set(parts.get("episode_scope") or []))}
    key = json.dumps(key_data, sort_keys=True)
    legacy_key = json.dumps({"type": kind, "id": canonical_id}, sort_keys=True)
    existing = next((row for row in rows if row.get("dedupe_key") == key or
                     (row.get("dedupe_key") == legacy_key and row.get("mode", "standard") == parts.get("mode", "standard"))), None)
    workflow = existing or {"workflow_id": str(uuid.uuid4()), "dedupe_key": key, "created_at": now(), "action_history": []}
    active_states = {"REQUESTED", "SEARCHING", "ACQUIRING", "VERIFYING", "ACQUIRED", "AVAILABLE_IN_PLEX"}
    preserved_state = existing.get("current_state") if existing and existing.get("current_state") in active_states else plan["current_state"]
    workflow.update({"media_type": kind, "canonical_identity": identity, "desired_goal": parts["action"], "mode": parts.get("mode", "standard"),
                     "current_state": preserved_state, "last_checked": now(), "plan_only": True})
    if not existing:
        rows.append(workflow)
    _save_media_workflows(rows)
    plan["workflow_id"] = workflow["workflow_id"]
    if plan.get("confirmation_required"):
        # Dry-run output exposes the exact binding that a future write executor
        # would persist. It is never consumed or authorized by this planner.
        confirmation_plan = dict(plan)
        confirmation_plan.pop("confirmation_record", None)
        bridge_arguments = {
            "workflow_id": workflow["workflow_id"],
            "mode": "standard",
            "media_type": "tv" if kind in {"tv", "anime"} else kind,
            "canonical_external_id": identity.get("tmdb_id"),
            "season_scope": parts.get("season_scope", []),
            "episode_scope": [],
        }
        confirmation_arguments = (bridge_arguments
                                  if plan.get("mode") == "standard" and kind in {"movie", "tv", "anime"}
                                  else {"workflow_id": workflow["workflow_id"], "plan_version": "read-only-dry-run",
                                        "canonical_identity": identity, "bounded_write_plan": plan.get("bounded_write_plan", [])})
        plan["confirmation_record"] = media_confirmation_record(
            workflow_id=workflow["workflow_id"],
            plan=confirmation_plan,
            session_id=str(args.get("session_id") or "plan-only"),
            operation=("cli_debrid.media_standard_request"
                       if plan.get("mode") == "standard" and kind in {"movie", "tv", "anime"}
                       else f"{plan['writes_required'][0].get('owner')}.media_execute_goal"),
            arguments=confirmation_arguments,
        )
        workflow.update({"plan_version_hash": plan["confirmation_record"]["plan_version_hash"],
                         "confirmation_id": plan["confirmation_record"]["confirmation_id"],
                         "confirmation_status": "PENDING"})
        _save_media_workflows(rows)
    plan["idempotent"] = True
    return plan


async def media_get_workflow(args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = str(args.get("workflow_id", ""))
    row = next((item for item in _media_workflows() if item.get("workflow_id") == workflow_id), None)
    return {"found": bool(row), "workflow": row}


def _build_cli_debrid_request(args: dict[str, Any]) -> dict[str, Any]:
    """Translate one bounded Home-AI goal into cli_debrid's request shape."""
    media_type = str(args.get("media_type", "")).casefold()
    if media_type not in {"movie", "tv"}:
        raise ValueError("STANDARD_SCOPE_UNSUPPORTED")
    try:
        media_id = int(args.get("canonical_external_id"))
    except (TypeError, ValueError):
        raise ValueError("CANONICAL_EXTERNAL_ID_REQUIRED") from None
    if media_id <= 0:
        raise ValueError("CANONICAL_EXTERNAL_ID_REQUIRED")
    if args.get("episode_scope"):
        raise ValueError("STANDARD_EPISODE_SCOPE_UNSUPPORTED")

    seasons = args.get("season_scope") or []
    if media_type == "movie" and seasons:
        raise ValueError("MOVIE_SEASON_SCOPE_INVALID")
    if not isinstance(seasons, list) or any(not isinstance(item, int) or item < 0 or item > 99 for item in seasons):
        raise ValueError("INVALID_SEASON_SCOPE")

    payload: dict[str, Any] = {
        "mediaType": media_type,
        "mediaId": media_id,
        "is4k": False,
        "serverId": 0,
        "profileId": 0,
        "rootFolder": "/",
        "userId": 1,
    }
    if media_type == "tv" and seasons:
        payload["seasons"] = sorted(set(seasons))
    return payload


def _standard_bridge_secret() -> str:
    if CLIDEBRID_BRIDGE_TOKEN:
        return CLIDEBRID_BRIDGE_TOKEN
    try:
        return Path(CLIDEBRID_BRIDGE_TOKEN_FILE).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def _standard_argument_binding(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": str(args.get("workflow_id", "")),
        "mode": "standard",
        "media_type": str(args.get("media_type", "")).casefold(),
        "canonical_external_id": int(args.get("canonical_external_id")),
        "season_scope": sorted(set(args.get("season_scope") or [])),
        "episode_scope": [],
    }


def _standard_binding_hash(args: dict[str, Any]) -> str:
    value = json.dumps(_standard_argument_binding(args), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def _workflow_for_id(workflow_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    rows = _media_workflows()
    return rows, next((row for row in rows if row.get("workflow_id") == workflow_id), None)


def _save_workflow_update(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    row["updated_at"] = now()
    _save_media_workflows(rows)


async def media_standard_request(args: dict[str, Any]) -> dict[str, Any]:
    """Bounded standard/watch-first bridge; execution is disabled by default."""
    allowed_keys = {"workflow_id", "media_type", "canonical_external_id", "canonical_title", "season_scope",
                    "episode_scope", "confirmation_context", "session_id"}
    unexpected = sorted(set(args) - allowed_keys)
    if unexpected:
        return {"status": "rejected", "reason": "UNEXPECTED_ARGUMENT", "fields": unexpected, "write_executed": False}
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not workflow_id or len(workflow_id) > 128:
        return {"status": "rejected", "reason": "WORKFLOW_ID_REQUIRED", "write_executed": False}
    try:
        payload = _build_cli_debrid_request(args)
    except ValueError as exc:
        return {"status": "rejected", "reason": str(exc), "write_executed": False}

    storage_ok, storage_reason = _validate_standard_storage_contract(str(args.get("media_type", "")), payload)
    if not storage_ok:
        return {"status": "rejected", "reason": storage_reason, "write_executed": False}

    if args.get("episode_scope"):
        return {"status": "rejected", "reason": "STANDARD_EPISODE_SCOPE_UNSUPPORTED", "write_executed": False}
    if not STANDARD_MEDIA_WRITES_ENABLED:
        return {"status": "disabled", "reason": "STANDARD_MEDIA_WRITES_DISABLED", "write_executed": False, "request_shape": payload}
    if payload["mediaType"] == "movie" and not STANDARD_MOVIE_WRITES_ENABLED:
        return {"status": "disabled", "reason": "STANDARD_MOVIE_WRITES_DISABLED", "write_executed": False, "request_shape": payload}
    if payload["mediaType"] == "tv" and not STANDARD_SEASON_WRITES_ENABLED:
        return {"status": "disabled", "reason": "STANDARD_SEASON_WRITES_DISABLED", "write_executed": False, "request_shape": payload}
    if not _standard_bridge_secret():
        return {"status": "disabled", "reason": "BRIDGE_SECRET_MISSING", "write_executed": False, "request_shape": payload}

    required_binding = {"confirmation_id", "session_id", "plan_version_hash", "arguments_hash", "expires_at", "status"}
    binding = args.get("confirmation_context")
    if not isinstance(binding, dict) or not required_binding.issubset(binding):
        return {"status": "rejected", "reason": "CONFIRMATION_BINDING_REQUIRED", "write_executed": False}
    if binding.get("status") != "PENDING" or str(args.get("session_id", "")) != str(binding.get("session_id")):
        return {"status": "rejected", "reason": "CONFIRMATION_SESSION_OR_STATUS_INVALID", "write_executed": False}
    try:
        if datetime.now(timezone.utc) >= datetime.fromisoformat(str(binding["expires_at"])):
            return {"status": "rejected", "reason": "CONFIRMATION_EXPIRED", "write_executed": False}
    except (TypeError, ValueError):
        return {"status": "rejected", "reason": "CONFIRMATION_EXPIRY_INVALID", "write_executed": False}
    if str(binding.get("arguments_hash")) != _standard_binding_hash(args):
        return {"status": "rejected", "reason": "ARGUMENT_HASH_MISMATCH", "write_executed": False}

    rows, workflow = _workflow_for_id(workflow_id)
    if not workflow or workflow.get("mode") != "standard":
        return {"status": "rejected", "reason": "WORKFLOW_NOT_FOUND_OR_MODE_INVALID", "write_executed": False}
    storage_kind = str(workflow.get("canonical_identity", {}).get("media_type") or payload["mediaType"])
    storage_policy = media_storage_policy_for(storage_kind)
    if not storage_policy or not storage_policy.get("standard"):
        return {"status": "rejected", "reason": "STANDARD_STORAGE_POLICY_MISSING", "write_executed": False}
    if str(workflow.get("plan_version_hash")) != str(binding.get("plan_version_hash")):
        return {"status": "rejected", "reason": "PLAN_HASH_MISMATCH", "write_executed": False}
    if workflow.get("canonical_identity", {}).get("tmdb_id") != payload["mediaId"]:
        return {"status": "rejected", "reason": "CANONICAL_ID_MISMATCH", "write_executed": False}
    if binding.get("confirmation_id") != workflow.get("confirmation_id"):
        return {"status": "rejected", "reason": "CONFIRMATION_ID_MISMATCH", "write_executed": False}
    if workflow.get("confirmation_status") != "PENDING":
        return {"status": "rejected", "reason": "CONFIRMATION_ALREADY_CONSUMED", "write_executed": False}
    if workflow.get("current_state") in {"REQUESTED", "SEARCHING", "ACQUIRING", "VERIFYING", "ACQUIRED", "AVAILABLE_IN_PLEX"}:
        return {"status": "no_op", "reason": "STANDARD_WORKFLOW_ALREADY_ACTIVE_OR_SATISFIED", "write_executed": False, "workflow_id": workflow_id}

    title = str(args.get("canonical_title") or workflow.get("canonical_identity", {}).get("title") or "").strip()
    if title and not payload.get("seasons"):
        permanent_library = storage_policy["permanent"]["library"]
        standard_library = storage_policy["standard"]["library"]
        identity = workflow.get("canonical_identity", {})
        canonical_ids = {key: identity.get(key) for key in ("tmdb_id", "tvdb_id", "imdb_id") if identity.get(key)}
        permanent = await plex_match_canonical_media({"media_type": "movie" if payload["mediaType"] == "movie" else "show",
                                                       "title": title, "year": identity.get("year"),
                                                       "canonical_external_ids": canonical_ids, "library": permanent_library})
        standard = await plex_match_canonical_media({"media_type": "movie" if payload["mediaType"] == "movie" else "show",
                                                      "title": title, "year": identity.get("year"),
                                                      "canonical_external_ids": canonical_ids, "library": standard_library})
        if permanent.get("matched") and standard.get("matched"):
            workflow.update({"current_state": "AVAILABLE_IN_PLEX", "canonical_state": "AVAILABLE", "storage_class": "both"})
            _save_workflow_update(rows, workflow)
            return {"status": "no_op", "reason": "ALREADY_AVAILABLE_IN_BOTH_LIBRARIES", "write_executed": False, "workflow_id": workflow_id}
        if permanent.get("matched"):
            workflow.update({"current_state": "AVAILABLE_IN_PLEX", "canonical_state": "AVAILABLE", "storage_class": "permanent_local"})
            _save_workflow_update(rows, workflow)
            return {"status": "no_op", "reason": "ALREADY_AVAILABLE_PERMANENTLY", "write_executed": False, "workflow_id": workflow_id}
        if standard.get("matched"):
            workflow.update({"current_state": "AVAILABLE_IN_PLEX", "canonical_state": "AVAILABLE", "storage_class": "debrid"})
            _save_workflow_update(rows, workflow)
            return {"status": "no_op", "reason": "ALREADY_AVAILABLE_STANDARD", "write_executed": False, "workflow_id": workflow_id}

    # Claim the confirmation before the network write. A replay sees this
    # state and cannot submit the same standard request twice.
    workflow.update({"confirmation_status": "SUBMITTING", "canonical_state": "REQUESTED", "mode": "standard"})
    _save_workflow_update(rows, workflow)

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    headers["X-Home-AI-Bridge-Token"] = _standard_bridge_secret()
    async with httpx.AsyncClient(timeout=12, headers=headers) as client:
        try:
            await client.get(f"{CLIDEBRID_BASE}/api/v1/status")
            response = await client.post(f"{CLIDEBRID_BASE}/api/v1/request", json=payload)
            response.raise_for_status()
            body = response.json() if response.content else {}
        except Exception:
            workflow.update({"confirmation_status": "FAILED", "canonical_state": "FAILED", "failure_reason": "BRIDGE_UNAVAILABLE"})
            _save_workflow_update(rows, workflow)
            raise
    workflow.update({"confirmation_status": "CONSUMED", "current_state": "REQUESTED", "canonical_state": "REQUESTED",
                     "storage_class": "debrid", "standard_library": storage_policy["standard"]["library"],
                     "cli_debrid_state": "Wanted", "submitted_at": now(), "request_shape": payload,
                     "request_response": {key: body.get(key) for key in ("id", "status", "type", "createdAt", "updatedAt") if key in body}})
    _save_workflow_update(rows, workflow)
    return {
        "status": "submitted",
        "write_executed": True,
        "workflow_id": workflow_id,
        "cli_debrid": {key: body.get(key) for key in ("id", "status", "type", "createdAt", "updatedAt") if key in body},
        "request_shape": payload,
    }


async def media_policy_status(args: dict[str, Any]) -> dict[str, Any]:
    """Read-only policy/config validation; never changes a manager."""
    requested = str(args.get("media_type", "all")).casefold()
    types = [requested] if requested != "all" else ["music", "movies", "tv", "anime"]
    return {"policies": [await validate_media_policy(media_type) for media_type in types]}


async def media_storage_status(args: dict[str, Any]) -> dict[str, Any]:
    """Read-only storage contract exposed for tracing and final verification."""
    requested = str(args.get("media_type", "all")).casefold()
    types = [requested] if requested != "all" else ["movie", "tv", "anime"]
    return {
        "contracts": [
            {"media_type": media_type, **media_storage_policy_for(media_type),
             "standard_write_root_owned_by": "cli_debrid",
             "standard_forbidden_paths": sorted(STANDARD_FORBIDDEN_PATHS),
             "standard_cleanup_allowed": False}
            for media_type in types
        ]
    }


REGISTRY = [
    ("get_server_overview", "Current host, uptime, RAM/storage summary.", "read", "server", {}, server_overview),
    ("get_storage_status", "Current user-share and cache storage usage.", "read", "storage", {}, storage_status),
    ("get_gpu_status", "Current NVIDIA GPU telemetry and VRAM usage.", "read", "gpu", {}, gpu_status),
    ("list_containers", "List current Docker containers and status.", "read", "docker", {"status": {"type": "string"}}, list_containers),
    ("get_container_status", "Get one Docker container status.", "read", "docker", {"name": {"type": "string", "required": True}}, container_status),
    ("get_container_logs", "Get a capped tail of one container's logs.", "read", "docker", {"name": {"type": "string", "required": True}, "lines": {"type": "integer"}}, container_logs),
    ("restart_container", "Restart a named Docker container after confirmation.", "confirm", "docker", {"name": {"type": "string", "required": True}}, restart_container),
    ("plex_search", "Search Plex libraries and report matching library.", "read", "plex", {"query": {"type": "string", "required": True}, "library": {"type": "string"}}, plex_search),
    ("plex_library_lookup", "Fast metadata-only Plex availability lookup for media planning; does not query download services.", "read", "plex", {"query": {"type": "string", "required": True}, "library": {"type": "string"}}, plex_library_lookup),
    ("plex_artist_library", "List albums and tracks actually present for an exact artist in Plex Music.", "read", "plex", {"query": {"type": "string", "required": True}}, plex_artist_library),
    ("plex_library_counts", "Get distinct Plex library counts.", "read", "plex", {}, plex_counts),
    ("plex_recently_added", "Get the newest items from Plex library metadata; this does not query download services.", "read", "plex", {"media_type": {"type": "string"}, "library": {"type": "string"}, "limit": {"type": "integer"}}, plex_recently_added),
    ("plex_current_sessions", "Get active Plex playback and transcode sessions.", "read", "plex", {}, plex_sessions),
    ("sonarr_search_series", "Search Sonarr for a TV series.", "read", "sonarr", {"query": {"type": "string", "required": True}}, sonarr_search),
    ("sonarr_queue", "Get the current Sonarr queue.", "read", "sonarr", {}, lambda a: arr_queue("sonarr", a)),
    ("sonarr_health", "Get Sonarr health issues.", "read", "sonarr", {}, lambda a: arr_health("sonarr", a)),
    ("sonarr_missing_episodes", "Get Sonarr missing episodes.", "read", "sonarr", {}, lambda a: arr_missing("sonarr", a)),
    ("radarr_search_movie", "Search Radarr for a movie.", "read", "radarr", {"query": {"type": "string", "required": True}}, radarr_search),
    ("radarr_queue", "Get the current Radarr queue.", "read", "radarr", {}, lambda a: arr_queue("radarr", a)),
    ("radarr_health", "Get Radarr health issues.", "read", "radarr", {}, lambda a: arr_health("radarr", a)),
    ("radarr_missing_movies", "Get Radarr missing movies.", "read", "radarr", {}, lambda a: arr_missing("radarr", a)),
    ("lidarr_search_artist", "Search Lidarr for an artist.", "read", "lidarr", {"query": {"type": "string", "required": True}}, lidarr_search),
    ("lidarr_artist_status", "Get the managed Lidarr status for an artist, including album and track file counts.", "read", "lidarr", {"query": {"type": "string", "required": True}}, lidarr_artist_status),
    ("lidarr_import_status", "Check import/file state for the same Lidarr albums returned by an earlier wanted query.", "read", "lidarr", {"album_ids": {"type": "array", "required": True}}, lidarr_import_status),
    ("lidarr_search_album", "Search Lidarr for an album.", "read", "lidarr", {"query": {"type": "string", "required": True}}, lidarr_search_album),
    ("lidarr_queue", "Get the current Lidarr queue.", "read", "lidarr", {}, lambda a: arr_queue("lidarr", a)),
    ("lidarr_health", "Get Lidarr health issues.", "read", "lidarr", {}, lambda a: arr_health("lidarr", a)),
    ("lidarr_missing_tracks", "Get Lidarr missing tracks.", "read", "lidarr", {}, lambda a: arr_missing("lidarr", a)),
    ("frigate_status", "Check Frigate reachability and version.", "read", "frigate", {}, frigate_status),
    ("frigate_stats", "Get current Frigate camera and detector stats; this does not contain visual content.", "read", "frigate", {}, frigate_stats),
    ("frigate_recent_events", "Get recent Frigate object events.", "read", "frigate", {"camera": {"type": "string"}, "label": {"type": "string"}, "limit": {"type": "integer"}}, frigate_events),
    ("frigate_snapshot", "Get one current Frigate camera frame for an explicitly requested vision analysis.", "read", "frigate", {"camera": {"type": "string", "required": True}}, frigate_snapshot),
    ("frigate_event_snapshot", "Get the snapshot belonging to one specific Frigate event ID for grounded visual analysis.", "read", "frigate", {"event_id": {"type": "string", "required": True}}, frigate_event_snapshot),
    ("netdata_system_summary", "Get current Netdata host monitoring identity.", "read", "netdata", {}, netdata_summary),
    ("qbittorrent_summary", "Get current qBittorrent speeds, active downloads, stalls, and disk space.", "read", "qbittorrent", {}, qbittorrent_summary),
    ("qbittorrent_list", "List normalized qBittorrent items using a safe filter.", "read", "qbittorrent", {"filter": {"type": "string"}}, qbittorrent_list),
    ("qbittorrent_get", "Get one normalized qBittorrent item by hash.", "read", "qbittorrent", {"hash": {"type": "string", "required": True}}, qbittorrent_get),
    ("slskd_downloads", "Get active Soulseek downloads with bounded normalized results.", "read", "slskd", {"query": {"type": "string"}, "include_completed": {"type": "boolean"}}, slskd_downloads),
    ("slskd_search_status", "Get current Soulseek search status.", "read", "slskd", {}, slskd_search_status),
    ("music_enricher_status", "Get Music Enricher current and last-run status.", "read", "music_enricher", {}, music_enricher_status),
    ("music_enricher_quarantine", "Get Music Enricher quarantine-related state.", "read", "music_enricher", {"query": {"type": "string"}}, music_enricher_quarantine),
    ("beets_status", "Get fixed Beets library counts and latest import timestamp.", "read", "beets", {}, beets_status),
    ("beets_recent_imports", "Get recent Beets library imports.", "read", "beets", {}, beets_recent_imports),
    ("torbox_status", "Get sanitized Torbox client state.", "read", "torbox", {}, torbox_status),
    ("overseerr_status", "Get Overseerr service status.", "read", "overseerr", {}, overseerr_status),
    ("overseerr_recent_requests", "Get recent Overseerr request records without exposing credentials.", "read", "overseerr", {}, overseerr_recent_requests),
    ("web_search", "Search the public internet through the private SearXNG backend.", "read", "internet", {"query": {"type": "string", "required": True}}, web_search),
    ("web_fetch", "Fetch a public webpage as untrusted reference text; internal and private targets are blocked.", "read", "internet", {"url": {"type": "string", "required": True}}, web_fetch),
    ("weather_forecast", "Get current conditions or a daily forecast for an explicitly named city or configured home location.", "read", "weather", {"location": {"type": "string"}, "days_from_now": {"type": "integer"}}, weather_forecast),
    ("calculator", "Evaluate a numeric arithmetic expression deterministically.", "read", "utility", {"expression": {"type": "string", "required": True}}, calculator),
    ("unit_convert", "Convert supported storage and temperature units deterministically.", "read", "utility", {"value": {"type": "number", "required": True}, "from_unit": {"type": "string", "required": True}, "to_unit": {"type": "string", "required": True}}, unit_convert),
    ("current_datetime", "Get the current date and time for a named IANA timezone.", "read", "utility", {"timezone": {"type": "string"}}, current_datetime),
    ("wikipedia_search", "Search Wikipedia for factual reference pages.", "read", "knowledge", {"query": {"type": "string", "required": True}}, wikipedia_search),
    ("list_items", "Read a persistent personal list.", "read", "lists", {"list": {"type": "string"}}, list_items),
    ("add_list_items", "Add one or more items to a persistent personal list.", "write_low", "lists", {"list": {"type": "string"}, "item": {"type": "string"}, "items": {"type": "array"}}, add_list_items),
    ("remove_list_item", "Remove matching items from a persistent personal list.", "write_low", "lists", {"list": {"type": "string"}, "item": {"type": "string", "required": True}}, remove_list_items),
    ("clear_completed_list_items", "Clear completed items from a persistent personal list.", "write_low", "lists", {"list": {"type": "string"}}, clear_completed_list_items),
    ("investigate_downloads", "Correlate qBittorrent, Sonarr, Radarr, Lidarr, Slskd, and Torbox download state.", "read", "media_pipeline", {}, investigate_downloads),
    ("investigate_media_pipeline", "Investigate an artist or music item across Plex Music, Lidarr, qBittorrent, Slskd, Torbox, Music Enricher, and Beets. Destination absence does not stop the investigation.", "read", "media_pipeline", {"query": {"type": "string", "required": True}, "entity_type": {"type": "string"}, "focus": {"type": "string"}}, investigate_media_pipeline),
    ("investigate_plex_missing", "Investigate why a requested show or episode is not visible in Plex using Plex, Sonarr, qBittorrent, and Docker status.", "read", "media_pipeline", {"query": {"type": "string", "required": True}}, investigate_plex_missing),
    ("media_plan_goal", "Resolve a media goal into canonical identity, current library/manager state, bounded workflow steps, and any required confirmation. Planning only: never adds, searches, downloads, imports, or changes provider state.", "read", "media_planner", {"goal": {"type": "string", "required": True}, "media_type": {"type": "string"}}, media_plan_goal),
    ("media_policy_status", "Validate centralized media policies against live manager roots and quality/metadata profiles. Read-only; never changes provider state.", "read", "media_planner", {"media_type": {"type": "string"}}, media_policy_status),
    ("media_storage_status", "Show standard DB-library and permanent-library storage contracts without writing.", "read", "media_planner", {"media_type": {"type": "string"}}, media_storage_status),
    ("media_get_workflow", "Read one persisted media workflow by workflow ID; returns normalized lifecycle state and canonical identity.", "read", "media_planner", {"workflow_id": {"type": "string", "required": True}}, media_get_workflow),
    ("media_standard_request", "Submit one confirmed, canonical movie or whole-season watch-first request to the private cli_debrid bridge. Disabled until standard media writes are explicitly enabled; never accepts torrents, URLs, scraper commands, or credentials.", "confirm", "media_planner", {"workflow_id": {"type": "string", "required": True}, "media_type": {"type": "string", "required": True}, "canonical_external_id": {"type": "integer", "required": True}, "canonical_title": {"type": "string"}, "season_scope": {"type": "array"}, "episode_scope": {"type": "array"}, "confirmation_context": {"type": "object", "required": True}}, media_standard_request),
]
TOOLS = {x[0]: x for x in REGISTRY}
GROUP_SERVICES = {
    "server": {"server", "storage", "gpu", "docker", "netdata"},
    "plex": {"plex"},
    "tv": {"sonarr"},
    "movies": {"radarr"},
    "music": {"lidarr", "slskd", "music_enricher", "beets"},
    "downloads": {"qbittorrent", "torbox", "media_pipeline"},
    "cameras": {"frigate"},
    "requests": {"overseerr"},
    "internet": {"internet", "weather", "knowledge"},
    "utilities": {"utility"},
    "lists": {"lists"},
    "media": {"media_planner", "plex", "sonarr", "radarr", "lidarr", "media_pipeline", "qbittorrent", "torbox", "slskd", "music_enricher", "beets"},
}

CAPABILITY_METADATA = {
    "frigate_stats": {"aliases": ["camera health", "fps", "detector"], "examples": ["is my camera working", "are my cameras okay"], "freshness": "current", "visual_evidence": False},
    "frigate_recent_events": {"aliases": ["motion", "person detected", "recent camera event"], "examples": ["was someone at the door recently"], "freshness": "current", "visual_evidence": False},
    "frigate_snapshot": {"aliases": ["see camera", "what does it look like", "current image"], "examples": ["describe the front door right now"], "freshness": "current", "visual_evidence": True},
    "frigate_event_snapshot": {"aliases": ["event image", "detection image", "snapshot from that event"], "examples": ["describe the image from that detection"], "freshness": "event-scoped", "visual_evidence": True},
    "investigate_downloads": {"aliases": ["downloads", "queue", "stuck", "media pipeline"], "examples": ["what is downloading", "is anything stuck"], "group": "downloads", "freshness": "current"},
    "investigate_media_pipeline": {"aliases": ["music pipeline", "missing media", "artist status"], "examples": ["what is going on with UTOPIA", "how is Travis Scott coming along"], "group": "media_pipeline", "freshness": "current"},
    "get_storage_status": {"aliases": ["disk space", "free space", "storage"], "examples": ["how much storage do I have left"], "freshness": "current"},
    "list_containers": {"aliases": ["docker", "containers", "services"], "examples": ["how many containers are running"], "freshness": "current"},
    "lidarr_health": {"aliases": ["lidarr", "lidar", "music service health"], "examples": ["what is the status of LIDAR"], "freshness": "current"},
    "weather_forecast": {"aliases": ["weather", "forecast", "temperature", "rain"], "examples": ["what is the weather today", "what about tomorrow"], "freshness": "current"},
    "plex_recently_added": {"aliases": ["recently added", "last added", "newest in plex"], "examples": ["what was the last thing added to Plex"], "freshness": "current"},
    "plex_library_lookup": {"aliases": ["do i have", "is it in plex", "plex availability"], "examples": ["do I already have Rodeo"], "group": "plex", "freshness": "current"},
    "media_plan_goal": {"aliases": ["get media", "add movie", "request album", "put it in plex", "media goal"], "examples": ["get Rodeo by Travis Scott", "get the original animated Hobbit movie"], "group": "media", "freshness": "current"},
    "media_get_workflow": {"aliases": ["how is it doing", "is it downloading", "did it import", "media progress"], "examples": ["how is Rodeo doing"], "group": "media", "freshness": "current"},
    "calculator": {"aliases": ["calculate", "math", "percent", "percentage"], "examples": ["what is 17.5 percent of 438"], "freshness": "deterministic"},
    "unit_convert": {"aliases": ["convert", "gigabytes", "terabytes", "celsius", "fahrenheit"], "examples": ["convert 5 GB to MB"], "freshness": "deterministic"},
    "current_datetime": {"aliases": ["date", "time", "timezone", "today"], "examples": ["what time is it in Toronto"], "freshness": "current"},
    "wikipedia_search": {"aliases": ["wikipedia", "factual lookup", "encyclopedia"], "examples": ["look up this topic on Wikipedia"], "freshness": "reference"},
    "list_items": {"aliases": ["list", "grocery list", "packing list", "to do"], "examples": ["what's on my grocery list"], "group": "lists", "freshness": "current"},
    "add_list_items": {"aliases": ["add to list", "grocery list", "packing list"], "examples": ["put milk on my grocery list"], "group": "lists", "freshness": "current"},
    "remove_list_item": {"aliases": ["remove from list", "take off list"], "examples": ["remove milk from my grocery list"], "group": "lists", "freshness": "current"},
    "clear_completed_list_items": {"aliases": ["clear completed", "clean up list"], "examples": ["clear completed items"], "group": "lists", "freshness": "current"},
    "web_search": {"aliases": ["internet", "search online", "news", "documentation"], "examples": ["search the web for current release notes"], "freshness": "current", "untrusted": True},
    "web_fetch": {"aliases": ["open webpage", "read page"], "examples": ["fetch the official documentation"], "freshness": "current", "untrusted": True},
}

def capability_record(item):
    schema = public_schema(item)
    name, desc, permission, service, _, _ = item
    meta = CAPABILITY_METADATA.get(name, {})
    words = [name.replace("_", " "), desc, service, meta.get("group", service), *meta.get("aliases", []), *meta.get("examples", [])]
    return {**schema, "metadata": {"canonical_name": name, "aliases": meta.get("aliases", []), "examples": meta.get("examples", []), "group": meta.get("group", service), "read_write": permission, "confirmation_required": permission in {"confirm", "destructive"}, "freshness": meta.get("freshness", "current"), "required_service": service, "visual_evidence": meta.get("visual_evidence", False), "search_text": " ".join(words)}}

def _search_tokens(value: str) -> set[str]:
    stop = {"what", "is", "the", "my", "do", "you", "have", "i", "a", "an", "are", "on", "in", "of", "for", "to", "and", "how", "did", "it", "there", "right", "now", "please", "can"}
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 1 and token not in stop}

def discover_capabilities(query: str, max_results: int = 8, context: dict[str, Any] | None = None) -> list[dict]:
    q = _search_tokens(query)
    lowered = query.casefold()
    context = context or {}
    prior_group = str(context.get("group", "")).casefold()
    prior_tools = {str(item).casefold() for item in context.get("tools", [])}
    referents = _search_tokens(" ".join(str(item) for item in context.get("referents", [])))
    ranked = []
    for item in REGISTRY:
        record = capability_record(item)
        meta = record["metadata"]
        terms = _search_tokens(meta["search_text"])
        overlap = len(q & terms)
        exact = sum(2 for alias in meta["aliases"] if alias.casefold() in query.casefold())
        example = sum(2 for example in meta["examples"] if any(token in q for token in _search_tokens(example)))
        score = overlap + exact + example
        if name := meta["canonical_name"]:
            if name in {"web_search", "web_fetch"} and re.search(r"\b(new|newest|latest|current|today|ongoing|news|policy|policies|version|release)\b", lowered): score += 7
            if name == "web_search" and not re.search(r"\b(fetch|open|read|page|url|website|article)\b", lowered): score += 3
            if name == "web_fetch" and re.search(r"\b(fetch|open|read|page|url|website|article)\b", lowered): score += 3
            if name == "frigate_stats" and re.search(r"\b(working|okay|online|offline|health|fps|detector)\b", lowered): score += 8
            if name == "frigate_recent_events" and re.search(r"\b(recent|recently|motion|detected|was someone|who was)\b", lowered): score += 8
            if name == "frigate_snapshot" and re.search(r"\b(describe|see|look|wearing|color|colour|right now|current image)\b", lowered): score += 8
            if prior_group == "cameras" and meta.get("group") == "frigate": score += 5
            if name.casefold() in prior_tools: score += 4
            if referents & terms: score += 2
        if score: ranked.append((score, record))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["metadata"]["canonical_name"]))
    return [{**record, "metadata": {**record["metadata"], "rank": index + 1, "score": score}} for index, (score, record) in enumerate(ranked[:max(1, min(max_results, 8))])]


class Invoke(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    client_id: str = "unknown"
    session_id: str = "unknown"
    confirmed: bool = False
    action_id: str | None = None


def public_schema(item):
    name, desc, permission, service, args, _ = item
    props = {k: {kk: vv for kk, vv in v.items() if kk != "required"} for k, v in args.items()}
    required = [k for k, v in args.items() if v.get("required")]
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required}} , "permission": permission, "service": service}


@app.get("/health")
async def health():
    return {"ok": True, "tools": len(REGISTRY), "service": "server-tools"}


@app.get("/registry")
async def registry(groups: str = ""):
    requested = {item.strip() for item in groups.split(",") if item.strip()}
    services = {service for group in requested for service in GROUP_SERVICES.get(group, set())}
    items = REGISTRY if not requested else [item for item in REGISTRY if item[3] in services]
    return {"tools": [public_schema(x) for x in items], "groups": sorted(requested)}


@app.get("/media/capabilities")
async def media_capabilities():
    return {"capabilities": MEDIA_CAPABILITY_REGISTRY, "lifecycle_states": MEDIA_LIFECYCLE,
            "write_execution": "disabled_until_planner_validation"}


@app.get("/discover")
async def discover(query: str, max_results: int = 8, context_json: str = ""):
    started = time.perf_counter()
    try:
        context = json.loads(context_json) if context_json else {}
        if not isinstance(context, dict): context = {}
    except json.JSONDecodeError:
        context = {}
    results = discover_capabilities(query, max_results, context)
    return {"tools": results, "query": query, "latency_ms": round((time.perf_counter() - started) * 1000, 3), "total_enabled": len(REGISTRY)}


@app.post("/invoke")
async def invoke(req: Invoke):
    item = TOOLS.get(req.name)
    if not item:
        raise HTTPException(404, "tool is not enabled")
    _, _, permission, service, _, fn = item
    if permission in {"confirm", "destructive"} and not req.confirmed:
        action_id = str(uuid.uuid4())
        audit({"client_id": req.client_id, "session_id": req.session_id, "tool": req.name, "service": service, "permission": permission, "arguments": safe_args(req.arguments), "status": "confirmation_required", "action_id": action_id})
        return {"tool": req.name, "service": service, "permission": permission, "status": "confirmation_required", "action_id": action_id, "result": {"message": "This action requires explicit confirmation before execution."}}
    started = time.monotonic()
    status = "ok"
    result: Any
    context_token = AUDIT_CONTEXT.set({"client_id": req.client_id, "session_id": req.session_id})
    try:
        call_arguments = dict(req.arguments)
        call_arguments.setdefault("session_id", req.session_id)
        result = await asyncio.wait_for(fn(call_arguments), timeout=12)
    except asyncio.TimeoutError:
        status, result = "timeout", {"error": f"{service} tool timed out"}
    except httpx.HTTPStatusError as exc:
        status, result = "unavailable", {"error": f"{service} API returned HTTP {exc.response.status_code}"}
    except Exception as exc:
        status, result = "error", {"error": f"{service} tool failed", "detail": type(exc).__name__}
    finally:
        AUDIT_CONTEXT.reset(context_token)
    audit({"client_id": req.client_id, "session_id": req.session_id, "tool": req.name, "service": service, "permission": permission, "arguments": safe_args(req.arguments), "status": status, "action_id": req.action_id, "duration_ms": round((time.monotonic() - started) * 1000), "result_summary": audit_result(result)})
    return {"tool": req.name, "service": service, "permission": permission, "status": status, "result": result}
