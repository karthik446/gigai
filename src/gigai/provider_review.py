"""Bounded provider-backed execution for one sealed G43 document review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Mapping
import uuid

from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, parse_json_front_matter, validate_entity_id, EntityPrefix
from .config import load_config
from .journal import JournalArtifact, JournalConflictError, _git as _journal_git, record_transition
from .model_execution import InvocationBudget, InvocationPolicy, SelectedReference, run_model_invocation
from .model_discovery import recorded_target_readiness, resolve_target_readiness
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


@dataclass(frozen=True)
class ProviderReviewCloseoutResult:
    """One non-authorizing, direct-operator clean-review closeout receipt."""

    closeout_id: str
    run_id: str
    run_plan_id: str
    receipt_path: str
    replayed: bool


def close_provider_review_no_fix_required(*, home_root: Path, requested_target: Path | None, gig_id: str | None, run_id: str, run_plan_id: str, direct_operator_confirmed: bool) -> ProviderReviewCloseoutResult:
    """Record the sole supported clean G43.1 closeout; it grants no new authority."""

    if not direct_operator_confirmed:
        raise ProviderReviewError("provider_review_closeout_confirmation_required", "no-fix closeout requires direct --confirm confirmation")
    try:
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
        resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
        sealed = read_run_plan(home_root=home_root, requested_target=requested_target, gig_id=resolved.gig_id, run_plan_id=run_plan_id)
    except (RunPlanError, WorkpadError, OSError, ValueError) as exc:
        raise ProviderReviewError(getattr(exc, "code", "provider_review_closeout_invalid"), str(exc)) from exc
    reviewer_ids = _require_standard_typed_closure_plan(sealed.plan)
    _require_direct_run_consent(resolved.path, run_id, run_plan_id, sealed.content_sha256)
    _require_run_plan_binding(resolved.path, resolved.gig_id, run_id, run_plan_id, sealed.content_sha256)
    base = f"runs/{run_id}/provider-reviews/{run_plan_id}"
    evidence = _authenticated_clean_evidence(resolved.path, base, run_id, run_plan_id, resolved.gig_id, sealed.plan)
    evidence["reviewer_participant_ids"] = reviewer_ids
    receipt_path = f"{base}/closeout/no-fix-required.json"
    existing = resolved.path / receipt_path
    if existing.exists() or existing.is_symlink():
        return _read_closeout_replay(resolved.path, receipt_path, run_id, run_plan_id, sealed.content_sha256, resolved.project_id, resolved.gig_id, evidence)
    receipt = {
        "schema_version": "1.0",
        "closeout_id": _uuid_id("provider_review_closeout"),
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "run_id": run_id,
        "run_plan_id": run_plan_id,
        "run_plan_content_sha256": sealed.content_sha256,
        "result_ref": evidence["result_ref"],
        "report_ref": evidence["report_ref"],
        "review_loop_ref": evidence["review_loop_ref"],
        "reviewer_participant_ids": evidence["reviewer_participant_ids"],
        "decision": "no_fix_required",
        "confirmed_by": {"kind": "operator", "id": "local-user", "model_target": None},
        "confirmed_at": _now(),
    }
    receipt_bytes = canonical_json_bytes(receipt)
    if not validate_serialized_contract("provider-review-closeout-receipt.schema.json", receipt_bytes).valid:
        raise ProviderReviewError("provider_review_closeout_invalid", "constructed no-fix closeout receipt failed schema validation")
    try:
        record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=_uuid_id("handoff"),
            transition="provider_review_no_fix_required",
            body=f"Provider review {run_id}/{run_plan_id} was directly confirmed as no fix required.",
            artifacts=(JournalArtifact(receipt_path, receipt_bytes),),
            front_matter={
                "gig_version": sealed.plan["gig_version"], "run_id": run_id,
                "outcome": "COMPLETE", "actor": {"kind": "operator", "id": "local-user", "model_target": None},
                "evidence": [evidence["result_ref"], evidence["report_ref"], evidence["review_loop_ref"]],
            },
            allow_artifact_replacement=False,
        )
    except JournalConflictError as exc:
        # Another caller may have published this exact closeout after our
        # initial read. The journal lock has been released; authenticate its
        # receipt instead of either overwriting it or failing an identical retry.
        if existing.exists() or existing.is_symlink():
            current_evidence = _authenticated_clean_evidence(
                resolved.path, base, run_id, run_plan_id, resolved.gig_id, sealed.plan
            )
            current_evidence["reviewer_participant_ids"] = reviewer_ids
            return _read_closeout_replay(
                resolved.path, receipt_path, run_id, run_plan_id,
                sealed.content_sha256, resolved.project_id, resolved.gig_id,
                current_evidence,
            )
        raise ProviderReviewError("provider_review_closeout_journal_failed", str(exc)) from exc
    except Exception as exc:
        raise ProviderReviewError("provider_review_closeout_journal_failed", str(exc)) from exc
    return ProviderReviewCloseoutResult(str(receipt["closeout_id"]), run_id, run_plan_id, receipt_path, False)


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
    _require_direct_run_consent(resolved.path, run_id, run_plan_id, sealed.content_sha256)
    root = run_root / "provider-reviews" / run_plan_id
    result_path = root / "result.json"
    if result_path.exists() and not result_path.is_symlink():
        return _read_replay(result_path, run_id, run_plan_id)
    if root.exists() or root.is_symlink():
        raise ProviderReviewError("provider_review_conflict", "provider review directory already exists without a terminal result")

    references, input_artifacts, reference_roles = _sealed_text_inputs(resolved.path, plan, run_id, run_plan_id)
    config = load_config(home_root)
    participants = [item for item in plan.get("participants", []) if isinstance(item, Mapping)]
    _require_current_targets(home_root, config, resolved.path, participants)
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
            prompt=_review_prompt(contract, participant, reference_roles),
            references=references,
            selected_reference_ids=tuple(item.reference_id for item in references),
            policy=policy,
            budget=budget,
        )
        reviewer_calls.append(execution)
        if execution.result is None or execution.record.get("outcome") != "succeeded":
            reviewer_failed = True
            continue
        parsed_findings, invalid = _parse_findings(execution.result.output_text, contract, references, reference_roles, trace_id, participant)
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
                prompt=_verify_prompt(findings, reference_roles),
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
        reviewer_invocations=[{"participant_id": str(participant["participant_id"]), "invocation_id": str(call.record["invocation_id"])} for participant, call in zip((item for item in participants if "reviewer" in item.get("roles", [])), reviewer_calls, strict=True)],
        references=references,
        reference_roles=reference_roles,
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


def _require_direct_run_consent(workpad: Path, run_id: str, run_plan_id: str, plan_digest: str) -> None:
    try:
        path, data = _safe_existing_artifact(workpad, f"runs/{run_id}/operator-consent.json")
    except ProviderReviewError as exc:
        if exc.code == "provider_review_closeout_evidence_missing":
            raise ProviderReviewError("provider_review_consent_missing", "provider review requires the Run's direct --confirm consent") from exc
        raise
    if path.is_symlink() or not path.is_file():
        raise ProviderReviewError("provider_review_consent_missing", "provider review requires the Run's direct --confirm consent")
    try:
        consent = parse_json_bytes(data)
    except Exception as exc:
        raise ProviderReviewError("provider_review_consent_missing", "Run consent is unreadable") from exc
    scope = consent.get("scope") if isinstance(consent, Mapping) else None
    if not isinstance(scope, Mapping) or consent.get("source") != "direct_cli_confirm" or scope.get("run_plan_id") != run_plan_id or scope.get("run_plan_content_sha256") != plan_digest or scope.get("provider_review_requested") is not True:
        raise ProviderReviewError("provider_review_consent_mismatch", "Run consent does not bind this exact sealed review plan")


def _require_standard_typed_closure_plan(plan: Mapping[str, object]) -> list[str]:
    """Keep the no-fix decision narrower than the general review executor."""

    profile = plan.get("profile")
    inputs = plan.get("inputs")
    participants = plan.get("participants")
    if not isinstance(profile, Mapping) or profile.get("profile_id") != "standard":
        raise ProviderReviewError("provider_review_closeout_not_eligible", "no-fix closeout requires the standard review profile")
    if not isinstance(inputs, list) or {item.get("role") for item in inputs if isinstance(item, Mapping)} != {"review_subject", "requirements_baseline"}:
        raise ProviderReviewError("provider_review_closeout_not_eligible", "no-fix closeout requires the typed G43.1 subject and baseline inputs")
    reviewers = [item for item in participants or [] if isinstance(item, Mapping) and "reviewer" in item.get("roles", [])]
    ids = [item.get("participant_id") for item in reviewers]
    groups = [item.get("independence_group") for item in reviewers]
    if len(reviewers) != 2 or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != 2 or any(not isinstance(value, str) or not value for value in groups) or len(set(groups)) != 2:
        raise ProviderReviewError("provider_review_closeout_not_eligible", "no-fix closeout requires two distinct independent reviewers")
    return [str(value) for value in ids]


def _require_run_plan_binding(workpad: Path, gig_id: str, run_id: str, plan_id: str, plan_digest: str) -> None:
    """A consent-shaped file cannot substitute a Run's sealed-plan bridge."""

    relative = f"runs/{run_id}/review/evidence/sealed-input.json"
    _path, data = _safe_existing_artifact(workpad, relative)
    try:
        bridge = parse_json_bytes(data)
    except Exception as exc:
        raise ProviderReviewError("provider_review_closeout_run_mismatch", "Run review bridge is unreadable") from exc
    if not isinstance(bridge, Mapping) or bridge.get("run_id") != run_id or bridge.get("run_plan_id") != plan_id or bridge.get("run_plan_content_sha256") != plan_digest:
        raise ProviderReviewError("provider_review_closeout_run_mismatch", "Run does not bind this exact sealed review Plan")
    _require_exact_journal_artifact(workpad, relative, data, gig_id, run_id, "run_review_loop_materialized", "g43-bridge")


