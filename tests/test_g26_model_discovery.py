from __future__ import annotations

from gigai.model_discovery import discover_installed_models, resolve_target_readiness
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
