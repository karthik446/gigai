from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

from gigai.lifecycle import create_offline
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from tests.scenarios import (
    InstalledGigAI,
    ScenarioHarness,
    ScenarioRoots,
    ScenarioSpec,
    copy_fixture_repository,
    create_recording_substitute,
)


def _git_executable() -> Path:
    candidate = Path("/usr/bin/git")
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which("git", path="/usr/bin:/bin")
    assert discovered is not None
    return Path(discovered).resolve()


def _uuids():
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{value:012x}")
        for value in range(1, 32)
    )
    return lambda: next(values)


def test_installed_open_without_id_resolves_active_private_workpad_with_target(
    tmp_path: Path,
) -> None:
    roots = ScenarioRoots.create(tmp_path / "active-open")
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
    initialize_target(
        home_root=roots.home,
        requested_target=roots.target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    created = create_offline(
        home_root=roots.home,
        requested_target=roots.target,
        name="active-open-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )

    result = ScenarioHarness(InstalledGigAI.current().command, roots).run(
        ScenarioSpec(
            name="active-open-with-target",
            argv=("open", "--with-target"),
            allowed_subprocesses=(_git_executable(), editor),
            extra_env=(
                ("GIGAI_RECORDING_BOUNDARY", "editor"),
                ("GIGAI_RECORDING_LOG", os.fspath(editor_log)),
            ),
        )
    )

    assert json.loads(editor_log.read_text(encoding="utf-8")) == {
        "argv": [
            "--literal-editor-arg",
            os.fspath(created.workpad),
            os.fspath(roots.target),
        ],
        "boundary": "editor",
    }
    assert result.stdout == "Opened the registered workpad and bound target.\n"
    assert result.target_before == result.target_after
    assert result.workpad_before == result.workpad_after
    assert result.home_before == result.home_after
