# Assistant News, Media, and Home-Control Corrections

Date: 2026-09-19

## Context

Four production conversations exposed related failures at the boundary between
intent classification, tool invocation, and response rendering:

- An explicitly in-depth Canadian news request ran several searches but fetched
  no articles, produced only two sentences, and repeated a stale claim naming
  Justin Trudeau as the current prime minister.
- A plot-description question correctly resolved *White Chicks* (2004), but the
  response unnecessarily offered to prepare a Plex request.
- A later explicit request for that movie required redundant conversational
  approval despite the request itself expressing the user's intended action.
- Named and typed Home Assistant requests were routed through model-generated
  arguments. The model sent an unsupported `device_id` field for an office-light
  command and no filters for a lights-state question, while whole-home commands
  happened to use a supported argument shape.

Home Assistant currently reports three Tuya lights as unavailable because their
physical switches are off. That is expected device state, not a fault to repair.
The assistant must report it accurately without diagnosing or claiming control.

## Goals

1. Make requested research depth control both evidence collection and answer
   depth.
2. Prevent stale or weak web evidence from becoming confident current-fact
   claims.
3. Keep media identification, Plex availability, and media acquisition as
   distinct user intents.
4. Treat an unambiguous explicit media-request command as authorization to run
   the configured request workflow without a redundant confirmation exchange.
5. Make common Home Assistant status and control phrasing deterministic across
   named entities, rooms, device types, states, and whole-home scopes.
6. Preserve existing safety boundaries, session isolation, typed tools, and
   truthful reporting of unavailable devices and failed writes.

## Non-goals

- Repairing Tuya devices that are unavailable because a physical switch is off.
- Adding automatic media recommendations or unsolicited Plex/request offers.
- Replacing the semantic tool retriever or local language model.
- Broadening Home Assistant access beyond the existing authorized light and
  switch entities.
- Removing clarification when media identity is genuinely ambiguous.

## Design

### 1. Current-news research

Research mode remains selected from the user's language, but its contract is
made enforceable rather than advisory.

For a deep current-news request, the assistant will:

1. Run multiple topic-oriented discovery searches within the requested recency
   window.
2. Fetch article text for multiple independent, strong sources rather than
   synthesizing from snippets alone.
3. Prefer primary or authoritative sources for government office-holders,
   policy announcements, and other easily verified institutional facts.
4. Cross-check central claims and omit a claim when the evidence conflicts or
   cannot be verified.
5. Deduplicate syndicated or repeated coverage before choosing the major
   stories.
6. Produce a genuinely detailed spoken answer when the user asks for depth.
   The global one-or-two-sentence default must yield to explicit depth intent.

Deep-mode completion will require a minimum evidence shape: several successful
searches, at least two successfully fetched sources when fetchable URLs exist,
and enough distinct supported topics for a useful roundup. If that threshold is
not met, the assistant will state that the available research was incomplete
instead of filling gaps from model memory.

Current office-holder claims will require fetched authoritative evidence or
corroborating current sources. Search-result snippets alone are insufficient.

### 2. Media intent separation

The current predicates will be consolidated into a single current-turn media
operation contract used consistently by planning, response rendering, and
follow-up state:

- `MEDIA_DISCOVERY`: identify a movie, show, album, or other item from clues.
- `MEDIA_LIBRARY_QUERY`: check whether the identified item is already in Plex.
- `MEDIA_REQUEST`: make an identified item available through the configured
  acquisition workflow.
- `MEDIA_STATUS`: report the state of an existing request.
- `MEDIA_PLAY`: playback or delivery intent, which remains separate from
  acquisition.

A descriptive question such as “What's that movie where two cops dress as
blonde women?” is `MEDIA_DISCOVERY`. Its direct answer is only the canonical
title and year. It does not check Plex, stage a request offer, or append a
question about acquisition.

Successful discovery stores the canonical identity as the conversational
referent. A subsequent “Can you request it?”, “Add that movie,” or equivalent
referential command becomes `MEDIA_REQUEST` for that exact identity.

### 3. Explicit media-request authorization

An explicit, unambiguous request command is itself the user's authorization for
the standard bounded acquisition action. The flow will be:

1. Resolve one canonical media identity.
2. Check current Plex/manager/workflow state.
3. If already available in Plex, perform no write and say it is already there.
4. If already requested or actively managed, perform no duplicate write and
   report the current state.
5. If absent and eligible, submit the standard request immediately and report
   whether ingestion was accepted.

The assistant will not ask “Do you want me to request it?” after the user has
already issued that request. It will still ask one concise clarification when
identity resolution returns multiple plausible candidates or required scope is
missing. Confirmation safeguards for unrelated disruptive actions, such as
container restarts, are unchanged.

