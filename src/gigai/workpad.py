"""Private workpad provisioning, resolution, active authority, and opening."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
from typing import Callable, Iterator

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
    GitTargetError,
    ResolvedTarget,
    TargetBindingError,
    resolve_target,
)


WORKPAD_GITIGNORE = b"/objects/\n/scratch/\n/state.sqlite\n"
# v2 is intentionally exact.  It is a layout declaration, not a trust grant:
# callers still validate every root, component, and inventoried artifact.
WORKPAD_V2_GITIGNORE = (
    b"/objects/\n/scratch/\n/state.sqlite\n/state.sqlite-journal\n"
    b"/state.sqlite-wal\n/state.sqlite-shm\n/indexes/\n/reports/scout/\n"
    b"/README.md\n/CHANGELOG.md\n/gig.py\n/tools/\n/goalgraphs/\n/ui/\n"
)
WORKPAD_LAYOUT_PATH = "manifests/workpad-layout.json"
# Run-local artifacts that are additive, never journaled, and never named
# explicitly by any commit: unlike WORKPAD_GITIGNORE/WORKPAD_V2_GITIGNORE
# (a tracked, byte-exact layout declaration every caller validates), these
# patterns live in the untracked `.git/info/exclude` so they can be extended
# without disturbing that committed contract or its hash. Generic run-local
# artifact roots (not gig-specific names) so core stays free of gig imports.
RUN_LOCAL_ARTIFACT_EXCLUDES = (
    "/runs/*/raw/",
    "/runs/*/progress/",
    "/runs/*/logs/",
)
_V2_ROOTS = frozenset({
    "README.md", "CHANGELOG.md", "gig.py", "tools", "goalgraphs", "ui",
    "docs", "references", "run-inputs", "records", "indexes", "reports",
    "runs", "run-plans", "review-inputs", "manifests", "handoffs", "scratch",
    "state.sqlite", "graph-selections", "addressed", "feedback", "findings",
    "review", "traces", "occurrences", "comparisons", "decisions", "goals",
    "gig.md",
    # SQLite's own sidecars for state.sqlite, already declared (ignored) by
    # WORKPAD_V2_GITIGNORE: a rollback journal exists while any writer's
    # transaction is open, and this listing is taken without that writer's
    # lock (uat-bug-034), so refusing it fails concurrent readers.
    "state.sqlite-journal", "state.sqlite-wal", "state.sqlite-shm",
})
WORKPAD_GIT_USER_NAME = "GigAI Journal"
WORKPAD_GIT_USER_EMAIL = "local@gigai.invalid"
PROVISION_FAILPOINTS = ("after_staging", "after_publish", "after_registry")
ProvisionObserver = Callable[[str], None]


# --- 0110-033: reuse of unchanged authority checks inside a read ------------
#
# Resolving a workpad costs 13 git subprocesses and a journal read as many
# again, every call, for facts that only change when the workpad does. A
# caller that only READS (a GET route) opens ``committed_read_cache()``;
# inside it, on that thread, a check that already passed for the workpad's
# current fingerprint is not run again, and ``journal`` keeps the committed
# reads it made at that fingerprint. Outside the block nothing is reused:
# every writer, the CLI and every other caller run each check as before.
#
# The fingerprint is everything those checks read, taken without git: the
# journal head (``.git/HEAD`` and its loose ref), the top-level entries, the
# identity of ``.git``, ``tools`` and ``handoffs``, and the identity, size
# and change time of ``.git/config``, ``.gitignore`` and the layout marker.
# A result is only kept when the fingerprint was the same before and after
# it was computed. A head that cannot be read from the files (a packed ref)
# is never cached.
#
# A journal write moves the head. What was kept at the old head still holds
# at the new one only when the new head is a straight descendant of the old
# one and none of the commits between them touched a path the kept result
# was read from (``read_still_holds``: one ``git log`` per head move, shared
# by everything kept). So a recorded application is read again by the
# applications read, and leaves the profiles read as it was.

_READ_SCOPE = threading.local()
_READ_CACHE_LOCK = threading.Lock()
_validated_repositories: dict[tuple[object, ...], tuple[object, ...]] = {}
_resolved_targets: dict[tuple[object, ...], tuple[tuple[object, ...], ResolvedTarget]] = {}
_GIT_DISCOVERY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM")
# (workpad, old head, new head) -> the commits between them, newest first, each with the paths it touched; None: not a straight descendant.
_committed_between: dict[tuple[str, str, str], tuple[tuple[str, frozenset[str]], ...] | None] = {}
_COMMITTED_BETWEEN_MAX = 64


_read_cache_key_locks: dict[tuple[object, ...], threading.Lock] = {}
_READ_CACHE_KEY_LOCKS_MAX = 16384


def read_cache_key_lock(key: tuple[object, ...]) -> threading.Lock:
    """One lock per kept read, so readers arriving together make the read once."""

    with _READ_CACHE_LOCK:
        if len(_read_cache_key_locks) > _READ_CACHE_KEY_LOCKS_MAX:
            _read_cache_key_locks.clear()  # a lock in use stays referenced by its holder
        return _read_cache_key_locks.setdefault(key, threading.Lock())


@contextmanager
def committed_read_cache() -> Iterator[None]:
    """Reuse unchanged workpad checks and committed journal reads on this thread, for a read."""

    depth = getattr(_READ_SCOPE, "depth", 0)
    _READ_SCOPE.depth = depth + 1
    try:
        yield
    finally:
        _READ_SCOPE.depth = depth


def committed_read_cache_active() -> bool:
    return getattr(_READ_SCOPE, "depth", 0) > 0


def _file_signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        found = os.lstat(path)
    except OSError:
        return None
    return (found.st_mode, found.st_ino, found.st_size, found.st_mtime_ns)


def _directory_signature(path: Path) -> tuple[int, int] | None:
    """What kind of entry ``path`` is and which one: a directory's size and change time move with its content."""

    try:
        found = os.lstat(path)
    except OSError:
        return None
    return (found.st_mode, found.st_ino)


