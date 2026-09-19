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
requires a sentinel raw query in the UI prompt and feeds a raw tool-result list
through the actual assistant trace_projection.project_trace function and the
actual voice-api-app.py openai_tool_trace_footer function. That raw metadata
contains a private/internal token-bearing URL, raw query, snippet, token, raw
exception, hostile HTML/Markdown title, CGNAT/local/malformed destinations, and
one safe fetched URL. The provider uses the production streaming response,
speech registry, and `/v1/audio/speech` handler. Only the external turn and
synthesizer IO are replaced: the turn holds its source for three seconds, and
the synthesizer records and returns exactly the text it receives. The fixture
does not manufacture 204 responses.

## Reproducible browser acceptance

Run from the repository root. The fixture key/password are public disposable
test data, not Home-AI credentials.

~~~bash
set -eu
docker build -f qa/Dockerfile.assistant_integration -t home-ai-assistant-sdd-qa .
export QA_NETWORK=home-ai-progress-source-acceptance
export QA_DATA="$(mktemp -d /tmp/openwebui-progress-source.XXXXXX)"
export PLAYWRIGHT_HOME="$(mktemp -d /tmp/openwebui-progress-source-playwright.XXXXXX)"
npm install --prefix "$PLAYWRIGHT_HOME" playwright@1.52.0
docker pull mcr.microsoft.com/playwright:v1.52.0-noble
docker network create "$QA_NETWORK"
docker run -d --rm --name home-ai-progress-source-provider \
  --network "$QA_NETWORK" --network-alias progress-source-provider \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/repo:ro" -w /repo home-ai-assistant-sdd-qa \
  python -m uvicorn qa.openwebui_live_p0:progress_source_probe --host 0.0.0.0 --port 8000
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
  -v "$PWD:/workspace:ro" -v "$QA_DATA:/evidence" \
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
- the projected hostile title's `Unsafe title witness` prefix is visible as
  inert human text; no evil.example anchor, img/script, or event-handler node
  is created;
- raw query, snippet, private/internal URL, token, or raw error is absent from
  the assistant message.
- the native renderer and Open WebUI create only the approved public anchor;
- actual pinned `getMessageContentParts`, `cleanText`, and `removeFormattings`
  from `/_app/immutable/chunks/BzfgYq-h.js.map` preprocess full displays and
  progress/footer fragments in punctuation, paragraphs, and none modes;
- those inputs reach the production speech handler: metadata returns 204 with
  zero synthesis calls, and answer-bearing parts synthesize only the answer;
- live progress parts return 204 before the answer is registered.

## Observed result

The completed browser invocation returned:

~~~json
{
  "fixture_delay_ms": 3000,
  "early_progress_visible": true,
  "early_final_visible": false,
  "early_elapsed_ms": 487,
  "live_speech_silent_count": 5,
  "completed_progress_line_count": 2,
  "final_elapsed_ms": 3413,
  "reloaded_progress_line_count": 2,
  "speech": {
    "punctuation": {"silent": 13, "spoken": 1},
    "paragraphs": {"silent": 39, "spoken": 1},
    "none": {"silent": 2, "spoken": 1}
  },
  "native_anchors": ["https://example.com/news"],
  "status": "pass"
}
~~~

This is graphical acceptance evidence from the UI itself; no synthetic
assistant message was inserted.

Speech processing is evaluated from the pinned client's source map by
`qa/openwebui_pinned_speech.mjs`, which erases TypeScript annotations only.
It posts the resulting parts to the real handler through the disposable QA
provider and checks recorded synthesis calls. This verifies the server TTS
boundary, not browser-local speech engines or audio playback quality.

Source-map SHA-256:
`ba6079a375623d62108dbe67fa834a4f836ca5e796c4646aee0325a0a15e1f47`.
Current screenshots were written to the disposable evidence directory, then
preserved in this worktree's ignored
`.superpowers/sdd/2026-09-19-openwebui-progress-and-source-transparency/final-browser/`
before the disposable UI and its data were removed:

| Screenshot | SHA-256 |
| --- | --- |
| progress-visible-before-source-completes.png | b57f2278a0071f280679b1296bda1f924ad447eda8695b82da84940fa7aa205d |
| progress-source-final.png | be0c9234b6b2124efc2695be4417d29fedce580e3ab49235b44d1e9f2b5aa161 |
| progress-source-reload.png | 14e04c5cde4164981207a254a669819606a24e52bb8c65f87c537b00771ca200 |

The fragment registry retains the existing 15-minute lifetime and 256-display
capacity. An expired or pre-restart isolated plain source title has no
provenance; it cannot safely be distinguished from ordinary speech input.
Complete unregistered Markdown footers retain the bounded grammar fallback.
Registered ordinary answers take precedence over colliding metadata fragments.

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
  assistant/test_trace_projection.py assistant/test_trace_rendering.py assistant/test_progress_events.py \
  assistant/test_openai_gateway.py assistant/test_tts_normalization.py \
  assistant/test_grounding_regressions.py \
  qa/test_assistant_conversation_integration.py
~~~

Focused result: 670 passed, 563 warnings in 26.96s. The warnings are the
existing audioop and FastAPI startup-event deprecations; the cache provider was
disabled so the read-only cache warnings are absent.

The current fix-round full production-shaped suite used this exact runnable
command:

~~~bash
docker build -f qa/Dockerfile.assistant_integration -t home-ai-assistant-sdd-qa .
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -w /repo home-ai-assistant-sdd-qa python -m pytest -q -p no:cacheprovider --tb=short
~~~

Result:

~~~text
1112 passed, 563 warnings in 52.41s
~~~

Its warnings were existing audioop/FastAPI deprecations; pytest also reports its
existing unset async fixture-scope deprecation. Cache writes were disabled.
The rebuilt QA image was
`sha256:9b4060805748de6ee401614067d8bc1746f80dd4d89d4eac1a057db9ee4111fb`.
This final fix changes the owned speech boundary, display URL validation and
trace selection, with one packaging COPY for the new speech helper. Open WebUI,
production services, deployments and the separate branch blockers are unchanged.

## Teardown

~~~bash
docker stop home-ai-progress-source-provider home-ai-progress-source-webui
docker network rm "$QA_NETWORK"
case "$QA_DATA" in /tmp/openwebui-progress-source.*) find "$QA_DATA" -depth -delete ;; *) exit 1 ;; esac
case "$PLAYWRIGHT_HOME" in /tmp/openwebui-progress-source-playwright.*) find "$PLAYWRIGHT_HOME" -depth -delete ;; *) exit 1 ;; esac
~~~
