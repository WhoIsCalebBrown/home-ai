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
