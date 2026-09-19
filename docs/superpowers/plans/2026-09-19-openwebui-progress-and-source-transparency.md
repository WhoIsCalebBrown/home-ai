# Open WebUI Progress and Source Transparency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream a safe, compact progress preamble to unchanged Open WebUI, replace opaque tool-name traces with useful verified source links, keep all diagnostics out of speech, and handle quiet Canadian-news periods honestly.

**Architecture:** The completed compatibility probe proved Open WebUI 0.11.3 ignores non-content status metadata, so the user selected normal content streaming instead of maintaining an Open WebUI extension. Add two focused server-owned boundaries: a pure trace/source projector and a request-scoped progress emitter feeding a bounded SSE queue. The saved graphical response contains at most four safe progress lines, the final answer, and rich source diagnostics; TTS receives only the separately captured plain answer.

**Tech Stack:** Python 3.12, FastAPI/Starlette, asyncio, httpx, OpenAI-compatible SSE, Open WebUI 0.11.3, pytest, vanilla browser JavaScript, Docker.

**Spec:** `docs/superpowers/specs/2026-09-19-openwebui-progress-and-source-transparency-design.md`

## Global Constraints

- Do not expose raw tool arguments, search queries, result bodies, snippets, exception text, credentials, URL fragments, or unapproved query parameters.
- Progress and trace data are display-only and must never reach TTS.
- Do not use synthetic OpenAI tool calls or hidden HTML for progress.
- Prefer successfully fetched final URLs over search-only candidates.
- Permit only public HTTP(S) display links and render remote titles as text, never HTML.
- Limit projected trace output to three sources per search, twelve entries total, and bounded strings/bytes.
- Non-streaming OpenAI-compatible responses retain their existing response shape.
- A failed progress event must not fail the tool call or final answer.
- Open WebUI remains unchanged: no fork, plugin, custom image, or frontend extension.
- Progress is ordinary persisted content in a `**Working**` Markdown preamble, limited to four distinct major-stage lines and separated from the final answer by `---`.
- Run tests through the production-shaped Docker image; host pytest is not available.
- Do not merge, deploy, or alter Home Assistant/Open WebUI configuration within this plan.

## Review Focus

- A client disconnects while a tool is blocked: the responder is cancelled/drained and no background task or queue remains.
- A fetched page returns a hostile title or redirect URL: the UI shows bounded text and never creates an unsafe link.
- A search URL contains tokens, household terms, fragments, or tracking parameters: none appear in progress, trace, logs added by this feature, or speech.
- Several retries and fetches occur quickly: the persisted preamble contains no more than four distinct major-stage lines.
- Open WebUI sends the rich footer alone to `/v1/audio/speech`: the endpoint returns HTTP 204 and invokes no synthesizer.

---

### Task 1: Prove the pinned Open WebUI transient-status contract

**Files:**
- Create: `qa/openwebui_status_probe.py`
- Create: `docs/qa/openwebui-status-probe.md`
- Modify: `docs/open-webui-home-ai.md`
- Test: `qa/openwebui_status_probe.py`

**Interfaces:**
- Consumes: Open WebUI 0.11.3's configured external OpenAI-provider path and standard `chat.completion.chunk` frames.
- Produces: a recorded `PASS` contract naming the exact non-content event Open WebUI renders transiently, or a recorded `BLOCKED` result that pauses this plan until the user selects a documented fallback. The completed probe recorded `BLOCKED`; the user then selected persisted ordinary content with no Open WebUI customization.

- [ ] **Step 1: Add a deterministic mock SSE provider**

Create a small FastAPI probe that emits a role chunk, two candidate status frames, a delayed final content chunk, a stop chunk, and `[DONE]`. Keep it runnable only as QA code:

```python
STATUS_FRAMES = [
    {"choices": [{"index": 0, "delta": {
        "home_ai_status": {"phase": "tool_started", "label": "Reading Example News…"}
    }, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {
        "home_ai_status": {"phase": "tool_finished", "label": "Read Example News"}
    }, "finish_reason": None}]},
]

async def events():
    yield sse_chunk({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
    for frame in STATUS_FRAMES:
        yield sse_chunk(frame)
        await asyncio.sleep(1)
    yield sse_chunk({"choices": [{"index": 0, "delta": {"content": "Final probe answer."}, "finish_reason": None}]})
    yield sse_chunk({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    yield "data: [DONE]\n\n"
```