def _safe_existing_artifact(workpad: Path, relative: str) -> tuple[Path, bytes]:
    path = _safe_artifact_path(workpad, relative)
    if path.is_symlink() or not path.is_file():
        raise ProviderReviewError("provider_review_closeout_evidence_missing", "provider review terminal evidence is unavailable")
    try:
        return path, path.read_bytes()
    except OSError as exc:
        raise ProviderReviewError("provider_review_closeout_evidence_missing", "provider review terminal evidence is unavailable") from exc


def _artifact_ref(relative: str, data: bytes, media_type: str = "application/json") -> dict[str, object]:
    return {"path": relative, "content_sha256": digest_imported_bytes(data), "media_type": media_type, "size_bytes": len(data)}


def _authenticated_clean_evidence(workpad: Path, base: str, run_id: str, plan_id: str, gig_id: str, plan: Mapping[str, object]) -> dict[str, object]:
    """Read zero-finding terminal evidence only when its producer journaled it."""

    paths = {"result": f"{base}/result.json", "report": f"{base}/report.json", "loop": f"{base}/review-loop.json"}
    raw = {name: _safe_existing_artifact(workpad, relative)[1] for name, relative in paths.items()}
    try:
        result = parse_json_bytes(raw["result"])
        report = parse_json_bytes(raw["report"])
        loop = parse_json_bytes(raw["loop"])
    except Exception as exc:
        raise ProviderReviewError("provider_review_closeout_evidence_invalid", "provider review terminal evidence is unreadable") from exc
    if not isinstance(result, Mapping) or result.get("run_id") != run_id or result.get("run_plan_id") != plan_id or result.get("status") != "complete" or result.get("finding_count") != 0:
        raise ProviderReviewError("provider_review_closeout_not_clean", "provider review result is not a clean terminal result for this Run and Plan")
    if not isinstance(report, Mapping) or not validate_serialized_contract("report.schema.json", raw["report"]).valid or report.get("status") != "complete" or report.get("finding_ids") != []:
        raise ProviderReviewError("provider_review_closeout_not_clean", "provider review report is not a schema-valid zero-finding completion")
    if not isinstance(loop, Mapping) or not validate_review_loop(raw["loop"]).valid or loop.get("run_id") != run_id or loop.get("gig_id") != gig_id or loop.get("state") != "complete" or loop.get("finding_ids") != []:
        raise ProviderReviewError("provider_review_closeout_not_clean", "provider review loop is not a schema-valid zero-finding completion")
    _require_authenticated_reviewer_invocations(workpad, result, plan, gig_id, run_id)
    _require_terminal_artifacts_journaled(workpad, tuple(paths.values()), raw, gig_id, run_id)
    return {
        "result_ref": _artifact_ref(paths["result"], raw["result"]),
        "report_ref": _artifact_ref(paths["report"], raw["report"]),
        "review_loop_ref": _artifact_ref(paths["loop"], raw["loop"]),
        "reviewer_participant_ids": [],  # replaced from the sealed Plan before receipt construction
    }


