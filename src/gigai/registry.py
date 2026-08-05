"""Strict versioned user-local registry for target and workpad bindings."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Callable, Iterator

from .canonical import EntityPrefix, InvalidIdentifierError, validate_entity_id


REGISTRY_FILENAME = "registry.sqlite"
REGISTRY_BACKUP_FILENAME = "registry.sqlite.v1.bak"
REGISTRY_V1_SCHEMA_VERSION = 1
REGISTRY_SCHEMA_VERSION = 2
REGISTRY_APPLICATION_ID = 0x47494741
TARGET_KINDS = frozenset({"git", "non-git"})
PROJECT_TABLE_SQL = """\
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY NOT NULL,
    target_locator TEXT NOT NULL UNIQUE,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('git', 'non-git'))
) WITHOUT ROWID
"""
WORKPAD_TABLE_SQL = """\
CREATE TABLE workpads (
    gig_id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL,
    workpad_locator TEXT NOT NULL UNIQUE,
    UNIQUE (project_id, gig_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
) WITHOUT ROWID
"""
ACTIVE_WORKPAD_TABLE_SQL = """\
CREATE TABLE active_workpads (
    project_id TEXT PRIMARY KEY NOT NULL,
    gig_id TEXT NOT NULL,
    FOREIGN KEY (project_id, gig_id) REFERENCES workpads(project_id, gig_id)
) WITHOUT ROWID
"""
EXPECTED_REGISTRY_TABLES = frozenset(
    {"projects", "workpads", "active_workpads"}
)
MIGRATION_FAILPOINTS = (
    "before_backup_publish",
    "before_transaction",
    "after_workpads_table",
    "after_active_workpads_table",
    "before_version_write",
    "before_commit",
    "after_commit",
)
MigrationObserver = Callable[[str], None]


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


@dataclass(frozen=True)
class WorkpadRecord:
    gig_id: str
    project_id: str
    workpad_locator: str


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

    def find_workpad(self, gig_id: str) -> WorkpadRecord | None:
        row = self._connection.execute(
            "SELECT gig_id, project_id, workpad_locator "
            "FROM workpads WHERE gig_id = ?",
            (gig_id,),
        ).fetchone()
        return _workpad_record(row)

    def find_project_workpad(
        self, project_id: str, gig_id: str
    ) -> WorkpadRecord | None:
        row = self._connection.execute(
            "SELECT gig_id, project_id, workpad_locator FROM workpads "
            "WHERE project_id = ? AND gig_id = ?",
            (project_id, gig_id),
        ).fetchone()
        return _workpad_record(row)

    def insert_workpad(self, record: WorkpadRecord) -> None:
        _validate_workpad_record(record)
        try:
            self._connection.execute(
                "INSERT INTO workpads(gig_id, project_id, workpad_locator) "
                "VALUES (?, ?, ?)",
                (record.gig_id, record.project_id, record.workpad_locator),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "Gig ID, project binding, or canonical workpad locator conflicts"
            ) from exc

    def find_active_workpad(self, project_id: str) -> WorkpadRecord | None:
        row = self._connection.execute(
            "SELECT workpads.gig_id, workpads.project_id, workpads.workpad_locator "
            "FROM active_workpads JOIN workpads "
            "ON active_workpads.project_id = workpads.project_id "
            "AND active_workpads.gig_id = workpads.gig_id "
            "WHERE active_workpads.project_id = ?",
            (project_id,),
        ).fetchone()
        return _workpad_record(row)

    def select_active_workpad(self, project_id: str, gig_id: str) -> None:
        if self.find_project_workpad(project_id, gig_id) is None:
            raise RegistryConflictError(
                "active Gig must name an existing workpad for the same project"
            )
        self._connection.execute(
            "INSERT INTO active_workpads(project_id, gig_id) VALUES (?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET gig_id = excluded.gig_id",
            (project_id, gig_id),
        )


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

    def workpad_records(self) -> tuple[WorkpadRecord, ...]:
        connection = _connect(self.path)
        try:
            rows = connection.execute(
                "SELECT gig_id, project_id, workpad_locator FROM workpads "
                "ORDER BY project_id, gig_id"
            ).fetchall()
            return tuple(
                record
                for row in rows
                if (record := _workpad_record(row)) is not None
            )
        except sqlite3.DatabaseError as exc:
            raise RegistryCorruptError(f"registry read failed: {exc}") from exc
        finally:
            connection.close()


def registry_path(home_root: Path) -> Path:
    return home_root / REGISTRY_FILENAME


def registry_backup_path(home_root: Path) -> Path:
    return home_root / REGISTRY_BACKUP_FILENAME


def open_project_registry(
    home_root: Path,
    *,
    create: bool,
    migration_observer: MigrationObserver | None = None,
) -> tuple[ProjectRegistry, bool]:
    path = registry_path(home_root)
    created = False
    if not path.exists():
        if not create:
            raise RegistryCorruptError(f"registry is missing at {path}")
        _create_registry_atomic(path)
        created = True
    version = _validate_registry(path)
    if version == REGISTRY_V1_SCHEMA_VERSION:
        with _migration_lock(path):
            version = _validate_registry(path)
            if version == REGISTRY_V1_SCHEMA_VERSION:
                _migrate_registry_v1_to_v2(
                    path,
                    registry_backup_path(home_root),
                    migration_observer=migration_observer,
                )
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
            connection.execute(PROJECT_TABLE_SQL)
            connection.execute(WORKPAD_TABLE_SQL)
            connection.execute(ACTIVE_WORKPAD_TABLE_SQL)
            connection.execute(f"PRAGMA user_version = {REGISTRY_SCHEMA_VERSION}")
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


def _validate_registry(path: Path) -> int:
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
            actual = user_version[0] if user_version else None
            if actual not in {REGISTRY_V1_SCHEMA_VERSION, REGISTRY_SCHEMA_VERSION}:
                raise RegistryVersionError(
                    f"registry schema version {actual!r} is unsupported; expected "
                    f"{REGISTRY_SCHEMA_VERSION} or migratable predecessor "
                    f"{REGISTRY_V1_SCHEMA_VERSION}; no migration was attempted"
                )
            _validate_schema(connection, version=int(actual))
            return int(actual)
        finally:
            connection.close()
    except RegistryError:
        raise
    except sqlite3.DatabaseError as exc:
        raise RegistryCorruptError(f"registry is unreadable: {exc}") from exc


def _validate_schema(connection: sqlite3.Connection, *, version: int) -> None:
    expected_definitions = {"projects": PROJECT_TABLE_SQL}
    if version == REGISTRY_SCHEMA_VERSION:
        expected_definitions.update(
            {
                "workpads": WORKPAD_TABLE_SQL,
                "active_workpads": ACTIVE_WORKPAD_TABLE_SQL,
            }
        )
    expected_tables = set(expected_definitions)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if tables != expected_tables:
        raise RegistryCorruptError(
            f"registry has unexpected tables: {sorted(tables)}"
        )
    for table, expected_sql in expected_definitions.items():
        table_definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if table_definition != (expected_sql,):
            raise RegistryCorruptError(
                f"registry {table} table definition is not the versioned schema"
            )

    _validate_columns(
        connection,
        "projects",
        (
            ("project_id", "TEXT", 1, 1),
            ("target_locator", "TEXT", 1, 0),
            ("target_kind", "TEXT", 1, 0),
        ),
    )
    _require_unique_columns(connection, "projects", {("target_locator",)})
    for row in connection.execute(
        "SELECT project_id, target_locator, target_kind FROM projects"
    ):
        _record(row)

    if version == REGISTRY_SCHEMA_VERSION:
        _validate_columns(
            connection,
            "workpads",
            (
                ("gig_id", "TEXT", 1, 1),
                ("project_id", "TEXT", 1, 0),
                ("workpad_locator", "TEXT", 1, 0),
            ),
        )
        _require_unique_columns(
            connection,
            "workpads",
            {("workpad_locator",), ("project_id", "gig_id")},
        )
        _validate_foreign_keys(
            connection,
            "workpads",
            {("projects", "project_id", "project_id", "NO ACTION", "NO ACTION")},
        )
        _validate_columns(
            connection,
            "active_workpads",
            (
                ("project_id", "TEXT", 1, 1),
                ("gig_id", "TEXT", 1, 0),
            ),
        )
        _validate_foreign_keys(
            connection,
            "active_workpads",
            {
                ("workpads", "project_id", "project_id", "NO ACTION", "NO ACTION"),
                ("workpads", "gig_id", "gig_id", "NO ACTION", "NO ACTION"),
            },
        )
        for row in connection.execute(
            "SELECT gig_id, project_id, workpad_locator FROM workpads"
        ):
            _workpad_record(row)
        for project_id, gig_id in connection.execute(
            "SELECT project_id, gig_id FROM active_workpads"
        ):
            try:
                validate_entity_id(str(project_id), expected_prefix=EntityPrefix.PROJECT)
                validate_entity_id(str(gig_id), expected_prefix=EntityPrefix.GIG)
            except InvalidIdentifierError as exc:
                raise RegistryCorruptError(
                    "registry contains an invalid active project or Gig ID"
                ) from exc

    foreign_key_failures = tuple(connection.execute("PRAGMA foreign_key_check"))
    if foreign_key_failures:
        raise RegistryCorruptError(
            f"registry foreign-key integrity failed: {foreign_key_failures!r}"
        )

    unexpected_objects = tuple(
        row
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type != 'table' ORDER BY type, name"
        )
        if row[0] != "index" or row[3] is not None or row[2] not in expected_tables
    )
    if unexpected_objects:
        raise RegistryCorruptError(
            f"registry has unexpected schema objects: {unexpected_objects!r}"
        )


def _validate_columns(
    connection: sqlite3.Connection,
    table: str,
    expected: tuple[tuple[str, str, int, int], ...],
) -> None:
    columns = tuple(
        (row[1], row[2], row[3], row[5])
        for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    )
    if columns != expected:
        raise RegistryCorruptError(f"registry {table} table shape is invalid")


def _require_unique_columns(
    connection: sqlite3.Connection,
    table: str,
    required: set[tuple[str, ...]],
) -> None:
    indexes = tuple(
        connection.execute(f"PRAGMA index_list({_quote_identifier(table)})")
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
    missing = required - unique_columns
    if missing:
        raise RegistryCorruptError(
            f"registry {table} uniqueness constraints are missing: {sorted(missing)}"
        )


def _validate_foreign_keys(
    connection: sqlite3.Connection,
    table: str,
    expected: set[tuple[str, str, str, str, str]],
) -> None:
    actual = {
        (row[2], row[3], row[4], row[5], row[6])
        for row in connection.execute(
            f"PRAGMA foreign_key_list({_quote_identifier(table)})"
        )
    }
    if actual != expected:
        raise RegistryCorruptError(
            f"registry {table} foreign-key constraints are invalid"
        )


def _migrate_registry_v1_to_v2(
    path: Path,
    backup: Path,
    *,
    migration_observer: MigrationObserver | None,
) -> None:
    observer = migration_observer or (lambda _step: None)
    observer("before_backup_publish")
    _publish_v1_backup(path, backup)
    observer("before_transaction")
    connection = _connect(path)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        version_row = connection.execute("PRAGMA user_version").fetchone()
        version = int(version_row[0]) if version_row else -1
        if version == REGISTRY_SCHEMA_VERSION:
            _validate_schema(connection, version=version)
            connection.rollback()
            return
        if version != REGISTRY_V1_SCHEMA_VERSION:
            raise RegistryVersionError(
                f"registry schema version {version!r} changed during migration; "
                "no migration was attempted"
            )
        _validate_schema(connection, version=version)
        try:
            connection.execute(WORKPAD_TABLE_SQL)
            observer("after_workpads_table")
            connection.execute(ACTIVE_WORKPAD_TABLE_SQL)
            observer("after_active_workpads_table")
            observer("before_version_write")
            connection.execute(f"PRAGMA user_version = {REGISTRY_SCHEMA_VERSION}")
            observer("before_commit")
            connection.commit()
            observer("after_commit")
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()


@contextmanager
def _migration_lock(path: Path) -> Iterator[None]:
    """Serialize the v1 backup and schema transition across local processes."""

    lock_path = path.with_name(f".{path.name}.lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        with os.fdopen(descriptor, "r+b", closefd=True) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise RegistryPermissionError(
                    "registry migration lock must be a private regular file"
                )
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except RegistryError:
        raise
    except OSError as exc:
        raise RegistryPermissionError(
            f"registry migration lock is unavailable: {exc}"
        ) from exc


def _publish_v1_backup(path: Path, backup: Path) -> None:
    if backup.exists() or backup.is_symlink():
        _validate_existing_backup(path, backup)
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{backup.name}.", suffix=".tmp", dir=backup.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source = sqlite3.connect(path)
        destination = sqlite3.connect(temporary)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        os.chmod(temporary, 0o600)
        _validate_registry_version(temporary, REGISTRY_V1_SCHEMA_VERSION)
        if _project_values(temporary) != _project_values(path):
            raise RegistryCorruptError(
                "registry v1 backup does not preserve exact project rows"
            )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        try:
            os.link(temporary, backup)
        except FileExistsError:
            _validate_existing_backup(path, backup)
        directory_descriptor = os.open(backup.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except RegistryError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise RegistryPermissionError(
            f"registry v1 backup publication failed: {exc}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _validate_existing_backup(path: Path, backup: Path) -> None:
    try:
        _validate_registry_version(backup, REGISTRY_V1_SCHEMA_VERSION)
        if _project_values(backup) != _project_values(path):
            raise RegistryCorruptError(
                "registry v1 backup conflicts with the live v1 project rows"
            )
    except RegistryError as exc:
        raise RegistryCorruptError(
            f"registry v1 backup is conflicting or invalid: {exc}"
        ) from exc


def _validate_registry_version(path: Path, expected_version: int) -> None:
    actual = _validate_registry(path)
    if actual != expected_version:
        raise RegistryVersionError(
            f"registry backup schema version {actual} is unsupported; "
            f"expected {expected_version}; no migration was attempted"
        )


def _project_values(path: Path) -> tuple[tuple[object, ...], ...]:
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


def _workpad_record(
    row: tuple[object, ...] | sqlite3.Row | None,
) -> WorkpadRecord | None:
    if row is None:
        return None
    record = WorkpadRecord(
        gig_id=str(row[0]),
        project_id=str(row[1]),
        workpad_locator=str(row[2]),
    )
    _validate_workpad_record(record)
    return record


def _validate_workpad_record(record: WorkpadRecord) -> None:
    try:
        validate_entity_id(record.gig_id, expected_prefix=EntityPrefix.GIG)
        validate_entity_id(record.project_id, expected_prefix=EntityPrefix.PROJECT)
    except InvalidIdentifierError as exc:
        raise RegistryCorruptError(
            "registry contains an invalid project or Gig ID"
        ) from exc
    locator = Path(record.workpad_locator)
    if not locator.is_absolute() or "\0" in record.workpad_locator:
        raise RegistryCorruptError("registry contains an invalid workpad locator")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


__all__ = [
    "ACTIVE_WORKPAD_TABLE_SQL",
    "EXPECTED_REGISTRY_TABLES",
    "MIGRATION_FAILPOINTS",
    "PROJECT_TABLE_SQL",
    "ProjectRecord",
    "ProjectRegistry",
    "WorkpadRecord",
    "REGISTRY_APPLICATION_ID",
    "REGISTRY_BACKUP_FILENAME",
    "REGISTRY_FILENAME",
    "REGISTRY_SCHEMA_VERSION",
    "REGISTRY_V1_SCHEMA_VERSION",
    "RegistryConflictError",
    "RegistryCorruptError",
    "RegistryError",
    "RegistryPermissionError",
    "RegistryTransaction",
    "RegistryVersionError",
    "WORKPAD_TABLE_SQL",
    "open_project_registry",
    "registry_backup_path",
    "registry_path",
]
