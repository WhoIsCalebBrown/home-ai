from fake_media_backend import FakeMediaBackend


def test_standard_movie_is_idempotent_and_tracks_provider_states():
    backend = FakeMediaBackend()
    plan = backend.plan("movie", 8467)
    assert plan["result"] == "PLAN_READY"
    assert backend.submit(plan) == {"status": "SUBMITTED", "write_count": 1, "state": "REQUESTED"}
    assert backend.submit(plan)["status"] == "NO_OP"
    key = plan["key"]
    for raw, expected in (("Wanted", "REQUESTED"), ("Scraping", "SEARCHING"), ("Adding", "ACQUIRING"), ("Checking", "VERIFYING"), ("Collected", "ACQUIRED_NOT_VISIBLE")):
        assert backend.transition(key, raw) == expected
    assert backend.plex_verify(key, True) == "AVAILABLE"
    assert backend.items[key].request_count == 1


def test_scope_is_part_of_the_idempotency_key():
    backend = FakeMediaBackend()
    season_one = backend.plan("tv", 95396, seasons=[1])
    season_two = backend.plan("tv", 95396, seasons=[2])
    assert season_one["key"] != season_two["key"]
    assert backend.submit(season_one)["write_count"] == 1
    assert backend.submit(season_two)["write_count"] == 1
    assert len(backend.items) == 2


def test_episode_scope_is_fail_closed():
    backend = FakeMediaBackend()
    plan = backend.plan("tv", 95396, episodes=[8])
    assert backend.submit(plan)["status"] == "BLOCKED_UNSUPPORTED_SCOPE"
    assert not backend.items


def test_failed_attempt_can_be_replanned_without_duplicate_active_request():
    backend = FakeMediaBackend()
    plan = backend.plan("movie", 1362)
    assert backend.submit(plan)["write_count"] == 1
    key = plan["key"]
    backend.items[key].state = "FAILED"
    retry = backend.plan("movie", 1362)
    assert retry["result"] == "PLAN_READY"
    assert backend.submit(retry)["write_count"] == 1
    assert backend.items[key].request_count == 2
