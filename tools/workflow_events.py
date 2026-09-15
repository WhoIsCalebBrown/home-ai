"""Durable append-only workflow event timeline.

Adapted from HookReel's download_events table (app/database.py), generalized
beyond movies/episodes to any workflow_id and typed with a closed event
vocabulary shared conceptually with the audit log (see EVENT_TYPES below and
audit()/safe_args() in server-tools-app.py).

This module is additive, not authoritative:

  - The JSON workflow store (_media_workflows/_save_media_workflows,
    MEDIA_WORKFLOWS_PATH) remains the current-state source of truth, itself
    always re-verified against live provider/Plex evidence by media_status
    and media_diagnose before being shown to a user (see those functions'
    own docstrings: "The persisted workflow is correlation/history, not
    proof of current state").
  - This module's events are for history, debugging, explanation, audit, and
    timeline reconstruction ONLY -- "what happened with that movie", "did it
    ever find a candidate", "how long was it downloading". No function here
    is consulted by media_status or media_diagnose to answer "what is true
    right now"; if live evidence and this event history ever disagree, live
    evidence wins, unconditionally. That invariant is enforced by the simple
    fact that get_events() is a pure read with no current-state semantics,
    and by tools/test_workflow_events.py::test_current_state_wins_over_history.

Every write here is best-effort: a logging failure must never break the
request path it is describing. Callers should wrap log_event() in try/except
(or use the provided log_event_safe helper) exactly the way audit() already
swallows its own write errors.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

WORKFLOW_EVENTS_DB = Path(os.getenv("WORKFLOW_EVENTS_DB", "/data/workflow-events.sqlite3"))

# Closed vocabulary. Not every pipeline emits every event type -- movies/TV
# use the acquisition-shaped events, music pipelines currently emit a
# smaller subset (IDENTIFIED/RESOLVED/AVAILABILITY_CHECKED/PLAN_CREATED/
# CONFIRMATION_*) since Lidarr writes remain disabled
# (home-ai-system-contract.json writes_enabled: false for music).
EVENT_TYPES = frozenset({
    "IDENTIFIED", "RESOLVED", "AVAILABILITY_CHECKED",
    "OFFER_PRESENTED", "OFFER_ACCEPTED",
    "PLAN_CREATED", "CONFIRMATION_REQUESTED", "CONFIRMATION_CONSUMED",
    "REQUEST_SUBMITTED", "SEARCHING", "CANDIDATE_FOUND", "QUEUED", "ACQUIRING",
    "REMOTE_READY", "MOUNT_VISIBLE", "COLLECTED", "IMPORT_PENDING", "IMPORTED",
    "PLEX_SCAN_PENDING", "PLEX_MATCH_PENDING", "AVAILABLE", "FAILED", "BLOCKED",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_events (
    event_id            TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL,
    session_id          TEXT,
    canonical_subject_id TEXT,
    event_type          TEXT NOT NULL,
    event_detail        TEXT,
    source_service       TEXT,
    backend_object_id    TEXT,
    timestamp            REAL NOT NULL,
    trace_id             TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow_id ON workflow_events(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_events_timestamp ON workflow_events(timestamp);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or WORKFLOW_EVENTS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path | None = None) -> None:
    connection = _connect(db_path)
    try:
        connection.executescript(_SCHEMA)
        connection.commit()
    finally:
        connection.close()


def log_event(
    *,
    workflow_id: str,
    event_type: str,
    session_id: str | None = None,
    canonical_subject_id: str | None = None,
    event_detail: str | None = None,
    source_service: str | None = None,
    backend_object_id: str | None = None,
    trace_id: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Insert one lifecycle event. Raises on a closed-vocabulary violation --
    callers should still wrap this in try/except at the call site to keep a
    logging failure from ever affecting the request it's describing, but an
    unknown event_type is a programming error worth surfacing in tests
    rather than silently swallowing."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown workflow event_type: {event_type!r}")
    init_db(db_path)
    connection = _connect(db_path)
    event_id = str(uuid.uuid4())
    try:
        connection.execute(
            "INSERT INTO workflow_events (event_id, workflow_id, session_id, canonical_subject_id,"
            " event_type, event_detail, source_service, backend_object_id, timestamp, trace_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, workflow_id, session_id, canonical_subject_id, event_type,
             event_detail, source_service, backend_object_id, time.time(), trace_id),
        )
        connection.commit()
    finally:
        connection.close()
    return event_id


def log_event_safe(**kwargs) -> str | None:
    """Best-effort wrapper -- use this at production call sites."""
    try:
        return log_event(**kwargs)
    except Exception:
        return None


def get_events(workflow_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY timestamp ASC",
            (workflow_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def get_events_for_subject(canonical_subject_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM workflow_events WHERE canonical_subject_id = ? ORDER BY timestamp ASC",
            (canonical_subject_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]
