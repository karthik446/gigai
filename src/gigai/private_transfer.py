"""Safe, explicit Scout definition export and private-history transfer.

The two archive formats are intentionally unrelated.  A definition archive is
portable template material only; a private transfer is an operator-confirmed
copy of one Gig's journaled history.  Neither archive grants approval, changes
an active selection, or contains provider credentials/configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
from importlib import resources
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Iterable, Mapping
import uuid
import zipfile

from jsonschema import Draft202012Validator

from .canonical import EntityPrefix, canonical_json_bytes, digest_imported_bytes, parse_json_bytes, validate_entity_id
from .journal import JournalConflictError, run_with_journal_writer
from .journal import JournalArtifact, record_transition
from .private_records import migrate_workpad_layout
from .project_binding import load_project_binding
from .scout.template import scout_source_files
from .workpad import provision_workpad, resolve_bound_project


class PrivateTransferError(RuntimeError):
    """Typed refusal for unsafe archives or ambiguous destinations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TransferResult:
    archive: Path
    kind: str
    files: tuple[str, ...]
    project_id: str | None = None
    gig_id: str | None = None


@dataclass(frozen=True)
class LocalBindingResult:
    """A restored private tree bound to a fresh local journal substrate."""

    project_id: str
    gig_id: str
    workpad: Path
    imported_files: tuple[str, ...]
    journal_handoff_id: str
    active_selection: bool


DEFINITION_KIND = "scout_definition_export"
PRIVATE_KIND = "scout_private_transfer"
DEFINITION_SCHEMA_VERSION = "1.0"
PRIVATE_SCHEMA_VERSION = "1.0"
_MANIFEST_NAME = "manifest.json"
_MAX_FILES = 4096
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 1000
_DEFINITION_ROOTS = {"README.md", "CHANGELOG.md", "gig.py", "goalgraphs", "ui", "tools"}
_PRIVATE_ROOTS = {"README.md", "CHANGELOG.md", "gig.py", "tools", "goalgraphs", "ui", "docs", "references", "run-inputs", "records", "runs", "run-plans", "review-inputs", "handoffs"}
# A transfer is deliberately conservative about local configuration.  This is
# an exclusion inventory, not a claim that arbitrary prose can be classified
# safely by filename or content.
_SECRET_PARTS = {"credentials", "secrets", "tokens", "token", "password", "private-key", "private_key", ".git"}
_CONFIG_BASENAMES = {"config", "config.json", "config.toml", "config.yaml", "config.yml", "settings", "settings.json", "settings.toml", "settings.yaml", "settings.yml"}
_SECURE_DIR_FD_FUNCTIONS = (os.open, os.mkdir, os.stat, os.unlink, os.rmdir)


