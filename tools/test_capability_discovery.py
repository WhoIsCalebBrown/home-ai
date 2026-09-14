import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("server_tools_app", Path(__file__).with_name("server-tools-app.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def names(query):
    return [item["metadata"]["canonical_name"] for item in module.discover_capabilities(query, 8)]


def test_discovery_distinguishes_camera_capabilities():
    assert names("is the front door camera working")[:1] == ["frigate_stats"]
    assert names("was someone at the front door recently")[:1] == ["frigate_recent_events"]
    assert names("describe the front door right now")[:1] == ["frigate_snapshot"]
    assert names("describe the image from that detection")[:1] == ["frigate_event_snapshot"]


def test_discovery_alerts_are_events_not_camera_stats():
    assert names("any alerts from the front camera")[:1] == ["frigate_recent_events"]


def test_discovery_handles_local_aliases_and_utilities():
    assert "lidarr_health" in names("what is the status of LIDAR")[:4]
    assert names("what is 17.5 percent of 438")[0] == "calculator"
    assert names("convert 5 GB to MB")[0] == "unit_convert"


def test_calculator_and_units_are_deterministic():
    import asyncio
    assert asyncio.run(module.calculator({"expression": "17.5 * 438 / 100"}))["value"] == 76.65
    assert round(asyncio.run(module.unit_convert({"value": 5, "from_unit": "GB", "to_unit": "MB"}))["result"]) == 5000


def test_discovery_is_bounded():
    assert len(module.discover_capabilities("server media camera internet", 8)) <= 8


def test_lidarr_missing_tracks_preserves_album_identifier(monkeypatch):
    import asyncio

    async def fake_arr_get(service, path, args=None):
        return {"totalRecords": 1, "records": [{"id": 395, "title": "HOOD POET", "artist": {"artistName": "Polo G"}}]}

    monkeypatch.setattr(module, "arr_get", fake_arr_get)
    result = asyncio.run(module.arr_missing("lidarr", {}))
    assert result["items"][0]["album_id"] == 395


def test_media_capability_registry_is_semantic_and_writes_are_not_enabled():
    assert "media.library.check" in module.MEDIA_CAPABILITY_REGISTRY["plex"]["capabilities"]
    assert "media.acquire" in module.MEDIA_CAPABILITY_REGISTRY["torbox-client"]["capabilities"]
    assert module.MEDIA_CAPABILITY_REGISTRY["torbox-client"]["risk"] == "READ_ONLY"
    assert "media.request" in module.MEDIA_CAPABILITY_REGISTRY["lidarr"]["capabilities"]


def test_media_goal_parsing_preserves_identity_parts():
    rodeo = module._media_goal_parts("Get Rodeo by Travis Scott")
    assert rodeo["media_type"] == "album"
    assert rodeo["title_query"] == "Rodeo"
    assert rodeo["artist_query"] == "Travis Scott"
    hobbit = module._media_goal_parts("Get the original animated Hobbit movie")
    assert hobbit["media_type"] == "movie"
    assert "Hobbit" in hobbit["title_query"]


def test_media_plan_is_read_only_and_idempotent(monkeypatch, tmp_path):
    import asyncio
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"

    async def album_lookup(_):
        return {"matches": [{"title": "Rodeo", "artist": "Travis Scott", "release_date": "2015-09-04", "foreign_album_id": "album-1", "album_type": "Album"}]}

    async def plex_lookup(_):
        return {"matches": [], "available": False}

    async def managed(*_):
        return []

    monkeypatch.setattr(module, "lidarr_search_album", album_lookup)
    monkeypatch.setattr(module, "plex_library_lookup", plex_lookup)
    monkeypatch.setattr(module, "arr_get", managed)
    first = asyncio.run(module.media_plan_goal({"goal": "Get Rodeo by Travis Scott"}))
    second = asyncio.run(module.media_plan_goal({"goal": "Get Rodeo by Travis Scott"}))
    assert first["plan_only"] is True
    assert first["writes_required"][0]["status"] in {"NOT_EXECUTED", "BLOCKED_POLICY"}
    assert first["workflow_id"] == second["workflow_id"]
    assert len(module._media_workflows()) == 1


def test_media_policy_is_centralized_and_fails_closed():
    assert module.MEDIA_POLICY["music"]["root_folder"] == "/data/media/music"
    assert module.MEDIA_POLICY["music"]["quality_profile_id"] == 2
    assert module.MEDIA_POLICY["movies"]["quality_profile_id"] is None
    assert module.MEDIA_POLICY["anime"]["quality_profile_id"] is None


def test_media_confirmation_binds_session_plan_and_expiry():
    from datetime import datetime, timedelta, timezone

    plan = {"canonical_identity": {"media_type": "movie", "title": "The Hobbit", "tmdb_id": 1362}}
    record = module.media_confirmation_record(
        workflow_id="wf-hobbit",
        plan=plan,
        session_id="session-a",
        operation="radarr.add_movie",
        arguments={"tmdb_id": 1362, "quality_profile_id": 11},
    )
    assert module.validate_media_confirmation(record, session_id="session-a", current_plan=plan)[0]
    assert module.validate_media_confirmation(record, session_id="session-b", current_plan=plan)[1] == "SESSION_MISMATCH"
    changed = {**plan, "canonical_identity": {**plan["canonical_identity"], "tmdb_id": 999}}
    assert module.validate_media_confirmation(record, session_id="session-a", current_plan=changed)[1] == "PLAN_CHANGED"
    expired = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert module.validate_media_confirmation(record, session_id="session-a", current_plan=plan, now_value=expired)[1] == "EXPIRED"
