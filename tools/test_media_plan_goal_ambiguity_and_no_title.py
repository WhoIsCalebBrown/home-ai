"""Real Tools-level tests for three production bugs found on LIVE deployed
traffic (audit log confirmed) after this session's earlier work shipped:

1. _pick_match's OWN native ambiguity (a close/tied score, as opposed to the
   CROSS_DOMAIN_CANDIDATE/QUERY_DRIFT paths, which already populated
   plan["candidates"]) never surfaced the tied candidates -- media_plan_goal
   fell through to a dead end instead of a real "did you mean X or Y?"
   question. Reproduced here with REAL Radarr-shaped multi-candidate
   fixtures (multiple MCU titles, "Avengers" among them, as seen live),
   not the simplified qa/test_assistant_conversation_integration.py fake,
   which never exercises the real _pick_match tie-breaking path.

2. A creator/cast hint ("... by Tommy Wiseau") was extracted into
   artist_query correctly, but never passed into _pick_match for
   movie/tv/anime -- so the existing artist-boost scoring (already used by
   the album branch) never had a chance to disambiguate a tie using it.
   Tested generically, with a name other than "Tommy Wiseau" too.

3. A vague/titleless request ("Can you request a movie for me?", "I want to
   add a movie to my server.") or a browse/count-shaped question ("do I have
   any movies on my server?", "what movies do I have?") was sent to
   Radarr/Plex as a literal, garbage search string instead of being
   recognized as having no real title at all.

No real network call is made anywhere in this file -- httpx is only ever
reached through radarr_search/sonarr_search (monkeypatched below).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("server-tools-app.py")
sys.path.insert(0, str(Path(__file__).parent))


def _load_app(tmp_path=None):
    spec = importlib.util.spec_from_file_location("server_tools_app_ambiguity_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if tmp_path is not None:
        module.MEDIA_WORKFLOWS_PATH = tmp_path / "media-workflows.json"
        import workflow_events
        workflow_events.WORKFLOW_EVENTS_DB = tmp_path / "workflow-events.sqlite3"
    return module


@pytest.fixture
def app(tmp_path):
    return _load_app(tmp_path)


# --- Root cause #1: real multi-candidate tie-breaking must surface candidates

# Deliberately no candidate titled exactly "Avengers" -- that would hit the
# exact-match shortcut and resolve confidently, masking the real tie. This
# mirrors the live scenario: multiple MCU titles all plausibly match, none
# of them is an exact literal match for the query.
MCU_TIE_MATCHES = [
    {"title": "The Avengers", "year": "2012", "tmdbId": 24428},
    {"title": "Avengers: Endgame", "year": "2019", "tmdbId": 299534},
    {"title": "Avengers: Infinity War", "year": "2018", "tmdbId": 299536},
]


def _stub_movie_side_calls(app, monkeypatch):
    """media_plan_goal's movie branch also checks Plex and Radarr's managed
    library after identity resolution -- stub those too so a plan_only/read
    test never reaches the real network, even when a confident identity
    happens to resolve."""
    async def fake_plex_match_canonical_media(args):
        return {"matched": False, "candidates": []}

    async def fake_arr_get(service, path, args=None):
        return []

    monkeypatch.setattr(app, "plex_match_canonical_media", fake_plex_match_canonical_media)
    monkeypatch.setattr(app, "arr_get", fake_arr_get)


@pytest.mark.asyncio
async def test_ambiguous_multi_candidate_movie_surfaces_real_candidates(app, monkeypatch):
    """Real production bug: "do i have avengers on my plex server?"-shaped
    requests hit a genuine multi-title tie in Radarr (several MCU films all
    plausibly match "avengers") and used to dead-end with zero visibility
    into what was actually tied."""
    async def fake_radarr_search(args):
        return {"matches": MCU_TIE_MATCHES}

    async def fake_sonarr_search(args):
        return {"matches": []}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({"goal": "do i have avengers on my plex server?", "media_type": "movie"})

    assert plan["canonical_identity"] is None
    assert plan["ambiguous"] is True
    assert plan["ambiguity_reason"] == "NO_CONFIDENT_MATCH"
    candidates = plan["candidates"]
    assert len(candidates) >= 2
    titles = {c["title"] for c in candidates}
    assert titles & {"The Avengers", "Avengers: Endgame", "Avengers: Infinity War"}


@pytest.mark.asyncio
async def test_ambiguous_tv_candidate_also_surfaces_real_candidates(app, monkeypatch):
    """Same tie-breaking gap, TV/anime branch."""
    async def fake_sonarr_search(args):
        return {"matches": [
            {"title": "The Office", "year": "2005", "tvdbId": 73244},
            {"title": "The Office", "year": "2001", "tvdbId": 72108},
        ]}

    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)

    plan = await app.media_plan_goal({"goal": "the show The Office", "media_type": "tv"})

    assert plan["canonical_identity"] is None
    assert plan["ambiguous"] is True
    assert plan["ambiguity_reason"] == "NO_CONFIDENT_MATCH"
    assert len(plan["candidates"]) == 2


@pytest.mark.asyncio
async def test_confident_single_match_is_unaffected(app, monkeypatch):
    """Negative control: a clean, unambiguous single match must still
    resolve straight to canonical_identity -- this fix must not make
    previously-confident matches newly ambiguous."""
    async def fake_radarr_search(args):
        return {"matches": [{"title": "Dune", "year": "2021", "tmdbId": 438631}]}

    async def fake_sonarr_search(args):
        return {"matches": []}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({"goal": "get Dune 2021", "media_type": "movie"})

    assert plan["canonical_identity"]["title"] == "Dune"
    assert plan.get("ambiguous") is False
    assert "candidates" not in plan


# --- Root cause #2: creator/cast hint must be usable by _pick_match's boost

def test_pick_match_uses_artist_hint_to_break_a_tie():
    """Generic: the existing artist-boost scoring (score += 1.0 when the
    artist string appears in the candidate row) must actually receive the
    hint for movie/tv, not just album. Uses a name other than Tommy Wiseau
    to prove this is not a special case."""
    matches = [
        {"title": "The Room", "year": "2003", "tmdbId": 17654, "cast": ["Tommy Wiseau"]},
        {"title": "The Room", "year": "2019", "tmdbId": 555555, "cast": ["Olivia Cooke"]},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "The Room", "Tommy Wiseau")
    assert ambiguous is False
    assert identity["tmdbId"] == 17654


def test_pick_match_artist_hint_generalizes_to_other_names():
    matches = [
        {"title": "It", "year": "1990", "tmdbId": 111, "cast": ["Tim Curry"]},
        {"title": "It", "year": "2017", "tmdbId": 222, "cast": ["Bill Skarsgard"]},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "It", "Bill Skarsgard")
    assert ambiguous is False
    assert identity["tmdbId"] == 222


def test_pick_match_with_no_artist_hint_is_unaffected():
    """artist=None (the common case) must remain a no-op -- this is a
    regression guard on the boost's own gating (`if artist_cf and ...`)."""
    matches = [{"title": "Dune", "year": "2021", "tmdbId": 438631}]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "Dune", None)
    assert ambiguous is False
    assert identity["tmdbId"] == 438631


