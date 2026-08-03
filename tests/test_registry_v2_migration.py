from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from gigai.registry import (
    ACTIVE_WORKPAD_TABLE_SQL,
    EXPECTED_REGISTRY_TABLES,
    MIGRATION_FAILPOINTS,
    PROJECT_TABLE_SQL,
    REGISTRY_APPLICATION_ID,
    REGISTRY_SCHEMA_VERSION,
    REGISTRY_V1_SCHEMA_VERSION,
    WORKPAD_TABLE_SQL,
    RegistryCorruptError,
    RegistryVersionError,
    open_project_registry,
    registry_backup_path,
    registry_path,
)


FIXTURE = Path(__file__).parent / "fixtures" / "registry" / "v1-populated.sql"
PROJECT_ROWS = (
    (
        "project_12345678-1234-4234-9234-123456789abc",
        "/fixture/targets/git-one",
        "git",
    ),
    (
        "project_87654321-4321-4432-a321-cba987654321",
        "/fixture/targets/non-git-two",
        "non-git",
    ),
)


def _materialize_v1(home: Path) -> Path:
    home.mkdir()
    path = registry_path(home)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(FIXTURE.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    path.chmod(0o600)
    return path


def _read_version(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute("PRAGMA user_version").fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def _tables(path: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(
            connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            )
        )
    finally:
        connection.close()


def _project_rows(path: Path) -> tuple[tuple[str, str, str], ...]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return tuple(
            connection.execute(
                "SELECT project_id, target_locator, target_kind "
                "FROM projects ORDER BY project_id"
            )
        )
    finally:
        connection.close()


def _legacy_v1_open(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()
        if version != (REGISTRY_V1_SCHEMA_VERSION,):
            raise RegistryVersionError(
                f"registry schema version {version[0] if version else None!r} "
                f"is unsupported; expected {REGISTRY_V1_SCHEMA_VERSION}; "
                "no migration was attempted"
            )
        tables = _tables(path)
        if tables != {"projects": PROJECT_TABLE_SQL}:
            raise RegistryCorruptError("legacy v1 exact-schema validation failed")
    finally:
        connection.close()


def test_registry_v2_constants_advance_as_one_contract() -> None:
    assert REGISTRY_V1_SCHEMA_VERSION == 1
    assert REGISTRY_SCHEMA_VERSION == 2
    assert EXPECTED_REGISTRY_TABLES == frozenset(
        {"projects", "workpads", "active_workpads"}
    )
    assert WORKPAD_TABLE_SQL == """\
CREATE TABLE workpads (
    gig_id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    workpad_locator TEXT NOT NULL UNIQUE,
    UNIQUE (project_id, gig_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
) WITHOUT ROWID
"""
    assert ACTIVE_WORKPAD_TABLE_SQL == """\
CREATE TABLE active_workpads (
    project_id TEXT PRIMARY KEY NOT NULL,
    gig_id TEXT NOT NULL,
    FOREIGN KEY (project_id, gig_id) REFERENCES workpads(project_id, gig_id)
) WITHOUT ROWID
"""
    assert MIGRATION_FAILPOINTS == (
        "before_backup_publish",
        "before_transaction",
        "after_workpads_table",
        "after_active_workpads_table",
        "before_version_write",
        "before_commit",
        "after_commit",
    )


def test_populated_v1_migrates_with_exact_rows_and_retained_backup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)

    registry, created = open_project_registry(home, create=False)

    assert created is False
    assert registry.path == path
    assert _read_version(path) == REGISTRY_SCHEMA_VERSION
    assert _tables(path) == {
        "active_workpads": ACTIVE_WORKPAD_TABLE_SQL,
        "projects": PROJECT_TABLE_SQL,
        "workpads": WORKPAD_TABLE_SQL,
    }
    assert _project_rows(path) == PROJECT_ROWS
    backup = registry_backup_path(home)
    assert backup.stat().st_mode & 0o777 == 0o600
    assert _read_version(backup) == REGISTRY_V1_SCHEMA_VERSION
    assert _tables(backup) == {"projects": PROJECT_TABLE_SQL}
    assert _project_rows(backup) == PROJECT_ROWS


@pytest.mark.parametrize(
    ("failpoint", "expected_version", "backup_exists"),
    (
        ("before_backup_publish", 1, False),
        ("before_transaction", 1, True),
        ("after_workpads_table", 1, True),
        ("after_active_workpads_table", 1, True),
        ("before_version_write", 1, True),
        ("before_commit", 1, True),
        ("after_commit", 2, True),
    ),
)
def test_migration_exception_failpoints_leave_only_complete_versions(
    tmp_path: Path,
    failpoint: str,
    expected_version: int,
    backup_exists: bool,
) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)

    def stop_at(observed: str) -> None:
        if observed == failpoint:
            raise RuntimeError(f"stopped at {observed}")

    with pytest.raises(RuntimeError, match=failpoint):
        open_project_registry(home, create=False, migration_observer=stop_at)

    assert _read_version(path) == expected_version
    expected_tables = (
        {"projects": PROJECT_TABLE_SQL}
        if expected_version == 1
        else {
            "active_workpads": ACTIVE_WORKPAD_TABLE_SQL,
            "projects": PROJECT_TABLE_SQL,
            "workpads": WORKPAD_TABLE_SQL,
        }
    )
    assert _tables(path) == expected_tables
    assert _project_rows(path) == PROJECT_ROWS
    assert registry_backup_path(home).exists() is backup_exists


@pytest.mark.parametrize("failpoint", MIGRATION_FAILPOINTS)
def test_process_crash_failpoints_recover_to_complete_v1_or_v2(
    tmp_path: Path, failpoint: str
) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)
    script = """
import os
from pathlib import Path
import sys
from gigai.registry import open_project_registry

home = Path(sys.argv[1])
failpoint = sys.argv[2]

def crash(observed: str) -> None:
    if observed == failpoint:
        os._exit(73)

open_project_registry(home, create=False, migration_observer=crash)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, os.fspath(home), failpoint],
        capture_output=True,
        check=False,
        shell=False,
    )

    assert result.returncode == 73
    version = _read_version(path)
    assert version in {REGISTRY_V1_SCHEMA_VERSION, REGISTRY_SCHEMA_VERSION}
    open_project_registry(home, create=False)
    assert _read_version(path) == REGISTRY_SCHEMA_VERSION
    assert _tables(path) == {
        "active_workpads": ACTIVE_WORKPAD_TABLE_SQL,
        "projects": PROJECT_TABLE_SQL,
        "workpads": WORKPAD_TABLE_SQL,
    }
    assert _project_rows(path) == PROJECT_ROWS
    assert not tuple(home.glob("*migrat*"))
    assert not tuple(home.glob("*.tmp"))


def test_two_concurrent_migrators_converge(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)
    script = (
        "from pathlib import Path; import sys; "
        "from gigai.registry import open_project_registry; "
        "open_project_registry(Path(sys.argv[1]), create=False)"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, os.fspath(home)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=20) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    assert _read_version(path) == REGISTRY_SCHEMA_VERSION
    assert _project_rows(path) == PROJECT_ROWS
    assert not tuple(home.glob("*migrat*"))


def test_conflicting_backup_is_never_overwritten(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _materialize_v1(home)
    backup = registry_backup_path(home)
    backup.write_bytes(b"conflicting backup\n")
    backup.chmod(0o600)
    before = backup.read_bytes()

    with pytest.raises(RegistryCorruptError, match="backup"):
        open_project_registry(home, create=False)

    assert backup.read_bytes() == before
    assert _read_version(registry_path(home)) == REGISTRY_V1_SCHEMA_VERSION


@pytest.mark.parametrize(
    "mutation",
    (
        "CREATE TABLE workpads (gig_id TEXT)",
        "CREATE TABLE active_workpads (project_id TEXT)",
        "CREATE TABLE unexpected (value TEXT)",
    ),
)
def test_partial_or_unexpected_v1_schema_never_migrates(
    tmp_path: Path, mutation: str
) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)
    connection = sqlite3.connect(path)
    try:
        connection.execute(mutation)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RegistryCorruptError, match="unexpected tables"):
        open_project_registry(home, create=False)

    assert not registry_backup_path(home).exists()
    assert _read_version(path) == REGISTRY_V1_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("project_id", "project_not-canonical"),
        ("target_locator", "relative/target"),
    ),
)
def test_malformed_populated_v1_rows_never_reach_backup_or_migration(
    tmp_path: Path, column: str, value: str
) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            f"UPDATE projects SET {column} = ? WHERE project_id = ?",
            (value, PROJECT_ROWS[0][0]),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RegistryCorruptError, match="invalid"):
        open_project_registry(home, create=False)

    assert not registry_backup_path(home).exists()
    assert _read_version(path) == REGISTRY_V1_SCHEMA_VERSION


@pytest.mark.parametrize("version", (0, 3, 99))
def test_unknown_versions_refuse_migration_and_backup(
    tmp_path: Path, version: int
) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA user_version = {version}")
    finally:
        connection.close()

    with pytest.raises(RegistryVersionError, match="no migration"):
        open_project_registry(home, create=False)

    assert not registry_backup_path(home).exists()


def test_legacy_v1_reader_refuses_live_v2_registry(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = _materialize_v1(home)
    open_project_registry(home, create=False)

    with pytest.raises(RegistryVersionError, match="no migration"):
        _legacy_v1_open(path)
