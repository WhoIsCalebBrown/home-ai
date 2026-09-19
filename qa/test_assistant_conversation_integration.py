"""Production-shaped Assistant conversation integration tests.

Unlike assistant/test_subject_model.py (unit tests on the model classes in
isolation) and assistant/test_pending_offer_integration.py (ast-sliced pure
functions), this module imports and drives the REAL
assistant/voice-api-app.py `respond()` coroutine -- the actual session/
conversation code path a live WebSocket turn runs through, including real
`sessions`, `pending`, `pending_offers`, `conversation_context` module state
and the real `turn_context`/`explicit_domain`/`discover_tools`/`invoke_tool`/
`stage_media_offer`/`is_confirmation` control flow inside `respond()`.

This requires the module's actual runtime dependency stack (fastapi, wyoming,
nemo_text_processing/pynini) to import cleanly, which is why this file only
runs inside qa/Dockerfile.assistant_integration -- see that file and the
final report for why the plain host venv could not run this.

Since merging main's "dispatch bounded camera plans deterministically" work
(preflight_plan() now runs live in respond(), where it used to be dead code),
`preflight_plan()` deterministically routes a much broader set of
media-shaped phrasing than before -- e.g. "check Plex for X by ARTIST" can
land on investigate_media_pipeline instead of media_plan_goal depending on
exact wording. Several turns below intentionally use phrasing verified (via
a direct preflight_plan() call, not guesswork) to return an EMPTY plan, so
the turn actually reaches Qwen/discover_tools/the scripted ollama_script
instead of being intercepted deterministically -- if you change a turn's
wording, re-check preflight_plan(text, {}) directly before assuming it will
still reach the fake Ollama script.

Only the network boundary is faked:
  - `discover_tools`     -> replaced with a fake bounded-discovery function
                            returning canned schemas/candidates (never all
                            68 tools -- the fake enforces the same <=5 bound
                            the real Tools /discover endpoint does).
  - `invoke_tool`        -> replaced with FakeToolsBackend, an in-memory
                            stand-in for Home-AI-Tools that returns
                            realistic payload shapes (canonical_identity,
                            current_state, confirmation_required,
                            confirmation_record) matching
                            tools/server-tools-app.py's real contracts, and
                            that NEVER performs a real write -- writes are
                            recorded, not executed (see FakeToolsBackend
                            .submitted_writes).
  - Ollama's raw `httpx.AsyncClient(...).post(f"{OLLAMA}/api/chat", ...)`
                            call inside respond() -> the module's own
                            `httpx` name is rebound to a fake httpx-shaped
                            object for the duration of each test, restored
                            in a fixture teardown. This does not touch the
                            real httpx package or affect any other module.
  - `speak`/`prepare_tts_text` -> `speak` replaced with a no-op recorder
                            (no real TTS synthesis network call).

Everything else -- turn_context, explicit_domain, is_confirmation,
classify_offer_reply, stage_media_offer, available_actions,
contextual_entity_resolution, resolved_followup_text, the confirmation
`pending[...]` machinery, the new `pending_offers[...]` machinery -- is the
real, unmodified production code.
"""

import asyncio
import contextlib
import importlib.util
import json
import re
import sys
import time
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import pytest

APP_PATH = Path("/app/voice-api-app.py")


def test_home_followup_plan_keeps_exact_result_set_and_exclusion(app):
    context = {
        "referent_type": "home_entities",
        "home_result_set": [
            {"entity_id": "light.office", "name": "Office Light", "area": "Office"},
            {"entity_id": "light.hall", "name": "Hallway Light", "area": "Hallway"},
            {"entity_id": "switch.office", "name": "Office Plug", "area": "Office"},
        ],
    }
    assert app._home_followup_plan("Which of those are lights?", context) == [
        ("home_get_state", {"entity_ids": ["light.office", "light.hall"]})
    ]
    assert app._home_followup_plan("Turn those off except the hallway.", context) == [
        ("home_control", {"entity_ids": ["light.office", "switch.office"], "action": "turn_off"})
    ]


def test_home_followup_wrong_room_stays_explicitly_scoped(app):
    context = {
        "referent_type": "home_entities",
        "home_result_set": [{"entity_id": "light.office", "name": "Office Light", "area": "Office"}],
        "home_result_query": {"entity_or_area": "on"},
    }
    assert app._home_followup_plan("What about upstairs?", context) == [
        ("home_get_state", {"scope": "upstairs", "state": "on"})
    ]


def test_home_control_repairs_model_device_id_slug(app):
    assert app.normalize_home_tool_arguments(
        "home_control",
        {"device_id": "office_lights", "action": "turn_on"},
        "turn on the office lights",
    ) == {"entity_or_area": "office lights", "action": "turn_on"}


def test_home_followup_explicit_topic_switch_wins(app):
    context = {
        "referent_type": "home_entities",
        "home_result_set": [{"entity_id": "light.office", "name": "Office Light", "area": "Office"}],
    }
    assert app._home_followup_plan("What about Plex?", context) == []
    assert app._home_followup_plan("Why didn't Bedroom Lamp respond?", context) == []


def test_home_followup_brightness_and_warm_white_bind_exact_set(app):
    context = {
        "referent_type": "home_entities",
        "home_result_set": [
            {"entity_id": "light.office_a", "name": "Office A", "area": "Office"},
            {"entity_id": "light.office_b", "name": "Office B", "area": "Office"},
        ],
    }
    assert app._home_followup_plan("Make them a bit dimmer.", context) == [
        ("home_control", {"entity_ids": ["light.office_a", "light.office_b"],
                          "action": "adjust_brightness", "parameters": {"brightness_delta_pct": -10}})
    ]
    assert app._home_followup_plan("Make them warm white.", context) == [
        ("home_control", {"entity_ids": ["light.office_a", "light.office_b"],
                          "action": "set_color_temperature", "parameters": {"color_temp_kelvin": 2700}})
    ]
    assert app._home_followup_plan("Can those lights be dimmed?", context) == [
        ("home_get_state", {"entity_ids": ["light.office_a", "light.office_b"]})
    ]


@pytest.mark.parametrize(("text", "expected"), [
    ("Are any lights still on?", ("home_get_state", {"domain": "lights", "state": "on"})),
    ("Which outlets are off?", ("home_get_state", {"domain": "outlets", "state": "off"})),
    ("Is everything off?", ("home_get_state", {"state": "off", "aggregate_check": "off"})),
    ("How many lights do I have?", ("home_find_device", {"query": "lights"})),
    ("Which lights can change colour?", ("home_find_device", {"query": "lights"})),
    ("Can Light Fixture 1 be dimmed?", ("home_find_device", {"query": "light fixture 1"})),
    ("When did Light Fixture 1 turn on?", ("home_get_activity", {"entity_or_area": "light fixture 1", "hours": 168})),
    ("Why didn't Bedroom Lamp respond?", ("home_get_activity", {"entity_or_area": "bedroom lamp", "hours": 168})),
])
def test_common_home_questions_are_deterministic_local_reads(app, text, expected):
    assert app.preflight_plan(text, {}) == [expected]


def test_home_tool_result_records_exact_conversational_referent(app):
    app.conversation_context["home-context"] = {}
    app.record_tool_referent("home-context", "home_get_state", {"state": "on"}, {
        "status": "ok", "result": {"devices": [
            {"entity_id": "light.office", "name": "Office", "area": "Office"},
            {"entity_id": "light.hall", "name": "Hall", "area": "Hallway"},
        ]},
    })
    context = app.conversation_context["home-context"]
    assert context["referent_type"] == "home_entities"
    assert context["referent_ids"] == ["light.office", "light.hall"]


def test_home_clarification_reply_binds_one_candidate_and_original_operation(app):
    app.conversation_context["home-clarify"] = {
        "pending_home_candidates": [
            {"entity_id": "light.office", "name": "Office Light", "aliases": []},
            {"entity_id": "light.bed", "name": "Bedroom Lamp", "aliases": []},
        ],
        "pending_home_operation": "home_get_activity",
    }
    context = app.turn_context("home-clarify", "Bedroom Lamp")
    assert context["referent_ids"] == ["light.bed"]
    assert app.preflight_plan("Bedroom Lamp", context) == [
        ("home_get_activity", {"entity_ids": ["light.bed"], "hours": 168})
    ]
if not APP_PATH.exists():
    APP_PATH = Path(__file__).resolve().parents[1] / "assistant" / "voice-api-app.py"

sys.path.insert(0, str(APP_PATH.parent))


def _load_app():
    spec = importlib.util.spec_from_file_location("voice_api_app_integration", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Mirrors tools/server-tools-app.py's _media_title_candidate_words /
# assistant/voice-api-app.py's copy of the same classifier (separate
# processes each carry their own copy; this fake mirrors the shape rather
# than importing production code, consistent with the rest of this file's
# "mirror the contract" philosophy).
_TITLELESS_CATEGORY_WORDS = {"movie", "movies", "film", "films", "show", "shows", "series", "tv",
                             "episode", "episodes", "season", "seasons", "album", "albums", "music",
                             "song", "songs", "track", "tracks", "library", "libraries", "anime"}
_TITLELESS_SCAFFOLDING = {"do", "does", "did", "i", "have", "has", "any", "some", "what",
                          "what's", "whats", "which", "how", "many", "show", "me", "my", "in",
                          "on", "is", "are", "of", "the", "a", "an", "to", "for", "your",
                          "server", "plex", "got", "get", "give", "grab", "find", "add", "mean",
                          "request", "want", "put", "can", "could", "would", "you", "please",
                          "there"}


def _looks_titleless(text: str) -> bool:
    tokens = re.findall(r"[a-z0-9']+", text.casefold())
    return not [t for t in tokens if t not in _TITLELESS_SCAFFOLDING and t not in _TITLELESS_CATEGORY_WORDS]


# --- Fake Home-AI-Tools backend --------------------------------------------

class FakeToolsBackend:
    """In-memory stand-in for Home-AI-Tools, keyed by canonical identity.

    Mirrors the real contracts documented in tools/server-tools-app.py:
    media_plan_goal's canonical_identity/current_state/confirmation_required/
    confirmation_record shape, and media_standard_request's single-use
    confirmation consumption. Never performs a real write -- `submit_write`
    only appends to `self.submitted_writes` and returns a fabricated success
    payload, exactly the "fake write boundary" the task requires.
    """

    def __init__(self):
        self.library: dict[str, dict] = {}  # canonical_id -> {"state": ..., "identity": {...}}
        self.web_index: dict[str, dict] = {}  # lowercase title -> {"media_type", "canonical_identity"}
        self.workflows: dict[str, dict] = {}  # workflow_id -> identity, populated by media_plan_goal (matches real _media_workflows())
        self.submitted_writes: list[dict] = []
        self.consumed_confirmations: set[str] = set()
        self.call_log: list[tuple[str, dict]] = []
        self.media_execution_calls: list[dict] = []
        self.planner_confirmations: list[dict] = []
        self.web_search_fixtures: list[list[dict] | None] = []
        self.web_fetch_failures: set[str] = set()
        self.web_fetch_final_urls: dict[str, str] = {}
        self.web_fetch_contents: dict[str, str] = {}
        self.web_fetch_metadata: dict[str, dict] = {}
        self.simulate_drift_for: str | None = None
        self.drift_candidate_title: str = ""
        self.drift_candidate_year: str | None = None
        self.media_standard_request_override: dict | None = None
        self.person_index: dict[str, str] = {}  # casefold(person name) -> title, mirrors the real web-discovery fallback's person-hint -> title resolution (tools/server-tools-app.py's _web_discover_title), tested there directly against real functions -- this fake only proves the ASSISTANT-side handoff after identity resolves, not the discovery mechanism itself.
        self.home_entities = {
            "light.office": {"entity_id": "light.office", "name": "Office Light", "area": "Office", "state": "on", "supported_color_modes": ["hs", "color_temp"], "brightness_pct": 60},
            "light.hall": {"entity_id": "light.hall", "name": "Hallway Light", "area": "Hallway", "state": "on", "supported_color_modes": ["hs", "color_temp"], "brightness_pct": 80},
            "switch.router": {"entity_id": "switch.router", "name": "Router", "area": "Office", "state": "on", "supported_color_modes": []},
            "light.bed": {"entity_id": "light.bed", "name": "Bedroom Light", "area": "Bedroom", "state": "unavailable", "supported_color_modes": ["hs"]},
        }

    def seed_person(self, person: str, title: str) -> None:
        self.person_index[person.casefold()] = title

    def seed_library(self, title: str, *, media_type: str, state: str, **identity_fields):
        canonical_id = identity_fields.get("tmdb_id") or identity_fields.get("tvdb_id") or identity_fields.get("foreign_album_id") or title
        self.library[str(canonical_id)] = {
            "state": state,
            "identity": {"media_type": media_type, "title": title, **identity_fields},
        }

    def seed_web(self, title: str, *, media_type: str, **identity_fields):
        self.web_index.setdefault(title.casefold(), [])
        self.web_index[title.casefold()].append({"media_type": media_type, "canonical_identity": {"media_type": media_type, "title": title, **identity_fields}})

    def _find_identity(self, title: str) -> dict | list[dict] | None:
        """Returns a single identity dict, a list (ambiguous across
        media types/entries), or None. Real media_plan_goal returns
        ambiguous=True with candidates when Radarr/Sonarr/Lidarr disagree
        about what a title refers to -- this mirrors that at the fake
        boundary rather than arbitrarily picking one."""
        library_hits = [e["identity"] for e in self.library.values() if e["identity"].get("title", "").casefold() == title.casefold()]
        web_hits = [e["canonical_identity"] for e in self.web_index.get(title.casefold(), [])]
        hits = library_hits or web_hits
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        return hits

    async def invoke(self, name: str, arguments: dict, client_id: str, request_id: str, confirmed: bool = False, action_id: str | None = None) -> dict:
        self.call_log.append((name, dict(arguments)))
        if name in {"home_get_state", "home_get_area_state", "home_find_device"}:
            devices = list(self.home_entities.values())
            exact_ids = arguments.get("entity_ids")
            if isinstance(exact_ids, list):
                devices = [self.home_entities[value] for value in exact_ids if value in self.home_entities]
            state = arguments.get("state") or (arguments.get("entity_or_area") if arguments.get("entity_or_area") in {"on", "off", "available", "unavailable"} else None)
            if state == "available":
                devices = [item for item in devices if item["state"] not in {"unavailable", "unknown"}]
            elif state:
                devices = [item for item in devices if item["state"] == state]
            domain = str(arguments.get("domain") or "").rstrip("s")
            if domain in {"outlet", "plug", "switch"}:
                domain = "switch"
            elif domain in {"lamp", "light"}:
                domain = "light"
            if domain:
                devices = [item for item in devices if item["entity_id"].startswith(domain + ".")]
            area = arguments.get("area") if name != "home_get_area_state" else arguments.get("area")
            if area:
                devices = [item for item in devices if item.get("area", "").casefold() == str(area).casefold()]
            return {"tool": name, "status": "ok", "result": {"status": "ok", "devices": [dict(item) for item in devices], "count": len(devices), "query_state": state}}
        if name == "home_control":
            exact_ids = arguments.get("entity_ids")
            targets = list(exact_ids) if isinstance(exact_ids, list) else list(self.home_entities)
            protected = []
            bulk_target = len(targets) > 1 or arguments.get("entity_or_area") in {
                "everything", "all devices", "all home devices", "all switches", "all outlets"
            }
            if bulk_target:
                protected = [dict(self.home_entities[value]) for value in targets
                             if value in self.home_entities and value.startswith("switch.")]
                targets = [value for value in targets if not value.startswith("switch.")]
            action = arguments.get("action")
            for entity_id in targets:
                if entity_id not in self.home_entities or self.home_entities[entity_id]["state"] == "unavailable":
                    continue
                if action == "turn_off":
                    self.home_entities[entity_id]["state"] = "off"
                elif action == "turn_on":
                    self.home_entities[entity_id]["state"] = "on"
                elif action == "set_brightness":
                    self.home_entities[entity_id]["brightness_pct"] = arguments.get("parameters", {}).get("brightness_pct")
            self.submitted_writes.append({"name": name, "arguments": dict(arguments)})
            payload = {"status": "partial" if protected else "executed",
                       "outcome": "partial_action_verified" if protected else "home_assistant_reported_target_state",
                       "verified": True, "protected": protected, "target_entity_ids": targets}
            return {"tool": name, "status": "ok", "result": payload}
        if name == "web_search":
            if self.web_search_fixtures:
                fixture = self.web_search_fixtures.pop(0)
                if fixture is None:
                    return {"tool": name, "status": "error", "result": {"error": "fixture search failure"}}
                return {"tool": name, "status": "ok", "result": {
                    "results": [dict(item) for item in fixture],
                }}
            query = str(arguments.get("query", ""))
            entries = None
            for title, hits in self.web_index.items():
                if title in query.casefold():
                    entries = hits
                    break
            if not entries:
                return {"tool": name, "status": "ok", "result": {"results": []}}
            return {"tool": name, "status": "ok", "result": {
                "results": [{"title": e["canonical_identity"]["title"], "url": "https://example.invalid/x",
                             "snippet": f"{e['canonical_identity']['title']} is a {e['media_type']}."} for e in entries],
            }}
        if name == "web_fetch":
            url = str(arguments.get("url", ""))
            if url in self.web_fetch_failures:
                return {"tool": name, "status": "error", "result": {
                    "url": url,
                    "error": "fixture fetch failure",
                }}
            final_url = self.web_fetch_final_urls.get(url, url)
            return {"tool": name, "status": "ok", "result": {
                "url": final_url,
                "content": self.web_fetch_contents.get(url, f"Fixture article body for {final_url}."),
                **self.web_fetch_metadata.get(url, {}),
            }}
        if name == "media_plan_goal":
            goal = str(arguments.get("goal", ""))
            if _looks_titleless(goal):
                # Simulates the real tools/server-tools-app.py NO_TITLE_GIVEN
                # shape (media_plan_goal short-circuits before touching any
                # provider when the goal has no title-shaped content) -- this
                # conversation-level test proves the ASSISTANT correctly
                # stages/consumes the pending-clarification mechanism around
                # that shape, not that this fake reimplements the real
                # _media_title_candidate_words classifier itself (that is
                # unit/integration-tested directly against the real function
                # in tools/test_media_plan_goal_ambiguity_and_no_title.py).
                return {"tool": name, "status": "ok", "result": {
                    "canonical_identity": None, "current_state": "NO_TITLE_GIVEN", "ambiguous": False,
                    "confirmation_required": False, "goal": {"title_query": ""},
                    "message": "I didn't catch a specific title -- what would you like me to look for?",
                }}
            if self.simulate_drift_for and self.simulate_drift_for.casefold() in goal.casefold():
                # Simulates the real tools/server-tools-app.py query-drift
                # guardrail's output shape for a search that resolved a
                # single, low-similarity candidate -- this conversation-level
                # test proves the ASSISTANT correctly turns that into a
                # disambiguation prompt, not that this fake reimplements the
                # real difflib-based drift detection itself (that is
                # unit/integration-tested directly against the real function
                # in tools/test_query_drift_guardrail.py).
                return {"tool": name, "status": "ok", "result": {
                    "canonical_identity": None, "current_state": "AMBIGUOUS_IDENTITY", "ambiguous": True,
                    "confirmation_required": False, "ambiguity_reason": "QUERY_DRIFT",
                    "candidates": [{"title": self.drift_candidate_title, "year": self.drift_candidate_year, "media_type": "movie"}],
                }}
            title = goal
            for person, mapped_title in self.person_index.items():
                if person in goal.casefold():
                    title = mapped_title
                    break
            else:
                all_known_titles = (
                    [e["identity"].get("title") for e in self.library.values()]
                    + [hit["canonical_identity"].get("title") for hits in self.web_index.values() for hit in hits]
                )
                for candidate_title in all_known_titles:
                    if candidate_title and candidate_title.casefold() in goal.casefold():
                        title = candidate_title
                        break
            identity = self._find_identity(title)
            if identity is None:
                return {"tool": name, "status": "ok", "result": {"canonical_identity": None, "current_state": "NOT_FOUND", "ambiguous": False, "confirmation_required": False}}
            if isinstance(identity, list):
                # Mirrors real media_plan_goal's requested_year narrowing: if
                # the (disambiguation-resolved) goal string already embeds a
                # year or media-type word that narrows to exactly one
                # candidate, resolve directly instead of re-reporting
                # ambiguous -- this is what lets the assistant's disambiguation
                # follow-up ("the new one" -> re-issues goal with the year
                # appended) actually converge.
                year_match = re.search(r"\b(19|20)\d{2}\b", goal)
                narrowed = identity
                if year_match:
                    narrowed = [c for c in narrowed if str(c.get("year")) == year_match.group(0)] or narrowed
                for word, media_type in {"movie": "movie", "album": "album", "show": "tv", "anime": "anime"}.items():
                    if re.search(rf"\b{word}\b", goal, re.I):
                        narrowed = [c for c in narrowed if str(c.get("media_type", "")).casefold() == media_type] or narrowed
                if len(narrowed) == 1:
                    identity = narrowed[0]
                else:
                    return {"tool": name, "status": "ok", "result": {
                        "canonical_identity": None, "current_state": "AMBIGUOUS_IDENTITY", "ambiguous": True,
                        "confirmation_required": False,
                        "candidates": [{"title": c.get("title"), "year": c.get("year"), "media_type": c.get("media_type")} for c in identity],
                    }}
            canonical_id = identity.get("tmdb_id") or identity.get("tvdb_id") or identity.get("foreign_album_id") or identity.get("title")
            entry = self.library.get(str(canonical_id))
            state = entry["state"] if entry else "IDENTIFIED"
            workflow_id = f"wf-{canonical_id}"
            self.workflows[workflow_id] = identity
            # Mirrors the real media_plan_goal: confirmation_required is only
            # set when the parsed goal action is "ensure_available" (a
            # request-shaped ask), never merely because the item is
            # identified/absent -- "do I have it?" must not itself produce a
            # write confirmation.
            is_request_shaped = bool(re.search(r"\b(get|give|grab|find|add|request|want|put|download|acquire)\b", goal, re.I))
            confirmation_required = state != "AVAILABLE_IN_PLEX" and state != "NOT_FOUND" and is_request_shaped
            result = {"canonical_identity": identity, "current_state": state, "ambiguous": False,
                      "confirmation_required": confirmation_required, "workflow_id": workflow_id,
                      "writes_required": bool(confirmation_required)}
            if confirmation_required:
                confirmation_id = str(uuid.uuid4())
                media_type = identity.get("media_type")
                # Real media_plan_goal: standard mode (movie/tv/anime) bridges
                # to cli_debrid; everything else (album, today) builds a
                # confirmation naming "<owner>.media_execute_goal" -- a tool
                # name that has NO implementation anywhere in
                # tools/server-tools-app.py's REGISTRY. Accepting that
                # confirmation later is a structural dead end (see
                # test_music_write_path_is_structurally_dead_end below), not
                # a policy flag this fake or any caller can flip.
                is_standard = media_type in {"movie", "tv", "anime"}
                operation = "cli_debrid.media_standard_request" if is_standard else "lidarr.media_execute_goal"
                result["confirmation_record"] = {
                    "workflow_id": workflow_id, "confirmation_id": confirmation_id,
                    "plan_version_hash": f"hash-{confirmation_id}",
                    "arguments": {"workflow_id": workflow_id, "canonical_external_id": canonical_id,
                                  "media_type": media_type, "season_scope": []},
                    "operation": operation, "canonical_external_id": canonical_id,
                    "canonical_media_type": media_type, "title": identity.get("title"),
                    "session_id": client_id, "status": "PENDING",
                    "arguments_hash": f"arguments-{confirmation_id}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
                    "manager": operation.split(".", 1)[0],
                }
                self.planner_confirmations.append(result["confirmation_record"])
            return {"tool": name, "status": "ok", "result": result}
        if name == "media_execute_goal":
            # Real /invoke: TOOLS.get("media_execute_goal") is None -> HTTP
            # 404 -> assistant's invoke_tool returns this exact shape. There
            # is no fixture to provide here because there is nothing to
            # fake: the real tool does not exist.
            return {"tool": name, "status": "error", "result": {"error": "That tool is not enabled."}}
        if name == "plex_artist_library":
            query = str(arguments.get("query", ""))
            albums = []
            artist = query
            for entry in self.library.values():
                candidate = entry["identity"]
                if candidate.get("media_type") != "album":
                    continue
                candidate_artist = str(candidate.get("artist") or "")
                if query.casefold() in candidate_artist.casefold():
                    artist = candidate_artist
                    albums.append({"title": candidate.get("title"), "year": candidate.get("year")})
            return {"tool": name, "status": "ok", "result": {
                "found": bool(albums), "query": query, "artist": artist, "albums": albums,
            }}
        if name in {"plex_search", "plex_library_lookup"}:
            query = str(arguments.get("query", ""))
            matches = []
            for entry in self.library.values():
                candidate = entry["identity"]
                if query.casefold() in str(candidate.get("title") or "").casefold():
                    library_name = "Movies" if candidate.get("media_type") == "movie" else "TV Shows"
                    matches.append({**candidate, "library_title": library_name, "library": library_name})
            if not matches:
                found = self._find_identity(query)
                if isinstance(found, dict):
                    matches = [found]
            return {"tool": name, "status": "ok", "result": {"matched": bool(matches), "available": bool(matches), "matches": matches, "library_title": "Movies"}}
        if name == "media_status" or name == "plex_match_canonical_media":
            workflow_id_arg = str(arguments.get("workflow_id") or "").strip()
            title = arguments.get("title") or arguments.get("query") or ""
            identity = self.workflows.get(workflow_id_arg) if workflow_id_arg else None
            if identity is None:
                identity = self._find_identity(str(title))
            if identity is None:
                return {"tool": name, "status": "ok", "result": {"matched": False, "current_state": "NOT_FOUND"}}
            if isinstance(identity, list):
                return {"tool": name, "status": "ok", "result": {"matched": False, "current_state": "AMBIGUOUS_IDENTITY",
                                                                    "candidates": [{"title": c.get("title"), "media_type": c.get("media_type")} for c in identity]}}
            canonical_id = identity.get("tmdb_id") or identity.get("tvdb_id") or identity.get("foreign_album_id") or identity.get("title")
            entry = self.library.get(str(canonical_id))
            state = entry["state"] if entry else "ABSENT"
            return {"tool": name, "status": "ok", "result": {"matched": state not in {"ABSENT", "NOT_FOUND"}, "current_state": state, "canonical_identity": identity}}
        if name == "media_standard_request":
            self.media_execution_calls.append({"arguments": dict(arguments), "confirmed": confirmed,
                                               "action_id": action_id, "session_id": client_id})
            workflow_id = arguments.get("workflow_id")
            confirmation_context = arguments.get("confirmation_context") or {}
            confirmation_id = confirmation_context.get("confirmation_id")
            if self.media_standard_request_override is not None:
                if confirmation_id:
                    self.consumed_confirmations.add(confirmation_id)
                return {"tool": name, "status": "ok", "result": dict(self.media_standard_request_override)}
            if confirmation_id in self.consumed_confirmations:
                return {"tool": name, "status": "ok", "result": {"status": "rejected", "reason": "CONFIRMATION_ALREADY_CONSUMED", "write_executed": False}}
            if confirmation_id:
                self.consumed_confirmations.add(confirmation_id)
            if any(write.get("workflow_id") == workflow_id for write in self.submitted_writes):
                return {"tool": name, "status": "ok", "result": {"status": "no_op", "reason": "STANDARD_WORKFLOW_ALREADY_ACTIVE_OR_SATISFIED", "write_executed": False}}
            self.submitted_writes.append({"workflow_id": workflow_id, "arguments": dict(arguments)})
            return {"tool": name, "status": "ok", "result": {"status": "submitted", "write_executed": True, "ingestion_confirmed": True, "workflow_id": workflow_id}}
        if name == "get_storage_status":
            # Reproduces the exact irrelevant-tool-result shape from the
            # real production transcript this test module's
            # test_real_production_transcript_replay is built from --
            # deliberately fixture-shaped as ok/relevant-looking data so the
            # relevance gate (not a fake-data quirk) is what has to keep it
            # out of a media-identification answer.
            return {"tool": name, "status": "ok", "result": {"user_free_bytes": 15300000000000, "cache_free_bytes": 179100000000}}
        if name == "frigate_snapshot":
            return {"tool": name, "status": "ok", "result": {"camera": arguments.get("camera", "front_door"), "vision_ready": False, "description": "The porch is empty right now."}}
        if name == "investigate_media_pipeline":
            # Real preflight_plan() now deterministically routes some
            # artist-bearing phrasing ("X by ARTIST") to this cross-service
            # tool rather than media_plan_goal/web_search (found via the
            # main-merge conversation-integration run, not by inspection).
            # grounded_investigation_answer() only special-cases a "utopia"
            # test fixture and returns None otherwise, so a non-"utopia"
            # turn falls through to stream_final's normal synthesis using
            # this result as evidence -- status="ok" here is what matters
            # for all_live_results_failed() to stay False; the exact field
            # shape mirrors the real tool's (plex/lidarr_artist_status/
            # lidarr_albums/slskd/music_enricher/beets/torbox/qbittorrent).
            query = str(arguments.get("query") or arguments.get("focus") or "")
            identity = None
            for hits in self.web_index.values():
                for hit in hits:
                    if hit["canonical_identity"].get("title", "").casefold() in query.casefold():
                        identity = hit["canonical_identity"]
            for entry in self.library.values():
                if entry["identity"].get("title", "").casefold() in query.casefold():
                    identity = entry["identity"]
            return {"tool": name, "status": "ok", "result": {
                "investigation": "music_pipeline", "query": query,
                "sources_checked": ["plex_music", "lidarr", "qbittorrent", "slskd", "torbox", "music_enricher", "beets"],
                "plex": {"matches": [identity] if identity else []},
                "lidarr_artist_status": {"known": bool(identity), "matches": []},
                "lidarr_albums": {"matches": [identity] if identity else []},
                "slskd": {"active_count": 0, "completed_count": 0, "items": []},
                "music_enricher": {"count": 0, "items": []},
                "beets": {"count": 0, "items": []},
                "torbox": {"summary": {"active": 0, "completed": 0, "errored": 0, "pulling": 0}},
            }}
        if name == "list_items":
            return {"tool": name, "status": "ok", "result": {"list": arguments.get("list", "grocery"), "items": [], "count": 0}}
        if name == "get_container_logs":
            return {"tool": name, "status": "ok", "result": {"name": arguments.get("name"), "lines": "\x02\x00\x00\x00\x00\x00\x00%INFO:     Started server process [1]\n"}}
        if name == "investigate_downloads":
            return {"tool": name, "status": "ok", "result": {"investigation": "downloads", "sources_checked": ["qbittorrent"], "sources": {"qbittorrent": {"torrent_count": 269, "active_count": 0}}}}
        if name == "unraid_container_status":
            return {"tool": name, "status": "ok", "result": {
                "name": arguments.get("container"), "state": "running",
                "memory_display": "512 MB", "cpu_percent": 2.5,
            }}
        if name == "unraid_storage_status":
            return {"tool": name, "status": "ok", "result": {"target": arguments.get("target"), "status": "ONLINE", "used_percent": 57.0, "free_bytes": 212_000_000_000}}
        if name == "plex_library_counts":
            return {"tool": name, "status": "ok", "result": {"libraries": [
                {"library": "Movies", "type": "movie", "items": 123},
                {"library": "Anime", "type": "show", "items": 45},
                {"library": "TV Shows", "type": "show", "items": 22},
            ]}}
        return {"tool": name, "status": "error", "result": {"error": f"FakeToolsBackend has no fixture for tool {name!r}"}}


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    def text_messages(self) -> list[str]:
        return [m["text"] for m in self.sent if m.get("type") == "text"]

    def last_text(self) -> str:
        texts = self.text_messages()
        return texts[-1] if texts else ""


class _FakeOllamaResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeStreamResponse:
    def __init__(self, text: str):
        self._text = text

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield json.dumps({"message": {"content": self._text}, "done": True})


class _FakeStreamContext:
    def __init__(self, text: str):
        self._text = text

    async def __aenter__(self):
        return _FakeStreamResponse(self._text)

    async def __aexit__(self, *exc):
        return False


class _FakeOllamaClient:
    """Fakes the two shapes respond()/stream_final/generate_final use:
    non-streaming `.post()` (tool-dispatch rounds and generate_final) served
    from `script`, and the streaming `.stream()` context manager (final
    synthesis) which always yields exactly `final_text`."""

    def __init__(self, script: list[dict], final_text: str, owner: "_FakeHttpxModule | None" = None):
        self._script = script
        self._final_text = final_text
        self._owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kwargs):
        if "/api/chat" in url:
            message = self._script.pop(0) if self._script else {"message": {"content": "", "tool_calls": []}}
            return _FakeOllamaResponse(message)
        raise RuntimeError(f"OllamaFake received unexpected POST {url}")

    def stream(self, method, url, json=None, **kwargs):
        if "/api/chat" in url:
            # Record the exact payload (in particular its `messages`) the
            # real model would have been shown for final synthesis -- this
            # fake always returns _final_text regardless of that content,
            # so asserting on _final_text alone (a test-authored fixture
            # string) never actually exercises the relevance gate. Tests
            # that need to prove what evidence reached synthesis must
            # inspect last_stream_payload instead of only the returned text.
            if self._owner is not None:
                self._owner.last_stream_payload = json
            return _FakeStreamContext(self._final_text)
        raise RuntimeError(f"OllamaFake received unexpected stream {method} {url}")


