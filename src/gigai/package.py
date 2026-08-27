"""Portable project-local GigAI package boundaries for G41."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import stat
import sqlite3
import subprocess
import tempfile
import tomllib
from typing import Any, Mapping
import uuid

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
    generate_entity_id,
    parse_json_bytes,
    validate_entity_id,
)
from .config import ConfigurationError, config_path, load_config, migrate_config
from .project_binding import binding_path, load_project_binding
from .target_binding import (
    TargetBindingError,
    TargetInitLock,
    initialize_target,
    resolve_target,
)
from .validators import validate_serialized_contract


PACKAGE_DIRECTORY = Path(".gigai") / "packages"
PACKAGE_MANIFEST = "package.json"
PRIVATE_EXCLUDE_LINES = (
    b"/.gigai/project.toml\n",
    b"/.gigai/local/\n",
    b"/.gigai/runs/\n",
    b"/.gigai/cache/\n",
    b"/.gigai/snapshots/\n",
    b"/.gigai/locks/\n",
)
ROOT_EXCLUDE_LINE = b"/.gigai/\n"
MAX_PACKAGE_FILE_BYTES = 4 * 1024 * 1024


class PackageError(ValueError):
    """A package boundary or migration operation failed closed."""

    def __init__(self, message: str, *, code: str = "package_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PackageInspection:
    package_id: str
    package_version: int
    package_root: Path
    manifest: Mapping[str, Any]
    content_digest: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class PackageInitResult:
    project_id: str
    package_id: str
    package_root: Path
    package_digest: str
    target_kind: str
    binding_created: bool
    registry_changed: bool
    exclude_changed: bool
    reconciled: bool
    adopted: bool


@dataclass(frozen=True)
class PackageInstallResult:
    package_id: str
    package_digest: str
    package_root: Path
    installation_record: Path
    status: str


@dataclass(frozen=True)
class PackageExportResult:
    package_id: str
    package_digest: str
    destination: Path
    status: str


@dataclass(frozen=True)
class UpgradeResult:
    package: PackageInitResult
    migration_record: Path
    source_config_digest: str
    destination_config_digest: str
    registry_preserved: bool


def package_root(target_root: Path, package_id: str) -> Path:
    return target_root / PACKAGE_DIRECTORY / package_id


def _reject_symlink_components(path: Path, *, label: str) -> None:
    """Reject every symlink component before a path is resolved or created."""

    candidate = path.expanduser()
    lexical = candidate if candidate.is_absolute() else Path.cwd() / candidate
    current = Path(lexical.anchor)
    for component in lexical.parts:
        if component == lexical.anchor:
            continue
        current /= component
        if current.is_symlink():
            raise PackageError(
                f"{label} contains a symlink component",
                code="symlink_refused",
            )


def inspect_package(root: Path) -> PackageInspection:
    candidate = root.expanduser()
    _reject_symlink_components(candidate, label="package root")
    package_root_path = candidate.resolve(strict=False)
    if not package_root_path.is_dir():
        raise PackageError("package root must be a regular directory", code="package_root_invalid")
    manifest_path = package_root_path / PACKAGE_MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PackageError("package manifest is missing or is not a regular file", code="manifest_missing")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise PackageError(f"package manifest is unreadable: {exc}", code="manifest_unreadable") from exc
    report = validate_serialized_contract("gig-package.schema.json", manifest_bytes)
    if not report.valid:
        raise PackageError(
            "package manifest failed schema validation: "
            + "; ".join(f"{item.location}: {item.message}" for item in report.findings),
            code="manifest_invalid",
        )
    manifest = parse_json_bytes(manifest_bytes)
    if not isinstance(manifest, Mapping):
        raise PackageError("package manifest must be an object", code="manifest_invalid")
    try:
        package_id = validate_entity_id(
            str(manifest["package_id"]), expected_prefix=EntityPrefix.PACKAGE
        )
    except (KeyError, ValueError) as exc:
        raise PackageError("package_id is not canonical", code="package_identity_invalid") from exc
    if package_root_path.name != package_id:
        raise PackageError(
            "package directory name does not match package_id",
            code="package_identity_invalid",
        )

    expected_files: list[dict[str, object]] = []
    for path in sorted(package_root_path.rglob("*")):
        relative = path.relative_to(package_root_path).as_posix()
        if relative == PACKAGE_MANIFEST:
            continue
        if path.is_symlink():
            raise PackageError(f"package contains symlink: {relative}", code="symlink_refused")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackageError(f"package contains unsupported file: {relative}", code="file_type_refused")
        if Path(relative).parts[0] in {"hooks", "install", "scripts"}:
            raise PackageError(f"package command material is refused: {relative}", code="hook_refused")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o111:
            raise PackageError(f"executable package material is refused: {relative}", code="executable_refused")
        data = path.read_bytes()
        if len(data) > MAX_PACKAGE_FILE_BYTES:
            raise PackageError(f"package file is too large: {relative}", code="file_too_large")
        expected_files.append(
            {
                "path": relative,
                "content_sha256": digest_imported_bytes(data),
                "size_bytes": len(data),
            }
        )
    listed = manifest["files"]
    if not isinstance(listed, list) or listed != expected_files:
        raise PackageError("package manifest file inventory does not match package bytes", code="content_inventory_mismatch")
    content_digest = canonical_json_digest(expected_files)
    if manifest["content_digest"] != content_digest:
        raise PackageError("package content digest does not match its inventory", code="content_digest_mismatch")
    return PackageInspection(
        package_id=package_id,
        package_version=int(manifest["package_version"]),
        package_root=package_root_path,
        manifest=manifest,
        content_digest=content_digest,
        files=tuple(item["path"] for item in expected_files),
    )


def export_package(*, source_package: Path, destination: Path) -> PackageExportResult:
    """Copy one validated portable package without importing authority."""

    inspection = inspect_package(source_package)
    source = inspection.package_root
    destination_candidate = destination.expanduser()
    _reject_symlink_components(destination_candidate, label="export destination")
    destination_path = destination_candidate.resolve(strict=False)
    try:
        destination_path.relative_to(source)
    except ValueError:
        pass
    else:
        raise PackageError("export destination cannot be inside its source package", code="path_escape")
    if destination_path.exists():
        existing = inspect_package(destination_path)
        if existing.content_digest != inspection.content_digest:
            raise PackageError(
                "export destination contains a different package",
                code="package_conflict",
            )
        return PackageExportResult(
            package_id=inspection.package_id,
            package_digest=inspection.content_digest,
            destination=destination_path,
            status="existing",
        )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    _copy_package(source, destination_path)
    inspect_package(destination_path)
    return PackageExportResult(
        package_id=inspection.package_id,
        package_digest=inspection.content_digest,
        destination=destination_path,
        status="exported",
    )


def initialize_project_package(
    *,
    home_root: Path,
    requested_target: Path | None,
    adopt_package: bool = False,
    confirmed: bool = False,
    uuid_factory: Any = uuid.uuid4,
) -> PackageInitResult:
    """Bind a target, cut over ignores, and establish one portable package."""

    if adopt_package and not confirmed:
        raise PackageError("package adoption requires direct --confirm", code="confirmation_required")
    home = home_root.expanduser().resolve(strict=False)
    try:
        load_config(home)
    except ConfigurationError as exc:
        raise PackageError(str(exc), code="configuration_invalid") from exc
    target = resolve_target(requested_target)
    if target.kind == "git":
        if adopt_package:
            _validate_adoption_tree(target.root)
        else:
            tracked = _git_paths(target.root)
            if tracked and not binding_path(target.root).is_file():
                raise PackageError(
                    "tracked portable package requires explicit --adopt-package --confirm",
                    code="adoption_required",
                )
            _validate_existing_packages(target.root)
    lock = (
        TargetInitLock(target.root / ".git" / "gigai-package.lock")
        if target.kind == "git"
        else None
    )
    if lock is None:
        binding = _initialize_and_prepare(
            home=home,
            requested_target=requested_target,
            target=target,
            adopt_package=adopt_package,
            uuid_factory=uuid_factory,
        )
    else:
        with lock:
            binding = _initialize_and_prepare(
                home=home,
                requested_target=requested_target,
                target=target,
                adopt_package=adopt_package,
                uuid_factory=uuid_factory,
            )
    return binding


def _initialize_and_prepare(
    *,
    home: Path,
    requested_target: Path | None,
    target: Any,
    adopt_package: bool,
    uuid_factory: Any,
) -> PackageInitResult:
    try:
        binding = initialize_target(
            home_root=home,
            requested_target=requested_target,
            allow_tracked_portable=adopt_package or binding_path(target.root).is_file(),
            uuid_factory=uuid_factory,
        )
    except TargetBindingError as exc:
        raise PackageError(str(exc), code="target_binding_failed") from exc
    exclude_changed = False
    if target.kind == "git":
        exclude_changed = _cutover_git_exclude(target.root)
    roots = _package_roots(target.root)
    inspections = tuple(inspect_package(root) for root in roots)
    if adopt_package:
        if len(inspections) != 1:
            raise PackageError("package adoption requires exactly one portable package", code="adoption_package_count")
        inspection = inspections[0]
        adopted = True
    elif inspections:
        inspection = inspections[0]
        adopted = False
    else:
        package_id = generate_entity_id(
            EntityPrefix.PACKAGE,
            is_persisted=lambda candidate: (target.root / PACKAGE_DIRECTORY / candidate).exists(),
            uuid_factory=uuid_factory,
        )
        root = package_root(target.root, package_id)
        root.mkdir(parents=True, exist_ok=False)
        manifest = {
            "schema_version": "1.0",
            "package_id": package_id,
            "package_version": 1,
            "project_scope": "repository",
            "content_digest": canonical_json_digest([]),
            "files": [],
        }
        _write_atomic(root / PACKAGE_MANIFEST, canonical_json_bytes(manifest))
        inspection = inspect_package(root)
        adopted = False
    return PackageInitResult(
        project_id=binding.project_id,
        package_id=inspection.package_id,
        package_root=inspection.package_root,
        package_digest=inspection.content_digest,
        target_kind=binding.target_kind,
        binding_created=binding.binding_created,
        registry_changed=binding.registry_changed,
        exclude_changed=exclude_changed or binding.exclude_changed,
        reconciled=binding.reconciled,
        adopted=adopted,
    )


def install_package(
    *,
    home_root: Path,
    requested_target: Path,
    source_package: Path,
) -> PackageInstallResult:
    """Install validated portable bytes without importing project authority."""

    resolved_home = home_root.expanduser().resolve(strict=False)
    try:
        config = load_config(resolved_home)
    except ConfigurationError as exc:
        raise PackageError(str(exc), code="configuration_invalid") from exc
    if config.home_root.resolve(strict=False) != resolved_home:
        raise PackageError(
            "configuration belongs to a different GigAI home",
            code="configuration_home_conflict",
        )
    inspection = inspect_package(source_package)
    target = resolve_target(requested_target)
    if target.kind == "git":
        _cutover_git_exclude(target.root)
    destination = package_root(target.root, inspection.package_id)
    if destination.exists():
        existing = inspect_package(destination)
        if existing.content_digest != inspection.content_digest:
            raise PackageError("destination package identity has a different digest", code="package_conflict")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_package(inspection.package_root, destination)
        inspect_package(destination)
    record_dir = config.home_root / "local" / "package-installations"
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / f"{inspection.package_id}-{inspection.content_digest[7:]}.json"
    record = {
        "schema_version": "1.0",
        "package_id": inspection.package_id,
        "content_digest": inspection.content_digest,
        "source_revision": f"package:{inspection.package_id}:v{inspection.package_version}",
        "destination_home": os.fspath(config.home_root),
        "installation_status": "installed",
        "requirements": [],
        "installed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if record_path.exists():
        existing = parse_json_bytes(record_path.read_bytes())
        if existing != record:
            record["installed_at"] = existing.get("installed_at", record["installed_at"])
            _write_atomic(record_path, canonical_json_bytes(record))
    else:
        _write_atomic(record_path, canonical_json_bytes(record))
    return PackageInstallResult(
        package_id=inspection.package_id,
        package_digest=inspection.content_digest,
        package_root=destination,
        installation_record=record_path,
        status="installed",
    )


def upgrade_installation(
    *,
    home_root: Path,
    requested_target: Path | None,
    confirmed: bool = False,
) -> UpgradeResult:
    """Migrate a supported predecessor and publish the project package boundary."""

    if not confirmed:
        raise PackageError("upgrade requires direct --confirm", code="confirmation_required")
    home = home_root.expanduser().resolve(strict=False)
    path = config_path(home)
    if not path.is_file():
        raise PackageError("configuration is missing; run 'gigai setup'", code="configuration_missing")
    config_before = path.read_bytes()
    source_config_digest = digest_imported_bytes(config_before)
    version = _config_version(config_before)
    workpad_root = _configured_workpad_root(config_before)
    workpad_fingerprint_before = _tree_fingerprint(workpad_root)
    private_home_fingerprint_before = _private_home_fingerprint(home)
    try:
        target = resolve_target(requested_target)
        gigai_directory_before = (target.root / ".gigai").exists()
        package_roots_before = {
            root.name for root in _package_roots(target.root)
        }
        binding_before = (
            binding_path(target.root).read_bytes()
            if binding_path(target.root).is_file()
            else None
        )
        exclude_path = (
            target.root / ".git" / "info" / "exclude"
            if target.kind == "git"
            else None
        )
        exclude_before = (
            exclude_path.read_bytes()
            if exclude_path is not None and exclude_path.is_file()
            else None
        )
    except (OSError, TargetBindingError, PackageError, ValueError) as exc:
        raise PackageError(str(exc), code="upgrade_preflight_failed") from exc
    migration_root = home / "local" / "migrations" / "v0.1.6-to-v0.1.7"
    backup = migration_root / "config.toml.v0.1.6.bak"
    if version == "1.0":
        if backup.exists():
            if backup.read_bytes() != config_before:
                raise PackageError("v0.1.6 configuration backup conflicts", code="backup_conflict")
        else:
            _write_atomic(backup, config_before)
    registry_path = home / "registry.sqlite"
    registry_before = registry_path.read_bytes() if registry_path.exists() else None
    registry_fingerprint_before = _registry_fingerprint(registry_path)
    try:
        migrate_config(home)
        package = initialize_project_package(
            home_root=home,
            requested_target=target.requested_path,
        )
    except (ConfigurationError, OSError, TargetBindingError, PackageError) as exc:
        _restore_upgrade_state(
            config_path=path,
            config_before=config_before,
            registry_path=registry_path,
            registry_before=registry_before,
            target=target,
            gigai_directory_before=gigai_directory_before,
            package_roots_before=package_roots_before,
            binding_before=binding_before,
            exclude_path=exclude_path,
            exclude_before=exclude_before,
        )
        raise PackageError(str(exc), code="upgrade_failed") from exc
    try:
        return _finalize_upgrade(
            path=path,
            migration_root=migration_root,
            package=package,
            source_config_digest=source_config_digest,
            registry_path=registry_path,
            registry_before=registry_before,
            registry_fingerprint_before=registry_fingerprint_before,
            workpad_root=workpad_root,
            workpad_fingerprint_before=workpad_fingerprint_before,
            private_home_fingerprint_before=private_home_fingerprint_before,
        )
    except (ConfigurationError, OSError, PackageError) as exc:
        _restore_upgrade_state(
            config_path=path,
            config_before=config_before,
            registry_path=registry_path,
            registry_before=registry_before,
            target=target,
            gigai_directory_before=gigai_directory_before,
            package_roots_before=package_roots_before,
            binding_before=binding_before,
            exclude_path=exclude_path,
            exclude_before=exclude_before,
        )
        raise PackageError(str(exc), code="upgrade_failed") from exc


def _finalize_upgrade(
    *,
    path: Path,
    migration_root: Path,
    package: PackageInitResult,
    source_config_digest: str,
    registry_path: Path,
    registry_before: bytes | None,
    registry_fingerprint_before: str | None,
    workpad_root: Path,
    workpad_fingerprint_before: str | None,
    private_home_fingerprint_before: str | None,
) -> UpgradeResult:
    config_after = path.read_bytes()
    registry_after = registry_path.read_bytes() if registry_path.exists() else None
    if registry_before is not None and registry_after is None:
        raise PackageError("registry disappeared during upgrade", code="preservation_failed")
    registry_fingerprint_after = _registry_fingerprint(registry_path)
    workpad_fingerprint_after = _tree_fingerprint(workpad_root)
    private_home_fingerprint_after = _private_home_fingerprint(path.parent)
    if registry_before is not None and registry_fingerprint_before != registry_fingerprint_after:
        raise PackageError(
            "registry identities or authority links changed during upgrade",
            code="preservation_failed",
        )
    if workpad_fingerprint_before != workpad_fingerprint_after:
        raise PackageError(
            "workpad identities or journal evidence changed during upgrade",
            code="preservation_failed",
        )
    if private_home_fingerprint_before != private_home_fingerprint_after:
        raise PackageError(
            "private home identities or capability state changed during upgrade",
            code="preservation_failed",
        )
    record = {
        "schema_version": "1.0",
        "migration": "v0.1.6-to-v0.1.7",
        "source_config_digest": source_config_digest,
        "destination_config_digest": digest_imported_bytes(config_after),
        "package_id": package.package_id,
        "package_digest": package.package_digest,
        "registry_present_before": registry_before is not None,
        "registry_present_after": registry_after is not None,
        "registry_fingerprint": registry_fingerprint_after,
        "workpad_fingerprint": workpad_fingerprint_after,
        "private_home_fingerprint": private_home_fingerprint_after,
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    record_path = migration_root / "migration.json"
    if record_path.exists():
        existing = parse_json_bytes(record_path.read_bytes())
        if not isinstance(existing, Mapping) or existing.get("package_id") != package.package_id:
            raise PackageError("migration record conflicts with existing package", code="migration_conflict")
        record = dict(existing)
        record["package_digest"] = package.package_digest
        record["registry_present_after"] = registry_after is not None
    _write_atomic(record_path, canonical_json_bytes(record))
    recorded_source_digest = str(record["source_config_digest"])
    recorded_destination_digest = str(record["destination_config_digest"])
    return UpgradeResult(
        package=package,
        migration_record=record_path,
        source_config_digest=recorded_source_digest,
        destination_config_digest=recorded_destination_digest,
        registry_preserved=(
            registry_before is None
            or (
                registry_after is not None
                and registry_fingerprint_before == registry_fingerprint_after
            )
        ),
    )


def _registry_fingerprint(path: Path) -> str | None:
    """Hash logical registry identities, excluding SQLite layout/version bytes."""

    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise PackageError("registry is not a regular file", code="preservation_failed")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        records: dict[str, list[list[object]]] = {}
        for table, query in (
            ("projects", "SELECT project_id, target_locator, target_kind FROM projects ORDER BY project_id"),
            ("workpads", "SELECT gig_id, project_id, workpad_locator FROM workpads ORDER BY project_id, gig_id"),
            ("active_workpads", "SELECT project_id, gig_id FROM active_workpads ORDER BY project_id, gig_id"),
        ):
            if table in tables:
                records[table] = [list(row) for row in connection.execute(query)]
        return canonical_json_digest(records)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise PackageError(f"registry preservation inventory failed: {exc}", code="preservation_failed") from exc
    finally:
        if connection is not None:
            connection.close()


def _restore_upgrade_state(
    *,
    config_path: Path,
    config_before: bytes,
    registry_path: Path,
    registry_before: bytes | None,
    target: Any,
    gigai_directory_before: bool,
    package_roots_before: set[str],
    binding_before: bytes | None,
    exclude_path: Path | None,
    exclude_before: bytes | None,
) -> None:
    """Restore only files and package roots this upgrade could have created."""

    _write_atomic(config_path, config_before)
    if registry_before is None:
        if registry_path.exists():
            registry_path.unlink()
    elif registry_path.read_bytes() != registry_before:
        _write_atomic(registry_path, registry_before)

    current_roots = _package_roots(target.root)
    for root in current_roots:
        if root.name in package_roots_before:
            continue
        if root.is_symlink():
            root.unlink()
        else:
            shutil.rmtree(root)
    binding = binding_path(target.root)
    if binding_before is None:
        if binding.exists():
            binding.unlink()
    elif binding.read_bytes() != binding_before:
        _write_atomic(binding, binding_before)
    gigai_directory = target.root / ".gigai"
    packages_directory = gigai_directory / "packages"
    if not gigai_directory_before:
        if packages_directory.is_dir() and not packages_directory.is_symlink():
            packages_directory.rmdir()
        if gigai_directory.is_dir() and not gigai_directory.is_symlink():
            gigai_directory.rmdir()
    if exclude_path is not None:
        if exclude_before is None:
            if exclude_path.exists():
                exclude_path.unlink()
        elif exclude_path.read_bytes() != exclude_before:
            _write_atomic(exclude_path, exclude_before)


def _configured_workpad_root(data: bytes) -> Path:
    try:
        payload = tomllib.loads(data.decode("utf-8"))
        paths = payload.get("paths")
        if not isinstance(paths, Mapping):
            paths = payload.get("configuration", {}).get("paths")
        if not isinstance(paths, Mapping) or not isinstance(paths.get("workpad_root"), str):
            raise ValueError
        root = Path(paths["workpad_root"]).expanduser()
        if not root.is_absolute():
            raise ValueError
        return root.resolve(strict=False)
    except (UnicodeError, tomllib.TOMLDecodeError, TypeError, ValueError, AttributeError) as exc:
        raise PackageError(
            "configuration workpad root cannot be inventoried",
            code="preservation_failed",
        ) from exc


def _tree_fingerprint(root: Path) -> str | None:
    """Hash private file identities and bytes without recording their contents."""

    if not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise PackageError("workpad root is not a regular directory", code="preservation_failed")
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise PackageError(
                f"workpad inventory refuses symlink: {relative}",
                code="preservation_failed",
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackageError(
                f"workpad inventory refuses unsupported file: {relative}",
                code="preservation_failed",
            )
        data = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "content_sha256": digest_imported_bytes(data),
                "size_bytes": len(data),
            }
        )
    return canonical_json_digest(entries)


def _private_home_fingerprint(root: Path) -> str | None:
    """Hash existing private home files while excluding migration outputs."""

    if not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise PackageError("GigAI home is not a regular directory", code="preservation_failed")
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in {"config.toml", "registry.sqlite", "registry.sqlite.v1.bak"}:
            continue
        if relative == "local/migrations" or relative.startswith("local/migrations/"):
            continue
        if path.is_symlink():
            raise PackageError(
                f"private home inventory refuses symlink: {relative}",
                code="preservation_failed",
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackageError(
                f"private home inventory refuses unsupported file: {relative}",
                code="preservation_failed",
            )
        data = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "content_sha256": digest_imported_bytes(data),
                "size_bytes": len(data),
            }
        )
    return canonical_json_digest(entries)


def _config_version(data: bytes) -> str:
    try:
        payload = tomllib.loads(data.decode("utf-8"))
        version = payload.get("schema_version")
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PackageError("configuration predecessor is malformed", code="configuration_malformed") from exc
    if not isinstance(version, str):
        raise PackageError("configuration schema version is missing", code="configuration_version_missing")
    return version


def _package_roots(target_root: Path) -> tuple[Path, ...]:
    parent = target_root / PACKAGE_DIRECTORY
    if not parent.exists():
        return ()
    if parent.is_symlink() or not parent.is_dir():
        raise PackageError("portable package directory is invalid", code="package_directory_invalid")
    roots = []
    for child in sorted(parent.iterdir()):
        if child.is_symlink() or not child.is_dir():
            raise PackageError(f"unexpected package entry: {child.name}", code="package_entry_invalid")
        roots.append(child)
    return tuple(roots)


def _validate_adoption_tree(root: Path) -> None:
    tracked = _git_paths(root)
    for path in tracked:
        if not path.startswith(".gigai/packages/"):
            raise PackageError(
                "package adoption refuses tracked private or unknown .gigai content",
                code="tracked_private_refused",
            )
    binding = binding_path(root)
    if binding.exists():
        try:
            load_project_binding(root)
        except ValueError as exc:
            raise PackageError(str(exc), code="binding_invalid") from exc
    roots = _package_roots(root)
    if len(roots) != 1:
        raise PackageError("package adoption requires exactly one portable package", code="adoption_package_count")
    inspect_package(roots[0])


def _validate_existing_packages(root: Path) -> None:
    """Validate existing portable packages before target-binding mutation."""

    roots = _package_roots(root)
    if len(roots) > 1:
        raise PackageError(
            "project contains multiple portable packages; choose one explicitly",
            code="package_ambiguous",
        )
    if roots:
        inspect_package(roots[0])


def _git_paths(root: Path) -> tuple[str, ...]:
    executable = shutil.which("git")
    if executable is None:
        raise PackageError("Git executable is unavailable", code="git_inspection_failed")
    try:
        result = subprocess.run(
            [executable, "-C", os.fspath(root), "ls-files", "-z", "--", ".gigai"],
            check=True,
            capture_output=True,
            shell=False,
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackageError("could not inspect tracked .gigai content", code="git_inspection_failed") from exc
    return tuple(item.decode("utf-8") for item in result.stdout.split(b"\0") if item)


def _cutover_git_exclude(root: Path) -> bool:
    exclude = root / ".git" / "info" / "exclude"
    before = exclude.read_bytes() if exclude.exists() else b""
    lines = before.splitlines(keepends=True)
    root_matches = [line for line in lines if line.rstrip(b"\r\n") == ROOT_EXCLUDE_LINE.rstrip(b"\n")]
    if len(root_matches) > 1:
        raise PackageError("Git exclude has duplicate v0.1.6 whole-directory rules", code="exclude_ambiguous")
    private = {line.rstrip(b"\r\n") for line in PRIVATE_EXCLUDE_LINES}
    private_by_key = {
        line.rstrip(b"\r\n"): line for line in PRIVATE_EXCLUDE_LINES
    }
    output: list[bytes] = []
    replaced = False
    seen_private: set[bytes] = set()
    for line in lines:
        if line.rstrip(b"\r\n") == ROOT_EXCLUDE_LINE.rstrip(b"\n"):
            if not replaced:
                for private_line in PRIVATE_EXCLUDE_LINES:
                    key = private_line.rstrip(b"\r\n")
                    if key not in seen_private:
                        output.append(private_line)
                        seen_private.add(key)
                replaced = True
            continue
        key = line.rstrip(b"\r\n")
        if key in private:
            if key not in seen_private:
                output.append(private_by_key[key])
                seen_private.add(key)
            continue
        output.append(line)
    if not replaced:
        separator = b"" if not output or output[-1].endswith((b"\n", b"\r")) else b"\n"
        if separator:
            output.append(separator)
        for line in PRIVATE_EXCLUDE_LINES:
            key = line.rstrip(b"\r\n")
            if key not in seen_private:
                output.append(line)
                seen_private.add(key)
    else:
        missing = [line for line in PRIVATE_EXCLUDE_LINES if line.rstrip(b"\r\n") not in seen_private]
        output.extend(missing)
    after = b"".join(output)
    if after == before:
        return False
    _write_atomic(exclude, after)
    if exclude.read_bytes() != after:
        raise PackageError("Git exclude replacement could not be verified", code="exclude_verification_failed")
    observed_lines = exclude.read_bytes().splitlines()
    if observed_lines.count(ROOT_EXCLUDE_LINE.rstrip(b"\n")) != 0 or any(
        observed_lines.count(line.rstrip(b"\n")) != 1 for line in PRIVATE_EXCLUDE_LINES
    ):
        raise PackageError("Git exclude replacement is incomplete", code="exclude_verification_failed")
    return True


def _copy_package(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_symlink() or (item.is_file() and stat.S_IMODE(item.stat().st_mode) & 0o111):
            shutil.rmtree(destination, ignore_errors=True)
            raise PackageError("package copy encountered unsafe material", code="package_copy_refused")
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "PACKAGE_DIRECTORY",
    "PACKAGE_MANIFEST",
    "PackageError",
    "PackageInitResult",
    "PackageInspection",
    "PackageInstallResult",
    "PackageExportResult",
    "UpgradeResult",
    "initialize_project_package",
    "inspect_package",
    "export_package",
    "install_package",
    "upgrade_installation",
    "package_root",
]