def _reject(code: str, message: str) -> None:
    raise PrivateTransferError(code, message)


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _reject("archive_path_unsafe", "archive member path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _reject("archive_path_unsafe", "archive member path escapes its destination")
    if any(part.startswith("~") for part in path.parts):
        _reject("archive_path_unsafe", "archive member path is ambiguous")
    return str(path)


def _secret_path(path: str) -> bool:
    parts = tuple(part.lower() for part in PurePosixPath(path).parts)
    for part in parts:
        if part.startswith(".env") or part in _SECRET_PARTS or part in _CONFIG_BASENAMES:
            return True
        if part.endswith((".pem", ".key", ".p12", ".pfx", ".crt")):
            return True
    return False


def _check_portable_links(relative: str, data: bytes) -> None:
    """Reject operational links to a source machine without rewriting history.

    JSON object fields that conventionally carry a path are checked recursively;
    ordinary strings and prose remain byte-for-byte untouched.  Markdown local
    links are checked because they are operational navigation, while prose
    mentioning an old home is intentionally retained.
    """
    if relative.endswith(".json"):
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        def walk(value: object) -> None:
            if isinstance(value, Mapping):
                for child_key, child in value.items():
                    if (
                        isinstance(child_key, str)
                        and child_key.lower().endswith(
                            ("path", "href", "uri", "locator", "workspace", "workpad")
                        )
                        and isinstance(child, str)
                        and (child.startswith("/") or child.startswith("file://"))
                    ):
                        _reject(
                            "portable_link_unsafe",
                            f"operational link in {relative} points to an absolute source path",
                        )
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(value)
    elif relative.endswith((".md", ".markdown")) and b"](" in data:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return
        for match in re.findall(r"\]\(([^)]+)\)", text):
            if match.startswith(("/", "file://")):
                _reject(
                    "portable_link_unsafe",
                    f"operational link in {relative} points to an absolute source path",
                )


def _check_source(root: Path, relative: str, *, private: bool) -> bytes:
    relative = _safe_relative(relative)
    if _secret_path(relative):
        _reject("credential_excluded", "credential or local configuration material is not portable")
    path = root / relative
    parent = path.parent
    while parent != root:
        if parent.is_symlink():
            _reject("symlink_refused", "source path traverses a symlinked directory")
        parent = parent.parent
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise PrivateTransferError("archive_path_unsafe", "source path escapes the workpad") from exc
    if path.is_symlink():
        _reject("symlink_refused", "symlinked source is not portable")
    if not path.is_file():
        _reject("source_missing", f"selected source is unavailable: {relative}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PrivateTransferError("source_unavailable", "selected source cannot be read") from exc
    if len(data) > _MAX_FILE_BYTES:
        _reject("source_too_large", "selected source exceeds archive limits")
    _check_portable_links(relative, data)
    return data


def _walk(root: Path, roots: Iterable[str], *, private: bool) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for item in roots:
        item = _safe_relative(item)
        path = root / item
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            _reject("symlink_refused", "selected root is a symlink")
        if path.is_file():
            files[item] = _check_source(root, item, private=private)
            continue
        for child in sorted(path.rglob("*")):
            relative = child.relative_to(root).as_posix()
            if child.is_symlink():
                _reject("symlink_refused", "archive source contains a symlink")
            if child.is_file():
                if len(files) >= _MAX_FILES:
                    _reject("archive_too_large", "archive contains too many files")
                if private and _secret_path(relative):
                    # Private transfer has a closed exclusion inventory:
                    # credentials/config are omitted, while definition export
                    # refuses an explicitly selected private member.
                    continue
                files[relative] = _check_source(root, relative, private=private)
    return files


def _manifest(
    kind: str,
    files: Mapping[str, bytes],
    *,
    project_id: str | None = None,
    gig_id: str | None = None,
    journal_head: str | None = None,
) -> dict[str, object]:
    if len(files) > _MAX_FILES:
        _reject("archive_too_large", "archive contains too many files")
    total = sum(len(data) for data in files.values())
    if total > _MAX_TOTAL_BYTES:
        _reject("archive_too_large", "archive exceeds total size limit")
    result: dict[str, object] = {
        "schema_version": DEFINITION_SCHEMA_VERSION if kind == DEFINITION_KIND else PRIVATE_SCHEMA_VERSION,
        "kind": kind, "format": "zip", "files": [
            {"path": path, "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)}
            for path, data in sorted(files.items())
        ],
    }
    if project_id is not None:
        result["project_id"] = project_id
    if gig_id is not None:
        result["gig_id"] = gig_id
    if journal_head is not None:
        result["journal_head"] = journal_head
    if kind == PRIVATE_KIND:
        result["disclosure"] = "explicit operator-selected private Gig history; no credentials or provider configuration"
        result["state_sqlite"] = "rebuildable_omitted"
        result["activation"] = "none"
    _validate_manifest_contract(result)
    return result


def _validate_manifest_contract(value: Mapping[str, object]) -> None:
    schema_name = (
        "scout-definition-export-manifest.schema.json"
        if value.get("kind") == DEFINITION_KIND
        else "scout-private-transfer-manifest.schema.json"
    )
    try:
        schema = json.loads(resources.files("gigai.schemas").joinpath(schema_name).read_text())
        errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    except (OSError, TypeError, ValueError) as exc:
        _reject("archive_manifest_invalid", "transfer manifest schema is unavailable")
        raise AssertionError("unreachable") from exc
    if errors:
        _reject("archive_manifest_invalid", "transfer manifest does not meet its strict schema")


def _write_archive(destination: Path, manifest: Mapping[str, object], files: Mapping[str, bytes]) -> TransferResult:
    destination = destination.expanduser()
    if destination.exists() or destination.is_symlink():
        _reject("destination_exists", "refusing to overwrite an existing archive")
    parent = destination.parent
    if parent.is_symlink() or not parent.is_dir() or any(ancestor.is_symlink() for ancestor in (parent, *parent.parents) if ancestor != parent.anchor):
        _reject("destination_unsafe", "archive destination parent is unsafe")
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(prefix=".scout-transfer-", suffix=".zip", dir=str(parent))
        os.close(fd)
        temporary = Path(raw)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(_MANIFEST_NAME, canonical_json_bytes(manifest))
            for path, data in sorted(files.items()):
                info = zipfile.ZipInfo(path)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                archive.writestr(info, data)
        # link is the file equivalent of O_EXCL: a creator racing this
        # operation cannot cause us to replace its destination.
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            _reject("destination_exists", "refusing to overwrite an existing archive")
            raise AssertionError("unreachable") from exc
        temporary.unlink(missing_ok=True)
        temporary = None
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return TransferResult(destination, str(manifest["kind"]), tuple(sorted(files)), manifest.get("project_id"), manifest.get("gig_id"))  # type: ignore[arg-type]


def export_definition(*, workpad: Path, destination: Path) -> TransferResult:
    """Export safe editable definition material, never records or reports."""
    root = workpad.expanduser()
    if root.is_symlink() or not root.is_dir():
        _reject("source_unsafe", "workpad is not a regular directory")
    bundled = dict(scout_source_files())
    files: dict[str, bytes] = {}
    for relative in sorted(bundled):
        if relative == "definition/scout-source.json":
            continue
        if relative.split("/", 1)[0] not in _DEFINITION_ROOTS:
            continue
        candidate = root / relative
        files[relative] = _check_source(root, relative, private=False) if candidate.exists() else bundled[relative]
    # Include user-added editable files only under the declared source roots.
    for relative, data in _walk(root, _DEFINITION_ROOTS, private=False).items():
        if relative in {".git", "state.sqlite"} or _secret_path(relative):
            _reject("credential_excluded", "definition export contains private configuration")
        files[relative] = data
    return _write_archive(destination, _manifest(DEFINITION_KIND, files), files)


def backup_private(*, workpad: Path, destination: Path, project_id: str | None = None, gig_id: str | None = None, selected_paths: Iterable[str] | None = None) -> TransferResult:
    """Create an explicit private transfer archive for one Gig."""
    root = workpad.expanduser()
    if root.is_symlink() or not root.is_dir():
        _reject("source_unsafe", "workpad is not a regular directory")
    roots = tuple(selected_paths) if selected_paths is not None else tuple(sorted(_PRIVATE_ROOTS))
    for item in roots:
        if not isinstance(item, str) or _safe_relative(item).split("/", 1)[0] not in _PRIVATE_ROOTS:
            _reject("private_scope_invalid", "private transfer path is outside the selected Gig")
    journal_head: str | None = None
    if (project_id is None) != (gig_id is None):
        _reject("private_scope_invalid", "project and Gig identities must be supplied together")
    if project_id is not None and gig_id is not None:
        def capture(writer: object) -> tuple[dict[str, bytes], str]:
            # Capture all selected journal families from one writer snapshot.
            prefixes = tuple(
                f"{item.split('/', 1)[0]}/"
                for item in sorted(set(roots))
                if "/" in item or item in {
                    "docs", "references",
                    "run-inputs", "records", "runs", "run-plans",
                    "review-inputs",
                }
            )
            prefixes = tuple(sorted(set(prefixes)))
            snapshot = writer.snapshot(prefixes) if prefixes else None  # type: ignore[attr-defined]
            files = dict(snapshot.artifacts) if snapshot is not None else {}
            # Editable software roots are intentionally working-copy
            # conveniences, not journal authority.  Capture them while
            # holding the same writer lock, but do not pretend they are
            # immutable records.
            editable_roots = tuple(
                item
                for item in ("tools", "goalgraphs", "ui")
                if item in roots or any(path.startswith(item + "/") for path in roots)
            )
            files.update(_walk(root, editable_roots, private=True))
            # Root files are not representable as directory prefixes; read
            # them while the same journal writer lock is held.
            for item in roots:
                if "/" not in item:
                    candidate = root / item
                    if candidate.is_file() and not candidate.is_symlink():
                        files[item] = _check_source(root, item, private=True)
            return files, snapshot.head if snapshot is not None else "unknown"

        try:
            files, journal_head = run_with_journal_writer(
                workpad=root,
                project_id=project_id,
                gig_id=gig_id,
                operation=capture,
            )
        except (JournalConflictError, OSError) as exc:
            raise PrivateTransferError("journal_snapshot_invalid", "private transfer could not capture one committed journal snapshot") from exc
    else:
        files = _walk(root, roots, private=True)
    files = {path: data for path, data in files.items() if path != "state.sqlite" and not path.startswith("reports/") and not path.startswith("scratch/") and path not in {".git/config"}}
    manifest = _manifest(PRIVATE_KIND, files, project_id=project_id, gig_id=gig_id, journal_head=journal_head)
    return _write_archive(destination, manifest, files)


def _read_archive(archive_path: Path, *, expected_kind: str) -> tuple[dict[str, object], dict[str, bytes]]:
    archive_path = archive_path.expanduser()
    if (
        archive_path.is_symlink()
        or not archive_path.is_file()
        or any(ancestor.is_symlink() for ancestor in archive_path.parents)
    ):
        _reject("archive_source_unsafe", "archive must be one regular local file")
    try:
        archive = zipfile.ZipFile(archive_path)
        infos = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise PrivateTransferError("archive_invalid", "archive is not a valid Scout transfer") from exc
    try:
        if len(infos) > _MAX_FILES + 1:
            _reject("archive_invalid", "archive has duplicate or excessive members")
        raw_names = [info.filename for info in infos]
        if len(set(raw_names)) != len(raw_names):
            _reject("archive_invalid", "archive contains duplicate members")
        names = [_safe_relative(name) for name in raw_names]
        if len(set(names)) != len(names):
            _reject("archive_invalid", "archive contains colliding member paths")
        if names.count(_MANIFEST_NAME) != 1:
            _reject("archive_invalid", "archive manifest is missing")
        manifest_info = next(info for info, name in zip(infos, names) if name == _MANIFEST_NAME)
        _check_zip_info_bounds(manifest_info, manifest=True, total=0)
        manifest_raw = _read_zip_member(archive, manifest_info, limit=_MAX_MANIFEST_BYTES)
        value = parse_json_bytes(manifest_raw)
        if not isinstance(value, dict) or value.get("kind") != expected_kind or not isinstance(value.get("files"), list):
            _reject("archive_kind_mismatch", "archive manifest kind is unsupported")
        _validate_manifest_contract(value)
        rows: dict[str, Mapping[str, object]] = {}
        for row in value["files"]:
            if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
                _reject("archive_invalid", "archive file manifest is malformed")
            path = _safe_relative(row["path"])
            if path in rows or path == _MANIFEST_NAME:
                _reject("archive_invalid", "archive manifest contains duplicate paths")
            if _secret_path(path):
                _reject("credential_excluded", "archive contains a private or unlisted path")
            if expected_kind == DEFINITION_KIND and path.split("/", 1)[0] not in _DEFINITION_ROOTS:
                _reject("archive_scope_invalid", "archive contains an unlisted definition path")
            if expected_kind == PRIVATE_KIND and (
                path.split("/", 1)[0] not in _PRIVATE_ROOTS
                or path.startswith(("manifests/", "state.sqlite", "reports/", "scratch/", ".git/"))
            ):
                _reject("private_authority_excluded", "private archive contains machine-local authority")
            if row.get("content_sha256") is not None and not isinstance(row.get("content_sha256"), str):
                _reject("archive_invalid", "archive digest is malformed")
            if type(row.get("size_bytes")) is not int or row.get("size_bytes") < 0:
                _reject("archive_invalid", "archive size is malformed")
            rows[path] = row
        member_names = {name for name in names if name != _MANIFEST_NAME}
        if member_names != set(rows):
            _reject("archive_invalid", "archive members do not match its manifest")
        files: dict[str, bytes] = {}
        total = 0
        for path, row in rows.items():
            info = next(info for info, name in zip(infos, names) if name == path)
            # Any non-regular Unix marker (including symlink/hard-link style
            # entries) is refused even though ZipFile can read its bytes.
            mode = (info.external_attr >> 16) & 0o170000
            if mode not in {0, 0o100000}:
                _reject("symlink_refused", "archive links are not permitted")
            _check_zip_info_bounds(info, manifest=False, total=total)
            declared_size = int(row["size_bytes"])
            if declared_size != info.file_size:
                _reject("archive_digest_mismatch", "archive member size differs from the manifest")
            total += declared_size
            if total > _MAX_TOTAL_BYTES:
                _reject("archive_too_large", "archive exceeds transfer limits")
            data = _read_zip_member(archive, info, limit=_MAX_FILE_BYTES)
            _check_portable_links(path, data)
            if row.get("content_sha256") != digest_imported_bytes(data) or row.get("size_bytes") != len(data):
                _reject("archive_digest_mismatch", "archive bytes differ from the manifest")
            files[path] = data
        return value, files
    finally:
        archive.close()


def _read_restored_tree(restored: Path) -> dict[str, bytes]:
    restored = restored.expanduser()
    if (
        restored.is_symlink()
        or not restored.is_dir()
        or any(ancestor.is_symlink() for ancestor in restored.parents)
    ):
        _reject("restore_source_unsafe", "restored private tree is not a regular local directory")
    files: dict[str, bytes] = {}
    for child in sorted(restored.rglob("*")):
        relative = child.relative_to(restored).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            _reject("restore_authority_present", "restored tree must not contain a Git authority")
        if child.is_symlink():
            _reject("symlink_refused", "restored tree contains a symlink")
        if not child.is_file():
            continue
        path = _safe_relative(relative)
        if _secret_path(path):
            _reject("credential_excluded", "restored tree contains credential or local configuration material")
        if path.startswith(("manifests/", "state.sqlite", "reports/", "scratch/")):
            _reject("restore_authority_present", "restored tree contains machine-local authority")
        data = child.read_bytes()
        if len(data) > _MAX_FILE_BYTES:
            _reject("source_too_large", "restored source exceeds transfer limits")
        _check_portable_links(path, data)
        files[path] = data
        if len(files) > _MAX_FILES:
            _reject("archive_too_large", "restored tree contains too many files")
    return files


def _check_zip_info_bounds(info: zipfile.ZipInfo, *, manifest: bool, total: int) -> None:
    """Check declared ZIP sizes before any decompression/allocation."""
    limit = _MAX_MANIFEST_BYTES if manifest else _MAX_FILE_BYTES
    if info.is_dir() or info.file_size < 0 or info.file_size > limit:
        _reject("archive_too_large", "archive member exceeds transfer limits")
    if total + info.file_size > _MAX_TOTAL_BYTES and not manifest:
        _reject("archive_too_large", "archive exceeds transfer limits")
    if info.file_size and info.compress_size == 0:
        _reject("archive_invalid", "archive member has invalid compression metadata")
    if info.compress_size and info.file_size > info.compress_size * _MAX_COMPRESSION_RATIO:
        _reject("archive_too_large", "archive member compression ratio exceeds limit")


def _read_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, limit: int) -> bytes:
    """Read one member in bounded chunks after validating its declared size."""
    output = bytearray()
    try:
        with archive.open(info, "r") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, limit - len(output) + 1))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    _reject("archive_too_large", "archive member exceeds transfer limits")
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        raise PrivateTransferError("archive_invalid", "archive member cannot be read") from exc
    return bytes(output)


