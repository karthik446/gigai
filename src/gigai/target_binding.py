"""Idempotent Git and non-Git target binding with exact effect boundaries."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Callable
import uuid

from .canonical import EntityPrefix, generate_entity_id
from .config import ConfigurationError, load_config
from .project_binding import (
    BINDING_DIRECTORY,
    BINDING_FILENAME,
    ProjectBinding,
    ProjectBindingError,
    binding_path,
    load_project_binding,
    new_project_binding,
    write_project_binding_atomic,
)
from .registry import (
    ProjectRecord,
    RegistryConflictError,
    RegistryError,
    RegistryTransaction,
    open_project_registry,
    registry_path,
)


EXCLUDE_ENTRY = b"/.gigai/\n"
INIT_LOCK_NAME = "gigai-init.lock"
INIT_LOCK_OWNER = "owner"
INIT_LOCK_TIMEOUT_SECONDS = 10.0
INCOMPLETE_LOCK_GRACE_SECONDS = INIT_LOCK_TIMEOUT_SECONDS


class TargetBindingError(RuntimeError):
    code = "target_binding_error"


class TargetUnavailableError(TargetBindingError):
    code = "target_unavailable"


class TargetIdentityChangedError(TargetBindingError):
    code = "target_identity_changed"


class GitTargetError(TargetBindingError):
    code = "git_target_error"


class TrackedBindingError(TargetBindingError):
    code = "tracked_binding_refused"


class ConflictingBindingError(TargetBindingError):
    code = "binding_conflict"


class TargetPermissionError(TargetBindingError):
    code = "target_permission_denied"


class InitLockUnavailableError(TargetBindingError):
    code = "init_lock_unavailable"


@dataclass(frozen=True)
class ResolvedTarget:
    requested_path: Path
    requested_identity: Path
    root: Path
    kind: str


@dataclass(frozen=True)
class TargetBindingResult:
    project_id: str
    target_kind: str
    binding_created: bool
    registry_changed: bool
    exclude_changed: bool
    reconciled: bool


class TargetInitLock:
    def __init__(self, path: Path, *, timeout: float = INIT_LOCK_TIMEOUT_SECONDS) -> None:
        self.path = path
        self.timeout = timeout
        self._held = False
        self._owner = f"{os.getpid()} {uuid.uuid4().hex}\n"

    def __enter__(self) -> TargetInitLock:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir(mode=0o700)
                self._write_owner()
                self._held = True
                return self
            except FileExistsError:
                if self.path.is_symlink() or not self.path.is_dir():
                    raise InitLockUnavailableError(
                        "target initialization lock path is not a private directory"
                    )
                self._recover_abandoned_lock()
                if time.monotonic() >= deadline:
                    raise InitLockUnavailableError(
                        f"target initialization lock is unavailable at {self.path}"
                    )
                time.sleep(0.025)
            except OSError as exc:
                raise InitLockUnavailableError(
                    f"target initialization lock cannot be created: {exc}"
                ) from exc

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._held:
            try:
                owner = self.path / INIT_LOCK_OWNER
                if owner.read_text(encoding="ascii") != self._owner:
                    raise InitLockUnavailableError(
                        "target initialization lock ownership changed unexpectedly"
                    )
                owner.unlink()
                self.path.rmdir()
            except OSError as cleanup_error:
                raise InitLockUnavailableError(
                    f"target initialization lock cleanup failed: {cleanup_error}"
                ) from cleanup_error
            finally:
                self._held = False

    def _write_owner(self) -> None:
        owner = self.path / INIT_LOCK_OWNER
        temporary = self.path / f".{INIT_LOCK_OWNER}-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(self._owner)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, owner)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
                owner.unlink(missing_ok=True)
                self.path.rmdir()
            except OSError:
                pass
            raise

    def _recover_abandoned_lock(self) -> None:
        owner_path = self.path / INIT_LOCK_OWNER
        try:
            owner = owner_path.read_text(encoding="ascii")
        except FileNotFoundError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except FileNotFoundError:
                return
            if age < INCOMPLETE_LOCK_GRACE_SECONDS:
                return
            owner = ""
        except (OSError, UnicodeError):
            return
        fields = owner.split()
        if len(fields) == 2 and fields[0].isdigit() and _process_is_alive(int(fields[0])):
            return
        try:
            if owner_path.exists() and owner_path.read_text(encoding="ascii") != owner:
                return
            abandoned = self.path.with_name(
                f".{self.path.name}.abandoned-{uuid.uuid4().hex}"
            )
            self.path.rename(abandoned)
        except (FileNotFoundError, FileExistsError, OSError, UnicodeError):
            return
        try:
            (abandoned / INIT_LOCK_OWNER).unlink(missing_ok=True)
            abandoned.rmdir()
        except OSError as exc:
            raise InitLockUnavailableError(
                "abandoned target initialization lock could not be cleaned"
            ) from exc


def resolve_target(
    requested: Path | None,
    *,
    cwd: Path | None = None,
) -> ResolvedTarget:
    working_directory = (cwd or Path.cwd()).resolve(strict=True)
    explicit = requested is not None
    raw = requested if requested is not None else working_directory
    lexical = raw.expanduser()
    if not lexical.is_absolute():
        lexical = working_directory / lexical
    lexical = Path(os.path.abspath(lexical))
    try:
        requested_identity = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TargetUnavailableError(
            f"target path is unavailable or has a broken alias: {lexical}"
        ) from exc
    if not requested_identity.is_dir():
        raise TargetUnavailableError(f"target is not a directory: {lexical}")

    top = _git(requested_identity, "rev-parse", "--show-toplevel", check=False)
    if top.returncode == 0:
        try:
            root = Path(top.stdout.strip()).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise GitTargetError("Git reported an unavailable repository root") from exc
        return ResolvedTarget(
            requested_path=lexical,
            requested_identity=requested_identity,
            root=root,
            kind="git",
        )
    if not explicit:
        raise GitTargetError(
            "the current directory is not inside a Git repository; use --target "
            "for an explicit non-Git target"
        )
    return ResolvedTarget(
        requested_path=lexical,
        requested_identity=requested_identity,
        root=requested_identity,
        kind="non-git",
    )


def assert_target_identity_stable(target: ResolvedTarget) -> None:
    try:
        current_requested = target.requested_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TargetIdentityChangedError(
            "target alias became unavailable before binding"
        ) from exc
    try:
        if not os.path.samefile(current_requested, target.requested_identity):
            raise TargetIdentityChangedError(
                "target alias was repointed before binding; no mutation occurred"
            )
    except OSError as exc:
        raise TargetIdentityChangedError(
            "target identity could not be revalidated before binding"
        ) from exc
    if target.kind == "git":
        top = _git(current_requested, "rev-parse", "--show-toplevel", check=False)
        if top.returncode != 0:
            raise TargetIdentityChangedError(
                "target stopped resolving to its original Git repository"
            )
        try:
            current_root = Path(top.stdout.strip()).resolve(strict=True)
            if not os.path.samefile(current_root, target.root):
                raise TargetIdentityChangedError(
                    "target now resolves to a different Git repository"
                )
        except OSError as exc:
            raise TargetIdentityChangedError(
                "Git target identity could not be revalidated"
            ) from exc


def initialize_target(
    *,
    home_root: Path,
    requested_target: Path | None,
    cwd: Path | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> TargetBindingResult:
    """Bind one target without creating a workpad or touching tracked content."""

    home = home_root.expanduser().resolve(strict=False)
    try:
        config = load_config(home)
    except ConfigurationError as exc:
        raise TargetBindingError(str(exc)) from exc
    try:
        home_matches = os.path.samefile(config.home_root, home)
    except OSError:
        home_matches = config.home_root.resolve(strict=False) == home
    if not home_matches:
        raise TargetBindingError(
            f"configuration at {home} declares a different home root: {config.home_root}"
        )
    target = resolve_target(requested_target, cwd=cwd)
    try:
        if target.kind == "git":
            return _initialize_git_target(
                home=home,
                target=target,
                uuid_factory=uuid_factory,
            )
        return _initialize_non_git_target(
            home=home,
            target=target,
            uuid_factory=uuid_factory,
        )
    except TargetBindingError:
        raise
    except (RegistryError, ProjectBindingError, OSError) as exc:
        raise TargetBindingError(str(exc)) from exc


def _initialize_git_target(
    *,
    home: Path,
    target: ResolvedTarget,
    uuid_factory: Callable[[], uuid.UUID],
) -> TargetBindingResult:
    status_before = _git_bytes(
        target.root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    _preflight_git_target(home, target.root)
    lock_path = _git_path(target.root, INIT_LOCK_NAME)
    with TargetInitLock(lock_path):
        assert_target_identity_stable(target)
        _preflight_git_target(home, target.root)
        existing_binding = _optional_binding(target.root)
        registry, registry_created = open_project_registry(home, create=True)
        try:
            with registry.transaction() as transaction:
                result = _bind_git_transaction(
                    transaction=transaction,
                    target=target,
                    existing_binding=existing_binding,
                    registry_created=registry_created,
                    uuid_factory=uuid_factory,
                )
        except (RegistryError, ProjectBindingError, OSError) as exc:
            raise TargetBindingError(str(exc)) from exc
    status_after = _git_bytes(
        target.root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    status_is_exact = status_after == status_before
    status_is_binding_reconciliation = (
        result.reconciled
        and status_after == _without_untracked_binding(status_before)
    )
    if not status_is_exact and not status_is_binding_reconciliation:
        raise TargetBindingError(
            "machine-readable Git status changed during init; binding was not reported successful"
        )
    return result


def _bind_git_transaction(
    *,
    transaction: RegistryTransaction,
    target: ResolvedTarget,
    existing_binding: ProjectBinding | None,
    registry_created: bool,
    uuid_factory: Callable[[], uuid.UUID],
) -> TargetBindingResult:
    target_record = transaction.find_target(target.root)
    binding_created = False
    reconciled = False
    registry_changed = registry_created

    if existing_binding is None:
        if target_record is not None:
            raise ConflictingBindingError(
                "registry names this Git target but its authoritative project.toml is missing"
            )
        project_id = generate_entity_id(
            EntityPrefix.PROJECT,
            is_persisted=lambda candidate: transaction.find_project(candidate) is not None,
            uuid_factory=uuid_factory,
        )
        binding = new_project_binding(project_id)
        binding_created = write_project_binding_atomic(target.root, binding)
    else:
        binding = existing_binding
        project_id = binding.project_id
        id_record = transaction.find_project(project_id)
        if target_record is not None and target_record.project_id != project_id:
            raise ConflictingBindingError(
                "canonical target locator is registered to a different project ID"
            )
        if id_record is not None and not _record_matches_target(id_record, target):
            raise ConflictingBindingError(
                "project ID is registered to a different canonical target"
            )
        reconciled = target_record is None or id_record is None

    exclude_changed = _ensure_exclude_entry(target.root)
    if target_record is None:
        transaction.insert(
            ProjectRecord(
                project_id=project_id,
                target_locator=os.fspath(target.root),
                target_kind="git",
            )
        )
        registry_changed = True
    return TargetBindingResult(
        project_id=project_id,
        target_kind="git",
        binding_created=binding_created,
        registry_changed=registry_changed,
        exclude_changed=exclude_changed,
        reconciled=reconciled,
    )


def _initialize_non_git_target(
    *,
    home: Path,
    target: ResolvedTarget,
    uuid_factory: Callable[[], uuid.UUID],
) -> TargetBindingResult:
    assert_target_identity_stable(target)
    registry, registry_created = open_project_registry(home, create=True)
    try:
        with registry.transaction() as transaction:
            assert_target_identity_stable(target)
            existing = transaction.find_target(target.root)
            if existing is not None:
                if existing.target_kind != "non-git":
                    raise ConflictingBindingError(
                        "canonical target locator is already registered as a Git target"
                    )
                return TargetBindingResult(
                    project_id=existing.project_id,
                    target_kind="non-git",
                    binding_created=False,
                    registry_changed=registry_created,
                    exclude_changed=False,
                    reconciled=False,
                )
            project_id = generate_entity_id(
                EntityPrefix.PROJECT,
                is_persisted=lambda candidate: transaction.find_project(candidate) is not None,
                uuid_factory=uuid_factory,
            )
            transaction.insert(
                ProjectRecord(
                    project_id=project_id,
                    target_locator=os.fspath(target.root),
                    target_kind="non-git",
                )
            )
            return TargetBindingResult(
                project_id=project_id,
                target_kind="non-git",
                binding_created=False,
                registry_changed=True,
                exclude_changed=False,
                reconciled=False,
            )
    except (RegistryError, OSError) as exc:
        raise TargetBindingError(str(exc)) from exc


def _preflight_git_target(home: Path, root: Path) -> None:
    tracked = _git_bytes(root, "ls-files", "-z", "--", BINDING_DIRECTORY)
    if tracked:
        raise TrackedBindingError(
            "tracked .gigai content is refused; resolve it explicitly before init"
        )
    directory = root / BINDING_DIRECTORY
    if directory.is_symlink():
        raise ConflictingBindingError("target .gigai path must not be a symlink")
    if directory.exists() and not directory.is_dir():
        raise ConflictingBindingError("target .gigai path is not a directory")
    if directory.is_dir():
        unexpected = sorted(
            path.name
            for path in directory.iterdir()
            if path.name != binding_path(root).name
        )
        if unexpected:
            raise ConflictingBindingError(
                f"target .gigai contains unexpected entries: {unexpected}"
            )
        project_path = binding_path(root)
        if project_path.is_symlink():
            raise ConflictingBindingError("project.toml must not be a symlink")
        if project_path.exists():
            load_project_binding(root)
    root_mode = stat.S_IMODE(root.stat().st_mode)
    if root_mode & 0o222 == 0:
        raise TargetPermissionError(f"target root is read-only: {root}")
    exclude = _exclude_path(root)
    if exclude.is_symlink():
        raise TargetPermissionError("Git info/exclude must not be a symlink")
    if not exclude.parent.is_dir():
        raise TargetPermissionError(f"Git exclude parent is unavailable: {exclude.parent}")
    if stat.S_IMODE(exclude.parent.stat().st_mode) & 0o222 == 0:
        raise TargetPermissionError(f"Git exclude parent is read-only: {exclude.parent}")
    if exclude.exists() and stat.S_IMODE(exclude.stat().st_mode) & 0o222 == 0:
        raise TargetPermissionError(f"Git exclude file is read-only: {exclude}")
    registry = registry_path(home)
    if registry.exists():
        open_project_registry(home, create=False)
    elif stat.S_IMODE(home.stat().st_mode) & 0o222 == 0:
        raise TargetPermissionError(f"GigAI home is read-only: {home}")


def _optional_binding(root: Path) -> ProjectBinding | None:
    path = binding_path(root)
    if not path.exists():
        return None
    return load_project_binding(root)


def _record_matches_target(record: ProjectRecord, target: ResolvedTarget) -> bool:
    if record.target_kind != target.kind:
        return False
    stored = Path(record.target_locator)
    try:
        return stored.exists() and os.path.samefile(stored, target.root)
    except OSError:
        return False


def _ensure_exclude_entry(root: Path) -> bool:
    path = _exclude_path(root)
    before = path.read_bytes() if path.exists() else b""
    lines = before.splitlines()
    count = sum(line == EXCLUDE_ENTRY.rstrip(b"\n") for line in lines)
    if count == 1:
        return False
    if count == 0:
        separator = b"" if not before or before.endswith((b"\n", b"\r")) else b"\n"
        after = before + separator + EXCLUDE_ENTRY
    else:
        kept = [
            line
            for line in before.splitlines(keepends=True)
            if line.rstrip(b"\r\n") != EXCLUDE_ENTRY.rstrip(b"\n")
        ]
        prefix = b"".join(kept)
        separator = b"" if not prefix or prefix.endswith((b"\n", b"\r")) else b"\n"
        after = prefix + separator + EXCLUDE_ENTRY
    _write_bytes_atomic(path, after)
    return True


def _without_untracked_binding(status: bytes) -> bytes:
    binding_entry = f"?? {BINDING_DIRECTORY}/{BINDING_FILENAME}".encode()
    entries = [entry for entry in status.split(b"\0") if entry]
    kept = [entry for entry in entries if entry != binding_entry]
    return b"".join(entry + b"\0" for entry in kept)


def _process_is_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _exclude_path(root: Path) -> Path:
    return _git_path(root, "info/exclude")


def _git_path(root: Path, name: str) -> Path:
    result = _git(root, "rev-parse", "--git-path", name)
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = root / path
    return Path(os.path.abspath(path))


def _git_bytes(root: Path, *args: str) -> bytes:
    result = _git(root, *args, text=False)
    return result.stdout


def _git(
    root: Path,
    *args: str,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    executable = shutil.which("git")
    if executable is None:
        raise GitTargetError("Git executable is unavailable")
    completed = subprocess.run(
        [executable, "-C", os.fspath(root), *args],
        capture_output=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        text=text,
        check=False,
        shell=False,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr if text else completed.stderr.decode("utf-8", "replace")
        raise GitTargetError(f"Git command failed: {stderr.strip()}")
    return completed


__all__ = [
    "ConflictingBindingError",
    "GitTargetError",
    "InitLockUnavailableError",
    "ResolvedTarget",
    "TargetBindingError",
    "TargetBindingResult",
    "TargetIdentityChangedError",
    "TargetInitLock",
    "TargetPermissionError",
    "TargetUnavailableError",
    "TrackedBindingError",
    "assert_target_identity_stable",
    "initialize_target",
    "resolve_target",
]