- [ ] **Step 2: Unit-test the probe's wire format**

Assert that status frames have no `content`, the only content is `Final probe answer.`, and the stream ends exactly once:

```python
def test_probe_never_places_status_in_content():
    frames = list(decoded_probe_frames())
    status = [f for f in frames if "home_ai_status" in f["choices"][0]["delta"]]
    assert [f["choices"][0]["delta"].get("content") for f in status] == [None, None]
    assert content_text(frames) == "Final probe answer."
```

- [ ] **Step 3: Run the probe tests in Docker**

Run:

```bash
docker build -t home-ai-assistant-sdd -f assistant/Dockerfile assistant
docker run --rm --network none --read-only \
  -v "$PWD/qa:/workspace/qa:ro" \
  home-ai-assistant-sdd python -m pytest -q /workspace/qa/openwebui_status_probe.py
```

Expected: all probe wire-format tests pass.

- [ ] **Step 4: Exercise the probe through the pinned Open WebUI**

Run the mock provider on an isolated Docker network with a disposable Open WebUI 0.11.3 container. Point only that disposable container at the probe. Send one chat request and record all three observations in `docs/qa/openwebui-status-probe.md`:

1. Whether `Reading Example News…` appears before `Final probe answer.`.
2. Whether the status disappears or remains outside the saved assistant message after completion and page reload.
3. Whether `Final probe answer.` is the only persisted assistant content.

Record the exact request, emitted frames, pinned image digest, observed UI behavior, and a `PASS` or `BLOCKED` decision. Do not change the production Open WebUI provider.

- [ ] **Step 5: Apply the decision gate**

`PASS` requires all three observations above. Name the proven event shape in `docs/open-webui-home-ai.md` and continue to Task 2. If any observation fails, record `BLOCKED`, stop this plan, and present these exact choices to the user:

- Maintain a minimal Open WebUI extension that renders `home_ai_status` transiently.
- Persist compact progress lines in assistant content and accept that they remain in conversation history.

Do not choose automatically.

Recorded decision after the completed probe: the user selected persisted compact
progress text so the published Open WebUI container remains unchanged. Tasks 2+
therefore use the revised persistent-preamble contract in Global Constraints and
Task 4.

- [ ] **Step 6: Commit the probe evidence**

```bash
git add qa/openwebui_status_probe.py docs/qa/openwebui-status-probe.md docs/open-webui-home-ai.md
git commit -m "test: prove Open WebUI progress event compatibility"
```

### Task 2: Project raw tool results into a safe rich trace

**Files:**
- Create: `assistant/trace_projection.py`
- Create: `assistant/test_trace_projection.py`
- Modify: `assistant/Dockerfile`

**Interfaces:**
- Consumes: `list[dict]` tool results in the existing `live_results` shape.
- Produces: `project_trace(live_results: list[dict]) -> list[dict]` containing only `tool`, `action`, `status`, and bounded `sources`; `safe_display_url(value: str) -> str | None`.

- [ ] **Step 1: Write failing safe-URL and source-projection tests**

Cover a successful redirected fetch, a search result, deduplication, and every Review Focus URL/title class:

```python
def test_project_trace_prefers_fetched_final_source_and_drops_sensitive_data():
    trace = project_trace([
        {"tool": "web_search", "status": "ok", "result": {
            "query": "private household terms",
            "results": [{"title": "Candidate", "url": "https://news.example/a?token=secret", "snippet": "hidden"}],
        }},
        {"tool": "web_fetch", "status": "ok", "result": {
            "title": "Final story", "url": "https://news.example/final?utm_source=x#part", "published": "2026-09-19",
            "content": "must not escape",
        }},
    ])
    assert trace[-1]["sources"] == [{
        "title": "Final story", "domain": "news.example",
        "url": "https://news.example/final", "kind": "fetched",
        "published": "2026-09-19",
    }]
    assert "private" not in repr(trace)
    assert "hidden" not in repr(trace)
    assert "content" not in repr(trace)
```