def workpad_head_without_git(root: Path) -> str | None:
    """The journal head commit, read from ``.git`` files; ``None`` when only git can tell."""

    git_dir = root / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if head.startswith("ref: "):
            ref = head[len("ref: "):].strip()
            if not ref.startswith("refs/heads/") or ".." in ref.split("/"):
                return None
            head = (git_dir / ref).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if len(head) not in (40, 64) or any(character not in "0123456789abcdef" for character in head):
        return None
    return head


def workpad_fingerprint(root: Path) -> tuple[object, ...] | None:
    """What the workpad's authority checks read, without git; ``None`` when it cannot be taken."""

    head = workpad_head_without_git(root)
    if head is None:
        return None
    try:
        entries = tuple(sorted(os.listdir(root)))
    except OSError:
        return None
    return (
        head,
        entries,
        *(_directory_signature(root / name) for name in (".git", "tools", "handoffs")),
        *(_file_signature(root / name) for name in (".git/config", ".gitignore", WORKPAD_LAYOUT_PATH)),
    )


def straight_commits_between(root: Path, old: str, new: str) -> tuple[tuple[str, frozenset[str]], ...] | None:
    """Each commit after ``old`` up to ``new``, newest first, with the paths it touched; ``None`` unless ``new`` descends straight from ``old``.

    "Straight": each commit from ``new`` back has exactly one parent and the
    chain ends at ``old``, which is how the journal grows. Anything else (a
    rewritten or reset history, a merge) answers ``None``: nothing kept at
    ``old`` is trusted at ``new``. One ``git log`` for each pair of heads,
    kept for the readers that ask next (0110-036: the per-commit lists are
    what an incremental read applies).
    """

    key = (os.fspath(root), old, new)
    with _READ_CACHE_LOCK:
        if key in _committed_between:
            return _committed_between[key]
    result = _straight_chain(root, old, new)
    with _READ_CACHE_LOCK:
        if len(_committed_between) >= _COMMITTED_BETWEEN_MAX:
            _committed_between.pop(next(iter(_committed_between)))
        _committed_between[key] = result
    return result


def _straight_chain(root: Path, old: str, new: str, *only: str) -> tuple[tuple[str, frozenset[str]], ...] | None:
    """One ``git log old..new``: each commit, newest first, with the paths it touched; ``None`` unless a straight line.

    With ``only``, every commit is still listed (no history simplification)
    and its paths are limited to those: the cost of a commit is then one look
    at those paths, not a whole diff (0110-043).
    """

    limited = (("--full-history", "--sparse"), ("--", *only)) if only else ((), ())
    listing = _git(root, "log", "-z", "--format=%x01%H %P", "--name-only", *limited[0], f"{old}..{new}", *limited[1], check=False)
    chain: list[tuple[str, list[str], set[str]]] = []
    if listing.returncode == 0:
        for chunk in listing.stdout.split("\x00"):
            if not chunk:
                continue
            if chunk[0] == "\x01":
                commit, _space, parents = chunk[1:].partition(" ")
                chain.append((commit, parents.split(), set()))
                continue
            name = chunk[1:] if chunk[0] == "\n" else chunk
            if name and chain:
                chain[-1][2].add(name)
    straight = bool(chain) and chain[0][0] == new and chain[-1][1] == [old] and all(
        parents == [chain[index + 1][0]] for index, (_commit, parents, _names) in enumerate(chain[:-1])
    )
    return tuple((commit, frozenset(names)) for commit, _parents, names in chain) if straight else None


def paths_committed_between(root: Path, old: str, new: str) -> frozenset[str] | None:
    """Every path the commits after ``old`` up to ``new`` touched; ``None`` unless ``new`` descends straight from ``old``.

    See :func:`straight_commits_between` for "straight".
    """

    commits = straight_commits_between(root, old, new)
    if commits is None:
        return None
    return frozenset().union(*(names for _commit, names in commits))


def repository_check_holds(root: Path, project_id: str, gig_id: str, now: tuple[object, ...]) -> bool:
    """Whether this workpad passed :func:`_validate_workpad_repository` at a fingerprint that still holds at ``now``.

    0110-036: the journal's own workpad check asks git for a subset of what
    that check asks (the layout marker, the ignore rules, the four ownership
    markers, no remote), so inside a read it need not ask again.
    """

    with _READ_CACHE_LOCK:
        passed = [at for key, at in _validated_repositories.items() if key[:3] == (os.fspath(root), project_id, gig_id)]
    return any(read_still_holds(root, at, now, layout_paths_touched) for at in passed)


def read_still_holds(
    root: Path,
    held: tuple[object, ...],
    now: tuple[object, ...],
    touches: Callable[[frozenset[str]], bool],
) -> bool:
    """Whether what was read at fingerprint ``held`` is what a read at ``now`` would be.

    The same fingerprint: yes. Only the head moved: yes when the commits in
    between touched nothing the read depends on (``touches`` says whether a
    set of paths does). Anything else changed: no.
    """

    if held == now:
        return True
    if held[1:] != now[1:]:
        return False
    touched = paths_committed_between(root, str(held[0]), str(now[0]))
    return touched is not None and not touches(touched)


def layout_paths_touched(touched: frozenset[str]) -> bool:
    """The two committed files the workpad's own checks read: the layout marker and the ignore rules."""

    return WORKPAD_LAYOUT_PATH in touched or ".gitignore" in touched


