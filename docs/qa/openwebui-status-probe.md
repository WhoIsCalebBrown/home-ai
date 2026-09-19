# Open WebUI transient-status compatibility probe

Date: 2026-09-19

## Decision: BLOCKED

Pinned Open WebUI 0.11.3 does not render a namespaced non-content OpenAI SSE
delta as transient status. The candidate event was:

```json
{"choices":[{"index":0,"delta":{"home_ai_status":{"phase":"tool_started","label":"Reading Example News…"}},"finish_reason":null}]}
```

The second candidate was the same shape with `phase: "tool_finished"` and
`label: "Read Example News"`. Neither has `delta.content`.

## Reproduction

The disposable provider is `qa/openwebui_status_probe.py`. It advertises the
synthetic `home-ai-probe` model at `GET /v1/models` and emits this exact
sequence at `POST /v1/chat/completions`:

1. assistant role chunk;
2. `tool_started` status (`Reading Example News…`);
3. `tool_finished` status (`Read Example News`);
4. delayed `Final probe answer.` content chunk;
5. stop chunk;
6. one `data: [DONE]` marker.

The manual disposable setup was:

```bash
docker network create home-ai-status-probe-20260919
docker run -d --rm --name home-ai-status-provider \
  --network home-ai-status-probe-20260919 --network-alias status-probe \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --mount type=bind,src="$PWD/qa",dst=/workspace/qa,readonly \
  --workdir /workspace/qa home-ai-assistant-sdd \
  python -m uvicorn openwebui_status_probe:app --host 0.0.0.0 --port 8000
docker run -d --rm --name home-ai-status-webui \
  --network home-ai-status-probe-20260919 -p 127.0.0.1:18093:8080 \
  -e OPENAI_API_BASE_URLS='http://status-probe:8000/v1' \
  -e OPENAI_API_KEYS='probe-key' -e ENABLE_SIGNUP=true \
  -v "$(mktemp -d /tmp/openwebui-status-probe.XXXXXX):/app/backend/data" \
  ghcr.io/open-webui/open-webui@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
```

The pinned image digest was:

```text
ghcr.io/open-webui/open-webui@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
```

The Open WebUI container was isolated on that network and bound only to
`127.0.0.1:18093`; production Open WebUI and its `voiceai` network were not
changed. A disposable Chromium browser on the same isolated network created a
disposable admin user, selected `home-ai-probe`, and sent `Run status probe
again`.

## Observations

Browser text snapshots from the UI request were:

```text
T+400ms:  Reading Example News… = false; Final probe answer. = false
T+1600ms: Reading Example News… = false; Final probe answer. = false
T+3200ms: Reading Example News… = false; Final probe answer. = true
reload:   Reading Example News… = false; Final probe answer. = true
```

Therefore:

1. `Reading Example News…` did **not** appear before the final answer.
2. No status appeared either before completion or after reload; it was not
   rendered transiently.
3. `Final probe answer.` was the only persisted assistant content after reload.

Directly reading the provider stream independently confirmed the emitted
status frames followed by the final content, stop chunk, and exactly one
`[DONE]`. The wire-format tests also assert that only the final frame contains
content and that `[DONE]` occurs exactly once.

## Required next decision

Do not begin product streaming/progress work from this event shape. Present
these choices for approval; do not choose automatically:

- Maintain a minimal Open WebUI extension that renders `home_ai_status` transiently.
- Persist compact progress lines in assistant content and accept that they remain in conversation history.

## QA command note

The brief's literal Assistant-image command is not runnable in the current
repository: `assistant/Dockerfile` copies `assistant/...` paths but is invoked
with `assistant` as build context; when built from the repository root instead,
the image has no `pytest`; and `--read-only` supplies no temporary directory.
The wire tests were run in the existing disposable QA image with a read-only
root plus tmpfs:

```bash
docker build -t home-ai-status-probe-qa -f qa/Dockerfile qa
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$PWD/qa:/workspace/qa:ro" home-ai-status-probe-qa \
  python -m pytest -q -p no:cacheprovider /workspace/qa/openwebui_status_probe.py
```

That run passed `3 passed`. This packaging issue is separate from—and does not
change—the BLOCKED product-compatibility decision.
