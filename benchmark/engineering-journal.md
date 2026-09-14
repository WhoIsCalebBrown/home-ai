# Home-AI engineering journal

## 2026-09-14 — cross-domain context contamination

- Problem: a stale `weather` context for Floridablanca rewrote explicit Welland, news, and Lidarr/Plex turns into `weather in Floridablanca today`.
- Evidence: production `discovery-debug.jsonl`, client `gaming_pc`; the first bad route was the Welland turn. The retained context already contained `location=Floridablanca`. `resolved_followup_text()` treated any `and` as a weather follow-up and returned the stale location before discovery. The same rewrite persisted through the news and media turns.
- Public patterns reviewed: Ollama native tool-call message/result sequencing (official `ollama/ollama` tool-calling documentation, main at `53fed2611281`, accessed 2026-09-14); OpenVoiceOS serialized `Session` and explicit add/remove context (official `OpenVoiceOS/ovos-core`, dev at `4a095d879468`, accessed 2026-09-14); Open WebUI native tools and stable prompt/tool context (official `open-webui/open-webui`, main at `0a7c15832fb3`, accessed 2026-09-14).
- Hypothesis: current-turn explicit topic/entity state must outrank inherited context; only referential follow-ups may inherit the prior scoped state.
- Change: added routing-only alias normalization, explicit topic detection, bounded weather-location extraction, current-turn context replacement, scoped provenance metadata, and Ollama request trace records. Added regression cases for Welland parsing, topic changes, and LiDAR/Plexium aliases.
- Result: local grounding regressions pass. Deployment pending live browser validation.
- Keep/rollback: reversible source change; preserve baseline commits `0089884` and `de3d6ed`.

## 2026-09-14 — live validation and production fallback

- Live reproduction after the fix: Toronto → St. Catharines → tomorrow preserved the replaced location and day offset. Plex count, downloads provenance, Frigate event freshness, and visual snapshot follow-ups passed through HTTPS/WSS/STT/tool/Qwen/TTS/browser.
- 9B re-check: current Ollama `qwen3.5:9b-q4_K_M` remained partially offloaded with Faster-Whisper active: 13% CPU / 87% GPU at 4096 and 12% CPU / 88% GPU at 6144; 8192 previously measured 14% CPU / 86% GPU. It was not promoted.
- 4B production baseline: `qwen3.5:4b`, `LLM_CONTEXT=8192`, `100% GPU`, Ollama context 8192. Ten warm browser interactions measured median first browser playback 2.99 s and empirical P95 3.44 s.
- TTS/GPU: Kokoro `am_puck` at `1.18` is running on GTX 1660; Chatterbox remains installed but stopped. Frigate and Plex containers are healthy.
- Quantization research: current Ollama tags expose 9B `q4_K_M` (6.6 GB), `q8_0` (11 GB), and larger bf16/MLX variants; no smaller official CUDA-compatible 9B tag was selected. Lower-bit community variants were not installed because current llama.cpp/Qwen issue history shows backend/quant-specific risks.
- Keep/rollback: final runtime is the safe 4B/8192 + Kokoro configuration. Exact previous 4B/4096 + Chatterbox templates remain backed up on the host; source commits `38fd650`, `e3f53e3`, and `b39d25d` contain the scoped-state, tracing, and explicit deployment-default changes.

## Research notes

- No third-party code was copied or installed. Only architectural ideas were adapted.
- Ollama’s current documented native sequence is assistant tool call → tool-role result → follow-up chat request.
- OVOS currently carries a serialized session on every bus message and exposes explicit context add/remove/clear events.
- Open WebUI currently treats native function calling as the default and emphasizes keeping stable system/tool context for cache reuse.

## 2026-09-14 — 9B mixed-domain synthesis regression

- Problem: live 9B turns retained camera/weather context and final synthesis sometimes answered an older weather question despite correct web/media tool selection.
- Evidence: production trace showed `route_query` being rewritten with `front door camera` for the GPU turn; the Ollama request contained long prior history but no authoritative current-turn interpretation. The generic unavailable string was also unconditional for non-weather/non-Lidarr failures.
- Public pattern adapted: current-turn canonical request plus scoped session context, following OVOS explicit session context; Ollama’s documented assistant-tool-result sequencing; Open WebUI’s separation of normalized message metadata from chat content. References and access-date commits are recorded above.
- Change: added explicit domain-transition gating (server terms override camera language), current-news follow-up preflight, social acknowledgement short-circuit, media correction handling, domain-scoped unavailable responses, and a shared `resolved_current_request` synthesis contract containing raw/normalized text, domain, entities, referents, selected tools, and result keys.
- Result: local regressions and CI passed; deployed assistant image commit `2672fe0`. Live Toronto→tomorrow, Plex count, Frigate event, and provenance fixtures reached the new contract; 9B remains active at 8192.
- Keep/rollback: assistant-only deployment; rollback to the prior assistant image/template remains available. No tools, model, TTS, camera, or media services were changed.

## 2026-09-14 — repair turns, safe lists, and measured concurrency

- Public references checked: OHF-Voice/wyoming-faster-whisper `main` / v3.5.0 (`5b5854f`, accessed 2026-09-14), OpenVoiceOS/ovos-core `dev` (`4a095d879468`), Open WebUI `main` (`0a7c15832fb3`), and Home Assistant core/frontend current branches. No third-party code was copied. The adapted patterns were contextual vocabulary/entity biasing, explicit serialized session state, first-class tool-result provenance, and bounded risk classes.
- Change: `845dd0c` preserves the immediately preceding resolved media request for STT-shaped corrections such as “I’m at Lidar”, so a repair reruns the previous media investigation instead of incorrectly switching to a Lidarr health query.
- Change: `e82c058`/`228af0f` add persistent, bounded grocery/shopping/packing/todo list tools. Read-only and low-risk list writes do not require confirmation; restart/destructive actions retain confirmation. Storage is `/mnt/cache/appdata/voice-tools/home-ai-lists.json` on the host.
- Change: `e82c058` adds a mobile-first assistant UI with explicit listening/transcribing/thinking/speaking status and a collapsed diagnostics panel. It is responsive, but a full installable PWA manifest/service worker is not yet enabled.
- Change: media/download investigations now run independent read-only service calls concurrently. A live media investigation measured roughly 975 ms parent wall time with per-service timings recorded; no state-changing tools were parallelized.
- Change: `bbfd4b3` adds a bounded recovery for clipped STT list phrasing (“the milk on my grocery list”), without globally rewriting words. CI/build is pending before deployment.
- Safety: no Home Assistant container is present on the host, so smart-home tools remain intentionally unavailable rather than being claimed or faked. No new public port, unrestricted shell, SQL, Docker socket, or credential path was added.
