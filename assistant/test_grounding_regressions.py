"""Regression checks for live grounding, provenance, camera safety, and aliases."""

import ast
import json
import re
import time
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("voice-api-app.py").read_text())
needed = {"SOURCE_NAMES", "ARTIST_ALIASES", "DOMAIN_ENTITIES", "artist_from_speech", "visual_question", "activity_question", "front_door_presence_question", "dynamic_fact_question", "current_external_question", "explicit_web_search_request", "historical_camera_question", "historical_camera_window", "plex_query_from_speech", "investigation_query_from_speech", "deterministic_plan", "preflight_plan", "evidence_supported_answer", "grounded_camera_presence_answer", "direct_structured_answer", "media_plan_response", "routing_aliases", "contextual_entity_resolution", "is_repair_turn", "repair_route_text", "weather_location_from_text", "explicit_topic", "turn_context", "resolved_followup_text", "conversation_context", "explicit_domain", "social_acknowledgement", "repeat_intent", "rephrase_intent", "repair_decimal_spacing", "round_weather_temperatures", "complete_speakable_sentence", "direct_file_request", "playback_request", "media_identity_signal", "media_acquisition_language", "media_goal_request", "media_status_question", "media_nouns_for_status", "media_title_status_signal", "media_status_display_title", "is_confirmation", "store_provenance", "provenance_question"}
def is_needed_assignment(node):
    targets = getattr(node, "targets", [])
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return isinstance(node, (ast.Assign, ast.AnnAssign)) and any(getattr(target, "id", None) in needed for target in targets)


nodes = [node for node in tree.body if getattr(node, "name", None) in needed or is_needed_assignment(node)]
namespace = {"json": json, "re": re, "time": time, "provenance": {}}
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
contextual_entity_resolution = namespace["contextual_entity_resolution"]
is_repair_turn = namespace["is_repair_turn"]
repair_route_text = namespace["repair_route_text"]
repeat_intent = namespace["repeat_intent"]
rephrase_intent = namespace["rephrase_intent"]
repair_decimal_spacing = namespace["repair_decimal_spacing"]
round_weather_temperatures = namespace["round_weather_temperatures"]
complete_speakable_sentence = namespace["complete_speakable_sentence"]
direct_structured_answer = namespace["direct_structured_answer"]
media_plan_response = namespace["media_plan_response"]
is_confirmation = namespace["is_confirmation"]
store_provenance = namespace["store_provenance"]
provenance_question = namespace["provenance_question"]
current_external_question = namespace["current_external_question"]
explicit_web_search_request = namespace["explicit_web_search_request"]
historical_camera_question = namespace["historical_camera_question"]
historical_camera_window = namespace["historical_camera_window"]
direct_file_request = namespace["direct_file_request"]
playback_request = namespace["playback_request"]
media_goal_request = namespace["media_goal_request"]
media_status_question = namespace["media_status_question"]
media_nouns_for_status = namespace["media_nouns_for_status"]
media_title_status_signal = namespace["media_title_status_signal"]
media_status_display_title = namespace["media_status_display_title"]


def test_download_followup_uses_recorded_sources():
    expected = ["qbittorrent", "sonarr", "radarr", "lidarr", "slskd", "torbox"]
    assert [SOURCE_NAMES[name] for name in expected] == ["qBittorrent", "Sonarr", "Radarr", "Lidarr", "Slskd", "Torbox"]


def test_lidar_alias_resolves_to_canonical_lidarr():
    assert preflight_plan("Can you restart LIDAR?") == [("restart_container", {"name": "lidarr"})]


def test_confirmation_target_is_canonical_in_plan():
    assert preflight_plan("Restart Lidarr") == [("restart_container", {"name": "lidarr"})]


def test_natural_media_acquisition_language_resolves_to_planner():
    variants = [
        "Get me Dumb and Dumber from 1994.",
        "Give me Dumb and Dumber from 1994.",
        "Grab me Dumb and Dumber from 1994.",
        "Can you add Dumb and Dumber from 1994?",
        "Put Dumb and Dumber from 1994 on Plex.",
        "I want Dumb and Dumber from 1994.",
        "Find Dumb and Dumber from 1994 for me.",
    ]
    for text in variants:
        assert media_goal_request(text)
        assert explicit_domain(text) == "media"
        assert preflight_plan(text) == [("media_plan_goal", {"goal": text})]


