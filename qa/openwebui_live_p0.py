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
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import httpx


WRITE_WORDS = re.compile(r"\b(?:confirm|approve|download|acquire|add|request|get|grab|restart|delete|remove|turn on|turn off)\b", re.I)
SECRET_KEYS = re.compile(r"(?:token|authorization|password|secret|api[_-]?key)", re.I)


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
        self.chat_documents: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self.client.close()

    def new_chat(self, title: str = "P0 LIVE regression") -> str:
        chat = {"title": title, "models": [self.model], "messages": [],
                "history": {"messages": {}, "currentId": None}, "tags": ["p0-live-regression"]}
        response = self.client.post(f"{self.base_url}/api/v1/chats/new", json={"chat": chat})
        response.raise_for_status()
        chat_id = str(response.json()["id"])
        self.chat_documents[chat_id] = chat
        return chat_id

    def _persist_visible_turn(self, chat_id: str, prompt: str, answer: str) -> None:
        chat = self.chat_documents.get(chat_id)
        if not chat:
            return
        history = chat["history"]
        messages = history["messages"]
        parent_id = history.get("currentId")
        user_message_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        if parent_id in messages:
            messages[parent_id].setdefault("childrenIds", []).append(user_message_id)
        messages[user_message_id] = {
            "id": user_message_id, "parentId": parent_id, "childrenIds": [assistant_message_id],
            "role": "user", "content": prompt, "timestamp": int(time.time()),
        }
        messages[assistant_message_id] = {
            "id": assistant_message_id, "parentId": user_message_id, "childrenIds": [],
            "role": "assistant", "content": answer, "done": True, "model": self.model,
            "timestamp": int(time.time()),
        }
        history["currentId"] = assistant_message_id
        response = self.client.post(f"{self.base_url}/api/v1/chats/{chat_id}", json={"chat": chat})
        response.raise_for_status()

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
            self._persist_visible_turn(chat_id, prompt, record.answer)
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
        prompts = args.prompt or ["How full is cache?"]
        cache_expected = ({"used_gb": args.cache_used_gb, "free_gb": args.cache_free_gb, "percent": args.cache_percent}
                          if all(value is not None for value in (args.cache_used_gb, args.cache_free_gb, args.cache_percent))
                          else {})
        records = []
        for prompt in prompts:
            records.append(asdict(api.turn(prompt, expected=cache_expected if "cache" in prompt.casefold() else {})))
        # Same first prompt in two independent chats is the minimum isolation
        # probe; follow-ups remain separate and never approve a write.
        identical = args.identity_prompt
        chat_a = api.new_chat("P0 LIVE identical prompt A")
        chat_b = api.new_chat("P0 LIVE identical prompt B")
        records.extend(asdict(x) for x in _chain(api, [identical, "Do I have it?"], chat_a))
        records.extend(asdict(x) for x in _chain(api, [identical, "What year did it come out?"], chat_b))
        result = {"harness": "openwebui_live_p0", "base_url": args.base_url, "model": args.model,
                  "user_id": args.user_id, "safe_mode": args.safe_mode, "production_writes": 0,
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
