"""Security and shape tests for the pure rich-trace projector."""

import json

import pytest

from trace_projection import (
    ACTION_LABELS,
    MAX_DOMAIN_CHARS,
    MAX_SOURCES_PER_SEARCH,
    MAX_TITLE_CHARS,
    project_trace,
    safe_display_url,
)


def test_project_trace_prefers_fetched_final_source_and_drops_sensitive_data():
    trace = project_trace([
        {"tool": "web_search", "status": "ok", "result": {
            "query": "private household terms",
            "results": [{
                "title": "Candidate", "url": "https://news.example/a?token=secret", "snippet": "hidden",
            }],
        }},
        {"tool": "web_fetch", "status": "ok", "result": {
            "title": "Final story", "url": "https://news.example/final?utm_source=x#part",
            "published": "2026-09-19", "content": "must not escape",
        }},
    ])

    assert trace[-1]["sources"] == [{
        "title": "Final story", "domain": "news.example",
        "url": "https://news.example/final", "kind": "fetched",
        "published": "2026-09-19",
    }]
    assert "private" not in repr(trace)
    assert "hidden" not in repr(trace)
    assert "content" not in repr(trace)


@pytest.mark.parametrize("value", [
    "javascript:alert(1)",
    "data:text/html,<img src=x onerror=alert(1)>",
    "https://user:password@example.com/private",
    "https://localhost:8080/admin",
    "https://unraid/",
    "https://printer.local/status",
    "https://service.lan/status",
    "https://10.0.0.8/status",
    "https://172.16.0.4/status",
    "https://192.168.1.8/status",
    "https://169.254.1.4/status",
    "https://127.0.0.1/status",
    "https://[::1]/status",
    "https://[fe80::1]/status",
    "https://example.com/path\nnext",
    "https://example.com/path\x00",
    # WHATWG/browser host normalization turns these into private loopback
    # targets even though urllib.parse leaves them as apparently public text.
    "https://%31%32%37.0.0.1/",
    "https://2130706433/",
    "https://0x7f000001/",
    "https://0177.0.0.1/",
    "https://１２７．０．０．１/",
    "https://ⓛⓞⓒⓐⓛⓗⓞⓢⓣ/",
    # Empty userinfo is still userinfo and must not be discarded as falsey.
    "https://@example.com/",
    "https://:@example.com/",
    # urlsplit().port is None for a trailing colon, so reject that malformed
    # authority explicitly alongside other invalid port forms.
    "https://example.com:/",
    "https://example.com:bad/",
    "https://example.com:65536/",
    "https://example.com:+443/",
    "https://[2606:4700:4700::1111]:/",
])
def test_safe_display_url_rejects_unsafe_or_sensitive_urls(value):
    assert safe_display_url(value) is None


