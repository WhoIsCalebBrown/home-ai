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


def _noncanonical_numeric_ipv4(host: str) -> bool:
    """Reject browser/WHATWG alternate spellings of numeric IPv4 hosts."""
    if host.isdigit():
        return True
    if host.casefold().startswith("0x") and all(char in "0123456789abcdefx" for char in host.casefold()):
        return True
    parts = host.split(".")
    if len(parts) > 1 and all(part.isdigit() for part in parts):
        if len(parts) != 4:
            return True
        return any(part != str(int(part)) or not 0 <= int(part) <= 255 for part in parts)
    return any(
        part.casefold().startswith("0x")
        and all(char in "0123456789abcdefx" for char in part.casefold())
        for part in parts
    )


def safe_display_url(value: str) -> str | None:
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x1f\x7f]", value):
        return None
    try:
        parsed = urlsplit(value)
        raw_host = (parsed.hostname or "").casefold()
        host = raw_host.removesuffix(".")
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    if "@" in parsed.netloc or "\\" in parsed.netloc or parsed.netloc.endswith(":"):
        return None
    authority_host = f"[{raw_host}]" if ":" in raw_host else raw_host
    if not re.fullmatch(re.escape(authority_host) + r"(?::[0-9]+)?", parsed.netloc, re.IGNORECASE):
        return None
    if port == 0:
        return None
    if "%" in host or any(ord(char) > 127 for char in host) or _noncanonical_numeric_ipv4(host):
        return None
    if host in {"localhost", "unraid", "tower", "host.docker.internal", "metadata.google.internal"}:
        return None
    if host.endswith((".localhost", ".local", ".lan", ".internal", ".docker", ".home")):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address:
        if not address.is_global or address.is_multicast or address.is_reserved:
            return None
    elif len(host) > MAX_DOMAIN_CHARS or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host,
    ) or host.rsplit(".", 1)[-1].isdigit():
        return None
    display_host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))


def project_trace(live_results: list[dict]) -> list[dict]:
    entries: list[dict] = []
    positions: list[int] = []
    seen_urls: set[str] = set()
    # Reserve the bounded display budget for distinct fetched evidence before
    # discovery/retry activity. Restore chronological order after selection.
    fetched_positions = []
    fetched_urls: set[str] = set()
    for index, raw in enumerate(live_results):
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        url = safe_display_url(result.get("url"))
        if (raw.get("tool") == "web_fetch" and raw.get("status") == "ok"
                and raw.get("operation_ok", True) is not False and url and url not in fetched_urls):
            fetched_urls.add(url)
            fetched_positions.append(index)
    priority = set(fetched_positions)
    for index in fetched_positions + [index for index in range(len(live_results)) if index not in priority]:
        raw = live_results[index]
        tool = str(raw.get("tool") or "unknown")
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        ok = raw.get("status") == "ok" and raw.get("operation_ok", True) is not False
        search_results = result.get("results")
        if not isinstance(search_results, list):
            search_results = []
        raw_result_count = result.get("result_count")
        try:
            if isinstance(raw_result_count, bool):
                raise ValueError("boolean result count")
            result_count = int(raw_result_count or 0)
            count_valid = result_count >= 0
        except (TypeError, ValueError):
            result_count = 0
            count_valid = False
        if not count_valid:
            search_results = []
        empty_search = tool == "web_search" and (result_count <= 0 or not search_results)
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
            for candidate in search_results[:MAX_SOURCES_PER_SEARCH]:
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
            continue
        positions.append(index)
        if len(entries) >= MAX_TRACE_ENTRIES:
            break
    return [entry for _, entry in sorted(zip(positions, entries))]
