from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import load_config
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_bound_project


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


def test_initialized_non_git_target_resolves_implicitly_from_its_directory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workpad = tmp_path / "workpads"
    target = tmp_path / "target"
    target.mkdir()
    setup = CliRunner().invoke(
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
        ],
    )
    assert setup.exit_code == 0, setup.output

    initialized = initialize_target(home_root=home, requested_target=target)
    assert initialized.target_kind == "non-git"

    resolved = resolve_bound_project(
        home_root=home,
        requested_target=None,
        cwd=target,
    )
    assert resolved.project_id == initialized.project_id
    assert resolved.target_root == target.resolve()
    assert resolved.target_kind == "non-git"
