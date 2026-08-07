"""Local, content-addressed Review Bundle and Contract substrate.

G15 deliberately stops at deterministic artifact validation. This module does
not invoke a model, discover or install a tool, access a provider, or mutate a
user target.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from pathlib import Path
import re
from typing import Any
import uuid

from .canonical import (
    CanonicalizationError,
    canonical_json_bytes,
    canonical_json_digest,
    canonicalize_owned_text,
    digest_imported_bytes,
    parse_json_bytes,
)
from .validators import (
    ValidationFinding,
    ValidationReport,
    validate_serialized_contract,
)


class ReviewBundleError(ValueError):
    """A Review Bundle cannot be materialized or verified safely."""


class ReviewContractError(ValueError):
    """A Review Contract cannot be used by the deterministic substrate."""


_SAFE_RELATIVE = re.compile(r"^(?!/)(?!.*\\\\)(?!.*(^|/)\.\.(/|$)).+$")
_G15_SCHEMA_NAMES = {
    "review-bundle.schema.json",
    "review-contract.schema.json",
    "finding.schema.json",
    "feedback.schema.json",
    "adjudication.schema.json",
    "trace.schema.json",
    "report.schema.json",
}


def _merge_findings(*reports: ValidationReport) -> ValidationReport:
    findings = [finding for report in reports for finding in report.findings]
    return ValidationReport(tuple(sorted(set(findings))))


def _schema_report(schema_name: str, payload: bytes) -> ValidationReport:
    return validate_serialized_contract(schema_name, payload)


def _path_error(location: str, code: str, message: str) -> ValidationFinding:
    return ValidationFinding(location, code, message)


def _safe_path(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or not _SAFE_RELATIVE.fullmatch(relative):
        return None
    candidate = root / relative
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _check_bundle_objects(
    root: Path,
    bundle: Mapping[str, Any],
    objects: Mapping[str, bytes] | None = None,
) -> ValidationReport:
    findings: list[ValidationFinding] = []
    references = bundle.get("references", [])
    seen_ids: set[str] = set()
    allowed_ids = set(
        bundle.get("redaction_policy", {}).get("allowed_reference_ids", [])
    )
    reference_ids = {
        item.get("reference_id") for item in references if isinstance(item, Mapping)
    }
    for index, reference in enumerate(references):
        location = f"references/{index}"
        if not isinstance(reference, Mapping):
            continue
        reference_id = reference.get("reference_id")
        if reference_id in seen_ids:
            findings.append(
                _path_error(
                    location + "/reference_id",
                    "duplicate_reference_id",
                    "reference_id occurs more than once",
                )
            )
        seen_ids.add(reference_id)
        if reference_id not in allowed_ids:
            findings.append(
                _path_error(
                    location + "/reference_id",
                    "reference_not_allowed",
                    "reference is not named by the redaction policy",
                )
            )
        path_value = reference.get("path")
        path = _safe_path(root, path_value)
        if path is None:
            findings.append(
                _path_error(
                    location + "/path",
                    "unsafe_reference_path",
                    "reference path must be workpad-relative",
                )
            )
            continue
        payload: bytes | None = None
        if objects is not None:
            payload = objects.get(str(path_value))
            if payload is None:
                findings.append(
                    _path_error(
                        location + "/path",
                        "missing_reference_bytes",
                        "materialization bytes are missing",
                    )
                )
        elif path.is_symlink() or not path.is_file():
            findings.append(
                _path_error(
                    location + "/path",
                    "missing_reference_bytes",
                    "reference object is missing or not a regular file",
                )
            )
        if (
            payload is None
            and objects is None
            and path.is_file()
            and not path.is_symlink()
        ):
            payload = path.read_bytes()
        if payload is None:
            continue
        expected_digest = reference.get("content_sha256")
        actual_digest = digest_imported_bytes(payload)
        if actual_digest != expected_digest:
            findings.append(
                _path_error(
                    location + "/content_sha256",
                    "reference_digest_mismatch",
                    "reference bytes do not match content_sha256",
                )
            )
        if len(payload) != reference.get("size_bytes"):
            findings.append(
                _path_error(
                    location + "/size_bytes",
                    "reference_size_mismatch",
                    "reference bytes do not match size_bytes",
                )
            )

    if not allowed_ids.issubset(reference_ids):
        findings.append(
            _path_error(
                "redaction_policy/allowed_reference_ids",
                "unknown_allowed_reference",
                "redaction policy names an unknown reference",
            )
        )

    tool_ref = bundle.get("tool_requirements")
    if isinstance(tool_ref, Mapping):
        path = _safe_path(root, tool_ref.get("path"))
        if path is None:
            findings.append(
                _path_error(
                    "tool_requirements/path",
                    "unsafe_tool_requirements_path",
                    "tool requirements path must be workpad-relative",
                )
            )
        else:
            payload = (
                objects.get(str(tool_ref["path"]))
                if objects is not None
                else (
                    path.read_bytes()
                    if path.is_file() and not path.is_symlink()
                    else None
                )
            )
            if payload is None:
                findings.append(
                    _path_error(
                        "tool_requirements/path",
                        "missing_tool_requirements",
                        "tool requirements object is missing",
                    )
                )
            else:
                if digest_imported_bytes(payload) != tool_ref.get("content_sha256"):
                    findings.append(
                        _path_error(
                            "tool_requirements/content_sha256",
                            "tool_requirements_digest_mismatch",
                            "tool requirements bytes do not match content_sha256",
                        )
                    )
                if len(payload) != tool_ref.get("size_bytes"):
                    findings.append(
                        _path_error(
                            "tool_requirements/size_bytes",
                            "tool_requirements_size_mismatch",
                            "tool requirements bytes do not match size_bytes",
                        )
                    )
    return ValidationReport(tuple(sorted(set(findings))))


def validate_review_bundle(root: Path, manifest_bytes: bytes) -> ValidationReport:
    """Validate a canonical Bundle manifest and the exact bytes it names."""

    schema = _schema_report("review-bundle.schema.json", manifest_bytes)
    try:
        bundle = parse_json_bytes(manifest_bytes)
    except CanonicalizationError:
        return schema
    if manifest_bytes != canonical_json_bytes(bundle):
        schema = _merge_findings(
            schema,
            ValidationReport(
                (
                    _path_error(
                        "$",
                        "noncanonical_manifest",
                        "Bundle manifest is not canonical JSON",
                    ),
                )
            ),
        )
    if not isinstance(bundle, Mapping):
        return schema
    return _merge_findings(schema, _check_bundle_objects(root, bundle))


def materialize_review_bundle(
    root: Path,
    manifest: Mapping[str, Any],
    objects: Mapping[str, bytes],
) -> bytes:
    """Atomically validate then write a Bundle manifest and its object bytes."""

    manifest_bytes = canonical_json_bytes(dict(manifest))
    schema = _schema_report("review-bundle.schema.json", manifest_bytes)
    object_report = _check_bundle_objects(root, manifest, objects)
    report = _merge_findings(schema, object_report)
    if not report.valid:
        raise ReviewBundleError(
            "invalid Review Bundle: " + "; ".join(item.code for item in report.findings)
        )

    writes: list[tuple[Path, bytes]] = []
    for relative, payload in objects.items():
        path = _safe_path(root, relative)
        if path is None:
            raise ReviewBundleError("object path must be workpad-relative")
        writes.append((path, payload))
    manifest_path = root / "manifests" / "review-bundle.json"
    writes.append((manifest_path, manifest_bytes))
    for path, payload in writes:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (path.is_symlink() or path.read_bytes() != payload):
            raise ReviewBundleError(
                f"refusing to overwrite divergent Bundle artifact: {path.name}"
            )
    for path, payload in writes:
        path.write_bytes(payload)
    final = validate_review_bundle(root, manifest_bytes)
    if not final.valid:
        raise ReviewBundleError(
            "materialized Review Bundle failed replay: "
            + "; ".join(item.code for item in final.findings)
        )
    return manifest_bytes


def validate_review_contract(contract_bytes: bytes) -> ValidationReport:
    """Validate a Contract and its deterministic G15 policy constraints."""

    schema = _schema_report("review-contract.schema.json", contract_bytes)
    try:
        contract = parse_json_bytes(contract_bytes)
    except CanonicalizationError:
        return schema
    if not isinstance(contract, Mapping):
        return schema
    findings: list[ValidationFinding] = []
    criteria = contract.get("criteria", [])
    criterion_ids = [
        item.get("criterion_id") for item in criteria if isinstance(item, Mapping)
    ]
    if len(criterion_ids) != len(set(criterion_ids)):
        findings.append(
            _path_error(
                "criteria", "duplicate_criterion_id", "criterion IDs must be unique"
            )
        )
    evaluator_plan = contract.get("evaluator_plan", [])
    evaluator_ids = [
        item.get("evaluator_id") for item in evaluator_plan if isinstance(item, Mapping)
    ]
    if len(evaluator_ids) != len(set(evaluator_ids)):
        findings.append(
            _path_error(
                "evaluator_plan",
                "duplicate_evaluator_id",
                "evaluator IDs must be unique",
            )
        )
    known_evaluators = set(evaluator_ids)
    for index, criterion in enumerate(criteria):
        if isinstance(criterion, Mapping):
            missing = set(criterion.get("evaluator_ids", [])) - known_evaluators
            if missing:
                findings.append(
                    _path_error(
                        f"criteria/{index}/evaluator_ids",
                        "unknown_evaluator_id",
                        "criterion references an unknown evaluator",
                    )
                )
    if any(
        item.get("stage") == "model"
        for item in evaluator_plan
        if isinstance(item, Mapping)
    ):
        findings.append(
            _path_error(
                "evaluator_plan",
                "unsupported_model_evaluator",
                "G15 does not invoke model-backed evaluators",
            )
        )
    return _merge_findings(schema, ValidationReport(tuple(findings)))


def _stable_prefixed_id(prefix: str, value: Any) -> str:
    digest = bytearray(
        bytes.fromhex(canonical_json_digest(value).split(":", 1)[1])[:16]
    )
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return f"{prefix}_{uuid.UUID(bytes=bytes(digest))}"


def validate_finding(
    finding_bytes: bytes,
    bundle: Mapping[str, Any] | None = None,
) -> ValidationReport:
    """Validate a Finding and ensure citations point at the Bundle bytes."""

    report = _schema_report("finding.schema.json", finding_bytes)
    try:
        finding = parse_json_bytes(finding_bytes)
    except CanonicalizationError:
        return report
    if not isinstance(finding, Mapping):
        return report
    findings: list[ValidationFinding] = []
    if bundle is not None:
        references = {
            item.get("reference_id"): item
            for item in bundle.get("references", [])
            if isinstance(item, Mapping)
        }
        for index, evidence in enumerate(finding.get("evidence", [])):
            if not isinstance(evidence, Mapping):
                continue
            reference = references.get(evidence.get("reference_id"))
            if reference is None:
                findings.append(
                    _path_error(
                        f"evidence/{index}/reference_id",
                        "unknown_reference",
                        "finding cites an unknown reference",
                    )
                )
            elif evidence.get("content_sha256") != reference.get("content_sha256"):
                findings.append(
                    _path_error(
                        f"evidence/{index}/content_sha256",
                        "evidence_digest_mismatch",
                        "finding citation digest differs from the Bundle",
                    )
                )
    return _merge_findings(report, ValidationReport(tuple(findings)))


_FINDING_TRANSITIONS = {
    "open": frozenset({"accepted", "rejected", "deferred", "unanswerable"}),
    "accepted": frozenset({"resolved"}),
    "deferred": frozenset({"open", "accepted", "rejected", "unanswerable"}),
    "rejected": frozenset(),
    "unanswerable": frozenset(),
    "resolved": frozenset(),
}


def validate_finding_transition(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> ValidationReport:
    """Validate the explicit Finding lifecycle without mutating either record."""

    findings: list[ValidationFinding] = []
    if previous.get("finding_id") != current.get("finding_id"):
        findings.append(
            _path_error(
                "finding_id",
                "finding_id_mismatch",
                "Finding transitions must retain the same finding_id",
            )
        )
    old_status = previous.get("status")
    new_status = current.get("status")
    if new_status not in _FINDING_TRANSITIONS.get(old_status, frozenset()):
        findings.append(
            _path_error(
                "status",
                "invalid_finding_transition",
                f"Finding status cannot transition from {old_status!r} to {new_status!r}",
            )
        )
    return ValidationReport(tuple(sorted(set(findings))))


def merge_findings(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate Findings while retaining evaluator provenance."""

    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for finding in findings:
        evidence_key = tuple(
            sorted(
                (
                    item.get("reference_id"),
                    item.get("content_sha256"),
                    item.get("locator"),
                )
                for item in finding.get("evidence", [])
            )
        )
        normalized_text = re.sub(
            r"\s+",
            " ",
            f"{finding.get('title', '').strip()}\n{finding.get('description', '').strip()}",
        )
        key = (
            finding.get("criterion_id"),
            finding.get("severity"),
            evidence_key,
            normalized_text,
        )
        groups.setdefault(key, []).append(finding)

    merged: list[dict[str, Any]] = []
    for key, group in sorted(groups.items(), key=lambda item: repr(item[0])):
        base = copy.deepcopy(min(group, key=lambda item: str(item.get("finding_id"))))
        if len(group) > 1:
            provenance = {
                canonical_json_bytes(item): item
                for finding in group
                for item in finding.get("source_evaluators", [finding.get("evaluator")])
                if isinstance(item, Mapping)
            }
            base["source_evaluators"] = [
                copy.deepcopy(item) for _, item in sorted(provenance.items())
            ]
            peer_ids = sorted(
                str(item.get("finding_id"))
                for item in group
                if item.get("finding_id") != base.get("finding_id")
            )
            base["disagreement"] = {
                "present": True,
                "peer_finding_ids": peer_ids,
                "summary": "independent evaluators produced the same finding",
            }
        stable_key = {
            "criterion_id": key[0],
            "severity": key[1],
            "evidence": [list(item) for item in key[2]],
            "text": key[3],
            "source_evaluators": base.get("source_evaluators", []),
        }
        base["finding_id"] = _stable_prefixed_id("finding", stable_key)
        merged.append(base)
    return merged


