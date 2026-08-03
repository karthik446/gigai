from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from gigai.project_binding import load_project_binding
from gigai.registry import WorkpadRecord, open_project_registry, registry_path
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import (
    NoActiveGigError,
    WORKPAD_GITIGNORE,
    WorkpadConflictError,
    WorkpadPermissionError,
    WorkpadUnavailableError,
    open_locations,
    provision_workpad,
    resolve_workpad,
    select_active_workpad,
)


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_GIG_ID = "gig_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        capture_output=True,
        text=True,
        check=check,
        shell=False,
    )


def _configured_non_git(tmp_path: Path, *, editor: Path | None = None) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    workpad_root = tmp_path / "workpads"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=workpad_root,
            editor_argv=(os.fspath(editor or Path("/usr/bin/true")),),
            open_with_target=False,
        )
    )
    result = initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    assert result.project_id == PROJECT_ID
    return home, workpad_root, target


def _configured_git(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    workpad_root = tmp_path / "workpads"
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "--initial-branch=main", "--quiet")
    run_setup(
        build_config(
            home_root=home,
            workpad_root=workpad_root,
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    result = initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    assert result.project_id == PROJECT_ID
    return home, workpad_root, target


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update((path.stat().st_mode & 0o777).to_bytes(2, "big"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_provisioning_creates_only_unborn_private_git_and_is_byte_idempotent(
    tmp_path: Path,
) -> None:
    home, workpad_root, _target = _configured_non_git(tmp_path)

    first = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    digest = _tree_digest(first.path)
    second = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)

    assert first.path == (
        workpad_root / "projects" / PROJECT_ID / "gigs" / GIG_ID
    )
    assert first.published is True
    assert first.registry_changed is True
    assert second.published is False
    assert second.registry_changed is False
    assert second.reconciled is True
    assert _tree_digest(first.path) == digest
    assert {path.name for path in first.path.iterdir()} == {".git", ".gitignore"}
    assert WORKPAD_GITIGNORE == b"/objects/\n/scratch/\n/state.sqlite\n"
    assert (first.path / ".gitignore").read_bytes() == WORKPAD_GITIGNORE
    assert _git(first.path, "rev-parse", "--verify", "HEAD", check=False).returncode != 0
    assert _git(first.path, "remote").stdout == ""
    assert _git(first.path, "config", "--local", "user.name").stdout.strip() == "GigAI Journal"
    assert _git(first.path, "config", "--local", "user.email").stdout.strip() == "local@gigai.invalid"
    assert _git(first.path, "config", "--local", "gigai.project-id").stdout.strip() == PROJECT_ID
    assert _git(first.path, "config", "--local", "gigai.gig-id").stdout.strip() == GIG_ID
    registry, _ = open_project_registry(home, create=False)
    assert registry.workpad_records() == (
        WorkpadRecord(GIG_ID, PROJECT_ID, os.fspath(first.path)),
    )


def test_provisioning_has_no_identity_allocator_ownership_escape() -> None:
    path = Path(__file__).parents[1] / "src" / "gigai" / "workpad.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    prohibited = {"uuid4", "generate_entity_id", "allocate_id", "new_gig_id"}
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    imports = {
        alias.name.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not prohibited & (names | imports)


@pytest.mark.parametrize("failpoint", ("after_staging", "after_publish", "after_registry"))
def test_provisioning_exception_boundaries_reconcile_without_replacement(
    tmp_path: Path, failpoint: str
) -> None:
    home, workpad_root, _target = _configured_non_git(tmp_path)
    destination = workpad_root / "projects" / PROJECT_ID / "gigs" / GIG_ID

    def stop(observed: str) -> None:
        if observed == failpoint:
            raise RuntimeError(observed)

    with pytest.raises(RuntimeError, match=failpoint):
        provision_workpad(
            home_root=home,
            project_id=PROJECT_ID,
            gig_id=GIG_ID,
            provision_observer=stop,
        )

    if failpoint == "after_staging":
        assert not destination.exists()
    else:
        assert destination.is_dir()
    result = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    assert result.path == destination
    assert result.reconciled is (failpoint != "after_staging")
    assert not tuple(destination.parent.glob(f".{GIG_ID}.provision-*"))


def test_hard_crash_after_staging_reconciles_exact_substrate(tmp_path: Path) -> None:
    home, workpad_root, _target = _configured_non_git(tmp_path)
    script = """
import os
from pathlib import Path
import sys
from gigai.workpad import provision_workpad

def crash(step: str) -> None:
    if step == "after_staging":
        os._exit(74)

provision_workpad(
    home_root=Path(sys.argv[1]), project_id=sys.argv[2], gig_id=sys.argv[3],
    provision_observer=crash,
)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, os.fspath(home), PROJECT_ID, GIG_ID],
        capture_output=True,
        check=False,
        shell=False,
    )
    assert crashed.returncode == 74
    parent = workpad_root / "projects" / PROJECT_ID / "gigs"
    assert len(tuple(parent.glob(f".{GIG_ID}.provision-*"))) == 1

    result = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)

    assert result.reconciled is True
    assert result.published is True
    assert not tuple(parent.glob(f".{GIG_ID}.provision-*"))


def test_missing_read_only_and_repointed_mounts_fail_without_fallback(
    tmp_path: Path,
) -> None:
    home, workpad_root, _target = _configured_non_git(tmp_path)
    workpad_root.rmdir()
    with pytest.raises(WorkpadUnavailableError, match="no fallback"):
        provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    assert not workpad_root.exists()

    workpad_root.mkdir()
    workpad_root.chmod(0o500)
    try:
        with pytest.raises(WorkpadPermissionError, match="read-only"):
            provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    finally:
        workpad_root.chmod(0o700)

    original = tmp_path / "original-workpads"
    workpad_root.rename(original)
    replacement = tmp_path / "replacement-workpads"
    replacement.mkdir()
    workpad_root.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(WorkpadConflictError, match="symlink"):
        provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    assert not tuple(replacement.iterdir())


def test_resolution_rejects_locator_marker_remote_and_symlink_conflicts(
    tmp_path: Path,
) -> None:
    home, _workpad_root, target = _configured_non_git(tmp_path)
    provisioned = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    resolved = resolve_workpad(
        home_root=home, requested_target=target, gig_id=GIG_ID
    )
    assert resolved.path == provisioned.path

    connection = sqlite3.connect(registry_path(home))
    try:
        connection.execute(
            "UPDATE workpads SET workpad_locator = ? WHERE gig_id = ?",
            (os.fspath(tmp_path / "elsewhere"), GIG_ID),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(WorkpadConflictError, match="locator"):
        resolve_workpad(home_root=home, requested_target=target, gig_id=GIG_ID)
    connection = sqlite3.connect(registry_path(home))
    try:
        connection.execute(
            "UPDATE workpads SET workpad_locator = ? WHERE gig_id = ?",
            (os.fspath(provisioned.path), GIG_ID),
        )
        connection.commit()
    finally:
        connection.close()

    _git(provisioned.path, "config", "--local", "gigai.gig-id", OTHER_GIG_ID)
    with pytest.raises(WorkpadConflictError, match="marker"):
        resolve_workpad(home_root=home, requested_target=target, gig_id=GIG_ID)
    _git(provisioned.path, "config", "--local", "gigai.gig-id", GIG_ID)
    _git(provisioned.path, "remote", "add", "origin", "https://example.invalid/repo.git")
    with pytest.raises(WorkpadConflictError, match="remote"):
        resolve_workpad(home_root=home, requested_target=target, gig_id=GIG_ID)
    _git(provisioned.path, "remote", "remove", "origin")

    relocated = tmp_path / "relocated"
    provisioned.path.rename(relocated)
    provisioned.path.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(WorkpadConflictError, match="redirected"):
        resolve_workpad(home_root=home, requested_target=target, gig_id=GIG_ID)


def test_no_active_is_typed_and_git_binding_is_authoritative(tmp_path: Path) -> None:
    home, _workpad_root, target = _configured_git(tmp_path)
    first = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=OTHER_GIG_ID)
    binding_before = (target / ".gigai" / "project.toml").read_bytes()
    registry_before = registry_path(home).read_bytes()

    with pytest.raises(NoActiveGigError, match="no_active_gig"):
        resolve_workpad(home_root=home, requested_target=target, gig_id=None)
    assert (target / ".gigai" / "project.toml").read_bytes() == binding_before
    assert registry_path(home).read_bytes() == registry_before

    select_active_workpad(
        home_root=home, requested_target=target, gig_id=GIG_ID
    )
    binding = load_project_binding(target)
    assert binding.active_gig_id == GIG_ID
    assert b"/" not in (target / ".gigai" / "project.toml").read_bytes()
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        transaction.select_active_workpad(PROJECT_ID, OTHER_GIG_ID)

    resolved = resolve_workpad(
        home_root=home, requested_target=target, gig_id=None
    )
    assert resolved.path == first.path
    with registry.transaction() as transaction:
        active = transaction.find_active_workpad(PROJECT_ID)
    assert active is not None and active.gig_id == GIG_ID


def test_non_git_active_authority_is_registry_only(tmp_path: Path) -> None:
    home, _workpad_root, target = _configured_non_git(tmp_path)
    provisioned = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    target_before = tuple(target.iterdir())

    select_active_workpad(home_root=home, requested_target=target, gig_id=GIG_ID)
    resolved = resolve_workpad(
        home_root=home, requested_target=target, gig_id=None
    )

    assert resolved.path == provisioned.path
    assert tuple(target.iterdir()) == target_before


def test_open_uses_structured_recording_editor_argv(tmp_path: Path) -> None:
    log = tmp_path / "editor.json"
    editor = tmp_path / "record-editor"
    editor.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['EDITOR_LOG']).write_text("
        "json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    editor.chmod(0o755)
    home, _workpad_root, target = _configured_non_git(tmp_path, editor=editor)
    provisioned = provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID)
    environment_before = os.environ.get("EDITOR_LOG")
    os.environ["EDITOR_LOG"] = os.fspath(log)
    try:
        result = open_locations(
            home_root=home,
            requested_target=target,
            gig_id=GIG_ID,
            target_only=False,
            with_target=True,
        )
    finally:
        if environment_before is None:
            os.environ.pop("EDITOR_LOG", None)
        else:
            os.environ["EDITOR_LOG"] = environment_before

    assert result.opened_workpad is True
    assert result.opened_target is True
    assert json.loads(log.read_text(encoding="utf-8")) == [
        os.fspath(provisioned.path),
        os.fspath(target),
    ]
