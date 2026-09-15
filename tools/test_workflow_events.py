"""Unit tests for tools/workflow_events.py -- no production I/O, uses a
temp-file SQLite DB per test via the db_path override."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import workflow_events as we


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "events.sqlite3"


def test_log_and_get_events_round_trip(db_path):
    we.log_event(workflow_id="wf-1", event_type="IDENTIFIED", canonical_subject_id="subj-1",
                 source_service="tools", db_path=db_path)
    we.log_event(workflow_id="wf-1", event_type="REQUEST_SUBMITTED", canonical_subject_id="subj-1",
                 source_service="cli_debrid", db_path=db_path)
    events = we.get_events("wf-1", db_path=db_path)
    assert [e["event_type"] for e in events] == ["IDENTIFIED", "REQUEST_SUBMITTED"]
    assert events[0]["canonical_subject_id"] == "subj-1"


def test_events_are_ordered_by_timestamp(db_path):
    for event_type in ("IDENTIFIED", "PLAN_CREATED", "CONFIRMATION_REQUESTED", "CONFIRMATION_CONSUMED"):
        we.log_event(workflow_id="wf-2", event_type=event_type, db_path=db_path)
    events = we.get_events("wf-2", db_path=db_path)
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


def test_unknown_event_type_is_rejected(db_path):
    with pytest.raises(ValueError):
        we.log_event(workflow_id="wf-3", event_type="MADE_UP_EVENT", db_path=db_path)


def test_log_event_safe_never_raises_on_bad_input(db_path):
    result = we.log_event_safe(workflow_id="wf-4", event_type="NOT_A_REAL_EVENT", db_path=db_path)
    assert result is None


def test_get_events_for_unknown_workflow_returns_empty(db_path):
    assert we.get_events("does-not-exist", db_path=db_path) == []


def test_get_events_for_subject_spans_workflows(db_path):
    we.log_event(workflow_id="wf-a", event_type="IDENTIFIED", canonical_subject_id="subj-x", db_path=db_path)
    we.log_event(workflow_id="wf-b", event_type="AVAILABILITY_CHECKED", canonical_subject_id="subj-x", db_path=db_path)
    events = we.get_events_for_subject("subj-x", db_path=db_path)
    assert {e["workflow_id"] for e in events} == {"wf-a", "wf-b"}


def test_current_state_wins_over_history(db_path):
    """This module has no function that reports 'current state' -- it is
    read-only history. Simulate a live-evidence-vs-history disagreement and
    confirm resolving it is entirely the caller's responsibility, not
    something this module could get wrong by construction."""
    we.log_event(workflow_id="wf-5", event_type="FAILED", event_detail="stale history says failed", db_path=db_path)
    live_evidence_state = "AVAILABLE"  # what a fresh Plex/provider check found "now"
    history = we.get_events("wf-5", db_path=db_path)
    assert history[-1]["event_type"] == "FAILED"
    # A caller must use live_evidence_state, not history, to answer "is it
    # available right now" -- this module provides no API that would let a
    # caller mistakenly treat history as current truth.
    assert not hasattr(we, "current_state")
    assert not hasattr(we, "get_current_state")
    resolved_for_user = live_evidence_state
    assert resolved_for_user == "AVAILABLE"
