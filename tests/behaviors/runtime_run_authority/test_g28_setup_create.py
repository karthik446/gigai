from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.config import load_config
from gigai.target_binding import GitTargetError, initialize_target
from gigai.workpad import WorkpadConflictError, resolve_bound_project


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
            "--credential-ref",
            "openai=environment:OPENAI_API_KEY",
            "--endpoint",
            "openai=openai_api:openai:https://api.example.test",
            "--model-target",
            "remote=openai:gpt-test",
            "--create-model-target",
            "remote",
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


def test_initialized_non_git_target_resolves_implicitly_from_a_subfolder(
    tmp_path: Path,
) -> None:
    """cwd inside a subfolder of a bound non-Git target still resolves it.

    Mirrors test_initialized_non_git_target_resolves_implicitly_from_its_directory
    but from a nested working directory, so e.g. `gigai scout status` works
    from anywhere under the target, not just its exact root.
    """

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
            "--credential-ref",
            "openai=environment:OPENAI_API_KEY",
            "--endpoint",
            "openai=openai_api:openai:https://api.example.test",
            "--model-target",
            "remote=openai:gpt-test",
            "--create-model-target",
            "remote",
        ],
    )
    assert setup.exit_code == 0, setup.output

    initialized = initialize_target(home_root=home, requested_target=target)
    assert initialized.target_kind == "non-git"

    subfolder = target / "nested" / "deeper"
    subfolder.mkdir(parents=True)

    resolved = resolve_bound_project(
        home_root=home,
        requested_target=None,
        cwd=subfolder,
    )
    assert resolved.project_id == initialized.project_id
    assert resolved.target_root == target.resolve()
    assert resolved.target_kind == "non-git"


def test_unbound_non_git_cwd_still_errors_without_target(tmp_path: Path) -> None:
    """An unbound non-Git directory (never `gigai init --target`-ed) still
    requires an explicit --target; implicit cwd resolution never invents a
    binding, it only recognizes one already registered for that exact path
    or an ancestor of it.
    """

    home = tmp_path / "home"
    workpad = tmp_path / "workpads"
    unbound = tmp_path / "unbound"
    unbound.mkdir()
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
            "--credential-ref",
            "openai=environment:OPENAI_API_KEY",
            "--endpoint",
            "openai=openai_api:openai:https://api.example.test",
            "--model-target",
            "remote=openai:gpt-test",
            "--create-model-target",
            "remote",
        ],
    )
    assert setup.exit_code == 0, setup.output

    with pytest.raises((GitTargetError, WorkpadConflictError)):
        resolve_bound_project(
            home_root=home,
            requested_target=None,
            cwd=unbound,
        )