class _FakeHttpxModule:
    """Rebinds only voice_api_app's own `httpx` name -- see module docstring."""

    def __init__(self, script: list[dict], final_text: str = ""):
        self._script = script
        self._final_text = final_text
        self.last_stream_payload: dict | None = None

    def AsyncClient(self, *args, **kwargs):
        return _FakeOllamaClient(self._script, self._final_text, owner=self)


@pytest.fixture
def app():
    module = _load_app()
    module.speak = _noop_speak
    yield module


async def _noop_speak(ws, request_id, text, prepared=False):
    return None


@pytest.fixture
def backend():
    return FakeToolsBackend()


@pytest.fixture
def session(app, backend):
    """One isolated conversation session: a client_id plus a driver that
    wires the fakes in for exactly the duration of each turn, so state in
    `app.sessions`/`app.pending`/`app.pending_offers`/`app.conversation_context`
    persists across turns within a test the same way it does across
    WebSocket messages in production, but different tests never share it."""

    client_id = f"test-{uuid.uuid4()}"
    ws = FakeWebSocket()

    async def invoke_tool_fake(name, arguments, cid, rid, confirmed=False, action_id=None):
        return await backend.invoke(name, arguments, cid, rid, confirmed=confirmed, action_id=action_id)

    async def discover_tools_fake(user_text, context):
        # Bounded, never the full registry -- mirrors the real <=5 cap.
        catalog = {
            "media_plan_goal": {"type": "function", "function": {"name": "media_plan_goal", "description": "plan media", "parameters": {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]}}},
            "media_status": {"type": "function", "function": {"name": "media_status", "description": "media status", "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": []}}},
            "web_search": {"type": "function", "function": {"name": "web_search", "description": "search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            "weather_forecast": {"type": "function", "function": {"name": "weather_forecast", "description": "weather", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": []}}},
        }
        selected = list(catalog.values())[:5]
        candidates = [{"metadata": {"canonical_name": k, "score": 1.0}} for k in catalog]
        return [item["function"] for item in selected], candidates, 1.0

    class Driver:
        def __init__(self):
            self.client_id = client_id
            self.ws = ws
            self.backend = backend
            self.app = app

        async def turn_for(self, turn_client_id: str, user_text: str, ollama_script: list[dict] | None = None, final_text: str = "") -> str:
            """Run one respond() turn with the fakes wired in, return the
            final spoken/text answer for convenience.

            `ollama_script` scripts non-streaming dispatch-round responses
            (each entry consumed by one tool-dispatch POST). `final_text` is
            what the streaming final-synthesis call (stream_final) always
            yields when a turn falls through to generic Qwen synthesis
            rather than being answered by a deterministic branch."""
            request_id = str(uuid.uuid4())
            app.invoke_tool = invoke_tool_fake
            app.discover_tools = discover_tools_fake
            httpx_fake = _FakeHttpxModule(ollama_script if ollama_script is not None else [{"message": {"content": "", "tool_calls": []}}], final_text)
            app.httpx = httpx_fake
            self.last_stream_payload = None
            before = len(ws.sent)
            await app.respond(ws, turn_client_id, request_id, user_text)
            self.last_stream_payload = httpx_fake.last_stream_payload
            return "\n".join(m["text"] for m in ws.sent[before:] if m.get("type") == "text")

        async def turn(self, user_text: str, ollama_script: list[dict] | None = None, final_text: str = "") -> str:
            return await self.turn_for(self.client_id, user_text, ollama_script, final_text)

    return Driver()


# --- Isolated conversational-home execution (all writes stay in memory). ---

@pytest.mark.asyncio
async def test_home_result_set_exclusion_control_and_reconciliation(session):
    first = await session.turn("What's on?")
    assert "Office Light" in first and "Hallway Light" in first
    controlled = await session.turn("Turn those off except the hallway.")
    assert "excluded" in controlled.casefold()
    assert session.backend.home_entities["light.office"]["state"] == "off"
    assert session.backend.home_entities["light.hall"]["state"] == "on"
    assert session.backend.home_entities["switch.router"]["state"] == "on"
    checked = await session.turn("Did they all turn off?")
    assert "Office Light: off" in checked
    assert "Hallway Light: on" in checked


@pytest.mark.asyncio
async def test_home_exact_brightness_and_bulk_protected_load(session):
    await session.turn("What's on in the office?")
    await session.turn("Make those lights 30%.")
    assert session.backend.home_entities["light.office"]["brightness_pct"] == 30
    bulk = await session.turn("Turn everything off.", ollama_script=[{"message": {"content": "", "tool_calls": [
        {"function": {"name": "home_control", "arguments": {"entity_or_area": "everything", "action": "turn_off"}}}
    ]}}])
    assert "excluded" in bulk.casefold()
    assert session.backend.home_entities["switch.router"]["state"] == "on"


@pytest.mark.asyncio
async def test_negative_home_followup_never_becomes_a_control(session):
    await session.turn("What's on?")
    before = list(session.backend.submitted_writes)
    reply = await session.turn(
        "Don't turn them off.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "home_control", "arguments": {
                "entity_ids": ["light.office", "light.hall", "switch.router"],
                "action": "turn_off",
            }}},
        ]}}],
    )
    assert "won't" in reply.casefold()
    assert session.backend.submitted_writes == before


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "Please don't ever turn them off.",
    "Should I turn them off?",
    "If I turn them off will that save power?",
])
async def test_non_imperative_home_control_discussion_never_executes(session, utterance):
    await session.turn("What's on?")
    before = list(session.backend.submitted_writes)
    reply = await session.turn(
        utterance,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "home_control", "arguments": {
                "entity_ids": ["light.office", "light.hall", "switch.router"],
                "action": "turn_off",
            }}},
        ]}}],
    )
    assert "haven't changed" in reply.casefold() or "won't" in reply.casefold()
    assert session.backend.submitted_writes == before


@pytest.mark.asyncio
async def test_unresolved_home_exclusion_clarifies_without_control(session):
    await session.turn("What's on?")
    before = list(session.backend.submitted_writes)
    reply = await session.turn(
        "Turn them off except the missing lamp.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "home_control", "arguments": {
                "entity_ids": ["light.office", "light.hall", "switch.router"],
                "action": "turn_off",
            }}},
        ]}}],
    )
    assert "missing lamp" in reply.casefold()
    assert "which" in reply.casefold() or "couldn't match" in reply.casefold()
    assert session.backend.submitted_writes == before


# --- Scenario: discover -> library read -> explicit request -> fake write ---

@pytest.mark.asyncio
async def test_full_discover_library_request_confirmation_conversation(session):
    session.backend.seed_web("Cowboy Bebop", media_type="anime", tmdb_id="30991")

    reply1 = await session.turn(
        "Do you know Cowboy Bebop?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Cowboy Bebop"}}},
        ]}}],
        final_text="Cowboy Bebop is an anime series.",
    )
    assert "cowboy bebop" in reply1.casefold()
    assert session.client_id not in session.app.pending_offers

    subject_before = session.app.conversation_context.get(session.client_id, {}).get("latest_resolved_referent")
    assert subject_before and "cowboy bebop" in subject_before.casefold()

    reply2 = await session.turn(
        "Do I have Cowboy Bebop in Plex?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Cowboy Bebop"}}},
        ]}}],
        final_text="It's not in Plex yet.",
    )
    assert session.client_id not in session.app.pending  # no write confirmation yet
    assert session.client_id not in session.app.pending_offers
    assert not session.backend.submitted_writes

    reply3 = await session.turn(
        "Can you get it?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Cowboy Bebop"}}},
        ]}}],
    )
    assert len(session.backend.submitted_writes) == 1
    assert session.client_id not in session.app.pending

    reply4 = await session.turn("Go for it.")
    assert len(session.backend.submitted_writes) == 1, "exactly one execution"
    assert session.client_id not in session.app.pending  # single-use, consumed

    reply5 = await session.turn("Go for it.")
    assert len(session.backend.submitted_writes) == 1, "a stale/replayed confirmation must never submit twice"


@pytest.mark.asyncio
async def test_disabled_tv_writes_gives_a_clear_reason_not_a_dead_end(session):
    # Real production bug found in a live naive-user sweep: "Can you get
    # me the show Silo" -> confirmed -> "I couldn't hand that off to your
    # media system." This server has TV show requests deliberately turned
    # off (STANDARD_SEASON_WRITES_ENABLED=false) -- a real, nameable
    # limitation -- but the old message gave zero explanation, reading
    # like a broken/opaque failure instead of "shows aren't enabled yet."
    session.backend.seed_library("Silo", media_type="tv", state="ABSENT", tvdb_id="371980")
    session.backend.media_standard_request_override = {"status": "disabled", "reason": "STANDARD_SEASON_WRITES_DISABLED", "write_executed": False}
    reply = await session.turn(
        "Can you get me the show Silo",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get the show Silo", "media_type": "tv"}}},
        ]}}],
    )
    assert session.client_id not in session.app.pending
    assert reply == "TV show requests aren't turned on for me yet -- only movie requests are currently enabled."
    assert not session.backend.submitted_writes


@pytest.mark.asyncio
async def test_no_op_confirmation_does_not_claim_false_progress(session):
    # Real production bug found in a 65-conversation live sweep: "I want
    # to watch Whiplash" -> "You already have Whiplash in Plex." ->
    # confirmed with a plain "yes" -> "It's already on the way." --
    # misleading; cli_debrid's "no_op" status here means the opposite of
    # "in progress" (ALREADY_AVAILABLE_IN_BOTH_LIBRARIES/_PERMANENTLY/
    # _STANDARD -- see media_standard_request), i.e. it is already fully
    # done, not "on its way."
    session.backend.seed_library("Whiplash", media_type="movie", state="AVAILABLE_IN_PLEX", tmdb_id="244786")
    await session.turn(
        "I want to watch Whiplash",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Whiplash", "media_type": "movie"}}},
        ]}}],
    )
    session.backend.media_standard_request_override = {"status": "no_op", "reason": "ALREADY_AVAILABLE_IN_BOTH_LIBRARIES", "write_executed": False}
    import time as _time
    session.app.pending[session.client_id] = {
        "name": "media_standard_request",
        "arguments": {"workflow_id": "wf-244786", "canonical_external_id": "244786", "media_type": "movie",
                       "confirmation_context": {"confirmation_id": "conf-1", "session_id": session.client_id,
                                                 "plan_version_hash": "hash-1", "expires_at": 9999999999, "status": "PENDING"}},
        "action_id": "conf-1", "conversation_id": session.client_id, "session_id": session.client_id,
        "expires": _time.time() + 120, "workflow_id": "wf-244786", "canonical_external_id": "244786",
        "plan_version_hash": "hash-1",
    }
    reply = await session.turn("yes")
    assert reply == "You already have that -- no need to request it again."
    assert not session.backend.submitted_writes


# --- Offer decline (#9) ------------------------------------------------

@pytest.mark.asyncio
async def test_offer_decline_no_write_no_rediscovery(session):
    session.backend.seed_library("Cowboy Bebop", media_type="anime", state="ABSENT", tmdb_id="30991")
    await session.turn(
        "Do I have Cowboy Bebop?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Cowboy Bebop"}}},
        ]}}, {"message": {"content": "Not yet.", "tool_calls": []}}],
    )
    session.app.pending_offers[session.client_id] = {
        "offer": session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-1", operation="media_status"),
        "arguments": {"title": "Cowboy Bebop"}, "description": "check whether it's in Plex",
    }
    decline_reply = await session.turn("No.")
    assert session.client_id not in session.app.pending_offers
    assert session.client_id not in session.app.pending
    assert not session.backend.submitted_writes
    assert not any(name == "media_standard_request" for name, _ in session.backend.call_log)


# --- Offer expiry (#10) -------------------------------------------------

@pytest.mark.asyncio
async def test_offer_expiry_does_not_execute_stale_offer(session):
    offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-1", operation="media_status", ttl_seconds=1)
    session.app.pending_offers[session.client_id] = {"offer": offer, "arguments": {"title": "Cowboy Bebop"}, "description": "check whether it's in Plex"}
    # Force expiry deterministically rather than sleeping in a test.
    object.__setattr__(offer, "expires_at", time.time() - 1)
    await session.turn("Yeah.")
    assert not any(name == "media_status" for name, _ in session.backend.call_log)
    assert not session.backend.submitted_writes


# --- Offer topic-switch (#8) --------------------------------------------

@pytest.mark.asyncio
async def test_topic_switch_does_not_consume_offer(session):
    offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-1", operation="media_status")
    session.app.pending_offers[session.client_id] = {"offer": offer, "arguments": {"title": "Cowboy Bebop"}, "description": "check whether it's in Plex"}
    await session.turn(
        "Actually what's the weather tomorrow?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "weather_forecast", "arguments": {}}},
        ]}}, {"message": {"content": "It'll be sunny.", "tool_calls": []}}],
    )
    assert not any(name == "media_status" for name, _ in session.backend.call_log)
    # The offer is not required to survive a topic switch in this
    # implementation (it is popped on any non-accept classification), but a
    # write must never have happened, and the underlying subject must remain
    # available in conversation_context for a later continuation.
    assert not session.backend.submitted_writes


@pytest.mark.asyncio
async def test_accept_prefix_with_topic_switch_does_not_execute_offer(session):
    """'Yeah, but first what's the weather tomorrow?' must not silently
    perform the offered action (spec #8)."""
    offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-1", operation="media_status")
    session.app.pending_offers[session.client_id] = {"offer": offer, "arguments": {"title": "Cowboy Bebop"}, "description": "check whether it's in Plex"}
    await session.turn(
        "Yeah, but first what's the weather tomorrow?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "weather_forecast", "arguments": {}}},
        ]}}, {"message": {"content": "It'll be sunny.", "tool_calls": []}}],
    )
    assert not any(name == "media_status" for name, _ in session.backend.call_log)
    assert not session.backend.submitted_writes


# --- Multiple subjects (#11) --------------------------------------------

@pytest.mark.asyncio
async def test_explicit_subject_override_does_not_execute_stale_offer_for_other_subject(session):
    dune_offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-dune", operation="media_status")
    session.app.pending_offers[session.client_id] = {"offer": dune_offer, "arguments": {"title": "Dune"}, "description": "check whether Dune is in Plex"}
    session.backend.seed_library("Cowboy Bebop", media_type="anime", state="ABSENT", tmdb_id="30991")
    await session.turn(
        "Check Cowboy Bebop instead.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Cowboy Bebop"}}},
        ]}}, {"message": {"content": "Checked Cowboy Bebop.", "tool_calls": []}}],
    )
    dune_calls = [args for name, args in session.backend.call_log if "dune" in json.dumps(args).casefold()]
    assert not dune_calls, "the stale Dune offer must never execute just because 'yeah'-shaped routing ran"


# --- Web -> Plex -> request (#12) ---------------------------------------

@pytest.mark.asyncio
async def test_web_to_plex_to_request_same_subject(session):
    session.backend.seed_web("Segua", media_type="tv", tvdb_id="999")
    await session.turn(
        "Can you find this show called Segua online?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Segua"}}},
        ]}}, {"message": {"content": "Segua is a TV series.", "tool_calls": []}}],
    )
    referent = session.app.conversation_context.get(session.client_id, {}).get("latest_resolved_referent")
    assert referent and "segua" in referent.casefold()
    assert not session.backend.submitted_writes, "an online lookup is read-only"

    await session.turn(
        "Do I have it?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Segua"}}},
        ]}}, {"message": {"content": "Not yet.", "tool_calls": []}}],
    )
    plan_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert any("segua" in str(a.get("goal", "")).casefold() for a in plan_calls), "no repeated title required from the user, but the same subject must reach media_plan_goal"

    await session.turn(
        "No? Then get it.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Segua"}}},
        ]}}],
    )
    assert session.client_id not in session.app.pending
    assert len(session.backend.submitted_writes) == 1

    await session.turn("Go ahead.")
    assert len(session.backend.submitted_writes) == 1


# --- Full weather -> media -> web -> plex -> request chain (#7) --------