@pytest.mark.parametrize(("value", "expected"), [
    ("https://Example.COM:443/news?utm_source=tracking#part", "https://example.com/news"),
    ("http://example.com:80", "http://example.com/"),
    ("https://example.com:8443/news", "https://example.com:8443/news"),
    ("https://example.com/item?token=signed-secret", "https://example.com/item"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com.", "https://example.com/"),
    ("https://[2606:4700:4700::1111]/news", "https://[2606:4700:4700::1111]/news"),
])
def test_safe_display_url_normalizes_public_urls(value, expected):
    assert safe_display_url(value) == expected


def test_project_trace_projects_search_candidates_without_copying_candidate_urls():
    trace = project_trace([{
        "tool": "web_search", "status": "ok", "result": {
            "result_count": 4,
            "results": [
                {"title": "First\nsource", "domain": "news.example", "url": "https://news.example/a?token=secret", "date": "2026-09-18"},
                {"title": "Second source", "domain": "other.example", "url": "https://other.example/b", "date": "2026-09-17"},
                {"title": "Third source", "domain": "third.example", "url": "https://third.example/c", "date": "2026-09-16"},
                {"title": "Fourth source", "domain": "fourth.example", "url": "https://fourth.example/d"},
                "not-a-dict",
            ],
        },
    }])

    assert trace == [{
        "tool": "web_search",
        "action": "Searched the web",
        "status": "complete",
        "sources": [
            {"title": "First source", "domain": "news.example", "url": None, "kind": "candidate", "published": "2026-09-18"},
            {"title": "Second source", "domain": "other.example", "url": None, "kind": "candidate", "published": "2026-09-17"},
            {"title": "Third source", "domain": "third.example", "url": None, "kind": "candidate", "published": "2026-09-16"},
        ],
    }]
    assert "secret" not in repr(trace)


def test_project_trace_deduplicates_normalized_fetched_urls():
    trace = project_trace([
        {"tool": "web_fetch", "status": "ok", "result": {
            "title": "Story one", "url": "https://news.example/story?token=one",
        }},
        {"tool": "web_fetch", "status": "ok", "result": {
            "title": "Story duplicate", "url": "https://NEWS.example/story#section",
        }},
    ])

    assert trace[0]["sources"]
    assert trace[1]["sources"] == []


def test_project_trace_uses_fixed_actions_and_failure_states():
    live_results = [
        {"tool": tool, "status": "ok", "result": {}}
        for tool in ACTION_LABELS
    ]
    live_results.extend([
        {"tool": "web_search", "status": "ok", "result": {"result_count": 0}},
        {"tool": "web_fetch", "status": "timeout", "result": {"error": "secret failure"}},
        {"tool": "future_tool", "status": "ok", "operation_ok": False, "result": {"secret": "x"}},
    ])

    trace = project_trace(live_results)

    assert [entry["action"] for entry in trace[: len(ACTION_LABELS)]] == list(ACTION_LABELS.values())
    assert trace[len(ACTION_LABELS)]["status"] == "no results"
    assert trace[len(ACTION_LABELS) + 1]["status"] == "failed"
    assert trace[len(ACTION_LABELS) + 2]["status"] == "failed"
    assert all(set(entry) == {"tool", "action", "status", "sources"} for entry in trace)
    assert "secret" not in repr(trace)


def test_project_trace_limits_entries_sources_and_text_without_raw_keys():
    long_title = "</a><img onerror=alert(1)>" + "x" * 300
    raw = [
        {"tool": "web_search", "status": "ok", "result": {
            "results": [
                {"title": long_title, "domain": "d" * 500, "date": "date\n" + "x" * 100}
                for _ in range(MAX_SOURCES_PER_SEARCH + 2)
            ],
            "private_query": "household terms",
        }}
        for _ in range(20)
    ]

    trace = project_trace(raw)

    assert len(trace) <= 12
    assert all(len(entry["sources"]) <= MAX_SOURCES_PER_SEARCH for entry in trace)
    source = trace[0]["sources"][0]
    assert len(source["title"]) == MAX_TITLE_CHARS
    assert len(source["domain"]) == MAX_DOMAIN_CHARS
    assert len(source["published"]) == 40
    assert len(json.dumps(trace, ensure_ascii=False).encode("utf-8")) <= 16_384
    assert "household" not in repr(trace)
    assert "onerror" in source["title"]


def test_project_trace_ignores_non_mapping_results_and_missing_values():
    trace = project_trace([
        {"tool": None, "status": None, "result": "not-a-dict"},
        {"tool": "web_search", "status": "ok", "result": {"results": None}},
        {"tool": "web_fetch", "status": "ok", "result": {"url": None}},
    ])

    assert trace == [
        {"tool": "unknown", "action": "Used an assistant tool", "status": "failed", "sources": []},
        {"tool": "web_search", "action": "Searched the web", "status": "no results", "sources": []},
        {"tool": "web_fetch", "action": "Opened source", "status": "complete", "sources": []},
    ]


@pytest.mark.parametrize("result", [
    {"result_count": "wat", "results": [{"title": "must be ignored", "domain": "public.example"}]},
    {"result_count": "wat", "results": 1},
    {"result_count": 1, "results": 1},
    {"result_count": 1, "results": None},
    {"result_count": -1, "results": [{"title": "must be ignored", "domain": "public.example"}]},
])
def test_project_trace_treats_malformed_search_shapes_as_empty(result):
    trace = project_trace([{"tool": "web_search", "status": "ok", "result": result}])

    assert trace == [{
        "tool": "web_search",
        "action": "Searched the web",
        "status": "no results",
        "sources": [],
    }]
