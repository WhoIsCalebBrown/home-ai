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

## Live TV schema experiment

- Observation: the live cli_debrid database has 48,677 `episode` rows and zero `tv` rows; a Severance sample stores `season_number=2`, while `requested_season=0` is a boolean flag.
- Hypothesis: the prior TV acknowledgement query (`type='tv'`, `requested_season` as scope) could not confirm season ingestion or report TV progress correctly.
- Experiment/control: read-only inspection of the live schema and existing Severance rows; disposable fixture with an episode row for TMDB 95396/season 2; full QA passed 109 tests.
- Fix: Tools `sha-dea4851` accepts the actual episode-backed schema, uses `season_number` for exact scope, and keeps movie matching on `type='movie'`.
- Live replication: `media_status` for an existing Severance workflow now returned `AVAILABLE` with exact TV identity and episode-backed cli_debrid evidence; production smoke remained green. No TV write was invoked.
- Live read-only status: The Hobbit is `AVAILABLE` in Movies-DB with exact TMDB 1362 evidence and `storage_class=debrid`; Dumb and Dumber is `NO_CANDIDATE` from cli_debrid `Blacklisted`, with no Plex match. No request was submitted during this hardening pass.
- Live read-only smoke lane succeeded for containers, Plex, Frigate events, weather, media storage, and SearXNG-backed web search.
- Diagnostic routing deployment: Tools/Assistant `sha-6663b4a` adds bounded `media_diagnose`, human-safe deterministic responses, and a media-context ranking boost so a known workflow's “why is it stuck?” does not select the broad download investigator first. Isolated suite: 112 tests passed before deployment. Live discovery exposes 68 tools; Dumb and Dumber diagnosis returned `NO_ACCEPTABLE_CANDIDATE` at `cli_debrid`, with no mutation.
- Deployment recovery: the first coordinated SSH deployment timed out during image pull before recreation; runtime was verified unchanged, then the two owned services were pulled/recreated separately. Final runtime and persistent templates match `sha-6663b4a`, both retain `server-tools`/`voiceai`, and both carry the Unraid management label. Contract validation, drift report, and read-only smoke all pass.
- Isolated qualification lane now has 102 passing tests, including 140 acquisition-language mutations, parallel cross-session confirmation rejection, side-effect tripwires, expiry, provider failure, exact episode fail-closed behavior, restart persistence, and canonical-ID collision checks. A 50-iteration network-isolated safety soak also passed.
- The safe QA runner intentionally uses a network-isolated test container; its first implementation failed because it attempted a runtime package install, then was corrected by baking pytest/FastAPI/httpx/Pydantic into `qa/Dockerfile`. The corrected runner passed.

## 2026-09-15 06:15Z audio-lane checkpoint

- Observation: text-only qualification did not prove the production voice path. A real WAV generated by the existing Pocket service was sent through the live Assistant WebSocket and Faster-Whisper Wyoming endpoint.
- Full audio control: `How many containers are running?` transcribed as `how many containers are running.`, selected `list_containers`, returned the grounded answer, and emitted TTS audio. No write-capable tool was exposed or invoked.
- First adversarial audio pass found two real defects: current front-door wording selected `frigate_recent_events` instead of `frigate_snapshot`; a retained container follow-up caused `StopIteration` when mapping “stopped” to the internal `exited` filter. Both received generic fixes and permanent regressions.
- Additional bounded STT repair: “storage”→“stores” is corrected only inside an unmistakable `how much ... left/free/space` capacity question. The observed storage voice variant then selected `get_storage_status` successfully.
- Multi-turn real audio replication passed on persistent WebSocket sessions: server running→stopped; current-politics→explicit web-search correction (web search and fetch on both turns); historical Frigate event→event snapshot→live snapshot. Parallel independent audio sessions also passed with isolated client IDs.
- Read-only audio scenario results: containers, stopped containers, weather, Plex recently-added, storage, current politics, Nvidia news, current Frigate, disfluent container requests, direct-file rejection, and the multi-turn controls. TTS audio chunks were counted and discarded by the harness; no production media write endpoint was called.
- Assistant deployed as owned image `ghcr.io/whoiscalebbrown/home-ai-assistant:sha-c40f7c9`; Tools remains `ghcr.io/whoiscalebbrown/home-ai-tools:sha-6663b4a`. The Assistant template was synchronized after deployment; Unraid drift is clean. Tools was restarted once for owned-service recovery validation; third-party services were not restarted or modified.
- Current local QA after the routing/status fixes: 121 tests passed; conversation matrix 31 scenarios with no failures. CI run `34935203976` passed for the prior Assistant image; the later Assistant-only image build for `sha-c40f7c9` passed. The harness expansion is in `sha-62108dd` and does not alter runtime code.
- Remaining falsification target: media status needs a retained canonical workflow to prove status continuation in the real audio lane without creating a new production plan; use an isolated fake adapter for that state-machine case, not a new real media request.
- 2026-09-15 08:06Z checkpoint: live Assistant `sha-3effa66` and Tools `sha-3904414` are healthy; contract and Unraid drift checks are clean. The final read-only audio catalog completed 22/22 scenarios. Ten distinct multi-turn real-audio conversations completed 21/21 turns, covering server, weather, Plex, media status, web correction, historical/live Frigate switching, and cross-domain transitions. Three concurrent audio sessions (politics, Nvidia, containers) also passed; web latency rose to approximately 15–20 seconds under contention. Assistant-only restart recovery passed through the production smoke path and a real container query. No production media writes or destructive operations were performed. Remaining P1: 100+ broader voice corpus, sustained soak/concurrency testing, fault-injection/restart-state expansion, STT title corruption, intermittent web-fetch availability, and broader TV/anime/music lifecycle coverage.