def _require_authenticated_reviewer_invocations(workpad: Path, result: Mapping[str, object], plan: Mapping[str, object], gig_id: str, run_id: str) -> None:
    expected = {
        str(item.get("participant_id")): str(item.get("model_target_id"))
        for item in plan.get("participants", [])
        if isinstance(item, Mapping) and "reviewer" in item.get("roles", [])
    }
    observed = result.get("reviewer_invocations")
    if not isinstance(observed, list) or len(observed) != len(expected):
        raise ProviderReviewError("provider_review_closeout_invocation_invalid", "terminal review result lacks one invocation for each sealed reviewer")
    seen: set[str] = set()
    for item in observed:
        if not isinstance(item, Mapping) or not isinstance(item.get("participant_id"), str) or not isinstance(item.get("invocation_id"), str):
            raise ProviderReviewError("provider_review_closeout_invocation_invalid", "terminal reviewer invocation identity is malformed")
        participant_id = item["participant_id"]
        invocation_id = item["invocation_id"]
        if participant_id not in expected or participant_id in seen:
            raise ProviderReviewError("provider_review_closeout_invocation_invalid", "terminal reviewer invocation identities do not match the sealed Plan")
        seen.add(participant_id)
        relative = f"runs/{run_id}/model-invocations/{invocation_id}/record.json"
        _path, data = _safe_existing_artifact(workpad, relative)
        try:
            record = parse_json_bytes(data)
        except Exception as exc:
            raise ProviderReviewError("provider_review_closeout_invocation_invalid", "reviewer invocation record is unreadable") from exc
        if not isinstance(record, Mapping) or not validate_serialized_contract("model-invocation.schema.json", data).valid or record.get("run_id") != run_id or record.get("invocation_id") != invocation_id or record.get("role") != "reviewer" or record.get("configured_selector") != expected[participant_id] or record.get("outcome") != "succeeded":
            raise ProviderReviewError("provider_review_closeout_invocation_invalid", "reviewer invocation record does not bind the sealed reviewer")
        _require_exact_journal_artifact(workpad, relative, data, gig_id, run_id, "goal_completed", "g18-model-execution")
        request = record.get("request")
        if not isinstance(request, Mapping):
            raise ProviderReviewError("provider_review_closeout_invocation_invalid", "reviewer invocation request evidence is malformed")
        _require_invocation_ref(workpad, request.get("request_artifact"), gig_id, run_id)
        extensions = record.get("extensions")
        response_refs = [item.get("value") for item in extensions if isinstance(item, Mapping) and item.get("namespace") == "gigai.g18" and item.get("name") == "response_artifact"] if isinstance(extensions, list) else []
        if len(response_refs) != 1:
            raise ProviderReviewError("provider_review_closeout_invocation_invalid", "successful reviewer invocation lacks exactly one response artifact")
        _require_invocation_ref(workpad, response_refs[0], gig_id, run_id)
    if seen != set(expected):
        raise ProviderReviewError("provider_review_closeout_invocation_invalid", "not every sealed reviewer has authenticated successful invocation evidence")


