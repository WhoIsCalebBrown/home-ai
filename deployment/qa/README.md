# Home-AI restricted live-read-only QA stack

This directory is a versioned deployment bundle for a parallel acceptance
stack. It is intentionally separate from the production containers and data.
The templates are not applied automatically.

The stack is fail-closed:

- QA Tools runs with `HOME_AI_QA_MODE=live_readonly`.
- QA Assistant talks only to `home-ai-qa-tools:8090` and exposes model ID
  `home-ai-qa`.
- QA Open WebUI has its own persistent data directory and provider URL.
- QA Tools must not receive production media-write credentials. The Tools
  image also enforces the read-only mode server-side; prompt text and client
  `dry_run` flags are not security boundaries.
- Every secret path must exist before starting a container. Do not substitute
  production secret paths.

The target Docker network is `voiceai-qa`. Create it only after reviewing the
templates, and attach only explicitly approved read-only dependencies.

## Provisioning

Run `provision.sh` from a trusted Unraid shell after setting the documented
environment variables. It validates directories, secret files, image digests,
and network isolation before it invokes any Docker command. It does not create
users unless `QA_ADMIN_TOKEN`, `QA_USER_EMAIL`, and `QA_USER_PASSWORD` are all
provided. The user is created through Open WebUI's supported admin endpoint and
is not granted admin status.

`rollback.sh` removes only the three QA containers and the `voiceai-qa`
network. It never touches production containers, images, or data.

These templates deliberately contain no secret values.
