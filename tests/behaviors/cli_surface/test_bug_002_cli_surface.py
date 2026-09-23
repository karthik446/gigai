from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import CredentialReference, Endpoint, ModelTarget
from gigai.setup import build_config, run_setup


def test_public_help_hides_developer_commands_and_internal_help_retains_access() -> None:
    runner = CliRunner()

    public = runner.invoke(cli, ["--help"])
    assert public.exit_code == 0, public.output
    assert "internal" not in public.output
    assert "eval" not in public.output
    assert "improve" not in public.output

    internal = runner.invoke(cli, ["internal", "--help"])
    assert internal.exit_code == 0, internal.output
    assert "eval" in internal.output
    assert "improve" in internal.output


def test_models_human_output_uses_labels_and_reports_unconfigured_api_targets(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)

    result = CliRunner().invoke(cli, ["models", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert "Offline fixture" not in result.output
    assert "OpenAI API: not_configured" in result.output
    assert "OpenRouter API: not_configured" in result.output
    assert "offline-default" not in result.output
    assert "codex-default" not in result.output
    assert "claude-default" not in result.output


def test_models_json_reports_missing_api_reference_without_probe(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("openai-api", "environment", "MISSING_KEY"),),
        endpoints=(
            Endpoint("offline", "deterministic"),
            Endpoint("openai", "openai_api", credential="openai-api"),
        ),
        model_targets=(
            ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ModelTarget("openai-default", "openai", "gpt-test", ("text",), 64),
        ),
    )
    run_setup(config)

    result = CliRunner().invoke(
        cli,
        ["models", "--home", str(home), "--probe", "openai-default", "--json"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    target = next(item for item in payload["configured"] if item["target_name"] == "openai-default")
    assert target["state"] == "credential_reference_missing"
    assert target["credential_status"] == "missing_or_unusable"
    assert "MISSING_KEY" not in result.output
    assert "no probe was attempted" in payload["probe"]["reason"]


def test_unbound_project_guidance_is_actionable_in_json(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )

    result = CliRunner().invoke(
        cli,
        ["status", "--target", str(target), "--home", str(home), "--json"],
    )

    assert result.exit_code != 0, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "project_unbound"
    assert "gigai init --target PATH" in payload["error"]["message"]


def test_public_workpad_commands_use_actionable_unbound_guidance(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    runner = CliRunner()

    path_result = runner.invoke(
        cli,
        ["workpad", "path", "--target", str(target), "--home", str(home)],
    )
    check_result = runner.invoke(
        cli,
        ["check", "--target", str(target), "--home", str(home), "--json"],
    )
    open_result = runner.invoke(
        cli,
        ["open", "--target-root", str(target), "--home", str(home)],
    )

    assert path_result.exit_code != 0, path_result.output
    assert "gigai init --target PATH" in path_result.output
    assert "target is not bound to a GigAI project" not in path_result.output

    assert check_result.exit_code == 1, check_result.output
    check_payload = json.loads(check_result.output)
    assert check_payload["error"]["code"] == "project_unbound"
    assert "gigai init --target PATH" in check_payload["error"]["message"]

    assert open_result.exit_code != 0, open_result.output
    assert "gigai init --target PATH" in open_result.output
    assert "target is not bound to a GigAI project" not in open_result.output