def _require_invocation_ref(workpad: Path, reference: object, gig_id: str, run_id: str) -> None:
    if not isinstance(reference, Mapping) or not isinstance(reference.get("path"), str) or not isinstance(reference.get("content_sha256"), str) or type(reference.get("size_bytes")) is not int:
        raise ProviderReviewError("provider_review_closeout_invocation_invalid", "invocation artifact reference is malformed")
    relative = reference["path"]
    _path, data = _safe_existing_artifact(workpad, relative)
    if digest_imported_bytes(data) != reference["content_sha256"] or len(data) != reference["size_bytes"]:
        raise ProviderReviewError("provider_review_closeout_invocation_invalid", "invocation artifact bytes changed")
    _require_exact_journal_artifact(workpad, relative, data, gig_id, run_id, "goal_completed", "g18-model-execution")


def _require_terminal_artifacts_journaled(workpad: Path, paths: tuple[str, ...], raw: Mapping[str, bytes], gig_id: str, run_id: str) -> None:
    """Require the exact bytes in one authenticated G43.1 terminal handoff."""

    commits = _journal_git(workpad, "log", "--format=%H", "--", *paths, check=False).stdout.splitlines()
    for commit in commits:
        changed = set(_journal_git(workpad, "show", "--format=", "--name-only", commit).stdout.splitlines())
        if not set(paths).issubset(changed):
            continue
        if any(_journal_git(workpad, "show", f"{commit}:{path}", check=False).stdout.encode("utf-8") != raw[name] for name, path in zip(("result", "report", "loop"), paths, strict=True)):
            continue
        handoffs = [path for path in changed if path.startswith("handoffs/") and path.endswith(".txt")]
        for handoff in handoffs:
            shown = _journal_git(workpad, "show", f"{commit}:{handoff}", check=False)
            if shown.returncode != 0:
                continue
            try:
                metadata, _body = parse_json_front_matter(shown.stdout.encode("utf-8"))
            except Exception:
                continue
            actor = metadata.get("actor")
            if metadata.get("transition") == "goal_completed" and metadata.get("gig_id") == gig_id and metadata.get("run_id") == run_id and metadata.get("outcome") == "COMPLETE" and isinstance(actor, Mapping) and actor.get("kind") == "gigai" and actor.get("id") == "g43.1-provider-review":
                return
    raise ProviderReviewError("provider_review_closeout_evidence_unjournaled", "provider review terminal artifacts are not authenticated G43.1 journal evidence")


