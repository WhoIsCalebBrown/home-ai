# Deterministic Home Assistant Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route common named, area, type/state, broad-state, and all-lights utterances to exact typed Home Assistant arguments, while reporting unavailable devices truthfully and preserving authorization protections.

**Architecture:** Add a small pure parser for high-confidence home utterances and invoke it early from `preflight_plan()`. The parser emits only existing `home_get_state`, `home_control`, and `home_find_device` contracts. Existing tool-layer entity resolution, authorization, bulk protections, reconciliation, and unavailable-state rendering remain the enforcement boundary.

**Tech Stack:** Python 3.12, FastAPI Assistant service, pytest, fake Home Assistant backend integration harness, Docker QA image.

**Spec:** [Assistant News, Media, and Home-Control Corrections](../specs/2026-09-19-assistant-news-media-home-control-design.md)

## Global Constraints

- Do not modify Home Assistant, Tuya, credentials, entities, or physical-switch state.
- Do not broaden the authorized entity set or weaken protected/bulk-load exclusions.
- Questions, negations, hypotheticals, and explanations must never become writes.
- Model alias repair remains defense in depth; common supported phrases must not depend on model-generated arguments.
- An unavailable target must never be described as successfully controlled.

## Review Focus

- `What lights are on?` must emit `{"domain": "light", "state": "on"}`.
- `What devices are on?` must emit `{"state": "on"}`, not `{"entity_or_area": "on"}` and not `{}`.
- `Turn on the office lights` must emit `{"entity_or_area": "office lights", "action": "turn_on"}`.
- Both verb orders (`turn on X`, `turn X on`) must work without matching questions such as “Can X turn on?”
- Existing whole-home, exclusion, authorization, and unavailable-device behavior must keep passing.

---

## Task 1: Define the typed home-language matrix

**Files:**

- Modify: `assistant/test_grounding_regressions.py:10-120`
- Modify: `qa/test_assistant_conversation_integration.py:70-180`
- Modify: `assistant/voice-api-app.py:3128-3440`

- [ ] Add `home_direct_plan` to the AST-slice `needed` set and bind it for unit tests.
- [ ] Add table-driven failing unit tests with exact expected arguments:

```python
@pytest.mark.parametrize(("text", "expected"), [
    ("What lights are on?", [("home_get_state", {"domain": "light", "state": "on"})]),
    ("Which outlets are off?", [("home_get_state", {"domain": "switch", "state": "off"})]),
    ("What devices are on?", [("home_get_state", {"state": "on"})]),
    ("Is the bedroom lamp on?", [("home_get_state", {"entity_or_area": "bedroom lamp", "state": "on"})]),
    ("Which office lights are on?", [("home_get_state", {"entity_or_area": "office lights", "domain": "light", "state": "on"})]),
    ("Turn on the office lights", [("home_control", {"entity_or_area": "office lights", "action": "turn_on"})]),
    ("Turn the office lights off", [("home_control", {"entity_or_area": "office lights", "action": "turn_off"})]),
    ("Turn off all the lights", [("home_control", {"entity_or_area": "all lights", "action": "turn_off"})]),
])
def test_home_direct_plan_contract(text, expected):
    assert home_direct_plan(text) == expected
```

- [ ] Add negative controls asserting `[]` for:
  - `Don't turn on the office lights.`
  - `Can the office lights turn on?`
  - `Why didn't the bedroom lamp turn on?`
  - `If I turn off the lights, will that save power?`
  - `I said turn on the office lights yesterday.`
- [ ] Update the existing deterministic-home expected domains from plural aliases (`lights`, `outlets`) to canonical tool domains (`light`, `switch`) only if the real Tools contract confirms those values.
- [ ] Run:

```bash
pytest -q assistant/test_grounding_regressions.py -k 'home_direct_plan or common_home_questions'
```

Expected: missing helper and the two reported state queries still produce wrong/no filters.

- [ ] Implement `home_direct_plan(text)` immediately before `preflight_plan()` using anchored `fullmatch` patterns and a single noun→domain mapping (`light/lamp -> light`, `outlet/plug/switch -> switch`).
- [ ] Parse read forms before control forms. Require an imperative control shape at the beginning of the utterance; reject negation, modal questions, conditionals, retrospective/explanatory wording.
- [ ] Preserve meaningful target text (`office lights`, `bedroom lamp`, `all lights`) in `entity_or_area`; normalize whitespace/hyphens but do not invent entity IDs.
- [ ] Call `home_direct_plan()` near the start of `preflight_plan()`, after an already-resolved exact home clarification/follow-up but before broad legacy home regexes. Replace overlapping legacy branches rather than maintaining two contradictory parsers.
- [ ] Re-run focused unit tests and commit:

