import asyncio
import contextvars
import html
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Local Server Tools", version="2026.09.13")

TOWER = os.getenv("TOWER_URL", "http://192.168.40.44")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://SearXNG:8080").rstrip("/")
DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
AUDIT = Path(os.getenv("AUDIT_LOG", "/data/audit.jsonl"))
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


def safe_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: ("[redacted]" if any(s in k.lower() for s in ("key", "token", "password", "secret")) else v) for k, v in args.items()}


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
    items = [normalize_container(r) for r in rows]
    if status:
        items = [x for x in items if x["state"] == status or status.lower() in x["status"].lower()]
    return {"count": len(items), "containers": items[:200]}


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
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    async with httpx.AsyncClient(transport=transport, timeout=10) as client:
        r = await client.post(f"http://docker/containers/{name}/restart", params={"t": 10})
        r.raise_for_status()
    return {"ok": True, "name": name, "action": "restarted"}


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
            results.append({"title": item.get("title", ""), "url": item["url"], "snippet": item.get("content", "")})
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
    rows = await arr_get(service, "/api/v3/health")
    return {"service": service, "issues": [{"level": x.get("level"), "message": x.get("message")} for x in rows[:20]], "healthy": not any(x.get("level") == "error" for x in rows)}


async def arr_queue(service: str, _: dict[str, Any]) -> dict[str, Any]:
    data = await arr_get(service, "/api/v3/queue", {"page": 1, "pageSize": 50})
    records = data.get("records", data if isinstance(data, list) else [])
    return {"service": service, "total": data.get("totalRecords", len(records)) if isinstance(data, dict) else len(records), "items": [{"title": x.get("title"), "status": x.get("status"), "sizeleft": x.get("sizeleft"), "protocol": x.get("protocol")} for x in records[:50]]}


async def sonarr_search(args):
    rows = await arr_get("sonarr", "/api/v3/series/lookup", {"term": args["query"]})
    return {"matches": [{"title": x.get("title"), "year": x.get("year"), "tvdbId": x.get("tvdbId"), "overview": x.get("overview", "")[:240]} for x in rows[:20]]}


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


async def frigate_status(_: dict[str, Any]) -> dict[str, Any]:
    data = await get_json("frigate", "/api/version")
    return {"reachable": True, "version": data.get("version") if isinstance(data, dict) else data}


async def frigate_stats(_: dict[str, Any]) -> dict[str, Any]:
    data = await get_json("frigate", "/api/stats")
    cameras = data.get("cameras", {}) if isinstance(data, dict) else {}
    return {"cameras": {name: {"camera_fps": value.get("camera_fps"), "detection_fps": value.get("detection_fps"), "process_fps": value.get("process_fps"), "detection_enabled": value.get("detection_enabled")} for name, value in cameras.items()}, "detector": data.get("detectors", {}) if isinstance(data, dict) else {}}


async def frigate_events(args: dict[str, Any]) -> dict[str, Any]:
    params = {"limit": min(int(args.get("limit", 10)), 50)}
    if args.get("camera"): params["camera"] = args["camera"]
    if args.get("label"): params["label"] = args["label"]
    rows = await get_json("frigate", "/api/events", params)
    return {"events": [{"id": x.get("id"), "camera": x.get("camera"), "label": x.get("label"), "start_time": x.get("start_time"), "end_time": x.get("end_time"), "has_clip": x.get("has_clip"), "has_snapshot": x.get("has_snapshot")} for x in rows[:50]]}


async def arr_missing(service: str, _: dict[str, Any]) -> dict[str, Any]:
    path = {"sonarr": "/api/v3/wanted/missing", "radarr": "/api/v3/wanted/missing", "lidarr": "/api/v1/wanted/missing"}[service]
    data = await arr_get(service, path, {"page": 1, "pageSize": 50})
    records = data.get("records", []) if isinstance(data, dict) else []
    return {"service": service, "count": data.get("totalRecords", len(records)), "items": [{"title": x.get("title"), "series": x.get("series", {}).get("title") if isinstance(x.get("series"), dict) else None, "artist": x.get("artist", {}).get("artistName") if isinstance(x.get("artist"), dict) else None, "season": x.get("seasonNumber"), "episode": x.get("episodeNumber")} for x in records[:50]]}


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


async def investigate_downloads(_: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for name, fn in (("qbittorrent", qbittorrent_summary), ("sonarr", lambda a: arr_queue("sonarr", a)), ("radarr", lambda a: arr_queue("radarr", a)), ("lidarr", lambda a: arr_queue("lidarr", a)), ("slskd", slskd_downloads), ("torbox", torbox_status)):
        try:
            value = await fn({})
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
    return {"investigation": "downloads", "sources": result}


async def investigation_step(parent: str, name: str, fn, args: dict[str, Any]):
    started = time.monotonic()
    ctx = AUDIT_CONTEXT.get()
    try:
        value = await fn(args)
        audit({**ctx, "tool": name, "parent_tool": parent, "service": name.split("_")[0],
               "permission": "read", "arguments": safe_args(args), "status": "ok",
               "duration_ms": round((time.monotonic() - started) * 1000)})
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
             ("torbox", "torbox_status", torbox_status, {"query": query})]
    for name, fn_name, fn, fn_args in calls:
        try:
            value = await investigation_step("investigate_media_pipeline", fn_name, fn, fn_args)
            if name == "beets":
                value["items"] = [x for x in value.get("items", []) if query.casefold() in json.dumps(x).casefold()][:20]
                value["count"] = len(value["items"])
            elif isinstance(value, dict) and isinstance(value.get("items"), list):
                value["items"] = value["items"][:5]
            result[name] = value
        except Exception as exc:
            result[name] = {"error": f"{name} unavailable", "detail": type(exc).__name__}
    try:
        torrent_rows = await investigation_step("investigate_media_pipeline", "qbittorrent_list", lambda _: qbit_request("/api/v2/torrents/info", {"filter": "all"}), {})
        result["qbittorrent"] = {"items": [normalize_qbit(row) for row in torrent_rows if query.casefold() in (row.get("name", "") + " " + row.get("category", "")).casefold()][:5]}
    except Exception as exc:
        result["qbittorrent"] = {"error": "qBittorrent unavailable", "detail": type(exc).__name__}
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


