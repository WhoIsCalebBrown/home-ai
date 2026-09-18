"""Fixture-only coverage for the sanitized infrastructure guardrails."""

from infrastructure_guardrails import audit, collect_remote, image_is_mutable


def container(name, image="example/service:1", **kwargs):
    return {"name": name, "image": image, "running": True, "ports": {}, "networks": {},
            "mounts": [], "host_config": {"restart_policy": "unless-stopped"}, **kwargs}


def ids(findings):
    return {item["id"] for item in findings}


def test_flags_camera_publication_without_fetching_media():
    frigate = container("frigate", ports={
        "5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}],
        "8554/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8554"}],
    })
    result = ids(audit({"containers": [frigate]}))
    assert "FRIGATE_WEB_ALL_INTERFACE_PUBLICATION" in result
    assert "FRIGATE_RESTREAM_HOST_PUBLICATION" in result


def test_flags_tools_privilege_and_mcp_booleans_without_reporting_values():
    tools = container("Home-AI-Tools", mounts=[
        {"source": "/mnt/cache/appdata", "destination": "/appdata", "rw": False},
        {"source": "/var/run/docker.sock", "destination": "/var/run/docker.sock", "rw": False},
    ])
    snapshot = {"containers": [tools], "host": {
        "docker_filesystem_percent": 91.2,
        "unraid_mcp": {"reachable": True, "api_token_configured": False, "read_only": False},
    }}
    result = ids(audit(snapshot))
    assert {"TOOLS_BROAD_APPDATA_MOUNT", "TOOLS_DOCKER_SOCKET_MOUNT", "DOCKER_FILESYSTEM_NEAR_FULL",
            "UNRAID_MCP_TOKEN_MISSING", "UNRAID_MCP_NOT_READ_ONLY"} <= result


def test_flags_qa_camera_and_production_boundary_drift():
    snapshot = {"containers": [], "camera_read_policy": {
        "qa_allowed": True, "qa_trusted_identity_required": False,
        "production_trusted_identity_required": False,
    }, "qa": {"networks": ["voiceai"], "production_networks": ["voiceai", "backend"],
              "production_mutation_endpoint_reachable": True}}
    result = ids(audit(snapshot))
    assert {"QA_CAMERA_READ_ALLOWED", "QA_CAMERA_POLICY_UNVERIFIED", "PRODUCTION_CAMERA_POLICY_UNVERIFIED",
            "QA_PRODUCTION_NETWORK_SHARED", "QA_PRODUCTION_MUTATION_REACHABLE"} <= result


def test_policy_detects_alias_and_port_drift():
    tools = container("Home-AI-Tools", ports={"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8123"}]},
                      networks={"voiceai": {"aliases": ["tools"], "dns_names": ["tools"]}})
    result = ids(audit({"containers": [tools]}, {"containers": {
        "Home-AI-Tools": {"published_ports": [], "network_aliases": {"voiceai": ["server-tools"]}},
    }}))
    assert {"UNEXPECTED_HOST_PORT", "NETWORK_ALIAS_DRIFT"} <= result


def test_rollback_and_mutable_image_checks():
    rollback = container("home-ai-rollback", image="registry.local/home-ai:qualified", running=False,
                         host_config={"restart_policy": "always"})
    result = ids(audit({"containers": [rollback]}))
    assert "ROLLBACK_INSTANCE_ACTIVE_OR_AUTOSTART" in result
    assert "MUTABLE_IMAGE_REFERENCE" in result
    assert image_is_mutable("registry.local/home-ai:qualified")
    assert not image_is_mutable("registry.local/home-ai@sha256:" + "a" * 64)


def test_remote_collector_projection_does_not_request_environment_or_commands(monkeypatch):
    captured = {}

    class Result:
        stdout = "[]"

    def fake_run(command, **kwargs):
        captured["command"] = command
        return Result()

    monkeypatch.setattr("infrastructure_guardrails.subprocess.run", fake_run)
    assert collect_remote("unraid") == {"containers": [], "host": {}, "qa": {}, "camera_read_policy": {}}
    remote_command = captured["command"][-1]
    assert "Config.Env" not in remote_command
    assert ".Args" not in remote_command
    assert ".Path" not in remote_command
