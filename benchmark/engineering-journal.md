# Home-AI engineering journal

## 2026-09-14 — cross-domain context contamination

- Problem: a stale `weather` context for Floridablanca rewrote explicit Welland, news, and Lidarr/Plex turns into `weather in Floridablanca today`.
- Evidence: production `discovery-debug.jsonl`, client `gaming_pc`; the first bad route was the Welland turn. The retained context already contained `location=Floridablanca`. `resolved_followup_text()` treated any `and` as a weather follow-up and returned the stale location before discovery. The same rewrite persisted through the news and media turns.
- Public patterns reviewed: Ollama native tool-call message/result sequencing (official `ollama/ollama` tool-calling documentation, main at `53fed2611281`, accessed 2026-09-14); OpenVoiceOS serialized `Session` and explicit add/remove context (official `OpenVoiceOS/ovos-core`, dev at `4a095d879468`, accessed 2026-09-14); Open WebUI native tools and stable prompt/tool context (official `open-webui/open-webui`, main at `0a7c15832fb3`, accessed 2026-09-14).
- Hypothesis: current-turn explicit topic/entity state must outrank inherited context; only referential follow-ups may inherit the prior scoped state.
- Change: added routing-only alias normalization, explicit topic detection, bounded weather-location extraction, current-turn context replacement, scoped provenance metadata, and Ollama request trace records. Added regression cases for Welland parsing, topic changes, and LiDAR/Plexium aliases.
- Result: local grounding regressions pass. Deployment pending live browser validation.
- Keep/rollback: reversible source change; preserve baseline commits `0089884` and `de3d6ed`.

## Research notes

- No third-party code was copied or installed. Only architectural ideas were adapted.
- Ollama’s current documented native sequence is assistant tool call → tool-role result → follow-up chat request.
- OVOS currently carries a serialized session on every bus message and exposes explicit context add/remove/clear events.
- Open WebUI currently treats native function calling as the default and emphasizes keeping stable system/tool context for cache reuse.
