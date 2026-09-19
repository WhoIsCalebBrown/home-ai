"""P0 regression coverage for authoritative structured tool responses."""

import ast
import re
from pathlib import Path


SOURCE = Path(__file__).with_name("voice-api-app.py")


def _direct_answer():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {"direct_structured_answer", "library_category_followup", "library_count_category"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    namespace = {"re": re, "tts_suppressed": type("Suppressed", (), {"get": staticmethod(lambda: False)})()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["direct_structured_answer"]


direct_structured_answer = _direct_answer()


def _ok(tool, result):
    return {"tool": tool, "status": "ok", "transport_ok": True, "operation_ok": True, "result": result}


def test_cache_numbers_are_rendered_only_from_authoritative_fields():
    answer = direct_structured_answer("How full is cache?", [_ok("unraid_storage_status", {
        "target": "cache", "total_bytes": 500_000_000_000, "used_bytes": 290_000_000_000,
        "free_bytes": 210_000_000_000, "used_percent": 58,
    })])
    assert answer == "Your Cache is 58% full: 290 GB used and 210 GB free."
    assert "120 GB" not in answer
    assert "180 GB" not in answer
    assert "200 GB" not in answer


def test_unraid_health_uses_returned_primitives_without_synthesis():
    answer = direct_structured_answer("Give me a quick server status.", [_ok("unraid_system_health", {
        "array_state": "STARTED", "parity_valid": True, "array_used_percent": 58,
        "cpu_usage_percent": 12, "cpu_temp_celsius": 53, "ram_usage_percent": 44, "uptime_seconds": 183_900,
        "running_containers": 14, "total_containers": 16, "firing_alerts": [],
    })])
    assert answer == "Server health: array started; parity valid; array 58% used; CPU 12%; CPU 53°C; RAM 44%; uptime 2d 3h; 14/16 containers running; no firing alerts."


def test_gpu_success_is_rendered_deterministically():
    answer = direct_structured_answer("What's the GPU usage?", [_ok("get_gpu_status", {
        "gpus": [{"model": "RTX 3070", "vram_used_mib": 1234, "vram_total_mib": 8192,
                  "utilization_percent": 42, "temperature_c": 61}],
    })])
    assert answer == "RTX 3070: 1234 MiB of 8192 MiB VRAM, 42% utilization, 61°C."


def test_gpu_operation_failure_cannot_be_presented_as_success():
    answer = direct_structured_answer("What's the GPU usage?", [{
        "tool": "get_gpu_status", "status": "unavailable", "transport_ok": True,
        "operation_ok": False,
        "result": {"error": "GPU telemetry unavailable", "error_code": "GPU_TELEMETRY_UNAVAILABLE"},
    }])
    assert answer == "I can't read GPU telemetry right now."


def test_home_control_partial_result_names_unavailable_and_protected_devices():
    answer = direct_structured_answer("Turn off all the switches.", [_ok("home_control", {
        "status": "partial",
        "unavailable": [{"name": "Neon Socket 1"}],
        "protected": [{"name": "Router"}],
    })])
    assert answer == "The permitted devices were handled, but unavailable: Neon Socket 1; protected: Router."


def test_home_control_partial_no_action_says_no_command_was_sent():
    answer = direct_structured_answer("Turn off all the switches.", [_ok("home_control", {
        "status": "partial", "outcome": "no_action", "target_entity_ids": [],
        "unavailable": [{"name": "Neon Socket 1"}],
        "protected": [{"name": "Router"}],
    })])
    assert answer == "No command was sent. Unavailable: Neon Socket 1; protected: Router."


def test_container_operation_failure_is_not_successful_status():
    answer = direct_structured_answer("Is Plex running?", [{
        "tool": "unraid_container_status", "status": "invalid_arguments", "transport_ok": True,
        "operation_ok": False, "result": {"error": "a container name is required"},
    }])
    assert answer == "I need a container name before I can check its status."


def test_container_and_plex_counts_are_direct_structured_answers():
    container = direct_structured_answer("Is Plex running?", [_ok("unraid_container_status", {
        "found": True, "name": "Plex-Media-Server", "state": "running", "status": "healthy",
        "cpu_percent": 3.5, "memory_display": "1.2 GiB / 4 GiB",
    })])
    counts = direct_structured_answer("How many movies do I have?", [_ok("plex_library_counts", {
        "libraries": [{"library": "Movies", "type": "movie", "items": 123}, {"library": "TV Shows", "type": "show", "items": 45}],
    })])
    assert container == "Plex-Media-Server is running, healthy, CPU 3.5%, memory 1.2 GiB / 4 GiB."
    assert counts == "Plex library counts: Movies: 123."


def test_category_followup_uses_only_returned_plex_library_names_and_types():
    answer = direct_structured_answer("What about anime?", [_ok("plex_library_counts", {
        "libraries": [
            {"library": "Movies", "type": "movie", "items": 123},
            {"library": "Anime", "type": "show", "items": 45},
        ],
    })])
    assert answer == "Plex library counts: Anime: 45."
    absent = direct_structured_answer("What about anime?", [_ok("plex_library_counts", {
        "libraries": [{"library": "Movies", "type": "movie", "items": 123}],
    })])
    assert absent == "I couldn't find a configured Plex anime library to count."


def test_storage_state_followup_uses_storage_status_not_container_semantics():
    answer = direct_structured_answer("Is it running?", [_ok("unraid_storage_status", {
        "target": "cache", "status": "ONLINE", "used_percent": 58,
    })])
    assert answer == "Cache status is ONLINE."


def test_collective_library_inventory_groups_only_returned_plex_matches():
    answer = direct_structured_answer("What Galactic Saga stuff do I have?", [_ok("plex_search", {
        "query": "Galactic Saga",
        "matches": [
            {"title": "Galactic Saga", "year": 2011, "media_type": "movie", "library_title": "Movies"},
            {"title": "Galactic Saga: Origins", "year": 2015, "media_type": "show", "library_title": "TV Shows"},
        ],
    })])
    assert answer == "In Plex, I found Movies: Galactic Saga (2011); TV Shows: Galactic Saga: Origins (2015)."
