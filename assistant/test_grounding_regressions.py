"""Regression checks for live grounding, provenance, camera safety, and aliases."""

import ast
import json
import re
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("voice-api-app.py").read_text())
needed = {"SOURCE_NAMES", "ARTIST_ALIASES", "artist_from_speech", "visual_question", "front_door_presence_question", "current_external_question", "plex_query_from_speech", "investigation_query_from_speech", "deterministic_plan", "preflight_plan", "evidence_supported_answer", "grounded_camera_presence_answer"}
nodes = [node for node in tree.body if getattr(node, "name", None) in needed or (isinstance(node, (ast.Assign, ast.AnnAssign)) and any(getattr(target, "id", None) in needed for target in getattr(node, "targets", [])))]
namespace = {"json": json, "re": re}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "voice-api-app.py", "exec"), namespace)
SOURCE_NAMES = namespace["SOURCE_NAMES"]
evidence_supported_answer = namespace["evidence_supported_answer"]
preflight_plan = namespace["preflight_plan"]
visual_question = namespace["visual_question"]
grounded_camera_presence_answer = namespace["grounded_camera_presence_answer"]


def test_download_followup_uses_recorded_sources():
    expected = ["qbittorrent", "sonarr", "radarr", "lidarr", "slskd", "torbox"]
    assert [SOURCE_NAMES[name] for name in expected] == ["qBittorrent", "Sonarr", "Radarr", "Lidarr", "Slskd", "Torbox"]


def test_lidar_alias_resolves_to_canonical_lidarr():
    assert preflight_plan("Can you restart LIDAR?") == [("restart_container", {"name": "lidarr"})]


def test_confirmation_target_is_canonical_in_plan():
    assert preflight_plan("Restart Lidarr") == [("restart_container", {"name": "lidarr"})]


def test_visual_claim_without_image_is_rejected():
    assert visual_question("What color shirt are they wearing?")
    answer = evidence_supported_answer("I can see a white hoodie and black hat.", "What color shirt are they wearing?", [])
    assert answer == "I can't actually see the current camera image with the tools I have right now."


def test_camera_stats_cannot_ground_visual_claims():
    result = [{"tool": "frigate_stats", "status": "ok", "result": {"cameras": {"front_door": {"camera_fps": 5.0}}}}]
    answer = evidence_supported_answer("Someone is wearing a pink hat.", "What color hat are they wearing?", result)
    assert "can't actually see" in answer


def test_current_external_questions_prefer_research():
    assert preflight_plan("What are Donald Trump's latest trade policies?") == [("web_search", {"query": "What are Donald Trump's latest trade policies?"})]
    assert preflight_plan("What's the newest version of Ollama?") == [("web_search", {"query": "What's the newest version of Ollama?"})]


def test_recent_frigate_event_does_not_prove_current_presence():
    result = {"events": [{"label": "person", "camera": "front_door", "age_seconds": 30, "active": False}]}
    answer = grounded_camera_presence_answer(result)
    assert "30 seconds ago" in answer
    assert "no longer active" in answer


def test_somebody_front_door_phrase_uses_frigate_events():
    assert preflight_plan("Is somebody at my front door?")[0][0] == "frigate_recent_events"


def test_active_frigate_event_can_ground_current_presence():
    result = {"events": [{"label": "person", "camera": "front_door", "age_seconds": 2, "active": True}]}
    assert "active person event" in grounded_camera_presence_answer(result)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("grounding regressions passed")
