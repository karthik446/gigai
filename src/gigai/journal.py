"""Durable private-Git semantic journal for an already provisioned workpad."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Iterator

from .canonical import EntityPrefix, digest_owned_text, render_json_front_matter, validate_entity_id
from .diagnostics import run_mount_probes
from .workpad import WORKPAD_GITIGNORE, WORKPAD_GIT_USER_EMAIL, WORKPAD_GIT_USER_NAME


LOCK_FILENAME = "gigai-writer.lock"
LOCK_TIMEOUT_SECONDS = 10.0
SEQUENCE_TRAILER = "GigAI-Handoff-Sequence"
HANDOFF_TRAILER = "GigAI-Handoff"
TRANSITIONS = frozenset({"creation_started", "gig_proposal_ready", "gig_proposal_revised", "gig_proposal_approved", "gig_proposal_rejected", "run_started", "goal_started", "goal_completed", "goal_failed", "goal_blocked", "gate_waiting", "gate_continued", "recovery_followed", "run_succeeded", "run_failed", "run_cancelled", "run_interrupted", "gig_accepted", "gig_closed"})
JournalObserver = Callable[[str], None]


class JournalError(RuntimeError):
    code = "journal_error"


class JournalConflictError(JournalError):
    code = "journal_conflict"


class JournalReconciliationRequired(JournalError):
    code = "journal_reconciliation_required"


class InterprocessLockUnavailable(JournalError):
    code = "interprocess_lock_unavailable"


@dataclass(frozen=True)
class JournalEntry:
    sequence: int
    handoff_id: str
    path: Path
    commit: str


@dataclass(frozen=True)
class ReconciliationResult:
    reconciled: bool
    sequence: int | None
    commit: str | None


def record_transition(*, workpad: Path, project_id: str, gig_id: str, handoff_id: str, transition: str, body: str, lock_timeout_seconds: float = LOCK_TIMEOUT_SECONDS, observer: JournalObserver | None = None) -> JournalEntry:
    """Durably record one caller-owned semantic transition under the writer lock."""

    observer = observer or (lambda _step: None)
    _validate_ids(project_id, gig_id, handoff_id)
    _validate_transition(transition, body)
    root = _validate_workpad(workpad, project_id, gig_id)
    _require_mount_probes(root)
    with _writer_lock(root / ".git" / LOCK_FILENAME, lock_timeout_seconds):
        mount = _mount_identity(root)
        head = _read_head(root)
        sequence, previous_handoff, previous_commit = _next_sequence(root, head)
        handoffs = root / "handoffs"
        handoffs.mkdir(mode=0o700, exist_ok=True)
        destination = handoffs / f"{sequence:012d}-{transition.replace('_', '-')}.txt"
        if destination.exists() or destination.is_symlink():
            raise JournalReconciliationRequired("next handoff path is already present and uncommitted")
        document = _render_handoff(sequence, gig_id, handoff_id, transition, body, previous_commit)
        temporary = _write_atomic_temporary(handoffs, document)
        observer("before_replace")
        os.replace(temporary, destination)
        _fsync_directory(handoffs)
        observer("after_replace")
        _assert_mount_identity(root, mount)
        observer("before_commit")
        _commit_handoff(root, destination, sequence, handoff_id, previous_handoff, transition)
        _assert_mount_identity(root, mount)
        commit = _head_commit(root)
        observer("after_commit")
        return JournalEntry(sequence, handoff_id, destination, commit)


def reconcile_journal(*, workpad: Path, project_id: str, gig_id: str, lock_timeout_seconds: float = LOCK_TIMEOUT_SECONDS) -> ReconciliationResult:
    """Explicitly reconcile one interrupted writer state; normal writes never scan."""

    _validate_ids(project_id, gig_id, None)
    root = _validate_workpad(workpad, project_id, gig_id)
    _require_mount_probes(root)
    with _writer_lock(root / ".git" / LOCK_FILENAME, lock_timeout_seconds):
        handoffs = root / "handoffs"
        if not handoffs.exists():
            return ReconciliationResult(False, None, _head_commit(root, required=False))
        for temporary in handoffs.glob(".gigai-handoff-*.tmp"):
            temporary.unlink()
        head = _read_head(root)
        sequence, previous_handoff, _previous_commit = _next_sequence(root, head)
        candidates = sorted(handoffs.glob(f"{sequence:012d}-*.txt"))
        if not candidates:
            return ReconciliationResult(False, None, _head_commit(root, required=False))
        if len(candidates) != 1:
            raise JournalReconciliationRequired("journal recovery found ambiguous next handoffs")
        path = candidates[0]
        metadata = _read_handoff(path)
        if metadata.get("sequence") != sequence or metadata.get("gig_id") != gig_id:
            raise JournalReconciliationRequired("journal recovery found an invalid next handoff")
        handoff_id = metadata.get("handoff_id")
        transition = metadata.get("transition")
        if not isinstance(handoff_id, str) or not isinstance(transition, str):
            raise JournalReconciliationRequired("journal recovery handoff lacks canonical identity")
        _commit_handoff(root, path, sequence, handoff_id, previous_handoff, transition)
        return ReconciliationResult(True, sequence, _head_commit(root))


@contextmanager
def _writer_lock(path: Path, timeout_seconds: float) -> Iterator[None]:
    if os.name != "posix":
        raise InterprocessLockUnavailable("interprocess_lock_unavailable: POSIX flock is required")
    if timeout_seconds < 0:
        raise InterprocessLockUnavailable("interprocess_lock_unavailable: timeout must be non-negative")
    import fcntl

    deadline = time.monotonic() + timeout_seconds
    with path.open("a+b") as stream:
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise InterprocessLockUnavailable(
                        "interprocess_lock_unavailable: writer lock timeout "
                        f"owner={_lock_owner(path)}"
                    ) from None
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        try:
            stream.seek(0)
            stream.truncate()
            stream.write(f"pid={os.getpid()}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
            yield
        finally:
            stream.seek(0)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _validate_ids(project_id: str, gig_id: str, handoff_id: str | None) -> None:
    try:
        validate_entity_id(project_id, expected_prefix=EntityPrefix.PROJECT)
        validate_entity_id(gig_id, expected_prefix=EntityPrefix.GIG)
        if handoff_id is not None:
            validate_entity_id(handoff_id, expected_prefix=EntityPrefix.HANDOFF)
    except Exception as exc:
        raise JournalConflictError("journal caller supplied invalid canonical ownership IDs") from exc


def _lock_owner(path: Path) -> str:
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"
    return value or "unknown"


def _require_mount_probes(root: Path) -> None:
    failed = [check.id for check in run_mount_probes(root) if check.status != "PASS"]
    if failed:
        raise InterprocessLockUnavailable(
            "interprocess_lock_unavailable: configured workpad mount probe failed "
            + ",".join(failed)
        )


def _validate_transition(transition: str, body: str) -> None:
    if transition not in TRANSITIONS or type(body) is not str or not body.strip():
        raise JournalConflictError("journal transition or body is invalid")


def _validate_workpad(workpad: Path, project_id: str, gig_id: str) -> Path:
    root = workpad.resolve(strict=True)
    if root != workpad or root.is_symlink() or not root.is_dir():
        raise JournalConflictError("journal workpad is unavailable or redirected")
    expected = {"user.name": WORKPAD_GIT_USER_NAME, "user.email": WORKPAD_GIT_USER_EMAIL, "gigai.project-id": project_id, "gigai.gig-id": gig_id}
    if (root / ".gitignore").read_bytes() != WORKPAD_GITIGNORE:
        raise JournalConflictError("journal workpad ignore rules differ from G05")
    for key, value in expected.items():
        observed = _git(root, "config", "--local", "--get", key, check=False)
        if observed.returncode != 0 or observed.stdout.rstrip("\n") != value:
            raise JournalConflictError("journal workpad ownership marker mismatches")
    if _git(root, "remote").stdout.strip():
        raise JournalConflictError("journal workpad has a remote")
    return root


def _read_head(root: Path) -> str | None:
    result = _git(root, "rev-parse", "--verify", "HEAD", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _head_commit(root: Path, *, required: bool = True) -> str | None:
    value = _read_head(root)
    if value is None and required:
        raise JournalReconciliationRequired("journal head is unexpectedly unborn")
    return value


def _next_sequence(root: Path, head: str | None) -> tuple[int, str | None, str | None]:
    if head is None:
        return 1, None, None
    message = _git(root, "show", "--format=%B", "--no-patch", "HEAD").stdout
    trailers = _trailers(message)
    sequence_value = trailers.get(SEQUENCE_TRAILER)
    handoff_id = trailers.get(HANDOFF_TRAILER)
    if sequence_value is None or handoff_id is None or not sequence_value.isdigit() or len(sequence_value) != 12:
        raise JournalReconciliationRequired("journal head lacks valid handoff trailers")
    sequence = int(sequence_value)
    try:
        validate_entity_id(handoff_id, expected_prefix=EntityPrefix.HANDOFF)
    except Exception as exc:
        raise JournalReconciliationRequired("journal head has an invalid handoff trailer") from exc
    path = _git(root, "show", "--format=", "--name-only", "HEAD").stdout
    if not any(name.startswith(f"handoffs/{sequence_value}-") for name in path.splitlines()):
        raise JournalReconciliationRequired("journal head does not commit its named handoff")
    return sequence + 1, handoff_id, head


def _trailers(message: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in message.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            if key in {SEQUENCE_TRAILER, HANDOFF_TRAILER}:
                result[key] = value
    return result


def _render_handoff(sequence: int, gig_id: str, handoff_id: str, transition: str, body: str, previous_commit: str | None) -> bytes:
    normalized_body = body.rstrip("\n") + "\n"
    metadata: dict[str, object] = {"schema_version": "1.0", "handoff_id": handoff_id, "sequence": sequence, "gig_id": gig_id, "gig_version": None, "goal_id": None, "goal_version": None, "run_id": None, "transition": transition, "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "actor": {"kind": "gigai", "id": "journal", "model_target": None}, "parent_handoff_ids": [], "previous_journal_commit": previous_commit, "goal_graph_sha256": None, "source_manifest_sha256": None, "outcome": None, "evidence": [], "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "not_applicable"}, "body_sha256": digest_owned_text(normalized_body)}
    return render_json_front_matter(metadata, normalized_body)


def _write_atomic_temporary(directory: Path, data: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".gigai-handoff-", suffix=".tmp", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _commit_handoff(root: Path, path: Path, sequence: int, handoff_id: str, previous_handoff: str | None, transition: str) -> None:
    _git(root, "add", "--", ".gitignore", os.fspath(path.relative_to(root)))
    message = f"journal: {transition.replace('_', ' ')}\n\n{SEQUENCE_TRAILER}: {sequence:012d}\n{HANDOFF_TRAILER}: {handoff_id}"
    if previous_handoff is not None:
        message += f"\nGigAI-Previous-Handoff: {previous_handoff}"
    _git(root, "commit", "--quiet", "-m", message)


def _read_handoff(path: Path) -> dict[str, object]:
    from .canonical import parse_json_front_matter
    metadata, _body = parse_json_front_matter(path.read_bytes())
    return metadata


def _mount_identity(root: Path) -> tuple[int, int]:
    stat_result = root.stat()
    return stat_result.st_dev, stat_result.st_ino


def _assert_mount_identity(root: Path, expected: tuple[int, int]) -> None:
    if _mount_identity(root) != expected:
        raise JournalConflictError("configured journal mount changed during mutation")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git")
    if executable is None:
        raise JournalConflictError("Git executable is unavailable")
    result = subprocess.run([executable, "-C", os.fspath(root), *args], env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0"}, capture_output=True, text=True, check=False, shell=False)
    if check and result.returncode != 0:
        raise JournalConflictError(f"journal Git operation failed: {result.stderr.strip()}")
    return result


__all__ = ["InterprocessLockUnavailable", "JournalConflictError", "JournalEntry", "JournalError", "JournalReconciliationRequired", "ReconciliationResult", "record_transition", "reconcile_journal"]
