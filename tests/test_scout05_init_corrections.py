from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import subprocess

from click.testing import CliRunner
import pytest

from gigai.catalog import CatalogEntry
from gigai.cli import cli
from gigai.default_init import DefaultInitError, initialize_defaults, normalize_username
from gigai.registry import (
    ACTIVE_WORKPAD_TABLE_SQL,
    PROJECT_TABLE_SQL,
    REGISTRY_APPLICATION_ID,
    REGISTRY_V2_SCHEMA_VERSION,
    RegistryMigrationRequired,
    WORKPAD_TABLE_SQL,
    open_project_registry,
)
from gigai.setup import build_config, run_setup
from gigai.workpad import workpad_layout_version


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", target], check=True
    )
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    return home, target


def _entry(name: str) -> CatalogEntry:
    return CatalogEntry(
        catalog_id=name,
        definition_version="1.0",
        title=name,
        summary="synthetic release-eligible default",
        capabilities=("fixture.prepare",),
        files={"definition/gig.md": f"# {name}\n".encode()},
    )


def _binding_commits(workpad: Path) -> tuple[str, ...]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(workpad),
            "log",
            "--format=%H",
            "--",
            "manifests/template-instance-binding.json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def test_default_init_journals_v2_layout_and_typed_json_errors(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    missing = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--json"]
    )
    assert missing.exit_code == 1
    assert '"code":"username_required"' in missing.output
    assert not (target / ".gigai").exists()

    result = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=(_entry("one"),),
    )
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        record = transaction.find_workpad(result.instances[0].gig_id)
    assert record is not None
    assert workpad_layout_version(
        Path(record.workpad_locator),
        project_id=result.package.project_id,
        gig_id=record.gig_id,
    ) == 2
    assert result.scout_status == "binding_only_unready"
    assert "proposal" in result.instances[0].next_action

    conflict = CliRunner().invoke(
        cli,
        [
            "init",
            "--home",
            str(home),
            "--target",
            str(target),
            "--username",
            "other",
            "--json",
        ],
    )
    assert conflict.exit_code == 1
    assert '"code":"workspace_owner_conflict"' in conflict.output


def test_binding_resume_reads_its_single_committed_artifact(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    inventory = (_entry("one"),)

    def interrupt(step: str) -> None:
        if step == "binding_published":
            raise RuntimeError("stop after journal")

    with pytest.raises(RuntimeError, match="stop after journal"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=inventory,
            observer=interrupt,
        )
    registry, _ = open_project_registry(home, create=False)
    workpads = registry.workpad_records()
    assert len(workpads) == 1
    workpad = workpads[0]
    gig_id = workpad.gig_id
    root = Path(workpad.workpad_locator)
    commits = _binding_commits(root)
    assert len(commits) == 1

    # An independent manifest writer has an unrelated working artifact while
    # init resumes after journal publication but before cache repair.
    (root / "manifests" / "unrelated-source-inventory.json").write_text(
        "{}", encoding="utf-8"
    )

    resumed = initialize_defaults(
        home_root=home, requested_target=target, username=None, inventory=inventory
    )
    assert resumed.instances[0].gig_id == gig_id
    assert _binding_commits(root) == commits

    connection = sqlite3.connect(home / "registry.sqlite")
    try:
        connection.execute(
            "UPDATE template_instances SET binding_sha256 = ? WHERE gig_id = ?",
            ("sha256:" + "0" * 64, gig_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DefaultInitError) as refused:
        initialize_defaults(
            home_root=home, requested_target=target, username=None, inventory=inventory
        )
    assert refused.value.code == "template_reconciliation_required"
    assert _binding_commits(root) == commits


def test_pending_owner_digest_rejects_changed_username_without_new_binding(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    inventory = (_entry("one"),)

    def interrupt(step: str) -> None:
        if step == "binding_published":
            raise RuntimeError("stop after journal")

    with pytest.raises(RuntimeError, match="stop after journal"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=inventory,
            observer=interrupt,
        )
    # The binding exists even though no cache row was inserted by the interrupted call.
    connection = sqlite3.connect(home / "registry.sqlite")
    try:
        connection.execute("UPDATE workspace_owners SET username = ?", ("renamed",))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DefaultInitError) as refused:
        initialize_defaults(
            home_root=home, requested_target=target, username=None, inventory=inventory
        )
    assert refused.value.code == "workspace_owner_conflict"


def test_pending_inventory_mismatch_reports_the_pinned_batch_recovery(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)

    def interrupt(step: str) -> None:
        if step == "intent_prepared":
            raise RuntimeError("stop after intent")

    with pytest.raises(RuntimeError, match="stop after intent"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=(_entry("one"), _entry("two")),
            observer=interrupt,
        )
    with pytest.raises(DefaultInitError) as refused:
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username=None,
            inventory=(_entry("one"),),
        )
    assert refused.value.code == "template_instance_conflict"
    assert "package that started this batch" in str(refused.value)


def test_username_controls_are_rejected_but_joiners_and_private_use_are_preserved(
    tmp_path: Path,
) -> None:
    assert normalize_username("A\u200dB\ue000") == "A\u200dB\ue000"
    with pytest.raises(DefaultInitError) as invalid:
        normalize_username("A\u0085B")
    assert invalid.value.code == "username_invalid"

    home, target = _setup(tmp_path)
    initialize_defaults(home_root=home, requested_target=target, username="owner")
    connection = sqlite3.connect(home / "registry.sqlite")
    try:
        connection.execute("UPDATE workspace_owners SET username = ?", ("bad\u0085row",))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DefaultInitError) as corrupt:
        initialize_defaults(home_root=home, requested_target=target, username=None)
    assert corrupt.value.code == "workspace_owner_invalid"


def test_read_only_registry_refuses_legacy_migration_and_init_reports_setup(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    path = home / "registry.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA application_id = {REGISTRY_APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {REGISTRY_V2_SCHEMA_VERSION}")
        connection.execute(PROJECT_TABLE_SQL)
        connection.execute(WORKPAD_TABLE_SQL)
        connection.execute(ACTIVE_WORKPAD_TABLE_SQL)
        connection.commit()
    finally:
        connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(RegistryMigrationRequired):
        open_project_registry(home, create=False)
    assert not (home / "registry.sqlite.v2.bak").exists()

    result = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=(_entry("one"),),
    )
    assert "after_v3_commit" in result.setup_steps
    assert (home / "registry.sqlite.v2.bak").is_file()
