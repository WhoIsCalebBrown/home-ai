"""Project raw tool results into a bounded, safe display trace."""

import ipaddress
import json
import re
from urllib.parse import urlsplit, urlunsplit


MAX_TRACE_ENTRIES = 12
MAX_SOURCES_PER_SEARCH = 3
MAX_TITLE_CHARS = 180
MAX_DOMAIN_CHARS = 253

ACTION_LABELS = {
    "web_search": "Searched the web",
    "web_fetch": "Opened source",
    "weather_forecast": "Checked the forecast",
    "plex_search": "Checked Plex",
    "home_get_state": "Checked your home",
}


def clean_text(value: object, limit: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def safe_display_url(value: str) -> str | None:
    if not value or re.search(r"[\x00-\x1f\x7f]", value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    if host in {"localhost", "unraid", "tower", "host.docker.internal", "metadata.google.internal"}:
        return None
    if host.endswith((".local", ".lan", ".internal", ".docker", ".home")):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local
                    or address.is_multicast or address.is_reserved or address.is_unspecified):
        return None
    display_host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))


def project_trace(live_results: list[dict]) -> list[dict]:
    entries: list[dict] = []
    seen_urls: set[str] = set()
    for raw in live_results[:MAX_TRACE_ENTRIES]:
        tool = str(raw.get("tool") or "unknown")
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        ok = raw.get("status") == "ok" and raw.get("operation_ok", True) is not False
        empty_search = tool == "web_search" and int(result.get("result_count") or 0) == 0
        status = "failed" if not ok else "no results" if empty_search else "complete"
        sources: list[dict] = []
        if tool == "web_fetch" and ok:
            url = safe_display_url(str(result.get("url") or ""))
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({
                    "title": clean_text(result.get("title"), MAX_TITLE_CHARS),
                    "domain": clean_text(urlsplit(url).hostname, MAX_DOMAIN_CHARS),
                    "url": url,
                    "kind": "fetched",
                    "published": clean_text(result.get("published"), 40) or None,
                })
        elif tool == "web_search" and ok:
            for candidate in list(result.get("results") or [])[:MAX_SOURCES_PER_SEARCH]:
                if not isinstance(candidate, dict):
                    continue
                domain = clean_text(candidate.get("domain"), MAX_DOMAIN_CHARS)
                sources.append({
                    "title": clean_text(candidate.get("title"), MAX_TITLE_CHARS),
                    "domain": domain,
                    "url": None,
                    "kind": "candidate",
                    "published": clean_text(candidate.get("date"), 40) or None,
                })
        entries.append({
            "tool": tool,
            "action": ACTION_LABELS.get(tool, "Used an assistant tool"),
            "status": status,
            "sources": sources,
        })
        if len(json.dumps(entries, ensure_ascii=False).encode("utf-8")) > 16_384:
            entries.pop()
            break
    return entries
