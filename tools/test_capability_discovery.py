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


def test_unrelated_activity_language_does_not_select_frigate():
    assert names("how is Dumb and Dumber doing")[:1] in (["media_status"], ["media_get_workflow"])
    assert names("what happened today in American politics")[:1] == ["web_search"]


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


def test_frigate_version_parser_accepts_live_plain_text_response():
    assert module.parse_text_or_json_payload("0.17.2-3d4dd3a\n") == "0.17.2-3d4dd3a"
    assert module.parse_text_or_json_payload('{"version":"0.17.2"}') == {"version": "0.17.2"}


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
    assert module.MEDIA_POLICY["movies"]["quality_profile_id"] == 11
    assert module.MEDIA_POLICY["anime"]["quality_profile_id"] == 17


def test_standard_storage_isolated_from_permanent_paths():
    assert module.MEDIA_STORAGE_POLICY["movie"]["standard"] == {
        "library": "Movies-DB", "path": "/data/symlinked/Movies"
    }
    assert module.MEDIA_STORAGE_POLICY["tv"]["standard"] == {
        "library": "TV Shows-DB", "path": "/data/symlinked/TV Shows"
    }
    assert module.MEDIA_STORAGE_POLICY["anime"]["standard"] == {
        "library": "Anime-DB", "path": "/data/symlinked/Anime TV Shows"
    }
    assert module.MEDIA_STORAGE_POLICY["movie"]["permanent"]["path"] == "/data/media/movies"
    assert module.MEDIA_STORAGE_POLICY["tv"]["permanent"]["path"] == "/data/media/tv"
    assert module.MEDIA_STORAGE_POLICY["anime"]["permanent"]["path"] == "/data/media/anime"
    assert module._validate_standard_storage_contract("movie", module._build_cli_debrid_request({
        "media_type": "movie", "canonical_external_id": 1362,
    })) == (True, "VALID")


def test_standard_bridge_rejects_destination_override():
    import asyncio
    result = asyncio.run(module.media_standard_request({
        "workflow_id": "wf", "media_type": "movie", "canonical_external_id": 1362,
        "root_folder": "/data/media/movies",
    }))
    assert result["reason"] == "UNEXPECTED_ARGUMENT"


def test_canonical_plex_match_rejects_hobbit_trilogy_for_1977():
    requested = {"title": "The Hobbit", "year": 1977, "external_ids": {"tmdb": "1362"}}
    trilogy = [
        {"title": "The Hobbit: An Unexpected Journey", "year": 2012, "external_ids": {"tmdb": " Hobbit-2012 "}},
        {"title": "The Hobbit: The Desolation of Smaug", "year": 2013, "external_ids": {"tmdb": "1170358"}},
        {"title": "The Hobbit: The Battle of the Five Armies", "year": 2014, "external_ids": {"tmdb": "2310332"}},
    ]
    assert all(module._evaluate_plex_candidate(row, requested).get("rejected_reason") == "canonical_identity_mismatch"
               for row in trilogy)


def test_canonical_plex_match_remake_and_exact_positive_cases():
    for title, year, requested_id, other_id in [
        ("Dune", 1984, "841", "438631"),
        ("The Lion King", 1994, "8587", "420818"),
        ("Suspiria", 1977, "11906", "361292"),
    ]:
        requested = {"title": title, "year": year, "external_ids": {"tmdb": requested_id}}
        assert module._evaluate_plex_candidate({"title": title, "year": year, "external_ids": {"tmdb": other_id}}, requested)["rejected_reason"] == "canonical_identity_mismatch"
        assert module._evaluate_plex_candidate({"title": title, "year": year, "external_ids": {"tmdb": requested_id}}, requested)["match_method"] == "tmdb"


def test_canonical_plex_match_falls_back_only_to_exact_title_year():
    requested = {"title": "The Hobbit", "year": 1977, "external_ids": {"tmdb": "1362"}}
    assert module._evaluate_plex_candidate({"title": "The Hobbit", "year": 1977, "external_ids": {}}, requested)["match_method"] == "title_year"
    assert module._evaluate_plex_candidate({"title": "The Hobbit", "year": 2012, "external_ids": {}}, requested)["rejected_reason"] == "year_mismatch"


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


