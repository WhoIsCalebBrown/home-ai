# Home-AI Tool Validation Inventory

Generated from the real 74-tool `_discoverable_registry()` plus tonight's actual Open WebUI evidence
(not assumed from unit-test coverage). `media_standard_request` is excluded by design
(`MODEL_FACING_EXCLUDED_TOOLS`) and is never model-reachable -- tracked separately, not in this table.

## Summary

- **NOT_VALIDATED**: 38
- **PARTIALLY_VALIDATED**: 1
- **VALIDATED_LIVE**: 33
- **WRITE_TOOL_NOT_SAFE_TO_LIVE_TEST**: 2
- **Total tools**: 74

## Full Inventory

| Service | Tool | Read/Write | Args | Discovery Metadata | Status | Evidence / Notes |
|---|---|---|---|---|---|---|
| beets | `beets_recent_imports` | read | - | yes | NOT_VALIDATED |  |
| beets | `beets_status` | read | - | yes | NOT_VALIDATED |  |
| docker | `get_container_logs` | read | name,lines | yes | VALIDATED_LIVE | Show me the last few log lines for the Home-AI-Tools container. |
| docker | `get_container_status` | read | name | yes | NOT_VALIDATED |  |
| docker | `list_containers` | read | status | yes | VALIDATED_LIVE | List the docker containers running right now. |
| docker | `restart_container` | confirm | name | yes | WRITE_TOOL_NOT_SAFE_TO_LIVE_TEST | State-changing; validate routing/plan only, never execute live without explicit authorization |
| frigate | `frigate_activity_details` | read | review_id,event_id | yes | NOT_VALIDATED |  |
| frigate | `frigate_event_activity` | read | event_id | yes | NOT_VALIDATED |  |
| frigate | `frigate_event_snapshot` | read | event_id | yes | NOT_VALIDATED |  |
| frigate | `frigate_recent_activity` | read | camera,label,limit,latest_only,since,until | yes | VALIDATED_LIVE | What happened at the front door recently? |
| frigate | `frigate_recent_events` | read | camera,label,limit,since,until | yes | VALIDATED_LIVE | (indirectly, plan-level) front-door recency queries |
| frigate | `frigate_snapshot` | read | camera | yes | NOT_VALIDATED |  |
| frigate | `frigate_stats` | read | - | yes | VALIDATED_LIVE | What are the current Frigate camera stats? |
| frigate | `frigate_status` | read | - | yes | NOT_VALIDATED |  |
| gpu | `get_gpu_status` | read | - | yes | VALIDATED_LIVE | What's the current GPU usage? (correctly reports no telemetry available) |
| home | `home_activate_scene` | write_low | scene | yes | NOT_VALIDATED |  |
| home | `home_control` | write_low | entity_or_area,action,parameters | yes | VALIDATED_LIVE | Turn on the neon lights. |
| home | `home_find_device` | read | query | yes | VALIDATED_LIVE | Find all my light devices. |
| home | `home_get_area_state` | read | area | yes | NOT_VALIDATED |  |
| home | `home_get_state` | read | entity_or_area | yes | VALIDATED_LIVE | What's the state of the neon lights? |
| internet | `web_fetch` | read | url,max_chars,extract | yes | VALIDATED_LIVE | (as part of the web_search research loop) |
| internet | `web_search` | read | query,max_results,recency_days,domains,search_type | yes | VALIDATED_LIVE | Search the web for the latest news on SpaceX. |
| knowledge | `wikipedia_search` | read | query | yes | NOT_VALIDATED |  |
| lidarr | `lidarr_artist_status` | read | query | yes | NOT_VALIDATED |  |
| lidarr | `lidarr_health` | read | - | yes | VALIDATED_LIVE | Any Lidarr health issues? |
| lidarr | `lidarr_import_status` | read | album_ids | yes | NOT_VALIDATED |  |
| lidarr | `lidarr_missing_tracks` | read | - | yes | NOT_VALIDATED |  |
| lidarr | `lidarr_queue` | read | - | yes | NOT_VALIDATED |  |
| lidarr | `lidarr_search_album` | read | query | yes | NOT_VALIDATED |  |
| lidarr | `lidarr_search_artist` | read | query | yes | VALIDATED_LIVE | Search Lidarr for the artist Radiohead. |
| lists | `add_list_items` | write_low | list,item,items | yes | VALIDATED_LIVE | Add milk and eggs to my grocery list. |
| lists | `clear_completed_list_items` | write_low | list | yes | WRITE_TOOL_NOT_SAFE_TO_LIVE_TEST | State-changing; validate routing/plan only, never execute live without explicit authorization |
| lists | `list_items` | read | list | yes | VALIDATED_LIVE | What's on my grocery list? |
| lists | `remove_list_item` | write_low | list,item | yes | VALIDATED_LIVE | Remove milk from my grocery list. |
| media_pipeline | `investigate_downloads` | read | - | yes | VALIDATED_LIVE | Investigate my downloads across all services. |
| media_pipeline | `investigate_media_pipeline` | read | query,entity_type,focus | yes | NOT_VALIDATED |  |
| media_pipeline | `investigate_plex_missing` | read | query | **MISSING** | VALIDATED_LIVE | Why isn't The Matrix showing up in my Plex library? |
| media_planner | `media_diagnose` | read | workflow_id | yes | NOT_VALIDATED |  |
| media_planner | `media_get_workflow` | read | workflow_id | yes | NOT_VALIDATED |  |
| media_planner | `media_plan_goal` | read | goal,media_type,session_id | yes | PARTIALLY_VALIDATED | Do I have Dumb and Dumber in Plex? -- works, but one run hit a real ReadTimeout (likely transient infra, not yet proven reliable) |
| media_planner | `media_policy_status` | read | media_type | yes | NOT_VALIDATED |  |
| media_planner | `media_status` | read | workflow_id,query,title,media_type | yes | VALIDATED_LIVE | Did I already request The Room? |
| media_planner | `media_storage_status` | read | media_type | yes | NOT_VALIDATED |  |
| music_enricher | `music_enricher_quarantine` | read | query | yes | NOT_VALIDATED |  |
| music_enricher | `music_enricher_status` | read | - | yes | NOT_VALIDATED |  |
| netdata | `netdata_system_summary` | read | - | yes | NOT_VALIDATED |  |
| overseerr | `overseerr_recent_requests` | read | - | yes | NOT_VALIDATED |  |
| overseerr | `overseerr_status` | read | - | yes | VALIDATED_LIVE | What's the Overseerr status? |
| plex | `plex_artist_library` | read | query | yes | NOT_VALIDATED |  |
| plex | `plex_current_sessions` | read | - | yes | NOT_VALIDATED |  |
| plex | `plex_library_counts` | read | - | yes | VALIDATED_LIVE | How many movies and shows do I have in Plex? |
| plex | `plex_library_lookup` | read | query,library | yes | NOT_VALIDATED |  |
| plex | `plex_recently_added` | read | media_type,library,limit | yes | VALIDATED_LIVE | What's new in my Plex library? |
| plex | `plex_search` | read | query,library | yes | VALIDATED_LIVE | Is anything playing on Plex right now? |
| qbittorrent | `qbittorrent_get` | read | hash | yes | NOT_VALIDATED |  |
| qbittorrent | `qbittorrent_list` | read | filter | yes | NOT_VALIDATED |  |
| qbittorrent | `qbittorrent_summary` | read | - | yes | NOT_VALIDATED |  |
| radarr | `radarr_health` | read | - | yes | VALIDATED_LIVE | Any Radarr health issues? |
| radarr | `radarr_missing_movies` | read | - | yes | NOT_VALIDATED |  |
| radarr | `radarr_queue` | read | - | yes | NOT_VALIDATED |  |
| radarr | `radarr_search_movie` | read | query | yes | VALIDATED_LIVE | Search Radarr for the movie Inception. |
| server | `get_server_overview` | read | - | yes | NOT_VALIDATED |  |
| slskd | `slskd_downloads` | read | query,include_completed | yes | VALIDATED_LIVE | Any active Soulseek downloads? |
| slskd | `slskd_search_status` | read | - | yes | NOT_VALIDATED |  |
| sonarr | `sonarr_health` | read | - | yes | VALIDATED_LIVE | Any Sonarr health issues? |
| sonarr | `sonarr_missing_episodes` | read | - | yes | NOT_VALIDATED |  |
| sonarr | `sonarr_queue` | read | - | yes | NOT_VALIDATED |  |
| sonarr | `sonarr_search_series` | read | query | yes | NOT_VALIDATED |  |
| storage | `get_storage_status` | read | - | yes | VALIDATED_LIVE | How much storage do I have left on the server? |
| torbox | `torbox_status` | read | - | yes | VALIDATED_LIVE | What's the Torbox status? |
| utility | `calculator` | read | expression | yes | VALIDATED_LIVE | What's 452 times 17? |
| utility | `current_datetime` | read | timezone | yes | VALIDATED_LIVE | What time is it in Tokyo right now? |
| utility | `unit_convert` | read | value,from_unit,to_unit | yes | VALIDATED_LIVE | Convert 5 gigabytes to megabytes. |
| weather | `weather_forecast` | read | location,days_from_now | yes | VALIDATED_LIVE | What's the weather like right now? |
