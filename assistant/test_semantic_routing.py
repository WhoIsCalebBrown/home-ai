import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from semantic_routing import (  # noqa: E402
    discovery_context,
    has_referential_language,
    narrow_capability_entries,
    retrieval_confidence,
    semantic_query,
)


def test_discovery_context_drops_sticky_domain_and_tool_history():
    context = discovery_context({
        "domain": "weather", "group": "weather", "tools": ["weather_forecast"],
        "latest_resolved_referent": {"title": "Segua"},
        "unresolved_request": "Segua, the Chinese Siamese Cat",
    })
    assert "domain" not in context
    assert "group" not in context
    assert "tools" not in context
    assert "Segua" in " ".join(context["referents"])


def test_elliptical_turn_gets_referent_without_getting_old_domain():
    query = semantic_query("Can you find it on the internet?", {"domain": "weather", "latest_resolved_referent": {"title": "Segua"}})
    assert query.startswith("Can you find it on the internet?")
    assert "weather" not in query.casefold()


def test_explicit_new_turn_is_not_treated_as_referential():
    assert not has_referential_language("what happened today in American politics")
    assert semantic_query("what happened today in American politics", {"domain": "weather"}) == "what happened today in American politics"


def test_retrieval_confidence_is_bounded_and_auditable():
    result = retrieval_confidence([
        {"metadata": {"canonical_name": "web_search", "score": 8}},
        {"metadata": {"canonical_name": "weather_forecast", "score": 2}},
    ])
    assert result["top"] == "web_search"
    assert result["margin"] == 6


def entry(name, group):
    return {"function": {"name": name}, "metadata": {"canonical_name": name, "group": group}}


def test_explicit_weather_group_excludes_unrelated_retrieval_hits():
    entries = [entry("web_search", "internet"), entry("web_fetch", "internet"), entry("weather_forecast", "weather"), entry("current_datetime", "utility")]
    selected = narrow_capability_entries(entries, {"group": "weather"})
    assert [item["function"]["name"] for item in selected] == ["weather_forecast"]


def test_explicit_server_group_excludes_write_and_web_capabilities():
    entries = [entry("list_containers", "docker"), entry("web_search", "internet"), entry("clear_completed_list_items", "lists")]
    selected = narrow_capability_entries(entries, {"group": "server"})
    assert [item["function"]["name"] for item in selected] == ["list_containers"]


def test_unstructured_turn_preserves_semantic_retrieval_ranking():
    entries = [entry("web_search", "internet"), entry("weather_forecast", "weather")]
    selected = narrow_capability_entries(entries, {})
    assert [item["function"]["name"] for item in selected] == ["web_search", "weather_forecast"]


def test_unknown_group_fails_open_to_retrieval_not_previous_context():
    entries = [entry("web_search", "internet"), entry("media_status", "media")]
    selected = narrow_capability_entries(entries, {"group": "unknown"})
    assert [item["function"]["name"] for item in selected] == ["web_search", "media_status"]
