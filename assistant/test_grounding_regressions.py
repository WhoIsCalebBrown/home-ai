"""Regression checks for live grounding, provenance, camera safety, and aliases."""

import ast
import json
import re
import time
from pathlib import Path

tree = ast.parse(Path(__file__).with_name("voice-api-app.py").read_text())
needed = {"SOURCE_NAMES", "ARTIST_ALIASES", "DOMAIN_ENTITIES", "artist_from_speech", "visual_question", "activity_question", "front_door_presence_question", "current_camera_presence_question", "grounded_recent_activity_answer", "historical_timing_question", "grounded_event_timing_answer", "dynamic_fact_question", "current_external_question", "explicit_web_search_request", "historical_camera_question", "historical_camera_window", "plex_query_from_speech", "investigation_query_from_speech", "deterministic_plan", "preflight_plan", "evidence_supported_answer", "grounded_camera_presence_answer", "direct_structured_answer", "media_plan_response", "routing_aliases", "contextual_entity_resolution", "is_repair_turn", "repair_route_text", "weather_location_from_text", "explicit_topic", "turn_context", "resolved_followup_text", "conversation_context", "explicit_domain", "social_acknowledgement", "underspecified_read_request", "repeat_intent", "rephrase_intent", "repair_decimal_spacing", "round_weather_temperatures", "complete_speakable_sentence", "direct_file_request", "playback_request", "media_identity_signal", "media_acquisition_language", "media_goal_request", "media_status_question", "media_nouns_for_status", "media_title_status_signal", "retained_media_status_repair", "media_status_display_title", "is_confirmation", "store_provenance", "provenance_question", "ambiguous_container_status_followup", "all_live_results_failed", "discovery_question", "_tokens_for_discovery", "_DISCOVERY_QUESTION_PATTERNS", "_DISCOVERY_QUESTION_STOPWORDS", "_media_title_candidate_words", "_MEDIA_CATEGORY_WORDS", "_MEDIA_QUESTION_SCAFFOLDING", "plex_query_from_speech", "guess_media_title", "fresh_title_restatement", "media_intent", "media_library_query", "library_category_followup", "referential_media_library_question", "referential_media_request", "retained_media_goal", "canonical_identity_matches", "enforce_retained_media_identity", "collective_library_query", "referential_web_query", "storage_state_followup", "operation_for_plan", "_descriptive_media_clue", "natural_weather_summary", "web_result_useful", "web_search_query_from_text", "_WEB_QUERY_LEADING_SCAFFOLDING", "_WEB_QUERY_TRAILING_FILLER", "_WEB_QUERY_NESTED_SCAFFOLDING", "web_recovery_queries", "collapse_repeated_sentences", "_timezone_from_text", "_TIMEZONE_CITY_MAP", "high_confidence_auto_dispatch", "CONTAINER_DISPLAY_NAMES", "_server_container_followup_target", "canonical_media_year_answer"}
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
high_confidence_auto_dispatch = namespace["high_confidence_auto_dispatch"]
_server_container_followup_target = namespace["_server_container_followup_target"]
collapse_repeated_sentences = namespace["collapse_repeated_sentences"]
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
library_category_followup = namespace["library_category_followup"]
referential_media_library_question = namespace["referential_media_library_question"]
referential_media_request = namespace["referential_media_request"]
retained_media_goal = namespace["retained_media_goal"]
canonical_identity_matches = namespace["canonical_identity_matches"]
enforce_retained_media_identity = namespace["enforce_retained_media_identity"]
collective_library_query = namespace["collective_library_query"]
referential_web_query = namespace["referential_web_query"]
storage_state_followup = namespace["storage_state_followup"]
operation_for_plan = namespace["operation_for_plan"]
_descriptive_media_clue = namespace["_descriptive_media_clue"]
canonical_media_year_answer = namespace["canonical_media_year_answer"]
web_search_query_from_text = namespace["web_search_query_from_text"]
web_recovery_queries = namespace["web_recovery_queries"]


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
    assert preflight_plan("What happened today in Canada?") == [("web_search", {"query": "What happened today in Canada"})]


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
    assert preflight_plan("What are Donald Trump's latest trade policies?") == [("web_search", {"query": "What are Donald Trump's latest trade policies"})]
    assert preflight_plan("What's the newest version of Ollama?") == [("web_search", {"query": "What's the newest version of Ollama"})]


def test_politics_today_is_web_not_camera():
    assert current_external_question("Can you give me a rundown of what happened today in American politics?")
    assert preflight_plan("Can you give me a rundown of what happened today in American politics?") == [("web_search", {"query": "what happened today in American politics"})]


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


