# Open WebUI Progress and Source Transparency

Date: 2026-09-19

## Context

Open WebUI currently connects to the assistant through its OpenAI-compatible
`/v1/chat/completions` endpoint. Although callers can request streaming, the
endpoint waits for the complete assistant turn—including every tool call—before
it creates the stream. The user therefore sees a blank response while searches
and article fetches run, followed by the complete answer in one content chunk.

The final browser response appends a `Tools used` footer, but that footer keeps
only the internal tool name and an `ok` or error state. Search and fetch tools
already retain useful source metadata such as title, domain, final URL, and
publication date. That information is discarded before display. The screenshot
that motivated this change shows several indistinguishable `web_search — ok`
entries and no way to inspect which sources supported the news answer.

The assistant is used primarily through Open WebUI today. Basic interactions
may become voice-only later, but the graphical interface will remain in use.
Progress and source details therefore need to improve the graphical experience
without leaking diagnostic text into synthesized speech.

## Goals

1. Give Open WebUI users immediate, useful feedback while tools are running.
2. Show which trustworthy sources were actually opened and used, with safe
   clickable links when possible.
3. Keep tool arguments, search queries, internal errors, and sensitive URL data
   out of the client-facing trace.
4. Maintain a strict display/speech boundary: progress and diagnostic source
   details are never spoken.
5. Handle quiet news periods honestly by broadening the time window rather than
   inventing depth or substituting unrelated stories.
6. Preserve compatibility with the pinned Open WebUI deployment and the native
   assistant client.

## Non-goals

- Exposing raw tool requests, results, snippets, article bodies, or model
  reasoning.
- Giving Open WebUI direct access to backend tools.
- Rendering every search result as a citation.
- Modifying Home Assistant or media behavior as part of this feature.
- Replacing Open WebUI or maintaining a large permanent fork of it.
- Speaking progress messages, URLs, tool names, or the source trace aloud.

## Design

### 1. Compatibility probe

The first implementation step is a no-production-change compatibility probe
against the pinned Open WebUI version. A small test provider will send
non-content streaming metadata before a normal final answer. The probe will
determine whether Open WebUI can render or preserve one of these transient
status forms without inserting it into the saved assistant message:

1. A normal OpenAI chat-completion chunk with a namespaced
   `delta.home_ai_status` object and no `delta.content`.
2. A provider-supported status or event field already recognized by the pinned
   Open WebUI release.

Synthetic tool calls and hidden HTML are explicitly excluded. They would
misrepresent backend authority or risk appearing as permanent raw markup.

If the pinned release cannot display transient provider metadata, implementation
will stop at this decision point. The alternatives—an intentionally persistent
compact progress log or a minimal maintained Open WebUI extension—will be
presented for approval. The assistant will not silently pollute conversation
history with `Searching…` text.

### 2. Real response streaming

Once a compatible status channel is confirmed, the OpenAI endpoint will create
its streaming response before running the assistant turn. A request-scoped,
bounded asynchronous queue will connect the responder to the SSE generator.

The stream lifecycle will be:

1. Send the normal assistant role chunk.
2. Start the assistant turn in a supervised asynchronous task.
3. Send transient progress events as tools begin and finish.
4. Send the final user-facing answer as content.
5. Send the normal stop chunk and `[DONE]`.

Client disconnection, timeout, or responder failure will cancel or drain the
task safely. Queue size and event size will be bounded so a slow client cannot
create unbounded memory use. Non-streaming callers retain their current single
JSON response contract.

### 3. Central progress events

Progress instrumentation will live at the common tool-execution boundary so it
covers deterministic tools, model-selected tools, and deep-news recovery
fetches consistently. It will emit server-owned metadata before and after a
tool call rather than exposing raw tool arguments.

Example display labels are:

- `web_search`: `Searching recent Canadian headlines…`
- `web_fetch`: `Reading CBC News…`
- weather: `Checking the forecast…`
- media library lookup: `Checking Plex…`
- Home Assistant read: `Checking your home…`

Labels come from a fixed mapping. A fetched source label may include only a
normalized public hostname or known display domain. Search terms, URL paths,
query strings, request bodies, tool results, exception text, and credentials
must never appear in progress events.

Repeated low-value events will be coalesced. Deep research may show a small
sequence such as `Searching…`, `Reading CBC News…`, and `Comparing 4 sources…`,
not one line for every internal retry.

### 4. Rich final trace

A single server-owned projection function will convert raw tool results into a
bounded display schema used by both the native UI and OpenAI facade. It will
replace hand-written projections that currently retain only tool and status.

Each entry may contain:

- a friendly action label;
- a normalized outcome (`complete`, `no results`, or `failed`);
- a bounded source list containing safe title, domain, URL, source kind, and
  optional publication date.