REGISTRY = [
    ("get_server_overview", "Current host, uptime, RAM/storage summary.", "read", "server", {}, server_overview),
    ("get_storage_status", "Current user-share and cache storage usage.", "read", "storage", {}, storage_status),
    ("get_gpu_status", "Current NVIDIA GPU telemetry and VRAM usage.", "read", "gpu", {}, gpu_status),
    ("list_containers", "List current Docker containers and status.", "read", "docker", {"status": {"type": "string"}}, list_containers),
    ("get_container_status", "Get one Docker container status.", "read", "docker", {"name": {"type": "string", "required": True}}, container_status),
    ("get_container_logs", "Get a capped tail of one container's logs.", "read", "docker", {"name": {"type": "string", "required": True}, "lines": {"type": "integer"}}, container_logs),
    ("restart_container", "Restart a named Docker container after confirmation.", "confirm", "docker", {"name": {"type": "string", "required": True}}, restart_container),
    ("plex_search", "Search Plex libraries and report matching library.", "read", "plex", {"query": {"type": "string", "required": True}, "library": {"type": "string"}}, plex_search),
    ("plex_artist_library", "List albums and tracks actually present for an exact artist in Plex Music.", "read", "plex", {"query": {"type": "string", "required": True}}, plex_artist_library),
    ("plex_library_counts", "Get distinct Plex library counts.", "read", "plex", {}, plex_counts),
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
    ("lidarr_search_album", "Search Lidarr for an album.", "read", "lidarr", {"query": {"type": "string", "required": True}}, lidarr_search_album),
    ("lidarr_queue", "Get the current Lidarr queue.", "read", "lidarr", {}, lambda a: arr_queue("lidarr", a)),
    ("lidarr_health", "Get Lidarr health issues.", "read", "lidarr", {}, lambda a: arr_health("lidarr", a)),
    ("lidarr_missing_tracks", "Get Lidarr missing tracks.", "read", "lidarr", {}, lambda a: arr_missing("lidarr", a)),
    ("frigate_status", "Check Frigate reachability and version.", "read", "frigate", {}, frigate_status),
    ("frigate_stats", "Get current Frigate camera and detector stats.", "read", "frigate", {}, frigate_stats),
    ("frigate_recent_events", "Get recent Frigate object events.", "read", "frigate", {"camera": {"type": "string"}, "label": {"type": "string"}, "limit": {"type": "integer"}}, frigate_events),
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
    ("investigate_downloads", "Correlate qBittorrent, Sonarr, Radarr, Lidarr, Slskd, and Torbox download state.", "read", "media_pipeline", {}, investigate_downloads),
    ("investigate_media_pipeline", "Investigate an artist or music item across Plex Music, Lidarr, qBittorrent, Slskd, Torbox, Music Enricher, and Beets. Destination absence does not stop the investigation.", "read", "media_pipeline", {"query": {"type": "string", "required": True}, "entity_type": {"type": "string"}, "focus": {"type": "string"}}, investigate_media_pipeline),
    ("investigate_plex_missing", "Investigate why a requested show or episode is not visible in Plex using Plex, Sonarr, qBittorrent, and Docker status.", "read", "media_pipeline", {"query": {"type": "string", "required": True}}, investigate_plex_missing),
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
    "internet": {"internet"},
}


class Invoke(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    client_id: str = "unknown"
    session_id: str = "unknown"
    confirmed: bool = False


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


@app.post("/invoke")
async def invoke(req: Invoke):
    item = TOOLS.get(req.name)
    if not item:
        raise HTTPException(404, "tool is not enabled")
    _, _, permission, service, _, fn = item
    if permission != "read" and not req.confirmed:
        action_id = str(uuid.uuid4())
        audit({"client_id": req.client_id, "session_id": req.session_id, "tool": req.name, "service": service, "permission": permission, "arguments": safe_args(req.arguments), "status": "confirmation_required", "action_id": action_id})
        return {"tool": req.name, "service": service, "permission": permission, "status": "confirmation_required", "action_id": action_id, "result": {"message": "This action requires explicit confirmation before execution."}}
    started = time.monotonic()
    status = "ok"
    result: Any
    context_token = AUDIT_CONTEXT.set({"client_id": req.client_id, "session_id": req.session_id})
    try:
        result = await asyncio.wait_for(fn(req.arguments), timeout=12)
    except asyncio.TimeoutError:
        status, result = "timeout", {"error": f"{service} tool timed out"}
    except httpx.HTTPStatusError as exc:
        status, result = "unavailable", {"error": f"{service} API returned HTTP {exc.response.status_code}"}
    except Exception as exc:
        status, result = "error", {"error": f"{service} tool failed", "detail": type(exc).__name__}
    finally:
        AUDIT_CONTEXT.reset(context_token)
    audit({"client_id": req.client_id, "session_id": req.session_id, "tool": req.name, "service": service, "permission": permission, "arguments": safe_args(req.arguments), "status": status, "duration_ms": round((time.monotonic() - started) * 1000)})
    return {"tool": req.name, "service": service, "permission": permission, "status": status, "result": result}
