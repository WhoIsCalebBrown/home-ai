# Tool discovery research

This implementation adapts architectural ideas, not third-party source code.

## References reviewed

- [HA-MCP](https://github.com/homeassistant-ai/ha-mcp): BM25/search-based deferred discovery for large catalogs, bounded result counts, and explicit read/write annotations. MIT-licensed. We adopted the bounded server-side discovery pattern and kept execution inside Home-AI-Tools.
- [Plex MCP Server](https://github.com/niavasha/plex-mcp-server): a unified typed Plex/Arr surface, separate tool schemas/registry, and read operations kept distinct from opt-in writes. MIT-licensed. We used this as a checklist for media capability coverage; no code was copied.
- [yarr](https://github.com/dinglebear-ai/yarr): service adapters behind a constrained media-fleet boundary, explicit service status, bounded responses, and immutable deployment guidance. MIT-licensed. We retained our existing Python adapters rather than adding another server.
- [NickM-27/VoiceAssistant](https://github.com/NickM-27/VoiceAssistant): speech formatting belongs in the TTS proxy/frontend, while tools and integrations remain separate. We already follow this separation with the NeMo speech frontend.
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling): the application supplies schemas, executes returned calls, and sends results back as tool messages. This remains the execution loop; discovery now limits the schemas supplied per request.
- [Open WebUI tools](https://docs.openwebui.com/features/extensibility/plugin/tools/) and [MCP guidance](https://docs.openwebui.com/features/extensibility/mcp/): external tool servers should hold credentials server-side, be admin-scoped, and be treated as a security boundary. We did not add Open WebUI, MCP, shell execution, or another container.

## Adopted

- A typed capability registry with aliases, examples, capability group, freshness, read/write classification, confirmation requirement, service dependency, and visual-evidence flag.
- Deterministic bounded discovery (`/discover`) using metadata token overlap plus intent-specific boosts for Frigate health, events, and snapshots. It does not run a second LLM call.
- Read-only tools for Open-Meteo weather, safe arithmetic, storage/temperature conversion, current date/time, and Wikipedia search.
- Public web search results now include title, URL, domain, snippet, date when supplied by SearXNG, and rank.
- Narrow follow-up context for weather locations and media-pipeline entities, while retaining exact investigation provenance.

## Rejected

- Installing Home Assistant, Open WebUI, or multiple MCP servers: they would duplicate the existing orchestration/security boundary.
- Generic shell/SQL/filesystem/Docker passthrough: incompatible with the existing allowlist and confirmation model.
- An embedding model or a second Qwen routing pass: unnecessary latency and another failure surface for this catalog size.
- Paid places/YouTube providers: no credential was available or authorized; no disabled integration boundary was needed for this pass.
