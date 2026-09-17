#!/usr/bin/env python3
"""Run bounded P1 conversational baselines through the QA Open WebUI path.

This runner deliberately contains no approval utterances.  Request-shaped turns
are useful for observing planning, but the server-enforced QA identity remains
read-only and cannot execute a business mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from openwebui_live_p0 import OpenWebUILive, _redact


APPROVAL = re.compile(r"^\s*(?:yes|yep|yeah|confirm|approve|do it|go ahead)\s*[.!]?\s*$", re.I)


SCENARIOS: dict[str, list[str]] = {
    "descriptive_continuity": [
        "What's that Tom Hanks movie where he's on an island with a volleyball?",
        "What year did it come out?",
        "Do I have it?",
        "Can you look it up on the internet?",
    ],
    "descriptive_brad_pitt": [
        "What's that Brad Pitt movie about fly fishing in Montana?",
    ],
    "descriptive_matt_damon": [
        "What's that movie where Matt Damon grows potatoes on Mars?",
    ],
    "accumulated_clues": [
        "I'm trying to remember a Brad Pitt movie.",
        "He does fly fishing.",
        "I think it's in Montana.",
    ],
    "room_disambiguation": [
        "Get The Room.",
        "No, the 2003 movie.",
        "The Tommy Wiseau one.",
    ],
    "library_count_scope": [
        "How many movies do I have?",
        "What about anime?",
    ],
    "container_count_scope": [
        "How many containers are running?",
        "What about stopped?",
    ],
    "cache_ambiguous_status": [
        "How full is cache?",
        "Is it running?",
    ],
    "broad_avengers_inventory": [
        "What Avengers stuff do I have?",
    ],
    "avengers_completeness": [
        "Do I have all the Avengers movies?",
    ],
    "avengers_request_scope": [
        "Get Avengers.",
    ],
    "music_read_only": [
        "What Radiohead music do I have?",
        "Is OK Computer in my library?",
    ],
    "topic_switches": [
        "What's the weather?",
        "Any recent camera events?",
        "How full is cache?",
        "Give me an in-depth review of Canadian news today.",
    ],
}

HELD_OUT_SCENARIOS: dict[str, list[str]] = {
    "heldout_identity_continuity": [
        "What's the movie about a drummer who loses his hearing?",
        "When was it released?",
        "Is it in my library?",
        "Search online for more details.",
    ],
    "heldout_descriptive_request": [
        "I want to add the movie about a linguist communicating with aliens.",
    ],
    "heldout_incremental_clues": [
        "I'm trying to remember a science-fiction movie.",
        "A soldier keeps reliving the same battle.",
        "Emily Blunt is in it.",
    ],
    "heldout_conflicting_constraints": [
        "Get Crash.",
        "The 2004 one.",
        "The David Cronenberg film.",
    ],
    "heldout_count_scope": [
        "How many films are in my library?",
        "What about TV?",
    ],
    "heldout_broad_inventory": [
        "What Star Trek content do I have?",
    ],
    "heldout_music_read_only": [
        "What Pink Floyd music do I have?",
        "Is The Dark Side of the Moon in my library?",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OPENWEBUI_BASE_URL", "http://localhost:18091"))
    parser.add_argument("--model", default=os.getenv("OPENWEBUI_MODEL", "home-ai-qa"))
    parser.add_argument("--user-id", default=os.getenv("OPENWEBUI_USER_ID", "qa-p1-user"))
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--output", required=True)
    all_scenarios = {**SCENARIOS, **HELD_OUT_SCENARIOS}
    parser.add_argument("--scenario", action="append", choices=sorted(all_scenarios))
    parser.add_argument("--held-out", action="store_true", help="run held-out paraphrases instead of the reported baseline wording")
    args = parser.parse_args()

    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        parser.error("token file is empty")
    selected = args.scenario or list(HELD_OUT_SCENARIOS if args.held_out else SCENARIOS)
    if any(APPROVAL.fullmatch(prompt) for name in selected for prompt in all_scenarios[name]):
        raise SystemExit("approval utterances are forbidden in the live read-only runner")

    api = OpenWebUILive(args.base_url, token, args.model, args.user_id, args.timeout, safe_mode=True)
    results: list[dict] = []
    try:
        for name in selected:
            chat_id = api.new_chat(f"P1 BASELINE {name}")
            turns = [asdict(api.turn(prompt, chat_id=chat_id)) for prompt in all_scenarios[name]]
            results.append({"scenario": name, "chat_id": chat_id, "turns": turns})

        # The exact same first prompt in independent chats remains an explicit
        # regression case; neither chain contains a state-changing approval.
        if not args.held_out or not args.scenario:
            identical = ("What's the movie about a drummer who loses his hearing?" if args.held_out
                         else "What's that Tom Hanks movie where he's on an island with a volleyball?")
            for suffix, followup in (("A", "Do I have it?"), ("B", "What year did it come out?")):
                chat_id = api.new_chat(f"P1 {'HELD OUT' if args.held_out else 'BASELINE'} identical {suffix}")
                turns = [asdict(api.turn(prompt, chat_id=chat_id)) for prompt in (identical, followup)]
                results.append({"scenario": f"identical_prompt_{suffix}", "chat_id": chat_id, "turns": turns})
    finally:
        api.close()

    output = {
        "harness": "openwebui_live_p1",
        "mode": "live_readonly",
        "model": args.model,
        "user_id": args.user_id,
        "production_writes": 0,
        "approval_utterances": 0,
        "held_out": args.held_out,
        "scenarios": results,
    }
    Path(args.output).write_text(json.dumps(_redact(output), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
