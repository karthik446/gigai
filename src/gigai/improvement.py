"""G20 semantic validation for subordinate improvement manifests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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


def evaluate_quality_replay(
    *,
    baseline: Mapping[str, Mapping[str, int | str]],
    candidate: Mapping[str, Mapping[str, int | str]],
    minimums: Mapping[str, int | str],
    maximums: Mapping[str, int | str],
    case_counts: Mapping[str, int],
) -> dict[str, object]:
    """Compare baseline/candidate metrics over the fixed three-way split.

    The caller supplies metrics produced by the accepted S16-EVAL extension;
    this function owns only deterministic bar and no-regression evaluation.
    Metrics are integers or decimal strings because canonical GigAI JSON does
    not admit floating-point identity values.
    """

    splits = ("development", "calibration", "final_held_out_acceptance")
    result: dict[str, object] = {}
    for split in splits:
        if split not in baseline or split not in candidate or split not in case_counts:
            raise ImprovementRefusedError("quality replay is missing a corpus split", code="quality_replay_incomplete")
        base = baseline[split]
        actual = candidate[split]
        metrics: dict[str, int | str] = {}
        bar_pass = True
        no_regression = True
        for metric, minimum in minimums.items():
            if metric not in actual or metric not in base:
                raise ImprovementRefusedError("quality replay is missing a metric", code="quality_replay_incomplete")
            metrics[metric] = actual[metric]
            bar_pass &= _decimal(actual[metric]) >= _decimal(minimum)
            no_regression &= _decimal(actual[metric]) >= _decimal(base[metric])
        for metric, maximum in maximums.items():
            if metric not in actual or metric not in base:
                raise ImprovementRefusedError("quality replay is missing a metric", code="quality_replay_incomplete")
            metrics[metric] = actual[metric]
            bar_pass &= _decimal(actual[metric]) <= _decimal(maximum)
            no_regression &= _decimal(actual[metric]) <= _decimal(base[metric])
        result[split] = {
            "case_count": case_counts[split],
            "bar_pass": bar_pass,
            "metrics": metrics,
            "no_regression": no_regression,
        }
    final = result["final_held_out_acceptance"]
    assert isinstance(final, Mapping)
    result["final_holdout_pass"] = final["bar_pass"] is True
    result["no_regression"] = all(
        isinstance(value, Mapping) and value.get("no_regression") is True
        for value in result.values()
        if isinstance(value, Mapping)
    )
    return result


def _decimal(value: int | str) -> Decimal:
    if type(value) is int:
        return Decimal(value)
    if type(value) is str:
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ImprovementRefusedError("quality metric is not numeric", code="quality_metric_invalid") from exc
    raise ImprovementRefusedError("quality metric has an unsupported type", code="quality_metric_invalid")


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
    "evaluate_quality_replay",
    "validate_improvement_manifest",
]
