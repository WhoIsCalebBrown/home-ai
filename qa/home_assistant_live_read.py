#!/usr/bin/env python3
"""Read-only Home Assistant inventory/history latency probe for deployment QA."""

import asyncio
import json
import os
import time
import urllib.parse
import urllib.request

import websockets


BASE = os.getenv("HOME_ASSISTANT_URL", "http://192.168.40.44:8123").rstrip("/")
TOKEN_PATH = os.getenv("HOME_ASSISTANT_TOKEN_FILE", "/config/home-ai/secrets/home-assistant.token")


async def snapshot(token):
    url = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
    started = time.perf_counter()
    results = {}
    async with websockets.connect(url, open_timeout=8) as socket:
        assert json.loads(await socket.recv())["type"] == "auth_required"
        await socket.send(json.dumps({"type": "auth", "access_token": token}))
        assert json.loads(await socket.recv())["type"] == "auth_ok"
        commands = ["get_states", "config/area_registry/list", "config/floor_registry/list",
                    "config/device_registry/list", "config/entity_registry/list"]
        for number, command in enumerate(commands, 1):
            await socket.send(json.dumps({"id": number, "type": command}))
            reply = json.loads(await socket.recv())
            assert reply.get("success"), (command, reply)
            results[command] = reply.get("result") or []
    return results, round((time.perf_counter() - started) * 1000, 1)


def history(token, entity_ids):
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    query = urllib.parse.urlencode({"filter_entity_id": ",".join(entity_ids),
                                    "minimal_response": "false", "no_attributes": "true",
                                    "significant_changes_only": "true"})
    request = urllib.request.Request(f"{BASE}/api/history/period/{start}?{query}",
                                     headers={"Authorization": f"Bearer {token}"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read())
    return sum(len(series) for series in payload), round((time.perf_counter() - started) * 1000, 1)


async def main():
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()
    data, websocket_ms = await snapshot(token)
    states = data["get_states"]
    entity_registry = {item.get("entity_id"): item for item in data["config/entity_registry/list"]}
    relevant = [item for item in states
                if item.get("entity_id", "").split(".", 1)[0] in {"light", "switch"}
                and (entity_registry.get(item.get("entity_id"), {}).get("disabled_by") is None)
                and (entity_registry.get(item.get("entity_id"), {}).get("hidden_by") is None)]
    routines = [item for item in states if item.get("entity_id", "").split(".", 1)[0] in {"scene", "script", "automation"}]
    history_events, history_ms = history(token, [item["entity_id"] for item in relevant])
    print(json.dumps({
        "home_assistant_version_probe": "supported_api_ok",
        "websocket_snapshot_ms": websocket_ms,
        "history_24h_ms": history_ms,
        "areas": [item.get("name") for item in data["config/area_registry/list"]],
        "floors": [item.get("name") for item in data["config/floor_registry/list"]],
        "device_registry_count": len(data["config/device_registry/list"]),
        "entity_registry_count": len(data["config/entity_registry/list"]),
        "authorized_entities": [{"entity_id": item.get("entity_id"), "state": item.get("state")} for item in relevant],
        "routines": [{"entity_id": item.get("entity_id"), "state": item.get("state")} for item in routines],
        "history_events_24h": history_events,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
