"""Safe integration tests for the real standard-media executor.

These tests import the production Home-AI-Tools module and exercise its planner,
confirmation binding, storage guard, idempotency check, and ingestion
acknowledgement logic.  The only replaced boundary is the outbound cli_debrid
HTTP call and the read-only provider/Plex observations.  No production network,
database, filesystem, or media service is reachable from the QA container.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx


def _load_tools():
    path = Path(__file__).parents[1] / "tools" / "server-tools-app.py"
    spec = importlib.util.spec_from_file_location("home_ai_tools_executor", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RecordingAsyncClient:
    posts = []

    def __init__(self, *args, **kwargs):
        self.headers = kwargs.get("headers", {})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json):
        self.posts.append({"url": url, "json": json, "headers": dict(self.headers)})
        return httpx.Response(
            200,
            json={"accepted": True, "source": "Overseerr_1"},
            request=httpx.Request("POST", url),
        )


class FailingAsyncClient(RecordingAsyncClient):
    async def post(self, url, json):
        self.posts.append({"url": url, "json": json, "headers": dict(self.headers)})
        raise httpx.ConnectError("simulated bridge outage", request=httpx.Request("POST", url))


def _configure(module, tmp_path, evidence):
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
    module.STANDARD_MEDIA_BACKEND_READY = True
    module.STANDARD_MEDIA_WRITES_ENABLED = True
    module.STANDARD_MOVIE_WRITES_ENABLED = True
    module.STANDARD_SEASON_WRITES_ENABLED = True
    module.CLIDEBRID_BASE = "http://fake-cli-debrid/webhook"
    module.CLIDEBRID_BRIDGE_TOKEN = "qa-only-token"
    module._standard_bridge_secret = lambda: "qa-only-token"
    module.httpx.AsyncClient = RecordingAsyncClient
    module._cli_debrid_exact_item_evidence = lambda payload: evidence.pop(0)

    async def fake_radarr_search(args):
        return {"matches": [{"title": "Dumb and Dumber", "year": 1994, "tmdbId": 8467}]}

    async def fake_arr_get(service, path, params=None):
        return []

    module.radarr_search = fake_radarr_search
    module.arr_get = fake_arr_get

    async def unmatched_plex(*args, **kwargs):
        return {"matched": False, "match_method": None, "candidates": []}

    module.plex_match_canonical_media = unmatched_plex
    RecordingAsyncClient.posts = []


def _plan_and_bound_args(module, session_id="qa-session"):
    plan = asyncio.run(module.media_plan_goal({
        "goal": "Get Dumb and Dumber from 1994",
        "media_type": "movie",
        "session_id": session_id,
    }))
    assert plan["canonical_identity"]["tmdb_id"] == 8467
    assert plan["confirmation_required"] is True
    record = plan["confirmation_record"]
    args = dict(record["arguments"])
    args["confirmation_context"] = record
    args["session_id"] = session_id
    return plan, record, args


def test_real_executor_confirms_ingestion_and_is_idempotent(tmp_path, monkeypatch):
    module = _load_tools()
    evidence = [
        {"matched": False, "media_type": "movie", "tmdb_id": 8467, "rows": []},
        {"matched": True, "media_type": "movie", "tmdb_id": 8467,
         "rows": [{"id": 41, "tmdb_id": 8467, "state": "Wanted", "type": "movie"}]},
    ]
    _configure(module, tmp_path, evidence)
    plan, record, args = _plan_and_bound_args(module)

    result = asyncio.run(module.media_standard_request(args))

    assert result["status"] == "submitted"
    assert result["ingestion_confirmed"] is True
    assert len(RecordingAsyncClient.posts) == 1
    request = RecordingAsyncClient.posts[0]
    assert request["url"] == "http://fake-cli-debrid/webhook/"
    assert request["json"]["media"]["media_type"] == "movie"
    assert request["json"]["media"]["tmdbId"] == 8467
    assert request["json"]["request"]["requestedBy_username"] == "Home-AI"
    assert "token" not in json.dumps(request["json"]).casefold()

    workflow = module._workflow_for_id(plan["workflow_id"])[1]
    assert workflow["canonical_state"] == "REQUESTED"
    assert workflow["confirmation_status"] == "CONSUMED"

    # A consumed confirmation cannot be replayed into a second webhook.
    replay = asyncio.run(module.media_standard_request(args))
    assert replay["status"] == "rejected"
    assert replay["reason"] == "CONFIRMATION_ALREADY_CONSUMED"
    assert len(RecordingAsyncClient.posts) == 1


def test_http_success_without_exact_persistence_is_failed_ingestion(tmp_path):
    module = _load_tools()
    evidence = [
        {"matched": False, "media_type": "movie", "tmdb_id": 8467, "rows": []},
        {"matched": False, "media_type": "movie", "tmdb_id": 8467, "rows": []},
    ]
    _configure(module, tmp_path, evidence)
    plan, record, args = _plan_and_bound_args(module)

    result = asyncio.run(module.media_standard_request(args))

    assert result["status"] == "failed_ingestion"
    assert result["submission_transport_success"] is True
    assert result["ingestion_confirmed"] is False
    assert result["reason"] == "CONTENT_SOURCE_NOT_MATCHED"
    assert len(RecordingAsyncClient.posts) == 1
    workflow = module._workflow_for_id(plan["workflow_id"])[1]
    assert workflow["canonical_state"] == "FAILED_INGESTION"
    assert workflow["confirmation_status"] == "CONSUMED"


def test_bridge_transport_failure_returns_structured_unavailable_and_consumes_approval(tmp_path):
    module = _load_tools()
    _configure(module, tmp_path, [{"matched": False, "rows": []}])
    module.httpx.AsyncClient = FailingAsyncClient
    plan, record, args = _plan_and_bound_args(module)

    result = asyncio.run(module.media_standard_request(args))

    assert result["status"] == "unavailable"
    assert result["reason"] == "BRIDGE_UNAVAILABLE"
    assert result["submission_transport_success"] is False
    assert result["ingestion_confirmed"] is False
    assert result["write_executed"] is False
    workflow = module._workflow_for_id(plan["workflow_id"])[1]
    assert workflow["canonical_state"] == "FAILED_INGESTION"
    assert workflow["failure_reason"] == "BRIDGE_UNAVAILABLE"
    assert workflow["confirmation_status"] == "CONSUMED"

    replay = asyncio.run(module.media_standard_request(args))
    assert replay["status"] == "rejected"
    assert replay["reason"] == "CONFIRMATION_ALREADY_CONSUMED"


def test_exact_live_item_produces_noop_without_post(tmp_path):
    module = _load_tools()
    evidence = [{
        "matched": True,
        "media_type": "movie",
        "tmdb_id": 8467,
        "rows": [{"id": 41, "tmdb_id": 8467, "state": "Scraping", "type": "movie"}],
    }]
    _configure(module, tmp_path, evidence)
    plan, record, args = _plan_and_bound_args(module)

    result = asyncio.run(module.media_standard_request(args))

    assert result["status"] == "no_op"
    assert result["write_executed"] is False
    assert len(RecordingAsyncClient.posts) == 0
    workflow = module._workflow_for_id(plan["workflow_id"])[1]
    assert workflow["canonical_state"] == "REQUESTED"
    assert workflow["confirmation_status"] == "INVALIDATED"


def test_storage_contract_rejects_provider_destination_from_bounded_args(tmp_path):
    module = _load_tools()
    _configure(module, tmp_path, [{"matched": False, "rows": []}])
    args = {
        "workflow_id": "not-created",
        "media_type": "movie",
        "canonical_external_id": 8467,
        "canonical_title": "Dumb and Dumber",
        "mode": "standard",
        "rootFolder": "/data/media/movies",
    }
    result = asyncio.run(module.media_standard_request(args))
    assert result["status"] == "rejected"
    assert result["reason"] == "UNEXPECTED_ARGUMENT"
    assert not RecordingAsyncClient.posts
