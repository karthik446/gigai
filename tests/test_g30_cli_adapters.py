from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest

from gigai.adapters.claude_cli import ClaudeCLIAdapter
from gigai.adapters.codex_cli import CodexCLIAdapter
from gigai.adapters.port import (
    InvocationRequest,
    ModelAuthenticationRequired,
    ModelInvocationError,
)
from gigai.config import CredentialReference, Endpoint, ModelTarget, Profile
from gigai.model_targets import ModelTargetResolutionError, resolve_model_target
from gigai.setup import build_config


def _request(target: str = "cli-default") -> InvocationRequest:
    return InvocationRequest(
        target_name=target,
        endpoint_name="cli",
        model="model-under-test",
        role="gig-builder",
        prompt="Return a bounded answer.",
        target_capabilities=frozenset({"text"}),
        max_output_tokens=32,
    )


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-model-cli"
    path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_codex_adapter_uses_structured_output_and_excludes_synthetic_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIGAI_SYNTHETIC_SECRET", "must-not-cross-process-boundary")
    executable = _script(
        tmp_path,
        """
import json, os
assert os.getcwd() != os.environ.get("PWD", "")
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "codex-ok"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}))
print(json.dumps({"type": "secret-check", "item": {"type": "agent_message", "text": str("GIGAI_SYNTHETIC_SECRET" in os.environ)}}))
""",
    )

    result = CodexCLIAdapter(executable=str(executable)).invoke(_request())

    assert result.output_text == "codex-ok\nFalse"
    assert result.resolved_model == "model-under-test"
    assert result.normalized_usage.input_tokens == 2
    assert result.normalized_usage.output_tokens == 3


def test_claude_adapter_parses_json_result(tmp_path: Path) -> None:
    executable = _script(
        tmp_path,
        'import json; print(json.dumps({"type": "result", "subtype": "success", "result": "claude-ok", "model": "claude-test", "usage": {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10}}))',
    )

    result = ClaudeCLIAdapter(executable=str(executable)).invoke(_request())

    assert result.output_text == "claude-ok"
    assert result.resolved_model == "claude-test"
    assert result.normalized_usage.total_tokens == 10
    assert result.cost_status == "provider_reported"


def test_cli_adapter_rejects_malformed_output(tmp_path: Path) -> None:
    executable = _script(tmp_path, 'print("not-json")')

    with pytest.raises(ModelInvocationError, match="malformed"):
        ClaudeCLIAdapter(executable=str(executable)).invoke(_request())


def test_cli_adapter_classifies_structured_authentication_failure(tmp_path: Path) -> None:
    executable = _script(
        tmp_path,
        'import json; print(json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})); raise SystemExit(1)',
    )

    with pytest.raises(ModelAuthenticationRequired, match="authentication_required"):
        ClaudeCLIAdapter(executable=str(executable)).invoke(_request())


def test_cli_adapter_timeout_is_terminal_and_bounded(tmp_path: Path) -> None:
    executable = _script(tmp_path, "import time; time.sleep(2)")

    with pytest.raises(ModelInvocationError, match="timed out"):
        ClaudeCLIAdapter(executable=str(executable), timeout_seconds=0.05).invoke(_request())


def test_cli_endpoints_are_typed_without_gigai_credentials(tmp_path: Path) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("openai", "environment", "OPENAI_API_KEY"),),
        endpoints=(
            Endpoint(name="codex", adapter="codex_cli"),
            Endpoint(name="openai", adapter="openai_api", credential="openai"),
        ),
        model_targets=(
            ModelTarget("cli-default", "codex", "model-under-test", ("text",), 32),
            ModelTarget("openai-default", "openai", "gpt-test", ("text",), 32),
        ),
        profiles=(
            Profile("default", "cli-default", "cli-default", "cli-default"),
        ),
    )

    assert {endpoint.adapter for endpoint in config.endpoints} == {"codex_cli", "openai_api"}


def test_disabled_model_target_cannot_be_resolved_for_invocation(tmp_path: Path) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(Endpoint(name="codex", adapter="codex_cli"),),
        model_targets=(
            ModelTarget("disabled", "codex", "model-under-test", ("text",), 32, enabled=False),
        ),
        profiles=(Profile("default", "disabled", "disabled", "disabled"),),
    )

    with pytest.raises(ModelTargetResolutionError, match="disabled"):
        resolve_model_target(config, "disabled")
