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
