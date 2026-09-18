"""Unit tests for the bounded Unraid Management Agent MCP adapters.

Only unraid_mcp_call's httpx transport is faked -- everything above it
(the six unraid_* wrapper functions) is real, unmodified production code,
following the same fake-the-network-boundary-only discipline as
test_media_workflow_integration.py.
"""
import asyncio
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("server_tools_app", Path(__file__).with_name("server-tools-app.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _mcp_result(payload) -> str:
    return "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})


def _install_fake_mcp(monkeypatch, tool_results: dict[str, object]):
    """tool_results maps MCP tool_name -> the python object that tool
    should appear to return (already JSON-serializable)."""
    _json = json  # alias captured before `post(..., json=None)` shadows the name below

    class FakeResponse:
        def __init__(self, text="", headers=None):
            self.text = text
            self.headers = headers or {}

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None, **kwargs):
            method = (json or {}).get("method")
            if method == "initialize":
                return FakeResponse(headers={"Mcp-Session-Id": "fake-session-1"})
            if method == "notifications/initialized":
                return FakeResponse()
            if method == "tools/call":
                name = json["params"]["name"]
                if name not in tool_results:
                    return FakeResponse("event: message\ndata: " + _json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": f"no fixture for {name}"}}))
                fixture = tool_results[name]
                payload = fixture(json["params"].get("arguments") or {}) if callable(fixture) else fixture
                return FakeResponse(_mcp_result(payload))
            raise AssertionError(f"unexpected MCP method: {method}")

    monkeypatch.setattr(module, "httpx", type("FakeHttpxModule", (), {"AsyncClient": FakeAsyncClient}))


def test_storage_status_array_and_named_disk(monkeypatch):
    _install_fake_mcp(monkeypatch, {
        "get_array_status": {"state": "STARTED", "free_bytes": 100, "total_bytes": 400, "used_percent": 75.0, "parity_valid": True, "num_disks": 3},
        "list_disks": [
            {"name": "cache", "role": "cache", "status": "DISK_OK", "size_bytes": 500, "used_bytes": 300, "free_bytes": 200},
            {"name": "disk1", "role": "data", "status": "DISK_OK", "size_bytes": 1000, "used_bytes": 400, "free_bytes": 600},
        ],
    })
    array = asyncio.run(module.unraid_storage_status({"target": "array"}))
    assert array["target"] == "array" and array["free_bytes"] == 100 and array["total_bytes"] == 400

    cache = asyncio.run(module.unraid_storage_status({"target": "cache"}))
    assert cache["found"] is True and cache["used_percent"] == 60.0

    missing = asyncio.run(module.unraid_storage_status({"target": "nope"}))
    assert missing["found"] is False


def test_disks_listing_excludes_virtual_devices_from_fullest_ranking(monkeypatch):
    # Real production bug found live: "Which disk is fullest?" answered
    # "The Log disk is fullest at 97.4%" -- true but deeply misleading. Log
    # is a 128MB tmpfs-backed syslog partition, not a physical disk a user
    # cares about, and is EXPECTED to run near full. Virtual devices (role
    # "log"/"docker_vdisk"/"unknown") must never win a fullest-disk ranking
    # or clutter a real disk-capacity listing.
    _install_fake_mcp(monkeypatch, {
        "list_disks": [
            {"name": "disk1", "role": "data", "status": "DISK_OK", "size_bytes": 1000, "used_bytes": 400},
            {"name": "cache", "role": "cache", "status": "DISK_OK", "size_bytes": 500, "used_bytes": 300},
            {"name": "flash", "role": "unknown", "status": "DISK_OK", "size_bytes": 100, "used_bytes": 95},
            {"name": "Docker vDisk", "role": "docker_vdisk", "status": "DISK_OK", "size_bytes": 200, "used_bytes": 190},
            {"name": "Log", "role": "log", "status": "DISK_OK", "size_bytes": 128, "used_bytes": 124},
        ],
    })
    result = asyncio.run(module.unraid_storage_status({"target": "disks"}))
    names = [d["name"] for d in result["disks"]]
    assert names == ["disk1", "cache"]
    assert "Log" not in names and "flash" not in names and "Docker vDisk" not in names


def test_disk_health_flags_only_real_problems_not_virtual_devices(monkeypatch):
    # Real finding from live inspection: get_health_status reported
    # "warning_disks: 8" while every actual physical disk showed DISK_OK /
    # PASSED -- the virtual devices (flash, Docker vDisk, Log) simply don't
    # report a real SMART status and must not be counted as unhealthy.
    _install_fake_mcp(monkeypatch, {
        "get_array_status": {"state": "STARTED", "parity_valid": True},
        "list_disks": [
            {"name": "parity", "role": "parity", "status": "DISK_OK", "smart_status": "PASSED", "temperature_celsius": 37},
            {"name": "disk1", "role": "data", "status": "DISK_OK", "smart_status": "PASSED", "temperature_celsius": 39},
            {"name": "flash", "role": "unknown", "status": "DISK_OK", "smart_status": "UNKNOWN", "temperature_celsius": 0},
            {"name": "Docker vDisk", "role": "docker_vdisk", "status": "DISK_OK", "smart_status": None, "temperature_celsius": 0},
        ],
    })
    health = asyncio.run(module.unraid_disk_health({}))
    assert health["concerning_disks"] == []
    assert health["hottest_disk"]["name"] == "disk1"


def test_container_status_found_and_not_found(monkeypatch):
    _install_fake_mcp(monkeypatch, {
        "get_container_info": {"name": "Plex", "state": "running", "status": "Up 3 days", "network_mode": "bridge", "cpu_percent": 1.2, "memory_display": "0.5 GB / 4 GB"},
    })
    info = asyncio.run(module.unraid_container_status({"container": "Plex"}))
    assert info["found"] is True and info["state"] == "running"

    empty = asyncio.run(module.unraid_container_status({"container": ""}))
    assert "error" in empty


def test_container_status_reports_memory_in_megabytes_below_one_gigabyte(monkeypatch):
    # Real production bug found live: "How much memory is Home-AI using?"
    # answered "about 0.13 gigabytes" -- correct but an unnatural spoken
    # unit. The upstream MCP's own memory_display field is always formatted
    # in GB regardless of magnitude; reformat from the raw byte fields
    # ourselves, choosing MB below 1 GB.
    _install_fake_mcp(monkeypatch, {
        "get_container_info": {"name": "Home-AI-Assistant", "state": "running", "status": "Up",
                                "memory_usage_bytes": 147443712, "memory_limit_bytes": 67346886656,
                                "memory_display": "0.14 GB / 62.72 GB"},
    })
    info = asyncio.run(module.unraid_container_status({"container": "Home-AI-Assistant"}))
    assert info["memory_display"] == "141 MB / 62.72 GB"

    # A container using more than a gigabyte still reports GB.
    _install_fake_mcp(monkeypatch, {
        "get_container_info": {"name": "Plex-Media-Server", "state": "running", "status": "Up",
                                "memory_usage_bytes": 3_350_000_000, "memory_limit_bytes": 67346886656,
                                "memory_display": "3.12 GB / 62.72 GB"},
    })
    info = asyncio.run(module.unraid_container_status({"container": "Plex-Media-Server"}))
    assert info["memory_display"] == "3.12 GB / 62.72 GB"


def test_container_status_falls_back_to_fuzzy_name_match(monkeypatch):
    # Real production bug: "Is Plex running?" was answered "isn't running"
    # even while Plex was plainly running and consuming CPU/RAM in the same
    # session -- CONTAINER_DISPLAY_NAMES maps "plex" -> "Plex", but the real
    # Docker container is named "Plex-Media-Server" and get_container_info's
    # own lookup is exact-match, not fuzzy. A miss must fall back to a
    # case-insensitive substring match against the live container list
    # before concluding the container does not exist.
    def _get_container_info(arguments):
        container_id = arguments.get("container_id")
        if container_id == "Plex-Media-Server":
            return {"name": "Plex-Media-Server", "state": "running", "status": "Up 10 days", "cpu_percent": 7.8}
        return {}

    _install_fake_mcp(monkeypatch, {
        "get_container_info": _get_container_info,
        "list_containers": [
            {"name": "Plex-Media-Server", "cpu_percent": 7.8, "status": "Up 10 days"},
            {"name": "Ollama", "cpu_percent": 1.0, "status": "Up 10 days"},
        ],
    })
    info = asyncio.run(module.unraid_container_status({"container": "Plex"}))
    assert info["found"] is True and info["name"] == "Plex-Media-Server"

    still_missing = asyncio.run(module.unraid_container_status({"container": "Nonexistent-Thing"}))
    assert still_missing["found"] is False


def test_container_metrics_does_not_win_discovery_for_a_generic_storage_followup():
    # Real production bug found in a live continuity test: "What's using
    # most of it?" (a referential storage-breakdown follow-up with no
    # explicit domain and no ram/cpu/memory keyword of its own) scored
    # unraid_container_metrics as the top discovery candidate purely via
    # alias/gram overlap on the generic word "using" -- Qwen then answered
    # with its CPU/RAM numbers presented as if they were disk-space
    # consumption, a real question/answer mismatch. unraid_container_metrics
    # only ever legitimately answers a RAM/CPU/unhealthy-container question,
    # so it must not win when none of those words are present, even though
    # its aliases happen to share the word "using" with unrelated questions.
    results = module.discover_capabilities("What's using most of it?", max_results=8)
    names = [r["metadata"]["canonical_name"] for r in results]
    assert "unraid_container_metrics" not in names[:1], f"unraid_container_metrics should not win a generic 'using' query, got top candidates: {names}"

    # The legitimate RAM/CPU/unhealthy questions must still work correctly.
    ram_results = module.discover_capabilities("What's using the most RAM?", max_results=8)
    assert ram_results[0]["metadata"]["canonical_name"] == "unraid_container_metrics"
    unhealthy_results = module.discover_capabilities("Are any containers unhealthy?", max_results=8)
    assert unhealthy_results[0]["metadata"]["canonical_name"] == "unraid_container_metrics"


def test_container_metrics_ranks_by_cpu_and_flags_unhealthy(monkeypatch):
    _install_fake_mcp(monkeypatch, {
        "list_containers": [
            {"name": "a", "cpu_percent": 1.0, "memory_usage_bytes": 100, "status": "Up 1 day"},
            {"name": "b", "cpu_percent": 9.0, "memory_usage_bytes": 50, "status": "Up 2 days (unhealthy)"},
        ],
    })
    metrics = asyncio.run(module.unraid_container_metrics({"sort_by": "cpu", "limit": 5}))
    assert metrics["top"][0]["name"] == "b"
    assert metrics["unhealthy_containers"] == ["b"]


def test_system_health_survives_alerts_call_failing(monkeypatch):
    # The alerts call is optional/best-effort; any failure there (not just
    # RuntimeError) must not sink the primary health summary.
    _install_fake_mcp(monkeypatch, {
        "get_health_status": {"array_state": "STARTED", "cpu_usage": 10.0, "ram_usage": 20.0, "uptime": 1000},
    })
    health = asyncio.run(module.unraid_system_health({}))
    assert health["array_state"] == "STARTED"
    assert health["firing_alerts"] == []


def test_gpu_status_normalizes_two_unraid_mcp_gpus(monkeypatch):
    _install_fake_mcp(monkeypatch, {
        "get_gpu_metrics": [
            {"name": "NVIDIA GeForce RTX 3070", "uuid": "GPU-3070",
             "memory_used_bytes": 5_199_888_384, "memory_total_bytes": 8_589_934_592,
             "utilization_gpu_percent": 31, "temperature_celsius": 42,
             "power_draw_watts": 20.45},
            {"name": "NVIDIA GeForce GTX 1660 SUPER", "uuid": "GPU-1660",
             "memory_used_bytes": 1_139_802_112, "memory_total_bytes": 6_442_450_944,
             "utilization_gpu_percent": 7, "temperature_celsius": 51,
             "power_draw_watts": 40.21},
        ],
    })

    result = asyncio.run(module.gpu_status({}))

    assert result == {"gpus": [
        {"model": "NVIDIA GeForce RTX 3070", "uuid": "GPU-3070",
         "vram_used_mib": 4959, "vram_total_mib": 8192,
         "utilization_percent": 31, "temperature_c": 42, "power_w": 20.45},
        {"model": "NVIDIA GeForce GTX 1660 SUPER", "uuid": "GPU-1660",
         "vram_used_mib": 1087, "vram_total_mib": 6144,
         "utilization_percent": 7, "temperature_c": 51, "power_w": 40.21},
    ]}


def test_gpu_status_returns_operation_failure_when_unraid_mcp_fails(monkeypatch):
    async def fail_gpu_metrics(*_args, **_kwargs):
        raise RuntimeError("Unraid MCP unavailable")

    monkeypatch.setattr(module, "unraid_mcp_call", fail_gpu_metrics)

    result = asyncio.run(module.gpu_status({}))

    assert result == {"error": "GPU telemetry unavailable",
                      "error_code": "GPU_TELEMETRY_UNAVAILABLE",
                      "detail": "RuntimeError", "evidence_available": False}


def test_unraid_tools_are_registered_and_discoverable():
    names = {item[0] for item in module.REGISTRY}
    for tool in ["unraid_storage_status", "unraid_disk_health", "unraid_container_status",
                 "unraid_container_metrics", "unraid_system_health"]:
        assert tool in names
    ranked = [r["metadata"]["canonical_name"] for r in module.discover_capabilities("How full is the cache drive?", 5)]
    assert "unraid_storage_status" in ranked
