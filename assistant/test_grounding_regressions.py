"""Regression checks for live grounding, provenance, camera safety, and aliases."""

import ast
import json
import re
import time
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("voice-api-app.py").read_text())
needed = {"SOURCE_NAMES", "ARTIST_ALIASES", "DOMAIN_ENTITIES", "artist_from_speech", "visual_question", "activity_question", "front_door_presence_question", "current_camera_presence_question", "grounded_recent_activity_answer", "historical_timing_question", "grounded_event_timing_answer", "dynamic_fact_question", "current_external_question", "explicit_web_search_request", "historical_camera_question", "historical_camera_window", "plex_query_from_speech", "investigation_query_from_speech", "deterministic_plan", "preflight_plan", "evidence_supported_answer", "grounded_camera_presence_answer", "direct_structured_answer", "media_plan_response", "routing_aliases", "contextual_entity_resolution", "is_repair_turn", "repair_route_text", "weather_location_from_text", "explicit_topic", "turn_context", "resolved_followup_text", "conversation_context", "explicit_domain", "social_acknowledgement", "underspecified_read_request", "repeat_intent", "rephrase_intent", "repair_decimal_spacing", "round_weather_temperatures", "complete_speakable_sentence", "direct_file_request", "playback_request", "media_identity_signal", "media_acquisition_language", "media_goal_request", "media_status_question", "media_nouns_for_status", "media_title_status_signal", "retained_media_status_repair", "media_status_display_title", "is_confirmation", "store_provenance", "provenance_question", "ambiguous_container_status_followup", "all_live_results_failed", "discovery_question", "_tokens_for_discovery", "_DISCOVERY_QUESTION_PATTERNS", "_DISCOVERY_QUESTION_STOPWORDS", "_media_title_candidate_words", "_MEDIA_CATEGORY_WORDS", "_MEDIA_QUESTION_SCAFFOLDING", "plex_query_from_speech", "guess_media_title", "fresh_title_restatement", "media_intent", "media_library_query", "_descriptive_media_clue", "natural_weather_summary"}
def is_needed_assignment(node):
    targets = getattr(node, "targets", [])
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return isinstance(node, (ast.Assign, ast.AnnAssign)) and any(getattr(target, "id", None) in needed for target in targets)


nodes = [node for node in tree.body if getattr(node, "name", None) in needed or is_needed_assignment(node)]
from semantic_routing import has_referential_language

namespace = {"json": json, "re": re, "time": time, "provenance": {}, "has_referential_language": has_referential_language}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "voice-api-app.py", "exec"), namespace)
SOURCE_NAMES = namespace["SOURCE_NAMES"]
evidence_supported_answer = namespace["evidence_supported_answer"]
preflight_plan = namespace["preflight_plan"]
visual_question = namespace["visual_question"]
grounded_camera_presence_answer = namespace["grounded_camera_presence_answer"]
grounded_recent_activity_answer = namespace["grounded_recent_activity_answer"]
historical_timing_question = namespace["historical_timing_question"]
grounded_event_timing_answer = namespace["grounded_event_timing_answer"]
routing_aliases = namespace["routing_aliases"]
weather_location_from_text = namespace["weather_location_from_text"]
turn_context = namespace["turn_context"]
resolved_followup_text = namespace["resolved_followup_text"]
conversation_context = namespace["conversation_context"]
explicit_domain = namespace["explicit_domain"]
social_acknowledgement = namespace["social_acknowledgement"]
underspecified_read_request = namespace["underspecified_read_request"]
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
ambiguous_container_status_followup = namespace["ambiguous_container_status_followup"]
all_live_results_failed = namespace["all_live_results_failed"]
media_goal_request = namespace["media_goal_request"]
media_status_question = namespace["media_status_question"]
media_nouns_for_status = namespace["media_nouns_for_status"]
media_title_status_signal = namespace["media_title_status_signal"]
retained_media_status_repair = namespace["retained_media_status_repair"]
media_status_display_title = namespace["media_status_display_title"]
plex_query_from_speech = namespace["plex_query_from_speech"]
guess_media_title = namespace["guess_media_title"]
fresh_title_restatement = namespace["fresh_title_restatement"]
media_intent = namespace["media_intent"]
media_library_query = namespace["media_library_query"]
_descriptive_media_clue = namespace["_descriptive_media_clue"]


