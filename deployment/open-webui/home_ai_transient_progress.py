"""
title: Home-AI Transient Progress
author: WhoIsCalebBrown
version: 1.0.0
description: Keep Home-AI progress visible while streaming, then remove display-only artifacts.
"""

import re


_TRACE_MARKER = "<!-- home-ai-display-trace -->"
_PROGRESS_HEADER = "**Working**\n"
_PROGRESS_SEPARATOR = "\n---\n\n"
_FIXED_PROGRESS_LABELS = {
    "Searching the web…",
    "Reading a source…",
    "Checking the forecast…",
    "Checking Plex…",
    "Checking your home…",
    "Working…",
    "Reading CBC…",
    "Reading Reuters…",
    "Reading BBC…",
}
_PUBLIC_HOST_LABEL = re.compile(
    r"Reading [a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+…"
)
_TRACE_MARKER_LINE = re.compile(
    r"(?m)^[ \t]*<!-- home-ai-display-trace -->[ \t]*(?:\r?\n|$)"
)


def _home_ai_model(value: object) -> bool:
    if isinstance(value, dict):
        value = value.get("id")
    return isinstance(value, str) and (value == "home-ai" or value.startswith("home-ai-"))


def _valid_progress_label(label: str) -> bool:
    return label in _FIXED_PROGRESS_LABELS or bool(_PUBLIC_HOST_LABEL.fullmatch(label))


def _strip_completed_progress(content: str) -> str:
    if not content.startswith(_PROGRESS_HEADER):
        return content
    separator = content.find(_PROGRESS_SEPARATOR, len(_PROGRESS_HEADER))
    if separator < 0:
        return content
    lines = [line for line in content[len(_PROGRESS_HEADER):separator].splitlines() if line]
    if not 1 <= len(lines) <= 4:
        return content
    labels = [line.removeprefix("- ") for line in lines]
    if any(not line.startswith("- ") for line in lines):
        return content
    if len(labels) != len(set(labels)) or any(not _valid_progress_label(label) for label in labels):
        return content
    return content[separator + len(_PROGRESS_SEPARATOR):]


def _strip_trace_marker(content: str) -> str:
    return _TRACE_MARKER_LINE.sub("", content, count=1)


def clean_completed_home_ai_content(content: object) -> object:
    if not isinstance(content, str):
        return content
    return _strip_trace_marker(_strip_completed_progress(content))


def _clean_structured_output(output: object) -> None:
    if not isinstance(output, list):
        return
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = clean_completed_home_ai_content(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                part["text"] = clean_completed_home_ai_content(part.get("text"))


class Filter:
    def stream(self, event: dict):
        """Prevent the private display/speech marker from flashing in the live stream."""
        if not isinstance(event, dict):
            return event
        for choice in event.get("choices", []):
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict) or not isinstance(delta.get("content"), str):
                continue
            delta["content"] = delta["content"].replace(_TRACE_MARKER + "\n", "", 1)
        return event

    def outlet(self, body: dict, __model__: object = None):
        """Replace the completed message with its answer and visible research trace only."""
        if not isinstance(body, dict) or not _home_ai_model(body.get("model") or __model__):
            return body
        messages = body.get("messages")
        if not isinstance(messages, list):
            return body
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            message["content"] = clean_completed_home_ai_content(message.get("content"))
            _clean_structured_output(message.get("output"))
            break
        return body