def _new_destination(destination: Path) -> Path:
    destination = destination.expanduser()
    if destination.exists() or destination.is_symlink():
        _reject("destination_exists", "refusing to overwrite an existing destination")
    if (
        destination.parent.is_symlink()
        or not destination.parent.is_dir()
        or any(ancestor.is_symlink() for ancestor in destination.parent.parents)
    ):
        _reject("destination_unsafe", "destination parent is unsafe")
    return destination


def _secure_publication_flags() -> int:
    """Return the flags required for descriptor-anchored publication.

    The importer deliberately refuses platforms/filesystems without the
    directory-FD and no-follow primitives needed to keep checked ancestors
    from being reopened by pathname.
    """
    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    if (
        os.name == "nt"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(function not in supports_dir_fd for function in _SECURE_DIR_FD_FUNCTIONS)
    ):
        _reject("destination_unsupported", "secure directory-fd publication is unavailable on this platform")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory_chain(path: Path, flags: int) -> tuple[int, list[int]]:
    """Open an existing absolute directory chain without following links."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    parts = absolute.parts
    if not parts or parts[0] != os.sep:
        _reject("destination_unsafe", "destination path must be absolute")
    opened: list[int] = []
    try:
        descriptor = os.open(os.sep, flags)
        opened.append(descriptor)
        for part in parts[1:]:
            descriptor = os.open(part, flags, dir_fd=descriptor)
            opened.append(descriptor)
        return opened[-1], opened
    except OSError as exc:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            _reject("destination_unsafe", "destination path contains a symlink or non-directory ancestor")
        raise


def _same_entry(parent_fd: int, name: str, expected: os.stat_result, *, directory: bool) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    if current.st_dev != expected.st_dev or current.st_ino != expected.st_ino:
        return False
    return stat.S_ISDIR(current.st_mode) if directory else stat.S_ISREG(current.st_mode)


def _extract_new(manifest: Mapping[str, object], files: Mapping[str, bytes], destination: Path) -> TransferResult:
    destination = destination.expanduser()
    flags = _secure_publication_flags()
    absolute = Path(os.path.abspath(os.fspath(destination)))
    if absolute.name in {"", ".", ".."}:
        _reject("destination_unsafe", "destination must name a new directory")
    parent_fd, ancestor_fds = _open_directory_chain(absolute.parent, flags)
    opened_dirs = list(ancestor_fds)
    destination_fd: int | None = None
    destination_identity: os.stat_result | None = None
    created_dirs: list[tuple[int, str, os.stat_result]] = []
    created_files: list[tuple[int, str, os.stat_result]] = []

    def unsafe_path(exc: OSError) -> None:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            _reject("destination_unsafe", "destination path redirects during publication")
        raise exc

    try:
        try:
            os.mkdir(absolute.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            _reject("destination_exists", "refusing to overwrite an existing destination")
            raise AssertionError("unreachable") from exc
        except OSError as exc:
            unsafe_path(exc)
        destination_identity = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        try:
            destination_fd = os.open(absolute.name, flags, dir_fd=parent_fd)
        except OSError as exc:
            unsafe_path(exc)
        opened_dirs.append(destination_fd)
        for path in sorted(files):
            relative = PurePosixPath(_safe_relative(path))
            relative_parts = relative.parts
            if not relative_parts:
                _reject("archive_path_unsafe", "archive member path is empty")
            assert destination_fd is not None
            current_fd = destination_fd
            for part in relative_parts[:-1]:
                try:
                    child_fd = os.open(part, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    made = False
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current_fd)
                        made = True
                    except FileExistsError:
                        pass
                    except OSError as exc:
                        unsafe_path(exc)
                    try:
                        created = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                    except OSError as exc:
                        unsafe_path(exc)
                    if not stat.S_ISDIR(created.st_mode):
                        _reject("destination_unsafe", "destination path redirects during publication")
                    if made:
                        created_dirs.append((current_fd, part, created))
                    try:
                        child_fd = os.open(part, flags, dir_fd=current_fd)
                    except OSError as exc:
                        unsafe_path(exc)
                except OSError as exc:
                    unsafe_path(exc)
                opened_dirs.append(child_fd)
                current_fd = child_fd
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            try:
                descriptor = os.open(relative_parts[-1], file_flags, 0o600, dir_fd=current_fd)
            except FileExistsError as exc:
                _reject("destination_exists", "refusing to overwrite an existing destination member")
                raise AssertionError("unreachable") from exc
            except OSError as exc:
                unsafe_path(exc)
            created_files.append((current_fd, relative_parts[-1], os.fstat(descriptor)))
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(files[path])
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
        os.fsync(destination_fd)
    except Exception:
        for parent, name, identity in reversed(created_files):
            if _same_entry(parent, name, identity, directory=False):
                try:
                    os.unlink(name, dir_fd=parent)
                except OSError:
                    pass
        for parent, name, identity in reversed(created_dirs):
            if _same_entry(parent, name, identity, directory=True):
                try:
                    os.rmdir(name, dir_fd=parent)
                except OSError:
                    pass
        if destination_identity is not None and _same_entry(parent_fd, absolute.name, destination_identity, directory=True):
            try:
                os.rmdir(absolute.name, dir_fd=parent_fd)
            except OSError:
                pass
        for descriptor in reversed(opened_dirs):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    for descriptor in reversed(opened_dirs):
        try:
            os.close(descriptor)
        except OSError:
            pass
    return TransferResult(destination, str(manifest["kind"]), tuple(sorted(files)), manifest.get("project_id"), manifest.get("gig_id"))  # type: ignore[arg-type]


def import_definition(*, archive: Path, destination: Path) -> TransferResult:
    manifest, files = _read_archive(archive, expected_kind=DEFINITION_KIND)
    return _extract_new(manifest, files, destination)


def restore_private(*, archive: Path, destination: Path, expected_project_id: str | None = None, expected_gig_id: str | None = None) -> TransferResult:
    manifest, files = _read_archive(archive, expected_kind=PRIVATE_KIND)
    if expected_project_id is not None and manifest.get("project_id") != expected_project_id:
        _reject("private_scope_mismatch", "private archive belongs to another project")
    if expected_gig_id is not None and manifest.get("gig_id") != expected_gig_id:
        _reject("private_scope_mismatch", "private archive belongs to another Gig")
    return _extract_new(manifest, files, destination)


def bind_restored_private(
    *,
    restored: Path,
    home_root: Path,
    requested_target: Path,
    project_id: str,
    gig_id: str,
) -> LocalBindingResult:
    """Explicitly bind restored history to a fresh local journal.

    This operation reuses the historical project/Gig identities but creates a
    new local Git/journal substrate.  It refuses a target that already selects
    a Gig, never imports active pointers/consents, and does not select the
    restored Gig.  A later operator approval is required before execution.
    """
    try:
        validate_entity_id(project_id, expected_prefix=EntityPrefix.PROJECT)
        validate_entity_id(gig_id, expected_prefix=EntityPrefix.GIG)
    except Exception as exc:
        _reject("private_scope_invalid", "local binding identities are not canonical")
        raise AssertionError("unreachable") from exc
    files = _read_restored_tree(restored)
    if not files:
        _reject("restore_empty", "restored private tree contains no selected history or definition")
    try:
        bound = resolve_bound_project(
            home_root=home_root,
            requested_target=requested_target,
        )
    except Exception as exc:
        raise PrivateTransferError(
            "local_binding_unavailable",
            "second-home target is not freshly bound to the restored project",
        ) from exc
    if bound.project_id != project_id:
        _reject("private_scope_mismatch", "restored project identity differs from the local target binding")
    if bound.target_kind == "git":
        try:
            binding = load_project_binding(bound.target_root)
        except Exception as exc:
            raise PrivateTransferError("local_binding_unavailable", "local project binding is unreadable") from exc
        if binding.active_gig_id is not None:
            _reject(
                "fresh_selection_required",
                "local target already selects a Gig; clear selection before private binding",
            )
    try:
        provisioned = provision_workpad(
            home_root=home_root,
            project_id=project_id,
            gig_id=gig_id,
        )
        if provisioned.reconciled:
            _reject("destination_exists", "local restored Gig already has a workpad")
        migrate_workpad_layout(
            workpad=provisioned.path,
            project_id=project_id,
            gig_id=gig_id,
        )
    except PrivateTransferError:
        raise
    except Exception as exc:
        raise PrivateTransferError(
            "local_binding_failed",
            "fresh local workpad could not be provisioned for restored history",
        ) from exc
    destination = provisioned.path
    for path, data in sorted(files.items()):
        if path.split("/", 1)[0] not in {
            "README.md", "CHANGELOG.md", "gig.py", "tools", "goalgraphs", "ui"
        }:
            # Journal-owned history is published atomically by the transition
            # below; do not pre-create immutable destinations.
            continue
        target = destination / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            _reject("destination_exists", "fresh local workpad contains an unexpected restored member")
        target.write_bytes(data)
        target.chmod(0o600)
    # Root source copies are ignored by layout v2 and remain inert working
    # material.  Journal records/references/docs retain exact historical bytes.
    journal_files = {
        path: data
        for path, data in files.items()
        if path.split("/", 1)[0] not in {"README.md", "CHANGELOG.md", "gig.py", "tools", "goalgraphs", "ui"}
    }
    receipt = canonical_json_bytes({
        "schema_version": "1.0",
        "kind": "scout_private_restore_binding",
        "project_id": project_id,
        "gig_id": gig_id,
        "activation": "none",
        "fresh_approval_required": True,
        "files": [
            {"path": path, "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)}
            for path, data in sorted(files.items())
        ],
    })
    journal_files["records/transfers/restored-private-binding.json"] = receipt
    try:
        entry = record_transition(
            workpad=destination,
            project_id=project_id,
            gig_id=gig_id,
            handoff_id=f"handoff_{uuid.uuid4()}",
            transition="private_reference_imported",
            body="Explicit private history restore bound to a fresh local journal; approval and active selection remain required.",
            artifacts=tuple(JournalArtifact(path, data) for path, data in sorted(journal_files.items())),
            front_matter={
                "restore_binding": "fresh_local_journal",
                "activation": "none",
                "fresh_approval_required": True,
                "active_selection": False,
            },
            allow_artifact_replacement=False,
        )
    except Exception as exc:
        raise PrivateTransferError(
            "local_binding_failed",
            "restored history could not be committed to the fresh local journal",
        ) from exc
    return LocalBindingResult(
        project_id=project_id,
        gig_id=gig_id,
        workpad=destination,
        imported_files=tuple(sorted(files)),
        journal_handoff_id=entry.handoff_id,
        active_selection=False,
    )


# Friendly names used by the copied wrapper and by early fixture consumers.
export_scout_definition = export_definition
import_scout_definition = import_definition
create_private_backup = backup_private
restore_private_backup = restore_private


__all__ = [
    "DEFINITION_KIND", "PRIVATE_KIND", "PrivateTransferError", "TransferResult",
    "backup_private", "create_private_backup", "export_definition",
    "export_scout_definition", "import_definition", "import_scout_definition",
    "restore_private", "restore_private_backup", "bind_restored_private", "LocalBindingResult",
]
