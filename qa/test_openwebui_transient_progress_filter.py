import importlib.util
from pathlib import Path


FILTER_PATH = (
    Path(__file__).resolve().parents[1]
    / "deployment"
    / "open-webui"
    / "home_ai_transient_progress.py"
)
SPEC = importlib.util.spec_from_file_location("home_ai_transient_progress", FILTER_PATH)
assert SPEC and SPEC.loader
FILTER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FILTER_MODULE)


def test_outlet_removes_completed_progress_and_private_marker_but_keeps_trace():
    body = {
        "model": "home-ai",
        "messages": [
            {"role": "user", "content": "What happened?"},
            {
                "id": "answer-1",
                "role": "assistant",
                "content": (
                    "**Working**\n- Checking your home…\n\n---\n\n"
                    "A person entered the foyer.\n\n"
                    "<!-- home-ai-display-trace -->\n---\n**Research activity**\n"
                    "- Used an assistant tool — complete"
                ),
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": (
                            "**Working**\n- Checking your home…\n\n---\n\n"
                            "A person entered the foyer.\n\n"
                            "<!-- home-ai-display-trace -->\n---\n**Research activity**\n"
                            "- Used an assistant tool — complete"
                        ),
                    }],
                }],
            },
        ],
    }

    result = FILTER_MODULE.Filter().outlet(body)

    assert result["messages"][-1]["content"] == (
        "A person entered the foyer.\n\n---\n**Research activity**\n"
        "- Used an assistant tool — complete"
    )
    assert result["messages"][-1]["output"][0]["content"][0]["text"] == (
        "A person entered the foyer.\n\n---\n**Research activity**\n"
        "- Used an assistant tool — complete"
    )


def test_outlet_only_changes_latest_assistant_message():
    old = "**Working**\n- Working…\n\n---\n\nOld answer."
    body = {
        "model": "home-ai",
        "messages": [
            {"role": "assistant", "content": old},
            {"role": "user", "content": "Again"},
            {"role": "assistant", "content": "**Working**\n- Working…\n\n---\n\nNew answer."},
        ],
    }

    result = FILTER_MODULE.Filter().outlet(body)

    assert result["messages"][0]["content"] == old
    assert result["messages"][-1]["content"] == "New answer."


def test_outlet_does_not_strip_other_models_or_user_shaped_content():
    content = "**Working**\n- User-authored bullet\n\n---\n\nKeep this."
    other = {"model": "another-model", "messages": [{"role": "assistant", "content": content}]}
    malformed = {"model": "home-ai", "messages": [{"role": "assistant", "content": content}]}

    assert FILTER_MODULE.Filter().outlet(other)["messages"][0]["content"] == content
    assert FILTER_MODULE.Filter().outlet(malformed)["messages"][0]["content"] == content


def test_stream_hides_only_the_exact_trace_marker():
    event = {
        "choices": [
            {
                "delta": {
                    "content": (
                        "Answer.\n\n<!-- home-ai-display-trace -->\n---\n"
                        "**Research activity**\n"
                    )
                }
            }
        ]
    }

    result = FILTER_MODULE.Filter().stream(event)

    assert result["choices"][0]["delta"]["content"] == (
        "Answer.\n\n---\n**Research activity**\n"
    )


def test_outlet_accepts_model_from_openwebui_context():
    body = {
        "messages": [
            {"role": "assistant", "content": "**Working**\n- Reading example.com…\n\n---\n\nDone."}
        ]
    }

    result = FILTER_MODULE.Filter().outlet(body, __model__={"id": "home-ai"})

    assert result["messages"][0]["content"] == "Done."


def test_outlet_accepts_home_ai_qa_and_fixture_model_ids():
    for model in ("home-ai-qa", "home-ai-progress-source-fixture"):
        body = {
            "model": model,
            "messages": [
                {"role": "assistant", "content": "**Working**\n- Working…\n\n---\n\nDone."}
            ],
        }

        result = FILTER_MODULE.Filter().outlet(body)

        assert result["messages"][0]["content"] == "Done."
