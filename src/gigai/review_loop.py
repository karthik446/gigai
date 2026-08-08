"""Deterministic, workpad-only Review Loop orchestration for G16.

The loop deliberately uses the existing G15 artifact substrate and G06 journal.
It does not invoke providers, tools, subprocesses, or user targets.  The
fixture evaluator and addresser exist to prove lifecycle ordering and artifact
parentage before G18 supplies live evaluator effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
import uuid

from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from .journal import JournalArtifact, JournalEntry, record_transition
from .review import (
    materialize_review_bundle,
    validate_addressed_artifact,
    validate_finding,
    validate_review_contract,
    validate_review_loop,
)
from .validators import validate_serialized_contract


class ReviewLoopError(RuntimeError):
    """A deterministic Review Loop cannot continue safely."""


_PROFILES: dict[str, tuple[str, str, str]] = {
    "research": ("research-article-pair", "article", "Compare two research articles."),
    "climate": ("climate-article-pair", "article", "Compare two climate articles."),
    "pull-request": ("pull-request-diff", "pull_request_diff", "Review a pull request diff."),
    "repository": ("repository-snapshot", "repository_snapshot", "Review a repository snapshot."),
    "spreadsheet": ("spreadsheet-csv", "csv", "Review a spreadsheet or CSV analysis."),
}


@dataclass(frozen=True)
class ReviewLoopResult:
    loop_id: str
    state: str
    run_id: str
    profile: str
    workpad: Path
    report_id: str
    finding_ids: tuple[str, ...]
    addressed_artifact_id: str | None
    journal_entries: tuple[JournalEntry, ...]


def _stable_id(prefix: str, value: object) -> str:
    raw = bytes.fromhex(
        digest_imported_bytes(canonical_json_bytes(value)).split(":", 1)[1]
    )[:16]
    data = bytearray(raw)
    data[6] = (data[6] & 0x0F) | 0x40
    data[8] = (data[8] & 0x3F) | 0x80
    return f"{prefix}_{uuid.UUID(bytes=bytes(data))}"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write(root: Path, relative: str, payload: bytes, *, allow_replace: bool = False) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not allow_replace and (path.is_symlink() or path.read_bytes() != payload):
        raise ReviewLoopError(f"refusing to overwrite divergent artifact: {relative}")
    path.write_bytes(payload)


def _ref(path: str, payload: bytes, media_type: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(payload),
        "media_type": media_type,
        "size_bytes": len(payload),
    }


def _profile_source(profile: str) -> tuple[str, str, str, bytes]:
    try:
        name, kind, question = _PROFILES[profile]
    except KeyError as exc:
        raise ReviewLoopError(f"unsupported deterministic profile: {profile}") from exc
    source = (
        f"GigAI fixture profile: {name}\n"
        f"Question: {question}\n"
        "Seeded claim: every cited source has a stable digest.\n"
    ).encode("utf-8")
    return name, kind, question, source


def _contract(profile: str, contract_id: str) -> dict[str, object]:
    _name, _kind, question, _source = _profile_source(profile)
    return {
        "schema_version": "1.0",
        "contract_id": contract_id,
        "contract_version": 1,
        "created_at": "2026-08-08T00:00:00Z",
        "created_by": {"kind": "gigai", "id": "g16-fixture", "model_target": None},
        "name": f"g16-{profile}",
        "question": question,
        "reference_roles": ["primary"],
        "criteria": [{
            "criterion_id": "criterion_digest",
            "description": "The cited fixture bytes are digest-stable.",
            "severity": "high",
            "required_evidence": ["reference digest"],
            "citation_requirement": "required",
            "evaluator_ids": ["evaluator_deterministic"],
        }],
        "severity_model": {"levels": ["info", "low", "medium", "high", "critical"], "ordering": ["info", "low", "medium", "high", "critical"]},
        "evidence_requirements": ["reference digest"],
        "output_shape": {"machine_media_type": "application/json", "human_media_type": "text/markdown", "required_sections": ["findings"]},
        "clarification_policy": "block_run",
        "cycle_cap": 1,
        "escalation_policy": "adjudication",
        "allowed_effects": ["write_workpad"],
        "evaluator_plan": [{"evaluator_id": "evaluator_deterministic", "evaluator_version": "g16.1", "stage": "deterministic"}],
        "redaction_policy": {"mode": "local_only", "policy_version": "g16-local-only-1", "detector_version": None},
    }


def _loop_record(
    *,
    loop_id: str,
    run_id: str,
    gig_id: str,
    bundle_id: str,
    contract_id: str,
    state: str,
    stages: list[dict[str, object]],
    cycle_cap: int,
    cycle_count: int,
    finding_ids: list[str],
    report_ids: list[str],
    feedback_ids: list[str],
    adjudication_ids: list[str],
    trace_ids: list[str],
    addressed_ids: list[str],
    terminal: dict[str, object] | None = None,
) -> dict[str, object]:
    now = _now()
    return {
        "schema_version": "1.0",
        "loop_id": loop_id,
        "loop_version": 1,
        "run_id": run_id,
        "gig_id": gig_id,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "state": state,
        "cycle_cap": cycle_cap,
        "cycle_count": cycle_count,
        "stage_sequence": stages,
        "finding_ids": sorted(finding_ids),
        "report_ids": sorted(report_ids),
        "feedback_ids": sorted(feedback_ids),
        "adjudication_ids": sorted(adjudication_ids),
        "trace_ids": sorted(trace_ids),
        "addressed_artifact_ids": sorted(addressed_ids),
        "terminal_decision": terminal,
        "created_at": now,
        "updated_at": now,
    }


def run_review_loop(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    run_id: str,
    profile: str = "research",
    cycle_limit_case: bool = False,
    feedback_decision: str = "accepted",
    unresolved_disagreement: bool = False,
    partial_address_case: bool = False,
) -> ReviewLoopResult:
    """Run one deterministic Review Loop fixture and persist its evidence."""

    run_details_path = workpad / "runs" / run_id / "run-details.json"
    if not run_details_path.is_file() or run_details_path.is_symlink():
        raise ReviewLoopError("G16 requires an existing sealed Run")
    try:
        run_details = parse_json_bytes(run_details_path.read_bytes())
    except Exception as exc:
        raise ReviewLoopError("G16 RunDetails are malformed") from exc
    if not isinstance(run_details, Mapping) or run_details.get("status") != "succeeded":
        raise ReviewLoopError("G16 requires a successfully sealed deterministic Run")
    if feedback_decision not in {"accepted", "clarification_requested"}:
        raise ReviewLoopError("unsupported deterministic feedback decision")

    name, kind, question, source = _profile_source(profile)
    bundle_id = _stable_id("bundle", {"profile": profile})
    contract_id = _stable_id("contract", {"profile": profile})
    loop_id = _stable_id("loop", {"run_id": run_id, "profile": profile})
    reference_id = _stable_id("ref", {"profile": profile})
    reference_path = f"review/references/{profile}.txt"
    source_digest = digest_imported_bytes(source)
    bundle = {
        "schema_version": "1.0",
        "bundle_id": bundle_id,
        "bundle_version": 1,
        "created_at": "2026-08-08T00:00:00Z",
        "created_by": {"kind": "gigai", "id": "g16-fixture", "model_target": None},
        "name": name,
        "question": question,
        "references": [{
            "reference_id": reference_id,
            "role": "primary",
            "kind": kind,
            "path": reference_path,
            "media_type": "text/plain",
            "content_sha256": source_digest,
            "canonical_sha256": None,
            "size_bytes": len(source),
            "provenance": {"source_kind": "generated", "locator": f"g16:{profile}", "acquired_at": "2026-08-08T00:00:00Z", "acquisition_method": "deterministic fixture", "source_revision": None},
            "sensitivity": "public",
            "redaction_status": "not_required",
        }],
        "tool_requirements": None,
        "redaction_policy": {"mode": "local_only", "allowed_reference_ids": [reference_id], "policy_version": "g16-local-only-1", "detector_version": None},
    }
    contract = _contract(profile, contract_id)
    bundle_bytes = canonical_json_bytes(bundle)
    contract_bytes = canonical_json_bytes(contract)
    if not validate_serialized_contract("review-bundle.schema.json", bundle_bytes).valid:
        raise ReviewLoopError("fixture Bundle failed schema validation")
    if not validate_review_contract(contract_bytes).valid:
        raise ReviewLoopError("fixture Contract failed validation")
    try:
        materialize_review_bundle(workpad, bundle, {reference_path: source}, manifest_relative_path=f"manifests/review-bundles/{profile}.json")
    except Exception as exc:
        raise ReviewLoopError("fixture Bundle failed closed during materialization") from exc
    _write(workpad, f"manifests/review-contracts/{profile}.json", contract_bytes)

    trace_id = _stable_id("trace", {"run_id": run_id, "profile": profile})
    trace = {
        "schema_version": "1.0", "trace_id": trace_id, "trace_version": 1,
        "created_at": "2026-08-08T00:00:00Z", "bundle_id": bundle_id,
        "contract_id": contract_id, "run_id": run_id, "goal_id": None,
        "invocation_id": None,
        "events": [{"sequence": 1, "kind": "deterministic_evaluation", "payload_sha256": source_digest, "evaluator_id": "evaluator_deterministic"}],
        "redaction_policy": "g16-local-only-1", "variable_fields": ["created_at"],
    }
    trace_bytes = canonical_json_bytes(trace)
    if not validate_serialized_contract("trace.schema.json", trace_bytes).valid:
        raise ReviewLoopError("fixture Trace failed schema validation")
    _write(workpad, f"traces/{trace_id}.json", trace_bytes)

    finding_id = _stable_id("finding", {"run_id": run_id, "profile": profile, "criterion": "criterion_digest"})
    finding = {
        "schema_version": "1.0", "finding_id": finding_id, "finding_version": 1,
        "criterion_id": "criterion_digest", "status": "open", "severity": "high",
        "title": "Fixture citation is digest-backed", "description": "The primary fixture reference has a stable content digest.",
        "evidence": [{"reference_id": reference_id, "content_sha256": source_digest, "locator": "source", "quote": "Seeded claim"}],
        "evaluator": {"evaluator_id": "evaluator_deterministic", "evaluator_version": "g16.1", "stage": "deterministic"},
        "source_evaluators": [{"evaluator_id": "evaluator_deterministic", "evaluator_version": "g16.1", "stage": "deterministic"}],
        "trace_id": trace_id, "confidence": "1.0", "disagreement": {"present": False, "peer_finding_ids": [], "summary": None},
        "created_at": "2026-08-08T00:00:00Z",
    }
    finding_bytes = canonical_json_bytes(finding)
    if not validate_finding(finding_bytes, bundle).valid:
        raise ReviewLoopError("fixture Finding failed validation")
    _write(workpad, f"findings/{finding_id}/v1-open.json", finding_bytes)

    feedback_id = _stable_id("feedback", {"run_id": run_id, "profile": profile})
    feedback = {"schema_version": "1.0", "feedback_id": feedback_id, "feedback_version": 1, "created_at": "2026-08-08T00:00:00Z", "actor": {"kind": "operator", "id": "g16-fixture"}, "finding_ids": [finding_id], "decision": feedback_decision, "text": "Clarify the digest-backed finding." if feedback_decision == "clarification_requested" else "Accept the digest-backed finding.", "rationale": "Fixture feedback is explicit."}
    feedback_bytes = canonical_json_bytes(feedback)
    if not validate_serialized_contract("feedback.schema.json", feedback_bytes).valid:
        raise ReviewLoopError("fixture Feedback failed validation")
    _write(workpad, f"feedback/{feedback_id}.json", feedback_bytes)
    report_id = _stable_id("report", {"run_id": run_id, "profile": profile})
    blocked_before_address = feedback_decision == "clarification_requested" or unresolved_disagreement
    report_bytes = _render_report(workpad, report_id, bundle_id, contract_id, trace_id, finding_id, feedback_id, "blocked" if blocked_before_address else "complete")
    addressed_id: str | None = None
    addressed_bytes: bytes | None = None
    if feedback_decision == "accepted" and not unresolved_disagreement:
        finding["status"] = "accepted"
        _write(workpad, f"findings/{finding_id}/v2-accepted.json", canonical_json_bytes(finding))
    if feedback_decision == "accepted" and not cycle_limit_case and not unresolved_disagreement:
        addressed_id = _stable_id("addressed", {"run_id": run_id, "profile": profile})
        addressed = {"schema_version": "1.0", "artifact_id": addressed_id, "artifact_version": 1, "loop_id": loop_id, "bundle_id": bundle_id, "contract_id": contract_id, "report_id": report_id, "source_artifact": _ref(reference_path, source, "text/plain"), "content_sha256": source_digest, "media_type": "text/plain", "size_bytes": len(source), "accepted_finding_ids": [finding_id], "status": "partial" if partial_address_case else "addressed", "created_at": "2026-08-08T00:00:00Z"}
        addressed_bytes = canonical_json_bytes(addressed)
        if not validate_addressed_artifact(addressed_bytes).valid:
            raise ReviewLoopError("fixture addressed artifact failed validation")
        _write(workpad, f"addressed/{addressed_id}.json", addressed_bytes)

    stages: list[dict[str, object]] = []
    entries: list[JournalEntry] = []
    loop_state = "reviewing"
    loop_path = "manifests/review-loop.json"
    stage_names = ["reviewing", "verifying", "feedback_pending"] if blocked_before_address else ["reviewing", "verifying", "feedback_pending", "addressing", "closing"]
    terminal_stage = stage_names[-1]
    terminal_state = "blocked" if cycle_limit_case or blocked_before_address or partial_address_case else "complete"
    initial_paths = [
        f"manifests/review-bundles/{profile}.json",
        f"manifests/review-contracts/{profile}.json",
        reference_path,
        f"traces/{trace_id}.json",
        f"findings/{finding_id}/v1-open.json",
        f"feedback/{feedback_id}.json",
        f"reports/{report_id}.json",
        f"reports/{report_id}.md",
    ]
    if feedback_decision == "accepted" and not unresolved_disagreement:
        initial_paths.append(f"findings/{finding_id}/v2-accepted.json")
    if addressed_id:
        initial_paths.append(f"addressed/{addressed_id}.json")
    for index, stage in enumerate(stage_names):
        stages.append({"state": stage, "sequence": index + 1})
        loop_state = stage
        terminal = None
        loop = _loop_record(loop_id=loop_id, run_id=run_id, gig_id=gig_id, bundle_id=bundle_id, contract_id=contract_id, state=loop_state, stages=stages, cycle_cap=1, cycle_count=1 if cycle_limit_case else 0, finding_ids=[finding_id], report_ids=[report_id], feedback_ids=[feedback_id], adjudication_ids=[], trace_ids=[trace_id], addressed_ids=[addressed_id] if addressed_id else [], terminal=terminal)
        loop_bytes = canonical_json_bytes(loop)
        loop_report = validate_review_loop(loop_bytes)
        if not loop_report.valid:
            raise ReviewLoopError(f"invalid loop state at {stage}: {[item.code + ':' + item.message for item in loop_report.findings]}")
        _write(workpad, loop_path, loop_bytes, allow_replace=True)
        goal_id = _stable_id("goal", {"run_id": run_id, "stage": stage})
        parent = entries[-1].handoff_id if entries else None
        front = {"gig_version": 1, "run_id": run_id, "goal_id": goal_id, "goal_version": 1, "parent_handoff_ids": [parent] if parent else [], "outcome": "STARTED", "actor": {"kind": "gigai", "id": "g16-fixture", "model_target": None}}
        stage_artifacts = [JournalArtifact(loop_path, loop_bytes)]
        if index == 0:
            stage_artifacts.extend(JournalArtifact(path, (workpad / path).read_bytes()) for path in initial_paths)
        entries.append(record_transition(workpad=workpad, project_id=project_id, gig_id=gig_id, handoff_id=_stable_id("handoff", {"run_id": run_id, "stage": stage, "kind": "started"}), transition="goal_started", body=f"G16 stage {stage} started.", artifacts=tuple(stage_artifacts), front_matter=front))
        if stage == terminal_stage:
            loop_state = terminal_state
            stages.append({"state": loop_state, "sequence": len(stages) + 1})
            reason = "cycle limit exhausted" if cycle_limit_case else ("operator clarification requested" if feedback_decision == "clarification_requested" else ("unresolved evaluator disagreement" if unresolved_disagreement else ("partial address" if partial_address_case else "all accepted Findings resolved")))
            terminal = {"state": loop_state, "reason": reason, "next_action": "provide clarification" if feedback_decision == "clarification_requested" else ("adjudicate disagreement" if unresolved_disagreement else ("provide a new address pass" if cycle_limit_case else None))}
            loop = _loop_record(loop_id=loop_id, run_id=run_id, gig_id=gig_id, bundle_id=bundle_id, contract_id=contract_id, state=loop_state, stages=stages, cycle_cap=1, cycle_count=1 if cycle_limit_case else 0, finding_ids=[finding_id], report_ids=[report_id], feedback_ids=[feedback_id], adjudication_ids=[], trace_ids=[trace_id], addressed_ids=[addressed_id] if addressed_id else [], terminal=terminal)
            loop_bytes = canonical_json_bytes(loop)
            if not validate_review_loop(loop_bytes).valid:
                raise ReviewLoopError("invalid terminal loop state")
            _write(workpad, loop_path, loop_bytes, allow_replace=True)
        front["outcome"] = "COMPLETE"
        front["parent_handoff_ids"] = [entries[-1].handoff_id]
        entries.append(record_transition(workpad=workpad, project_id=project_id, gig_id=gig_id, handoff_id=_stable_id("handoff", {"run_id": run_id, "stage": stage, "kind": "completed"}), transition="goal_completed", body=f"G16 stage {stage} completed.", artifacts=(JournalArtifact(loop_path, loop_bytes),), front_matter=front))
    return ReviewLoopResult(loop_id, loop_state, run_id, profile, workpad, report_id, (finding_id,), addressed_id, tuple(entries))


def _render_report(root: Path, report_id: str, bundle_id: str, contract_id: str, trace_id: str, finding_id: str, feedback_id: str, status: str) -> bytes:
    human = b"# Deterministic Review Report\n\nThe fixture finding is digest-backed.\n"
    human_ref = _ref(f"reports/{report_id}.md", human, "text/markdown")
    base = {"schema_version": "1.0", "report_id": report_id, "report_version": 1, "created_at": "2026-08-08T00:00:00Z", "bundle_id": bundle_id, "contract_id": contract_id, "trace_ids": [trace_id], "finding_ids": [finding_id], "feedback_ids": [feedback_id], "adjudication_ids": [], "status": status, "human_report": human_ref}
    base["machine_report_sha256"] = digest_imported_bytes(canonical_json_bytes(base))
    machine = canonical_json_bytes(base)
    if not validate_serialized_contract("report.schema.json", machine).valid:
        raise ReviewLoopError("fixture Report failed validation")
    _write(root, f"reports/{report_id}.md", human)
    _write(root, f"reports/{report_id}.json", machine)
    return machine


__all__ = ["ReviewLoopError", "ReviewLoopResult", "run_review_loop"]
