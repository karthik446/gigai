"""Bounded G43 review planning and immutable sealed Run Plan evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import mimetypes
from pathlib import Path
import shutil
import uuid
from typing import Callable, Iterable, Mapping

from .canonical import canonical_json_bytes, derive_deterministic_id, digest_imported_bytes, parse_json_bytes
from .config import load_config
from .journal import JournalArtifact, _git as _journal_git, record_transition
from .model_discovery import (
    discover_runtime_snapshot,
    recorded_target_readiness,
    resolve_target_readiness,
)
from .model_targets import resolve_model_target
from .run import RunError, _resolve_authority, _validate_authority
from .validators import ValidationFinding, ValidationReport, validate_serialized_contract
from .workpad import ResolvedWorkpad, resolve_workpad


class RunPlanError(RuntimeError):
    """A stable, safe G43 planning failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RunPlanResult:
    run_plan_id: str
    content_sha256: str
    plan: dict[str, object]
    workpad: Path
    created: bool


@dataclass(frozen=True)
class BaselineApprovalResult:
    """One direct-operator approval of exact requirements-baseline bytes."""

    approval_id: str
    content_sha256: str
    approval: dict[str, object]
    workpad: Path


@dataclass(frozen=True)
class _TypedReviewInputs:
    """Precomputed G43.1 closure-review sources and their durable records."""

    inputs: tuple[dict[str, object], ...]
    snapshot_refs: tuple[dict[str, object], ...]
    record_refs: tuple[dict[str, object], ...]
    approval_ref: dict[str, object]
    extra_sealed_refs: tuple[dict[str, object], ...]
    artifacts: tuple[JournalArtifact, ...]


_PROFILES: dict[str, dict[str, object]] = {
    "focused": {"roles": (("reviewer", "verifier"),), "calls": 2, "tokens": 8000, "cost": "0.50", "wall": 300000, "review": 1, "verify": 1, "adjudicate": 0},
    "standard": {"roles": (("reviewer",), ("reviewer",), ("verifier",), ("adjudicator",)), "calls": 12, "tokens": 30000, "cost": "3.00", "wall": 600000, "review": 2, "verify": 1, "adjudicate": 1},
    "deep": {"roles": (("reviewer",), ("reviewer",), ("reviewer",), ("verifier",), ("adjudicator",)), "calls": 18, "tokens": 60000, "cost": "8.00", "wall": 1200000, "review": 3, "verify": 2, "adjudicate": 2},
    "var": {"roles": (("reviewer",), ("reviewer",), ("reviewer",), ("verifier",), ("verifier",), ("adjudicator",)), "calls": 24, "tokens": 100000, "cost": "15.00", "wall": 1800000, "review": 3, "verify": 2, "adjudicate": 2},
}
_TASK_CLASSES = frozenset({"planning", "research", "fact_check", "document_review", "code_review", "comparison"})
_ARTIFACT_CLASSES = frozenset({"text", "code", "structured_data", "mixed", "unknown"})
_TERMINAL_STATES = frozenset({"sealed", "handed_to_run_authority", "blocked", "cancelled", "rejected", "inconclusive"})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _artifact(path: str, data: bytes, media_type: str = "application/json") -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": media_type, "size_bytes": len(data)}


def _safe_uuid_suffix(value: str, prefix: str, *, code: str) -> str:
    try:
        if not value.startswith(prefix):
            raise ValueError
        raw = value.removeprefix(prefix)
        parsed = uuid.UUID(raw)
        if parsed.version != 4 or str(parsed) != raw:
            raise ValueError
    except (ValueError, AttributeError):
        raise RunPlanError(code, "approval ID must be canonical") from None
    return value


def _safe_approval_id(value: str) -> str:
    return _safe_uuid_suffix(
        value,
        "requirements_baseline_approval_",
        code="requirements_baseline_approval_missing",
    )


def _text_artifact(source: Path, *, error_code: str, label: str) -> tuple[bytes, str]:
    if source.is_symlink() or not source.is_file():
        raise RunPlanError(error_code, f"{label} must be one explicit regular text file")
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise RunPlanError(error_code, f"{label} is unavailable") from exc
    media = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    if media not in {"text/plain", "text/markdown"}:
        raise RunPlanError(error_code, f"{label} must be plain text or Markdown")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunPlanError(error_code, f"{label} must be valid UTF-8") from exc
    return data, media


def _safe_ref_data(
    root: Path,
    reference: object,
    *,
    code: str,
    message: str,
    changed_code: str | None = None,
) -> tuple[dict[str, object], bytes]:
    if not isinstance(reference, Mapping):
        raise RunPlanError(code, message)
    path_value = reference.get("path")
    digest = reference.get("content_sha256")
    size = reference.get("size_bytes")
    if not isinstance(path_value, str) or not isinstance(digest, str) or type(size) is not int:
        raise RunPlanError(code, message)
    candidate = root / path_value
    if Path(path_value).is_absolute() or "\\" in path_value or ".." in Path(path_value).parts:
        raise RunPlanError(code, message)
    try:
        _reject_symlink_components(root, candidate)
    except RunPlanError as exc:
        raise RunPlanError(code, message) from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise RunPlanError(code, message)
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise RunPlanError(code, message) from exc
    if digest_imported_bytes(data) != digest or len(data) != size:
        raise RunPlanError(changed_code or code, message)
    return dict(reference), data


def _is_sealed_reference(plan: Mapping[str, object], reference: Mapping[str, object]) -> bool:
    sources = plan.get("sealed_sources")
    if not isinstance(sources, list):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("path") == reference.get("path")
        and item.get("content_sha256") == reference.get("content_sha256")
        and item.get("media_type") == reference.get("media_type")
        and item.get("size_bytes") == reference.get("size_bytes")
        for item in sources
    )


def _journaled_baseline_approval(workpad: Path, approval_path: str) -> bool:
    """A hand-written receipt is never an operator approval.

    The approval command writes the receipt through the private journal.  A
    plan reader checks the committed path history so an agent or Plan builder
    cannot manufacture an approval-shaped JSON artifact beside a plan.
    """

    result = _journal_git(workpad, "log", "--format=%s", "--", approval_path, check=False)
    return any(
        line.strip() == "journal: requirements baseline approved"
        for line in result.stdout.splitlines()
    )


