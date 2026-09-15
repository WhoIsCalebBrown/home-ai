"""Model-facing semantic routing helpers.

This module deliberately contains no capability-specific vocabulary.  It only
decides how to present the current turn and structured referents to capability
retrieval.  Safety, authorization, canonical identity, and write policy remain
server-side invariants.
"""

from __future__ import annotations

import re
from typing import Any


REFERENTIAL_WORDS = frozenset(
    {"it", "that", "this", "there", "them", "they", "he", "she", "those", "one"}
)


# This is a capability-policy boundary, not a natural-language vocabulary.
# Once the current turn has been resolved to a structured domain, unrelated
# capabilities must not be offered to the small model as competing choices.
_CAPABILITY_GROUP_ALIASES = {
    "weather": frozenset({"weather"}),
    "server": frozenset({"server", "docker", "system"}),
    "internet": frozenset({"internet", "web", "knowledge"}),
    "cameras": frozenset({"cameras", "frigate"}),
    "media": frozenset({"media", "plex", "movies", "tv", "music", "downloads", "requests"}),
}


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def has_referential_language(text: str) -> bool:
    return bool(_tokens(text) & REFERENTIAL_WORDS)


def discovery_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """Return only structured context useful for resolving a referent.

    In particular, domain/group/previous-tools are intentionally omitted: a
    previous tool is evidence about the past, not an instruction for the new
    turn.
    """
    source = context or {}
    referents: list[str] = []
    for key in (
        "canonical_identity",
        "latest_resolved_referent",
        "latest_media_workflow",
        "latest_media_status",
        "latest_event",
        "query",
        "topic",
        "unresolved_request",
        "location",
        "camera",
        "subject",
    ):
        value = source.get(key)
        if value:
            referents.append(str(value))
    return {
        "referents": referents,
        "unresolved_topic": source.get("unresolved_request") or source.get("topic"),
        "latest_event_id": source.get("latest_event_id"),
        "canonical_identity": source.get("canonical_identity"),
        "latest_media_workflow": source.get("latest_media_workflow"),
    }


def semantic_query(text: str, context: dict[str, Any] | None = None) -> str:
    """Build a retrieval query without rewriting the user's intent.

    A referent is appended only when the new utterance is elliptical.  The
    prior domain is never appended and never substitutes for the new request.
    """
    source = context or {}
    if not has_referential_language(text):
        return text
    referent = source.get("latest_resolved_referent")
    if not referent:
        referent = source.get("canonical_identity") or source.get("latest_media_workflow")
    if not referent:
        referent = source.get("unresolved_request") or source.get("topic")
    if not referent:
        return text
    return f"{text} [referent: {referent}]"


def retrieval_confidence(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded confidence summary for audit and clarification logic."""
    if not candidates:
        return {"status": "NO_CANDIDATES", "top": None, "margin": None}
    top = candidates[0].get("metadata", {})
    second = candidates[1].get("metadata", {}) if len(candidates) > 1 else {}
    top_score = float(top.get("score", 0) or 0)
    second_score = float(second.get("score", 0) or 0)
    return {
        "status": "RETRIEVED",
        "top": top.get("canonical_name"),
        "top_score": top_score,
        "margin": top_score - second_score if len(candidates) > 1 else top_score,
        "candidate_count": len(candidates),
    }


def semantic_preflight_allowed(tool_name: str) -> bool:
    """Only deterministic, non-language safety helpers may bypass Qwen.

    Confirmation continuation and backend authorization are handled elsewhere;
    semantic domain/tool selection must go through retrieved schemas and the
    model tool loop.
    """
    return tool_name in {"calculator", "unit_convert"}


def narrow_capability_entries(
    entries: list[dict[str, Any]],
    context: dict[str, Any] | None,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Keep model-facing retrieval inside the current explicit capability group.

    Semantic retrieval ranks candidates, but a small model should not have to
    choose between weather, web, and Docker tools for one explicit weather
    question.  The group is produced by the current-turn resolver; previous
    domain/tool state is never consulted here.  If no structured group exists,
    the retriever's ranking remains authoritative.
    """
    bounded = entries[:max_results]
    group = (context or {}).get("group")
    allowed = _CAPABILITY_GROUP_ALIASES.get(str(group), frozenset())
    if not allowed:
        return bounded
    matching = [
        entry for entry in entries
        if str(entry.get("metadata", {}).get("group", "")).casefold() in allowed
    ]
    return (matching or bounded)[:max_results]
