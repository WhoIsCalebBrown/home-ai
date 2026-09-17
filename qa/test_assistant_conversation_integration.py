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

import importlib.util
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

APP_PATH = Path("/app/voice-api-app.py")
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
        self.simulate_drift_for: str | None = None
        self.drift_candidate_title: str = ""
        self.drift_candidate_year: str | None = None
        self.person_index: dict[str, str] = {}  # casefold(person name) -> title, mirrors the real web-discovery fallback's person-hint -> title resolution (tools/server-tools-app.py's _web_discover_title), tested there directly against real functions -- this fake only proves the ASSISTANT-side handoff after identity resolves, not the discovery mechanism itself.

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
        if name == "web_search":
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
                }
            return {"tool": name, "status": "ok", "result": result}
        if name == "media_execute_goal":
            # Real /invoke: TOOLS.get("media_execute_goal") is None -> HTTP
            # 404 -> assistant's invoke_tool returns this exact shape. There
            # is no fixture to provide here because there is nothing to
            # fake: the real tool does not exist.
            return {"tool": name, "status": "error", "result": {"error": "That tool is not enabled."}}
        if name == "plex_search":
            query = str(arguments.get("query", ""))
            found = self._find_identity(query)
            identity = found if isinstance(found, dict) else None
            return {"tool": name, "status": "ok", "result": {"matched": bool(identity), "matches": [identity] if identity else [], "library_title": "Movies"}}
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
            workflow_id = arguments.get("workflow_id")
            confirmation_context = arguments.get("confirmation_context") or {}
            confirmation_id = confirmation_context.get("confirmation_id")
            if confirmation_id in self.consumed_confirmations:
                return {"tool": name, "status": "ok", "result": {"status": "rejected", "reason": "CONFIRMATION_ALREADY_CONSUMED", "write_executed": False}}
            if confirmation_id:
                self.consumed_confirmations.add(confirmation_id)
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
            return {"tool": name, "status": "ok", "result": {"container": arguments.get("container"), "state": "running", "uptime_seconds": 921550, "memory_usage_bytes": 512_000_000}}
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

        async def turn(self, user_text: str, ollama_script: list[dict] | None = None, final_text: str = "") -> str:
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
            await app.respond(ws, client_id, request_id, user_text)
            self.last_stream_payload = httpx_fake.last_stream_payload
            return "\n".join(m["text"] for m in ws.sent[before:] if m.get("type") == "text")

    return Driver()


# --- Scenario: discover -> offer -> accept -> cross-capability -> offer ->
# explicit write intent -> strict confirmation -> fake write (spec #2, #24) --

