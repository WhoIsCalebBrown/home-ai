"""Side-effect-free contracts for conversational Home Assistant targeting."""

import ast
import asyncio
import importlib.util
from pathlib import Path
import re

import pytest


def _load_tools():
    path = Path(__file__).parents[1] / "tools" / "server-tools-app.py"
    spec = importlib.util.spec_from_file_location("home_ai_tools_home_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _normalize_home_tool_arguments():
    path = Path(__file__).parents[1] / "assistant" / "voice-api-app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if getattr(node, "name", None) == "normalize_home_tool_arguments")
    namespace = {"re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["normalize_home_tool_arguments"]


def _entity(entity_id, state, name, area, *, modes=None, brightness=None):
    attrs = {"friendly_name": name, "supported_color_modes": modes or []}
    if brightness is not None:
        attrs["brightness"] = brightness
    return {
        "entity_id": entity_id, "state": state, "attributes": attrs,
        "_area_name": area, "_aliases": [], "_device_id": entity_id + "-device",
        "_device_name": name, "_integration": "fake",
        "last_changed": "2026-09-17T00:00:00+00:00",
        "last_updated": "2026-09-17T00:00:00+00:00",
    }


def test_unavailable_is_not_counted_as_off():
    module = _load_tools()
    entities = [
        _entity("light.hall", "off", "Hallway", "Hall"),
        _entity("light.bed", "unavailable", "Bedroom Lamp", "Bedroom"),
    ]

    async def inventory():
        return entities, {}

    module._home_assistant_entities = inventory
    result = asyncio.run(module.home_get_state({"entity_or_area": "off"}))
    assert [item["entity_id"] for item in result["devices"]] == ["light.hall"]
    assert result["count"] == 1


def test_exact_retained_set_is_revalidated_without_substitution():
    module = _load_tools()
    entities = [_entity("light.hall", "on", "Hallway", "Hall")]

    async def inventory():
        return entities, {}

    module._home_assistant_entities = inventory
    result = asyncio.run(module.home_get_state({
        "entity_ids": ["light.hall", "light.removed"],
    }))
    assert result["status"] == "partial"
    assert [item["entity_id"] for item in result["devices"]] == ["light.hall"]
    assert result["missing_or_unauthorized"] == ["light.removed"]


def test_empty_retained_set_does_not_broaden_to_whole_home():
    module = _load_tools()
    entities = [_entity("light.hall", "on", "Hallway", "Hall")]

    async def inventory():
        return entities, {}

    module._home_assistant_entities = inventory
    result = asyncio.run(module.home_get_state({"entity_ids": []}))
    assert result["status"] == "ok"
    assert result["devices"] == []


def test_relative_brightness_requires_observed_baseline():
    module = _load_tools()
    entities = [_entity("light.office", "on", "Office Light", "Office", modes=["brightness"])]

    async def inventory():
        return entities, {}

    module._home_assistant_entities = inventory
    module.HOME_WRITE_ALLOWED_ENTITIES.add("light.office")
    result = asyncio.run(module.home_control({
        "entity_ids": ["light.office"], "action": "adjust_brightness",
        "parameters": {"brightness_delta_pct": -10},
    }))
    assert result["status"] == "indeterminate"
    assert "baseline" in result["message"]


def test_unavailable_exact_control_never_submits_a_command():
    module = _load_tools()
    entities = [_entity("light.bed", "unavailable", "Bedroom Lamp", "Bedroom")]

    async def inventory():
        return entities, {}

    module._home_assistant_entities = inventory
    module.HOME_WRITE_ALLOWED_ENTITIES.add("light.bed")
    result = asyncio.run(module.home_control({"entity_ids": ["light.bed"], "action": "turn_off"}))
    assert result["status"] == "unavailable"


def test_new_read_visible_entity_does_not_gain_write_permission():
    module = _load_tools()
    entities = [_entity("light.new_device", "off", "New Device", "Office")]

    async def inventory():
        return entities, {}

    module._home_assistant_entities = inventory
    assert "light.new_device" not in module.HOME_WRITE_ALLOWED_ENTITIES
    result = asyncio.run(module.home_control({"entity_ids": ["light.new_device"], "action": "turn_on"}))
    assert result["status"] == "forbidden"


def test_empty_exact_history_scope_never_calls_history_backend(monkeypatch):
    module = _load_tools()

    async def inventory():
        return [_entity("light.hall", "on", "Hallway", "Hall")], {}

    class ForbiddenHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("empty exact history scope must not call Home Assistant history")

    module._home_assistant_entities = inventory
    monkeypatch.setattr(module.httpx, "AsyncClient", ForbiddenHttpClient)
    result = asyncio.run(module.home_get_activity({"entity_ids": ["light.removed"], "hours": 24}))
    assert result["status"] == "partial"
    assert result["devices"] == []
    assert result["changes"] == []
    assert result["missing_or_unauthorized"] == ["light.removed"]


def test_routine_inventory_rejects_non_routine_domains_before_home_assistant_access():
    module = _load_tools()

    async def forbidden_snapshot(_commands):
        raise AssertionError("invalid routine kind must fail before Home Assistant access")

    module._home_assistant_ws = forbidden_snapshot
    for kind in ("camera", "person", "lock", "sensor", "unknown"):
        try:
            asyncio.run(module.home_list_routines({"kind": kind}))
        except ValueError as exc:
            assert "routine kind" in str(exc).casefold()
        else:
            raise AssertionError(f"{kind} unexpectedly crossed the routine-domain boundary")


def test_exact_multi_switch_control_keeps_bulk_unclassified_loads_protected(monkeypatch):
    module = _load_tools()
    entities = [
        _entity("switch.router", "on", "Router", "Office"),
        _entity("switch.server", "on", "Server", "Office"),
    ]

    async def inventory():
        return entities, {}

    class ForbiddenHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("bulk-unclassified switches must not receive a service call")

    module._home_assistant_entities = inventory
    module.HOME_WRITE_ALLOWED_ENTITIES.update({"switch.router", "switch.server"})
    module.HOME_BULK_SAFE_ENTITIES.clear()
    monkeypatch.setattr(module.httpx, "AsyncClient", ForbiddenHttpClient)
    result = asyncio.run(module.home_control({
        "entity_ids": ["switch.router", "switch.server"],
        "action": "turn_off",
    }))
    assert result["status"] == "forbidden"
    assert result["target_entity_ids"] == []
    assert {item["entity_id"] for item in result["protected"]} == {"switch.router", "switch.server"}


def test_bulk_scope_survives_authorization_filtering(monkeypatch):
    module = _load_tools()
    entities = [
        _entity("switch.router", "on", "Router", "Office"),
        _entity("light.unapproved", "on", "Unapproved Light", "Office"),
    ]

    async def inventory():
        return entities, {}

    class ForbiddenHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("authorization filtering must not turn a bulk request into a single safe write")

    module._home_assistant_entities = inventory
    module.HOME_WRITE_ALLOWED_ENTITIES.clear()
    module.HOME_WRITE_ALLOWED_ENTITIES.add("switch.router")
    module.HOME_BULK_SAFE_ENTITIES.clear()
    monkeypatch.setattr(module.httpx, "AsyncClient", ForbiddenHttpClient)
    result = asyncio.run(module.home_control({
        "entity_ids": ["switch.router", "light.unapproved"],
        "action": "turn_off",
    }))
    assert result["status"] == "forbidden"
    assert result["target_entity_ids"] == []
    assert [item["entity_id"] for item in result["protected"]] == ["switch.router"]


def test_bulk_switch_control_only_writes_default_safe_switches(monkeypatch):
    monkeypatch.delenv("HOME_BULK_SAFE_ENTITIES", raising=False)
    module = _load_tools()
    entities = [
        _entity("light.office_light", "on", "Office Light", "Office"),
        _entity("switch.neon_light_socket_1", "on", "Neon Socket 1", "Office"),
        _entity("switch.neon_lights_socket_1", "on", "Neon Socket 2", "Office"),
        _entity("switch.neon_lights_socket_1_2", "on", "Neon Socket 3", "Office"),
        _entity("switch.router", "on", "Router", "Office"),
    ]

    async def inventory():
        return entities, {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        payloads = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            self.payloads.append(json)
            return FakeResponse()

    module._home_assistant_entities = inventory
    module._home_assistant_token = lambda: "test-token"
    module.HOME_WRITE_ALLOWED_ENTITIES.add("switch.router")
    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(module.home_control({"entity_or_area": "all switches", "action": "turn_off"}))

    assert result["status"] == "partial"
    assert result["target_entity_ids"] == [
        "switch.neon_light_socket_1",
        "switch.neon_lights_socket_1",
        "switch.neon_lights_socket_1_2",
    ]
    assert FakeAsyncClient.payloads == [{"entity_id": [
        "switch.neon_light_socket_1",
        "switch.neon_lights_socket_1",
        "switch.neon_lights_socket_1_2",
    ]}]
    assert [item["entity_id"] for item in result["protected"]] == ["switch.router"]


def test_bulk_switch_control_preserves_protected_when_safe_switch_is_unavailable(monkeypatch):
    monkeypatch.delenv("HOME_BULK_SAFE_ENTITIES", raising=False)
    module = _load_tools()
    entities = [
        _entity("switch.neon_light_socket_1", "unavailable", "Neon Socket 1", "Office"),
        _entity("switch.router", "on", "Router", "Office"),
    ]

    async def inventory():
        return entities, {}

    class ForbiddenHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unavailable and protected switches must not receive a service call")

    module._home_assistant_entities = inventory
    module.HOME_WRITE_ALLOWED_ENTITIES.add("switch.router")
    monkeypatch.setattr(module.httpx, "AsyncClient", ForbiddenHttpClient)
    result = asyncio.run(module.home_control({"entity_or_area": "all switches", "action": "turn_off"}))

    assert result["status"] == "partial"
    assert result["target_entity_ids"] == []
    assert [item["entity_id"] for item in result["unavailable"]] == ["switch.neon_light_socket_1"]
    assert [item["entity_id"] for item in result["protected"]] == ["switch.router"]


@pytest.mark.parametrize("utterance", [
    "Turn off everything except switches.",
    "Turn off everything except the outlets.",
    "Turn off everything except the plugs.",
])
def test_switch_exclusion_routes_only_lights(monkeypatch, utterance):
    arguments = _normalize_home_tool_arguments()("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
        "entity_ids": ["light.office_light", "switch.neon_light_socket_1"],
    }, utterance)
    module = _load_tools()
    entities = [
        _entity("light.office_light", "on", "Office Light", "Office"),
        _entity("switch.neon_light_socket_1", "on", "Neon Socket 1", "Office"),
        _entity("switch.router", "on", "Router", "Office"),
    ]

    async def inventory():
        return entities, {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        payloads = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            self.payloads.append(json)
            return FakeResponse()

    module._home_assistant_entities = inventory
    module._home_assistant_token = lambda: "test-token"
    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(module.home_control(arguments))

    assert arguments == {
        "action": "turn_off", "entity_or_area": "all lights",
        "entity_ids": ["light.office_light"],
    }
    assert result["target_entity_ids"] == ["light.office_light"]
    assert FakeAsyncClient.payloads == [{"entity_id": ["light.office_light"]}]


def test_mixed_whole_home_control_writes_lights_and_safe_switches(monkeypatch):
    arguments = _normalize_home_tool_arguments()("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
    }, "Turn off all lights and switches.")
    module = _load_tools()
    entities = [
        _entity("light.office_light", "on", "Office Light", "Office"),
        _entity("switch.neon_light_socket_1", "on", "Neon Socket 1", "Office"),
    ]

    async def inventory():
        return entities, {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        payloads = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            self.payloads.append(json)
            return FakeResponse()

    module._home_assistant_entities = inventory
    module._home_assistant_token = lambda: "test-token"
    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(module.home_control(arguments))

    assert arguments["entity_or_area"] == "everything"
    assert result["target_entity_ids"] == ["light.office_light", "switch.neon_light_socket_1"]
    assert FakeAsyncClient.payloads == [
        {"entity_id": ["light.office_light"]},
        {"entity_id": ["switch.neon_light_socket_1"]},
    ]


def test_switch_bulk_filters_mixed_exact_ids_before_tools_control(monkeypatch):
    arguments = _normalize_home_tool_arguments()("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
        "entity_ids": ["light.office_light", "switch.neon_light_socket_1"],
    }, "Turn off all switches.")
    module = _load_tools()
    entities = [
        _entity("light.office_light", "on", "Office Light", "Office"),
        _entity("switch.neon_light_socket_1", "on", "Neon Socket 1", "Office"),
    ]

    async def inventory():
        return entities, {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        payloads = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            self.payloads.append(json)
            return FakeResponse()

    module._home_assistant_entities = inventory
    module._home_assistant_token = lambda: "test-token"
    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(module.home_control(arguments))

    assert arguments["entity_ids"] == ["switch.neon_light_socket_1"]
    assert result["target_entity_ids"] == ["switch.neon_light_socket_1"]
    assert FakeAsyncClient.payloads == [{"entity_id": ["switch.neon_light_socket_1"]}]


def test_switch_bulk_empty_filtered_exact_ids_sends_no_command(monkeypatch):
    arguments = _normalize_home_tool_arguments()("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
        "entity_ids": ["light.office_light"],
    }, "Turn off all switches.")
    module = _load_tools()

    async def inventory():
        return [_entity("light.office_light", "on", "Office Light", "Office")], {}

    class ForbiddenHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("an empty exact set must not send a Home Assistant command")

    module._home_assistant_entities = inventory
    monkeypatch.setattr(module.httpx, "AsyncClient", ForbiddenHttpClient)
    result = asyncio.run(module.home_control(arguments))

    assert arguments["entity_ids"] == []
    assert result["status"] == "not_found"
