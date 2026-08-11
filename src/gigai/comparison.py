"""Deterministic comparison of two completed G21 occurrences."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Callable
import uuid

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    generate_entity_id,
    parse_json_bytes,
    validate_entity_id,
)
from .occurrence import OccurrenceResult, attach_comparison, read_occurrence
from .validators import validate_serialized_contract
from .workpad import WorkpadError, resolve_workpad


class ComparisonError(ValueError):
    """A comparison cannot be derived from two exact occurrence inputs."""


UUIDFactory = Callable[[], uuid.UUID]


def compare_occurrences(
    *,
    home_root: Path,
    requested_target: Path | None,
    current_occurrence_id: str,
    prior_occurrence_id: str | None = None,
    gig_id: str | None = None,
    method_id: str = "canonical-json-diff",
    method_version: str = "v1",
    uuid_factory: UUIDFactory = uuid.uuid4,
) -> tuple[dict[str, object], OccurrenceResult]:
    """Publish one comparison and attach it to the current occurrence."""

    try:
        resolved = resolve_workpad(
            home_root=home_root,
            requested_target=requested_target,
            gig_id=gig_id,
            allow_semantic_state=True,
        )
    except (WorkpadError, OSError, ValueError) as exc:
        raise ComparisonError(str(exc)) from exc
    current = read_occurrence(workpad=resolved.path, occurrence_id=current_occurrence_id)
    prior_id = prior_occurrence_id or current.get("prior_occurrence_id")
    if not isinstance(prior_id, str):
        raise ComparisonError("comparison requires an explicit prior occurrence")
    prior = read_occurrence(workpad=resolved.path, occurrence_id=prior_id)
    if current["occurrence_id"] == prior["occurrence_id"]:
        raise ComparisonError("comparison requires two distinct occurrences")
    if current["gig_id"] != prior["gig_id"] or current["gig_id"] != resolved.gig_id:
        raise ComparisonError("comparison occurrences must belong to one Gig")
    if current["state"] != "run_terminal" or prior["state"] not in {"run_terminal", "closed", "compared"}:
        raise ComparisonError("comparison requires completed occurrence Runs")
    current_run_id = current.get("run_id")
    prior_run_id = prior.get("run_id")
    if not isinstance(current_run_id, str) or not isinstance(prior_run_id, str):
        raise ComparisonError("comparison occurrences must both name a Run")
    current_run = _read_run(resolved.path, current_run_id)
    prior_run = _read_run(resolved.path, prior_run_id)
    _verify_ref(resolved.path, current["snapshot"]["artifact"])
    _verify_ref(resolved.path, prior["snapshot"]["artifact"])
    _verify_ref(resolved.path, current_run["goal_graph"])
    _verify_ref(resolved.path, prior_run["goal_graph"])
    current_output = _run_output_ref(resolved.path, current_run_id)
    prior_output = _run_output_ref(resolved.path, prior_run_id)
    current_contracts = _contract_refs(resolved.path, current_run)
    prior_contracts = _contract_refs(resolved.path, prior_run)

    result = "unchanged"
    reason: str | None = None
    if current_run.get("gig_id") != prior_run.get("gig_id"):
        result, reason = "incomparable", "Run Gig identities differ"
    elif current["snapshot"].get("bundle_id") != prior["snapshot"].get("bundle_id"):
        result, reason = "incomparable", "Review Bundle identities differ"
    elif current_run.get("goal_graph", {}).get("content_sha256") != prior_run.get("goal_graph", {}).get("content_sha256"):
        result, reason = "incomparable", "Goal Graph identities differ"
    elif _ref_digests(current_contracts) != _ref_digests(prior_contracts):
        result, reason = "incomparable", "Review Contract identity sets differ"
    elif current_output is None or prior_output is None:
        result, reason = "blocked", "one or both Run outputs are unavailable"
    elif current_output["content_sha256"] != prior_output["content_sha256"]:
        result = "changed"

    comparison_id = _new_comparison_id(resolved.path, uuid_factory)
    evidence = [
        item
        for item in (
            current_output,
            prior_output,
            current["snapshot"]["artifact"],
            prior["snapshot"]["artifact"],
        )
        if isinstance(item, Mapping)
    ]
    comparison: dict[str, object] = {
        "schema_version": "1.0",
        "comparison_version": 1,
        "comparison_id": comparison_id,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "current_occurrence_id": current["occurrence_id"],
        "prior_occurrence_id": prior["occurrence_id"],
        "current_run_id": current_run_id,
        "prior_run_id": prior_run_id,
        "current_gig_version": current_run["gig_version"],
        "prior_gig_version": prior_run["gig_version"],
        "current_snapshot": current["snapshot"]["artifact"],
        "prior_snapshot": prior["snapshot"]["artifact"],
        "current_output": current_output,
        "prior_output": prior_output,
        "current_goal_graph": current_run["goal_graph"],
        "prior_goal_graph": prior_run["goal_graph"],
        "current_review_contracts": current_contracts,
        "prior_review_contracts": prior_contracts,
        "method_id": method_id,
        "method_version": method_version,
        "result": result,
        "reason": reason,
        "evidence": evidence,
        "selected_winner": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    payload = canonical_json_bytes(comparison)
    report = validate_serialized_contract("gig-comparison.schema.json", payload)
    if not report.valid:
        raise ComparisonError("comparison failed schema validation")
    attached = attach_comparison(
        home_root=home_root,
        requested_target=requested_target,
        occurrence_id=current_occurrence_id,
        comparison=comparison,
        gig_id=resolved.gig_id,
    )
    return comparison, attached


def _read_run(workpad: Path, run_id: str) -> dict[str, object]:
    try:
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
    except Exception as exc:
        raise ComparisonError("invalid Run identity") from exc
    path = workpad / "runs" / run_id / "run-manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ComparisonError("sealed Run manifest is unavailable")
    payload = path.read_bytes()
    report = validate_serialized_contract("run-manifest.schema.json", payload)
    if not report.valid:
        raise ComparisonError("Run manifest failed schema validation")
    value = parse_json_bytes(payload)
    if not isinstance(value, Mapping) or value.get("run_id") != run_id or value.get("status") != "sealed":
        raise ComparisonError("Run manifest is not a sealed Run authority")
    return dict(value)


def _run_output_ref(workpad: Path, run_id: str) -> dict[str, object] | None:
    path = workpad / "runs" / run_id / "target-after.json"
    if path.is_symlink() or not path.is_file():
        return None
    payload = path.read_bytes()
    return _artifact_ref(path.relative_to(workpad).as_posix(), payload)


def _contract_refs(workpad: Path, run: Mapping[str, object]) -> list[dict[str, object]]:
    contracts = run.get("goal_contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ComparisonError("sealed Run has no Goal Contract identities")
    result: list[dict[str, object]] = []
    for item in contracts:
        if not isinstance(item, Mapping) or not isinstance(item.get("contract"), Mapping):
            raise ComparisonError("sealed Run Goal Contract identity is malformed")
        ref = dict(item["contract"])
        _verify_ref(workpad, ref)
        result.append(ref)
    return result


def _verify_ref(workpad: Path, ref: Mapping[str, object]) -> bytes:
    path_value = ref.get("path")
    if not isinstance(path_value, str) or Path(path_value).is_absolute() or ".." in Path(path_value).parts or "\\" in path_value:
        raise ComparisonError("comparison artifact path is unsafe")
    path = workpad / path_value
    try:
        path.resolve(strict=False).relative_to(workpad.resolve(strict=False))
    except ValueError as exc:
        raise ComparisonError("comparison artifact escapes the workpad") from exc
    if path.is_symlink() or not path.is_file():
        raise ComparisonError("comparison artifact is missing or redirected")
    payload = path.read_bytes()
    if digest_imported_bytes(payload) != ref.get("content_sha256") or len(payload) != ref.get("size_bytes"):
        raise ComparisonError("comparison artifact digest or size differs")
    return payload


def _artifact_ref(path: str, payload: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(payload),
        "media_type": "application/json",
        "size_bytes": len(payload),
    }


def _ref_digests(refs: list[Mapping[str, object]]) -> tuple[object, ...]:
    return tuple(
        (item.get("content_sha256"), item.get("size_bytes"), item.get("media_type"))
        for item in refs
    )


def _new_comparison_id(workpad: Path, uuid_factory: UUIDFactory) -> str:
    return generate_entity_id(
        EntityPrefix.COMPARISON,
        is_persisted=lambda value: (workpad / "comparisons" / value).exists(),
        uuid_factory=uuid_factory,
    )


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["ComparisonError", "compare_occurrences"]