@pytest.mark.asyncio
async def test_weather_media_web_plex_request_full_chain(session):
    """"What's the weather?" -> "Do you know this show called Segua?" ->
    "Can you find it on the internet?" -> "Do I have it?" -> "Okay, get it."
    -- the old weather domain must never reassert itself at any later step
    merely because a follow-up is short."""
    session.backend.seed_web("Segua", media_type="tv", tvdb_id="999")

    await session.turn(
        "What's the weather?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "weather_forecast", "arguments": {}}},
        ]}}, {"message": {"content": "It's sunny and 70 degrees.", "tool_calls": []}}],
    )
    assert session.app.conversation_context.get(session.client_id, {}).get("domain") == "weather"

    await session.turn(
        "Do you know this show called Segua?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Segua"}}},
        ]}}],
        final_text="Segua is a TV series.",
    )
    context_after_media = session.app.conversation_context.get(session.client_id, {})
    assert context_after_media.get("domain") != "weather"
    referent = context_after_media.get("latest_resolved_referent")
    assert referent and "segua" in referent.casefold()

    await session.turn(
        "Can you find it on the internet?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Segua"}}},
        ]}}],
        final_text="Segua is a TV series about survival.",
    )
    assert not any(name == "weather_forecast" for name, _ in session.backend.call_log[-2:]), "weather must never reassert itself"

    await session.turn(
        "Do I have it?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Segua"}}},
        ]}}],
    )
    plan_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert any("segua" in str(a.get("goal", "")).casefold() for a in plan_calls)
    assert session.client_id not in session.app.pending

    await session.turn(
        "Okay, get it.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Segua"}}},
        ]}}],
    )
    assert session.client_id not in session.app.pending
    assert len(session.backend.submitted_writes) == 1


# --- Red team: Tools failure during offer execution must never write (#23) --

@pytest.mark.asyncio
async def test_tools_failure_during_offer_acceptance_produces_no_write(session):
    async def failing_invoke(name, arguments, cid, rid, confirmed=False, action_id=None):
        return {"tool": name, "status": "error", "result": {"error": "Tool service unavailable", "detail": "ConnectError"}}

    session.app.invoke_tool = failing_invoke
    offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-1", operation="media_plan_goal")
    session.app.pending_offers[session.client_id] = {"offer": offer, "arguments": {"goal": "Cowboy Bebop"}, "description": "check whether it's in Plex"}
    await session.turn("Yeah.")
    assert session.client_id not in session.app.pending
    assert not session.backend.submitted_writes


# --- Music / Lidarr conversation integration (spec item #1) ----------------
# Real architecture (read-only verified from tools/server-tools-app.py):
# Lidarr owns canonical album identity (lidarr_search_album/foreignAlbumId)
# and wanted/managed state; slskd owns Soulseek search/downloads;
# music_enricher owns post-download enrichment/quarantine; beets owns import;
# Plex "Music" library owns final visibility. media_plan_goal's album branch
# builds a confirmation naming operation "lidarr.media_execute_goal" -- a
# tool name with NO entry in the real REGISTRY/TOOLS dispatch table (grep
# confirms zero matches for "media_execute_goal" as a def or dict key
# anywhere in tools/server-tools-app.py). The real /invoke endpoint 404s any
# unregistered tool name, which assistant's invoke_tool turns into
# {"status": "error", "result": {"error": "That tool is not enabled."}} --
# this is the actual mechanism behind "Lidarr writes are policy-disabled":
# the executor was never built, not a feature flag that could be flipped.

@pytest.mark.asyncio
async def test_music_discovery_offer_and_status_no_cli_debrid(session):
    session.backend.seed_web("Rodeo", media_type="album", artist="Travis Scott", foreign_album_id="fa-rodeo-1")

    reply1 = await session.turn(
        "Do you know Rodeo by Travis Scott?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Rodeo Travis Scott"}}},
        ]}}],
        final_text="Rodeo is a Travis Scott album.",
    )
    assert "rodeo" in reply1.casefold()
    referent = session.app.conversation_context.get(session.client_id, {}).get("latest_resolved_referent")
    assert referent and "rodeo" in referent.casefold()

    # NOTE: a bare "Do I already have it?" hits a different, earlier
    # deterministic short-circuit in respond() (media_status_question /
    # retained_media_status_repair) that requires an existing
    # latest_media_workflow in context -- there isn't one yet after a pure
    # web_search discovery, so it answers "no matching live workflow"
    # without ever reaching the Qwen loop or media_plan_goal. That is a real
    # gap (flagged in the final report), separate from what this test is
    # isolating -- so this turn uses the same "Can you check Plex/library
    # for X?" phrasing already proven to route through the Qwen loop in
    # test_full_discover_offer_accept_write_conversation.
    # "Do I have Rodeo?" is now deterministically routed straight to
    # media_plan_goal by preflight_plan (media_goal_request matches "have"),
    # bypassing the ollama_script entirely -- verified directly against
    # preflight_plan before relying on it here, per this file's module
    # docstring note on merge-caused routing sensitivity.
    await session.turn("Do I have Rodeo?")
    assert not any(name in {"radarr_search", "sonarr_search", "media_standard_request"} for name, _ in session.backend.call_log), (
        "a music subject must never route through movie/TV tooling"
    )
    assert session.client_id not in session.app.pending

    await session.turn(
        "Get it.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Rodeo"}}},
        ]}}],
    )
    action = session.app.pending.get(session.client_id)
    assert action is not None
    assert action["name"] == "media_execute_goal", "music confirmations name the (nonexistent) manager executor, never cli_debrid"

    # "Go for it." must not produce a write -- the executor does not exist.
    await session.turn("Go for it.")
    assert not session.backend.submitted_writes
    assert any(name == "media_execute_goal" for name, _ in session.backend.call_log[-2:]), (
        "accepting the confirmation must actually attempt the named tool and hit the real 404 behavior, not silently no-op"
    )


@pytest.mark.asyncio
async def test_music_status_followup_same_workflow(session):
    """Regression test for the "2298" media-status-follow-up bug (predates
    this session -- confirmed present in `main` at the same line -- root
    cause: respond() called the narrow deterministic_plan()/
    semantic_preflight_allowed() pair, scoped to calculator/unit_convert
    only, so `live_results` was always empty for a media-status question,
    and a canned "no matching live workflow" short-circuit fired
    unconditionally before Qwen/discover_tools ever got a chance -- even
    when a real latest_media_workflow/canonical_identity already existed in
    context. Fixed by gating that short-circuit on the ABSENCE of a
    resolvable media referent, so a genuine follow-up now falls through to
    the normal bounded-discovery + Qwen tool-call path instead of being
    preempted. See voice-api-app.py's has_resolvable_media_referent comment
    for the full root-cause writeup.
    """
    session.backend.seed_library("Rodeo", media_type="album", state="ACQUIRED_NOT_VISIBLE", foreign_album_id="fa-rodeo-2", artist="Travis Scott")
    # "Do I have X?" phrasing itself hits the same media_status_question
    # short-circuit as a bare question (no canonical_identity/workflow
    # exists yet on the very first turn), so this uses the same
    # "Can you check Plex for X?" phrasing already proven to route through
    # the Qwen loop elsewhere in this file, to actually create the workflow
    # this test's second turn needs.
    await session.turn(
        "Is Rodeo in my library?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Rodeo"}}},
        ]}}],
    )
    # stage_media_confirmation (the only writer of latest_media_workflow/
    # canonical_identity) early-returns when no write confirmation is
    # required, so an "identified but not yet actionable" result like this
    # one only ever populates latest_resolved_referent (via
    # record_tool_referent), not latest_media_workflow -- check the field
    # that's actually set, not the one that isn't for this shape of result.
    referent_before = session.app.conversation_context.get(session.client_id, {}).get("latest_resolved_referent")
    assert referent_before and "rodeo" in referent_before.casefold(), "setup sanity check -- a referent must exist in context for this test to mean anything"

    full_reply = await session.turn(
        "How's the Rodeo album doing?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_status", "arguments": {"title": "Rodeo"}}},
        ]}}],
        final_text="It's been collected but Plex hasn't picked it up yet.",
    )
    assert "matching live workflow" not in full_reply.casefold(), "the canned short-circuit must not fire when a workflow exists in context"
    status_calls = [args for name, args in session.backend.call_log if name == "media_status"]
    assert status_calls, "media_status must actually be invoked now that the short-circuit no longer preempts it"
    assert "rodeo" in full_reply.casefold()


@pytest.mark.asyncio
async def test_status_followup_with_no_referent_still_asks_or_declines_safely(session):
    """The negative control (spec item #10): with NO resolvable media
    referent at all, the short-circuit's original behavior is correct and
    must be preserved -- a bare "How's it doing?" with nothing in context
    must not silently invent a subject or call a tool speculatively."""
    reply = await session.turn("How's it doing?")
    assert "matching live workflow" in reply.casefold()
    assert not session.backend.call_log


@pytest.mark.asyncio
async def test_status_followup_negative_control_weather_stays_weather(session):
    reply = await session.turn(
        "How's the weather doing?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "weather_forecast", "arguments": {}}},
        ]}}],
        final_text="It's sunny and 72 degrees.",
    )
    assert any(name == "weather_forecast" for name, _ in session.backend.call_log)
    assert "matching live workflow" not in reply.casefold()


@pytest.mark.parametrize("phrase", [
    "How's it doing?", "Any progress?", "Is it ready?", "Did it find anything?",
    "Why isn't it ready?", "What happened with it?",
])
@pytest.mark.parametrize("media_type,title,identity_fields", [
    ("movie", "Dune", {"tmdb_id": "1", "year": "2021"}),
    ("tv", "Segua", {"tvdb_id": "999"}),
    ("album", "Rodeo", {"foreign_album_id": "fa-rodeo-9", "artist": "Travis Scott"}),
])
@pytest.mark.asyncio
async def test_2298_regression_matrix_with_active_subject(session, phrase, media_type, title, identity_fields):
    """Spec item #9's regression matrix, "one active media subject" cell:
    every status-shaped phrase, for every media type, must actually reach a
    real status/diagnose tool once a workflow is resolved -- context
    resolution decides the outcome, not the phrase's exact wording."""
    session.backend.seed_library(title, media_type=media_type, state="SEARCHING", **identity_fields)
    await session.turn(
        f"Get {title}.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": f"get {title}"}}},
        ]}}],
    )
    session.app.pending.pop(session.client_id, None)  # this cell only cares about status-follow-up routing, not confirmation
    reply = await session.turn(
        phrase,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_status" if "why" not in phrase.casefold() else "media_diagnose", "arguments": {"title": title}}},
        ]}}],
        final_text=f"{title} is still being searched for.",
    )
    assert "matching live workflow" not in reply.casefold()


@pytest.mark.parametrize("phrase", ["How's it doing?", "Is it ready?"])
@pytest.mark.asyncio
async def test_2298_regression_no_active_subject_asks_or_declines(session, phrase):
    """Spec item #9, "no media subject" cell: with no resolvable referent,
    a status question must not fabricate one. Each phrase gets its own
    fresh session/turn -- reusing one session across a loop lets the first
    canned response become latest_assistant_response and can change how a
    later short utterance classifies (repeat/rephrase intent), which is not
    what this test is about.

    "Any progress?" is intentionally excluded here: the real
    media_status_question() classifier does not treat it as a status
    question in isolation (it has no recognized question-frame word like
    "how's/is/what's", and its fallback path's narrower word list does not
    include "progress" even though the main status_word regex does) -- so a
    bare "Any progress?" with zero context does not hit this short-circuit
    at all, by design of the pre-existing classifier, not a defect this
    pass introduced or is scoped to fix. It IS covered, correctly, in
    test_2298_regression_matrix_with_active_subject, where a referent
    already exists and normal routing (not this short-circuit) handles it.
    """
    reply = await session.turn(phrase)
    assert "matching live workflow" in reply.casefold()
    assert not session.backend.call_log


@pytest.mark.asyncio
async def test_2298_regression_web_turn_in_between_does_not_lose_subject(session):
    """Spec item #9, "web turn in between" cell."""
    session.backend.seed_library("Dune", media_type="movie", state="SEARCHING", tmdb_id="1", year="2021")
    await session.turn(
        "Get Dune.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Dune"}}},
        ]}}],
    )
    session.app.pending.pop(session.client_id, None)
    await session.turn(
        "Can you search the web for the director?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Dune director"}}},
        ]}}],
        final_text="Denis Villeneuve directed it.",
    )
    reply = await session.turn(
        "How's it doing?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_status", "arguments": {"title": "Dune"}}},
        ]}}],
        final_text="Still searching for a copy.",
    )
    assert "matching live workflow" not in reply.casefold()


@pytest.mark.asyncio
async def test_2298_regression_weather_turn_in_between_does_not_lose_subject(session):
    """Spec item #9, "weather turn in between" cell."""
    session.backend.seed_library("Dune", media_type="movie", state="SEARCHING", tmdb_id="1", year="2021")
    await session.turn(
        "Get Dune.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Dune"}}},
        ]}}],
    )
    session.app.pending.pop(session.client_id, None)
    await session.turn(
        "What's the weather?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "weather_forecast", "arguments": {}}},
        ]}}],
        final_text="Sunny today.",
    )
    reply = await session.turn(
        "How's it doing?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_status", "arguments": {"title": "Dune"}}},
        ]}}],
        final_text="Still searching for a copy.",
    )
    assert "matching live workflow" not in reply.casefold()


@pytest.mark.asyncio
async def test_ambiguous_cross_media_title_requires_clarification_not_arbitrary_choice(session):
    """"Do you know Blonde?" is plausibly an album (Frank Ocean) or a movie
    with the same title -- the real media_plan_goal reports ambiguous=True
    with candidates rather than silently picking one; this proves the
    conversation layer surfaces that as a clarification, not a guess."""
    session.backend.seed_web("Blonde", media_type="album", artist="Frank Ocean", foreign_album_id="fa-blonde-1")
    session.backend.seed_web("Blonde", media_type="movie", tmdb_id="999888")

    await session.turn(
        "Do you know Blonde?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Blonde"}}},
        ]}}],
    )
    assert session.client_id not in session.app.pending
    assert session.client_id not in session.app.pending_offers, "an unresolved ambiguous identity must never become an offer"
    assert not session.backend.submitted_writes


# --- Subject switch during offer (spec item #8) -----------------------

@pytest.mark.asyncio
async def test_explicit_subject_switch_replaces_offer_without_executing_it(session):
    """"Want me to check Cowboy Bebop in Plex?" / "Actually check Dune
    instead." -- the new explicit subject wins, the Cowboy Bebop offer is
    never executed, and Dune becomes the active subject."""
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="1", year="2021")
    cowboy_offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-cowboy", operation="plex_match_canonical_media")
    session.app.pending_offers[session.client_id] = {
        "offer": cowboy_offer, "arguments": {"title": "Cowboy Bebop"}, "description": "check whether Cowboy Bebop is in Plex",
    }
    await session.turn(
        "Actually check Dune instead.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune"}}},
        ]}}],
    )
    cowboy_calls = [args for name, args in session.backend.call_log if "cowboy" in json.dumps(args).casefold()]
    assert not cowboy_calls, "the old offer's subject must never be queried just because routing ran"
    dune_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal" and "dune" in str(args.get("goal", "")).casefold()]
    assert dune_calls, "the new explicit subject must actually be resolved"


# --- Concurrent sessions (spec item #17) -------------------------------

@pytest.mark.asyncio
async def test_concurrent_sessions_do_not_leak_subjects_or_offers(app, backend):
    """Two simultaneous conversations (movie subject+offer, album
    subject+offer) must not share pending/pending_offers/conversation_context
    state -- each client_id is its own session by construction (all state is
    keyed by client_id), this proves it holds under real interleaved use."""
    backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="1", year="2021")
    backend.seed_library("Rodeo", media_type="album", state="ABSENT", foreign_album_id="fa-rodeo-3", artist="Travis Scott")
    ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
    client_a, client_b = f"session-a-{uuid.uuid4()}", f"session-b-{uuid.uuid4()}"

    async def invoke_tool_fake(name, arguments, cid, rid, confirmed=False, action_id=None):
        return await backend.invoke(name, arguments, cid, rid, confirmed=confirmed, action_id=action_id)

    async def discover_tools_fake(user_text, context):
        catalog = [{"type": "function", "function": {"name": "media_plan_goal", "description": "plan media", "parameters": {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]}}}]
        return [item["function"] for item in catalog], [{"metadata": {"canonical_name": "media_plan_goal", "score": 1.0}}], 1.0

    app.invoke_tool = invoke_tool_fake
    app.discover_tools = discover_tools_fake

    for client_id, ws, title in ((client_a, ws_a, "Dune"), (client_b, ws_b, "Rodeo")):
        app.httpx = _FakeHttpxModule([{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": f"get {title}"}}},
        ]}}])
        await app.respond(ws, client_id, str(uuid.uuid4()), f"Get {title}.")

    action_a = app.pending.get(client_a)
    action_b = app.pending.get(client_b)
    assert action_a is None and action_b is not None
    assert len(backend.submitted_writes) == 1
    dune_write = backend.submitted_writes[0]
    assert dune_write["arguments"]["confirmation_context"]["title"] == "Dune"
    assert dune_write["arguments"]["session_id"] == client_a
    assert action_b["arguments"]["confirmation_context"]["title"] == "Rodeo"
    assert dune_write["arguments"] != action_b["arguments"]
    assert app.conversation_context.get(client_a, {}).get("latest_media_workflow", {}).get("title") != "Rodeo"

    # A stale approval in session A must not consume session B's confirmation.
    app.httpx = _FakeHttpxModule([{"message": {"content": "", "tool_calls": []}}], "Done, got Dune.")
    await app.respond(ws_a, client_a, str(uuid.uuid4()), "Go for it.")
    assert len(backend.submitted_writes) == 1
    assert backend.submitted_writes[0]["arguments"]["confirmation_context"]["title"] == "Dune"
    assert client_b in app.pending, "confirming session A must never consume or clear session B's pending confirmation"


@pytest.mark.asyncio
async def test_confirmation_and_offer_in_one_openwebui_chat_are_invisible_to_another(session):
    """A bare confirmation in chat B must never observe or consume chat A.

    The IDs use the exact Open WebUI session-key shape produced by the
    compatibility facade.  This exercises the real ``respond`` lookup path,
    rather than merely asserting that two Python dict keys differ.
    """
    chat_a = "openwebui:user-a:chat-a"
    chat_b = "openwebui:user-a:chat-b"
    staged_confirmation = {"name": "media_standard_request", "action_id": "only-a", "expires": time.time() + 60}
    staged_offer = session.app.PendingOffer.create(
        session_id=chat_a, subject_ref="subject-a", operation="media_plan_goal"
    )
    session.app.pending[chat_a] = staged_confirmation
    session.app.pending_offers[chat_a] = {"offer": staged_offer, "arguments": {"goal": "The Room"}, "description": "A only"}
    session.app.conversation_context[chat_a] = {"latest_resolved_referent": "The Room", "operation": "media_request"}
    session.app.sessions[chat_a] = [{"role": "user", "content": "Get The Room."}]

    await session.turn_for(chat_b, "Yes.", final_text="I need an active request in this chat first.")

    assert session.app.pending[chat_a] is staged_confirmation
    assert session.app.pending_offers[chat_a]["offer"] is staged_offer
    assert chat_b not in session.app.pending
    assert chat_b not in session.app.pending_offers
    assert session.backend.submitted_writes == []


# --- Failure injection: Plex unavailable during an accepted offer (#15) --

@pytest.mark.asyncio
async def test_plex_unavailable_during_offer_acceptance_is_truthful_no_write(session):
    async def unavailable_invoke(name, arguments, cid, rid, confirmed=False, action_id=None):
        if name == "plex_match_canonical_media":
            return {"tool": name, "status": "unavailable", "result": {"error": "plex API returned HTTP 502", "error_code": "BACKEND_UNAVAILABLE", "retryable": True}}
        return await session.backend.invoke(name, arguments, cid, rid, confirmed=confirmed, action_id=action_id)

    session.app.invoke_tool = unavailable_invoke
    offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-1", operation="plex_match_canonical_media")
    session.app.pending_offers[session.client_id] = {"offer": offer, "arguments": {"title": "Cowboy Bebop"}, "description": "check whether it's in Plex"}
    await session.turn("Yeah.")
    assert session.client_id not in session.app.pending, "a failed read must never become a write confirmation"
    assert not session.backend.submitted_writes


# --- Multi-subject disambiguation dialogue (spec items #3-#6) --------------

@pytest.mark.asyncio
async def test_disambiguation_dialogue_resolves_the_new_one(session):
    """"Do you know Dune?" -> two plausible movies -> Assistant asks which
    one -> "The new one." -> resolves to 2021, discards the 1984 candidate,
    preserves that exact canonical identity for the next turn."""
    session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
    session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")

    reply1 = await session.turn(
        "Do you know Dune?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune"}}},
        ]}}],
    )
    assert "1984" in reply1 and "2021" in reply1
    disambiguation = session.app.conversation_context.get(session.client_id, {}).get("pending_disambiguation")
    assert disambiguation and len(disambiguation["candidates"]) == 2

    reply2 = await session.turn("The new one.")
    assert "pending_disambiguation" not in session.app.conversation_context.get(session.client_id, {})
    plan_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert any("2021" in str(a.get("goal", "")) for a in plan_calls)
    assert not any("1984" in str(a.get("goal", "")) for a in plan_calls[-1:])

    await session.turn(
        "Do I have it?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune 2021"}}},
        ]}}],
    )
    status_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert all("1984" not in str(a.get("goal", "")) for a in status_calls), "the discarded 1984 candidate must never resurface"


@pytest.mark.asyncio
async def test_cross_media_disambiguation_album_vs_movie(session):
    """Spec item #4: "Blonde" is ambiguous across album/movie -- clean,
    stable fixture already used in the prior round's disambiguation test."""
    session.backend.seed_web("Blonde", media_type="album", artist="Frank Ocean", foreign_album_id="fa-blonde-1")
    session.backend.seed_web("Blonde", media_type="movie", tmdb_id="999888", year="2019")

    reply1 = await session.turn(
        "Do you know Blonde?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Blonde"}}},
        ]}}],
    )
    assert "which one" in reply1.casefold() or "mean" in reply1.casefold()
    assert session.client_id not in session.app.pending
    assert session.client_id not in session.app.pending_offers

    await session.turn("The album.")
    plan_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert any("album" in str(a.get("goal", "")).casefold() for a in plan_calls[-1:])
    assert "pending_disambiguation" not in session.app.conversation_context.get(session.client_id, {})


@pytest.mark.asyncio
async def test_dominant_candidate_does_not_force_clarification(session):
    """Spec item #4: clarification is only asked when genuinely required --
    a single unambiguous match must not be forced through the disambiguation
    dialogue."""
    session.backend.seed_web("Cowboy Bebop", media_type="anime", tmdb_id="30991")
    reply = await session.turn(
        "Do you know Cowboy Bebop?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Cowboy Bebop"}}},
        ]}}],
    )
    assert "which one" not in reply.casefold()
    assert "pending_disambiguation" not in session.app.conversation_context.get(session.client_id, {})


# --- Disambiguation follow-up language matrix (spec item #5) --------------

@pytest.mark.parametrize("reply_text,expected_year", [
    ("the new one", "2021"), ("the newest one", "2021"), ("2021", "2021"),
    ("the old one", "1984"), ("the original", "1984"), ("1984", "1984"),
    ("the first one", "1984"), ("the second one", "2021"),
])
@pytest.mark.asyncio
async def test_disambiguation_followup_language_matrix(session, reply_text, expected_year):
    session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
    session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")
    await session.turn(
        "Do you know Dune?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune"}}},
        ]}}],
    )
    await session.turn(reply_text)
    plan_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert plan_calls and expected_year in str(plan_calls[-1].get("goal", "")), f"{reply_text!r} must resolve to {expected_year}"


@pytest.mark.parametrize("reply_text", ["the movie", "the show", "the series"])
@pytest.mark.asyncio
async def test_disambiguation_followup_media_type_language(session, reply_text):
    session.backend.seed_web("Blonde", media_type="album", artist="Frank Ocean", foreign_album_id="fa-blonde-2")
    session.backend.seed_web("Blonde", media_type="movie", tmdb_id="777", year="2019")
    await session.turn(
        "Do you know Blonde?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Blonde"}}},
        ]}}],
    )
    await session.turn(reply_text)
    plan_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    if reply_text == "the movie":
        assert plan_calls and "movie" in str(plan_calls[-1].get("goal", "")).casefold()
    # "the show"/"the series" have no matching candidate here (album/movie
    # only) -- resolve_disambiguation_reply correctly returns None for
    # those, which is asserted by the ambiguous-reply test below rather
    # than here (this test only checks the movie/album pair resolves).


# --- Ambiguous "yes" must not select a subject (spec item #6) --------------

@pytest.mark.asyncio
async def test_bare_yes_does_not_select_among_candidates(session):
    session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
    session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")
    await session.turn(
        "Do you know Dune?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune"}}},
        ]}}],
    )
    calls_before = len(session.backend.call_log)
    reply = await session.turn("Yeah.")
    assert "which one" in reply.casefold() or "mean" in reply.casefold()
    assert len(session.backend.call_log) == calls_before, "an unresolved reply must not invoke any tool"
    assert session.client_id not in session.app.pending
    assert not session.backend.submitted_writes
    disambiguation = session.app.conversation_context.get(session.client_id, {}).get("pending_disambiguation")
    assert disambiguation and len(disambiguation["candidates"]) == 2, "the candidate set must survive an unresolved reply"


