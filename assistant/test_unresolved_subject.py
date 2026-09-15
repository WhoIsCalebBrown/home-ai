"""Unit tests for UnresolvedSubject (subject_model.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from subject_model import UnresolvedSubject, unresolved_subject_from_dict


def test_new_creates_with_hints():
    subject = UnresolvedSubject.new("media", "The Room", media_type="movie", year=None)
    assert subject.title_or_name == "The Room"
    assert subject.hints["media_type"] == "movie"
    assert subject.failed_resolution_attempts == 0


def test_enrich_adds_new_hint_without_losing_existing():
    subject = UnresolvedSubject.new("media", "The Room", media_type="movie")
    enriched = subject.enrich(year=2003)
    assert enriched.hints == {"media_type": "movie", "year": 2003}
    assert enriched.title_or_name == "The Room"


def test_enrich_never_overwrites_with_empty_value():
    subject = UnresolvedSubject.new("media", "The Room", media_type="movie", year=2003)
    enriched = subject.enrich(year=None, media_type="")
    assert enriched.hints == {"media_type": "movie", "year": 2003}


def test_with_failed_attempt_increments_counter_and_preserves_hints():
    subject = UnresolvedSubject.new("media", "The Room", media_type="movie")
    failed_once = subject.with_failed_attempt()
    assert failed_once.failed_resolution_attempts == 1
    assert failed_once.hints == {"media_type": "movie"}
    failed_twice = failed_once.with_failed_attempt()
    assert failed_twice.failed_resolution_attempts == 2


def test_resolution_goal_text_includes_title_year_and_type():
    subject = UnresolvedSubject.new("media", "The Room", media_type="movie", year=2003)
    assert subject.resolution_goal_text() == "The Room 2003 movie"


def test_resolution_goal_text_without_year():
    subject = UnresolvedSubject.new("media", "The Room", media_type="movie")
    assert subject.resolution_goal_text() == "The Room movie"


def test_round_trip_through_dict():
    subject = UnresolvedSubject.new("media", "The Room", media_type="movie", year=2003)
    restored = unresolved_subject_from_dict(subject.to_dict())
    assert restored == subject


def test_from_dict_of_none_is_none():
    assert unresolved_subject_from_dict(None) is None
    assert unresolved_subject_from_dict({}) is None