def test_recent_activity_answer_never_prepends_a_stray_yes():
    # Real production bug: "What happened at the front door recently?" (a
    # WH-question, not yes/no -- front_door_presence_question() is already
    # False for it, and this function's only caller is reached precisely
    # when that presence-question branch did NOT fire) got answered "Yes.
    # about 1.7 hours ago, a single person walks into the front foyer..."
    # -- an ungrammatical "Yes." with nothing for it to affirm.
    answer = grounded_recent_activity_answer({
        "latest_only": True,
        "reviews": [{
            "time": {"start": {"relative_time": "about 1.7 hours ago"}},
            "genai": {"shortSummary": "a single person walks into the front foyer."},
            "objects": ["person"],
        }],
    })
    assert not answer.startswith("Yes.")


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
    # Intentional behavior change: "server status" now resolves to the new,
    # far more detailed unraid_system_health tool (array/CPU/RAM/uptime/
    # firing-alerts summary) instead of a bare container count, once the
    # Unraid Management Agent MCP adapter tools existed to answer it properly.
    assert preflight_plan("What is the server status?") == [("unraid_system_health", {})]


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


def test_library_count_category_followup_preserves_count_operation_not_title_search():
    context = {"latest_operation": "PLEX_LIBRARY_COUNT", "operation_scope": {"category": "movie"}}
    assert library_category_followup("What about anime?") == "anime"
    assert preflight_plan("What about anime?", context) == [("plex_library_counts", {})]
    # An explicit new server question must outrank the inherited Plex count.
    assert preflight_plan("How many containers are running?", context) == [("list_containers", {"status": "running"})]


def test_storage_state_followup_reuses_storage_target_never_container_without_name():
    context = {"latest_operation": "STORAGE_CAPACITY", "operation_scope": {"target": "cache"}}
    assert storage_state_followup("Is it running?", context)
    assert preflight_plan("Is it running?", context) == [("unraid_storage_status", {"target": "cache"})]
    assert preflight_plan("Is Plex running?", context) != [("unraid_storage_status", {"target": "cache"})]


def test_referential_media_operations_preserve_subject_while_source_changes():
    context = {"canonical_identity": {"title": "Cast Away", "media_type": "movie", "year": 2000},
               "latest_resolved_referent": "Cast Away"}
    assert referential_media_library_question("Do I have it?", context)
    assert preflight_plan("Do I have it?", context) == [
        ("media_plan_goal", {"goal": "Cast Away from 2000", "media_type": "movie"})
    ]
    assert referential_web_query("Can you look it up on the internet?", context) == "Cast Away"
    assert preflight_plan("Can you look it up on the internet?", context) == [
        ("web_search", {"query": "Cast Away"})
    ]


def test_collective_library_inventory_is_read_only_while_acquisition_stays_canonical():
    assert collective_library_query("What Galactic Saga stuff do I have?") == "Galactic Saga"
    assert preflight_plan("What Galactic Saga stuff do I have?") == [
        ("plex_search", {"query": "Galactic Saga"})
    ]
    acquisition = preflight_plan("Get Galactic Saga.")
    assert acquisition == [("media_plan_goal", {"goal": "Get Galactic Saga."})]


def test_operation_for_plan_uses_existing_media_vocabulary_and_bounded_scopes():
    operation, scope = operation_for_plan("Get The Room.", {"operation": "MEDIA_REQUEST"}, [("media_plan_goal", {"goal": "Get The Room."})])
    assert operation == "MEDIA_REQUEST"
    assert scope == {}
    operation, scope = operation_for_plan("How full is cache?", {}, [("unraid_storage_status", {"target": "cache"})])
    assert operation == "STORAGE_CAPACITY"
    assert scope == {"target": "cache"}


def test_referential_media_identity_is_preserved_and_validated():
    expected = {"title": "The Thing", "media_type": "movie", "year": 1982, "tmdb_id": 1091}
    assert preflight_plan("Get it.", {"canonical_identity": expected}) == [
        ("media_plan_goal", {"goal": "get The Thing from 1982", "media_type": "movie"})
    ]
    good = {"tool": "media_plan_goal", "status": "ok", "result": {"canonical_identity": dict(expected)}}
    assert enforce_retained_media_identity(expected, good) == good
    wrong = {"tool": "media_plan_goal", "status": "ok", "result": {
        "canonical_identity": {"title": "The Thing", "media_type": "movie", "year": 2011, "tmdb_id": 60935}}}
    rejected = enforce_retained_media_identity(expected, wrong)
    assert rejected["status"] == "error"
    assert rejected["error"]["code"] == "CANONICAL_IDENTITY_MISMATCH"
    assert rejected["result"] == {"ok": False, "error_code": "CANONICAL_IDENTITY_MISMATCH"}
    assert "canonical_identity" not in rejected["result"]