@pytest.mark.asyncio
async def test_plural_reply_to_three_candidates_explains_single_plan_boundary(session):
    for index, year in enumerate((2001, 2002, 2003), start=1):
        session.backend.seed_web("Example Story", media_type="movie", year=year, tmdb_id=str(9000 + index))
    await session.turn(
        "Get Example Story",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Get Example Story"}}},
        ]}}],
    )
    calls_before = len(session.backend.call_log)
    reply = await session.turn("Both")
    assert "three" in reply.casefold()
    assert "one exact request at a time" in reply.casefold()
    assert len(session.backend.call_log) == calls_before
    assert not session.backend.submitted_writes


@pytest.mark.asyncio
async def test_bare_oh_after_media_turn_terminates_without_tool_or_model(session):
    session.app.conversation_context[session.client_id] = {
        "domain": "media",
        "canonical_identity": {"title": "Example Story", "year": 2001, "media_type": "movie", "tmdb_id": "9001"},
    }
    calls_before = len(session.backend.call_log)
    reply = await session.turn("Oh")
    assert reply == "Okay."
    assert len(session.backend.call_log) == calls_before
    assert "camera" not in reply.casefold()


@pytest.mark.asyncio
async def test_disambiguation_status_combined(session):
    """Spec item #12: "Do you know Dune?" / "the new one" / "Do I have it?"
    / "How's it doing?" -- the same resolved 2021 subject throughout,
    including through the (now-fixed) status follow-up."""
    session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
    session.backend.seed_library("Dune", media_type="movie", year="2021", tmdb_id="438631", state="SEARCHING")
    await session.turn(
        "Do you know Dune?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune"}}},
        ]}}],
    )
    await session.turn("The new one.")
    reply = await session.turn(
        "How's it doing?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_status", "arguments": {"title": "Dune"}}},
        ]}}],
        final_text="Still searching for a copy.",
    )
    assert "matching live workflow" not in reply.casefold()
    status_calls = [args for name, args in session.backend.call_log if name == "media_status"]
    assert status_calls


# --- Camera/media coexistence in one session (post-merge regression) -------
# Proves main's "dispatch bounded camera plans deterministically" work and
# this branch's PendingOffer/disambiguation/referent-tracking work coexist
# correctly: neither side's conversation_context writes clobber the other's
# fields, and neither side's tool ever fires because of the other's state.

@pytest.mark.asyncio
async def test_active_media_offer_survives_an_interleaved_camera_question(session):
    session.backend.seed_web("Cowboy Bebop", media_type="anime", tmdb_id="30991")
    offer = session.app.PendingOffer.create(session_id=session.client_id, subject_ref="subj-1", operation="media_plan_goal")
    session.app.pending_offers[session.client_id] = {"offer": offer, "arguments": {"goal": "Cowboy Bebop"}, "description": "check whether it's in Plex"}
    session.app.conversation_context[session.client_id] = {"latest_resolved_referent": "Cowboy Bebop"}

    camera_reply = await session.turn("Is anyone at the front door right now?")
    assert any(name == "frigate_snapshot" for name, _ in session.backend.call_log)
    assert not any(name == "media_plan_goal" for name, _ in session.backend.call_log), (
        "an unrelated camera question must never consume the stale media offer"
    )
    # The camera turn's store_provenance-style context write must not have
    # dropped the referent or the still-pending offer.
    assert session.client_id in session.app.pending_offers, "the media offer must survive an interleaved camera question"
    assert session.app.conversation_context.get(session.client_id, {}).get("latest_resolved_referent") == "Cowboy Bebop"

    calls_before = len(session.backend.call_log)
    resume_reply = await session.turn("Yeah, check it.")
    assert len(session.backend.call_log) > calls_before, "returning to the media follow-up must actually invoke the offer's operation"
    assert session.client_id not in session.app.pending_offers


@pytest.mark.asyncio
async def test_active_camera_context_survives_an_interleaved_media_question(session):
    session.app.conversation_context[session.client_id] = {
        "domain": "camera", "latest_domain": "camera", "kind": "camera", "group": "cameras",
        "camera": "front_door", "latest_event_id": "event-777", "latest_review_id": "review-42",
    }
    session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")

    await session.turn(
        "Do you know Dune?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Dune"}}},
        ]}}],
        final_text="Dune is a science fiction movie.",
    )
    context_after_media = session.app.conversation_context.get(session.client_id, {})
    assert context_after_media.get("latest_event_id") == "event-777", "a media discovery turn must not wipe the retained camera event"
    assert context_after_media.get("latest_review_id") == "review-42"
    assert "dune" in (context_after_media.get("latest_resolved_referent") or "").casefold()

    camera_reply = await session.turn("Are they still there?")
    assert any(name == "frigate_snapshot" for name, _ in session.backend.call_log[-1:]), (
        "the retained camera event must still route a live-presence follow-up correctly after the media detour"
    )


# --- Bounded deep-news evidence recovery ----------------------------------
# A model that returns no follow-up calls after discovery must not prevent
# recovery-search evidence from being fetched. These fixtures use multiple
# domains so a first-search-only fetch block cannot accidentally satisfy the
# deep-research readiness contract.

def _quiet_news_fixtures(session, monkeypatch, *, day=18, strong=False, still_sparse=False, corroborated=None):
    """Synthetic network responses; real respond/gates/synthesis stay enabled."""
    now = datetime(2026, 9, day, 12, tzinfo=ZoneInfo("America/Toronto")).timestamp()
    monkeypatch.setattr(session.app, "time", SimpleNamespace(**{**vars(time), "time": lambda: now}))
    noise = [{"url": "https://unrelated-world.example/football", "date": f"2026-09-{day}",
              "title": "Overseas football results"},
             {"url": "https://world-noise.example/markets", "date": f"2026-09-{day}",
              "title": "Overseas markets"}]
    urls = ["https://cbc.ca/news/funding", "https://reuters.com/world/canada/housing"]
    date = f"2026-09-{day if strong else day - 1}"
    coverage = [{"url": url, "date": date, "title": title} for url, title in zip(
        urls, ["Canada research funding", "Canada housing update"])]
    session.backend.web_search_fixtures = ([coverage, [], []] if strong else
                                          [noise, [], [], noise if still_sparse else coverage + noise])
    session.backend.web_fetch_contents[noise[0]["url"]] = "Overseas football teams won their matches."
    session.backend.web_fetch_contents[noise[1]["url"]] = "Overseas markets reported a quiet session."
    session.backend.web_fetch_contents[urls[0]] = "Canada announced research funding."
    session.backend.web_fetch_contents[urls[1]] = "Canada reported new housing construction figures."
    if corroborated is not None:
        session.backend.web_fetch_contents[urls[0]] += " Alice Doe is the prime minister."
        if corroborated:
            session.backend.web_fetch_contents[urls[1]] += " Alice Doe is the prime minister."
    return urls, date


@pytest.mark.asyncio
@pytest.mark.parametrize("day,window", [(18, 2), (20, 3)])
async def test_quiet_canada_news_widens_once_with_disclosure_and_dated_sources(session, monkeypatch, day, window):
    urls, date = _quiet_news_fixtures(session, monkeypatch, day=day)
    reply = await session.turn("Give me an in-depth review of the recent news in Canada today",
                               final_text="The fetched coverage describes funding and housing developments.")
    searches = [args for name, args in session.backend.call_log if name == "web_search"]
    assert [args["recency_days"] for args in searches] == [1, 1, 1, window]
    assert all("canada" in args["query"].casefold() for args in searches)
    assert "same-day coverage is limited" in reply.casefold()
    assert "Canada" in reply and f"2026-09-{day}" in reply
    assert session.last_stream_payload is not None
    evidence = [json.loads(msg["content"]) for msg in session.last_stream_payload["messages"]
                if msg.get("role") == "tool" and msg.get("name") == "web_fetch"]
    assert {item["url"] for item in evidence} == set(urls)
    assert all(item["date"] == date for item in evidence)
    assert "unrelated-world.example" not in json.dumps(session.last_stream_payload["messages"])
    traces = [event for event in session.ws.sent if event.get("type") == "trace"]
    assert all(url in json.dumps(traces) for url in urls)
    fetched_sources = [source for event in traces for entry in event["entries"] for source in entry["sources"]
                       if source["kind"] == "fetched" and source["url"] in urls]
    assert {source["published"] for source in fetched_sources} == {date}
    assert len(session.backend.call_log) <= 16


def _dated_canadian_fetches(session):
    """Give scheduler fixtures explicit article geography and publication dates."""
    for batch in session.backend.web_search_fixtures:
        for candidate in batch or []:
            url = candidate["url"]
            session.backend.web_fetch_contents[url] = f"Canadian technology reporting for {url}."
            session.backend.web_fetch_metadata[url] = {"date": datetime.now(ZoneInfo("America/Toronto")).date().isoformat()}


@pytest.mark.asyncio
async def test_quiet_canada_news_strong_same_day_evidence_never_widens(session, monkeypatch):
    _quiet_news_fixtures(session, monkeypatch, strong=True)
    reply = await session.turn("Give me an in-depth review of Canada news today",
                               final_text="Canada announced funding and housing developments.")
    assert session.last_stream_payload is not None
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1]
    assert "same-day coverage is limited" not in reply.casefold()


@pytest.mark.asyncio
async def test_quiet_canada_news_world_noise_stays_sparse_after_one_retry(session, monkeypatch):
    _quiet_news_fixtures(session, monkeypatch, still_sparse=True)
    reply = await session.turn("Give me an in-depth review of Canada news today",
                               final_text="An invented worldwide roundup must not be used.")
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1, 2]
    assert session.last_stream_payload is None
    assert "same-day coverage is limited" in reply.casefold()
    assert "Canada" in reply and "enough" in reply
    assert "invented" not in reply and "worldwide" not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("corroborated", [False, True])
async def test_quiet_canada_news_widening_preserves_current_holder_corroboration(session, monkeypatch, corroborated):
    _quiet_news_fixtures(session, monkeypatch, corroborated=corroborated)
    reply = await session.turn("Give me an in-depth review of Canada news today",
                               final_text="Alice Doe is the prime minister.")
    assert session.last_stream_payload is not None
    assert "same-day coverage is limited" in reply.casefold()
    if corroborated:
        assert "Alice Doe is the prime minister." in reply
    else:
        assert "can't safely verify that current office-holder" in reply
        assert "Alice Doe is the prime minister." not in reply


@pytest.mark.asyncio
async def test_quiet_canada_news_reserves_retry_budget_and_overrides_model_window(session, monkeypatch):
    urls, date = _quiet_news_fixtures(session, monkeypatch)
    session.backend.web_search_fixtures[0] = [
        {"url": f"https://world-{index}.example/sport", "date": "2026-09-18"}
        for index in range(20)
    ]
    reply = await session.turn(
        "Give me an in-depth review of Canada news today",
        ollama_script=[{"message": {"tool_calls": [{"function": {"name": "web_search", "arguments": {
            "query": "Canada news", "recency_days": 30,
        }}}]}}], final_text="The articles describe Canadian funding and housing.",
    )
    assert len(session.backend.call_log) == 16
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1, 2]
    assert session.last_stream_payload is not None
    assert "same-day coverage is limited" in reply.casefold()
    sources = [source for event in session.ws.sent if event.get("type") == "trace"
               for entry in event["entries"] for source in entry["sources"] if source["kind"] == "fetched"]
    assert set(urls) <= {source["url"] for source in sources}
    assert all(source["published"] == date for source in sources if source["url"] in urls)


@pytest.mark.asyncio
async def test_quiet_canada_news_model_fetched_noise_cannot_leak_into_synthesis(session, monkeypatch):
    _quiet_news_fixtures(session, monkeypatch)
    await session.turn(
        "Give me an in-depth review of Canada news today",
        ollama_script=[{"message": {"tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canada news"}}},
            {"function": {"name": "web_fetch", "arguments": {"url": "https://unrelated-world.example/football"}}},
        ]}}], final_text="The articles describe Canadian funding and housing.",
    )
    assert session.last_stream_payload is not None
    assert "unrelated-world.example" not in json.dumps(session.last_stream_payload["messages"])


@pytest.mark.asyncio
async def test_quiet_canada_news_dated_roundup_does_not_discard_current_role_conflicts(session, monkeypatch):
    _quiet_news_fixtures(session, monkeypatch, corroborated=True)
    url = "https://canada.gc.ca/current-government"
    session.backend.web_search_fixtures[0].append({"url": url, "title": "Canada government"})
    session.backend.web_fetch_contents[url] = "Bob Roe is the prime minister. Canada government directory."
    reply = await session.turn("Give me an in-depth review of Canada news today",
                               final_text="Alice Doe is the prime minister.")
    assert session.last_stream_payload is not None
    assert "can't safely verify that current office-holder" in reply
    assert "Alice Doe is the prime minister." not in reply


@pytest.mark.asyncio
async def test_quiet_canada_news_geography_cannot_hide_institutional_role_conflict(session, monkeypatch):
    _quiet_news_fixtures(session, monkeypatch, corroborated=True)
    url = "https://pm.gc.ca/current-government"
    session.backend.web_search_fixtures[0].append({"url": url})
    session.backend.web_fetch_contents[url] = "Bob Roe is the prime minister."
    reply = await session.turn("Give me an in-depth review of Canada news today",
                               final_text="Alice Doe is the prime minister.")
    assert session.last_stream_payload is not None
    assert "can't safely verify that current office-holder" in reply
    assert "Alice Doe is the prime minister." not in reply


@pytest.mark.asyncio
async def test_quiet_canada_news_ambiguous_geography_cannot_make_roundup_ready(session, monkeypatch):
    urls, _ = _quiet_news_fixtures(session, monkeypatch, strong=True)
    session.backend.web_fetch_contents[urls[0]] = "A Labrador won the dog show in London."
    session.backend.web_fetch_contents[urls[1]] = "Ontario, California approved new city transport services."
    session.backend.web_search_fixtures.append([])
    reply = await session.turn("Give me an in-depth review of Canada news today", final_text="Unsupported roundup.")
    assert session.last_stream_payload is None
    assert "still couldn't verify enough" in reply
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", ["technology ", "", "top "])
async def test_quiet_canada_news_requested_topic_controls_evidence_gate(session, monkeypatch, topic):
    urls, _ = _quiet_news_fixtures(session, monkeypatch, strong=True)
    session.backend.web_fetch_contents[urls[0]] = "Canada's hockey team won a league match."
    session.backend.web_fetch_contents[urls[1]] = "Canadian hockey players prepared for their next tournament."
    session.backend.web_search_fixtures.append([])
    reply = await session.turn(f"Give me an in-depth review of Canada {topic}news today", final_text="The hockey season continued.")
    if topic == "technology ":
        assert session.last_stream_payload is None
        assert "still couldn't verify enough" in reply
        assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1, 2]
    else:
        assert session.last_stream_payload is not None
        assert "same-day coverage is limited" not in reply.casefold()


@pytest.mark.asyncio
async def test_quiet_canada_news_topic_cannot_be_bypassed_by_request_word_overlap(session, monkeypatch):
    urls, _ = _quiet_news_fixtures(session, monkeypatch, strong=True)
    session.backend.web_fetch_contents[urls[0]] = "Canadian hockey players are competing in a league final."
    session.backend.web_fetch_contents[urls[1]] = "Canada's hockey teams are preparing for a tournament."
    session.backend.web_search_fixtures.append([])
    reply = await session.turn("What are today’s top technology headlines in Canada? Give me an in-depth review.",
                               final_text="The hockey teams are competing.")
    assert session.last_stream_payload is None
    assert "still couldn't verify enough" in reply
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [
    "I want an in-depth review of today’s news in Canada",
    "What are today’s biggest headlines in Canada? Give me an in-depth review.",
])
async def test_quiet_canada_news_generic_language_keeps_broad_strong_coverage(session, monkeypatch, prompt):
    urls, _ = _quiet_news_fixtures(session, monkeypatch, strong=True)
    reply = await session.turn(prompt, final_text="Canada reported funding and housing developments.")
    assert session.last_stream_payload is not None
    evidence = [json.loads(msg["content"]) for msg in session.last_stream_payload["messages"]
                if msg.get("role") == "tool" and msg.get("name") == "web_fetch"]
    assert {item["url"] for item in evidence} == set(urls)
    assert "same-day coverage is limited" not in reply.casefold()
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1]


@pytest.mark.asyncio
async def test_quiet_canada_news_technology_synonyms_satisfy_requested_category(session, monkeypatch):
    urls, _ = _quiet_news_fixtures(session, monkeypatch, strong=True)
    session.backend.web_fetch_contents[urls[0]] = "Canada announced semiconductor manufacturing investment."
    session.backend.web_fetch_contents[urls[1]] = "Canadian software developers launched a new platform."
    reply = await session.turn("What are today’s top technology headlines in Canada? Give me an in-depth review.",
                               final_text="Canada reported semiconductor and software developments.")
    assert session.last_stream_payload is not None
    assert "same-day coverage is limited" not in reply.casefold()
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1]


@pytest.mark.asyncio
async def test_quiet_canada_news_wider_topic_matches_only_enter_synthesis(session, monkeypatch):
    urls, _ = _quiet_news_fixtures(session, monkeypatch, strong=True)
    session.backend.web_fetch_contents[urls[0]] = "Canadian hockey teams finished a league match."
    session.backend.web_fetch_contents[urls[1]] = "Canada hosted a hockey tournament."
    wider_urls = ["https://tech-one.example/chips", "https://tech-two.example/software"]
    session.backend.web_search_fixtures.append([
        {"url": wider_urls[0], "date": "2026-09-17"}, {"url": wider_urls[1], "date": "2026-09-17"},
    ])
    session.backend.web_fetch_contents[wider_urls[0]] = "Canada announced new semiconductor technology funding."
    session.backend.web_fetch_contents[wider_urls[1]] = "Canadian software companies expanded their engineering teams."
    reply = await session.turn("Give me an in-depth review of Canada technology news today", final_text="Technology coverage included chips and software.")
    assert "same-day coverage is limited" in reply.casefold()
    evidence = [json.loads(msg["content"]) for msg in session.last_stream_payload["messages"]
                if msg.get("role") == "tool" and msg.get("name") == "web_fetch"]
    assert {item["url"] for item in evidence} == set(wider_urls)
    assert "hockey" not in json.dumps(session.last_stream_payload["messages"]).casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize("local_now,window,start,end", [
    ("2026-03-09T00:30:00", 2, "2026-03-08", "2026-03-09"),
    ("2026-11-01T23:30:00", 3, "2026-10-30", "2026-11-01"),
])
async def test_quiet_canada_news_dst_disclosure_matches_toronto_calendar(session, monkeypatch, local_now, window, start, end):
    now = datetime.fromisoformat(local_now).replace(tzinfo=ZoneInfo("America/Toronto")).timestamp()
    monkeypatch.setattr(session.app, "time", SimpleNamespace(**{**vars(time), "time": lambda: now}))
    session.backend.web_search_fixtures = [[], [], [], []]
    reply = await session.turn("Give me an in-depth review of Canada news today", final_text="Unsupported roundup.")
    assert f"{start} through {end}" in reply
    assert [args["recency_days"] for name, args in session.backend.call_log if name == "web_search"] == [1, 1, 1, window]


@pytest.mark.asyncio
async def test_deep_news_recovery_fetches_results_from_each_search(session):
    user_text = "Please give me an in-depth review of Canada's technology news today."
    session.backend.web_search_fixtures = [
        [{"title": "Initial discovery", "url": "https://wire.test/initial", "snippet": "Initial report."}],
        [{"title": "Recovery source", "url": "https://public.test/recovery", "snippet": "Independent report."}],
        [{"title": "Follow-up source", "url": "https://regional.test/follow-up", "snippet": "Regional report."}],
    ]
    _dated_canadian_fetches(session)

    await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canada technology news"}}},
        ]}}],
        final_text="Here is the researched news summary.",
    )

    profile = session.app.research_profile(user_text)
    search_calls = [arguments for name, arguments in session.backend.call_log if name == "web_search"]
    fetch_calls = [arguments for name, arguments in session.backend.call_log if name == "web_fetch"]
    assert session.last_stream_payload is not None
    fetched_results = [
        json.loads(message["content"])
        for message in session.last_stream_payload["messages"]
        if message.get("role") == "tool" and message.get("name") == "web_fetch"
    ]
    fetched_domains = {
        re.match(r"https?://([^/]+)", str(result["url"])).group(1)
        for result in fetched_results
    }

    assert len(search_calls) == 3, "deep recovery must issue all three successful discovery searches"
    assert len(fetch_calls) >= 2, "recovery-search results must be fetched as evidence, not only searched"
    assert len(fetched_domains) >= 2, "deep evidence must include fetched sources from different domains"
    assert len(session.backend.call_log) <= profile["max_calls"]


@pytest.mark.asyncio
async def test_research_recovery_prefers_a_new_final_redirect_domain(session):
    user_text = "Please give me an in-depth review of Canada's technology news today."
    redirect_url = "https://a.test/redirect"
    session.backend.web_search_fixtures = [
        [{"title": "Redirecting source", "url": redirect_url, "snippet": "Initial report."}],
        [
            {"title": "First recovery source", "url": "https://d.test/first", "snippet": "First host."},
            {"title": "Same final domain", "url": "https://b.test/second", "snippet": "Duplicate host."},
            {"title": "Independent source", "url": "https://c.test/independent", "snippet": "Independent host."},
        ],
        [],
    ]
    session.backend.web_fetch_final_urls[redirect_url] = "https://b.test/final"
    _dated_canadian_fetches(session)

    await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canada technology news"}}},
        ]}}],
        final_text="Here is the researched news summary.",
    )

    assert session.last_stream_payload is not None
    fetched_results = [
        json.loads(message["content"])
        for message in session.last_stream_payload["messages"]
        if message.get("role") == "tool" and message.get("name") == "web_fetch"
    ]
    final_domains = {
        re.match(r"https?://([^/]+)", str(result["url"])).group(1)
        for result in fetched_results
    }
    assert final_domains == {"b.test", "c.test", "d.test"}


@pytest.mark.asyncio
async def test_research_recovery_fetch_failure_still_reaches_successful_evidence(session):
    user_text = "Please give me an in-depth review of Canada's technology news today."
    failed_url = "https://failed.test/initial"
    later_url = "https://later.test/initial"
    recovery_url = "https://recovery.test/update"
    session.backend.web_search_fixtures = [
        [
            {"title": "Failed fetch", "url": failed_url, "snippet": "Unavailable report."},
            {"title": "Later candidate", "url": later_url, "snippet": "Available report."},
        ],
        [{"title": "Recovery evidence", "url": recovery_url, "snippet": "Recovery report."}],
        [{"title": "Third discovery", "url": "https://third.test/context", "snippet": "Context report."}],
    ]
    session.backend.web_fetch_failures.add(failed_url)

    _dated_canadian_fetches(session)
    await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canada technology news"}}},
        ]}}],
        final_text="Here is the researched news summary.",
    )

    profile = session.app.research_profile(user_text)
    fetch_calls = [arguments for name, arguments in session.backend.call_log if name == "web_fetch"]
    assert failed_url in [arguments["url"] for arguments in fetch_calls]
    assert later_url in [arguments["url"] for arguments in fetch_calls], "a failed first candidate must not stop later candidates"
    assert recovery_url in [arguments["url"] for arguments in fetch_calls], "recovery-search candidates must still be scheduled"
    assert len(session.backend.call_log) <= profile["max_calls"]

    assert session.last_stream_payload is not None
    successful_fetch_messages = [
        json.loads(message["content"])
        for message in session.last_stream_payload["messages"]
        if message.get("role") == "tool" and message.get("name") == "web_fetch"
        and json.loads(message["content"]).get("content")
    ]
    assert len(successful_fetch_messages) >= profile["minimum_fetches"], (
        "a failed fetch must not count as one of the required successful deep-research fetches"
    )
    assert any(message.get("url") == later_url for message in successful_fetch_messages)


@pytest.mark.asyncio
async def test_deep_news_model_calls_never_exceed_research_budget(session):
    user_text = "Please give me an in-depth review of Canada's technology news today."
    session.backend.web_search_fixtures = [
        [
            {"title": f"Source {index}A", "url": f"https://source-{index}-a.test/news", "snippet": "Report A."},
            {"title": f"Source {index}B", "url": f"https://source-{index}-b.test/news", "snippet": "Report B."},
        ]
        for index in range(8)
    ]

    def search_call(index: int) -> dict:
        return {"function": {"name": "web_search", "arguments": {"query": f"Canada technology news {index}"}}}

    await session.turn(
        user_text,
        ollama_script=[
            {"message": {"content": "", "tool_calls": [search_call(index)]}}
            for index in range(4)
        ] + [{"message": {"content": "", "tool_calls": [search_call(index) for index in range(4, 8)]}}],
        final_text="Here is the researched news summary.",
    )

    assert len(session.backend.call_log) <= session.app.research_profile(user_text)["max_calls"]