def validate_feedback(feedback_bytes: bytes) -> ValidationReport:
    return _schema_report("feedback.schema.json", feedback_bytes)


def validate_adjudication(adjudication_bytes: bytes) -> ValidationReport:
    return _schema_report("adjudication.schema.json", adjudication_bytes)


def validate_trace(trace_bytes: bytes) -> ValidationReport:
    """Validate a Trace and its contiguous, hash-only event sequence."""

    report = _schema_report("trace.schema.json", trace_bytes)
    try:
        trace = parse_json_bytes(trace_bytes)
    except CanonicalizationError:
        return report
    if not isinstance(trace, Mapping):
        return report
    findings: list[ValidationFinding] = []
    sequences = [
        event.get("sequence")
        for event in trace.get("events", [])
        if isinstance(event, Mapping)
    ]
    if sequences != list(range(1, len(sequences) + 1)):
        findings.append(
            _path_error(
                "events",
                "noncontiguous_trace",
                "trace event sequences must start at one and be contiguous",
            )
        )
    return _merge_findings(report, ValidationReport(tuple(findings)))


def redact_text(text: str, secrets: tuple[str, ...]) -> str:
    """Apply an explicit local redaction list without guessing at identities."""

    redacted = text
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        redacted = redacted.replace(secret, "<redacted>")
    return redacted