def test_successful_topic_switch_clears_stale_operation():
    client_id = "topic-switch"
    conversation_context[client_id] = {
        "latest_operation": "STORAGE_CAPACITY", "operation_scope": {"target": "cache"},
    }
    store_provenance(client_id, [{"tool": "weather_forecast", "status": "ok", "result": {
        "source": "Open-Meteo", "location": {"name": "Toronto"}}}])
    assert "latest_operation" not in conversation_context[client_id]
    assert "operation_scope" not in conversation_context[client_id]
    assert preflight_plan("Is it running?", conversation_context[client_id]) == []


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


def test_canonical_media_year_followup_uses_retained_authoritative_identity_only():
    context = {"canonical_identity": {"title": "Cast Away", "year": "2000", "tmdb_id": "8358"}}
    assert canonical_media_year_answer(context, "What year did it come out?") == "Cast Away came out in 2000."
    assert canonical_media_year_answer({"latest_resolved_referent": "Cast Away"}, "What year did it come out?") is None
    assert canonical_media_year_answer(context, "Do I have it?") is None


def test_single_weak_candidate_is_not_announced_as_more_than_one():
    # Real production bug found live: after _pick_match's relevance-floor
    # fix (added to stop offering fabricated candidates for a fictional
    # title), a query can legitimately resolve to exactly ONE plausible-
    # but-not-confident candidate -- "I found more than one possible
    # match: Frieren: Beyond Journey's End (2023)." is grammatically wrong
    # and confusing with only one title listed.
    result = {"canonical_identity": None, "ambiguous": True, "ambiguity_reason": "NO_CONFIDENT_MATCH",
              "candidates": [{"title": "Frieren: Beyond Journey's End", "year": "2023"}],
              "goal": {"media_type": "tv", "title_query": "Frieren"}}
    answer = media_plan_response("Can you find the anime Frieren", [{"tool": "media_plan_goal", "status": "ok", "result": result}])
    assert answer == "I found a possible match: Frieren: Beyond Journey's End (2023). Is that the one you mean?"


def test_media_diagnosis_is_human_and_stops_at_proven_boundary():
    result = {"title": "Dumb and Dumber", "canonical_state": "NO_CANDIDATE",
              "diagnosis": "NO_ACCEPTABLE_CANDIDATE", "canonical_identity": {"title": "Dumb and Dumber"}}
    assert direct_structured_answer("why is it stuck", [{"tool": "media_diagnose", "status": "ok", "result": result}]) == "I couldn't find a suitable copy of Dumb and Dumber."


def test_not_requested_status_gives_a_clear_offer_not_a_dead_end():
    # Real production gap found in a live naive-user sweep: "What's the
    # status of Arcane?"/"Can I watch Bird Box?" for a title that was
    # added directly in Sonarr/Radarr (never formally REQUESTED through
    # Home-AI) used to dead-end with "I don't have a tracked request for
    # that yet" even when the title was correctly identified live --
    # media_status's new live-identification fallback returns "found":
    # True with canonical_state "NOT_REQUESTED" for this exact case, and
    # the answer must clearly offer to start a request rather than
    # sounding like a permanent dead end.
    result = {"found": True, "canonical_state": "NOT_REQUESTED", "canonical_identity": {"title": "Arcane"}}
    answer = direct_structured_answer("What's the status of Arcane?", [{"tool": "media_status", "status": "ok", "result": result}])
    assert answer == "I found Arcane, but nothing has been requested for it yet. Want me to start that?"

    diagnose_result = {"title": "Arcane", "canonical_state": "NOT_REQUESTED", "diagnosis": "IDENTIFIED_NOT_REQUESTED",
                        "canonical_identity": {"title": "Arcane"}}
    diagnose_answer = direct_structured_answer("why hasn't Arcane started", [{"tool": "media_diagnose", "status": "ok", "result": diagnose_result}])
    assert diagnose_answer == "I found Arcane, but nothing has been requested for it yet. Want me to start that?"


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
    assert preflight_plan("Anything interesting with AI specifically?") == [("web_search", {"query": "Anything interesting with AI specifically"})]


def test_conversational_news_request_becomes_a_clean_search_query():
    # A verbose, conversational phrasing must not be sent to the search
    # backend verbatim: request-verb scaffolding and literal "today"/"now"
    # tokens reliably return zero results even though the underlying topic
    # has live coverage, since recency is already carried by recency_days.
    assert web_search_query_from_text(
        "can you give me an in depth review on the canadian news for today"
    ) == "canadian news"
    assert web_search_query_from_text("what is happening in canada today") == "canada"
    assert preflight_plan("can you give me an in depth review on the canadian news for today") == [
        ("web_search", {"query": "canadian news"})
    ]
    recovery = web_recovery_queries("can you give me an in depth review on the canadian news for today")
    assert recovery[0] == "canadian news"
    assert all("2026" not in query and "September" not in query for query in recovery)