def test_bounded_docker_stt_repair_preserves_server_routing():
    repaired = routing_aliases("me to dock or run and count.")
    assert "Docker" in repaired
    assert preflight_plan(repaired) == [("list_containers", {"status": "running"})]
    assert routing_aliases("The boat is near the dock.") == "The boat is near the dock."
    assert not provenance_question("What Docker services are up?")
    assert provenance_question("Which services did you check?")


def test_not_found_media_status_retains_domain_for_next_title_followup():
    conversation_context["media-not-found"] = {"domain": "media", "group": "media"}
    store_provenance("media-not-found", [{
        "tool": "media_status", "status": "ok",
        "result": {"found": False, "status": "NOT_FOUND", "query": "The Hobbit"},
    }])
    state = conversation_context["media-not-found"]
    assert state["domain"] == "media"
    assert state["latest_media_status"]["status"] == "NOT_FOUND"
    assert preflight_plan("What about Dumb and Dumber?", state) == [("media_status", {"query": "What about Dumb and Dumber?"})]


def test_explicit_container_domain_beats_repair_inheritance():
    conversation_context["repair-domain"] = {"domain": "weather", "group": "internet", "last_route_text": "weather today"}
    state = turn_context("repair-domain", "I mean, containers are stopped.")
    assert state["domain"] == "server"
    assert preflight_plan("I mean, containers are stopped.", state) == [("list_containers", {"status": "exited"})]


def test_direct_file_and_playback_requests_do_not_become_acquisition():
    assert direct_file_request("Send me the Dumb and Dumber movie file here.")
    assert direct_file_request("Upload Dumb and Dumber into this chat.")
    assert not media_goal_request("Send me the Dumb and Dumber movie file here.")
    assert not media_goal_request("Upload Dumb and Dumber into this chat.")
    assert playback_request("Play Dumb and Dumber.")
    assert not media_goal_request("Play Dumb and Dumber.")


def test_media_status_questions_use_live_status_capability():
    assert media_status_question("How is The Hobbit doing?")
    assert preflight_plan("How is The Hobbit doing?") == [("media_status", {"query": "How is The Hobbit doing?"})]
    assert preflight_plan("dumb and dumber downloaded") == [("media_status", {"query": "dumb and dumber downloaded"})]
    assert preflight_plan("at the hobbit finish") == [("media_status", {"query": "at the hobbit finish"})]
    assert preflight_plan("as the Hobbit Radiumplex") == [("media_status", {"query": "as the Hobbit Radiumplex"})]
    assert preflight_plan("dumb and dumb to get found") == [("media_status", {"query": "dumb and dumb to get found"})]
    assert preflight_plan("dumb and dumb already") == [("media_status", {"query": "dumb and dumb already"})]
    assert preflight_plan("I watch The Hobbit now") == [("media_status", {"query": "I watch The Hobbit now"})]
    assert preflight_plan("I watched The Hobbit now") == [("media_status", {"query": "I watched The Hobbit now"})]
    assert preflight_plan("Gotta watch The Hobbit now") == [("media_status", {"query": "Gotta watch The Hobbit now"})]
    assert preflight_plan("Is Dumb and Dumber ready in Plex?") == [("media_status", {"query": "Is Dumb and Dumber ready in Plex?"})]
    assert media_title_status_signal("as the 10th kingdom ready")
    assert preflight_plan("as the 10th kingdom ready") == [("media_status", {"query": "as the 10th kingdom ready"})]
    assert preflight_plan("that's the 10th kingdom ready") == [("media_status", {"query": "that's the 10th kingdom ready"})]
    assert preflight_plan("has the 10th kingdom ready") == [("media_status", {"query": "has the 10th kingdom ready"})]
    assert media_title_status_signal("How is Dumb and Dumber doing?")
    assert preflight_plan("How is Dumb and Dumber doing?") == [("media_status", {"query": "How is Dumb and Dumber doing?"})]
    assert preflight_plan("I was Dumb and Dumber doing") == [("media_status", {"query": "I was Dumb and Dumber doing"})]