def _target_fingerprint(requested: Path) -> tuple[object, ...]:
    """Whether ``requested`` is inside a Git work tree, and which one: what ``resolve_target`` asks git."""

    try:
        identity = requested.resolve(strict=True)
    except (OSError, RuntimeError):
        return (None,)
    nearest: tuple[object, ...] = (None,)
    for candidate in (identity, *identity.parents):
        signature = _directory_signature(candidate / ".git")
        if signature is not None:
            nearest = (os.fspath(candidate), signature)
            break
    return (os.fspath(identity), _directory_signature(identity), nearest, *(os.environ.get(name) for name in _GIT_DISCOVERY_ENV))


def _resolve_target(requested: Path | None, *, cwd: Path | None) -> ResolvedTarget:
    """``resolve_target``, its answer kept inside a ``committed_read_cache`` while the target is unchanged."""

    if requested is None or not committed_read_cache_active():
        return resolve_target(requested, cwd=cwd)
    key = (os.fspath(requested), None if cwd is None else os.fspath(cwd))
    before = _target_fingerprint(requested)
    with _READ_CACHE_LOCK:
        held = _resolved_targets.get(key)
    if held is not None and held[0] == before:
        return held[1]
    target = resolve_target(requested, cwd=cwd)
    if _target_fingerprint(requested) == before:
        with _READ_CACHE_LOCK:
            _resolved_targets[key] = (before, target)
    return target


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
    reconcile_existing_journal: bool = False,
) -> ProvisionedWorkpad:
    """Publish an empty Git substrate for caller-owned canonical IDs.

    ``reconcile_existing_journal`` is for a caller resuming its own pinned
    journaled creation transaction; it never permits arbitrary unrecognized
    workpad roots.
    """

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
        _validate_workpad_repository(
            destination,
            project_id,
            gig_id,
            allow_journal=reconcile_existing_journal,
        )
        reconciled = True
    else:
        staged = _find_staged_workpad(parent, gig_id, project_id)
        if staged is not None:
            _publish_staged(staged, destination)
            reconciled = True
            published = True
        else:
            staged = Path(tempfile.mkdtemp(prefix=f".{gig_id}.provision-", dir=parent))
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
    allow_semantic_state: bool = False,
) -> ResolvedWorkpad:
    gig_id = _canonical_id(gig_id, EntityPrefix.GIG)
    home, config = _load_owned_config(home_root)
    bound = _resolve_bound_project(home, requested_target, cwd=cwd)
    resolved = _resolve_registered(
        config, home, bound, gig_id, allow_semantic_state=allow_semantic_state
    )
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
    tolerate_invalid_registry_rows: bool = False,
) -> BoundProject:
    """Resolve one existing target binding without selecting a Gig."""

    home, _config = _load_owned_config(home_root)
    return _resolve_bound_project(
        home,
        requested_target,
        cwd=cwd,
        tolerate_invalid_registry_rows=tolerate_invalid_registry_rows,
    )


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
                "no_active_gig: the target has no explicitly selected active Gig; "
                "run `gigai gig use <gig_id>` to select one"
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
                "no_active_gig: the target has no explicitly selected active Gig; "
                "run `gigai gig use <gig_id>` to select one"
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
            (resolved.path, bound.target_root) if opened_target else (resolved.path,)
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


def _find_staged_workpad(parent: Path, gig_id: str, project_id: str) -> Path | None:
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
    ensure_run_local_artifact_excludes(root)
    _validate_workpad_repository(root, project_id, gig_id)


def ensure_run_local_artifact_excludes(root: Path) -> bool:
    """Add any missing ``RUN_LOCAL_ARTIFACT_EXCLUDES`` lines to ``.git/info/exclude``.

    Idempotent and additive-only: existing lines (including ones an operator
    or a future packet added) are never rewritten or reordered, only
    appended to. Safe to call on every workpad creation and, cheaply, before
    every clean-authority check on an existing workpad -- it does one
    ``read_text``/``write_text`` and never touches tracked history or the
    journal/database locks. Returns True if the file was changed.
    """

    exclude_path = root / ".git" / "info" / "exclude"
    try:
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    except OSError:
        return False
    existing_lines = set(existing.splitlines())
    missing = [line for line in RUN_LOCAL_ARTIFACT_EXCLUDES if line not in existing_lines]
    if not missing:
        return False
    exclude_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    addition = "".join(f"{line}\n" for line in missing)
    if existing and not existing.endswith("\n"):
        addition = "\n" + addition
    try:
        with exclude_path.open("a", encoding="utf-8") as stream:
            stream.write(addition)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        return False
    return True


