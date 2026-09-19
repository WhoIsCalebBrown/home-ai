# Open WebUI transient-status compatibility probe

Date: 2026-09-19

## Decision: BLOCKED

Pinned Open WebUI 0.11.3 does not render the candidate non-content OpenAI SSE
delta transiently:

```json
{"choices":[{"index":0,"delta":{"home_ai_status":{"phase":"tool_started","label":"Reading Example News…"}},"finish_reason":null}]}
```

The second status has `phase: "tool_finished"` and label `Read Example News`.
Neither candidate has `delta.content`.

## Reproducible disposable procedure

Run from the repository root. This uses a new Docker network and data
directory only; it never attaches to production `voiceai`.

```bash
set -eu
docker build -t home-ai-status-probe-qa -f qa/Dockerfile qa
export PROBE_NETWORK=home-ai-status-probe-20260919
export PROBE_DATA="$(mktemp -d /tmp/openwebui-status-probe.XXXXXX)"
docker network create "$PROBE_NETWORK"
docker run -d --rm --name home-ai-status-provider \
  --network "$PROBE_NETWORK" --network-alias status-probe \
  --mount type=bind,src="$PWD/qa",dst=/workspace/qa,readonly \
  --workdir /workspace/qa home-ai-status-probe-qa sh -ec \
  'python -m pip install --no-cache-dir uvicorn==0.35.0 && exec python -m uvicorn openwebui_status_probe:app --host 0.0.0.0 --port 8000'
docker run -d --rm --name home-ai-status-webui \
  --network "$PROBE_NETWORK" -p 127.0.0.1:18093:8080 \
  -e OPENAI_API_BASE_URLS='http://status-probe:8000/v1' \
  -e OPENAI_API_KEYS='probe-key' -e ENABLE_SIGNUP=true \
  -v "$PROBE_DATA:/app/backend/data" \
  ghcr.io/open-webui/open-webui@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
```

Both provider and tests use the same valid QA image,
`home-ai-status-probe-qa`. The provider installs pinned `uvicorn==0.35.0` only
inside its short-lived writable container; neither image nor production is
changed. Pinned Open WebUI digest:

```text
ghcr.io/open-webui/open-webui@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
```

Verify the provider from a container on its Docker network (not host curl,
because `status-probe` is Docker DNS), then record the actual synthetic stream:

```bash
docker run --rm --network "$PROBE_NETWORK" curlimages/curl:8.11.1 \
  -fsS http://status-probe:8000/v1/models
docker run --rm --network "$PROBE_NETWORK" curlimages/curl:8.11.1 \
  -NsS -X POST http://status-probe:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"home-ai-probe","stream":true,"messages":[{"role":"user","content":"Run status probe"}]}'
```

The first response advertises `home-ai-probe`. The second is exactly role →
`tool_started` → `tool_finished` → `Final probe answer.` → stop → one
`[DONE]`.

### Browser request capture and persisted-message check

Install the disposable browser driver outside the repository. The credentials
below belong only to the temporary `PROBE_DATA` directory.

```bash
mkdir -p /tmp/openwebui-status-probe-playwright
npm install --prefix /tmp/openwebui-status-probe-playwright playwright@1.52.0
docker pull mcr.microsoft.com/playwright:v1.52.0-noble
docker run --rm --network "$PROBE_NETWORK" --shm-size=1gb \
  -e NODE_PATH=/node_modules \
  -v /tmp/openwebui-status-probe-playwright/node_modules:/node_modules:ro \
  mcr.microsoft.com/playwright:v1.52.0-noble node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.setDefaultTimeout(10000);
  const chatRequests = [];
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().includes('chat/completions')) {
      const body = JSON.parse(request.postData());
      chatRequests.push({ method: request.method(), url: request.url(), model: body.model,
        stream: body.stream, user_message: body.user_message.content });
    }
  });
  await page.goto('http://home-ai-status-webui:8080/auth?redirect=%2F', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Get started' }).click();
  await page.locator('input').nth(0).fill('Probe User');
  await page.locator('input[type=email]').fill('probe@example.test');
  await page.locator('input[type=password]').fill('ProbePassword123!');
  await page.getByRole('button', { name: 'Create Admin Account' }).click({ force: true, noWaitAfter: true });
  await page.waitForTimeout(1800);
  const releaseNotes = page.locator('button').filter({ hasText: 'Okay' });
  if (await releaseNotes.count()) await releaseNotes.click({ force: true, noWaitAfter: true });
  await page.locator('button[aria-label*=Selected]').waitFor();
  const input = page.locator('[contenteditable=true]');
  await input.fill('Run status probe');
  const started = Date.now();
  await input.press('Control+Enter');
  for (const target of [400, 1600, 3200]) {
    await page.waitForTimeout(Math.max(0, target - (Date.now() - started)));
    const text = await page.locator('body').innerText();
    console.log('T+' + target + ' started=' + text.includes('Reading Example News…') +
      ' finished=' + text.includes('Read Example News') + ' final=' + text.includes('Final probe answer.'));
  }
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(700);
  const persisted = await page.locator('body').innerText();
  console.log('RELOAD started=' + persisted.includes('Reading Example News…') +
    ' finished=' + persisted.includes('Read Example News') + ' final=' + persisted.includes('Final probe answer.'));
  console.log('CHAT_REQUEST=' + JSON.stringify(chatRequests));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });"
```

The capture prints the actual browser `POST /api/chat/completions` request but
only stable synthetic fields: method, URL, model, stream, and probe message.
It deliberately omits session IDs, authentication, generated profile fields,
and tokens. The full-page reload is the safe persistence check: it observes the
ordinary logged-in UI, without reading SQLite, host files, or another user's
data.

Expected output:

```text
T+400 started=false finished=false final=false
T+1600 started=false finished=false final=false
T+3200 started=false finished=false final=true
RELOAD started=false finished=false final=true
CHAT_REQUEST=[{"method":"POST","url":"http://home-ai-status-webui:8080/api/chat/completions","model":"home-ai-probe","stream":true,"user_message":"Run status probe"}]
```

The statuses never render before final content and do not appear after reload;
the only persisted assistant content is the final answer. The third condition
alone does not pass the gate; all three are required.

Teardown removes only the disposable objects:

```bash
docker stop home-ai-status-provider home-ai-status-webui
docker network rm "$PROBE_NETWORK"
case "$PROBE_DATA" in /tmp/openwebui-status-probe.*) find "$PROBE_DATA" -depth -delete ;; *) exit 1 ;; esac
```

## Required next decision

Do not begin product streaming/progress work from this event shape. Present
these choices for approval; do not choose automatically:

- Maintain a minimal Open WebUI extension that renders `home_ai_status` transiently.
- Persist compact progress lines in assistant content and accept that they remain in conversation history.

## QA wire-format command

```bash
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$PWD/qa:/workspace/qa:ro" home-ai-status-probe-qa \
  python -m pytest -q -p no:cacheprovider /workspace/qa/openwebui_status_probe.py
```

This command currently reports `2 passed`. The test posts to the FastAPI
endpoint and parses actual emitted SSE data events: role → two non-content
statuses → sole final content → stop → exactly one `[DONE]`.
