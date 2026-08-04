from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.adapters.factory import ModelAdapterBinding, resolve_model_adapter
from gigai.adapters.openai_api import OpenAIAPIAdapter
from gigai.adapters.openrouter_api import OpenRouterAPIAdapter
from gigai.adapters.port import InvocationRequest, InvocationResult, NormalizedUsage
from gigai.config import (
    CONFIG_SCHEMA_VERSION,
    ConfigurationMigrationError,
    CredentialReference,
    Endpoint,
    ModelTarget,
    Profile,
    config_path,
    load_config,
    migrate_config,
)
from gigai.diagnostics import render_report_json, run_live_doctor
from gigai.cli import cli
from gigai.setup import build_config, run_setup


def _v1_config(home: Path, workpad: Path) -> str:
    return f'''schema_version = "1.0"
credentials = []

[paths]
home_root = "{home}"
workpad_root = "{workpad}"

[editor]
argv = ["/usr/bin/true"]
open_with_target = false

[[endpoints]]
name = "offline"
adapter = "deterministic"

[[model_targets]]
name = "offline-default"
endpoint = "offline"
model = "fixture-v1"

[[profiles]]
name = "default"
planner = "offline-default"
critic = "offline-default"
adjudicator = "offline-default"

[standard_pack]
name = "standard"
version = "1"
content_digest = "sha256:test"
'''


def _request() -> InvocationRequest:
    return InvocationRequest(
        target_name="small",
        endpoint_name="provider",
        model="example/small",
        role="doctor",
        prompt="hello",
        target_capabilities=frozenset({"text"}),
        max_output_tokens=12,
        reasoning_effort="high",
    )


def test_v1_config_migration_is_explicit_atomic_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    workpad = tmp_path / "workpad"
    home.mkdir()
    workpad.mkdir()
    path = config_path(home)
    original = _v1_config(home, workpad).encode("utf-8")
    path.write_bytes(original)

    import gigai.config as config_module

    def interrupted_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated atomic replacement interruption")

    monkeypatch.setattr(config_module.os, "replace", interrupted_replace)
    with pytest.raises(ConfigurationMigrationError, match="simulated atomic replacement"):
        migrate_config(home)
    assert path.read_bytes() == original

    monkeypatch.undo()
    migrated, changed = migrate_config(home)
    rerun, rerun_changed = migrate_config(home)

    assert changed is True
    assert rerun_changed is False
    assert migrated == rerun == load_config(home)
    assert migrated.schema_version == CONFIG_SCHEMA_VERSION
    text = path.read_text(encoding="utf-8")
    assert 'schema_version = "2.0"' in text
    assert 'capabilities = ["text"]' in text
    assert "max_output_tokens = 64" in text


def test_malformed_v1_config_fails_before_mutating_bytes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workpad = tmp_path / "workpad"
    home.mkdir()
    workpad.mkdir()
    path = config_path(home)
    original = _v1_config(home, workpad).replace('model = "fixture-v1"\n', "")
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigurationMigrationError):
        migrate_config(home)
    assert path.read_text(encoding="utf-8") == original


