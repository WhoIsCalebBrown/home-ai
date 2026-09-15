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
- Live read-only status: The Hobbit is `AVAILABLE` in Movies-DB with exact TMDB 1362 evidence and `storage_class=debrid`; Dumb and Dumber is `NO_CANDIDATE` from cli_debrid `Blacklisted`, with no Plex match. No request was submitted during this hardening pass.
- Live read-only smoke lane succeeded for containers, Plex, Frigate events, weather, media storage, and SearXNG-backed web search.
- Isolated qualification lane now has 96 passing tests, including side-effect tripwires, cross-session confirmation rejection, expiry, provider failure, exact episode fail-closed behavior, restart persistence, and canonical-ID collision checks.
- The safe QA runner intentionally uses a network-isolated test container; its first implementation failed because it attempted a runtime package install, then was corrected by baking pytest/FastAPI/httpx/Pydantic into `qa/Dockerfile`. The corrected runner passed.
