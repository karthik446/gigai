"""G19's narrowly bounded, explicitly authorized target effect."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable, Mapping
import uuid

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
    validate_entity_id,
)
from .journal import JournalArtifact, JournalEntry, record_transition
from .project_binding import binding_path, load_project_binding
from .review import validate_review_loop_artifacts
from .target_binding import (
    GitTargetError,
    _git,
    assert_target_identity_stable,
    resolve_target,
)
from .validators import validate_serialized_contract, validate_target_effect
from .workpad import ResolvedWorkpad


TARGET_EFFECT_PATH = "manifests/target-effects"
TARGET_EFFECT_TRANSITIONS = {
    "effect_authorized": "target_effect_authorized",
    "prepared": "target_effect_prepared",
    "exposed": "target_effect_exposed",
    "verified": "target_effect_verified",
    "applied": "target_effect_applied",
    "refused": "target_effect_refused",
    "failed": "target_effect_failed",
    "cancelled": "target_effect_cancelled",
    "rolled_back": "target_effect_rolled_back",
    "blocked": "target_effect_blocked",
}
TERMINAL_STATES = frozenset(
    {"applied", "refused", "failed", "cancelled", "rolled_back", "blocked"}
)
TargetEffectObserver = Callable[[str], None]
TargetEffectClock = Callable[[], str]


class TargetEffectError(RuntimeError):
    """A target effect cannot safely continue."""

    code = "target_effect_error"


class TargetEffectRefusedError(TargetEffectError):
    code = "target_effect_refused"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class TargetEffectRecoveryRequired(TargetEffectError):
    code = "target_effect_recovery_required"


@dataclass(frozen=True)
class TargetEffectResult:
    record: dict[str, object]
    journal_entries: tuple[JournalEntry, ...]


def authorize_target_effect(
    *,
    resolved: ResolvedWorkpad,
    proposal_id: str,
    relative_target_path: str,
    source_artifact_path: str,
    operator: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    clock: TargetEffectClock | None = None,
) -> TargetEffectResult:
    """Create a separate operator authorization without changing the target."""

    _require_git_workpad(resolved)
    _validate_proposal_id(proposal_id)
    operator = _validate_operator(operator)
    _assert_approved_context(resolved, proposal_id)
    target = _observe_target(resolved)
    if target["status_bytes"]:
        raise TargetEffectRefusedError("target must be clean before authorization", code="target_dirty")
    target_path = _safe_target_file(resolved.target_root, relative_target_path)
    source_path = _safe_workpad_file(resolved.path, source_artifact_path)
    source_bytes = source_path.read_bytes()
    target_bytes = target_path.read_bytes()
    _require_document_bytes(source_bytes, "source artifact")
    _require_document_bytes(target_bytes, "target file")
    mode = stat.S_IMODE(target_path.stat().st_mode)
    now = clock or _now
    effect_id = _new_effect_id(uuid_factory)
    source_ref = _artifact_ref(source_artifact_path, source_bytes, _media_type(source_artifact_path))
    authorization = {
        "gig_proposal_id": proposal_id,
        "operator": dict(operator),
        "target_binding_sha256": target["binding_sha256"],
        "relative_target_path": relative_target_path,
        "source_artifact_sha256": source_ref["content_sha256"],
        "expected_before_sha256": digest_imported_bytes(target_bytes),
        "expected_after_sha256": source_ref["content_sha256"],
        "authorized_at": now(),
        "cancellation_policy": "before_exposure_only",
        "commit_policy": "leave_uncommitted",
    }
    authorization["authorization_sha256"] = _digest_without(authorization, "authorization_sha256")
    patch_identity = {
        "relative_target_path": relative_target_path,
        "source_artifact_sha256": source_ref["content_sha256"],
        "expected_before_sha256": authorization["expected_before_sha256"],
        "expected_after_sha256": authorization["expected_after_sha256"],
        "expected_file_mode": mode,
    }
    patch_identity["descriptor_sha256"] = _digest_without(patch_identity, "descriptor_sha256")
    record = {
        "schema_version": "1.0",
        "effect_id": effect_id,
        "effect_version": 1,
        "state": "effect_authorized",
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "gig_proposal_id": proposal_id,
        "target": {
            "kind": "git",
            "binding_sha256": target["binding_sha256"],
            "repository_identity_sha256": target["repository_identity_sha256"],
            "git_head": target["git_head"],
        },
        "operator": dict(operator),
        "effect_kind": "write_target",
        "operation": "replace_file",
        "relative_target_path": relative_target_path,
        "source_artifact": source_ref,
        "expected_before_sha256": authorization["expected_before_sha256"],
        "expected_after_sha256": authorization["expected_after_sha256"],
        "expected_file_mode": mode,
        "authorization": authorization,
        "cancellation_policy": "before_exposure_only",
        "commit_policy": "leave_uncommitted",
        "patch_identity": patch_identity,
        "target_before_manifest": None,
        "target_after_manifest": None,
        "created_at": now(),
        "updated_at": now(),
        "terminal_reason": None,
    }
    return _persist(
        resolved,
        record,
        artifacts=(JournalArtifact(source_artifact_path, source_bytes),),
        clock=now,
    )


def prepare_target_effect(
    *,
    resolved: ResolvedWorkpad,
    effect_id: str,
    clock: TargetEffectClock | None = None,
) -> TargetEffectResult:
    """Seal the action-time before manifest and staged policy without exposure."""

    record = _load_record(resolved, effect_id)
    if record["state"] != "effect_authorized":
        raise TargetEffectError("only effect_authorized records can be prepared")
    try:
        _revalidate_record_context(resolved, record)
        target_path = _safe_target_file(resolved.target_root, str(record["relative_target_path"]))
        source_path = _safe_workpad_file(resolved.path, str(record["source_artifact"]["path"]))
        target = _observe_target(resolved)
        source_bytes = source_path.read_bytes()
        target_bytes = target_path.read_bytes()
        _require_clean_expected_target(record, target, target_path, target_bytes)
        if digest_imported_bytes(source_bytes) != record["expected_after_sha256"]:
            raise TargetEffectRefusedError("source artifact digest changed before preparation", code="source_digest_mismatch")
        before_bytes, before_ref = _manifest(
            resolved,
            record,
            target,
            relative_path=str(record["relative_target_path"]),
            file_bytes=target_bytes,
            mode=stat.S_IMODE(target_path.stat().st_mode),
            phase="before",
        )
        now = clock or _now
        next_record = _next_record(record, state="prepared", clock=now)
        next_record["target_before_manifest"] = before_ref
        return _persist(
            resolved,
            next_record,
            artifacts=(JournalArtifact(str(before_ref["path"]), before_bytes),),
            clock=now,
        )
    except TargetEffectRefusedError as exc:
        _persist_terminal(resolved, record, "refused", exc.code, clock)
        raise


def apply_target_effect(
    *,
    resolved: ResolvedWorkpad,
    effect_id: str,
    observer: TargetEffectObserver | None = None,
    clock: TargetEffectClock | None = None,
) -> TargetEffectResult:
    """Atomically expose, verify, and terminally apply one prepared effect."""

    observer = observer or (lambda _step: None)
    record = _load_record(resolved, effect_id)
    if record["state"] == "applied":
        return TargetEffectResult(record, ())
    if record["state"] != "prepared":
        raise TargetEffectError("only prepared records can be applied")
    try:
        _revalidate_record_context(resolved, record)
        target_path = _safe_target_file(resolved.target_root, str(record["relative_target_path"]))
        source_path = _safe_workpad_file(resolved.path, str(record["source_artifact"]["path"]))
        target = _observe_target(resolved)
        source_bytes = source_path.read_bytes()
        target_bytes = target_path.read_bytes()
        _require_clean_expected_target(record, target, target_path, target_bytes)
        if digest_imported_bytes(source_bytes) != record["expected_after_sha256"]:
            raise TargetEffectRefusedError("source artifact digest changed before exposure", code="source_digest_mismatch")
    except TargetEffectRefusedError as exc:
        _persist_terminal(resolved, record, "refused", exc.code, clock)
        raise
    _stage_and_replace(target_path, source_bytes, int(record["expected_file_mode"]), observer)

    now = clock or _now
    exposed = _next_record(record, state="exposed", clock=now)
    exposed_result = _persist(resolved, exposed, clock=now)
    observer("after_exposed_record")
    try:
        target = _observe_target(resolved)
        after_bytes = target_path.read_bytes()
        _require_after_target(record, target, target_path, after_bytes)
    except TargetEffectError:
        recovery = recover_target_effect(resolved=resolved, effect_id=effect_id, clock=now)
        return TargetEffectResult(recovery.record, exposed_result.journal_entries + recovery.journal_entries)

    after_bytes, after_ref = _manifest(
        resolved,
        record,
        target,
        relative_path=str(record["relative_target_path"]),
        file_bytes=after_bytes,
        mode=stat.S_IMODE(target_path.stat().st_mode),
        phase="after",
    )
    verified = _next_record(exposed, state="verified", clock=now)
    verified["target_after_manifest"] = after_ref
    verified_result = _persist(
        resolved,
        verified,
        artifacts=(JournalArtifact(str(after_ref["path"]), after_bytes),),
        clock=now,
    )
    observer("after_verified_record")
    applied = _next_record(verified, state="applied", clock=now)
    applied_result = _persist(
        resolved,
        applied,
        artifacts=(JournalArtifact(str(after_ref["path"]), after_bytes),),
        clock=now,
    )
    return TargetEffectResult(
        applied,
        exposed_result.journal_entries
        + verified_result.journal_entries
        + applied_result.journal_entries,
    )


def cancel_target_effect(
    *,
    resolved: ResolvedWorkpad,
    effect_id: str,
    clock: TargetEffectClock | None = None,
) -> TargetEffectResult:
    """Cancel only before exposure; post-exposure cancellation invokes recovery."""

    record = _load_record(resolved, effect_id)
    if record["state"] in TERMINAL_STATES:
        return TargetEffectResult(record, ())
    if record["state"] == "exposed":
        return recover_target_effect(resolved=resolved, effect_id=effect_id, clock=clock)
    if record["state"] not in {"effect_authorized", "prepared"}:
        raise TargetEffectError("target effect cannot be cancelled in its current state")
    now = clock or _now
    cancelled = _next_record(record, state="cancelled", clock=now)
    cancelled["terminal_reason"] = "operator_cancelled_before_exposure"
    return _persist(resolved, cancelled, clock=now)


def recover_target_effect(
    *,
    resolved: ResolvedWorkpad,
    effect_id: str,
    clock: TargetEffectClock | None = None,
) -> TargetEffectResult:
    """Resolve an interrupted prepared/exposed/verified effect fail-closed."""

    record = _load_record(resolved, effect_id)
    if record["state"] in {"effect_authorized", "prepared"}:
        if record["state"] == "effect_authorized":
            return TargetEffectResult(record, ())
        target_path = _safe_target_file(resolved.target_root, str(record["relative_target_path"]))
        target = _observe_target(resolved)
        current = target_path.read_bytes()
        if _is_before_state(record, target, target_path, current):
            return TargetEffectResult(record, ())
        reason = "ambiguous_state_after_prepared"
        return _persist_terminal(resolved, record, "blocked", reason, clock)
    if record["state"] == "exposed":
        target_path = _safe_target_file(resolved.target_root, str(record["relative_target_path"]))
        target = _observe_target(resolved)
        current = target_path.read_bytes()
        if _is_after_state(record, target, target_path, current):
            return _finish_after_exposure(resolved, record, target, current, clock)
        if _is_before_state(record, target, target_path, current):
            return _persist_terminal(resolved, record, "rolled_back", "before_state_restored", clock)
        return _persist_terminal(resolved, record, "blocked", "ambiguous_exposed_state", clock)
    if record["state"] == "verified":
        target_path = _safe_target_file(resolved.target_root, str(record["relative_target_path"]))
        target = _observe_target(resolved)
        current = target_path.read_bytes()
        if _is_after_state(record, target, target_path, current):
            now = clock or _now
            applied = _next_record(record, state="applied", clock=now)
            return _persist(resolved, applied, clock=now)
        return _persist_terminal(resolved, record, "blocked", "verified_state_changed", clock)
    return TargetEffectResult(record, ())


def _finish_after_exposure(
    resolved: ResolvedWorkpad,
    record: dict[str, object],
    target: dict[str, object],
    current: bytes,
    clock: TargetEffectClock | None,
) -> TargetEffectResult:
    now = clock or _now
    after_bytes, after_ref = _manifest(
        resolved,
        record,
        target,
        relative_path=str(record["relative_target_path"]),
        file_bytes=current,
        mode=int(record["expected_file_mode"]),
        phase="after",
    )
    verified = _next_record(record, state="verified", clock=now)
    verified["target_after_manifest"] = after_ref
    verified_result = _persist(
        resolved,
        verified,
        artifacts=(JournalArtifact(str(after_ref["path"]), after_bytes),),
        clock=now,
    )
    applied = _next_record(verified, state="applied", clock=now)
    applied_result = _persist(
        resolved,
        applied,
        artifacts=(JournalArtifact(str(after_ref["path"]), after_bytes),),
        clock=now,
    )
    return TargetEffectResult(
        applied,
        verified_result.journal_entries + applied_result.journal_entries,
    )


def _persist_terminal(
    resolved: ResolvedWorkpad,
    record: dict[str, object],
    state: str,
    reason: str,
    clock: TargetEffectClock | None,
) -> TargetEffectResult:
    now = clock or _now
    terminal = _next_record(record, state=state, clock=now)
    terminal["terminal_reason"] = reason
    return _persist(resolved, terminal, clock=now)


def _persist(
    resolved: ResolvedWorkpad,
    record: dict[str, object],
    *,
    artifacts: tuple[JournalArtifact, ...] = (),
    clock: TargetEffectClock | None = None,
) -> TargetEffectResult:
    report = validate_target_effect(record)
    if not report.valid:
        codes = ", ".join(item.code for item in report.findings)
        raise TargetEffectError(f"target-effect record failed validation: {codes}")
    path = f"{TARGET_EFFECT_PATH}/{record['effect_id']}.json"
    payload = canonical_json_bytes(record)
    entry = record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_handoff_id(uuid.uuid4),
        transition=TARGET_EFFECT_TRANSITIONS[str(record["state"])],
        body=f"Target effect {record['effect_id']} advanced to {record['state']}.",
        artifacts=(*artifacts, JournalArtifact(path, payload)),
    )
    return TargetEffectResult(record, (entry,))


def _load_record(resolved: ResolvedWorkpad, effect_id: str) -> dict[str, object]:
    _validate_effect_id(effect_id)
    path = resolved.path / TARGET_EFFECT_PATH / f"{effect_id}.json"
    if path.is_symlink() or not path.is_file():
        raise TargetEffectError("target-effect record is unavailable")
    payload = path.read_bytes()
    report = validate_target_effect(payload)
    if not report.valid:
        raise TargetEffectError("target-effect record is invalid")
    value = parse_json_bytes(payload)
    if not isinstance(value, dict):
        raise TargetEffectError("target-effect record is not an object")
    return value


def _next_record(record: Mapping[str, object], *, state: str, clock: TargetEffectClock) -> dict[str, object]:
    next_record = dict(record)
    next_record["state"] = state
    next_record["effect_version"] = int(record["effect_version"]) + 1
    next_record["updated_at"] = clock()
    if state not in TERMINAL_STATES:
        next_record["terminal_reason"] = None
    return next_record


def _revalidate_record_context(resolved: ResolvedWorkpad, record: Mapping[str, object]) -> None:
    _assert_approved_context(resolved, str(record["gig_proposal_id"]))
    target = _observe_target(resolved)
    expected = record["target"]
    if target["binding_sha256"] != expected["binding_sha256"]:
        raise TargetEffectRefusedError("target binding changed", code="target_binding_changed")
    if target["repository_identity_sha256"] != expected["repository_identity_sha256"]:
        raise TargetEffectRefusedError("repository identity changed", code="target_identity_changed")
    if target["git_head"] != expected["git_head"]:
        raise TargetEffectRefusedError("Git HEAD changed", code="target_head_changed")


def _assert_approved_context(resolved: ResolvedWorkpad, proposal_id: str) -> None:
    proposal_path = resolved.path / "manifests/gig-proposal.json"
    active_path = resolved.path / "manifests/active-gig-version.json"
    loop_path = resolved.path / "manifests/review-loop.json"
    for path in (proposal_path, active_path, loop_path):
        if path.is_symlink() or not path.is_file():
            raise TargetEffectRefusedError("required approved/review authority is unavailable", code="authority_missing")
    proposal_bytes = proposal_path.read_bytes()
    active_bytes = active_path.read_bytes()
    loop_bytes = loop_path.read_bytes()
    if not validate_serialized_contract("gig-proposal.schema.json", proposal_bytes).valid:
        raise TargetEffectRefusedError("active proposal is invalid", code="proposal_invalid")
    if not validate_serialized_contract("active-gig-version.schema.json", active_bytes).valid:
        raise TargetEffectRefusedError("active Gig version is invalid", code="active_version_invalid")
    loop_report = validate_review_loop_artifacts(resolved.path, loop_bytes)
    if not loop_report.valid:
        raise TargetEffectRefusedError("Review Loop artifacts are invalid", code="review_artifacts_invalid")
    proposal = parse_json_bytes(proposal_bytes)
    active = parse_json_bytes(active_bytes)
    loop = parse_json_bytes(loop_bytes)
    if not isinstance(proposal, Mapping) or not isinstance(active, Mapping) or not isinstance(loop, Mapping):
        raise TargetEffectRefusedError("approved/review authority is malformed", code="authority_malformed")
    if (
        proposal.get("status") != "approved"
        or proposal.get("proposal_id") != proposal_id
        or proposal.get("gig_id") != resolved.gig_id
        or proposal.get("project_id") != resolved.project_id
        or active.get("approved_proposal_id") != proposal_id
        or active.get("gig_id") != resolved.gig_id
        or loop.get("gig_id") != resolved.gig_id
        or loop.get("state") != "complete"
        or not loop.get("addressed_artifact_ids")
    ):
        raise TargetEffectRefusedError("approved proposal and complete addressed Review Loop are required", code="review_prerequisite_missing")
    for artifact_id in loop["addressed_artifact_ids"]:
        artifact_path = resolved.path / "addressed" / f"{artifact_id}.json"
        artifact = parse_json_bytes(artifact_path.read_bytes())
        if not isinstance(artifact, Mapping) or artifact.get("status") != "addressed":
            raise TargetEffectRefusedError("Review Loop has no complete addressed artifact", code="addressed_artifact_incomplete")


def _observe_target(resolved: ResolvedWorkpad) -> dict[str, object]:
    if resolved.target_kind != "git":
        raise TargetEffectRefusedError("G19 v1 requires a bound Git target", code="non_git_target")
    try:
        target = resolve_target(resolved.target_root)
        assert_target_identity_stable(target)
        root = target.root
        binding = load_project_binding(root)
        if binding.project_id != resolved.project_id:
            raise TargetEffectRefusedError("Git project binding changed", code="target_binding_changed")
        binding_bytes = binding_path(root).read_bytes()
        binding_sha256 = digest_imported_bytes(binding_bytes)
        head_result = _git(root, "rev-parse", "HEAD", check=False)
        if head_result.returncode != 0 or not head_result.stdout.strip():
            raise TargetEffectRefusedError("Git target has no committed HEAD", code="target_head_missing")
        status_result = _git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            text=False,
        )
        status_bytes = status_result.stdout
        identity = canonical_json_bytes(
            {"project_id": resolved.project_id, "binding_sha256": binding_sha256}
        )
        return {
            "binding_sha256": binding_sha256,
            "repository_identity_sha256": digest_imported_bytes(identity),
            "git_head": head_result.stdout.strip(),
            "status_bytes": status_bytes,
            "status_sha256": digest_imported_bytes(status_bytes) if status_bytes else None,
        }
    except (GitTargetError, OSError) as exc:
        raise TargetEffectRefusedError(f"Git target observation failed: {exc}", code="target_observation_failed") from exc


def _require_clean_expected_target(
    record: Mapping[str, object],
    target: Mapping[str, object],
    target_path: Path,
    target_bytes: bytes,
) -> None:
    if target["status_bytes"]:
        raise TargetEffectRefusedError("target worktree/index is not clean", code="target_dirty")
    if digest_imported_bytes(target_bytes) != record["expected_before_sha256"]:
        raise TargetEffectRefusedError("target before digest changed", code="before_digest_mismatch")
    if stat.S_IMODE(target_path.stat().st_mode) != record["expected_file_mode"]:
        raise TargetEffectRefusedError("target file mode changed", code="mode_mismatch")


def _require_after_target(
    record: Mapping[str, object],
    target: Mapping[str, object],
    target_path: Path,
    target_bytes: bytes,
) -> None:
    if digest_imported_bytes(target_bytes) != record["expected_after_sha256"]:
        raise TargetEffectRefusedError("target after digest mismatch", code="after_digest_mismatch")
    if stat.S_IMODE(target_path.stat().st_mode) != record["expected_file_mode"]:
        raise TargetEffectRefusedError("target after mode mismatch", code="mode_mismatch")
    entries = [item for item in target["status_bytes"].split(b"\0") if item]
    expected = str(record["relative_target_path"]).encode()
    if len(entries) != 1 or len(entries[0]) < 4 or entries[0][3:] != expected:
        raise TargetEffectRefusedError("target delta is not exactly the authorized file", code="target_delta_mismatch")


def _is_before_state(record: Mapping[str, object], target: Mapping[str, object], path: Path, data: bytes) -> bool:
    return (
        not target["status_bytes"]
        and digest_imported_bytes(data) == record["expected_before_sha256"]
        and stat.S_IMODE(path.stat().st_mode) == record["expected_file_mode"]
    )


def _is_after_state(record: Mapping[str, object], target: Mapping[str, object], path: Path, data: bytes) -> bool:
    try:
        _require_after_target(record, target, path, data)
    except (TargetEffectError, OSError):
        return False
    return True


def _manifest(
    resolved: ResolvedWorkpad,
    record: Mapping[str, object],
    target: Mapping[str, object],
    *,
    relative_path: str,
    file_bytes: bytes,
    mode: int,
    phase: str,
) -> tuple[bytes, dict[str, object]]:
    payload = {
        "schema_version": "1.0",
        "phase": phase,
        "effect_id": record["effect_id"],
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "target_identity_sha256": target["repository_identity_sha256"],
        "binding_sha256": target["binding_sha256"],
        "git_head": target["git_head"],
        "status_sha256": target["status_sha256"],
        "relative_target_path": relative_path,
        "file_mode": mode,
        "size_bytes": len(file_bytes),
        "content_sha256": digest_imported_bytes(file_bytes),
    }
    data = canonical_json_bytes(payload)
    path = f"{TARGET_EFFECT_PATH}/{record['effect_id']}-{phase}.json"
    return data, _artifact_ref(path, data, "application/json")


def _stage_and_replace(
    target_path: Path,
    data: bytes,
    mode: int,
    observer: TargetEffectObserver,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.gigai-effect-",
        suffix=".tmp",
        dir=target_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        observer("before_exposure")
        os.replace(temporary, target_path)
        directory_fd = os.open(target_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        observer("after_exposure")
    finally:
        temporary.unlink(missing_ok=True)


def _safe_target_file(root: Path, relative: str) -> Path:
    return _safe_file(root, relative, "target")


def _safe_workpad_file(root: Path, relative: str) -> Path:
    return _safe_file(root, relative, "workpad")


def _safe_file(root: Path, relative: str, label: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or "\\" in relative or not relative or ".." in path.parts or "." in path.parts:
        raise TargetEffectRefusedError(f"{label} path is not a safe relative path", code="unsafe_target_path")
    candidate = root / path
    if not candidate.is_relative_to(root):
        raise TargetEffectRefusedError(f"{label} path escaped its root", code="unsafe_target_path")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise TargetEffectRefusedError(f"{label} path contains a symlink", code="unsafe_target_path")
    if not candidate.is_file() or candidate.is_symlink():
        raise TargetEffectRefusedError(f"{label} path is not a regular file", code="target_file_invalid")
    return candidate


def _require_document_bytes(data: bytes, label: str) -> None:
    if b"\0" in data:
        raise TargetEffectRefusedError(f"{label} is not a text document", code="document_invalid")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TargetEffectRefusedError(f"{label} is not valid UTF-8", code="document_invalid") from exc


def _artifact_ref(path: str, data: bytes, media_type: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "canonical_sha256": digest_imported_bytes(data) if media_type == "application/json" else None,
        "media_type": media_type,
        "size_bytes": len(data),
    }


def _digest_without(value: Mapping[str, object], excluded: str) -> str:
    return digest_imported_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != excluded}))


def _media_type(path: str) -> str:
    return "text/markdown" if path.lower().endswith((".md", ".markdown")) else "text/plain"


def _validate_operator(value: Mapping[str, object]) -> dict[str, object]:
    operator = dict(value)
    if operator.get("kind") != "operator" or not isinstance(operator.get("id"), str) or not operator["id"]:
        raise TargetEffectRefusedError("target-effect authorization requires an operator actor", code="operator_invalid")
    if set(operator) - {"kind", "id", "model_target"}:
        raise TargetEffectRefusedError("operator actor contains an unsupported field", code="operator_invalid")
    if operator.get("model_target") is not None:
        raise TargetEffectRefusedError("operator actor cannot carry a model target", code="operator_invalid")
    return operator


def _validate_proposal_id(value: str) -> None:
    try:
        validate_entity_id(value, expected_prefix=EntityPrefix.GIG_PROPOSAL)
    except Exception as exc:
        raise TargetEffectRefusedError("proposal identity is invalid", code="proposal_invalid") from exc


def _validate_effect_id(value: str) -> None:
    if not value.startswith("effect_") or len(value) != 43:
        raise TargetEffectError("effect identity is invalid")


def _new_effect_id(uuid_factory: Callable[[], uuid.UUID]) -> str:
    return f"effect_{uuid_factory()}"


def _new_handoff_id(uuid_factory: Callable[[], uuid.UUID]) -> str:
    return f"handoff_{uuid_factory()}"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _require_git_workpad(resolved: ResolvedWorkpad) -> None:
    if resolved.target_kind != "git":
        raise TargetEffectRefusedError("G19 v1 requires a bound Git target", code="non_git_target")


__all__ = [
    "TargetEffectError",
    "TargetEffectRecoveryRequired",
    "TargetEffectRefusedError",
    "TargetEffectResult",
    "apply_target_effect",
    "authorize_target_effect",
    "cancel_target_effect",
    "prepare_target_effect",
    "recover_target_effect",
]
