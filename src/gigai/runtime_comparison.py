"""Durable, domain-neutral comparison of two explicitly selected runtimes.

R6 is an execution-setup comparison, not a model benchmark.  This module owns
the portable evaluation-pack reader, deterministic source-grounded grader, and
the journal-backed comparison service.  It deliberately has no Scout imports;
Scout (or another Gig) supplies the versioned synthetic pack.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import json
from importlib import resources
import time
from pathlib import Path
from typing import Any
import uuid

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
    generate_entity_id,
    parse_json_bytes,
    validate_entity_id,
)
from .config import GigAIConfig
from .index import read_index
from .journal import JournalArtifact, JournalError, read_committed_artifact, record_transition
from .model_execution import (
    InvocationBudget,
    InvocationPolicy,
    ModelInvocationExecution,
    SelectedReference,
    run_model_invocation,
)
from .model_targets import resolve_model_target
from .model_discovery import resolve_target_readiness
from .run import RunError, _resolve_authority, resolve_selected_graph_authority
from .validators import validate_serialized_contract
from .workpad import ResolvedWorkpad, WorkpadError, resolve_workpad


PACK_RESOURCE = "runtime-comparison-pack-v1.json"
PACK_SCHEMA = "runtime-evaluation-pack.schema.json"
COMPARISON_SCHEMA = "runtime-comparison.schema.json"
ATTEMPT_SCHEMA = "runtime-comparison-attempt.schema.json"
INTENT_SCHEMA = "runtime-comparison-intent.schema.json"
PACK_VERSION = "1.0"
GRADER_ID = "gigai.source-grounded-comparison-grader"
GRADER_VERSION = "1.0"
OUTPUT_CONTRACT_VERSION = "r7-output-contract:1"


class RuntimeComparisonError(ValueError):
    """A comparison pack, setup, invocation, or persisted result is invalid."""

    code = "runtime_comparison_invalid"


@dataclass(frozen=True)
class EvaluationPack:
    payload: dict[str, Any]
    digest: str

    @property
    def pack_id(self) -> str:
        return str(self.payload["pack_id"])

    @property
    def cases(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.payload["cases"])


@dataclass(frozen=True)
class ComparisonSetup:
    """One explicit configured setup; no implicit target/default resolution."""

    setup_id: str
    target_name: str
    expected_adapter: str
    role: str = "reviewer"
    harness_version: str = "gigai-runtime-harness:1"
    prompt_version: str = "r7-output-contract:1"
    settings: Mapping[str, object] | None = None


InvocationService = Callable[..., ModelInvocationExecution]


def _setups(local_target: str, luna_target: str, *, retry_failed: bool) -> tuple[ComparisonSetup, ComparisonSetup]:
    retry_settings = {"max_retries": 1} if retry_failed else {"max_retries": 0}
    return (
        ComparisonSetup("qwen_ollama", local_target, "ollama_local", settings={"transport": "ollama_api", "think": False, **retry_settings}),
        ComparisonSetup("luna_codex", luna_target, "codex_cli", settings={"transport": "codex_cli", "tools": "bounded", **retry_settings}),
    )


def _canonical_model_digest(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        return None
    candidate = value if value.startswith("sha256:") else f"sha256:{value}"
    return candidate if len(candidate) == 71 and all(char in "0123456789abcdef" for char in candidate[7:]) else None


def _configured_identity(config: GigAIConfig, setup: ComparisonSetup) -> dict[str, object]:
    target = resolve_model_target(config, setup.target_name)
    return {
        "adapter": target.endpoint.adapter,
        "endpoint": target.endpoint.name,
        "model": target.target.model,
        "model_digest": _canonical_model_digest(target.target.model_digest),
    }


def _comparison_intent(
    *, resolved: ResolvedWorkpad, active: Mapping[str, object], authority: Mapping[str, object],
    graph: Mapping[str, object], pack: EvaluationPack, comparison_id: str, graph_selector: str | None,
    setups: tuple[ComparisonSetup, ...], run_ids: tuple[str, ...],
    configured_identities: tuple[dict[str, object], ...], consent: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "1.0", "kind": "runtime_comparison_intent", "comparison_id": comparison_id,
        "project_id": resolved.project_id, "gig_id": resolved.gig_id, "gig_version": active["active_version"],
        "graph_selector": graph_selector,
        "authority": {"journal_commit": authority.get("commit"), "graph_id": graph.get("graph_id"), "goal_id": pack.payload["goal_id"], "pack_sha256": pack.digest, "consent": dict(consent)},
        "pack": {"pack_id": pack.pack_id, "pack_version": pack.payload["pack_version"], "content_sha256": pack.digest},
        "setups": [{"setup_id": setup.setup_id, "target_name": setup.target_name, "expected_adapter": setup.expected_adapter, "settings": dict(setup.settings or {}), "configured_identity": identity} for setup, identity in zip(setups, configured_identities, strict=True)],
        "attempts": [{"setup_id": setup.setup_id, "run_id": run_id} for setup, run_id in zip(setups, run_ids, strict=True)],
        "created_at": _now(),
    }


def _read_intent(resolved: ResolvedWorkpad, comparison_id: str) -> tuple[dict[str, object], str]:
    try:
        data, commit = read_committed_artifact(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
            path=f"comparisons/{comparison_id}/intent.json",
        )
    except (JournalError, OSError, ValueError) as exc:
        raise RuntimeComparisonError("comparison execution intent is unavailable") from exc
    try:
        payload = parse_json_bytes(data)
    except ValueError as exc:
        raise RuntimeComparisonError("comparison execution intent is malformed") from exc
    if not isinstance(payload, dict) or payload.get("comparison_id") != comparison_id:
        raise RuntimeComparisonError("comparison execution intent identity is invalid")
    report = validate_serialized_contract(INTENT_SCHEMA, data)
    if not report.valid:
        raise RuntimeComparisonError("comparison execution intent failed schema validation")
    return payload, commit


def _validate_setup_targets(config: GigAIConfig, setups: tuple[ComparisonSetup, ...]) -> None:
    for setup in setups:
        target = resolve_model_target(config, setup.target_name)
        if target.endpoint.adapter != setup.expected_adapter:
            raise RuntimeComparisonError(f"setup {setup.setup_id} is not an eligible {setup.expected_adapter} target")
        if setup.expected_adapter == "ollama_local" and not target.target.model_digest:
            raise RuntimeComparisonError("local comparison target must have an explicit model digest")


def bundled_pack_bytes() -> bytes:
    """Return the installed pack, so operation does not require a source tree."""

    try:
        return resources.files("gigai.data").joinpath(PACK_RESOURCE).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise RuntimeComparisonError("packaged synthetic evaluation pack is unavailable") from exc


def load_evaluation_pack(path: Path | None = None) -> EvaluationPack:
    try:
        data = path.read_bytes() if path is not None else bundled_pack_bytes()
        payload = parse_json_bytes(data)
    except (OSError, ValueError) as exc:
        raise RuntimeComparisonError(f"cannot read evaluation pack: {exc}") from exc
    return validate_evaluation_pack(payload)


def validate_evaluation_pack(payload: object) -> EvaluationPack:
    if not isinstance(payload, dict):
        raise RuntimeComparisonError("evaluation pack must be an object")
    report = validate_serialized_contract(PACK_SCHEMA, canonical_json_bytes(payload))
    if not report.valid:
        raise RuntimeComparisonError(
            "evaluation pack failed schema validation: "
            + "; ".join(f"{item.location}: {item.message}" for item in report.findings)
        )
    if payload.get("pack_version") != PACK_VERSION:
        raise RuntimeComparisonError("unsupported evaluation pack version")
    cases = payload["cases"]
    if len({item["case_id"] for item in cases}) != len(cases):
        raise RuntimeComparisonError("evaluation pack case IDs must be unique")
    if any(item.get("synthetic") is not True for item in cases):
        raise RuntimeComparisonError("comparison pack contains non-synthetic input")
    for case in cases:
        _output_contract_criteria(case)
    grader = payload["grader"]
    if grader.get("id") != GRADER_ID or grader.get("version") != GRADER_VERSION:
        raise RuntimeComparisonError("unsupported comparison grader")
    pack = EvaluationPack(dict(payload), canonical_json_digest(payload))
    _validate_grader_vectors(pack)
    return pack


def _output_contract_criteria(case: Mapping[str, object]) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    """Validate the public contract against the deterministic grader criteria."""
    contract = case.get("output_contract")
    if contract is None:
        return None
    if not isinstance(contract, Mapping) or contract.get("version") != OUTPUT_CONTRACT_VERSION:
        raise RuntimeComparisonError("evaluation case output contract is unsupported")
    criteria = contract.get("criteria")
    expected = case.get("expected")
    expected_criteria = expected.get("criteria") if isinstance(expected, Mapping) else None
    if not isinstance(criteria, list) or not criteria:
        raise RuntimeComparisonError("evaluation case output contract has no criteria")
    if not isinstance(expected_criteria, Mapping):
        raise RuntimeComparisonError("evaluation case expected criteria are malformed")
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in criteria:
        if not isinstance(item, Mapping) or not isinstance(item.get("criterion_id"), str) or not isinstance(item.get("description"), str):
            raise RuntimeComparisonError("evaluation case output criterion is malformed")
        criterion_id = item["criterion_id"]
        if criterion_id in seen:
            raise RuntimeComparisonError("evaluation case output criterion IDs must be unique")
        seen.add(criterion_id)
        rows.append((criterion_id, item["description"]))
    if seen != set(expected_criteria):
        raise RuntimeComparisonError("evaluation case output criteria do not match expected grader criteria")
    return OUTPUT_CONTRACT_VERSION, tuple(rows)


def validate_grader(pack: EvaluationPack | Mapping[str, object]) -> dict[str, object]:
    """Validate known-good and known-bad vectors before candidate grading."""

    loaded = pack if isinstance(pack, EvaluationPack) else validate_evaluation_pack(pack)
    vectors = loaded.payload["grader_validation"]
    good = grade_output(loaded, vectors["known_good"]["case_id"], vectors["known_good"]["output"])
    bad = grade_output(loaded, vectors["known_bad"]["case_id"], vectors["known_bad"]["output"])
    if good["status"] != "pass" or bad["status"] != "fail":
        raise RuntimeComparisonError("grader validation vectors did not prove pass/fail behavior")
    return {"status": "pass", "grader_id": GRADER_ID, "grader_version": GRADER_VERSION, "known_good": good, "known_bad": bad}


def _comparison_prompt(case: Mapping[str, object]) -> str:
    """Append the frozen public output shape without exposing expected answers."""
    prompt = case.get("prompt")
    if not isinstance(prompt, str):
        raise RuntimeComparisonError("evaluation case prompt is malformed")
    if case.get("output_contract") is None:
        # Historical packs retain their original prompt and remain readable.
        return prompt
    validated = _output_contract_criteria(case)
    if validated is None:
        return prompt
    _version, criteria = validated
    return "\n".join(
        (
            prompt,
            "",
            "Output contract (r7-output-contract:1): return exactly one JSON object with no markdown.",
            'Top-level fields: "verdict", "criteria", "unsupported_claims".',
            '"verdict" must be exactly "pass" or "fail".',
            '"criteria" must contain exactly one object for each criterion below, with only "criterion_id", "status", and "evidence_ids".',
            '"status" must be exactly "supported", "unsupported", or "unknown"; "evidence_ids" must contain only IDs from supplied evidence.',
            '"unsupported_claims" must be an array of non-empty strings; do not add fields or prose outside the JSON object.',
            "Criteria:",
            *(f"- {criterion_id}: {description}" for criterion_id, description in criteria),
        )
    )


def grade_output(pack: EvaluationPack, case_id: str, output: str | Mapping[str, object]) -> dict[str, object]:
    case = next((item for item in pack.cases if item["case_id"] == case_id), None)
    if case is None:
        raise RuntimeComparisonError(f"unknown evaluation case: {case_id}")
    value: object = output
    if isinstance(output, str):
        try:
            value = json.loads(output)
        except json.JSONDecodeError:
            value = None
    expected = case["expected"]
    evidence_by_id = {item["id"]: item for item in case["evidence"]}
    checks: list[dict[str, object]] = []
    if not isinstance(value, dict) or set(value) != {"verdict", "criteria", "unsupported_claims"}:
        return {"case_id": case_id, "status": "fail", "failure_class": "invalid_output", "checks": [], "observed": None}
    criteria = value.get("criteria")
    valid_criteria = (
        isinstance(criteria, list)
        and all(
            isinstance(item, dict)
            and set(item) == {"criterion_id", "status", "evidence_ids"}
            and isinstance(item.get("criterion_id"), str)
            and item.get("status") in {"supported", "unsupported", "unknown"}
            and isinstance(item.get("evidence_ids"), list)
            and all(isinstance(ref, str) for ref in item["evidence_ids"])
            for item in criteria
        )
    )
    checks.append({"id": "output_shape", "passed": valid_criteria})
    if not valid_criteria:
        return {"case_id": case_id, "status": "fail", "failure_class": "invalid_output", "checks": checks, "observed": value}
    criterion_ids = [item["criterion_id"] for item in criteria]
    duplicate_criteria = len(criterion_ids) != len(set(criterion_ids))
    by_id = {item["criterion_id"]: item for item in criteria}
    checks.append({"id": "duplicate_criteria", "passed": not duplicate_criteria})
    checks.append({"id": "criterion_set", "passed": not duplicate_criteria and set(by_id) == set(expected["criteria"])})
    evidence = {ref for item in criteria for ref in item["evidence_ids"]}
    criterion_evidence_ok = True
    for item in criteria:
        criterion_id = item["criterion_id"]
        spec = expected.get("criterion_evidence", {}).get(criterion_id, {})
        allowed_ids = set(spec.get("ids", ())) if isinstance(spec, Mapping) else set()
        allowed_kinds = set(spec.get("kinds", ())) if isinstance(spec, Mapping) else set()
        cited = item["evidence_ids"]
        if len(cited) != len(set(cited)) or not set(cited).issubset(allowed_ids):
            criterion_evidence_ok = False
            continue
        if item["status"] in {"supported", "unsupported"} and not cited:
            criterion_evidence_ok = False
        if any(evidence_by_id.get(ref, {}).get("kind") not in allowed_kinds for ref in cited):
            criterion_evidence_ok = False
    checks.append({"id": "source_grounding", "passed": criterion_evidence_ok})
    checks.append({"id": "verdict", "passed": value.get("verdict") == expected["verdict"]})
    criteria_pass = all(by_id.get(key, {}).get("status") == status for key, status in expected["criteria"].items())
    checks.append({"id": "criterion_results", "passed": criteria_pass})
    unsupported = value.get("unsupported_claims")
    unsupported_ok = isinstance(unsupported, list) and all(isinstance(item, str) and item.strip() for item in unsupported)
    if expected.get("require_unsupported_claim"):
        unsupported_ok = unsupported_ok and bool(unsupported)
    else:
        unsupported_ok = unsupported_ok and not unsupported
    checks.append({"id": "unsupported_claims", "passed": unsupported_ok})
    return {
        "case_id": case_id,
        "status": "pass" if all(item["passed"] for item in checks) else "fail",
        "failure_class": None if all(item["passed"] for item in checks) else "grader_rejected",
        "checks": checks,
        "observed": value,
        "source_evidence_ids": sorted(evidence),
    }


def run_comparison(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    config: GigAIConfig,
    local_target: str,
    luna_target: str,
    operator_consent: Mapping[str, object],
    pack_path: Path | None = None,
    wait: bool = True,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    invocation_service: InvocationService | None = None,
    graph_selector: str | None = None,
    retry_failed: bool = False,
) -> dict[str, object]:
    """Run two independent attempts through the normal model invocation seam.

    ``invocation_service`` exists for injected transports in offline tests.  The
    default always uses :func:`run_model_invocation`; it never downloads a model,
    starts a daemon, or falls back between setup targets.
    """

    _validate_consent(operator_consent)
    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
        projection = read_index(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
        authority = _resolve_authority(resolved, projection, None)
        graph, selected_descriptor = resolve_selected_graph_authority(resolved, authority, graph_selector)
        pack, pack_ref = _load_authenticated_pack(
            resolved=resolved, authority=authority, graph=graph,
            selected_descriptor=selected_descriptor, pack_path=pack_path,
        )
    except (JournalError, WorkpadError, RunError, OSError, ValueError) as exc:
        raise RuntimeComparisonError(str(exc)) from exc
    active = projection.active_version
    if not isinstance(active, Mapping) or active.get("gig_id") != gig_id or type(active.get("active_version")) is not int:
        raise RuntimeComparisonError("comparison requires one authenticated active Gig version")
    bound_consent = _bind_consent(operator_consent, resolved, active, pack, graph)
    setups = _setups(local_target, luna_target, retry_failed=retry_failed)
    _validate_setup_targets(config, setups)
    comparison_id = _new_id(resolved.path, EntityPrefix.COMPARISON, uuid_factory, "comparisons")
    run_ids = tuple(_new_id(resolved.path, EntityPrefix.RUN, uuid_factory, "runs") for _ in setups)
    configured_identities = tuple(_configured_identity(config, setup) for setup in setups)
    intent = _comparison_intent(
        resolved=resolved, active=active, authority=authority, graph=graph,
        pack=pack, comparison_id=comparison_id, graph_selector=graph_selector,
        setups=setups, run_ids=run_ids, configured_identities=configured_identities,
        consent=bound_consent,
    )
    intent_bytes = canonical_json_bytes(intent)
    if not validate_serialized_contract(INTENT_SCHEMA, intent_bytes).valid:
        raise RuntimeComparisonError("comparison intent failed schema validation")
    record_transition(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        handoff_id=_new_id(resolved.path, EntityPrefix.HANDOFF, uuid_factory, "handoffs"),
        transition="comparison_started",
        body=f"Runtime comparison {comparison_id} intent sealed before attempt execution.",
        artifacts=(JournalArtifact(f"comparisons/{comparison_id}/intent.json", intent_bytes),),
        front_matter={"comparison_id": comparison_id, "outcome": "STARTED", "actor": {"kind": "operator", "id": "local-user"}},
    )
    attempts: list[dict[str, object]] = []
    for setup, run_id in zip(setups, run_ids, strict=True):
        attempts.append(_run_attempt(
            resolved=resolved,
            config=config,
            active=active,
            pack=pack,
            graph=graph,
            authority=authority,
            selected_descriptor=selected_descriptor,
            pack_ref=pack_ref,
            setup=setup,
            comparison_id=comparison_id,
            operator_consent=bound_consent,
            wait=wait,
            uuid_factory=uuid_factory,
            invocation_service=invocation_service,
            run_id=run_id,
            configured_identity=_configured_identity(config, setup),
            resume=False,
        ))
    comparison = _comparison_payload(
        resolved, active, pack, validate_grader(pack), comparison_id, setups, attempts,
        authority=authority, graph=graph, selected_descriptor=selected_descriptor,
        pack_ref=pack_ref, consent=bound_consent,
    )
    comparison_bytes = canonical_json_bytes(comparison)
    report = validate_serialized_contract(COMPARISON_SCHEMA, comparison_bytes)
    if not report.valid:
        raise RuntimeComparisonError("comparison result failed schema validation: " + "; ".join(f"{item.location}: {item.message}" for item in report.findings))
    if comparison["status"] == "interrupted":
        return comparison
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(resolved.path, EntityPrefix.HANDOFF, uuid_factory, "handoffs"),
        transition="comparison_published",
        body=f"Runtime comparison {comparison_id} published; no application state was changed.",
        artifacts=(JournalArtifact(f"comparisons/{comparison_id}.json", comparison_bytes),),
        front_matter={"comparison_id": comparison_id, "run_ids": [item["run_id"] for item in attempts], "outcome": comparison["status"].upper(), "actor": {"kind": "gigai", "id": "runtime-comparison"}},
    )
    return comparison


def resume_comparison(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    config: GigAIConfig,
    comparison_id: str,
    local_target: str,
    luna_target: str,
    operator_consent: Mapping[str, object],
    wait: bool = True,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    invocation_service: InvocationService | None = None,
    retry_failed: bool = False,
) -> dict[str, object]:
    """Resume one sealed intent using its original Runs and authority scope."""

    _validate_consent(operator_consent)
    try:
        validate_entity_id(comparison_id, expected_prefix=EntityPrefix.COMPARISON)
        resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
        try:
            existing = show_comparison(home_root=home_root, requested_target=requested_target, gig_id=gig_id, comparison_id=comparison_id)
        except RuntimeComparisonError:
            existing = None
        if existing is not None:
            raise RuntimeComparisonError("comparison is already published; terminal cases require explicit retry in a new comparison")
        intent, _intent_commit = _read_intent(resolved, comparison_id)
        if intent.get("gig_id") != resolved.gig_id or intent.get("project_id") != resolved.project_id:
            raise RuntimeComparisonError("comparison resume scope does not match the sealed intent")
        projection = read_index(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)
        authority = _resolve_authority(resolved, projection, int(intent["gig_version"]))
        graph, selected_descriptor = resolve_selected_graph_authority(resolved, authority, intent.get("graph_selector"))
        pack, pack_ref = _load_authenticated_pack(resolved=resolved, authority=authority, graph=graph, selected_descriptor=selected_descriptor, pack_path=None)
        if pack.digest != intent["pack"]["content_sha256"] or graph.get("graph_id") != intent["authority"]["graph_id"] or pack.payload.get("goal_id") != intent["authority"]["goal_id"]:
            raise RuntimeComparisonError("comparison resume authority differs from the sealed intent")
        active = projection.active_version
        if not isinstance(active, Mapping) or active.get("gig_id") != gig_id or active.get("active_version") != intent["gig_version"]:
            raise RuntimeComparisonError("comparison resume requires the sealed approved Gig version")
        bound_consent = _bind_consent(operator_consent, resolved, active, pack, graph)
        if bound_consent != intent["authority"]["consent"]:
            raise RuntimeComparisonError("comparison resume consent differs from the sealed intent")
        setups = _setups(local_target, luna_target, retry_failed=retry_failed)
        _validate_setup_targets(config, setups)
        plans = intent.get("attempts")
        if not isinstance(plans, list) or len(plans) != len(setups):
            raise RuntimeComparisonError("comparison intent attempt plan is malformed")
        attempts: list[dict[str, object]] = []
        for setup, plan in zip(setups, plans, strict=True):
            if not isinstance(plan, Mapping) or plan.get("setup_id") != setup.setup_id:
                raise RuntimeComparisonError("comparison resume setup scope differs from the sealed intent")
            configured = _configured_identity(config, setup)
            expected = next((item.get("configured_identity") for item in intent["setups"] if isinstance(item, Mapping) and item.get("setup_id") == setup.setup_id), None)
            expected_settings = next((item.get("settings") for item in intent["setups"] if isinstance(item, Mapping) and item.get("setup_id") == setup.setup_id), None)
            if configured != expected or dict(setup.settings or {}) != expected_settings:
                raise RuntimeComparisonError("comparison resume target or settings differ from the sealed intent")
            attempts.append(_run_attempt(
                resolved=resolved, config=config, active=active, pack=pack, graph=graph,
                authority=authority, selected_descriptor=selected_descriptor, pack_ref=pack_ref,
                setup=setup, comparison_id=comparison_id, operator_consent=bound_consent,
                wait=wait, uuid_factory=uuid_factory, invocation_service=invocation_service,
                run_id=str(plan["run_id"]), configured_identity=configured, resume=True,
            ))
    except (JournalError, WorkpadError, RunError, OSError, ValueError) as exc:
        if isinstance(exc, RuntimeComparisonError):
            raise
        raise RuntimeComparisonError(str(exc)) from exc
    comparison = _comparison_payload(
        resolved, active, pack, validate_grader(pack), comparison_id, setups, attempts,
        authority=authority, graph=graph, selected_descriptor=selected_descriptor,
        pack_ref=pack_ref, consent=bound_consent,
    )
    comparison_bytes = canonical_json_bytes(comparison)
    report = validate_serialized_contract(COMPARISON_SCHEMA, comparison_bytes)
    if not report.valid:
        raise RuntimeComparisonError("comparison result failed schema validation: " + "; ".join(f"{item.location}: {item.message}" for item in report.findings))
    if comparison["status"] == "interrupted":
        return comparison
    record_transition(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        handoff_id=_new_id(resolved.path, EntityPrefix.HANDOFF, uuid_factory, "handoffs"),
        transition="comparison_published",
        body=f"Runtime comparison {comparison_id} published after resume; no application state was changed.",
        artifacts=(JournalArtifact(f"comparisons/{comparison_id}.json", comparison_bytes),),
        front_matter={"comparison_id": comparison_id, "run_ids": [item["run_id"] for item in attempts], "outcome": comparison["status"].upper(), "actor": {"kind": "gigai", "id": "runtime-comparison"}},
    )
    return comparison


def show_comparison(*, home_root: Path, requested_target: Path | None, gig_id: str, comparison_id: str) -> dict[str, object]:
    try:
        validate_entity_id(comparison_id, expected_prefix=EntityPrefix.COMPARISON)
        resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
        data, comparison_head = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=f"comparisons/{comparison_id}.json")
        payload = parse_json_bytes(data)
    except (JournalError, WorkpadError, OSError, ValueError) as exc:
        raise RuntimeComparisonError(str(exc)) from exc
    if not isinstance(payload, dict) or payload.get("comparison_id") != comparison_id:
        raise RuntimeComparisonError("comparison artifact identity is invalid")
    try:
        _authenticate_comparison(resolved, payload, comparison_head=comparison_head)
    except (JournalError, OSError, ValueError) as exc:
        raise RuntimeComparisonError(str(exc)) from exc
    return payload


def comparison_status(*, home_root: Path, requested_target: Path | None, gig_id: str, comparison_id: str) -> dict[str, object]:
    """Return the authenticated comparison plus durable per-case checkpoints."""
    try:
        payload = show_comparison(home_root=home_root, requested_target=requested_target, gig_id=gig_id, comparison_id=comparison_id)
    except RuntimeComparisonError as show_error:
        resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
        try:
            read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=f"comparisons/{comparison_id}.json")
        except JournalError:
            pass
        else:
            raise show_error
        intent, _ = _read_intent(resolved, comparison_id)
        return _status_from_intent(resolved, intent)
    statuses: list[dict[str, object]] = []
    for attempt in payload.get("attempts", ()):
        if not isinstance(attempt, Mapping):
            continue
        statuses.append({"setup_id": attempt.get("setup_id"), "run_id": attempt.get("run_id"), "status": attempt.get("status"), "completed_cases": [case.get("case_id") for case in attempt.get("cases", ()) if isinstance(case, Mapping)], "failed_cases": [case.get("case_id") for case in attempt.get("cases", ()) if isinstance(case, Mapping) and case.get("grade", {}).get("status") != "pass"], "retry_counts": {case.get("case_id"): case.get("retries", 0) for case in attempt.get("cases", ()) if isinstance(case, Mapping)}})
    return {"comparison_id": payload.get("comparison_id"), "status": payload.get("status"), "attempts": statuses, "application_state_changed": payload.get("application_state_changed")}


def _status_from_intent(resolved: ResolvedWorkpad, intent: Mapping[str, object]) -> dict[str, object]:
    statuses: list[dict[str, object]] = []
    for plan in intent.get("attempts", ()):
        if not isinstance(plan, Mapping):
            continue
        run_id = plan.get("run_id")
        if not isinstance(run_id, str):
            continue
        try:
            data, _ = read_committed_artifact(
                workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
                path=f"runs/{run_id}/run-details.json", allow_replaced_run_details=True,
            )
            details = parse_json_bytes(data)
        except (JournalError, OSError, ValueError):
            details = {"status": "not_started", "goals": []}
        evidence = []
        goals = details.get("goals", ()) if isinstance(details, Mapping) else ()
        if isinstance(goals, list) and goals and isinstance(goals[0], Mapping):
            evidence = goals[0].get("evidence", [])
        rows = []
        for ref in evidence if isinstance(evidence, list) else ():
            if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                continue
            try:
                result_bytes, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=str(ref["path"]))
                result = parse_json_bytes(result_bytes)
            except (JournalError, OSError, ValueError):
                continue
            if isinstance(result, Mapping):
                rows.append(result)
        statuses.append({"setup_id": plan.get("setup_id"), "run_id": run_id, "status": details.get("status", "not_started") if isinstance(details, Mapping) else "not_started", "completed_cases": [row.get("case_id") for row in rows if row.get("terminal") is True], "failed_cases": [row.get("case_id") for row in rows if row.get("terminal") is True and row.get("grade", {}).get("status") != "pass"], "retry_counts": {row.get("case_id"): row.get("retries", 0) for row in rows}})
    interrupted = any(item["status"] == "interrupted" for item in statuses)
    return {"comparison_id": intent.get("comparison_id"), "status": "interrupted" if interrupted else "running", "attempts": statuses, "application_state_changed": False, "resumable": True}


def render_comparison_markdown(payload: Mapping[str, object]) -> str:
    lines = [f"# Runtime comparison {payload['comparison_id']}", "", f"Status: **{payload['status']}**", f"Pack: `{payload['pack']['pack_id']}@{payload['pack']['pack_version']}`", "", "| Setup | Run | Backend | Status | Passed cases |", "|---|---|---|---|---|"]
    for attempt in payload.get("attempts", []):
        cases = attempt.get("cases", []) if isinstance(attempt, Mapping) else []
        passed = sum(1 for case in cases if isinstance(case, Mapping) and case.get("grade", {}).get("status") == "pass")
        lines.append(f"| {attempt['setup_id']} | `{attempt['run_id']}` | {attempt['backend_identity']} | {attempt['status']} | {passed}/{len(cases)} |")
    lines.extend(["", "No winner was selected; this records setup differences rather than model-only quality.", ""])
    return "\n".join(lines)


def _load_authenticated_pack(
    *, resolved: ResolvedWorkpad, authority: Mapping[str, object],
    graph: Mapping[str, object], selected_descriptor: Mapping[str, object] | None,
    pack_path: Path | None,
) -> tuple[EvaluationPack, dict[str, object]]:
    """Load only the evaluation bytes declared by the approved Graph Set.

    The installed bundled pack remains useful for grader-only validation, but
    a comparison must use a pack declared by the selected Gig definition.  A
    caller path is accepted only as a byte-for-byte cross-check of that
    journaled source; it can never introduce a new pack.
    """
    commit = authority.get("commit")
    graph_set_ref = authority.get("graph_set_ref")
    if selected_descriptor is None or not isinstance(commit, str) or not isinstance(graph_set_ref, Mapping):
        raise RuntimeComparisonError("comparison requires an approved Graph Set with a Gig-owned evaluation pack")
    evaluation_ref = selected_descriptor.get("evaluation_contract")
    if not isinstance(evaluation_ref, Mapping) or not isinstance(evaluation_ref.get("path"), str):
        raise RuntimeComparisonError("selected Graph has no authenticated evaluation contract")
    try:
        contract_bytes, _ = read_committed_artifact(
            workpad=resolved.path, project_id=resolved.project_id,
            gig_id=resolved.gig_id, path=str(evaluation_ref["path"]), head=commit,
        )
    except (JournalError, OSError, ValueError) as exc:
        raise RuntimeComparisonError("selected evaluation contract is not journal-authenticated") from exc
    if digest_imported_bytes(contract_bytes) != evaluation_ref.get("content_sha256") or len(contract_bytes) != evaluation_ref.get("size_bytes"):
        raise RuntimeComparisonError("selected evaluation contract bytes do not match its authority reference")
    try:
        contract = parse_json_bytes(contract_bytes)
    except ValueError as exc:
        raise RuntimeComparisonError("selected evaluation contract is not JSON") from exc
    if not isinstance(contract, Mapping) or contract.get("gig_id") != resolved.gig_id:
        raise RuntimeComparisonError("evaluation contract belongs to another Gig")
    declared = contract.get("pack_ref")
    if not isinstance(declared, Mapping):
        raise RuntimeComparisonError("evaluation contract does not declare a Gig-owned evaluation pack")
    path = declared.get("path")
    digest = declared.get("content_sha256")
    size = declared.get("size_bytes")
    if not isinstance(path, str) or not isinstance(digest, str) or type(size) is not int:
        raise RuntimeComparisonError("evaluation pack reference is malformed")
    try:
        pack_bytes, _ = read_committed_artifact(
            workpad=resolved.path, project_id=resolved.project_id,
            gig_id=resolved.gig_id, path=path, head=commit,
        )
    except (JournalError, OSError, ValueError) as exc:
        raise RuntimeComparisonError("Gig-owned evaluation pack is not journal-authenticated") from exc
    if len(pack_bytes) != size or digest_imported_bytes(pack_bytes) != digest:
        raise RuntimeComparisonError("Gig-owned evaluation pack bytes do not match its reference")
    if pack_path is not None:
        try:
            supplied = pack_path.read_bytes()
        except OSError as exc:
            raise RuntimeComparisonError("comparison pack path is unavailable") from exc
        if supplied != pack_bytes:
            raise RuntimeComparisonError("caller pack differs from the selected Gig-owned pack")
    try:
        pack = validate_evaluation_pack(parse_json_bytes(pack_bytes))
    except (ValueError, RuntimeComparisonError) as exc:
        raise RuntimeComparisonError("Gig-owned evaluation pack is invalid") from exc
    if pack.payload.get("owner") != resolved.gig_id:
        raise RuntimeComparisonError("evaluation pack owner does not match the selected Gig")
    selected = pack.payload.get("selected_graph")
    if not isinstance(selected, Mapping) or selected.get("graph_id") != graph.get("graph_id") or selected.get("graph_version") != graph.get("graph_version"):
        raise RuntimeComparisonError("evaluation pack graph identity does not match approved authority")
    goal_ids = {item.get("goal_id") for item in graph.get("goals", ()) if isinstance(item, Mapping)}
    if pack.payload.get("goal_id") not in goal_ids:
        raise RuntimeComparisonError("evaluation pack goal is not an approved Goal")
    case_contracts = [case.get("output_contract") for case in pack.cases if case.get("output_contract") is not None]
    if case_contracts:
        if len(case_contracts) != len(pack.cases):
            raise RuntimeComparisonError("evaluation pack output contract is only partially declared")
        versions = {contract.get("version") for contract in case_contracts if isinstance(contract, Mapping)}
        if versions != {OUTPUT_CONTRACT_VERSION} or selected.get("output_contract_version") != OUTPUT_CONTRACT_VERSION:
            raise RuntimeComparisonError("evaluation pack output contract version is not coherent")
        binding = contract.get("runtime_comparison")
        approved_output_ref = selected_descriptor.get("output_contract") if selected_descriptor is not None else None
        if (
            not isinstance(binding, Mapping)
            or binding.get("output_contract_version") != OUTPUT_CONTRACT_VERSION
            or binding.get("output_contract_ref") != approved_output_ref
        ):
            raise RuntimeComparisonError("approved evaluation binding does not authenticate the runtime output contract")
    return pack, dict(declared)


def _bind_consent(consent: Mapping[str, object], resolved: ResolvedWorkpad, active: Mapping[str, object], pack: EvaluationPack, graph: Mapping[str, object]) -> dict[str, object]:
    bound = dict(consent)
    scope = dict(bound.get("scope", {})) if isinstance(bound.get("scope"), Mapping) else {}
    expected = {"project_id": resolved.project_id, "gig_id": resolved.gig_id, "gig_version": active["active_version"], "graph_id": graph.get("graph_id"), "goal_id": pack.payload["goal_id"], "pack_sha256": pack.digest}
    for key, value in expected.items():
        if key in scope and scope[key] != value:
            raise RuntimeComparisonError("operator consent scope does not match selected authority")
    bound["scope"] = expected
    return bound


def _authenticate_pack_binding(
    resolved: ResolvedWorkpad,
    payload: Mapping[str, object],
    authority: Mapping[str, object],
    pack: Mapping[str, object],
    *,
    authority_head: str,
) -> None:
    """Bind the approval raw source to the published canonical pack identity."""
    source_ref = pack.get("source_ref")
    if not isinstance(source_ref, Mapping) or not isinstance(source_ref.get("path"), str):
        raise RuntimeComparisonError("comparison pack source reference is missing")
    pack_bytes, _publisher = read_committed_artifact(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        path=str(source_ref["path"]),
        head=authority_head,
    )
    if len(pack_bytes) != source_ref.get("size_bytes") or digest_imported_bytes(pack_bytes) != source_ref.get("content_sha256"):
        raise RuntimeComparisonError("comparison raw pack source bytes changed")
    try:
        source_payload = parse_json_bytes(pack_bytes)
        source_pack = validate_evaluation_pack(source_payload)
    except (ValueError, RuntimeComparisonError) as exc:
        raise RuntimeComparisonError("comparison raw pack source is invalid") from exc
    if source_pack.digest != pack.get("content_sha256"):
        raise RuntimeComparisonError("comparison canonical pack digest is not authenticated")
    if source_pack.pack_id != pack.get("pack_id") or source_pack.payload.get("pack_version") != pack.get("pack_version"):
        raise RuntimeComparisonError("comparison pack identity is not authenticated")
    if pack.get("case_ids") != [item["case_id"] for item in source_pack.cases]:
        raise RuntimeComparisonError("comparison pack case identity is not authenticated")
    if source_pack.payload.get("owner") != payload.get("gig_id"):
        raise RuntimeComparisonError("comparison pack owner is not authenticated")
    if source_pack.payload.get("goal_id") != authority.get("goal_id") or source_pack.payload.get("selected_graph") != authority.get("selected_graph"):
        raise RuntimeComparisonError("comparison pack graph or goal binding is not authenticated")
    if authority.get("input_sha256") != source_pack.digest:
        raise RuntimeComparisonError("comparison authority canonical pack identity is not authenticated")
    consent = authority.get("consent")
    scope = consent.get("scope") if isinstance(consent, Mapping) else None
    if not isinstance(scope, Mapping) or scope.get("pack_sha256") != source_pack.digest:
        raise RuntimeComparisonError("comparison consent is not bound to canonical pack identity")


def _authenticate_comparison(resolved: ResolvedWorkpad, payload: Mapping[str, object], *, comparison_head: str) -> None:
    report = validate_serialized_contract(COMPARISON_SCHEMA, canonical_json_bytes(payload))
    if not report.valid:
        raise RuntimeComparisonError("comparison artifact failed schema validation")
    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        raise RuntimeComparisonError("comparison attempts are malformed")
    authority = payload.get("authority")
    pack = payload.get("pack")
    if not isinstance(authority, Mapping) or not isinstance(pack, Mapping):
        raise RuntimeComparisonError("comparison authority is missing")
    authority_head = authority.get("journal_commit")
    if not isinstance(authority_head, str) or not authority_head:
        raise RuntimeComparisonError("comparison approved authority head is missing")
    for ref in (authority.get("graph_set_ref"), authority.get("goal_graph_ref"), authority.get("selected_graph_ref")):
        if ref is None:
            continue
        if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
            raise RuntimeComparisonError("comparison authority reference is malformed")
        data, _publisher = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=str(ref["path"]), head=authority_head)
        if digest_imported_bytes(data) != ref.get("content_sha256") or len(data) != ref.get("size_bytes"):
            raise RuntimeComparisonError("comparison authority reference bytes changed")
    _authenticate_pack_binding(resolved, payload, authority, pack, authority_head=authority_head)
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or not isinstance(attempt.get("run_ref"), Mapping):
            raise RuntimeComparisonError("comparison attempt has no authenticated Run reference")
        run_ref = attempt["run_ref"]
        run_path = run_ref.get("path")
        if not isinstance(run_path, str):
            raise RuntimeComparisonError("comparison Run reference is malformed")
        run_bytes, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=run_path, head=comparison_head)
        if digest_imported_bytes(run_bytes) != run_ref.get("content_sha256") or len(run_bytes) != run_ref.get("size_bytes"):
            raise RuntimeComparisonError("comparison Run reference bytes changed")
        run = parse_json_bytes(run_bytes)
        if not isinstance(run, Mapping) or run.get("run_id") != attempt.get("run_id") or run.get("gig_id") != payload.get("gig_id"):
            raise RuntimeComparisonError("comparison Run reference identity is invalid")
        schema_name = "run-manifest-v2.schema.json" if run.get("schema_version") == "2.0" else "run-manifest.schema.json"
        if not validate_serialized_contract(schema_name, run_bytes).valid:
            raise RuntimeComparisonError("comparison Run manifest failed schema validation")
        cases = attempt.get("cases")
        if not isinstance(cases, list):
            raise RuntimeComparisonError("comparison case checkpoints are malformed")
        for case in cases:
            if not isinstance(case, Mapping) or not isinstance(case.get("result"), Mapping):
                raise RuntimeComparisonError("comparison case has no authenticated result reference")
            result_ref = case["result"]
            result_path = result_ref.get("path")
            if not isinstance(result_path, str):
                raise RuntimeComparisonError("comparison result reference is malformed")
            result_bytes, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=result_path, head=comparison_head)
            result = parse_json_bytes(result_bytes)
            if not isinstance(result, Mapping) or result.get("case_id") != case.get("case_id") or digest_imported_bytes(result_bytes) != result_ref.get("content_sha256") or len(result_bytes) != result_ref.get("size_bytes"):
                raise RuntimeComparisonError("comparison case result identity is invalid")


def _load_attempt_cases(resolved: ResolvedWorkpad, details: Mapping[str, object] | None) -> list[dict[str, object]]:
    if details is None:
        return []
    goals = details.get("goals", ())
    if not isinstance(goals, list) or not goals or not isinstance(goals[0], Mapping):
        return []
    evidence = goals[0].get("evidence", ())
    if not isinstance(evidence, list):
        return []
    rows: list[dict[str, object]] = []
    for ref in evidence:
        if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
            raise RuntimeComparisonError("comparison Run checkpoint evidence is malformed")
        try:
            data, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=str(ref["path"]))
            payload = parse_json_bytes(data)
        except (JournalError, OSError, ValueError) as exc:
            raise RuntimeComparisonError("comparison Run checkpoint evidence is unavailable") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("case_id"), str):
            raise RuntimeComparisonError("comparison Run checkpoint evidence is invalid")
        rows.append({
            "case_id": payload["case_id"], "case_version": payload.get("case_version"),
            "input_sha256": payload.get("input_sha256"), "result": dict(ref),
            "grade": payload.get("grade", {}), "wall_time_ms": payload.get("wall_time_ms", 0),
            "retries": payload.get("retries", 0), "invocation_id": payload.get("invocation_id"),
            "usage_unknown": payload.get("usage_unknown", True), "terminal": payload.get("terminal", True),
            "observed_identity": payload.get("observed_identity"),
        })
    return rows


def _identity_observation(
    *, record: Mapping[str, object] | None, target: object, setup: ComparisonSetup,
    injected: bool,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Return an explicit observation and a structured mismatch, if any."""
    if not isinstance(record, Mapping):
        return {"status": "unknown", "reason": "no_invocation_record"}, None
    local = record.get("local_identity")
    has_transport_identity = any(record.get(key) is not None for key in ("provider_family", "endpoint_identity", "adapter_identity"))
    if injected and not has_transport_identity and not isinstance(local, Mapping):
        return {"status": "unknown", "reason": "synthetic_injection"}, None
    expected_adapter = target.endpoint.adapter
    expected_endpoint = target.endpoint.name
    expected_model = target.target.model
    expected_digest = _canonical_model_digest(target.target.model_digest)
    observed_adapter = record.get("provider_family")
    observed_endpoint = record.get("endpoint_identity")
    observed_model = record.get("resolved_model")
    observed_digest = None
    local_observed = local.get("observed") if isinstance(local, Mapping) else None
    if isinstance(local_observed, Mapping):
        observed_digest = _canonical_model_digest(local_observed.get("model_digest"))
    if isinstance(local, Mapping) and observed_digest is None and local.get("configured_model_digest"):
        observed_digest = _canonical_model_digest(local.get("configured_model_digest"))
    observed = {"status": "observed", "adapter": observed_adapter, "endpoint": observed_endpoint, "model": observed_model, "model_digest": observed_digest}
    mismatch: dict[str, object] | None = None
    if observed_adapter != expected_adapter:
        mismatch = {"code": "observed_adapter_mismatch", "message": f"observed adapter {observed_adapter!r} does not match configured {expected_adapter!r}", "retryable": False, "invocation_id": record.get("invocation_id")}
    elif observed_endpoint != expected_endpoint:
        mismatch = {"code": "observed_endpoint_mismatch", "message": f"observed endpoint {observed_endpoint!r} does not match configured {expected_endpoint!r}", "retryable": False, "invocation_id": record.get("invocation_id")}
    elif observed_model != expected_model:
        mismatch = {"code": "observed_model_mismatch", "message": f"observed model {observed_model!r} does not match configured {expected_model!r}", "retryable": False, "invocation_id": record.get("invocation_id")}
    elif expected_adapter == "ollama_local" and (not isinstance(local_observed, Mapping) or local_observed.get("status") != "passed" or observed_digest != expected_digest):
        mismatch = {"code": "observed_digest_unavailable_or_mismatch", "message": "local Ollama invocation did not return the configured model digest", "retryable": False, "invocation_id": record.get("invocation_id")}
    return observed, mismatch