## 2026-09-15 09:00Z sustained audio hardening checkpoint

- Assistant runtime is now `sha-719292d`; Tools remains `sha-3904414`; Qwen/TTS and all third-party images are unchanged. Contract pin and Unraid runtime/template drift are clean.
- Real audio catalog expanded to 72 read-only scenarios. The first full run exposed bounded STT/routing defects in Plex recency, current-web phrasing, and media-status assertions. Fixes were generalized, regression-tested, published, and revalidated through the live WebSocket lane.
- Repeated real-audio controls now route `Plex edition`/`flex edition` recency variants to `plex_recently_added`; noisy `online/latest/developments` variants to `web_search`; dropped-question media status forms such as `at the hobbit finish`, `dumb and dumber downloaded`, `get found`, and fused `Radium Plex` to `media_status` without writes.
- Ten multi-turn real-audio conversations completed with no harness errors after the latest deployment. Three concurrent isolated sessions and Assistant-only restart/recovery also passed. No production media write was invoked.
- Remaining voice limitations are honest/conservative: severe transcript collapse (for example a one-word transcript) cannot be safely repaired; playback-like wording remains distinct from availability; intermittent `web_fetch` failures are reported as unavailable rather than fabricated success.
- Scientific status remains candidate-validated, not complete: the lane has strong static/property/contract/full-E2E/adversarial evidence, but still lacks a 100+ distinct multi-turn voice corpus, prolonged soak, broad fake fault-injection matrix, and complete TV/anime/music lifecycle audio coverage.
- Read-only live status reconciliation: Dumb and Dumber has exactly one cli_debrid row for TMDB 8467 with raw state `Blacklisted`; the semantic result is `NO_CANDIDATE`, with no Plex match. The 10th Kingdom has no tracked Home-AI workflow. This is not an active acquisition and no write was performed.
- Final checkpoint at `2026-09-15T09:13:32Z`: production smoke, 68-tool discovery, contract validation, and Unraid drift all clean on Assistant `sha-bc2d30f` / Tools `sha-3904414`. The isolated 131-test suite passed in a 50-process restart soak. Remaining audio limitation: bare playback-like transcripts such as `Watch The Hobbit now` cannot be safely distinguished from a direct playback command, so the router fails closed instead of asserting availability.
- Extended voice qualification: the generated catalog ran 45 real multi-turn conversations / 91 audio turns before the cross-domain fix. It exposed seven failures: four camera/current-domain precedence cases, one media-title STT loss, one Docker→doctor STT loss, and one severe route transcript. The generic precedence fix was deployed as Assistant `sha-17491b0`; four affected conversations were rerun live and all four passed with `frigate_snapshot`/`web_search`/`media_status` as appropriate. The remaining full-corpus gap is the conservative handling of bare playback-like STT, which is intentionally not guessed.
- Five additional mixed-domain conversations (10 real audio turns) passed after the fix, bringing the executed multi-turn voice corpus to 50 conversations / 101 turns. These covered server↔Plex, weather↔server, media↔storage, live↔historical Frigate, and web↔web transitions with no write-capable tools selected.

## 2026-09-15 final audio hardening checkpoint

