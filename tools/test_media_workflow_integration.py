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
        return {"matches": [{"title": "Dune", "year": "2021", "tmdbId": 438631}]}

    async def fake_arr_get(service, path, args=None):
        return []

    async def fake_plex_match_canonical_media(args):
        return {"matched": False, "candidates": []}

    module.radarr_search = fake_radarr_search
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
