# Home-AI hardening run — 2026-09-15

UTC start: `2026-09-15T04:16:25Z`

This journal records evidence and falsification attempts. Production media writes are out of scope; state-changing tests terminate in `qa/fake_media_backend.py`.

## P0: unresolved media plan could claim a request started

- Observation: the live 10th Kingdom trace had no canonical identity and no write plan, followed by Qwen saying the request had started.
- Hypothesis: planner failures fall through to generative synthesis without an evidence gate.
- Prediction: an error/ambiguous `media_plan_goal` result should produce a deterministic non-started response and no confirmation.
- Control: an actionable Dumb and Dumber plan still reaches the confirmation path.
- Experiment: `media_plan_response` tests cover unresolved, error, and actionable results; the 10th Kingdom cross-domain test also asserts no workflow is persisted.
- Result: all focused tests pass; unresolved results cannot claim started/requested.
- Falsification attempt: malformed explicit framing, generic title, and status-error result variants were included in the generated matrix.
- Replication: 31 routing scenarios and the full assistant regression set pass.

## P0: provider failure was reported as active progress

- Observation: live Dumb and Dumber state was `Blacklisted` while the persisted workflow still said `REQUESTED`.
- Hypothesis: live status reduction lacks a provider-failure branch and falls back to stale workflow state.
- Prediction: `Blacklisted` must reduce to `NO_CANDIDATE`, while exact Plex evidence must take precedence over stale workflow state.
- Control: live The Hobbit state is `Collected` plus exact Movies-DB TMDB 1362 match and must reduce to `AVAILABLE`.
- Experiment: `media_status` adapter tests inject `Collected` and `Blacklisted` rows with stale workflow states.
- Result: adapter returns `AVAILABLE` for exact Hobbit evidence and `NO_CANDIDATE` for Blacklisted.
- Falsification attempt: remake/trilogy Plex candidates remain rejected by canonical IDs; title-only evidence is not accepted.
- Replication: 85 tests pass in a Python 3.12 application-image container.

## P1: capability discovery over-scored unrelated Frigate tools

- Observation: generic “doing” / “what happened” language ranked Frigate activity ahead of media/web tools.
- Hypothesis: visual capability boosts were applied without an explicit camera signal.
- Prediction: the boost must require camera/event vocabulary; politics/media queries must rank web/media first.
- Control: “alerts from the front camera” still ranks recent Frigate events, and event-image wording ranks the event snapshot.
- Experiment: capability discovery regression tests plus live `/discover` checks after deployment.
- Result: focused container suite passes; the narrowed ranker returns correct domain-leading capabilities.
- Falsification attempt: historical event, live snapshot, politics, and media-status queries were compared.

## Contract/deployment evidence

- Static: machine-readable contract and read-only Unraid drift report added.
- Unit/property: fake media backend tests enforce idempotency, exact season scope, and episode fail-closed behavior.
- Contract/integration: 85 tests execute against Python 3.12 application dependencies in a disposable container.
- Full E2E: pending post-deployment Assistant-path smoke checks; no write-capable production call will be made.
- Adversarial/live read-only: live inventory, DNS/health, capability discovery, workflow status, and Plex/provider evidence checked.

## Post-deployment checkpoint

- CI run `34930186784` passed for the hardened Assistant and Tools images.
- Assistant and Tools were recreated from first-party Home-AI images `sha-68d9ff2`; no third-party container was restarted or modified.
- Persistent Unraid templates now point to `sha-68d9ff2` and retain `TOOLS_URL=http://server-tools:8090`.
- Runtime/template drift report is clean; Tools health reports contract `1.0` and 67 tools.
- Recovery experiment: Assistant initially started before Tools and logged `TOOLS_BACKEND_UNAVAILABLE`; after Tools became healthy it recovered to `TOOLS_BACKEND_READY` without a code change. This proves recovery but also identifies startup ordering as a remaining availability concern.
- The provider-read failure fix is deployed in Assistant/Tools `sha-c4e9420`; runtime image digests are Assistant `sha256:0a450291ee768205ed568c7c17154c937c4d057e3d1d5e915e6f8139c7315fef` and Tools `sha256:da8dd8fae140beb34cf338084c070b43c4f2a2b55704cda3d224cfd52efa9b3f`. Rollback templates are timestamped under `/boot/config/plugins/dockerMan/templates-user/*.bak-hardening-*` on Unraid.

## Frigate health-parser experiment

- Observation: the production read-only smoke lane returned `JSONDecodeError` for `frigate_status`, while `frigate_recent_events` succeeded.
- Hypothesis: the live Frigate `/api/version` endpoint returns plain text, but the generic Tools JSON helper was used.
- Prediction/control: direct in-container read should show `text/plain`; JSON event endpoints should remain unchanged.
- Experiment: direct read returned `200 text/plain` with `0.17.2-3d4dd3a`; the focused parser regression covers both plain text and JSON bodies; the disposable QA lane passed 104 tests.
- Fix: Tools `sha-275d670` uses an explicit bounded text-or-JSON parser only for `/api/version`. The production smoke lane now fails on structured tool errors instead of treating HTTP 200 as success.
- Live replication: Assistant discovery still exposes 67 tools; `frigate_status` now returns `status=ok`, `reachable=true`, and the live version through the Assistant network path. No Frigate image or configuration was changed.
- Rollback: `/boot/config/plugins/dockerMan/templates-user/Home-AI-Tools.xml.bak-hardening-20260915T052759Z`.

## Media executor revalidation experiment

- Observation: persisted workflows included stale active states and at least one pending approval for an item already present in Plex; the executor checked persisted state before live provider evidence.
- Hypothesis: stale state could suppress a legitimate retry or leave a pending confirmation unnecessarily actionable.
- Control: exact canonical Plex matching and exact cli_debrid TMDB/season evidence are authoritative; title/state text alone is not.
- Experiment: added a no-op regression with an enabled executor, stale `SEARCHING` state, exact live `Wanted` evidence, and a pending confirmation. The request produced no external call and invalidated the approval. The full disposable suite passed 108 tests.
- Fix: Tools `sha-1e7a923` revalidates cli_debrid and canonical Plex before any webhook, refuses writes on provider read failure, and retires pending confirmations when a live no-op is proven.
- Live replication: production smoke passed through the Assistant network path; runtime/template drift is empty after the template update. No media write was invoked.
- Live read-only status: The Hobbit is `AVAILABLE` in Movies-DB with exact TMDB 1362 evidence and `storage_class=debrid`; Dumb and Dumber is `NO_CANDIDATE` from cli_debrid `Blacklisted`, with no Plex match. No request was submitted during this hardening pass.
- Live read-only smoke lane succeeded for containers, Plex, Frigate events, weather, media storage, and SearXNG-backed web search.
- Isolated qualification lane now has 102 passing tests, including 140 acquisition-language mutations, parallel cross-session confirmation rejection, side-effect tripwires, expiry, provider failure, exact episode fail-closed behavior, restart persistence, and canonical-ID collision checks. A 50-iteration network-isolated safety soak also passed.
- The safe QA runner intentionally uses a network-isolated test container; its first implementation failed because it attempted a runtime package install, then was corrected by baking pytest/FastAPI/httpx/Pydantic into `qa/Dockerfile`. The corrected runner passed.
