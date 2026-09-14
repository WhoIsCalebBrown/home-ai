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
