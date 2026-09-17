# P0 acceptance-closure evidence — 2026-09-17

This is a sanitized incident and acceptance record. It deliberately contains
no bearer tokens, API keys, passwords, authorization headers, or sensitive
tool payloads.

## Historical unintended production submission

- Classification: `TEST_ENVIRONMENT_NOT_ISOLATED`
- Open WebUI chat: `P0 final confirmation isolation A`
- Frontend chat ID: `ad12a2c1-24ba-4419-a54d-cabce5cf41ea`
- Home-AI session: `openwebui:<redacted-user-id>:ad12a2c1-24ba-4419-a54d-cabce5cf41ea`
- Initial utterance: `Get Anatomy of a Fall (2023).`
- Presented subject: Anatomy of a Fall (2023), TMDB 915935
- Approval utterance: `Yes.`
- Approval source: automated live QA harness operated by Codex
- Workflow ID: `71fd7f6c-0cff-4f83-9845-2f67397ecb81`
- Confirmation/action ID: `1d20a3b5-972a-4169-a3c9-8eef535b5c99`
- Executor: production `media_standard_request` to the private cli_debrid
  bridge
- Outcome: one attempted and successful production submission; cli_debrid
  item 80797 and Torbox torrent 99158989 were observed in the retained audit
  trail

The canonical identity and Home-AI session did not change between planning and
approval. Normal session, confirmation, plan-hash, argument-hash, expiry, and
single-use checks passed. A companion chat had a separate session and
confirmation and did not produce a second submission. The failure was using a
production-capable live acceptance environment for an approval test, not a
cross-chat authorization bypass.

Authoritative retained evidence remains on Unraid in:

- `/mnt/cache/appdata/voice-tools/audit.jsonl`
- `/mnt/cache/appdata/voice-tools/media-workflows.json`
- `/mnt/cache/appdata/voice-tools/workflow-events.sqlite3`
- `/mnt/cache/appdata/cli_debrid/logs/primary_app_err.log`
- `/mnt/cache/appdata/home-ai/open-webui/webui.db`

No automated cleanup, cancellation, retry, or replacement was performed.

## Credential rotation closure

The former OpenAI-compatible key was captured by an overly broad diagnostic
environment inspection. The key was rotated immediately. Verification is
performed by comparing and exercising values in memory; neither the former nor
replacement value is printed.

- Former key: rejected by Home-AI with HTTP 401.
- Replacement key: accepted by Home-AI with HTTP 200.
- Production Open WebUI model provider: operational with the replacement.
- Open WebUI chat, STT, and TTS persistent settings and active Unraid template:
  updated to the replacement.
- Assistant diagnostic/QA logs: no occurrence of either credential.
- The retired value remains only in retained incident-era artifacts, including
  the preserved rollback configuration and historical SQLite pages. Access is
  limited to the server-side appdata/rollback scope; those artifacts were not
  erased because they are rollback and incident evidence.

Future verification must use the bounded credential verifier and QA harness;
it must not print container environments or provider configuration values.

