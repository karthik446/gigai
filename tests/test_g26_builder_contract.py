from __future__ import annotations

import copy

from gigai.canonical import canonical_json_bytes
from gigai.validators import (
    SCHEMA_NAMES,
    validate_gig_builder_session,
    validate_proposal_draft_manifest,
    validate_serialized_contract,
)


SESSION = "session_00000000-0000-4000-8000-000000000001"
PROJECT = "project_00000000-0000-4000-8000-000000000002"
GIG = "gig_00000000-0000-4000-8000-000000000003"
SHA = "sha256:" + "1" * 64
NOW = "2026-08-13T00:00:00Z"


def _artifact(path: str = "draft/intent.txt") -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": SHA,
        "media_type": "text/plain",
        "size_bytes": 12,
    }


def _budget() -> dict[str, object]:
    return {
        "max_model_calls": 4,
        "max_tool_calls": 0,
        "max_tokens": 4000,
        "max_cost": None,
        "currency": None,
        "max_wall_time_ms": 300000,
        "max_parallel_goals": 1,
    }


def _selection() -> dict[str, object]:
    return {
        "target_name": "offline-default",
        "endpoint_name": "offline",
        "model": "fixture-v1",
        "adapter": "deterministic",
        "readiness": "usable",
        "selection_actor": {"kind": "operator", "id": "local-user"},
        "selection_digest": SHA,
    }


def _accounting() -> dict[str, object]:
    return {
        "model_calls": 0,
        "input_tokens": None,
        "output_tokens": None,
        "elapsed_ms": 0,
        "cost": None,
        "cost_currency": None,
    }


def _session(*, state: str = "clarify") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "record_version": 1,
        "session_id": SESSION,
        "project_id": PROJECT,
        "gig_id": GIG,
        "request_kind": "create",
        "state": state,
        "revision": 1,
        "parent_revision": None,
        "round": 1,
        "max_rounds": 8,
        "intent": {
            "text_artifact": _artifact(),
            "content_sha256": SHA,
            "answered_at": NOW,
            "actor": {"kind": "operator", "id": "local-user"},
        },
        "references": [],
        "questions": [
            {
                "question_id": "main_drive",
                "answer_type": "text",
                "required": True,
                "options": [],
                "depends_on": [],
                "rationale": "What should drive this Gig?",
                "provenance": "model://fixture-v1",
            }
        ],
        "answers": [],
        "model_selection": _selection(),
        "policy": {
            "network": "local_only",
            "credential_reference": None,
            "budget": _budget(),
            "cancellation": "operator_or_timeout",
        },
        "accounting": _accounting(),
        "draft": None,
        "terminal_reason": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _manifest(*, status: str = "completed") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": "draft_manifest_00000000-0000-4000-8000-000000000004",
        "session_id": SESSION,
        "project_id": PROJECT,
        "gig_id": GIG,
        "parent_manifest_id": None,
        "model_selection": {
            "target_name": "offline-default",
            "endpoint_name": "offline",
            "model": "fixture-v1",
            "adapter": "deterministic",
            "selection_digest": SHA,
        },
        "build": {
            "status": status,
            "mode": "deterministic_fixture",
            "started_at": NOW,
            "completed_at": NOW if status == "completed" else None,
            "accounting": _accounting(),
        },
        "proposal_artifact": _artifact("draft/proposal.json"),
        "research": {
            "citations": [
                {
                    "claim_id": "claim_intent",
                    "source_kind": "operator_statement",
                    "locator": "session://intent",
                    "source_sha256": SHA,
                    "verification": "verified",
                }
            ],
            "assumptions": [],
            "unresolved_questions": [],
        },
        "boundary": {
            "reference_ids": [],
            "network": "local_only",
            "credential_reference": None,
            "effects": ["write_workpad"],
        },
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_g26_adds_two_packaged_contract_resources() -> None:
    assert len(SCHEMA_NAMES) == 29
    assert "gig-builder-session.schema.json" in SCHEMA_NAMES
    assert "proposal-draft-manifest.schema.json" in SCHEMA_NAMES
    assert validate_serialized_contract(
        "gig-builder-session.schema.json", canonical_json_bytes(_session())
    ).valid
    assert validate_serialized_contract(
        "proposal-draft-manifest.schema.json", canonical_json_bytes(_manifest())
    ).valid


def test_g26_session_requires_terminal_reason_and_draft_at_the_right_states() -> None:
    blocked = _session(state="blocked")
    blocked["terminal_reason"] = "model_unavailable"
    assert validate_serialized_contract(
        "gig-builder-session.schema.json", canonical_json_bytes(blocked)
    ).valid

    missing_reason = copy.deepcopy(blocked)
    missing_reason["terminal_reason"] = None
    assert not validate_serialized_contract(
        "gig-builder-session.schema.json", canonical_json_bytes(missing_reason)
    ).valid

    ready = _session(state="proposal_draft_ready")
    ready["draft"] = _artifact("draft/proposal-manifest.json")
    assert validate_serialized_contract(
        "gig-builder-session.schema.json", canonical_json_bytes(ready)
    ).valid

    missing_draft = copy.deepcopy(ready)
    missing_draft["draft"] = None
    assert not validate_serialized_contract(
        "gig-builder-session.schema.json", canonical_json_bytes(missing_draft)
    ).valid


def test_g26_contract_rejects_unusable_build_target_and_forbidden_effects() -> None:
    unavailable = _session()
    unavailable["model_selection"]["readiness"] = "unavailable"  # type: ignore[index]
    assert validate_serialized_contract(
        "gig-builder-session.schema.json", canonical_json_bytes(unavailable)
    ).valid

    forbidden = _manifest()
    forbidden["boundary"]["effects"] = ["write_target"]  # type: ignore[index]
    assert not validate_serialized_contract(
        "proposal-draft-manifest.schema.json", canonical_json_bytes(forbidden)
    ).valid

    incomplete = _manifest(status="completed")
    incomplete["build"]["completed_at"] = None  # type: ignore[index]
    assert not validate_serialized_contract(
        "proposal-draft-manifest.schema.json", canonical_json_bytes(incomplete)
    ).valid


def test_g26_semantic_validators_reject_digest_and_boundary_drift() -> None:
    session = _session(state="researching")
    session["intent"]["content_sha256"] = "sha256:" + "2" * 64  # type: ignore[index]
    report = validate_gig_builder_session(session)
    assert not report.valid
    assert {item.code for item in report.findings} == {"intent_digest_mismatch"}

    manifest = _manifest()
    manifest["build"]["mode"] = "configured_model"  # type: ignore[index]
    manifest["boundary"]["network"] = "local_only"  # type: ignore[index]
    report = validate_proposal_draft_manifest(manifest)
    assert not report.valid
    assert "configured_model_network_boundary_invalid" in {
        item.code for item in report.findings
    }
