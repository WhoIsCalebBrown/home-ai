import importlib.util
import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

BRIDGE_PATH = Path(__file__).parents[1] / "deployment/vps-cli-bridge/bridge.py"
spec = importlib.util.spec_from_file_location("vps_cli_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "media_items.db"
    c = sqlite3.connect(p)
    c.execute("create table media_items (id integer, tmdb_id text, state text, type text, location_on_disk text)")
    c.executemany("insert into media_items values (?,?,?,?,?)", [
        (1, "27205", "Collected", "movie", "/data/symlinked/Movies/Inception (2010)/Inception.mkv"),
        (2, "999", "Wanted", "movie", None),
        (3, "27205", "Wanted", "episode", None),
    ])
    c.commit(); c.close()
    monkeypatch.setattr(bridge, "DB_PATH", str(p))
    return p


def test_exact_collected_movie_uses_live_read_only_sqlite(db):
    result = bridge.exact_movie_status(27205)
    assert result["status"] == "present"
    assert result["vps_collected"] is True
    assert result["replica_paths"] == ["/Movies/Inception (2010)/Inception.mkv"]
    assert result["rows"] == [{"state": "Collected", "vps_collected": True,
                               "replica_path": "/Movies/Inception (2010)/Inception.mkv"}]


def test_exact_absent_id_and_in_progress_movie_are_distinct(db):
    assert bridge.exact_movie_status(123456)["status"] == "absent"
    result = bridge.exact_movie_status(999)
    assert result["status"] == "present" and not result["vps_collected"]
    assert result["rows"][0]["state"] == "Wanted"


def test_status_query_is_read_only_under_wal(db):
    c = sqlite3.connect(db)
    c.execute("pragma journal_mode=WAL")
    c.execute("insert into media_items values (4,'444','Checking','movie',null)")
    c.commit()
    assert bridge.exact_movie_status(444)["rows"][0]["state"] == "Checking"
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("delete from media_items")
    ro.close()


def test_only_allowed_movie_request_shape(monkeypatch):
    payload = {"notification_type": "MEDIA_PENDING", "subject": "Home-AI standard request movie",
               "request": {"request_id": "home_ai_wf-123", "requestedBy_username": "Home-AI",
                           "requestedBy_email": "home-ai@system"},
               "media": {"media_type": "movie", "tmdbId": 27205, "from_overseerr": True}}
    assert bridge.validate_request(payload) == payload
    with pytest.raises(ValueError):
        bridge.validate_request({**payload, "media": {**payload["media"], "media_type": "tv"}})
    with pytest.raises(ValueError):
        bridge.validate_request({**payload, "sql": "select *"})


def test_request_forwards_only_valid_payload(monkeypatch):
    seen = {}
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def fake_open(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        seen["timeout"] = timeout
        return Response()
    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_open)
    payload = {"notification_type": "MEDIA_PENDING", "subject": "Home-AI standard request movie",
               "request": {"request_id": "home_ai_wf-123", "requestedBy_username": "Home-AI",
                           "requestedBy_email": "home-ai@system"},
               "media": {"media_type": "movie", "tmdbId": 27205, "from_overseerr": True}}
    assert bridge.forward_movie_request(payload) == {"accepted": True, "authority": "vps_cli_debrid"}
    assert seen["url"] == "http://127.0.0.1:5000/webhook/"
    assert seen["body"] == payload


def test_http_status_requires_auth_and_returns_only_scoped_data(db, monkeypatch):
    monkeypatch.setattr(bridge, "TOKEN_FILE", "/unused-test-path")
    monkeypatch.setattr(bridge, "read_token", lambda: b"test-only-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/movies/27205"
        with pytest.raises(HTTPError) as error:
            urlopen(url, timeout=2)
        assert error.value.code == 401
        req = Request(url, headers={"X-Home-AI-Bridge-Token": "test-only-secret"})
        with urlopen(req, timeout=2) as response:
            result = json.load(response)
        assert result["status"] == "present" and result["vps_collected"] is True
        assert "title" not in result and "location_on_disk" not in result
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.asyncio
async def test_ambiguous_post_reconciles_before_any_retry(tmp_path, monkeypatch):
    from test_media_workflow_integration import _load_app
    module, _events = _load_app(tmp_path)
    plan = await module.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie", "session_id": "sess-amb"})
    evidence_calls = {"count": 0}
    def evidence(_payload):
        evidence_calls["count"] += 1
        if evidence_calls["count"] == 1:
            return {"matched": False, "rows": []}  # preflight
        return {"matched": True, "rows": [{"state": "Wanted"}]}  # POST outcome reconciliation
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", evidence)
    requests = {"count": 0}
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, *a, **k):
            requests["count"] += 1
            raise module.httpx.TimeoutException("simulated timeout")
    monkeypatch.setattr(module, "httpx", type("FakeHttpxModule", (), {"AsyncClient": FakeClient}))
    module.VPS_CLIDEBRID_BRIDGE_URL = "http://bridge.test"
    result = await module.media_standard_request({
        "workflow_id": plan["workflow_id"], "media_type": "movie", "canonical_title": "Dune",
        "canonical_external_id": 438631, "confirmation_context": plan["confirmation_record"],
        "session_id": "sess-amb",
    })
    assert result["status"] == "submitted" and result["retry_safe"] is False
    assert requests["count"] == 1 and evidence_calls["count"] == 2


@pytest.mark.asyncio
async def test_vps_collection_replication_and_local_plex_visibility_are_distinct(tmp_path, monkeypatch):
    from test_media_workflow_integration import _load_app
    module, _events = _load_app(tmp_path)
    module._save_media_workflows([{
        "workflow_id": "wf-state", "media_type": "movie", "mode": "standard",
        "canonical_identity": {"media_type": "movie", "tmdb_id": 27205, "title": "Inception", "year": 2010},
        "current_state": "QUEUED", "storage_class": "debrid",
    }])
    async def plex(args):
        return {"matched": args.get("library") == "Movies-DB" and module.VISIBLE,
                "match_method": "tmdb", "candidates": []}
    module.VISIBLE = False
    async def noop(*_args, **_kwargs): return {"matched": False, "candidates": []}
    module.plex_match_canonical_media = plex
    module._cli_debrid_exact_item_evidence = lambda _: {
        "matched": True, "rows": [{"state": "Collected"}], "vps_collected": True,
        "replica_paths": ["/Movies/Inception (2010)/Inception.mkv"],
    }
    module._vps_catalog_path_replicated = lambda _path: False
    assert (await module.media_status({"workflow_id": "wf-state"}))["canonical_state"] == "COLLECTED_NOT_REPLICATED"
    module._vps_catalog_path_replicated = lambda _path: True
    assert (await module.media_status({"workflow_id": "wf-state"}))["canonical_state"] == "REPLICATED_NOT_VISIBLE"
    module.VISIBLE = True
    assert (await module.media_status({"workflow_id": "wf-state"}))["canonical_state"] == "VISIBLE_IN_PLEX_LOCAL"