@pytest.mark.asyncio
async def test_deep_news_synthesis_prompt_allows_a_detailed_supported_roundup(session):
    """Removing the deep synthesis contract must make this prompt check fail."""
    user_text = "Please give me an in-depth review of Canada's technology news today."
    session.backend.web_search_fixtures = [
        [{"title": "Technology policy", "url": "https://policy.example/news", "snippet": "A policy development."}],
        [{"title": "Research funding", "url": "https://research.example/news", "snippet": "A research development."}],
        [{"title": "Industry", "url": "https://industry.example/news", "snippet": "An industry development."}],
    ]
    _dated_canadian_fetches(session)

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canada technology news"}}},
        ]}}],
        final_text="The supported developments include policy, research, and industry changes.",
    )

    assert reply == "The supported developments include policy, research, and industry changes."
    assert session.last_stream_payload is not None
    system_text = "\n".join(
        str(message.get("content") or "")
        for message in session.last_stream_payload["messages"]
        if message.get("role") == "system"
    )
    assert "The user explicitly requested depth" in system_text
    assert "several distinct supported developments" in system_text
    assert "multi-paragraph" in system_text


@pytest.mark.asyncio
async def test_deep_news_office_holder_prompt_prefers_fetched_evidence_to_a_snippet(session):
    """A stale snippet must not be eligible evidence for the current holder."""
    user_text = "Please give me an in-depth review of current Canadian government news."
    authoritative_url = "https://canada.gc.ca/government/current-holder"
    session.backend.web_search_fixtures = [
        [{"title": "Government update", "url": authoritative_url, "snippet": "Snippet Holder is the current office-holder."}],
        [{"title": "Policy update", "url": "https://parliament.example/policy", "snippet": "Parliamentary context."}],
        [{"title": "Regional update", "url": "https://regional.example/news", "snippet": "Regional context."}],
    ]
    session.backend.web_fetch_contents[authoritative_url] = "Fetched Holder is the current office-holder."

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "current Canadian government news"}}},
        ]}}],
        final_text="Fetched Holder is the current office-holder.",
    )

    assert reply == "Fetched Holder is the current office-holder."
    assert session.last_stream_payload is not None
    prompt_text = json.dumps(session.last_stream_payload["messages"])
    system_text = "\n".join(
        str(message.get("content") or "")
        for message in session.last_stream_payload["messages"]
        if message.get("role") == "system"
    )
    assert "Snippet Holder" not in prompt_text and "Fetched Holder" in prompt_text
    assert "Current office-holder claims require fetched evidence" in system_text


@pytest.mark.asyncio
async def test_deep_news_rejects_a_current_office_holder_repeated_only_from_a_snippet(session):
    """Repeating a stale snippet holder must fail after deep fetched-only grounding."""
    user_text = "Please give me an in-depth review of current Canadian government news."
    authoritative_url = "https://canada.gc.ca/government/current-holder"
    session.backend.web_search_fixtures = [
        [{"title": "Government update", "url": authoritative_url, "snippet": "Snippet Holder is the current office-holder."}],
        [{"title": "Policy update", "url": "https://parliament.example/policy", "snippet": "Parliamentary context."}],
        [{"title": "Regional update", "url": "https://regional.example/news", "snippet": "Regional context."}],
    ]
    session.backend.web_fetch_contents[authoritative_url] = "Fetched Holder is the current office-holder."

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "current Canadian government news"}}},
        ]}}],
        final_text="Snippet Holder is the current office-holder.",
    )

    assert reply == "I can't safely verify that current office-holder from the fetched evidence."
    assert session.last_stream_payload is not None
    assert "Snippet Holder" not in json.dumps(session.last_stream_payload["messages"])


@pytest.mark.asyncio
async def test_deep_news_removes_tool_call_prose_from_the_final_synthesis_prompt(session):
    """A dispatch message's prose must not carry a stale snippet into final synthesis."""
    user_text = "Please give me an in-depth review of current Canadian government news."
    authoritative_url = "https://canada.gc.ca/government/current-holder"
    session.backend.web_search_fixtures = [
        [{"title": "Government update", "url": authoritative_url, "snippet": "Search-result holder."}],
        [{"title": "Policy update", "url": "https://parliament.example/policy", "snippet": "Parliamentary context."}],
        [{"title": "Regional update", "url": "https://regional.example/news", "snippet": "Regional context."}],
    ]
    session.backend.web_fetch_contents[authoritative_url] = "Fetched Holder is the current office-holder."

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {
            "content": "Snippet Holder is the current office-holder.",
            "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "current Canadian government news"}}}],
        }}],
        final_text="Fetched Holder is the current office-holder.",
    )

    assert reply == "Fetched Holder is the current office-holder."
    assert session.last_stream_payload is not None
    final_messages = session.last_stream_payload["messages"]
    dispatch_messages = [
        message for message in final_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert dispatch_messages and all(message.get("content") == "" for message in dispatch_messages)
    assert "Snippet Holder" not in json.dumps(final_messages)


@pytest.mark.asyncio
async def test_deep_news_rejects_current_holder_when_fetched_text_only_names_a_former_holder(session):
    """A fetched name alone cannot support the asserted current office-holder role."""
    user_text = "Please give me an in-depth review of current Canadian government news."
    authoritative_url = "https://canada.gc.ca/government/current-holder"
    session.backend.web_search_fixtures = [
        [{"title": "Government update", "url": authoritative_url, "snippet": "Former Holder is the current office-holder."}],
        [{"title": "Policy update", "url": "https://parliament.example/policy", "snippet": "Parliamentary context."}],
        [{"title": "Regional update", "url": "https://regional.example/news", "snippet": "Regional context."}],
    ]
    session.backend.web_fetch_contents[authoritative_url] = (
        "Former Holder previously held the office. Fetched Holder is the current office-holder."
    )

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "current Canadian government news"}}},
        ]}}],
        final_text="Former Holder is the current office-holder.",
    )

    assert reply == "I can't safely verify that current office-holder from the fetched evidence."


@pytest.mark.asyncio
async def test_deep_news_rejects_an_attributed_false_report_about_a_current_holder(session):
    """An attributed, refuted report cannot establish the current office-holder."""
    user_text = "Please give me an in-depth review of current Canadian government news."
    false_report_url = "https://news-one.example/current-holder"
    second_url = "https://news-two.example/current-holder"
    third_url = "https://news-three.example/current-holder"
    session.backend.web_search_fixtures = [
        [{"title": "Claim report", "url": false_report_url, "snippet": "Former Holder is current."}],
        [{"title": "Department update", "url": second_url, "snippet": "Current-holder update."}],
        [{"title": "Background", "url": third_url, "snippet": "Government context."}],
    ]
    session.backend.web_fetch_contents[false_report_url] = (
        "A false report claimed Former Holder is the current office-holder; "
        "the department says Fetched Holder is. Fetched Holder is the current office-holder."
    )
    session.backend.web_fetch_contents[second_url] = "Fetched Holder is the current office-holder."

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "current Canadian government news"}}},
        ]}}],
        final_text="Former Holder is the current office-holder.",
    )

    assert reply == "I can't safely verify that current office-holder from the fetched evidence."


@pytest.mark.asyncio
async def test_deep_news_accepts_a_current_holder_corroborated_by_independent_fetched_sources(session):
    """Two independent direct fetched assertions may support the current holder."""
    user_text = "Please give me an in-depth review of current Canadian government news."
    first_url = "https://news-one.example/current-holder"
    second_url = "https://news-two.example/current-holder"
    third_url = "https://news-three.example/current-holder"
    session.backend.web_search_fixtures = [
        [{"title": "First current-holder report", "url": first_url, "snippet": "Current-holder update."}],
        [{"title": "Second current-holder report", "url": second_url, "snippet": "Independent current-holder update."}],
        [{"title": "Background", "url": third_url, "snippet": "Government context."}],
    ]
    session.backend.web_fetch_contents[first_url] = "Corroborated Holder is the current office-holder. The department announced new funding."
    session.backend.web_fetch_contents[second_url] = "Corroborated Holder is the current office-holder. Independent reporting examined the policy debate."

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "current Canadian government news"}}},
        ]}}],
        final_text="Corroborated Holder is the current office-holder.",
    )

    assert reply == "Corroborated Holder is the current office-holder."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_url,first_content,second_content,final_text,accepted",
    [
        pytest.param("https://canada.gc.ca/holder", "The claim that Alice Doe is the current office-holder was debunked.", "Unrelated economic context.", "Alice Doe is the current office-holder.", False, id="debunked-embedded-claim"),
        pytest.param("https://news-one.example/holder", "Unrelated political context.", "Unrelated economic context.", "Alice Doe is the current Foreign Minister.", False, id="arbitrary-role"),
        pytest.param("https://gov.example/holder", "Alice Doe is the current office-holder.", "Unrelated economic context.", "Alice Doe is the current office-holder.", False, id="fake-government-prefix"),
        pytest.param("https://news.gov.example/holder", "Alice Doe is the current office-holder.", "Unrelated economic context.", "Alice Doe is the current office-holder.", False, id="fake-government-infix"),
        pytest.param("https://canada.gc.ca.example/holder", "Alice Doe is the current office-holder.", "Unrelated economic context.", "Alice Doe is the current office-holder.", False, id="government-suffix-spoof"),
        pytest.param("https://canada.gc.ca/holder", '"Alice Doe is the current Foreign Minister."', "Unrelated economic context.", "Alice Doe is the current Foreign Minister.", False, id="quoted-claim"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister, a claim subsequently debunked.", "Unrelated economic context.", "Alice Doe is the current Foreign Minister.", False, id="refuted-suffix"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is not the current Foreign Minister.", "Unrelated economic context.", "Alice Doe is the current Foreign Minister.", False, id="negated-claim"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "Bob Roe is the current Foreign Minister.", "Alice Doe is the current Foreign Minister.", False, id="conflicting-holder"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "Bob Roe is the current Foreign Minister, following the election.", "Alice Doe is the current Foreign Minister.", False, id="conflicting-holder-with-context"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "Alice Doe is not the current Foreign Minister.", "Alice Doe is the current Foreign Minister.", False, id="contradictory-negation"),
        pytest.param("https://alias.news-two.example/holder", "Alice Doe is the current Foreign Minister.", "Alice Doe is the current Foreign Minister.", "Alice Doe is the current Foreign Minister.", False, id="same-publisher-subdomains"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "Unrelated economic context.", "Alice Doe is the current Foreign Minister.", True, id="safe-government"),
        pytest.param("https://department.gov.uk/holder", "Alice Doe is the current Foreign Minister.", "Unrelated economic context.", "Alice Doe is the current Foreign Minister.", True, id="safe-government-country-suffix"),
        pytest.param("https://news-one.example/holder", "Alice Doe is the current Foreign Minister.", "Alice Doe is current Foreign Minister.", "Alice Doe is the current Foreign Minister.", True, id="independent-domains"),
        pytest.param("https://news-one.example/holder", "Unrelated political context.", "Unrelated economic context.", "The current Foreign Minister is Alice Doe.", False, id="reverse-output-claim"),
        pytest.param("https://canada.gc.ca/holder", "Unrelated political context.", "Unrelated economic context.", "Alice Doe is the prime minister.", False, id="present-role-unrelated-evidence"),
        pytest.param("https://news-one.example/holder", "Alice Doe is the prime minister.", "Unrelated economic context.", "Alice Doe is the prime minister.", False, id="present-role-single-publisher"),
        pytest.param("https://alias.news-two.example/holder", "Alice Doe is the prime minister.", "Alice Doe is the prime minister.", "Alice Doe is the prime minister.", False, id="present-role-same-publisher"),
        pytest.param("https://gov.example/holder", "Alice Doe is the prime minister.", "Unrelated economic context.", "Alice Doe is the prime minister.", False, id="present-role-fake-government"),
        pytest.param("https://canada.gc.ca/holder", "A false report claimed:\nAlice Doe is the current office-holder.", "Unrelated economic context.", "Alice Doe is the current office-holder.", False, id="newline-preserves-attribution"),
        pytest.param("https://canada.gc.ca/holder", "A false report claimed\nAlice Doe is the current office-holder.", "Unrelated economic context.", "Alice Doe is the current office-holder.", False, id="bare-newline-preserves-attribution"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "The current Foreign Minister is Bob Roe, following the election.", "Alice Doe is the current Foreign Minister.", False, id="reverse-conflict-with-context"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the prime minister.", "The prime minister is Bob Roe, following the election.", "Alice Doe is the prime minister.", False, id="present-role-reverse-conflict"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the prime minister.", "Unrelated economic context.", "Alice Doe is the prime minister.", True, id="present-role-safe-government"),
        pytest.param("https://news-one.example/holder", "Alice Doe is the prime minister.", "Alice Doe is prime minister.", "Alice Doe is the prime minister.", True, id="present-role-independent-publishers"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the prime minister.", "Unrelated economic context.", "Alice Doe is the current prime minister.", True, id="present-evidence-supports-current-claim"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current prime minister.", "Unrelated economic context.", "Alice Doe is prime minister.", True, id="current-evidence-supports-present-claim"),
        pytest.param("https://canada.gc.ca/holder", "Background context.\nAlice Doe is the prime minister.", "Unrelated economic context.", "Alice Doe is the prime minister.", True, id="terminal-punctuation-starts-assertion"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "The current Foreign Minister is Bob Roe (following the election).", "Alice Doe is the current Foreign Minister.", False, id="reverse-conflict-parenthetical-context"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "The current Foreign Minister is Bob Roe — following the election.", "Alice Doe is the current Foreign Minister.", False, id="reverse-conflict-dash-context"),
        pytest.param("https://canada.gc.ca/holder", "Alice Doe is the current Foreign Minister.", "Unrelated economic context.", "The current Foreign Minister is Alice Doe.", True, id="reverse-output-supported-person-first"),
    ],
)
async def test_deep_news_current_role_evidence_boundary(
    session, first_url, first_content, second_content, final_text, accepted,
):
    """Weak relationship matching or hostname trust must not authorize speech."""
    second_url = "https://news-two.example/holder"
    session.backend.web_search_fixtures = [
        [{"title": "First update", "url": first_url, "snippet": "Government update."}],
        [{"title": "Second update", "url": second_url, "snippet": "Independent update."}],
        [{"title": "Background", "url": "https://news-three.example/context", "snippet": "Economic update."}],
    ]
    session.backend.web_fetch_contents[first_url] = first_content
    session.backend.web_fetch_contents[second_url] = second_content
    reply = await session.turn(
        "Please give me an in-depth review of current Canadian government news.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "current Canadian government news"}}},
        ]}}],
        final_text=final_text,
    )
    assert reply == (final_text if accepted else "I can't safely verify that current office-holder from the fetched evidence.")


@pytest.mark.asyncio
async def test_deep_news_incomplete_fetched_evidence_returns_limitation_without_synthesis(session):
    """Removing the readiness gate must make this return the canned model answer."""
    user_text = "Please give me an in-depth review of Canada's technology news today."
    fetched_url = "https://policy.example/news"
    failed_urls = {"https://research.example/news", "https://industry.example/news"}
    session.backend.web_search_fixtures = [
        [{"title": "Technology policy", "url": fetched_url, "snippet": "A policy development."}],
        [{"title": "Research funding", "url": "https://research.example/news", "snippet": "A research development."}],
        [{"title": "Industry", "url": "https://industry.example/news", "snippet": "An industry development."}],
    ]
    session.backend.web_fetch_failures.update(failed_urls)

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canada technology news"}}},
        ]}}],
        final_text="This canned model roundup must not be used.",
    )

    assert "still couldn't verify enough independent Canadian coverage" in reply
    assert session.last_stream_payload is None
    assert len([name for name, _ in session.backend.call_log if name == "web_search"]) == 4


@pytest.mark.asyncio
async def test_deep_news_empty_or_invalid_search_results_return_limitation_without_synthesis(session):
    """Three successful searches with no fetchable article must not permit a roundup."""
    user_text = "Please give me an in-depth review of Canada's technology news today."
    session.backend.web_search_fixtures = [
        [{"title": "Malformed result", "url": "not-a-url", "snippet": "No article URL."}],
        [],
        [{"title": "Unsupported scheme", "url": "ftp://example.test/news", "snippet": "No HTTP article URL."}],
    ]

    reply = await session.turn(
        user_text,
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canada technology news"}}},
        ]}}],
        final_text="This canned model roundup must not be used when no article was fetched.",
    )

    assert "still couldn't verify enough independent Canadian coverage" in reply
    assert session.last_stream_payload is None
    assert len([name for name, _ in session.backend.call_log if name == "web_search"]) == 4
    assert not any(name == "web_fetch" for name, _ in session.backend.call_log)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_count", [1, 6])
async def test_research_recovery_retries_failed_searches_until_three_succeed(session, failure_count):
    session.backend.web_search_fixtures = [None] * failure_count + [
        [{"url": "https://one.example/story"}],
        [{"url": "https://two.example/story"}],
        [],
    ]
    reply = await session.turn(
        "Give me an in-depth review of current Canadian news.",
        ollama_script=[{"message": {"tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canadian news"}}},
        ]}}],
        final_text="The fetched stories describe two developments.",
    )
    assert reply == "The fetched stories describe two developments."
    searches = [args for name, args in session.backend.call_log if name == "web_search"]
    assert len(searches) == failure_count + 3
    assert len(session.backend.call_log) <= 16


@pytest.mark.asyncio
async def test_research_recovery_keeps_untried_candidates_after_two_failed_fetches(session):
    urls = [f"https://source-{index}.example/article" for index in range(4)]
    session.backend.web_search_fixtures = [[{"url": url} for url in urls], [], []]
    session.backend.web_fetch_failures.update(urls[:2])
    reply = await session.turn(
        "Give me an in-depth review of current Canadian news.",
        ollama_script=[{"message": {"tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canadian news"}}},
        ]}}],
        final_text="The fetched stories describe two developments.",
    )
    assert reply == "The fetched stories describe two developments."
    assert [args["url"] for name, args in session.backend.call_log if name == "web_fetch"] == urls
    assert len(session.backend.call_log) <= 16


@pytest.mark.asyncio
async def test_world_news_late_successes_keep_fetched_links_after_eleven_failures(session):
    failed = [f"https://failed-{index}.example/story" for index in range(11)]
    good = ["https://one.example/story", "https://two.example/story"]
    session.backend.web_search_fixtures = [
        [{"url": url} for url in failed], [{"url": good[0]}], [{"url": good[1]}],
    ]
    session.backend.web_fetch_failures.update(failed)
    reply = await session.turn(
        "Give me an in-depth review of world news today.",
        ollama_script=[{"message": {"tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "world news today"}}},
        ]}}],
        final_text="The fetched stories describe two world developments.",
    )
    assert reply == "The fetched stories describe two world developments."
    assert len(session.backend.call_log) == 16
    assert session.backend.call_log[13][0] == "web_fetch"
    assert session.backend.call_log[13][1]["url"] == good[0]
    assert session.backend.call_log[15][0] == "web_fetch"
    assert session.backend.call_log[15][1]["url"] == good[1]
    assert session.last_stream_payload is not None
    trace = next(item["entries"] for item in reversed(session.ws.sent) if item.get("type") == "trace")
    assert len(trace) <= 12
    assert [source["url"] for item in trace for source in item["sources"] if source["kind"] == "fetched"] == good
    footer = session.app.openai_tool_trace_footer(trace)
    assert all(f"]({url})" in footer for url in good)
    assert "fixture fetch failure" not in footer


@pytest.mark.asyncio
@pytest.mark.parametrize("model_fetch", [False, True])
async def test_deep_news_retains_search_date_when_fetch_has_no_publication_date(session, model_fetch):
    old_url = "https://canada.gc.ca/announcement"
    session.backend.web_search_fixtures = [
        [{"url": old_url, "date": "2015-10-19"}],
        [{"url": "https://independent.example/news"}],
        [],
    ]
    session.backend.web_fetch_contents[old_url.split("#")[0]] = "Alice Doe is the prime minister."
    reply = await session.turn(
        "Give me an in-depth review of current Canadian news.",
        ollama_script=[{"message": {"tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Canadian news"}}},
        ]}}] + ([{"message": {"tool_calls": [
            {"function": {"name": "web_fetch", "arguments": {"url": old_url}}},
        ]}}] if model_fetch else []),
        final_text="Alice Doe is the prime minister.",
    )
    assert reply == "I can't safely verify that current office-holder from the fetched evidence."
    fetched = [json.loads(message["content"]) for message in session.last_stream_payload["messages"]
               if message.get("role") == "tool" and message.get("name") == "web_fetch"]
    assert next(item for item in fetched if item["url"] == old_url.split("#")[0])["date"] == "2015-10-19"


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["Explain photosynthesis in-depth.", "Explain cellular respiration in-depth."])
async def test_deep_news_gate_does_not_apply_to_general_depth_requests(session, prompt):
    answer = "Plants convert light into chemical energy through a sequence of reactions."
    reply = await session.turn(prompt, ollama_script=[], final_text=answer)
    assert reply == answer
    assert not any(name in {"web_search", "web_fetch"} for name, _ in session.backend.call_log)
    system_text = "\n".join(str(message.get("content") or "") for message in session.last_stream_payload["messages"]
                            if message.get("role") == "system")
    assert "The user explicitly requested depth" in system_text
    assert "several distinct supported developments" not in system_text
    assert "Current office-holder claims require fetched evidence" not in system_text


# --- Real production transcript replay (UnresolvedSubject / relevance gate) -
# Reproduces the exact reported failure: capability discovery/Qwen picked
# get_storage_status + plex_search for a movie-identification question and
# answered with storage capacity; a later "It's a movie from 2003" did not
# refine the same subject; "search for it online" reused a stale Canada-news
# web topic instead of The Room. Production (Home-AI-Assistant sha-9edf900,
# Home-AI-Tools sha-b0d1f99, confirmed via `docker inspect` on the live
# containers) predates this entire subject/offer/UnresolvedSubject
# architecture -- this replay is against the merged worktree state, not a
# claim about exactly reproducing the older production code path.

@pytest.mark.asyncio
async def test_real_production_transcript_replay(session):
    # Deliberately NOT seeded yet -- the real transcript's media_plan_goal
    # call genuinely found nothing for a bare "The Room" search. It becomes
    # discoverable only once the year is known, added right before the
    # enrichment turn below, so this test proves the RETRY mechanism (an
    # enriched query succeeding where the bare title failed), not a fake
    # that was always going to match regardless of enrichment.

    # Turns 1-5: weather -> Frigate -> Frigate followup -> Frigate activity
    # -> Canada news. Establishes a stale web topic and stale camera context
    # before the media conversation begins, matching the real transcript.
    await session.turn(
        "What's the weather today?",
        ollama_script=[{"message": {"content": "", "tool_calls": [{"function": {"name": "weather_forecast", "arguments": {}}}]}}],
        final_text="It's sunny and 68 degrees today.",
    )
    await session.turn(
        "Any recent camera events?",
        ollama_script=[{"message": {"content": "", "tool_calls": [{"function": {"name": "frigate_snapshot", "arguments": {"camera": "front_door"}}}]}}],
        final_text="Nothing at the front door right now.",
    )
    await session.turn(
        "What's the top news stories today in Canada?",
        ollama_script=[{"message": {"content": "", "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": "top news stories Canada today"}}}]}}],
        final_text="Canada's top story today is about the upcoming budget.",
    )
    stale_web_topic = session.app.conversation_context.get(session.client_id, {}).get("latest_resolved_referent")
    assert stale_web_topic and "canada" in stale_web_topic.casefold(), "setup sanity check: a stale web referent must exist before the media conversation starts"

    # Turn 6: "Do you know the movie The Room?" -- reproduce the exact
    # reported bad tool selection (get_storage_status + plex_search, no
    # media_plan_goal) and assert the irrelevant result cannot ground the
    # answer regardless.
    reply = await session.turn(
        "Do you know the movie The Room?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "get_storage_status", "arguments": {}}},
            {"function": {"name": "plex_search", "arguments": {"query": "The Room"}}},
        ]}}],
        final_text="Sorry, I don't have that information.",
    )
    # The fake Ollama stream always returns final_text verbatim regardless of
    # what evidence it was shown, so asserting on `reply` alone would not
    # exercise the relevance gate at all -- inspect the actual payload
    # respond() built for final synthesis instead (see
    # _FakeOllamaClient.stream's last_stream_payload capture).
    assert session.last_stream_payload is not None, "this turn must reach final synthesis (no deterministic single-tool shortcut consumed it)"
    synthesis_text = json.dumps(session.last_stream_payload.get("messages", []))
    assert "15300000000000" not in synthesis_text and "179100000000" not in synthesis_text, (
        "get_storage_status's result must never reach the evidence shown to final synthesis for a media-identification question"
    )
    context_after_discovery = session.app.conversation_context.get(session.client_id, {})
    assert "room" in (context_after_discovery.get("latest_resolved_referent") or "").casefold(), (
        "The Room must become the referent even though canonical identity was not established this turn"
    )
    assert context_after_discovery.get("domain") != "camera" and context_after_discovery.get("domain") != "weather"

    # Turn 7: "I want to request a movie called The Room." -- media_plan_goal
    # genuinely fails to identify (fake backend has no non-enriched match by
    # design, matching the real transcript's reported failure), and this is
    # where the structured UnresolvedSubject must actually get staged.
    await session.turn(
        "I want to request a movie called The Room.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Room"}}},
        ]}}],
    )
    unresolved = session.app.conversation_context.get(session.client_id, {}).get("latest_unresolved_subject")
    assert unresolved is not None, "failed resolution must not erase the subject"
    assert unresolved["title_or_name"].casefold() == "the room"
    assert session.client_id not in session.app.pending, "a failed identification must never become a write confirmation"

    # Turn 8: "It's a movie from 2003" -- must ENRICH the existing unresolved
    # subject and retry resolution, not route as an independent fresh query.
    # Only now does the title become discoverable (matching the real
    # transcript: the year is what let metadata search actually find it).
    session.backend.seed_web("The Room", media_type="movie", year="2003", tmdb_id="17181")
    calls_before = len(session.backend.call_log)
    await session.turn("It's a movie from 2003")
    plan_calls_this_turn = [args for name, args in session.backend.call_log[calls_before:] if name == "media_plan_goal"]
    assert plan_calls_this_turn, "the enrichment reply must retry media_plan_goal"
    assert "2003" in str(plan_calls_this_turn[-1].get("goal", "")), "the retry must carry the year hint forward"
    assert "room" in str(plan_calls_this_turn[-1].get("goal", "")).casefold(), "the retry must still carry the original title"
    context_after_enrichment = session.app.conversation_context.get(session.client_id, {})
    assert context_after_enrichment.get("latest_unresolved_subject") is None, "successful enrichment must promote, not leave, the unresolved subject"
    assert "room" in (context_after_enrichment.get("canonical_identity") or {}).get("title", "").casefold()
    assert session.client_id not in session.app.pending, "identification succeeding is not itself a write confirmation"

    # Turn 9: "Can you search for it on the internet?" -- must search The
    # Room, never fall back to the stale Canada-news topic from turn 3.
    reply9 = await session.turn(
        "Can you search for it on the internet?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "The Room 2003 movie"}}},
        ]}}],
        final_text="The Room (2003) is a cult classic directed by and starring Tommy Wiseau.",
    )
    web_calls = [args for name, args in session.backend.call_log if name == "web_search"]
    assert "room" in str(web_calls[-1].get("query", "")).casefold(), "the web search must target The Room, not the stale Canada-news topic"
    assert "canada" not in str(web_calls[-1].get("query", "")).casefold()
    assert "canada" not in reply9.casefold(), "the response must not resurrect the stale Canada-news topic"