```bash
git add assistant/voice-api-app.py assistant/test_grounding_regressions.py qa/test_assistant_conversation_integration.py
git commit -m "fix: deterministically parse common home commands"
```

## Task 2: Exercise the exact production conversation path

**Files:**

- Modify: `qa/test_assistant_conversation_integration.py:680-830`

- [ ] Add an integration test for `What lights are on?` that asserts the fake backend call log contains exactly `home_get_state` with `domain=light,state=on`, and the response names only returned on-lights plus unavailable lights when the backend includes them.
- [ ] Add an integration test for `What devices are on?` that asserts a broad authorized-state read with `state=on` and no `entity_or_area` misuse.
- [ ] Add an integration test for `Turn on the office lights` that asserts:
  - `home_control` receives `entity_or_area="office lights"` and `action="turn_on"`;
  - no Ollama-scripted tool call is needed;
  - exactly the resolved authorized office light is targeted.
- [ ] Add a second target whose fake state is `unavailable`; assert the answer says `unavailable`, the backend records no successful state transition for it, and the response contains no success claim.
- [ ] Run in the integration image:

```bash
docker build -f qa/Dockerfile.assistant_integration -t home-ai-assistant-plan-test .
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo home-ai-assistant-plan-test \
  pytest -p no:cacheprovider -q qa/test_assistant_conversation_integration.py \
  -k 'lights_are_on or devices_are_on or office_lights or unavailable_home'
```

Expected before implementation: empty/wrong read filters or dependence on model-generated `device_id`.

- [ ] If deterministic rendering currently drops unavailable entries, fix only the Assistant's home result renderer to list the returned state. Do not infer that a physical switch, Wi-Fi, or Tuya is the cause.
- [ ] Re-run and commit:

```bash
git add assistant/voice-api-app.py qa/test_assistant_conversation_integration.py
git commit -m "test: cover named and typed home conversations"
```

## Task 3: Preserve alias repair and safety boundaries

**Files:**

- Modify: `qa/test_assistant_conversation_integration.py:90-180`
- Modify if a test exposes a gap: `assistant/voice-api-app.py:1858-1915`
- Modify if a test exposes a gap: `qa/test_home_conversation_contract.py`

- [ ] Keep the existing `device_id=office_lights` normalization regression as defense in depth, but add tests that:
  - an ambiguous or empty `device_id` does not broaden to all devices;
  - explicit `entity_ids` remain unchanged;
  - unknown argument aliases are rejected/ignored rather than converted into another target;
  - deterministic common commands never reach this repair path.
- [ ] Re-run home contract tests:

```bash
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo home-ai-assistant-plan-test \
  pytest -p no:cacheprovider -q qa/test_home_conversation_contract.py \
  qa/test_assistant_conversation_integration.py -k 'home'
```

- [ ] If failures reveal a genuine normalization gap, make the smallest safe change to `normalize_home_tool_arguments()`; do not add fuzzy entity resolution in the Assistant.
- [ ] Confirm these existing cases still pass: result-set exclusions, protected bulk loads, negative follow-ups, exact brightness, clarification binding, and activity questions.
- [ ] Commit only if code/tests changed:

```bash
git add assistant/voice-api-app.py qa/test_assistant_conversation_integration.py qa/test_home_conversation_contract.py
git commit -m "test: preserve home routing safety boundaries"
```

## Task 4: Run safe QA and prepare immutable deployment handoff

**Files:**

- Modify only when an actual regression requires it

- [ ] Run local unit regressions:

```bash
pytest -q assistant/test_grounding_regressions.py
```

- [ ] Run full safe QA:

```bash
qa/run_safe_qa.sh
```

- [ ] Run `git diff --check` and inspect `git status --short`.
- [ ] Confirm no HA/Tuya configuration, secret, or entity-registry file changed.
- [ ] Record deployment handoff without deploying:
  - current production Assistant image tag;
  - candidate immutable Assistant tag;
  - QA evidence for the reported phrases and negative controls;
  - prior tag for rollback.
- [ ] Tools should not need a new image. If implementation discovers an actual Tools defect, stop and amend the design/plan before changing that service.
