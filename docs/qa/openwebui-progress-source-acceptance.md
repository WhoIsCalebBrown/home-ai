# Open WebUI progress and source transparency acceptance

Date: 2026-09-19

## Result: PASS — disposable acceptance only

This used a new Docker network, a new temporary Open WebUI data directory, and
the unchanged pinned image. It did not attach to production, Home Assistant, or
any production provider. The provider was the deterministic authenticated QA
fixture in qa/openwebui_live_p0.py. It sends ordinary Working content, holds a
fake source for 1.5 seconds, then sends the final answer and rich trace. It
never executes a real tool.

Pinned client:

~~~text
ghcr.io/open-webui/open-webui@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
~~~

## Reproduction

Run from the repository root. The fixture bearer key below is public QA-only
data; it is not a Home-AI credential.

~~~bash
set -eu
docker build -t home-ai-progress-source-qa -f qa/Dockerfile qa
export QA_NETWORK=home-ai-progress-source-acceptance
export QA_DATA="$(mktemp -d /tmp/openwebui-progress-source.XXXXXX)"
docker network create "$QA_NETWORK"
docker run -d --rm --name home-ai-progress-source-provider \
  --network "$QA_NETWORK" --network-alias progress-source-provider \
  --mount type=bind,src="$PWD/qa",dst=/workspace/qa,readonly \
  --workdir /workspace/qa home-ai-progress-source-qa sh -ec \
  'python -m pip install --no-cache-dir uvicorn==0.35.0 >/dev/null && exec python -m uvicorn openwebui_live_p0:progress_source_probe --host 0.0.0.0 --port 8000'
docker run -d --rm --name home-ai-progress-source-webui \
  --network "$QA_NETWORK" -p 127.0.0.1:18094:8080 \
  -e OPENAI_API_BASE_URLS='http://progress-source-provider:8000/v1' \
  -e OPENAI_API_KEYS='progress-source-fixture-key' -e ENABLE_SIGNUP=true \
  -v "$QA_DATA:/app/backend/data" \
  ghcr.io/open-webui/open-webui@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
~~~

After health succeeds, create the temporary fixture user through the disposable
UI. Authenticate the QA harness without printing its temporary user token:

~~~bash
export OPENWEBUI_TOKEN="$(curl -fsS -X POST http://127.0.0.1:18094/api/v1/auths/signin \
  -H 'Content-Type: application/json' \
  -d '{"email":"progress-source@example.test","password":"FixturePassword123!"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
docker run --rm --network host -e OPENWEBUI_TOKEN \
  -v "$PWD/qa:/workspace/qa:ro" -v /tmp:/evidence home-ai-progress-source-qa \
  python /workspace/qa/openwebui_live_p0.py \
  --base-url http://127.0.0.1:18094 \
  --model home-ai-progress-source-fixture \
  --progress-source-acceptance \
  --output /evidence/openwebui-progress-source-harness.json
~~~

The acceptance mode records only ordinary content after it passes the
safe-display contract. It requires timestamped Working before final content,
one through four distinct safe labels, a separator, an answer, a rich trace,
and no fixture raw-query/snippet/private-URL/tool-exception sentinel. It writes
the accepted display text to the authenticated disposable chat for reload.

## Observed graphical acceptance

Playwright Chromium created a disposable account, sent the fixture prompt, took
an early screenshot while the source was blocked, waited for completion, then
reloaded the page.

| Check | Observation |
| --- | --- |
| Timing | Harness timestamps: Working 18.89 ms; separator 1517.42 ms; first final-answer/trace content 1517.61 ms. Progress led final content by 1498.72 ms. |
| Visible progress | The early snapshot contained Working, Searching the web, and Reading example.com; it did not contain the final answer. |
| Persistence | Reload retained the two-line preamble, separator, answer, and Research activity; two lines is within the four-line limit. |
| Fetched source | Exactly one https://example.com/news anchor was present and clickable. |
| Hostile title | The Markdown-shaped hostile title rendered as inert spoof/evil.example text; no anchor had an evil.example destination. |
| Display privacy | The accepted display contained no raw fixture query, snippet, private URL, token, or exception sentinel. |