Parameterized negative cases must include `javascript:`, `data:`, user-info, localhost, `.local`, private/link-local IP literals, control characters, signed/token query strings, and a hostile `</a><img onerror=alert(1)>` title.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
docker build -t home-ai-assistant-sdd -f assistant/Dockerfile assistant
docker run --rm --network none --read-only home-ai-assistant-sdd \
  python -m pytest -q assistant/test_trace_projection.py
```

Expected: import failure because `trace_projection.py` does not exist.

- [ ] **Step 3: Implement the pure projector**

Use standard-library parsing and IP validation only. Define fixed action labels and limits:

```python
MAX_TRACE_ENTRIES = 12
MAX_SOURCES_PER_SEARCH = 3
MAX_TITLE_CHARS = 180
MAX_DOMAIN_CHARS = 253

ACTION_LABELS = {
    "web_search": "Searched the web",
    "web_fetch": "Opened source",
    "weather_forecast": "Checked the forecast",
    "plex_search": "Checked Plex",
    "home_get_state": "Checked your home",
}

def clean_text(value: object, limit: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]

def safe_display_url(value: str) -> str | None:
    if not value or re.search(r"[\x00-\x1f\x7f]", value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    if host in {"localhost", "unraid", "tower", "host.docker.internal", "metadata.google.internal"}:
        return None
    if host.endswith((".local", ".lan", ".internal", ".docker", ".home")):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local
                    or address.is_multicast or address.is_reserved or address.is_unspecified):
        return None
    display_host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))

def project_trace(live_results: list[dict]) -> list[dict]:
    entries: list[dict] = []
    seen_urls: set[str] = set()
    for raw in live_results[:MAX_TRACE_ENTRIES]:
        tool = str(raw.get("tool") or "unknown")
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        ok = raw.get("status") == "ok" and raw.get("operation_ok", True) is not False
        empty_search = tool == "web_search" and int(result.get("result_count") or 0) == 0
        status = "failed" if not ok else "no results" if empty_search else "complete"
        sources: list[dict] = []
        if tool == "web_fetch" and ok:
            url = safe_display_url(str(result.get("url") or ""))
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({
                    "title": clean_text(result.get("title"), MAX_TITLE_CHARS),
                    "domain": clean_text(urlsplit(url).hostname, MAX_DOMAIN_CHARS),
                    "url": url,
                    "kind": "fetched",
                    "published": clean_text(result.get("published"), 40) or None,
                })
        elif tool == "web_search" and ok:
            for candidate in list(result.get("results") or [])[:MAX_SOURCES_PER_SEARCH]:
                if not isinstance(candidate, dict):
                    continue
                domain = clean_text(candidate.get("domain"), MAX_DOMAIN_CHARS)
                sources.append({
                    "title": clean_text(candidate.get("title"), MAX_TITLE_CHARS),
                    "domain": domain,
                    "url": None,
                    "kind": "candidate",
                    "published": clean_text(candidate.get("date"), 40) or None,
                })
        entries.append({
            "tool": tool,
            "action": ACTION_LABELS.get(tool, "Used an assistant tool"),
            "status": status,
            "sources": sources,
        })
        if len(json.dumps(entries, ensure_ascii=False).encode("utf-8")) > 16_384:
            entries.pop()
            break
    return entries
