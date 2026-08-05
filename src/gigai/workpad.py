"""Private workpad provisioning, resolution, active authority, and opening."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Callable

from .canonical import EntityPrefix, InvalidIdentifierError, validate_entity_id
from .config import ConfigurationError, GigAIConfig, load_config
from .project_binding import (
    ProjectBindingError,
    load_project_binding,
    write_project_binding_atomic,
)
from .registry import (
    ProjectRecord,
    RegistryError,
    WorkpadRecord,
    open_project_registry,
)
from .target_binding import (
    ResolvedTarget,
    TargetBindingError,
    resolve_target,
)


WORKPAD_GITIGNORE = b"/objects/\n/scratch/\n/state.sqlite\n"
WORKPAD_GIT_USER_NAME = "GigAI Journal"
WORKPAD_GIT_USER_EMAIL = "local@gigai.invalid"
PROVISION_FAILPOINTS = ("after_staging", "after_publish", "after_registry")
ProvisionObserver = Callable[[str], None]


class WorkpadError(RuntimeError):
    code = "workpad_error"


class WorkpadUnavailableError(WorkpadError):
    code = "workpad_unavailable"


class WorkpadConflictError(WorkpadError):
    code = "workpad_conflict"


class WorkpadPermissionError(WorkpadError):
    code = "workpad_permission_denied"


class NoActiveGigError(WorkpadError):
    code = "no_active_gig"


class EditorInvocationError(WorkpadError):
    code = "editor_invocation_failed"


@dataclass(frozen=True)
class BoundProject:
    project_id: str
    target_root: Path
    target_kind: str


@dataclass(frozen=True)
class ResolvedWorkpad:
    project_id: str
    gig_id: str
    path: Path
    target_root: Path
    target_kind: str


@dataclass(frozen=True)
class ProvisionedWorkpad:
    project_id: str
    gig_id: str
    path: Path
    published: bool
    registry_changed: bool
    reconciled: bool


@dataclass(frozen=True)
class OpenResult:
    opened_workpad: bool
    opened_target: bool


def provision_workpad(
    *,
    home_root: Path,
    project_id: str,
    gig_id: str,
    provision_observer: ProvisionObserver | None = None,
) -> ProvisionedWorkpad:
    """Publish an empty Git substrate for caller-owned canonical IDs."""

    project_id = _canonical_id(project_id, EntityPrefix.PROJECT)
    gig_id = _canonical_id(gig_id, EntityPrefix.GIG)
    observer = provision_observer or (lambda _step: None)
    home, config = _load_owned_config(home_root)
    root = _workpad_authority(config)
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        project = transaction.find_project(project_id)
        if project is None:
            raise WorkpadConflictError(
                "caller-supplied project ID is not registered to a target"
            )
    destination = _expected_workpad(root, project_id, gig_id)
    parent = destination.parent
    _ensure_private_topology(root, parent)

    reconciled = False
    published = False
    if destination.exists() or destination.is_symlink():
        _validate_workpad_repository(destination, project_id, gig_id)
        reconciled = True
    else:
        staged = _find_staged_workpad(parent, gig_id, project_id)
        if staged is not None:
            _publish_staged(staged, destination)
            reconciled = True
            published = True
        else:
            staged = Path(
                tempfile.mkdtemp(prefix=f".{gig_id}.provision-", dir=parent)
            )
            try:
                _initialize_workpad_repository(staged, project_id, gig_id)
                observer("after_staging")
                _publish_staged(staged, destination)
                published = True
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        observer("after_publish")

    record = WorkpadRecord(
        gig_id=gig_id,
        project_id=project_id,
        workpad_locator=os.fspath(destination),
    )
    registry_changed = _register_record(registry.path.parent, record)
    observer("after_registry")
    return ProvisionedWorkpad(
        project_id=project_id,
        gig_id=gig_id,
        path=destination,
        published=published,
        registry_changed=registry_changed,
        reconciled=reconciled,
    )


def register_existing_workpad(
    *, home_root: Path, project_id: str, gig_id: str
) -> WorkpadRecord:
    project_id = _canonical_id(project_id, EntityPrefix.PROJECT)
    gig_id = _canonical_id(gig_id, EntityPrefix.GIG)
    home, config = _load_owned_config(home_root)
    root = _workpad_authority(config)
    destination = _expected_workpad(root, project_id, gig_id)
    _validate_workpad_repository(destination, project_id, gig_id)
    record = WorkpadRecord(gig_id, project_id, os.fspath(destination))
    _register_record(home, record)
    return record


def select_active_workpad(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    cwd: Path | None = None,
) -> ResolvedWorkpad:
    gig_id = _canonical_id(gig_id, EntityPrefix.GIG)
    home, config = _load_owned_config(home_root)
    bound = _resolve_bound_project(home, requested_target, cwd=cwd)
    resolved = _resolve_registered(config, home, bound, gig_id)
    registry, _ = open_project_registry(home, create=False)
    if bound.target_kind == "git":
        try:
            binding = load_project_binding(bound.target_root)
            write_project_binding_atomic(
                bound.target_root,
                replace(binding, active_gig_id=gig_id),
            )
        except (ProjectBindingError, OSError) as exc:
            raise WorkpadConflictError(str(exc)) from exc
    try:
        with registry.transaction() as transaction:
            transaction.select_active_workpad(bound.project_id, gig_id)
    except RegistryError as exc:
        raise WorkpadConflictError(str(exc)) from exc
    return resolved


def resolve_bound_project(
    *,
    home_root: Path,
    requested_target: Path | None,
    cwd: Path | None = None,
) -> BoundProject:
    """Resolve one existing target binding without selecting a Gig."""

    home, _config = _load_owned_config(home_root)
    return _resolve_bound_project(home, requested_target, cwd=cwd)


def resolve_workpad(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str | None,
    cwd: Path | None = None,
    allow_semantic_state: bool = False,
) -> ResolvedWorkpad:
    home, config = _load_owned_config(home_root)
    bound = _resolve_bound_project(home, requested_target, cwd=cwd)
    registry, _ = open_project_registry(home, create=False)
    selected = _canonical_id(gig_id, EntityPrefix.GIG) if gig_id is not None else None

    if selected is None and bound.target_kind == "git":
        try:
            binding = load_project_binding(bound.target_root)
        except ProjectBindingError as exc:
            raise WorkpadConflictError(str(exc)) from exc
        selected = binding.active_gig_id
        if selected is None:
            raise NoActiveGigError(
                "no_active_gig: the target has no explicitly selected active Gig"
            )
        with registry.transaction() as transaction:
            if transaction.find_project_workpad(bound.project_id, selected) is None:
                raise WorkpadConflictError(
                    "authoritative active Gig has no registered workpad"
                )
            derived = transaction.find_active_workpad(bound.project_id)
            if derived is None or derived.gig_id != selected:
                transaction.select_active_workpad(bound.project_id, selected)
    elif selected is None:
        with registry.transaction() as transaction:
            active = transaction.find_active_workpad(bound.project_id)
        if active is None:
            raise NoActiveGigError(
                "no_active_gig: the target has no explicitly selected active Gig"
            )
        selected = active.gig_id

    assert selected is not None
    return _resolve_registered(
        config, home, bound, selected, allow_semantic_state=allow_semantic_state
    )


def open_locations(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str | None,
    target_only: bool,
    with_target: bool,
    cwd: Path | None = None,
    allow_semantic_state: bool = False,
) -> OpenResult:
    if target_only and with_target:
        raise WorkpadConflictError("--target and --with-target are mutually exclusive")
    if target_only and gig_id is not None:
        raise WorkpadConflictError("--target cannot be combined with a Gig ID")
    home, config = _load_owned_config(home_root)
    bound = _resolve_bound_project(home, requested_target, cwd=cwd)
    opened_workpad = not target_only
    if target_only:
        locations = (bound.target_root,)
        opened_target = True
    else:
        resolved = resolve_workpad(
            home_root=home,
            requested_target=bound.target_root,
            gig_id=gig_id,
            cwd=cwd,
            allow_semantic_state=allow_semantic_state,
        )
        opened_target = with_target or config.open_with_target
        locations = (
            (resolved.path, bound.target_root)
            if opened_target
            else (resolved.path,)
        )
    completed = subprocess.run(
        [*config.editor_argv, *(os.fspath(path) for path in locations)],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise EditorInvocationError(
            f"configured editor exited with status {completed.returncode}"
        )
    return OpenResult(
        opened_workpad=opened_workpad,
        opened_target=opened_target,
    )


def _load_owned_config(home_root: Path) -> tuple[Path, GigAIConfig]:
    home = home_root.expanduser().resolve(strict=False)
    try:
        config = load_config(home)
    except ConfigurationError as exc:
        raise WorkpadUnavailableError(str(exc)) from exc
    try:
        matches = os.path.samefile(config.home_root, home)
    except OSError:
        matches = config.home_root.resolve(strict=False) == home
    if not matches:
        raise WorkpadConflictError(
            "configuration declares a different GigAI home authority"
        )
    return home, config


def _workpad_authority(config: GigAIConfig) -> Path:
    configured = config.workpad_root
    if configured.is_symlink():
        raise WorkpadConflictError("configured workpad authority must not be a symlink")
    try:
        root = configured.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkpadUnavailableError(
            "configured workpad mount is unavailable; no fallback was selected"
        ) from exc
    if not root.is_dir():
        raise WorkpadUnavailableError(
            "configured workpad mount is not a directory; no fallback was selected"
        )
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o222 == 0:
        raise WorkpadPermissionError("configured workpad mount is read-only")
    if root != configured:
        raise WorkpadConflictError(
            "configured workpad authority resolves to a different path"
        )
    return root


def _expected_workpad(root: Path, project_id: str, gig_id: str) -> Path:
    expected = root / "projects" / project_id / "gigs" / gig_id
    if not expected.is_relative_to(root) or expected == root:
        raise WorkpadConflictError("deterministic workpad path escaped its authority")
    return expected


def _ensure_private_topology(root: Path, parent: Path) -> None:
    current = root
    for part in parent.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise WorkpadConflictError("workpad topology contains a symlink")
        if current.exists() and not current.is_dir():
            raise WorkpadConflictError("workpad topology contains a non-directory")
        current.mkdir(mode=0o700, exist_ok=True)
        current.chmod(0o700)
    try:
        if not os.path.samefile(root, parent.parents[2]):
            raise WorkpadConflictError("workpad project root changed identity")
    except OSError as exc:
        raise WorkpadUnavailableError("workpad topology cannot be revalidated") from exc


def _find_staged_workpad(
    parent: Path, gig_id: str, project_id: str
) -> Path | None:
    candidates = sorted(parent.glob(f".{gig_id}.provision-*"))
    if not candidates:
        return None
    valid: list[Path] = []
    for candidate in candidates:
        try:
            _validate_workpad_repository(candidate, project_id, gig_id)
        except WorkpadError as exc:
            raise WorkpadConflictError(
                "interrupted workpad publication contains ambiguous or foreign state"
            ) from exc
        valid.append(candidate)
    if len(valid) != 1:
        raise WorkpadConflictError(
            "multiple interrupted workpad publications require explicit recovery"
        )
    return valid[0]


def _publish_staged(staged: Path, destination: Path) -> None:
    try:
        staged.rename(destination)
    except FileExistsError:
        raise WorkpadConflictError(
            "workpad destination appeared during atomic publication"
        ) from None
    directory_descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _initialize_workpad_repository(root: Path, project_id: str, gig_id: str) -> None:
    _git(root, "init", "--initial-branch=main", "--quiet")
    _git(root, "config", "--local", "user.name", WORKPAD_GIT_USER_NAME)
    _git(root, "config", "--local", "user.email", WORKPAD_GIT_USER_EMAIL)
    _git(root, "config", "--local", "gigai.project-id", project_id)
    _git(root, "config", "--local", "gigai.gig-id", gig_id)
    ignore = root / ".gitignore"
    with ignore.open("xb") as stream:
        stream.write(WORKPAD_GITIGNORE)
        stream.flush()
        os.fsync(stream.fileno())
    ignore.chmod(0o600)
    _validate_workpad_repository(root, project_id, gig_id)


def _validate_workpad_repository(
    root: Path,
    project_id: str,
    gig_id: str,
    *,
    allow_journal: bool = False,
    allow_semantic_state: bool = False,
) -> None:
    if root.is_symlink() or not root.is_dir():
        raise WorkpadConflictError("workpad path is missing, redirected, or not a directory")
    entries = {path.name for path in root.iterdir()}
    allowed = {".git", ".gitignore"}
    if allow_journal:
        allowed.add("handoffs")
    if allow_semantic_state:
        allowed.update({"gig.md", "goals", "reviews", "decisions", "manifests", "scratch"})
    if not {".git", ".gitignore"}.issubset(entries) or not entries <= allowed:
        raise WorkpadConflictError(
            "workpad contains semantic or unexpected top-level state"
        )
    handoffs = root / "handoffs"
    if handoffs.exists() and (handoffs.is_symlink() or not handoffs.is_dir()):
        raise WorkpadConflictError("workpad handoff directory is redirected or invalid")
    ignore = root / ".gitignore"
    if ignore.is_symlink() or ignore.read_bytes() != WORKPAD_GITIGNORE:
        raise WorkpadConflictError("workpad ignore rules differ from the G05 contract")
    inside = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise WorkpadConflictError("workpad is not a local Git repository")
    git_dir = _git(root, "rev-parse", "--absolute-git-dir").stdout.strip()
    if Path(git_dir).resolve(strict=True) != (root / ".git").resolve(strict=True):
        raise WorkpadConflictError("workpad uses an unexpected Git directory")
    expected_config = {
        "user.name": WORKPAD_GIT_USER_NAME,
        "user.email": WORKPAD_GIT_USER_EMAIL,
        "gigai.project-id": project_id,
        "gigai.gig-id": gig_id,
    }
    for key, expected in expected_config.items():
        value = _git(root, "config", "--local", "--get", key, check=False)
        if value.returncode != 0 or value.stdout.rstrip("\n") != expected:
            raise WorkpadConflictError(f"workpad Git ownership marker {key} mismatches")
    if _git(root, "remote").stdout.strip():
        raise WorkpadConflictError("workpad must not configure a Git remote")
    if not allow_journal and _git(root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0:
        raise WorkpadConflictError("G05 workpad must remain unborn without a commit")


def _register_record(home: Path, record: WorkpadRecord) -> bool:
    registry, _ = open_project_registry(home, create=False)
    try:
        with registry.transaction() as transaction:
            project = transaction.find_project(record.project_id)
            if project is None:
                raise WorkpadConflictError(
                    "workpad project is not registered to a target"
                )
            existing = transaction.find_workpad(record.gig_id)
            if existing is None:
                transaction.insert_workpad(record)
                return True
            if existing != record:
                raise WorkpadConflictError(
                    "Gig ID is registered to a different project or workpad locator"
                )
            return False
    except RegistryError as exc:
        raise WorkpadConflictError(str(exc)) from exc


def _resolve_bound_project(
    home: Path,
    requested_target: Path | None,
    *,
    cwd: Path | None,
) -> BoundProject:
    try:
        target = resolve_target(requested_target, cwd=cwd)
        registry, _ = open_project_registry(home, create=False)
        with registry.transaction() as transaction:
            record = transaction.find_target(target.root)
        if record is None:
            raise WorkpadConflictError("target is not bound to a GigAI project")
        _validate_target_record(record, target)
        if target.kind == "git":
            binding = load_project_binding(target.root)
            if binding.project_id != record.project_id:
                raise WorkpadConflictError(
                    "authoritative Git binding conflicts with the private registry"
                )
        return BoundProject(record.project_id, target.root, target.kind)
    except WorkpadError:
        raise
    except (TargetBindingError, ProjectBindingError, RegistryError, OSError) as exc:
        raise WorkpadConflictError(str(exc)) from exc


def _validate_target_record(record: ProjectRecord, target: ResolvedTarget) -> None:
    if record.target_kind != target.kind:
        raise WorkpadConflictError("target kind conflicts with its registry binding")
    try:
        if not os.path.samefile(record.target_locator, target.root):
            raise WorkpadConflictError(
                "target locator resolves to a different filesystem identity"
            )
    except OSError as exc:
        raise WorkpadUnavailableError("registered target is unavailable") from exc


def _resolve_registered(
    config: GigAIConfig,
    home: Path,
    bound: BoundProject,
    gig_id: str,
    *,
    allow_semantic_state: bool = False,
) -> ResolvedWorkpad:
    root = _workpad_authority(config)
    expected = _expected_workpad(root, bound.project_id, gig_id)
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        record = transaction.find_workpad(gig_id)
    if record is None:
        raise WorkpadUnavailableError("requested Gig has no registered workpad")
    if record.project_id != bound.project_id:
        raise WorkpadConflictError("requested Gig belongs to a different project")
    if record.workpad_locator != os.fspath(expected):
        raise WorkpadConflictError(
            "registered workpad locator differs from its configured authority"
        )
    try:
        resolved = expected.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkpadUnavailableError("registered workpad is unavailable") from exc
    if resolved != expected or not resolved.is_relative_to(root):
        raise WorkpadConflictError("registered workpad is redirected outside its authority")
    _validate_workpad_repository(
        expected,
        bound.project_id,
        gig_id,
        allow_journal=True,
        allow_semantic_state=allow_semantic_state,
    )
    return ResolvedWorkpad(
        project_id=bound.project_id,
        gig_id=gig_id,
        path=expected,
        target_root=bound.target_root,
        target_kind=bound.target_kind,
    )


def _canonical_id(value: str, prefix: EntityPrefix) -> str:
    try:
        return validate_entity_id(value, expected_prefix=prefix)
    except InvalidIdentifierError as exc:
        raise WorkpadConflictError(
            f"caller supplied an invalid canonical {prefix.value} ID"
        ) from exc


def _git(
    root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git")
    if executable is None:
        raise WorkpadUnavailableError("Git executable is unavailable")
    completed = subprocess.run(
        [executable, "-C", os.fspath(root), *args],
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        },
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if check and completed.returncode != 0:
        raise WorkpadConflictError(
            f"Git workpad operation failed: {completed.stderr.strip()}"
        )
    return completed


__all__ = [
    "BoundProject",
    "EditorInvocationError",
    "NoActiveGigError",
    "OpenResult",
    "PROVISION_FAILPOINTS",
    "ProvisionedWorkpad",
    "ResolvedWorkpad",
    "WORKPAD_GITIGNORE",
    "WORKPAD_GIT_USER_EMAIL",
    "WORKPAD_GIT_USER_NAME",
    "WorkpadConflictError",
    "WorkpadError",
    "WorkpadPermissionError",
    "WorkpadUnavailableError",
    "open_locations",
    "provision_workpad",
    "register_existing_workpad",
    "resolve_bound_project",
    "resolve_workpad",
    "select_active_workpad",
]
