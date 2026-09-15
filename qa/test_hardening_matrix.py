"""High-volume pure routing fuzz/mutation checks; no production I/O."""

from conversation_matrix import load_router


def test_acquisition_language_mutations_preserve_media_route():
    router = load_router()
    verbs = ["get", "give me", "grab me", "add", "request", "find", "I want"]
    wrappers = ["", "please ", "uh, ", "can you ", "can you get a, can you request the movie "]
    suffixes = ["", " please", " for me", " when you can"]
    count = 0
    for wrapper in wrappers:
        for verb in verbs:
            for suffix in suffixes:
                text = f"{wrapper}{verb} Dumb and Dumber from 1994{suffix}"
                assert router["explicit_domain"](text) == "media", text
                assert router["preflight_plan"](text)[0][0] == "media_plan_goal", text
                count += 1
    assert count == 140


def test_current_external_topics_break_sticky_local_domains():
    router = load_router()
    for text in (
        "what happened today in American politics",
        "what's the latest Nvidia news",
        "what happened in the world this morning",
        "can you search the web for today's AI news",
    ):
        assert router["preflight_plan"](text)[0][0] == "web_search", text


def test_historical_camera_language_does_not_become_live_snapshot():
    router = load_router()
    text = "about an hour ago there were two camera events at the front door"
    assert router["historical_camera_question"](text)
    assert router["preflight_plan"](text)[0][0] == "frigate_recent_activity"


def test_direct_file_and_playback_language_remain_distinct():
    router = load_router()
    for text in (
        "send me the Dumb and Dumber movie file here",
        "upload Dumb and Dumber into this chat",
        "play Dumb and Dumber",
    ):
        assert not router["media_goal_request"](text), text
