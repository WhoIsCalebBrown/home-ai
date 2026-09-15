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
import re
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
        "allowed_tools": {"media_status"},
    },
    "media_status_failed": {
        "text": "How is Dumb and Dumber doing?",
        "allowed_tools": {"media_status"},
    },
    "media_status_missing": {
        "text": "Is The 10th Kingdom ready?",
        "allowed_tools": {"media_status"},
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
        "require_tool": False,
    },
    "containers_filler": {
        "text": "Uh, can you tell me how many containers are running?",
        "allowed_tools": {"list_containers"},
    },
    "containers_restart": {
        "text": "Can you— how many containers are running?",
        "allowed_tools": {"list_containers"},
    },
    "containers_docker": {
        "text": "How many Docker containers are currently running?",
        "allowed_tools": {"list_containers"},
    },
    "containers_total": {
        "text": "What is the total number of containers?",
        "allowed_tools": {"list_containers"},
    },
    "weather_tomorrow": {
        "text": "What will the weather be tomorrow?",
        "allowed_tools": {"weather_forecast"},
    },
    "weather_filler": {
        "text": "Uh, could you check the weather for me?",
        "allowed_tools": {"weather_forecast"},
    },
    "plex_recent": {
        "text": "What was most recently added to Plex?",
        "allowed_tools": {"plex_recently_added"},
    },
    "politics_current": {
        "text": "What are the latest developments in American politics?",
        "allowed_tools": {"web_search", "web_fetch"},
    },
    "web_explicit": {
        "text": "Search the web for the latest news about Nvidia.",
        "allowed_tools": {"web_search", "web_fetch"},
    },
    "frigate_live": {
        "text": "Is anyone at the front door right now?",
        "allowed_tools": {"frigate_snapshot", "frigate_recent_events"},
    },
    "frigate_history": {
        "text": "What happened at the front door about an hour ago?",
        "allowed_tools": {"frigate_recent_events"},
    },
}

READ_ONLY_CONVERSATIONS = {
    "server_followup": [
        ("How many containers are running?", {"list_containers"}),
        ("What about stopped?", {"list_containers"}),
    ],
    "server_ambiguous_start": [
        ("How many containers are running?", {"list_containers"}),
        ("What about start?", set()),
    ],
    "web_correction": [
        ("What happened today in American politics?", {"web_search", "web_fetch"}),
        ("Can you search the web for that?", {"web_search", "web_fetch"}),
    ],
    "camera_history": [
        ("About an hour ago, what happened at the front door?", {"frigate_recent_events"}),
        # The live read-only state may legitimately contain no matching event.
        # In that branch the safe response is deterministic and must not call
        # a visual tool or fall back to the live camera.
        ("What were they wearing?", set()),
        ("What's at the front door right now?", {"frigate_snapshot"}),
    ],
    "media_status_chain": [
        ("How is The Hobbit doing?", {"media_status"}),
        ("What about Dumb and Dumber?", {"media_status"}),
    ],
    "media_status_asr_repair": [
        ("How is The Hobbit doing?", {"media_status"}),
        # Regression for the observed Faster-Whisper shape where a status
        # question became "I was dumb in Dumberdorn".  The retained workflow
        # must remain authoritative; no title alias or write is permitted.
        ("I was dumb in Dumberdorn.", {"media_status"}),
    ],
    "web_then_weather": [
        ("What happened today in American politics?", {"web_search", "web_fetch"}),
        ("What's the weather today?", {"weather_forecast"}),
    ],
    "media_then_web": [
        ("Is The 10th Kingdom ready?", {"media_status"}),
        ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
    ],
    "weather_then_containers": [
        ("What's the weather today?", {"weather_forecast"}),
        ("How many containers are running?", {"list_containers"}),
    ],
    "camera_then_web": [
        ("What's happening at the front door right now?", {"frigate_snapshot"}),
        ("What's happening in American politics today?", {"web_search", "web_fetch"}),
    ],
    "web_then_camera": [
        ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
        ("Is anyone at the front door right now?", {"frigate_snapshot"}),
    ],
    "plex_then_storage": [
        ("What's the last thing added to Plex?", {"plex_recently_added"}),
        ("How much storage do I have left?", {"get_storage_status"}),
    ],
}

