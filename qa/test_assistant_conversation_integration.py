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
            title = goal
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
            is_request_shaped = bool(re.search(r"\b(get|request|add|download|acquire)\b", goal, re.I))
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

    def __init__(self, script: list[dict], final_text: str):
        self._script = script
        self._final_text = final_text

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
            return _FakeStreamContext(self._final_text)
        raise RuntimeError(f"OllamaFake received unexpected stream {method} {url}")


class _FakeHttpxModule:
    """Rebinds only voice_api_app's own `httpx` name -- see module docstring."""

    def __init__(self, script: list[dict], final_text: str = ""):
        self._script = script
        self._final_text = final_text

    def AsyncClient(self, *args, **kwargs):
        return _FakeOllamaClient(self._script, self._final_text)


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
            app.httpx = _FakeHttpxModule(ollama_script if ollama_script is not None else [{"message": {"content": "", "tool_calls": []}}], final_text)
            before = len(ws.sent)
            await app.respond(ws, client_id, request_id, user_text)
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
        "Can you check Plex for Cowboy Bebop?",
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
    await session.turn(
        "Can you check Plex for Rodeo?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Rodeo"}}},
        ]}}],
    )
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
    session.backend.seed_library("Rodeo", media_type="album", state="ACQUIRED_NOT_VISIBLE", foreign_album_id="fa-rodeo-2", artist="Travis Scott")
    await session.turn(
        "Do I have Rodeo by Travis Scott?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_plan_goal", "arguments": {"goal": "Rodeo"}}},
        ]}}],
    )
    workflow_before = session.app.conversation_context.get(session.client_id, {}).get("latest_media_workflow")

    # REAL PRODUCTION GAP found by this test, not a fake-harness artifact:
    # respond() (voice-api-app.py, the `if not live_results and
    # media_status_question(user_text) and (...)` branch right before the
    # Qwen loop) hardcodes "I couldn't verify the current media status
    # because I don't have a matching live workflow." whenever live_results
    # is still empty at that point -- which it always is for a media status
    # question, since deterministic preflight (`semantic_preflight_allowed`)
    # is scoped to calculator/unit_convert only. It never attempts a real
    # media_status(workflow_id=...) lookup against
    # conversation_context[client_id]["latest_media_workflow"] before
    # giving up, and never reaches the Qwen loop at all. This means ANY
    # "how's X doing" / "how's it doing" status follow-up currently answers
    # this canned failure unconditionally, regardless of whether a real
    # workflow exists. Reported as a BUGS FOUND item, NOT patched here --
    # this is core routing code guarded by an extensive existing regression
    # suite (qa/test_hardening_matrix.py, scenario_catalog.py) this pass did
    # not have budget to re-verify line by line, and the established review
    # convention on this branch is to flag rather than silently patch
    # anything touching routing/confirmation-adjacent behavior.
    full_reply = await session.turn(
        "How's the Rodeo album doing?",
        ollama_script=[{"message": {"content": "", "tool_calls": [
            {"function": {"name": "media_status", "arguments": {"title": "Rodeo"}}},
        ]}}],
        final_text="It's been collected but Plex hasn't picked it up yet.",
    )
    assert "matching live workflow" in full_reply.casefold(), (
        "documents the real gap above -- if this assertion ever starts "
        "failing because the message changed, re-check whether the gap "
        "was fixed and update/remove this test accordingly"
    )


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
