"""Contract tests for truthful Tools failure and malformed-result semantics."""

import asyncio
import importlib.util
from pathlib import Path

import httpx


class _Request:
    def __init__(self, token: str):
        self.headers = {"X-Home-AI-Tools-Token": token}


def _invoke(module, request):
    module.TOOLS_SERVICE_TOKEN_FILE = ""
    module.TOOLS_SERVICE_TOKEN = "qa-tools-token"
    return module.invoke(_Request("qa-tools-token"), request)


def _load_tools():
    path = Path(__file__).parents[1] / "tools" / "server-tools-app.py"
    spec = importlib.util.spec_from_file_location("home_ai_tools_failure_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _install(module, fn):
    module.TOOLS["qa_failure_probe"] = ("qa_failure_probe", "isolated", "read", "qa", {}, fn)


def test_timeout_is_structured_and_contains_no_evidence(tmp_path):
    module = _load_tools()
    module.AUDIT = tmp_path / "audit.jsonl"

    async def slow(_):
        await asyncio.sleep(13)

    _install(module, slow)
    result = asyncio.run(_invoke(module, module.Invoke(name="qa_failure_probe")))
    assert result["status"] == "timeout"
    assert result["result"]["error_code"] == "TIMEOUT"
    assert result["result"]["evidence_available"] is False


def test_http_500_is_backend_unavailable_not_success(tmp_path):
    module = _load_tools()
    module.AUDIT = tmp_path / "audit.jsonl"

    async def failing(_):
        request = httpx.Request("GET", "http://qa.invalid")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("qa", request=request, response=response)

    _install(module, failing)
    result = asyncio.run(_invoke(module, module.Invoke(name="qa_failure_probe")))
    assert result["status"] == "unavailable"
    assert result["result"]["error_code"] == "BACKEND_UNAVAILABLE"
    assert result["result"]["http_status"] == 500
    assert result["result"]["evidence_available"] is False


def test_malformed_success_payload_is_invalid_tool_result(tmp_path):
    module = _load_tools()
    module.AUDIT = tmp_path / "audit.jsonl"

    async def malformed(_):
        return ["not", "a", "mapping"]

    _install(module, malformed)
    result = asyncio.run(_invoke(module, module.Invoke(name="qa_failure_probe")))
    assert result["status"] == "error"
    assert result["result"]["error_code"] == "INVALID_TOOL_RESULT"
    assert result["result"]["evidence_available"] is False
