"""Contract tests for the additive OpenAI-compatible Home-AI facade."""

import hashlib
from pathlib import Path


SOURCE = Path(__file__).with_name("voice-api-app.py")


def _load_helpers():
    import ast
    import types

    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {
        "OPENAI_COMPAT_MODEL",
        "_openai_session_id",
        "_latest_user_message",
        "_is_openwebui_housekeeping_request",
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
        and any(alias.name == "re" for alias in node.names)
    ]
    selected += [node for node in tree.body if (isinstance(node, ast.FunctionDef) and node.name in names) or is_needed_assignment(node)]
    namespace["Request"] = object
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
    assert session == "webui:u1:chat-a"
    assert session != "webui:u1:chat-b"


def test_stateless_provider_fallback_derives_stable_chat_key_from_first_user_turn():
    module = _load_helpers()
    body = {"messages": [{"role": "user", "content": "What's the weather?"}]}
    actual = module._openai_session_id(Request(), body)
    expected = "webui:default:derived-" + hashlib.sha256(b"What's the weather?").hexdigest()[:24]
    assert actual == expected


def test_session_key_sanitizes_untrusted_identifiers():
    module = _load_helpers()
    actual = module._openai_session_id(
        Request(), {"metadata": {"user_id": "u/one", "chat_id": "../../other"}}
    )
    assert actual == "webui:u_one:.._.._other"


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
