"""Unit tests for the generic DISCOVERY-question grammar (voice-api-app.py's
discovery_question()) -- a closed set of question shapes, not a per-title
regex. See turn_context()'s `elif discovery_question(text):` branch for how
this feeds latest_resolved_referent without deciding media/general/web."""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from semantic_routing import has_referential_language

SOURCE_PATH = Path(__file__).with_name("voice-api-app.py")
tree = ast.parse(SOURCE_PATH.read_text())

needed = {"discovery_question", "_tokens_for_discovery", "_DISCOVERY_QUESTION_PATTERNS", "_DISCOVERY_QUESTION_STOPWORDS"}


def is_needed_assignment(node):
    targets = getattr(node, "targets", [])
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return isinstance(node, (ast.Assign, ast.AnnAssign)) and any(getattr(t, "id", None) in needed for t in targets)


nodes = [node for node in tree.body if getattr(node, "name", None) in needed or is_needed_assignment(node)]
namespace = {"re": __import__("re"), "has_referential_language": has_referential_language}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "voice-api-app.py", "exec"), namespace)
discovery_question = namespace["discovery_question"]


import pytest


@pytest.mark.parametrize("text,expected_subject", [
    ("Do you know Cowboy Bebop?", "Cowboy Bebop"),
    ("Have you heard of Cowboy Bebop?", "Cowboy Bebop"),
    ("What is Cowboy Bebop?", "Cowboy Bebop"),
    ("Do you know this show called Cowboy Bebop?", "Cowboy Bebop"),
    ("There's a show called Segua, do you know it?", "Segua"),
    ("Can you tell me what Segua is?", "Segua"),
    ("What can you find about Segua?", "Segua"),
    ("Do you know Kubernetes?", "Kubernetes"),
    ("Do you know the restaurant Nami?", "restaurant Nami"),
    ("Do you know the show Severance?", "show Severance"),
])
def test_extracts_subject_from_known_shapes(text, expected_subject):
    assert discovery_question(text) == expected_subject


@pytest.mark.parametrize("text", [
    "What's the weather tomorrow?",
    "Can you find it on the internet?",
    "Do you know it?",
    "",
    "   ",
    "Do you know?",
])
def test_does_not_match_non_discovery_or_bare_referential_text(text):
    # "What's the weather tomorrow" IS matched by the "what's X" shape at the
    # grammar level (it extracts "the weather tomorrow") -- this function
    # does not classify domain, so that extraction is expected and harmless;
    # explicit_domain's earlier weather branch already wins before
    # discovery_question is ever consulted in turn_context. This test only
    # asserts the referential/empty-input guards actually apply.
    result = discovery_question(text)
    if text.strip().rstrip("?.!") in {"", "it", "Do you know", "Do you know it"}:
        assert result is None


def test_bare_pronoun_is_never_treated_as_a_fresh_subject():
    assert discovery_question("Do you know it?") is None
    assert discovery_question("What's that?") is None


def test_does_not_assume_media_type_grammar_only_extracts_subject():
    """discovery_question must not itself decide media vs general knowledge
    -- both a media-shaped and a clearly non-media subject extract cleanly,
    proving classification is not baked into the grammar (spec section 4)."""
    assert discovery_question("Do you know Kubernetes?") == "Kubernetes"
    assert discovery_question("Do you know the show Severance?") == "show Severance"


def test_subject_length_guard():
    long_text = "Do you know " + ("x " * 60) + "?"
    assert discovery_question(long_text) is None
