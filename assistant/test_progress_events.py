"""Privacy and bounded rendering contracts for persisted tool progress."""

import importlib

import pytest


def progress_module():
    return importlib.import_module("progress_events")


def test_fetch_progress_exposes_domain_but_not_path_or_query():
    event = progress_module().safe_progress_event(
        "web_fetch", {"url": "https://www.cbc.ca/news/private?q=household-token"}, "started"
    )
    assert event == {"phase": "tool_started", "label": "Reading CBC…"}
    assert "private" not in repr(event)
    assert "household-token" not in repr(event)


@pytest.mark.parametrize("arguments", [None, [], "secret", {"url": []}, {"url": 42}])
def test_invalid_argument_shapes_get_a_generic_label(arguments):
    assert progress_module().safe_progress_event("web_fetch", arguments, "started") == {
        "phase": "tool_started", "label": "Reading a source…",
    }


@pytest.mark.parametrize("url", [
    "http://localhost/secret", "http://10.2.3.4/secret", "http://[::1]/secret",
    "http://server-tools:8090/secret", "http://house.lan/secret", "http://house.internal/secret",
    "http://127.1/secret", "http://0x7f000001/secret", "http://user:token@example.com/secret",
    "http://@example.com/secret", "http://%31%32%37.0.0.1/secret",
    "https://evil[spoof](x).example/secret", "https://evil_*`name.example/secret",
    "https://" + "a" * 63 + "." + "b" * 63 + ".com/secret",
    "https://www.cbc.ca\n/secret", "file:///household/secret",
])
def test_private_malformed_and_oversized_fetch_hosts_are_not_displayed(url):
    assert progress_module().safe_progress_event("web_fetch", {"url": url}, "started") == {
        "phase": "tool_started", "label": "Reading a source…",
    }


@pytest.mark.parametrize(("tool", "label"), [
    ("future_tool", "Working…"), ("weather_forecast", "Checking the forecast…"),
    ("plex_search", "Checking Plex…"), ("home_get_state", "Checking your home…"),
    ("web_search", "Searching the web…"),
])
def test_fixed_labels_do_not_expose_arguments_or_results(tool, label):
    event = progress_module().safe_progress_event(
        tool, {"query": "household secret"}, "started", {"error": "private backend trace"},
    )
    assert event == {"phase": "tool_started", "label": label}


def test_public_host_is_normalized_and_unknown_phases_are_ignored():
    progress = progress_module()
    assert progress.safe_progress_event("web_fetch", {"url": "https://WWW.Example.COM./private?q=secret"}, "started") == {
        "phase": "tool_started", "label": "Reading example.com…",
    }
    assert progress.safe_progress_event("web_fetch", {}, "private error") is None


def test_preamble_coalesces_duplicates_caps_stages_and_ignores_completions():
    progress = progress_module()
    preamble = progress.ProgressPreamble()
    chunks = []
    for tool in ["web_search"] * 100 + ["weather_forecast", "plex_search", "home_get_state", "future_tool"]:
        for phase in ("started", "finished", "failed"):
            chunk = preamble.add(progress.safe_progress_event(tool, {"secret": "no"}, phase, {"error": "no"}))
            if chunk:
                chunks.append(chunk)
    text = "".join(chunks)
    assert text == "**Working**\n- Searching the web…\n- Checking the forecast…\n- Checking Plex…\n- Checking your home…\n"
    assert progress.ProgressPreamble().add(progress.safe_progress_event("web_search", {}, "started")) == "**Working**\n- Searching the web…\n"


@pytest.mark.parametrize("event", [
    None, [], {"phase": "tool_started", "label": "raw private result"},
    {"phase": "tool_started", "label": "Reading localhost…"},
    {"phase": "tool_started", "label": "Reading evil_*`name.example…"},
])
def test_preamble_rejects_non_projected_labels(event):
    assert progress_module().ProgressPreamble().add(event) is None
