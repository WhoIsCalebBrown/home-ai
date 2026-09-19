"""Schema regressions for the live voice/audio trace observer."""

import importlib.util
from pathlib import Path
import sys


SOURCE = Path(__file__).with_name("voice_audio_e2e.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("voice_audio_e2e_trace", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_projected_trace_entries_count_complete_tools_as_selected():
    module = _load_module()
    trace = module.trace_entries({"type": "trace", "entries": [{
        "tool": "weather_forecast", "action": "Checked the forecast", "status": "complete", "sources": [],
    }]})
    assert module.selected_trace_tools(trace) == {"weather_forecast"}


def test_trace_observer_ignores_legacy_tools_payload():
    module = _load_module()
    assert module.trace_entries({"type": "trace", "tools": [{"tool": "weather_forecast", "status": "ok"}]}) == []