def _require_exact_journal_artifact(workpad: Path, relative: str, data: bytes, gig_id: str, run_id: str, transition: str, actor_id: str) -> None:
    for commit in _journal_git(workpad, "log", "--format=%H", "--", relative, check=False).stdout.splitlines():
        shown = _journal_git(workpad, "show", f"{commit}:{relative}", check=False)
        if shown.returncode != 0 or shown.stdout.encode("utf-8") != data:
            continue
        for handoff in _journal_git(workpad, "show", "--format=", "--name-only", commit).stdout.splitlines():
            if not handoff.startswith("handoffs/"):
                continue
            candidate = _journal_git(workpad, "show", f"{commit}:{handoff}", check=False)
            try:
                metadata, _body = parse_json_front_matter(candidate.stdout.encode("utf-8"))
            except Exception:
                continue
            actor = metadata.get("actor")
            if metadata.get("transition") == transition and metadata.get("gig_id") == gig_id and metadata.get("run_id") == run_id and isinstance(actor, Mapping) and actor.get("kind") == "gigai" and actor.get("id") == actor_id:
                return
    raise ProviderReviewError("provider_review_closeout_run_unjournaled", "Run-to-Plan binding is not authenticated journal evidence")


def _read_closeout_replay(workpad: Path, receipt_path: str, run_id: str, plan_id: str, plan_digest: str, project_id: str, gig_id: str, evidence: Mapping[str, object]) -> ProviderReviewCloseoutResult:
    path, data = _safe_existing_artifact(workpad, receipt_path)
    if not validate_serialized_contract("provider-review-closeout-receipt.schema.json", data).valid:
        raise ProviderReviewError("provider_review_closeout_conflict", "existing closeout receipt is invalid")
    receipt = parse_json_bytes(data)
    expected = {"project_id": project_id, "gig_id": gig_id, "run_id": run_id, "run_plan_id": plan_id, "run_plan_content_sha256": plan_digest, "decision": "no_fix_required", "result_ref": evidence["result_ref"], "report_ref": evidence["report_ref"], "review_loop_ref": evidence["review_loop_ref"], "reviewer_participant_ids": evidence["reviewer_participant_ids"]}
    if not isinstance(receipt, Mapping) or any(receipt.get(key) != value for key, value in expected.items()) or not _journaled_closeout(workpad, receipt_path, data, gig_id, run_id):
        raise ProviderReviewError("provider_review_closeout_conflict", "existing closeout receipt does not bind this exact clean review")
    closeout_id = receipt.get("closeout_id")
    if not isinstance(closeout_id, str):
        raise ProviderReviewError("provider_review_closeout_conflict", "existing closeout receipt is invalid")
    return ProviderReviewCloseoutResult(closeout_id, run_id, plan_id, receipt_path, True)