@pytest.mark.asyncio
async def test_discovery_question_never_selects_irrelevant_tools_for_multiple_media_types(session):
    """Spec item #8's generalized regression (not a single "The Room"
    special case): a discovery-shaped media question, across media types,
    must never let an irrelevant server/weather/camera tool ground the
    answer even if one is somehow invoked."""
    scenarios = [
        ("Do you know the movie Moon?", "Moon", "movie"),
        ("Do you know the show Dark?", "Dark", "tv"),
        ("Do you know the album Thriller?", "Thriller", "album"),
        ("Do you know Cowboy Bebop?", "Cowboy Bebop", "anime"),
        ("Do you know something called Severance?", "Severance", "tv"),
    ]
    for utterance, title, media_type in scenarios:
        session.app.conversation_context.pop(session.client_id, None)
        session.backend.seed_web(title, media_type=media_type, tmdb_id=str(uuid.uuid4().int)[:6])
        reply = await session.turn(
            utterance,
            ollama_script=[{"message": {"content": "", "tool_calls": [
                {"function": {"name": "get_storage_status", "arguments": {}}},
                {"function": {"name": "media_plan_goal", "arguments": {"goal": title}}},
            ]}}],
            final_text=f"{title} is a {media_type}.",
        )
        assert "terabyte" not in reply.casefold() and "free on your" not in reply.casefold(), f"{utterance!r} must not ground on get_storage_status"


# --- Second real production transcript replay (stale-production, items #8-12)
# Reproduces: "Can you request the movie The Room?" (with an actually-correct
# canonical match this time -- the TOOLS-level "Room" false-positive bug is
# regression-tested directly in tools/test_media_goal_title_extraction.py,
# since this conversation harness's FakeToolsBackend does its own simplified
# title matching and never exercises the real _media_goal_parts regex bug),
# then a follow-up correction, an explicit storage-topic switch, and a
# return to the media subject -- all through the real respond() path.

@pytest.mark.asyncio
async def test_storage_topic_switch_and_return_to_media_subject(session):
    session.backend.seed_library("The Room", media_type="movie", year="2003", tmdb_id="17181", state="AVAILABLE_IN_PLEX")

    # Turn 1: explicit request -- resolves confidently (fake backend has an
    # exact, unambiguous match) and reports existing availability rather than
    # planning a new request.
    reply1 = await session.turn(
        "Can you request the movie The Room?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Room", "media_type": "movie"}}},
        ]}}],
    )
    assert "storage" not in reply1.casefold() and "terabyte" not in reply1.casefold()
    assert "weather" not in reply1.casefold()

    # Turn 2: follow-up correction/reinforcement -- must not call
    # get_storage_status, must not abandon the media subject.
    calls_before = len(session.backend.call_log)
    reply2 = await session.turn("No, I mean a movie called The Room.")
    new_calls = session.backend.call_log[calls_before:]
    assert not any(name == "get_storage_status" for name, _ in new_calls)
    context_after_correction = session.app.conversation_context.get(session.client_id, {})
    assert "room" in (context_after_correction.get("latest_resolved_referent") or "").casefold()

    # Turn 3: explicit new storage question -- must get a real storage
    # answer, never "Room is ready in Plex" or any media-grounded response.
    reply3 = await session.turn(
        "What's using up most of the space in the cache?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "get_storage_status", "arguments": {}}},
        ]}}],
        final_text="Your cache is mostly used by the downloads share.",
    )
    assert "room" not in reply3.casefold() and "plex" not in reply3.casefold(), (
        "an explicit new storage question must never be answered with stale media context"
    )
    storage_calls = [args for name, args in session.backend.call_log if name == "get_storage_status"]
    assert storage_calls, "the explicit storage question must actually invoke get_storage_status"

    # Turn 4: return to media -- explicit intent must recover the subject,
    # not get hijacked by the just-established storage domain, and an
    # irrelevant get_storage_status result (if somehow invoked) must not be
    # eligible to ground this answer either.
    reply4 = await session.turn(
        "Can you request The Room by Tommy Wiseau?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "get_storage_status", "arguments": {}}},
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Room", "media_type": "movie"}}},
        ]}}],
    )
    assert session.last_stream_payload is None or "179100000000" not in json.dumps(session.last_stream_payload.get("messages", [])), (
        "get_storage_status must not ground the returned-to-media answer even if accidentally invoked"
    )
    plan_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert plan_calls and "room" in str(plan_calls[-1].get("goal", "")).casefold()
    assert "terabyte" not in reply4.casefold() and "cache" not in reply4.casefold()


# --- Query-drift guardrail, conversation level (fresh fixture, not "The Room") -

@pytest.mark.asyncio
async def test_query_drift_asks_for_clarification_instead_of_silent_wrong_match(session):
    """A deliberately-planted title-mangling bug (goal "The Beacon" somehow
    resolving to a lone candidate titled "Beach Party") would, on the real
    tools/server-tools-app.py side, be caught by the query-drift guardrail
    and returned as ambiguous=True/QUERY_DRIFT rather than a confident
    canonical_identity. This test proves the ASSISTANT side of that contract:
    given that shape, it must ask a clarifying question, never silently
    proceed as if it had confidently identified anything."""
    session.backend.simulate_drift_for = "The Beacon"
    session.backend.drift_candidate_title = "Beach Party"
    session.backend.drift_candidate_year = "1963"

    reply = await session.turn(
        "Can you request the movie The Beacon?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Beacon", "media_type": "movie"}}},
        ]}}],
    )
    assert "beach party" in reply.casefold() or "which one" in reply.casefold() or "mean" in reply.casefold(), (
        "a drifted match must produce a clarifying question naming the uncertain candidate, not a silent answer"
    )
    assert session.client_id not in session.app.pending, "a drifted match must never become a write confirmation"
    assert not session.backend.submitted_writes
    disambiguation = session.app.conversation_context.get(session.client_id, {}).get("pending_disambiguation")
    assert disambiguation is not None, "the existing disambiguation machinery must be reused, not a second path"
    assert disambiguation["candidates"][0]["title"] == "Beach Party"

    # A bare "yes" must not silently accept the uncertain candidate either --
    # same never-guess discipline as any other disambiguation.
    calls_before = len(session.backend.call_log)
    reply2 = await session.turn("Yeah.")
    assert len(session.backend.call_log) == calls_before, "an unresolved reply must not invoke any tool"
    assert session.client_id not in session.app.pending


class _FakeGatewayRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


class _GatewayStream:
    """Drive real ASGI sends; HTTPX's ASGITransport buffers streaming bodies."""

    def __init__(self, app, body, headers=(), *, spec_version="2.0", fail_content_send=False, hold_content_send=None):
        self.app = app
        self.body = body
        self.headers = headers
        self.spec_version = spec_version
        self.fail_content_send = fail_content_send
        self.hold_content_send = hold_content_send
        self.output = asyncio.Queue()
        self.disconnected = asyncio.Event()
        self.messages = []

    async def __aenter__(self):
        self.request_sent = False

        async def receive():
            if not self.request_sent:
                self.request_sent = True
                return {"type": "http.request", "body": json.dumps(self.body).encode(), "more_body": False}
            await self.disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            self.messages.append(message)
            if self.fail_content_send and b'"content":' in message.get("body", b""):
                raise OSError("connection closed")
            if self.hold_content_send is not None and b'"content":' in message.get("body", b""):
                await self.hold_content_send.wait()
            await self.output.put(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": self.spec_version},
                 "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
                 "query_string": b"", "headers": [(b"authorization", b"Bearer qa-only"), *self.headers],
                 "server": ("test", 80), "client": ("test", 1)}
        self.task = asyncio.create_task(self.app(scope, receive, send))
        return self

    async def next_message(self):
        try:
            return await asyncio.wait_for(self.output.get(), 1)
        except TimeoutError:
            pytest.fail("OpenAI stream did not send a frame while the tool was blocked")

    async def __aexit__(self, *args):
        self.disconnected.set()
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(self.task, 1)
        finally:
            if not self.task.done():
                self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task


def _openai_stream_body(**extra):
    return {"model": "home-ai", "stream": True,
            "messages": [{"role": "user", "content": "Check a source."}], **extra}


def _stream_frames(messages):
    wire = b"".join(m.get("body", b"") for m in messages).decode()
    return [json.loads(line[6:]) if line != "data: [DONE]" else "[DONE]"
            for line in wire.splitlines() if line.startswith("data: ")]


def _stream_content(messages):
    return "".join(frame["choices"][0]["delta"].get("content", "")
                   for frame in _stream_frames(messages) if isinstance(frame, dict))


@pytest.fixture
def gateway_tool(app, monkeypatch):
    import httpx

    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup = {"delay": 0.01}
    calls = []
    payload = {"tool": "web_fetch", "status": "ok", "transport_ok": True, "operation_ok": True,
               "result": {"title": "Private result title", "content": "private result body"}}
    response_status = {"code": 200}

    class ToolClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            cleanup_started.set()
            await asyncio.sleep(cleanup["delay"] if args[0] is asyncio.CancelledError else 0)
            closed.set()

        async def post(self, url, json, headers):
            assert url == f"{app.TOOLS_URL}/invoke"
            calls.append(json)
            entered.set()
            await release.wait()
            return httpx.Response(response_status["code"], json=payload, request=httpx.Request("POST", url))

    async def respond(sink, client_id, request_id, user_text):
        result = await app.invoke_tool("web_fetch", {"url": "https://www.cbc.ca/private?token=secret"}, client_id, request_id)
        assert result == payload
        await sink.send_json({"type": "text", "text": "A verified answer.", "request_id": request_id})

    monkeypatch.setattr(app, "OPENAI_COMPAT_API_KEY", "qa-only")
    monkeypatch.setattr(app, "OPENAI_COMPAT_API_KEY_FILE", "")
    monkeypatch.setattr(app, "httpx", SimpleNamespace(AsyncClient=ToolClient))
    monkeypatch.setattr(app, "respond", respond)
    monkeypatch.setattr(app, "discovery_audit", lambda event: None)
    return SimpleNamespace(entered=entered, release=release, closed=closed, payload=payload, calls=calls,
                           response_status=response_status, cleanup_started=cleanup_started, cleanup=cleanup)


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.0", "2.3", "2.4"])
async def test_openai_stream_sends_role_and_safe_progress_before_tool_completes(app, gateway_tool, spec_version):
    async with _GatewayStream(app.app, _openai_stream_body(), spec_version=spec_version) as stream:
        start = await stream.next_message()
        assert start["type"] == "http.response.start"
        assert start["status"] == 200
        headers = dict(start["headers"])
        assert headers[b"x-home-ai-session"].startswith(b"legacy:")
        assert headers[b"x-home-ai-request"].startswith(b"req-")
        role = await stream.next_message()
        assert _stream_frames([role])[0]["choices"][0]["delta"] == {"role": "assistant"}
        while "Reading CBC…" not in _stream_content(stream.messages):
            await stream.next_message()
        assert not gateway_tool.release.is_set()
        assert not gateway_tool.closed.is_set()
        await asyncio.wait_for(gateway_tool.entered.wait(), 1)
        assert gateway_tool.calls[0]["client_id"] == headers[b"x-home-ai-session"].decode()
        assert gateway_tool.calls[0]["turn_id"] == headers[b"x-home-ai-turn"].decode()
        assert gateway_tool.calls[0]["trace_id"] == headers[b"x-home-ai-trace"].decode()
        gateway_tool.release.set()
        await asyncio.wait_for(stream.task, 1)
    assert _stream_content(stream.messages) == "**Working**\n- Reading CBC…\n\n---\n\nA verified answer."
    frames = _stream_frames(stream.messages)
    assert sum(f == "[DONE]" for f in frames) == 1
    assert sum(isinstance(f, dict) and f["choices"][0]["finish_reason"] == "stop" for f in frames) == 1
    assert frames[-1] == "[DONE]"
    assert app.spoken_text_for_openai_display(_stream_content(stream.messages)) == "A verified answer."


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_openai_stream_disconnect_or_cancel_awaits_tool_cleanup(app, gateway_tool, cancel):
    previous_tasks = asyncio.all_tasks()
    async with _GatewayStream(app.app, _openai_stream_body()) as stream:
        await stream.next_message()
        await asyncio.wait_for(gateway_tool.entered.wait(), 1)
        if cancel:
            stream.task.cancel()
        else:
            stream.disconnected.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(stream.task, 1)
        assert gateway_tool.closed.is_set()
        assert not gateway_tool.release.is_set()
    assert app.tts_suppressed.get() is False
    assert app.progress_sink_context.get() is None
    assert asyncio.all_tasks() == previous_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.0", "2.3", "2.4"])
async def test_openai_stream_send_disconnect_awaits_tool_cleanup(app, gateway_tool, spec_version):
    from starlette.requests import ClientDisconnect

    previous_tasks = asyncio.all_tasks()
    with pytest.raises(ClientDisconnect if spec_version == "2.4" else OSError):
        async with _GatewayStream(app.app, _openai_stream_body(), spec_version=spec_version, fail_content_send=True) as stream:
            await asyncio.wait_for(stream.task, 1)
    assert gateway_tool.closed.is_set()
    assert not gateway_tool.release.is_set()
    assert asyncio.all_tasks() == previous_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.0", "2.3", "2.4"])
@pytest.mark.parametrize("shutdown", ["disconnect", "cancel", "disconnect_then_cancel"])
async def test_openai_stream_send_error_then_disconnect_preserves_http_cleanup(
    app, gateway_tool, spec_version, shutdown,
):
    from starlette.requests import ClientDisconnect

    gateway_tool.cleanup["delay"] = 0.05
    previous_tasks = asyncio.all_tasks()
    cleanup_complete_at_return = None
    with contextlib.suppress(asyncio.CancelledError, OSError, ClientDisconnect):
        async with _GatewayStream(
            app.app, _openai_stream_body(), spec_version=spec_version, fail_content_send=True,
        ) as stream:
            await asyncio.wait_for(gateway_tool.cleanup_started.wait(), 1)
            assert gateway_tool.entered.is_set()
            assert not gateway_tool.closed.is_set()
            assert not gateway_tool.release.is_set()
            if shutdown == "cancel":
                stream.task.cancel()
            else:
                stream.disconnected.set()
                if shutdown == "disconnect_then_cancel":
                    # Let the receive supervisor begin joining the stream,
                    # then cancel the request while HTTP cleanup still runs.
                    await asyncio.sleep(0.01)
                    stream.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, OSError, ClientDisconnect):
                await asyncio.wait_for(stream.task, 1)
            cleanup_complete_at_return = gateway_tool.closed.is_set()
    assert cleanup_complete_at_return is True, "second shutdown signal interrupted send-error HTTP cleanup"
    assert asyncio.all_tasks() == previous_tasks
    assert app.progress_sink_context.get() is None
    assert "[DONE]" not in _stream_frames(stream.messages)


@pytest.mark.asyncio
async def test_openai_stream_coalesces_flood_without_blocking_tool_results(app, gateway_tool, monkeypatch):
    completed = asyncio.Event()
    client_reading = asyncio.Event()

    async def respond(sink, client_id, request_id, user_text):
        for tool in ["web_search"] * 100 + ["weather_forecast", "plex_search", "home_get_state", "future_tool"]:
            result = await app.invoke_tool(tool, {"query": "private query"}, client_id, request_id)
            assert result == gateway_tool.payload
        completed.set()
        await sink.send_json({"type": "text", "text": "Done."})

    monkeypatch.setattr(app, "respond", respond)
    gateway_tool.release.set()
    async with _GatewayStream(app.app, _openai_stream_body(), hold_content_send=client_reading) as stream:
        await asyncio.wait_for(completed.wait(), 1)
        client_reading.set()
        await asyncio.wait_for(stream.task, 1)
    content = _stream_content(stream.messages)
    assert content == "**Working**\n- Searching the web…\n- Checking the forecast…\n- Checking Plex…\n- Checking your home…\n\n---\n\nDone."
    assert len(_stream_frames(stream.messages)) <= 9


@pytest.mark.asyncio
async def test_openai_stream_failure_is_generic_and_terminates(app, gateway_tool, monkeypatch):
    async def respond(*args):
        raise RuntimeError("private stack token=secret")

    monkeypatch.setattr(app, "respond", respond)
    async with _GatewayStream(app.app, _openai_stream_body()) as stream:
        await asyncio.wait_for(stream.task, 1)
    assert _stream_content(stream.messages) == "Home-AI could not complete this request."
    assert _stream_frames(stream.messages)[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_openai_stream_timeout_closes_tool_and_finishes(app, gateway_tool, monkeypatch):
    monkeypatch.setattr(app, "OPENAI_STREAM_TIMEOUT_SECONDS", 0.05)
    async with _GatewayStream(app.app, _openai_stream_body()) as stream:
        await asyncio.wait_for(stream.task, 1)
    assert gateway_tool.closed.is_set()
    assert _stream_content(stream.messages).endswith("\n---\n\nHome-AI could not complete this request.")
    assert _stream_frames(stream.messages)[-1] == "[DONE]"


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.0", "2.3", "2.4"])
@pytest.mark.parametrize("cancel_request", [False, True])
async def test_openai_stream_disconnect_during_timeout_preserves_http_cleanup(
    app, gateway_tool, monkeypatch, spec_version, cancel_request,
):
    monkeypatch.setattr(app, "OPENAI_STREAM_TIMEOUT_SECONDS", 0.02)
    gateway_tool.cleanup["delay"] = 0.05
    previous_tasks = asyncio.all_tasks()
    async with _GatewayStream(app.app, _openai_stream_body(), spec_version=spec_version) as stream:
        await asyncio.wait_for(gateway_tool.cleanup_started.wait(), 1)
        assert gateway_tool.entered.is_set()
        assert not gateway_tool.closed.is_set()
        assert not gateway_tool.release.is_set()
        if cancel_request:
            stream.task.cancel()
        else:
            stream.disconnected.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(stream.task, 1)
        assert gateway_tool.closed.is_set(), "disconnect interrupted timeout-triggered HTTP cleanup"
        assert _stream_content(stream.messages) == "**Working**\n- Reading CBC…\n"
        assert "[DONE]" not in _stream_frames(stream.messages)
    assert asyncio.all_tasks() == previous_tasks
    assert app.progress_sink_context.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.0", "2.3", "2.4"])
async def test_openai_stream_idle_disconnect_supervises_blocked_tool(app, gateway_tool, spec_version):
    previous_tasks = asyncio.all_tasks()
    async with _GatewayStream(app.app, _openai_stream_body(), spec_version=spec_version) as stream:
        while "Reading CBC…" not in _stream_content(stream.messages):
            await stream.next_message()
        assert not gateway_tool.release.is_set()
        stream.disconnected.set()
        completed, _ = await asyncio.wait({stream.task}, timeout=0.25)
        # Keep a deliberately failing implementation from leaking test tasks.
        if not completed:
            stream.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream.task
        assert completed, "idle disconnect was not observed while the tool was blocked"
        assert gateway_tool.closed.is_set()
        assert _stream_content(stream.messages) == "**Working**\n- Reading CBC…\n"
        assert "[DONE]" not in _stream_frames(stream.messages)
    assert asyncio.all_tasks() == previous_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["projection", "sink"])
