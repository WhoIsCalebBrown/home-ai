#!/usr/bin/env bash
set -euo pipefail

# Execute the checked-in harness inside the live Assistant image without
# copying files into or modifying the container.  The default scenario is
# strictly read-only.  Do not add write-capable scenarios to this entrypoint.
host=${HOME_AI_HOST:-unraid}
container=${HOME_AI_ASSISTANT_CONTAINER:-Home-AI-Assistant}
if [[ $# -eq 0 ]]; then
  set -- --scenario containers
elif [[ "$1" != --* ]]; then
  set -- --scenario "$@"
fi

printf '%q ' "$@" >/dev/null
if [[ -z "${HOME_AI_AUDIO_CLIENT_ID:-}" ]]; then
  client_id="qa-audio-e2e-$(date -u +%s%N)"
else
  client_id="$HOME_AI_AUDIO_CLIENT_ID"
fi
ssh "$host" "docker exec -i '$container' python3 - $* --client-id '$client_id'" < "$(dirname "$0")/voice_audio_e2e.py"
