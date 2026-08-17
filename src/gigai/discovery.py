"""G27 subordinate discovery-manifest construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Mapping
import uuid

from .canonical import EntityPrefix, canonical_json_bytes, digest_imported_bytes
from .config import GigAIConfig
from .journal import JournalArtifact
from .model_discovery import resolve_target_readiness
from .proposal_interview import InterviewSession
from .validators import validate_serialized_contract


class DiscoveryError(ValueError):
    """A G27 discovery result cannot be serialized truthfully."""


_BUILTIN_QUESTION_IDS = frozenset({"scope", "references", "effect", "privacy", "capability"})
_CAPABILITY_IDS = (
    "local_reference_read",
    "model_invocation",
    "bounded_research",
    "proposal_construction",
    "approved_run_execution",
    "target_effect",
)


@dataclass(frozen=True)
class DiscoveryArtifacts:
    """A manifest and its subordinate content-addressed artifacts."""

    manifest: dict[str, object]
    artifacts: tuple[JournalArtifact, ...]


def build_discovery_artifacts(
    *,
    config: GigAIConfig,
    model_target: str,
    session: InterviewSession,
    reference_bytes: Mapping[str, bytes],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> DiscoveryArtifacts:
    """Build one schema-validated G27 manifest without granting authority."""

    direction_questions = tuple(
        question
        for question in session.questions
        if question.question_id not in _BUILTIN_QUESTION_IDS
        and not question.question_id.startswith("clarification-")
    )
    if len(direction_questions) > 5:
        raise DiscoveryError("discovery question round exceeds the five-question ceiling")
    references_by_id = {item.reference_id: item for item in session.references}
    for reference_id in session.selected_reference_ids:
        content = reference_bytes.get(reference_id)
        reference = references_by_id.get(reference_id)
        if content is None or reference is None or digest_imported_bytes(content) != reference.content_sha256:
            raise DiscoveryError("selected reference bytes do not match the interview digest")
    target = next((item for item in config.model_targets if item.name == model_target), None)
    if target is None:
        raise DiscoveryError(f"unknown discovery model target {model_target!r}")
    endpoint = next((item for item in config.endpoints if item.name == target.endpoint), None)
    if endpoint is None:
        raise DiscoveryError(f"discovery model target {model_target!r} has no endpoint")
    selection = {
        "target_name": target.name,
        "endpoint_name": endpoint.name,
        "model": target.model,
        "adapter": endpoint.adapter,
    }
    selection_digest = digest_imported_bytes(canonical_json_bytes(selection))
    readiness = resolve_target_readiness(config, model_target)
    model_status = readiness.readiness if readiness.readiness in {
        "detected", "configured", "verified", "usable", "unavailable", "unsupported"
    } else "unavailable"
    local_reference_status = "usable" if session.selected_reference_ids else "configured"
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    stable_payload = {
        "schema_version": "1.0",
        "kind": "stable_gig_definition",
        "gig_id": session.gig_id,
        "request_artifact": dict(session.request_artifact),
        "reference_ids": list(session.selected_reference_ids),
        "fields": ["goals", "constraints", "outputs", "stable_references"],
    }
    run_input_payload = {
        "schema_version": "1.0",
        "kind": "run_input_contract",
        "gig_id": session.gig_id,
        "fields": [],
    }
    provenance_payload = {
        "schema_version": "1.0",
        "kind": "question_generation",
        "model_selection": selection,
        "model_selection_digest": selection_digest,
        "question_ids": [item.question_id for item in direction_questions],
    }
    stable_bytes = canonical_json_bytes(stable_payload)
    run_input_bytes = canonical_json_bytes(run_input_payload)
    provenance_bytes = canonical_json_bytes(provenance_payload)
    stable_path = "review/discovery/stable-definition.json"
    run_input_path = "review/discovery/run-input-contract.json"
    provenance_path = "review/discovery/question-generation.json"

    def artifact(path: str, data: bytes) -> dict[str, object]:
        return {
            "path": path,
            "content_sha256": digest_imported_bytes(data),
            "media_type": "application/json",
            "size_bytes": len(data),
        }

    answers_by_id = {item.question_id: item for item in session.answers}
    questions = [
        {
            "question_id": item.question_id,
            "answer_type": item.answer_type,
            "required": item.required,
            "options": list(item.options),
            "depends_on": list(item.depends_on),
            "rationale": item.rationale,
            "provenance": item.provenance,
        }
        for item in direction_questions
    ]
    answers = [
        {
            "question_id": item.question_id,
            "answer_type": item.answer_type,
            "value": item.value,
            "answered_at": item.answered_at,
        }
        for question in direction_questions
        if (item := answers_by_id.get(question.question_id)) is not None
    ]
    sources = [
        {
            "source_id": f"source_{reference_id.removeprefix('ref_')}",
            "kind": "local_reference",
            "locator": f"reference://{reference_id}",
            "selected": True,
            "reference_id": reference_id,
        }
        for reference_id in session.selected_reference_ids
    ]
    capabilities = [
        {
            "capability_id": "local_reference_read",
            "status": local_reference_status,
            "status_source": "runtime_check",
            "network": "local_only",
            "effects": ["read_local"] if session.selected_reference_ids else [],
        },
        {
            "capability_id": "model_invocation",
            "status": model_status,
            "status_source": "runtime_check",
            "network": "local_only" if endpoint.adapter == "deterministic" else "configured_provider_only",
            "effects": [],
        },
        {
            "capability_id": "bounded_research",
            "status": "usable" if model_status == "usable" else "unavailable",
            "status_source": "accepted_contract",
            "network": "local_only" if endpoint.adapter == "deterministic" else "configured_provider_only",
            "effects": [],
        },
        {
            "capability_id": "proposal_construction",
            "status": "usable",
            "status_source": "accepted_contract",
            "network": "local_only",
            "effects": ["write_workpad"],
        },
        {
            "capability_id": "approved_run_execution",
            "status": "unsupported",
            "status_source": "accepted_contract",
            "network": "local_only",
            "effects": [],
        },
        {
            "capability_id": "target_effect",
            "status": "unsupported",
            "status_source": "accepted_contract",
            "network": "local_only",
            "effects": [],
        },
    ]
    manifest = {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": f"{EntityPrefix.DISCOVERY_MANIFEST}_{uuid_factory()}",
        "session_id": session.session_id,
        "project_id": session.project_id,
        "gig_id": session.gig_id,
        "request_kind": "create" if session.request_kind != "improve" else "improve",
        "parent_manifest_id": None,
        "capabilities": capabilities,
        "research_plan": {
            "status": "planned" if sources else "not_started",
            "sources": sources,
            "network": "local_only" if endpoint.adapter == "deterministic" else "configured_provider_only",
            "privacy": session.privacy_choice or "local_only",
            "credential_reference": endpoint.credential,
            "budget": {
                "max_model_calls": 2,
                "max_tool_calls": 0,
                "max_tokens": target.max_output_tokens,
                "max_cost": None,
                "currency": None,
                "max_wall_time_ms": 300000,
                "max_parallel_goals": 1,
            },
            "evidence_requirements": ["retain unresolved claims", "retain selected source identities"],
        },
        "question_rounds": [
            {
                "round": 1,
                "parent_round": None,
                "status": "sufficient" if not direction_questions else "answered" if answers else "pending",
                "model_selection_digest": selection_digest,
                "provenance_artifact": artifact(provenance_path, provenance_bytes),
                "questions": questions,
                "answers": answers,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            }
        ],
        "stable_definition": {
            "artifact": artifact(stable_path, stable_bytes),
            "fields": ["goals", "constraints", "outputs", "stable_references"],
        },
        "run_input_contract": {"artifact": artifact(run_input_path, run_input_bytes), "fields": []},
        "improve_context": None,
        "created_at": session.created_at or now,
        "updated_at": session.updated_at or now,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    report = validate_serialized_contract("gig-discovery-manifest.schema.json", manifest_bytes)
    if not report.valid:
        raise DiscoveryError("discovery manifest failed schema validation: " + ", ".join(item.code for item in report.findings))
    return DiscoveryArtifacts(
        manifest=manifest,
        artifacts=(
            JournalArtifact(stable_path, stable_bytes),
            JournalArtifact(run_input_path, run_input_bytes),
            JournalArtifact(provenance_path, provenance_bytes),
            JournalArtifact("manifests/gig-discovery-manifest.json", manifest_bytes),
        ),
    )


__all__ = ["DiscoveryArtifacts", "DiscoveryError", "build_discovery_artifacts"]
