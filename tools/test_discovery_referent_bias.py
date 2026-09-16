"""Real, cross-service reproduction of a live production bug: a stale
structured referent (a camera event from an earlier turn) biased bounded
capability discovery for a completely unrelated follow-up.

Live example: user asks for vocabulary quiz sentences, gets a fine answer,
then says "Question 1?" as a natural continuation -- and gets back an
unrelated Frigate camera door-activity summary. Root cause: turn_context()
correctly keeps `camera`/`latest_event` referents in context indefinitely
(so a later genuinely-referential follow-up can still use them), but
discover_capabilities()'s `referent_overlap` scoring boost
(tools/server-tools-app.py) used to apply unconditionally -- with no check
for whether the NEW utterance has any connection to that stale referent.

This test drives the REAL functions on both sides of that boundary:
assistant/semantic_routing.py's discovery_context() (now gated on
has_referential_language()/context["domain"]) feeding tools/
server-tools-app.py's real discover_capabilities() -- not a fake
respond()-level harness, which stubs discover_tools() entirely and would
never actually exercise either function.
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "assistant"))

spec = importlib.util.spec_from_file_location("server_tools_app_referent_bias_test", Path(__file__).with_name("server-tools-app.py"))
_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_module)
discover_capabilities = _module.discover_capabilities

from semantic_routing import discovery_context  # noqa: E402

STALE_CAMERA_CONTEXT = {
    "camera": "front_door",
    "latest_event": "person detected at the front door",
    "latest_event_id": "evt-123",
}


def _camera_tool_names(query: str, context: dict) -> set[str]:
    retrieval_context = discovery_context(context, query)
    results = discover_capabilities(query, max_results=5, context=retrieval_context)
    return {item["metadata"]["canonical_name"] for item in results} & {
        "frigate_snapshot", "frigate_stats", "frigate_recent_events",
    }


def test_unrelated_followup_after_stale_camera_referent_is_not_hijacked():
    """The real production repro: no camera words, no pronoun, nothing
    connecting this utterance to the retained camera event. The stale
    referent must produce EXACTLY the same candidate set as no referent at
    all -- any pre-existing baseline noise in short-query n-gram scoring is
    out of scope for this fix; what matters is the referent adds no bias."""
    baseline = _camera_tool_names("Question 1?", {})
    assert _camera_tool_names("Question 1?", STALE_CAMERA_CONTEXT) == baseline


def test_other_unrelated_followups_after_stale_camera_referent_are_also_safe():
    """Broader than the one verbatim sentence -- several genuinely unrelated
    follow-ups after the same stale referent must each match their own
    referent-free baseline. Deliberately avoids bare pronouns/demonstratives
    from has_referential_language()'s word list ("it", "that", "one", ...)
    in this corpus -- those are, by design, treated as a connecting signal
    (see test_genuine_referential_followup_still_resolves_against_the_referent
    and the module docstring on discovery_context); a phrase like "a harder
    one" is a known, accepted false-positive of reusing that existing
    heuristic, not something this fix attempts to further disambiguate."""
    for text in (
        "What's another example sentence?",
        "Give me a different topic to talk about.",
        "Let's move on to something else.",
        "Can you help me with my homework instead?",
    ):
        baseline = _camera_tool_names(text, {})
        assert _camera_tool_names(text, STALE_CAMERA_CONTEXT) == baseline, text


def test_genuine_referential_followup_still_resolves_against_the_referent():
    """Negative control: a real elliptical continuation ("when did THAT
    happen", "what were THEY doing") must still get the referent boost --
    this fix must not sever real follow-up resolution, only stale-referent
    hijacking of unrelated turns."""
    assert "frigate_recent_events" in _camera_tool_names("when did that happen?", STALE_CAMERA_CONTEXT)
    assert "frigate_recent_events" in _camera_tool_names("what were they doing?", STALE_CAMERA_CONTEXT)


def test_explicit_domain_continuation_also_still_gets_the_referent_boost():
    """A turn that turn_context() has already resolved to the camera domain
    (context["domain"] == "camera") is connected even without a pronoun."""
    context = {**STALE_CAMERA_CONTEXT, "domain": "camera"}
    assert "frigate_recent_events" in _camera_tool_names("is anything happening at the front door", context)


def test_no_referent_at_all_does_not_crash_and_is_the_baseline():
    """Sanity check that the baseline itself (no context at all) is a stable,
    deterministic call -- the differential tests above compare against this."""
    assert _camera_tool_names("Question 1?", {}) == _camera_tool_names("Question 1?", {})
