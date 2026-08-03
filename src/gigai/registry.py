"""Minimal versioned user-local registry for target bindings."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Iterator

from .canonical import EntityPrefix, InvalidIdentifierError, validate_entity_id


REGISTRY_FILENAME = "registry.sqlite"
REGISTRY_SCHEMA_VERSION = 1
REGISTRY_APPLICATION_ID = 0x47494741
TARGET_KINDS = frozenset({"git", "non-git"})
PROJECT_TABLE_SQL = """\
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY NOT NULL,
    target_locator TEXT NOT NULL UNIQUE,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('git', 'non-git'))
) WITHOUT ROWID
"""


class RegistryError(RuntimeError):
    code = "registry_error"


class RegistryCorruptError(RegistryError):
    code = "registry_corrupt"


class RegistryVersionError(RegistryError):
    code = "registry_version_unsupported"


class RegistryConflictError(RegistryError):
    code = "registry_conflict"


class RegistryPermissionError(RegistryError):
    code = "registry_permission_denied"


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    target_locator: str
    target_kind: str


class RegistryTransaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def find_project(self, project_id: str) -> ProjectRecord | None:
        row = self._connection.execute(
            "SELECT project_id, target_locator, target_kind "
            "FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return _record(row)

    def find_target(self, target: Path) -> ProjectRecord | None:
        locator = os.fspath(target)
        row = self._connection.execute(
            "SELECT project_id, target_locator, target_kind "
            "FROM projects WHERE target_locator = ?",
            (locator,),
        ).fetchone()
        direct = _record(row)
        if direct is not None:
            return direct
        for candidate_row in self._connection.execute(
            "SELECT project_id, target_locator, target_kind FROM projects "
            "ORDER BY project_id"
        ):
            candidate = _record(candidate_row)
            assert candidate is not None
            stored = Path(candidate.target_locator)
            try:
                if stored.exists() and os.path.samefile(stored, target):
                    return candidate
            except OSError:
                continue
        return None

    def insert(self, record: ProjectRecord) -> None:
        _validate_record(record)
        try:
            self._connection.execute(
                "INSERT INTO projects(project_id, target_locator, target_kind) "
                "VALUES (?, ?, ?)",
                (record.project_id, record.target_locator, record.target_kind),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "project ID or canonical target locator is already registered"
            ) from exc

    def count(self) -> int:
        row = self._connection.execute("SELECT count(*) FROM projects").fetchone()
        assert row is not None
        return int(row[0])


class ProjectRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def transaction(self) -> Iterator[RegistryTransaction]:
        connection = _connect(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            transaction = RegistryTransaction(connection)
            yield transaction
            connection.commit()
        except RegistryError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RegistryCorruptError(f"registry transaction failed: {exc}") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def records(self) -> tuple[ProjectRecord, ...]:
        connection = _connect(self.path)
        try:
            rows = connection.execute(
                "SELECT project_id, target_locator, target_kind FROM projects "
                "ORDER BY project_id"
            ).fetchall()
            return tuple(record for row in rows if (record := _record(row)) is not None)
        except sqlite3.DatabaseError as exc:
            raise RegistryCorruptError(f"registry read failed: {exc}") from exc
        finally:
            connection.close()


def registry_path(home_root: Path) -> Path:
    return home_root / REGISTRY_FILENAME


def open_project_registry(home_root: Path, *, create: bool) -> tuple[ProjectRegistry, bool]:
    path = registry_path(home_root)
    created = False
    if not path.exists():
        if not create:
            raise RegistryCorruptError(f"registry is missing at {path}")
        _create_registry_atomic(path)
        created = True
    _validate_registry(path)
    return ProjectRegistry(path), created


def _create_registry_atomic(path: Path) -> None:
    if not path.parent.is_dir():
        raise RegistryPermissionError(f"registry parent is unavailable: {path.parent}")
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    if parent_mode & 0o222 == 0:
        raise RegistryPermissionError(f"registry parent is read-only: {path.parent}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(f"PRAGMA application_id = {REGISTRY_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {REGISTRY_SCHEMA_VERSION}")
            connection.execute(PROJECT_TABLE_SQL)
            connection.commit()
        finally:
            connection.close()
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise RegistryPermissionError(f"registry creation failed: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _validate_registry(path: Path) -> None:
    if path.is_symlink():
        raise RegistryCorruptError("registry path must not be a symlink")
    try:
        info = path.stat()
    except OSError as exc:
        raise RegistryCorruptError(f"registry path is unavailable: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise RegistryCorruptError(f"registry path is not a regular file: {path}")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise RegistryPermissionError(
            "registry permissions expose private target locators; require mode 0600"
        )
    if mode != 0o600:
        raise RegistryPermissionError(
            "registry is read-only or has unsupported permissions; require mode 0600"
        )
    try:
        connection = _connect(path)
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check != ("ok",):
                raise RegistryCorruptError("registry integrity check did not return ok")
            application_id = connection.execute("PRAGMA application_id").fetchone()
            user_version = connection.execute("PRAGMA user_version").fetchone()
            if application_id != (REGISTRY_APPLICATION_ID,):
                raise RegistryCorruptError("registry application identity is invalid")
            if user_version != (REGISTRY_SCHEMA_VERSION,):
                actual = user_version[0] if user_version else None
                raise RegistryVersionError(
                    f"registry schema version {actual!r} is unsupported; expected "
                    f"{REGISTRY_SCHEMA_VERSION}; no migration was attempted"
                )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if tables != {"projects"}:
                raise RegistryCorruptError(
                    f"registry has unexpected tables: {sorted(tables)}"
                )
            table_definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
            ).fetchone()
            if table_definition != (PROJECT_TABLE_SQL,):
                raise RegistryCorruptError(
                    "registry projects table definition is not the versioned schema"
                )
            columns = tuple(
                (row[1], row[2], row[3], row[5])
                for row in connection.execute("PRAGMA table_info(projects)")
            )
            if columns != (
                ("project_id", "TEXT", 1, 1),
                ("target_locator", "TEXT", 1, 0),
                ("target_kind", "TEXT", 1, 0),
            ):
                raise RegistryCorruptError("registry projects table shape is invalid")
            indexes = tuple(
                row for row in connection.execute("PRAGMA index_list(projects)")
            )
            unique_columns = {
                tuple(
                    item[2]
                    for item in connection.execute(
                        f"PRAGMA index_info({_quote_identifier(row[1])})"
                    )
                )
                for row in indexes
                if row[2] == 1
            }
            if ("target_locator",) not in unique_columns:
                raise RegistryCorruptError(
                    "registry target_locator uniqueness constraint is missing"
                )
        finally:
            connection.close()
    except (RegistryError, RegistryVersionError):
        raise
    except sqlite3.DatabaseError as exc:
        raise RegistryCorruptError(f"registry is unreadable: {exc}") from exc


def _connect(path: Path) -> sqlite3.Connection:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o222 == 0:
        raise RegistryPermissionError(f"registry is read-only: {path}")
    try:
        connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection
    except sqlite3.DatabaseError as exc:
        raise RegistryCorruptError(f"registry cannot be opened: {exc}") from exc


def _record(row: tuple[object, ...] | sqlite3.Row | None) -> ProjectRecord | None:
    if row is None:
        return None
    record = ProjectRecord(
        project_id=str(row[0]),
        target_locator=str(row[1]),
        target_kind=str(row[2]),
    )
    _validate_record(record)
    return record


def _validate_record(record: ProjectRecord) -> None:
    try:
        validate_entity_id(record.project_id, expected_prefix=EntityPrefix.PROJECT)
    except InvalidIdentifierError as exc:
        raise RegistryCorruptError("registry contains an invalid project ID") from exc
    target = Path(record.target_locator)
    if not target.is_absolute() or "\0" in record.target_locator:
        raise RegistryCorruptError("registry contains an invalid target locator")
    if record.target_kind not in TARGET_KINDS:
        raise RegistryCorruptError("registry contains an invalid target kind")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


__all__ = [
    "ProjectRecord",
    "ProjectRegistry",
    "RegistryConflictError",
    "RegistryCorruptError",
    "RegistryError",
    "RegistryPermissionError",
    "RegistryTransaction",
    "RegistryVersionError",
    "REGISTRY_APPLICATION_ID",
    "REGISTRY_FILENAME",
    "REGISTRY_SCHEMA_VERSION",
    "open_project_registry",
    "registry_path",
]
