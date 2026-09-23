from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import subprocess

from click.testing import CliRunner
import pytest

from gigai.catalog import CatalogEntry
from gigai.cli import cli
from gigai.default_init import DefaultInitError, initialize_defaults
from gigai.package import PackageError
from gigai.project_binding import load_project_binding
from gigai.registry import open_project_registry
from gigai.registry import (
    ACTIVE_WORKPAD_TABLE_SQL,
    PROJECT_TABLE_SQL,
    REGISTRY_APPLICATION_ID,
    REGISTRY_V2_SCHEMA_VERSION,
    WORKPAD_TABLE_SQL,
)
from gigai.setup import build_config, run_setup


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
        files={"definition/gig.md": f"# {name}\n".encode("utf-8")},
    )


def test_defaults_provision_two_instances_without_active_selection_and_preserve_them(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    inventory = (_entry("first-default"), _entry("second-default"))

    first = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="  Rémi ",
        inventory=inventory,
    )

    assert first.username == "Rémi"
    assert {row.template_id for row in first.instances} == {
        "first-default",
        "second-default",
    }
    assert {row.status for row in first.instances} == {"binding_recorded"}
    assert first.scout_status == "binding_only_unready"
    assert load_project_binding(target).active_gig_id is None
    registry, _created = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        assert transaction.find_active_workpad(first.package.project_id) is None
        owner = transaction.find_workspace_owner(first.package.project_id)
        assert owner is not None
        assert owner.username == "Rémi"

    repeated = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=inventory,
    )
    assert {row.gig_id for row in repeated.instances} == {
        row.gig_id for row in first.instances
    }
    assert {row.status for row in repeated.instances} == {"existing"}

    extended = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=(*inventory, _entry("third-default")),
    )
    by_template = {row.template_id: row.gig_id for row in extended.instances}
    assert by_template["first-default"] == next(
        row.gig_id for row in first.instances if row.template_id == "first-default"
    )
    assert by_template["second-default"] == next(
        row.gig_id for row in first.instances if row.template_id == "second-default"
    )
    assert "third-default" in by_template


def test_interrupted_binding_resumes_the_reserved_instance_and_rebuilds_only_cache(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    inventory = (_entry("first-default"), _entry("second-default"))

    def interrupt(step: str) -> None:
        if step == "binding_published":
            raise RuntimeError("injected interruption")

    with pytest.raises(RuntimeError, match="injected interruption"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=inventory,
            observer=interrupt,
        )

    registry, _created = open_project_registry(home, create=False)
    reserved = {row.gig_id for row in registry.workpad_records()}
    assert len(reserved) == 1
    resumed = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=inventory,
    )
    assert reserved <= {row.gig_id for row in resumed.instances}
    assert len({row.gig_id for row in resumed.instances}) == 2
    with registry.transaction() as transaction:
        for row in resumed.instances:
            assert transaction.find_template_instance(
                resumed.package.project_id, row.template_id
            ) is not None


def test_missing_or_conflicting_username_refuses_without_initial_package_write(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    with pytest.raises(DefaultInitError, match="--username is required") as missing:
        initialize_defaults(home_root=home, requested_target=target, username=None)
    assert missing.value.code == "username_required"
    assert not (target / ".gigai").exists()

    initialize_defaults(home_root=home, requested_target=target, username="owner")
    with pytest.raises(DefaultInitError, match="conflicts") as conflict:
        initialize_defaults(home_root=home, requested_target=target, username="different")
    assert conflict.value.code == "workspace_owner_conflict"


def test_cli_init_is_username_gated_then_reports_prepared_defaults(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    missing = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--json"]
    )
    assert missing.exit_code != 0
    assert "--username is required" in missing.output
    assert not (target / ".gigai").exists()

    initialized = CliRunner().invoke(
        cli,
        [
            "init",
            "--home",
            str(home),
            "--target",
            str(target),
            "--username",
            "owner",
            "--json",
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    assert '"scout_status":"binding_only_unready"' in initialized.output
    assert '"instances":[' in initialized.output


def test_target_preflight_keeps_private_roots_closed_to_bound_real_directories(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    initialize_defaults(home_root=home, requested_target=target, username="owner")

    local = target / ".gigai" / "local"
    relocated = tmp_path / "relocated-local"
    local.rename(relocated)
    local.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(PackageError, match="non-symlink directory"):
        initialize_defaults(home_root=home, requested_target=target, username=None)

    local.unlink()
    relocated.rename(local)
    (target / ".gigai" / "unexpected").mkdir()
    with pytest.raises(PackageError, match="unexpected entries"):
        initialize_defaults(home_root=home, requested_target=target, username=None)


def test_target_preflight_refuses_tracked_private_init_state(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    initialize_defaults(home_root=home, requested_target=target, username="owner")
    subprocess.run(
        ["git", "-C", str(target), "add", "-f", ".gigai/local/default-init.json"],
        check=True,
    )

    with pytest.raises(PackageError, match="tracked .gigai content is refused"):
        initialize_defaults(home_root=home, requested_target=target, username=None)


def test_target_preflight_refuses_non_directory_private_init_root(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    initialize_defaults(home_root=home, requested_target=target, username="owner")
    locks = target / ".gigai" / "locks"
    locks.rmdir()
    locks.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PackageError, match="non-symlink directory"):
        initialize_defaults(home_root=home, requested_target=target, username=None)


def test_v2_registry_migrates_additively_before_owner_and_instance_binding(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
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

    registry, created = open_project_registry(
        home, create=False, allow_migration=True
    )
    assert created is False
    with registry.transaction() as transaction:
        assert transaction.count() == 0
    backup = home / "registry.sqlite.v2.bak"
    assert backup.is_file()
    backup_connection = sqlite3.connect(backup)
    try:
        assert backup_connection.execute("PRAGMA user_version").fetchone() == (2,)
    finally:
        backup_connection.close()