async def test_tool_result_survives_progress_failure(app, gateway_tool, monkeypatch, stage):
    import progress_events

    events = []

    async def sink(event):
        events.append(event)
        raise RuntimeError("private sink failure")

    if stage == "projection":
        def bad_projection(*args):
            raise RuntimeError("private projection failure")
        monkeypatch.setattr(progress_events, "safe_progress_event", bad_projection)
    token = progress_events.progress_sink_context.set(sink)
    gateway_tool.release.set()
    try:
        result = await app.invoke_tool("web_fetch", {"url": "https://cbc.ca/private?token=secret"}, "client", "request")
    finally:
        progress_events.progress_sink_context.reset(token)
    assert result == gateway_tool.payload
    if stage == "sink":
        assert events == [{"phase": "tool_started", "label": "Reading CBC…"}, {"phase": "tool_finished", "label": "Complete"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["operation", "missing", "transport"])
async def test_tool_failures_emit_generic_progress_without_changing_outcome(app, gateway_tool, failure):
    events = []

    async def sink(event):
        events.append(event)

    if failure == "operation":
        gateway_tool.payload["operation_ok"] = False
        gateway_tool.payload["result"]["error"] = "private backend failure"
    else:
        gateway_tool.response_status["code"] = 404 if failure == "missing" else 503
    gateway_tool.release.set()
    token = app.progress_sink_context.set(sink)
    try:
        result = await app.invoke_tool("web_fetch", {"url": "http://server-tools/private?token=secret"}, "client", "request")
    finally:
        app.progress_sink_context.reset(token)
    assert result["operation_ok"] is False
    assert result["transport_ok"] is (failure != "transport")
    if failure == "operation":
        assert result == gateway_tool.payload
    assert events == [{"phase": "tool_started", "label": "Reading a source…"}, {"phase": "tool_failed", "label": "Tool unavailable"}]


@pytest.mark.asyncio
async def test_openai_nonstream_keeps_json_schema_without_progress(app, gateway_tool):
    gateway_tool.release.set()
    async with _GatewayStream(app.app, _openai_stream_body(stream=False)) as stream:
        await asyncio.wait_for(stream.task, 1)
    start = stream.messages[0]
    assert dict(start["headers"])[b"x-home-ai-session"].startswith(b"legacy:")
    body = json.loads(b"".join(m.get("body", b"") for m in stream.messages))
    assert body["object"] == "chat.completion"
    assert body["choices"] == [{"index": 0, "message": {"role": "assistant", "content": "A verified answer."}, "finish_reason": "stop"}]
    assert body["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_openai_gateway_keeps_rich_footer_and_plain_speech(app, gateway_tool, monkeypatch, streaming):
    gateway_tool.release.set()

    async def respond(sink, client_id, request_id, user_text):
        await app.invoke_tool("web_fetch", {"url": "https://cbc.ca/private?token=secret"}, client_id, request_id)
        await app.emit_trace(sink, request_id, [{"tool": "web_fetch", "status": "ok", "result": {
            "url": "https://cbc.ca/news/update?token=secret", "title": "Canada update",
        }}])
        await sink.send_json({"type": "text", "text": "A verified answer."})

    monkeypatch.setattr(app, "respond", respond)
    async with _GatewayStream(app.app, _openai_stream_body(stream=streaming)) as stream:
        await asyncio.wait_for(stream.task, 1)
    if streaming:
        display = _stream_content(stream.messages)
        assert display.startswith("**Working**\n- Reading CBC…\n\n---\n\nA verified answer.")
    else:
        body = json.loads(b"".join(m.get("body", b"") for m in stream.messages))
        display = body["choices"][0]["message"]["content"]
        assert display.startswith("A verified answer.")
        assert "**Working**" not in display
    assert "<!-- home-ai-display-trace -->" in display
    assert "[Canada update](https://cbc.ca/news/update)" in display
    assert "token=secret" not in display
    assert display.count("A verified answer.") == 1
    assert app.spoken_text_for_openai_display(display) == "A verified answer."


@pytest.mark.asyncio
async def test_openai_stream_housekeeping_has_no_progress_or_tool_calls(app, gateway_tool, monkeypatch):
    async def generate_final(messages):
        return '{"title": "Source Check"}'

    monkeypatch.setattr(app, "generate_final", generate_final)
    async with _GatewayStream(app.app, {**OPENWEBUI_TITLE_TASK, "stream": True}) as stream:
        await asyncio.wait_for(stream.task, 1)
    assert _stream_content(stream.messages) == '{"title": "Source Check"}'
    assert gateway_tool.calls == []
    assert _stream_frames(stream.messages)[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_openai_stream_progress_and_identity_are_request_local(app, gateway_tool, monkeypatch):
    gateway_tool.release.set()

    async def respond(sink, client_id, request_id, user_text):
        tool = "web_search" if client_id == "legacy:search" else "weather_forecast"
        await app.invoke_tool(tool, {"query": "private household query"}, client_id, request_id)
        await sink.send_json({"type": "text", "text": "Done."})

    monkeypatch.setattr(app, "respond", respond)
    async with _GatewayStream(app.app, _openai_stream_body(), [(b"x-home-ai-session-id", b"search")]) as first:
        async with _GatewayStream(app.app, _openai_stream_body(), [(b"x-home-ai-session-id", b"forecast")]) as second:
            await asyncio.wait_for(asyncio.gather(first.task, second.task), 1)
    assert _stream_content(first.messages) == "**Working**\n- Searching the web…\n\n---\n\nDone."
    assert _stream_content(second.messages) == "**Working**\n- Checking the forecast…\n\n---\n\nDone."
    assert app.progress_sink_context.get() is None


OPENWEBUI_TITLE_TASK = {"messages": [{"role": "user", "content": (
    "### Task:\nGenerate a concise, 3-5 word title with an emoji summarizing "
    "the chat history.\n### Chat History:\n<chat_history>\nUSER: do i have "
    "any movies on my server\n</chat_history>"
)}]}


@pytest.mark.asyncio
async def test_openwebui_housekeeping_request_never_reaches_respond(app, monkeypatch):
    """Real production bug: OpenWebUI's own internal title/tags/follow-up
    generation calls were fed verbatim into the real respond() pipeline,
    firing real tool calls against every backend service for no reason on
    every single chat message. This proves the gateway now detects that
    shape and answers directly (via generate_final, a single tool-free
    Ollama completion) instead of ever invoking respond()."""
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("respond() must never be invoked for an OpenWebUI housekeeping request")

    async def fake_generate_final(messages):
        assert messages == OPENWEBUI_TITLE_TASK["messages"]
        return '{"title": "Server Movie Check"}'

    monkeypatch.setattr(app, "respond", fail_if_called)
    monkeypatch.setattr(app, "generate_final", fake_generate_final)

    answer, client_id, trace = await app._openai_chat_turn(OPENWEBUI_TITLE_TASK, _FakeGatewayRequest())

    assert answer == '{"title": "Server Movie Check"}'
    assert trace == []


@pytest.mark.asyncio
async def test_genuine_chat_message_through_gateway_still_uses_the_real_pipeline(app, monkeypatch):
    """Negative control: a real user chat turn arriving through the same
    gateway endpoint must still flow through the full respond() pipeline,
    not be swept into the housekeeping bypass."""
    called = {"respond": False}

    async def fake_respond(sink, client_id, request_id, user_text):
        called["respond"] = True
        await sink.send_json({"type": "text", "text": "It is sunny today.", "request_id": request_id})

    async def fail_if_called(messages):
        raise AssertionError("generate_final() must not be used for a real chat turn")

    monkeypatch.setattr(app, "respond", fake_respond)
    monkeypatch.setattr(app, "generate_final", fail_if_called)

    body = {"messages": [{"role": "user", "content": "What's the weather like today?"}]}
    answer, client_id, trace = await app._openai_chat_turn(body, _FakeGatewayRequest())

    assert called["respond"] is True
    assert answer == "It is sunny today."


@pytest.mark.asyncio
async def test_gateway_join_collapses_duplicate_text_messages_across_emits(app, monkeypatch):
    """Real production bug: a real OpenWebUI request for
    'List the docker containers running right now.' came back as "You've
    got 50 containers running. You've got 50 containers running." --
    emit_answer() already collapses an adjacent duplicate WITHIN one call,
    but respond() emitted the same sentence as two SEPARATE text messages in
    one turn, and the gateway's naive "".join() reintroduced the exact same
    duplicate one level up. The join must apply the same collapse."""
    async def fake_respond(sink, client_id, request_id, user_text):
        await sink.send_json({"type": "text", "text": "You've got 50 containers running.", "request_id": request_id})
        await sink.send_json({"type": "text", "text": "You've got 50 containers running.", "request_id": request_id})

    monkeypatch.setattr(app, "respond", fake_respond)

    body = {"messages": [{"role": "user", "content": "List the docker containers running right now."}]}
    answer, client_id, trace = await app._openai_chat_turn(body, _FakeGatewayRequest())

    assert answer == "You've got 50 containers running."


# --- Generalized replay of a live production transcript (real bug: a stale --
# --- garbled title survived a fresh restatement, and a bare clarification ---
# --- answer got hijacked by an unrelated capability) -----------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("title,creator", [
    ("Interstellar", "Christopher Nolan"),
    ("Whiplash", "Damien Chazelle"),
])
async def test_fresh_title_restatement_replaces_stale_subject_and_bare_reply_resolves(session, title, creator):
    """Generalized version of a real live transcript (not hardcoded to one
    title -- run for two unrelated titles/creators to prove the fix is
    generic):

    1. An acquisition request for a title that fails to resolve stages a
       stale UnresolvedSubject.
    2. A full, clean restatement of a DIFFERENT, real title must REPLACE
       that stale subject rather than merging onto it or echoing the old
       garbled text back to the user.
    3. A later titleless request ("Can you request the movie?") stages a
       pending clarification.
    4. The very next turn -- a bare one-word reply that IS the title -- must
       be tried directly against that clarification instead of falling
       through to unrelated routing.
    """
    session.backend.seed_library(title, media_type="movie", state="IDENTIFIED", tmdb_id=hash(title) % 100000)

    # Turn 1: a request for something that does not exist -- stages a stale,
    # garbled UnresolvedSubject the way a real failed resolution would.
    reply1 = await session.turn("Can you get me the movie Zzyzx Nonexistent Title?")
    assert "zzyzx" in reply1.casefold() or "couldn't find" in reply1.casefold() or "confident" in reply1.casefold()
    stale = session.app.conversation_context.get(session.client_id, {}).get("latest_unresolved_subject")
    assert stale is not None, "a failed resolution must stage an UnresolvedSubject to enrich against"
    assert "zzyzx" in stale["title_or_name"].casefold()

    # Turn 2: a full, clean restatement of a real, different title. Must
    # REPLACE the stale "Zzyzx..." subject -- not merge a hint onto it, and
    # never echo "Zzyzx" back to the user.
    reply2 = await session.turn(f"Can you give me the movie {title} by {creator}?")
    assert "zzyzx" not in reply2.casefold(), "a fresh title restatement must never echo the old garbled subject back"
    assert session.backend.submitted_writes[-1]["arguments"]["confirmation_context"]["title"] == title
    last_goal_calls = [args for name, args in session.backend.call_log if name == "media_plan_goal"]
    assert "zzyzx" not in str(last_goal_calls[-1]).casefold(), "the retry must not carry the stale title forward"

    # Turn 3: a titleless request must ask a direct clarifying question and
    # stage a pending clarification (root cause #3's new mechanism).
    reply3 = await session.turn("Can you request the movie?")
    assert "what would you like me to look for" in reply3.casefold()
    clarification = session.app.conversation_context.get(session.client_id, {}).get("pending_title_clarification")
    assert clarification is not None, "the clarifying question must stage a pending_title_clarification, mirroring pending_offers/pending_disambiguation"

    # Turn 4: a bare one-word reply that IS the answer to that question must
    # be tried directly as the title -- not routed to an unrelated
    # capability, which is the exact live production bug this proves fixed.
    calls_before = len(session.backend.call_log)
    reply4 = await session.turn(title)
    assert "already" in reply4.casefold(), f"the resolved title was already requested, got: {reply4!r}"
    assert len(session.backend.submitted_writes) == 1
    new_calls = [args for name, args in session.backend.call_log[calls_before:] if name == "media_plan_goal"]
    assert new_calls, "the bare reply must be tried against media_plan_goal, not silently dropped"
    assert session.app.conversation_context.get(session.client_id, {}).get("pending_title_clarification") is None, (
        "the clarification must be consumed once answered"
    )


@pytest.mark.asyncio
async def test_unrelated_explicit_request_still_outranks_a_pending_title_clarification(session):
    """Same discipline as pending_offers/pending_disambiguation: a
    genuinely new, clearly-unrelated explicit request must outrank a stale
    clarification prompt rather than being force-fed into title resolution."""
    await session.turn("Can you request the movie?")
    assert session.app.conversation_context.get(session.client_id, {}).get("pending_title_clarification") is not None

    reply = await session.turn(
        "What's the weather like today?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "weather_forecast", "arguments": {"location": "Welland"}}},
        ]}}],
        final_text="It is 12 degrees in Welland.",
    )
    assert "movie" not in reply.casefold() and "title" not in reply.casefold()


# --- Target behavior: "Add The Thing." -> year ambiguity -> "the older ----
# --- one" -- reuses the EXISTING pending_disambiguation machinery ---------
# --- (test_disambiguation_followup_language_matrix already proves this   --
# --- for "Dune"; this is the literal user-specified example, kept        --
# --- separate for direct traceability to the requirement). ---------------

@pytest.mark.asyncio
async def test_add_the_thing_year_ambiguity_the_older_one(session):
    session.backend.seed_web("The Thing", media_type="movie", year="1982", tmdb_id="1091")
    session.backend.seed_web("The Thing", media_type="movie", year="2011", tmdb_id="60308")

    await session.turn(
        "Add The Thing.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Thing", "media_type": "movie"}}},
        ]}}],
    )
    disambiguation = session.app.conversation_context.get(session.client_id, {}).get("pending_disambiguation")
    assert disambiguation is not None
    assert {c.get("year") for c in disambiguation["candidates"]} == {"1982", "2011"}

    calls_before = len(session.backend.call_log)
    await session.turn("The older one.")
    plan_calls = [args for name, args in session.backend.call_log[calls_before:] if name == "media_plan_goal"]
    assert plan_calls and "1982" in str(plan_calls[-1].get("goal", "")), "\"older\" (comparative) must resolve like \"old\""
    assert len(session.backend.submitted_writes) == 1
    assert session.backend.submitted_writes[0]["arguments"]["canonical_external_id"] == "1091"
    assert session.client_id not in session.app.pending


# --- Tool fan-out check: a media clarification reply must not trigger ----
# --- global capability discovery for unrelated domains. ------------------

@pytest.mark.asyncio
async def test_disambiguation_reply_does_not_fan_out_to_unrelated_tools(session):
    """"the older one" must be consumed by the pending_disambiguation
    check BEFORE reaching discover_tools/Qwen at all -- a media
    clarification reply must never cause camera/weather/other tool
    discovery to run."""
    session.backend.seed_web("The Thing", media_type="movie", year="1982", tmdb_id="1091")
    session.backend.seed_web("The Thing", media_type="movie", year="2011", tmdb_id="60308")
    await session.turn(
        "Add The Thing.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Thing", "media_type": "movie"}}},
        ]}}],
    )
    calls_before = [name for name, _ in session.backend.call_log]
    await session.turn("The older one.")
    new_calls = [name for name, _ in session.backend.call_log[len(calls_before):]]
    assert new_calls == ["media_plan_goal", "media_standard_request"], new_calls


@pytest.mark.asyncio
async def test_pending_title_clarification_does_not_fan_out_to_unrelated_tools(session):
    await session.turn("Can you request the movie?")
    calls_before = len(session.backend.call_log)
    await session.turn("Interstellar")
    new_calls = [name for name, _ in session.backend.call_log[calls_before:]]
    assert new_calls == ["media_plan_goal"], f"a bare clarification reply must only touch media_plan_goal, got: {new_calls}"


# --- Cancellation and expiry -----------------------------------------------

@pytest.mark.asyncio
async def test_pending_title_clarification_can_be_cancelled_by_a_competing_domain(session):
    """A genuinely new, unrelated explicit request cancels the stale
    clarification rather than being force-fed into title resolution (the
    clarification itself is left in place for its own TTL, matching the
    offer/disambiguation precedent -- it is the NEW request that must not
    be swallowed)."""
    await session.turn("Can you request the movie?")
    assert session.app.conversation_context.get(session.client_id, {}).get("pending_title_clarification") is not None
    reply = await session.turn(
        "What's the weather like today?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "weather_forecast", "arguments": {"location": "Welland"}}},
        ]}}],
        final_text="It is 12 degrees in Welland.",
    )
    assert "movie" not in reply.casefold() and "title" not in reply.casefold()
    assert session.app.conversation_context.get(session.client_id, {}).get("pending_title_clarification") is not None, (
        "a stale clarification is left in place for its own TTL, same as the offer/disambiguation precedent -- "
        "it is the competing request that is not swallowed by it, not the clarification itself that is cleared"
    )


@pytest.mark.asyncio
async def test_pending_title_clarification_expires(session):
    await session.turn("Can you request the movie?")
    clarification = session.app.conversation_context.get(session.client_id, {}).get("pending_title_clarification")
    assert clarification is not None
    clarification["created_at"] = 0.0  # force expiry
    session.app.conversation_context[session.client_id]["pending_title_clarification"] = clarification

    calls_before = len(session.backend.call_log)
    await session.turn("Interstellar")
    assert session.app.conversation_context.get(session.client_id, {}).get("pending_title_clarification") is None
    # Expired -- the reply falls through to normal routing (which may
    # legitimately call some other tool, e.g. plex_search) instead of being
    # force-tried against media_plan_goal as an answer to the stale question.
    new_calls = [name for name, _ in session.backend.call_log[calls_before:]]
    assert "media_plan_goal" not in new_calls


@pytest.mark.asyncio
async def test_pending_disambiguation_expires(session):
    session.backend.seed_web("The Thing", media_type="movie", year="1982", tmdb_id="1091")
    session.backend.seed_web("The Thing", media_type="movie", year="2011", tmdb_id="60308")
    await session.turn(
        "Add The Thing.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Thing", "media_type": "movie"}}},
        ]}}],
    )
    disambiguation = session.app.conversation_context.get(session.client_id, {}).get("pending_disambiguation")
    disambiguation["created_at"] = 0.0
    session.app.conversation_context[session.client_id]["pending_disambiguation"] = disambiguation
    reply = await session.turn("The older one.")
    assert session.app.conversation_context.get(session.client_id, {}).get("pending_disambiguation") is None


# --- Clarification vs. confirmation: never the same concept ---------------

@pytest.mark.asyncio
async def test_discovery_clarification_does_not_authorize_media_write(session):
    """Selection after discovery supplies identity but no request authority."""
    session.backend.seed_web("The Thing", media_type="movie", year="1982", tmdb_id="1091")
    session.backend.seed_web("The Thing", media_type="movie", year="2011", tmdb_id="60308")
    await session.turn(
        "Do you know The Thing?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Thing", "media_type": "movie"}}},
        ]}}],
    )
    await session.turn("The older one.")
    assert not session.backend.submitted_writes
    action = session.app.pending.get(session.client_id)
    assert action is None


# --- Descriptive media discovery (real live production bug): a plain --
# --- question about a media item, described by person + plot rather   --
# --- than a known title, must reach media_plan_goal -- not the plex   --
# --- library-search dead end, and not the old "no matching live       --
# --- workflow" status short-circuit. Reproduced through the REAL      --
# --- respond()/preflight_plan deterministic path, not helper calls.   --

@pytest.mark.asyncio
async def test_read_only_media_identification_does_not_stage_an_acquisition_offer(session):
    """A descriptive identity question answers with the canonical title only.

    This catches the regression where the current MEDIA_DISCOVERY operation
    was stored in turn context but stage_media_offer() only examined stale
    operation fields, appending a Plex/request offer to an informational
    answer.
    """
    session.backend.seed_library(
        "White Chicks", media_type="movie", state="ABSENT", tmdb_id="12153", year="2004"
    )
    # The integration fake's resolver models catalog clue matching through
    # its person-index seam; this phrase is the reported descriptive clue.
    session.backend.seed_person("two cops", "White Chicks")

    reply = await session.turn(
        "What's that movie where two cops dress as blonde women?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "White Chicks"}}},
        ]}}],
    )

    assert "white chicks (2004)" in reply.casefold()
    assert not any(phrase in reply.casefold() for phrase in ("want me", "request", "plex", "availability"))
    assert session.client_id not in session.app.pending
    assert session.client_id not in session.app.pending_offers
    assert session.app.conversation_context[session.client_id]["canonical_identity"] == {
        "media_type": "movie", "title": "White Chicks", "tmdb_id": "12153", "year": "2004",
    }

@pytest.mark.asyncio
async def test_scenario_2_descriptive_question_reaches_media_plan_goal_not_plex_search(session):
    """Real production bug: "What's that Brad Pitt movie about fly fishing
    in Montana?" was deterministically routed to plex_search with the
    entire descriptive sentence as the literal library query (zero
    matches, "I couldn't find that ... in my library"). It must instead
    reach media_plan_goal so structured lookup and the web-discovery
    fallback get a chance."""
    reply = await session.turn("What's that Brad Pitt movie about fly fishing in Montana?")
    called = [name for name, _ in session.backend.call_log]
    assert "media_plan_goal" in called
    assert "plex_search" not in called
    assert "in my library" not in reply.casefold()


@pytest.mark.asyncio
async def test_scenario_3_descriptive_question_does_not_hit_old_status_dead_end(session):
    """Real production bug (0.06s canned response, confirmed via live
    trace): "What's that Tom Hanks movie where he's stuck on an island
    with a volleyball?" was classified MEDIA_STATUS purely because "stuck"
    collides with download-status vocabulary, triggering the
    no-matching-live-workflow short-circuit before Qwen or media_plan_goal
    were ever reached. It must now reach media_plan_goal instead."""
    reply = await session.turn("What's that Tom Hanks movie where he's stuck on an island with a volleyball?")
    called = [name for name, _ in session.backend.call_log]
    assert "media_plan_goal" in called
    assert "media_status" not in called
    assert "matching live workflow" not in reply.casefold()


@pytest.mark.parametrize("text", [
    "What's that Brad Pitt movie about fly fishing in Montana?",
    "What is that Brad Pitt movie about fly fishing in Montana?",
    "What's the name of the Brad Pitt movie where he fly fishes?",
    "Which Brad Pitt movie has fly fishing in Montana?",
    "Do you know the Brad Pitt movie where he fly fishes?",
    "Can you identify the Brad Pitt movie with fly fishing?",
])
@pytest.mark.asyncio
async def test_phrasing_variants_all_reach_media_plan_goal(session, text):
    """Item 10: several phrasings of the same descriptive question must
    all converge on the real identity resolver, none interpreted as
    workflow status or a bare library search."""
    await session.turn(text)
    called = [name for name, _ in session.backend.call_log]
    assert "media_plan_goal" in called, text
    assert "media_status" not in called, text


@pytest.mark.parametrize("text", [
    "I want the Brad Pitt fly fishing movie.",
    "Add the Brad Pitt movie where he fishes in Montana.",
    "Get me that Brad Pitt fishing movie.",
    "Request the movie with Brad Pitt and fly fishing.",
])
@pytest.mark.asyncio
async def test_request_variants_execute_bound_media_request_same_turn(session, text):
    """Explicit requests consume one planner binding without another approval."""
    session.backend.seed_person("brad pitt", "A River Runs Through It")
    session.backend.seed_library("A River Runs Through It", media_type="movie", state="ABSENT", tmdb_id="11202")
    await session.turn(text)
    called = [name for name, _ in session.backend.call_log]
    assert "media_plan_goal" in called, text
    assert len(session.backend.submitted_writes) == 1, text
    assert session.client_id not in session.app.pending


@pytest.mark.parametrize("discover_first,request_text", [
    (False, "Request Dune."),
    (False, "Add Dune."),
    (True, "Can you request it?"),
])
@pytest.mark.asyncio
async def test_explicit_media_request_preserves_server_binding(session, discover_first, request_text):
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631", year="2021")
    if discover_first:
        await session.turn("Do you know Dune?", ollama_script=[{"message": {"tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune"}}},
        ]}}])
        assert not session.backend.submitted_writes
    reply = await session.turn(request_text)
    assert len(session.backend.submitted_writes) == 1
    assert len(session.backend.media_execution_calls) == 1
    call = session.backend.media_execution_calls[0]
    record = session.backend.planner_confirmations[-1]
    assert call["confirmed"] is True
    assert call["action_id"] == record["confirmation_id"]
    assert call["session_id"] == session.client_id
    assert call["arguments"] == {
        "workflow_id": "wf-438631", "canonical_external_id": "438631", "media_type": "movie",
        "season_scope": [], "confirmation_context": record, "session_id": session.client_id,
    }
    assert call["arguments"]["confirmation_context"] is record
    assert "looking for it" in reply.casefold()
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_repeated_explicit_media_request_returns_executor_no_op(session):
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    await session.turn("Request Dune.")
    reply = await session.turn("Request Dune.")
    assert len(session.backend.media_execution_calls) == 2
    assert len(session.backend.submitted_writes) == 1
    assert "already" in reply.casefold() and "looking for it" not in reply.casefold()
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_explicit_media_request_already_in_plex_skips_executor(session):
    session.backend.seed_library("Dune", media_type="movie", state="AVAILABLE_IN_PLEX", tmdb_id="438631")
    reply = await session.turn("Request Dune.")
    assert "already" in reply.casefold() and "plex" in reply.casefold()
    assert not session.backend.media_execution_calls
    assert not session.backend.submitted_writes
    assert session.client_id not in session.app.pending


@pytest.mark.parametrize("request_text", ["Request Dune.", "Do you know Dune?"])
@pytest.mark.asyncio
async def test_ambiguous_media_preserves_original_operation_until_selection(session, request_text):
    session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
    session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")
    await session.turn(request_text, ollama_script=[{"message": {"tool_calls": [
        {"function": {"name": "media_plan_goal", "arguments": {"goal": "Dune"}}},
    ]}}])
    entry = session.app.conversation_context[session.client_id]["pending_disambiguation"]
    assert entry["original_operation"] == ("MEDIA_REQUEST" if request_text.startswith("Request") else "MEDIA_DISCOVERY")
    assert not session.backend.media_execution_calls
    await session.turn("yes")
    assert not session.backend.media_execution_calls
    await session.turn("The new one.")
    expected_writes = 1 if request_text.startswith("Request") else 0
    assert len(session.backend.submitted_writes) == expected_writes
    if expected_writes:
        assert session.backend.submitted_writes[0]["arguments"]["canonical_external_id"] == "438631"
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_bare_yes_without_pending_media_action_never_writes(session):
    await session.turn("yes")
    assert not session.backend.media_execution_calls
    assert not session.backend.submitted_writes


@pytest.mark.parametrize("outcome,expected", [
    ({"status": "disabled", "reason": "STANDARD_MOVIE_WRITES_DISABLED"}, "aren't turned on"),
    ({"status": "rejected", "reason": "CONFIRMATION_SESSION_OR_STATUS_INVALID"}, "expired or didn't match"),
    ({"status": "failed_ingestion"}, "couldn't hand that off"),
    ({"status": "submitted", "ingestion_confirmed": False}, "couldn't confirm"),
    ({}, "couldn't confirm"),
])
@pytest.mark.asyncio
async def test_explicit_media_request_reports_executor_outcome_truthfully(session, outcome, expected):
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    session.backend.media_standard_request_override = outcome
    reply = await session.turn("Request Dune.")
    assert len(session.backend.media_execution_calls) == 1
    assert expected in reply.casefold()
    assert "looking for it" not in reply.casefold()
    assert session.client_id not in session.app.pending
    state = session.app.conversation_context[session.client_id]["latest_media_workflow"]
    assert state["execution_status"] == outcome.get("status", "ok")