def _journaled_closeout(workpad: Path, receipt_path: str, receipt_bytes: bytes, gig_id: str, run_id: str) -> bool:
    commits = _journal_git(workpad, "log", "--format=%H", "--", receipt_path, check=False).stdout.splitlines()
    for commit in commits:
        committed = _journal_git(workpad, "show", f"{commit}:{receipt_path}", check=False)
        if committed.returncode != 0 or committed.stdout.encode("utf-8") != receipt_bytes:
            continue
        for handoff in _journal_git(workpad, "show", "--format=", "--name-only", commit).stdout.splitlines():
            if not handoff.startswith("handoffs/"):
                continue
            shown = _journal_git(workpad, "show", f"{commit}:{handoff}", check=False)
            try:
                metadata, _body = parse_json_front_matter(shown.stdout.encode("utf-8"))
            except Exception:
                continue
            actor = metadata.get("actor")
            if metadata.get("transition") == "provider_review_no_fix_required" and metadata.get("gig_id") == gig_id and metadata.get("run_id") == run_id and isinstance(actor, Mapping) and actor.get("kind") == "operator" and actor.get("id") == "local-user":
                return True
    return False


def _sealed_text_inputs(workpad: Path, plan: Mapping[str, object], run_id: str, plan_id: str) -> tuple[tuple[SelectedReference, ...], list[dict[str, object]], dict[str, str]]:
    refs: list[SelectedReference] = []
    artifacts: list[dict[str, object]] = []
    roles: dict[str, str] = {}
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
        role = item.get("role")
        roles[reference_id] = str(role) if isinstance(role, str) else "primary"
    if not refs:
        raise ProviderReviewError("provider_review_not_eligible", "provider review needs at least one sealed text input")
    return tuple(refs), artifacts, roles


def _require_current_targets(home_root: Path, config, workpad: Path, participants: list[Mapping[str, object]]) -> None:
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
        if readiness.readiness == "configured":
            readiness = recorded_target_readiness(home_root, config, target) or readiness
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


def _review_prompt(contract: Mapping[str, object], participant: Mapping[str, object], reference_roles: Mapping[str, str]) -> str:
    criteria = contract.get("criteria", [])
    role_lines = "\n".join(
        f"- {'Review subject' if role == 'review_subject' else 'Requirements baseline' if role == 'requirements_baseline' else role}: reference_id={reference_id}"
        for reference_id, role in sorted(reference_roles.items())
    )
    typed_closure = set(reference_roles.values()) == {"review_subject", "requirements_baseline"}
    output_shape = (
        "For criterion_requirements, each finding must instead contain an `evidence` array with exactly two objects "
        "(reference_id and locator): one citing the Requirements baseline and one citing the Review subject. "
        if typed_closure
        else ""
    )
    return (
        "You are one independent document reviewer. Review only the supplied sealed references. "
        "Do not use outside knowledge or propose file edits. Return strict JSON: your entire response must be exactly one JSON object "
        "with a top-level `findings` array: no Markdown fences, no prose, no headings, or commentary before or after it. "
        "Do not emit any second JSON value. "
        "Each finding must contain criterion_id, severity (info|low|medium|high|critical), title, description, "
        "reference_id, locator, and confidence (a string from 0 to 1). If no issue exists, return {\"findings\":[]}.\n"
        + output_shape
        + "Sealed reference roles:\n"
        + role_lines
        + "\n"
        f"Question: {contract.get('question')}\nCriteria: {json.dumps(criteria, sort_keys=True)}\n"
        f"Participant: {participant.get('participant_id')}"
    )