def _validate_workpad_repository(
    root: Path,
    project_id: str,
    gig_id: str,
    *,
    allow_journal: bool = False,
    allow_semantic_state: bool = False,
) -> None:
    if root.is_symlink() or not root.is_dir():
        raise WorkpadConflictError(
            "workpad path is missing, redirected, or not a directory"
        )
    # 0110-033: inside a read, a workpad that already passed these checks
    # with this exact fingerprint is not asked again (12 git subprocesses);
    # readers arriving together ask once.
    cache_key = (os.fspath(root), project_id, gig_id, allow_journal, allow_semantic_state)
    # 0110-11 STORE2: inside ``one_operation()`` (and no read), a pass this
    # same operation made, with the configuration proven by the one listing,
    # is not asked of git again while the fingerprint says nothing these
    # checks read has changed. Anything else is the whole check, as before.
    passed = None if committed_read_cache_active() else getattr(_OPERATION, "passed", None)
    asked_at = _operation_fingerprint(root) if passed is not None else None
    if passed is not None and asked_at is not None:
        held = passed.pop(cache_key, None)
        if held is not None and read_still_holds(root, held, asked_at, layout_paths_touched):
            passed[cache_key] = asked_at
            return
        proven = _check_workpad_repository(root, project_id, gig_id, allow_journal=allow_journal, allow_semantic_state=allow_semantic_state)
        if proven and _operation_fingerprint(root) == asked_at:
            passed[cache_key] = asked_at
        return
    checked_at = workpad_fingerprint(root) if committed_read_cache_active() else None
    if checked_at is None:
        _check_workpad_repository(root, project_id, gig_id, allow_journal=allow_journal, allow_semantic_state=allow_semantic_state)
        return
    with read_cache_key_lock(("repository", *cache_key)):
        with _READ_CACHE_LOCK:
            passed_at = _validated_repositories.get(cache_key)
        if passed_at is None or not read_still_holds(root, passed_at, checked_at, layout_paths_touched):
            _check_workpad_repository(root, project_id, gig_id, allow_journal=allow_journal, allow_semantic_state=allow_semantic_state)
            if workpad_fingerprint(root) != checked_at:
                return  # it moved while it was checked: this pass is not kept
        with _READ_CACHE_LOCK:
            _validated_repositories[cache_key] = checked_at


def _check_workpad_repository(
    root: Path,
    project_id: str,
    gig_id: str,
    *,
    allow_journal: bool = False,
    allow_semantic_state: bool = False,
) -> bool:
    """The checks of :func:`_validate_workpad_repository`, asked of git every time.

    Returns whether the configuration was proven by the one listing
    (:func:`ownership_config_proven`); ``False`` when the five old questions
    answered instead. Either way the workpad passed.
    """

    entries = {path.name for path in root.iterdir()}
    allowed = {".git", ".gitignore"}
    layout_version = workpad_layout_version(root, project_id=project_id, gig_id=gig_id)
    if allow_journal:
        allowed.add("handoffs")
        allowed.add("scratch")
        allowed.add("manifests")
    if allow_semantic_state:
        allowed.update(
            {
                "gig.md",
                "goals",
                "reviews",
                "reports",
                "decisions",
                "manifests",
                "scratch",
                "state.sqlite",
                "runs",
                    "run-plans",
                    "graph-selections",
                "review-inputs",
                "addressed",
                "feedback",
                "findings",
                "review",
                "traces",
                    "tools",
                    "occurrences",
                    "comparisons",
            }
        )
    if layout_version == 2:
        allowed.update(_V2_ROOTS)
    if not {".git", ".gitignore"}.issubset(entries) or not entries <= allowed:
        unexpected = sorted(entries - allowed)
        detail = f": {', '.join(unexpected)}" if unexpected else ""
        raise WorkpadConflictError(
            "workpad contains semantic or unexpected top-level state" + detail
        )
    tools = root / "tools"
    if tools.exists() and (tools.is_symlink() or not tools.is_dir()):
        raise WorkpadConflictError("workpad tools root is redirected or invalid")
    handoffs = root / "handoffs"
    if handoffs.exists() and (handoffs.is_symlink() or not handoffs.is_dir()):
        raise WorkpadConflictError("workpad handoff directory is redirected or invalid")
    ignore = root / ".gitignore"
    expected_ignore = WORKPAD_V2_GITIGNORE if layout_version == 2 else WORKPAD_GITIGNORE
    if ignore.is_symlink() or ignore.read_bytes() != expected_ignore:
        raise WorkpadConflictError("workpad ignore rules differ from the declared layout contract")
    git_dir = _work_tree_git_dir(root)
    if Path(git_dir).resolve(strict=True) != (root / ".git").resolve(strict=True):
        raise WorkpadConflictError("workpad uses an unexpected Git directory")
    proven = ownership_config_proven(root, project_id, gig_id)
    if not proven:
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
    if (
        not allow_journal
        and _git(root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0
    ):
        raise WorkpadConflictError("G05 workpad must remain unborn without a commit")
    return proven


# --- 0110-11 STORE2: the workpad checks' git questions, asked once ------------
#
# A workpad check asked git five questions about the configuration (four
# ``git config --local --get`` and ``git remote``), each a process, and the
# repository check two more about the work tree. Every question stays. When
# ONE process can prove all the old answers it is the only one started; when
# it cannot, the old questions are asked, unchanged, and every refusal (and
# its text) is theirs. Nothing is kept between two checks.


def _work_tree_git_dir(root: Path) -> str:
    """The Git directory of the work tree ``root`` is in; refuses what is not a work tree.

    ``rev-parse --is-inside-work-tree`` and ``--absolute-git-dir`` in one
    process: git answers them in the order asked, a line each. Anything but
    "true" and a directory is settled by the two processes this replaced.
    """

    both = _git(root, "rev-parse", "--is-inside-work-tree", "--absolute-git-dir", check=False)
    inside_work_tree, newline, git_dir = both.stdout.partition("\n")
    if both.returncode == 0 and inside_work_tree.strip() == "true" and newline and git_dir.strip():
        return git_dir.strip()
    inside = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise WorkpadConflictError("workpad is not a local Git repository")
    return _git(root, "rev-parse", "--absolute-git-dir").stdout.strip()


# What ``git init`` writes into a new work tree's own configuration, with the
# values it writes (the file mode, symlink, case and Unicode probes depend on
# the file system). ``_initialize_workpad_repository`` adds the four markers.
_GIT_INIT_CONFIG: dict[bytes, tuple[bytes, ...]] = {
    b"core.repositoryformatversion": (b"0",),
    b"core.filemode": (b"true", b"false"),
    b"core.bare": (b"false",),
    b"core.logallrefupdates": (b"true",),
    b"core.symlinks": (b"false",),
    b"core.ignorecase": (b"true",),
    b"core.precomposeunicode": (b"true", b"false"),
}
# Entries the ENVIRONMENT gives git (``GIT_CONFIG_COUNT``; an agent's shell
# sets ``credential.interactive``) in sections neither ``git config --get``
# nor ``git remote`` reads: git consults them only when it asks for a login.
_UNREAD_COMMAND_SECTIONS = (b"credential.",)


def ownership_config_proven(root: Path, project_id: str, gig_id: str) -> bool:
    """Whether ONE git process proves the four ownership markers and that no remote is configured.

    ``git config --list -z --show-scope`` lists every entry git itself reads
    for this repository (every scope, includes followed), each with its
    scope. ``True`` only when:

    * every entry of the repository (scope ``local``) is one ``git init``
      writes, with a value it writes, or one of the four markers with exactly
      the expected value;
    * every other entry comes from the environment (scope ``command``), in a
      section the old questions never read;
    * all four markers are there.

    Then no include directive exists in any scope (it would be an entry), so
    every ``local`` entry is in ``.git/config`` itself, the file ``git config
    --local --get`` reads: each marker's every value is the expected one. And
    nothing git reads names a remote, in a configuration that is otherwise a
    new repository's: ``git remote`` prints nothing.

    ``False`` proves nothing and refuses nothing (another key or value, a
    worktree, global or system entry, a listing that fails or does not parse,
    a git older than ``--show-scope``, no git): the caller asks the old
    questions.
    """

    executable = shutil.which("git")
    if executable is None:
        return False
    listed = subprocess.run(
        [executable, "-C", os.fspath(root), "config", "--list", "-z", "--show-scope"],
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        },
        capture_output=True,
        check=False,
        shell=False,
    )
    fields = listed.stdout.split(b"\0")
    # Each entry is ``<scope> NUL <key> LF <value> NUL``.
    if listed.returncode != 0 or fields.pop() != b"" or len(fields) % 2:
        return False
    markers = {
        b"user.name": WORKPAD_GIT_USER_NAME.encode("utf-8"),
        b"user.email": WORKPAD_GIT_USER_EMAIL.encode("utf-8"),
        b"gigai.project-id": project_id.encode("utf-8"),
        b"gigai.gig-id": gig_id.encode("utf-8"),
    }
    proven: set[bytes] = set()
    for scope, entry in zip(fields[0::2], fields[1::2]):
        key, has_value, value = entry.partition(b"\n")
        if scope == b"command":
            if not key.startswith(_UNREAD_COMMAND_SECTIONS):
                return False
        elif scope != b"local" or not has_value:
            return False
        elif key in markers:
            if value != markers[key]:
                return False
            proven.add(key)
        elif value not in _GIT_INIT_CONFIG.get(key, ()):
            return False
    return len(proven) == len(markers)