def test_download_followup_uses_recorded_sources():
    expected = ["qbittorrent", "sonarr", "radarr", "lidarr", "slskd", "torbox"]
    assert [SOURCE_NAMES[name] for name in expected] == ["qBittorrent", "Sonarr", "Radarr", "Lidarr", "Slskd", "Torbox"]


def test_damaged_media_status_uses_retained_workflow_only():
    context = {"latest_media_workflow": {"workflow_id": "wf-1"}, "domain": "media"}
    for damaged in ("I was dumb in Dumberdorn.", "That was Dumb and Dumberdorn.", "It is dumb in Dumberdorn."):
        assert retained_media_status_repair(damaged, context)
        assert preflight_plan(damaged, context) == [("media_status", {"workflow_id": "wf-1"})]
    assert not retained_media_status_repair(damaged, {})


def test_damaged_media_status_cannot_override_explicit_new_domain():
    context = {"latest_media_workflow": {"workflow_id": "wf-1"}, "domain": "media"}
    assert not retained_media_status_repair("I was thinking about politics today.", context)


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


def test_media_request_to_plex_server_beats_generic_server_domain():
    text = "I want to add a TV show to my Plex server, can you request Sagwa, the Chinese Siamese Cat?"
    assert explicit_domain(text, {"domain": "server"}) == "media"
    assert preflight_plan(text) == [("media_plan_goal", {"goal": text})]


def test_bare_recent_question_requires_a_scope_instead_of_tool_fanout():
    clarification = underspecified_read_request("What's the most recent?", {"domain": "server", "referent_type": "containers"})
    assert clarification and "most recent" in clarification
    assert underspecified_read_request("What's the latest news?", {"domain": "server"}) is None


def test_confirmation_without_pending_action_is_not_routed_to_a_writer():
    assert is_confirmation("Yeah, let's add it")
    assert is_confirmation("Yeah, go for it")
    assert social_acknowledgement("Got it")


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


def test_past_tense_media_status_frame_keeps_title_status_route():
    assert media_status_question("what was happening with the 10th kingdom.")
    assert preflight_plan("what was happening with the 10th kingdom.") == [("media_status", {"query": "what was happening with the 10th kingdom."})]


def test_container_followup_repairs_whisper_stops_variant():
    conversation_context["container-stops"] = {"domain": "server", "referent_type": "containers", "group": "server"}
    route = resolved_followup_text("container-stops", "What about stops?")
    assert route == "how many containers are stopped"
    assert preflight_plan(route, conversation_context["container-stops"]) == [("list_containers", {"status": "exited"})]


def test_web_attempt_cannot_be_described_as_no_web_access():
    no_results = [{"tool": "web_search", "status": "ok", "result": {"results": []}}]
    assert evidence_supported_answer("I don't have access to live news.", "What happened today?", no_results) == "I searched the web, but I couldn't find reliable current results."
    with_results = [{"tool": "web_search", "status": "ok", "result": {"results": [{"title": "A current source"}]}}]
    assert evidence_supported_answer("I don't have access to live news.", "What happened today?", with_results) == "I found current web results, but I couldn't synthesize a reliable summary from them yet."


def test_direct_file_and_playback_requests_do_not_become_acquisition():
    assert direct_file_request("Send me the Dumb and Dumber movie file here.")
    assert direct_file_request("Upload Dumb and Dumber into this chat.")
    assert not media_goal_request("Send me the Dumb and Dumber movie file here.")
    assert not media_goal_request("Upload Dumb and Dumber into this chat.")
    assert playback_request("a movie inside this conversation")
    assert not media_goal_request("a movie inside this conversation")


def test_total_live_tool_failure_cannot_become_model_grounded_answer():
    failed = [{"tool": "web_search", "status": "timeout", "result": {"error_code": "TIMEOUT", "evidence_available": False}}]
    assert all_live_results_failed(failed)
    assert not all_live_results_failed([{"tool": "web_search", "status": "ok", "result": {"results": []}}])
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
    assert preflight_plan("Show me recent front door events.")[0][0] == "frigate_recent_activity"
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
    assert plan[0][0] == "frigate_recent_activity"
    assert plan[0][1]["camera"] == "front_door"
    assert plan[0][1]["latest_only"] is False
    assert "since" in plan[0][1] and "until" in plan[0][1]


