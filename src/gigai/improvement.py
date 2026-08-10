"""G20 semantic validation for subordinate improvement manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .canonical import canonical_json_bytes, parse_json_bytes, validate_entity_id, EntityPrefix
from .learning import LearningError, validate_learning_record
from .validators import validate_serialized_contract


ALLOWED_TARGETS = frozenset({"review_contract", "rubric", "verifier"})
FORBIDDEN_PATH_PARTS = frozenset(
    {
        "allowed_effects",
        "redaction",
        "network",
        "credentials",
        "credential",
        "provider",
        "budgets",
        "budget",
        "cycle_caps",
        "cycle_cap",
        "recovery_policy",
        "parallelism",
        "target_effect",
        "target_authority",
    }
)


class ImprovementError(RuntimeError):
    """An improvement manifest cannot safely become approval-ready."""

    code = "improvement_error"


class ImprovementRefusedError(ImprovementError):
    code = "improvement_refused"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class ImprovementGates:
    evidence_sufficient: bool
    quality_passed: bool


def validate_improvement_manifest(
    manifest: Mapping[str, object] | bytes,
    learning_records: Mapping[str, Mapping[str, object] | bytes],
) -> tuple[dict[str, object], ImprovementGates]:
    """Validate schema, evidence provenance, allowlist, and quality attestations."""

    payload = manifest if isinstance(manifest, bytes) else canonical_json_bytes(manifest)
    report = validate_serialized_contract("improvement-manifest.schema.json", payload)
    if not report.valid:
        codes = ", ".join(item.code for item in report.findings)
        raise ImprovementRefusedError(f"improvement manifest failed validation: {codes}", code="invalid_manifest")
    parsed = parse_json_bytes(payload)
    if not isinstance(parsed, dict):
        raise ImprovementRefusedError("improvement manifest must be an object", code="invalid_manifest")
    validate_entity_id(parsed["manifest_id"], expected_prefix=EntityPrefix.IMPROVEMENT_MANIFEST)
    ids = parsed["learning_record_ids"]
    assert isinstance(ids, list)
    records: dict[str, dict[str, object]] = {}
    for learning_id in ids:
        if learning_id not in learning_records:
            raise ImprovementRefusedError("manifest cites a missing learning record", code="missing_learning_record")
        try:
            record = validate_learning_record(learning_records[learning_id])
        except LearningError as exc:
            raise ImprovementRefusedError(str(exc), code="invalid_learning_record") from exc
        records[learning_id] = record

    _validate_record_bindings(parsed, records)
    evidence_gate = parsed["evidence_gate"]
    assert isinstance(evidence_gate, Mapping)
    supporting_ids = evidence_gate["supporting_record_ids"]
    assert isinstance(supporting_ids, list)
    if not set(supporting_ids) <= set(records):
        raise ImprovementRefusedError("evidence gate cites a record outside the manifest", code="evidence_scope")
    evidence_sufficient = any(
        records[record_id]["provenance"] in {"observed_outcome", "evaluator_judgment"}
        for record_id in supporting_ids
    )
    if not evidence_sufficient:
        raise ImprovementRefusedError(
            "operator feedback or accepted outcomes cannot satisfy the evidence gate",
            code="evidence_insufficient",
        )

    changes = parsed["changes"]
    assert isinstance(changes, list)
    for change in changes:
        assert isinstance(change, Mapping)
        target = change["target"]
        path = change["path"]
        if target not in ALLOWED_TARGETS or not isinstance(path, str) or not path.startswith(f"{target}."):
            raise ImprovementRefusedError("change is outside the review/rubric/verifier allowlist", code="forbidden_change")
        if any(part in FORBIDDEN_PATH_PARTS for part in path.split(".")):
            raise ImprovementRefusedError("change path is explicitly forbidden", code="forbidden_change")

    quality_gate = parsed["quality_gate"]
    assert isinstance(quality_gate, Mapping)
    split_names = ("development", "calibration", "final_holdout")
    if not all(_split_passes(quality_gate[name]) for name in split_names):
        raise ImprovementRefusedError("quality gate did not pass every corpus split", code="quality_gate_failed")
    if quality_gate["final_holdout_pass"] is not True or quality_gate["no_regression"] is not True:
        raise ImprovementRefusedError("quality gate reports a held-out failure or regression", code="quality_gate_failed")
    return parsed, ImprovementGates(True, True)


def _validate_record_bindings(
    manifest: Mapping[str, object], records: Mapping[str, Mapping[str, object]]
) -> None:
    for record in records.values():
        if record["project_id"] != manifest["project_id"] or record["gig_id"] != manifest["gig_id"]:
            raise ImprovementRefusedError("learning record belongs to another Gig", code="learning_binding")
        if record["active_version"] != manifest["base_gig_version"]:
            raise ImprovementRefusedError("learning record was observed against another base version", code="learning_binding")


def _split_passes(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    return value.get("case_count", 0) > 0 and value.get("bar_pass") is True


__all__ = [
    "ALLOWED_TARGETS",
    "FORBIDDEN_PATH_PARTS",
    "ImprovementError",
    "ImprovementGates",
    "ImprovementRefusedError",
    "validate_improvement_manifest",
]
