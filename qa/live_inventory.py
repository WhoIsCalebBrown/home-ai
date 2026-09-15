"""Collect a sanitized, read-only Docker inventory from an Unraid host."""

import argparse
import json
import shlex
import subprocess


def remote(host: str, command: str) -> str:
    result = subprocess.run(["ssh", host, command], text=True, capture_output=True, check=True)
    return result.stdout


def image_details(host: str, image_ref: str) -> dict:
    raw = remote(host, f"docker image inspect {shlex.quote(image_ref)}")
    item = json.loads(raw)[0]
    return {
        "image_id": item.get("Id"),
        "repo_digests": item.get("RepoDigests") or [],
        "created": item.get("Created"),
        "architecture": item.get("Architecture"),
        "os": item.get("Os"),
        "labels": item.get("Config", {}).get("Labels") or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="unraid")
    args = parser.parse_args()
    names = remote(args.host, "docker ps -a --format '{{.Names}}'").splitlines()
    inventory = {"host": args.host, "containers": {}}
    image_cache = {}
    for name in sorted(filter(None, names)):
        raw = remote(args.host, f"docker inspect {name}")
        item = json.loads(raw)[0]
        image_ref = item.get("Config", {}).get("Image")
        if image_ref not in image_cache:
            image_cache[image_ref] = image_details(args.host, image_ref)
        networks = item.get("NetworkSettings", {}).get("Networks", {})
        state = item.get("State", {})
        host_config = item.get("HostConfig", {})
        config = item.get("Config", {})
        inventory["containers"][name] = {
            "image": image_ref,
            "image_details": image_cache[image_ref],
            "status": state.get("Status"),
            "health": (state.get("Health") or {}).get("Status"),
            "started_at": state.get("StartedAt"),
            "restart_policy": host_config.get("RestartPolicy", {}).get("Name"),
            "network_mode": host_config.get("NetworkMode"),
            "networks": {
                network: {"ip": data.get("IPAddress"), "aliases": data.get("Aliases", []), "dns_names": data.get("DNSNames", [])}
                for network, data in networks.items()
            },
            "ports": item.get("NetworkSettings", {}).get("Ports", {}),
            "mounts": [{"source": m.get("Source"), "destination": m.get("Destination"), "mode": m.get("Mode"), "rw": m.get("RW")} for m in item.get("Mounts", [])],
            "entrypoint": config.get("Entrypoint"),
            "command": config.get("Cmd"),
            "container_labels": config.get("Labels") or {},
            "env_keys": sorted((entry.split("=", 1)[0] for entry in config.get("Env", []))),
        }
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
