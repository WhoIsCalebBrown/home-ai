#!/usr/bin/env bash
set -euo pipefail

# Run the immutable Tools image in the isolated confirmation lane. This
# confirmation matrix needs no network dependency at all.
: "${HOME_AI_TOOLS_IMAGE:?set HOME_AI_TOOLS_IMAGE to the immutable candidate image}"
: "${HOME_AI_QA_STATE_ROOT:?set a dedicated empty QA state directory}"
: "${HOME_AI_TOOLS_TOKEN_FILE:?set a QA-only Assistant-to-Tools token file}"

test -d "$HOME_AI_QA_STATE_ROOT"
test -f "$HOME_AI_TOOLS_TOKEN_FILE"
qa_state_root=$(realpath -e -- "$HOME_AI_QA_STATE_ROOT")
case "$qa_state_root" in
  /mnt/cache/appdata/home-ai/qa/isolated-execution|/mnt/cache/appdata/home-ai/qa/isolated-execution/*) ;;
  *) echo "refusing non-dedicated isolated QA state root: $qa_state_root" >&2; exit 1 ;;
esac
test ! -e "$qa_state_root/../media-workflows.json"

docker run --rm --name home-ai-tools-isolated \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$qa_state_root:/qa-state:rw" \
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
