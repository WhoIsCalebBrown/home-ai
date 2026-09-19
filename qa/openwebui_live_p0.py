#!/usr/bin/env python3
"""Safe live Open WebUI regression harness for the Home-AI P0 round.

This deliberately uses Open WebUI's OpenAI-compatible ``/api/chat/completions``
endpoint, rather than calling Home-AI or Home-AI-Tools directly.  It is an
operator-run script, not a pytest test: no token is accepted on the command
line, output is JSON, and confirmation/write-shaped prompts are refused unless
``--safe-mode`` is explicitly supplied (safe mode still never approves writes).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, HTTPException, Request


WRITE_WORDS = re.compile(r"\b(?:confirm|approve|download|acquire|add|request|get|grab|restart|delete|remove|turn on|turn off)\b", re.I)
SECRET_KEYS = re.compile(r"(?:token|authorization|password|secret|api[_-]?key)", re.I)
DISPLAY_TRACE_MARKER = "<!-- home-ai-display-trace -->"
SAFE_PROGRESS_LABELS = {
    "Searching the web…", "Reading a source…", "Checking the forecast…",
    "Checking Plex…", "Checking your home…", "Working…", "Reading CBC…",
    "Reading Reuters…", "Reading BBC…",
}
UNSAFE_DISPLAY_MARKERS = (
    "household-private-query", "private-source.invalid", "raw fixture snippet",
    "fixture-token-9f1c", "tool exception fixture", "traceback", "token=",
    "authorization:", "127.0.0.1", "localhost",
)


@dataclass
class Turn:
    prompt: str
    answer: str = ""
    chat_id: str = ""
    user_id: str = ""
    home_ai_session_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    tools_footer: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    grounding_ok: bool | None = None
    elapsed_ms: float = 0.0
    error: str | None = None
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    progress_before_answer: bool | None = None
    persisted_progress_lines: int | None = None


def _is_safe_progress_label(label: str) -> bool:
    """Accept only the owned fixed labels or a bounded public hostname."""
    if label in SAFE_PROGRESS_LABELS:
        return True
    host = label.removeprefix("Reading ").removesuffix("…")
    return bool(re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
        host,
    )) and len(host) <= 80 and not host.endswith((".local", ".internal", ".lan", ".home"))


def _assert_progress_source_display(display: str, events: list[dict[str, Any]]) -> tuple[float, float, int]:
    """Check the persisted Open WebUI display boundary without logging secrets.

    The event list intentionally stores only ordinary assistant content deltas
    after this checker has accepted them.  This keeps the operator JSON useful
    for timing evidence without turning the live harness into a raw SSE log.
    """
    assert display.startswith("**Working**\n"), "missing ordinary Working preamble"
    preamble, separator, remainder = display.partition("\n---\n\n")
    assert separator, "missing progress/final-answer separator"
    lines = preamble.splitlines()
    assert lines and lines[0] == "**Working**", "invalid progress header"
    progress = [line.removeprefix("- ") for line in lines[1:] if line.startswith("- ")]
    assert 1 <= len(progress) <= 4, "preamble must contain one through four progress lines"
    assert len(progress) == len(set(progress)), "preamble progress lines must be distinct"
    assert all(_is_safe_progress_label(label) for label in progress), "unsafe progress label"
    assert remainder.strip(), "missing final answer"
    assert DISPLAY_TRACE_MARKER in remainder, "missing rich source trace"
    lowered = display.casefold()
    assert not any(marker in lowered for marker in UNSAFE_DISPLAY_MARKERS), "unsafe display data leaked"

    progress_at = next((event["t_ms"] for event in events if event.get("content", "").startswith("**Working**")), None)
    answer_at = next((event["t_ms"] for event in events if event.get("after_separator") and event.get("content", "").strip()), None)
    assert progress_at is not None and answer_at is not None, "missing timestamped progress or answer chunk"
    assert progress_at < answer_at, "ordinary progress did not precede final answer"
    return float(progress_at), float(answer_at), len(progress)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEYS.search(str(k)) else _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", value)
    return value


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return str(content or "")


def _footer(answer: str) -> list[str]:
    # Open WebUI/Home-AI commonly emits a compact tool trace footer. Keep the
    # parser intentionally permissive because formatting is UI-owned.
    return sorted(set(re.findall(r"(?im)(?:^|[| ])`?([a-z][a-z0-9_]{2,})`?\s*[—-]\s*(?:ok|unavailable|error|timeout|invalid arguments)", answer)))


def numeric_grounding(answer: str, expected: dict[str, Any], tolerance: float = 0.0) -> bool:
    """Check authoritative numeric facts without requiring exact prose."""
    wanted_values = [float(value) for value in expected.values() if isinstance(value, (int, float))]
    values = [float(x.replace(",", "")) for x in re.findall(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?", answer)]
    for key, wanted in expected.items():
        if not isinstance(wanted, (int, float)):
            continue
        if not any(abs(actual - float(wanted)) <= tolerance for actual in values):
            return False
    # Cache responses are intentionally compact. Any additional numeric claim
    # is suspect because it did not come from the supplied authoritative
    # fields (the production bug was an invented second free-space number).
    return all(any(abs(actual - wanted) <= tolerance for wanted in wanted_values) for actual in values)


class OpenWebUILive:
    def __init__(self, base_url: str, token: str, model: str, user_id: str, timeout: float, safe_mode: bool):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.user_id = user_id
        self.safe_mode = safe_mode
        self.client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def close(self) -> None:
        self.client.close()

    def new_chat(self, title: str = "P0 LIVE regression") -> str:
        chat = {"title": title, "models": [self.model], "messages": [],
                "history": {"messages": {}, "currentId": None}, "tags": ["p0-live-regression"]}
        response = self.client.post(f"{self.base_url}/api/v1/chats/new", json={"chat": chat})
        response.raise_for_status()
        chat_id = str(response.json()["id"])
        return chat_id

    def progress_source_turn(self, prompt: str, chat_id: str | None = None) -> Turn:
        """Exercise a streamed, read-only progress/source response through Open WebUI.

        This is deliberately separate from ``turn`` so normal P0 regression
        coverage keeps its non-streaming contract.  It records monotonic
        content-event timestamps and validates the stream contract. Persisted
        UI behavior is deliberately checked only by the browser acceptance
        script; this HTTP harness never writes an assistant message.
        """
        if WRITE_WORDS.search(prompt) and not self.safe_mode:
            raise ValueError(f"refusing write/confirmation-shaped prompt: {prompt!r}; pass --safe-mode to run non-approving coverage")
        chat_id = chat_id or self.new_chat("Progress/source live acceptance")
        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": True,
                   "chat_id": chat_id, "metadata": {"user_id": self.user_id, "chat_id": chat_id}}
        started = time.perf_counter()
        record = Turn(prompt=prompt, chat_id=chat_id, user_id=self.user_id)
        content_parts: list[str] = []
        saw_separator = False
        try:
            with self.client.stream("POST", f"{self.base_url}/api/chat/completions", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ")
                    if data == "[DONE]":
                        record.stream_events.append({"t_ms": round((time.perf_counter() - started) * 1000, 2), "event": "done"})
                        continue
                    frame = json.loads(data)
                    delta = ((frame.get("choices") or [{}])[0].get("delta") or {})
                    content = delta.get("content")
                    if not isinstance(content, str) or not content:
                        continue
                    now_ms = round((time.perf_counter() - started) * 1000, 2)
                    content_parts.append(content)
                    # A separator may be emitted in its own content delta.  A
                    # later non-empty delta is the first answer/trace event.
                    event = {"t_ms": now_ms, "event": "content", "content": content,
                             "after_separator": saw_separator}
                    record.stream_events.append(event)
                    if "\n---\n" in content:
                        saw_separator = True
            record.answer = "".join(content_parts)
            progress_at, answer_at, line_count = _assert_progress_source_display(record.answer, record.stream_events)
            record.progress_before_answer = progress_at < answer_at
            record.persisted_progress_lines = line_count
            record.tools_footer = _footer(record.answer)
        except Exception as exc:
            record.error = type(exc).__name__ + ": " + str(exc)
        record.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return record

    def turn(self, prompt: str, chat_id: str | None = None, expected: dict[str, Any] | None = None) -> Turn:
        if WRITE_WORDS.search(prompt) and not self.safe_mode:
            raise ValueError(f"refusing write/confirmation-shaped prompt: {prompt!r}; pass --safe-mode to run non-approving coverage")
        chat_id = chat_id or self.new_chat()
        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": False,
                   "chat_id": chat_id, "metadata": {"user_id": self.user_id, "chat_id": chat_id}}
        started = time.perf_counter()
        record = Turn(prompt=prompt, chat_id=chat_id, user_id=self.user_id, expected=expected or {})
        try:
            response = self.client.post(f"{self.base_url}/api/chat/completions", json=payload)
            try:
                record.raw = _redact(response.json())
            except Exception:
                record.raw = {"http_status": response.status_code}
            response.raise_for_status()
            body = response.json()
            record.raw = _redact(body)
            record.answer = _text((body.get("choices") or [{}])[0].get("message", {}).get("content", ""))
            record.tools_footer = _footer(record.answer)
            record.home_ai_session_id = response.headers.get("x-home-ai-session")
            record.request_id = response.headers.get("x-home-ai-request")
            record.trace_id = response.headers.get("x-home-ai-trace")
            if record.expected:
                record.grounding_ok = numeric_grounding(record.answer, record.expected)
        except Exception as exc:
            record.error = type(exc).__name__ + ": " + str(exc)
        record.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return record


def _chain(api: OpenWebUILive, prompts: list[str], chat_id: str | None = None) -> list[Turn]:
    chat_id = chat_id or api.new_chat()
    return [api.turn(prompt, chat_id=chat_id) for prompt in prompts]


# This tiny provider is QA-only.  It lets an operator observe the *unchanged*
# pinned Open WebUI client with a deterministic blocked-source stream, without
# pointing a disposable browser at a real Home-AI or any production dependency.
progress_source_probe = FastAPI()
PROBE_KEY = "progress-source-fixture-key"
PROBE_MODEL = "home-ai-progress-source-fixture"
PROBE_RAW_QUERY = "household-private-query"
PROBE_PRIVATE_URL = "http://private-source.invalid/household?token=fixture-token-9f1c"
PROBE_SNIPPET = "raw fixture snippet"
PROBE_TOKEN = "fixture-token-9f1c"
PROBE_TOOL_ERROR = "tool exception fixture"
# The ordinary prefix is intentional: Open WebUI's Markdown renderer can
# normalize a leading escaped tag differently between versions.  It gives the
# browser a human-visible witness that this raw hostile title reached the
# production projection/footer boundary, while the following HTML/Markdown
# remains the injection payload being tested.
PROBE_HOSTILE_TITLE = 'Unsafe title witness <img src="x" onerror="alert(1)"> [spoof](https://evil.example)'
PROBE_PROMPT = f"Show the QA fixture for {PROBE_RAW_QUERY}."
PROBE_BLOCK_SECONDS = 3.0
PROBE_SAFE_TITLE = "Fixture source"
PROBE_ANSWER = "Here is the fixture answer."
PROBE_PROGRESS = "**Working**\n- Searching the web…\n- Reading example.com…\n"
PROBE_UNSAFE_URLS = [
    "http://100.64.0.1/", "https://nas/", "https://myhost.localhost/",
    "https://example.com\\private.local/", "https://%31%32%37.0.0.1/",
    "https://１２７．０．０．１/", "https://@example.com/", "https://example.com:bad/",
]
PROBE_SPOKEN: list[str] = []
PROBE_LIVE_RESULTS = [
    {
        "tool": "web_search",
        "status": "ok",
        "result": {
            "query": f"{PROBE_RAW_QUERY} token={PROBE_TOKEN}",
            "result_count": 1,
            "results": [{
                "title": PROBE_HOSTILE_TITLE,
                "domain": "example.com",
                "url": PROBE_PRIVATE_URL,
                "snippet": PROBE_SNIPPET,
            }],
        },
    },
    {
        "tool": "web_fetch",
        "status": "ok",
        "result": {
            "url": "https://example.com/news?token=fixture-token-9f1c#section",
            "title": PROBE_SAFE_TITLE,
            "content": PROBE_SNIPPET,
        },
    },
    {
        "tool": "web_fetch",
        "status": "failed",
        "operation_ok": False,
        "result": {"url": PROBE_PRIVATE_URL, "error": PROBE_TOOL_ERROR},
    },
]
PROBE_LIVE_RESULTS.extend({
    "tool": "web_fetch", "status": "ok", "result": {"title": "Rejected URL", "url": url},
} for url in PROBE_UNSAFE_URLS)


@lru_cache(maxsize=1)
def _fixture_app():
    """Use production rendering, registration and TTS; fake only external IO."""
    repository = Path(__file__).resolve().parents[1]
    assistant_dir = repository / "assistant"
    if str(assistant_dir) not in sys.path:
        sys.path.insert(0, str(assistant_dir))
    from trace_projection import project_trace

    spec = importlib.util.spec_from_file_location(
        "home_ai_fixture_voice_app", assistant_dir / "voice-api-app.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the production trace footer")
    app_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app_module)
    app_module.OPENAI_COMPAT_API_KEY = PROBE_KEY
    app_module.OPENAI_COMPAT_API_KEY_FILE = ""

    async def synthesize(text):
        PROBE_SPOKEN.append(text)
        return text.encode()

    async def turn(body, request):
        sink = app_module.progress_sink_context.get()
        for label in ["Searching the web…", "Reading example.com…"]:
            await sink({"phase": "tool_started", "label": label})
        await asyncio.sleep(PROBE_BLOCK_SECONDS)
        return PROBE_ANSWER, "fixture-session", project_trace(PROBE_LIVE_RESULTS)

    app_module.synthesize_pocket = synthesize
    app_module._openai_chat_turn = turn
    return app_module


def _fixture_display_answer() -> str:
    app_module = _fixture_app()
    project_trace = app_module.project_trace
    trace = project_trace(PROBE_LIVE_RESULTS)
    rendered = app_module.openai_tool_trace_footer(trace)
    raw_metadata = (PROBE_PRIVATE_URL, PROBE_SNIPPET, PROBE_TOKEN, PROBE_TOOL_ERROR)
    if not rendered or any(value in repr(trace) or value in rendered for value in raw_metadata):
        raise RuntimeError("fixture projection leaked raw tool metadata")
    if PROBE_HOSTILE_TITLE not in repr(trace):
        raise RuntimeError("fixture hostile title did not reach projection")
    return PROBE_ANSWER + rendered


def _require_probe_auth(request: Request) -> None:
    if request.headers.get("authorization") != f"Bearer {PROBE_KEY}":
        raise HTTPException(401, "QA fixture requires its disposable bearer key")


@progress_source_probe.get("/v1/models")
async def progress_source_models(request: Request):
    _require_probe_auth(request)
    return {"object": "list", "data": [{"id": PROBE_MODEL, "object": "model", "owned_by": "home-ai-qa"}]}


@progress_source_probe.post("/v1/chat/completions")
async def progress_source_chat(request: Request):
    _require_probe_auth(request)
    body = await request.json()
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    prompt = " ".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))
    if body.get("model") != PROBE_MODEL or not body.get("stream") or PROBE_RAW_QUERY not in prompt:
        raise HTTPException(400, "fixture accepts only its streamed QA model")
    _fixture_display_answer()  # Fail closed on raw-fixture projection leakage.
    return _fixture_app()._openai_stream_response(body, request, "fixture-session")


@progress_source_probe.post("/v1/audio/speech")
async def progress_source_tts(request: Request):
    _require_probe_auth(request)
    return await _fixture_app().openai_speech(request)


@progress_source_probe.get("/qa/evidence")
async def progress_source_evidence(request: Request):
    _require_probe_auth(request)
    return {
        "display": PROBE_PROGRESS + "\n---\n\n" + _fixture_display_answer(),
        "progress": PROBE_PROGRESS,
        "footer": _fixture_app().openai_tool_trace_footer(_fixture_app().project_trace(PROBE_LIVE_RESULTS)),
        "trace": _fixture_app().project_trace(PROBE_LIVE_RESULTS),
        "spoken": PROBE_SPOKEN,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OPENWEBUI_BASE_URL", "http://localhost:3000"))
    parser.add_argument("--model", default=os.getenv("OPENWEBUI_MODEL", "home-ai"))
    parser.add_argument("--user-id", default=os.getenv("OPENWEBUI_USER_ID", "qa-p0-user"))
    parser.add_argument("--token-env", default="OPENWEBUI_TOKEN", help="environment variable containing the bearer token")
    parser.add_argument("--token-file", help="read the bearer token from a protected file instead of an environment variable")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--safe-mode", action="store_true", help="allow write-shaped prompts for refusal/confirmation isolation only; never approves")
    parser.add_argument("--prompt", action="append", help="single prompt; repeat for multiple isolated chats")
    parser.add_argument("--identity-prompt", default="What's that Tom Hanks movie where he's on an island with a volleyball?",
                        help="read-only prompt used in two separate chats for subject/session isolation")
    parser.add_argument("--cache-used-gb", type=float, help="authoritative rounded cache used value from the raw tool result")
    parser.add_argument("--cache-free-gb", type=float, help="authoritative rounded cache free value from the raw tool result")
    parser.add_argument("--cache-percent", type=float, help="authoritative cache used percentage from the raw tool result")
    parser.add_argument("--progress-source-acceptance", action="store_true",
                        help="run one authenticated streamed read-only progress/source acceptance turn")
    parser.add_argument("--progress-source-prompt", default=PROBE_PROMPT,
                        help="read-only prompt for --progress-source-acceptance; use only the disposable fixture")
    parser.add_argument("--output", default="-", help="JSON output path, or - for stdout")
    args = parser.parse_args(argv)
    token = ""
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as handle:
            token = handle.read().strip()
    else:
        token = os.getenv(args.token_env, "")
    if not token:
        parser.error(f"set ${args.token_env} or --token-file; tokens are never accepted as command-line arguments")
    api = OpenWebUILive(args.base_url, token, args.model, args.user_id, args.timeout, args.safe_mode)
    try:
        # The deterministic fixture accepts exactly one prompt.  Keep this
        # mode isolated from ordinary P0 turns and their identity follow-ups.
        prompts = [] if args.progress_source_acceptance else (args.prompt or ["How full is cache?"])
        cache_expected = ({"used_gb": args.cache_used_gb, "free_gb": args.cache_free_gb, "percent": args.cache_percent}
                          if all(value is not None for value in (args.cache_used_gb, args.cache_free_gb, args.cache_percent))
                          else {})
        records = []
        for prompt in prompts:
            records.append(asdict(api.turn(prompt, expected=cache_expected if "cache" in prompt.casefold() else {})))
        if args.progress_source_acceptance:
            records.append(asdict(api.progress_source_turn(args.progress_source_prompt)))
        if not args.progress_source_acceptance:
            # Same first prompt in two independent chats is the minimum
            # isolation probe; follow-ups remain separate and never approve a
            # write.
            identical = args.identity_prompt
            chat_a = api.new_chat("P0 LIVE identical prompt A")
            chat_b = api.new_chat("P0 LIVE identical prompt B")
            records.extend(asdict(x) for x in _chain(api, [identical, "Do I have it?"], chat_a))
            records.extend(asdict(x) for x in _chain(api, [identical, "What year did it come out?"], chat_b))
        result = {"harness": "openwebui_live_p0", "base_url": args.base_url, "model": args.model,
                  "user_id": args.user_id, "safe_mode": args.safe_mode, "production_writes": 0,
                  "progress_source_acceptance": args.progress_source_acceptance,
                  "records": _redact(records)}
        output = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output == "-":
            print(output)
        else:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(output + "\n")
        return 0
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