def test_nested_conversational_filler_is_also_stripped_from_the_search_query():
    # Real production example flagged directly by the user: "can you give me
    # an in depth review of what's gone on in the canadian news today" only
    # had its OUTER request-verb scaffolding stripped ("can you give me an
    # in depth review of"), leaving a second, still-unclean layer ("what's
    # gone on in the canadian news") that also returned zero usable SearXNG
    # results.
    assert web_search_query_from_text(
        "can you give me an in depth review of what's gone on in the canadian news today"
    ) == "canadian news"


def test_yesterday_is_recognized_as_a_fresh_research_question_not_a_media_goal():
    # Real production bug found by the user: "can you give me an in depth
    # review of what's gone on in the canadian news yesterday" was routed to
    # media_plan_goal (its "give" verb matched media_acquisition_language)
    # instead of web_search, because current_external_question()'s
    # freshness vocabulary only recognized "today"/"now"/"current" and not
    # "yesterday" -- asking about yesterday's news is still unambiguously a
    # fresh research question, not a stale one.
    assert current_external_question("what's gone on in the canadian news yesterday")
    assert preflight_plan(
        "can you give me an in depth review of what's gone on in the canadian news yesterday"
    ) == [("web_search", {"query": "canadian news yesterday"})]


def test_high_confidence_auto_dispatch_bypasses_qwen_only_when_safe():
    # Found by a full-catalog live validation sweep: Qwen sometimes ignores
    # a correctly, decisively top-ranked candidate for introspective tools
    # it has little training signal for -- "Give me a quick server
    # overview" ranked get_server_overview #1 by a wide margin, yet Qwen
    # called media_plan_goal instead. This generalizes the fix instead of
    # writing a hand-written route for every such tool: it only fires for a
    # dominant, read-only, zero-required-argument winner.
    dominant_read_no_args = [
        {"canonical_name": "get_server_overview", "score": 15.5, "read_write": "read"},
        {"canonical_name": "get_container_logs", "score": 2.8, "read_write": "read"},
    ]
    schemas_no_args = [
        {"name": "get_server_overview", "parameters": {"type": "object", "properties": {}, "required": []}},
    ]
    assert high_confidence_auto_dispatch(dominant_read_no_args, schemas_no_args) == [("get_server_overview", {})]

    # A tool needing an argument (name/query) must never auto-dispatch --
    # there is no safe way to guess that argument deterministically here.
    dominant_needs_arg = [
        {"canonical_name": "get_container_status", "score": 12.6, "read_write": "read"},
        {"canonical_name": "get_container_logs", "score": 6.5, "read_write": "read"},
    ]
    schemas_needs_arg = [
        {"name": "get_container_status", "parameters": {"type": "object", "properties": {"name": {}}, "required": ["name"]}},
    ]
    assert high_confidence_auto_dispatch(dominant_needs_arg, schemas_needs_arg) == []

    # A write/confirm-permission tool must never auto-dispatch even if
    # dominant and zero-arg.
    dominant_write = [
        {"canonical_name": "home_activate_scene", "score": 12.4, "read_write": "write_low"},
        {"canonical_name": "media_plan_goal", "score": 6.6, "read_write": "read"},
    ]
    assert high_confidence_auto_dispatch(dominant_write, []) == []

    # A merely-plausible (not dominant) top score must not auto-dispatch --
    # this only fires when discovery is unambiguous.
    close_scores = [
        {"canonical_name": "plex_search", "score": 8.0, "read_write": "read"},
        {"canonical_name": "plex_library_counts", "score": 7.0, "read_write": "read"},
    ]
    assert high_confidence_auto_dispatch(close_scores, [{"name": "plex_search", "parameters": {"required": []}}]) == []


def test_named_container_status_question_resolves_deterministically():
    # Same root cause and same fix shape as get_container_logs above:
    # "What's the status of the Home-AI-Tools container?" ranked
    # get_container_status #1 by a wide margin in discovery, yet Qwen
    # called list_containers instead in a real live validation run.
    assert preflight_plan("What's the status of the Home-AI-Tools container?") == [
        ("get_container_status", {"name": "Home-AI-Tools"})
    ]
    assert preflight_plan("Is the Plex container running?") == [("get_container_status", {"name": "Plex"})]