Screenshots were inspected before teardown and are deliberately not committed:

~~~text
/tmp/openwebui-progress-source.b4E1it/progress-visible-before-source-completes.png
sha256 6a34b4d59048ca2508655513b484511f779f36745ff73fd52f794cdf878ebd2e

/tmp/openwebui-progress-source.b4E1it/progress-source-final.png
sha256 dff8daa6760863bc80465d0084870934ec64629bf96dfc76bea98106be9e549b
~~~

## Voice acceptance

The production-shaped TTS normalization and gateway tests below verify the
actual Assistant boundary: combined preamble/answer/trace maps to the plain
answer only, and progress-only plus trace/source-only requests return HTTP 204
without invoking a synthesizer. The disposable fixture also returns 204 for
display-only input:

~~~bash
docker run --rm --network "$QA_NETWORK" curlimages/curl:8.11.1 \
  -sS -o /dev/null -w '%{http_code}\n' -X POST \
  http://progress-source-provider:8000/v1/audio/speech \
  -H 'Authorization: Bearer progress-source-fixture-key' \
  -H 'Content-Type: application/json' \
  -d '{"input":"**Working**\n- Searching the web…\n\n---\n\n<!-- home-ai-display-trace -->"}'
~~~

Observed: 204. The fixture has no synthesizer; the production-shaped suite is
the authoritative test for the real TTS endpoint.

## Production-shaped test evidence

Focused suite, run once:

~~~bash
docker build -t home-ai-assistant-sdd -f assistant/Dockerfile assistant
docker run --rm --network none --read-only home-ai-assistant-sdd \
  python -m pytest -q assistant/test_trace_projection.py \
  assistant/test_progress_events.py assistant/test_openai_gateway.py \
  assistant/test_tts_normalization.py assistant/test_grounding_regressions.py \
  qa/test_assistant_conversation_integration.py
~~~

Result: one focused execution was started in the rebuilt
home-ai-assistant-sdd-qa image (image ID
ac97c355b4d1a18c51e96926ace19fb9af9d1671c7a6f05c66f039e60d214f06).
It selected 643 tests. Its attached terminal capture was cut off at 89 percent
by the runner while the container continued; the final full run below is the
authoritative completion evidence and includes all 643 selected tests. The
focused command used no pytest cache provider, so it avoided the read-only
cache warnings seen in the exact full command.

Final full suite, run once and not rerun after documentation-only edits:

~~~bash
docker build -t home-ai-assistant-sdd -f assistant/Dockerfile assistant
docker run --rm --network none --read-only home-ai-assistant-sdd python -m pytest -q
~~~

Result: 1087 passed, 563 warnings in 54.65s. Warnings were the existing
pytest-asyncio fixture-loop-scope deprecation, Python audioop deprecation, 280
FastAPI startup-event deprecations, and two pytest cache write warnings caused
by the intentionally read-only repository. No tests failed or skipped.

The literal plan command using build context assistant was also attempted once
and failed before pytest: assistant/Dockerfile uses COPY assistant/... and
therefore requires the repository-root build context. This is the previously
recorded task-2 Docker-context mismatch, not an application failure. The
commands above use the existing production-shaped QA Dockerfile with the same
Python/base/system/runtime requirements and pytest added only for verification.

## Scope and teardown

No deployment or Home Assistant/Open WebUI configuration change is authorized.
Remove only the disposable objects after evidence is saved:

~~~bash
docker stop home-ai-progress-source-provider home-ai-progress-source-webui
docker network rm "$QA_NETWORK"
case "$QA_DATA" in /tmp/openwebui-progress-source.*) find "$QA_DATA" -depth -delete ;; *) exit 1 ;; esac
~~~