# One save resolved its workpad 5 to 10 times (the store functions each
# resolve again what their caller resolved), and every resolution ran the
# repository check. ``one_operation()`` marks one operation on one thread.
# Inside it a repeated resolution still loads the configuration, asks the
# registry, checks the authority paths and asks git about the target; only
# the repository check's git processes are not started again, and only when
#
# * this same operation already passed that check for the same workpad, ids
#   and flags, with the configuration proven by the one listing (so no file
#   but ``.git/config`` holds configuration the check read), and
# * the fingerprint taken now is the one taken then (the read scope's:
#   journal head, top-level names, the identity of ``.git``, ``tools`` and
#   ``handoffs``, the identity, size and times of ``.git/config``,
#   ``.gitignore`` and the layout marker; here also when each of those three
#   inodes last changed), or only the head moved, along a straight line of
#   commits none of which touched the layout marker or the ignore rules.
#
# Nothing is kept past the operation, in another thread, or by time. A pass
# is kept only when the fingerprint was the same before and after the check.
# The journal does not look at this scope: every journal read and write makes
# its own workpad check, as before.

_OPERATION = threading.local()


@contextmanager
def one_operation() -> Iterator[None]:
    """One operation on this thread: its workpad's repository check is asked of git once while nothing it reads changes.

    Also a decorator (``@one_operation()``). Inside another one it adds
    nothing and ends nothing: the outer operation goes on.
    """

    if getattr(_OPERATION, "passed", None) is not None:
        yield
        return
    passed: dict[tuple[object, ...], tuple[object, ...]] = {}
    _OPERATION.passed = passed
    try:
        yield
    finally:
        if getattr(_OPERATION, "passed", None) is passed:
            _OPERATION.passed = None


def _operation_fingerprint(root: Path) -> tuple[object, ...] | None:
    """:func:`workpad_fingerprint`, and when the inode of each file it holds by size and time last changed."""

    fingerprint = workpad_fingerprint(root)
    if fingerprint is None:
        return None
    changed: list[int | None] = []
    for name in (".git/config", ".gitignore", WORKPAD_LAYOUT_PATH):
        try:
            changed.append(os.lstat(root / name).st_ctime_ns)
        except OSError:
            changed.append(None)
    return (*fingerprint, *changed)