def test_generic_media_and_local_status_variants_route_deterministically():
    assert preflight_plan("What's new in Plex?") == [("plex_recently_added", {"limit": 1})]
    assert preflight_plan("Show me the latest Plex addition.") == [("plex_recently_added", {"limit": 1})]
    assert preflight_plan("Which movie did Plex add last?") == [("plex_recently_added", {"limit": 1})]
    assert preflight_plan("How cold is it outside today?") == [("weather_forecast", {"location": None, "days_from_now": 0})]
    assert preflight_plan("I have enough stores left.") == [("get_storage_status", {})]
    assert preflight_plan("What is happening with The 10th Kingdom?") == [
        ("media_status", {"query": "What is happening with The 10th Kingdom?"})
    ]
    assert preflight_plan("Where's Dumb and Dumber in the pipeline?") == [
        ("media_status", {"query": "Where's Dumb and Dumber in the pipeline?"})
    ]


def test_recent_front_door_events_are_local_history_not_web():
    assert preflight_plan("Show me recent front door events.")[0][0] == "frigate_recent_events"
    assert preflight_plan("Can you check the camera right now?") == [("frigate_snapshot", {"camera": "front_door"})]
    assert preflight_plan("What is happening at the front door?") == [("frigate_snapshot", {"camera": "front_door"})]
    assert preflight_plan("What happened today in Canada?") == [("web_search", {"query": "What happened today in Canada?"})]


def test_plex_recency_repairs_are_bounded_to_explicit_library_context():
    assert preflight_plan("Show me the latest Plex edition.") == [("plex_recently_added", {"limit": 1})]
    assert preflight_plan("Which movie did Plex add last?") == [("plex_recently_added", {"limit": 1})]
    assert "edition" in routing_aliases("The special edition is missing")
    assert preflight_plan("Show me the latest flex edition.") == [("plex_recently_added", {"limit": 1})]
    assert "flex" in routing_aliases("The flex setting is comfortable")


def test_voice_current_info_repair_is_bounded_to_online_development_language():
    assert current_external_question("online for the latest in video development")
    assert not current_external_question("The online video is playing")


def test_media_status_does_not_turn_acquisition_language_into_status():
    assert preflight_plan("Get Dumb and Dumber from 1994.") == [("media_plan_goal", {"goal": "Get Dumb and Dumber from 1994."})]


def test_media_status_not_found_cannot_become_a_model_claim():
    answer = direct_structured_answer(
        "Is The 10th Kingdom ready?",
        [{"tool": "media_status", "status": "ok", "result": {"found": False, "status": "NOT_FOUND", "query": "The 10th Kingdom"}}],
    )
    assert media_status_display_title({"query": "Is The 10th Kingdom ready?"}, "") == "The 10th Kingdom"
    assert answer == "I don't have a tracked request for The 10th Kingdom yet."


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


def test_politics_today_is_web_not_camera():
    assert current_external_question("Can you give me a rundown of what happened today in American politics?")
    assert preflight_plan("Can you give me a rundown of what happened today in American politics?") == [("web_search", {"query": "Can you give me a rundown of what happened today in American politics?"})]


def test_explicit_web_search_retries_unresolved_topic():
    conversation_context.clear()
    turn_context("web", "What happened today in American politics?")
    assert explicit_web_search_request("Can't you do a web search?")
    context = turn_context("web", "Can't you do a web search?")
    assert preflight_plan("Can't you do a web search?", context) == [("web_search", {"query": "What happened today in American politics?"})]


def test_historical_camera_language_uses_bounded_events():
    text = "A little over an hour ago, there were two camera events at the front door."
    assert historical_camera_question(text)
    plan = preflight_plan(text)
    assert plan[0][0] == "frigate_recent_events"
    assert plan[0][1]["camera"] == "front_door"
    assert "since" in plan[0][1] and "until" in plan[0][1]


def test_event_activity_followup_uses_event_id():
    context = {"domain": "camera", "group": "cameras", "latest_event_id": "event-123"}
    assert preflight_plan("What were they doing?", context) == [("frigate_event_activity", {"event_id": "event-123"})]


def test_historical_camera_zero_results_never_fall_back_to_live_snapshot():
    assert preflight_plan("What were they wearing?", {"domain": "camera", "group": "cameras"}) == []


