from __future__ import annotations

import copy
from pathlib import Path

from gigai.canonical import (
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
)
from gigai.review import (
    ReviewBundleError,
    materialize_review_bundle,
    merge_findings,
    validate_adjudication,
    validate_review_bundle,
    validate_review_contract,
    validate_feedback,
    validate_finding,
    validate_finding_transition,
    validate_trace,
    render_report,
)
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


IDS = {
    "bundle": "bundle_00000000-0000-4000-8000-000000000001",
    "article": "ref_00000000-0000-4000-8000-000000000002",
    "repo": "ref_00000000-0000-4000-8000-000000000003",
    "csv": "ref_00000000-0000-4000-8000-000000000004",
    "contract": "contract_00000000-0000-4000-8000-000000000005",
}


def _bundle() -> tuple[dict[str, object], dict[str, bytes]]:
    payloads = {
        "references/objects/article.txt": b"A source article.\n",
        "references/objects/repository.txt": b"git snapshot bytes\n",
        "references/objects/data.csv": b"month,total\n2026-08,42\n",
    }
    references = []
    for reference_id, role, kind, path in (
        (IDS["article"], "primary-source", "article", "references/objects/article.txt"),
        (
            IDS["repo"],
            "subject-repository",
            "repository_snapshot",
            "references/objects/repository.txt",
        ),
        (IDS["csv"], "comparison-data", "csv", "references/objects/data.csv"),
    ):
        payload = payloads[path]
        references.append(
            {
                "reference_id": reference_id,
                "role": role,
                "kind": kind,
                "path": path,
                "media_type": "text/plain" if kind != "csv" else "text/csv",
                "content_sha256": digest_imported_bytes(payload),
                "size_bytes": len(payload),
                "provenance": {
                    "source_kind": "generated",
                    "locator": f"fixture://{role}",
                    "acquired_at": "2026-08-07T00:00:00Z",
                    "acquisition_method": "checked-in-fixture",
                    "source_revision": None,
                },
                "sensitivity": "public",
                "redaction_status": "not_required",
            }
        )
    return (
        {
            "schema_version": "1.0",
            "bundle_id": IDS["bundle"],
            "bundle_version": 1,
            "created_at": "2026-08-07T00:00:00Z",
            "created_by": {"kind": "gigai", "id": "g15-fixture", "model_target": None},
            "name": "g15-fixture-bundle",
            "question": "What changed and what evidence supports it?",
            "references": references,
            "tool_requirements": None,
            "redaction_policy": {
                "mode": "local_only",
                "allowed_reference_ids": [IDS["article"], IDS["repo"], IDS["csv"]],
                "policy_version": "g15-redaction-1",
                "detector_version": None,
            },
        },
        payloads,
    )


def _contract() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "contract_id": IDS["contract"],
        "contract_version": 1,
        "created_at": "2026-08-07T00:00:00Z",
        "created_by": {"kind": "gigai", "id": "g15-fixture", "model_target": None},
        "name": "g15-review-contract",
        "question": "Does the evidence support the stated conclusion?",
        "reference_roles": ["primary-source", "subject-repository", "comparison-data"],
        "criteria": [
            {
                "criterion_id": "criterion_evidence",
                "description": "Every conclusion has a supporting citation.",
                "severity": "high",
                "required_evidence": ["citation"],
                "citation_requirement": "required",
                "evaluator_ids": ["evaluator_citations"],
            }
        ],
        "severity_model": {
            "levels": ["info", "low", "medium", "high", "critical"],
            "ordering": ["info", "low", "medium", "high", "critical"],
        },
        "evidence_requirements": ["citation"],
        "output_shape": {
            "machine_media_type": "application/json",
            "human_media_type": "text/markdown",
            "required_sections": ["findings", "decision"],
        },
        "clarification_policy": "block_run",
        "cycle_cap": 1,
        "escalation_policy": "operator",
        "allowed_effects": ["read_target", "write_workpad"],
        "evaluator_plan": [
            {
                "evaluator_id": "evaluator_citations",
                "evaluator_version": "fixture-1",
                "stage": "deterministic",
            }
        ],
        "redaction_policy": {
            "mode": "local_only",
            "policy_version": "g15-redaction-1",
            "detector_version": None,
        },
    }


def test_g15_additive_schema_inventory_is_exact() -> None:
    assert len(SCHEMA_NAMES) == 31
    assert {
        "review-bundle.schema.json",
        "review-contract.schema.json",
        "finding.schema.json",
        "feedback.schema.json",
        "adjudication.schema.json",
        "trace.schema.json",
        "report.schema.json",
        "model-exchange.schema.json",
        "model-invocation.schema.json",
    }.issubset(SCHEMA_NAMES)


def test_bundle_materializes_and_replays_exact_bytes(tmp_path: Path) -> None:
    bundle, objects = _bundle()
    manifest = materialize_review_bundle(tmp_path, bundle, objects)

    assert validate_review_bundle(tmp_path, manifest).valid
    assert (tmp_path / "manifests/review-bundle.json").read_bytes() == manifest
    assert parse_json_bytes(manifest)["bundle_id"] == IDS["bundle"]

    tampered = tmp_path / "references/objects/article.txt"
    tampered.write_bytes(b"tampered\n")
    report = validate_review_bundle(tmp_path, manifest)
    assert "reference_digest_mismatch" in {item.code for item in report.findings}