def test_unraid_storage_and_health_questions_resolve_deterministically():
    # Found by a live acceptance-test sweep against the newly-added Unraid
    # Management Agent MCP adapter tools: two DIFFERENT earlier catch-alls
    # (the acquisition_verb + leftover-words untyped-media-request gate for
    # "Give me a quick server status", and the generic gpu/container/
    # server-status block for "cache"/"container"/"server status" keywords)
    # both fired before ever reaching a storage/health-specific check,
    # sending these to media_plan_goal or a bare list_containers count
    # instead of the new, far more detailed tools.
    assert preflight_plan("How full is the cache drive?") == [("unraid_storage_status", {"target": "cache"})]
    assert preflight_plan("How much space is left on the array?") == [("unraid_storage_status", {"target": "array"})]
    assert preflight_plan("Which disk is fullest?") == [("unraid_storage_status", {"target": "disks"})]
    assert preflight_plan("Is the array healthy?") == [("unraid_disk_health", {})]
    assert preflight_plan("Are any disks having errors?") == [("unraid_disk_health", {})]
    assert preflight_plan("Are any containers unhealthy?") == [("unraid_container_metrics", {})]
    assert preflight_plan("Give me a quick server status.") == [("unraid_system_health", {})]
    assert preflight_plan("Is anything wrong with the server?") == [("unraid_system_health", {})]
    # Real production bug found live: "Give me a quick server overview."
    # scored get_server_overview (15.47) and unraid_system_health (12.97)
    # too close together for high_confidence_auto_dispatch's 5.0-margin
    # threshold, leaving the choice to Qwen -- which then ignored BOTH
    # sensible candidates and called media_plan_goal instead, resolving to
    # unrelated cartoon titles ("Quick Draw McGraw"). "overview"/"summary"
    # are the same request shape as "status"/"health" for this deterministic
    # route -- must not depend on Qwen's tool choice at all.
    assert preflight_plan("Give me a quick server overview.") == [("unraid_system_health", {})]
    # A true storage-BREAKDOWN question (what's consuming the space) is a
    # different shape unraid_storage_status cannot answer (see
    # capability-gap.md) and must fall through to the existing tested path,
    # not be confidently answered with the wrong kind of data.
    assert preflight_plan("What's using up most of the space in the cache?") == [("get_storage_status", {})]


def test_named_container_uptime_and_memory_questions_resolve_deterministically():
    # "Is Plex running?"/"How long has Plex been running?"/"How much memory
    # is Home-AI using?" all name a real container but never say the literal
    # word "container", so they fell through to media/web routing instead
    # ("Plex" is a media-identity word; "Home-AI" tripped
    # current_external_question's bare "ai" keyword via the hyphen-bounded
    # substring). Bounded to the known CONTAINER_DISPLAY_NAMES set so this
    # cannot turn an unrelated "is the light on" into a container lookup,
    # and must not steal a real media-library question just because "Plex"
    # and a bare "up" both appear in it.
    assert preflight_plan("How long has Plex been running?") == [("unraid_container_status", {"container": "Plex"})]
    assert preflight_plan("Is Plex running?") == [("unraid_container_status", {"container": "Plex"})]
    assert preflight_plan("How much memory is Home-AI using?") == [("unraid_container_status", {"container": "Home-AI-Assistant"})]
    assert preflight_plan("Is the front door light on?")[0][0] != "unraid_container_status"
    assert preflight_plan("Why isn't The Matrix showing up in my Plex library?")[0][0] == "investigate_plex_missing"


def test_storage_followup_naming_a_container_continues_the_server_topic():
    # Real production bug found in a live continuity test: "How full is
    # cache?" -> "What's using most of it?" -> "What's inside appdata?" ->
    # "What about Plex?" reclassified the last turn as "media" purely
    # because explicit_domain()'s bare "plex" keyword outranks any inherited
    # storage topic, producing an unrelated media-acquisition non-answer.
    # turn_context() now recognizes this bounded "what about X" continuation
    # (via _server_container_followup_target, keyed on latest_tool_result
    # rather than the non-sticky per-turn "domain" signal) and routes it to
    # the real capability that exists -- the container's own status.
    context = {"container_followup": "plex"}
    assert preflight_plan("What about Plex?", context) == [("unraid_container_status", {"container": "Plex"})]

    # Without a resolved container_followup (a genuinely fresh "What about
    # Plex?" with no prior storage context), this must not fire.
    assert preflight_plan("What about Plex?", {})[0][0] != "unraid_container_status"


def test_container_followup_target_requires_a_real_preceding_server_tool_call():
    prior_after_storage_call = {"latest_tool_result": {"tools": ["unraid_storage_status"]}}
    assert _server_container_followup_target("What about Plex?", prior_after_storage_call) == "plex"
    assert _server_container_followup_target("How about Home-AI?", prior_after_storage_call) == "home-ai"

    # No preceding server/storage tool call: never fires, even with the same
    # "what about X" phrasing -- this is a continuation signal, not a
    # standalone container-name detector.
    prior_after_media_call = {"latest_tool_result": {"tools": ["plex_search"]}}
    assert _server_container_followup_target("What about Plex?", prior_after_media_call) is None
    assert _server_container_followup_target("What about Plex?", {}) is None

    # A fresh sentence that merely mentions a container name, without the
    # bounded "what about X"/"how about X" continuation frame, never fires.
    assert _server_container_followup_target("Is Plex a good media server?", prior_after_storage_call) is None


