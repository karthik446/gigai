from __future__ import annotations

from copy import deepcopy

from gigai.canonical import canonical_json_bytes
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


SESSION = "session_12345678-1234-4234-9234-123456789abc"
PROJECT = "project_12345678-1234-4234-9234-123456789abc"
GIG = "gig_12345678-1234-4234-9234-123456789abc"
LEARNING = "learning_12345678-1234-4234-9234-123456789abc"
DIGEST = "sha256:" + "a" * 64
NOW = "2026-08-14T00:00:00Z"


def _artifact(path: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": DIGEST,
        "media_type": "application/json",
        "size_bytes": 128,
    }


def _budget() -> dict[str, object]:
    return {
        "max_model_calls": 2,
        "max_tool_calls": 2,
        "max_tokens": 1000,
        "max_cost": None,
        "currency": None,
        "max_wall_time_ms": 60000,
        "max_parallel_goals": 1,
    }


def _question(question_id: str = "desired_output") -> dict[str, object]:
    return {
        "question_id": question_id,
        "answer_type": "choice",
        "required": True,
        "options": ["draft", "final"],
        "depends_on": [],
        "rationale": "This answer changes the Gig definition.",
        "provenance": "model://offline-default/fixture-v1",
    }


def _round(*, questions: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "round": 1,
        "parent_round": None,
        "status": "sufficient" if not questions else "answered",
        "model_selection_digest": DIGEST,
        "provenance_artifact": _artifact("discovery/question-generation.json"),
        "questions": questions or [],
        "answers": [],
        "created_at": NOW,
        "updated_at": NOW,
    }


def _manifest(*, request_kind: str = "create") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": "discovery_manifest_12345678-1234-4234-9234-123456789abc",
        "session_id": SESSION,
        "project_id": PROJECT,
        "gig_id": GIG,
        "request_kind": request_kind,
        "parent_manifest_id": None,
        "capabilities": [
            {
                "capability_id": "model_invocation",
                "status": "usable",
                "status_source": "runtime_check",
                "network": "local_only",
                "effects": [],
            }
        ],
        "research_plan": {
            "status": "not_started",
            "sources": [],
            "network": "local_only",
            "privacy": "local_only",
            "credential_reference": None,
            "budget": _budget(),
            "evidence_requirements": ["retain unresolved claims"],
        },
        "question_rounds": [_round(questions=[_question()])],
        "stable_definition": {
            "artifact": _artifact("discovery/stable-definition.json"),
            "fields": ["goals", "outputs"],
        },
        "run_input_contract": {
            "artifact": _artifact("discovery/run-input-contract.json"),
            "fields": [
                {
                    "field_id": "job_url",
                    "value_type": "url",
                    "required": True,
                    "source": "operator",
                }
            ],
        },
        "improve_context": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_g27_adds_one_schema_resource_and_accepts_create_manifest() -> None:
    assert len(SCHEMA_NAMES) == 31
    assert "gig-discovery-manifest.schema.json" in SCHEMA_NAMES
    report = validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(_manifest())
    )
    assert report.valid, report.as_dict()


def test_improve_context_is_conditionally_required() -> None:
    missing = _manifest(request_kind="improve")
    report = validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(missing)
    )
    assert not report.valid

    improved = _manifest(request_kind="improve")
    improved["improve_context"] = {
        "learning_record_ids": [LEARNING],
        "active_version": 1,
        "summary_artifact": _artifact("discovery/improve-summary.json"),
        "max_source_bytes": 4096,
        "omitted_content_policy": "raw_unselected_and_hidden_context_excluded",
    }
    report = validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(improved)
    )
    assert report.valid, report.as_dict()

    create_with_context = deepcopy(_manifest())
    create_with_context["improve_context"] = improved["improve_context"]
    assert not validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(create_with_context)
    ).valid


def test_question_round_allows_zero_but_rejects_six_questions() -> None:
    empty = _manifest()
    empty["question_rounds"] = [_round()]
    assert validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(empty)
    ).valid

    too_many = _manifest()
    too_many["question_rounds"] = [
        _round(questions=[_question(f"question_{index}") for index in range(6)])
    ]
    assert not validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(too_many)
    ).valid


def test_research_source_kind_controls_reference_shape() -> None:
    local_missing_reference = _manifest()
    local_missing_reference["research_plan"]["sources"] = [  # type: ignore[index]
        {
            "source_id": "source_resume",
            "kind": "local_reference",
            "locator": "reference://resume",
            "selected": True,
            "reference_id": None,
        }
    ]
    assert not validate_serialized_contract(
        "gig-discovery-manifest.schema.json",
        canonical_json_bytes(local_missing_reference),
    ).valid

    external_with_reference = _manifest()
    external_with_reference["research_plan"]["sources"] = [  # type: ignore[index]
        {
            "source_id": "source_job_posting",
            "kind": "external_source",
            "locator": "https://example.test/job",
            "selected": True,
            "reference_id": "ref_12345678-1234-4234-9234-123456789abc",
        }
    ]
    assert not validate_serialized_contract(
        "gig-discovery-manifest.schema.json",
        canonical_json_bytes(external_with_reference),
    ).valid


def test_manifest_rejects_unknown_fields_and_forbidden_capability_status_shape() -> None:
    unknown = _manifest()
    unknown["proposal_id"] = None
    assert not validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(unknown)
    ).valid

    unsupported = _manifest()
    unsupported["capabilities"][0]["status"] = "granted"  # type: ignore[index]
    assert not validate_serialized_contract(
        "gig-discovery-manifest.schema.json", canonical_json_bytes(unsupported)
    ).valid