def test_bundle_refuses_invalid_digest_before_writing(tmp_path: Path) -> None:
    bundle, objects = _bundle()
    invalid = copy.deepcopy(bundle)
    invalid["references"][0]["content_sha256"] = "sha256:" + "0" * 64

    try:
        materialize_review_bundle(tmp_path, invalid, objects)
    except ReviewBundleError as exc:
        assert "reference_digest_mismatch" in str(exc)
    else:
        raise AssertionError("invalid bundle unexpectedly materialized")
    assert not (tmp_path / "manifests/review-bundle.json").exists()


def test_bundle_rejects_symlinked_reference_bytes(tmp_path: Path) -> None:
    bundle, objects = _bundle()
    materialize_review_bundle(tmp_path, bundle, objects)
    target = tmp_path / "references/objects/article.txt"
    target.unlink()
    outside = tmp_path.parent / "g15-outside-reference.txt"
    outside.write_bytes(objects["references/objects/article.txt"])
    target.symlink_to(outside)
    report = validate_review_bundle(
        tmp_path,
        (tmp_path / "manifests/review-bundle.json").read_bytes(),
    )
    assert "unsafe_reference_path" in {item.code for item in report.findings}


def test_review_contract_validates_and_rejects_model_stage() -> None:
    contract = canonical_json_bytes(_contract())
    assert validate_review_contract(contract).valid
    assert validate_serialized_contract("review-contract.schema.json", contract).valid

    model_contract = _contract()
    model_contract["evaluator_plan"][0]["stage"] = "model"
    report = validate_review_contract(canonical_json_bytes(model_contract))
    assert "unsupported_model_evaluator" in {item.code for item in report.findings}


def test_review_contract_rejects_unknown_evaluator() -> None:
    contract = _contract()
    contract["criteria"][0]["evaluator_ids"] = ["evaluator_missing"]
    report = validate_review_contract(canonical_json_bytes(contract))
    assert "unknown_evaluator_id" in {item.code for item in report.findings}


def _finding(
    reference_id: str, evaluator_id: str, finding_id: str
) -> dict[str, object]:
    bundle, _objects = _bundle()
    reference = next(
        item for item in bundle["references"] if item["reference_id"] == reference_id
    )
    evaluator = {
        "evaluator_id": evaluator_id,
        "evaluator_version": "fixture-1",
        "stage": "deterministic",
    }
    return {
        "schema_version": "1.0",
        "finding_id": finding_id,
        "finding_version": 1,
        "criterion_id": "criterion_evidence",
        "status": "open",
        "severity": "high",
        "title": "Missing citation",
        "description": "The conclusion has no supporting citation.",
        "evidence": [
            {
                "reference_id": reference_id,
                "content_sha256": reference["content_sha256"],
                "locator": "bytes:0-4",
                "quote": "A source",
            }
        ],
        "evaluator": evaluator,
        "source_evaluators": [evaluator],
        "trace_id": "trace_00000000-0000-4000-8000-000000000006",
        "confidence": "0.90",
        "disagreement": {"present": False, "peer_finding_ids": [], "summary": None},
        "created_at": "2026-08-07T00:00:00Z",
    }


def test_findings_require_real_bundle_evidence_and_merge_provenance(
    tmp_path: Path,
) -> None:
    bundle, objects = _bundle()
    materialize_review_bundle(tmp_path, bundle, objects)
    first = _finding(
        IDS["article"],
        "evaluator_citations",
        "finding_00000000-0000-4000-8000-000000000007",
    )
    second = copy.deepcopy(first)
    second["finding_id"] = "finding_00000000-0000-4000-8000-000000000008"
    second["evaluator"] = {
        "evaluator_id": "evaluator_second",
        "evaluator_version": "fixture-1",
        "stage": "deterministic",
    }
    second["source_evaluators"] = [second["evaluator"]]
    assert validate_finding(canonical_json_bytes(first), bundle).valid
    assert validate_finding(canonical_json_bytes(second), bundle).valid

    merged = merge_findings([first, second])
    assert len(merged) == 1
    assert merged[0]["disagreement"]["present"] is False
    assert len(merged[0]["source_evaluators"]) == 2
    assert merge_findings([second, first])[0]["finding_id"] == merged[0]["finding_id"]

    conflict = copy.deepcopy(first)
    conflict["finding_id"] = "finding_00000000-0000-4000-8000-000000000009"
    conflict["description"] = "The conclusion is supported by the cited source."
    conflict["evaluator"] = {
        "evaluator_id": "evaluator_conflict",
        "evaluator_version": "fixture-1",
        "stage": "deterministic",
    }
    conflict["source_evaluators"] = [conflict["evaluator"]]
    conflicting = merge_findings([first, conflict])
    assert len(conflicting) == 2
    assert all(item["disagreement"]["present"] for item in conflicting)
    assert all(item["disagreement"]["peer_finding_ids"] for item in conflicting)

    invalid = copy.deepcopy(first)
    invalid["evidence"][0]["content_sha256"] = "sha256:" + "0" * 64
    report = validate_finding(canonical_json_bytes(invalid), bundle)
    assert "evidence_digest_mismatch" in {item.code for item in report.findings}


