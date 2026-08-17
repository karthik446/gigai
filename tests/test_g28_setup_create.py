from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import load_config


def test_setup_persists_the_operator_selected_create_model_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workpad = tmp_path / "workpads"
    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(workpad),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "openai=environment:OPENAI_API_KEY",
            "--endpoint",
            "openai=openai_api:openai:https://api.example.test",
            "--model-target",
            "remote=openai:gpt-test",
            "--create-model-target",
            "remote",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    config = load_config(home)
    default_profile = next(item for item in config.profiles if item.name == "default")
    assert default_profile.planner == "remote"


def test_setup_rejects_unconfigured_create_model_target(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(tmp_path / "home"),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            "--create-model-target",
            "missing",
        ],
    )
    assert result.exit_code != 0
    assert "is not configured" in result.output
