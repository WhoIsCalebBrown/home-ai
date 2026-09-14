#!/usr/bin/env bash
set -euo pipefail

# Safe local retention for Home-AI GHCR images.
# Default is a dry run. Use --apply only after a successful deployment.
# This never touches volumes, containers, non-Home-AI images, or untagged
# images outside the two explicitly named repositories.

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
elif [[ "${1:-}" != "" ]]; then
  echo "usage: $0 [--apply]" >&2
  exit 2
fi

repos=(
  "ghcr.io/whoiscalebbrown/home-ai-assistant"
  "ghcr.io/whoiscalebbrown/home-ai-tools"
)

# Keep the rolling deployment, the previous known-good SHA, and the explicitly
# named rollback image. Add a newer rollback tag here before retiring an older
# one. Container references are also protected dynamically below.
protected=(
  "home-ai-assistant:stable"
  "home-ai-assistant:sha-e314e27"
  "home-ai-assistant:rollback-e3f53e3"
  "home-ai-tools:stable"
  "home-ai-tools:sha-e314e27"
)

is_protected() {
  local short="$1"
  for keep in "${protected[@]}"; do
    [[ "$short" == "$keep" ]] && return 0
  done
  return 1
}

for repo in "${repos[@]}"; do
  docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' "$repo" |
    while read -r ref image_id; do
      [[ -n "$ref" ]] || continue
      tag="${ref##*:}"
      [[ "$tag" == sha-* || "$tag" == rollback-* ]] || continue

      short="${repo##*/}:$tag"
      is_protected "$short" && continue

      refs="$(docker ps -a --no-trunc --filter "ancestor=$image_id" --format '{{.Names}}' | paste -sd, -)"
      if [[ -n "$refs" ]]; then
        echo "KEEP $ref ($image_id): referenced by $refs"
        continue
      fi

      if (( APPLY )); then
        echo "REMOVE $ref ($image_id): unreferenced superseded Home-AI revision"
        docker image rm "$ref"
      else
        echo "WOULD REMOVE $ref ($image_id): unreferenced superseded Home-AI revision"
      fi
    done
done

if (( APPLY )); then
  echo "Home-AI image retention complete; protected tags were not modified."
else
  echo "Dry run only; rerun with --apply to remove listed unreferenced revisions."
fi