def _run_attempt(**kwargs: object) -> dict[str, object]:
    """Serialize one setup attempt; an OS lock closes the crash/replay gap."""
    resolved = kwargs["resolved"]
    run_id = kwargs["run_id"]
    if not isinstance(resolved, ResolvedWorkpad) or not isinstance(run_id, str):
        raise RuntimeComparisonError("comparison attempt scope is malformed")
    lock_path = resolved.path / ".git" / f"comparison-{run_id}.lock"
    try:
        lock = lock_path.open("a+", encoding="ascii")
    except OSError as exc:
        raise RuntimeComparisonError("comparison attempt lock is unavailable") from exc
    with lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeComparisonError("comparison attempt is already running") from exc
        try:
            return _run_attempt_locked(**kwargs)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _run_attempt_locked(*, resolved: ResolvedWorkpad, config: GigAIConfig, active: Mapping[str, object], pack: EvaluationPack, graph: Mapping[str, object], authority: Mapping[str, object], selected_descriptor: Mapping[str, object] | None, pack_ref: Mapping[str, object], setup: ComparisonSetup, comparison_id: str, operator_consent: Mapping[str, object], wait: bool, uuid_factory: Callable[[], uuid.UUID], invocation_service: InvocationService | None, run_id: str, configured_identity: Mapping[str, object], resume: bool) -> dict[str, object]:
    if not isinstance(run_id, str):
        raise RuntimeComparisonError("comparison attempt Run ID is malformed")
    goal_id = str(pack.payload["goal_id"])
    run_dir = resolved.path / "runs" / run_id
    existing_details: Mapping[str, object] | None = None
    if resume:
        try:
            existing_bytes, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=f"runs/{run_id}/run-details.json", allow_replaced_run_details=True)
            parsed_details = parse_json_bytes(existing_bytes)
        except (JournalError, OSError, ValueError) as exc:
            raise RuntimeComparisonError("comparison resume Run checkpoint is unavailable") from exc
        if not isinstance(parsed_details, Mapping) or parsed_details.get("run_id") != run_id:
            raise RuntimeComparisonError("comparison resume Run checkpoint identity is invalid")
        existing_details = parsed_details
        if not run_dir.is_dir():
            raise RuntimeComparisonError("comparison resume Run directory is unavailable")
    else:
        run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    graph = dict(graph)
    graph_bytes = canonical_json_bytes(graph)
    selected_goal = next((item for item in graph.get("goals", ()) if isinstance(item, Mapping) and item.get("goal_id") == goal_id), None)
    if not isinstance(selected_goal, Mapping) or not isinstance(selected_goal.get("contract"), Mapping) or not isinstance(selected_goal["contract"].get("path"), str):
        raise RuntimeComparisonError("evaluation pack Goal is missing its approved contract")
    try:
        contract_bytes, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=str(selected_goal["contract"]["path"]), head=str(authority["commit"]))
    except (JournalError, OSError, ValueError) as exc:
        raise RuntimeComparisonError("approved Goal contract is not journal-authenticated") from exc
    target = resolve_model_target(config, setup.target_name)
    readiness = resolve_target_readiness(config, setup.target_name)
    if readiness.readiness in {"unsupported", "unavailable"}:
        raise RuntimeComparisonError(f"setup {setup.setup_id} is not configured for invocation: {readiness.reason or readiness.readiness}")
    identity = {"setup_id": setup.setup_id, "target_name": setup.target_name, "adapter": target.endpoint.adapter, "endpoint": target.endpoint.name, "model": target.target.model, "configured_digest": target.target.model_digest, "configured_readiness": readiness.readiness, "configured_readiness_states": list(readiness.states), "settings": dict(setup.settings or {})}
    identity_bytes = canonical_json_bytes(identity)
    target_ref = _artifact_ref(f"runs/{run_id}/sealed/setup-identity.json", identity_bytes)
    graph_ref = _artifact_ref(f"runs/{run_id}/goal-graph.json", graph_bytes)
    contract_ref = _artifact_ref(f"runs/{run_id}/sealed/grader-contract.json", contract_bytes)
    consent_bytes = canonical_json_bytes(dict(operator_consent))
    consent_ref = _artifact_ref(f"runs/{run_id}/operator-consent.json", consent_bytes)
    goal_graph_sha = digest_imported_bytes(graph_bytes)
    now = str(existing_details.get("started_at", _now())) if existing_details is not None else _now()
    pack_bytes = canonical_json_bytes(pack.payload)
    sealed_pack_ref = _artifact_ref(f"runs/{run_id}/sealed/evaluation-pack.json", pack_bytes)
    manifest = {"schema_version": "1.0", "run_id": run_id, "gig_id": resolved.gig_id, "gig_version": active["active_version"], "authority": "run_invocation", "status": "sealed", "sealed_at": now, "invoked_by": {"kind": "operator", "id": "local-user"}, "invocation_argv": ["gigai", "comparison", "start", "--setup", setup.setup_id], "run_brief": _artifact_ref(f"runs/{run_id}/run-brief.md", f"# Runtime comparison {comparison_id}\n".encode()), "goal_graph": graph_ref, "goal_contracts": [{"goal_id": goal_id, "goal_version": 1, "contract": contract_ref}], "target_observation": target_ref, "profile": "comparison", "resolved_models": [{"role": "reviewer", "model_target": setup.target_name, "endpoint": target.endpoint.name, "configured_selector": setup.target_name, "resolved_identity": target.target.model, "resolution_source": "pinned", "compatibility_status": "COMPATIBLE"}], "resolved_tools": [], "sealed_sources": [target_ref, sealed_pack_ref, consent_ref], "effects": ["write_workpad"], "aggregate_budget": {"max_model_calls": len(pack.cases), "max_tool_calls": 0, "max_tokens": len(pack.cases) * target.target.max_output_tokens, "max_cost": None, "currency": None, "max_wall_time_ms": 900000, "max_parallel_goals": 1}, "input_canonical_sha256": pack.digest}
    details = _details(run_id, resolved.gig_id, int(active["active_version"]), goal_id, goal_graph_sha, now, status="preparing")
    manifest_bytes = canonical_json_bytes(manifest)
    if not validate_serialized_contract("run-manifest.schema.json", manifest_bytes).valid:
        raise RuntimeComparisonError("comparison Run manifest failed the standard Run contract")
    prepared = {f"runs/{run_id}/run-manifest.json": manifest_bytes, f"runs/{run_id}/run-brief.md": f"# Runtime comparison {comparison_id}\n".encode(), f"runs/{run_id}/goal-graph.json": graph_bytes, f"runs/{run_id}/sealed/grader-contract.json": contract_bytes, f"runs/{run_id}/sealed/setup-identity.json": identity_bytes, f"runs/{run_id}/sealed/evaluation-pack.json": pack_bytes, f"runs/{run_id}/operator-consent.json": consent_bytes, f"runs/{run_id}/run-details.json": canonical_json_bytes(details)}
    if existing_details is not None:
        try:
            committed_manifest, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=f"runs/{run_id}/run-manifest.json")
            committed_identity, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=f"runs/{run_id}/sealed/setup-identity.json")
        except (JournalError, OSError, ValueError) as exc:
            raise RuntimeComparisonError("comparison resume Run manifest is unavailable") from exc
        prepared[f"runs/{run_id}/run-manifest.json"] = committed_manifest
        prepared[f"runs/{run_id}/sealed/setup-identity.json"] = committed_identity
        target_ref = _artifact_ref(f"runs/{run_id}/sealed/setup-identity.json", committed_identity)
    if existing_details is None:
        record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=_new_id(resolved.path, EntityPrefix.HANDOFF, uuid_factory, "handoffs"), transition="run_started", body=f"Runtime comparison attempt {run_id} sealed for {setup.setup_id}.", artifacts=tuple(JournalArtifact(path, data) for path, data in prepared.items()), front_matter={"run_id": run_id, "gig_version": active["active_version"], "comparison_id": comparison_id, "outcome": "SEALED", "actor": {"kind": "operator", "id": "local-user"}})
    budget = InvocationBudget(max_model_calls=len(pack.cases), max_tokens=len(pack.cases) * target.target.max_output_tokens)
    case_rows = _load_attempt_cases(resolved, existing_details) if existing_details is not None else []
    interrupted = False
    for case in pack.cases:
        previous = next((item for item in case_rows if item.get("case_id") == case["case_id"]), None)
        if isinstance(previous, Mapping) and previous.get("terminal") is True and not (
            bool((setup.settings or {}).get("max_retries")) and previous.get("grade", {}).get("status") != "pass"
        ):
            continue
        case_started = time.monotonic()
        prompt = _comparison_prompt(case)
        case_input = canonical_json_bytes({"case_id": case["case_id"], "case_version": case["case_version"], "prompt": prompt, "input": case["input"], "evidence": case["evidence"]})
        reference_id = _new_id(resolved.path, EntityPrefix.REFERENCE, uuid_factory, "runs")
        reference = SelectedReference(reference_id, f"runs/{run_id}/cases/{case['case_id']}/input.json", case_input, digest_imported_bytes(case_input), "application/json")
        execution: ModelInvocationExecution | None = None
        invocation_error: dict[str, object] | None = None
        prior_errors: list[object] = []
        if isinstance(previous, Mapping) and isinstance(previous.get("result"), Mapping) and isinstance(previous["result"].get("path"), str):
            try:
                previous_bytes, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=str(previous["result"]["path"]))
                previous_payload = parse_json_bytes(previous_bytes)
                if isinstance(previous_payload, Mapping) and isinstance(previous_payload.get("errors"), list):
                    prior_errors = list(previous_payload["errors"])
            except (JournalError, OSError, ValueError) as exc:
                raise RuntimeComparisonError("comparison prior case checkpoint is unavailable") from exc
        invocation_errors: list[dict[str, object]] = []
        retries = 0
        max_retries = int((setup.settings or {}).get("max_retries", 1))
        while True:
            try:
                service = invocation_service or run_model_invocation
                execution = service(resolved=resolved, config=config, run_id=run_id, goal_id=goal_id, model_target=setup.target_name, role=setup.role, prompt=prompt, references=(reference,), selected_reference_ids=(reference_id,), policy=InvocationPolicy(allowed_reference_ids=frozenset({reference_id}), local_allowed=target.endpoint.adapter == "ollama_local", network_allowed=target.endpoint.adapter == "codex_cli", offline=False), budget=budget, uuid_factory=uuid_factory, commit_goal_transition=False)
            except KeyboardInterrupt:
                interrupted = True
                invocation_error = {"code": "comparison_interrupted", "message": "comparison invocation interrupted", "retryable": False, "invocation_id": None}
            except Exception as exc:
                invocation_error = {"code": "comparison_invocation_exception", "message": str(exc).replace("\n", " ")[:500], "retryable": False, "invocation_id": None}
            if invocation_error is not None:
                invocation_errors.append(invocation_error)
            record = execution.record if execution is not None else None
            record_error = dict(record["error"]) if isinstance(record, Mapping) and isinstance(record.get("error"), Mapping) else None
            if record_error is not None:
                invocation_error = record_error
                invocation_errors.append(record_error)
            retryable = bool(invocation_error and invocation_error.get("retryable"))
            if not retryable or retries >= max_retries or interrupted:
                break
            retries += 1
            execution = None
        output_text = execution.result.output_text if execution is not None and execution.result is not None else None
        record = execution.record if execution is not None else None
        observed_identity, identity_error = _identity_observation(record=record, target=target, setup=setup, injected=invocation_service is not None)
        if identity_error is not None:
            invocation_error = identity_error
            invocation_errors.append(identity_error)
        terminal = not interrupted
        grade = grade_output(pack, case["case_id"], output_text or "") if output_text is not None and identity_error is None else {"case_id": case["case_id"], "status": "fail", "failure_class": identity_error["code"] if identity_error else "invocation_failed", "checks": [], "observed": None}
        elapsed_ms = max(0, int((time.monotonic() - case_started) * 1000))
        case_payload = {"case_id": case["case_id"], "case_version": case["case_version"], "input_sha256": digest_imported_bytes(case_input), "output_text": output_text, "error": invocation_error, "errors": prior_errors + invocation_errors, "wall_time_ms": elapsed_ms, "retries": retries, "usage": record.get("usage") if isinstance(record, Mapping) else {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "usage_unknown": not isinstance(record, Mapping) or record.get("usage", {}).get("total_tokens") is None, "grade": grade, "invocation_id": record.get("invocation_id") if isinstance(record, Mapping) else None, "terminal": terminal, "observed_identity": observed_identity}
        case_bytes = canonical_json_bytes(case_payload)
        case_path = f"runs/{run_id}/cases/{case['case_id']}/result.json" if previous is None else f"runs/{run_id}/cases/{case['case_id']}/result-resume-{uuid_factory().hex}.json"
        artifacts = [JournalArtifact(f"runs/{run_id}/cases/{case['case_id']}/input.json", case_input), JournalArtifact(case_path, case_bytes)]
        if execution is not None:
            artifacts.extend(execution.artifacts)
        case_rows = [item for item in case_rows if item.get("case_id") != case["case_id"]]
        case_rows.append({"case_id": case["case_id"], "case_version": case["case_version"], "input_sha256": case_payload["input_sha256"], "result": _artifact_ref(case_path, case_bytes), "grade": grade, "wall_time_ms": elapsed_ms, "retries": retries, "invocation_id": case_payload["invocation_id"], "usage_unknown": case_payload["usage_unknown"], "terminal": terminal, "observed_identity": observed_identity})
        details = _details(run_id, resolved.gig_id, int(active["active_version"]), goal_id, goal_graph_sha, now, status="running", evidence=case_rows, target_before=target_ref)
        record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=_new_id(resolved.path, EntityPrefix.HANDOFF, uuid_factory, "handoffs"), transition="goal_completed" if grade["status"] == "pass" else "goal_failed", body=f"Runtime comparison case {case['case_id']} checkpointed for {setup.setup_id}.", artifacts=tuple(artifacts), front_matter={"run_id": run_id, "goal_id": goal_id, "comparison_id": comparison_id, "case_id": case["case_id"], "outcome": "COMPLETE" if grade["status"] == "pass" else "INTERRUPTED" if interrupted else "FAILED", "actor": {"kind": "gigai", "id": "runtime-comparison", "model_target": setup.target_name}})
        if interrupted:
            break
    status = "interrupted" if interrupted else "succeeded" if case_rows and all(item["terminal"] and item["grade"]["status"] == "pass" for item in case_rows) else "failed"
    details = _details(run_id, resolved.gig_id, int(active["active_version"]), goal_id, goal_graph_sha, now, status=status, evidence=case_rows, target_before=target_ref)
    details_bytes = canonical_json_bytes(details)
    record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=_new_id(resolved.path, EntityPrefix.HANDOFF, uuid_factory, "handoffs"), transition="run_succeeded" if status == "succeeded" else "run_interrupted" if status == "interrupted" else "run_failed", body=f"Runtime comparison attempt {run_id} ended {status}.", artifacts=(JournalArtifact(f"runs/{run_id}/run-details.json", details_bytes),), front_matter={"run_id": run_id, "comparison_id": comparison_id, "outcome": status.upper(), "actor": {"kind": "gigai", "id": "runtime-comparison"}})
    observed = [item.get("observed_identity") for item in case_rows if item.get("observed_identity") is not None]
    if observed:
        identity["observed_identity"] = observed[-1]
    return {"setup_id": setup.setup_id, "target_name": setup.target_name, "backend_identity": identity, "run_id": run_id, "status": status, "cases": case_rows, "run_ref": _artifact_ref(f"runs/{run_id}/run-manifest.json", prepared[f"runs/{run_id}/run-manifest.json"])}


