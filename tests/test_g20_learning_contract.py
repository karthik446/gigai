from __future__ import annotations

from copy import deepcopy

from gigai.canonical import canonical_json_bytes
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_12345678-1234-4234-9234-123456789abc"
RUN_ID = "run_12345678-1234-4234-9234-123456789abc"
GOAL_ID = "goal_12345678-1234-4234-9234-123456789abc"
LEARNING_ID = "learning_12345678-1234-4234-9234-123456789abc"
MANIFEST_ID = "improve_manifest_12345678-1234-4234-9234-123456789abc"
PROPOSAL_ID = "gp_12345678-1234-4234-9234-123456789abc"
DIGEST = "sha256:" + "a" * 64


def _artifact(path: str = "evidence/finding.json") -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": DIGEST,
        "media_type": "application/json",
        "size_bytes": 12,
    }


def _learning(provenance: str = "observed_outcome") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "record_version": 1,
        "learning_id": LEARNING_ID,
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "subject": {"kind": "run", "run_id": RUN_ID},
        "active_version": 1,
        "active_pointer_sha256": DIGEST,
        "source": {
            "kind": "finding",
            "source_id": "finding_12345678-1234-4234-9234-123456789abc",
            "artifact": _artifact(),
        },
        "provenance": provenance,
        "observed_at": "2026-08-10T12:00:00Z",
        "explanation": "The observation is descriptive only.",
        "created_at": "2026-08-10T12:00:00Z",
        "updated_at": "2026-08-10T12:00:00Z",
    }


def _split() -> dict[str, object]:
    return {"case_count": 8, "bar_pass": True, "metrics": {"recall": 1, "false_positive_rate": 0}}


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": MANIFEST_ID,
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "base_gig_version": 1,
        "parent_proposal_id": PROPOSAL_ID,
        "learning_record_ids": [LEARNING_ID],
        "change_request": "Tighten the review rubric threshold.",
        "changes": [
            {
                "target": "rubric",
                "path": "rubric.minimum_evidence",
                "operation": "replace",
                "before": _artifact("gig/rubric-before.json"),
                "after": _artifact("gig/rubric-after.json"),
            }
        ],
        "evidence_gate": {
            "result": "pass",
            "report": _artifact("learning/evidence-gate.json"),
            "supporting_record_ids": [LEARNING_ID],
            "checked_at": "2026-08-10T12:00:00Z",
        },
        "quality_gate": {
            "result": "pass",
            "report": _artifact("learning/quality-gate.json"),
            "evaluator_version": "s16-eval-g20-v1",
            "corpus_id": "corpus_g20_improvement_v1",
            "baseline_sha256": DIGEST,
            "candidate_sha256": DIGEST,
            "baseline": {"development": {"recall": 1, "false_positive_rate": 0}, "calibration": {"recall": 1, "false_positive_rate": 0}, "final_held_out_acceptance": {"recall": 1, "false_positive_rate": 0}},
            "candidate": {"development": {"recall": 1, "false_positive_rate": 0}, "calibration": {"recall": 1, "false_positive_rate": 0}, "final_held_out_acceptance": {"recall": 1, "false_positive_rate": 0}},
            "minimums": {"recall": 1},
            "maximums": {"false_positive_rate": 1},
            "case_counts": {"development": 4, "calibration": 2, "final_held_out_acceptance": 2},
            "development": _split(),
            "calibration": _split(),
            "final_holdout": _split(),
            "final_holdout_pass": True,
            "no_regression": True,
            "checked_at": "2026-08-10T12:00:00Z",
        },
        "created_at": "2026-08-10T12:00:00Z",
        "updated_at": "2026-08-10T12:00:00Z",
    }


def test_g20_adds_exactly_two_schema_resources() -> None:
    assert len(SCHEMA_NAMES) == 25
    assert "learning-record.schema.json" in SCHEMA_NAMES
    assert "improvement-manifest.schema.json" in SCHEMA_NAMES


def test_each_learning_provenance_value_has_a_valid_shape() -> None:
    for provenance in (
        "observed_outcome",
        "evaluator_judgment",
        "operator_feedback",
        "accepted_outcome",
    ):
        report = validate_serialized_contract(
            "learning-record.schema.json", canonical_json_bytes(_learning(provenance))
        )
        assert report.valid, report.as_dict()


def test_learning_record_rejects_wrong_source_identity_and_unknown_fields() -> None:
    wrong_source = _learning()
    wrong_source["source"] = {
        "kind": "finding",
        "source_id": "feedback_12345678-1234-4234-9234-123456789abc",
        "artifact": _artifact(),
    }
    assert not validate_serialized_contract(
        "learning-record.schema.json", canonical_json_bytes(wrong_source)
    ).valid

    unknown = _learning()
    unknown["authoritative_summary"] = "not allowed"
    assert not validate_serialized_contract(
        "learning-record.schema.json", canonical_json_bytes(unknown)
    ).valid


def test_improvement_manifest_accepts_typed_change_and_both_gate_reports() -> None:
    report = validate_serialized_contract(
        "improvement-manifest.schema.json", canonical_json_bytes(_manifest())
    )
    assert report.valid, report.as_dict()


def test_improvement_manifest_rejects_forbidden_path_but_preserves_gate_semantics() -> None:
    forbidden = _manifest()
    forbidden["changes"] = [
        {
            "target": "rubric",
            "path": "allowed_effects.write_target",
            "operation": "replace",
            "before": _artifact("before.json"),
            "after": _artifact("after.json"),
        }
    ]
    assert not validate_serialized_contract(
        "improvement-manifest.schema.json", canonical_json_bytes(forbidden)
    ).valid

    regressing = deepcopy(_manifest())
    regressing["quality_gate"]["no_regression"] = False
    # The schema carries the result; the semantic quality gate rejects it.
    assert validate_serialized_contract(
        "improvement-manifest.schema.json", canonical_json_bytes(regressing)
    ).valid