def test_generic_recent_front_door_question_selects_latest_review_only():
    plan = preflight_plan("Has anything happened at the front door recently?")
    assert plan == [
        ("frigate_recent_activity", {
            "camera": "front_door", "label": "person", "limit": 1,
            "latest_only": True, "since": plan[0][1]["since"], "until": plan[0][1]["until"]
        })
    ]


def test_event_activity_followup_uses_event_id():
    context = {"domain": "camera", "group": "cameras", "latest_event_id": "event-123"}
    assert preflight_plan("What were they doing?", context) == [("frigate_activity_details", {"event_id": "event-123"})]


def test_event_timing_followup_uses_event_scoped_normalized_evidence():
    context = {"domain": "camera", "group": "cameras", "latest_event_id": "event-123"}
    assert preflight_plan("How long were they there?", context) == [
        ("frigate_activity_details", {"event_id": "event-123"})
    ]
    assert preflight_plan("What time was that?", context) == [
        ("frigate_activity_details", {"event_id": "event-123"})
    ]


def test_current_presence_followup_switches_from_history_to_live_snapshot():
    context = {"domain": "camera", "group": "cameras", "latest_event_id": "event-123"}
    assert preflight_plan("Are they still there?", context) == [
        ("frigate_snapshot", {"camera": "front_door"})
    ]


def test_historical_appearance_never_uses_current_snapshot():
    context = {"domain": "camera", "group": "cameras", "latest_event_id": "event-123"}
    assert preflight_plan("What did they look like?", context) == [
        ("frigate_event_snapshot", {"event_id": "event-123"})
    ]


def test_historical_time_followup_never_uses_current_datetime():
    context = {"domain": "camera", "group": "cameras", "latest_event_id": "event-123"}
    plan = preflight_plan("What time was that?", context)
    assert plan == [("frigate_activity_details", {"event_id": "event-123"})]
    assert all(name != "current_datetime" for name, _ in plan)


def test_latest_activity_answer_separates_age_from_duration():
    answer = grounded_recent_activity_answer({
        "latest_only": True,
        "reviews": [{
            "time": {
                "start": {"relative_time": "about 10 minutes ago"},
                "duration_seconds": 8,
            },
            "genai": {"shortSummary": "a person moved through the foyer for several seconds."},
        }],
    })
    assert "about 10 minutes ago" in answer
    assert "8" not in answer  # the concise GenAI summary is used here
    assert "10 minutes" not in answer.split("for")[-1]


def test_historical_clock_answer_uses_event_normalized_time():
    assert historical_timing_question("What time was that?")
    answer = grounded_event_timing_answer({
        "events": [{"time": {
            "start": {"display": "2026-09-15 03:23:40 PM"},
            "end": {"display": "2026-09-15 03:23:51 PM"},
        }}],
        "genai": {"scene": "around 02:23 PM"},
    })
    assert "03:23:40 PM" in answer
    assert "02:23 PM" not in answer


def test_resolved_event_activity_followup_beats_historical_search():
    context = {"domain": "camera", "group": "cameras", "latest_event_id": "event-123"}
    assert preflight_plan("analyze activity for event event-123 from camera front_door", context) == [
        ("frigate_activity_details", {"event_id": "event-123"})
    ]


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


def test_camera_context_maps_outside_now_to_live_snapshot():
    context = {"domain": "camera", "group": "cameras", "camera": "front_door"}
    conversation_context["camera-outside"] = context
    routed = resolved_followup_text("camera-outside", "What's happening outside now?")
    assert preflight_plan(routed, context) == [("frigate_snapshot", {"camera": "front_door"})]


def test_retained_media_diagnosis_beats_generic_status():
    context = {"latest_media_workflow": {"workflow_id": "wf-8467"}}
    assert preflight_plan("Why isn't it ready?", context) == [
        ("media_diagnose", {"workflow_id": "wf-8467"})
    ]


