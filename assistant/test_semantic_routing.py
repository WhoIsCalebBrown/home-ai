import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from semantic_routing import (  # noqa: E402
    discovery_context,
    has_referential_language,
    retrieval_confidence,
    semantic_query,
)


def test_discovery_context_drops_sticky_domain_and_tool_history():
    context = discovery_context({
        "domain": "weather",
        "group": "weather",
        "tools": ["weather_forecast"],
        "latest_resolved_referent": {"title": "Segua"},
        "unresolved_request": "Segua, the Chinese Siamese Cat",
    })
    assert "domain" not in context
    assert "group" not in context
    assert "tools" not in context
    assert "Segua" in " ".join(context["referents"])


def test_elliptical_turn_gets_referent_without_getting_old_domain():
    query = semantic_query(
        "Can you find it on the internet?",
        {"domain": "weather", "latest_resolved_referent": {"title": "Segua"}},
    )
    assert query.startswith("Can you find it on the internet?")
    assert "Segua" in query
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
