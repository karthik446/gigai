from __future__ import annotations

from copy import deepcopy
from gigai.canonical import canonical_json_bytes
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


DIGEST = "sha256:" + "a" * 64
PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_12345678-1234-4234-9234-123456789abc"
OCCURRENCE_ID = "occurrence_12345678-1234-4234-9234-123456789abc"
PRIOR_OCCURRENCE_ID = "occurrence_22345678-1234-4234-9234-123456789abc"
RUN_ID = "run_12345678-1234-4234-9234-123456789abc"
PRIOR_RUN_ID = "run_22345678-1234-4234-9234-123456789abc"
COMPARISON_ID = "comparison_12345678-1234-4234-9234-123456789abc"


def _artifact(path: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": DIGEST,
        "media_type": "application/json",
        "size_bytes": 10,
    }


def _snapshot() -> dict[str, object]:
    return {
        "bundle_id": "bundle_12345678-1234-4234-9234-123456789abc",
        "bundle_version": 1,
        "artifact": _artifact("manifests/review-bundles/daily.json"),
        "reference_set_sha256": DIGEST,
    }


def _occurrence() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "occurrence_version": 1,
        "occurrence_id": OCCURRENCE_ID,
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "gig_version": 1,
        "cadence": "daily",
        "occurrence_key": "2026-08-10",
        "trigger_actor": {"kind": "operator", "id": "test-operator"},
        "outcome_actor": None,
        "scheduled_for": "2026-08-10T12:00:00Z",
        "snapshot": _snapshot(),
        "prior_occurrence_id": None,
        "run_id": RUN_ID,
        "state": "run_terminal",
        "outcome": "succeeded",
        "comparison": None,
        "reason": None,
        "created_at": "2026-08-10T12:00:00Z",
        "updated_at": "2026-08-10T12:01:00Z",
    }


def _comparison() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "comparison_version": 1,
        "comparison_id": COMPARISON_ID,
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "current_occurrence_id": OCCURRENCE_ID,
        "prior_occurrence_id": PRIOR_OCCURRENCE_ID,
        "current_run_id": RUN_ID,
        "prior_run_id": PRIOR_RUN_ID,
        "current_gig_version": 1,
        "prior_gig_version": 1,
        "current_snapshot": _artifact("manifests/review-bundles/current.json"),
        "prior_snapshot": _artifact("manifests/review-bundles/prior.json"),
        "current_output": _artifact("runs/current/terminal.json"),
        "prior_output": _artifact("runs/prior/terminal.json"),
        "current_goal_graph": _artifact("runs/current/goal-graph.json"),
        "prior_goal_graph": _artifact("runs/prior/goal-graph.json"),
        "current_review_contracts": [_artifact("runs/current/review-contract.json")],
        "prior_review_contracts": [_artifact("runs/prior/review-contract.json")],
        "method_id": "canonical-json-diff",
        "method_version": "v1",
        "result": "unchanged",
        "reason": None,
        "evidence": [_artifact("comparisons/unchanged.json")],
        "selected_winner": None,
        "created_at": "2026-08-10T12:02:00Z",
        "updated_at": "2026-08-10T12:02:00Z",
    }


def test_g21_adds_exactly_two_packaged_resources() -> None:
    assert len(SCHEMA_NAMES) == 31
    assert "gig-occurrence.schema.json" in SCHEMA_NAMES
    assert "gig-comparison.schema.json" in SCHEMA_NAMES


def test_occurrence_schema_accepts_each_declared_cadence() -> None:
    for cadence in ("daily", "weekly", "monthly"):
        payload = _occurrence()
        payload["cadence"] = cadence
        assert validate_serialized_contract(
            "gig-occurrence.schema.json", canonical_json_bytes(payload)
        ).valid


def test_occurrence_schema_rejects_unknown_fields() -> None:
    payload = _occurrence()
    payload["scheduler"] = "not allowed"
    assert not validate_serialized_contract(
        "gig-occurrence.schema.json", canonical_json_bytes(payload)
    ).valid


def test_occurrence_schema_requires_refusal_reason_outcome_and_actor() -> None:
    payload = _occurrence()
    payload.update({"state": "missed", "outcome": "missed", "reason": "no trigger"})
    assert not validate_serialized_contract(
        "gig-occurrence.schema.json", canonical_json_bytes(payload)
    ).valid
    payload["outcome_actor"] = {"kind": "operator", "id": "test-operator"}
    assert validate_serialized_contract(
        "gig-occurrence.schema.json", canonical_json_bytes(payload)
    ).valid


def test_occurrence_schema_requires_comparison_when_compared() -> None:
    payload = _occurrence()
    payload["state"] = "compared"
    assert not validate_serialized_contract(
        "gig-occurrence.schema.json", canonical_json_bytes(payload)
    ).valid
    payload["comparison"] = _artifact("comparisons/result.json")
    assert validate_serialized_contract(
        "gig-occurrence.schema.json", canonical_json_bytes(payload)
    ).valid


def test_comparison_schema_enforces_null_winner() -> None:
    assert validate_serialized_contract(
        "gig-comparison.schema.json", canonical_json_bytes(_comparison())
    ).valid
    payload = deepcopy(_comparison())
    payload["selected_winner"] = "current"
    assert not validate_serialized_contract(
        "gig-comparison.schema.json", canonical_json_bytes(payload)
    ).valid


def test_comparison_schema_accepts_incomparable_with_explicit_reason() -> None:
    payload = _comparison()
    payload["result"] = "incomparable"
    payload["reason"] = "review contract identities differ"
    assert validate_serialized_contract(
        "gig-comparison.schema.json", canonical_json_bytes(payload)
    ).valid
