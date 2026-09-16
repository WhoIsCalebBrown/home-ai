"""Tests for the generic query-drift guardrail in media_plan_goal.

Not "The Room"-specific -- that instance of the bug is fixed at its source
in _media_goal_parts (see test_media_goal_title_extraction.py). This module
proves the SEPARATE, generic guardrail: even if some future extraction bug
mangles the search string, a candidate whose title has drifted too far from
what the user actually said must never be silently accepted as
canonical_identity -- it must be routed through the existing disambiguation
machinery instead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import importlib.util
spec = importlib.util.spec_from_file_location("server_tools_app_drift_test", Path(__file__).with_name("server-tools-app.py"))
_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_module)
_title_similarity = _module._title_similarity
_query_drift_detected = _module._query_drift_detected
_query_drift_check_skipped = _module._query_drift_check_skipped
QUERY_DRIFT_SIMILARITY_THRESHOLD = _module.QUERY_DRIFT_SIMILARITY_THRESHOLD


def test_threshold_value_is_the_chosen_0_7():
    assert QUERY_DRIFT_SIMILARITY_THRESHOLD == 0.7


# --- Corpus: cases that MUST trigger the guardrail (low similarity) --------

def test_drift_detected_for_mangled_extraction():
    """A generic stand-in for "the movie The Room" -> "Room": some
    extraction quirk drops most of the actual title."""
    assert _query_drift_detected("The Lighthouse", "House") is True


def test_drift_detected_for_wildly_different_candidate():
    assert _query_drift_detected("Spirited Away", "Interstellar") is True


def test_drift_detected_for_similar_but_distinct_films():
    """"Moon" vs "Moonlight" are different films (2009 vs 2016) -- close
    enough to be a plausible accidental match, which is exactly the case
    this guardrail exists for."""
    assert _query_drift_detected("Moon", "Moonlight") is True


def test_drift_detected_for_garbled_query():
    assert _query_drift_detected("random garbled xk92 query", "Interstellar") is True


# --- Corpus: cases that must NOT trigger (legitimate near-matches) --------

def test_no_drift_for_exact_match():
    assert _query_drift_detected("Interstellar", "Interstellar") is False


def test_no_drift_for_case_and_article_differences():
    assert _query_drift_detected("the lighthouse", "The Lighthouse") is False


def test_no_drift_for_sequel_year_suffix():
    assert _query_drift_detected("Blade Runner", "Blade Runner 2049") is False


def test_no_drift_for_parenthetical_year():
    assert _query_drift_detected("Interstellar", "Interstellar (2014)") is False


def test_no_drift_for_bare_year_alongside_title():
    """"Dune 2021" vs a bare "Dune" candidate must not drift-trigger just
    because the year is part of the hint but not the candidate's bare
    title -- a bare year is not part of the title for similarity purposes."""
    assert _query_drift_detected("Dune 2021", "Dune") is False


def test_no_drift_for_punctuation_only_differences():
    assert _query_drift_detected("Everything Everywhere All at Once", "Everything Everywhere All At Once") is False


# --- Skip conditions (spec item #3) ----------------------------------------

def test_skip_when_hard_external_id_supplied():
    assert _query_drift_check_skipped({"tmdb_id": "17181"}, None, [{"title": "Anything"}]) is True
    assert _query_drift_check_skipped({"canonical_external_id": "17181"}, None, []) is True
    assert _query_drift_check_skipped({"tvdb_id": "999"}, None, []) is True
    assert _query_drift_check_skipped({"imdb_id": "tt1234567"}, None, []) is True


def test_skip_when_single_candidate_from_explicit_year_filtered_search():
    assert _query_drift_check_skipped({}, 2003, [{"title": "Anything", "year": 2003}]) is True


def test_does_not_skip_without_a_hard_id_or_year_filtered_singleton():
    assert _query_drift_check_skipped({}, None, [{"title": "Anything"}]) is False
    # requested_year given but MULTIPLE candidates survived the year filter
    # -- still worth checking, a singleton is what makes the year filter
    # strong evidence, not merely having a year at all.
    assert _query_drift_check_skipped({}, 2003, [{"title": "A", "year": 2003}, {"title": "B", "year": 2003}]) is False


# --- Reused-identity case (spec item #4's explicit re-trigger check) -------

import pytest


@pytest.mark.asyncio
async def test_media_plan_goal_end_to_end_routes_drifted_match_to_disambiguation(monkeypatch, tmp_path):
    """Real media_plan_goal, only Radarr faked: a search whose lone
    candidate has drifted too far from the requested title must come back
    ambiguous=True with candidates (the exact shape stage_disambiguation
    already knows how to consume), never a silent canonical_identity."""
    module = _module
    monkeypatch.setattr(module, "MEDIA_WORKFLOWS_PATH", tmp_path / "media-workflows.json")

    async def fake_radarr_search(args):
        # A generic stand-in for a mangled/wrong-but-plausible single result.
        return {"matches": [{"title": "House", "year": "2019", "tmdbId": 555555}]}

    async def fake_plex_match_canonical_media(args):
        return {"matched": False, "candidates": []}

    async def fake_arr_get(service, path, args=None):
        return []

    monkeypatch.setattr(module, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(module, "plex_match_canonical_media", fake_plex_match_canonical_media)
    monkeypatch.setattr(module, "arr_get", fake_arr_get)

    plan = await module.media_plan_goal({"goal": "get me the movie The Lighthouse", "media_type": "movie", "session_id": "sess-1"})
    assert plan["ambiguous"] is True
    assert plan["canonical_identity"] is None
    assert plan.get("ambiguity_reason") == "QUERY_DRIFT"
    assert plan["candidates"] and plan["candidates"][0]["title"] == "House"


@pytest.mark.asyncio
async def test_media_plan_goal_end_to_end_accepts_legitimate_close_match(monkeypatch, tmp_path):
    """The guardrail must not block a real, legitimately-resolved title --
    only a genuine low-similarity mismatch."""
    module = _module
    monkeypatch.setattr(module, "MEDIA_WORKFLOWS_PATH", tmp_path / "media-workflows.json")

    async def fake_radarr_search(args):
        return {"matches": [{"title": "The Lighthouse", "year": "2019", "tmdbId": 555555}]}

    async def fake_plex_match_canonical_media(args):
        return {"matched": False, "candidates": []}

    async def fake_arr_get(service, path, args=None):
        return []

    monkeypatch.setattr(module, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(module, "plex_match_canonical_media", fake_plex_match_canonical_media)
    monkeypatch.setattr(module, "arr_get", fake_arr_get)

    plan = await module.media_plan_goal({"goal": "get me the movie The Lighthouse", "media_type": "movie", "session_id": "sess-1"})
    assert plan["ambiguous"] is False
    assert plan["canonical_identity"]["title"] == "The Lighthouse"


def test_status_and_diagnose_paths_never_run_the_drift_check_at_all():
    """media_status/media_diagnose operate on an already-established
    workflow_id, not a fresh title search -- they never call
    _pick_match/_query_drift_detected in the first place (confirmed by
    reading media_status/media_diagnose: neither references
    _query_drift_detected or _pick_match anywhere), so a resolved
    canonical identity being reused for a status/diagnose call can never
    be re-flagged as drifted."""
    import re
    source = Path(__file__).with_name("server-tools-app.py").read_text()
    status_fn = re.search(r"async def media_status\(.*?\n(?=async def |\Z)", source, re.S).group(0)
    diagnose_fn = re.search(r"async def media_diagnose\(.*?\n(?=async def |\Z)", source, re.S).group(0)
    assert "_query_drift_detected" not in status_fn
    assert "_query_drift_detected" not in diagnose_fn
    assert "_pick_match" not in status_fn
    assert "_pick_match" not in diagnose_fn
