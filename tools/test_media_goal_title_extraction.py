"""Regression tests for the real "The Room" -> "Room" false-positive bug.

ROOT CAUSE (confirmed by reading the actual code, not guessed): in
_media_goal_parts(), the title-cleanup regex
`r"\\b(?:the|original|animated|version|movie|film|series|show|whole|entire|all)\\b"`
stripped "the" as a bare, standalone noise word ANYWHERE in the string --
intended to remove request framing like "the movie X" / "the whole series",
it also destroyed a real leading article that is part of the actual title
("the movie The Room" -> title_query "Room"). _evaluate_plex_candidate's own
title comparison (_normalize_identity_title) was never the problem -- it
correctly distinguishes "the room" from "room" -- but by the time it ran,
the WRONG title ("Room") had already been searched and picked as the
canonical match upstream, in Radarr/Sonarr lookup and _pick_match.

A second, related bug: media_type inference checked the generic "by PERSON"
album heuristic before movie/tv keywords, so "The Room by Tommy Wiseau" (no
movie/film word) could misclassify a movie request as an album search,
throwing away "Tommy Wiseau" as an identity hint instead of using it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import importlib.util
spec = importlib.util.spec_from_file_location("server_tools_app_title_test", Path(__file__).with_name("server-tools-app.py"))
_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_module)
_media_goal_parts = _module._media_goal_parts
_normalize_identity_title = _module._normalize_identity_title
_evaluate_plex_candidate = _module._evaluate_plex_candidate


def test_leading_article_survives_request_framing():
    parts = _media_goal_parts("Can you request the movie The Room?", None)
    assert parts["title_query"] == "The Room", (
        "the request-framing 'the movie' must be stripped, but the title's "
        "own leading article must survive"
    )
    assert parts["media_type"] == "movie"


def test_leading_article_survives_various_phrasings():
    cases = {
        "The Room 2003": "The Room 2003",  # media_type unknown here (no classifier word) -- title_query untouched
        "The Room from 2003": "The Room",
        "The Tommy Wiseau movie The Room": "The Tommy Wiseau The Room",
    }
    for goal, expected in cases.items():
        assert _media_goal_parts(goal, None)["title_query"] == expected, goal


def test_similarly_named_titles_are_never_conflated_by_normalization():
    """Spec item #5: "The Room", "Room", "A Room", "Room 104", "The
    Roommate", "Room (2015)" must never collapse into the same normalized
    identity string."""
    titles = ["The Room", "Room", "A Room", "Room 104", "The Roommate", "Room (2015)"]
    normalized = {title: _normalize_identity_title(title) for title in titles}
    assert len(set(normalized.values())) == len(titles), normalized


def test_evaluate_plex_candidate_rejects_title_only_match_for_different_title():
    requested = {"title": "The Room", "year": 2003, "external_ids": {"tmdb": "17181"}}
    candidate_room_2015 = {"title": "Room", "year": 2015, "external_ids": {"tmdb": "141052"}}
    result = _evaluate_plex_candidate(candidate_room_2015, requested)
    # Both sides carry a stable ID here, so this is rejected as a conflicting
    # canonical identity (an even stronger rejection than a bare title
    # mismatch would be) -- either way, it must never be accepted.
    assert result.get("rejected_reason") in {"title_mismatch", "canonical_identity_mismatch"}
    assert "match_method" not in result

    # The title-only case (no external IDs on either side) is what actually
    # exercises the title_mismatch branch.
    requested_no_id = {"title": "The Room", "year": 2003, "external_ids": {}}
    candidate_no_id = {"title": "Room", "year": 2015, "external_ids": {}}
    result_no_id = _evaluate_plex_candidate(candidate_no_id, requested_no_id)
    assert result_no_id.get("rejected_reason") == "title_mismatch"


def test_evaluate_plex_candidate_accepts_exact_canonical_id_match():
    requested = {"title": "The Room", "year": 2003, "external_ids": {"tmdb": "17181"}}
    candidate = {"title": "The Room", "year": 2003, "external_ids": {"tmdb": "17181"}}
    result = _evaluate_plex_candidate(candidate, requested)
    assert result["match_method"] == "tmdb"
    assert result["confidence"] == 1.0


def test_evaluate_plex_candidate_rejects_conflicting_canonical_id():
    """A candidate sharing a title but with a DIFFERENT external ID must
    never be accepted even if title/year happen to line up by coincidence."""
    requested = {"title": "The Room", "year": 2003, "external_ids": {"tmdb": "17181"}}
    candidate = {"title": "The Room", "year": 2003, "external_ids": {"tmdb": "999999"}}
    result = _evaluate_plex_candidate(candidate, requested)
    assert result.get("rejected_reason") == "canonical_identity_mismatch"


def test_media_type_classifier_prefers_explicit_movie_word_over_by_person():
    """"the movie by that director" must not become an album search just
    because it contains a bare "by X" phrase alongside an explicit movie
    word."""
    parts = _media_goal_parts("get me the movie by that director called Inception", None)
    assert parts["media_type"] == "movie"


def test_creator_hint_does_not_redefine_media_type_when_already_known():
    """When the caller already knows media_type=movie (as the assistant
    does once a subject is established), "by PERSON" must never override
    it -- this is the explicit-media_type path, not the text-inference
    fallback."""
    parts = _media_goal_parts("The Room by Tommy Wiseau", media_type="movie")
    assert parts["media_type"] == "movie"
    assert parts["artist_query"] == "Tommy Wiseau"
    assert parts["title_query"] == "The Room"