- The isolated safety suite reached 141 passing tests. The production standard-media executor was exercised against a recording webhook fake: exact canonical persistence produced `submitted`/`ingestion_confirmed`; HTTP 200 without exact persistence produced `FAILED_INGESTION`; exact live provider evidence produced a zero-POST no-op; replayed confirmation was rejected; permanent-storage arguments were rejected.
- A fresh 50-conversation / 101-turn audio run was used as a falsification pass. It exposed and corrected generic defects in: bounded Docker STT repair, media-domain retention after truthful `NOT_FOUND`, provenance wording intercepting “What Docker services are up?”, explicit-domain precedence over “I mean” repair inheritance, past-tense media status framing, and the Whisper “stops” container follow-up.
- Final targeted real-audio validation passed for the corrected Docker/weather/server, media status, 10th Kingdom status, web correction, Frigate→web, and server follow-up conversations. Three concurrent isolated sessions also passed tool/session separation. Web responses are now guarded against claiming no web access after an actual search attempt.
- Final Assistant runtime is owned image `ghcr.io/whoiscalebbrown/home-ai-assistant:sha-5227f9f`; Tools remains `ghcr.io/whoiscalebbrown/home-ai-tools:sha-3904414`; cli_debrid and all other third-party images are unchanged. Health is READY, Tools exposes 68 capabilities, the contract is 1.0, and Unraid runtime/template drift is empty.
- Remaining evidence gap: the full post-final-deployment 101-turn corpus was not rerun after the last two focused fixes; the preceding full run had one remaining `stops` transcript failure, which was then fixed and passed in a real audio conversation. A full clean rerun remains P1 if additional runtime is available. Severe transcript collapse remains fail-closed rather than guessed, and production write execution remains excluded from autonomous QA.

## 2026-09-15 post-final-deployment qualification update

- The full post-`sha-5227f9f` real-audio corpus was rerun through the live Assistant WebSocket and existing Pocket-generated audio lane: 50 conversations, 101 audio turns, 0 harness errors, exit 0.
- The disposable network-isolated qualification runner passed 141 tests. The conversation matrix passed 31 scenarios and 12 confirmation forms with no failures.
- Read-only production smoke passed: Assistant/Tools health, 68-tool discovery, container status, media storage contract, Frigate status, and web/media discovery. Unraid runtime/template drift remained empty.
- This closes the previously noted post-final audio-corpus evidence gap. It does not close the deliberate safety boundary around real media writes, nor the broader coverage gaps called out below: prolonged production soak, full fault-injection permutations, and complete TV/anime/music lifecycle voice coverage.

## 2026-09-15 continuation: adversarial audio and deployment recovery

- A five-session concurrent audio run exposed an intermittent Faster-Whisper substitution: `What about stopped?` became `What about start?`. The safe fix is fail-closed clarification (`Did you mean the stopped containers, or are you asking to start one?`), never an automatic alias that could authorize a write. Isolated tests reached 142 passing tests; the real audio clarification conversation passed after deployment.
- A subsequent 51-conversation / 103-turn corpus exposed two additional STT boundary variants: dropped `what` in `About Dumb and Dumber?`, and `What's new in Plex?` rendered as `was new in Plex`. The former now remains a bounded media-status read; the latter now remains a bounded Plex-recency read. Isolated tests reached 143 passing tests. The deployed targeted Plex and media conversations passed.
- The same corpus recorded one title corruption (`Hobbit` → `hobby`) that remained read-only but lost canonical identity. No title-specific alias was added; this remains an STT/canonical-resolution P1 limitation requiring better confidence-aware recovery.
- Assistant-only deployment advanced to `sha-45581dd` after CI success. Tools remains `sha-3904414`; third-party images remain unchanged. Runtime/template drift is empty and production smoke/health passed.
- Deployment recovery found `/var/lib/docker` at 99% usage from accumulated old owned Assistant image tags. Explicitly unused old Home-AI Assistant tags were removed while retaining the running image and rollback tags `sha-21dfd0e`, `sha-c1993d5`, and `sha-5227f9f`. No application containers, volumes, media paths, or third-party images were removed. Docker usage fell to 57%.
- Final post-`sha-45581dd` real-audio corpus rerun completed cleanly: 51 conversations, 103 audio turns, 0 harness errors. This includes the new ambiguous-container clarification conversation and the dropped-Plex-question-frame regression. The previously observed title corruption remains a limitation only when Whisper emits a malformed transcript; it did not recur in this run.