def app_module():
    return _load_app()


# --- Root cause #3 + addendum: no title at all / browse-shaped questions

@pytest.mark.asyncio
@pytest.mark.parametrize("goal", [
    "Can you request a movie for me?",
    "I want to add a movie to my server.",
    "do i have any movies on my server?",
    "what movies do i have?",
    "how many movies do i have",
    "show me my tv shows",
])
async def test_titleless_or_browse_shaped_requests_never_hit_a_provider(app, goal, monkeypatch):
    """None of these name a specific item -- media_plan_goal must recognize
    that generically (by utterance SHAPE, not by matching this exact
    sentence) and never search Radarr/Sonarr/Lidarr for the literal raw
    text."""
    called = {"radarr": False, "sonarr": False, "lidarr": False}

    async def fake_radarr_search(args):
        called["radarr"] = True
        return {"matches": []}

    async def fake_sonarr_search(args):
        called["sonarr"] = True
        return {"matches": []}

    async def fake_lidarr_search_album(args):
        called["lidarr"] = True
        return {"matches": []}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    monkeypatch.setattr(app, "lidarr_search_album", fake_lidarr_search_album)

    plan = await app.media_plan_goal({"goal": goal})

    assert plan["current_state"] == "NO_TITLE_GIVEN"
    assert plan["ambiguous"] is False
    assert plan["canonical_identity"] is None
    assert not any(called.values()), f"a provider was called for a titleless/browse-shaped goal: {called}"
    assert plan["message"]


