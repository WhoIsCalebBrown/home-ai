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
  # Ollama is the only explicitly shared production dependency used by the
  # live read-only QA stack. Detach it from the QA network without restarting
  # or otherwise changing the production service.
  if docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' voiceai-qa | grep -qw Ollama; then
    docker network disconnect voiceai-qa Ollama >/dev/null
  fi
  docker network rm voiceai-qa >/dev/null
fi
echo 'Removed only QA containers and the voiceai-qa network.'