def render_report(
    root: Path,
    *,
    report_id: str,
    bundle_id: str,
    contract_id: str,
    trace_ids: list[str],
    finding_ids: list[str],
    feedback_ids: list[str],
    adjudication_ids: list[str],
    status: str,
    created_at: str,
    human_text: str,
    redactions: tuple[str, ...] = (),
) -> bytes:
    """Write replayable machine and human reports from local source IDs."""

    human_bytes = canonicalize_owned_text(redact_text(human_text, redactions))
    human_path = root / "reports" / f"{report_id}.md"
    human_ref = {
        "path": f"reports/{report_id}.md",
        "content_sha256": digest_imported_bytes(human_bytes),
        "media_type": "text/markdown",
        "size_bytes": len(human_bytes),
    }
    base = {
        "schema_version": "1.0",
        "report_id": report_id,
        "report_version": 1,
        "created_at": created_at,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "trace_ids": sorted(trace_ids),
        "finding_ids": sorted(finding_ids),
        "feedback_ids": sorted(feedback_ids),
        "adjudication_ids": sorted(adjudication_ids),
        "status": status,
        "human_report": human_ref,
    }
    base["machine_report_sha256"] = digest_imported_bytes(canonical_json_bytes(base))
    machine_bytes = canonical_json_bytes(base)
    report = validate_serialized_contract("report.schema.json", machine_bytes)
    if not report.valid:
        raise ReviewBundleError(
            "invalid rendered report: "
            + "; ".join(item.code for item in report.findings)
        )
    for path, payload in (
        (human_path, human_bytes),
        (root / "reports" / f"{report_id}.json", machine_bytes),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (path.is_symlink() or path.read_bytes() != payload):
            raise ReviewBundleError(
                f"refusing to overwrite divergent report: {path.name}"
            )
    human_path.write_bytes(human_bytes)
    (root / "reports" / f"{report_id}.json").write_bytes(machine_bytes)
    return machine_bytes


__all__ = [
    "ReviewBundleError",
    "ReviewContractError",
    "merge_findings",
    "materialize_review_bundle",
    "redact_text",
    "render_report",
    "validate_adjudication",
    "validate_review_bundle",
    "validate_review_contract",
    "validate_feedback",
    "validate_finding",
    "validate_finding_transition",
    "validate_trace",
]
