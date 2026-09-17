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


def test_search_service_for_scaffolding_is_stripped_from_the_title_query():
    # Real production bug found live: "Search Sonarr for the show Breaking
    # Bad" only had "the show" stripped by the classifier-word cleanup,
    # leaving "Search Sonarr for Breaking Bad" as the literal query sent to
    # Sonarr's own lookup -- "search"/"sonarr"/"for" then diluted its fuzzy
    # title match, returning unrelated results ("Search for the Truth",
    # "Star Wars: The Bad Batch") ranked ahead of the real exact "Breaking
    # Bad" match. "Search <service> for" is request framing, never part of
    # a real title.
    assert _media_goal_parts("Search Sonarr for the show Breaking Bad", None)["title_query"] == "Breaking Bad"
    assert _media_goal_parts("Search Radarr for the movie Inception", None)["title_query"] == "Inception"
    assert _media_goal_parts("Search Lidarr for Rodeo by Travis Scott", None)["title_query"] == "Rodeo"


def test_naive_user_request_phrasings_strip_cleanly_to_a_bare_title():
    # Real production bugs found in a live naive-user sweep: several
    # extremely common, zero-jargon ways to ask for or ask about media
    # left request-framing words in the literal query sent to Radarr/
    # Sonarr's own fuzzy lookup, diluting or breaking the match.
    assert _media_goal_parts("I want to watch Deadpool and Wolverine", None)["title_query"] == "Deadpool and Wolverine"
    assert _media_goal_parts("I want to see the movie Inception", None)["title_query"] == "Inception"
    assert _media_goal_parts("Can I watch Bird Box?", None)["title_query"] == "Bird Box"
    assert _media_goal_parts("Am I able to watch Arcane?", None)["title_query"] == "Arcane"


def test_ampersand_and_and_are_treated_as_the_same_connecting_word():
    # Real production bug found live: "I want to watch Deadpool and
    # Wolverine" failed to resolve as an exact match against Radarr's own
    # "Deadpool & Wolverine" -- a real title using "&" is exactly how a
    # person would naturally say the same title with "and" out loud, but
    # bare token comparison drops "&" as punctuation entirely, so the
    # connecting word only survives on the user's side, producing a false
    # ambiguity between the real movie and two unrelated titles that also
    # merely contain "wolverine".
    matches = [
        {"title": "Deadpool & Wolverine", "year": "2024", "tmdbId": 533535},
        {"title": "Wolverine and the X-Men", "year": "2009", "tmdbId": 1},
        {"title": "Wolverine", "year": "2011", "tmdbId": 2},
    ]
    identity, ambiguous, _candidates = _module._pick_match(matches, "Deadpool and Wolverine")
    assert ambiguous is False
    assert identity["title"] == "Deadpool & Wolverine"


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


def test_comma_separated_creator_hint_is_handled_like_by_name():
    """Real live bug: "The Room, Tommy Wiseau." still failed to resolve
    even with an unambiguous title, because the trailing ", Tommy Wiseau"
    clause polluted the search string instead of being split out as a
    creator hint the same way "by Tommy Wiseau" already is. Generic --
    tested with a second, unrelated name too."""
    parts = _media_goal_parts("The Room, Tommy Wiseau.", media_type="movie")
    assert parts["title_query"] == "The Room"
    assert parts["artist_query"] == "Tommy Wiseau"

    parts2 = _media_goal_parts("Whiplash, Damien Chazelle.", media_type="movie")
    assert parts2["title_query"] == "Whiplash"
    assert parts2["artist_query"] == "Damien Chazelle"


def test_comma_hint_does_not_misfire_on_a_real_title_with_a_comma():
    """A comma followed by media-structure language (a year, "the 2014
    movie", "Vol. 2") is part of the title/qualifier itself, not a
    separate creator name -- must not be split out."""
    parts = _media_goal_parts("Interstellar, the 2014 movie.", media_type="movie")
    assert parts["artist_query"] is None

    parts2 = _media_goal_parts("Kill Bill, Vol. 2", media_type="movie")
    assert parts2["artist_query"] is None
