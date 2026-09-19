"""Contract tests for the additive OpenAI-compatible Home-AI facade."""

from pathlib import Path
from urllib.parse import quote


SOURCE = Path(__file__).with_name("voice-api-app.py")


def _load_helpers():
    import ast
    import types

    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {
        "OPENAI_COMPAT_MODEL",
        "_openai_session_id",
        "_prepare_openai_turn",
        "_latest_user_message",
        "_is_openwebui_housekeeping_request",
        "_safe_markdown_text",
        "_safe_markdown_destination",
        "openai_tool_trace_footer",
        "remove_openai_display_metadata",
    }
    assignment_names = {"_OPENWEBUI_HOUSEKEEPING_TASK_SIGNATURES"}

    def is_needed_assignment(node):
        targets = getattr(node, "targets", [])
        return isinstance(node, ast.Assign) and any(getattr(target, "id", None) in assignment_names for target in targets)

    module = types.SimpleNamespace(OPENAI_COMPAT_MODEL="home-ai")
    namespace = module.__dict__
    selected = [
        node for node in tree.body
        if isinstance(node, ast.Import)
        and any(alias.name in {"re", "uuid"} for alias in node.names)
    ]
    selected += [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module in {"trace_projection", "urllib.parse"}
    ]
    selected += [node for node in tree.body if (isinstance(node, ast.FunctionDef) and node.name in names) or is_needed_assignment(node)]
    namespace["Request"] = object
    namespace["quote"] = quote
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return module


