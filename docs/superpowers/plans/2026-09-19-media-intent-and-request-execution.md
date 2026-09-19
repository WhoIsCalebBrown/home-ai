# Media Intent and Request Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer media-identification questions without unsolicited acquisition offers, retain the resolved identity, and execute an explicit unambiguous request exactly once without asking for redundant confirmation.

**Architecture:** Make one current-turn media operation (`MEDIA_DISCOVERY`, `MEDIA_LIBRARY_QUERY`, `MEDIA_REQUEST`, `MEDIA_STATUS`, or `MEDIA_PLAY`) authoritative across planning and rendering. Keep all writes behind the existing server-issued confirmation record and canonical ID, but consume that bound record immediately when the current utterance is already an explicit request. Extract shared result rendering/state updates so same-turn and legacy pending execution cannot diverge.

**Tech Stack:** Python 3.12, FastAPI Assistant service, pytest, fake media planner/executor integration harness, OpenAI-compatible TTS endpoint.

**Spec:** [Assistant News, Media, and Home-Control Corrections](../specs/2026-09-19-assistant-news-media-home-control-design.md)

## Global Constraints

- Never request media from a discovery-only or availability-only utterance.
- Never reconstruct executor arguments from prose; use only `confirmation_record.arguments` from `media_plan_goal`.
- Preserve session binding, canonical external ID binding, plan hash validation, TTL, and executor idempotency.
- Ambiguous identity or missing scope performs no write and asks one clarification.
- A bare `yes` without bound state performs no write.
- Container restart and other disruptive-action confirmations are unchanged.

## Review Focus

- The White Chicks plot-description wording must classify as discovery even though it does not use “identify.”
- Discovery returns title/year only and creates neither `pending` nor `pending_offers`.
- “Can you request it?” must use the retained canonical identity and submit once in that turn.
- Already-in-Plex/already-managed outcomes must remain no-op and be described accurately.
- The browser may show a tool footer, but `/v1/audio/speech` must not speak `media_plan_goal — ok` or any trace row.

---

## Task 1: Make the media operation contract cover descriptive questions

**Files:**

- Modify: `assistant/test_grounding_regressions.py:10-120`
- Modify: `assistant/test_grounding_regressions.py:1120-1210`
- Modify: `assistant/voice-api-app.py:2308-2720`

- [ ] Add table-driven failing tests for `media_intent()`:

```python
@pytest.mark.parametrize(("text", "expected"), [
    ("What's that movie where two cops dress as blonde women?", "MEDIA_DISCOVERY"),
    ("Do I have White Chicks in Plex?", "MEDIA_LIBRARY_QUERY"),
    ("Can you request White Chicks?", "MEDIA_REQUEST"),
    ("How is my White Chicks request doing?", "MEDIA_STATUS"),
    ("Play White Chicks in the living room", "MEDIA_PLAY"),
])
def test_media_operation_contract(text, expected):
    assert media_intent(text, {}) == expected
```

- [ ] Add precedence negatives: “What is White Chicks about?” is discovery/read-only; “Can you play the trailer?” is playback, not acquisition; download-status wording remains status.
- [ ] Run:

```bash
pytest -q assistant/test_grounding_regressions.py -k 'media_operation_contract or descriptive_media'
```

Expected: the White Chicks descriptive question does not return `MEDIA_DISCOVERY`.

- [ ] Refactor `media_intent()` to evaluate the existing predicates in explicit precedence order: direct file/playback, status, library, explicit acquisition, descriptive discovery. Use `_descriptive_media_clue()` for discovery rather than duplicating its vocabulary.
- [ ] Ensure `operation_for_plan()` stores this operation as `_pending_operation`/`latest_operation` for all `media_plan_goal` routes.
- [ ] Re-run focused tests and commit:

```bash
git add assistant/voice-api-app.py assistant/test_grounding_regressions.py
git commit -m "fix: classify descriptive media questions as discovery"
```

## Task 2: Remove unsolicited acquisition offers from read-only media answers

**Files:**

- Modify: `qa/test_assistant_conversation_integration.py:830-890`
- Modify: `qa/test_assistant_conversation_integration.py:2330-2430`
- Modify: `assistant/voice-api-app.py:4470-4525`
- Modify: `assistant/voice-api-app.py:5500-5700`
- Modify: `assistant/voice-api-app.py:5880-5960`

- [ ] Add an integration regression using the reported shape:
  1. Seed canonical `White Chicks` (2004).
  2. Ask `What's that movie where two cops dress as blonde women?`.
  3. Assert the answer identifies `White Chicks (2004)`.
  4. Assert it contains no `want me`, `request`, `Plex`, or availability claim.
  5. Assert neither `pending` nor `pending_offers` contains the session.
  6. Assert `conversation_context[client_id]["canonical_identity"]` is retained.
- [ ] Change the old discover→offer test so read-only identification/library turns no longer expect an offer. Keep a separate test only if non-acquisition offers are still a supported product behavior.
- [ ] Run the focused integration tests in the QA image and confirm the current response appends an offer.
- [ ] Remove the read-only media call sites that invoke `stage_media_offer()`, or gate them strictly so `MEDIA_DISCOVERY` and `MEDIA_LIBRARY_QUERY` can never stage an acquisition-related offer. Do not weaken `direct_structured_answer()` canonical identity rendering.
- [ ] Delete obsolete acquisition-offer tests only after equivalent no-offer and explicit-follow-up coverage exists; do not delete generic offer-state safety tests that still protect another feature.
- [ ] Re-run focused tests and commit:

