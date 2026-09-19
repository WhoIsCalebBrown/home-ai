# Deep Current-News Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit in-depth current-news requests collect enough article evidence, reject unsupported current facts, and produce a genuinely detailed answer or an honest incomplete-research response.

**Architecture:** Keep the existing bounded research loop and web tools, but centralize source selection and evidence accounting in pure helpers. Every successful search path—including recovery searches—feeds the same fetch scheduler. Deep-mode synthesis is allowed only after the evidence contract is satisfied, and receives a depth-specific instruction that overrides the normal short-answer default.

**Tech Stack:** Python 3.12, FastAPI Assistant service, pytest, fake Tools/Ollama integration harness, Docker QA image.

**Spec:** [Assistant News, Media, and Home-Control Corrections](../specs/2026-09-19-assistant-news-media-home-control-design.md)

## Global Constraints

- Do not hard-code Canadian office-holder names or news stories.
- Do not treat search snippets as fetched evidence.
- Do not let failed/blocked fetches count toward readiness.
- Preserve the existing research call ceilings and use the current `web_search`/`web_fetch` contracts.
- Do not contact production services from tests.

## Review Focus

- Recovery searches must fetch sources just like model-issued searches.
- Deep mode must require at least three successful searches and two successful, independently hosted fetches when candidate URLs exist.
- Current office-holder claims must be based on fetched evidence, not model memory or snippets.
- The explicit depth request must override the global one-or-two-sentence style default.
- Failure to meet the evidence contract must produce a limitation, not a plausible-sounding roundup.

---

## Task 1: Specify the deep-research evidence contract with pure tests

**Files:**

- Modify: `assistant/test_grounding_regressions.py:10-120`
- Modify: `assistant/test_grounding_regressions.py:880-940`
- Modify: `assistant/voice-api-app.py:3863-3940`

- [ ] Add `research_profile`, `research_fetch_candidates`, `research_evidence_shape`, and `deep_research_ready` to the AST-slice `needed` set and bind them from `namespace`.
- [ ] Add failing tests that make the contract explicit:

```python
def test_in_depth_canadian_news_selects_deep_profile():
    profile = research_profile("Give me an in-depth review of today's news in Canada")
    assert profile["mode"] == "deep"
    assert profile["minimum_searches"] == 3
    assert profile["minimum_fetches"] == 2


def test_deep_research_requires_successful_independent_fetches():
    evidence = [
        {"tool": "web_search", "status": "ok", "result": {"results": [{"url": "https://a.example/1"}]}},
        {"tool": "web_search", "status": "ok", "result": {"results": [{"url": "https://b.example/2"}]}},
        {"tool": "web_search", "status": "ok", "result": {"results": [{"url": "https://c.example/3"}]}},
        {"tool": "web_fetch", "status": "ok", "result": {"url": "https://a.example/1", "content": "article one"}},
    ]
    assert not deep_research_ready(evidence, candidate_urls_exist=True)
    evidence.append({"tool": "web_fetch", "status": "ok", "result": {"url": "https://b.example/2", "content": "article two"}})
    assert deep_research_ready(evidence, candidate_urls_exist=True)
```

- [ ] Add a source-selection test showing URL de-duplication and domain diversity, with authoritative domains selected before repeated commercial domains.
- [ ] Run the focused tests and confirm they fail because the new helpers/profile fields do not exist:

```bash
pytest -q assistant/test_grounding_regressions.py -k 'deep_research or in_depth_canadian_news or research_fetch_candidates'
```

Expected: collection or assertion failures naming the missing contract.

- [ ] Implement the pure helpers next to `research_profile()`:
  - `research_fetch_candidates(result, seen_urls, seen_domains, limit)` returns normalized, unique URLs; ranks `.gc.ca`/`.gov.*` and source diversity ahead of a second URL from the same host.
  - `research_evidence_shape(live_results)` counts successful searches, successful non-empty fetches, distinct fetched URLs, and distinct fetched domains.
  - `deep_research_ready(live_results, candidate_urls_exist)` requires three successful searches and, when URLs were available, at least two successful fetches from two domains.
  - Extend the deep profile with `minimum_searches=3`, `minimum_fetches=2`; use zero/one values appropriate to quick/normal modes so callers do not embed magic numbers.
- [ ] Run the focused tests and confirm they pass.
- [ ] Commit:

```bash
git add assistant/voice-api-app.py assistant/test_grounding_regressions.py
git commit -m "test: define deep news evidence contract"
```

## Task 2: Route every search result through one bounded fetch scheduler

**Files:**

- Modify: `assistant/voice-api-app.py:5720-5870`
- Modify: `qa/test_assistant_conversation_integration.py:180-540`
- Modify: `qa/test_assistant_conversation_integration.py` (add research scenarios near other web scenarios)

