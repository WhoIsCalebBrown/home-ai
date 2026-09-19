"""Static browser-rendering contract tests (this repository has no JS runner)."""

from pathlib import Path
import re


SOURCE = Path(__file__).with_name("voice-api-index.html")


def _function_body(name: str) -> str:
    source = SOURCE.read_text(encoding="utf-8")
    match = re.search(rf"function {name}\([^)]*\)\{{", source)
    assert match, f"{name} must be a named browser function"
    depth = 0
    for offset, character in enumerate(source[match.end() - 1:], start=match.end() - 1):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():offset + 1]
    raise AssertionError(f"{name} has no closing brace")


def test_trace_renderer_uses_dom_text_nodes_and_rejects_unapproved_links():
    renderer = _function_body("renderTrace")
    approved_url = _function_body("approvedTraceUrl")
    assert "document.createElement('a')" in renderer
    assert ".textContent=" in renderer
    assert ".innerHTML" not in renderer
    assert "if(!source.url" in renderer
    assert "!/^https?:\\/\\//i.test(source.url)" in renderer
    assert "url.protocol!=='http:'" in approved_url
    assert "url.protocol!=='https:'" in approved_url