def test_container_logs_request_is_not_swallowed_by_generic_container_catch_all():
    # Real production bug found by the user directly inspecting a live
    # transcript: "Show me the last few log lines for the Home-AI-Tools
    # container." matched the generic docker/service catch-all ("container"
    # is in its word list) before ever reaching a logs-specific check, so it
    # got routed to list_containers (a container count) instead of
    # get_container_logs. The container name's real casing must survive.
    assert preflight_plan("Show me the last few log lines for the Home-AI-Tools container.") == [
        ("get_container_logs", {"name": "Home-AI-Tools"})
    ]


def test_investigate_downloads_request_is_not_swallowed_by_generic_service_catch_all():
    # Same root cause as the logs bug above: "Investigate my downloads
    # across all services." matched the generic catch-all ("services") and
    # got routed to list_containers -- a completely wrong domain -- instead
    # of the tool built to correlate qBittorrent/Sonarr/Radarr/Lidarr/Slskd/
    # Torbox download state. A single-service question ("any active
    # Soulseek downloads") must still reach discovery/its own tool, not this
    # broad multi-service correlation tool.
    assert preflight_plan("Investigate my downloads across all services.") == [("investigate_downloads", {})]
    assert preflight_plan("Any active Soulseek downloads?") == []


def test_sonarr_queue_answer_is_not_rejected_for_the_generic_word_downloading():
    # Real production bug found live: "Is my Sonarr queue empty right now?"
    # -- a perfectly accurate answer, since every queue item's status was
    # literally "completed" -- was rejected by the unsupported-claim guard
    # purely because the generic topical word "downloading" never appears
    # verbatim in Sonarr's own status vocabulary. "downloading" describes
    # the whole investigation's own subject (investigate_downloads) and is
    # not a reliable fabrication signal like the other dynamic_words.
    results = [{"tool": "investigate_downloads", "status": "ok", "result": {
        "investigation": "downloads", "sources_checked": ["sonarr"],
        "sources": {"sonarr": {"service": "sonarr", "total": 24, "items": [
            {"title": "x", "status": "completed", "sizeleft": 0, "protocol": "torrent"}]}},
    }}]
    answer = "Your Sonarr queue currently has 24 items, but none appear to be actively downloading right now."
    assert evidence_supported_answer(answer, "Is my Sonarr queue empty right now?", results) == answer
    # A genuinely unsupported specific technical claim must still be rejected.
    fabricated = "Your Sonarr queue has a certificate error and 3 quarantined items."
    assert evidence_supported_answer(fabricated, "Is my Sonarr queue empty right now?", results) != fabricated


def test_frigate_stats_request_outranks_the_recent_events_catch_all():
    # Real production bug: "What are the current Frigate camera stats?"
    # matched the recent-events branch ("camera" is in its word list)
    # before ever reaching the frigate_stats branch, so an explicit
    # "stats"/"statistics" request always lost to a recent-activity
    # narrative instead of actual fps/detector numbers.
    assert preflight_plan("What are the current Frigate camera stats?") == [("frigate_stats", {})]


def test_datetime_question_resolves_deterministically_not_via_web_search():
    # Real production bug: "What time is it in Tokyo right now?" used
    # web_search instead of the deterministic current_datetime tool, and
    # returned a factually wrong date. Time/date has one authoritative
    # source and needs no model judgment.
    assert preflight_plan("What time is it in Tokyo right now?") == [("current_datetime", {"timezone": "Asia/Tokyo"})]
    assert preflight_plan("What time is it?") == [("current_datetime", {})]
    assert preflight_plan("What's the weather today?")[0][0] == "weather_forecast"


def test_named_device_state_question_resolves_to_home_get_state_not_area():
    # Real production bug: "What's the state of the neon lights?" scored
    # home_get_area_state fractionally higher than home_get_state in
    # discovery (home_get_area_state takes a room/area name, not a device
    # name), and Qwen picked the area tool for a named DEVICE -- falsely
    # reporting "I couldn't find any lights" even though the device exists.
    # Bounded to actual light/switch language so unrelated "state of X"
    # questions (a Sonarr download, a Docker service) are unaffected.
    assert preflight_plan("What's the state of the neon lights?") == [("home_get_state", {"entity_or_area": "neon lights"})]
    assert preflight_plan("What's the state of my Sonarr download?") != [("home_get_state", {"entity_or_area": "my sonarr download"})]


