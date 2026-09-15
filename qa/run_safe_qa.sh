#!/usr/bin/env bash
set -euo pipefail

# Safe QA lane: source is mounted read-only and all state-changing calls are
# terminated in fake adapters.  This script never contacts production APIs.
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
image_name=${HOME_AI_QA_IMAGE:-home-ai-hardening-test:py312}

docker build -f "$repo_dir/qa/Dockerfile" -t "$image_name" "$repo_dir" >/dev/null
docker run --rm --network none \
  -v "$repo_dir:/repo:ro" -w /repo "$image_name" \
  pytest -p no:cacheprovider -q assistant/test_grounding_regressions.py tools/test_capability_discovery.py qa
