#!/bin/sh
set -eu

# Deliberately requires explicit configuration. This script is not run by CI
# and never creates or derives credentials.
qa_root=${QA_ROOT:-/mnt/cache/appdata/home-ai/qa}
qa_token=${QA_TOOLS_TOKEN_FILE:-$qa_root/secrets/tools-service.token}
qa_openai_key=${QA_OPENAI_KEY_FILE:-$qa_root/secrets/openai-compat.key}
qa_webui_secret=${QA_WEBUI_SECRET_FILE:-$qa_root/secrets/webui.secret}
qa_image_tag=${QA_IMAGE_TAG:-stable}

fail() { echo "QA stack refused: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "run from a trusted Unraid root shell"
command -v docker >/dev/null 2>&1 || fail "docker is required"

for d in "$qa_root/secrets" "$qa_root/tools-state" "$qa_root/assistant-state" "$qa_root/open-webui"; do
  [ -d "$d" ] || fail "missing dedicated QA directory: $d"
done
for f in "$qa_token" "$qa_openai_key" "$qa_webui_secret"; do
  [ -f "$f" ] || fail "missing dedicated QA secret file: $f"
  [ "$(stat -c '%a' "$f")" = 600 ] || fail "QA secret must be mode 0600: $f"
done

# This stack must not accidentally reach the production Tools container.
# Keep the QA network distinct. It is not marked internal because the operator
# may explicitly allow inference/read-only dependencies; containers are still
# attached only to this network by these templates.
docker network inspect voiceai-qa >/dev/null 2>&1 || docker network create voiceai-qa >/dev/null
docker network inspect voiceai-qa >/dev/null 2>&1 || fail "QA network unavailable"
qa_members=$(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' voiceai-qa)
printf '%s\n' "$qa_members" | grep -Eq '(^| )server-tools( |$)' && fail "production Tools is attached to voiceai-qa"

# Templates are intentionally applied by an operator through Unraid's UI.
# Refuse to auto-deploy: this script only validates the safe prerequisites.
echo "Validated QA prerequisites for image tag $qa_image_tag; review and apply the three QA templates manually."

if [ -n "${QA_ADMIN_TOKEN:-}" ] || [ -n "${QA_USER_EMAIL:-}" ] || [ -n "${QA_USER_PASSWORD:-}" ]; then
  [ -n "${QA_ADMIN_TOKEN:-}" ] && [ -n "${QA_USER_EMAIL:-}" ] && [ -n "${QA_USER_PASSWORD:-}" ] || fail "set all QA user provisioning variables or none"
  [ -n "${QA_WEBUI_URL:-}" ] || fail "QA_WEBUI_URL required for user provisioning"
  curl --fail --silent --show-error --request POST "$QA_WEBUI_URL/api/v1/auths/add" \
    --header "Authorization: Bearer $QA_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data '{"email":"'"$QA_USER_EMAIL"'","password":"'"$QA_USER_PASSWORD"'","name":"Home-AI QA","role":"user"}' >/dev/null \
    || fail "Open WebUI non-admin QA user provisioning failed"
  echo "Provisioned the requested non-admin QA user."
fi
