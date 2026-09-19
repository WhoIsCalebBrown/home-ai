"""Request-local, display-only progress with no tool payloads in its output."""

import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from urllib.parse import urlsplit

from trace_projection import safe_display_url


progress_sink_context: ContextVar[Callable[[dict], Awaitable[None]] | None] = ContextVar(
    "progress_sink", default=None,
)

_START_LABELS = {
    "web_search": "Searching the web…",
    "web_fetch": "Reading a source…",
    "weather_forecast": "Checking the forecast…",
    "plex_search": "Checking Plex…",
    "home_get_state": "Checking your home…",
}
_DOMAIN_LABELS = {"cbc.ca": "CBC", "reuters.com": "Reuters", "bbc.com": "BBC", "bbc.co.uk": "BBC"}
_DNS_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def _public_display_host(url: object) -> str | None:
    # Reuse the trace's public URL policy, then require a short DNS hostname
    # that cannot introduce Markdown. Do not truncate attacker-controlled text.
    safe_url = safe_display_url(url) if isinstance(url, str) else None
    if not safe_url:
        return None
    host = (urlsplit(safe_url).hostname or "").removeprefix("www.")
    return host if len(host) <= 80 and _DNS_HOST.fullmatch(host) else None


def safe_progress_event(tool: str, arguments: dict, phase: str, result: dict | None = None) -> dict | None:
    """Project only fixed labels and public hostnames; never read result data."""
    if phase == "finished":
        return {"phase": "tool_finished", "label": "Complete"}
    if phase == "failed":
        return {"phase": "tool_failed", "label": "Tool unavailable"}
    if phase != "started":
        return None
    label = _START_LABELS.get(tool, "Working…") if isinstance(tool, str) else "Working…"
    if tool == "web_fetch" and isinstance(arguments, dict):
        host = _public_display_host(arguments.get("url"))
        if host:
            label = f"Reading {_DOMAIN_LABELS.get(host, host)}…"
    return {"phase": "tool_started", "label": label}


async def emit_tool_progress(tool: str, arguments: dict, phase: str, result: dict | None = None) -> None:
    """Progress failure must not change a tool result; cancellation propagates."""
    try:
        sink = progress_sink_context.get()
        if sink is not None:
            event = safe_progress_event(tool, arguments, phase, result)
            if event is not None:
                await sink(event)
    except Exception:
        pass


class ProgressPreamble:
    """Persist at most four distinct started stages for a single request."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def add(self, event: dict | None) -> str | None:
        if not isinstance(event, dict) or event.get("phase") != "tool_started" or len(self._seen) >= 4:
            return None
        label = event.get("label")
        if not isinstance(label, str) or label in self._seen:
            return None
        fixed = {*_START_LABELS.values(), "Working…", *(f"Reading {name}…" for name in _DOMAIN_LABELS.values())}
        if label not in fixed:
            host = label.removeprefix("Reading ").removesuffix("…")
            if label != f"Reading {host}…" or _public_display_host(f"https://{host}/") != host:
                return None
        prefix = "**Working**\n" if not self._seen else ""
        self._seen.add(label)
        return f"{prefix}- {label}\n"