def _read_baseline_approval_ref(
    resolved: ResolvedWorkpad,
    approval_ref: object,
    *,
    missing_code: str = "requirements_baseline_approval_missing",
) -> tuple[dict[str, object], dict[str, object], bytes]:
    reference, data = _safe_ref_data(
        resolved.path,
        approval_ref,
        code=missing_code,
        message="requirements baseline approval is missing or changed",
    )
    if not validate_serialized_contract("requirements-baseline-approval.schema.json", data).valid:
        raise RunPlanError("requirements_baseline_approval_invalid", "requirements baseline approval is invalid")
    approval = parse_json_bytes(data)
    if not isinstance(approval, dict):
        raise RunPlanError("requirements_baseline_approval_invalid", "requirements baseline approval is invalid")
    approval_id = approval.get("approval_id")
    if not isinstance(approval_id, str):
        raise RunPlanError("requirements_baseline_approval_invalid", "requirements baseline approval is invalid")
    _safe_approval_id(approval_id)
    expected_path = f"review-inputs/requirements-baseline-approvals/{approval_id}/approval.json"
    if reference.get("path") != expected_path:
        raise RunPlanError("requirements_baseline_approval_invalid", "requirements baseline approval path is invalid")
    if approval.get("project_id") != resolved.project_id or approval.get("gig_id") != resolved.gig_id:
        raise RunPlanError("requirements_baseline_approval_invalid", "requirements baseline approval belongs to another project or Gig")
    if not _journaled_baseline_approval(resolved.path, expected_path):
        raise RunPlanError("requirements_baseline_approval_invalid", "requirements baseline approval was not directly journaled")
    baseline_ref, _baseline = _safe_ref_data(
        resolved.path,
        approval.get("baseline_snapshot_ref"),
        code="requirements_baseline_missing",
        message="approved requirements baseline is missing or changed",
        changed_code="requirements_baseline_changed",
    )
    if baseline_ref.get("media_type") not in {"text/plain", "text/markdown"}:
        raise RunPlanError("requirements_baseline_invalid", "approved requirements baseline must be text or Markdown")
    try:
        _baseline.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunPlanError("requirements_baseline_invalid", "approved requirements baseline must be valid UTF-8") from exc
    return approval, reference, data


