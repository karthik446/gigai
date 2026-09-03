"""Bounded G43 review planning and immutable sealed Run Plan evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import mimetypes
from pathlib import Path
import shutil
import uuid
from typing import Iterable, Mapping

from .canonical import canonical_json_bytes, derive_deterministic_id, digest_imported_bytes, parse_json_bytes
from .config import load_config
from .journal import JournalArtifact, record_transition
from .model_discovery import discover_runtime_snapshot, resolve_target_readiness
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


def _target_name(profile: object, role: str) -> str:
    for field in (role, "critic", "adjudicator", "planner"):
        candidate = getattr(profile, field, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    raise RunPlanError("target_not_usable", f"default profile has no target for {role}")


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


def _review_contract(contract_id: str, now: str) -> bytes:
    return canonical_json_bytes({
        "schema_version": "1.0", "contract_id": contract_id, "contract_version": 1,
        "created_at": now, "created_by": {"kind": "gigai", "id": "g43-planner", "model_target": None},
        "name": "sealed-run-review", "question": "Review the sealed declared inputs against the approved Gig requirements.",
        "reference_roles": ["primary"], "criteria": [{"criterion_id": "criterion_requirements", "description": "Declared inputs and approved requirements are reconciled.", "severity": "high", "required_evidence": ["sealed-input"], "citation_requirement": "required", "evaluator_ids": ["evaluator_g43"]}],
        "severity_model": {"levels": ["info", "low", "medium", "high", "critical"], "ordering": ["info", "low", "medium", "high", "critical"]},
        "evidence_requirements": ["sealed-input"], "output_shape": {"machine_media_type": "application/json", "human_media_type": "text/markdown", "required_sections": ["findings"]},
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


def create_run_plan(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None, version: int | None = None, task_class: str | None = None, artifact_class: str | None = None, profile_id: str | None = None, input_paths: Iterable[Path] = (), override_reason: str | None = None, profile_opt_in_reason: str | None = None, reviewer_targets: Iterable[str] = (), verifier_targets: Iterable[str] = (), adjudicator_targets: Iterable[str] = ()) -> RunPlanResult:
    resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
    try:
        authority = _resolve_authority(resolved, __import__("gigai.index", fromlist=["read_index"]).read_index(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id), version)
        _validate_authority(resolved, authority["graph"], authority["proposal"])
    except RunError as exc:
        raise RunPlanError("run_plan_authority_refused", str(exc)) from exc
    inputs_raw = tuple(input_paths)
    if not inputs_raw:
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
    for index, source in enumerate(inputs_raw):
        if source.is_symlink() or not source.is_file():
            raise RunPlanError("run_plan_input_mismatch", "each input must be a regular explicit file")
        data = source.read_bytes()
        media = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        input_bytes.append((f"input_{chr(ord('a') + index)}", data, media))
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
        readiness = resolve_target_readiness(config, target_name)
        if readiness.readiness != "usable":
            raise RunPlanError("target_not_usable", f"target {target_name!r} is not usable for {role}")
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
    contract_bytes = _review_contract(contract_id, proposal_created_at if isinstance(proposal_created_at, str) else "2026-01-01T00:00:00Z")
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
        "input_refs": [digest_imported_bytes(data) for _, data, _ in input_bytes],
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
    input_refs = [_artifact(f"{base}/inputs/{input_id}.bin", data, media) for input_id, data, media in input_bytes]
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
    plan = {"schema_version": "1.0", "run_plan_id": run_plan_id, "plan_version": 1, "state": "sealed", "gig_id": resolved.gig_id, "gig_version": authority["version"], "journal_commit": authority["commit"], "project_id": resolved.project_id, "workpad_locator": f"registry:{resolved.project_id}", "goal_graph": graph_ref, "review_contract": contract_ref, "classification": {"task_class": selected_task, "artifact_class": selected_artifact, "confidence": "high", "classifier_version": "g43-deterministic-1", "considered_inputs": input_refs, "reason": "explicit declared inputs have a bounded deterministic classification", "override": override}, "profile": {"profile_id": selected_profile, "profile_version": 1, "selection": "operator" if profile_id else "deterministic", "opt_in": opt_in, "usage_unreported_policy": "block_before_call"}, "phases": phases, "participants": participants, "inputs": [{"input_id": input_id, "role": "primary", "record_ref": ref, "snapshot_ref": ref} for (input_id, _data, _media), ref in zip(input_bytes, input_refs, strict=True)], "capabilities": {"manifest": capability_ref, "required_capability_ids": capability_ids}, "effects": ["write_workpad"], "budget": projection["budget"], "policy_sha256": projection["policy_sha256"], "discovery_snapshot_refs": projection["discovery_snapshot_refs"], "sealed_sources": [graph_ref, contract_ref, discovery_ref, *input_refs, *target_refs, *([capability_ref] if capability_ref else [])], "created_at": now, "sealed_at": now, "sealed_by": {"kind": "operator", "id": "local-user", "model_target": None}}
    plan_bytes = canonical_json_bytes(plan)
    validation = validate_run_plan(plan_bytes)
    if not validation.valid:
        raise RunPlanError("run_plan_invalid", "; ".join(item.message for item in validation.findings))
    artifacts = (JournalArtifact(f"{base}/run-plan.json", plan_bytes), JournalArtifact(str(contract_ref["path"]), contract_bytes), JournalArtifact(str(discovery_ref["path"]), snapshot_bytes), *(JournalArtifact(str(ref["path"]), data) for (_id, data, _media), ref in zip(input_bytes, input_refs, strict=True)), *target_artifacts)
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