The execution remains bound to server-generated canonical identity and
session/workflow state. A bare “yes” without a pending, identity-bound action
must never start a request.

### 4. Home Assistant intent and argument normalization

Common light and switch language will use deterministic parsing before model
tool selection. Supported shapes include:

- Named entity: “turn on the office light” or “is the bedroom lamp on?”
- Area/type: “turn off the living-room lights” or “which office lights are on?”
- Type/state: “what lights are on?” or “which outlets are off?”
- Broad state: “what devices are on?”
- Whole-home control: “turn off all the lights.”

The parser will emit only the typed tool contracts:

- `home_control` receives `entity_or_area` or validated `entity_ids`, `action`,
  and bounded parameters.
- `home_get_state` receives explicit `domain`, `state`, `area`, `scope`, or
  validated `entity_ids` filters.
- `home_find_device` always receives the required `query` field, including an
  explicit empty string only for a supported inventory request.

Model-generated aliases such as `device_id` will not silently pass through. A
normalization layer may translate a safe, unambiguous alias to the typed field;
otherwise validation must reject it without executing a different or broader
target.

Unavailable devices remain visible in read results. A control request targeting
an unavailable light must say that Home Assistant reports it unavailable and
must not claim success. Whole-home results must distinguish executed targets
from unavailable, unauthorized, protected, or unsupported targets.

### 5. Response behavior

Deterministic renderers will be used for these high-confidence outcomes:

- Media discovery: “That's *White Chicks* (2004).”
- Already available: “You already have *White Chicks* in Plex.”
- Accepted request: a concise statement that the request was submitted and is
  being processed.
- Home state: name matching devices and their returned state.
- Unavailable home target: identify the device as unavailable without inferring
  why its physical connectivity is absent.

The browser/tool trace may continue showing tools used, but spoken output must
not read internal tool names or diagnostics aloud.

## Error handling and safety

- Web claims are grounded only in current tool evidence; incomplete evidence
  yields a qualified response rather than model-memory substitution.
- Failed or blocked article fetches are skipped and do not count toward the
  deep-research evidence threshold.
- Media writes remain idempotent and canonical-ID-bound. Ambiguity prevents a
  write.
- A failed media submission is reported as failed; transport success alone is
  not described as an accepted request.
- Home-control target resolution remains restricted to authorized entities and
  preserves bulk-switch protections.
- No service restarts, Home Assistant credential changes, or Tuya integration
  changes are part of this work.

## Testing

Regression tests will be built from the reported conversations and generalized
by intent shape.

### News

- “Give me an in-depth review of today's news in Canada” selects deep mode.
- Deep mode performs multiple searches and multiple fetches when URLs are
  available.
- The final response is not constrained to the normal short-answer budget.
- Unsupported current office-holder claims cannot be synthesized from snippets.
- Incomplete evidence produces an explicit limitation rather than invented
  detail.

### Media

- Plot-description discovery returns only canonical title/year and stages no
  acquisition offer.
- The canonical identity survives for “Can you request it?”
- An explicit unambiguous request executes one standard request without a
  redundant confirmation prompt.
- Already-in-Plex and already-requested cases are idempotent and informative.
- Ambiguous identity asks one clarification and performs no write.
- Bare confirmations without identity-bound state perform no write.

### Home Assistant

- Named light, room lights, type/state, broad-state, and all-lights utterances
  produce exact typed arguments.
- “What lights are on?” becomes `domain=light, state=on`.
- “What devices are on?” becomes `state=on` across the authorized scope.
- “Turn on the office lights” targets the Office area/light rather than an empty
  query or unsupported `device_id` field.
- Unavailable targets are reported as unavailable and never as controlled.
- Existing authorization and bulk-control safety tests continue passing.

Tests will run first against isolated tools/fake executors and QA. Production
promotion will follow the repository's immutable-image and rollback process,
with authenticated behavior checks after deployment.

## Deployment and rollback

1. Record the current production image tags and Docker image-filesystem
   headroom.
2. Build immutable Assistant and Tools images from the reviewed source commit.
3. Run focused unit/conversation tests and the production-shaped QA suite.
4. Deploy to QA and replay the reported utterances plus negative controls.
5. Promote the tested immutable tags to production without changing Home
   Assistant or Tuya configuration.
6. Verify health, authentication boundaries, media idempotency, news evidence
   behavior, and Home Assistant reads/controls.

Rollback consists of restoring the recorded prior Assistant and Tools image
tags/templates. No state migration is introduced by this design.
