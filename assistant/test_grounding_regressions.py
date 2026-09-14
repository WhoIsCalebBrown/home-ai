"""Regression checks for live grounding, provenance, camera safety, and aliases."""

import ast
import json
import re
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("voice-api-app.py").read_text())
needed = {"SOURCE_NAMES", "ARTIST_ALIASES", "artist_from_speech", "visual_question", "front_door_presence_question", "current_external_question", "plex_query_from_speech", "investigation_query_from_speech", "deterministic_plan", "preflight_plan", "evidence_supported_answer", "grounded_camera_presence_answer", "routing_aliases", "weather_location_from_text", "explicit_topic", "turn_context", "resolved_followup_text", "conversation_context", "explicit_domain", "social_acknowledgement"}
def is_needed_assignment(node):
    targets = getattr(node, "targets", [])
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return isinstance(node, (ast.Assign, ast.AnnAssign)) and any(getattr(target, "id", None) in needed for target in targets)


nodes = [node for node in tree.body if getattr(node, "name", None) in needed or is_needed_assignment(node)]
namespace = {"json": json, "re": re}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "voice-api-app.py", "exec"), namespace)
SOURCE_NAMES = namespace["SOURCE_NAMES"]
evidence_supported_answer = namespace["evidence_supported_answer"]
preflight_plan = namespace["preflight_plan"]
visual_question = namespace["visual_question"]
grounded_camera_presence_answer = namespace["grounded_camera_presence_answer"]
routing_aliases = namespace["routing_aliases"]
weather_location_from_text = namespace["weather_location_from_text"]
turn_context = namespace["turn_context"]
resolved_followup_text = namespace["resolved_followup_text"]
conversation_context = namespace["conversation_context"]
explicit_domain = namespace["explicit_domain"]
social_acknowledgement = namespace["social_acknowledgement"]


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


def test_explicit_weather_location_beats_old_context():
    assert weather_location_from_text("And what's the current weather in Welland, Ontario?") == "Welland, Ontario"
    assert preflight_plan("And what's the current weather in Welland, Ontario?") == [("weather_forecast", {"location": "Welland, Ontario", "days_from_now": 0})]


def test_weather_followup_keeps_location_without_topic_contamination():
    assert preflight_plan("What is the weather tomorrow?") == [("weather_forecast", {"location": None, "days_from_now": 1})]


def test_media_aliases_are_routing_only():
    text = routing_aliases("Is there anything on LiDAR that's going to be added to Plexium?")
    assert "Lidarr" in text and "Plex" in text
    assert preflight_plan(text)[0][0] == "investigate_media_pipeline"
    assert "Lidarr" in routing_aliases("Is litter going to Plex right now?")


def test_explicit_topic_change_clears_weather_bias():
    conversation_context.clear()
    turn_context("scenario", "What's the weather in Welland, Ontario?")
    turn_context("scenario", "What's happening in the news today?")
    assert resolved_followup_text("scenario", "What's happening in the news today?") == "What's happening in the news today?"
    assert conversation_context["scenario"]["kind"] == "web_research"


def test_weather_followup_inherits_only_when_referential():
    conversation_context.clear()
    turn_context("scenario", "What's the weather in Toronto?")
    assert resolved_followup_text("scenario", "What about tomorrow?") == "weather in Toronto tomorrow"
    assert resolved_followup_text("scenario", "And what's current news today?") == "And what's current news today?"


def test_explicit_domain_switches_override_camera_context():
    conversation_context.clear()
    turn_context("scenario", "Is somebody at my front door right now?")
    assert explicit_domain("How many GPUs are being used right now?", conversation_context["scenario"]) == "server"
    assert "front door camera" not in resolved_followup_text("scenario", "How many GPUs are being used right now?")
    assert preflight_plan("How many GPUs are being used right now?") == [("get_gpu_status", {})]


def test_container_see_is_server_not_vision():
    conversation_context.clear()
    assert explicit_domain("What containers can you see?") == "server"
    assert preflight_plan("What containers can you see?") == [("list_containers", {})]


def test_current_news_followup_about_ai_uses_web():
    conversation_context.clear()
    turn_context("scenario", "What's the biggest news story in Canada today?")
    assert preflight_plan("Anything interesting with AI specifically?") == [("web_search", {"query": "Anything interesting with AI specifically?"})]


def test_media_correction_keeps_media_intent():
    conversation_context.clear()
    turn_context("scenario", "Is there anything in lidar going to Plex?")
    corrected = routing_aliases("Yeah, I meant Lidarr and I also meant Plex, not flux")
    assert explicit_domain(corrected, conversation_context["scenario"]) == "media"
    assert preflight_plan(corrected)[0][0] == "investigate_media_pipeline"


def test_social_acknowledgement_does_not_inherit_tools():
    assert social_acknowledgement("Thank you.")
    conversation_context.clear()
    turn_context("scenario", "Is somebody at my front door?")
    assert explicit_domain("Thank you.", conversation_context["scenario"]) == "general"


def test_server_fallback_cannot_leak_into_news_synthesis():
    result = [{"tool": "web_search", "status": "ok", "result": {"results": [{"title": "AI news"}]}}]
    answer = evidence_supported_answer("I couldn't verify that current server information because the required live tool result was unavailable.", "Anything interesting with AI specifically?", result, "web_research")
    assert "server" not in answer.lower()
    assert "news results" in answer.lower()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("grounding regressions passed")