def test_server_status_is_a_bounded_read():
    assert preflight_plan("What is the server status?") == [("list_containers", {})]


def test_asr_where_its_title_stays_a_media_status_read():
    assert preflight_plan("out where it's dumb and dumber") == [
        ("media_status", {"query": "out where it's dumb and dumber"})
    ]


def test_asr_so_what_about_start_stays_ambiguous():
    context = {"referent_type": "containers"}
    assert ambiguous_container_status_followup("So what about start?", context)


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
    assert preflight_plan("About Dumb and Dumber?", {"domain": "media"}) == [
        ("media_status", {"query": "About Dumb and Dumber?"})
    ]


def test_container_followup_maps_stopped_to_exited_without_crashing():
    context = {"referent_type": "containers"}
    assert preflight_plan("What have I stopped?", context) == [
        ("list_containers", {"status": "exited"})
    ]


def test_asr_start_stopped_collision_fails_closed_to_clarification():
    context = {"referent_type": "containers"}
    assert ambiguous_container_status_followup("What about start?", context) is True
    assert ambiguous_container_status_followup("Start Plex", context) is False
    assert ambiguous_container_status_followup("What about stopped?", context) is False


def test_storage_stt_repair_is_bounded_to_capacity_questions():
    assert routing_aliases("How much stores do I have left?") == "How much storage do I have left?"
    assert routing_aliases("The stores are closed") == "The stores are closed"


def test_plex_recency_repair_handles_dropped_opening_frame():
    repaired = routing_aliases("was new in Plex")
    assert repaired == "what's new in Plex"
    assert preflight_plan("was new in Plex") == [("plex_recently_added", {"limit": 1})]


def test_plex_recency_repair_handles_its_new_dropped_frame():
    repaired = routing_aliases("it's new in Plex")
    assert repaired == "what's new in Plex"
    assert preflight_plan("it's new in Plex") == [("plex_recently_added", {"limit": 1})]


def test_standalone_title_where_question_is_media_status_read():
    assert media_title_status_signal("Where's Dumb and Dumber?")
    assert preflight_plan("Where's Dumb and Dumber?") == [
        ("media_status", {"query": "Where's Dumb and Dumber?"})
    ]
    assert not media_title_status_signal("Where is my package?")


def test_title_status_turn_overrides_inherited_weather_domain():
    assert explicit_domain("That's the Hobbit ready.", {"domain": "weather"}) == "media"
    assert preflight_plan("That's the Hobbit ready.", {"domain": "weather"}) == [
        ("media_status", {"query": "That's the Hobbit ready."})
    ]


def test_live_outside_now_does_not_look_like_media_title_status():
    assert not media_title_status_signal("What's happening outside now?")


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


def test_weather_asr_frame_does_not_become_a_location():
    assert weather_location_from_text("for weather today") is None
    assert weather_location_from_text("weather forecast for tomorrow") is None
    assert preflight_plan("for weather today") == [("weather_forecast", {"location": None, "days_from_now": 0})]


def test_simple_structured_reads_bypass_synthesis_pass():
    # Weather is no longer a direct_structured_answer bypass -- it is now
    # intentionally always synthesized by Qwen from the enriched forecast
    # evidence (WEATHER_SYNTHESIS_RULE) for more natural broadcaster-style
    # phrasing, so there is no deterministic string to assert here anymore.
    weather = {"source": "Open-Meteo", "days_from_now": 0, "temperature_unit": "C",
               "location": {"name": "Welland"}, "current": {"temperature_2m": 20, "weather_code": 0}}
    assert direct_structured_answer("What's the weather?", [{"tool": "weather_forecast", "status": "ok", "result": weather}]) is None
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


def test_is_confirmation_tolerates_an_interposed_please():
    """Real production bug: a live end-to-end test showed "Yes, please
    request it." never fullmatched is_confirmation()'s regex -- the
    politeness word "please" interposed between the affirmation and the
    action phrase was not tolerated, so a genuine confirmation reply fell
    through to Qwen's own tool-selection instead of the deterministic
    pending[client_id] path. Generic fix (an optional "please" slot), not
    a hardcode of this one sentence -- tested with several phrasings."""
    for text in ("Yes, please request it.", "Yeah, please add it.", "yes please",
                 "please request it", "please go ahead", "okay please request it", "Yes please"):
        assert is_confirmation(text), text
    # Negative control: an unrelated sentence or a fresh request must not
    # be swept in just because it contains "please" or an action verb.
    for text in ("Please tell me the weather.", "Get me Dumb and Dumber from 1994, please.",
                 "Can you please check the containers?"):
        assert not is_confirmation(text), text


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


