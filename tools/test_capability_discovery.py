import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("server_tools_app", Path(__file__).with_name("server-tools-app.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def names(query):
    return [item["metadata"]["canonical_name"] for item in module.discover_capabilities(query, 8)]


def test_discovery_distinguishes_camera_capabilities():
    assert names("is the front door camera working")[:1] == ["frigate_stats"]
    assert names("was someone at the front door recently")[:1] == ["frigate_recent_events"]
    assert names("describe the front door right now")[:1] == ["frigate_snapshot"]
    assert names("describe the image from that detection")[:1] == ["frigate_event_snapshot"]


def test_discovery_alerts_are_events_not_camera_stats():
    assert names("any alerts from the front camera")[:1] == ["frigate_recent_events"]


def test_discovery_handles_local_aliases_and_utilities():
    assert "lidarr_health" in names("what is the status of LIDAR")[:4]
    assert names("what is 17.5 percent of 438")[0] == "calculator"
    assert names("convert 5 GB to MB")[0] == "unit_convert"


def test_calculator_and_units_are_deterministic():
    import asyncio
    assert asyncio.run(module.calculator({"expression": "17.5 * 438 / 100"}))["value"] == 76.65
    assert round(asyncio.run(module.unit_convert({"value": 5, "from_unit": "GB", "to_unit": "MB"}))["result"]) == 5000


def test_discovery_is_bounded():
    assert len(module.discover_capabilities("server media camera internet", 8)) <= 8
