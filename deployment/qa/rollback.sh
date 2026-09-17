#!/bin/sh
set -eu

command -v docker >/dev/null 2>&1 || { echo 'docker is required' >&2; exit 1; }

# Explicit names only: production containers, images, and data are untouched.
for name in Open-WebUI-QA Home-AI-QA-Assistant Home-AI-QA-Tools; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    docker rm --force "$name" >/dev/null
  fi
done
if docker network inspect voiceai-qa >/dev/null 2>&1; then
  docker network rm voiceai-qa >/dev/null 2>&1 || true
fi
echo 'Removed only QA containers and the voiceai-qa network.'
