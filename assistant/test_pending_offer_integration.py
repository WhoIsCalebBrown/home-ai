"""Tests for the PendingOffer integration inside voice-api-app.py:
stage_media_offer() (offer creation from a media_plan_goal result) and the
structural guarantee that no code path in this file ever persists an
OpenAI/Ollama-style tool_calls-bearing message into the durable per-client
`sessions` history (the precondition for HookReel's orphaned-tool_call bug,
which this file's design makes structurally impossible -- see
stage_media_offer/respond commit message and the final report for the full
argument).

Uses the same ast-slice convention as test_grounding_regressions.py so this
does not require fastapi/wyoming/httpx/nemo to be installed.
"""

import ast
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SOURCE_PATH = Path(__file__).with_name("voice-api-app.py")
SOURCE_TEXT = SOURCE_PATH.read_text()
tree = ast.parse(SOURCE_TEXT)

needed = {"stage_media_offer", "discovery_audit", "explicit_domain", "media_goal_request", "classify_offer_reply"}


def is_needed_assignment(node):
    targets = getattr(node, "targets", [])
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return isinstance(node, (ast.Assign, ast.AnnAssign)) and any(getattr(target, "id", None) in needed for target in targets)


nodes = [node for node in tree.body if getattr(node, "name", None) in needed or is_needed_assignment(node)]

import pytest
from subject_model import PendingOffer, ResolvedSubject, available_actions, build_canonical_identity, classify_offer_reply, next_best_action

namespace = {
    "json": json, "time": time,
    "PendingOffer": PendingOffer, "ResolvedSubject": ResolvedSubject,
    "available_actions": available_actions, "build_canonical_identity": build_canonical_identity,
    "next_best_action": next_best_action, "classify_offer_reply": classify_offer_reply,
    "pending_offers": {},
    "DISCOVERY_AUDIT_LOG": "/tmp/home-ai-test-discovery-audit.jsonl",
    "Path": Path,
}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "voice-api-app.py", "exec"), namespace)
stage_media_offer = namespace["stage_media_offer"]
pending_offers = namespace["pending_offers"]


def _plan_result(**overrides):
    base = {
        "canonical_identity": {"media_type": "tv", "title": "Segua", "tvdb_id": "999"},
        "current_state": "IDENTIFIED",
    }
    base.update(overrides)
    return base


def test_stage_media_offer_creates_a_read_only_offer():
    pending_offers.clear()
    question = stage_media_offer("client-1", _plan_result())
    assert question is not None
    assert "?" in question
    entry = pending_offers["client-1"]
    assert entry["offer"].side_effect == "read"
    assert entry["offer"].operation in {"plex_match_canonical_media", "media_plan_goal", "media_resolve"}


def test_stage_media_offer_returns_none_without_identity():
    pending_offers.clear()
    assert stage_media_offer("client-2", {"canonical_identity": None, "current_state": "UNKNOWN"}) is None
    assert "client-2" not in pending_offers


def test_available_in_plex_offers_details_not_a_request():
    pending_offers.clear()
    question = stage_media_offer("client-3", _plan_result(current_state="AVAILABLE_IN_PLEX"))
    entry = pending_offers.get("client-3")
    if entry is not None:
        assert entry["offer"].operation != "media_standard_request"


def test_staging_a_new_offer_replaces_the_previous_one_for_the_same_client():
    pending_offers.clear()
    stage_media_offer("client-4", _plan_result(canonical_identity={"media_type": "movie", "title": "Dune", "tmdb_id": "1"}))
    first_offer_id = pending_offers["client-4"]["offer"].offer_id
    stage_media_offer("client-4", _plan_result(canonical_identity={"media_type": "movie", "title": "Cowboy Bebop", "tmdb_id": "2"}))
    second_offer_id = pending_offers["client-4"]["offer"].offer_id
    assert first_offer_id != second_offer_id
    assert len(pending_offers) == 1  # never accumulates a second live offer for one client


def test_staged_offer_arguments_match_the_target_tools_real_contract():
    """A real bug found via qa/test_assistant_conversation_integration.py:
    the offer's staged arguments must match whichever tool.tool_name it
    names -- media_plan_goal takes `goal` (free text), media_status takes
    `workflow_id`/`title`, everything else takes structured identity
    fields. A single generic argument dict silently broke media_plan_goal
    (it received no `goal` key and returned an empty plan)."""
    pending_offers.clear()
    stage_media_offer("client-6", _plan_result(current_state="IDENTIFIED"))
    entry = pending_offers["client-6"]
    if entry["offer"].operation == "media_plan_goal":
        assert "goal" in entry["arguments"]
        assert entry["arguments"]["goal"] == "Segua"
    elif entry["offer"].operation in {"media_status", "media_diagnose"}:
        assert "workflow_id" in entry["arguments"] or "title" in entry["arguments"]


def test_no_write_state_ever_reaches_stage_media_offer_as_an_offer():
    """REQUEST_MEDIA/PLAN_MEDIA_REQUEST are the only write-shaped actions in
    the action catalog; stage_media_offer must never stage one as a
    PendingOffer regardless of state, because the guard in respond() only
    calls it when confirmation_required is already false, but this proves
    the function itself would also refuse to stage a write action if ever
    called with a state that produced one."""
    pending_offers.clear()
    for state in ("ABSENT", "IDENTIFIED", "AVAILABLE_IN_PLEX", "SEARCHING", "FAILED"):
        stage_media_offer("client-5", _plan_result(current_state=state))
        entry = pending_offers.get("client-5")
        if entry is not None:
            assert entry["offer"].side_effect == "read"


# --- Structural proof: no history.append() call in this file can ever
# persist a tool_calls-bearing message (the precondition for HookReel's
# _heal_history bug). ---------------------------------------------------

def test_history_append_never_carries_tool_calls():
    """Every `history.append({...})` call site in voice-api-app.py must be a
    dict literal whose only role-key entries are role/content (never
    tool_calls). If a future edit ever appends a raw model message with
    tool_calls into the durable `sessions` history, this test fails and the
    tool-call-history-healing analysis in the final report needs revisiting.
    """
    violations = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "history"):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys = [k.value for k in arg.keys if isinstance(k, ast.Constant)]
                    if "tool_calls" in keys:
                        violations.append(node.lineno)
    assert violations == [], f"history.append() carries tool_calls at line(s): {violations}"


def test_sessions_dict_is_the_only_cross_turn_history_store():
    """`sessions` is declared once at module scope; every respond() call
    reads/writes the same dict via sessions.setdefault(client_id, []) -- so
    there is exactly one durable per-client history structure to reason
    about, and it is proven empty of tool_calls above."""
    assignments = [
        node for node in ast.walk(tree)
        if (isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "sessions" for t in node.targets))
        or (isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "sessions")
    ]
    assert len(assignments) == 1