def test_recent_frigate_event_does_not_prove_current_presence():
    result = {"events": [{"label": "person", "camera": "front_door", "age_seconds": 30, "active": False}]}
    answer = grounded_camera_presence_answer(result)
    assert "30 seconds ago" in answer
    assert "aren't there now" in answer


def test_somebody_front_door_phrase_uses_frigate_events():
    assert preflight_plan("Is somebody at my front door?")[0][0] == "frigate_recent_events"


def test_explicit_live_front_door_language_uses_current_snapshot():
    assert preflight_plan("What's happening at the front door right now?") == [
        ("frigate_snapshot", {"camera": "front_door"})
    ]


def test_retained_media_workflow_status_outranks_new_media_plan():
    context = {"latest_media_workflow": {"workflow_id": "wf-8467"}}
    assert preflight_plan("How is Dumb and Dumber doing?", context) == [
        ("media_status", {"workflow_id": "wf-8467"})
    ]


def test_media_what_about_followup_keeps_new_title_as_status_query():
    context = {"latest_media_workflow": {"workflow_id": "wf-1362"}}
    assert preflight_plan("What about Dumb and Dumber?", context) == [
        ("media_status", {"query": "What about Dumb and Dumber?"})
    ]
    assert preflight_plan("What about Dumb and Dumber?", {"domain": "media"}) == [
        ("media_status", {"query": "What about Dumb and Dumber?"})
    ]


def test_container_followup_maps_stopped_to_exited_without_crashing():
    context = {"referent_type": "containers"}
    assert preflight_plan("What have I stopped?", context) == [
        ("list_containers", {"status": "exited"})
    ]


def test_storage_stt_repair_is_bounded_to_capacity_questions():
    assert routing_aliases("How much stores do I have left?") == "How much storage do I have left?"
    assert routing_aliases("The stores are closed") == "The stores are closed"


def test_active_frigate_event_can_ground_current_presence():
    result = {"events": [{"label": "person", "camera": "front_door", "age_seconds": 2, "active": True}]}
    assert grounded_camera_presence_answer(result) == "Yeah, someone's at the front door."


def test_explicit_weather_location_beats_old_context():
    assert weather_location_from_text("And what's the current weather in Welland, Ontario?") == "Welland, Ontario"
    assert preflight_plan("And what's the current weather in Welland, Ontario?") == [("weather_forecast", {"location": "Welland, Ontario", "days_from_now": 0})]


def test_temperature_outside_routes_to_weather():
    plan = preflight_plan("What's the temperature outside in Welland?")
    assert plan == [("weather_forecast", {"location": "Welland", "days_from_now": 0})]


def test_home_weather_can_use_configured_default():
    assert preflight_plan("What's the weather?") == [("weather_forecast", {"location": None, "days_from_now": 0})]
    assert weather_location_from_text("Uh, could you check the weather for me?") is None


def test_simple_structured_reads_bypass_synthesis_pass():
    weather = {"source": "Open-Meteo", "days_from_now": 0, "temperature_unit": "C",
               "location": {"name": "Welland"}, "current": {"temperature_2m": 20, "weather_code": 0}}
    assert direct_structured_answer("What's the weather?", [{"tool": "weather_forecast", "status": "ok", "result": weather}]) == "It's about 20 degrees Celsius in Welland with clear skies."
    assert direct_structured_answer("What music is Lidarr looking for?", [{"tool": "lidarr_missing_tracks", "status": "ok", "result": {"count": 143}}]) == "Lidarr is currently looking for 143 albums."


def test_media_diagnosis_is_human_and_stops_at_proven_boundary():
    result = {"title": "Dumb and Dumber", "canonical_state": "NO_CANDIDATE",
              "diagnosis": "NO_ACCEPTABLE_CANDIDATE", "canonical_identity": {"title": "Dumb and Dumber"}}
    assert direct_structured_answer("why is it stuck", [{"tool": "media_diagnose", "status": "ok", "result": result}]) == "I couldn't find a suitable copy of Dumb and Dumber."


def test_unresolved_media_plan_cannot_claim_request_started():
    result = [{"tool": "media_plan_goal", "status": "ok", "result": {
        "goal": {"media_type": "movie", "title_query": "10th Kingdom"},
        "canonical_identity": None, "current_state": "UNKNOWN",
        "writes_required": [], "confirmation_required": False,
    }}]
    answer = media_plan_response("Please request the 10th Kingdom movie.", result)
    assert answer is not None
    assert "started" not in answer.casefold()