def test_finding_lifecycle_is_explicit_and_terminal_states_are_closed() -> None:
    finding = _finding(
        IDS["article"],
        "evaluator_citations",
        "finding_00000000-0000-4000-8000-000000000007",
    )
    accepted = copy.deepcopy(finding)
    accepted["status"] = "accepted"
    resolved = copy.deepcopy(accepted)
    resolved["status"] = "resolved"
    assert validate_finding_transition(finding, accepted).valid
    assert validate_finding_transition(accepted, resolved).valid
    assert "invalid_finding_transition" in {
        item.code for item in validate_finding_transition(resolved, accepted).findings
    }
    mismatched = copy.deepcopy(accepted)
    mismatched["finding_id"] = "finding_00000000-0000-4000-8000-000000000008"
    assert "finding_id_mismatch" in {
        item.code for item in validate_finding_transition(finding, mismatched).findings
    }


def test_feedback_adjudication_and_trace_boundaries_are_validated() -> None:
    feedback = {
        "schema_version": "1.0",
        "feedback_id": "feedback_00000000-0000-4000-8000-000000000009",
        "feedback_version": 1,
        "created_at": "2026-08-07T00:00:00Z",
        "actor": {"kind": "operator", "id": "local-user"},
        "finding_ids": ["finding_00000000-0000-4000-8000-000000000007"],
        "decision": "clarification_requested",
        "text": "Which source supports this claim?",
        "rationale": None,
    }
    adjudication = {
        "schema_version": "1.0",
        "adjudication_id": "adjudication_00000000-0000-4000-8000-000000000010",
        "adjudication_version": 1,
        "created_at": "2026-08-07T00:00:00Z",
        "actor": {"kind": "operator", "id": "local-user"},
        "decisions": [
            {
                "finding_id": "finding_00000000-0000-4000-8000-000000000007",
                "decision": "deferred",
                "rationale": "Need operator clarification.",
            }
        ],
    }
    trace = {
        "schema_version": "1.0",
        "trace_id": "trace_00000000-0000-4000-8000-000000000006",
        "trace_version": 1,
        "created_at": "2026-08-07T00:00:00Z",
        "bundle_id": IDS["bundle"],
        "contract_id": IDS["contract"],
        "run_id": None,
        "goal_id": None,
        "invocation_id": None,
        "events": [
            {
                "sequence": 1,
                "kind": "deterministic_check",
                "payload_sha256": "sha256:" + "1" * 64,
                "evaluator_id": "evaluator_citations",
            }
        ],
        "redaction_policy": "g15-redaction-1",
        "variable_fields": ["created_at"],
    }
    assert validate_feedback(canonical_json_bytes(feedback)).valid
    assert validate_adjudication(canonical_json_bytes(adjudication)).valid
    assert validate_trace(canonical_json_bytes(trace)).valid
    broken = copy.deepcopy(trace)
    broken["events"][0]["sequence"] = 2
    assert "noncontiguous_trace" in {
        item.code for item in validate_trace(canonical_json_bytes(broken)).findings
    }


def test_report_replay_redacts_explicit_sentinels(tmp_path: Path) -> None:
    machine = render_report(
        tmp_path,
        report_id="report_00000000-0000-4000-8000-000000000011",
        bundle_id=IDS["bundle"],
        contract_id=IDS["contract"],
        trace_ids=["trace_00000000-0000-4000-8000-000000000006"],
        finding_ids=[],
        feedback_ids=[],
        adjudication_ids=[],
        status="complete",
        created_at="2026-08-07T00:00:00Z",
        human_text="Review contact: person@example.test",
        redactions=("person@example.test",),
    )
    assert b"person@example.test" not in machine
    assert (
        b"person@example.test"
        not in (
            tmp_path / "reports/report_00000000-0000-4000-8000-000000000011.md"
        ).read_bytes()
    )


def test_report_generation_is_byte_stable(tmp_path: Path) -> None:
    kwargs = {
        "report_id": "report_00000000-0000-4000-8000-000000000012",
        "bundle_id": IDS["bundle"],
        "contract_id": IDS["contract"],
        "trace_ids": ["trace_00000000-0000-4000-8000-000000000006"],
        "finding_ids": [],
        "feedback_ids": [],
        "adjudication_ids": [],
        "status": "complete",
        "created_at": "2026-08-07T00:00:00Z",
        "human_text": "Stable report.\n",
    }
    first = render_report(tmp_path, **kwargs)
    second = render_report(tmp_path, **kwargs)
    assert first == second
    assert (
        tmp_path / "reports/report_00000000-0000-4000-8000-000000000012.md"
    ).read_bytes() == b"Stable report.\n"
