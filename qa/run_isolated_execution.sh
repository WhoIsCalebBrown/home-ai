#!/usr/bin/env bash
set -euo pipefail

# Run the immutable Tools image in the isolated confirmation lane.  The QA
# network must contain only explicitly provisioned fake/read dependencies; it
# is intentionally not the production voiceai network.
: "${HOME_AI_TOOLS_IMAGE:?set HOME_AI_TOOLS_IMAGE to the immutable candidate image}"
: "${HOME_AI_QA_STATE_ROOT:?set a dedicated empty QA state directory}"
: "${HOME_AI_TOOLS_TOKEN_FILE:?set a QA-only Assistant-to-Tools token file}"

test -d "$HOME_AI_QA_STATE_ROOT"
test -f "$HOME_AI_TOOLS_TOKEN_FILE"
test ! -e "$HOME_AI_QA_STATE_ROOT/../media-workflows.json"

if ! docker network inspect home-ai-qa >/dev/null 2>&1; then
  docker network create --internal home-ai-qa >/dev/null
fi

docker run --rm --name home-ai-tools-isolated \
  --network home-ai-qa \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$HOME_AI_QA_STATE_ROOT:/qa-state:rw" \
  -v "$HOME_AI_TOOLS_TOKEN_FILE:/run/secrets/home-ai-tools-token:ro" \
  -e HOME_AI_QA_MODE=isolated_execution \
  -e HOME_AI_QA_EXECUTOR=fake \
  -e HOME_AI_QA_STATE_ROOT=/qa-state \
  -e TOOLS_SERVICE_TOKEN_FILE=/run/secrets/home-ai-tools-token \
  -e AUDIT_LOG=/qa-state/audit.jsonl \
  -e MEDIA_WORKFLOWS_PATH=/qa-state/media-workflows.json \
  -e LISTS_PATH=/qa-state/home-ai-lists.json \
  -e USER_PROFILE_PATH=/qa-state/user-profile.json \
  -e CLIDEBRID_BASE=http://127.0.0.1:9/forbidden \
  -e CLIDEBRID_BRIDGE_TOKEN= \
  -e CLIDEBRID_BRIDGE_TOKEN_FILE= \
  "$HOME_AI_TOOLS_IMAGE"