def test_media_plan_error_cannot_fall_through_to_qwen():
    result = [{"tool": "media_plan_goal", "status": "error", "result": {"reason": "AMBIGUOUS_IDENTITY"}}]
    answer = media_plan_response("Get the 10th Kingdom.", result)
    assert answer == "I couldn't identify one confident media match without changing anything."


def test_actionable_media_plan_is_left_for_confirmation_path():
    result = [{"tool": "media_plan_goal", "status": "ok", "result": {
        "goal": {"media_type": "movie"},
        "canonical_identity": {"title": "Dumb and Dumber", "year": 1994, "tmdb_id": 8467},
        "current_state": "IDENTIFIED",
        "writes_required": [{"owner": "cli_debrid", "capability": "media.standard_request"}],
        "confirmation_required": True,
    }}]
    assert media_plan_response("Get Dumb and Dumber from 1994.", result) is None


def test_common_affirmations_are_confirmation_candidates_but_scope_is_elsewhere():
    for text in ("please do", "okay", "I confirm", "yeah, go for it", "go ahead"):
        assert is_confirmation(text)
    for text in ("maybe", "what happens if I do?", "hold on", "not yet"):
        assert not is_confirmation(text)


def test_external_blackhawk_topic_overrides_camera_context():
    conversation_context["blackhawk"] = {"domain": "camera", "group": "cameras", "camera": "front_door"}
    text = resolved_followup_text("blackhawk", "I heard something about a Blackhawk flying over Toronto. Look into that for me.")
    assert "front door camera" not in text.casefold()
    assert preflight_plan(text)[0][0] == "web_search"


def test_recently_added_is_plex_only():
    assert preflight_plan("What's the last thing added to Plex?") == [("plex_recently_added", {"limit": 1})]


def test_weather_followup_keeps_location_without_topic_contamination():
    assert preflight_plan("What is the weather tomorrow?") == [("weather_forecast", {"location": None, "days_from_now": 1})]


def test_noisy_weather_followup_preserves_active_location():
    plan = preflight_plan(
        "Yeah, just what time with the weather isn't well in Ontario",
        {"domain": "weather", "kind": "weather", "location": "Welland, Ontario"},
    )
    assert plan == [("weather_forecast", {"location": "Welland, Ontario", "days_from_now": 0})]


def test_container_summary_is_status_specific():
    answer = evidence_supported_answer(
        "Your server currently has 61 containers.",
        "How many containers are running?",
        [{"tool": "list_containers", "status": "ok", "result": {
            "count": 49, "status_filter": "running",
            "summary": {"total": 61, "running": 49, "stopped": 12, "paused": 0, "restarting": 0, "dead": 0},
        }}],
        "server",
    )
    assert answer == "You've got 49 containers running."


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


def test_container_running_followup_preserves_referent():
    context = {"domain": "server", "referent_type": "containers"}
    assert preflight_plan("How many are running?", context) == [("list_containers", {"status": "running"})]


def test_front_door_alerts_use_events_not_stats():
    assert preflight_plan("Any alerts from our front camera?")[0][0] == "frigate_recent_events"


def test_event_image_followup_preserves_event_id():
    context = {"domain": "camera", "group": "cameras", "camera": "front_door", "latest_event_id": "event-123"}
    assert resolved_followup_text("scenario", "Describe the image from that detection.") == "Describe the image from that detection."
    assert preflight_plan("describe the event image for event event-123", context) == [("frigate_event_snapshot", {"event_id": "event-123"})]


def test_plex_acquisition_followup_uses_download_investigation():
    context = {"domain": "media", "referent_type": "plex_movies"}
    assert preflight_plan("Anything being added or looked for?", context) == [("investigate_downloads", {})]


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