def test_media_plan_response_no_title_given_asks_what_to_look_for():
    """Root cause #3, live production bug: "Can you request a movie for
    me?" / "I want to add a movie to my server." used to search a provider
    for garbage text. media_plan_goal now short-circuits with
    current_state=NO_TITLE_GIVEN before any provider is touched; the
    assistant must turn that into an honest question, not the generic
    "not confident" dead end."""
    live_results = [{"tool": "media_plan_goal", "status": "ok", "result": {
        "current_state": "NO_TITLE_GIVEN", "ambiguous": False,
        "message": "I didn't catch a specific title -- what would you like me to look for?",
    }}]
    assert media_plan_response("Can you request a movie for me?", live_results) == \
        "I didn't catch a specific title -- what would you like me to look for?"


def test_media_plan_response_zero_matches_is_honest_not_generic_dead_end():
    """Root cause #1's second half: a genuinely empty match list (not an
    ambiguous tie) must say what was actually searched for, not the vague
    "I couldn't identify a confident media match" dead end."""
    live_results = [{"tool": "media_plan_goal", "status": "ok", "result": {
        "canonical_identity": None, "ambiguous": False, "current_state": "UNKNOWN",
        "goal": {"title_query": "Some Nonexistent Film"},
    }}]
    assert media_plan_response("do I have Some Nonexistent Film", live_results) == \
        "I couldn't find anything called 'Some Nonexistent Film'."


def test_media_plan_response_ambiguous_tie_still_asks_which_one():
    """Negative control: root cause #1's NO_CONFIDENT_MATCH candidates path
    must still produce the existing "did you mean X or Y?" question, not
    the new zero-match message."""
    live_results = [{"tool": "media_plan_goal", "status": "ok", "result": {
        "canonical_identity": None, "ambiguous": True, "ambiguity_reason": "NO_CONFIDENT_MATCH",
        "candidates": [{"title": "The Avengers", "year": "2012"}, {"title": "Avengers: Endgame", "year": "2019"}],
        "goal": {"title_query": "avengers"},
    }}]
    response = media_plan_response("do I have avengers", live_results)
    assert "Which one do you mean?" in response
    assert "The Avengers" in response and "Avengers: Endgame" in response


def test_preflight_plan_routes_browse_shaped_movie_question_to_library_counts():
    """Addendum, live production bug: "do i have any movies on my server?"
    was sent to plex_search with the literal garbled sentence as the query
    (zero real matches, later mislabeled as a tool failure). A browse-shaped
    question with no real title must route to plex_library_counts instead,
    generically -- tested with several phrasings, not just this one
    sentence."""
    for text in ("do i have any movies on my server?", "what movies do i have?",
                 "how many movies do i have", "show me my tv shows"):
        assert preflight_plan(text) == [("plex_library_counts", {})], text


def test_preflight_plan_still_routes_a_real_title_to_plex_search():
    """Negative control: an utterance that DOES name something must not be
    swept into the generic browse/count route -- it goes to whichever
    real-title tool it already routed to (media_plan_goal, since this
    phrasing also reads as acquisition-shaped; a plex-only phrasing below
    proves the plex_search branch itself is unaffected)."""
    plan = preflight_plan("do i have avengers on my plex server?")
    assert plan and plan[0][0] != "plex_library_counts"
    plan2 = preflight_plan("which library is interstellar in")
    assert ("plex_search", {"query": plex_query_from_speech("which library is interstellar in")}) in plan2