def test_cli_debrid_standard_request_shape_is_canonical_and_bounded():
    assert module._build_cli_debrid_request({
        "media_type": "movie", "canonical_external_id": 1362,
    }) == {
        "mediaType": "movie", "mediaId": 1362, "is4k": False,
        "serverId": 0, "profileId": 0, "rootFolder": "/", "userId": 1,
    }
    assert module._build_cli_debrid_request({
        "media_type": "tv", "canonical_external_id": 95396, "season_scope": [2, 2],
    })["seasons"] == [2]


def test_cli_debrid_standard_request_rejects_episode_scope_and_is_disabled_by_default():
    import asyncio
    import pytest

    with pytest.raises(ValueError, match="STANDARD_EPISODE_SCOPE_UNSUPPORTED"):
        module._build_cli_debrid_request({
            "media_type": "tv", "canonical_external_id": 1, "episode_scope": [8],
        })
    result = asyncio.run(module.media_standard_request({
        "workflow_id": "wf-test",
        "media_type": "movie",
        "canonical_external_id": 1362,
        "confirmation_context": {
            "confirmation_id": "c", "session_id": "s", "plan_version_hash": "p",
            "arguments_hash": "a", "expires_at": "2099-01-01T00:00:00+00:00",
        },
    }))
    assert result["status"] == "disabled"
    assert result["write_executed"] is False


def test_cli_debrid_standard_request_accepts_bound_standard_mode():
    import asyncio

    result = asyncio.run(module.media_standard_request({
        "workflow_id": "wf-test",
        "mode": "standard",
        "media_type": "movie",
        "canonical_external_id": 1362,
    }))
    assert result["status"] == "disabled"
    assert result["reason"] != "UNEXPECTED_ARGUMENT"


def test_pending_confirmation_is_invalidated_when_live_provider_already_has_item():
    workflow = {"confirmation_status": "PENDING"}
    module._invalidate_confirmation(workflow, "LIVE_CLIDEBRID_REQUEST_OR_COLLECTION_EXISTS")
    assert workflow["confirmation_status"] == "INVALIDATED"
    assert workflow["confirmation_invalidated_reason"] == "LIVE_CLIDEBRID_REQUEST_OR_COLLECTION_EXISTS"


def test_enabled_executor_revalidates_provider_before_using_stale_active_state(monkeypatch, tmp_path):
    import asyncio
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
    module.STANDARD_MEDIA_BACKEND_READY = True
    module.STANDARD_MEDIA_WRITES_ENABLED = True
    module.STANDARD_MOVIE_WRITES_ENABLED = True
    module._standard_bridge_secret = lambda: "test-secret"
    args = {"workflow_id": "wf-stale-active", "mode": "standard", "media_type": "movie",
            "canonical_external_id": 8467, "canonical_title": "Dumb and Dumber", "season_scope": [],
            "episode_scope": [], "session_id": "session-a"}
    plan = {"canonical_identity": {"media_type": "movie", "tmdb_id": 8467, "title": "Dumb and Dumber", "year": 1994}}
    confirmation_args = {key: args[key] for key in ("workflow_id", "mode", "media_type",
                                                      "canonical_external_id", "season_scope", "episode_scope")}
    record = module.media_confirmation_record(workflow_id=args["workflow_id"], plan=plan,
                                              session_id="session-a", operation="cli_debrid.webhook",
                                              arguments=confirmation_args)
    module._save_media_workflows([{
        "workflow_id": args["workflow_id"], "media_type": "movie", "mode": "standard",
        "canonical_identity": plan["canonical_identity"], "current_state": "SEARCHING",
        "plan_version_hash": record["plan_version_hash"], "confirmation_id": record["confirmation_id"],
        "confirmation_status": "PENDING",
    }])
    args["confirmation_context"] = record
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", lambda _: {
        "matched": True, "rows": [{"state": "Wanted", "tmdb_id": 8467, "type": "movie"}]
    })
    result = asyncio.run(module.media_standard_request(args))
    assert result["status"] == "no_op", result
    assert result["write_executed"] is False
    assert module._media_workflows()[0]["confirmation_status"] == "INVALIDATED"


def test_standard_binding_changes_when_scope_or_identity_changes():
    movie = {"workflow_id": "wf", "media_type": "movie", "canonical_external_id": 1362}
    season = {"workflow_id": "wf", "media_type": "tv", "canonical_external_id": 95396, "season_scope": [2]}
    assert module._standard_binding_hash(movie) != module._standard_binding_hash({**movie, "canonical_external_id": 999})
    assert module._standard_binding_hash(season) != module._standard_binding_hash({**season, "season_scope": [1]})