```

Import `ipaddress`, `json`, `re`, and `urlsplit`/`urlunsplit` from `urllib.parse` in this focused module. The implementation must never copy arbitrary keys from a raw result.

- [ ] **Step 4: Run focused and malicious-input tests**

Run the same Docker command. Expected: all trace projection tests pass and `repr(trace)` contains none of the forbidden raw data.

- [ ] **Step 5: Commit the projector**

```bash
git add assistant/trace_projection.py assistant/test_trace_projection.py assistant/Dockerfile
git commit -m "feat: project safe rich tool traces"
```

### Task 3: Render rich trace entries in both graphical clients

**Files:**
- Modify: `assistant/voice-api-app.py:5350-6420,6552-6760`
- Modify: `assistant/voice-api-index.html`
- Modify: `assistant/test_openai_gateway.py`
- Create: `assistant/test_trace_rendering.py`
- Test: `qa/test_assistant_conversation_integration.py`

**Interfaces:**
- Consumes: `project_trace(live_results)` from Task 2.
- Produces: `emit_trace(ws, request_id: str, live_results: list[dict])`; `openai_tool_trace_footer(trace: list[dict]) -> str` with a stable marker; native `trace.entries` rendering.

- [ ] **Step 1: Add failing OpenAI-footer tests**

Assert friendly actions, safe Markdown links, stable marker, deduplication, bounds, and no raw internal fields:

```python
def test_rich_footer_names_opened_sources_without_raw_tool_data():
    footer = app.openai_tool_trace_footer([{
        "tool": "web_fetch", "action": "Opened source", "status": "complete",
        "sources": [{"title": "Canada update", "domain": "cbc.ca", "url": "https://cbc.ca/news/update", "kind": "fetched"}],
    }])
    assert "<!-- home-ai-display-trace -->" in footer
    assert "Opened source" in footer
    assert "[Canada update](https://cbc.ca/news/update)" in footer
    assert "web_fetch" not in footer
```

- [ ] **Step 2: Add a failing native-renderer safety test**

Extract the trace renderer into a named browser function and test that it creates anchors with `textContent`, rejects a missing/unsafe URL, and never assigns remote data through `innerHTML`. Use the repository's available JavaScript test runner; if none exists, test the generated HTML/JS contract from Python and include a live disposable-browser QA assertion in Task 7.

- [ ] **Step 3: Confirm RED**

Rebuild `home-ai-assistant-sdd` and run:

```bash
docker run --rm --network none --read-only home-ai-assistant-sdd \
  python -m pytest -q assistant/test_openai_gateway.py assistant/test_trace_rendering.py \
  qa/test_assistant_conversation_integration.py -k 'trace or source'
```

Expected: failures because the current trace exposes only tool/status.

- [ ] **Step 4: Integrate the shared projection**

Import `project_trace`, add one `emit_trace()` helper, and replace every hand-written trace-dictionary comprehension in `respond()`. Capture the already-projected latest trace in `_OpenAIResponseSocket`. Do not pass raw `live_results` into either frontend.

- [ ] **Step 5: Render friendly trace and sources**

Update `openai_tool_trace_footer()` to emit the stable marker, friendly activity, and safe source links. Update the native UI trace handler to create DOM nodes and anchors using `document.createElement`, `.textContent`, and the server-approved `href`. Preserve the final answer above the trace.

- [ ] **Step 6: Run focused and conversation tests**

Expected: rich trace tests pass; existing conversation answers and tool authorization remain unchanged.

- [ ] **Step 7: Commit graphical trace integration**

```bash
git add assistant/voice-api-app.py assistant/voice-api-index.html \
  assistant/test_openai_gateway.py assistant/test_trace_rendering.py \
  qa/test_assistant_conversation_integration.py
git commit -m "feat: show verified sources in tool traces"
```

### Task 4: Stream a bounded persistent progress preamble before tool completion

**Files:**
- Create: `assistant/progress_events.py`
- Create: `assistant/test_progress_events.py`
- Modify: `assistant/voice-api-app.py:2127-2171,6552-6830`
- Modify: `assistant/test_openai_gateway.py`
- Test: `qa/test_assistant_conversation_integration.py`

**Interfaces:**
- Consumes: Task 1's verified result that Open WebUI renders only ordinary content deltas and the user's choice to leave its container unchanged.
- Produces: `progress_sink_context: ContextVar[Callable[[dict], Awaitable[None]] | None]`; `safe_progress_event(tool: str, arguments: dict, phase: str, result: dict | None = None) -> dict | None`; queue-backed OpenAI SSE whose progress deltas form one `**Working**` preamble with at most four unique lines and a `---` separator before the answer.

- [ ] **Step 1: Write failing progress-label privacy tests**

```python
def test_fetch_progress_exposes_domain_but_not_path_or_query():
    event = safe_progress_event(
        "web_fetch", {"url": "https://www.cbc.ca/news/private?q=household-token"}, "started"
    )
    assert event == {"phase": "tool_started", "label": "Reading CBC…"}
    assert "private" not in repr(event)
    assert "household-token" not in repr(event)