```bash
git add assistant/voice-api-app.py qa/test_assistant_conversation_integration.py
git commit -m "fix: stop offering media requests after identification"
```

## Task 3: Execute an explicit canonical request in the same turn

**Files:**

- Modify: `assistant/voice-api-app.py:3979-4025`
- Modify: `assistant/voice-api-app.py:5060-5290`
- Modify: `assistant/voice-api-app.py:5500-5960`
- Modify: `qa/test_assistant_conversation_integration.py:830-935`
- Modify: `qa/test_assistant_conversation_integration.py:2390-2490`

- [ ] Replace the old “request variants proceed to confirmation” expectation with failing scenarios:
  - direct named request submits one `media_standard_request` and leaves no pending action;
  - discovery followed by “Can you request it?” submits the retained canonical ID once;
  - repeated explicit request returns the executor's no-op/already-managed result without a second write;
  - already-in-Plex planning performs no executor write and says it is already in Plex;
  - an ambiguous request stages disambiguation and writes nothing;
  - bare `yes` without pending state writes nothing.
- [ ] Assert the executor call receives `confirmed=True`, the planner's exact `confirmation_id`, and arguments containing the original `confirmation_context`, `workflow_id`, `canonical_external_id`, and session ID.
- [ ] Run the focused tests and confirm they fail because an explicit request only populates `pending`.
- [ ] Extract pure/shared result handling from the current `action_name == "media_standard_request"` branch:
  - `media_request_outcome(action, result)` updates `latest_media_workflow` and returns the existing truthful messages for submitted, already available/no-op, disabled, rejected, and failed ingestion.
  - No message may claim success from outer transport status alone.
- [ ] Add `execute_bound_media_request(client_id, request_id, action)` that invokes the stored action with `confirmed=True` and its stored `action_id`, then calls the shared outcome helper.
- [ ] After a non-ambiguous `media_plan_goal` result:
  1. call `stage_media_confirmation()` exactly as today;
  2. if the authoritative current/original operation is `MEDIA_REQUEST`, atomically pop the staged action and call `execute_bound_media_request()` in the same turn;
  3. otherwise render the read-only result and leave no write action staged.
- [ ] For disambiguation, store the original operation alongside `original_goal`. After one candidate is selected, preserve `MEDIA_REQUEST` so the now-unambiguous bound plan executes once; discovery ambiguity remains read-only.
- [ ] Keep `media_standard_request` excluded from model-facing tools. Do not call it with title/year arguments or bypass its confirmation record.
- [ ] Re-run focused tests and commit:

```bash
git add assistant/voice-api-app.py qa/test_assistant_conversation_integration.py
git commit -m "fix: execute explicit bound media requests once"
```

## Task 4: Guarantee tool traces remain display-only

**Files:**

- Modify: `assistant/test_tts_normalization.py`
- Modify if tests expose a gap: `assistant/voice-api-app.py:6260-6410`

- [ ] Add regression tests for both Markdown and flattened Open WebUI inputs:

```python
def test_media_tool_trace_is_removed_from_speech():
    assert remove_openai_tool_trace(
        "That's White Chicks (2004).\n\n---\n**Tools used**\n- `media_plan_goal` — ok"
    ) == "That's White Chicks (2004)."
    assert remove_openai_tool_trace("Tools used media_plan_goal — ok") == ""
```

- [ ] Add an endpoint-level test proving a display-only trace request returns HTTP 204 and the registered full display response resolves to the spoken answer without the footer.
- [ ] Run:

```bash
pytest -q assistant/test_tts_normalization.py assistant/test_openai_gateway.py -k 'trace or speech'
```

- [ ] If a case fails, strengthen only `remove_openai_tool_trace()` or the display→speech registry lookup; do not remove the browser trace.
- [ ] Re-run and commit:

```bash
git add assistant/voice-api-app.py assistant/test_tts_normalization.py assistant/test_openai_gateway.py
git commit -m "test: keep media tool traces out of speech"
```

## Task 5: Run media safety and full QA regressions

**Files:**

- Modify only when an actual regression requires it

- [ ] Run focused media suites:

```bash
pytest -q assistant/test_grounding_regressions.py assistant/test_pending_offer_integration.py
docker build -f qa/Dockerfile.assistant_integration -t home-ai-assistant-plan-test .
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo home-ai-assistant-plan-test \
  pytest -p no:cacheprovider -q qa/test_assistant_conversation_integration.py \
  -k 'media or discover or request or confirmation or disambiguation'
```

- [ ] Run `qa/test_p0_confirmation_matrix.py` and verify non-media confirmation behavior is unchanged.
- [ ] Run `qa/run_safe_qa.sh`.
- [ ] Verify `git diff --check` and a clean `git status --short` after final commits.
- [ ] Record deployment handoff without deploying: prior Assistant tag, candidate immutable tag, test evidence, and rollback tag. The Tools image should not change for this feature.

