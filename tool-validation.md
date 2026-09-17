# Home-AI Tool Validation Inventory

Covers the pre-existing 74-tool registry (`_discoverable_registry()`), validated against real
Open WebUI evidence from two live sweeps tonight (37 + 31 scenarios), not assumed from unit-test
coverage. The 5 new `unraid_*` tools added this session are tracked separately below.
`media_standard_request` is excluded by design (`MODEL_FACING_EXCLUDED_TOOLS`) and is never
model-reachable.

## Summary (pre-existing 74-tool registry)

- **BLOCKED**: 7
- **FAIL**: 12
- **NOT_VALIDATED**: 1
- **PARTIALLY_VALIDATED**: 12
- **VALIDATED_LIVE**: 41
- **WRITE_TOOL_NOT_SAFE_TO_LIVE_TEST**: 1
- **Total**: 74

## Full Inventory

| Service | Tool | Read/Write | Args | Discovery Metadata | Status | Notes |
|---|---|---|---|---|---|---|
| beets | `beets_recent_imports` | read | - | yes | FAIL | misrouted to media_status, nonsensical answer |
| beets | `beets_status` | read | - | yes | VALIDATED_LIVE |  |
| docker | `get_container_logs` | read | name,lines | yes | VALIDATED_LIVE |  |
| docker | `get_container_status` | read | name | yes | FAIL | was misrouted to list_containers -- FIXED this session via a deterministic name-extraction route |
| docker | `list_containers` | read | status | yes | VALIDATED_LIVE |  |
| docker | `restart_container` | confirm | name | yes | WRITE_TOOL_NOT_SAFE_TO_LIVE_TEST | State-changing; routing/plan validated only, never executed live |
| frigate | `frigate_activity_details` | read | review_id,event_id | yes | BLOCKED | requires an existing event_id referent from prior conversation context |
| frigate | `frigate_event_activity` | read | event_id | yes | BLOCKED | requires an existing event_id referent |
| frigate | `frigate_event_snapshot` | read | event_id | yes | BLOCKED | requires an existing event_id referent |
| frigate | `frigate_recent_activity` | read | camera,label,limit,latest_only,since,until | yes | VALIDATED_LIVE |  |
| frigate | `frigate_recent_events` | read | camera,label,limit,since,until | yes | VALIDATED_LIVE |  |
| frigate | `frigate_snapshot` | read | camera | yes | VALIDATED_LIVE |  |
| frigate | `frigate_stats` | read | - | yes | VALIDATED_LIVE |  |
| frigate | `frigate_status` | read | - | yes | PARTIALLY_VALIDATED | answered via frigate_snapshot instead of the dedicated reachability tool; plausible-sounding but not the right tool |
| gpu | `get_gpu_status` | read | - | yes | VALIDATED_LIVE |  |
| home | `home_activate_scene` | write_low | scene | yes | FAIL | misrouted to plex_search entirely |
| home | `home_control` | write_low | entity_or_area,action,parameters | yes | VALIDATED_LIVE |  |
| home | `home_find_device` | read | query | yes | VALIDATED_LIVE |  |
| home | `home_get_area_state` | read | area | yes | FAIL | misrouted to home_control (device tool, not area tool) |
| home | `home_get_state` | read | entity_or_area | yes | VALIDATED_LIVE |  |
| internet | `web_fetch` | read | url,max_chars,extract | yes | VALIDATED_LIVE |  |
| internet | `web_search` | read | query,max_results,recency_days,domains,search_type | yes | VALIDATED_LIVE |  |
| knowledge | `wikipedia_search` | read | query | yes | VALIDATED_LIVE |  |
| lidarr | `lidarr_artist_status` | read | query | yes | FAIL | misrouted to plex_artist_library (wrong domain), errored |
| lidarr | `lidarr_health` | read | - | yes | VALIDATED_LIVE |  |
| lidarr | `lidarr_import_status` | read | album_ids | yes | BLOCKED | requires prior album_ids from a lidarr_missing_tracks workflow |
| lidarr | `lidarr_missing_tracks` | read | - | yes | FAIL | misrouted to lidarr_artist_status, errored |
| lidarr | `lidarr_queue` | read | - | yes | PARTIALLY_VALIDATED | answered via investigate_downloads (real data) instead of the dedicated tool |
| lidarr | `lidarr_search_album` | read | query | yes | PARTIALLY_VALIDATED | misrouted to lidarr_search_artist but stayed in-domain with a reasonable answer |
| lidarr | `lidarr_search_artist` | read | query | yes | VALIDATED_LIVE |  |
| lists | `add_list_items` | write_low | list,item,items | yes | VALIDATED_LIVE |  |
| lists | `clear_completed_list_items` | write_low | list | yes | NOT_VALIDATED |  |
| lists | `list_items` | read | list | yes | VALIDATED_LIVE |  |
| lists | `remove_list_item` | write_low | list,item | yes | VALIDATED_LIVE |  |
| media_pipeline | `investigate_downloads` | read | - | yes | VALIDATED_LIVE |  |
| media_pipeline | `investigate_media_pipeline` | read | query,entity_type,focus | yes | FAIL | total misroute: treated the whole question as a title search |
| media_pipeline | `investigate_plex_missing` | read | query | **MISSING** | VALIDATED_LIVE |  |
| media_planner | `media_diagnose` | read | workflow_id | yes | BLOCKED | requires an existing workflow_id |
| media_planner | `media_get_workflow` | read | workflow_id | yes | BLOCKED | requires an existing workflow_id |
| media_planner | `media_plan_goal` | read | goal,media_type,session_id | yes | PARTIALLY_VALIDATED | Do I have Dumb and Dumber in Plex? -- works, but one run hit a real ReadTimeout (transient infra, not yet proven reliable) |
| media_planner | `media_policy_status` | read | media_type | yes | FAIL | total misroute to web_search; answered about real-world Canadian film policy, not Home-AI's own media policy config |
| media_planner | `media_status` | read | workflow_id,query,title,media_type | yes | VALIDATED_LIVE |  |
| media_planner | `media_storage_status` | read | media_type | yes | PARTIALLY_VALIDATED | answered via get_storage_status (real numbers) instead of the dedicated tool |
| music_enricher | `music_enricher_quarantine` | read | query | yes | PARTIALLY_VALIDATED | answered via investigate_media_pipeline instead of the dedicated tool |
| music_enricher | `music_enricher_status` | read | - | yes | PARTIALLY_VALIDATED | answered via investigate_media_pipeline instead of the dedicated tool |
| netdata | `netdata_system_summary` | read | - | yes | VALIDATED_LIVE |  |
| overseerr | `overseerr_recent_requests` | read | - | yes | VALIDATED_LIVE |  |
| overseerr | `overseerr_status` | read | - | yes | VALIDATED_LIVE |  |
| plex | `plex_artist_library` | read | query | yes | FAIL | misrouted to media_plan_goal, nonsense disambiguation |
| plex | `plex_current_sessions` | read | - | yes | PARTIALLY_VALIDATED | answered via plex_search instead of the dedicated tool |
| plex | `plex_library_counts` | read | - | yes | VALIDATED_LIVE |  |
| plex | `plex_library_lookup` | read | query,library | yes | FAIL | misrouted to media_plan_goal, nonsense disambiguation |
| plex | `plex_recently_added` | read | media_type,library,limit | yes | VALIDATED_LIVE |  |
| plex | `plex_search` | read | query,library | yes | VALIDATED_LIVE |  |
| qbittorrent | `qbittorrent_get` | read | hash | yes | BLOCKED | requires a specific torrent hash |
| qbittorrent | `qbittorrent_list` | read | filter | yes | VALIDATED_LIVE |  |
| qbittorrent | `qbittorrent_summary` | read | - | yes | VALIDATED_LIVE |  |
| radarr | `radarr_health` | read | - | yes | VALIDATED_LIVE |  |
| radarr | `radarr_missing_movies` | read | - | yes | PARTIALLY_VALIDATED | answered via plex_search+investigate_downloads instead of the dedicated tool |
| radarr | `radarr_queue` | read | - | yes | PARTIALLY_VALIDATED | answered via investigate_downloads (real data) instead of the dedicated tool |
| radarr | `radarr_search_movie` | read | query | yes | VALIDATED_LIVE |  |
| server | `get_server_overview` | read | - | yes | FAIL | was misrouted to media_plan_goal -- FIXED this session via the new high_confidence_auto_dispatch mechanism |
| slskd | `slskd_downloads` | read | query,include_completed | yes | VALIDATED_LIVE |  |
| slskd | `slskd_search_status` | read | - | yes | VALIDATED_LIVE |  |
| sonarr | `sonarr_health` | read | - | yes | VALIDATED_LIVE |  |
| sonarr | `sonarr_missing_episodes` | read | - | yes | PARTIALLY_VALIDATED | answered via investigate_downloads (real data) instead of the dedicated tool |
| sonarr | `sonarr_queue` | read | - | yes | PARTIALLY_VALIDATED | investigate_downloads' unsupported-claim guard fired, giving a vague non-answer instead of the real queue count |
| sonarr | `sonarr_search_series` | read | query | yes | FAIL | misrouted to media_plan_goal, nonsense disambiguation |
| storage | `get_storage_status` | read | - | yes | VALIDATED_LIVE |  |
| torbox | `torbox_status` | read | - | yes | VALIDATED_LIVE |  |
| utility | `calculator` | read | expression | yes | VALIDATED_LIVE |  |
| utility | `current_datetime` | read | timezone | yes | VALIDATED_LIVE |  |
| utility | `unit_convert` | read | value,from_unit,to_unit | yes | VALIDATED_LIVE |  |
| weather | `weather_forecast` | read | location,days_from_now | yes | VALIDATED_LIVE |  |

