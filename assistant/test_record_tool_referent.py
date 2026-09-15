"""Unit tests for record_tool_referent -- the fix for web_search (and other
identification-capable tool) results never feeding back into
conversation_context.latest_resolved_referent, found via
qa/test_assistant_conversation_integration.py."""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SOURCE_PATH = Path(__file__).with_name("voice-api-app.py")
tree = ast.parse(SOURCE_PATH.read_text())

needed = {"record_tool_referent", "_REFERENT_ARGUMENT_KEYS", "conversation_context"}


def is_needed_assignment(node):
    targets = getattr(node, "targets", [])
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return isinstance(node, (ast.Assign, ast.AnnAssign)) and any(getattr(t, "id", None) in needed for t in targets)


nodes = [node for node in tree.body if getattr(node, "name", None) in needed or is_needed_assignment(node)]
namespace = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "voice-api-app.py", "exec"), namespace)
record_tool_referent = namespace["record_tool_referent"]
conversation_context = namespace["conversation_context"]


def test_web_search_result_sets_referent_from_query():
    conversation_context.clear()
    record_tool_referent("c1", "web_search", {"query": "Segua"}, {"tool": "web_search", "status": "ok", "result": {"results": []}})
    assert conversation_context["c1"]["latest_resolved_referent"] == "Segua"


def test_media_plan_goal_result_prefers_canonical_title_over_raw_goal():
    conversation_context.clear()
    record_tool_referent(
        "c1", "media_plan_goal", {"goal": "cowboy bebop anime show"},
        {"tool": "media_plan_goal", "status": "ok", "result": {"canonical_identity": {"title": "Cowboy Bebop"}}},
    )
    assert conversation_context["c1"]["latest_resolved_referent"] == "Cowboy Bebop"


def test_unrecognized_tool_does_not_touch_referent():
    conversation_context.clear()
    conversation_context["c1"] = {"latest_resolved_referent": "existing"}
    record_tool_referent("c1", "get_storage_status", {}, {"tool": "get_storage_status", "status": "ok", "result": {}})
    assert conversation_context["c1"]["latest_resolved_referent"] == "existing"


def test_empty_argument_and_no_identity_leaves_referent_untouched():
    conversation_context.clear()
    conversation_context["c1"] = {"latest_resolved_referent": "existing"}
    record_tool_referent("c1", "web_search", {"query": ""}, {"tool": "web_search", "status": "ok", "result": {}})
    assert conversation_context["c1"]["latest_resolved_referent"] == "existing"
