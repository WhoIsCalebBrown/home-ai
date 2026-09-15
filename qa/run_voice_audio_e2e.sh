#!/usr/bin/env bash
set -euo pipefail

# Execute the checked-in harness inside the live Assistant image without
# copying files into or modifying the container.  The default scenario is
# strictly read-only.  Do not add write-capable scenarios to this entrypoint.
host=${HOME_AI_HOST:-unraid}
container=${HOME_AI_ASSISTANT_CONTAINER:-Home-AI-Assistant}
scenario=${1:-containers}

ssh "$host" "docker exec -i '$container' python3 - --scenario '$scenario'" < "$(dirname "$0")/voice_audio_e2e.py"