@pytest.mark.asyncio
async def test_explicit_media_request_without_server_confirmation_id_never_writes(session, monkeypatch):
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    original = session.backend.invoke

    async def incomplete_planner(name, arguments, client_id, request_id, **kwargs):
        result = await original(name, arguments, client_id, request_id, **kwargs)
        if name == "media_plan_goal":
            result["result"]["confirmation_record"].pop("confirmation_id")
        return result

    monkeypatch.setattr(session.backend, "invoke", incomplete_planner)
    await session.turn("Request Dune.")
    assert not session.backend.media_execution_calls
    assert not session.backend.submitted_writes
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_explicit_media_request_after_unresolved_title_executes_current_request(session):
    await session.turn("Request Nonexistent Zzyzx movie.")
    assert not session.backend.submitted_writes
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    await session.turn("Can you get me the movie Dune?")
    assert len(session.backend.submitted_writes) == 1
    assert session.backend.submitted_writes[0]["arguments"]["canonical_external_id"] == "438631"
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_title_clarification_preserves_explicit_request_until_scope_is_known(session):
    await session.turn("Can you request the movie?")
    assert not session.backend.submitted_writes
    assert session.client_id not in session.app.pending
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    await session.turn("Dune")
    assert len(session.backend.submitted_writes) == 1
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_discovery_model_request_arguments_do_not_stage_write(session):
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    await session.turn("Do you know Dune?", ollama_script=[{"message": {"tool_calls": [
        {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Dune"}}},
    ]}}])
    assert not session.backend.media_execution_calls
    assert session.client_id not in session.app.pending


@pytest.mark.parametrize("ambiguous", [False, True])
@pytest.mark.asyncio
async def test_media_clarification_new_read_request_does_not_inherit_write_intent(session, ambiguous):
    if ambiguous:
        session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
        session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")
        await session.turn("Request Dune.")
        reply = "Do I have the 2021 movie in Plex?"
    else:
        await session.turn("Can you request the movie?")
        session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
        reply = "Do I have Dune?"
    await session.turn(reply)
    assert not session.backend.submitted_writes
    assert session.client_id not in session.app.pending


@pytest.mark.parametrize("reply", [
    "Do I have the 2021 one?",
    "Is the 2021 one in my library?",
    "No, not the 2021 one.",
])
@pytest.mark.asyncio
async def test_media_request_ambiguity_nonaffirmative_reply_never_writes(session, reply):
    session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
    session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")
    await session.turn("Request Dune.")
    original = session.app.conversation_context[session.client_id]["pending_disambiguation"]
    await session.turn(reply)
    assert not session.backend.media_execution_calls
    assert not session.backend.submitted_writes
    assert session.client_id not in session.app.pending
    assert session.app.conversation_context[session.client_id]["pending_disambiguation"] == original


@pytest.mark.parametrize("text", [
    "Can you give me information about the movie Dune?",
    "Can you find out about the movie Dune?",
    "Do not request the movie Dune.",
])
@pytest.mark.asyncio
async def test_media_information_and_negation_never_authorize_request(session, text):
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    await session.turn(text, ollama_script=[{"message": {"tool_calls": [
        {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Dune"}}},
    ]}}])
    assert not session.backend.media_execution_calls
    assert not session.backend.submitted_writes
    assert session.client_id not in session.app.pending


@pytest.mark.parametrize("text", [
    "Can you find the movie Dune?",
    "Can you find me the movie Dune?",
])
@pytest.mark.asyncio
async def test_media_find_wording_identifies_without_authorizing_request(session, text):
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    reply = await session.turn(text)
    assert not session.backend.media_execution_calls
    assert not session.backend.submitted_writes
    assert session.client_id not in session.app.pending
    assert session.client_id not in session.app.pending_offers
    assert "dune" in reply.casefold()
    assert any(name == "media_plan_goal" for name, _ in session.backend.call_log)


@pytest.mark.parametrize("request_text,selection", [
    ("Request the movie Dune from 2021.", None),
    ("Request Dune.", "The 2021 one."),
    ("Request Dune.", "The new one."),
])
@pytest.mark.asyncio
async def test_positive_media_authorization_executes_one_exact_binding(session, request_text, selection):
    session.backend.seed_web("Dune", media_type="movie", year="1984", tmdb_id="841")
    session.backend.seed_web("Dune", media_type="movie", year="2021", tmdb_id="438631")
    await session.turn(request_text)
    if selection:
        assert not session.backend.media_execution_calls
        await session.turn(selection)
    assert len(session.backend.media_execution_calls) == 1
    assert len(session.backend.submitted_writes) == 1
    call = session.backend.media_execution_calls[0]
    record = session.backend.planner_confirmations[-1]
    assert call["confirmed"] is True
    assert call["action_id"] == record["confirmation_id"]
    assert call["arguments"]["confirmation_context"] is record
    assert call["arguments"]["canonical_external_id"] == "438631"
    assert call["arguments"]["session_id"] == session.client_id
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_knowledge_to_availability_handoff(session):
    """Item 12: descriptive discovery resolves identity (read-only) and
    promotes it to a resolved subject; a LATER "Do I have it?" uses that
    subject for a real Plex availability check, and "if not, get it"
    proceeds to normal request planning -- never a production write during
    this exchange."""
    session.backend.seed_person("tom hanks", "Cast Away")
    session.backend.seed_library("Cast Away", media_type="movie", state="ABSENT", tmdb_id="8358")

    reply1 = await session.turn("What's that Tom Hanks movie where he's stuck on an island with a volleyball?")
    assert "cast away" in reply1.casefold()
    assert session.client_id not in session.app.pending, "a read-only identity question must never itself stage a write confirmation"

    reply2 = await session.turn(
        "Do I have it?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Cast Away"}}},
        ]}}],
    )
    assert "cast away" in reply2.casefold()
    assert not session.backend.submitted_writes


@pytest.mark.asyncio
async def test_descriptive_identity_year_followup_uses_canonical_session_fact(session):
    """Production-shaped regression: an identified title's year is not a
    workflow-status question and must not cause another media tool call."""
    session.backend.seed_person("tom hanks", "Cast Away")
    session.backend.seed_library("Cast Away", media_type="movie", state="ABSENT", tmdb_id="8358", year="2000")

    await session.turn("What's that Tom Hanks movie where he's stuck on an island with a volleyball?")
    identity = session.app.conversation_context[session.client_id].get("canonical_identity") or {}
    assert identity.get("title") == "Cast Away"
    assert str(identity.get("year")) == "2000"

    calls_before = len(session.backend.call_log)
    reply = await session.turn("What year did it come out?")
    calls_this_turn = [name for name, _ in session.backend.call_log[calls_before:]]
    assert reply == "Cast Away came out in 2000."
    assert "media_status" not in calls_this_turn
    assert not calls_this_turn


@pytest.mark.asyncio
async def test_referential_planner_drift_is_quarantined_before_answer_or_state(session):
    session.backend.seed_person("avery actor", "The Thing")
    session.backend.seed_library(
        "The Thing", media_type="movie", state="ABSENT", tmdb_id="1091", year="1982"
    )
    await session.turn("What's that Avery Actor movie called The Thing?")
    expected = dict(session.app.conversation_context[session.client_id]["canonical_identity"])

    original_invoke = session.backend.invoke

    async def drifting_invoke(name, arguments, client_id, request_id, confirmed=False, action_id=None):
        if name == "media_plan_goal":
            session.backend.call_log.append((name, dict(arguments)))
            return {"tool": name, "status": "ok", "result": {
                "canonical_identity": {
                    "title": "The Thing", "media_type": "movie", "year": "2011", "tmdb_id": "60935",
                },
                "current_state": "AVAILABLE_IN_PLEX", "ambiguous": False,
                "confirmation_required": False,
            }}
        return await original_invoke(
            name, arguments, client_id, request_id, confirmed=confirmed, action_id=action_id
        )

    session.backend.invoke = drifting_invoke
    reply = await session.turn("Do I have it?")

    assert session.app.conversation_context[session.client_id]["canonical_identity"] == expected
    assert "2011" not in reply
    assert "you have" not in reply.casefold()
    assert "couldn't verify" in reply.casefold()
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_identify_then_library_then_web_preserves_subject_but_changes_operation(session):
    """A canonical subject survives details/library/web operation changes.

    The follow-ups are deliberately short and natural; neither the literal
    pronoun nor an older unrelated web topic may become the tool query.
    """
    session.backend.seed_person("tom hanks", "Cast Away")
    session.backend.seed_library("Cast Away", media_type="movie", state="ABSENT", tmdb_id="8358", year="2000")
    await session.turn("What's that Tom Hanks movie where he's stuck on an island with a volleyball?")
    assert await session.turn("What year did it come out?") == "Cast Away came out in 2000."
    reply = await session.turn("Do I have it?")
    assert reply == "I couldn't find Cast Away in Plex."
    calls_before = len(session.backend.call_log)
    await session.turn(
        "Can you look it up on the internet?",
        ollama_script=[{"message": {"content": "", "tool_calls": []}}],
        final_text="Cast Away is a 2000 film.",
    )
    calls = session.backend.call_log[calls_before:]
    assert ("web_search", {"query": "Cast Away"}) in calls
    context = session.app.conversation_context[session.client_id]
    assert context["canonical_identity"]["title"] == "Cast Away"
    assert context["latest_operation"] == "MEDIA_WEB_RESEARCH"


@pytest.mark.asyncio
async def test_library_count_category_followup_retains_count_not_literal_anime_search(session):
    first = await session.turn("How many movies do I have?")
    assert first == "Plex library counts: Movies: 123."
    assert session.app.conversation_context[session.client_id]["latest_operation"] == "PLEX_LIBRARY_COUNT"
    calls_before = len(session.backend.call_log)
    second = await session.turn("What about anime?")
    calls = session.backend.call_log[calls_before:]
    assert second == "Plex library counts: Anime: 45."
    assert calls == [("plex_library_counts", {})]
    assert not any(name == "plex_search" and "anime" in str(args).casefold() for name, args in calls)


@pytest.mark.asyncio
async def test_cache_state_followup_reuses_cache_not_container_without_argument(session):
    await session.turn("How full is cache?")
    assert session.app.conversation_context[session.client_id]["operation_scope"] == {"target": "cache"}
    calls_before = len(session.backend.call_log)
    reply = await session.turn("Is it running?")
    calls = session.backend.call_log[calls_before:]
    assert calls == [("unraid_storage_status", {"target": "cache"})]
    assert reply == "Cache status is ONLINE."
    assert not any(name == "unraid_container_status" for name, _ in calls)


@pytest.mark.asyncio
async def test_explicit_new_question_outranks_retained_library_count_operation(session):
    await session.turn("How many movies do I have?")
    calls_before = len(session.backend.call_log)
    await session.turn("How many containers are running?", final_text="There are 2 running containers.")
    calls = session.backend.call_log[calls_before:]
    assert any(name == "list_containers" for name, _ in calls)
    assert not any(name == "plex_library_counts" for name, _ in calls)


@pytest.mark.asyncio
async def test_collective_library_inventory_is_broad_but_request_stays_canonical(session):
    session.backend.seed_library("Galactic Saga", media_type="movie", state="AVAILABLE_IN_PLEX", tmdb_id="101", year=2011)
    session.backend.seed_library("Galactic Saga: Origins", media_type="tv", state="AVAILABLE_IN_PLEX", tvdb_id="202", year=2015)
    reply = await session.turn("What Galactic Saga stuff do I have?")
    assert "Movies: Galactic Saga (2011)" in reply
    assert "TV Shows: Galactic Saga: Origins (2015)" in reply
    calls_before = len(session.backend.call_log)
    await session.turn("Get Galactic Saga.")
    calls = session.backend.call_log[calls_before:]
    assert any(name == "media_plan_goal" for name, _ in calls)
    assert not any(name == "plex_search" for name, _ in calls)


def test_creator_hint_only_resolves_when_catalog_candidates_supply_unique_people_evidence(app):
    candidates = [
        {"title": "Same Title", "year": 2003, "media_type": "movie", "people": ["Creator One"]},
        {"title": "Same Title", "year": 2015, "media_type": "movie", "people": ["Creator Two"]},
    ]
    assert app.resolve_disambiguation_reply("The Creator Two one.", candidates) == candidates[1]
    # Missing people evidence remains uncertainty, not negative proof.
    assert app.resolve_disambiguation_reply("The Unknown Person one.", candidates) is None


@pytest.mark.asyncio
async def test_knowledge_to_request_handoff(session):
    """Descriptive discovery retains the exact identity for an explicit request."""
    session.backend.seed_person("brad pitt", "A River Runs Through It")
    session.backend.seed_library("A River Runs Through It", media_type="movie", state="ABSENT", tmdb_id="11202")

    reply1 = await session.turn("What's that Brad Pitt movie about fly fishing in Montana?")
    assert "a river runs through it" in reply1.casefold()
    assert session.client_id not in session.app.pending

    await session.turn("Add it.")
    assert session.client_id not in session.app.pending
    assert len(session.backend.submitted_writes) == 1
    assert session.backend.submitted_writes[0]["arguments"]["canonical_external_id"] == "11202"


@pytest.mark.asyncio
async def test_music_inventory_operation_persists_for_named_album_followup(session):
    session.backend.seed_library(
        "OK Computer", media_type="album", state="AVAILABLE_IN_PLEX",
        foreign_album_id="album-ok-computer", artist="Radiohead", year=1997,
    )

    reply1 = await session.turn("What Radiohead music do I have?")
    assert "OK Computer" in reply1
    state = session.app.conversation_context[session.client_id]
    assert state["latest_operation"] == "PLEX_MUSIC_ARTIST_INVENTORY"
    assert state["latest_resolved_referent"] == "Radiohead"

    calls_before = len(session.backend.call_log)
    reply2 = await session.turn("Is OK Computer in my library?")
    calls = session.backend.call_log[calls_before:]
    assert ("plex_library_lookup", {"query": "OK Computer", "library": "Music"}) in calls
    assert "OK Computer" in reply2
    assert not any(name == "media_plan_goal" for name, _ in calls)


# --- Structural unreachability of media_standard_request from Qwen's own --
# --- tool-selection: real live production bug, most safety-critical fix  --
# --- of the session. --------------------------------------------------- --

@pytest.mark.asyncio
async def test_qwen_cannot_call_media_standard_request_directly_even_if_it_tries(session):
    """Real production bug: Qwen selected media_standard_request directly
    as an ordinary tool, invented its own {"title", "year"} arguments from
    conversational memory, and only failed to write because that tool's
    OWN internal argument-hash validation happened to catch it -- a
    secondary safety layer, not the primary gate this session's write-
    safety guarantees were built around. Simulates a misbehaving/
    hallucinating model that emits a tool_call for a name that was never
    even offered (the fake catalog here, like the real discovery surface,
    never includes media_standard_request) -- the dispatch loop must
    refuse to execute it outright, not merely rely on it failing
    downstream."""
    reply = await session.turn(
        "Yes, please request it.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_standard_request", "arguments": {"title": "Primer", "year": "2004"}}},
        ]}}],
        final_text="I can't do that directly.",
    )
    assert not any(name == "media_standard_request" for name, _ in session.backend.call_log), (
        "media_standard_request must never reach invoke_tool via the model tool-selection loop"
    )
    assert not session.backend.submitted_writes
    action = session.app.pending.get(session.client_id)
    assert action is None or action.get("name") != "media_standard_request", (
        "a hallucinated tool_call must never populate a real pending confirmation for this tool"
    )


@pytest.mark.asyncio
async def test_legitimate_confirmed_media_request_still_executes_end_to_end(session):
    """Negative control: closing the Qwen-direct-call gap must not break
    the actual legitimate path -- a real staged pending[client_id]
    confirmation, answered with a real "yes", must still execute the fake
    write exactly as before."""
    session.backend.seed_library("Dune", media_type="movie", state="ABSENT", tmdb_id="438631")
    result = await session.backend.invoke("media_plan_goal", {"goal": "get Dune 2021"}, session.client_id, "legacy-request")
    session.app.stage_media_confirmation(session.client_id, "legacy-request", result["result"])
    action = session.app.pending.get(session.client_id)
    assert action is not None
    assert action["name"] in {"media_standard_request", "media_execute_goal"}

    await session.turn("Yes, please request it.")
    assert len(session.backend.submitted_writes) == 1
    assert session.client_id not in session.app.pending


# --- Bug A: an untyped media request must still reach media_plan_goal, ---
# --- through the real preflight_plan()/respond() path, no ollama_script --
# --- needed since this fires deterministically. --------------------------

@pytest.mark.asyncio
async def test_bug_a_untyped_request_reaches_media_plan_goal_through_respond(session):
    """Real production bug: "can you request Sagwa The Chinese Siamese
    Cat" (no type word at all) produced "I don't have any tools or access
    to request media" with ZERO tools called -- Qwen was never even
    routed toward media_plan_goal. This must now fire deterministically,
    with no ollama_script needed at all."""
    session.backend.seed_web("Sagwa The Chinese Siamese Cat", media_type="tv", tvdb_id="77670")
    reply = await session.turn("Can you request Sagwa The Chinese Siamese Cat")
    called = [name for name, _ in session.backend.call_log]
    assert "media_plan_goal" in called
    assert session.backend.submitted_writes[0]["arguments"]["canonical_external_id"] == "77670"
    assert "no tools" not in reply.casefold() and "no access" not in reply.casefold()


# --- Bug B: "yes do that" must continue an already-offered identity ------
# --- toward confirmation, not lose it. ------------------------------------

@pytest.mark.asyncio
async def test_bug_b_yes_do_that_continues_the_offered_identity(session):
    """Real production bug: after Sagwa was identified and offered ("I can
    request it... when you're ready"), "yes do that" produced "I couldn't
    identify a confident media match without changing anything" -- a
    fresh, empty media_plan_goal re-run that lost the resolved identity
    entirely, because is_confirmation() only recognized "it" forms, not
    "that"."""
    session.backend.seed_library("Sagwa The Chinese Siamese Cat", media_type="tv", state="ABSENT", tvdb_id="77670")
    await session.turn(
        "Get me Sagwa The Chinese Siamese Cat",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Sagwa The Chinese Siamese Cat"}}},
        ]}}],
    )
    assert session.client_id not in session.app.pending
    assert len(session.backend.submitted_writes) == 1

    await session.turn("yes do that")
    assert len(session.backend.submitted_writes) == 1
    assert session.client_id not in session.app.pending


@pytest.mark.asyncio
async def test_container_logs_result_is_not_silently_dropped_before_synthesis(session):
    # Real production bug: _DOMAIN_TOOL_PREFIXES is a hardcoded allowlist
    # that filter_relevant_tool_results() uses to decide which live tool
    # results reach synthesis at all -- "get_container_logs" was missing
    # from the "server" domain's tuple, so a real, successful call's result
    # was silently dropped (grounding_results became []) before
    # evidence_supported_answer ever saw it, and its dynamic_fact_question
    # guard then reported "unavailable" purely because the (filtered-empty)
    # results list had nothing in it -- even though Qwen's own synthesis
    # text was perfectly fine and never even mentioned unavailability.
    reply = await session.turn(
        "Show me the last few log lines for the Home-AI-Tools container.",
        final_text="Here are the recent log lines for Home-AI-Tools: the server started successfully.",
    )
    assert reply == "Here are the recent log lines for Home-AI-Tools: the server started successfully."


@pytest.mark.asyncio
async def test_named_container_status_result_is_not_silently_dropped_before_synthesis(session):
    # Fourth instance of the same allowlist-completeness bug: "Is Plex
    # running?" resolves to "media" domain (explicit_domain's bare "plex"
    # keyword) and preflight_plan correctly routes it to
    # unraid_container_status -- but "media" domain's _DOMAIN_TOOL_PREFIXES
    # tuple did not include "unraid_container_status", so the real,
    # successful result was silently dropped before synthesis, producing a
    # false "I couldn't verify the current media pipeline" answer even
    # though the container status call plainly succeeded.
    reply = await session.turn(
        "Is Plex running?",
        final_text="Yes, Plex is running and has been up for about 10 days.",
    )
    assert reply == "Plex is running, CPU 2.5%, memory 512 MB."


@pytest.mark.asyncio
async def test_storage_followup_naming_a_container_continues_the_server_topic_end_to_end(session):
    # Real production bug found in a live continuity test: "How full is
    # cache?" -> "What's using most of it?" -> "What's inside appdata?" ->
    # "What about Plex?" reclassified the last turn as "media" purely
    # because explicit_domain()'s bare "plex" keyword outranks any inherited
    # storage topic, producing an unrelated media-acquisition non-answer
    # ("I couldn't identify a confident media match") instead of continuing
    # the storage-usage line of questioning. turn_context() now recognizes
    # this bounded "what about X" continuation (keyed on the immediately
    # preceding tool call, not the non-sticky per-turn "domain" signal) and
    # routes it deterministically to the real capability that exists.
    await session.turn(
        "How full is cache?",
        final_text="The cache is about 57 percent full.",
    )
    reply = await session.turn(
        "What about Plex?",
        final_text="Plex is running and using about 512 megabytes of memory.",
    )
    assert reply == "Plex is running, CPU 2.5%, memory 512 MB."


@pytest.mark.asyncio
async def test_home_ai_container_memory_result_is_not_silently_dropped_before_synthesis(session):
    # Fifth instance of the same allowlist-completeness bug: "How much
    # memory is Home-AI using?" resolves to "web_research" domain
    # (explicit_domain's bare "ai" keyword matches the hyphen-bounded
    # substring in "Home-AI") even though preflight_plan correctly routes
    # it to unraid_container_status -- "web_research" domain's tuple did
    # not include it, so the real, successful result was silently dropped.
    reply = await session.turn(
        "How much memory is Home-AI using?",
        final_text="Home-AI-Assistant is using about 512 megabytes of memory.",
    )
    assert reply == "Home-AI-Assistant is running, CPU 2.5%, memory 512 MB."


@pytest.mark.asyncio
async def test_investigate_downloads_result_is_not_silently_dropped_before_synthesis(session):
    # Same root cause as the logs bug above: "investigate_downloads" was
    # missing from BOTH the "server" and "media" domain tuples in
    # _DOMAIN_TOOL_PREFIXES (the turn can land in either domain depending on
    # phrasing), so its real, successful result was silently dropped before
    # synthesis and the turn fell back to a generic "unavailable" answer.
    reply = await session.turn(
        "Investigate my downloads across all services.",
        final_text="You have 269 torrents in qBittorrent, none currently active.",
    )
    assert reply == "You have 269 torrents in qBittorrent, none currently active."


def test_every_domain_relevant_tool_has_a_matching_prefix(app):
    # _DOMAIN_TOOL_PREFIXES is a hand-maintained allowlist: any tool name
    # missing from its own domain's tuple gets its real, successful result
    # silently dropped before synthesis ever sees it (filter_relevant_
    # tool_results returns [] for that tool), which then reads to the user
    # as a live-tool "unavailable" even though the call plainly succeeded --
    # found three times over in one night (get_container_logs,
    # investigate_downloads, overseerr_status) before this test existed.
    # Guard every currently-known tool this way instead of waiting for the
    # next one to slip through the same gap.
    checks = {
        "server": ["get_container_logs", "get_container_status", "get_gpu_status", "get_server_overview",
                   "get_storage_status", "list_containers", "restart_container", "investigate_downloads"],
        "media": ["overseerr_status", "overseerr_recent_requests", "investigate_downloads",
                  "investigate_plex_missing", "investigate_media_pipeline", "plex_library_counts",
                  "sonarr_health", "radarr_health", "lidarr_health", "torbox_status", "slskd_downloads",
                  "qbittorrent_summary", "beets_status", "music_enricher_status",
                  # "Is Plex running?" resolves to "media" domain (explicit_domain's
                  # bare "plex" keyword) but is answered by unraid_container_status --
                  # fourth instance of this exact bug class, found live.
                  "unraid_container_status"],
        "web_research": ["web_search", "web_fetch", "wikipedia_search",
                         # "How much memory is Home-AI using?" resolves to "web_research"
                         # domain via explicit_domain's bare "ai" keyword collision --
                         # fifth instance of this exact bug class, found live.
                         "unraid_container_status"],
        "weather": ["weather_forecast"],
        "camera": ["frigate_stats", "frigate_recent_activity", "frigate_recent_events"],
    }
    for domain, tools in checks.items():
        for tool in tools:
            result = [{"tool": tool, "status": "ok", "result": {"x": 1}}]
            filtered = app.filter_relevant_tool_results(result, {"domain": domain})
            assert filtered == result, f"{tool!r} was dropped under domain {domain!r} -- add it to _DOMAIN_TOOL_PREFIXES[{domain!r}]"


@pytest.mark.asyncio
async def test_generic_discovery_shape_does_not_default_non_media_subjects_to_media(session):
    # Real production bug: discovery_question()'s "what's X" shape is
    # intentionally domain-agnostic and matches ANY "what's X" sentence,
    # including "What's on my grocery list?" -- which made
    # _effective_relevance_domain() assume "media" domain purely from that
    # generic pattern match, which then made filter_relevant_tool_results()
    # drop list_items' real, successful (but genuinely empty) result before
    # synthesis ever saw it, producing a false "I don't have access" answer
    # instead of the real "your list is empty" one.
    reply = await session.turn(
        "What's on my grocery list?",
        final_text="I don't have access to your grocery list. I can only see what's on my screen right now, which doesn't include personal data like yours.",
    )
    assert reply == "That check succeeded, but it came back empty -- there's nothing there to report right now."


@pytest.mark.asyncio
async def test_generic_discovery_shape_respects_a_subjects_own_unambiguous_domain(session):
    # Same root cause as the grocery-list bug above, but for a subject with
    # its OWN unambiguous keyword domain rather than no domain at all:
    # "What's the state of the neon lights?" and "What's the current GPU
    # usage?" both match discovery_question()'s domain-agnostic "what's X"
    # shape, so _effective_relevance_domain() used to default them to
    # "media" too -- dropping home_get_state's/get_gpu_status's real result
    # (neither matches any "media_..." prefix) before synthesis ever saw
    # it. A subject's own home/server/weather keyword must win over the
    # generic "media" default.
    session.backend.responses = {}
    reply = await session.turn(
        "What's the current GPU usage?",
        final_text="I couldn't check the GPU usage because the required data wasn't available.",
    )
    assert "media" not in reply.lower()