def test_status_shaped_compound_sentences_are_not_acquisition_goals():
    """Root cause #2, live production bug: a compound status question that
    ALSO contains acquisition-flavored words ("request", "get", "put") --
    "What's the status of the movie The Room did I request it or get it
    put on my plex server" -- was misclassified as a fresh acquisition goal
    and sent to media_plan_goal with the entire raw sentence as the literal
    title. media_status_question() must outrank acquisition language here.
    Tested generically with several status-shaped phrasings, not just the
    one live sentence."""
    status_shaped = [
        "What's the status of the movie The Room did I request it or get it put on my plex server",
        "Did I ever request Interstellar",
        "Is Whiplash on my server or not",
        "What happened when I asked for Dune",
    ]
    for text in status_shaped:
        assert media_status_question(text), text
        assert not media_goal_request(text), text


def test_status_shaped_sentences_do_not_regress_real_acquisition_requests():
    """Negative control: broadening media_status_question's word lists must
    not turn genuine acquisition requests into status questions."""
    for text in (
        "Can you request the movie The Room?",
        "I want to request the movie Interstellar.",
        "Get me Dumb and Dumber from 1994.",
        "Can you add Dumb and Dumber from 1994?",
    ):
        assert not media_status_question(text), text
        assert media_goal_request(text), text


def test_fresh_title_restatement_recognizes_real_new_titles():
    """Root cause #1: a full, clean restatement of a title -- even with a
    leading correction clause or trailing creator hint -- must be
    recognized as a real title, generically, not per-phrase."""
    assert fresh_title_restatement("Can you give me the movie The Room by Tommy Wiseau?") == "The Room by Tommy Wiseau"
    assert fresh_title_restatement("I want to add a movie called The Room.") == "The Room"
    assert fresh_title_restatement("No, that's not what I mean. I want to add a movie called The Room.") == "The Room"
    assert fresh_title_restatement("The Room, Tommy Wiseau.") == "The Room, Tommy Wiseau"
    assert fresh_title_restatement("room.") == "room"
    assert fresh_title_restatement("Interstellar") == "Interstellar"


def test_fresh_title_restatement_rejects_bare_refinements():
    """Negative control: a bare year/type-only refinement has no title
    content of its own and must not be treated as a fresh title -- these
    stay on enrichment_reply_hint's existing merge-only path."""
    assert fresh_title_restatement("2003.") is None
    assert fresh_title_restatement("the movie") is None
    assert fresh_title_restatement("the 2003 one") is None
    assert fresh_title_restatement("It's a movie from 2003.") is None
    assert fresh_title_restatement("I mean the one from 2003.") is None
    assert fresh_title_restatement("Can you request the movie?") is None


def test_media_intent_classifies_the_five_operation_shapes():
    """Not every sentence with a title means "request this" -- verify the
    aggregated intent classifier correctly separates the five operation
    shapes using the existing, already-tested predicates.

    Note: a BARE title with no movie/show/album word and no year ("Add The
    Room.") is a known, deliberately-not-widened gap in this deterministic
    classifier -- media_identity_signal requires an explicit type word or
    year, and broadening it to accept any bare acquisition-verb + proper
    noun was tried and reverted: it collided with offer-acceptance replies
    ("Get it.", "Yeah, get it.") that also survive the same "real content"
    filter. That class of request still resolves correctly in production
    via the Qwen tool-calling path (media_plan_goal stays in the discovered
    candidate set and Qwen can call it directly), and once inside
    media_plan_goal the unknown-media-type cross-domain search (tools/
    server-tools-app.py) resolves it without requiring the type word --
    this is deterministic PRE-routing only, not the resolver itself."""
    assert media_intent("Can you request the movie The Room?") == "MEDIA_REQUEST"
    assert media_intent("What's the status of The Room? Did I request it already?") == "MEDIA_STATUS"
    assert media_intent("Do I have The Room on Plex?") == "MEDIA_LIBRARY_QUERY"
    assert media_intent("Do you know the movie The Room?") == "MEDIA_DISCOVERY"
    assert media_intent("Play The Room.") == "MEDIA_PLAY"


def test_media_intent_titleless_request_is_still_media_request():
    """"Can you add a movie?" has intent=MEDIA_REQUEST with no title yet --
    that is exactly the NO_TITLE_GIVEN case media_plan_goal already handles
    by asking a clarifying question, not a classification failure."""
    assert media_intent("Can you add a movie?") == "MEDIA_REQUEST"


