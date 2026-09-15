"""Unit tests for tools/canonical_identity.py and field-parity with its
assistant-side mirror (assistant/subject_model.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "assistant"))

from canonical_identity import CanonicalIdentity, build_canonical_identity, from_dict, CANONICAL_IDENTITY_FIELDS


def test_only_populates_applicable_fields():
    identity = build_canonical_identity(media_type="movie", title="Dune", year="2021", tmdb_id="438631")
    payload = identity.to_dict()
    assert payload == {"media_type": "movie", "title": "Dune", "year": "2021", "tmdb_id": "438631"}
    assert "tvdb_id" not in payload
    assert "imdb_id" not in payload


def test_unknown_kwargs_are_dropped_not_errored():
    identity = build_canonical_identity(media_type="movie", not_a_real_field="ignored")
    assert identity.to_dict() == {"media_type": "movie"}


def test_empty_identity_round_trips():
    assert CanonicalIdentity().to_dict() == {}
    assert CanonicalIdentity().is_empty() is True


def test_from_dict_and_back_is_stable():
    original = {"media_type": "tv", "title": "Severance", "tvdb_id": "371980"}
    assert from_dict(original).to_dict() == original


def test_merge_enriches_without_overwriting_existing_ids():
    partial = build_canonical_identity(media_type="tv", title="Segua")
    enriched = build_canonical_identity(media_type="tv", title="Segua", tvdb_id="999", year="2019")
    result = partial.merge(enriched)
    assert result.to_dict() == {"media_type": "tv", "title": "Segua", "tvdb_id": "999", "year": "2019"}


def test_merge_never_replaces_an_established_canonical_id():
    established = build_canonical_identity(media_type="movie", title="Dune", tmdb_id="438631", year="2021")
    conflicting = build_canonical_identity(media_type="movie", title="Dune", tmdb_id="WRONG-ID")
    result = established.merge(conflicting)
    assert result.tmdb_id == "438631"


def test_merge_unions_list_fields():
    a = build_canonical_identity(aliases=["Dune Part Two"])
    b = build_canonical_identity(aliases=["Dune: Part Two"])
    result = a.merge(b)
    assert set(result.aliases) == {"Dune Part Two", "Dune: Part Two"}


def test_field_parity_with_assistant_mirror():
    """The two CanonicalIdentity copies (tools/, assistant/) must never drift."""
    from subject_model import CanonicalIdentity as AssistantCanonicalIdentity
    from subject_model import CANONICAL_IDENTITY_FIELDS as ASSISTANT_FIELDS

    tools_fields = {f.name for f in CanonicalIdentity.__dataclass_fields__.values()}
    assistant_fields = {f.name for f in AssistantCanonicalIdentity.__dataclass_fields__.values()}
    assert tools_fields == assistant_fields
    assert set(CANONICAL_IDENTITY_FIELDS) == set(ASSISTANT_FIELDS)