def _comparison_payload(resolved: ResolvedWorkpad, active: Mapping[str, object], pack: EvaluationPack, grader_validation: Mapping[str, object], comparison_id: str, setups: tuple[ComparisonSetup, ...], attempts: list[dict[str, object]], *, authority: Mapping[str, object], graph: Mapping[str, object], selected_descriptor: Mapping[str, object] | None, pack_ref: Mapping[str, object], consent: Mapping[str, object]) -> dict[str, object]:
    success_count = sum(item["status"] == "succeeded" for item in attempts)
    status = "interrupted" if any(item["status"] == "interrupted" for item in attempts) else "succeeded" if success_count == len(attempts) else "partial" if success_count else "failed"
    goal_graph_ref = dict(selected_descriptor.get("goal_graph", {})) if isinstance(selected_descriptor, Mapping) else _artifact_ref("runs/authority/selected-goal-graph.json", canonical_json_bytes(graph))
    authority_payload = {"journal_commit": authority.get("commit"), "selected_graph": pack.payload["selected_graph"], "goal_id": pack.payload["goal_id"], "input_sha256": pack.digest, "goal_graph_ref": goal_graph_ref, "graph_set_ref": dict(authority.get("graph_set_ref", {})), "selected_graph_ref": goal_graph_ref if isinstance(selected_descriptor, Mapping) else None, "consent": dict(consent)}
    return {"schema_version": "1.0", "kind": "runtime_comparison", "comparison_version": 1, "comparison_id": comparison_id, "project_id": resolved.project_id, "gig_id": resolved.gig_id, "gig_version": active["active_version"], "authority": authority_payload, "pack": {"pack_id": pack.pack_id, "pack_version": pack.payload["pack_version"], "content_sha256": pack.digest, "case_ids": [item["case_id"] for item in pack.cases], "source_ref": dict(pack_ref)}, "grader": {"id": GRADER_ID, "version": GRADER_VERSION, "validation": grader_validation}, "setups": [{"setup_id": item.setup_id, "target_name": item.target_name, "expected_adapter": item.expected_adapter, "harness_version": item.harness_version, "prompt_version": item.prompt_version, "settings": dict(item.settings or {})} for item in setups], "attempts": attempts, "status": status, "selected_winner": None, "application_state_changed": False, "created_at": _now(), "updated_at": _now()}