def test_natural_why_isnt_it_in_plex_phrasing_reaches_investigate_plex_missing():
    # Real production bug found by the user directly inspecting a live
    # Open WebUI transcript: "Why isn't The Matrix showing up in my Plex
    # library?" fell through to a generic plex_library_counts answer
    # ("you have 1600 movies, might not be imported") instead of the tool
    # built specifically to explain a missing title (investigate_plex_missing,
    # which also checks Sonarr/Radarr/qBittorrent). The old regex required
    # its why/negation, media-word, and absence-word groups to appear in a
    # fixed left-to-right order, but this overwhelmingly natural phrasing
    # puts the absence word ("showing") BEFORE the media word ("Plex").
    assert preflight_plan("Why isn't The Matrix showing up in my Plex library?")[0][0] == "investigate_plex_missing"
    assert preflight_plan("Why is the movie missing from Plex?")[0][0] == "investigate_plex_missing"


def test_sentence_initial_capitalization_is_not_mistaken_for_a_person_name():
    # "Any Sonarr health issues?" and "Search Radarr for the movie Inception."
    # both start with an ordinary capitalized word followed by a capitalized
    # service name -- the person-name heuristic misread that as a two-word
    # person name (a descriptive media clue), routing both into
    # media_plan_goal, which then fuzzy-matched the whole sentence as a Plex
    # title and returned nonsense disambiguation candidates.
    assert not _descriptive_media_clue("Any Sonarr health issues?")
    assert not _descriptive_media_clue("Any Radarr health issues?")
    assert not _descriptive_media_clue("Search Radarr for the movie Inception.")
    assert not _descriptive_media_clue("Search Lidarr for the artist Radiohead.")
    # A genuine person-named descriptive clue must still be recognized.
    assert _descriptive_media_clue("What's that Tom Hanks movie where he's stuck on an island?")


def test_explicit_manager_search_resolves_directly_to_its_own_tool():
    # "Search Radarr/Lidarr for X" names its own manager service; the
    # generic Plex/pipeline catch-alls have no way to express that and
    # previously answered with the wrong tool (plex_search,
    # investigate_media_pipeline). Discovery/Qwen now ranks the real
    # radarr_search_movie/lidarr_search_artist tool decisively first for
    # this phrasing, but a real production run showed Qwen still sometimes
    # picks the wrong tool (or the wrong argument name) even then -- this
    # unambiguous imperative is resolved deterministically instead.
    assert preflight_plan("Search Radarr for the movie Inception.") == [("radarr_search_movie", {"query": "inception"})]
    assert preflight_plan("Search Lidarr for the artist Radiohead.") == [("lidarr_search_artist", {"query": "radiohead"})]


def test_plex_library_count_question_is_not_a_media_goal():
    # "How many movies and shows do I have in Plex?" contains "do i have"
    # (matched by the acquisition-goal regex) and "plex" (a media noun), so
    # it was sent whole to media_plan_goal, which fuzzy-matched the literal
    # sentence as a Plex title and returned unrelated disambiguation
    # candidates instead of a real library count.
    assert preflight_plan("How many movies and shows do I have in Plex?") == [("plex_library_counts", {})]


def test_bare_service_status_questions_are_not_media_workflow_status():
    # "What's the Torbox status?" mentioning a backend service name made
    # explicit_domain() classify the turn as media, which combined with
    # media_status_question()==True to trip the "no matching live workflow"
    # deterministic shortcut before discovery/Qwen ever got a chance to call
    # the real torbox_status tool. These are backend-service health checks,
    # not a movie/show lifecycle question, the same way lidarr/sonarr/radarr
    # were already excluded above.
    assert not media_status_question("What is the Torbox status?")
    assert not media_status_question("What is the Overseerr status?")
    assert not media_status_question("Any active Soulseek downloads?")
    assert not media_status_question("What is currently downloading in qBittorrent?")
    # A real title-lifecycle question must still be recognized.
    assert media_status_question("How is my Cast Away request going?")
    assert media_status_question("Did I already request The Room?")


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


def test_real_downloads_evidence_is_not_reported_as_an_outage():
    # investigate_downloads succeeded with real data, but the model's own
    # synthesis happened to echo the generic "current server information"
    # refusal phrase; a real production reply then falsely claimed the live
    # tool result was unavailable even though it plainly was not.
    result = [{"tool": "investigate_downloads", "status": "ok", "result": {"sources": {"qbittorrent": {"torrent_count": 269}}}}]
    answer = evidence_supported_answer(
        "I couldn't verify that current server information because the required live tool result was unavailable.",
        "What's currently downloading in qBittorrent?", result, "downloads",
    )
    assert "unavailable" not in answer.lower()
    assert "found live results" in answer.lower()


