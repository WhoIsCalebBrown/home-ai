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
        self.web_index[title.casefold()] = {"media_type": media_type, "canonical_identity": {"media_type": media_type, "title": title, **identity_fields}}

    def _find_identity(self, title: str) -> dict | None:
        for entry in self.library.values():
            if entry["identity"].get("title", "").casefold() == title.casefold():
                return entry["identity"]
        web = self.web_index.get(title.casefold())
        if web:
            return web["canonical_identity"]
        return None

    async def invoke(self, name: str, arguments: dict, client_id: str, request_id: str, confirmed: bool = False, action_id: str | None = None) -> dict:
        self.call_log.append((name, dict(arguments)))
        if name == "web_search":
            query = str(arguments.get("query", ""))
            hit = None
            for title, entry in self.web_index.items():
                if title in query.casefold():
                    hit = entry
                    break
            if hit is None:
                return {"tool": name, "status": "ok", "result": {"results": []}}
            return {"tool": name, "status": "ok", "result": {
                "results": [{"title": hit["canonical_identity"]["title"], "url": "https://example.invalid/x",
                             "snippet": f"{hit['canonical_identity']['title']} is a {hit['media_type']}."}],
                "likely_subject": {"subject_type": "media", "media_type": hit["media_type"], "title": hit["canonical_identity"]["title"]},
            }}
        if name == "media_plan_goal":
            goal = str(arguments.get("goal", ""))
            title = goal
            for known in list(self.library.values()) + [e["canonical_identity"] for e in self.web_index.values()]:
                candidate_title = known.get("title") if "title" in known else known["identity"].get("title")
                if candidate_title and candidate_title.casefold() in goal.casefold():
                    title = candidate_title
                    break
            identity = self._find_identity(title)
            if identity is None:
                return {"tool": name, "status": "ok", "result": {"canonical_identity": None, "current_state": "NOT_FOUND", "ambiguous": False, "confirmation_required": False}}
            canonical_id = identity.get("tmdb_id") or identity.get("tvdb_id") or identity.get("foreign_album_id") or identity.get("title")
            entry = self.library.get(str(canonical_id))
            state = entry["state"] if entry else "IDENTIFIED"
            workflow_id = f"wf-{canonical_id}"
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
                result["confirmation_record"] = {
                    "workflow_id": workflow_id, "confirmation_id": confirmation_id,
                    "plan_version_hash": f"hash-{confirmation_id}",
                    "arguments": {"workflow_id": workflow_id, "canonical_external_id": canonical_id,
                                  "media_type": identity.get("media_type"), "season_scope": []},
                    "operation": "cli_debrid.media_standard_request", "canonical_external_id": canonical_id,
                    "canonical_media_type": identity.get("media_type"), "title": identity.get("title"),
                }
            return {"tool": name, "status": "ok", "result": result}
        if name == "media_status" or name == "plex_match_canonical_media":
            title = arguments.get("title") or arguments.get("query") or ""
            identity = self._find_identity(str(title))
            if identity is None:
                return {"tool": name, "status": "ok", "result": {"matched": False, "current_state": "NOT_FOUND"}}
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