The final graphical trace should read like:

```text
Research activity
- Searched the web — 8 results
- Opened CBC News — Canada adds…
- Opened Reuters — Canadian officials…

Sources
- CBC News — [Canada adds…](https://www.cbc.ca/...)
- Reuters — [Canadian officials…](https://www.reuters.com/...)
```

Successfully fetched pages are preferred because the assistant actually read
them and their final redirect URL passed the server's public-URL checks.
Search-only candidates are either non-clickable or must pass the same display
URL policy before becoming links. The trace will show at most three sources per
search and twelve total entries, with strict per-field and total-byte limits.

### 5. Link and content safety

Remote titles, domains, and URLs are untrusted input. The projection will:

- allow only public HTTP or HTTPS destinations;
- reject user-info, private/local/link-local targets, control characters, and
  unsupported schemes;
- strip fragments and remove query strings unless a narrowly allowed parameter
  is essential;
- normalize and bound titles and domains;
- render links with DOM text APIs in the native client rather than HTML string
  interpolation;
- omit snippets, article bodies, raw errors, and arbitrary result fields.

The trace and its audit logs will contain projected metadata only. They will
not duplicate household-sensitive queries or raw URLs already used internally.

### 6. Display and speech separation

Progress events are display-only protocol events and never pass through answer
or audio emitters. The plain spoken answer is captured before the rich trace is
appended to the Open WebUI display response.

The existing display-to-speech registry remains the primary way to map a full
graphical response back to its spoken answer. The fallback sanitizer will use a
stable server-generated trace marker and remove that marker and everything
after it, including titles and links. A trace-only speech request returns HTTP
204 and invokes no synthesizer.

### 7. Quiet-news behavior

A request for news “today” keeps that time window as its first search. If the
assistant cannot find enough strong, relevant Canadian coverage for a useful
in-depth answer, it will:

1. Say that same-day coverage is limited.
2. Broaden the research window to the previous 48 hours, or the current weekend
   when appropriate.
3. Keep the geographic and topical scope in Canada rather than filling the
   answer with unrelated world results.
4. Label dates clearly and provide the fetched sources.
5. Offer a shorter honest roundup if the broader window is still sparse.

World-news follow-ups may change geographic scope explicitly, but they still
need fetched support and source links. Quiet periods never lower the evidence
threshold for current officeholders or other time-sensitive factual claims.

## Error handling

- A failed progress event must not fail the underlying tool or final answer.
- Tool failure produces a generic display outcome without raw exception text.
- If the streaming task fails before an answer, the endpoint emits a bounded
  user-facing error and closes the stream correctly.
- If a source URL fails display validation, its title/domain may be shown as
  plain text but it cannot become a link.
- If the Open WebUI compatibility probe fails, no live-progress implementation
  is shipped until the fallback is explicitly selected.
- Source projection is best-effort; absence of a rich trace does not change
  tool authorization or make unsupported claims acceptable.

## Testing

### Compatibility and streaming

- Verify the pinned Open WebUI renders the chosen status event transiently and
  does not persist it as assistant content.
- Block a fake tool and assert the client receives progress before that tool
  completes.
- Assert progress chunks contain no answer content, raw arguments, or result
  data.
- Verify one final answer, one stop chunk, and one `[DONE]` marker.
- Verify disconnect, timeout, cancellation, and non-streaming behavior.

### Trace projection and security

- Project search and final redirected fetch metadata into bounded trace entries.
- Prefer fetched sources and deduplicate repeated domains/URLs.
- Reject private IPs, local names, user-info, unsupported schemes, control
  characters, hostile titles, signed/tracking query strings, and oversized
  fields.
- Verify unknown tools receive a generic safe label.
- Verify native UI link rendering treats hostile titles as text.

### Speech isolation

- Verify progress events never schedule audio.
- Verify a rich trace containing titles and URLs is completely removed from
  synthesized speech.
- Verify a trace-only TTS request returns HTTP 204.
- Verify normal graphical answers synthesize exactly the answer text.

### Quiet news

- Simulate sparse same-day Canadian results and verify a clearly disclosed
  48-hour/weekend expansion.
- Verify unrelated global results do not satisfy the Canadian evidence target.
- Verify the final answer includes dates and safe links to fetched sources.
- Preserve deep-news evidence and current-officeholder verification tests.

## Rollout and rollback

The compatibility probe runs outside production first. The feature is then
verified through the direct OpenAI facade, the pinned Open WebUI interface, and
TTS before deployment. Progress visibility, final answer integrity, source-link
safety, and absence of spoken diagnostics are release gates.

The feature introduces no persistent data migration. Rollback restores the
previous immutable Assistant image; Open WebUI and Home Assistant configuration
remain unchanged unless a separately approved compatibility option requires an
Open WebUI extension.