def test_collapse_repeated_sentences_drops_only_adjacent_exact_duplicates():
    # A real production reply repeated the same sentence back-to-back
    # verbatim; collapse that, but never touch a later, non-adjacent repeat
    # or two merely-similar sentences.
    assert collapse_repeated_sentences(
        "You've got 50 containers running. You've got 50 containers running."
    ) == "You've got 50 containers running."
    assert collapse_repeated_sentences("Nothing is playing on Plex right now.") == "Nothing is playing on Plex right now."
    assert collapse_repeated_sentences(
        "It's 22 degrees. It's going to rain later. It's 22 degrees."
    ) == "It's 22 degrees. It's going to rain later. It's 22 degrees."


def test_empty_but_successful_list_read_is_not_reported_as_no_access():
    result = [{"tool": "list_items", "status": "ok", "result": {"list": "grocery", "items": [], "count": 0}}]
    answer = evidence_supported_answer("I don't have access to your grocery list right now.", "What's on my grocery list?", result, None)
    assert "no access" not in answer.lower() and "don't have access" not in answer.lower()
    assert "empty" in answer.lower()


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


def test_can_i_watch_is_recognized_as_a_status_question_not_a_streaming_refusal():
    # Real production bug found in a live naive-user sweep: "Can I watch
    # Bird Box?" -- an extremely natural way for someone with zero
    # knowledge of Radarr/Plex/cli_debrid to ask "is this available" --
    # got a pure training-bias refusal from Qwen ("I don't have access to
    # your TV or streaming services"), with no tool called at all. "watch"
    # (unlike "get"/"request"/"add") strongly signals an availability
    # check, not a fresh acquisition request, even with the "can I" modal
    # frame. Must also outrank _descriptive_media_clue's person-name-shape
    # suppression -- "Bird Box" looks exactly like a two-word person name.
    assert media_status_question("Can I watch Bird Box?") is True
    assert media_status_question("Am I able to watch Arcane?") is True
    assert media_title_status_signal("Can I watch Bird Box?") is True
    assert preflight_plan("Can I watch Bird Box?") == [("media_status", {"query": "Can I watch Bird Box?"})]


def test_status_of_bare_title_is_recognized_with_no_media_noun_or_referent():
    # Real production bug found live: "What's the status of Arcane?" (a
    # bare single-word title, no "request"/"movie"/"show" noun, no prior
    # conversational referent) never routed to media_status at all --
    # Qwen answered from pure training bias with no tool called. Root
    # cause: media_title_status_signal's generic fallback splits the
    # subject on the word "status" itself, assuming it comes at the END
    # of the phrase ("the movie X doing" -> split on "doing" -> keep "the
    # movie X"), but "status OF X" has the status word in the MIDDLE with
    # the title AFTER it, so splitting on "status" discarded "of Arcane"
    # entirely and kept only "the".
    assert media_title_status_signal("What's the status of Arcane?") is True
    assert preflight_plan("What's the status of Arcane?") == [("media_status", {"query": "What's the status of Arcane?"})]


def test_status_display_title_extracts_the_real_subject_not_a_stray_article():
    # Same root-cause bug class as media_title_status_signal above, found
    # in a SEPARATE function: after "media_status" now actually gets
    # called for "What's the status of Arcane?" (see the fix above), a
    # genuinely-not-found result displayed "I don't have a tracked
    # request for the yet." -- media_status_display_title's own generic
    # status-word truncation removes everything from "status" TO THE END
    # of the string, again assuming the status word comes last, again
    # discarding "of Arcane" and keeping only "the".
    assert media_status_display_title({}, "What's the status of Arcane?") == "Arcane"
    assert media_status_display_title({}, "What is the status of my Silo request?") == "Silo request"
    # Existing regressions this fix must not disturb.
    assert media_status_display_title({}, "Did I already request Interstellar?") == "Interstellar"


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


def test_p0_cache_wording_variants_use_authoritative_unraid_capacity():
    variants = (
        "How full is cache?", "How much space is left on cache?",
        "What's the cache usage?", "Show me cache capacity.",
        "How much cache space is free?", "What percent full is cache?",
        "Give me cache storage status.", "How used is the cache drive?",
        "How many GB are free on cache?", "Check cache disk space.",
        "Check cache fullness.",
    )
    for text in variants:
        assert preflight_plan(text) == [("unraid_storage_status", {"target": "cache"})], text


def test_p0_the_room_confirmation_prompt_is_a_media_goal_without_title_allowlist():
    assert preflight_plan("Get The Room.") == [("media_plan_goal", {"goal": "Get The Room."})]
    assert preflight_plan("Request the Matrix!") == [("media_plan_goal", {"goal": "Request the Matrix!"})]
    assert preflight_plan("Get the lights.") != [("media_plan_goal", {"goal": "Get the lights."})]


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