def workpad_layout_version(root: Path, *, project_id: str, gig_id: str) -> int:
    """Return the admitted layout version without treating an unknown marker as v1."""

    marker = root / WORKPAD_LAYOUT_PATH
    if marker.is_symlink():
        raise WorkpadConflictError("workpad layout marker is redirected")
    if not marker.exists():
        return 1
    if not marker.is_file():
        raise WorkpadConflictError("workpad layout marker is invalid")
    # 0110-043: the walk below visits every journal commit. Its answer for a
    # journal head is kept in ``scratch/`` and carried to a later head by one
    # listing of only the new commits; the kept publisher is then admitted by
    # the same checks. Whatever the kept answer cannot settle, and every
    # refusal, is the walk's.
    head = _journal_head(root)
    kept = _kept_layout_publisher(root, project_id, gig_id, head) if head is not None else None
    if kept is not None and head is not None:
        commit, marker_blob, read = kept
        try:
            version = _admitted_layout_version(root, marker, project_id, gig_id, commit, read)
        except WorkpadError:
            pass
        else:
            _keep_layout_publisher(root, project_id, gig_id, head, commit, marker_blob)
            return version
    # A shaped working-tree marker is not authority.  Resolve its one
    # immutable publisher and use the committed bytes for admission.
    walked = _git(root, "log", "--format=%H", *(() if head is None else (head,)), "--", WORKPAD_LAYOUT_PATH, check=False)
    publishers = [line for line in walked.stdout.splitlines() if line]
    if len(publishers) != 1:
        raise WorkpadConflictError("workpad layout marker is invalid") from ValueError()
    commit = publishers[0]
    version = _admitted_layout_version(
        root, marker, project_id, gig_id, commit, lambda path: _git_bytes(root, "show", f"{commit}:{path}")
    )
    if head is not None and _layout_check_path(root, create=True) is not None:
        found = _git_blobs(root, (f"{head}:{WORKPAD_LAYOUT_PATH}",))
        if found is not None and found[0] is not None:
            _keep_layout_publisher(root, project_id, gig_id, head, commit, found[0][0])
    return version


def _admitted_layout_version(
    root: Path,
    marker: Path,
    project_id: str,
    gig_id: str,
    commit: str,
    read: Callable[[str], bytes],
) -> int:
    """Admit the layout marker as published by ``commit``; ``read`` gives that commit's bytes of a path."""

    try:
        from .canonical import digest_imported_bytes, parse_json_bytes, parse_json_front_matter

        changed = _git(root, "show", "--format=", "--name-only", commit).stdout.splitlines()
        handoffs = [item for item in changed if item.startswith("handoffs/") and item.endswith(".txt")]
        if len(handoffs) != 1 or WORKPAD_LAYOUT_PATH not in changed or ".gitignore" not in changed:
            raise ValueError
        metadata, _body = parse_json_front_matter(read(handoffs[0]))
        marker_bytes = read(WORKPAD_LAYOUT_PATH)
        ignore_bytes = read(".gitignore")
        if metadata.get("transition") != "workpad_layout_migrated" or metadata.get("gig_id") != gig_id:
            raise ValueError
        references = metadata.get("artifact_refs")
        if not isinstance(references, list):
            raise ValueError

        expected_refs = {
            WORKPAD_LAYOUT_PATH: digest_imported_bytes(marker_bytes),
            ".gitignore": digest_imported_bytes(ignore_bytes),
        }
        if {
            item.get("path"): item.get("content_sha256")
            for item in references
            if isinstance(item, dict)
        } != expected_refs:
            raise ValueError
        if marker.read_bytes() != marker_bytes or (root / ".gitignore").read_bytes() != ignore_bytes:
            raise ValueError
        payload = parse_json_bytes(marker_bytes)
    except (OSError, ValueError) as exc:
        raise WorkpadConflictError("workpad layout marker is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "layout_version", "project_id", "gig_id", "ignore_sha256"
    }:
        raise WorkpadConflictError("workpad layout marker is malformed")
    if (
        payload.get("schema_version") != "2.0"
        or payload.get("layout_version") != 2
        or payload.get("project_id") != project_id
        or payload.get("gig_id") != gig_id
    ):
        raise WorkpadConflictError("workpad layout marker identity is invalid")
    if payload.get("ignore_sha256") != digest_imported_bytes(WORKPAD_V2_GITIGNORE):
        raise WorkpadConflictError("workpad layout marker ignore policy is invalid")
    return 2


# --- 0110-043: the layout marker's publisher, kept at the journal head ------
#
# ``scratch/workpad-layout-check.json`` says: at journal head H the walk found
# exactly one publisher P of the layout marker, whose blob there is B.
# ``scratch/`` is ignored by git in both layouts and never transferred;
# deleting the file is always safe (the next check walks once and writes it
# again). It is a cache, not a defence: it only ever replaces the walk, never
# an admission check, and it is used only when all of this holds:
#
# * it is this schema's, this project's and this Gig's, and well formed;
# * the head is H, or descends from H in a straight line of commits none of
#   which touched the marker (``_straight_chain``: one listing of only the
#   new commits, limited to the marker's path);
# * the marker's blob at the head is B and is P's blob of it.
#
# P is then admitted exactly as a walked publisher is (its handoff, digests,
# the working files' bytes, the marker's identity). Anything else, including
# any refusal on the way, is settled by the full walk with its own errors.

LAYOUT_CHECK_DIRECTORY = "scratch"
LAYOUT_CHECK_FILENAME = "workpad-layout-check.json"
LAYOUT_CHECK_SCHEMA = "workpad-layout-check/1"
_LAYOUT_CHECK_MAX_BYTES = 4096


def _is_object_id(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(character in "0123456789abcdef" for character in value)


def _journal_head(root: Path) -> str | None:
    """The journal head commit: from the ``.git`` files, or from git when only it can tell; ``None`` when there is none."""

    head = workpad_head_without_git(root)
    if head is None:
        head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).stdout.strip()
    return head if _is_object_id(head) else None


def _layout_check_path(root: Path, *, create: bool) -> Path | None:
    return scratch_cache_path(root, LAYOUT_CHECK_FILENAME, create=create)


