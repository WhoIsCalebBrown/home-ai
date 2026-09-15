"""Unit tests for assistant/subject_model.py: ResolvedSubject, CanonicalIdentity,
PendingOffer, available_actions, and offer-reply classification."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from subject_model import (
    Action,
    CanonicalIdentity,
    PendingOffer,
    ResolvedSubject,
    available_actions,
    build_canonical_identity,
    classify_offer_reply,
    disambiguate_subjects,
    next_best_action,
)


# --- CanonicalIdentity / ResolvedSubject enrichment -------------------------

def test_resolved_subject_enrichment_keeps_same_subject_id():
    web_found = ResolvedSubject.new(
        "media", "Segua",
        canonical_identity=build_canonical_identity(media_type="tv", title="Segua"),
        confidence="low", discovery_source="web_search",
    )
    media_resolved = ResolvedSubject.new(
        "media", "Segua",
        canonical_identity=build_canonical_identity(media_type="tv", title="Segua", tvdb_id="999"),
        confidence="high", discovery_source="media_resolve",
    )
    enriched = web_found.enrich(media_resolved)
    assert enriched.subject_id == web_found.subject_id  # not a new subject
    assert enriched.canonical_identity.tvdb_id == "999"
    assert enriched.confidence == "high"  # never regresses on enrichment
    assert enriched.discovery_source == "web_search"  # history, not overwritten


def test_enrichment_does_not_restart_understanding_from_scratch():
    subject = ResolvedSubject.new("media", "Cowboy Bebop", confidence="medium", attributes={"kind": "anime"})
    more = ResolvedSubject.new("media", "Cowboy Bebop", confidence="high", attributes={"episode_count": 26})
    enriched = subject.enrich(more)
    assert enriched.attributes == {"kind": "anime", "episode_count": 26}


# --- Disambiguation (spec section 30) --------------------------------------

def test_single_candidate_resolves_directly():
    subject = ResolvedSubject.new("media", "Dune")
    assert disambiguate_subjects([subject]) is subject


def test_conflicting_candidates_require_clarification():
    dune_1984 = ResolvedSubject.new("media", "Dune", canonical_identity=build_canonical_identity(year="1984", tmdb_id="1"))
    dune_2021 = ResolvedSubject.new("media", "Dune", canonical_identity=build_canonical_identity(year="2021", tmdb_id="2"))
    result = disambiguate_subjects([dune_1984, dune_2021])
    assert isinstance(result, list)
    assert len(result) == 2


def test_agreeing_candidates_collapse_to_the_higher_confidence_one():
    low = ResolvedSubject.new("media", "Segua", canonical_identity=build_canonical_identity(tmdb_id="42"), confidence="low")
    high = ResolvedSubject.new("media", "Segua", canonical_identity=build_canonical_identity(tmdb_id="42"), confidence="high")
    result = disambiguate_subjects([low, high])
    assert isinstance(result, ResolvedSubject)
    assert result is high


def test_disambiguate_requires_at_least_one_candidate():
    with pytest.raises(ValueError):
        disambiguate_subjects([])


# --- Offer reply classification (spec section 31) --------------------------

@pytest.mark.parametrize("text", [
    "yes", "yeah", "yep", "sure", "go ahead", "do it", "check it", "please",
    "okay", "ok", "why not", "sure.", "yeah!",
])
def test_accept_language(text):
    assert classify_offer_reply(text) == "accept"


@pytest.mark.parametrize("text", [
    "no", "nope", "not now", "never mind", "hold on", "don't",
])
def test_decline_language(text):
    assert classify_offer_reply(text) == "decline"


@pytest.mark.parametrize("text", [
    "maybe", "wait", "what will it do?", "actually what's the weather?",
    "actually what is the weather", "hmm",
])
def test_ambiguous_or_topic_switch_language(text):
    assert classify_offer_reply(text) == "ambiguous"


def test_accept_prefix_with_topic_switch_is_not_accepted():
    """'yeah, but what's the weather tomorrow?' must not be read as acceptance."""
    assert classify_offer_reply("yeah, but what's the weather tomorrow?") == "ambiguous"