def _validate_grader_vectors(pack: EvaluationPack) -> None:
    vectors = pack.payload.get("grader_validation")
    if not isinstance(vectors, Mapping) or not isinstance(vectors.get("known_good"), Mapping) or not isinstance(vectors.get("known_bad"), Mapping):
        raise RuntimeComparisonError("grader validation vectors are required")
    if vectors["known_good"].get("case_id") == vectors["known_bad"].get("case_id"):
        raise RuntimeComparisonError("grader validation vectors must use distinct cases")
    good = grade_output(pack, str(vectors["known_good"]["case_id"]), vectors["known_good"]["output"])
    bad = grade_output(pack, str(vectors["known_bad"]["case_id"]), vectors["known_bad"]["output"])
    if good["status"] != "pass" or bad["status"] != "fail":
        raise RuntimeComparisonError("grader known-good/known-bad vectors are invalid")


def _validate_consent(consent: Mapping[str, object]) -> None:
    if not isinstance(consent, Mapping) or set(consent) != {"action", "actor"} or consent.get("action") != "runtime_comparison" or consent.get("actor") != {"kind": "operator", "id": "local-user"}:
        raise RuntimeComparisonError("comparison requires direct operator consent")


def _artifact_ref(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}


def _new_id(workpad: Path, prefix: EntityPrefix, uuid_factory: Callable[[], uuid.UUID], directory: str) -> str:
    return generate_entity_id(prefix, is_persisted=lambda value: (workpad / directory / value).exists(), uuid_factory=uuid_factory)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _details(run_id: str, gig_id: str, version: int, goal_id: str, graph_digest: str, now: str, *, status: str, evidence: list[dict[str, object]] | None = None, target_before: dict[str, object] | None = None) -> dict[str, object]:
    usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}
    target = target_before or {"path": f"runs/{run_id}/sealed/setup-identity.json", "content_sha256": "sha256:" + "0" * 64, "media_type": "application/json", "size_bytes": 0}
    terminal = status in {"succeeded", "failed", "blocked", "cancelled", "interrupted"}
    goal_status = "complete" if status == "succeeded" else "failed" if terminal else status
    return {"schema_version": "1.0", "run_id": run_id, "gig_id": gig_id, "gig_version": version, "goal_graph_sha256": graph_digest, "status": status, "started_at": now, "finished_at": _now() if terminal else None, "goal_sets": {"pending": [], "ready": [], "active": [] if terminal else [goal_id], "complete": [goal_id] if status == "succeeded" else [], "failed": [goal_id] if status == "failed" else [], "blocked": [], "gated": [], "cancelled": []}, "goals": [{"goal_id": goal_id, "goal_version": 1, "executor": "runtime-comparison", "status": goal_status, "outcome": "COMPLETE" if status == "succeeded" else "FAILED" if terminal else None, "errors": [], "evidence": [item["result"] for item in evidence or []], "usage": usage, "started_at": now, "finished_at": _now() if terminal else None}], "critical_path": [goal_id], "realized_max_parallel_goals": 1, "execution_summary": "R6 synthetic runtime comparison attempt", "tool_errors": [], "model_errors": [], "aggregate_usage": usage, "remaining_budget": {"max_model_calls": 0, "max_tool_calls": 0, "max_tokens": 0, "max_cost": None, "currency": None, "max_wall_time_ms": 0, "max_parallel_goals": 1}, "target_before": target, "target_after": None, "completion_audit": {"status": "valid" if terminal else "draft", "path": None}, "terminal_handoff": None, "workpad_commit": None, "next_actions": []}


__all__ = ["ATTEMPT_SCHEMA", "COMPARISON_SCHEMA", "INTENT_SCHEMA", "EvaluationPack", "ComparisonSetup", "GRADER_ID", "GRADER_VERSION", "PACK_RESOURCE", "RuntimeComparisonError", "bundled_pack_bytes", "comparison_status", "grade_output", "load_evaluation_pack", "render_comparison_markdown", "resume_comparison", "run_comparison", "show_comparison", "validate_evaluation_pack", "validate_grader"]
