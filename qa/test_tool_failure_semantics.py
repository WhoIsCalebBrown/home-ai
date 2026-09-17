"""Contract tests for truthful Tools failure and malformed-result semantics."""

import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

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


def test_live_readonly_scope_denies_mutations_before_handler(monkeypatch, tmp_path):
    module = _load_tools()
    module.AUDIT = tmp_path / "audit.jsonl"
    module.QA_MODE = "live_readonly"
    called = False

    async def writer(_):
        nonlocal called
        called = True
        return {"status": "ok", "write_executed": True}

    module.TOOLS["qa_writer"] = ("qa_writer", "isolated", "confirm", "qa", {}, writer)
    result = asyncio.run(_invoke(module, module.Invoke(name="qa_writer", confirmed=True)))
    assert result["status"] == "disabled"
    assert result["result"]["error_code"] == "QA_LIVE_READONLY"
    assert result["result"]["write_executed"] is False
    assert called is False


def test_live_readonly_scope_denies_write_low_before_handler(tmp_path):
    module = _load_tools()
    module.AUDIT = tmp_path / "audit.jsonl"
    module.QA_MODE = "live_readonly"
    called = False

    async def writer(_):
        nonlocal called
        called = True
        return {"status": "ok"}

    module.TOOLS["qa_writer"] = ("qa_writer", "isolated", "write_low", "qa", {}, writer)
    result = asyncio.run(_invoke(module, module.Invoke(name="qa_writer", confirmed=True)))
    assert result["status"] == "disabled"
    assert result["result"]["error_code"] == "QA_LIVE_READONLY"
    assert result["result"]["write_executed"] is False
    assert called is False


def test_isolated_scope_denies_production_mutations_before_handler(tmp_path):
    module = _load_tools()
    module.AUDIT = tmp_path / "audit.jsonl"
    module.QA_MODE = "isolated_execution"
    called = False

    async def writer(_):
        nonlocal called
        called = True
        return {"status": "ok", "write_executed": True}

    module.TOOLS["qa_writer"] = ("qa_writer", "isolated", "destructive", "qa", {}, writer)
    result = asyncio.run(_invoke(module, module.Invoke(name="qa_writer", confirmed=True)))
    assert result["status"] == "disabled"
    assert result["result"]["error_code"] == "QA_ISOLATED_EXECUTOR_REQUIRED"
    assert called is False


def _qa_startup(tmp_path, **settings):
    env = os.environ.copy()
    for name in (
        "HOME_AI_QA_MODE", "HOME_AI_QA_EXECUTOR", "HOME_AI_QA_STATE_ROOT",
        "CLIDEBRID_BRIDGE_TOKEN", "CLIDEBRID_BRIDGE_TOKEN_FILE",
        "HOME_ASSISTANT_TOKEN", "HOME_ASSISTANT_TOKEN_FILE",
    ):
        env.pop(name, None)
    env.update({name: str(value) for name, value in settings.items()})
    app_path = Path(__file__).parents[1] / "tools" / "server-tools-app.py"
    script = (
        "import importlib.util; "
        f"s=importlib.util.spec_from_file_location('qa_startup', {str(app_path)!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
    )
    return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)


def test_isolated_mode_startup_is_fail_closed(tmp_path):
    valid_root = tmp_path / "isolated-state"
    valid_root.mkdir()
    valid = _qa_startup(
        tmp_path,
        HOME_AI_QA_MODE="isolated_execution",
        HOME_AI_QA_EXECUTOR="fake",
        HOME_AI_QA_STATE_ROOT=valid_root,
    )
    assert valid.returncode == 0, valid.stderr

    cases = (
        {"HOME_AI_QA_MODE": "unknown"},
        {"HOME_AI_QA_MODE": "isolated_execution", "HOME_AI_QA_STATE_ROOT": valid_root},
        {"HOME_AI_QA_MODE": "isolated_execution", "HOME_AI_QA_EXECUTOR": "fake",
         "HOME_AI_QA_STATE_ROOT": tmp_path / "missing"},
        {"HOME_AI_QA_MODE": "isolated_execution", "HOME_AI_QA_EXECUTOR": "fake",
         "HOME_AI_QA_STATE_ROOT": valid_root, "CLIDEBRID_BRIDGE_TOKEN": "must-refuse"},
    )
    for settings in cases:
        result = _qa_startup(tmp_path, **settings)
        assert result.returncode != 0, settings


def test_live_readonly_mode_refuses_mutation_credentials(tmp_path):
    valid = _qa_startup(tmp_path, HOME_AI_QA_MODE="live_readonly")
    assert valid.returncode == 0, valid.stderr
    for settings in (
        {"CLIDEBRID_BRIDGE_TOKEN": "must-refuse"},
        {"CLIDEBRID_BRIDGE_TOKEN_FILE": "/run/secrets/production-bridge"},
        {"HOME_ASSISTANT_TOKEN": "must-refuse"},
        {"HOME_ASSISTANT_TOKEN_FILE": "/run/secrets/production-home"},
    ):
        result = _qa_startup(tmp_path, HOME_AI_QA_MODE="live_readonly", **settings)
        assert result.returncode != 0, settings