def approve_requirements_baseline(
    *,
    home_root: Path,
    requested_target: Path | None,
    baseline_path: Path,
    gig_id: str | None = None,
    direct_operator_confirmed: bool = False,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> BaselineApprovalResult:
    """Freeze one public text baseline after direct local operator confirmation."""

    if not direct_operator_confirmed:
        raise RunPlanError(
            "requirements_baseline_confirmation_required",
            "baseline approval requires direct --confirm confirmation",
        )
    resolved = resolve_workpad(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    data, media = _text_artifact(
        baseline_path,
        error_code="requirements_baseline_invalid",
        label="requirements baseline",
    )
    approval_id = f"requirements_baseline_approval_{uuid_factory()}"
    _safe_approval_id(approval_id)
    base = f"review-inputs/requirements-baseline-approvals/{approval_id}"
    snapshot_ref = _artifact(f"{base}/baseline.bin", data, media)
    approval = {
        "schema_version": "1.0",
        "approval_id": approval_id,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "baseline_snapshot_ref": snapshot_ref,
        "approved_by": {"kind": "operator", "id": "local-user", "model_target": None},
        "approved_at": _now(),
    }
    approval_bytes = canonical_json_bytes(approval)
    if not validate_serialized_contract("requirements-baseline-approval.schema.json", approval_bytes).valid:
        raise RunPlanError("requirements_baseline_approval_invalid", "constructed requirements baseline approval is invalid")
    approval_ref = _artifact(f"{base}/approval.json", approval_bytes)
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=f"handoff_{uuid_factory()}",
        transition="requirements_baseline_approved",
        body=f"Requirements baseline {approval_id} was directly approved for sealed provider review.",
        artifacts=(
            JournalArtifact(str(snapshot_ref["path"]), data),
            JournalArtifact(str(approval_ref["path"]), approval_bytes),
        ),
        front_matter={
            "actor": {"kind": "operator", "id": "local-user", "model_target": None},
            "outcome": "APPROVED",
            "evidence": [snapshot_ref, approval_ref],
        },
    )
    return BaselineApprovalResult(approval_id, str(approval_ref["content_sha256"]), approval, resolved.path)


def _typed_role_inputs(plan: Mapping[str, object]) -> dict[str, Mapping[str, object]] | None:
    inputs = plan.get("inputs")
    if not isinstance(inputs, list):
        return None
    typed = [item for item in inputs if isinstance(item, Mapping) and item.get("role") in {"review_subject", "requirements_baseline"}]
    if not typed:
        return None
    if len(inputs) != 2 or len(typed) != 2:
        raise RunPlanError("review_input_roles_invalid", "closure review requires exactly one subject and one requirements baseline")
    by_role: dict[str, Mapping[str, object]] = {}
    for item in typed:
        role = item.get("role")
        if not isinstance(role, str) or role in by_role:
            raise RunPlanError("review_input_roles_invalid", "closure review requires exactly one subject and one requirements baseline")
        by_role[role] = item
    if set(by_role) != {"review_subject", "requirements_baseline"}:
        raise RunPlanError("review_input_roles_invalid", "closure review requires exactly one subject and one requirements baseline")
    return by_role


def _typed_review_inputs(
    *,
    resolved: ResolvedWorkpad,
    home_root: Path,
    requested_target: Path | None,
    review_subject: Path,
    approval_id: str,
    re_review_of: str | None,
) -> _TypedReviewInputs:
    """Build records for the G43.1 closure path without granting approval."""

    subject_data, subject_media = _text_artifact(
        review_subject,
        error_code="review_input_roles_invalid",
        label="review subject",
    )
    approval, approval_ref, _approval_bytes = _read_baseline_approval_ref(
        resolved,
        _approval_reference_for_id(resolved, approval_id),
    )
    baseline_ref = approval.get("baseline_snapshot_ref")
    baseline_ref, baseline_data = _safe_ref_data(
        resolved.path,
        baseline_ref,
        code="requirements_baseline_missing",
        message="approved requirements baseline is missing or changed",
        changed_code="requirements_baseline_changed",
    )
    if digest_imported_bytes(subject_data) == baseline_ref.get("content_sha256"):
        raise RunPlanError("review_input_roles_invalid", "review subject and requirements baseline must be distinct snapshots")
    subject_digest = digest_imported_bytes(subject_data)
    subject_snapshot_ref = _artifact(
        f"review-inputs/review-subjects/{subject_digest.removeprefix('sha256:')}.bin",
        subject_data,
        subject_media,
    )
    re_review: dict[str, object] | None = None
    extra_sealed_refs: tuple[dict[str, object], ...] = ()
    if re_review_of is not None:
        original = read_run_plan(
            home_root=home_root,
            requested_target=requested_target,
            gig_id=resolved.gig_id,
            run_plan_id=re_review_of,
        )
        original_roles = _typed_role_inputs(original.plan)
        if original_roles is None:
            raise RunPlanError("review_input_roles_invalid", "re-review original plan is not a typed closure review")
        original_baseline = original_roles["requirements_baseline"].get("snapshot_ref")
        if not isinstance(original_baseline, Mapping) or original_baseline.get("content_sha256") != baseline_ref.get("content_sha256"):
            raise RunPlanError("requirements_baseline_changed", "re-review requires the original approved baseline bytes")
        original_path = f"run-plans/{original.run_plan_id}/run-plan.json"
        original_ref = _artifact(original_path, (resolved.path / original_path).read_bytes())
        re_review = {
            "original_run_plan_ref": original_ref,
            "original_run_plan_sha256": original.content_sha256,
        }
        extra_sealed_refs = (original_ref,)

    subject_id = derive_deterministic_id(
        "review_input",
        {"role": "review_subject", "snapshot_sha256": subject_snapshot_ref["content_sha256"]},
    )
    baseline_id = derive_deterministic_id(
        "review_input",
        {
            "role": "requirements_baseline",
            "snapshot_sha256": baseline_ref["content_sha256"],
            "approval_sha256": approval_ref["content_sha256"],
            "re_review_of": re_review,
        },
    )
    subject_record = {
        "schema_version": "1.0",
        "review_input_id": subject_id,
        "role": "review_subject",
        "snapshot_ref": subject_snapshot_ref,
        "approval_ref": None,
        "re_review_of": None,
    }
    baseline_record = {
        "schema_version": "1.0",
        "review_input_id": baseline_id,
        "role": "requirements_baseline",
        "snapshot_ref": baseline_ref,
        "approval_ref": approval_ref,
        "re_review_of": re_review,
    }
    subject_record_bytes = canonical_json_bytes(subject_record)
    baseline_record_bytes = canonical_json_bytes(baseline_record)
    for record in (subject_record_bytes, baseline_record_bytes):
        if not validate_serialized_contract("review-input-record.schema.json", record).valid:
            raise RunPlanError("review_input_roles_invalid", "constructed review input record is invalid")
    subject_record_ref = _artifact(
        f"review-inputs/review-input-records/{subject_id}.json", subject_record_bytes
    )
    baseline_record_ref = _artifact(
        f"review-inputs/review-input-records/{baseline_id}.json", baseline_record_bytes
    )
    inputs = (
        {
            "input_id": "input_review_subject",
            "role": "review_subject",
            "record_ref": subject_record_ref,
            "snapshot_ref": subject_snapshot_ref,
        },
        {
            "input_id": "input_requirements_baseline",
            "role": "requirements_baseline",
            "record_ref": baseline_record_ref,
            "snapshot_ref": baseline_ref,
        },
    )
    return _TypedReviewInputs(
        inputs=inputs,
        snapshot_refs=(subject_snapshot_ref, baseline_ref),
        record_refs=(subject_record_ref, baseline_record_ref),
        approval_ref=approval_ref,
        extra_sealed_refs=extra_sealed_refs,
        artifacts=(
            JournalArtifact(str(subject_snapshot_ref["path"]), subject_data),
            JournalArtifact(str(subject_record_ref["path"]), subject_record_bytes),
            JournalArtifact(str(baseline_record_ref["path"]), baseline_record_bytes),
        ),
    )


def _approval_reference_for_id(resolved: ResolvedWorkpad, approval_id: str) -> dict[str, object]:
    _safe_approval_id(approval_id)
    path = f"review-inputs/requirements-baseline-approvals/{approval_id}/approval.json"
    candidate = resolved.path / path
    try:
        _reject_symlink_components(resolved.path, candidate)
    except RunPlanError as exc:
        raise RunPlanError("requirements_baseline_approval_missing", "requirements baseline approval is missing") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise RunPlanError("requirements_baseline_approval_missing", "requirements baseline approval is missing")
    data = candidate.read_bytes()
    return _artifact(path, data)


def _safe_plan_id(value: str) -> str:
    try:
        if not value.startswith("run_plan_"):
            raise ValueError
        raw = value.removeprefix("run_plan_")
        parsed = uuid.UUID(raw)
        if parsed.version != 4 or str(parsed) != raw:
            raise ValueError
    except (ValueError, AttributeError):
        raise RunPlanError("run_plan_not_found", "Run Plan ID must be canonical") from None
    return value


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    """Reject redirected path components, including links that stay in root."""
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise RunPlanError("run_plan_input_mismatch", "Run Plan path escapes the workpad") from None
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise RunPlanError("run_plan_input_mismatch", "Run Plan path contains a symlinked component")


def _verify_plan_sources(resolved: ResolvedWorkpad, plan: Mapping[str, object]) -> None:
    """Verify sealed source bytes while reading a plan, before journal lookup."""
    sources = plan.get("sealed_sources")
    if not isinstance(sources, list) or not sources:
        raise RunPlanError("run_plan_invalid", "Run Plan has no sealed sources")
    pairs: set[tuple[str, str]] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            raise RunPlanError("run_plan_invalid", "Run Plan source is malformed")
        relative = source.get("path")
        expected = source.get("content_sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise RunPlanError("run_plan_invalid", "Run Plan source is malformed")
        pair = (relative, expected)
        if pair in pairs:
            raise RunPlanError("run_plan_invalid", "Run Plan contains duplicate sealed sources")
        pairs.add(pair)
        path = resolved.path / relative
        if Path(relative).is_absolute() or "\\" in relative or ".." in Path(relative).parts:
            raise RunPlanError("run_plan_input_mismatch", "Run Plan source path is unsafe")
        _reject_symlink_components(resolved.path, path)
        if not path.is_file() or path.is_symlink():
            raise RunPlanError("run_plan_input_mismatch", "sealed Run Plan source changed or is unavailable")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RunPlanError("run_plan_input_mismatch", "sealed Run Plan source changed or is unavailable") from exc
        if digest_imported_bytes(data) != expected or len(data) != source.get("size_bytes"):
            raise RunPlanError("run_plan_input_mismatch", "sealed Run Plan source changed or is unavailable")


def _validate_g431_typed_inputs(resolved: ResolvedWorkpad, plan: Mapping[str, object]) -> None:
    """Validate the optional, strict two-role G43.1 closure-review envelope."""

    roles = _typed_role_inputs(plan)
    if roles is None:
        return
    subject = roles["review_subject"]
    baseline = roles["requirements_baseline"]
    for input_record in (subject, baseline):
        record_ref = input_record.get("record_ref")
        snapshot_ref = input_record.get("snapshot_ref")
        if not isinstance(record_ref, Mapping) or not isinstance(snapshot_ref, Mapping):
            raise RunPlanError("review_input_roles_invalid", "typed review input is malformed")
        if not _is_sealed_reference(plan, record_ref) or not _is_sealed_reference(plan, snapshot_ref):
            raise RunPlanError("review_input_not_sealed", "typed review input records and snapshots must be sealed")
    subject_record_ref, subject_record_bytes = _safe_ref_data(
        resolved.path,
        subject.get("record_ref"),
        code="review_input_not_sealed",
        message="review subject record is unavailable or changed",
    )
    baseline_record_ref, baseline_record_bytes = _safe_ref_data(
        resolved.path,
        baseline.get("record_ref"),
        code="review_input_not_sealed",
        message="requirements baseline record is unavailable or changed",
    )
    if not validate_serialized_contract("review-input-record.schema.json", subject_record_bytes).valid or not validate_serialized_contract("review-input-record.schema.json", baseline_record_bytes).valid:
        raise RunPlanError("review_input_roles_invalid", "typed review input record is invalid")
    subject_record = parse_json_bytes(subject_record_bytes)
    baseline_record = parse_json_bytes(baseline_record_bytes)
    if not isinstance(subject_record, Mapping) or not isinstance(baseline_record, Mapping):
        raise RunPlanError("review_input_roles_invalid", "typed review input record is invalid")
    if subject_record.get("role") != "review_subject" or baseline_record.get("role") != "requirements_baseline":
        raise RunPlanError("review_input_roles_invalid", "typed review input record roles do not match the Plan")
    if subject_record.get("snapshot_ref") != subject.get("snapshot_ref") or baseline_record.get("snapshot_ref") != baseline.get("snapshot_ref"):
        raise RunPlanError("review_input_roles_invalid", "typed review input record snapshots do not match the Plan")
    if subject_record.get("approval_ref") is not None or subject_record.get("re_review_of") is not None:
        raise RunPlanError("review_input_roles_invalid", "review subject cannot carry baseline approval or re-review authority")
    approval_ref = baseline_record.get("approval_ref")
    if not isinstance(approval_ref, Mapping):
        raise RunPlanError("requirements_baseline_approval_missing", "requirements baseline record has no approval")
    if not _is_sealed_reference(plan, approval_ref):
        raise RunPlanError("review_input_not_sealed", "requirements baseline approval must be sealed")
    approval, actual_approval_ref, _approval_bytes = _read_baseline_approval_ref(resolved, approval_ref)
    if actual_approval_ref != dict(approval_ref):
        raise RunPlanError("requirements_baseline_approval_invalid", "requirements baseline approval reference changed")
    baseline_snapshot_ref = baseline.get("snapshot_ref")
    if not isinstance(baseline_snapshot_ref, Mapping):
        raise RunPlanError("requirements_baseline_missing", "requirements baseline snapshot is missing")
    expected_baseline_ref = approval.get("baseline_snapshot_ref")
    if expected_baseline_ref != baseline_snapshot_ref:
        raise RunPlanError("requirements_baseline_changed", "requirements baseline bytes do not match their approval")
    _safe_ref_data(
        resolved.path,
        subject.get("snapshot_ref"),
        code="review_input_not_sealed",
        message="review subject snapshot is unavailable or changed",
    )
    _safe_ref_data(
        resolved.path,
        baseline_snapshot_ref,
        code="requirements_baseline_missing",
        message="requirements baseline snapshot is unavailable or changed",
        changed_code="requirements_baseline_changed",
    )
    if subject.get("snapshot_ref", {}).get("content_sha256") == baseline_snapshot_ref.get("content_sha256"):
        raise RunPlanError("review_input_roles_invalid", "review subject and requirements baseline must be distinct snapshots")
    re_review = baseline_record.get("re_review_of")
    if re_review is None:
        return
    if not isinstance(re_review, Mapping):
        raise RunPlanError("review_input_roles_invalid", "re-review binding is invalid")
    original_ref = re_review.get("original_run_plan_ref")
    expected_digest = re_review.get("original_run_plan_sha256")
    if not isinstance(original_ref, Mapping) or not isinstance(expected_digest, str):
        raise RunPlanError("review_input_roles_invalid", "re-review binding is invalid")
    if original_ref.get("content_sha256") != expected_digest or not _is_sealed_reference(plan, original_ref):
        raise RunPlanError("review_input_not_sealed", "re-review original Plan must be a sealed source")
    _original_ref, original_bytes = _safe_ref_data(
        resolved.path,
        original_ref,
        code="review_input_not_sealed",
        message="re-review original Plan is unavailable or changed",
    )
    original_validation = validate_run_plan(original_bytes)
    if not original_validation.valid:
        raise RunPlanError("review_input_roles_invalid", "re-review original Plan is invalid")
    original = parse_json_bytes(original_bytes)
    if not isinstance(original, Mapping) or original.get("project_id") != resolved.project_id or original.get("gig_id") != resolved.gig_id:
        raise RunPlanError("review_input_roles_invalid", "re-review original Plan belongs to another project or Gig")
    original_roles = _typed_role_inputs(original)
    if original_roles is None:
        raise RunPlanError("review_input_roles_invalid", "re-review original Plan is not a typed closure review")
    original_baseline = original_roles["requirements_baseline"].get("snapshot_ref")
    if not isinstance(original_baseline, Mapping) or original_baseline.get("content_sha256") != baseline_snapshot_ref.get("content_sha256"):
        raise RunPlanError("requirements_baseline_changed", "re-review requirements baseline changed")


def _target_name(profile: object, role: str) -> str:
    for field in (role, "critic", "adjudicator", "planner"):
        candidate = getattr(profile, field, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    raise RunPlanError("target_not_usable", f"default profile has no target for {role}")


def _readiness_for_sealing(home_root: Path, config, target_name: str):
    current = resolve_target_readiness(config, target_name)
    if current.readiness == "usable":
        return current
    if current.readiness not in {"configured"}:
        return current
    return recorded_target_readiness(home_root, config, target_name) or current


def _profile_budget(profile_id: str) -> dict[str, object]:
    profile = _PROFILES[profile_id]
    return {
        "max_model_calls": profile["calls"],
        "max_tool_calls": 0,
        "max_tokens": profile["tokens"],
        "max_cost": profile["cost"],
        "currency": "USD",
        "max_wall_time_ms": profile["wall"],
        "max_parallel_goals": 1,
    }


def _target_record(target_name: str, resolved_target: object) -> bytes:
    return canonical_json_bytes({
        "schema_version": "1.0",
        "target_id": target_name,
        "endpoint": resolved_target.endpoint.name,
        "adapter": resolved_target.endpoint.adapter,
        "model": resolved_target.target.model,
        "capabilities": list(resolved_target.target.capabilities),
        "readiness": "usable",
    })


def _target_reuse_disclosure(profile_id: str, target_records: list[tuple[str, tuple[str, ...], str, bytes, object]]) -> str | None:
    counts: dict[str, int] = {}
    for _participant_id, _roles, target_name, _data, _resolved in target_records:
        counts[target_name] = counts.get(target_name, 0) + 1
    reused = sorted(target for target, count in counts.items() if count > 1)
    if not reused:
        return None
    if profile_id == "focused":
        return "Focused profile is explicitly serial; reviewer and verifier roles share one configured target."
    return "Target reuse disclosed: role groups are distinct, but the configured target identity is shared; independence is limited to role isolation."


def _discovery_identity_digest(data: bytes) -> str:
    """Hash stable discovery facts, excluding per-capture bookkeeping."""
    payload = parse_json_bytes(data)
    if not isinstance(payload, Mapping):
        return digest_imported_bytes(data)
    stable = {key: value for key, value in payload.items() if key not in {"operation_id", "captured_at", "refresh_reason"}}
    return digest_imported_bytes(canonical_json_bytes(stable))


def _identity_projection(plan: Mapping[str, object]) -> dict[str, object]:
    """Return the digest projection, normalizing refs to immutable identities."""
    classification = plan.get("classification")
    profile = plan.get("profile")
    participants = plan.get("participants")
    phases = plan.get("phases")
    inputs = plan.get("inputs")
    capabilities = plan.get("capabilities")
    def ref_digest(value: object) -> object:
        return value.get("content_sha256") if isinstance(value, Mapping) else None
    override = classification.get("override") if isinstance(classification, Mapping) else None
    opt_in = profile.get("opt_in") if isinstance(profile, Mapping) else None
    return {
        "schema_version": plan.get("schema_version"), "gig_id": plan.get("gig_id"),
        "gig_version": plan.get("gig_version"), "project_id": plan.get("project_id"),
        "workpad_locator": plan.get("workpad_locator"), "journal_commit": plan.get("journal_commit"),
        "goal_graph_sha256": ref_digest(plan.get("goal_graph")),
        "review_contract_sha256": ref_digest(plan.get("review_contract")),
        "classification": {
            "task_class": classification.get("task_class") if isinstance(classification, Mapping) else None,
            "artifact_class": classification.get("artifact_class") if isinstance(classification, Mapping) else None,
            "override_reason": override.get("reason") if isinstance(override, Mapping) else None,
        },
        "profile_id": profile.get("profile_id") if isinstance(profile, Mapping) else None,
        "profile_version": profile.get("profile_version") if isinstance(profile, Mapping) else None,
        "usage_unreported_policy": profile.get("usage_unreported_policy") if isinstance(profile, Mapping) else None,
        "opt_in_reason": opt_in.get("reason") if isinstance(opt_in, Mapping) else None,
        "phases": [item.get("phase") for item in phases or [] if isinstance(item, Mapping)],
        "participants": [
            {"participant_id": item.get("participant_id"), "roles": item.get("roles"), "target": item.get("model_target_id"), "target_configuration_sha256": ref_digest(item.get("target_configuration_ref")), "target_reuse_disclosure": item.get("target_reuse_disclosure")}
            for item in participants or [] if isinstance(item, Mapping)
        ],
        "input_refs": [ref_digest(item.get("record_ref")) for item in inputs or [] if isinstance(item, Mapping)],
        "capability_ids": capabilities.get("required_capability_ids") if isinstance(capabilities, Mapping) else None,
        "effects": plan.get("effects"), "budget": plan.get("budget"),
        "policy_sha256": plan.get("policy_sha256"),
        "discovery_snapshot_refs": plan.get("discovery_snapshot_refs", []),
    }


def _review_contract(contract_id: str, now: str, *, typed_closure: bool = False) -> bytes:
    reference_roles = ["review_subject", "requirements_baseline"] if typed_closure else ["primary"]
    required_evidence = reference_roles if typed_closure else ["sealed-input"]
    return canonical_json_bytes({
        "schema_version": "1.0", "contract_id": contract_id, "contract_version": 1,
        "created_at": now, "created_by": {"kind": "gigai", "id": "g43-planner", "model_target": None},
        "name": "sealed-run-review", "question": "Review the sealed declared inputs against the approved Gig requirements.",
        "reference_roles": reference_roles, "criteria": [{"criterion_id": "criterion_requirements", "description": "The review subject is reconciled against the operator-approved requirements baseline.", "severity": "high", "required_evidence": required_evidence, "citation_requirement": "required", "evaluator_ids": ["evaluator_g43"]}],
        "severity_model": {"levels": ["info", "low", "medium", "high", "critical"], "ordering": ["info", "low", "medium", "high", "critical"]},
        "evidence_requirements": required_evidence, "output_shape": {"machine_media_type": "application/json", "human_media_type": "text/markdown", "required_sections": ["findings"]},
        "clarification_policy": "block_run", "cycle_cap": 1, "escalation_policy": "operator", "allowed_effects": ["write_workpad"],
        "evaluator_plan": [{"evaluator_id": "evaluator_g43", "evaluator_version": "1", "stage": "model"}],
        "redaction_policy": {"mode": "local_only", "policy_version": "g43-1", "detector_version": None},
    })


def _validate_plan_semantics(plan: Mapping[str, object]) -> ValidationReport:
    findings: list[ValidationFinding] = []
    phases = plan.get("phases")
    expected_phases = [("review", 1), ("verify", 2), ("adjudicate", 3), ("resolve", 4)]
    if not isinstance(phases, list) or [(item.get("phase"), item.get("sequence")) for item in phases if isinstance(item, dict)] != expected_phases:
        findings.append(ValidationFinding("phases", "run_plan_invalid", "phases must be the ordered Review, Verify, Adjudicate, Resolve sequence"))
    elif any(
        (item.get("state") != ("planned" if item.get("required") else "not_required"))
        or (not item.get("required") and not isinstance(item.get("not_required_reason"), str))
        or (item.get("required") and item.get("not_required_reason") is not None)
        for item in phases
    ):
        findings.append(ValidationFinding("phases", "run_plan_invalid", "phase state must match required and not_required phases need a reason"))
    participants = plan.get("participants")
    if isinstance(participants, list):
        ids = [item.get("participant_id") for item in participants if isinstance(item, dict)]
        groups = [item.get("independence_group") for item in participants if isinstance(item, dict)]
        if len(ids) != len(set(ids)) or len(groups) != len(set(groups)):
            findings.append(ValidationFinding("participants", "run_plan_invalid", "participant IDs and independence groups must be unique"))
        profile_id = plan.get("profile", {}).get("profile_id") if isinstance(plan.get("profile"), Mapping) else None
        expected_roles = _PROFILES.get(profile_id, {}).get("roles") if isinstance(profile_id, str) else None
        actual_roles = tuple(tuple(item.get("roles", ())) for item in participants if isinstance(item, Mapping))
        if expected_roles is not None and actual_roles != expected_roles:
            findings.append(ValidationFinding("participants", "run_plan_invalid", "participant roles must match the selected profile exactly"))
        reviewer_groups = {item.get("independence_group") for item in participants if isinstance(item, Mapping) and "reviewer" in item.get("roles", ())}
        if len(reviewer_groups) != sum(1 for item in participants if isinstance(item, Mapping) and "reviewer" in item.get("roles", ())):
            findings.append(ValidationFinding("participants", "run_plan_invalid", "reviewers must have independent groups"))
        target_counts: dict[object, int] = {}
        for item in participants:
            if isinstance(item, Mapping):
                target_counts[item.get("model_target_id")] = target_counts.get(item.get("model_target_id"), 0) + 1
        for item in participants:
            if not isinstance(item, Mapping):
                continue
            target = item.get("model_target_id")
            disclosure = item.get("target_reuse_disclosure")
            if target_counts.get(target, 0) > 1 and (not isinstance(disclosure, str) or not disclosure.strip()):
                findings.append(ValidationFinding("participants/target_reuse_disclosure", "target_reuse_undisclosed", "reused target identities require an explicit independence disclosure"))
        for item in participants:
            if isinstance(item, Mapping) and ("verifier" in item.get("roles", ()) or "adjudicator" in item.get("roles", ())):
                if item.get("independence_group") in reviewer_groups and profile_id != "focused":
                    findings.append(ValidationFinding("participants", "run_plan_invalid", "verifiers and adjudicators cannot share a reviewer group"))
        if isinstance(phases, list) and any(item.get("participant_ids") for item in phases if isinstance(item, Mapping) and item.get("phase") == "resolve"):
            findings.append(ValidationFinding("phases/resolve", "run_plan_invalid", "resolve is deterministic and cannot name provider participants"))
    profile = plan.get("profile")
    classification = plan.get("classification")
    if isinstance(profile, dict) and isinstance(classification, dict):
        if classification.get("confidence") in {"low", "ambiguous"} and classification.get("override") is None:
            findings.append(ValidationFinding("classification", "classification_ambiguous", "low-confidence classification requires an operator override"))
        if profile.get("profile_id") in {"deep", "var"} and profile.get("opt_in") is None:
            findings.append(ValidationFinding("profile/opt_in", "profile_opt_in_required", "deep and var plans require explicit operator opt-in"))
        if profile.get("usage_unreported_policy") not in {"block_before_call", "reserve_remaining_budget"}:
            findings.append(ValidationFinding("profile/usage_unreported_policy", "budget_invalid", "usage_unreported_policy must be explicit"))
    budget = plan.get("budget")
    if isinstance(profile, Mapping) and isinstance(budget, Mapping) and isinstance(profile.get("profile_id"), str) and profile["profile_id"] in _PROFILES:
        if dict(budget) != _profile_budget(profile["profile_id"]):
            findings.append(ValidationFinding("budget", "budget_invalid", "profile budget must match the bounded USD catalog ceiling"))
    if not isinstance(plan.get("policy_sha256"), str) or not isinstance(plan.get("discovery_snapshot_refs"), list) or not plan.get("discovery_snapshot_refs"):
        findings.append(ValidationFinding("policy_sha256", "run_plan_invalid", "sealed plans require policy and discovery identities"))
    sources = plan.get("sealed_sources")
    if isinstance(sources, list):
        pairs = [(item.get("path"), item.get("content_sha256")) for item in sources if isinstance(item, Mapping)]
        if len(pairs) != len(set(pairs)):
            findings.append(ValidationFinding("sealed_sources", "run_plan_invalid", "sealed source path and digest pairs must be unique"))
    try:
        _typed_role_inputs(plan)
    except RunPlanError as exc:
        findings.append(ValidationFinding("inputs", exc.code, str(exc)))
    sealed = plan.get("state") in _TERMINAL_STATES
    if sealed != (plan.get("sealed_at") is not None and plan.get("sealed_by") is not None):
        findings.append(ValidationFinding("sealed_at", "run_plan_invalid", "sealed plans require both seal time and sealing actor"))
    return ValidationReport(tuple(sorted(set(findings))))


def validate_run_plan(data: bytes) -> ValidationReport:
    report = validate_serialized_contract("run-plan.schema.json", data)
    if not report.valid:
        return report
    parsed = parse_json_bytes(data)
    plan = parsed if isinstance(parsed, dict) else {}
    report = _validate_plan_semantics(plan)
    if isinstance(parsed, dict):
        try:
            expected = derive_deterministic_id("run_plan", _identity_projection(parsed))
            if parsed.get("run_plan_id") != expected:
                report = ValidationReport(report.findings + (ValidationFinding("run_plan_id", "run_plan_digest_mismatch", "Run Plan ID does not match its identity projection"),))
        except (TypeError, ValueError):
            report = ValidationReport(report.findings + (ValidationFinding("run_plan_id", "run_plan_invalid", "Run Plan identity projection is malformed"),))
    return report


def _read_plan_path(resolved: ResolvedWorkpad, run_plan_id: str) -> Path:
    _safe_plan_id(run_plan_id)
    path = resolved.path / "run-plans" / run_plan_id / "run-plan.json"
    try:
        _reject_symlink_components(resolved.path, path)
    except RunPlanError:
        raise RunPlanError("run_plan_not_found", "Run Plan is unavailable") from None
    if path.is_symlink() or not path.is_file():
        raise RunPlanError("run_plan_not_found", "Run Plan is unavailable")
    return path


def read_run_plan(*, home_root: Path, requested_target: Path | None, run_plan_id: str, gig_id: str | None = None) -> RunPlanResult:
    resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
    path = _read_plan_path(resolved, run_plan_id)
    data = path.read_bytes()
    report = validate_run_plan(data)
    if not report.valid:
        if any(item.code == "run_plan_digest_mismatch" for item in report.findings):
            raise RunPlanError("run_plan_digest_mismatch", "Run Plan bytes no longer match their sealed identity")
        raise RunPlanError("run_plan_invalid", "Run Plan failed validation")
    plan = parse_json_bytes(data)
    if not isinstance(plan, dict) or plan.get("project_id") != resolved.project_id or plan.get("gig_id") != resolved.gig_id:
        raise RunPlanError("run_plan_authority_refused", "Run Plan does not belong to the resolved project and Gig")
    _validate_g431_typed_inputs(resolved, plan)
    _verify_plan_sources(resolved, plan)
    return RunPlanResult(run_plan_id, digest_imported_bytes(data), plan, resolved.path, False)


def list_run_plans(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None) -> tuple[RunPlanResult, ...]:
    resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
    root = resolved.path / "run-plans"
    if not root.exists():
        return ()
    results = []
    for candidate in sorted(root.iterdir()):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            results.append(read_run_plan(home_root=home_root, requested_target=requested_target, gig_id=resolved.gig_id, run_plan_id=candidate.name))
        except (RunPlanError, OSError, ValueError):
            continue
    return tuple(results)


def create_run_plan(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None, version: int | None = None, task_class: str | None = None, artifact_class: str | None = None, profile_id: str | None = None, input_paths: Iterable[Path] = (), review_subject: Path | None = None, requirements_baseline_approval_id: str | None = None, re_review_of: str | None = None, override_reason: str | None = None, profile_opt_in_reason: str | None = None, reviewer_targets: Iterable[str] = (), verifier_targets: Iterable[str] = (), adjudicator_targets: Iterable[str] = ()) -> RunPlanResult:
    resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
    try:
        authority = _resolve_authority(resolved, __import__("gigai.index", fromlist=["read_index"]).read_index(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id), version)
        _validate_authority(resolved, authority["graph"], authority["proposal"])
    except RunError as exc:
        raise RunPlanError("run_plan_authority_refused", str(exc)) from exc
    inputs_raw = tuple(input_paths)
    typed_closure = review_subject is not None or requirements_baseline_approval_id is not None or re_review_of is not None
    if typed_closure:
        if inputs_raw or review_subject is None or requirements_baseline_approval_id is None:
            raise RunPlanError(
                "review_input_roles_invalid",
                "typed closure review requires --review-subject and --requirements-baseline-approval, not --input",
            )
    elif not inputs_raw:
        raise RunPlanError("classification_ambiguous", "run-plan create requires at least one explicit --input artifact")
    if len(inputs_raw) > 16:
        raise RunPlanError("run_plan_invalid", "a Run Plan may contain at most sixteen explicit inputs")
    explicit_class = task_class is not None or artifact_class is not None
    selected_task = task_class or "document_review"
    selected_artifact = artifact_class or "text"
    if selected_task not in _TASK_CLASSES or selected_artifact not in _ARTIFACT_CLASSES or selected_artifact == "unknown":
        raise RunPlanError("classification_unsupported", "task and artifact class must be supported and explicit")
    if explicit_class and not override_reason:
        raise RunPlanError("classification_ambiguous", "an explicit classification requires --reason")
    selected_profile = profile_id or "focused"
    if selected_profile not in _PROFILES:
        raise RunPlanError("profile_not_allowed", "profile must be focused, standard, deep, or var")
    if selected_profile in {"deep", "var"} and not profile_opt_in_reason:
        raise RunPlanError("profile_opt_in_required", "deep and var require --profile-opt-in-reason")
    now = _now()
    input_bytes: list[tuple[str, bytes, str]] = []
    if not typed_closure:
        for index, source in enumerate(inputs_raw):
            if source.is_symlink() or not source.is_file():
                raise RunPlanError("run_plan_input_mismatch", "each input must be a regular explicit file")
            data = source.read_bytes()
            media = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            input_bytes.append((f"input_{chr(ord('a') + index)}", data, media))
    typed_inputs = (
        _typed_review_inputs(
            resolved=resolved,
            home_root=home_root,
            requested_target=requested_target,
            review_subject=review_subject,
            approval_id=requirements_baseline_approval_id,
            re_review_of=re_review_of,
        )
        if typed_closure and review_subject is not None and requirements_baseline_approval_id is not None
        else None
    )
    config = load_config(home_root)
    default_profile = next((item for item in config.profiles if item.name == "default"), None)
    if default_profile is None:
        raise RunPlanError("target_not_usable", "GigAI configuration has no default model profile")
    snapshot = discover_runtime_snapshot(refresh_reason="run_plan_create")
    snapshot_bytes = canonical_json_bytes(snapshot.to_shareable_dict())
    profile = _PROFILES[selected_profile]
    explicit_targets = {
        "reviewer": tuple(reviewer_targets),
        "verifier": tuple(verifier_targets),
        "adjudicator": tuple(adjudicator_targets),
    }
    expected_by_role = {
        role: sum(1 for roles in profile["roles"] if role in roles)
        for role in ("reviewer", "verifier", "adjudicator")
    }
    for role, supplied in explicit_targets.items():
        if supplied and len(supplied) != expected_by_role[role]:
            raise RunPlanError(
                "participant_target_count_invalid",
                f"{selected_profile}@1 requires exactly {expected_by_role[role]} --{role}-target value(s)",
            )
        if not expected_by_role[role] and supplied:
            raise RunPlanError(
                "participant_target_count_invalid",
                f"{selected_profile}@1 has no {role} participant",
            )
    target_offsets = {role: 0 for role in explicit_targets}
    assignment_specs: list[tuple[str, tuple[str, ...], str]] = []
    for index, roles in enumerate(profile["roles"], start=1):
        role = roles[0]
        supplied = explicit_targets[role]
        if supplied:
            target_name = supplied[target_offsets[role]]
            target_offsets[role] += 1
        else:
            target_name = _target_name(default_profile, role)
        readiness = _readiness_for_sealing(home_root, config, target_name)
        if readiness.readiness != "usable":
            raise RunPlanError(
                "target_not_usable",
                f"target {target_name!r} is not usable for {role}; run `gigai models --probe {target_name}` first",
            )
        assignment_specs.append((f"participant_p{index}", roles, target_name))
    target_records: list[tuple[str, tuple[str, ...], str, bytes, object]] = []
    for participant_id, roles, target_name in assignment_specs:
        try:
            resolved_target = resolve_model_target(config, target_name)
            target_data = _target_record(target_name, resolved_target)
        except (ValueError, RuntimeError) as exc:
            raise RunPlanError("target_not_usable", f"target {target_name!r} could not be resolved") from exc
        target_records.append((participant_id, roles, target_name, target_data, resolved_target))
    contract_id = derive_deterministic_id("contract", {"gig": resolved.gig_id, "version": authority["version"], "kind": "g43"})
    proposal_created_at = authority["proposal"].get("created_at") if isinstance(authority["proposal"], dict) else None
    contract_bytes = _review_contract(
        contract_id,
        proposal_created_at if isinstance(proposal_created_at, str) else "2026-01-01T00:00:00Z",
        typed_closure=typed_inputs is not None,
    )
    if not validate_serialized_contract("review-contract.schema.json", contract_bytes).valid:
        raise RunPlanError("run_plan_invalid", "derived review contract failed validation")
    # Compute identity from every authority-, routing-, input-, and budget-bearing
    # field before writing anything. Timestamps are intentionally excluded.
    capability_ids = ["gigai.offline"]
    projection = {
        "schema_version": "1.0", "gig_id": resolved.gig_id, "gig_version": authority["version"],
        "project_id": resolved.project_id, "workpad_locator": f"registry:{resolved.project_id}",
        "journal_commit": authority["commit"],
        "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(authority["graph"])),
        "review_contract_sha256": digest_imported_bytes(contract_bytes),
        "classification": {"task_class": selected_task, "artifact_class": selected_artifact, "override_reason": override_reason},
        "profile_id": selected_profile, "profile_version": 1,
        "usage_unreported_policy": "block_before_call", "opt_in_reason": profile_opt_in_reason,
        "phases": ["review", "verify", "adjudicate", "resolve"],
        "participants": [{"participant_id": participant_id, "roles": list(roles), "target": target, "target_configuration_sha256": digest_imported_bytes(target_data), "target_reuse_disclosure": _target_reuse_disclosure(selected_profile, target_records)} for participant_id, roles, target, target_data, _ in target_records],
        "input_refs": (
            [ref["content_sha256"] for ref in typed_inputs.record_refs]
            if typed_inputs is not None
            else [digest_imported_bytes(data) for _, data, _ in input_bytes]
        ),
        "capability_ids": capability_ids, "effects": ["write_workpad"],
        "budget": _profile_budget(selected_profile), "policy_sha256": digest_imported_bytes(contract_bytes),
        "discovery_snapshot_refs": [_discovery_identity_digest(snapshot_bytes)],
    }
    run_plan_id = derive_deterministic_id("run_plan", projection)
    destination = resolved.path / "run-plans" / run_plan_id / "run-plan.json"
    if destination.exists() and not destination.is_symlink():
        existing = read_run_plan(home_root=home_root, requested_target=requested_target, run_plan_id=run_plan_id, gig_id=resolved.gig_id)
        return RunPlanResult(run_plan_id, existing.content_sha256, existing.plan, resolved.path, False)
    base = f"run-plans/{run_plan_id}"
    input_refs = (
        list(typed_inputs.snapshot_refs)
        if typed_inputs is not None
        else [_artifact(f"{base}/inputs/{input_id}.bin", data, media) for input_id, data, media in input_bytes]
    )
    plan_inputs = (
        list(typed_inputs.inputs)
        if typed_inputs is not None
        else [
            {"input_id": input_id, "role": "primary", "record_ref": ref, "snapshot_ref": ref}
            for (input_id, _data, _media), ref in zip(input_bytes, input_refs, strict=True)
        ]
    )
    discovery_ref = _artifact(f"{base}/discovery.json", snapshot_bytes)
    contract_ref = _artifact(f"{base}/review-contract.json", contract_bytes)
    graph_bytes = canonical_json_bytes(authority["graph"])
    graph_ref = _artifact("manifests/goal-graph.json", graph_bytes)
    override = {"actor": {"kind": "operator", "id": "local-user", "model_target": None}, "reason": override_reason, "recorded_at": now} if explicit_class else None
    opt_in = {"actor": {"kind": "operator", "id": "local-user", "model_target": None}, "reason": profile_opt_in_reason, "recorded_at": now} if selected_profile in {"deep", "var"} else None
    participants = []
    target_artifacts: list[JournalArtifact] = []
    target_refs: list[dict[str, object]] = []
    for participant_id, roles, target_name, target_data, resolved_target in target_records:
        target_ref = _artifact(f"{base}/targets/{participant_id}.json", target_data)
        target_artifacts.append(JournalArtifact(str(target_ref["path"]), target_data))
        target_refs.append(target_ref)
        participants.append({"participant_id": participant_id, "roles": list(roles), "model_target_id": target_name, "target_configuration_ref": target_ref, "provider_id": resolved_target.endpoint.name, "discovery_ref": discovery_ref, "independence_group": "SERIAL" if selected_profile == "focused" else f"P{len(participants) + 1}", "assignment_reason": "configured target is usable at sealing", "target_reuse_disclosure": _target_reuse_disclosure(selected_profile, target_records)})
    role_ids = {role: [item["participant_id"] for item in participants if role in item["roles"]] for role in ("reviewer", "verifier", "adjudicator")}
    phases = [
        {"phase": "review", "sequence": 1, "required": True, "participant_ids": role_ids["reviewer"], "input_refs": input_refs, "output_kinds": ["finding"], "stopping_rule": f"at most {profile['review']} review passes", "state": "planned", "not_required_reason": None},
        {"phase": "verify", "sequence": 2, "required": True, "participant_ids": role_ids["verifier"], "input_refs": input_refs, "output_kinds": ["verification-record"], "stopping_rule": f"at most {profile['verify']} verification passes", "state": "planned", "not_required_reason": None},
        {"phase": "adjudicate", "sequence": 3, "required": bool(profile["adjudicate"]), "participant_ids": role_ids["adjudicator"], "input_refs": input_refs, "output_kinds": ["adjudication"], "stopping_rule": f"at most {profile['adjudicate']} adjudication loops", "state": "planned" if profile["adjudicate"] else "not_required", "not_required_reason": None if profile["adjudicate"] else "focused profile has no adjudication loop"},
        {"phase": "resolve", "sequence": 4, "required": True, "participant_ids": [], "input_refs": input_refs, "output_kinds": ["report"], "stopping_rule": "deterministic terminal resolution", "state": "planned", "not_required_reason": None},
    ]
    capability_ref = None
    active = __import__("gigai.index", fromlist=["read_index"]).read_index(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id).active_version
    if isinstance(active, dict) and isinstance(active.get("capability_manifest"), dict):
        capability_ref = active["capability_manifest"]
    typed_sealed_sources = (
        [*typed_inputs.record_refs, typed_inputs.approval_ref, *typed_inputs.extra_sealed_refs]
        if typed_inputs is not None
        else []
    )
    plan = {"schema_version": "1.0", "run_plan_id": run_plan_id, "plan_version": 1, "state": "sealed", "gig_id": resolved.gig_id, "gig_version": authority["version"], "journal_commit": authority["commit"], "project_id": resolved.project_id, "workpad_locator": f"registry:{resolved.project_id}", "goal_graph": graph_ref, "review_contract": contract_ref, "classification": {"task_class": selected_task, "artifact_class": selected_artifact, "confidence": "high", "classifier_version": "g43-deterministic-1", "considered_inputs": input_refs, "reason": "explicit declared inputs have a bounded deterministic classification", "override": override}, "profile": {"profile_id": selected_profile, "profile_version": 1, "selection": "operator" if profile_id else "deterministic", "opt_in": opt_in, "usage_unreported_policy": "block_before_call"}, "phases": phases, "participants": participants, "inputs": plan_inputs, "capabilities": {"manifest": capability_ref, "required_capability_ids": capability_ids}, "effects": ["write_workpad"], "budget": projection["budget"], "policy_sha256": projection["policy_sha256"], "discovery_snapshot_refs": projection["discovery_snapshot_refs"], "sealed_sources": [graph_ref, contract_ref, discovery_ref, *input_refs, *typed_sealed_sources, *target_refs, *([capability_ref] if capability_ref else [])], "created_at": now, "sealed_at": now, "sealed_by": {"kind": "operator", "id": "local-user", "model_target": None}}
    plan_bytes = canonical_json_bytes(plan)
    validation = validate_run_plan(plan_bytes)
    if not validation.valid:
        raise RunPlanError("run_plan_invalid", "; ".join(item.message for item in validation.findings))
    generic_input_artifacts = (
        tuple(
            JournalArtifact(str(ref["path"]), data)
            for (_id, data, _media), ref in zip(input_bytes, input_refs, strict=True)
        )
        if typed_inputs is None
        else ()
    )
    artifacts = (JournalArtifact(f"{base}/run-plan.json", plan_bytes), JournalArtifact(str(contract_ref["path"]), contract_bytes), JournalArtifact(str(discovery_ref["path"]), snapshot_bytes), *generic_input_artifacts, *(typed_inputs.artifacts if typed_inputs is not None else ()), *target_artifacts)
    plan_dir = resolved.path / "run-plans" / run_plan_id
    try:
        record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=derive_deterministic_id("handoff", {"run_plan_id": run_plan_id, "digest": digest_imported_bytes(plan_bytes)}), transition="run_plan_sealed", body=f"Run Plan {run_plan_id} sealed; no Run was allocated.", artifacts=artifacts, front_matter={"gig_version": authority["version"], "run_plan_id": run_plan_id, "run_plan_sha256": digest_imported_bytes(plan_bytes), "outcome": "SEALED", "actor": {"kind": "operator", "id": "local-user", "model_target": None}})
    except Exception:
        # Journal publication is the seal boundary. Do not leave addressable
        # plan bytes behind when publication fails before a handoff exists.
        if not any(path.name.endswith("-run-plan-sealed.txt") for path in (resolved.path / "handoffs").glob("*") if path.is_file()):
            shutil.rmtree(plan_dir, ignore_errors=True)
        raise
    return RunPlanResult(run_plan_id, digest_imported_bytes(plan_bytes), plan, resolved.path, True)


__all__ = ["RunPlanError", "RunPlanResult", "create_run_plan", "list_run_plans", "read_run_plan", "validate_run_plan"]
