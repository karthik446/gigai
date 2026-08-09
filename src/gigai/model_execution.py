"""Provider-backed invocation orchestration for G18.

This module is the only G18 seam that turns a configured model-port call into
durable evidence.  It performs the S18-05 boundary checks before invoking a
provider, never serializes a credential value, and commits a terminal
invocation record through the existing G06 journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from pathlib import Path
from typing import Any, Mapping
import uuid

from .adapters.factory import AdapterFactoryError, ModelAdapterBinding, resolve_model_adapter
from .adapters.port import InvocationResult, ModelInvocationCancelled, ModelInvocationError
from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
    validate_entity_id,
)
from .config import CredentialReference, GigAIConfig
from .credentials import (
    CredentialReferenceError,
    CredentialUnavailableError,
    reference_is_available,
    validate_reference,
)
from .journal import JournalArtifact, JournalEntry, record_transition
from .review import redact_text
from .validators import validate_model_invocation
from .workpad import ResolvedWorkpad


_REFERENCE_ID = re.compile(
    r"^ref_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
CHECK_ORDER_VERSION = "s18-05-1"
DEFAULT_REDACTION_POLICY = "g18-explicit-redaction-1"


class ModelExecutionError(RuntimeError):
    """A provider-backed invocation cannot be safely materialized."""

    code = "model_execution_failed"


@dataclass(frozen=True)
class SelectedReference:
    """Exact imported bytes made available to one invocation boundary."""

    reference_id: str
    path: str
    content: bytes
    content_sha256: str
    media_type: str = "text/plain"


@dataclass(frozen=True)
class InvocationPolicy:
    """Explicit effects allowed for one provider invocation."""

    allowed_reference_ids: frozenset[str] | None = None
    redaction_values: tuple[str, ...] = ()
    required_sensitive_values: tuple[str, ...] = ()
    network_allowed: bool = False
    offline: bool = False
    redaction_policy_version: str = DEFAULT_REDACTION_POLICY


@dataclass
class InvocationBudget:
    """Mutable per-Run model-call/token ledger with fail-closed reservation."""

    max_model_calls: int
    max_tokens: int
    model_calls: int = 0
    tokens: int = 0

    def __post_init__(self) -> None:
        if self.max_model_calls < 0 or self.max_tokens < 0:
            raise ModelExecutionError("budget limits must be non-negative")

    def reserve(self, requested_output_tokens: int) -> bool:
        if requested_output_tokens <= 0:
            raise ModelExecutionError("budget reservation requires positive output tokens")
        if self.model_calls >= self.max_model_calls:
            return False
        if self.tokens + requested_output_tokens > self.max_tokens:
            return False
        self.model_calls += 1
        self.tokens += requested_output_tokens
        return True


@dataclass(frozen=True)
class ModelInvocationExecution:
    """Result and journal evidence for one terminal invocation."""

    record: dict[str, object]
    result: InvocationResult | None
    journal_entry: JournalEntry


def run_model_invocation(
    *,
    resolved: ResolvedWorkpad,
    config: GigAIConfig,
    run_id: str,
    goal_id: str,
    model_target: str,
    role: str,
    prompt: str,
    references: tuple[SelectedReference, ...],
    selected_reference_ids: tuple[str, ...],
    policy: InvocationPolicy,
    budget: InvocationBudget | None = None,
    uuid_factory: Any = uuid.uuid4,
) -> ModelInvocationExecution:
    """Invoke one configured target after the explicit G18 boundary gate.

    ``references`` are caller-owned exact bytes.  Only IDs in
    ``selected_reference_ids`` are decoded and included in the provider input;
    all other bytes remain outside this function's adapter request.
    """

    validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
    validate_entity_id(goal_id, expected_prefix=EntityPrefix.GOAL)
    if not role or not prompt or "\0" in prompt:
        raise ModelExecutionError("invocation role and prompt must be non-empty and NUL-free")
    if not selected_reference_ids:
        raise ModelExecutionError("at least one explicitly selected reference is required")

    selected = _select_references(references, selected_reference_ids)
    binding = resolve_model_adapter(config, model_target)
    endpoint = binding.current.endpoint
    credential = _credential_for_endpoint(config, endpoint.credential)
    provider_family = endpoint.adapter
    remote = endpoint.adapter != "deterministic"

    selected_refs = tuple(_reference_ref(item) for item in selected)
    selection_reason: str | None = None
    redaction_result = "not_started"
    credential_lookup = "not_requested"
    network_result = "offline" if not remote else "not_checked"
    provider_input: str | None = None
    provider_input_sha256: str | None = None
    result: InvocationResult | None = None
    error: dict[str, object] | None = None
    outcome = "blocked"
    finish = "blocked"
    cancellation = "not_applicable"

    # S18-05 order: selection, credential shape, exact bytes, input,
    # redaction, network policy, then the adapter's transient credential read.
    if policy.allowed_reference_ids is not None and any(
        item not in policy.allowed_reference_ids for item in selected_reference_ids
    ):
        selection_reason = "reference_not_allowed"
    if selection_reason is None and remote:
        if credential is None:
            selection_reason = "credential_reference_missing"
            credential_lookup = "missing"
        else:
            try:
                validate_reference(credential)
                credential_lookup = "reference_valid"
            except CredentialReferenceError:
                selection_reason = "credential_reference_invalid"
                credential_lookup = "invalid"
    if selection_reason is None:
        try:
            provider_input = _build_provider_input(prompt, selected)
            provider_input_sha256 = digest_imported_bytes(provider_input.encode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            selection_reason = "selected_reference_not_text"
            error = _safe_error("selected_reference_not_text", exc)
    if selection_reason is None:
        redacted = redact_text(provider_input or "", policy.redaction_values)
        if any(value and value in redacted for value in policy.required_sensitive_values):
            redaction_result = "failed"
            selection_reason = "redaction_failed"
        else:
            redaction_result = "passed"
            provider_input = redacted
            provider_input_sha256 = digest_imported_bytes(redacted.encode("utf-8"))
    if selection_reason is None and remote:
        if policy.offline or not policy.network_allowed:
            network_result = "denied"
            selection_reason = "network_denied"
        else:
            network_result = "permitted"

    invocation_id = _new_id(EntityPrefix.INVOCATION, uuid_factory)
    request_payload = {
        "schema_version": "1.0",
        "role": role,
        "selected_reference_ids": list(selected_reference_ids),
        "input_sha256": provider_input_sha256,
        "blocked_reason": selection_reason,
    }
    request_bytes = canonical_json_bytes(request_payload)
    request_path = f"runs/{run_id}/model-invocations/{invocation_id}/request.json"
    request_ref = _artifact_ref(request_path, request_bytes, "application/json")

    if selection_reason is None:
        if remote:
            try:
                available = reference_is_available(credential) if credential else False
                if available is False:
                    credential_lookup = "missing"
                    raise CredentialUnavailableError("configured credential is unavailable")
                credential_lookup = "available" if available is True else "reference_valid"
            except (CredentialReferenceError, CredentialUnavailableError) as exc:
                selection_reason = "credential_unavailable"
                error = _safe_error(selection_reason, exc)
        if selection_reason is None and budget is not None:
            if not budget.reserve(binding.current.target.max_output_tokens):
                selection_reason = "budget_exhausted"
        if selection_reason is None:
            try:
                request = binding.request(role=role, prompt=provider_input or "")
                result = binding.port.invoke(request)
                outcome = "succeeded"
                finish = "completed"
            except CredentialUnavailableError as exc:
                selection_reason = "credential_unavailable"
                credential_lookup = "missing"
                error = _safe_error(selection_reason, exc)
            except ModelInvocationCancelled as exc:
                outcome, finish = "cancelled", "cancelled"
                cancellation = "acknowledged"
                error = _safe_error("model_invocation_cancelled", exc)
            except ModelInvocationError as exc:
                message = str(exc)
                lowered = message.lower()
                if "timed out" in lowered:
                    outcome, finish = "timeout", "timeout"
                    error = _safe_error("provider_timeout", exc)
                elif "unavailable" in lowered or "503" in lowered:
                    outcome, finish = "unavailable", "unavailable"
                    error = _safe_error("provider_unavailable", exc)
                else:
                    outcome, finish = "failed", "failed"
                    error = _safe_error("provider_invocation_failed", exc)
            except (AdapterFactoryError, ValueError) as exc:
                outcome, finish = "failed", "failed"
                error = _safe_error("provider_invocation_failed", exc)

    if selection_reason is not None:
        if selection_reason in {"credential_reference_missing", "credential_unavailable"}:
            outcome, finish = "unavailable", "unavailable"
        else:
            outcome, finish = "blocked", "blocked"
        error = error or _safe_error(selection_reason, RuntimeError(selection_reason))
    response_artifact: JournalArtifact | None = None
    response_artifact_ref: dict[str, object] | None = None
    if result is not None:
        response_payload = {
            "schema_version": "1.0",
            "output_text": result.output_text,
            "resolved_model": result.resolved_model,
            "raw_usage": _canonical_safe(result.raw_usage),
        }
        response_bytes = canonical_json_bytes(response_payload)
        response_path = f"runs/{run_id}/model-invocations/{invocation_id}/response.json"
        response_artifact = JournalArtifact(response_path, response_bytes)
        response_artifact_ref = _artifact_ref(response_path, response_bytes, "application/json")
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    usage = _usage(result)
    record = _invocation_record(
        run_id=run_id,
        goal_id=goal_id,
        invocation_id=invocation_id,
        role=role,
        provider_family=provider_family,
        configured_selector=model_target,
        endpoint_identity=endpoint.name,
        resolved_model=result.resolved_model if result else None,
        adapter_identity=f"{type(binding.port).__module__}.{type(binding.port).__qualname__}:1",
        selected_refs=selected_refs,
        request_ref=request_ref,
        request_sha256=digest_imported_bytes(request_bytes),
        outcome=outcome,
        finish=finish,
        cancellation=cancellation,
        error=error,
        usage=usage,
        redaction_result=redaction_result,
        redaction_policy_version=policy.redaction_policy_version,
        credential=credential,
        credential_lookup=credential_lookup,
        network_policy="offline" if not remote or policy.offline else "explicit_permission",
        network_result=network_result,
        response_artifact=response_artifact_ref,
        terminal_committed_at=now,
    )
    report = validate_model_invocation(record)
    if not report.valid:
        raise ModelExecutionError(
            "constructed model invocation failed validation: "
            + ", ".join(item.code for item in report.findings)
        )
    record_bytes = canonical_json_bytes(record)
    record_path = f"runs/{run_id}/model-invocations/{invocation_id}/record.json"
    artifacts = [
        JournalArtifact(request_path, request_bytes),
        JournalArtifact(record_path, record_bytes),
    ]
    if response_artifact is not None:
        artifacts.append(response_artifact)
    transition = "goal_completed" if outcome == "succeeded" else (
        "goal_blocked" if outcome == "blocked" else "goal_failed"
    )
    journal_entry = record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid_factory),
        transition=transition,
        body=f"G18 model invocation {invocation_id} terminalized as {outcome}.",
        artifacts=tuple(artifacts),
        front_matter={
            "run_id": run_id,
            "goal_id": goal_id,
            "goal_version": 1,
            "outcome": "COMPLETE" if outcome == "succeeded" else outcome.upper(),
            "actor": {"kind": "gigai", "id": "g18-model-execution", "model_target": model_target},
            "evidence": [request_ref, _artifact_ref(record_path, record_bytes, "application/json")],
            "usage": usage,
        },
    )
    return ModelInvocationExecution(record, result, journal_entry)


def _select_references(
    references: tuple[SelectedReference, ...], selected_ids: tuple[str, ...]
) -> tuple[SelectedReference, ...]:
    by_id = {item.reference_id: item for item in references}
    if len(by_id) != len(references):
        raise ModelExecutionError("reference IDs must be unique")
    for reference_id in selected_ids:
        if not _REFERENCE_ID.fullmatch(reference_id):
            raise ModelExecutionError("selected reference IDs must be canonical ref IDs")
        if reference_id not in by_id:
            raise ModelExecutionError("selected reference bytes are missing")
    return tuple(by_id[item] for item in selected_ids)


def _build_provider_input(prompt: str, references: tuple[SelectedReference, ...]) -> str:
    parts = [prompt]
    for reference in references:
        if digest_imported_bytes(reference.content) != reference.content_sha256:
            raise ValueError("reference digest mismatch")
        if Path(reference.path).is_absolute() or "\\" in reference.path or ".." in Path(reference.path).parts:
            raise ValueError("unsafe reference path")
        if len(reference.content) == 0:
            raise ValueError("reference bytes are empty")
        text = reference.content.decode("utf-8")
        parts.append(f"[{reference.reference_id}]\n{text}")
    return "\n".join(parts)


def _credential_for_endpoint(config: GigAIConfig, name: str | None) -> CredentialReference | None:
    if name is None:
        return None
    return next((item for item in config.credentials if item.name == name), None)


def _reference_ref(reference: SelectedReference) -> dict[str, object]:
    return {
        "reference_id": reference.reference_id,
        "content_sha256": reference.content_sha256,
    }


def _artifact_ref(path: str, content: bytes, media_type: str) -> dict[str, object]:
    digest = digest_imported_bytes(content)
    canonical_digest = canonical_json_digest(json.loads(content)) if media_type == "application/json" else None
    return {
        "path": path,
        "content_sha256": digest,
        "canonical_sha256": canonical_digest,
        "media_type": media_type,
        "size_bytes": len(content),
    }


def _usage(result: InvocationResult | None) -> dict[str, object]:
    if result is None:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cost": None,
            "currency": None,
            "cost_status": "not_applicable",
        }
    normalized = result.normalized_usage
    return {
        "input_tokens": normalized.input_tokens,
        "output_tokens": normalized.output_tokens,
        "total_tokens": normalized.total_tokens,
        "cost": None,
        "currency": None,
        "cost_status": result.cost_status,
    }


def _invocation_record(**values: object) -> dict[str, object]:
    record = {
        "schema_version": "1.0",
        "record_version": 1,
        "run_id": values["run_id"],
        "goal_id": values["goal_id"],
        "invocation_id": values["invocation_id"],
        "role": values["role"],
        "provider_family": values["provider_family"],
        "configured_selector": values["configured_selector"],
        "endpoint_identity": values["endpoint_identity"],
        "resolved_model": values["resolved_model"],
        "adapter_identity": values["adapter_identity"],
        "request": {
            "selected_references": list(values["selected_refs"]),
            "request_artifact": values["request_ref"],
            "request_sha256": values["request_sha256"],
        },
        "outcome": values["outcome"],
        "finish": values["finish"],
        "cancellation": values["cancellation"],
        "error": values["error"],
        "usage": values["usage"],
        "boundary": {
            "redaction": {"policy_version": values["redaction_policy_version"], "result": values["redaction_result"]},
            "credential": {"reference": _credential_metadata(values["credential"]), "lookup": values["credential_lookup"]},
            "network": {"policy": values["network_policy"], "result": values["network_result"]},
            "check_order_version": CHECK_ORDER_VERSION,
        },
        "extensions": [],
        "replay": {"stable_sha256": "sha256:" + "0" * 64, "variable_fields": ["invocation_id", "terminal_committed_at"]},
        "terminal_committed_at": values["terminal_committed_at"],
    }
    response_artifact = values.get("response_artifact")
    if response_artifact is not None:
        record["extensions"] = [{
            "namespace": "gigai.g18",
            "name": "response_artifact",
            "value_type": "object",
            "value": response_artifact,
        }]
    stable = {key: value for key, value in record.items() if key not in {"invocation_id", "terminal_committed_at", "replay"}}
    record["replay"] = {
        "stable_sha256": canonical_json_digest(stable),
        "variable_fields": ["invocation_id", "terminal_committed_at"],
    }
    return record


def _credential_metadata(credential: object) -> dict[str, str] | None:
    if not isinstance(credential, CredentialReference):
        return None
    return {"name": credential.name, "kind": credential.kind, "reference": credential.reference}


def _safe_error(code: str, exc: BaseException) -> dict[str, object]:
    message = str(exc).replace("\n", " ").strip() or code
    return {"code": code, "message": message[:500], "retryable": False, "invocation_id": None}


def _canonical_safe(value: object) -> object:
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float:
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_safe(item) for item in value]
    return str(value)


def _new_id(prefix: EntityPrefix, uuid_factory: Any) -> str:
    value = f"{prefix.value}_{uuid_factory()}"
    return validate_entity_id(value, expected_prefix=prefix)


__all__ = [
    "CHECK_ORDER_VERSION",
    "DEFAULT_REDACTION_POLICY",
    "InvocationPolicy",
    "InvocationBudget",
    "ModelExecutionError",
    "ModelInvocationExecution",
    "SelectedReference",
    "run_model_invocation",
]