def test_empty_reply_is_ambiguous():
    assert classify_offer_reply("") == "ambiguous"
    assert classify_offer_reply("   ") == "ambiguous"


# --- PendingOffer structural write-safety (spec sections 7, 8) -------------

def test_pending_offer_cannot_authorize_write():
    with pytest.raises(ValueError):
        PendingOffer(
            offer_id="o1", session_id="s1", subject_ref="subj-1", operation="media_standard_request",
            created_at=time.time(), expires_at=time.time() + 60, side_effect="write",  # type: ignore[arg-type]
        )


def test_pending_offer_has_no_write_authorization_fields():
    offer = PendingOffer.create(session_id="s1", subject_ref="subj-1", operation="plex_match_canonical_media")
    field_names = set(offer.__dataclass_fields__.keys())
    # PENDING_CONFIRMATION binds on these; a PendingOffer structurally cannot.
    forbidden = {"plan_version_hash", "arguments_hash", "confirmation_id", "canonical_identity"}
    assert not (field_names & forbidden)
    assert offer.side_effect == "read"


def test_pending_offer_is_immutable():
    offer = PendingOffer.create(session_id="s1", subject_ref="subj-1", operation="media_status")
    with pytest.raises(Exception):
        offer.side_effect = "write"  # type: ignore[misc]


def test_pending_offer_expiry():
    offer = PendingOffer.create(session_id="s1", subject_ref="subj-1", operation="media_status", ttl_seconds=1)
    assert offer.is_expired(now=offer.created_at) is False
    assert offer.is_expired(now=offer.created_at + 2) is True


# --- available_actions / next_best_action (spec sections 5, 6, 28, 44) -----

@pytest.mark.parametrize("state,expected_names", [
    ("AVAILABLE_IN_PLEX", {"GET_MEDIA_DETAILS"}),
    ("ABSENT", {"PLAN_MEDIA_REQUEST"}),
    ("SEARCHING", {"CHECK_MEDIA_STATUS"}),
    ("ACQUIRING", {"CHECK_MEDIA_STATUS"}),
    ("NO_CANDIDATE", {"DIAGNOSE_MEDIA"}),
    ("FAILED", {"DIAGNOSE_MEDIA"}),
    ("IMPORTED", {"CHECK_PROVIDER_STATUS"}),
])
def test_available_actions_matches_expected_state_mapping(state, expected_names):
    subject = ResolvedSubject.new("media", "Segua")
    actions = available_actions(subject, state)
    assert {a.name for a in actions} == expected_names


def test_available_state_never_offers_request():
    """AVAILABLE must never surface PLAN_MEDIA_REQUEST/REQUEST_MEDIA (spec section 28)."""
    subject = ResolvedSubject.new("media", "Segua")
    actions = available_actions(subject, "AVAILABLE_IN_PLEX")
    assert not any(a.name in {"PLAN_MEDIA_REQUEST", "REQUEST_MEDIA"} for a in actions)


def test_searching_never_offers_duplicate_request():
    subject = ResolvedSubject.new("media", "Segua")
    actions = available_actions(subject, "SEARCHING")
    assert not any(a.name in {"PLAN_MEDIA_REQUEST", "REQUEST_MEDIA"} for a in actions)


def test_non_media_subject_has_no_available_actions_yet():
    subject = ResolvedSubject.new("web_topic", "some article")
    assert available_actions(subject, "IDENTIFIED") == []


def test_next_best_action_prefers_request_over_status():
    subject = ResolvedSubject.new("media", "Segua")
    actions = available_actions(subject, "IDENTIFIED")
    best = next_best_action(actions)
    assert best is not None
    assert best.name in {"CHECK_LIBRARY", "PLAN_MEDIA_REQUEST"}
    # exactly one action surfaced, never the whole set as a menu
    assert isinstance(best, Action)


def test_next_best_action_of_empty_list_is_none():
    assert next_best_action([]) is None


def test_unknown_state_yields_no_hallucinated_actions():
    subject = ResolvedSubject.new("media", "Segua")
    assert available_actions(subject, "SOME_STATE_THAT_DOES_NOT_EXIST") == []
