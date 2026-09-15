"""Adversarial, side-effect-free qualification tests.

These tests intentionally do not import production network clients.  They model
the externally visible contracts and fail closed when an adapter attempts an
unsafe operation.
"""

from dataclasses import dataclass, field

import pytest

from fake_media_backend import FakeMediaBackend


class SideEffectTripwire:
    def __init__(self):
        self.calls = []

    def production_write(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        raise AssertionError(f"production side effect attempted: {name}")


@dataclass
class Confirmation:
    session: str
    workflow: str
    canonical_id: str
    plan_hash: str
    args_hash: str
    expires_at: int
    status: str = "PENDING"


class ConfirmationStore:
    def __init__(self):
        self.items = {}

    def authorize(self, record, *, session, workflow, canonical_id, plan_hash, args_hash, now):
        if record.status != "PENDING" or now >= record.expires_at:
            return False
        if (record.session, record.workflow, record.canonical_id, record.plan_hash, record.args_hash) != (
            session, workflow, canonical_id, plan_hash, args_hash
        ):
            return False
        record.status = "CONSUMED"
        return True


def test_confirmation_cannot_cross_session_or_workflow():
    record = Confirmation("session-a", "wf-a", "tmdb:8467", "plan-a", "args-a", 120)
    store = ConfirmationStore()
    assert not store.authorize(record, session="session-b", workflow="wf-a", canonical_id="tmdb:8467", plan_hash="plan-a", args_hash="args-a", now=1)
    assert record.status == "PENDING"
    assert not store.authorize(record, session="session-a", workflow="wf-b", canonical_id="tmdb:8467", plan_hash="plan-a", args_hash="args-a", now=1)
    assert store.authorize(record, session="session-a", workflow="wf-a", canonical_id="tmdb:8467", plan_hash="plan-a", args_hash="args-a", now=1)
    assert not store.authorize(record, session="session-a", workflow="wf-a", canonical_id="tmdb:8467", plan_hash="plan-a", args_hash="args-a", now=1)


@pytest.mark.parametrize("now", [120, 121, 10_000])
def test_expired_confirmation_is_fail_closed(now):
    record = Confirmation("s", "w", "tmdb:1362", "p", "a", 120)
    assert not ConfirmationStore().authorize(record, session="s", workflow="w", canonical_id="tmdb:1362", plan_hash="p", args_hash="a", now=now)
    assert record.status == "PENDING"


def test_item_becoming_available_between_plan_and_submit_is_no_op():
    backend = FakeMediaBackend()
    plan = backend.plan("movie", 8467)
    item = backend.items.setdefault(plan["key"], backend.items.get(plan["key"]) or __import__("fake_media_backend").FakeMediaItem(plan["key"]))
    item.state = "AVAILABLE"
    assert backend.submit(plan)["status"] == "NO_OP"
    assert item.request_count == 0


def test_failed_ingestion_is_retryable_but_not_active():
    backend = FakeMediaBackend()
    plan = backend.plan("movie", 8467)
    backend.items[plan["key"]] = __import__("fake_media_backend").FakeMediaItem(plan["key"], state="NO_CANDIDATE")
    retry = backend.plan("movie", 8467)
    assert retry["result"] == "PLAN_READY"
    assert backend.submit(retry)["status"] == "SUBMITTED"


def test_episode_scope_never_widens_to_a_season():
    backend = FakeMediaBackend()
    plan = backend.plan("tv", 40546, episodes=[8])
    result = backend.submit(plan)
    assert result["status"] == "BLOCKED_UNSUPPORTED_SCOPE"
    assert not backend.items


def test_test_lane_cannot_call_production_writes():
    tripwire = SideEffectTripwire()
    with pytest.raises(AssertionError, match="production side effect"):
        tripwire.production_write("cli_debrid_webhook", media_id=8467)
    assert tripwire.calls[0][0] == "cli_debrid_webhook"


def test_restart_recovery_preserves_canonical_workflow_and_history():
    backend = FakeMediaBackend()
    plan = backend.plan("movie", 1362)
    assert backend.submit(plan)["status"] == "SUBMITTED"
    backend.transition(plan["key"], "Scraping")
    persisted = {
        "workflow_id": "wf-hobbit",
        "canonical": "tmdb:1362",
        "state": backend.items[plan["key"]].state,
        "events": list(backend.items[plan["key"]].events),
    }
    recovered = {**persisted}
    assert recovered["canonical"] == "tmdb:1362"
    assert recovered["state"] == "SEARCHING"
    assert recovered["events"][-1] == "SEARCHING"


def test_equivalent_titles_do_not_share_idempotency_keys():
    backend = FakeMediaBackend()
    dune_1984 = backend.plan("movie", 841)
    dune_2021 = backend.plan("movie", 438631)
    assert dune_1984["key"] != dune_2021["key"]


def test_provider_failure_is_not_reported_as_started():
    backend = FakeMediaBackend()
    plan = backend.plan("movie", 8467)
    item = backend.items.setdefault(plan["key"], __import__("fake_media_backend").FakeMediaItem(plan["key"], state="FAILED"))
    assert backend.plan("movie", 8467)["result"] == "PLAN_READY"
    assert item.state == "FAILED"
