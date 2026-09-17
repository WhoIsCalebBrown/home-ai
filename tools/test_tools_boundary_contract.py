"""P0 contract tests for the Assistant -> Tools trust boundary.

These call the FastAPI handlers directly so no listener, Docker socket, or
real backend is involved.  The only mocked boundary is the registered tool
function itself.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException


spec = importlib.util.spec_from_file_location("server_tools_boundary_app", Path(__file__).with_name("server-tools-app.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Request:
    def __init__(self, token: str | None = None):
        self.headers = ({module.TOOLS_SERVICE_TOKEN_HEADER: token} if token else {})


def _configure_token(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "TOOLS_SERVICE_TOKEN", "test-tools-secret")
    monkeypatch.setattr(module, "TOOLS_SERVICE_TOKEN_FILE", "")
    monkeypatch.setattr(module, "AUDIT", tmp_path / "audit.jsonl")


def test_discovery_and_invocation_require_the_private_service_token(monkeypatch, tmp_path):
    _configure_token(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as missing:
        asyncio.run(module.discover(Request(), "cache"))
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        asyncio.run(module.invoke(Request("wrong"), module.Invoke(name="get_gpu_status")))
    assert wrong.value.status_code == 401

    result = asyncio.run(module.discover(Request("test-tools-secret"), "cache"))
    assert result["contract_version"] == module.TOOL_CONTRACT_VERSION


def test_health_is_minimal_without_auth_and_detailed_with_auth(monkeypatch, tmp_path):
    _configure_token(monkeypatch, tmp_path)
    anonymous = asyncio.run(module.health(Request()))
    assert anonymous == {"ok": True, "service": "server-tools"}
    authenticated = asyncio.run(module.health(Request("test-tools-secret")))
    assert authenticated["tools"] == len(module.REGISTRY)
    assert authenticated["contract_version"] == module.TOOL_CONTRACT_VERSION


def test_missing_required_container_argument_is_operation_failure_not_ok(monkeypatch, tmp_path):
    _configure_token(monkeypatch, tmp_path)
    response = asyncio.run(module.invoke(
        Request("test-tools-secret"),
        module.Invoke(name="unraid_container_status", trace_id="trace-1", turn_id="turn-1", tool_call_id="call-1"),
    ))
    assert response["transport_ok"] is True
    assert response["operation_ok"] is False
    assert response["status"] == "invalid_arguments"
    assert response["error"]["code"] == "INVALID_ARGUMENTS"
    assert response["trace_id"] == "trace-1"
    assert response["turn_id"] == "turn-1"
    assert response["tool_call_id"] == "call-1"


def test_gpu_structured_failure_is_unavailable_not_success(monkeypatch, tmp_path):
    _configure_token(monkeypatch, tmp_path)

    async def unavailable_gpu(_arguments):
        return {"error": "GPU telemetry unavailable", "error_code": "GPU_TELEMETRY_UNAVAILABLE",
                "detail": "FileNotFoundError", "evidence_available": False}

    original = module.TOOLS["get_gpu_status"]
    monkeypatch.setitem(module.TOOLS, "get_gpu_status", (*original[:-1], unavailable_gpu))
    response = asyncio.run(module.invoke(Request("test-tools-secret"), module.Invoke(name="get_gpu_status")))
    assert response["transport_ok"] is True
    assert response["operation_ok"] is False
    assert response["status"] == "unavailable"
    assert response["error"]["code"] == "GPU_TELEMETRY_UNAVAILABLE"
    assert response["result"]["evidence_available"] is False


@pytest.mark.parametrize("reported_status", ["rejected", "disabled", "unavailable", "failed_ingestion"])
def test_negative_adapter_status_is_never_wrapped_as_operation_success(monkeypatch, tmp_path, reported_status):
    _configure_token(monkeypatch, tmp_path)

    async def negative_result(_arguments):
        return {"status": reported_status, "reason": "TEST_NEGATIVE_RESULT", "write_executed": False}

    original = module.TOOLS["get_gpu_status"]
    monkeypatch.setitem(module.TOOLS, "get_gpu_status", (*original[:-1], negative_result))
    response = asyncio.run(module.invoke(Request("test-tools-secret"), module.Invoke(name="get_gpu_status")))
    assert response["transport_ok"] is True
    assert response["operation_ok"] is False
    assert response["status"] == reported_status
    assert response["error"]["code"] == "TEST_NEGATIVE_RESULT"