class Request:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_latest_user_message_ignores_frontend_system_and_assistant_messages():
    module = _load_helpers()
    assert module._latest_user_message({
        "messages": [
            {"role": "system", "content": "frontend metadata"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow-up"},
        ]
    }) == "follow-up"


def test_explicit_chat_id_maps_to_isolated_home_ai_session():
    module = _load_helpers()
    session = module._openai_session_id(Request(), {"metadata": {"user_id": "u1", "chat_id": "chat-a"}})
    assert session == "openwebui:u1:chat-a"
    assert session != "openwebui:u1:chat-b"


def test_same_chat_id_isolated_between_openwebui_users():
    """A frontend chat id is only unique within its owning user account."""
    module = _load_helpers()
    user_a = module._openai_session_id(
        Request(), {"metadata": {"user_id": "user-a", "chat_id": "shared-looking-id"}}
    )
    user_b = module._openai_session_id(
        Request(), {"metadata": {"user_id": "user-b", "chat_id": "shared-looking-id"}}
    )
    assert user_a == "openwebui:user-a:shared-looking-id"
    assert user_b == "openwebui:user-b:shared-looking-id"
    assert user_a != user_b


def test_deployed_openwebui_headers_are_the_primary_identity_contract():
    module = _load_helpers()
    request = Request({"x-openwebui-user-id": "owui-user", "x-openwebui-chat-id": "owui-chat"})
    assert module._openai_session_id(request, {"metadata": {"user_id": "wrong-user", "chat_id": "wrong-chat"}}) == "openwebui:owui-user:owui-chat"


def test_identical_first_prompts_without_identity_never_share_state():
    """Legacy callers are bounded to one turn rather than prompt-derived."""
    module = _load_helpers()
    body = {"messages": [{"role": "user", "content": "What's that Tom Hanks movie where he's on an island with a volleyball?"}]}
    first = module._openai_session_id(Request(), body)
    second = module._openai_session_id(Request(), body)
    assert first.startswith("legacy:")
    assert second.startswith("legacy:")
    assert first != second


def test_legacy_client_can_continue_only_with_its_explicit_session_token():
    module = _load_helpers()
    request = Request({"x-home-ai-session-id": "client-generated-session-123"})
    assert module._openai_session_id(request, {"messages": []}) == "legacy:client-generated-session-123"


def test_session_key_sanitizes_untrusted_identifiers():
    module = _load_helpers()
    actual = module._openai_session_id(
        Request(), {"metadata": {"user_id": "u/one", "chat_id": "../../other"}}
    )
    assert actual == "openwebui:u%2Fone:..%2F..%2Fother"


def test_legacy_response_token_can_be_echoed_without_double_prefix():
    module = _load_helpers()
    request = Request({"x-home-ai-session-id": "legacy:client-generated-session-123"})
    assert module._openai_session_id(request, {"messages": []}) == "legacy:client-generated-session-123"


def test_prepared_stream_headers_keep_the_same_legacy_session_and_correlation():
    module = _load_helpers()
    request = Request()
    body = {"messages": [{"role": "user", "content": "Check the weather."}]}
    first = module._prepare_openai_turn(body, request)
    assert module._prepare_openai_turn(body, request) == first
    client_id, correlation = first
    assert correlation["home_ai_session_id"] == client_id
    assert correlation["request_id"].startswith("req-")
    assert correlation["turn_id"].startswith("turn-")
    assert correlation["trace_id"].startswith("trace-")


# --- OpenWebUI internal housekeeping detection (real production bug: these ---
# --- were being routed through the full tool-discovery/execution pipeline ---

TITLE_TASK_PROMPT = (
    "### Task:\nGenerate a concise, 3-5 word title with an emoji summarizing "
    "the chat history.\n### Chat History:\n<chat_history>\nUSER: do I have "
    "any Travis Scott albums\nASSISTANT: Yes, Utopia is in your library.\n"
    "</chat_history>"
)
TAGS_TASK_PROMPT = (
    "### Task:\nGenerate 1-3 broad tags categorizing the main themes of the "
    "chat history, along with 1-3 more specific tags.\n### Chat History:\n"
    "<chat_history>\nUSER: what's the weather tomorrow\n</chat_history>"
)
FOLLOW_UP_TASK_PROMPT = (
    "### Task:\nSuggest 3-5 relevant follow-up questions or prompts that the "
    "user might naturally ask next in this conversation as a user, based on "
    "the chat history.\n### Chat History:\n<chat_history>\nUSER: is the room "
    "ready in plex\n</chat_history>"
)


def _body(prompt: str) -> dict:
    return {"messages": [{"role": "user", "content": prompt}]}


def test_recognizes_title_generation_housekeeping_task():
    module = _load_helpers()
    assert module._is_openwebui_housekeeping_request(_body(TITLE_TASK_PROMPT)) is True


def test_recognizes_tags_generation_housekeeping_task():
    module = _load_helpers()
    assert module._is_openwebui_housekeeping_request(_body(TAGS_TASK_PROMPT)) is True


def test_recognizes_follow_up_generation_housekeeping_task():
    module = _load_helpers()
    assert module._is_openwebui_housekeeping_request(_body(FOLLOW_UP_TASK_PROMPT)) is True


def test_genuine_user_chat_message_is_not_flagged_as_housekeeping():
    """Negative control: real chat traffic (including messages that mention
    "task" in passing) must still flow through the real pipeline normally."""
    module = _load_helpers()
    assert module._is_openwebui_housekeeping_request(_body("Can you request the movie The Room?")) is False
    assert module._is_openwebui_housekeeping_request(_body("What's my next task for today?")) is False
    assert module._is_openwebui_housekeeping_request(_body("### Task: remind me to take out the trash")) is False


def test_unrecognized_task_shape_is_not_swallowed():
    """Only the three task shapes with live audit evidence are matched -- a
    fourth, unseen "### Task:" shape must NOT be silently treated as
    housekeeping (that would risk swallowing a real user turn); it should
    still flow through the real pipeline until a real example is captured
    and a pattern is added deliberately."""
    module = _load_helpers()
    assert module._is_openwebui_housekeeping_request(_body("### Task:\nSummarize this document for me.")) is False


def test_rich_footer_names_opened_sources_without_raw_tool_data():
    module = _load_helpers()
    footer = module.openai_tool_trace_footer([{
        "tool": "web_fetch", "action": "Opened source", "status": "complete",
        "sources": [{
            "title": "Canada update", "domain": "cbc.ca",
            "url": "https://cbc.ca/news/update", "kind": "fetched",
        }],
    }])
    assert "<!-- home-ai-display-trace -->" in footer
    assert "Opened source" in footer
    assert "[Canada update](https://cbc.ca/news/update)" in footer
    assert "web_fetch" not in footer


def test_rich_footer_deduplicates_and_bounds_safe_projected_sources():
    module = _load_helpers()
    trace = [{
        "tool": "web_fetch", "action": "Opened source", "status": "complete",
        "sources": [{
            "title": "Safe source", "domain": "example.com",
            "url": "https://example.com/story?token=secret", "kind": "fetched",
            "query": "household terms", "content": "never display",
        }] * 4,
    }] * 13
    footer = module.openai_tool_trace_footer(trace)
    assert footer.count("Safe source") == 1
    assert footer.count("Opened source") <= 12
    assert "token=secret" not in footer
    assert "household terms" not in footer
    assert "never display" not in footer


def test_rich_footer_escapes_hostile_markdown_labels_and_destinations():
    module = _load_helpers()
    footer = module.openai_tool_trace_footer([{
        "action": "**Bold** _italic_ `code` [spoof](https://evil.example)",
        "status": "complete",
        "sources": [{
            "title": "**Bold** _italic_ `code` [spoof](https://evil.example)\\",
            "domain": "news_*`[spoof](x).example",
            "url": "https://example.com/](https://evil.example/)*_`\\",
        }],
    }])
    assert "\\*\\*Bold\\*\\*" in footer
    assert "\\_italic\\_" in footer
    assert "\\`code\\`" in footer
    assert r"\[spoof\]\(https\:\/\/evil\.example\)" in footer
    assert "news\\_\\*\\`\\[spoof\\]\\(x\\)\\.example" in footer
    assert "https://example.com/%5D%28https://evil.example/%29%2A_%60%5C" in footer


def test_display_metadata_fallback_removes_only_owned_rich_boundaries():
    module = _load_helpers()
    displayed = (
        "**Working**\n- Searching the web…\n\n---\n\n"
        "Here is the answer.\n\n<!-- home-ai-display-trace -->\n"
        "Research activity\n- Opened CBC News\n"
        "Sources\n- [Canada update](https://cbc.ca/news/update)"
    )
    assert module.remove_openai_display_metadata(displayed) == "Here is the answer."
    ordinary = "I was working on this.\n\n---\n\nThe separator is intentional."
    assert module.remove_openai_display_metadata(ordinary) == ordinary