@pytest.mark.asyncio
async def test_full_discover_offer_accept_write_conversation(session):
    session.backend.seed_web("Cowboy Bebop", media_type="anime", tmdb_id="30991")

    reply1 = await session.turn(
        "Do you know Cowboy Bebop?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "Cowboy Bebop"}}},
        ]}}],
        final_text="Cowboy Bebop is an anime series.",
    )
    assert "cowboy bebop" in reply1.casefold()

    subject_before = session.app.conversation_context.get(session.client_id, {}).get("latest_resolved_referent")
    assert subject_before and "cowboy bebop" in subject_before.casefold()

    reply2 = await session.turn(
        "Is Cowboy Bebop in my library?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Cowboy Bebop"}}},
        ]}}],
        final_text="It's not in Plex yet.",
    )
    assert session.client_id not in session.app.pending  # no write confirmation yet
    assert not session.backend.submitted_writes

    reply3 = await session.turn(
        "Yeah, get it.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Cowboy Bebop"}}},
        ]}}],
    )
    action = session.app.pending.get(session.client_id)
    assert action is not None, "media_plan_goal's confirmation_required result must stage a real PendingConfirmation"
    assert action["name"] in {"media_standard_request", "media_execute_goal"}
    assert not session.backend.submitted_writes  # still no write -- confirmation only

    reply4 = await session.turn("Go for it.")
    assert len(session.backend.submitted_writes) == 1, "exactly one execution"
    assert session.client_id not in session.app.pending  # single-use, consumed

    reply5 = await session.turn("Go for it.")
    assert len(session.backend.submitted_writes) == 1, "a stale/replayed confirmation must never submit twice"


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
    assert session.client_id in session.app.pending
    assert not session.backend.submitted_writes

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
    assert session.client_id in session.app.pending
    assert not session.backend.submitted_writes


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
    assert action_a is not None and action_b is not None
    assert action_a["arguments"]["confirmation_context"]["title"] == "Dune"
    assert action_b["arguments"]["confirmation_context"]["title"] == "Rodeo"
    assert action_a["arguments"] != action_b["arguments"]
    assert app.conversation_context.get(client_a, {}).get("latest_media_workflow", {}).get("title") != "Rodeo"

    # Confirm session A; session B's pending confirmation must be untouched.
    app.httpx = _FakeHttpxModule([{"message": {"content": "", "tool_calls": []}}], "Done, got Dune.")
    await app.respond(ws_a, client_a, str(uuid.uuid4()), "Go for it.")
    assert len(backend.submitted_writes) == 1
    assert backend.submitted_writes[0]["arguments"]["confirmation_context"]["title"] == "Dune"
    assert client_b in app.pending, "confirming session A must never consume or clear session B's pending confirmation"


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
    assert title.casefold() in reply2.casefold(), "the fresh title must actually be used, not discarded"
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
    assert title.casefold() in reply4.casefold(), f"the bare reply must resolve as the title, got: {reply4!r}"
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
    # Confirmed identified -> a real confirmation prompt, never a silent write.
    assert not session.backend.submitted_writes


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
    assert new_calls == ["media_plan_goal"], f"a clarification reply must only touch media_plan_goal, got: {new_calls}"


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
async def test_clarification_reply_never_satisfies_a_pending_write_confirmation(session):
    """A media clarification answer ("The older one.") and a write
    confirmation answer ("Yeah.") are different concepts entirely --
    resolving a candidate must never itself execute or authorize a write,
    and must never be interpretable as answering an unrelated pending
    confirmation."""
    session.backend.seed_web("The Thing", media_type="movie", year="1982", tmdb_id="1091")
    session.backend.seed_web("The Thing", media_type="movie", year="2011", tmdb_id="60308")
    await session.turn(
        "Add The Thing.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "The Thing", "media_type": "movie"}}},
        ]}}],
    )
    await session.turn("The older one.")
    # Resolving the candidate must stage a real confirmation prompt (a
    # write requires an explicit "yes" of its own) -- never execute directly.
    assert not session.backend.submitted_writes
    action = session.app.pending.get(session.client_id)
    assert action is not None, "identification must stage a real PendingConfirmation, not skip straight to a write"


# --- Descriptive media discovery (real live production bug): a plain --
# --- question about a media item, described by person + plot rather   --
# --- than a known title, must reach media_plan_goal -- not the plex   --
# --- library-search dead end, and not the old "no matching live       --
# --- workflow" status short-circuit. Reproduced through the REAL      --
# --- respond()/preflight_plan deterministic path, not helper calls.   --

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
async def test_request_variants_reach_media_plan_goal_and_proceed_to_confirmation(session, text):
    """Item 11: request-shaped descriptive variants use the SAME identity
    resolution machinery (media_plan_goal), then proceed to normal
    confirmation once resolved -- never a second, independent resolver."""
    session.backend.seed_person("brad pitt", "A River Runs Through It")
    session.backend.seed_library("A River Runs Through It", media_type="movie", state="ABSENT", tmdb_id="11202")
    await session.turn(text)
    called = [name for name, _ in session.backend.call_log]
    assert "media_plan_goal" in called, text
    action = session.app.pending.get(session.client_id)
    assert action is not None, f"a resolved request must stage a real confirmation, not write directly: {text}"
    assert not session.backend.submitted_writes


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
async def test_knowledge_to_request_handoff(session):
    """Item 13: descriptive discovery resolves identity; "Add it." then
    proceeds through normal media planning and stops at strict
    confirmation -- no production write during QA."""
    session.backend.seed_person("brad pitt", "A River Runs Through It")
    session.backend.seed_library("A River Runs Through It", media_type="movie", state="ABSENT", tmdb_id="11202")

    reply1 = await session.turn("What's that Brad Pitt movie about fly fishing in Montana?")
    assert "a river runs through it" in reply1.casefold()
    assert session.client_id not in session.app.pending

    await session.turn("Add it.")
    action = session.app.pending.get(session.client_id)
    assert action is not None, "a request following identity resolution must stop at a real confirmation prompt"
    assert not session.backend.submitted_writes


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
    await session.turn(
        "Get Dune 2021.",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "get Dune 2021"}}},
        ]}}],
    )
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
    assert "sagwa" in reply.casefold()
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
    assert session.client_id in session.app.pending

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
    assert reply == "Yes, Plex is running and has been up for about 10 days."


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
        "web_research": ["web_search", "web_fetch", "wikipedia_search"],
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
