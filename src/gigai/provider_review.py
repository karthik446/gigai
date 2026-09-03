"""Bounded provider-backed execution for one sealed G43 document review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Mapping
import uuid

from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, validate_entity_id, EntityPrefix
from .config import load_config
from .journal import JournalArtifact, record_transition
from .model_execution import InvocationBudget, InvocationPolicy, SelectedReference, run_model_invocation
from .model_discovery import resolve_target_readiness
from .model_targets import resolve_model_target
from .review import validate_adjudication, validate_finding, validate_review_loop
from .run_plan import RunPlanError, _target_record, read_run_plan
from .validators import validate_serialized_contract
from .workpad import WorkpadError, resolve_workpad


class ProviderReviewError(RuntimeError):
    """A sealed provider-backed review cannot be executed safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProviderReviewResult:
    run_id: str
    run_plan_id: str
    status: str
    finding_count: int
    report_path: str
    replayed: bool


def execute_provider_review(*, home_root: Path, requested_target: Path | None, gig_id: str | None, run_id: str, run_plan_id: str) -> ProviderReviewResult:
    """Execute one explicitly selected, sealed G43 review pass.

    This is intentionally a document-only execution seam.  It consumes only
    the plan's exact text snapshots, stores all provider replies through the
    existing model-execution journal boundary, and has no target effect.
    """

    try:
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
    except Exception as exc:
        raise ProviderReviewError("provider_review_run_not_found", "Run ID must be canonical") from exc
    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
        sealed = read_run_plan(home_root=home_root, requested_target=requested_target, gig_id=resolved.gig_id, run_plan_id=run_plan_id)
    except (RunPlanError, WorkpadError, OSError, ValueError) as exc:
        raise ProviderReviewError(getattr(exc, "code", "provider_review_plan_invalid"), str(exc)) from exc

    plan = sealed.plan
    _require_eligible_plan(plan)
    run_root = resolved.path / "runs" / run_id
    _require_direct_run_consent(run_root, run_plan_id, sealed.content_sha256)
    root = run_root / "provider-reviews" / run_plan_id
    result_path = root / "result.json"
    if result_path.exists() and not result_path.is_symlink():
        return _read_replay(result_path, run_id, run_plan_id)
    if root.exists() or root.is_symlink():
        raise ProviderReviewError("provider_review_conflict", "provider review directory already exists without a terminal result")

    references, input_artifacts = _sealed_text_inputs(resolved.path, plan, run_id, run_plan_id)
    config = load_config(home_root)
    participants = [item for item in plan.get("participants", []) if isinstance(item, Mapping)]
    _require_current_targets(config, resolved.path, participants)
    budget_raw = plan.get("budget")
    if not isinstance(budget_raw, Mapping):
        raise ProviderReviewError("provider_review_plan_invalid", "sealed plan has no budget")
    budget = InvocationBudget(
        max_model_calls=_positive_int(budget_raw.get("max_model_calls"), "max_model_calls"),
        max_tokens=_positive_int(budget_raw.get("max_tokens"), "max_tokens"),
    )
    _require_budget_capacity(config, participants, budget)
    policy = InvocationPolicy(
        allowed_reference_ids=frozenset(item.reference_id for item in references),
        network_allowed=True,
        offline=False,
        redaction_policy_version="g43.1-explicit-sealed-inputs",
    )
    now = _now()
    bundle_id = _uuid_id("bundle")
    contract = _read_contract(resolved.path, plan)
    contract_id = _string(contract.get("contract_id"), "review contract ID")
    trace_id = _uuid_id("trace")

    reviewer_calls = []
    findings: list[dict[str, object]] = []
    reviewer_failed = False
    for participant in [item for item in participants if "reviewer" in item.get("roles", [])]:
        target = _string(participant.get("model_target_id"), "reviewer target")
        execution = run_model_invocation(
            resolved=resolved,
            config=config,
            run_id=run_id,
            goal_id=_uuid_id("goal"),
            model_target=target,
            role="reviewer",
            prompt=_review_prompt(contract, participant),
            references=references,
            selected_reference_ids=tuple(item.reference_id for item in references),
            policy=policy,
            budget=budget,
        )
        reviewer_calls.append(execution)
        if execution.result is None or execution.record.get("outcome") != "succeeded":
            reviewer_failed = True
            continue
        parsed_findings, invalid = _parse_findings(execution.result.output_text, contract, references, trace_id, participant)
        if invalid:
            reviewer_failed = True
            continue
        findings.extend(parsed_findings)

    verifier_calls = []
    verifications: list[dict[str, object]] = []
    verifier_failed = False
    if findings:
        verifier = next((item for item in participants if "verifier" in item.get("roles", [])), None)
        if verifier is None:
            reviewer_failed = True
        else:
            target = _string(verifier.get("model_target_id"), "verifier target")
            execution = run_model_invocation(
                resolved=resolved,
                config=config,
                run_id=run_id,
                goal_id=_uuid_id("goal"),
                model_target=target,
                role="verifier",
                prompt=_verify_prompt(findings),
                references=references,
                selected_reference_ids=tuple(item.reference_id for item in references),
                policy=policy,
                budget=budget,
            )
            verifier_calls.append(execution)
            record, invalid = _verification_record(
                execution=execution,
                verifier=verifier,
                findings=findings,
                run_id=run_id,
                gig_id=resolved.gig_id,
                bundle_id=bundle_id,
                contract_id=contract_id,
                evidence=input_artifacts,
            )
            verifications.append(record)
            verifier_failed = invalid

    adjudicator_calls = []
    adjudications: list[dict[str, object]] = []
    adjudicator_failed = False
    adjudicate_required = any(
        item.get("phase") == "adjudicate" and item.get("required")
        for item in plan.get("phases", ())
        if isinstance(item, Mapping)
    )
    if findings and adjudicate_required and not verifier_failed:
        adjudicator = next((item for item in participants if "adjudicator" in item.get("roles", [])), None)
        if adjudicator is None:
            adjudicator_failed = True
        else:
            target = _string(adjudicator.get("model_target_id"), "adjudicator target")
            execution = run_model_invocation(
                resolved=resolved,
                config=config,
                run_id=run_id,
                goal_id=_uuid_id("goal"),
                model_target=target,
                role="adjudicator",
                prompt=_adjudicate_prompt(findings, verifications),
                references=references,
                selected_reference_ids=tuple(item.reference_id for item in references),
                policy=policy,
                budget=budget,
            )
            adjudicator_calls.append(execution)
            if execution.result is None or execution.record.get("outcome") != "succeeded":
                adjudicator_failed = True
            else:
                record, invalid = _adjudication_record(execution, adjudicator, findings)
                adjudications.append(record)
                adjudicator_failed = invalid

    status = "blocked" if reviewer_failed or verifier_failed or adjudicator_failed or any(call.result is None for call in verifier_calls) else "complete"
    artifacts, result = _terminal_artifacts(
        root_relative=f"runs/{run_id}/provider-reviews/{run_plan_id}",
        now=now,
        run_id=run_id,
        gig_id=resolved.gig_id,
        run_plan_id=run_plan_id,
        bundle_id=bundle_id,
        contract_id=contract_id,
        trace_id=trace_id,
        findings=findings,
        verifications=verifications,
        adjudications=adjudications,
        invocation_ids=[str(call.record["invocation_id"]) for call in (*reviewer_calls, *verifier_calls, *adjudicator_calls)],
        references=references,
        input_artifacts=input_artifacts,
        input_payloads={item.path: item.content for item in references},
        status=status,
    )
    _validate_terminal_artifacts(resolved.path, artifacts, result)
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_uuid_id("handoff"),
        transition="goal_completed" if status == "complete" else "goal_blocked",
        body=f"G43.1 provider-backed document review terminalized as {status}.",
        artifacts=tuple(JournalArtifact(path, data) for path, data in artifacts.items()),
        front_matter={
            "gig_version": plan["gig_version"], "run_id": run_id, "goal_id": _uuid_id("goal"), "goal_version": 1,
            "outcome": "COMPLETE" if status == "complete" else "BLOCKED",
            "actor": {"kind": "gigai", "id": "g43.1-provider-review", "model_target": None},
        },
    )
    return ProviderReviewResult(run_id, run_plan_id, status, len(findings), f"runs/{run_id}/provider-reviews/{run_plan_id}/report.md", False)


