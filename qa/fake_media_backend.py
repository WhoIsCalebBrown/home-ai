"""Deterministic fake media backend for safe state-machine tests.

It intentionally shares the semantic shape of the production bridge but has no
network, filesystem, Plex, manager, or provider access.
"""

from dataclasses import dataclass, field


@dataclass
class FakeMediaItem:
    key: tuple
    state: str = "IDENTIFIED"
    events: list[str] = field(default_factory=lambda: ["IDENTIFIED"])
    request_count: int = 0
    plex_visible: bool = False


class FakeMediaBackend:
    def __init__(self):
        self.items: dict[tuple, FakeMediaItem] = {}

    def plan(self, media_type: str, external_id: int, mode: str = "standard", seasons=(), episodes=()):
        key = (media_type, int(external_id), tuple(sorted(set(seasons))), tuple(sorted(set(episodes))), mode)
        item = self.items.get(key)
        if item and item.state not in {"FAILED", "NO_CANDIDATE"}:
            return {"result": "NO_OP", "key": key, "state": item.state}
        return {"result": "PLAN_READY", "key": key, "state": item.state if item else "IDENTIFIED"}

    def submit(self, plan: dict):
        key = tuple(plan["key"])
        if key[0] not in {"movie", "tv", "anime"} or key[3]:
            return {"status": "BLOCKED_UNSUPPORTED_SCOPE", "write_count": 0}
        item = self.items.setdefault(key, FakeMediaItem(key))
        if item.state not in {"IDENTIFIED", "FAILED", "NO_CANDIDATE"}:
            return {"status": "NO_OP", "write_count": 0, "state": item.state}
        item.request_count += 1
        item.state = "REQUESTED"
        item.events.append("REQUESTED")
        return {"status": "SUBMITTED", "write_count": 1, "state": item.state}

    def transition(self, key, raw_state: str):
        item = self.items[key]
        mapping = {"Wanted": "REQUESTED", "Scraping": "SEARCHING", "Adding": "ACQUIRING", "Checking": "VERIFYING", "Collected": "ACQUIRED_NOT_VISIBLE"}
        item.state = mapping.get(raw_state, "UNKNOWN")
        item.events.append(item.state)
        return item.state

    def plex_verify(self, key, visible: bool):
        item = self.items[key]
        item.plex_visible = bool(visible)
        if visible:
            item.state = "AVAILABLE"
            item.events.append("AVAILABLE")
        return item.state

