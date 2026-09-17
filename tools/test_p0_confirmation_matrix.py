"""Fail-closed, in-process confirmation acceptance matrix.

This suite deliberately never contacts a manager or cli_debrid.  It drives the
same Tools planning/execution functions with a temporary workflow/event store
and a counted fake HTTP boundary.  A test failure is therefore actionable
without risking a production mutation.
"""

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("tools_p0_matrix", ROOT / "server-tools-app.py")
module = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(ROOT))
SPEC.loader.exec_module(module)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    import workflow_events

    module.MEDIA_WORKFLOWS_PATH = tmp_path / "workflows.json"
    workflow_events.WORKFLOW_EVENTS_DB = tmp_path / "events.sqlite3"
    module.STANDARD_MEDIA_BACKEND_READY = True
    module.STANDARD_MEDIA_WRITES_ENABLED = True
    module.STANDARD_MOVIE_WRITES_ENABLED = True
    module._standard_bridge_secret = lambda: "fake-only-token"
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", lambda _: {"matched": False})
    monkeypatch.setattr(module, "plex_match_canonical_media", _async_false)
    async def fake_radarr(args):
        return {"matches": [{"title": "Dune", "year": "2021", "tmdbId": 438631}]}
    monkeypatch.setattr(module, "radarr_search", fake_radarr)
    monkeypatch.setattr(module, "sonarr_search", lambda args: _async_false())
    monkeypatch.setattr(module, "arr_get", _async_false)
    calls = []

    class Response:
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {"accepted": True}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, **kwargs):
            calls.append({"url": url, "json": json})
            return Response()

    monkeypatch.setattr(module, "httpx", type("FakeHttpx", (), {"AsyncClient": FakeClient}))
    return module, calls


async def _async_false(*args, **kwargs):
    return {"matched": False, "candidates": []}


async def _plan(mod, session):
    return await mod.media_plan_goal({
        "goal": "get Dune 2021", "media_type": "movie", "session_id": session,
    })


def _args(plan, session, **changes):
    result = {
        "workflow_id": plan["workflow_id"], "mode": "standard", "media_type": "movie",
        "canonical_external_id": 438631, "canonical_title": "Dune",
        "session_id": session, "confirmation_context": plan["confirmation_record"],
    }
    result.update(changes)
    return result


@pytest.mark.asyncio
async def test_matrix_a_no_pending_chat_b_yes_never_executes(harness):
    mod, calls = harness
    result = await mod.media_standard_request({"workflow_id": "missing", "session_id": "chat-b", "media_type": "movie", "canonical_external_id": 438631, "confirmation_context": {}})
    assert result["write_executed"] is False and not calls


@pytest.mark.asyncio
async def test_matrix_b_identical_prompts_confirm_only_b(harness):
    mod, calls = harness
    a, b = await _plan(mod, "openwebui:user:chat-a"), await _plan(mod, "openwebui:user:chat-b")
    result = await mod.media_standard_request(_args(b, "openwebui:user:chat-b"))
    assert result["write_executed"] is True and len(calls) == 1
    assert calls[0]["json"]["request"]["media_id"] == 438631
    workflow = next(r for r in mod._media_workflows() if r["workflow_id"] == a["workflow_id"])
    states = {item["confirmation_id"]: item["status"] for item in workflow["pending_confirmations"]}
    assert states[a["confirmation_record"]["confirmation_id"]] == "PENDING"
    assert states[b["confirmation_record"]["confirmation_id"]] == "CONSUMED"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "cross_session", "tamper_plan", "tamper_args", "consumed", "expired",
    "cancelled", "legacy", "missing_identity", "forged_identity",
])
async def test_matrix_negative_cases_never_execute(harness, case):
    mod, calls = harness
    plan = await _plan(mod, "openwebui:user:chat-a")
    binding = dict(plan["confirmation_record"])
    session = "openwebui:user:chat-a"
    current = _args(plan, session)
    if case == "cross_session" or case == "forged_identity":
        current["session_id"] = "openwebui:user:chat-b"
    elif case == "tamper_plan":
        current["canonical_external_id"] = 999
    elif case == "tamper_args":
        current["canonical_title"] = "Other Movie"
    elif case == "consumed" or case == "cancelled":
        binding["status"] = "CONSUMED" if case == "consumed" else "CANCELLED"
    elif case == "expired":
        binding["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    elif case == "legacy":
        current["session_id"] = "legacy:old-client"
        binding["session_id"] = "legacy:old-client"
    elif case == "missing_identity":
        current["session_id"] = ""
    current["confirmation_context"] = binding
    result = await mod.media_standard_request(current)
    assert result["write_executed"] is False, (case, result)
    assert not calls, (case, calls)


@pytest.mark.asyncio
async def test_matrix_replay_and_concurrent_approvals_at_most_once(harness):
    mod, calls = harness
    plan = await _plan(mod, "openwebui:user:chat-a")
    args = _args(plan, "openwebui:user:chat-a")
    first, second = await asyncio.gather(mod.media_standard_request(args), mod.media_standard_request(args))
    assert sum(bool(item.get("write_executed")) for item in (first, second)) == 1
    assert len(calls) == 1
    replay = await mod.media_standard_request(args)
    assert replay["write_executed"] is False
    assert len(calls) == 1
    row = next(r for r in mod._media_workflows() if r["workflow_id"] == plan["workflow_id"])
    assert row["confirmation_status"] in {"CONSUMED", "SUBMITTING"}


@pytest.mark.asyncio
async def test_matrix_restart_reconnect_does_not_resurrect_consumed(harness, tmp_path):
    mod, calls = harness
    plan = await _plan(mod, "openwebui:user:chat-a")
    args = _args(plan, "openwebui:user:chat-a")
    assert (await mod.media_standard_request(args))["write_executed"] is True
    # Reloading the module against the same persisted store models a process restart.
    fresh_spec = importlib.util.spec_from_file_location("tools_p0_restart", ROOT / "server-tools-app.py")
    fresh = importlib.util.module_from_spec(fresh_spec)
    fresh_spec.loader.exec_module(fresh)
    fresh.MEDIA_WORKFLOWS_PATH = mod.MEDIA_WORKFLOWS_PATH
    fresh.STANDARD_MEDIA_BACKEND_READY = True
    fresh.STANDARD_MEDIA_WRITES_ENABLED = True
    fresh._standard_bridge_secret = lambda: "fake-only-token"
    result = await fresh.media_standard_request(args)
    assert result["write_executed"] is False
    assert len(calls) == 1