def scratch_cache_path(root: Path, filename: str, *, create: bool) -> Path | None:
    """Where a kept-at-head file of this workpad lives in its private ``scratch/``; ``None`` when it cannot be used.

    Never through a redirected ``scratch/`` or a name that is not a plain
    file. Without ``create`` the file must exist; with it the directory is
    made when missing.
    """

    directory = root / LAYOUT_CHECK_DIRECTORY
    try:
        if directory.is_symlink():
            return None
        if not directory.is_dir():
            if not create or directory.exists():
                return None
            directory.mkdir(mode=0o700)
        path = directory / filename
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return None
        if not create and not path.exists():
            return None
    except OSError:
        return None
    return path


def _read_layout_check(root: Path, project_id: str, gig_id: str) -> tuple[str, str, str] | None:
    """The kept (head, publisher, marker blob) when the file is this Gig's and well formed."""

    path = _layout_check_path(root, create=False)
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            data = stream.read(_LAYOUT_CHECK_MAX_BYTES + 1)
        kept = json.loads(data) if len(data) <= _LAYOUT_CHECK_MAX_BYTES else None
    except (OSError, ValueError):
        return None
    if not isinstance(kept, dict) or set(kept) != {"schema", "project_id", "gig_id", "head", "publisher", "marker_blob"}:
        return None
    if kept["schema"] != LAYOUT_CHECK_SCHEMA or kept["project_id"] != project_id or kept["gig_id"] != gig_id:
        return None
    found = (kept["head"], kept["publisher"], kept["marker_blob"])
    if not all(_is_object_id(value) for value in found):
        return None
    return found


def _kept_layout_publisher(
    root: Path, project_id: str, gig_id: str, head: str
) -> tuple[str, str, Callable[[str], bytes]] | None:
    """The marker's one publisher as kept for ``head``, its marker blob, and a reader of its bytes; ``None``: walk."""

    kept = _read_layout_check(root, project_id, gig_id)
    if kept is None:
        return None
    kept_head, commit, marker_blob = kept
    if kept_head != head:
        between = _straight_chain(root, kept_head, head, WORKPAD_LAYOUT_PATH)
        if between is None or any(names for _commit, names in between):
            return None
    blobs: dict[str, bytes] = {}

    def read(path: str) -> bytes:
        # One ``git cat-file --batch`` for the publisher's three files and
        # the marker at the head, on the first ask (the handoff's name).
        if not blobs:
            names = tuple(dict.fromkeys((path, WORKPAD_LAYOUT_PATH, ".gitignore")))
            found = _git_blobs(root, (f"{head}:{WORKPAD_LAYOUT_PATH}", *(f"{commit}:{name}" for name in names)))
            objects = [item for item in found or () if item is not None]
            if len(objects) != len(names) + 1:
                raise WorkpadConflictError("Git workpad object lookup failed")
            published = dict(zip(names, objects[1:]))
            if not published[WORKPAD_LAYOUT_PATH][0] == objects[0][0] == marker_blob:
                raise WorkpadConflictError("workpad layout marker is invalid")
            blobs.update((name, content) for name, (_blob, content) in published.items())
        if path not in blobs:
            raise WorkpadConflictError("Git workpad object lookup failed")
        return blobs[path]

    return commit, marker_blob, read


