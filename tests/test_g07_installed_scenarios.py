from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad
from tests.scenarios import (
    InstalledGigAI,
    ScenarioHarness,
    ScenarioRoots,
    ScenarioSpec,
    copy_fixture_repository,
)
from tests.test_g07_contract_validators import _write_valid_proposal


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _git_executable() -> Path:
    candidate = Path("/usr/bin/git")
    return (
        candidate
        if candidate.is_file()
        else Path(shutil.which("git", path="/usr/bin:/bin") or "")
    )


def test_installed_check_validates_a_registered_proposal_without_mutation(
    tmp_path: Path,
) -> None:
    installed = InstalledGigAI.current()
    roots = ScenarioRoots.create(tmp_path / "check")
    copy_fixture_repository("python", roots.target, fixture_root=roots.fixtures)
    run_setup(
        build_config(
            home_root=roots.home,
            workpad_root=roots.workpad,
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    binding = initialize_target(
        home_root=roots.home,
        requested_target=roots.target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    assert binding.project_id == PROJECT_ID
    workpad = provision_workpad(
        home_root=roots.home, project_id=PROJECT_ID, gig_id=GIG_ID
    ).path
    _write_valid_proposal(workpad)
    assert {entry.name for entry in workpad.iterdir()} == {
        ".git",
        ".gitignore",
        "gig.md",
        "goals",
        "reviews",
        "decisions",
        "manifests",
    }
    result = ScenarioHarness(installed.command, roots).run(
        ScenarioSpec(
            name="check",
            argv=("check", GIG_ID, "--json"),
            allowed_subprocesses=(_git_executable(),),
        )
    )
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"findings": [], "valid": True}
    assert result.target_before == result.target_after
    assert result.workpad_before == result.workpad_after
    assert result.home_before == result.home_after
