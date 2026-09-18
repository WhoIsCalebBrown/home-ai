"""Read-only infrastructure-security guardrails for Unraid Docker metadata.

The collector deliberately requests a narrow Docker-inspect projection.  It
never reads or prints environment variables, command lines, credential values,
or HTTP response bodies.  The resulting snapshot can be retained as a
sanitized audit artifact and compared with an explicitly reviewed policy file.

This is an audit signal, not a firewall or an authorization mechanism.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ALL_INTERFACES = {"", "0.0.0.0", "::", "[::]"}
NEAR_FULL_PERCENT = 85.0


def finding(identifier: str, severity: str, subject: str, evidence: str) -> dict[str, str]:
    return {"id": identifier, "severity": severity, "subject": subject, "evidence": evidence}


def ports_for(container: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for port, bindings in (container.get("ports") or {}).items():
        container_port = str(port).split("/")[0]
        for binding in bindings or []:
            result.append({
                "container_port": container_port,
                "host_ip": str(binding.get("HostIp") or ""),
                "host_port": str(binding.get("HostPort") or ""),
            })
    return result


def is_all_interfaces(binding: dict[str, str]) -> bool:
    return binding["host_ip"] in ALL_INTERFACES


def image_is_mutable(image: str) -> bool:
    """A tag without a content digest can be moved by its registry."""
    return bool(image) and "@sha256:" not in image


def container_networks(container: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for name, details in (container.get("networks") or {}).items():
        result[name] = set(details.get("aliases") or []) | set(details.get("dns_names") or [])
    return result


def audit(snapshot: dict[str, Any], policy: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return deterministic findings from an already-sanitized snapshot."""
    policy = policy or {}
    findings: list[dict[str, str]] = []
    containers = snapshot.get("containers") or []
    by_name = {str(item.get("name")): item for item in containers}

    for item in containers:
        name = str(item.get("name") or "<unnamed>")
        image = str(item.get("image") or "")
        if image_is_mutable(image):
            findings.append(finding("MUTABLE_IMAGE_REFERENCE", "medium", name,
                                    "image is tag-only; record a repository digest before an update"))
        restart = str((item.get("host_config") or {}).get("restart_policy") or "")
        if "rollback" in name.lower() and (item.get("running") or restart.lower() not in {"", "no", "none"}):
            findings.append(finding("ROLLBACK_INSTANCE_ACTIVE_OR_AUTOSTART", "high", name,
                                    "rollback-labelled container is running or may restart automatically"))

        if name.lower() == "frigate" or "frigate" in name.lower():
            for binding in ports_for(item):
                port = binding["container_port"]
                if port in {"5000", "6060"} and is_all_interfaces(binding):
                    findings.append(finding("FRIGATE_WEB_ALL_INTERFACE_PUBLICATION", "high", name,
                                            f"camera web/API port {port} is bound on all host interfaces"))
                if port in {"8554", "8555"}:
                    findings.append(finding("FRIGATE_RESTREAM_HOST_PUBLICATION", "high", name,
                                            f"RTSP/WebRTC-related port {port} is host-published"))

        if name == "Home-AI-Tools":
            for mount in item.get("mounts") or []:
                source = str(mount.get("source") or "")
                destination = str(mount.get("destination") or "")
                if source == "/var/run/docker.sock" or destination == "/var/run/docker.sock":
                    findings.append(finding("TOOLS_DOCKER_SOCKET_MOUNT", "high", name,
                                            "Docker API socket is mounted; read-only mount is not a read-only API"))
                if source == "/mnt/cache/appdata" or source.startswith("/mnt/cache/appdata/"):
                    findings.append(finding("TOOLS_BROAD_APPDATA_MOUNT", "high", name,
                                            "mount grants Home-AI Tools access to the broad appdata tree"))

    host = snapshot.get("host") or {}
    docker_percent = host.get("docker_filesystem_percent")
    if isinstance(docker_percent, (int, float)) and docker_percent >= NEAR_FULL_PERCENT:
        findings.append(finding("DOCKER_FILESYSTEM_NEAR_FULL", "high", "host",
                                f"Docker filesystem is {docker_percent:.1f}% used"))

    mcp = host.get("unraid_mcp") or {}
    if mcp.get("reachable") is True and mcp.get("api_token_configured") is not True:
        findings.append(finding("UNRAID_MCP_TOKEN_MISSING", "critical", "Unraid Management MCP",
                                "reachable management MCP reports no configured API token"))
    if mcp.get("reachable") is True and mcp.get("read_only") is not True:
        findings.append(finding("UNRAID_MCP_NOT_READ_ONLY", "critical", "Unraid Management MCP",
                                "reachable management MCP is not confirmed read-only"))

    camera = snapshot.get("camera_read_policy") or {}
    if camera:
        if camera.get("qa_allowed") is True:
            findings.append(finding("QA_CAMERA_READ_ALLOWED", "critical", "QA policy",
                                    "QA identity is allowed to read camera data"))
        if camera.get("qa_trusted_identity_required") is not True:
            findings.append(finding("QA_CAMERA_POLICY_UNVERIFIED", "high", "QA policy",
                                    "QA camera-read denial is not tied to a trusted server-side identity"))
        if camera.get("production_trusted_identity_required") is not True:
            findings.append(finding("PRODUCTION_CAMERA_POLICY_UNVERIFIED", "high", "production policy",
                                    "production camera authorization is not confirmed server-side"))

    qa = snapshot.get("qa") or {}
    shared_networks = sorted(set(qa.get("networks") or []) & set(qa.get("production_networks") or []))
    if shared_networks:
        findings.append(finding("QA_PRODUCTION_NETWORK_SHARED", "high", "QA network",
                                "QA and production share network(s): " + ", ".join(shared_networks)))
    if qa.get("production_mutation_endpoint_reachable") is True:
        findings.append(finding("QA_PRODUCTION_MUTATION_REACHABLE", "critical", "QA network",
                                "QA can reach a production mutation endpoint"))

    expected = policy.get("containers") or {}
    for name, requirement in expected.items():
        actual = by_name.get(name)
        if actual is None:
            findings.append(finding("EXPECTED_CONTAINER_MISSING", "high", name, "container absent from runtime snapshot"))
            continue
        current_networks = container_networks(actual)
        for network, aliases in (requirement.get("network_aliases") or {}).items():
            missing = set(aliases) - current_networks.get(network, set())
            if missing:
                findings.append(finding("NETWORK_ALIAS_DRIFT", "high", name,
                                        f"network {network} missing alias(es): {', '.join(sorted(missing))}"))
        permitted_ports = {str(value) for value in requirement.get("published_ports") or []}
        if "published_ports" in requirement:
            unexpected = sorted({p["container_port"] for p in ports_for(actual)} - permitted_ports)
            if unexpected:
                findings.append(finding("UNEXPECTED_HOST_PORT", "high", name,
                                        "unexpected host-published port(s): " + ", ".join(unexpected)))

    # IPv4/IPv6 and TCP/UDP bindings can otherwise emit the same actionable
    # finding several times. Keep the report compact without hiding distinct
    # ports or subjects.
    unique = {
        (item["severity"], item["id"], item["subject"], item["evidence"]): item
        for item in findings
    }
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        unique.values(),
        key=lambda item: (severity_order.get(item["severity"], 9), item["id"], item["subject"], item["evidence"]),
    )