def _require_eligible_plan(plan: Mapping[str, object]) -> None:
    classification = plan.get("classification")
    if not isinstance(classification, Mapping) or classification.get("task_class") not in {"document_review", "planning"} or classification.get("artifact_class") not in {"text", "mixed"}:
        raise ProviderReviewError("provider_review_not_eligible", "provider review supports only sealed planning or document-review text plans")
    if plan.get("state") not in {"sealed", "handed_to_run_authority"}:
        raise ProviderReviewError("provider_review_plan_invalid", "Run Plan is not sealed")
    inputs = plan.get("inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 6:
        raise ProviderReviewError("provider_review_not_eligible", "provider review requires one to six explicit sealed inputs")


def _require_direct_run_consent(run_root: Path, run_plan_id: str, plan_digest: str) -> None:
    path = run_root / "operator-consent.json"
    if path.is_symlink() or not path.is_file():
        raise ProviderReviewError("provider_review_consent_missing", "provider review requires the Run's direct --confirm consent")
    try:
        consent = parse_json_bytes(path.read_bytes())
    except Exception as exc:
        raise ProviderReviewError("provider_review_consent_missing", "Run consent is unreadable") from exc
    scope = consent.get("scope") if isinstance(consent, Mapping) else None
    if not isinstance(scope, Mapping) or consent.get("source") != "direct_cli_confirm" or scope.get("run_plan_id") != run_plan_id or scope.get("run_plan_content_sha256") != plan_digest or scope.get("provider_review_requested") is not True:
        raise ProviderReviewError("provider_review_consent_mismatch", "Run consent does not bind this exact sealed review plan")


def _sealed_text_inputs(workpad: Path, plan: Mapping[str, object], run_id: str, plan_id: str) -> tuple[tuple[SelectedReference, ...], list[dict[str, object]]]:
    refs: list[SelectedReference] = []
    artifacts: list[dict[str, object]] = []
    for index, item in enumerate(plan.get("inputs", []), start=1):
        if not isinstance(item, Mapping) or not isinstance(item.get("snapshot_ref"), Mapping):
            raise ProviderReviewError("provider_review_plan_invalid", "Run Plan input is malformed")
        ref = item["snapshot_ref"]
        path = _safe_artifact_path(workpad, ref.get("path"))
        media = ref.get("media_type")
        if media not in {"text/plain", "text/markdown"}:
            raise ProviderReviewError("provider_review_input_unsupported", "provider review accepts only explicit text or Markdown inputs")
        if path.is_symlink() or not path.is_file():
            raise ProviderReviewError("provider_review_input_mismatch", "sealed input is unavailable")
        data = path.read_bytes()
        if digest_imported_bytes(data) != ref.get("content_sha256") or len(data) != ref.get("size_bytes"):
            raise ProviderReviewError("provider_review_input_mismatch", "sealed input bytes changed")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProviderReviewError("provider_review_input_unsupported", "sealed input is not UTF-8 text") from exc
        reference_id = _uuid_id("ref")
        relative = f"runs/{run_id}/provider-reviews/{plan_id}/inputs/input_{index}.txt"
        refs.append(SelectedReference(reference_id, relative, data, str(ref["content_sha256"]), str(media)))
        artifacts.append({"path": relative, "content_sha256": digest_imported_bytes(data), "media_type": str(media), "size_bytes": len(data)})
    if not refs:
        raise ProviderReviewError("provider_review_not_eligible", "provider review needs at least one sealed text input")
    return tuple(refs), artifacts


def _require_current_targets(config, workpad: Path, participants: list[Mapping[str, object]]) -> None:
    for participant in participants:
        target = _string(participant.get("model_target_id"), "participant target")
        ref = participant.get("target_configuration_ref")
        if not isinstance(ref, Mapping):
            raise ProviderReviewError("provider_review_plan_invalid", "participant target record is missing")
        recorded = _safe_artifact_path(workpad, ref.get("path"))
        if recorded.is_symlink() or not recorded.is_file() or digest_imported_bytes(recorded.read_bytes()) != ref.get("content_sha256"):
            raise ProviderReviewError("provider_review_target_mismatch", "sealed target record changed")
        try:
            current = _target_record(target, resolve_model_target(config, target))
            readiness = resolve_target_readiness(config, target)
        except Exception as exc:
            raise ProviderReviewError("provider_review_target_unavailable", f"selected target {target!r} is unavailable") from exc
        if digest_imported_bytes(current) != ref.get("content_sha256") or readiness.readiness != "usable":
            raise ProviderReviewError("provider_review_target_mismatch", f"selected target {target!r} changed or is not usable")


def _require_budget_capacity(config, participants: list[Mapping[str, object]], budget: InvocationBudget) -> None:
    if len(participants) > budget.max_model_calls:
        raise ProviderReviewError("provider_review_budget_refused", "sealed model-call budget cannot cover every participant")
    try:
        reservation = sum(resolve_model_target(config, _string(item.get("model_target_id"), "participant target")).target.max_output_tokens for item in participants)
    except Exception as exc:
        raise ProviderReviewError("provider_review_target_unavailable", "a sealed participant target is unavailable") from exc
    if reservation > budget.max_tokens:
        raise ProviderReviewError("provider_review_budget_refused", "sealed token budget cannot reserve every participant before execution")


def _read_contract(workpad: Path, plan: Mapping[str, object]) -> Mapping[str, object]:
    ref = plan.get("review_contract")
    if not isinstance(ref, Mapping):
        raise ProviderReviewError("provider_review_plan_invalid", "review contract is missing")
    path = _safe_artifact_path(workpad, ref.get("path"))
    if path.is_symlink() or not path.is_file() or digest_imported_bytes(path.read_bytes()) != ref.get("content_sha256"):
        raise ProviderReviewError("provider_review_plan_invalid", "review contract changed")
    payload = parse_json_bytes(path.read_bytes())
    if not isinstance(payload, Mapping) or not validate_serialized_contract("review-contract.schema.json", path.read_bytes()).valid:
        raise ProviderReviewError("provider_review_plan_invalid", "review contract is invalid")
    return payload


def _review_prompt(contract: Mapping[str, object], participant: Mapping[str, object]) -> str:
    criteria = contract.get("criteria", [])
    return (
        "You are one independent document reviewer. Review only the supplied sealed references. "
        "Do not use outside knowledge or propose file edits. Return strict JSON with a top-level `findings` array. "
        "Each finding must contain criterion_id, severity (info|low|medium|high|critical), title, description, "
        "reference_id, locator, and confidence (a string from 0 to 1). If no issue exists, return {\"findings\":[]}.\n"
        f"Question: {contract.get('question')}\nCriteria: {json.dumps(criteria, sort_keys=True)}\n"
        f"Participant: {participant.get('participant_id')}"
    )


def _verify_prompt(findings: list[dict[str, object]]) -> str:
    return (
        "You are an independent verifier. Check the supplied sealed references and these proposed findings. "
        "Return strict JSON with `outcomes`, one object per finding: finding_id, status "
        "(verified|contradicted|unverified|blocked), and reason. Do not use outside knowledge.\n"
        + json.dumps(findings, sort_keys=True, separators=(",", ":"))
    )


def _adjudicate_prompt(findings: list[dict[str, object]], verifications: list[dict[str, object]]) -> str:
    return (
        "You are the sealed adjudicator. Decide each proposed finding using only the supplied sealed references, "
        "the findings, and verifier records. Return strict JSON with `decisions`, one object per finding: "
        "finding_id, decision (accepted|rejected|deferred|unanswerable), and rationale. Do not use outside knowledge.\n"
        + json.dumps({"findings": findings, "verifications": verifications}, sort_keys=True, separators=(",", ":"))
    )


def _parse_findings(output: str, contract: Mapping[str, object], references: tuple[SelectedReference, ...], trace_id: str, participant: Mapping[str, object]) -> tuple[list[dict[str, object]], bool]:
    payload = _json_object(output)
    raw = payload.get("findings") if isinstance(payload, Mapping) else None
    if not isinstance(raw, list):
        return [], True
    criteria = {item.get("criterion_id") for item in contract.get("criteria", []) if isinstance(item, Mapping)}
    refs = {item.reference_id: item for item in references}
    evaluator = {"evaluator_id": "evaluator_g43", "evaluator_version": "g43.1", "stage": "model"}
    findings: list[dict[str, object]] = []
    invalid = len(raw) > 20
    for item in raw:
        if not isinstance(item, Mapping):
            invalid = True
            continue
        criterion = item.get("criterion_id")
        reference_id = item.get("reference_id")
        locator = item.get("locator")
        if criterion not in criteria or reference_id not in refs or not isinstance(locator, str) or not locator.strip():
            invalid = True
            continue
        severity = item.get("severity") if item.get("severity") in {"info", "low", "medium", "high", "critical"} else "medium"
        confidence = item.get("confidence") if isinstance(item.get("confidence"), str) and _confidence(item["confidence"]) else "0.5"
        finding = {
            "schema_version": "1.0", "finding_id": _uuid_id("finding"), "finding_version": 1,
            "criterion_id": criterion, "status": "open", "severity": severity,
            "title": _limited(item.get("title"), 500, "Untitled review finding"),
            "description": _limited(item.get("description"), 20000, "Model reported a review concern without a description."),
            "evidence": [{"reference_id": reference_id, "content_sha256": refs[reference_id].content_sha256, "locator": locator, "quote": None}],
            "evaluator": evaluator, "source_evaluators": [evaluator], "trace_id": trace_id,
            "confidence": confidence, "disagreement": {"present": False, "peer_finding_ids": [], "summary": None}, "created_at": _now(),
        }
        if not validate_finding(canonical_json_bytes(finding), _bundle_for_refs(references)).valid:
            invalid = True
            continue
        findings.append(finding)
    return findings, invalid


def _verification_record(*, execution, verifier: Mapping[str, object], findings: list[dict[str, object]], run_id: str, gig_id: str, bundle_id: str, contract_id: str, evidence: list[dict[str, object]]) -> tuple[dict[str, object], bool]:
    parsed = _json_object(execution.result.output_text) if execution.result is not None else {}
    by_id = {str(item["finding_id"]): item for item in findings}
    raw = parsed.get("outcomes") if isinstance(parsed, Mapping) else []
    answers = {item.get("finding_id"): item for item in raw if isinstance(item, Mapping)} if isinstance(raw, list) else {}
    outcomes = []
    invalid = not isinstance(raw, list) or set(answers) != set(by_id)
    for finding_id in by_id:
        item = answers.get(finding_id)
        status = item.get("status") if isinstance(item, Mapping) and item.get("status") in {"verified", "contradicted", "unverified", "blocked"} else "blocked"
        invalid = invalid or not isinstance(item, Mapping) or status == "blocked"
        reason = _limited(item.get("reason") if isinstance(item, Mapping) else None, 2000, "Verifier did not provide a schema-valid outcome.")
        outcomes.append({"finding_id": finding_id, "status": status, "evidence_refs": _local_evidence_refs(evidence), "reason": reason})
    record = {
        "schema_version": "1.0", "verification_id": _uuid_id("verification"), "run_id": run_id, "gig_id": gig_id,
        "bundle_id": bundle_id, "contract_id": contract_id, "verifier_participant_id": verifier["participant_id"],
        "verifier_target_id": verifier["model_target_id"], "source_finding_ids": list(by_id), "outcomes": outcomes, "created_at": _now(),
    }
    if not validate_serialized_contract("verification-record.schema.json", canonical_json_bytes(record)).valid:
        raise ProviderReviewError("provider_review_invalid_verification", "constructed verifier result failed schema validation")
    return record, invalid


def _adjudication_record(execution, adjudicator: Mapping[str, object], findings: list[dict[str, object]]) -> tuple[dict[str, object], bool]:
    parsed = _json_object(execution.result.output_text) if execution.result is not None else {}
    raw = parsed.get("decisions") if isinstance(parsed, Mapping) else None
    by_id = {str(item["finding_id"]): item for item in findings}
    answers = {item.get("finding_id"): item for item in raw if isinstance(item, Mapping)} if isinstance(raw, list) else {}
    invalid = not isinstance(raw, list) or set(answers) != set(by_id)
    decisions = []
    for finding_id in by_id:
        item = answers.get(finding_id)
        decision = item.get("decision") if isinstance(item, Mapping) and item.get("decision") in {"accepted", "rejected", "deferred", "unanswerable"} else "deferred"
        invalid = invalid or not isinstance(item, Mapping)
        decisions.append({"finding_id": finding_id, "decision": decision, "rationale": _limited(item.get("rationale") if isinstance(item, Mapping) else None, 20000, "Adjudicator did not provide a schema-valid decision.")})
    record = {
        "schema_version": "1.0", "adjudication_id": _uuid_id("adjudication"), "adjudication_version": 1,
        "created_at": _now(), "actor": {"kind": "model", "id": str(adjudicator["participant_id"]), "model_target": adjudicator["model_target_id"]},
        "decisions": decisions,
    }
    if not validate_adjudication(canonical_json_bytes(record)).valid:
        raise ProviderReviewError("provider_review_invalid_adjudication", "constructed adjudicator result failed schema validation")
    return record, invalid


def _terminal_artifacts(*, root_relative: str, now: str, run_id: str, gig_id: str, run_plan_id: str, bundle_id: str, contract_id: str, trace_id: str, findings: list[dict[str, object]], verifications: list[dict[str, object]], adjudications: list[dict[str, object]], invocation_ids: list[str], references: tuple[SelectedReference, ...], input_artifacts: list[dict[str, object]], input_payloads: Mapping[str, bytes], status: str) -> tuple[dict[str, bytes], dict[str, object]]:
    artifacts: dict[str, bytes] = {}
    for ref in input_artifacts:
        path = ref["path"]
        if not isinstance(path, str) or path not in input_payloads:
            raise ProviderReviewError("provider_review_input_mismatch", "provider review input bytes are unavailable")
        artifacts[path] = input_payloads[path]
    bundle = _bundle_payload(bundle_id, now, references)
    artifacts[f"{root_relative}/review/bundle.json"] = canonical_json_bytes(bundle)
    trace = {"schema_version": "1.0", "trace_id": trace_id, "trace_version": 1, "created_at": now, "bundle_id": bundle_id, "contract_id": contract_id, "run_id": run_id, "goal_id": None, "invocation_id": None, "events": [{"sequence": 1, "kind": "provider_review_completed", "payload_sha256": digest_imported_bytes(canonical_json_bytes({"run_plan_id": run_plan_id, "invocation_ids": invocation_ids})), "evaluator_id": "evaluator_g43"}], "redaction_policy": "g43.1-explicit-sealed-inputs", "variable_fields": ["created_at"]}
    trace_path = f"{root_relative}/review/traces/{trace_id}.json"
    artifacts[trace_path] = canonical_json_bytes(trace)
    finding_ids = []
    for finding in findings:
        finding_id = str(finding["finding_id"])
        finding_ids.append(finding_id)
        artifacts[f"{root_relative}/review/findings/{finding_id}/v1-open.json"] = canonical_json_bytes(finding)
    verification_ids = []
    for record in verifications:
        verification_id = str(record["verification_id"])
        verification_ids.append(verification_id)
        artifacts[f"{root_relative}/review/verification/{verification_id}.json"] = canonical_json_bytes(record)
    adjudication_ids = []
    for record in adjudications:
        adjudication_id = str(record["adjudication_id"])
        adjudication_ids.append(adjudication_id)
        artifacts[f"{root_relative}/review/adjudications/{adjudication_id}.json"] = canonical_json_bytes(record)
    report_id = _uuid_id("report")
    human_path = f"{root_relative}/report.md"
    human = "# Provider review\n\n" + ("No schema-valid findings were produced." if not finding_ids else "Review findings are available in the private evidence artifacts.") + "\n"
    artifacts[human_path] = human.encode("utf-8")
    report = {"schema_version": "1.1", "report_id": report_id, "report_version": 1, "created_at": now, "bundle_id": bundle_id, "contract_id": contract_id, "trace_ids": [trace_id], "finding_ids": finding_ids, "feedback_ids": [], "adjudication_ids": adjudication_ids, "verification_ids": verification_ids, "status": "complete" if status == "complete" else "blocked", "human_report": {"path": human_path, "content_sha256": digest_imported_bytes(artifacts[human_path]), "media_type": "text/markdown", "size_bytes": len(artifacts[human_path])}}
    report["machine_report_sha256"] = digest_imported_bytes(canonical_json_bytes(report))
    report_path = f"{root_relative}/report.json"
    artifacts[report_path] = canonical_json_bytes(report)
    terminal_state = "complete" if status == "complete" else "blocked"
    stages = [{"state": "reviewing", "sequence": 1}, {"state": "verifying", "sequence": 2}]
    if terminal_state == "complete":
        stages.extend((
            {"state": "feedback_pending", "sequence": 3},
            {"state": "addressing", "sequence": 4},
            {"state": "closing", "sequence": 5},
            {"state": "complete", "sequence": 6},
        ))
    else:
        stages.append({"state": "blocked", "sequence": 3})
    loop = {"schema_version": "1.1", "loop_id": _uuid_id("loop"), "loop_version": 1, "run_id": run_id, "gig_id": gig_id, "bundle_id": bundle_id, "contract_id": contract_id, "state": terminal_state, "cycle_cap": 1, "cycle_count": 1, "stage_sequence": stages, "finding_ids": finding_ids, "report_ids": [report_id], "feedback_ids": [], "adjudication_ids": adjudication_ids, "trace_ids": [trace_id], "verification_ids": verification_ids, "addressed_artifact_ids": [], "terminal_decision": {"state": terminal_state, "reason": "provider review completed" if status == "complete" else "one or more provider participants failed or returned invalid output", "next_action": "apply an explicit fix and create a fresh Run Plan" if finding_ids else None}, "created_at": now, "updated_at": now}
    artifacts[f"{root_relative}/review-loop.json"] = canonical_json_bytes(loop)
    result = {"schema_version": "1.0", "run_id": run_id, "run_plan_id": run_plan_id, "status": status, "finding_count": len(findings), "report_path": human_path}
    artifacts[f"{root_relative}/result.json"] = canonical_json_bytes(result)
    return artifacts, result


def _validate_terminal_artifacts(workpad: Path, artifacts: Mapping[str, bytes], result: Mapping[str, object]) -> None:
    for path, data in artifacts.items():
        if path.endswith(".json") and not path.endswith("result.json"):
            schema = "trace.schema.json" if path.endswith("/traces/" + Path(path).name) else None
            if path.endswith("/review/bundle.json"):
                schema = "review-bundle.schema.json"
            elif "/findings/" in path:
                schema = "finding.schema.json"
            elif "/verification/" in path:
                schema = "verification-record.schema.json"
            elif "/adjudications/" in path:
                schema = "adjudication.schema.json"
            elif path.endswith("report.json"):
                schema = "report.schema.json"
            elif path.endswith("review-loop.json"):
                schema = "review-loop.schema.json"
            if schema and not validate_serialized_contract(schema, data).valid:
                raise ProviderReviewError("provider_review_artifact_invalid", f"{schema} validation failed")
    loop = next(data for path, data in artifacts.items() if path.endswith("review-loop.json"))
    if not validate_review_loop(loop).valid:
        raise ProviderReviewError("provider_review_artifact_invalid", "review loop validation failed")


def _read_replay(path: Path, run_id: str, plan_id: str) -> ProviderReviewResult:
    try:
        payload = parse_json_bytes(path.read_bytes())
    except Exception as exc:
        raise ProviderReviewError("provider_review_conflict", "existing provider review result is invalid") from exc
    if not isinstance(payload, Mapping) or payload.get("run_id") != run_id or payload.get("run_plan_id") != plan_id:
        raise ProviderReviewError("provider_review_conflict", "existing provider review belongs to another Run or plan")
    return ProviderReviewResult(run_id, plan_id, _string(payload.get("status"), "provider review status"), int(payload.get("finding_count", 0)), _string(payload.get("report_path"), "provider review report"), True)


def _bundle_payload(bundle_id: str, now: str, references: tuple[SelectedReference, ...]) -> dict[str, object]:
    return {
        "schema_version": "1.0", "bundle_id": bundle_id, "bundle_version": 1, "created_at": now,
        "created_by": {"kind": "gigai", "id": "g43.1-provider-review", "model_target": None},
        "name": "provider-review", "question": "Review the sealed inputs against the approved Gig requirements.",
        "references": [
            {
                "reference_id": item.reference_id, "role": "primary", "kind": "other", "path": f"inputs/{Path(item.path).name}",
                "media_type": item.media_type, "content_sha256": item.content_sha256,
                "canonical_sha256": item.content_sha256, "size_bytes": len(item.content),
                "provenance": {"source_kind": "generated", "locator": "sealed-run-plan-input", "acquired_at": now, "acquisition_method": "g43.1-provider-review", "source_revision": None},
                "sensitivity": "private", "redaction_status": "not_required",
            }
            for item in references
        ],
        "tool_requirements": None,
        "redaction_policy": {"mode": "explicit_projection", "allowed_reference_ids": [item.reference_id for item in references], "policy_version": "g43.1-explicit-sealed-inputs", "detector_version": None},
    }


def _local_evidence_refs(evidence: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {**item, "path": f"inputs/{Path(str(item['path'])).name}"}
        for item in evidence
    ]


def _bundle_for_refs(references: tuple[SelectedReference, ...]) -> dict[str, object]:
    # Findings are validated before terminal artifacts receive their final
    # timestamp.  Their evidence authority is the same exact selected refs.
    return {"references": [{"reference_id": item.reference_id, "content_sha256": item.content_sha256} for item in references]}


def _safe_artifact_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute() or "\\" in relative or ".." in Path(relative).parts:
        raise ProviderReviewError("provider_review_plan_invalid", "artifact path is unsafe")
    candidate = root / relative
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ProviderReviewError("provider_review_plan_invalid", "artifact path contains a symlink")
    return candidate


def _json_object(value: str) -> Mapping[str, object]:
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _confidence(value: str) -> bool:
    try:
        parsed = float(value)
    except ValueError:
        return False
    return 0 <= parsed <= 1 and value == str(parsed) or value in {"0", "1", "1.0"}


def _limited(value: object, limit: int, fallback: str) -> str:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else fallback


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ProviderReviewError("provider_review_plan_invalid", f"sealed {name} is invalid")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderReviewError("provider_review_artifact_invalid", f"{name} is invalid")
    return value


def _uuid_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["ProviderReviewError", "ProviderReviewResult", "execute_provider_review"]
