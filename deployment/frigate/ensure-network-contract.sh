#!/bin/sh
set -eu

mode=${1:---check}
network=home-ai-frigate
allowed='Home-AI-Tools frigate'

case "$mode" in
  --check|--apply) ;;
  *) echo "usage: $0 [--check|--apply]" >&2; exit 2 ;;
esac

if ! docker network inspect "$network" >/dev/null 2>&1; then
  if [ "$mode" = --check ]; then
    echo "$network is missing" >&2
    exit 1
  fi
  docker network create --driver bridge \
    --label home-ai.scope=frigate-integrations "$network" >/dev/null
fi

for container in $allowed; do
  docker inspect "$container" >/dev/null
  if ! docker inspect -f '{{json .NetworkSettings.Networks}}' "$container" |
      jq -e --arg network "$network" 'has($network)' >/dev/null; then
    if [ "$mode" = --check ]; then
      echo "$container is not attached to $network" >&2
      exit 1
    fi
    docker network connect "$network" "$container"
  fi
done

members=$(docker network inspect "$network" |
  jq -r '.[0].Containers // {} | to_entries[].value.Name' | sort)
expected=$(printf '%s\n' $allowed | sort)
if [ "$members" != "$expected" ]; then
  echo "$network has unexpected or missing members" >&2
  exit 1
fi

frigate_url=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' Home-AI-Tools |
  sed -n 's/^FRIGATE_URL=//p')
if [ "$frigate_url" != http://frigate:5000 ]; then
  echo "Home-AI-Tools FRIGATE_URL does not use the contained endpoint" >&2
  exit 1
fi

if docker inspect -f '{{json .HostConfig.PortBindings}}' frigate |
    jq -e 'has("5000/tcp") or has("8554/tcp") or has("8555/tcp") or has("8555/udp")' >/dev/null; then
  echo "Frigate has a prohibited internal/restream host publication" >&2
  exit 1
fi

echo "Frigate network contract is satisfied"