def _keep_layout_publisher(root: Path, project_id: str, gig_id: str, head: str, commit: str, marker_blob: str) -> None:
    """Record the proven publisher for ``head``; a failure to write changes nothing but the next check."""

    if _read_layout_check(root, project_id, gig_id) == (head, commit, marker_blob):
        return
    path = _layout_check_path(root, create=True)
    if path is None:
        return
    content = json.dumps(
        {
            "schema": LAYOUT_CHECK_SCHEMA,
            "project_id": project_id,
            "gig_id": gig_id,
            "head": head,
            "publisher": commit,
            "marker_blob": marker_blob,
        },
        sort_keys=True,
    ).encode("ascii")
    staged: str | None = None
    try:
        descriptor, staged = tempfile.mkstemp(prefix=f".{LAYOUT_CHECK_FILENAME}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(staged, path)
    except OSError:
        if staged is not None:
            try:
                os.unlink(staged)
            except OSError:
                pass


def _git_blobs(root: Path, names: tuple[str, ...]) -> list[tuple[str, bytes] | None] | None:
    """Each named blob's id and bytes from one ``git cat-file --batch``; ``None`` for a name that is not a blob."""

    executable = shutil.which("git")
    if executable is None:
        raise WorkpadUnavailableError("Git executable is unavailable")
    if any("\n" in name for name in names):
        return None
    completed = subprocess.run(
        [executable, "-C", os.fspath(root), "cat-file", "--batch"],
        input="".join(f"{name}\n" for name in names).encode("utf-8"),
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        },
        capture_output=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        return None
    output = completed.stdout
    found: list[tuple[str, bytes] | None] = []
    position = 0
    for _name in names:
        end = output.find(b"\n", position)
        if end < 0:
            return None
        header = output[position:end].split(b" ")
        position = end + 1
        if len(header) != 3 or not header[2].isdigit():
            found.append(None)  # "<name> missing" and the like
            continue
        size = int(header[2])
        content = output[position:position + size]
        if len(content) != size or output[position + size:position + size + 1] != b"\n":
            return None
        position += size + 1
        blob = header[0].decode("ascii", "replace")
        found.append((blob, content) if header[1] == b"blob" and _is_object_id(blob) else None)
    return found if position == len(output) else None


# --- 0110-044: what the other kept-at-head files are built from --------------
#
# ``index`` keeps the journal's entries and ``journal`` keeps the publishers of
# every path, each for a journal head in ``scratch/``, under the rules of the
# layout check above: a cache, never a defence; carried to a later head only
# along a straight line of commits; any doubt is the full computation.


def journal_head(root: Path) -> str | None:
    """The journal head commit (the ``.git`` files, or git when only it can tell); ``None`` when there is none."""

    return _journal_head(root)


def read_git_blobs(root: Path, names: tuple[str, ...]) -> list[tuple[str, bytes] | None] | None:
    """Each named blob's id and bytes from one ``git cat-file --batch``; ``None`` for a name that is not a blob."""

    return _git_blobs(root, names)


def straight_history(
    root: Path, old: str | None, new: str, *only: str
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...] | None:
    """Each commit after ``old`` up to ``new`` (``old`` ``None``: from the first commit), newest first, with what it changed.

    A change is ``(status, path)`` as ``git log --name-status --no-renames``
    gives it (``A`` added, ``M`` modified, ``D`` deleted, ...): a caller that
    needs "only additions" can see it. ``None`` unless the commits are a
    straight line (one parent each, down to ``old`` or to a first commit with
    none), or when git cannot list them or a path is not UTF-8. With ``only``
    every commit is still listed and its changes are limited to those paths.
    One ``git log``, whose cost is the commits listed.
    """

    if not _is_object_id(new) or not (old is None or _is_object_id(old)):
        return None
    limited = (("--full-history", "--sparse"), ("--", *only)) if only else ((), ())
    try:
        listing = _git_bytes(
            root, "log", "-z", "--format=%x01%H %P", "--name-status", "--no-renames", *limited[0],
            new if old is None else f"{old}..{new}", *limited[1],
        ).decode("utf-8")
    except (WorkpadConflictError, UnicodeDecodeError):
        return None
    chain: list[tuple[str, list[str], list[tuple[str, str]]]] = []
    status: str | None = None
    for chunk in listing.split("\x00"):
        if status is not None:
            chain[-1][2].append((status, chunk))
            status = None
        elif not chunk:
            continue
        elif chunk[0] == "\x01":
            commit, _space, parents = chunk[1:].partition(" ")
            chain.append((commit, parents.split(), []))
        elif chain:
            status = chunk[1:] if chunk[0] == "\n" else chunk
        else:
            return None
    if status is not None or not chain or chain[0][0] != new or chain[-1][1] != ([] if old is None else [old]):
        return None
    if any(parents != [chain[index + 1][0]] for index, (_commit, parents, _changes) in enumerate(chain[:-1])):
        return None
    return tuple((commit, tuple(changes)) for commit, _parents, changes in chain)


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
    tolerate_invalid_registry_rows: bool = False,
) -> BoundProject:
    try:
        try:
            target = _resolve_target(requested_target, cwd=cwd)
        except GitTargetError:
            # An explicitly initialized non-Git target is still a valid implicit
            # target for commands run from that directory, or from a subfolder
            # of it.  resolve_target deliberately rejects implicit non-Git
            # paths because it has no registry context; use the registry only
            # to recognize an already-bound directory (walking up from cwd to
            # find it) and never to infer a sibling or unrelated target.
            if requested_target is not None:
                raise
            current = (cwd or Path.cwd()).resolve(strict=True)
            registry, _ = open_project_registry(
                home,
                create=False,
                tolerate_invalid_rows=tolerate_invalid_registry_rows,
            )
            found_root: Path | None = None
            with registry.transaction() as transaction:
                for candidate in (current, *current.parents):
                    record = transaction.find_target(candidate)
                    if record is not None and record.target_kind == "non-git":
                        found_root = candidate
                        break
            if found_root is None:
                raise
            target = ResolvedTarget(
                requested_path=current,
                requested_identity=current,
                root=found_root,
                kind="non-git",
            )
        registry, _ = open_project_registry(
            home,
            create=False,
            tolerate_invalid_rows=tolerate_invalid_registry_rows,
        )
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
        raise WorkpadConflictError(
            "registered workpad is redirected outside its authority"
        )
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


def _git_bytes(root: Path, *args: str) -> bytes:
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
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise WorkpadConflictError("Git workpad object lookup failed")
    return completed.stdout


__all__ = [
    "BoundProject",
    "EditorInvocationError",
    "LAYOUT_CHECK_DIRECTORY",
    "LAYOUT_CHECK_FILENAME",
    "LAYOUT_CHECK_SCHEMA",
    "NoActiveGigError",
    "OpenResult",
    "PROVISION_FAILPOINTS",
    "ProvisionedWorkpad",
    "RUN_LOCAL_ARTIFACT_EXCLUDES",
    "ResolvedWorkpad",
    "WORKPAD_GITIGNORE",
    "WORKPAD_V2_GITIGNORE",
    "WORKPAD_LAYOUT_PATH",
    "WORKPAD_GIT_USER_EMAIL",
    "WORKPAD_GIT_USER_NAME",
    "WorkpadConflictError",
    "WorkpadError",
    "WorkpadPermissionError",
    "WorkpadUnavailableError",
    "committed_read_cache",
    "committed_read_cache_active",
    "ensure_run_local_artifact_excludes",
    "journal_head",
    "layout_paths_touched",
    "one_operation",
    "open_locations",
    "ownership_config_proven",
    "paths_committed_between",
    "provision_workpad",
    "read_cache_key_lock",
    "read_git_blobs",
    "read_still_holds",
    "repository_check_holds",
    "scratch_cache_path",
    "straight_commits_between",
    "straight_history",
    "register_existing_workpad",
    "resolve_bound_project",
    "resolve_workpad",
    "select_active_workpad",
    "workpad_fingerprint",
    "workpad_head_without_git",
    "workpad_layout_version",
]