def collect_remote(host: str) -> dict[str, Any]:
    """Collect a deliberately narrow inspect projection on the remote host.

    No `Config.Env`, command, entrypoint, labels, or process arguments are
    requested.  Host-level policy booleans must be supplied separately after a
    protected local review; guessing them would create false assurance.
    """
    jq = r'''docker ps -aq | xargs -r docker inspect | jq '[.[] | {
      name: (.Name | ltrimstr("/")), image: .Config.Image,
      running: (.State.Running // false),
      ports: (.NetworkSettings.Ports // {}),
      networks: ((.NetworkSettings.Networks // {}) | with_entries(.value |= {
        aliases: (.Aliases // []), dns_names: (.DNSNames // [])
      })),
      mounts: [(.Mounts // [])[] | {source: .Source, destination: .Destination, rw: .RW}],
      host_config: {privileged: (.HostConfig.Privileged // false),
        network_mode: (.HostConfig.NetworkMode // ""),
        restart_policy: (.HostConfig.RestartPolicy.Name // "")}
    }]' '''
    completed = subprocess.run(["ssh", host, jq], check=True, capture_output=True, text=True)
    return {"containers": json.loads(completed.stdout), "host": {}, "qa": {}, "camera_read_policy": {}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="sanitized JSON snapshot")
    source.add_argument("--host", help="collect narrow Docker metadata over SSH")
    parser.add_argument("--policy", type=Path, help="reviewed expected ports and aliases JSON")
    parser.add_argument("--fail-on", choices=["none", "medium", "high", "critical"], default="none")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot else collect_remote(args.host)
    policy = json.loads(args.policy.read_text(encoding="utf-8")) if args.policy else {}
    findings = audit(snapshot, policy)
    print(json.dumps({"findings": findings, "finding_count": len(findings)}, indent=2, sort_keys=True))
    levels = {"none": 99, "medium": 1, "high": 2, "critical": 3}
    return int(any(levels[item["severity"]] >= levels[args.fail_on] for item in findings))


if __name__ == "__main__":
    raise SystemExit(main())
