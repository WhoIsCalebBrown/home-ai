"""Generated semantic qualification matrix; no production I/O or writes."""

from conversation_matrix import load_router


def test_request_language_property_matrix_has_broad_equivalence_coverage():
    r = load_router()
    verbs = ["get", "give me", "grab me", "add", "request", "find", "I want", "put"]
    wrappers = ["", "please ", "uh, ", "can you ", "can you get a, can you request the movie "]
    suffixes = ["", " please", " for me", " when you can", " on Plex"]
    count = 0
    for wrapper in wrappers:
        for verb in verbs:
            for suffix in suffixes:
                text = f"{wrapper}{verb} Dumb and Dumber from 1994{suffix}"
                if verb == "put" and "Plex" not in suffix:
                    continue
                assert r["explicit_domain"](text) == "media"
                assert r["preflight_plan"](text)[0][0] == "media_plan_goal"
                count += 1
    assert count == 180


def test_current_information_mutation_matrix_never_routes_to_local_domains():
    r = load_router()
    topics = ["American politics", "Nvidia", "technology", "world events", "markets", "sports"]
    temporal = ["today", "this morning", "this week", "latest", "recent developments"]
    count = 0
    for t in temporal:
        for topic in topics:
            text = f"what happened {t} in {topic}"
            assert r["preflight_plan"](text)[0][0] == "web_search", text
            count += 1
    assert count == 30


def test_historical_camera_mutation_matrix_stays_event_scoped():
    r = load_router()
    times = ["about an hour ago", "a little over an hour ago", "earlier today", "this morning"]
    nouns = ["camera events", "motion events", "detections", "someone at the front door"]
    count = 0
    for when in times:
        for noun in nouns:
            text = f"{when} there were two {noun} at the front door"
            assert r["historical_camera_question"](text)
            assert r["preflight_plan"](text)[0][0] == "frigate_recent_events"
            count += 1
    assert count == 16


def test_camera_scope_wins_over_freshness_but_public_topic_wins_without_camera_scope():
    r = load_router()
    assert r["preflight_plan"]("what happened this morning at the front door")[0][0] == "frigate_recent_events"
    assert r["preflight_plan"]("what happened this morning in Nvidia")[0][0] == "web_search"


def test_direct_delivery_and_playback_matrix_never_enters_acquisition():
    r = load_router()
    forms = ["send", "upload", "attach", "play", "stream"]
    suffixes = ["the Dumb and Dumber movie file here", "Dumb and Dumber into this chat", "Dumb and Dumber"]
    count = 0
    for verb in forms:
        for suffix in suffixes:
            text = f"{verb} {suffix}"
            assert not r["media_goal_request"](text), text
            count += 1
    assert count == 15
