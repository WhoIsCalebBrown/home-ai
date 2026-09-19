# Open WebUI progress and source transparency acceptance

Date: 2026-09-19

## Disposable, unchanged-client scope

The acceptance uses only a new Docker network and data directory. The client is
the unchanged pinned image below. It does not attach to production Open WebUI,
Home Assistant, voiceai, or a production provider, and it does not modify any
deployment or configuration.

~~~text
ghcr.io/open-webui/open-webui@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
~~~

The provider is the authenticated QA fixture in qa/openwebui_live_p0.py. It
requires a sentinel raw query in the UI prompt, retains a private URL, raw
snippet, token, raw error, and hostile title internally, emits only safe
progress labels, then holds the fake source for three seconds before producing
the final answer and safe rich trace.

## Reproducible browser acceptance

Run from the repository root. The fixture key/password are public disposable
test data, not Home-AI credentials.

~~~bash
set -eu
docker build -t home-ai-progress-source-qa -f qa/Dockerfile qa
export QA_NETWORK=home-ai-progress-source-acceptance
export QA_DATA="$(mktemp -d /tmp/openwebui-progress-source.XXXXXX)"
export PLAYWRIGHT_HOME="$(mktemp -d /tmp/openwebui-progress-source-playwright.XXXXXX)"
npm install --prefix "$PLAYWRIGHT_HOME" playwright@1.52.0
docker pull mcr.microsoft.com/playwright:v1.52.0-noble
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
until curl -fsS http://127.0.0.1:18094/health >/dev/null; do sleep 1; done
docker run --rm --network "$QA_NETWORK" --shm-size=1gb \
  -e NODE_PATH=/node_modules \
  -v "$PLAYWRIGHT_HOME/node_modules:/node_modules:ro" \
  -v "$PWD/qa:/workspace/qa:ro" -v "$QA_DATA:/evidence" \
  mcr.microsoft.com/playwright:v1.52.0-noble \
  node /workspace/qa/openwebui_progress_source_acceptance.mjs \
  --base-url http://home-ai-progress-source-webui:8080 \
  --artifacts-dir /evidence/browser
cat "$QA_DATA/browser/results.json"
~~~

The committed browser script creates or signs into the disposable account
through the UI, types the sentinel prompt into the contenteditable composer,
captures an early DOM/screenshot before the source release, waits for the
final answer, reloads the same UI, and then examines the naturally persisted
assistant message. It makes no request to the chats persistence API.

Artifacts are deterministic and inspectable under QA_DATA/browser while the
fixture is retained: results.json, progress-visible-before-source-completes.png,
progress-source-final.png, and progress-source-reload.png. They are not
committed because they include a temporary browser account and the intentional
raw-query user prompt. Re-run the command above to regenerate them; acceptance
does not rely on deleted temporary-file hashes.

The script fails unless all of these hold in the assistant DOM:

- ordinary Working/progress is visible before the delayed final response;
- the completed and reloaded messages retain one through four safe preamble
  lines and a rendered separator;
- final answer precedes Research activity;
- exactly one visible, enabled source anchor has href https://example.com/news;
- hostile title text is inert: no evil.example anchor, img/script, or event
  handler node is created;
- raw query, snippet, private/internal URL, token, or raw error is absent from
  the assistant message.

## Observed result

The completed browser invocation returned:

~~~json
{
  "fixture_delay_ms": 3000,
  "early_progress_visible": true,
  "early_final_visible": false,
  "early_elapsed_ms": 484,
  "completed_progress_line_count": 2,
  "final_elapsed_ms": 3394,
  "reloaded_progress_line_count": 2,
  "status": "pass"
}
~~~

This is graphical acceptance evidence from the UI itself; no synthetic
assistant message was inserted.

The fixture speech endpoint returned 204 for a preamble/trace-only request.
The real speech boundary is covered by the production-shaped TTS normalization
and OpenAI gateway tests: preamble and rich trace are removed, while
progress-only and trace-only input returns 204 without invoking a synthesizer.

## Production-shaped test evidence

The literal brief command using context assistant cannot build: the Dockerfile
contains COPY assistant/... and therefore requires repository-root context.
The following is the repository's successful production-shaped QA command,
using the same Python/base/system/runtime dependencies and adding pytest only
for verification:

~~~bash
docker build -f qa/Dockerfile.assistant_integration -t home-ai-assistant-sdd-qa .
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -w /repo home-ai-assistant-sdd-qa python -m pytest -q -p no:cacheprovider \
  assistant/test_trace_projection.py assistant/test_progress_events.py \
  assistant/test_openai_gateway.py assistant/test_tts_normalization.py \
  assistant/test_grounding_regressions.py \
  qa/test_assistant_conversation_integration.py
~~~

Focused result: 643 passed, 561 warnings in 30.39s. The warnings are the
existing audioop and FastAPI startup-event deprecations; the cache provider was
disabled so the read-only cache warnings are absent.

The final full run from the reviewed pre-fix commit remains:

~~~text
1087 passed, 563 warnings in 54.65s
~~~

Its warnings were existing pytest-asyncio/audioop/FastAPI deprecations plus two
expected pytest cache write warnings from the deliberately read-only repository.
The fix round changes QA/docs only; it does not alter Assistant runtime code.

## Teardown

~~~bash
docker stop home-ai-progress-source-provider home-ai-progress-source-webui
docker network rm "$QA_NETWORK"
case "$QA_DATA" in /tmp/openwebui-progress-source.*) find "$QA_DATA" -depth -delete ;; *) exit 1 ;; esac
case "$PLAYWRIGHT_HOME" in /tmp/openwebui-progress-source-playwright.*) find "$PLAYWRIGHT_HOME" -depth -delete ;; *) exit 1 ;; esac
~~~
