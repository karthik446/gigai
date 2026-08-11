"""Operator-triggered recurring Gig occurrences.

G21 deliberately provides a durable manual occurrence boundary, not a
scheduler. An occurrence records one explicit slot and one immutable Review
Bundle snapshot before it may invoke the existing deterministic Run path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import uuid
from typing import Any, Callable

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
    generate_entity_id,
    parse_json_bytes,
    validate_entity_id,
)
from .index import read_index
from .journal import JournalArtifact, record_transition
from .review import validate_review_bundle
from .run import RunError, RunResult, launch_run, read_run_details
from .validators import validate_serialized_contract
from .workpad import ResolvedWorkpad, WorkpadError, resolve_workpad


class OccurrenceError(ValueError):
    """An occurrence cannot be declared, triggered, or reconciled safely."""


OccurrenceObserver = Callable[[str], None]
UUIDFactory = Callable[[], uuid.UUID]

_CADENCES = frozenset({"daily", "weekly", "monthly"})
_TERMINAL_STATES = frozenset(
    {"closed", "blocked", "skipped", "cancelled", "unavailable", "failed", "missed"}
)
_REFUSAL_STATES = frozenset(
    {"blocked", "skipped", "cancelled", "unavailable", "failed", "missed"}
)
_MACHINE_ACTOR = {"kind": "gigai", "id": "occurrence"}
_TRANSITIONS = {
    "declared": frozenset({"triggered", "skipped", "cancelled", "unavailable", "failed", "missed"}),
    "triggered": frozenset({"snapshot_verified", "blocked", "skipped", "cancelled", "unavailable", "failed", "missed"}),
    "snapshot_verified": frozenset({"run_prepared", "blocked", "cancelled", "unavailable", "failed"}),
    "run_prepared": frozenset({"run_terminal", "blocked", "failed", "cancelled"}),
    "run_terminal": frozenset({"compared", "closed", "blocked"}),
    "compared": frozenset({"closed"}),
}
_JOURNAL_TRANSITIONS = {
    "declared": "occurrence_declared",
    "triggered": "occurrence_triggered",
    "snapshot_verified": "occurrence_snapshot_verified",
    "run_prepared": "occurrence_run_prepared",
    "run_terminal": "occurrence_run_terminal",
    "compared": "occurrence_compared",
    "closed": "occurrence_closed",
    "blocked": "occurrence_blocked",
    "skipped": "occurrence_skipped",
    "cancelled": "occurrence_cancelled",
    "unavailable": "occurrence_unavailable",
    "failed": "occurrence_failed",
    "missed": "occurrence_missed",
}


@dataclass(frozen=True)
class OccurrenceResult:
    occurrence_id: str
    gig_id: str
    gig_version: int
    state: str
    run_id: str | None
    workpad: Path
    record_path: Path
    run: RunResult | None = None


def declare_occurrence(
    *,
    home_root: Path,
    requested_target: Path | None,
    cadence: str,
    occurrence_key: str,
    snapshot_path: str,
    gig_id: str | None = None,
    version: int | None = None,
    prior_occurrence_id: str | None = None,
    scheduled_for: str | None = None,
    trigger_actor: Mapping[str, object] | None = None,
    uuid_factory: UUIDFactory = uuid.uuid4,
) -> OccurrenceResult:
    """Declare one unique operator-supplied occurrence slot."""

    resolved = _resolve(resolved_home=home_root, target=requested_target, gig_id=gig_id)
    _validate_slot(cadence, occurrence_key, scheduled_for)
    actor = _validate_actor(trigger_actor or {"kind": "operator", "id": "local-user"})
    pointer = _active_pointer(resolved)
    selected_version = _selected_version(pointer, version)
    snapshot = _verify_snapshot(resolved, snapshot_path)
    if prior_occurrence_id is not None:
        _validate_occurrence_id(prior_occurrence_id)

    existing = _find_by_slot(resolved.path, resolved.gig_id, cadence, occurrence_key)
    if existing is not None:
        expected = {
            "gig_version": selected_version,
            "snapshot": snapshot,
            "prior_occurrence_id": prior_occurrence_id,
        }
        if any(existing.get(key) != value for key, value in expected.items()):
            raise OccurrenceError("occurrence slot already exists with different binding")
        return _result(resolved, existing)

    occurrence_id = _new_occurrence_id(resolved.path, uuid_factory)
    now = _now()
    record: dict[str, object] = {
        "schema_version": "1.0",
        "occurrence_version": 1,
        "occurrence_id": occurrence_id,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "gig_version": selected_version,
        "cadence": cadence,
        "occurrence_key": occurrence_key,
        "trigger_actor": actor,
        "outcome_actor": None,
        "scheduled_for": scheduled_for,
        "snapshot": snapshot,
        "prior_occurrence_id": prior_occurrence_id,
        "run_id": None,
        "state": "declared",
        "outcome": None,
        "comparison": None,
        "reason": None,
        "created_at": now,
        "updated_at": now,
    }
    _publish(resolved, record, observer=None)
    return _result(resolved, record)


def trigger_occurrence(
    *,
    home_root: Path,
    requested_target: Path | None,
    occurrence_id: str,
    gig_id: str | None = None,
    wait: bool = False,
    uuid_factory: UUIDFactory = uuid.uuid4,
    observer: OccurrenceObserver | None = None,
) -> OccurrenceResult:
    """Trigger one declared occurrence through the existing G13 Run path."""

    observer = observer or (lambda _step: None)
    resolved = _resolve(resolved_home=home_root, target=requested_target, gig_id=gig_id)
    _validate_occurrence_id(occurrence_id)
    record = _read_record(resolved.path, occurrence_id)
    if record["gig_id"] != resolved.gig_id:
        raise OccurrenceError("occurrence Gig does not match the resolved workpad")
    if record["state"] in _TERMINAL_STATES:
        return _result(resolved, record)
    if record["state"] == "declared":
        record = _transition(resolved, record, "triggered", observer=observer)
    if record["state"] == "triggered":
        try:
            verified_snapshot = _verify_snapshot(
                resolved, str(record["snapshot"]["artifact"]["path"])
            )
            if verified_snapshot != record["snapshot"]:
                raise OccurrenceError("reference snapshot identity changed after declaration")
        except OccurrenceError as exc:
            record = _transition(
                resolved, record, "blocked", reason=_safe_reason(exc), observer=observer
            )
            return _result(resolved, record)
        record = _transition(resolved, record, "snapshot_verified", observer=observer)
    if record["state"] != "snapshot_verified":
        return _result(resolved, record)

    try:
        run = launch_run(
            home_root=home_root,
            requested_target=requested_target,
            gig_id=resolved.gig_id,
            version=int(record["gig_version"]),
            wait=wait,
            invocation_argv=("gigai", "occurrence", "trigger", occurrence_id),
            uuid_factory=uuid_factory,
            observer=observer,
        )
    except (RunError, WorkpadError, OSError, ValueError) as exc:
        record = _transition(
            resolved, record, "blocked", reason=_safe_reason(exc), observer=observer
        )
        return _result(resolved, record)

    record = dict(record)
    record["run_id"] = run.run_id
    record = _transition(resolved, record, "run_prepared", observer=observer, record=record)
    if not wait:
        return _result(resolved, record, run=run)
    return _reconcile_result(resolved, record, run, observer)


def reconcile_occurrence(
    *,
    home_root: Path,
    requested_target: Path | None,
    occurrence_id: str,
    gig_id: str | None = None,
    observer: OccurrenceObserver | None = None,
) -> OccurrenceResult:
    """Reconcile one prepared occurrence without retrying or relaunching it."""

    observer = observer or (lambda _step: None)
    resolved = _resolve(resolved_home=home_root, target=requested_target, gig_id=gig_id)
    record = _read_record(resolved.path, occurrence_id)
    if record["state"] != "run_prepared":
        return _result(resolved, record)
    run_id = record.get("run_id")
    if not isinstance(run_id, str):
        raise OccurrenceError("prepared occurrence has no Run identity")
    details = read_run_details(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=resolved.gig_id,
        run_id=run_id,
    )
    status = details.get("status")
    if status in {"preparing", "running"}:
        return _result(resolved, record)
    outcome = "succeeded" if status == "succeeded" else "interrupted" if status == "interrupted" else "failed"
    if outcome != "succeeded":
        record = _transition(
            resolved,
            record,
            "failed",
            reason=f"linked Run ended with {outcome}",
            outcome=outcome,
            observer=observer,
        )
        return _result(resolved, record)
    record = _transition(resolved, record, "run_terminal", outcome=outcome, observer=observer)
    return _transition_result(resolved, record, observer)


def mark_occurrence(
    *,
    home_root: Path,
    requested_target: Path | None,
    occurrence_id: str,
    state: str,
    reason: str,
    outcome_actor: Mapping[str, object],
    gig_id: str | None = None,
    observer: OccurrenceObserver | None = None,
) -> OccurrenceResult:
    """Record an explicit missed, skipped, unavailable, cancelled, or blocked state."""

    if state not in {"missed", "skipped", "unavailable", "cancelled", "blocked", "failed"}:
        raise OccurrenceError("mark_occurrence accepts only explicit terminal states")
    if not reason or len(reason) > 1000 or "\n" in reason:
        raise OccurrenceError("occurrence reason must be one share-safe line")
    actor = _validate_actor(outcome_actor)
    resolved = _resolve(resolved_home=home_root, target=requested_target, gig_id=gig_id)
    record = _read_record(resolved.path, occurrence_id)
    if record["state"] in _TERMINAL_STATES:
        return _result(resolved, record)
    if record["state"] == "run_prepared":
        run_id = record.get("run_id")
        if not isinstance(run_id, str):
            raise OccurrenceError("prepared occurrence has no Run identity")
        try:
            details = read_run_details(
                home_root=home_root,
                requested_target=requested_target,
                gig_id=resolved.gig_id,
                run_id=run_id,
            )
        except (RunError, WorkpadError, OSError, ValueError) as exc:
            raise OccurrenceError("prepared occurrence cannot be terminalized before Run reconciliation") from exc
        if details.get("status") in {"preparing", "running"}:
            raise OccurrenceError("occurrence Run is still in flight; reconcile before terminalization")
        raise OccurrenceError("occurrence Run is complete; reconcile before terminalization")
    record = _transition(
        resolved,
        record,
        state,
        reason=reason,
        outcome=state,
        outcome_actor=actor,
        observer=observer,
    )
    return _result(resolved, record)


def close_occurrence(
    *,
    home_root: Path,
    requested_target: Path | None,
    occurrence_id: str,
    gig_id: str | None = None,
    observer: OccurrenceObserver | None = None,
) -> OccurrenceResult:
    """Close a terminal Run occurrence without creating another Run."""

    resolved = _resolve(resolved_home=home_root, target=requested_target, gig_id=gig_id)
    record = _read_record(resolved.path, occurrence_id)
    if record["state"] == "closed":
        return _result(resolved, record)
    if record["state"] not in {"run_terminal", "compared"}:
        raise OccurrenceError("only a terminal Run or comparison can be closed")
    return _result(resolved, _transition(resolved, record, "closed", observer=observer))


def attach_comparison(
    *,
    home_root: Path,
    requested_target: Path | None,
    occurrence_id: str,
    comparison: Mapping[str, object],
    gig_id: str | None = None,
    observer: OccurrenceObserver | None = None,
) -> OccurrenceResult:
    """Attach one derived comparison, then close the occurrence explicitly."""

    resolved = _resolve(resolved_home=home_root, target=requested_target, gig_id=gig_id)
    record = _read_record(resolved.path, occurrence_id)
    if record["state"] == "closed" and record.get("comparison") is not None:
        return _result(resolved, record)
    if record["state"] != "run_terminal":
        raise OccurrenceError("comparison requires a successful terminal Run occurrence")
    comparison_bytes = canonical_json_bytes(dict(comparison))
    comparison_ref = {
        "path": f"comparisons/{comparison['comparison_id']}/comparison.json",
        "content_sha256": digest_imported_bytes(comparison_bytes),
        "media_type": "application/json",
        "size_bytes": len(comparison_bytes),
    }
    updated = dict(record)
    updated["comparison"] = comparison_ref
    updated["state"] = "compared"
    updated["updated_at"] = _now()
    _publish(
        resolved,
        updated,
        observer=observer,
        extra_artifacts=(JournalArtifact(comparison_ref["path"], comparison_bytes),),
        transition_override="comparison_published",
    )
    return _result(resolved, _transition(resolved, updated, "closed", observer=observer))


def read_occurrence(*, workpad: Path, occurrence_id: str) -> dict[str, object]:
    _validate_occurrence_id(occurrence_id)
    return _read_record(workpad, occurrence_id)


def _reconcile_result(
    resolved: ResolvedWorkpad,
    record: dict[str, object],
    run: RunResult,
    observer: OccurrenceObserver,
) -> OccurrenceResult:
    if run.status == "running":
        return _result(resolved, record, run=run)
    outcome = "succeeded" if run.status == "succeeded" else "interrupted" if run.status == "interrupted" else "failed"
    if outcome != "succeeded":
        record = _transition(
            resolved,
            record,
            "failed",
            reason=f"linked Run ended with {outcome}",
            outcome=outcome,
            observer=observer,
        )
        return _result(resolved, record, run=run)
    record = _transition(resolved, record, "run_terminal", outcome=outcome, observer=observer)
    return _transition_result(resolved, record, observer, run=run)


def _transition_result(
    resolved: ResolvedWorkpad,
    record: dict[str, object],
    observer: OccurrenceObserver,
    *,
    run: RunResult | None = None,
) -> OccurrenceResult:
    return _result(resolved, record, run=run)


def _transition(
    resolved: ResolvedWorkpad,
    previous: Mapping[str, object],
    state: str,
    *,
    reason: str | None = None,
    outcome: str | None = None,
    outcome_actor: Mapping[str, object] | None = None,
    observer: OccurrenceObserver | None = None,
    record: Mapping[str, object] | None = None,
) -> dict[str, object]:
    old_state = str(previous["state"])
    if state not in _TRANSITIONS.get(old_state, frozenset()):
        raise OccurrenceError(f"occurrence cannot transition from {old_state} to {state}")
    next_record = dict(record or previous)
    next_record["state"] = state
    if state in _REFUSAL_STATES and outcome_actor is None:
        outcome_actor = _MACHINE_ACTOR
    if outcome_actor is not None:
        next_record["outcome_actor"] = _validate_actor(outcome_actor)
    if outcome is not None:
        next_record["outcome"] = outcome
    elif state in _REFUSAL_STATES:
        next_record["outcome"] = state
    if reason is not None:
        next_record["reason"] = reason
    next_record["updated_at"] = _now()
    _publish(resolved, next_record, observer=observer)
    return next_record


def _publish(
    resolved: ResolvedWorkpad,
    record: Mapping[str, object],
    *,
    observer: OccurrenceObserver | None,
    extra_artifacts: tuple[JournalArtifact, ...] = (),
    transition_override: str | None = None,
) -> None:
    payload = canonical_json_bytes(dict(record))
    report = validate_serialized_contract("gig-occurrence.schema.json", payload)
    if not report.valid:
        raise OccurrenceError("occurrence failed schema validation")
    occurrence_id = str(record["occurrence_id"])
    path = f"occurrences/{occurrence_id}/occurrence.json"
    transition = transition_override or _JOURNAL_TRANSITIONS[str(record["state"])]
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=generate_entity_id(EntityPrefix.HANDOFF, is_persisted=lambda _value: False),
        transition=transition,
        body=f"Occurrence {occurrence_id} is {record['state']}.",
        artifacts=(JournalArtifact(path, payload), *extra_artifacts),
        front_matter={
            "gig_version": record["gig_version"],
            "run_id": record.get("run_id"),
            "source_manifest_sha256": digest_imported_bytes(payload),
            "outcome": str(record["state"]).upper(),
            "actor": record.get("outcome_actor") or _MACHINE_ACTOR,
        },
        observer=observer,
    )


def _resolve(*, resolved_home: Path, target: Path | None, gig_id: str | None) -> ResolvedWorkpad:
    try:
        return resolve_workpad(
            home_root=resolved_home,
            requested_target=target,
            gig_id=gig_id,
            allow_semantic_state=True,
        )
    except (WorkpadError, OSError, ValueError) as exc:
        raise OccurrenceError(str(exc)) from exc


def _active_pointer(resolved: ResolvedWorkpad) -> Mapping[str, object]:
    projection = read_index(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
    )
    pointer = projection.active_version
    if not isinstance(pointer, Mapping):
        raise OccurrenceError("no approved active Gig version")
    report = validate_serialized_contract(
        "active-gig-version.schema.json", canonical_json_bytes(dict(pointer))
    )
    if not report.valid or pointer.get("gig_id") != resolved.gig_id:
        raise OccurrenceError("active Gig version is invalid")
    return pointer


def _selected_version(pointer: Mapping[str, object], version: int | None) -> int:
    active = pointer.get("active_version")
    if type(active) is not int:
        raise OccurrenceError("active Gig version is invalid")
    if version is not None and version != active:
        raise OccurrenceError("G21 v1 only selects the current approved Gig version")
    return active


def _verify_snapshot(resolved: ResolvedWorkpad, snapshot_path: str) -> dict[str, object]:
    path = _safe_path(resolved.path, snapshot_path)
    if path is None or path.is_symlink() or not path.is_file():
        raise OccurrenceError("reference snapshot is missing, redirected, or not a file")
    payload = path.read_bytes()
    report = validate_review_bundle(resolved.path, payload)
    if not report.valid:
        raise OccurrenceError("reference snapshot failed Review Bundle validation")
    bundle = parse_json_bytes(payload)
    if not isinstance(bundle, Mapping):
        raise OccurrenceError("reference snapshot is not an object")
    references = []
    for item in bundle["references"]:
        references.append(
            {
                "reference_id": item["reference_id"],
                "path": item["path"],
                "content_sha256": item["content_sha256"],
                "size_bytes": item["size_bytes"],
            }
        )
    references.sort(key=lambda item: str(item["reference_id"]))
    return {
        "bundle_id": bundle["bundle_id"],
        "bundle_version": bundle["bundle_version"],
        "artifact": {
            "path": snapshot_path,
            "content_sha256": digest_imported_bytes(payload),
            "media_type": "application/json",
            "size_bytes": len(payload),
        },
        "reference_set_sha256": canonical_json_digest(references),
    }


def _find_by_slot(
    workpad: Path, gig_id: str, cadence: str, occurrence_key: str
) -> dict[str, object] | None:
    root = workpad / "occurrences"
    if not root.is_dir() or root.is_symlink():
        return None
    for path in sorted(root.glob("*/occurrence.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            value = parse_json_bytes(path.read_bytes())
        except Exception:
            continue
        if (
            isinstance(value, Mapping)
            and value.get("gig_id") == gig_id
            and value.get("cadence") == cadence
            and value.get("occurrence_key") == occurrence_key
        ):
            return dict(value)
    return None


def _read_record(workpad: Path, occurrence_id: str) -> dict[str, object]:
    path = _record_path(workpad, occurrence_id)
    if not path.is_file() or path.is_symlink():
        raise OccurrenceError("occurrence record is missing or redirected")
    try:
        value = parse_json_bytes(path.read_bytes())
    except Exception as exc:
        raise OccurrenceError("occurrence record is malformed") from exc
    if not isinstance(value, Mapping):
        raise OccurrenceError("occurrence record is malformed")
    report = validate_serialized_contract(
        "gig-occurrence.schema.json", canonical_json_bytes(dict(value))
    )
    if not report.valid:
        raise OccurrenceError("occurrence record failed schema validation")
    return dict(value)


def _record_path(workpad: Path, occurrence_id: str) -> Path:
    _validate_occurrence_id(occurrence_id)
    return workpad / "occurrences" / occurrence_id / "occurrence.json"


def _new_occurrence_id(workpad: Path, uuid_factory: UUIDFactory) -> str:
    return generate_entity_id(
        EntityPrefix.OCCURRENCE,
        is_persisted=lambda value: (workpad / "occurrences" / value).exists(),
        uuid_factory=uuid_factory,
    )


def _validate_occurrence_id(value: str) -> None:
    try:
        validate_entity_id(value, expected_prefix=EntityPrefix.OCCURRENCE)
    except Exception as exc:
        raise OccurrenceError("invalid occurrence identity") from exc


def _validate_slot(cadence: str, key: str, scheduled_for: str | None) -> None:
    if cadence not in _CADENCES:
        raise OccurrenceError("cadence must be daily, weekly, or monthly")
    if type(key) is not str or not key or len(key) > 128 or key != key.lower():
        raise OccurrenceError("occurrence_key must be canonical lowercase text")
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789._:-" for char in key):
        raise OccurrenceError("occurrence_key contains unsupported characters")
    if scheduled_for is not None:
        try:
            datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OccurrenceError("scheduled_for must be RFC 3339") from exc


def _validate_actor(actor: Mapping[str, object]) -> dict[str, object]:
    if actor.get("kind") not in {"operator", "gigai"}:
        raise OccurrenceError("occurrence trigger actor must be operator or gigai")
    identifier = actor.get("id")
    if not isinstance(identifier, str) or not identifier or len(identifier) > 255:
        raise OccurrenceError("occurrence trigger actor has an invalid id")
    return dict(actor)


def _safe_path(root: Path, relative: str) -> Path | None:
    if not isinstance(relative, str):
        return None
    candidate = root / relative
    if Path(relative).is_absolute() or "\\" in relative or ".." in Path(relative).parts:
        return None
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    return candidate


def _result(
    resolved: ResolvedWorkpad,
    record: Mapping[str, object],
    *,
    run: RunResult | None = None,
) -> OccurrenceResult:
    return OccurrenceResult(
        occurrence_id=str(record["occurrence_id"]),
        gig_id=resolved.gig_id,
        gig_version=int(record["gig_version"]),
        state=str(record["state"]),
        run_id=record.get("run_id") if isinstance(record.get("run_id"), str) else None,
        workpad=resolved.path,
        record_path=_record_path(resolved.path, str(record["occurrence_id"])),
        run=run,
    )


def _safe_reason(exc: BaseException) -> str:
    value = " ".join(str(exc).split())
    return value[:1000] or "occurrence preparation failed"


def _now() -> str:
    from datetime import UTC

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "OccurrenceError",
    "OccurrenceResult",
    "attach_comparison",
    "close_occurrence",
    "declare_occurrence",
    "mark_occurrence",
    "read_occurrence",
    "reconcile_occurrence",
    "trigger_occurrence",
]