def test_unknown_tool_progress_is_generic():
    assert safe_progress_event("future_tool", {"secret": "x"}, "started") == {
        "phase": "tool_started", "label": "Working…"
    }
```

Add cases for internal URLs, failures, repeated identical events, long hostnames, and invalid argument shapes.

- [ ] **Step 2: Write a failing timing test for the OpenAI stream**

Use a fake tool blocked on an `asyncio.Event`. Start the ASGI stream, read ordinary content frames until `**Working**` and the first safe progress line arrive, and assert the tool has not completed. Release the event, then assert the persisted content is exactly one bounded progress preamble, `---`, one final answer, one stop, and one `[DONE]`.

- [ ] **Step 3: Confirm RED**

Run:

```bash
docker build -t home-ai-assistant-sdd -f assistant/Dockerfile assistant
docker run --rm --network none --read-only home-ai-assistant-sdd \
  python -m pytest -q assistant/test_progress_events.py assistant/test_openai_gateway.py \
  qa/test_assistant_conversation_integration.py -k 'openai and stream'
```

Expected: no progress helper and the first chunk remains blocked behind `_openai_chat_turn()`.

- [ ] **Step 4: Implement safe progress mapping and coalescing**

Build labels only from a fixed mapping plus normalized public hostname. Never include query text or raw results. Keep request-local coalescing state that suppresses duplicates and caps the persisted preamble at four distinct major-stage lines. Emit started/major-stage lines only; do not persist routine finished events.

- [ ] **Step 5: Instrument the common tool boundary**

In `invoke_tool()`, emit a best-effort `started` event immediately before the HTTP call and a best-effort `finished` or `failed` event after it. Wrap progress emission in its own exception guard so it cannot change tool outcomes.

- [ ] **Step 6: Replace capture-then-stream with a bounded queue**

For `stream=true`, create `asyncio.Queue(maxsize=32)` before starting the responder. Run `_openai_chat_turn()` in a supervised task with the request's progress sink installed. On the first safe event, the SSE generator emits ordinary content `**Working**\n`; it then emits at most four `- <label>\n` content deltas. Before final content it emits `\n---\n\n`, followed by the answer/footer content, stop, and DONE frames. On disconnect or cancellation, cancel and await the responder task under `contextlib.suppress(asyncio.CancelledError)`.

- [ ] **Step 7: Preserve the non-streaming path**

Keep `stream=false` using `_openai_chat_turn()` directly. Verify its JSON schema, session header, display footer, and tool behavior are byte-for-byte compatible except for the intentionally richer footer.

- [ ] **Step 8: Run timing, disconnect, flood, and existing gateway tests**

Expected: progress arrives before the fake tool completes; disconnect leaves no tasks; 100 repeated tool events produce no more than four progress lines; the saved content contains the preamble and separator; existing session/housekeeping tests pass.

- [ ] **Step 9: Commit live progress**

```bash
git add assistant/progress_events.py assistant/test_progress_events.py \
  assistant/voice-api-app.py assistant/test_openai_gateway.py \
  qa/test_assistant_conversation_integration.py
git commit -m "feat: stream safe tool progress to Open WebUI"
```

### Task 5: Seal the rich display-to-speech boundary

**Files:**
- Modify: `assistant/voice-api-app.py:250-280,6735-6760,6838-6860`
- Modify: `assistant/test_tts_normalization.py`
- Test: `assistant/test_openai_gateway.py`

**Interfaces:**
- Consumes: stable trace marker/footer from Task 3 and persistent `**Working**` preamble from Task 4.
- Produces: `remove_openai_display_metadata(text: str) -> str` that strips the leading progress block and final trace; progress-only and trace-only TTS requests return 204.

- [ ] **Step 1: Write failing rich-trace speech tests**

```python
def test_rich_sources_and_persistent_progress_are_never_spoken():
    displayed = (
        "**Working**\n- Searching recent Canadian headlines…\n"
        "- Reading CBC News…\n\n---\n\n"
        "Here is the Canadian roundup.\n\n<!-- home-ai-display-trace -->\n"
        "Research activity\n- Opened CBC News\n"
        "Sources\n- [Canada update](https://cbc.ca/news/update)"
    )
    assert app.remove_openai_display_metadata(displayed) == "Here is the Canadian roundup."
