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
    }
    module = types.SimpleNamespace(OPENAI_COMPAT_MODEL="home-ai")
    namespace = module.__dict__
    selected = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    selected += [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
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