def test_cli_debrid_supported_webhook_payload_uses_semantic_identity_only():
    payload = module._build_cli_debrid_overseerr_webhook(
        {"media_type": "movie", "canonical_external_id": 1362}, "wf-hobbit"
    )
    assert payload["request"]["requestedBy_username"] == "Home-AI"
    assert payload["media"] == {"media_type": "movie", "tmdbId": 1362, "from_overseerr": True}
    assert "serverId" not in payload and "rootFolder" not in payload


def test_cli_debrid_season_webhook_preserves_exact_scope():
    payload = module._build_cli_debrid_overseerr_webhook(
        {"media_type": "tv", "canonical_external_id": 95396, "season_scope": [2, 2]}, "wf-severance"
    )
    assert payload["media"]["requested_seasons"] == [2, 2]
    assert payload["extra"] == [{"name": "Requested Seasons", "value": "2,2"}]


def test_cli_debrid_ingestion_ack_uses_tmdb_not_title(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "media_items.db"
    connection = sqlite3.connect(db)
    connection.execute("create table media_items (id integer, tmdb_id integer, title text, year integer, state text, type text, season_number integer, episode_number integer, requested_season integer, location_on_disk text, plex_verified integer)")
    connection.execute("insert into media_items values (1, 999, 'The Hobbit', 2012, 'Collected', 'movie', null, null, null, '/data/symlinked/Movies', 1)")
    connection.commit(); connection.close()
    monkeypatch.setattr(module, "CLIDEBRID_DB_PATH", str(db))
    payload = module._build_cli_debrid_overseerr_webhook({"media_type": "movie", "canonical_external_id": 1362}, "wf")
    evidence = module._cli_debrid_exact_item_evidence(payload)
    assert evidence["matched"] is False
    assert module._cli_debrid_failure_reason(evidence) == "CONTENT_SOURCE_NOT_MATCHED"


def test_cli_debrid_true_ingestion_ack_is_exact_tmdb(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "media_items.db"
    connection = sqlite3.connect(db)
    connection.execute("create table media_items (id integer, tmdb_id integer, title text, year integer, state text, type text, season_number integer, episode_number integer, requested_season integer, location_on_disk text, plex_verified integer)")
    connection.execute("insert into media_items values (2, 1362, 'The Hobbit', 1977, 'Wanted', 'movie', null, null, null, null, 0)")
    connection.commit(); connection.close()
    monkeypatch.setattr(module, "CLIDEBRID_DB_PATH", str(db))
    payload = module._build_cli_debrid_overseerr_webhook({"media_type": "movie", "canonical_external_id": 1362}, "wf")
    assert module._cli_debrid_exact_item_evidence(payload)["matched"] is True


def test_cli_debrid_true_season_ack_uses_live_episode_schema_and_scope(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "media_items.db"
    connection = sqlite3.connect(db)
    connection.execute("create table media_items (id integer, tmdb_id integer, title text, year integer, state text, type text, season_number integer, episode_number integer, requested_season integer, location_on_disk text, plex_verified integer)")
    connection.execute("insert into media_items values (3, 95396, 'Severance', 2022, 'Wanted', 'episode', 2, 8, 0, null, 0)")
    connection.commit(); connection.close()
    monkeypatch.setattr(module, "CLIDEBRID_DB_PATH", str(db))
    payload = module._build_cli_debrid_overseerr_webhook(
        {"media_type": "tv", "canonical_external_id": 95396, "season_scope": [2]}, "wf-severance"
    )
    evidence = module._cli_debrid_exact_item_evidence(payload)
    assert evidence["matched"] is True
    assert evidence["scoped_rows"][0]["season_number"] == 2


def test_media_status_uses_live_provider_state_and_does_not_trust_stale_workflow(monkeypatch, tmp_path):
    import asyncio
    import json
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
    module.MEDIA_WORKFLOWS_PATH.write_text(json.dumps([{
        "workflow_id": "wf-hobbit", "media_type": "movie", "mode": "standard",
        "current_state": "SEARCHING", "storage_class": "debrid",
        "canonical_identity": {"title": "The Hobbit", "year": 1977, "tmdb_id": 1362},
    }]))

    async def no_match(_):
        return {"matched": False, "match_method": None, "candidates": []}

    monkeypatch.setattr(module, "plex_match_canonical_media", no_match)
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", lambda _: {
        "matched": True, "rows": [{"state": "Collected", "tmdb_id": 1362, "type": "movie"}]
    })
    result = asyncio.run(module.media_status({"workflow_id": "wf-hobbit"}))
    assert result["canonical_state"] == "ACQUIRED_NOT_VISIBLE"
    assert result["source_workflow_state"] == "SEARCHING"


def test_media_status_does_not_report_blacklisted_item_as_requested(monkeypatch, tmp_path):
    import asyncio
    import json
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
    module.MEDIA_WORKFLOWS_PATH.write_text(json.dumps([{
        "workflow_id": "wf-dumb", "media_type": "movie", "mode": "standard",
        "current_state": "REQUESTED", "canonical_identity": {"title": "Dumb and Dumber", "year": 1994, "tmdb_id": 8467}
    }]))
    async def no_match(_): return {"matched": False, "candidates": []}
    monkeypatch.setattr(module, "plex_match_canonical_media", no_match)
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", lambda _: {
        "matched": True, "rows": [{"state": "Blacklisted", "tmdb_id": 8467, "type": "movie"}]
    })
    result = asyncio.run(module.media_status({"workflow_id": "wf-dumb"}))
    assert result["canonical_state"] == "NO_CANDIDATE"


