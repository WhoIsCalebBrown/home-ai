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


# --- Web discovery fallback: descriptive/person-based resolution ----------

@pytest.mark.asyncio
async def test_tom_hanks_island_volleyball_resolves_via_web_discovery(app, monkeypatch):
    """The target scenario this fallback exists for: no title at all, only
    a descriptive clue and a person mention. Structured lookup (Radarr)
    finds nothing for the literal description; web_search names the real
    film; that NAME is re-searched through the REAL Radarr lookup, never
    trusted directly -- proving "natural-language text is NOT canonical
    media identity" holds even for this new path."""
    radarr_calls = []

    async def fake_radarr_search(args):
        radarr_calls.append(args["query"])
        if "cast away" in args["query"].casefold():
            # Real live production shape: Radarr's lookup returns the real
            # feature ALONGSIDE a making-of/bonus-content entry that shares
            # every title token -- the fix under test must not let the
            # person hint ("Tom Hanks", absent from Radarr's row data)
            # suppress the exact-title-match shortcut and fall into a tie
            # against the bonus content.
            return {"matches": [
                {"title": "Cast Away", "year": "2000", "tmdbId": 8358, "vote_count": 9000},
                {"title": "Behind the Scenes: Cast Away", "year": "2000", "tmdbId": 999001, "vote_count": 3},
            ]}
        return {"matches": []}

    async def fake_sonarr_search(args):
        return {"matches": []}

    async def fake_web_search(args):
        assert "tom hanks" in args["query"].casefold(), "the person hint must reach the web search query"
        return {"query": args["query"], "results": [
            {"title": "Cast Away (2000) - IMDb", "url": "https://example.invalid/cast-away", "snippet": "A FedEx employee is stranded on an island."},
        ], "source": "SearXNG", "untrusted": True}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    monkeypatch.setattr(app, "web_search", fake_web_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({
        "goal": "the Tom Hanks movie where he's stuck on an island with a volleyball",
        "media_type": "movie",
    })

    assert plan["canonical_identity"] is not None, "web discovery must resolve this, not dead-end"
    assert plan["canonical_identity"]["title"] == "Cast Away"
    assert plan["canonical_identity"]["tmdb_id"] == 8358
    assert plan["ambiguity_reason"] == "WEB_DISCOVERY_MATCH"


@pytest.mark.asyncio
async def test_web_discovery_does_not_tie_against_bonus_content_for_a_different_film(app, monkeypatch):
    """Generalization proof: the fix must not be specific to "Cast Away" --
    a different film/person pairing, with a differently-worded bonus-
    content entry ("Interstellar: Making of"), must resolve the same way."""
    async def fake_radarr_search(args):
        if "interstellar" in args["query"].casefold():
            return {"matches": [
                {"title": "Interstellar", "year": "2014", "tmdbId": 157336, "vote_count": 32000},
                {"title": "Interstellar: Making of", "year": "2014", "tmdbId": 999002, "vote_count": 5},
            ]}
        return {"matches": []}

    async def fake_sonarr_search(args):
        return {"matches": []}

    async def fake_web_search(args):
        assert "anne hathaway" in args["query"].casefold()
        return {"query": args["query"], "results": [
            {"title": "Interstellar (2014) - IMDb", "url": "https://example.invalid/interstellar"},
        ]}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    monkeypatch.setattr(app, "web_search", fake_web_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({
        "goal": "the Anne Hathaway movie where he travels through a wormhole to save humanity",
        "media_type": "movie",
    })

    assert plan["canonical_identity"] is not None
    assert plan["canonical_identity"]["title"] == "Interstellar"
    assert plan["canonical_identity"]["tmdb_id"] == 157336
    assert plan["ambiguity_reason"] == "WEB_DISCOVERY_MATCH"


@pytest.mark.asyncio
async def test_web_discovery_only_triggers_with_a_real_person_or_description_clue(app, monkeypatch):
    """Negative control: a titleless request with NO descriptive/person
    clue must never trigger a web search fishing expedition -- it still
    goes through the existing NO_TITLE_GIVEN clarification path."""
    web_called = {"value": False}

    async def fake_web_search(args):
        web_called["value"] = True
        return {"query": args["query"], "results": []}

    monkeypatch.setattr(app, "web_search", fake_web_search)

    plan = await app.media_plan_goal({"goal": "Can you request a movie for me?"})

    assert plan["current_state"] == "NO_TITLE_GIVEN"
    assert web_called["value"] is False


@pytest.mark.asyncio
async def test_web_discovery_does_not_trigger_for_an_ordinary_unmatched_title(app, monkeypatch):
    """A plain, clean title that genuinely does not exist must NOT trigger
    a web search fishing expedition just because it found zero matches --
    only a real person/description clue should activate this fallback."""
    web_called = {"value": False}

    async def fake_radarr_search(args):
        return {"matches": []}

    async def fake_sonarr_search(args):
        return {"matches": []}

    async def fake_web_search(args):
        web_called["value"] = True
        return {"query": args["query"], "results": []}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    monkeypatch.setattr(app, "web_search", fake_web_search)

    plan = await app.media_plan_goal({"goal": "request the movie Zzyzx Nonexistent Reel", "media_type": "movie"})

    assert web_called["value"] is False
    assert plan["canonical_identity"] is None


@pytest.mark.asyncio
async def test_plot_only_description_resolves_via_web_discovery(app, monkeypatch):
    """A plot clue is useful evidence even when the user did not name an
    actor.  The web result still has to be revalidated by Radarr before it
    becomes canonical identity; this is identification, not acquisition."""
    web_calls = []

    async def fake_radarr_search(args):
        if args["query"].casefold() == "the martian":
            return {"matches": [{"title": "The Martian", "year": "2015", "tmdbId": 286217}]}
        return {"matches": []}

    async def fake_sonarr_search(args):
        return {"matches": []}

    async def fake_web_search(args):
        web_calls.append(args["query"])
        return {"results": [{"title": "The Martian (2015) - Wikipedia"}]}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    monkeypatch.setattr(app, "web_search", fake_web_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({
        "goal": "What is that movie about a stranded engineer growing potatoes on Mars?",
        "media_type": "movie",
    })

    assert web_calls, "the bounded fallback must use the plot clue"
    assert plan["canonical_identity"]["title"] == "The Martian"
    assert plan["goal"]["action"] == "inspect"
    assert plan["confirmation_required"] is False
    assert plan["writes_required"] == []


def test_person_evidence_prefers_only_a_supported_candidate_when_other_rows_are_unknown(app):
    """Missing cast/creator fields are unknown, not a negative constraint.

    The positive returned evidence is sufficient to choose one candidate;
    an otherwise-identical row that simply omitted cast must not be treated
    as contradicting the user's creator hint.
    """
    matches = [
        {"title": "Example", "year": "2001", "tmdbId": 1,
         "credits": {"cast": [{"name": "Avery Director"}]}},
        {"title": "Example", "year": "2005", "tmdbId": 2},
    ]
    identity, ambiguous, candidates = app._pick_match(matches, "Example", "Avery Director")
    assert identity["tmdbId"] == 1
    assert ambiguous is False
    assert candidates == []


def test_person_hint_without_catalog_evidence_remains_ambiguous_not_conflicting(app):
    """Do not invent creator conflict from absent catalog metadata."""
    matches = [
        {"title": "Example", "year": "2001", "tmdbId": 1},
        {"title": "Example", "year": "2005", "tmdbId": 2},
    ]
    identity, ambiguous, candidates = app._pick_match(matches, "Example", "Avery Director")
    assert identity is None
    assert ambiguous is True
    assert {candidate["tmdbId"] for candidate in candidates} == {1, 2}


def test_candidate_summaries_expose_only_available_people_evidence(app):
    summaries = app._candidate_summaries([
        {"title": "Example", "year": "2001", "tmdbId": 1, "cast": ["Avery Director"]},
        {"title": "Example Two", "year": "2002", "tmdbId": 2},
    ], "movie")
    assert summaries[0]["people"] == ["Avery Director"]
    assert summaries[1]["people"] == []


@pytest.mark.asyncio
async def test_web_discovery_result_is_re_validated_not_trusted_directly(app, monkeypatch):
    """If web_search names something that Radarr does NOT recognize at
    all, the fallback must fail honestly, never fabricate identity from
    the web result text itself."""
    async def fake_radarr_search(args):
        return {"matches": []}

    async def fake_sonarr_search(args):
        return {"matches": []}

    async def fake_web_search(args):
        return {"query": args["query"], "results": [
            {"title": "Some Unrelated Fan Blog Post - Not a real movie", "url": "https://example.invalid/x"},
        ]}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    monkeypatch.setattr(app, "web_search", fake_web_search)

    plan = await app.media_plan_goal({
        "goal": "the Tom Hanks movie where he's stuck on an island with a volleyball",
        "media_type": "movie",
    })

    assert plan["canonical_identity"] is None
    assert plan.get("ambiguity_reason") != "WEB_DISCOVERY_MATCH"


@pytest.mark.asyncio
async def test_web_discovery_reapplies_explicit_year_constraint(app, monkeypatch):
    calls = {"radarr": 0}

    async def fake_radarr_search(args):
        calls["radarr"] += 1
        if calls["radarr"] == 1:
            return {"matches": []}
        return {"matches": [
            {"title": "Example", "year": 2003, "tmdbId": 3},
            {"title": "Example", "year": 2015, "tmdbId": 15},
        ]}

    async def fake_discovery(title, person, media_type):
        return "Example"

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "_web_discover_title", fake_discovery)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({
        "goal": "the Avery Actor movie with the lighthouse from 2003",
        "media_type": "movie",
    })

    assert plan["canonical_identity"]["tmdb_id"] == 3
    assert plan["canonical_identity"]["year"] == 2003


@pytest.mark.asyncio
async def test_identity_only_resolution_does_not_create_workflow(app, monkeypatch, tmp_path):
    async def fake_radarr_search(args):
        return {"matches": [{"title": "Example", "year": 2003, "tmdbId": 3}]}

    async def fake_sonarr_search(args):
        return {"matches": []}

    monkeypatch.setattr(app, "radarr_search", fake_radarr_search)
    monkeypatch.setattr(app, "sonarr_search", fake_sonarr_search)
    _stub_movie_side_calls(app, monkeypatch)

    plan = await app.media_plan_goal({"goal": "the movie Example", "media_type": "movie"})

    assert plan["canonical_identity"]["tmdb_id"] == 3
    assert plan["workflow_id"] is None
    assert plan["idempotent"] is False
    assert not (tmp_path / "media-workflows.json").exists()


# --- Candidate ranking: a minor, capped popularity tiebreaker -------------

def test_pick_match_uses_vote_count_to_rank_candidates_when_still_ambiguous():
    """The real live gap: several equally-token-matching "Avengers"-titled
    rows (an obscure TV series and the well-known MCU film both contain
    "avengers") used to surface in an arbitrary/order-dependent sequence in
    the candidate list -- the coordinator's live re-test found obscure
    shows listed ahead of the well-known movie. vote_count (extracted
    defensively from Radarr/Sonarr's `ratings` field -- see
    _lookup_vote_count) is capped well below the 0.25 confidence-margin
    threshold, so a genuine tie correctly remains ambiguous (never
    silently guessed) -- but it now orders the SURFACED candidates toward
    the popular title first, so the clarification question names the real
    movie, not the obscure one."""
    matches = [
        {"title": "The Avengers: United They Stand", "year": "1999", "tmdbId": 1, "vote_count": 12},
        {"title": "The Avengers", "year": "2012", "tmdbId": 2, "vote_count": 28000},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "avengers")
    assert ambiguous is True
    assert candidates[0]["tmdbId"] == 2, "the popular, well-known title must be ranked first among the surfaced candidates"


# --- Exact-title-tie resolution: live-verified second root cause of the ---
# --- "Cast Away" disambiguation-against-obscure-content complaint --------

def test_pick_match_resolves_a_decisive_exact_title_tie_by_popularity():
    """Live-verified real second root cause (distinct from the artist-hint
    bug fixed last round): "Cast Away" (2000, the real Tom Hanks film) and
    "Cast Away" (2017, an obscure unrelated film) are both genuine
    EXACT-title matches for the query -- len(exact) == 2, so the exact-
    match shortcut correctly refuses to blindly pick one. With a
    695,975-to-1 real vote_count ratio, the capped fuzzy-match tiebreaker
    (max +0.05) could never bridge the required 0.25 margin -- this is a
    SEPARATE, more decisive rule specifically for a tie among the `exact`
    list itself, where there is zero remaining title-relevance
    uncertainty."""
    matches = [
        {"title": "Cast Away", "year": 2000, "tmdbId": 8358, "vote_count": 695975},
        {"title": "Cast Away", "year": 2017, "tmdbId": 534416, "vote_count": 1},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "Cast Away")
    assert ambiguous is False
    assert identity["tmdbId"] == 8358


def test_pick_match_exact_tie_with_comparable_popularity_stays_ambiguous():
    """Negative control: a genuine remake-ambiguity case -- two exact-title
    matches with COMPARABLE vote_counts (same order of magnitude, well
    under the 10x/50-vote-floor threshold) -- must still fail closed and
    surface both for disambiguation, never guessed."""
    matches = [
        {"title": "A Star Is Born", "year": 1976, "tmdbId": 1, "vote_count": 850},
        {"title": "A Star Is Born", "year": 2018, "tmdbId": 2, "vote_count": 3200},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "A Star Is Born")
    assert ambiguous is True
    assert identity is None
    tmdb_ids = {c["tmdbId"] for c in candidates}
    assert tmdb_ids == {1, 2}


def test_pick_match_exact_tie_rule_never_promotes_a_fuzzy_bonus_content_match():
    """Fuzzy-match isolation: alongside the two genuine exact "Cast Away"
    ties, the live-observed fuzzy matches ("Behind the Scenes: Cast Away",
    "Miss Cast Away and the Island Girls") are NOT exact-title matches --
    the new rule only ever compares within the `exact` list itself, so
    these must never factor into or be promoted by this decision, and the
    real popular film must still win outright."""
    matches = [
        {"title": "Cast Away", "year": 2000, "tmdbId": 8358, "vote_count": 695975},
        {"title": "Cast Away", "year": 2017, "tmdbId": 534416, "vote_count": 1},
        {"title": "Behind the Scenes: Cast Away", "year": 2000, "tmdbId": 999001, "vote_count": 3},
        {"title": "Miss Cast Away and the Island Girls", "year": 2004, "tmdbId": 999002, "vote_count": 8},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "Cast Away")
    assert ambiguous is False
    assert identity["tmdbId"] == 8358


def test_pick_match_vote_count_cannot_override_a_real_title_mismatch():
    """The popularity bonus is capped well below the margin threshold --
    a hugely popular but token-mismatched row must never win over a real,
    exact/near-exact title match."""
    matches = [
        {"title": "The Room", "year": "2003", "tmdbId": 1, "vote_count": 5},
        {"title": "Interstellar", "year": "2014", "tmdbId": 2, "vote_count": 9000000},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "The Room")
    assert identity["tmdbId"] == 1


def test_pick_match_missing_vote_count_is_a_safe_no_op():
    """Rows without a vote_count field (a Radarr/Sonarr response that does
    not carry the ratings data) must score exactly as before -- no
    fabricated popularity signal."""
    matches = [
        {"title": "The Avengers", "year": "1998", "tmdbId": 1},
        {"title": "The Avengers", "year": "2012", "tmdbId": 2},
    ]
    identity, ambiguous, candidates = app_module()._pick_match(matches, "the avengers")
    assert ambiguous is True
    assert len(candidates) == 2


def test_lookup_vote_count_extracts_from_documented_radarr_sonarr_shapes():
    """Direct coverage of the defensive extraction itself against the
    documented Radarr/Sonarr v3 lookup `ratings` shapes -- NOT verified
    against a live instance this round (no network access from this
    environment); this proves the extraction is a safe no-op for any row
    shape that does not match, rather than proving the shape is correct
    against real production data."""
    vote_count = app_module()._lookup_vote_count
    assert vote_count({"ratings": {"votes": 500, "value": 7.2}}) == 500
    assert vote_count({"ratings": {"tmdb": {"votes": 300, "value": 6.1}}}) == 300
    assert vote_count({"ratings": {}}) == 0
    assert vote_count({}) == 0
    assert vote_count({"ratings": "not a dict"}) == 0