- [ ] Extend `FakeToolsBackend` with deterministic `web_search` and `web_fetch` fixtures/call logging if its existing implementation cannot return multiple URLs and controlled fetch failures.
- [ ] Add a failing integration test whose fake Ollama stops after discovery, forcing the recovery-search branch. Assert:
  - exactly three successful `web_search` calls in deep mode;
  - at least two `web_fetch` calls;
  - fetched URLs come from at least two domains;
  - no call exceeds `profile["max_calls"]`.
- [ ] Add a second failing test where the first fetch fails and a later candidate succeeds; assert the failure does not consume the successful-fetch requirement and the scheduler continues within budget.
- [ ] Run:

```bash
docker build -f qa/Dockerfile.assistant_integration -t home-ai-assistant-plan-test .
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo home-ai-assistant-plan-test \
  pytest -p no:cacheprovider -q qa/test_assistant_conversation_integration.py -k 'deep_news or research_recovery'
```

Expected: deep recovery performs searches but no recovery-result fetches.

- [ ] Extract one local async fetch scheduler in `respond()` (or a module helper with injected invoker) that:
  1. receives each successful `web_search` result;
  2. asks `research_fetch_candidates()` for bounded candidates;
  3. calls `web_fetch` with `extract="article"` and the mode-specific `max_chars`;
  4. appends both `live_results` and compact tool messages;
  5. tracks attempted URLs separately from successful domains;
  6. stops at `max_calls`.
- [ ] Call that scheduler from both the model-tool-call branch and the recovery-search branch. Remove the duplicated model-only auto-fetch block.
- [ ] Re-run the two integration tests and confirm pass.
- [ ] Commit:

```bash
git add assistant/voice-api-app.py qa/test_assistant_conversation_integration.py
git commit -m "fix: fetch evidence from deep news recovery searches"
```

## Task 3: Enforce grounded, detailed synthesis and incomplete-evidence behavior

**Files:**

- Modify: `assistant/voice-api-app.py:3890-3930`
- Modify: `assistant/voice-api-app.py:5880-5950`
- Modify: `qa/test_assistant_conversation_integration.py` (deep-news scenarios)

- [ ] Add failing integration tests for these outcomes:
  - With three searches and two independent fetched articles, the final streaming prompt contains a deep-news synthesis instruction requiring multiple supported topics and allowing a multi-paragraph answer.
  - A snippet names an office-holder but fetched authoritative evidence names a different holder; the synthesis prompt explicitly limits the claim to fetched evidence and the canned final answer is the fetched value.
  - With candidate URLs but fewer than two successful fetches, the assistant returns an explicit incomplete-research limitation without calling final synthesis.
- [ ] The prompt assertion should target a stable contract string, for example:

```python
assert "Current office-holder claims require fetched evidence" in system_text
assert "The user explicitly requested depth" in system_text
```

- [ ] Run the focused integration tests and confirm failure.
- [ ] Add `deep_research_synthesis_instruction(shape)` that tells the model:
  - the user explicitly requested depth, so the normal short-answer default does not apply;
  - organize several distinct supported developments with context and significance;
  - use only fetched evidence for current office-holders and institutional facts;
  - omit conflicts that cannot be resolved from fetched sources;
  - never mention internal tool names.
- [ ] Before `stream_final()`, evaluate `deep_research_ready()`. If deep mode is not ready, return a deterministic limitation such as: `I couldn't complete a reliable in-depth roundup because I wasn't able to fetch enough independent current sources.` Do not ask the model to fill the gap.
- [ ] Append the deep synthesis instruction only after readiness succeeds. Preserve the existing quick/normal behavior.
- [ ] Re-run focused tests and then the complete Assistant integration module:

```bash
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo home-ai-assistant-plan-test \
  pytest -p no:cacheprovider -q qa/test_assistant_conversation_integration.py
```

- [ ] Commit:

```bash
git add assistant/voice-api-app.py qa/test_assistant_conversation_integration.py
git commit -m "fix: enforce deep news synthesis evidence"
```

## Task 4: Run the safe regression lane and document deployment checks

**Files:**

- Modify only if required by an actual failure: relevant test/code files above

- [ ] Run the unit regression file:

```bash
pytest -q assistant/test_grounding_regressions.py
```

- [ ] Run the repository safe QA lane:

```bash
qa/run_safe_qa.sh
```

Expected: all tests pass; the lane runs with `--network none` and fake write adapters.

- [ ] Inspect `git diff --check` and `git status --short`.
- [ ] Record for deployment handoff (do not deploy in this task): prior Assistant image tag, candidate immutable tag, QA commands, and rollback tag. Tools image should remain unchanged unless implementation proves a real tool-layer defect.
- [ ] Commit any test-driven correction made during the full run; otherwise leave the branch clean.