def _verify_prompt(findings: list[dict[str, object]], reference_roles: Mapping[str, str]) -> str:
    role_lines = "\n".join(
        f"- {'Review subject' if role == 'review_subject' else 'Requirements baseline' if role == 'requirements_baseline' else role}: reference_id={reference_id}"
        for reference_id, role in sorted(reference_roles.items())
    )
    return (
        "You are an independent verifier. Check the supplied sealed references and these proposed findings. "
        "Return strict JSON: your entire response must be exactly one JSON object with `outcomes`, one object per finding: finding_id, status "
        "(verified|contradicted|unverified|blocked), and reason. Do not use outside knowledge.\n"
        "Emit no Markdown fences, no prose, no headings, and no commentary before or after the JSON object. Do not emit a second JSON value.\n"
        "Sealed reference roles:\n"
        + role_lines
        + "\n"
        + json.dumps(findings, sort_keys=True, separators=(",", ":"))
    )


def _adjudicate_prompt(findings: list[dict[str, object]], verifications: list[dict[str, object]]) -> str:
    return (
        "You are the sealed adjudicator. Decide each proposed finding using only the supplied sealed references, "
        "the findings, and verifier records. Return strict JSON: your entire response must be exactly one JSON object with `decisions`, "
        "one object per finding: finding_id, decision (accepted|rejected|deferred|unanswerable), and rationale. "
        "Do not use outside knowledge. Emit no Markdown fences, no prose, no headings, and no commentary before or after the JSON object, "
        "and do not emit a second JSON value.\n"
        + json.dumps({"findings": findings, "verifications": verifications}, sort_keys=True, separators=(",", ":"))
    )