```

Add endpoint tests proving the registered full display response synthesizes only its plain answer; progress-only, footer-only, and source-only fragments return 204; and the graphical preamble never reaches the audio emitter.

- [ ] **Step 2: Confirm RED against the richer footer**

Run the TTS/gateway tests. Expected: the current flattened fallback sanitizer does not remove rich source lines completely.

- [ ] **Step 3: Implement marker-based fail-closed stripping**

Strip a leading `**Working**` block through its `---` separator, then use the exact generated trace marker as the final fallback boundary; retain the legacy footer regex for old saved messages. Keep the exact display-to-speech registry as the preferred path and retain its bounded lifetime/size behavior.

- [ ] **Step 4: Run all TTS normalization and gateway tests**

```bash
docker build -t home-ai-assistant-sdd -f assistant/Dockerfile assistant
docker run --rm --network none --read-only home-ai-assistant-sdd \
  python -m pytest -q assistant/test_tts_normalization.py assistant/test_openai_gateway.py \
  -k 'trace or speech or progress'
```

Expected: all tests pass and no diagnostic text reaches `synthesize_pocket`.

- [ ] **Step 5: Commit speech isolation**

```bash
git add assistant/voice-api-app.py assistant/test_tts_normalization.py assistant/test_openai_gateway.py
git commit -m "fix: keep rich progress and sources out of speech"
```

### Task 6: Broaden sparse same-day Canadian news without losing scope

**Files:**
- Modify: `assistant/voice-api-app.py:3850-4300,5800-6220`
- Modify: `assistant/test_grounding_regressions.py`
- Modify: `qa/test_assistant_conversation_integration.py`

**Interfaces:**
- Consumes: existing deep-news research request, evidence gate, final-domain/date tracking, and projected sources.
- Produces: one disclosed retry using `recency_days=2` (or a bounded weekend window of at most three days) when same-day Canadian evidence is sparse; Canada scope remains mandatory.

- [ ] **Step 1: Write failing sparse-news recovery tests**

Simulate same-day Canada searches that return unrelated global stories, followed by valid Canadian stories in a two-day window:

```python
async def test_sparse_today_canada_broadens_window_without_accepting_world_noise(session):
    answer = await session.respond("Give me an in-depth review of the recent news in Canada today")
    assert session.search_calls[0]["recency_days"] == 1
    assert any(call["recency_days"] in {2, 3} for call in session.search_calls[1:])
    assert "same-day coverage is limited" in answer.casefold()
    assert "Canada" in answer
    assert session.fetched_domains >= {"cbc.ca", "reuters.com"}
    assert "unrelated-world.example" not in session.synthesis_evidence
```

Add a weekend-date case, a still-sparse fallback, and a case proving current-officeholder verification remains mandatory after widening.

- [ ] **Step 2: Confirm RED**

Run focused grounding and conversation cases. Expected: current code either stops after sparse today results or accepts off-scope noise without the disclosed bounded retry.

- [ ] **Step 3: Implement one bounded disclosed widening step**

When the existing evidence gate reports too few relevant Canadian topics/fetched sources for a deep same-day request, issue one retry with the bounded wider window. Preserve the Canada relevance filter and evidence thresholds. Add an explicit synthesis instruction stating the original period was sparse and dates from the wider window must be identified.

- [ ] **Step 4: Run all deep-news and current-role tests**

Expected: same-day strong evidence does not trigger a retry; sparse evidence triggers one retry; unrelated global stories never satisfy the gate; officeholder corroboration is unchanged.

- [ ] **Step 5: Commit quiet-news recovery**

```bash
git add assistant/voice-api-app.py assistant/test_grounding_regressions.py \
  qa/test_assistant_conversation_integration.py
