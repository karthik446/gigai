from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad
from tests.scenarios import (
    InstalledGigAI,
    ScenarioHarness,
    ScenarioRoots,
    ScenarioSpec,
    copy_fixture_repository,
    create_recording_substitute,
)


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture
def installed_gigai() -> InstalledGigAI:
    return InstalledGigAI.current()


def _git_executable() -> Path:
    candidate = Path("/usr/bin/git")
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which("git", path="/usr/bin:/bin")
    assert discovered is not None
    return Path(discovered).resolve()


def _prepared(
    tmp_path: Path, installed_gigai: InstalledGigAI, name: str
) -> tuple[ScenarioRoots, ScenarioHarness, Path, Path]:
    roots = ScenarioRoots.create(tmp_path / name)
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    editor = create_recording_substitute(roots.fixtures, "record-editor")
    editor_log = roots.artifacts / "editor.json"
    run_setup(
        build_config(
            home_root=roots.home,
            workpad_root=roots.workpad,
            editor_argv=(os.fspath(editor), "--literal-editor-arg"),
            open_with_target=False,
        )
    )
    binding = initialize_target(
        home_root=roots.home,
        requested_target=roots.target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    assert binding.project_id == PROJECT_ID
    provisioned = provision_workpad(
        home_root=roots.home,
        project_id=PROJECT_ID,
        gig_id=GIG_ID,
    )
    return roots, ScenarioHarness(installed_gigai.command, roots), provisioned.path, editor_log


def test_installed_explicit_workpad_path_is_read_only_and_artifact_sanitized(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots, harness, workpad, _editor_log = _prepared(
        tmp_path, installed_gigai, "explicit-path"
    )

    result = harness.run(
        ScenarioSpec(
            name="workpad-path",
            argv=("workpad", "path", GIG_ID),
            allowed_subprocesses=(_git_executable(),),
        )
    )

    assert result.stdout == f"{workpad}\n"
    artifact = result.artifact.read_text(encoding="utf-8")
    assert os.fspath(roots.workpad) not in artifact
    assert "$WORKPAD/projects/" in artifact
    assert result.target_before == result.target_after
    assert result.workpad_before == result.workpad_after
    assert result.home_before == result.home_after


def test_installed_no_id_forms_fail_typed_without_active_or_mutation(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots, harness, _workpad, _editor_log = _prepared(
        tmp_path, installed_gigai, "missing-active"
    )

    path_result = harness.run(
        ScenarioSpec(
            name="path-no-active",
            argv=("workpad", "path"),
            expected_exit_codes=frozenset({1}),
            allowed_subprocesses=(_git_executable(),),
        )
    )
    open_result = harness.run(
        ScenarioSpec(
            name="open-no-active",
            argv=("open",),
            expected_exit_codes=frozenset({1}),
            allowed_subprocesses=(_git_executable(),),
        )
    )

    assert "no_active_gig" in path_result.stderr
    assert "no_active_gig" in open_result.stderr
    for result in (path_result, open_result):
        assert result.target_before == result.target_after
        assert result.workpad_before == result.workpad_after
        assert result.home_before == result.home_after


def test_installed_open_records_structured_workpad_and_target_argv(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots, harness, workpad, editor_log = _prepared(
        tmp_path, installed_gigai, "recorded-open"
    )
    editor = roots.fixtures / "record-editor"

    result = harness.run(
        ScenarioSpec(
            name="open-with-target",
            argv=("open", GIG_ID, "--with-target"),
            allowed_subprocesses=(_git_executable(), editor),
            extra_env=(
                ("GIGAI_RECORDING_BOUNDARY", "editor"),
                ("GIGAI_RECORDING_LOG", os.fspath(editor_log)),
            ),
        )
    )

    record = json.loads(editor_log.read_text(encoding="utf-8"))
    assert record == {
        "argv": [
            "--literal-editor-arg",
            os.fspath(workpad),
            os.fspath(roots.target),
        ],
        "boundary": "editor",
    }
    assert result.stdout == "Opened the registered workpad and bound target.\n"
    assert os.fspath(workpad) not in result.stdout
    assert result.target_before == result.target_after
    assert result.workpad_before == result.workpad_after
    assert result.home_before == result.home_after


def test_installed_open_target_only_requires_no_active_gig(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots, harness, _workpad, editor_log = _prepared(
        tmp_path, installed_gigai, "target-only"
    )
    editor = roots.fixtures / "record-editor"

    result = harness.run(
        ScenarioSpec(
            name="open-target-only",
            argv=("open", "--target"),
            allowed_subprocesses=(_git_executable(), editor),
            extra_env=(
                ("GIGAI_RECORDING_BOUNDARY", "editor"),
                ("GIGAI_RECORDING_LOG", os.fspath(editor_log)),
            ),
        )
    )

    record = json.loads(editor_log.read_text(encoding="utf-8"))
    assert record["argv"] == ["--literal-editor-arg", os.fspath(roots.target)]
    assert result.stdout == "Opened the bound target.\n"


def test_installed_cli_exposes_no_provision_or_activation_surface(
    tmp_path: Path, installed_gigai: InstalledGigAI
) -> None:
    roots = ScenarioRoots.create(tmp_path / "surface")
    harness = ScenarioHarness(installed_gigai.command, roots)

    workpad_help = harness.run(
        ScenarioSpec(name="workpad-help", argv=("workpad", "--help"))
    )
    for command in ("provision", "create", "activate", "select"):
        assert command not in workpad_help.stdout
    for command in ("provision", "activate", "select"):
        result = harness.run(
            ScenarioSpec(
                name=f"forbid-{command}",
                argv=("workpad", command),
                expected_exit_codes=frozenset({2}),
            )
        )
        assert f"No such command '{command}'" in result.stderr
