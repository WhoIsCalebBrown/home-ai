"""Collect a sanitized, read-only Docker inventory from an Unraid host."""

import argparse
import json
import subprocess


def remote(host: str, command: str) -> str:
    result = subprocess.run(["ssh", host, command], text=True, capture_output=True, check=True)
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="unraid")
    args = parser.parse_args()
    names = remote(args.host, "docker ps -a --format '{{.Names}}'").splitlines()
    inventory = {"host": args.host, "containers": {}}
    for name in sorted(filter(None, names)):
        raw = remote(args.host, f"docker inspect {name}")
        item = json.loads(raw)[0]
        networks = item.get("NetworkSettings", {}).get("Networks", {})
        inventory["containers"][name] = {
            "image": item.get("Config", {}).get("Image"),
            "status": item.get("State", {}).get("Status"),
            "health": (item.get("State", {}).get("Health") or {}).get("Status"),
            "started_at": item.get("State", {}).get("StartedAt"),
            "network_mode": item.get("HostConfig", {}).get("NetworkMode"),
            "networks": {
                network: {"ip": data.get("IPAddress"), "aliases": data.get("Aliases", [])}
                for network, data in networks.items()
            },
            "ports": item.get("NetworkSettings", {}).get("Ports", {}),
            "mounts": [{"source": m.get("Source"), "destination": m.get("Destination"), "mode": m.get("Mode"), "rw": m.get("RW")} for m in item.get("Mounts", [])],
            "entrypoint": item.get("Config", {}).get("Entrypoint"),
            "command": item.get("Config", {}).get("Cmd"),
        }
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