@pytest.mark.asyncio
async def test_a_real_title_still_reaches_the_provider(app, monkeypatch):
    """Negative control for root cause #3: an utterance that DOES name a
    specific item (a title word survives scaffolding/category stripping)
    must still proceed to a real lookup."""
    async def fake_radarr_search(args):
        return {"matches": [{"title": "Avengers: Endgame", "year": "2019", "tmdbId": 299534}]}

    async def fake_sonarr_search(args):
        return {"matches": []}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({"goal": "request Avengers: Endgame", "media_type": "movie"})

    assert plan["current_state"] != "NO_TITLE_GIVEN"
    assert plan["canonical_identity"]["title"] == "Avengers: Endgame"


def test_media_title_candidate_words_classifier_shapes():
    """Direct unit coverage of the generic shape classifier itself, beyond
    what the end-to-end media_plan_goal tests above already prove."""
    cand = app_module()._media_title_candidate_words
    assert cand("Can you request a movie for me?") == []
    assert cand("what's in my library") == []
    assert cand("season 2 of stranger things") == ["2", "stranger", "things"]
    assert cand("request the movie The Room by Tommy Wiseau") == ["room", "by", "tommy", "wiseau"]


# --- Unknown media type must not require the user to say "movie"/"show" ---

@pytest.mark.asyncio
async def test_unknown_media_type_resolves_via_cross_domain_search(app, monkeypatch):
    """Target behavior: "Do I have Avengers on Plex?" has no movie/show
    word and no year, so _media_goal_parts classifies media_type="unknown"
    -- media_plan_goal must not just give up (the real production bug: the
    unknown-kind branch skipped straight to ambiguous=True with zero
    candidates and never searched anything). It must search movie+TV
    together and resolve using the real identity/plex/arr logic, not a
    parallel path."""
    async def fake_radarr_search(args):
        # An exact-title match ("Avengers", not "Avengers: Endgame") so this
        # test isolates cross-domain resolution from the separate
        # query-drift guardrail (tools/test_query_drift_guardrail.py),
        # which would legitimately flag "avengers" -> "Avengers: Endgame"
        # as too dissimilar on its own.
        return {"matches": [{"title": "Avengers", "year": "1998", "tmdbId": 9320}]}

    async def fake_sonarr_search(args):
        return {"matches": []}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({"goal": "do i have avengers on plex", "media_type": None})

    assert plan["canonical_identity"]["title"] == "Avengers"
    assert plan["canonical_identity"]["media_type"] == "movie"


@pytest.mark.asyncio
async def test_unknown_media_type_cross_domain_tv_match_resolves_as_tv(app, monkeypatch):
    """Same cross-domain search, but the real answer is a TV series --
    proves the resolved kind is actually used (media_type="tv" in the
    final canonical identity), not hardcoded to movie."""
    async def fake_radarr_search(args):
        return {"matches": []}

    async def fake_sonarr_search(args):
        return {"matches": [{"title": "The Office", "year": "2005", "tvdbId": 73244, "tmdbId": 2316}]}

    async def fake_plex_match_canonical_media(args):
        return {"matched": False, "candidates": []}

    async def fake_arr_get(service, path, args=None):
        return []

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    monkeypatch.setattr(app, "plex_match_canonical_media", fake_plex_match_canonical_media)
    monkeypatch.setattr(app, "arr_get", fake_arr_get)

    plan = await app.media_plan_goal({"goal": "do i have the office on plex"})

    assert plan["canonical_identity"]["title"] == "The Office"
    assert plan["canonical_identity"]["media_type"] == "tv"


@pytest.mark.asyncio
async def test_unknown_media_type_cross_domain_ambiguity_retains_candidates(app, monkeypatch):
    """A tie across BOTH domains (or within one) must still surface real
    candidates, not collapse to a bare dead end -- same discipline as the
    single-domain NO_CONFIDENT_MATCH case."""
    async def fake_radarr_search(args):
        return {"matches": [{"title": "Titans", "year": "2011", "tmdbId": 1}]}

    async def fake_sonarr_search(args):
        return {"matches": [{"title": "Titans", "year": "2018", "tvdbId": 2}]}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)

    plan = await app.media_plan_goal({"goal": "do i have titans"})

    assert plan["canonical_identity"] is None
    assert plan["ambiguous"] is True
    assert plan["ambiguity_reason"] == "NO_CONFIDENT_MATCH"
    media_types = {c["media_type"] for c in plan["candidates"]}
    assert media_types == {"movie", "tv"}