def _parse_findings(output: str, contract: Mapping[str, object], references: tuple[SelectedReference, ...], reference_roles: Mapping[str, str], trace_id: str, participant: Mapping[str, object]) -> tuple[list[dict[str, object]], bool]:
    payload = _json_object(output)
    raw = payload.get("findings") if isinstance(payload, Mapping) else None
    if not isinstance(raw, list):
        return [], True
    criteria = {item.get("criterion_id") for item in contract.get("criteria", []) if isinstance(item, Mapping)}
    refs = {item.reference_id: item for item in references}
    typed_closure = set(reference_roles.values()) == {"review_subject", "requirements_baseline"}
    evaluator = {"evaluator_id": "evaluator_g43", "evaluator_version": "g43.1", "stage": "model"}
    findings: list[dict[str, object]] = []
    invalid = len(raw) > 20
    for item in raw:
        if not isinstance(item, Mapping):
            invalid = True
            continue
        criterion = item.get("criterion_id")
        if criterion not in criteria:
            invalid = True
            continue
        evidence: list[dict[str, object]] = []
        if typed_closure and criterion == "criterion_requirements":
            raw_evidence = item.get("evidence")
            if not isinstance(raw_evidence, list) or len(raw_evidence) != 2:
                invalid = True
                continue
            evidence_roles: set[str] = set()
            valid_evidence = True
            for candidate in raw_evidence:
                if not isinstance(candidate, Mapping):
                    valid_evidence = False
                    break
                reference_id = candidate.get("reference_id")
                locator = candidate.get("locator")
                role = reference_roles.get(reference_id) if isinstance(reference_id, str) else None
                if reference_id not in refs or role not in {"review_subject", "requirements_baseline"} or role in evidence_roles or not isinstance(locator, str) or not locator.strip():
                    valid_evidence = False
                    break
                evidence_roles.add(role)
                evidence.append({"reference_id": reference_id, "content_sha256": refs[reference_id].content_sha256, "locator": locator, "quote": None})
            if not valid_evidence or evidence_roles != {"review_subject", "requirements_baseline"}:
                invalid = True
                continue
        else:
            reference_id = item.get("reference_id")
            locator = item.get("locator")
            if reference_id not in refs or not isinstance(locator, str) or not locator.strip():
                invalid = True
                continue
            evidence.append({"reference_id": reference_id, "content_sha256": refs[reference_id].content_sha256, "locator": locator, "quote": None})
        severity = item.get("severity") if item.get("severity") in {"info", "low", "medium", "high", "critical"} else "medium"
        confidence = item.get("confidence") if isinstance(item.get("confidence"), str) and _confidence(item["confidence"]) else "0.5"
        finding = {
            "schema_version": "1.0", "finding_id": _uuid_id("finding"), "finding_version": 1,
            "criterion_id": criterion, "status": "open", "severity": severity,
            "title": _limited(item.get("title"), 500, "Untitled review finding"),
            "description": _limited(item.get("description"), 20000, "Model reported a review concern without a description."),
            "evidence": evidence,
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


def _terminal_artifacts(*, root_relative: str, now: str, run_id: str, gig_id: str, run_plan_id: str, bundle_id: str, contract_id: str, trace_id: str, findings: list[dict[str, object]], verifications: list[dict[str, object]], adjudications: list[dict[str, object]], invocation_ids: list[str], reviewer_invocations: list[dict[str, str]], references: tuple[SelectedReference, ...], reference_roles: Mapping[str, str], input_artifacts: list[dict[str, object]], input_payloads: Mapping[str, bytes], status: str) -> tuple[dict[str, bytes], dict[str, object]]:
    artifacts: dict[str, bytes] = {}
    for ref in input_artifacts:
        path = ref["path"]
        if not isinstance(path, str) or path not in input_payloads:
            raise ProviderReviewError("provider_review_input_mismatch", "provider review input bytes are unavailable")
        artifacts[path] = input_payloads[path]
    bundle = _bundle_payload(bundle_id, now, references, reference_roles)
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
    result = {"schema_version": "1.0", "run_id": run_id, "run_plan_id": run_plan_id, "status": status, "finding_count": len(findings), "reviewer_invocations": reviewer_invocations, "report_path": human_path}
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


def _bundle_payload(bundle_id: str, now: str, references: tuple[SelectedReference, ...], reference_roles: Mapping[str, str]) -> dict[str, object]:
    return {
        "schema_version": "1.0", "bundle_id": bundle_id, "bundle_version": 1, "created_at": now,
        "created_by": {"kind": "gigai", "id": "g43.1-provider-review", "model_target": None},
        "name": "provider-review", "question": "Review the sealed inputs against the approved Gig requirements.",
        "references": [
            {
                "reference_id": item.reference_id, "role": reference_roles.get(item.reference_id, "primary"), "kind": "other", "path": f"inputs/{Path(item.path).name}",
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


_MAX_PROVIDER_RESPONSE_CHARS = 1_000_000
_MAX_JSON_NESTING = 128
_FENCE_LINE = re.compile(r"(?m)^[ \t]*```(?P<info>[A-Za-z0-9_-]*)[ \t]*(?:\r?\n|$)")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member name: {key!r}")
        result[key] = item
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _within_json_nesting_bound(candidate: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > _MAX_JSON_NESTING:
                return False
        elif char in "}]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def _has_valid_unicode(value: object) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                return False
        elif isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return True


def _parse_framed_json_object(text: str) -> Mapping[str, object] | None:
    """Parse one bounded provider JSON object without changing provider bytes.

    The framing exception is intentionally narrow: one complete Markdown code
    fence may contain the object, with ordinary prose around it. Any other
    candidate payload, fence, duplicate key, array, or trailing JSON is rejected.
    """

    if not text or len(text) > _MAX_PROVIDER_RESPONSE_CHARS:
        return None
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None

    def parse_object(candidate: str) -> Mapping[str, object] | None:
        if not _within_json_nesting_bound(candidate):
            return None
        try:
            parsed = json.loads(
                candidate,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (RecursionError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, Mapping) and _has_valid_unicode(parsed) else None

    raw = parse_object(text.strip())
    if raw is not None:
        return raw

    markers = list(_FENCE_LINE.finditer(text))
    if len(markers) != 2:
        return None
    opening, closing = markers
    if opening.group("info") not in {"", "json"} or closing.group("info"):
        return None
    payload = text[opening.end() : closing.start()].strip()
    framed = parse_object(payload)
    if framed is None:
        return None

    # Any JSON delimiters or extra fence token in surrounding prose make the
    # framing ambiguous, even when those bytes do not form valid JSON.
    surrounding = text[: opening.start()] + text[closing.end() :]
    if any(delimiter in surrounding for delimiter in "{}[]") or "```" in surrounding:
        return None
    return framed


def _json_object(value: str) -> Mapping[str, object]:
    parsed = _parse_framed_json_object(value)
    return parsed if parsed is not None else {}


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


__all__ = [
    "ProviderReviewCloseoutResult",
    "ProviderReviewError",
    "ProviderReviewResult",
    "close_provider_review_no_fix_required",
    "execute_provider_review",
]