def test_generic_media_repair_reuses_previous_pipeline_request():
    conversation_context.clear()
    turn_context("scenario", "Is anything in litter going to Plex?")
    conversation_context["scenario"]["last_route_text"] = "Is anything in Lidarr going to Plex?"
    assert is_repair_turn("Yeah, I meant Lidarr.")
    repaired = repair_route_text("Yeah, I meant Lidarr.", conversation_context["scenario"])
    assert "Lidarr" in repaired and preflight_plan(repaired)[0][0] == "investigate_media_pipeline"
    assert is_repair_turn("Yeah, I'm at Lidar.")
    repaired_stt = repair_route_text("Yeah, I'm at Lidar.", conversation_context["scenario"])
    assert preflight_plan(repaired_stt)[0][0] == "investigate_media_pipeline"


def test_repair_preserves_weather_and_replaces_location():
    conversation_context.clear()
    turn_context("scenario", "Check tomorrow's weather in Toronto.")
    conversation_context["scenario"]["last_route_text"] = "weather in Toronto tomorrow"
    repaired = repair_route_text("Sorry, I meant Welland.", conversation_context["scenario"])
    assert repaired == "weather in Welland tomorrow"
    assert preflight_plan(repaired) == [("weather_forecast", {"location": "Welland", "days_from_now": 1})]


def test_weather_location_ignores_repeated_whisper_question_tail():
    assert weather_location_from_text("What is the weather in Toronto, what is the weather in Toronto, what is the weather in") == "Toronto"
    assert weather_location_from_text("What is the weather in Toronto, Ontario, what is the weather in Toronto") == "Toronto, Ontario"


def test_contextual_flux_resolution_is_not_global():
    assert contextual_entity_resolution("What is magnetic flux?")["text"] == "What is magnetic flux?"
    assert contextual_entity_resolution("What causes dental plaques?")["text"] == "What causes dental plaques?"
    resolved = contextual_entity_resolution("What's new on flux?", {"domain": "media"})
    assert "Plex" in resolved["text"]
    assert "Lidarr" in routing_aliases("Anything in litter eventually going to Plex?")


def test_lists_use_bounded_deterministic_tools():
    assert preflight_plan("Put milk on my grocery list") == [("add_list_items", {"list": "grocery", "item": "milk"})]
    assert preflight_plan("But milk on my grocery list") == [("add_list_items", {"list": "grocery", "item": "milk"})]
    assert preflight_plan("What's on my grocery list?") == [("list_items", {"list": "grocery"})]
    assert preflight_plan("Remove milk from my grocery list") == [("remove_list_item", {"list": "grocery", "item": "milk"})]


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


def test_repeat_is_deterministic_and_does_not_mean_refresh():
    assert repeat_intent("Say that again.")
    assert repeat_intent("Sorry, say that one more time.")
    assert repeat_intent("Repeat what you said.")
    assert not repeat_intent("Check that again.")
    assert not rephrase_intent("Say that again.")


def test_rephrase_is_separate_from_repeat():
    assert rephrase_intent("Say that another way.")
    assert rephrase_intent("Can you make that simpler?")
    assert rephrase_intent("What do you mean?")
    assert not repeat_intent("Explain that again.")


def test_decimal_spacing_repair_preserves_versions_and_ips():
    assert repair_decimal_spacing("18. 9 degrees") == "18.9 degrees"
    assert repair_decimal_spacing("7. 1 GB") == "7.1 GB"
    assert repair_decimal_spacing("7.3.2") == "7.3.2"
    assert repair_decimal_spacing("192.168.40.44") == "192.168.40.44"
    assert repair_decimal_spacing("The temperature is 18. 9. Tomorrow will be warmer.") == "The temperature is 18.9. Tomorrow will be warmer."


def test_weather_rounding_is_spoken_only_and_exact_requests_are_preserved():
    assert round_weather_temperatures("It is 18.9 degrees in Welland.", "What's the weather in Welland?") == "It is 19 degrees in Welland."
    assert round_weather_temperatures("It is 18.9 degrees.", "What's the exact temperature?") == "It is 18.9 degrees."


def test_streaming_does_not_split_numeric_periods():
    assert not complete_speakable_sentence("The temperature is 18.")
    assert not complete_speakable_sentence("Version 7.3.")
    assert not complete_speakable_sentence("The host is 192.168.40.44.")
    assert complete_speakable_sentence("The temperature is 18.9 degrees.")
    assert complete_speakable_sentence("Tomorrow will be warmer.")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("grounding regressions passed")
