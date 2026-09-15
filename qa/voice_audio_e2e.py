#!/usr/bin/env python3
"""Read-only full audio-lane smoke harness.

The script is intentionally run inside Home-AI-Assistant (or an equivalent
isolated Assistant image).  It synthesizes speech through the already deployed
Pocket endpoint, sends the WAV through the real Assistant WebSocket, and
records only semantic messages.  TTS audio chunks are counted and discarded.

This harness is restricted to read-only prompts by its scenario catalog.  It
fails closed if the Assistant selects a write-capable tool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass

import httpx
import websockets


READ_ONLY_SCENARIOS = {
    "containers": {
        "text": "How many containers are running?",
        "allowed_tools": {"list_containers"},
        "expected_transcript": "how many containers are running.",
    },
    "weather": {
        "text": "What's the weather today?",
        "allowed_tools": {"weather_forecast"},
    },
    "plex": {
        "text": "What's the last thing added to Plex?",
        "allowed_tools": {"plex_recently_added"},
    },
    "stopped": {
        "text": "How many containers are stopped?",
        "allowed_tools": {"list_containers"},
    },
    "storage": {
        "text": "How much storage do I have left?",
        "allowed_tools": {"get_storage_status"},
    },
    "media_status": {
        "text": "How is The Hobbit doing?",
        "allowed_tools": {"media_plan_goal"},
    },
    "politics": {
        "text": "What happened today in American politics?",
        "allowed_tools": {"web_search", "web_fetch"},
    },
    "nvidia": {
        "text": "What's the latest news about Nvidia?",
        "allowed_tools": {"web_search", "web_fetch"},
    },
    "frigate": {
        "text": "What's happening at the front door right now?",
        "allowed_tools": {"frigate_snapshot"},
    },
    "direct_file": {
        "text": "Send me the Dumb and Dumber movie file here.",
        "allowed_tools": set(),
    },
    "containers_filler": {
        "text": "Uh, can you tell me how many containers are running?",
        "allowed_tools": {"list_containers"},
    },
    "containers_restart": {
        "text": "Can you— how many containers are running?",
        "allowed_tools": {"list_containers"},
    },
}

READ_ONLY_CONVERSATIONS = {
    "server_followup": [
        ("How many containers are running?", {"list_containers"}),
        ("What about stopped?", {"list_containers"}),
    ],
    "web_correction": [
        ("What happened today in American politics?", {"web_search", "web_fetch"}),
        ("Can you search the web for that?", {"web_search", "web_fetch"}),
    ],
    "camera_history": [
        ("About an hour ago, what happened at the front door?", {"frigate_recent_events"}),
        ("What were they wearing?", {"frigate_event_snapshot", "frigate_event_activity"}),
        ("What's at the front door right now?", {"frigate_snapshot"}),
    ],
}


@dataclass
class AudioResult:
    scenario: str
    source_text: str
    transcript: str = ""
    tool_trace: list[dict] | None = None
    answer: str = ""
    semantic_messages: int = 0
    audio_chunks: int = 0
    elapsed_ms: float = 0.0
    error: str | None = None


async def run_scenario(name: str, client_id: str) -> AudioResult:
    scenario = READ_ONLY_SCENARIOS[name]
    result = AudioResult(name, scenario["text"], tool_trace=[])
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            response = await http.post(
                "http://pocket-tts:8095/v1/audio/speech",
                json={"input": scenario["text"]},
            )
            response.raise_for_status()
            wav = response.content
        async with websockets.connect("ws://127.0.0.1:8088/ws", max_size=20 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"type": "start", "client_id": client_id}))
            await ws.recv()
            await ws.send(wav)
            await ws.send(json.dumps({"type": "audio_end"}))
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                message = await asyncio.wait_for(ws.recv(), timeout=15)
                if isinstance(message, bytes):
                    result.audio_chunks += 1
                    continue
                payload = json.loads(message)
                kind = payload.get("type")
                if kind in {"state", "transcript", "text", "trace", "error", "done"}:
                    result.semantic_messages += 1
                if kind == "transcript":
                    result.transcript = payload.get("text", "")
                elif kind == "text":
                    result.answer = payload.get("text", "")
                elif kind == "trace":
                    result.tool_trace = payload.get("tools") or []
                elif kind == "error":
                    result.error = payload.get("error") or payload.get("message") or "assistant error"
                elif kind == "audio_chunk":
                    result.audio_chunks += 1
                elif kind == "done":
                    break
            else:
                result.error = "audio lane timeout"
        selected = {entry.get("tool") for entry in result.tool_trace or [] if entry.get("status") == "ok"}
        unexpected = selected - scenario["allowed_tools"]
        if unexpected:
            result.error = f"unexpected tool selection: {sorted(unexpected)}"
    except Exception as exc:  # pragma: no cover - exercised by live harness
        result.error = f"{type(exc).__name__}: {exc}"
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return result


async def run_conversation(name: str, client_id: str) -> list[dict]:
    """Run multiple real audio turns on one WebSocket/session context."""
    turns = READ_ONLY_CONVERSATIONS[name]
    results = []
    async with websockets.connect("ws://127.0.0.1:8088/ws", max_size=20 * 1024 * 1024) as ws:
        for index, (text, allowed_tools) in enumerate(turns):
            started = time.perf_counter()
            transcript = ""
            answer = ""
            traces = []
            chunks = 0
            error = None
            try:
                async with httpx.AsyncClient(timeout=60) as http:
                    response = await http.post("http://pocket-tts:8095/v1/audio/speech", json={"input": text})
                    response.raise_for_status()
                    wav = response.content
                await ws.send(json.dumps({"type": "start", "client_id": client_id}))
                await ws.recv()
                await ws.send(wav)
                await ws.send(json.dumps({"type": "audio_end"}))
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    message = await asyncio.wait_for(ws.recv(), timeout=20)
                    if isinstance(message, bytes):
                        chunks += 1
                        continue
                    payload = json.loads(message)
                    kind = payload.get("type")
                    if kind == "transcript": transcript = payload.get("text", "")
                    elif kind == "text": answer = payload.get("text", "")
                    elif kind == "trace": traces = payload.get("tools") or []
                    elif kind == "audio_chunk": chunks += 1
                    elif kind == "error": error = payload.get("error") or payload.get("message")
                    elif kind == "done": break
                else:
                    error = "audio lane timeout"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            selected = {item.get("tool") for item in traces if item.get("status") == "ok"}
            unexpected = selected - allowed_tools
            if unexpected: error = f"unexpected tool selection: {sorted(unexpected)}"
            results.append({"turn": index + 1, "source_text": text, "transcript": transcript,
                            "answer": answer, "tools": traces, "audio_chunks": chunks,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                            "error": error})
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=[*sorted(READ_ONLY_SCENARIOS), "all"], default="containers")
    parser.add_argument("--conversation", choices=sorted(READ_ONLY_CONVERSATIONS))
    parser.add_argument("--client-id", default="qa-audio-e2e")
    args = parser.parse_args()
    if args.conversation:
        results = await run_conversation(args.conversation, args.client_id)
        print(json.dumps({"conversation": args.conversation, "turns": results}, ensure_ascii=False, sort_keys=True))
        return 1 if any(turn.get("error") for turn in results) else 0
    names = sorted(READ_ONLY_SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = []
    for index, name in enumerate(names):
        result = await run_scenario(name, f"{args.client_id}-{index}")
        results.append(result.__dict__)
        print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))
    return 1 if any(item.get("error") for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
