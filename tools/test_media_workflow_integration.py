"""Real Tools-level integration test: drives the ACTUAL media_plan_goal ->
media_standard_request pipeline in tools/server-tools-app.py end to end,
faking only the outermost data-source calls (Radarr, Plex, cli_debrid's
webhook POST and its read-only evidence check) -- every hash/session/
workflow/storage-contract validation in between is the real, unmodified
production code. This is the Level-B "production-shaped Tools integration
test with fake writes" proof, and is what actually exercises
tools/workflow_events.py and the canonical-identity refactor through a real
write-confirmation round trip (spec items #14, #15, #16).

No real network call is made -- httpx.AsyncClient is only ever reached
through radarr_search/plex_match_canonical_media/arr_get (all monkeypatched)
and the cli_debrid webhook POST inside media_standard_request (also
monkeypatched). STANDARD_MEDIA_WRITES_ENABLED and friends are flipped to
True only inside this test module's own copy of the imported module, never
in a real config/env -- this proves the code path works, it does not turn
writes on anywhere real.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("server-tools-app.py")
sys.path.insert(0, str(Path(__file__).parent))


def _load_app(tmp_path):
    spec = importlib.util.spec_from_file_location("server_tools_app_integration", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
    import workflow_events
    workflow_events.WORKFLOW_EVENTS_DB = tmp_path / "workflow-events.sqlite3"
    module.STANDARD_MEDIA_BACKEND_READY = True
    module.STANDARD_MEDIA_WRITES_ENABLED = True
    module.STANDARD_MOVIE_WRITES_ENABLED = True
    module.STANDARD_SEASON_WRITES_ENABLED = True
    module._standard_bridge_secret = lambda: "fake-test-token"

    async def fake_radarr_search(args):
        # A more realistic fake: only match when the query is actually
        # about Dune, empty otherwise -- media_status's live-identification
        # fallback (added for "what's the status of X" when X was never
        # formally requested through Home-AI) now calls media_plan_goal
        # for ANY unmatched title, including genuinely-unknown ones
        # (test_fresh_session_status_check_for_never_requested_title_is_honest
        # queries "Interstellar"), so a fixture that always returns "Dune"
        # regardless of query would falsely manufacture an ambiguous/found
        # result for a title that should resolve to no match at all.
        if "dune" not in str(args.get("query", "")).casefold():
            return {"matches": []}
        return {"matches": [{"title": "Dune", "year": "2021", "tmdbId": 438631}]}

    async def fake_sonarr_search(args):
        # media_plan_goal's "unknown"-media-type branch (an unclassified
        # title with no "movie"/"show" word) searches Radarr AND Sonarr in
        # parallel -- mocked here purely so media_status's live-
        # identification fallback never makes a real network call when
        # testing a title with no explicit media type.
        return {"matches": []}

    async def fake_arr_get(service, path, args=None):
        return []

    async def fake_plex_match_canonical_media(args):
        return {"matched": False, "candidates": []}

    module.radarr_search = fake_radarr_search
    module.sonarr_search = fake_sonarr_search
    module.arr_get = fake_arr_get
    module.plex_match_canonical_media = fake_plex_match_canonical_media
    return module, workflow_events


@pytest.fixture
def app(tmp_path):
    module, events = _load_app(tmp_path)
    yield module, events


@pytest.mark.asyncio
async def test_real_media_plan_goal_produces_canonical_identity_and_events(app):
    module, events = app
    plan = await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-1"})
    assert plan["canonical_identity"]["tmdb_id"] == 438631
    assert plan["canonical_identity"]["title"] == "Dune"
    assert plan["confirmation_required"] is True
    workflow_id = plan["workflow_id"]
    assert workflow_id

    recorded = events.get_events(workflow_id)
    event_types = [e["event_type"] for e in recorded]
    assert event_types == ["IDENTIFIED", "AVAILABILITY_CHECKED", "PLAN_CREATED", "CONFIRMATION_REQUESTED"], (
        "events must be recorded in the order they actually happened"
    )
    assert recorded[0]["canonical_subject_id"] == "438631"


@pytest.mark.asyncio
async def test_real_confirmation_round_trip_to_fake_write(app, monkeypatch):
    module, events = app
    plan = await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-1"})
    workflow_id = plan["workflow_id"]
    confirmation = plan["confirmation_record"]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

        content = b'{"ok": true}'

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            FakeAsyncClient.last_payload = json
            return FakeResponse()

    monkeypatch.setattr(module, "httpx", type("FakeHttpxModule", (), {"AsyncClient": FakeAsyncClient}))
    # Called twice by the real code: once as a pre-check ("does this already
    # exist, so the confirmation is a no-op") and once after the webhook POST
    # to confirm ingestion (transport success is not treated as proof -- see
    # module docstring on _cli_debrid_exact_item_evidence). The pre-check
    # must see "not yet" so the test actually exercises a real submission.
    evidence_calls = {"count": 0}

    def fake_evidence(payload):
        evidence_calls["count"] += 1
        return {"matched": evidence_calls["count"] > 1}

    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", fake_evidence)

    args = {
        "workflow_id": workflow_id,
        "media_type": "movie",
        "canonical_title": "Dune",
        "canonical_external_id": 438631,
        "confirmation_context": confirmation,
        "session_id": "sess-1",
    }
    result = await module.media_standard_request(args)
    assert result["status"] == "submitted"
    assert result["write_executed"] is True
    assert FakeAsyncClient.last_payload is not None, "the fake write boundary must still be reached and inspectable, even though it never leaves the process"

    recorded = events.get_events(workflow_id)
    event_types = [e["event_type"] for e in recorded]
    assert event_types == [
        "IDENTIFIED", "AVAILABILITY_CHECKED", "PLAN_CREATED", "CONFIRMATION_REQUESTED",
        "CONFIRMATION_CONSUMED", "REQUEST_SUBMITTED", "QUEUED",
    ]

    # Canonical identity round-trip: the workflow row (read back fresh, not
    # the in-memory plan dict) still carries the same tmdb_id -- proving the
    # identity survived JSON persistence and a full confirmation/write cycle
    # without ever falling back to title-only matching.
    row = next(r for r in module._media_workflows() if r["workflow_id"] == workflow_id)
    assert row["canonical_identity"]["tmdb_id"] == 438631

    # A replay of the exact same confirmation must never submit twice.
    replay = await module.media_standard_request(args)
    assert replay["status"] == "rejected"
    assert replay["reason"] == "CONFIRMATION_ALREADY_CONSUMED"
    assert replay["write_executed"] is False


@pytest.mark.asyncio
async def test_repeated_status_observation_does_not_duplicate_transition_events(app):
    """Event idempotency (spec item #15): media_plan_goal called twice for
    the same identity without any write in between must not double-log
    PLAN_CREATED/CONFIRMATION_REQUESTED -- the real code only appends those
    events the same number of times it actually (re)creates a plan/
    confirmation, which happens on every planning call by design (each call
    is a fresh planning pass, not a cached observation) -- this test pins
    that current, intentional behavior so a future change that turns
    observation into duplication is caught."""
    module, events = app
    plan1 = await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-1"})
    plan2 = await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-1"})
    assert plan1["workflow_id"] == plan2["workflow_id"], "the same canonical item must dedupe to the same workflow"
    recorded = events.get_events(plan1["workflow_id"])
    identified_count = sum(1 for e in recorded if e["event_type"] == "IDENTIFIED")
    resolved_count = sum(1 for e in recorded if e["event_type"] == "RESOLVED")
    assert identified_count == 1 and resolved_count == 1, (
        "the first call logs IDENTIFIED (new workflow); the second logs "
        "RESOLVED (existing workflow) -- these are distinguishable event "
        "types precisely so a repeated observation is never confused with "
        "a fresh identification"
    )


@pytest.mark.asyncio
async def test_same_title_plans_keep_each_chat_confirmation_isolated_without_a_write(app, monkeypatch):
    """Global acquisition dedupe must not become global authorization state."""
    module, _events = app
    plan_a = await module.media_plan_goal({
        "goal": "get Dune 2021", "media_type": "movie", "session_id": "openwebui:user:chat-a",
    })
    plan_b = await module.media_plan_goal({
        "goal": "get Dune 2021", "media_type": "movie", "session_id": "openwebui:user:chat-b",
    })
    assert plan_a["workflow_id"] == plan_b["workflow_id"]
    assert plan_a["confirmation_record"]["confirmation_id"] != plan_b["confirmation_record"]["confirmation_id"]

    workflow = next(row for row in module._media_workflows() if row["workflow_id"] == plan_a["workflow_id"])
    pending = {item["confirmation_id"]: item for item in workflow["pending_confirmations"]}
    assert pending[plan_a["confirmation_record"]["confirmation_id"]]["session_id"] == "openwebui:user:chat-a"
    assert pending[plan_b["confirmation_record"]["confirmation_id"]]["session_id"] == "openwebui:user:chat-b"

    # Positive live evidence turns execution into a read-only no-op after all
    # confirmation/session/hash validation. Reaching no_op proves Chat B's
    # planning did not invalidate Chat A; no HTTP write boundary is reached.
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence",
                        lambda _payload: {"matched": True, "rows": [{"state": "queued"}]})
    result = await module.media_standard_request({
        "workflow_id": plan_a["workflow_id"], "media_type": "movie",
        "canonical_title": "Dune",
        "canonical_external_id": 438631,
        "confirmation_context": plan_a["confirmation_record"],
        "session_id": "openwebui:user:chat-a",
    })
    assert result["status"] == "no_op"
    assert result["write_executed"] is False
    workflow = next(row for row in module._media_workflows() if row["workflow_id"] == plan_a["workflow_id"])
    statuses = {item["confirmation_id"]: item["status"] for item in workflow["pending_confirmations"]}
    assert statuses[plan_a["confirmation_record"]["confirmation_id"]] == "INVALIDATED"
    assert statuses[plan_b["confirmation_record"]["confirmation_id"]] == "PENDING"


@pytest.mark.asyncio
async def test_status_check_finds_a_real_confirmed_request_by_natural_phrasing(app, monkeypatch):
    """Real bug found investigating a recurring user pain point ("after
    successfully requesting a movie, asking about its status later says it
    can't find/track it"): drives the FULL real lifecycle -- identify,
    confirm, fake write -- through the exact same production code as the
    confirmation round-trip test above, then asks for status the way a
    person actually would ("How is my Dune request going?"), not by the
    workflow_id or the bare title. Before the fix, media_status's
    title-extraction fallback required an EXACT normalized match against
    the stored canonical title; the crude regex-strip left "my Dune
    request" behind, which never equals "Dune", so a genuinely existing,
    correctly-identified request silently reported NOT_FOUND."""
    module, events = app

    plan = await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-1"})
    workflow_id = plan["workflow_id"]
    confirmation = plan["confirmation_record"]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

        content = b'{"ok": true}'

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(module, "httpx", type("FakeHttpxModule", (), {"AsyncClient": FakeAsyncClient}))
    evidence_calls = {"count": 0}

    def fake_evidence(payload):
        evidence_calls["count"] += 1
        # After the fake write, cli_debrid evidence reports the request as
        # actively queued/searching -- a realistic post-request state.
        return {"matched": evidence_calls["count"] > 1, "rows": [{"state": "queued"}] if evidence_calls["count"] > 1 else []}

    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", fake_evidence)

    result = await module.media_standard_request({
        "workflow_id": workflow_id, "media_type": "movie", "canonical_title": "Dune", "canonical_external_id": 438631,
        "confirmation_context": confirmation, "session_id": "sess-1",
    })
    assert result["status"] == "submitted" and result["write_executed"] is True

    for phrasing in (
        "How is my Dune request going?",
        "Did Dune download yet?",
        "is my dune request done",
        "Has my Dune request finished?",
    ):
        status = await module.media_status({"query": phrasing})
        assert status["found"] is True, f"real confirmed request not found for phrasing: {phrasing!r}"
        assert status["workflow_id"] == workflow_id
        assert status["canonical_identity"]["tmdb_id"] == 438631


@pytest.mark.asyncio
async def test_status_check_does_not_confuse_two_similarly_titled_requests(app):
    """Negative control: the filler-tolerant title matching in media_status
    must still fail closed on genuine ambiguity -- two different real
    workflows sharing the exact same title (a real, common case: a remake)
    must never be silently collapsed into one match. Also proves the
    fallback does not accidentally widen matching to an UNRELATED title
    that merely shares one word ("Dune: Part Two" must not match a query
    naming plain "Dune")."""
    module, events = app
    await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-1"})

    async def fake_radarr_search_part_two(args):
        return {"matches": [{"title": "Dune: Part Two", "year": "2024", "tmdbId": 693134}]}

    module.radarr_search = fake_radarr_search_part_two
    await module.media_plan_goal({"goal": "get Dune Part Two 2024", "media_type": "movie", "session_id": "sess-1"})

    # A query naming only "Dune" must match the "Dune" workflow alone --
    # "Dune: Part Two" shares one word but not the full title, so it must
    # never be treated as a candidate.
    status = await module.media_status({"query": "how is my dune request going"})
    assert status["found"] is True
    assert status["canonical_identity"]["title"] == "Dune"

    # Two DIFFERENT real workflows that genuinely share the exact same
    # title (e.g. a remake, or two Radarr entries the user requested
    # separately) must still fail closed as ambiguous, never guessed.
    async def fake_radarr_search_dune_1984(args):
        return {"matches": [{"title": "Dune", "year": "1984", "tmdbId": 950}]}

    module.radarr_search = fake_radarr_search_dune_1984
    await module.media_plan_goal({"goal": "get Dune 1984", "media_type": "movie", "session_id": "sess-1"})

    ambiguous_status = await module.media_status({"query": "how is my dune request going"})
    assert ambiguous_status["found"] is False
    assert ambiguous_status["status"] == "AMBIGUOUS"
    assert len(ambiguous_status["candidates"]) == 2


# --- Structural unreachability of media_standard_request from Qwen's own --
# --- tool-selection: the discovery-side half of the fix. -------------------

@pytest.mark.asyncio
async def test_media_standard_request_never_appears_in_discovery_for_any_query(app):
    """Real production bug: media_standard_request (a real write tool with
    its own dedicated hash/session-bound confirmation system) was
    reachable from Qwen's own tool-selection because nothing filtered it
    out of discover_capabilities()'s results -- Qwen selected it directly,
    invented its own arguments, and only failed because the tool's own
    internal validation happened to catch the malformed call. Swept across
    many query shapes, including obviously media-write-shaped ones, since
    the real live failure was triggered by exactly that kind of phrasing."""
    module, events = app
    for query in (
        "yes please request it", "get me primer 2004", "add the movie primer",
        "request dune", "download primer", "submit a request for dune",
        "confirm the request", "please add it", "media standard request",
        "", "what's the weather", "restart lidarr",
    ):
        max_results = 8 if query else 1
        results = module.discover_capabilities(query, max_results=max_results) if query else module.discover_capabilities("", max_results=1)
        names = {r["metadata"]["canonical_name"] for r in results}
        assert "media_standard_request" not in names, query
        assert "media_execute_goal" not in names, query


@pytest.mark.asyncio
async def test_fresh_session_verb_form_status_phrasings_find_the_real_workflow(app, monkeypatch):
    """Item 3's fresh-session integration test: ESP32-class voice devices
    will send every utterance as a brand-new, context-free session -- no
    shared conversation_context at all, ever. This test never touches
    conversation_context, pending, or any assistant-side session state --
    it drives the exact real request->confirm->fake-write lifecycle (same
    production code as the confirmation round-trip test above), then asks
    status purely via media_status({"query": ...}) with no workflow_id,
    proving the lookup works off the stored workflow + raw utterance text
    alone, for the natural VERB-form phrasing family a voice device would
    actually use ("Did I already request X?", not "is my X request
    going?")."""
    module, events = app

    plan = await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-1"})
    workflow_id = plan["workflow_id"]
    confirmation = plan["confirmation_record"]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

        content = b'{"ok": true}'

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(module, "httpx", type("FakeHttpxModule", (), {"AsyncClient": FakeAsyncClient}))
    evidence_calls = {"count": 0}

    def fake_evidence(payload):
        evidence_calls["count"] += 1
        return {"matched": evidence_calls["count"] > 1, "rows": [{"state": "queued"}] if evidence_calls["count"] > 1 else []}

    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", fake_evidence)

    result = await module.media_standard_request({
        "workflow_id": workflow_id, "media_type": "movie", "canonical_title": "Dune", "canonical_external_id": 438631,
        "confirmation_context": confirmation, "session_id": "sess-1",
    })
    assert result["status"] == "submitted" and result["write_executed"] is True

    # A genuinely fresh call, no prior state referenced whatsoever -- this
    # IS the fresh-session proof: media_status() only ever consults
    # _media_workflows() (the persisted store) and the raw query text.
    for phrasing in (
        "Did I already request Dune?",
        "Did I request Dune yet?",
        "Have I requested Dune?",
        "Did I ask for Dune already?",
        "Have I already asked for Dune?",
        "Was Dune ever requested?",
    ):
        status = await module.media_status({"query": phrasing})
        assert status["found"] is True, f"fresh-session lookup failed for: {phrasing!r}"
        assert status["workflow_id"] == workflow_id
        assert status["canonical_identity"]["tmdb_id"] == 438631


@pytest.mark.asyncio
async def test_fresh_session_status_check_for_never_requested_title_is_honest(app):
    """Item 5: a genuine "you never requested this" case (no workflow
    exists at all for the named title) must report NOT_FOUND honestly and
    specifically -- confirms what media_status actually returns so the
    assistant-side phrasing can reflect it clearly, rather than the vague
    "no matching live workflow" error-shaped wording."""
    module, events = app
    status = await module.media_status({"query": "Did I already request Interstellar?"})
    assert status["found"] is False
    assert status["status"] == "NOT_FOUND"


def test_media_standard_request_excluded_from_registry_endpoint_source(app):
    """The /registry endpoint (used when the assistant has no route text --
    the other real model-facing discovery entry point) must draw from the
    same filtered source, not iterate the raw REGISTRY directly."""
    module, events = app
    discoverable_names = {item[0] for item in module._discoverable_registry()}
    assert "media_standard_request" not in discoverable_names
    # The real invocation path must remain completely unaffected -- TOOLS
    # (used by /invoke) is built from the FULL, unfiltered REGISTRY.
    assert "media_standard_request" in module.TOOLS
    # A simple confirm-permission tool with no dedicated confirmation
    # system of its own must remain reachable exactly as before.
    assert "restart_container" in discoverable_names
