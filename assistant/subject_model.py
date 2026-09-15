"""Cross-capability subject/offer model.

Separates the concepts the routing code previously conflated into
active_domain / last_tool:

  1. HOW WE LEARNED ABOUT SOMETHING  -> ResolvedSubject.discovery_source
  2. WHAT THE THING IS               -> ResolvedSubject.canonical_identity
  3. WHAT THE USER WANTS NOW         -> classify_offer_reply() / the new turn's own intent
  4. WHAT ACTIONS ARE AVAILABLE NOW  -> available_actions()
  5. WHICH BACKEND IMPLEMENTS IT     -> Action.tool_name (semantic capability, not a backend detail)

This module contains no capability-specific vocabulary beyond the media
lifecycle it maps to available actions, matching semantic_routing.py's own
"no capability-specific vocabulary" discipline for anything not media-shaped.

CanonicalIdentity here mirrors tools/canonical_identity.py field-for-field.
The two modules are intentionally duplicated rather than shared, because
Home-AI-Assistant and Home-AI-Tools are separate images with separate
Dockerfiles/dependency trees (see docs/hardening-run-20260915.md on
first-party image provenance) -- a cross-image Python import would couple
their build/deploy lifecycles for a handful of dataclasses. Field-parity
between the two copies is enforced by
tools/test_canonical_identity.py::test_field_parity_with_assistant_mirror.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

CANONICAL_IDENTITY_FIELDS = (
    "media_type",
    "title",
    "year",
    "tmdb_id",
    "tvdb_id",
    "imdb_id",
    "musicbrainz_artist_id",
    "musicbrainz_release_group_id",
    "musicbrainz_release_id",
    "aliases",
    "artist",
    "foreign_album_id",
    "album_type",
    "series_type",
    "genres",
)

_EMPTY = (None, "", [])


@dataclass
class CanonicalIdentity:
    media_type: str | None = None
    title: str | None = None
    year: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None
    imdb_id: str | None = None
    musicbrainz_artist_id: str | None = None
    musicbrainz_release_group_id: str | None = None
    musicbrainz_release_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    artist: str | None = None
    foreign_album_id: str | None = None
    album_type: str | None = None
    series_type: str | None = None
    genres: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value not in _EMPTY}

    def is_empty(self) -> bool:
        return not self.to_dict()

    def merge(self, other: "CanonicalIdentity") -> "CanonicalIdentity":
        merged = dict(asdict(self))
        for key, value in asdict(other).items():
            if value in _EMPTY:
                continue
            existing = merged.get(key)
            if isinstance(existing, list) or isinstance(value, list):
                existing_list = existing if isinstance(existing, list) else []
                value_list = value if isinstance(value, list) else []
                merged[key] = sorted(set(existing_list) | set(value_list))
            elif existing in _EMPTY:
                merged[key] = value
        return CanonicalIdentity(**merged)


def build_canonical_identity(**kwargs) -> CanonicalIdentity:
    known = {key: value for key, value in kwargs.items() if key in CANONICAL_IDENTITY_FIELDS and value not in _EMPTY}
    return CanonicalIdentity(**known)


SubjectType = Literal["media", "web_topic", "frigate_event", "container", "person", "place"]
Confidence = Literal["low", "medium", "high"]


@dataclass
class ResolvedSubject:
    """WHAT WE ARE TALKING ABOUT -- kept independent of WHAT THE USER WANTS NOW.

    discovery_source records how the subject was first learned about (plex,
    web_search, media_resolve, frigate, ...) for audit/explanation purposes
    only. It never gates which capability can act on the subject next --
    "the discovery source does not own the subject" (user spec, section 1).
    """

    subject_id: str
    subject_type: SubjectType
    display_name: str
    canonical_identity: CanonicalIdentity = field(default_factory=CanonicalIdentity)
    confidence: Confidence = "low"
    discovery_source: str = ""
    attributes: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @staticmethod
    def new(
        subject_type: SubjectType,
        display_name: str,
        *,
        canonical_identity: CanonicalIdentity | None = None,
        confidence: Confidence = "low",
        discovery_source: str = "",
        attributes: dict | None = None,
    ) -> "ResolvedSubject":
        return ResolvedSubject(
            subject_id=str(uuid.uuid4()),
            subject_type=subject_type,
            display_name=display_name,
            canonical_identity=canonical_identity or CanonicalIdentity(),
            confidence=confidence,
            discovery_source=discovery_source,
            attributes=attributes or {},
        )

    def enrich(self, other: "ResolvedSubject") -> "ResolvedSubject":
        """Enrichment, not replacement: same subject_id, richer identity.

        Confidence only ever moves up on enrichment (a media_resolve call
        confirming a web-discovered title should not make the assistant less
        sure what it is talking about). discovery_source is left as the
        *original* source -- "how we learned about it" is history, not a
        moving pointer to whichever tool ran most recently.
        """
        order = {"low": 0, "medium": 1, "high": 2}
        confidence = self.confidence if order[self.confidence] >= order[other.confidence] else other.confidence
        attributes = {**self.attributes, **other.attributes}
        return ResolvedSubject(
            subject_id=self.subject_id,
            subject_type=self.subject_type,
            display_name=other.display_name or self.display_name,
            canonical_identity=self.canonical_identity.merge(other.canonical_identity),
            confidence=confidence,
            discovery_source=self.discovery_source or other.discovery_source,
            attributes=attributes,
            created_at=self.created_at,
            updated_at=time.time(),
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["canonical_identity"] = self.canonical_identity.to_dict()
        return payload


def disambiguate_subjects(candidates: list[ResolvedSubject]) -> ResolvedSubject | list[ResolvedSubject]:
    """Return a single subject if candidates agree, else the list to clarify.

    "Agreement" means every non-empty canonical identity field that more than
    one candidate carries has the same value across all of them -- e.g. two
    candidates both named "Dune" but with different `year`/`tmdb_id` are NOT
    the same subject and must not be silently collapsed into one (user spec
    section 30). A single candidate, or multiple candidates with identical
    canonical identity (same tmdb_id, etc.), resolve directly.
    """
    if not candidates:
        raise ValueError("disambiguate_subjects requires at least one candidate")
    if len(candidates) == 1:
        return candidates[0]
    identities = [c.canonical_identity.to_dict() for c in candidates]
    keys = {"tmdb_id", "tvdb_id", "imdb_id", "foreign_album_id", "year"}
    conflict = False
    for key in keys:
        values = {identity[key] for identity in identities if key in identity}
        if len(values) > 1:
            conflict = True
            break
    if conflict:
        return list(candidates)
    best = max(candidates, key=lambda c: {"low": 0, "medium": 1, "high": 2}[c.confidence])
    return best


_ACCEPT_PHRASES = frozenset({
    "yes", "yeah", "yea", "yep", "yup", "sure", "go ahead", "do it", "check it",
    "please", "okay", "ok", "why not", "sounds good", "please do", "go for it",
    # Parity with is_confirmation()'s strict grammar in voice-api-app.py,
    # which already accepts these for PENDING_CONFIRMATION -- an offer
    # reply classifier that disagreed with the confirmation classifier on
    # ordinary acceptance language ("get it", "request it") would silently
    # fall through as "ambiguous" and strand the offer. Found by
    # qa/test_assistant_conversation_integration.py's music scenario.
    "get it", "request it", "add it",
})
_DECLINE_PHRASES = frozenset({
    "no", "nope", "not now", "never mind", "nevermind", "hold on", "don't", "do not",
})
# A bare topic-switch/question word must never be read as acceptance, even
# though it superficially follows an offer turn.
_AMBIGUOUS_MARKERS = ("maybe", "wait", "what will", "what would", "actually", "hmm", "?")


def classify_offer_reply(text: str) -> Literal["accept", "decline", "ambiguous"]:
    """Classify a reply to a PendingOffer. Never returns "accept" for topic-switch text.

    An "actually, <new request>" reply must decline the offer rather than
    accept it, even though it starts near an acceptance-shaped word, because
    the newest explicit request always outranks a stale offer (user spec
    sections 17, 29). Callers are expected to check for a competing explicit
    new intent *before* consulting this classifier's "accept" result, since
    detecting a brand-new domain/intent is not this module's vocabulary to
    own (kept out of subject_model.py deliberately, same discipline as
    semantic_routing.py).
    """
    normalized = text.strip().casefold().rstrip(".!")
    if not normalized:
        return "ambiguous"
    for marker in _AMBIGUOUS_MARKERS:
        if marker in normalized:
            return "ambiguous"
    if normalized in _DECLINE_PHRASES or any(normalized.startswith(p + " ") for p in _DECLINE_PHRASES):
        return "decline"
    if normalized in _ACCEPT_PHRASES:
        return "accept"
    for phrase in _ACCEPT_PHRASES:
        if normalized == phrase or normalized.startswith(phrase + ",") or normalized.startswith(phrase + " "):
            # Still guard against "sure, but what's the weather" style turns.
            remainder = normalized[len(phrase):].lstrip(", ")
            if remainder and any(marker in remainder for marker in _AMBIGUOUS_MARKERS + ("what's", "what is")):
                return "ambiguous"
            return "accept"
    return "ambiguous"


@dataclass(frozen=True)
class PendingOffer:
    """A lightweight, read-only conversational continuation.

    Structural write-safety guarantee: `side_effect` is validated to be the
    literal string "read" in __post_init__, and this dataclass carries no
    plan_hash, args_hash, session-binding-to-write, or single-use-consumption
    field of the kind PENDING_CONFIRMATION requires (see
    tools/server-tools-app.py media_confirmation_record /
    validate_media_confirmation). There is no method on this class, and no
    function in this module, that turns a PendingOffer into authorization for
    a write -- accepting one only re-invokes the *same already-vetted
    read-only tool call* that produced the offer. Any future attempt to add a
    write-capable operation to a PendingOffer must change this literal type
    and will be caught by test_pending_offer_cannot_authorize_write.
    """

    offer_id: str
    session_id: str
    subject_ref: str
    operation: str
    created_at: float
    expires_at: float
    side_effect: Literal["read"] = "read"

    def __post_init__(self) -> None:
        if self.side_effect != "read":
            raise ValueError("PendingOffer.side_effect must be 'read' -- offers never authorize writes")

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    @staticmethod
    def create(session_id: str, subject_ref: str, operation: str, ttl_seconds: float = 90) -> "PendingOffer":
        created = time.time()
        return PendingOffer(
            offer_id=str(uuid.uuid4()),
            session_id=session_id,
            subject_ref=subject_ref,
            operation=operation,
            created_at=created,
            expires_at=created + ttl_seconds,
        )


# --- Available actions -----------------------------------------------------

@dataclass(frozen=True)
class Action:
    name: str
    tool_name: str
    side_effect: Literal["read", "write"]
    description: str


_MEDIA_ACTIONS = {
    "CHECK_LIBRARY": Action("CHECK_LIBRARY", "plex_match_canonical_media", "read", "check whether it's already in Plex"),
    "CHECK_MEDIA_STATUS": Action("CHECK_MEDIA_STATUS", "media_status", "read", "check its current status"),
    "GET_MEDIA_DETAILS": Action("GET_MEDIA_DETAILS", "media_resolve", "read", "get more details about it"),
    "WEB_RESEARCH": Action("WEB_RESEARCH", "web_search", "read", "look it up online"),
    "PLAN_MEDIA_REQUEST": Action("PLAN_MEDIA_REQUEST", "media_plan_goal", "read", "prepare a request for it"),
    "DIAGNOSE_MEDIA": Action("DIAGNOSE_MEDIA", "media_diagnose", "read", "look into what happened with it"),
    "CHECK_PROVIDER_STATUS": Action("CHECK_PROVIDER_STATUS", "media_status", "read", "check its delivery/provider status"),
    "REQUEST_MEDIA": Action("REQUEST_MEDIA", "media_standard_request", "write", "actually request it"),
}

# Maps a media lifecycle state (see MEDIA_LIFECYCLE in
# tools/server-tools-app.py) to the actions that are genuinely valid from
# that state. This is the deterministic layer the user's spec requires so
# Qwen can phrase a suggestion naturally but can never invent an affordance
# that isn't returned here (spec sections 5, 28, 44).
_STATE_ACTIONS: dict[str, tuple[str, ...]] = {
    "UNKNOWN": ("GET_MEDIA_DETAILS", "WEB_RESEARCH"),
    "IDENTIFIED": ("CHECK_LIBRARY", "PLAN_MEDIA_REQUEST"),
    "ALREADY_AVAILABLE": ("CHECK_LIBRARY",),
    "AVAILABLE_IN_PLEX": ("GET_MEDIA_DETAILS",),
    "AVAILABLE": ("GET_MEDIA_DETAILS",),
    "WANTED": ("CHECK_MEDIA_STATUS",),
    "REQUESTED": ("CHECK_MEDIA_STATUS",),
    "SEARCHING": ("CHECK_MEDIA_STATUS",),
    "CANDIDATE_FOUND": ("CHECK_MEDIA_STATUS",),
    "QUEUED": ("CHECK_MEDIA_STATUS",),
    "ACQUIRING": ("CHECK_MEDIA_STATUS",),
    "DOWNLOADED": ("CHECK_MEDIA_STATUS",),
    "PENDING_IMPORT": ("CHECK_MEDIA_STATUS", "CHECK_PROVIDER_STATUS"),
    "IMPORTED": ("CHECK_PROVIDER_STATUS",),
    "ENRICHING": ("CHECK_MEDIA_STATUS",),
    "ACQUIRED_NOT_VISIBLE": ("CHECK_PROVIDER_STATUS", "DIAGNOSE_MEDIA"),
    "NO_CANDIDATE": ("DIAGNOSE_MEDIA",),
    "FAILED": ("DIAGNOSE_MEDIA",),
    "FAILED_INGESTION": ("DIAGNOSE_MEDIA",),
    "PARTIAL_STATUS": ("DIAGNOSE_MEDIA", "CHECK_MEDIA_STATUS"),
    "BACKEND_UNAVAILABLE": ("DIAGNOSE_MEDIA",),
    "BLOCKED": ("DIAGNOSE_MEDIA",),
    "NOT_FOUND": ("WEB_RESEARCH",),
    "AMBIGUOUS_IDENTITY": ("GET_MEDIA_DETAILS",),
    "ABSENT": ("PLAN_MEDIA_REQUEST",),
}

# Priority order used by next_best_action -- earlier entries win when more
# than one action is valid from the current state.
_NEXT_ACTION_PRIORITY = (
    "REQUEST_MEDIA", "PLAN_MEDIA_REQUEST", "CHECK_MEDIA_STATUS", "DIAGNOSE_MEDIA",
    "CHECK_PROVIDER_STATUS", "CHECK_LIBRARY", "GET_MEDIA_DETAILS", "WEB_RESEARCH",
)


def available_actions(subject: ResolvedSubject, state: str) -> list[Action]:
    """The only actions Qwen may claim are possible for this subject right now.

    `state` is a MEDIA_LIFECYCLE value already computed server-side by
    media_status/media_plan_goal from live evidence -- this function does not
    call out to any backend itself, it only maps a known state to the
    actions valid from it. Non-media subject types currently return no
    actions (the media lifecycle is the only closed state machine this
    module knows about today); extending this to Frigate/container subjects
    is future work, not something this function should guess at.
    """
    if subject.subject_type != "media":
        return []
    names = _STATE_ACTIONS.get(state, ())
    return [_MEDIA_ACTIONS[name] for name in names if name in _MEDIA_ACTIONS]


def next_best_action(actions: list[Action]) -> Action | None:
    """Surface ONE contextually useful suggestion instead of a menu (spec section 6)."""
    if not actions:
        return None
    by_name = {a.name: a for a in actions}
    for name in _NEXT_ACTION_PRIORITY:
        if name in by_name:
            return by_name[name]
    return actions[0]