def test_media_status_never_falls_back_to_stale_workflow_on_provider_read_failure(monkeypatch, tmp_path):
    import asyncio
    import json
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
    module.MEDIA_WORKFLOWS_PATH.write_text(json.dumps([{
        "workflow_id": "wf-stale", "media_type": "movie", "mode": "standard",
        "current_state": "SEARCHING", "canonical_identity": {"title": "Example", "year": 2020, "tmdb_id": 123}
    }]))
    async def no_match(_): return {"matched": False, "candidates": []}
    monkeypatch.setattr(module, "plex_match_canonical_media", no_match)
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", lambda _: {
        "matched": False, "rows": [], "error": "OperationalError"
    })
    result = asyncio.run(module.media_status({"workflow_id": "wf-stale"}))
    assert result["canonical_state"] == "PARTIAL_STATUS"
    assert result["status_reason"] == "CLI_DEBRID_READ_FAILED"


def test_media_status_preserves_exact_plex_availability_when_provider_read_fails(monkeypatch, tmp_path):
    import asyncio
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
    module._save_media_workflows([{
        "workflow_id": "wf-visible", "media_type": "movie", "mode": "standard",
        "canonical_identity": {"media_type": "movie", "tmdb_id": 1362, "title": "The Hobbit", "year": 1977},
        "current_state": "SEARCHING", "storage_class": "debrid",
    }])

    async def plex(args):
        return {"matched": args.get("library") == "Movies-DB", "match_method": "tmdb", "plex_rating_key": "79598"}

    monkeypatch.setattr(module, "plex_match_canonical_media", plex)
    monkeypatch.setattr(module, "_cli_debrid_exact_item_evidence", lambda _: {"error": "database unavailable", "rows": []})
    result = asyncio.run(module.media_status({"workflow_id": "wf-visible"}))
    assert result["canonical_state"] == "AVAILABLE"
    assert result["storage_class"] == "debrid"
    assert result["status_reason"] == "CLI_DEBRID_READ_FAILED"


def test_movie_plan_surfaces_cross_domain_tv_candidate_without_writing(monkeypatch, tmp_path):
    import asyncio
    module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"

    async def no_movie(_):
        return {"matches": []}

    async def tv_candidate(_):
        return {"matches": [{"title": "The 10th Kingdom", "year": 2000, "tmdbId": 40546, "tvdbId": 78886, "seriesType": "standard"}]}

    monkeypatch.setattr(module, "radarr_search", no_movie)
    monkeypatch.setattr(module, "sonarr_search", tv_candidate)
    result = asyncio.run(module.media_plan_goal({"goal": "Please request the 10th Kingdom movie."}))
    assert result["canonical_identity"] is None
    assert result["ambiguity_reason"] == "CROSS_DOMAIN_CANDIDATE"
    assert result["candidates"][0]["tmdb_id"] == 40546
    assert result["writes_required"] == []
    assert result["confirmation_required"] is False
    assert result["workflow_id"] is None
    assert module._media_workflows() == []
