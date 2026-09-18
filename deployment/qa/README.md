# Home-AI restricted live-read-only QA stack

This directory is a versioned deployment bundle for a parallel acceptance
stack. It is intentionally separate from the production containers and data.
The templates are not applied automatically.

The stack is fail-closed:

- QA Tools runs with `HOME_AI_QA_MODE=live_readonly`.
- QA Tools sets `HOME_AI_CAMERA_READS_ENABLED=false`; it cannot discover or
  directly invoke Frigate event, metadata, snapshot, clip, or live-camera
  capabilities. The application refuses QA startup if that privacy setting is
  absent or enabled.
- QA Assistant talks only to `home-ai-qa-tools:8090` and exposes model ID
  `home-ai-qa`.
- QA Open WebUI has its own persistent data directory and provider URL.
- QA Tools must not receive acquisition-executor, Home Assistant, or host-
  administration credentials. The Tools image enforces the read-only mode
  server-side; prompt text and client `dry_run` flags are not security
  boundaries. Plex/Arr do not provide separate read-only API identities in
  this deployment, so live catalog acceptance mounts only their exact config
  files. Those tokens remain confined to the immutable QA Tools process and
  are never mounted into Assistant or Open WebUI.
- Every secret path must exist before starting a container. Do not substitute
  production secret paths.

The target Docker network is `voiceai-qa`. Create it only after reviewing the
templates, and attach only explicitly approved read-only dependencies.

The live-read-only lane currently permits two shared dependencies: Ollama for
inference and SearXNG for public search.  Attach them with the exact aliases
used by the templates/application defaults:

```sh
docker network connect --alias ollama --alias voice-ollama voiceai-qa Ollama
docker network connect --alias SearXNG voiceai-qa SearXNG
```

Do not attach production Tools, Docker, Home Assistant, host filesystems, or an
acquisition executor. QA Tools mounts only the exact Plex/Radarr/Sonarr/Lidarr
configuration files needed for real catalog reads and has only
`CAP_DAC_READ_SEARCH`, which is required for those root-owned files. It does
not mount `/mnt/cache`, `/mnt/user`, the appdata root, or cli_debrid secrets;
all mutation capabilities also remain denied by
`HOME_AI_QA_MODE=live_readonly`.

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