git commit -m "feat: widen sparse Canadian news research honestly"
```

### Task 7: Production-shaped verification and Open WebUI acceptance

**Files:**
- Modify: `qa/openwebui_live_p0.py`
- Modify: `docs/open-webui-home-ai.md`
- Create: `docs/qa/openwebui-progress-source-acceptance.md`

**Interfaces:**
- Consumes: Tasks 1-6 at one reviewed commit.
- Produces: repeatable acceptance evidence and a handoff back to the pre-existing branch completion queue; no deployment.

- [ ] **Step 1: Extend live QA for streaming order and source safety**

Add an authenticated QA case that records event timestamps and asserts the first ordinary progress content precedes final answer content. Assert the persisted final message contains at most four safe progress lines, the separator, answer, and rich trace, but no raw query, snippet, private URL, or tool exception.

- [ ] **Step 2: Add graphical and voice acceptance cases**

Against a disposable unchanged Open WebUI instance, verify visible progress while a fake source blocks, the bounded preamble remains after reload, fetched-source links are clickable, hostile source titles remain safe text, and TTS speaks neither preamble nor sources. Save exact commands and screenshots/observations in the acceptance document.

- [ ] **Step 3: Run focused suites once**

Run trace projection, progress, OpenAI gateway, TTS normalization, grounding/news, and conversation integration tests in the rebuilt production-shaped image.

- [ ] **Step 4: Run one final full suite**

```bash
docker build -t home-ai-assistant-sdd -f assistant/Dockerfile assistant
docker run --rm --network none --read-only home-ai-assistant-sdd python -m pytest -q
```

Expected: all tests pass. Record the exact pass count and warnings once; do not repeat the full suite after documentation-only edits.

- [ ] **Step 5: Verify repository scope**

Run `git diff --check`, inspect the diff from `f5ccd17`, confirm no deployment/configuration files changed unexpectedly, and confirm the worktree is clean.

- [ ] **Step 6: Commit QA evidence and documentation**

```bash
git add qa/openwebui_live_p0.py docs/open-webui-home-ai.md \
  docs/qa/openwebui-progress-source-acceptance.md
git commit -m "test: verify Open WebUI progress and source transparency"
```

## Branch Completion Queue After This Plan

Completing this plan does **not** make the feature branch ready to merge. Work must return to these existing plans and blockers in this order:

1. **Media final-fix wave** — `docs/superpowers/plans/2026-09-19-media-intent-and-request-execution.md`
   - Keep synopsis, plot, cast, “hear about,” and request-status questions read-only.
   - Prevent ambiguous-selection replies from executing a different or explicitly rejected title/year.
   - Make cancellation/rejection override current and inherited request authority.
   - Remove request offers from model-routed discovery/library answers.
   - Preserve the canonical referent for “Can you add that movie for me?” and “Add that movie.”
   - Support positive “Can you please request Dune?” and “Put Dune on my Plex.” without weakening safety.
2. **News residual fix** — `docs/superpowers/plans/2026-09-19-deep-current-news-research.md`
   - Correct title-before-name and bulleted officeholder relationships.
   - Stop ordinary organization prose from becoming officeholder evidence.
   - Preserve source dates across redirect aliases.
   - Reject negated officeholder claims such as “Alice Doe is not the prime minister.”
3. **Deterministic Home Assistant routing** — `docs/superpowers/plans/2026-09-19-deterministic-home-assistant-routing.md`
   - Implement named-light, room/type, state-filter, and whole-home parsing using typed arguments.
   - Report physically switched-off/unavailable lights truthfully without diagnosing them.
4. **Whole-branch verification**
   - Run one cross-feature adversarial review covering news, media, progress/trace/TTS, and home control.
   - Run the final production-shaped suite once.
   - Push the reviewed branch, open the PR, wait for required GitHub checks, and merge only when all blockers are closed.

The PR description must reproduce this queue with each item checked and link the relevant test evidence. No item may be dropped merely because a later feature added more commits.
