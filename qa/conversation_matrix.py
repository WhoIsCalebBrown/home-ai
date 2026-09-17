"""Deterministic, side-effect-free conversation QA matrix.

This lane intentionally stops at planning.  It imports only pure routing
functions from the Assistant source and cannot reach production adapters.
"""

import ast
import json
from pathlib import Path


def load_router():
    source = Path(__file__).resolve().parents[1] / "assistant/voice-api-app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {
        "media_goal_request", "media_acquisition_language", "media_identity_signal", "media_status_question",
        "media_title_status_signal", "visual_question",
        "direct_file_request", "playback_request", "preflight_plan", "deterministic_plan",
        "explicit_domain", "explicit_web_search_request", "current_external_question",
        "historical_camera_question", "historical_camera_window", "front_door_presence_question",
        "activity_question", "routing_aliases", "is_confirmation", "conversation_context",
        "turn_context", "resolved_followup_text", "is_repair_turn", "repair_route_text",
        "contextual_entity_resolution", "weather_location_from_text", "artist_from_speech",
        "social_acknowledgement", "retained_media_status_repair", "DOMAIN_ENTITIES", "ARTIST_ALIASES",
        "_descriptive_media_clue", "web_search_query_from_text",
        "library_category_followup", "referential_media_library_question", "referential_media_request",
        "collective_library_query", "referential_web_query", "storage_state_followup",
        "_WEB_QUERY_LEADING_SCAFFOLDING", "_WEB_QUERY_TRAILING_FILLER", "_WEB_QUERY_NESTED_SCAFFOLDING",
        "CONTAINER_DISPLAY_NAMES",
    }
    body = []
    for node in tree.body:
        targets = getattr(node, "targets", [])
        assigned = any(getattr(t, "id", None) in names for t in targets)
        if getattr(node, "name", None) in names or assigned:
            body.append(node)
    re_module = __import__("re")
    namespace = {
        "re": re_module,
        "time": __import__("time"),
        "json": json,
        "has_referential_language": lambda text: bool(re_module.search(
            r"\b(?:it|that|this|them|those|the\s+(?:one|other\s+one))\b", text, re_module.I
        )),
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def run_matrix() -> dict:
    r = load_router()
    movie_forms = [
        "Get me Dumb and Dumber from 1994.",
        "Give me Dumb and Dumber from 1994.",
        "Grab me Dumb and Dumber from 1994.",
        "Can you add Dumb and Dumber from 1994?",
        "Put Dumb and Dumber from 1994 on Plex.",
        "I want Dumb and Dumber from 1994.",
        "Find Dumb and Dumber from 1994 for me.",
    ]
    filler = ["uh ", "can you ", "please ", "Can you get a, can you request the movie "]
    cases = []
    for prefix in filler:
        for form in movie_forms:
            text = prefix + form
            plan = r["preflight_plan"](text)
            cases.append({"text": text, "plan": plan, "domain": r["explicit_domain"](text)})
    direct = [
        "Send me the Dumb and Dumber movie file here.",
        "Upload Dumb and Dumber into this chat.",
        "Play Dumb and Dumber.",
    ]
    for text in direct:
        cases.append({"text": text, "plan": r["preflight_plan"](text), "domain": r["explicit_domain"](text)})
    confirmations = ["yes", "yeah", "yep", "go for it", "yeah go for it", "do it", "please do", "okay", "I confirm", "no", "cancel", "maybe"]
    confirmation_results = {text: r["is_confirmation"](text) for text in confirmations}
    failures = []
    for case in cases[: len(filler) * len(movie_forms)]:
        if case["plan"] != [("media_plan_goal", {"goal": case["text"]})] or case["domain"] != "media":
            failures.append({"kind": "media_metamorphic", **case})
    for case in cases[-len(direct):]:
        if case["plan"]:
            failures.append({"kind": "direct_file_or_playback", **case})
    positive = {"yes", "yeah", "yep", "go for it", "yeah go for it", "do it", "please do", "okay", "I confirm"}
    for text, result in confirmation_results.items():
        if (text in positive) != result:
            failures.append({"kind": "confirmation", "text": text, "result": result})
    return {"scenario_count": len(cases), "confirmation_count": len(confirmations), "failures": failures, "ok": not failures}


if __name__ == "__main__":
    print(json.dumps(run_matrix(), indent=2))