def test_setup_command_explicitly_migrates_recognized_v1_configuration(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workpad = tmp_path / "workpad"
    home.mkdir()
    workpad.mkdir()
    config_path(home).write_text(_v1_config(home, workpad), encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        (
            "setup",
            "--non-interactive",
            "--home",
            os.fspath(home),
            "--workpad-root",
            os.fspath(workpad),
            "--editor",
            "/usr/bin/true",
            "--json",
        ),
    )

    assert result.exit_code == 0, result.output
    assert load_config(home).schema_version == CONFIG_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("adapter_type", "response", "expected_path", "expected_output", "expected_usage"),
    (
        (
            OpenAIAPIAdapter,
            {
                "model": "gpt-test-2026",
                "output": [{"content": [{"text": "openai-ok"}]}],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 5,
                    "total_tokens": 8,
                    "input_tokens_details": {"cached_tokens": 1},
                },
            },
            "/v1/responses",
            "openai-ok",
            (3, 5, 8),
        ),
        (
            OpenRouterAPIAdapter,
            {
                "model": "openrouter/test",
                "choices": [{"message": {"content": "router-ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
            },
            "/api/v1/chat/completions",
            "router-ok",
            (2, 4, 6),
        ),
    ),
)
def test_provider_adapters_share_port_and_preserve_raw_distinct_usage(
    adapter_type: type[OpenAIAPIAdapter] | type[OpenRouterAPIAdapter],
    response: dict[str, object],
    expected_path: str,
    expected_output: str,
    expected_usage: tuple[int, int, int],
) -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json=response, request=request)

    adapter = adapter_type(
        credential=CredentialReference("provider", "environment", "GIGAI_TEST_TOKEN"),
        credential_resolver=lambda _: "g11-secret-canary",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = adapter.invoke(_request())

    assert result.status == "success"
    assert result.output_text == expected_output
    assert result.resolved_model == response["model"]
    assert result.cost_status == "unavailable"
    assert (
        result.normalized_usage.input_tokens,
        result.normalized_usage.output_tokens,
        result.normalized_usage.total_tokens,
    ) == expected_usage
    assert observed[0].url.path == expected_path
    assert observed[0].headers["authorization"] == "Bearer g11-secret-canary"
    assert json.loads(observed[0].content)["reasoning"] == {"effort": "high"}
    assert "g11-secret-canary" not in repr(result)
    if adapter_type is OpenAIAPIAdapter:
        assert result.raw_usage["input_tokens_details"] == {"cached_tokens": 1}


def test_factory_resolves_remote_target_and_target_policy_before_transport(
    tmp_path: Path,
) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("openai", "environment", "OPENAI_API_KEY"),),
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="openai", adapter="openai_api", credential="openai"),
        ),
        model_targets=(
            ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ModelTarget("small", "openai", "gpt-test", ("text",), 9, "high"),
        ),
    )

    binding = resolve_model_adapter(config, "small")
    request = binding.request(role="diagnostic", prompt="hello")

    assert binding.current.endpoint.adapter == "openai_api"
    assert request.endpoint_name == "openai"
    assert request.model == "gpt-test"
    assert request.target_capabilities == frozenset({"text"})
    assert request.max_output_tokens == 9
    assert request.reasoning_effort == "high"
    with pytest.raises(ValueError, match="capability policy"):
        binding.request(
            role="diagnostic", prompt="hello", required_capabilities=frozenset({"image"})
        )


def test_live_doctor_is_opt_in_budgeted_and_redacts_the_runtime_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("openai", "environment", "OPENAI_API_KEY"),),
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="openai", adapter="openai_api", credential="openai"),
        ),
        model_targets=(
            ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ModelTarget("cheap", "openai", "gpt-test", ("text",), 7, "high"),
        ),
    )
    run_setup(config)
    monkeypatch.setenv("OPENAI_API_KEY", "g11-live-secret-canary")

    import gigai.diagnostics as diagnostics

    actual_resolver = diagnostics.resolve_model_adapter
    requests: list[InvocationRequest] = []

    class FakePort:
        def invoke(self, request: InvocationRequest) -> InvocationResult:
            requests.append(request)
            return InvocationResult(
                status="success",
                output_text="provider response that is deliberately not printed",
                resolved_model=request.model,
                raw_usage={"input_tokens": 1, "output_tokens": 1},
                normalized_usage=NormalizedUsage(1, 1, 2),
                cost_status="unavailable",
            )

    def fake_resolver(active_config: object, target_name: str) -> ModelAdapterBinding:
        binding = actual_resolver(active_config, target_name)  # type: ignore[arg-type]
        return binding if target_name == "offline-default" else ModelAdapterBinding(binding.current, FakePort())

    monkeypatch.setattr(diagnostics, "resolve_model_adapter", fake_resolver)
    report = run_live_doctor(home, "cheap")
    rendered = render_report_json(report)
    live = next(check for check in report.checks if check.id == "adapter.live")

    assert report.scope == "live"
    assert report.overall_status == "PASS"
    assert live.status == "PASS"
    assert requests[0].max_output_tokens == 7
    assert requests[0].reasoning_effort == "high"
    assert "resolved_model=gpt-test" in live.evidence_safe_to_share
    assert "g11-live-secret-canary" not in rendered
    assert "provider response" not in rendered


def test_only_factory_may_import_concrete_adapters_and_negative_probe_is_detected() -> None:
    package = Path(__file__).parents[1] / "src" / "gigai"
    concrete = {"deterministic", "openai_api", "openrouter_api"}

    def imported_concrete_modules(tree: ast.AST) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if node.module in concrete:
                    found.add(node.module)
                elif len(parts) >= 3 and parts[-2] == "adapters" and parts[-1] in concrete:
                    found.add(parts[-1])
        return found

    for path in package.rglob("*.py"):
        found = imported_concrete_modules(ast.parse(path.read_text(encoding="utf-8")))
        if found:
            assert path == package / "adapters" / "factory.py"
    assert imported_concrete_modules(
        ast.parse("from gigai.adapters.openai_api import OpenAIAPIAdapter")
    ) == {"openai_api"}


def test_raw_credential_resolution_is_called_only_by_http_transport() -> None:
    package = Path(__file__).parents[1] / "src" / "gigai"
    imports: list[Path] = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {"credentials", "..credentials"}:
                if any(item.name == "resolve_reference_value" for item in node.names):
                    imports.append(path)
    assert imports == [package / "adapters" / "http.py"]
