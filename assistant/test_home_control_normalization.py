"""Regression coverage for bounded Home Assistant control normalization."""

import ast
import re
from pathlib import Path

import pytest


SOURCE = Path(__file__).with_name("voice-api-app.py")


def _normalizer():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if getattr(node, "name", None) == "normalize_home_tool_arguments")
    namespace = {"re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["normalize_home_tool_arguments"]


normalize_home_tool_arguments = _normalizer()


@pytest.mark.parametrize("utterance", [
    "Turn off all the switches.",
    "Turn off all outlets.",
    "Turn off all plugs.",
])
def test_omitted_switch_bulk_target_normalizes_to_switches(utterance):
    assert normalize_home_tool_arguments("home_control", {"action": "turn_off"}, utterance) == {
        "action": "turn_off", "entity_or_area": "all switches",
    }


@pytest.mark.parametrize("utterance", [
    "Turn off all the switches.",
    "Turn off all outlets.",
    "Turn off all plugs.",
])
def test_explicit_switch_bulk_wording_overrides_model_everything_target(utterance):
    assert normalize_home_tool_arguments("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
    }, utterance) == {"action": "turn_off", "entity_or_area": "all switches"}


@pytest.mark.parametrize("utterance", [
    "Turn off everything except switches.",
    "Turn off everything except the outlets.",
    "Turn off everything except the plugs.",
])
def test_excluded_switch_category_normalizes_model_everything_target_to_lights(utterance):
    assert normalize_home_tool_arguments("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
    }, utterance) == {"action": "turn_off", "entity_or_area": "all lights"}


def test_non_whole_home_switch_negation_does_not_invent_all_lights_target():
    assert normalize_home_tool_arguments("home_control", {"action": "turn_off"},
                                         "Turn off the bedroom lamp, not the switches.") == {
        "action": "turn_off",
    }


def test_non_whole_home_switch_negation_preserves_narrow_model_target():
    assert normalize_home_tool_arguments("home_control", {
        "action": "turn_off", "entity_or_area": "bedroom lamp",
    }, "Turn off the bedroom lamp, not the switches.") == {
        "action": "turn_off", "entity_or_area": "bedroom lamp",
    }


@pytest.mark.parametrize("utterance", [
    "Turn off all lights and switches.",
    "Turn off all lights and light switches.",
    "Turn off all light switches and lamps.",
])
def test_mixed_light_and_switch_whole_home_intent_remains_everything(utterance):
    assert normalize_home_tool_arguments("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
    }, utterance) == {
        "action": "turn_off", "entity_or_area": "everything",
    }


def test_switch_bulk_filters_model_exact_ids_to_switch_domain():
    assert normalize_home_tool_arguments("home_control", {
        "action": "turn_off", "entity_or_area": "everything",
        "entity_ids": ["light.office_light", "switch.neon_light_socket_1"],
    }, "Turn off all switches.") == {
        "action": "turn_off", "entity_or_area": "all switches",
        "entity_ids": ["switch.neon_light_socket_1"],
    }


@pytest.mark.parametrize("model_target", [{}, {"entity_or_area": "everything"}])
def test_light_switch_bulk_filters_model_exact_ids_to_switch_domain(model_target):
    assert normalize_home_tool_arguments("home_control", {
        "action": "turn_off", **model_target,
        "entity_ids": ["light.office_light", "switch.neon_light_socket_1"],
    }, "Turn off all light switches.") == {
        "action": "turn_off", "entity_or_area": "all switches",
        "entity_ids": ["switch.neon_light_socket_1"],
    }