## New Unraid MCP Adapter Tools (added this session)

Final status after the definitive live 13-query acceptance sweep (run
after 5 real routing/data bugs found and fixed -- see commits 99e9588,
daaeade, b2dad56, bda46d5, and the fifth-instance fix in b2dad56/bda46d5).
All 13 acceptance queries now return correct, natural, mutually-consistent
answers verified against the real Unraid MCP's live data.

| Tool | Status | Live Result |
|---|---|---|
| `unraid_storage_status` | VALIDATED_LIVE | "How full is the cache drive?" / "How much space is left on the array?" / "Which disk is fullest?" all answered correctly with real byte-accurate percentages, cross-checked against a direct MCP call |
| `unraid_disk_health` | VALIDATED_LIVE | "Is the array healthy?" / "Are any disks having errors?" correctly identified the one real disabled parity disk without false-flagging virtual devices (flash/Docker vDisk/Log) as unhealthy |
| `unraid_container_status` | VALIDATED_LIVE (after a real bug fix) | Initially reported "Plex isn't running" while Plex was plainly running (CONTAINER_DISPLAY_NAMES "Plex" != real Docker name "Plex-Media-Server", an exact-match miss) -- fixed with a case-insensitive substring fallback against the live container list (bda46d5); "Is Plex running?", "How long has Plex been running?", "How much memory is Home-AI using?" all now correct and cross-consistent with unraid_container_metrics' own numbers |
| `unraid_container_metrics` | VALIDATED_LIVE for its designed questions; KNOWN GAP for storage-breakdown follow-ups | "What's using the most RAM/CPU?", "Are any containers unhealthy?" all correct. NOT valid evidence for a referential storage/cache "what's using it" follow-up -- see capability-gap.md's "Live Continuity-Test Finding" for a case where its CPU/RAM numbers were presented as disk-space consumption; this is a discovery/candidate-filtering gap, not a data-correctness bug in the tool itself |
| `unraid_system_health` | VALIDATED_LIVE | "Give me a quick server status." / "Is anything wrong with the server?" both produced accurate, evidence-backed summaries (array state, CPU/temp, container count, zero fabricated health score) |
