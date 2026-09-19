import asyncio
import tempfile
import time
import importlib.util

from nemo_text_processing.text_normalization.normalize import Normalizer

spec = importlib.util.spec_from_file_location("voice_api_app", "/app/voice-api-app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_media_tool_trace_is_removed_from_speech():
    """A display-only Open WebUI footer must never become spoken content."""
    assert app.remove_openai_tool_trace(
        "That's White Chicks (2004).\n\n---\n**Tools used**\n- `media_plan_goal` — ok"
    ) == "That's White Chicks (2004)."
    assert app.remove_openai_tool_trace("Tools used media_plan_goal — ok") == ""


def test_rich_sources_and_persistent_progress_are_never_spoken():
    displayed = (
        "**Working**\n- Searching recent Canadian headlines…\n"
        "- Reading CBC News…\n\n---\n\n"
        "Here is the Canadian roundup.\n\n<!-- home-ai-display-trace -->\n"
        "Research activity\n- Opened CBC News\n"
        "Sources\n- [Canada update](https://cbc.ca/news/update)"
    )
    assert app.remove_openai_display_metadata(displayed) == "Here is the Canadian roundup."


def test_fallback_does_not_delete_ordinary_working_or_separator_text():
    answer = "I was working on this.\n\n---\n\nThe separator is part of the answer."
    assert app.remove_openai_display_metadata(answer) == answer


def test_progress_trace_and_source_only_fragments_are_silent():
    progress_only = "**Working**\n- Searching recent Canadian headlines…\n\n---\n\n"
    trace_only = (
        "<!-- home-ai-display-trace -->\n---\n**Research activity**\n"
        "- Opened CBC News\nSources\n- [Canada update](https://cbc.ca/news/update)"
    )
    source_only = (
        "<!-- home-ai-display-trace -->\n---\n**Sources**\n"
        "- [Canada update](https://cbc.ca/news/update)"
    )
    assert app.remove_openai_display_metadata(progress_only) == ""
    assert app.remove_openai_display_metadata(trace_only) == ""
    assert app.remove_openai_display_metadata(source_only) == ""


class _SpeechRequest:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}

    async def json(self):
        return self.payload


def test_openai_speech_keeps_display_only_trace_silent_and_uses_registered_answer(monkeypatch):
    """Changing the TTS registry or sanitizer must not speak a tool footer."""
    monkeypatch.setattr(app, "_require_openai_auth", lambda request: None)
    synthesized = []

    async def fake_synthesize_pocket(text):
        synthesized.append(text)
        return b"RIFF"

    monkeypatch.setattr(app, "synthesize_pocket", fake_synthesize_pocket)

    footer_only = asyncio.run(app.openai_speech(_SpeechRequest({
        "input": "Tools used media_plan_goal — ok",
    })))
    assert footer_only.status_code == 204
    assert synthesized == []

    spoken = "That's White Chicks (2004)."
    displayed = spoken + (
        "\n\n<!-- home-ai-display-trace -->\n---\n**Research activity**\n"
        "- Opened CBC News\nSources\n- [Canada update](https://cbc.ca/news/update)"
    )
    app.register_openai_tts_text(displayed, spoken)
    response = asyncio.run(app.openai_speech(_SpeechRequest({"input": displayed})))

    assert response.status_code == 200
    assert response.body == b"RIFF"
    assert synthesized == [spoken]


def test_openai_speech_does_not_synthesize_progress_or_rich_trace_fragments(monkeypatch):
    monkeypatch.setattr(app, "_require_openai_auth", lambda request: None)
    synthesized = []

    async def fake_synthesize_pocket(text):
        synthesized.append(text)
        return b"RIFF"

    monkeypatch.setattr(app, "synthesize_pocket", fake_synthesize_pocket)
    fragments = [
        "**Working**\n- Reading CBC News…\n\n---\n\n",
        "<!-- home-ai-display-trace -->\n---\n**Research activity**\n- Opened CBC News",
        "<!-- home-ai-display-trace -->\n---\n**Sources**\n- CBC News",
    ]
    for fragment in fragments:
        response = asyncio.run(app.openai_speech(_SpeechRequest({"input": fragment})))
        assert response.status_code == 204
    assert synthesized == []


def main() -> None:
    with tempfile.TemporaryDirectory() as cache:
        started = time.perf_counter()
        app.speech_normalizer = Normalizer(
            input_case="cased",
            lang="en",
            cache_dir=cache,
            overwrite_cache=False,
            post_process=True,
        )
        init_seconds = time.perf_counter() - started
        app.pronunciation_entries = app.load_pronunciation_lexicon()
        cases = {
            "You have 1,575 movies and 69 GB free.": "one thousand five hundred and seventy five movies and sixty nine gigabytes free.",
            "There are 346 TV shows, 13 TB remaining, and 85% used.": "three hundred and forty six TV shows, thirteen terabytes remaining, and eighty five percent used.",
            "It is 22°C and the download is 3.5 GB.": "twenty two degrees Celsius and the download is three point five gigabytes.",
            "September 13, 2026": "september thirteenth, twenty twenty six",
            "Lidarr and qBittorrent are healthy.": "lid arr and Q Bittorrent are healthy.",
        }
        for original, expected_fragment in cases.items():
            _, normalized, adjusted = app.normalize_for_speech(original)
            assert expected_fragment in adjusted, (original, normalized, adjusted)
            assert original != adjusted
        print(f"normalization tests passed; init_seconds={init_seconds:.3f}")


if __name__ == "__main__":
    main()