def test_media_intent_none_for_unrelated_text():
    assert media_intent("What's the weather like today?") is None
    assert media_intent("How many containers are running?") is None


def test_media_status_question_negative_regressions_item_9():
    """Real production bug: media_status_question() incorrectly classified
    descriptive media-identification questions as workflow status checks
    purely because a plot word (e.g. "stuck") collided with legitimate
    download-status vocabulary. None of these describe a KNOWN item's
    current state/progress -- they ask to IDENTIFY an unknown item from a
    description, and must never be MEDIA_STATUS."""
    for text in (
        "What's that movie where Tom Hanks is on an island?",
        "What's the movie where Brad Pitt goes fishing?",
        "What's that show about a chemistry teacher making meth?",
        "What's the name of that movie with dreams inside dreams?",
        "Which movie has Matt Damon growing potatoes on Mars?",
        "What's that Brad Pitt movie about fly fishing in Montana?",
        "What's that Tom Hanks movie where he's stuck on an island with a volleyball?",
        "What's the Robin Williams movie where he dresses up as an old woman?",
    ):
        assert media_status_question(text) is False, text


def test_media_status_question_positive_regressions_item_9():
    """Negative control: real status/progress/availability questions about
    an already-known or referenced item must still correctly classify as
    MEDIA_STATUS -- the descriptive-clue exclusion must not overcorrect
    into blanket suppression."""
    for text in (
        "What's the status of that movie?",
        "How's that movie doing?",
        "Did it finish?",
        "Is it ready yet?",
        "Has it been added?",
        "What's the status of my Avengers request?",
        "How is The Hobbit doing?",
    ):
        assert media_status_question(text) is True, text


def test_descriptive_media_clue_excludes_imperative_verb_plus_service_name():
    """Regression: a bare two-capitalized-word shape alone is not enough
    signal for a person mention -- "Restart Lidarr" must not be mistaken
    for a person's name, which would misroute a container restart into
    media identity resolution."""
    assert preflight_plan("Restart Lidarr") == [("restart_container", {"name": "lidarr"})]


def test_status_request_noun_outranks_descriptive_clue_and_acquisition_language():
    """Real gap found investigating status-tracking (a recurring user pain
    point): a multi-word capitalized movie TITLE ("A River Runs Through
    It", "Cast Away") can match the same two-Title-Case-words shape used
    to detect a person's name, and the word "request" (as a NOUN naming an
    EXISTING request) collided with media_acquisition_language's "request"
    VERB check -- either one alone used to make these unambiguous status
    questions fail to route to the real status capability at all."""
    for text in (
        "How is my A River Runs Through It request going?",
        "Did A River Runs Through It download yet?",
        "Did Cast Away download yet?",
        "is my cast away request done",
        "How is my Cast Away request going?",
        "Has my Interstellar request finished?",
    ):
        assert media_status_question(text) is True, text
        assert preflight_plan(text) == [("media_status", {"query": text})], text


def test_status_request_noun_fix_does_not_regress_descriptive_discovery():
    """Negative control: the "request"-as-noun and title-shape carve-outs
    above must not resurrect the descriptive-discovery misclassification
    fixed two rounds ago."""
    for text in (
        "What's that Tom Hanks movie where he's stuck on an island with a volleyball?",
        "What's that Brad Pitt movie about fly fishing in Montana?",
    ):
        assert media_status_question(text) is False, text
        assert preflight_plan(text) == [("media_plan_goal", {"goal": text})], text


def test_past_tense_verb_form_status_family_reaches_media_status():
    """Fresh-session status classification fix: ESP32-class voice devices
    send every utterance as a brand-new, context-free session, so the
    natural VERB-form phrasing family ("Did I already request X?") must
    classify and route correctly with zero prior turn to lean on. Generic
    tense-based signal (an auxiliary "did/have/has" + I/we, or "was ...
    ever"), not a phrase list -- distinguishes past/perfect tense (status)
    from present/future/modal framing (a fresh request)."""
    for text in (
        "Did I already request Primer?", "Did I request Primer yet?",
        "Have I requested Primer?", "Did I ask for Primer already?",
        "Have I already asked for Primer?", "Was Primer ever requested?",
    ):
        assert media_status_question(text) is True, text
        assert preflight_plan(text) == [("media_status", {"query": text})], text


