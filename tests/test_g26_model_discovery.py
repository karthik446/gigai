from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gigai.adapters.port import (
    InvocationResult,
    ModelAuthenticationRequired,
    NormalizedUsage,
    ModelInvocationError,
)
from gigai.model_discovery import (
    discover_installed_models,
    probe_target_readiness,
    resolve_target_readiness,
)
from gigai.config import Endpoint, ModelTarget
from gigai.setup import build_config
from click.testing import CliRunner
from gigai.cli import cli
from gigai.setup import run_setup


def test_model_discovery_is_read_only_and_reports_cli_candidates() -> None:
    paths = {"codex": "/usr/local/bin/codex", "claude": None}
    seen: list[str] = []

    def fake_which(name: str) -> str | None:
        seen.append(name)
        return paths[name]

    detected = discover_installed_models(which=fake_which)
    assert seen == ["codex", "claude"]
    assert detected[0].readiness == "detected"
    assert detected[0].executable is not None
    assert detected[1].readiness == "unavailable"
    assert detected[1].executable is None


def test_model_discovery_records_bounded_cli_version_evidence(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    executable.write_text("#! /bin/sh\nprintf 'codex-cli test-version\\n'\n", encoding="utf-8")
    executable.chmod(0o755)

    detected = discover_installed_models(
        which=lambda name: str(executable) if name == "codex" else None
    )

    assert detected[0].readiness == "detected"
    assert detected[0].version == "codex-cli test-version"
    assert detected[1].version is None


def test_configured_deterministic_target_is_usable_without_a_model_call(tmp_path) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    readiness = resolve_target_readiness(config, "offline-default")
    assert readiness.readiness == "usable"
    assert readiness.adapter == "deterministic"


def test_configured_provider_is_not_reported_usable_without_an_explicit_probe(
    tmp_path, monkeypatch
) -> None:
    calls: list[str] = []

    class FakePort:
        def invoke(self, request):
            calls.append(request.prompt)
            raise AssertionError("discovery must not invoke a provider")

    binding = SimpleNamespace(
        current=SimpleNamespace(
            endpoint=Endpoint("codex", "codex_cli"),
            target=ModelTarget("codex-default", "codex", "default", ("text",), 32),
        ),
        port=FakePort(),
    )
    monkeypatch.setattr("gigai.model_discovery.resolve_model_adapter", lambda *_: binding)
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )

    readiness = resolve_target_readiness(config, "codex-default")

    assert readiness.readiness == "configured"
    assert "explicit readiness probe" in (readiness.reason or "")
    assert calls == []


def test_explicit_provider_probe_promotes_target_to_usable(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class FakePort:
        def invoke(self, request):
            calls.append(request.prompt)
            return InvocationResult(
                status="success",
                output_text="READY",
                resolved_model="codex-test",
                raw_usage={},
                normalized_usage=NormalizedUsage(None, None, None),
                cost_status="unavailable",
            )

    binding = SimpleNamespace(
        current=SimpleNamespace(
            endpoint=Endpoint("codex", "codex_cli"),
            target=ModelTarget("codex-default", "codex", "default", ("text",), 32),
        ),
        port=FakePort(),
        request=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr("gigai.model_discovery.resolve_model_adapter", lambda *_: binding)
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )

    readiness = probe_target_readiness(config, "codex-default")

    assert readiness.readiness == "usable"
    assert calls == ["Return exactly READY as a readiness check. Do not use tools or modify files."]


def test_explicit_provider_probe_fails_closed(tmp_path, monkeypatch) -> None:
    class FakePort:
        def invoke(self, request):
            raise ModelInvocationError("provider authentication unavailable")

    binding = SimpleNamespace(
        current=SimpleNamespace(
            endpoint=Endpoint("claude", "claude_cli"),
            target=ModelTarget("claude-default", "claude", "default", ("text",), 32),
        ),
        port=FakePort(),
        request=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr("gigai.model_discovery.resolve_model_adapter", lambda *_: binding)
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )

    readiness = probe_target_readiness(config, "claude-default")

    assert readiness.readiness == "unavailable"
    assert readiness.reason == "provider authentication unavailable"


def test_explicit_provider_probe_classifies_missing_authentication(tmp_path, monkeypatch) -> None:
    class FakePort:
        def invoke(self, request):
            raise ModelAuthenticationRequired(
                "authentication_required: Not logged in; run /login"
            )

    binding = SimpleNamespace(
        current=SimpleNamespace(
            endpoint=Endpoint("claude", "claude_cli"),
            target=ModelTarget("claude-default", "claude", "default", ("text",), 32),
        ),
        port=FakePort(),
        request=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr("gigai.model_discovery.resolve_model_adapter", lambda *_: binding)
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )

    readiness = probe_target_readiness(config, "claude-default")

    assert readiness.readiness == "configured"
    assert readiness.reason.startswith("authentication_required:")


def test_unknown_target_is_unavailable(tmp_path) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    readiness = resolve_target_readiness(config, "missing-target")
    assert readiness.readiness == "unavailable"


def test_models_command_reports_configured_target_without_secret_values(tmp_path) -> None:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)
    result = CliRunner().invoke(cli, ["models", "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    assert '"target_name":"offline-default"' in result.output
    assert "fixture-v1" in result.output
    assert "credential" not in result.output


def test_models_command_probe_is_explicit_and_reports_readiness(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)
    monkeypatch.setattr(
        "gigai.cli.probe_target_readiness",
        lambda _config, target: SimpleNamespace(
            target_name=target,
            endpoint_name="offline",
            model="fixture-v1",
            adapter="deterministic",
            readiness="usable",
            reason=None,
        ),
    )

    result = CliRunner().invoke(
        cli, ["models", "--home", str(home), "--probe", "offline-default", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert '"probe":{"adapter":"deterministic"' in result.output