# Generated read-only conversation families. These deliberately exercise the
# same semantic transitions with different natural language, without adding
# any write-capable turn to the live audio lane.
_GENERATED_CONVERSATION_FAMILIES = [
    (
        "server_weather",
        [
            ("How many containers are running?", {"list_containers"}),
            ("Which containers are up right now?", {"list_containers"}),
            ("Tell me the Docker running count.", {"list_containers"}),
            ("Are all the containers running?", {"list_containers"}),
            ("What is the current container total?", {"list_containers"}),
        ],
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How cold is it outside?", {"weather_forecast"}),
            ("What are conditions in Welland?", {"weather_forecast"}),
            ("Will it rain tomorrow?", {"weather_forecast"}),
            ("Give me the forecast for tomorrow.", {"weather_forecast"}),
        ],
    ),
    (
        "weather_server",
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How is the weather in Welland?", {"weather_forecast"}),
            ("What is the forecast right now?", {"weather_forecast"}),
            ("Will it rain today?", {"weather_forecast"}),
            ("How warm is it outside?", {"weather_forecast"}),
        ],
        [
            ("How many containers are running?", {"list_containers"}),
            ("What Docker services are up?", {"list_containers"}),
            ("Tell me how many containers are stopped.", {"list_containers"}),
            ("Which containers are currently running?", {"list_containers"}),
            ("How many containers are there?", {"list_containers"}),
        ],
    ),
    (
        "plex_storage",
        [
            ("What's the last thing added to Plex?", {"plex_recently_added"}),
            ("What was recently added to my library?", {"plex_recently_added"}),
            ("Show me the newest Plex addition.", {"plex_recently_added"}),
            ("Which movie did Plex add last?", {"plex_recently_added"}),
            ("What's new in Plex?", {"plex_recently_added"}),
        ],
        [
            ("How much storage do I have left?", {"get_storage_status"}),
            ("How much free space is on the server?", {"get_storage_status"}),
            ("Is there enough disk space left?", {"get_storage_status"}),
            ("How much room remains in cache?", {"get_storage_status"}),
            ("What is the storage status?", {"get_storage_status"}),
        ],
    ),
    (
        "media_web",
        [
            ("How is The Hobbit doing?", {"media_status"}),
            ("Is Dumb and Dumber ready?", {"media_status"}),
            ("What's happening with The 10th Kingdom?", {"media_status"}),
            ("Did Dumb and Dumber get found?", {"media_status"}),
            ("Where is The Hobbit in the pipeline?", {"media_status"}),
        ],
        [
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("What happened today in American politics?", {"web_search", "web_fetch"}),
            ("Search the web for current SpaceX news.", {"web_search", "web_fetch"}),
            ("What are today's major world events?", {"web_search", "web_fetch"}),
            ("Look up current technology developments.", {"web_search", "web_fetch"}),
        ],
    ),
    (
        "camera_web",
        [
            ("What's happening at the front door right now?", {"frigate_snapshot"}),
            ("Is anyone at the front door now?", {"frigate_snapshot"}),
            ("What does the front door camera show?", {"frigate_snapshot"}),
            ("Can you check the camera right now?", {"frigate_snapshot"}),
            ("Is there anyone at the door currently?", {"frigate_snapshot"}),
        ],
        [
            ("What's happening in American politics today?", {"web_search", "web_fetch"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("Can you search current news about AI?", {"web_search", "web_fetch"}),
            ("What happened today in Canada?", {"web_search", "web_fetch"}),
            ("Look up today's technology headlines.", {"web_search", "web_fetch"}),
        ],
    ),
    (
        "web_camera",
        [
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("What happened today in American politics?", {"web_search", "web_fetch"}),
            ("Search online for current AI news.", {"web_search", "web_fetch"}),
            ("What are today's world events?", {"web_search", "web_fetch"}),
            ("Look up the latest technology developments.", {"web_search", "web_fetch"}),
        ],
        [
            ("What's at the front door right now?", {"frigate_snapshot"}),
            ("Is anyone at the door currently?", {"frigate_snapshot"}),
            ("Can you check the front door camera now?", {"frigate_snapshot"}),
            ("What is happening at the front door?", {"frigate_snapshot"}),
            ("Show me the live front door view.", {"frigate_snapshot"}),
        ],
    ),
    (
        "history_weather",
        [
            ("What happened at the front door about an hour ago?", {"frigate_recent_events"}),
            ("Show me recent front door events.", {"frigate_recent_events"}),
            ("What did the camera record this morning?", {"frigate_recent_events"}),
            ("Were there any people at the front door recently?", {"frigate_recent_events"}),
            ("What happened at the door earlier today?", {"frigate_recent_events"}),
        ],
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How cold is it outside?", {"weather_forecast"}),
            ("What will the weather be tomorrow?", {"weather_forecast"}),
            ("Will it rain today?", {"weather_forecast"}),
            ("Give me the current Welland forecast.", {"weather_forecast"}),
        ],
    ),
    (
        "mixed_domain",
        [
            ("How many containers are running?", {"list_containers"}),
            ("What's the weather today?", {"weather_forecast"}),
            ("How is The Hobbit doing?", {"media_status"}),
            ("What's happening at the front door right now?", {"frigate_snapshot"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
        ],
        [
            ("What's the last thing added to Plex?", {"plex_recently_added"}),
            ("How many containers are stopped?", {"list_containers"}),
            ("How much storage do I have left?", {"get_storage_status"}),
            ("What happened at the front door about an hour ago?", {"frigate_recent_events"}),
            ("What are today's major world events?", {"web_search", "web_fetch"}),
        ],
    ),
]
for _family, _first_turns, _second_turns in _GENERATED_CONVERSATION_FAMILIES:
    for _index, (_first, _first_tools) in enumerate(_first_turns):
        _second, _second_tools = _second_turns[_index]
        READ_ONLY_CONVERSATIONS[f"generated_{_family}_{_index:02d}"] = [
            (_first, _first_tools), (_second, _second_tools)
        ]

# Additional multi-turn families used for the long-form voice qualification
# lane.  Every turn is read-only; these deliberately exercise context changes
# and status/diagnostic boundaries rather than repeating one happy path.
_ADVERSARIAL_CONVERSATION_FAMILIES = [
    (
        "media_diagnostics",
        [
            ("How is The Hobbit doing?", {"media_status"}),
            ("Is Dumb and Dumber ready yet?", {"media_status"}),
            ("Where is The 10th Kingdom in the pipeline?", {"media_status"}),
            ("Did Dumb and Dumber get found?", {"media_status"}),
            ("What's the status of The Hobbit?", {"media_status"}),
        ],
        [
            ("Why isn't it ready?", {"media_diagnose"}),
            ("What's taking so long?", {"media_diagnose"}),
            ("Can you diagnose that request?", {"media_diagnose"}),
            ("Is it stuck?", {"media_diagnose"}),
            ("Is it in Plex yet?", {"media_status"}),
        ],
    ),
    (
        "media_to_local",
        [
            ("How is The Hobbit doing?", {"media_status"}),
            ("Is Dumb and Dumber ready?", {"media_status"}),
            ("What's happening with The 10th Kingdom?", {"media_status"}),
            ("Did The Hobbit finish?", {"media_status"}),
            ("Where is Dumb and Dumber?", {"media_status"}),
        ],
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How many containers are running?", {"list_containers"}),
            ("What's at the front door right now?", {"frigate_snapshot"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("What's new in Plex?", {"plex_recently_added"}),
        ],
    ),
    (
        "local_to_media",
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How many containers are running?", {"list_containers"}),
            ("What's new in Plex?", {"plex_recently_added"}),
            ("What's happening at the front door right now?", {"frigate_snapshot"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
        ],
        [
            ("How is The Hobbit doing?", {"media_status"}),
            ("Is Dumb and Dumber ready?", {"media_status"}),
            ("What is happening with The 10th Kingdom?", {"media_status"}),
            ("Did The Hobbit get found?", {"media_status"}),
            ("Where is Dumb and Dumber in the pipeline?", {"media_status"}),
        ],
    ),
    (
        "history_live_boundary",
        [
            ("What happened at the front door about an hour ago?", {"frigate_recent_events"}),
            ("Show me recent front door events.", {"frigate_recent_events"}),
            ("What did the camera record this morning?", {"frigate_recent_events"}),
            ("Were there people at the front door recently?", {"frigate_recent_events"}),
            ("What happened at the door earlier today?", {"frigate_recent_events"}),
        ],
        [
            ("What's at the front door right now?", {"frigate_snapshot"}),
            ("Is anyone there currently?", {"frigate_snapshot"}),
            ("Can you show the live front door view?", {"frigate_snapshot"}),
            ("What's happening outside now?", {"frigate_snapshot"}),
            ("Who is at the door right now?", {"frigate_snapshot"}),
        ],
    ),
    (
        "web_local_boundary",
        [
            ("What happened today in American politics?", {"web_search", "web_fetch"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("Search the web for current SpaceX news.", {"web_search", "web_fetch"}),
            ("What are today's major world events?", {"web_search", "web_fetch"}),
            ("Look up current technology developments.", {"web_search", "web_fetch"}),
        ],
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How many containers are running?", {"list_containers"}),
            ("What's at the front door right now?", {"frigate_snapshot"}),
            ("How is The Hobbit doing?", {"media_status"}),
            ("What's new in Plex?", {"plex_recently_added"}),
        ],
    ),
    (
        "weather_location_retention",
        [
            ("What's the weather in Welland today?", {"weather_forecast"}),
            ("What are conditions in Welland?", {"weather_forecast"}),
            ("Will it rain tomorrow?", {"weather_forecast"}),
            ("How cold is it outside?", {"weather_forecast"}),
            ("Give me the forecast for tomorrow.", {"weather_forecast"}),
        ],
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How many containers are running?", {"list_containers"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("What's at the front door right now?", {"frigate_snapshot"}),
            ("Is The Hobbit ready?", {"media_status"}),
        ],
    ),
    (
        "server_plex_boundary",
        [
            ("How many containers are running?", {"list_containers"}),
            ("How many containers are stopped?", {"list_containers"}),
            ("What Docker services are up?", {"list_containers"}),
            ("What's the container total?", {"list_containers"}),
            ("What is the server status?", {"list_containers", "get_server_overview"}),
        ],
        [
            ("What's the last thing added to Plex?", {"plex_recently_added"}),
            ("What's new in Plex?", {"plex_recently_added"}),
            ("Which movie did Plex add last?", {"plex_recently_added"}),
            ("How much storage do I have left?", {"get_storage_status"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
        ],
    ),
    (
        "safe_delivery_contrast",
        [
            ("Send me the Dumb and Dumber movie file here.", set()),
            ("Upload Dumb and Dumber into this chat.", set()),
            ("Play the movie inside this conversation.", set()),
            ("Stream Dumb and Dumber here.", set()),
            ("Attach the video file.", set()),
        ],
        [
            ("What's the weather today?", {"weather_forecast"}),
            ("How is The Hobbit doing?", {"media_status"}),
            ("What's at the front door right now?", {"frigate_snapshot"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("How many containers are running?", {"list_containers"}),
        ],
    ),
    (
        "referential_repair",
        [
            ("How is The Hobbit doing?", {"media_status"}),
            ("What's at the front door right now?", {"frigate_snapshot"}),
            ("What happened today in American politics?", {"web_search", "web_fetch"}),
            ("What's the weather today?", {"weather_forecast"}),
            ("How many containers are running?", {"list_containers"}),
        ],
        [
            ("What about Dumb and Dumber?", {"media_status"}),
            ("What's happening in American politics today?", {"web_search", "web_fetch"}),
            ("Is anyone at the front door now?", {"frigate_snapshot"}),
            ("How cold is it outside?", {"weather_forecast"}),
            ("What about stopped?", {"list_containers"}),
        ],
    ),
    (
        "current_info_switch",
        [
            ("What's happening at the front door right now?", {"frigate_snapshot"}),
            ("How is The Hobbit doing?", {"media_status"}),
            ("What's the weather today?", {"weather_forecast"}),
            ("How many containers are running?", {"list_containers"}),
            ("What's new in Plex?", {"plex_recently_added"}),
        ],
        [
            ("What's happening in American politics today?", {"web_search", "web_fetch"}),
            ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
            ("What happened today in Canada?", {"web_search", "web_fetch"}),
            ("What's the latest news about technology?", {"web_search", "web_fetch"}),
            ("Can you search the web for current AI news?", {"web_search", "web_fetch"}),
        ],
    ),
]
for _family, _first_turns, _second_turns in _ADVERSARIAL_CONVERSATION_FAMILIES:
    for _index, (_first, _first_tools) in enumerate(_first_turns):
        _second, _second_tools = _second_turns[_index]
        READ_ONLY_CONVERSATIONS[f"adversarial_{_family}_{_index:02d}"] = [
            (_first, _first_tools), (_second, _second_tools)
        ]

# Independent read-only voice variants. These are intentionally phrased as
# different user requests rather than repeated invocations of one sentence.
# The matrix is executed through the real audio lane; it never includes a
# write-capable tool.
READ_ONLY_VARIANTS = [
    ("How many Docker containers are up?", {"list_containers"}),
    ("Tell me the running container count.", {"list_containers"}),
    ("Are any containers stopped?", {"list_containers"}),
    ("Which containers are currently running?", {"list_containers"}),
    ("Can you report the Docker service status?", {"list_containers"}),
    ("What's the weather like in Welland today?", {"weather_forecast"}),
    ("Give me tomorrow's forecast.", {"weather_forecast"}),
    ("Will it rain tomorrow?", {"weather_forecast"}),
    ("How cold is it outside today?", {"weather_forecast"}),
    ("What are the conditions in Welland right now?", {"weather_forecast"}),
    ("What's new in Plex?", {"plex_recently_added"}),
    ("Show me the latest Plex addition.", {"plex_recently_added"}),
    ("What was most recently added to my library?", {"plex_recently_added"}),
    ("Which movie did Plex add last?", {"plex_recently_added"}),
    ("Tell me the newest item in Plex.", {"plex_recently_added"}),
    ("How much room is left on storage?", {"get_storage_status"}),
    ("How much disk space is free?", {"get_storage_status"}),
    ("Do I have enough storage left?", {"get_storage_status"}),
    ("What is the free space on the server?", {"get_storage_status"}),
    ("How much space remains in cache?", {"get_storage_status"}),
    ("Is The Hobbit ready in Plex?", {"media_status"}),
    ("Did The Hobbit finish?", {"media_status"}),
    ("Is Dumb and Dumber ready?", {"media_status"}),
    ("Did Dumb and Dumber get found?", {"media_status"}),
    ("What is happening with The 10th Kingdom?", {"media_status"}),
    ("Is The 10th Kingdom ready yet?", {"media_status"}),
    ("What's the status of The Hobbit?", {"media_status"}),
    ("Has Dumb and Dumber downloaded?", {"media_status"}),
    ("Can I watch The Hobbit now?", {"media_status"}),
    ("Where is Dumb and Dumber in the pipeline?", {"media_status"}),
    ("Search the web for today's politics.", {"web_search", "web_fetch"}),
    ("What's the latest news about Nvidia?", {"web_search", "web_fetch"}),
    ("What's happening in technology today?", {"web_search", "web_fetch"}),
    ("Look up current news about SpaceX.", {"web_search", "web_fetch"}),
    ("Can you find recent developments in American politics?", {"web_search", "web_fetch"}),
    ("Search online for the latest Nvidia developments.", {"web_search", "web_fetch"}),
    ("What are today's major world events?", {"web_search", "web_fetch"}),
    ("Find current news about artificial intelligence.", {"web_search", "web_fetch"}),
    ("What's at the front door right now?", {"frigate_snapshot"}),
    ("Is anyone at the door currently?", {"frigate_snapshot"}),
    ("Can you check the front door camera now?", {"frigate_snapshot"}),
    ("What happened at the front door this morning?", {"frigate_recent_events"}),
    ("Show me recent front door events.", {"frigate_recent_events"}),
    ("Were there any people at the front door recently?", {"frigate_recent_events"}),
    ("What did the front door camera record today?", {"frigate_recent_events"}),
    ("Send the movie file into this chat.", set()),
    ("Upload Dumb and Dumber here.", set()),
    ("Can you attach the video file?", set()),
    ("Play the movie inside this conversation.", set()),
]
for _index, (_text, _tools) in enumerate(READ_ONLY_VARIANTS):
    READ_ONLY_SCENARIOS[f"variant_{_index:03d}"] = {
        "text": _text,
        "allowed_tools": _tools,
        "require_tool": bool(_tools),
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
    classification: str = ""


def classify_safe_non_success(trace: list[dict], answer: str, allowed_tools: set[str]) -> str | None:
    """Classify truthful safe outcomes separately from routing failures.

    A read-only tool can be selected and fail honestly, or ASR can turn a
    safe status question into a potentially mutating phrase.  Neither should
    be reported as a missing-tool routing defect when the response is
    explicitly truthful/confirmatory and no write-capable tool ran.
    """
    if any(item.get("tool") in allowed_tools and item.get("status") in {"error", "unavailable", "timeout"} for item in trace):
        if re.search(r"can't verify|couldn't verify|unavailable|couldn't reach|no live", answer, re.I):
            return "backend_read_failure_truthful"
    if not trace and re.search(r"did you mean|could you clarify|need more details|not sure", answer, re.I):
        return "safe_stt_recovery"
    if not trace and re.search(r"couldn't verify the current media status|no matching live workflow", answer, re.I):
        return "safe_stt_recovery"
    return None


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
        elif scenario.get("require_tool", bool(scenario["allowed_tools"])) and not selected:
            result.classification = classify_safe_non_success(result.tool_trace or [], result.answer, scenario["allowed_tools"]) or "routing_failure"
            if not result.classification.startswith(("backend_", "safe_")):
                result.error = "expected a read-only tool, but no tool was selected"
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
            classification = ""
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
            elif allowed_tools and not selected:
                classification = classify_safe_non_success(traces, answer, allowed_tools) or "routing_failure"
                if not classification.startswith(("backend_", "safe_")):
                    error = "expected a read-only tool, but no tool was selected"
            else:
                classification = "tool_success" if selected else "no_tool_expected"
            results.append({"turn": index + 1, "source_text": text, "transcript": transcript,
                            "answer": answer, "tools": traces, "audio_chunks": chunks,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                            "error": error, "classification": classification})
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=[*sorted(READ_ONLY_SCENARIOS), "all"], default="containers")
    parser.add_argument("--conversation", choices=[*sorted(READ_ONLY_CONVERSATIONS), "all"])
    parser.add_argument("--client-id", default="qa-audio-e2e")
    args = parser.parse_args()
    if args.conversation == "all":
        results = []
        for index, name in enumerate(sorted(READ_ONLY_CONVERSATIONS)):
            turns = await run_conversation(name, f"{args.client_id}-{index}")
            results.append({"conversation": name, "turns": turns})
            print(json.dumps(results[-1], ensure_ascii=False, sort_keys=True))
        return 1 if any(turn.get("error") for item in results for turn in item["turns"]) else 0
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
