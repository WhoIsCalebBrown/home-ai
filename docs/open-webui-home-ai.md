# Open WebUI client integration

Open WebUI is an additive client for Home-AI. It is not an agent provider and it
does not receive Home-AI tools or backend credentials. The single model exposed
to it is `home-ai`, served by the Assistant's OpenAI-compatible facade.

```text
Open WebUI / existing frontend / future ESP32
                 |
                 v
        Home-AI OpenAI facade
                 |
        Home-AI Assistant state
                 |
       Qwen + bounded Tools calls
```

## Deployed boundary

The Assistant exposes these private, bearer-authenticated routes on `voiceai`:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/audio/transcriptions`
- `POST /v1/audio/speech`

The model is `home-ai`; requests are translated into the existing
`respond()`/session path. Open WebUI never calls Ollama, Plex, Frigate, media
backends, or Home-AI Tools directly.

`stream=true` is supported as an OpenAI-compatible SSE response. Before a
tool-backed final answer, the Assistant can emit an ordinary persisted Markdown
`**Working**` preamble with no more than four distinct safe major-stage lines.
It then emits `---`, the answer, and the server-owned rich source trace. This
is deliberately not token-level streaming. The preamble remains in the saved
Open WebUI conversation by design. The bounded speech registry removes it and
the source trace from current messages, including the pinned client's stripped
and split Read Aloud inputs. Isolated plain fragments from history after the
registry's 15-minute lifetime or a server restart lack that provenance; the
[acceptance evidence](qa/openwebui-progress-source-acceptance.md) records this
limit and the complete-footer fallback.

## Transient-status compatibility gate (BLOCKED)

The pinned Open WebUI 0.11.3 image was probed on 2026-09-19 using a disposable
provider and a normal chat-completion chunk with a namespaced
`delta.home_ai_status` object and no `delta.content`. The UI did not render
`Reading Example News…` before the final answer. It persisted only `Final probe
answer.` after reload, so this event shape is **not** a proven transient status
contract. Do not ship live progress based on it. See
`docs/qa/openwebui-status-probe.md` for the pinned digest, frames, and required
approval alternatives.

## Session mapping

The facade maps `metadata.chat_id` (or `chat_id`/the Open WebUI header when
present) to a Home-AI session. User identity is included when supplied. The
fallback for clients that omit chat identity is deterministic from the first
user turn; clients should send a stable chat ID for strict conversation
isolation.

Pending offers, confirmations, subjects, referents, and workflows remain in
Home-AI. The frontend cannot authorize a write by itself.

## Audio

- STT: OpenAI-compatible transcription requests are translated to the existing
  Faster-Whisper/Wyoming service.
- TTS: speech requests are translated to Pocket TTS. `wav` and `mp3` are
  supported; `mp3` is converted by the owned Assistant adapter for browser
  playback.
- Home-AI suppresses its normal turn TTS for OpenAI chat calls so Open WebUI
  does not receive a duplicate audio response.

Future ESP32 clients should use a versioned owned voice-session protocol:

```text
POST /voice/v1/sessions
WS   /voice/v1/sessions/{session_id}
```

Client events: `session_start`, `audio_start`, `audio_chunk`, `audio_end`,
`cancel`. Server events: `listening`, `stt_partial`, `stt_final`, `thinking`,
`tool_started`, `tool_finished`, `response_text`, `tts_start`, `tts_audio`,
`tts_end`, `done`, and `error`.

The initial audio contract should be PCM16 mono at 16 kHz, chunked into short
binary frames. Wake-word detection remains on the device; the server performs
STT, reasoning, tools, and TTS. Each device gets an isolated default session,
for example `esp32:kitchen`, unless an explicit continuation is requested.

## Unraid deployment

- Official image: `ghcr.io/open-webui/open-webui:v0.11.3`
- Container: `Open-WebUI`
- Network: `voiceai`
- Persistent data: `/mnt/cache/appdata/home-ai/open-webui`
- LAN host port: `13000`
- HTTPS route: `https://assistant.calebs.online` through the existing Nginx Proxy Manager
- Home-AI gateway key: `/mnt/cache/appdata/home-ai/secrets/openai-compat.key`
- Open WebUI encryption key: `/mnt/cache/appdata/home-ai/open-webui/.webui-secret`
- Template: `deployment/Open-WebUI.xml`

The server-side Assistant key is read from a private mounted file. No key is
logged or included in Qwen context. The Open WebUI container receives only the
same private bearer key needed to call the facade; it does not receive any
backend service secrets.

The existing `assistant.calebs.online` proxy host was intentionally repointed
from the retired Assistant frontend at port `18088` to Open WebUI at port
`13000`. The Nginx Proxy Manager database was backed up before this change.

## Current qualification status

Validated directly:

- authenticated model discovery
- non-streaming read-only chat
- OpenAI-shaped single-boundary SSE
- Faster-Whisper transcription
- Pocket-TTS MP3 speech output
- Open WebUI health and private-network reachability to Home-AI
- existing Assistant and Tools remain separate and healthy

Not yet claimed as complete:

- browser microphone/playback through a logged-in Open WebUI account
- physical ESP32 hardware
- public reverse-proxy exposure
- token-level streaming

These are intentionally separate from the backend integration and do not
require changing the Home-AI brain or any third-party image.

## Progress and source acceptance

The pinned Open WebUI image remains unchanged. The repeatable disposable
acceptance procedure, including authenticated stream timestamps, browser reload
evidence, clickable-link and hostile-title checks, TTS boundary check, and
screenshot hashes, is recorded in
`docs/qa/openwebui-progress-source-acceptance.md`. It is a QA release gate;
it is not authorization to change this container, its production provider, or
Home Assistant configuration.