def test_past_tense_verb_form_fix_does_not_misclassify_fresh_requests():
    """Negative control: present/future/modal framing ("Can I...", "I'd
    like to...", "I want to...") must never be swept into status just
    because it shares the word "request"/"ask" with the past-tense family
    above -- this is the exact false positive the coordinator's own
    negative control demanded ("Can I request Primer?" must NOT become a
    status check on a nonexistent request)."""
    for text in (
        "Can I request Primer?", "I'd like to request Primer.",
        "I want to request Primer.", "Can we request Primer?",
    ):
        assert media_status_question(text) is False, text


def test_bug_a_untyped_media_request_reaches_media_plan_goal():
    """Bug A: an explicit request verb with NO type word at all ("Can you
    request Sagwa The Chinese Siamese Cat") must still reach
    media_plan_goal deterministically -- real production bug, Qwen
    claimed it had no capability to request media at all for this exact
    phrasing because the prior gate required BOTH a request verb AND a
    type-word noun."""
    for text in (
        "Can you request Sagwa The Chinese Siamese Cat",
        "Add Sagwa The Chinese Siamese Cat",
        "Get me Sagwa The Chinese Siamese Cat",
        "I want Sagwa The Chinese Siamese Cat",
    ):
        assert preflight_plan(text) == [("media_plan_goal", {"goal": text})], text


def test_bug_a_fix_does_not_misroute_unrelated_domains_or_bare_offer_replies():
    """Negative controls for Bug A's broadened gate: a different domain's
    own use of these verbs, and a bare pronoun/interjection offer-
    acceptance reply (caught by is_confirmation()/offer-acceptance earlier
    in respond(), never meant to reach this deterministic dispatch at
    all), must not be swept into media_plan_goal."""
    assert preflight_plan("add milk to my grocery list") != [("media_plan_goal", {"goal": "add milk to my grocery list"})]
    for text in ("Get it.", "Yeah, get it.", "No? Then get it.", "Can you find it on the internet?"):
        plan = preflight_plan(text)
        assert not (plan and plan[0][0] == "media_plan_goal"), text


def test_bug_b_do_that_and_it_are_interchangeable_confirmation_replies():
    """Bug B: "it" and "that" are interchangeable anaphoric references to
    an already-offered action -- real production bug, "yes do that" lost
    an already-resolved identity because only the "it" forms were
    recognized."""
    for text in ("yes do that", "yeah do that", "okay do that", "sure, do that",
                 "get that", "request that", "add that", "do that"):
        assert is_confirmation(text), text


def test_bug_b_fix_does_not_regress_existing_confirmation_phrasings():
    """No-regression confirmation: the existing "it" forms and the
    "please" phrasings from two rounds ago must still all work."""
    for text in ("yes do it", "get it", "request it", "add it", "do it",
                 "Yes, please request it.", "Yeah, please add it.", "please go ahead"):
        assert is_confirmation(text), text
    for text in ("What is the weather like?", "Get me Dumb and Dumber from 1994.", "maybe"):
        assert not is_confirmation(text), text


def test_never_requested_status_display_title_is_clean_for_verb_form_phrasings():
    """Item 5: a genuine "you never requested this" case must name the
    title cleanly ("I don't have a tracked request for Interstellar
    yet."), not echo the verb-phrase framing back ("...for I already
    request Interstellar yet.") -- real gap found alongside the
    fresh-session verb-form status fix, since media_status_display_title
    was only ever built to strip the older "how's X doing" framing."""
    for query, expected_title in (
        ("Did I already request Interstellar?", "Interstellar"),
        ("Did I request Interstellar yet?", "Interstellar"),
        ("Have I requested Interstellar?", "Interstellar"),
        ("Did I ask for Interstellar already?", "Interstellar"),
        ("Have I already asked for Interstellar?", "Interstellar"),
        ("Was Interstellar ever requested?", "Interstellar"),
    ):
        result = {"found": False, "status": "NOT_FOUND", "query": query}
        assert media_status_display_title(result, query) == expected_title, query


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("grounding regressions passed")
