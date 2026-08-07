"""Verify the installed G15 Review Bundle and evaluator substrate."""

from __future__ import annotations

from pathlib import Path
import tempfile

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.review import (
    materialize_review_bundle,
    merge_findings,
    render_report,
    validate_adjudication,
    validate_feedback,
    validate_finding,
    validate_review_bundle,
    validate_review_contract,
    validate_trace,
)


NOW = "2026-08-07T00:00:00Z"
BUNDLE = "bundle_00000000-0000-4000-8000-000000000001"
ARTICLE = "ref_00000000-0000-4000-8000-000000000002"
CONTRACT = "contract_00000000-0000-4000-8000-000000000005"
FINDING = "finding_00000000-0000-4000-8000-000000000007"
TRACE = "trace_00000000-0000-4000-8000-000000000006"
ZERO = "sha256:" + "0" * 64


def _actor() -> dict[str, object]:
    return {"kind": "gigai", "id": "installed-g15", "model_target": None}


def _bundle() -> tuple[dict[str, object], dict[str, bytes]]:
    payload = b"A source article.\n"
    digest = digest_imported_bytes(payload)
    return (
        {
            "schema_version": "1.0",
            "bundle_id": BUNDLE,
            "bundle_version": 1,
            "created_at": NOW,
            "created_by": _actor(),
            "name": "installed-g15-bundle",
            "question": "What evidence supports this conclusion?",
            "references": [
                {
                    "reference_id": ARTICLE,
                    "role": "primary-source",
                    "kind": "article",
                    "path": "references/article.txt",
                    "media_type": "text/plain",
                    "content_sha256": digest,
                    "size_bytes": len(payload),
                    "provenance": {
                        "source_kind": "generated",
                        "locator": "fixture://article",
                        "acquired_at": NOW,
                        "acquisition_method": "checked-in-fixture",
                        "source_revision": None,
                    },
                    "sensitivity": "public",
                    "redaction_status": "not_required",
                }
            ],
            "tool_requirements": None,
            "redaction_policy": {
                "mode": "local_only",
                "allowed_reference_ids": [ARTICLE],
                "policy_version": "g15-redaction-1",
                "detector_version": None,
            },
        },
        {"references/article.txt": payload},
    )


def _contract() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "contract_id": CONTRACT,
        "contract_version": 1,
        "created_at": NOW,
        "created_by": _actor(),
        "name": "installed-g15-contract",
        "question": "Does the evidence support the conclusion?",
        "reference_roles": ["primary-source"],
        "criteria": [
            {
                "criterion_id": "criterion_evidence",
                "description": "Every conclusion has supporting evidence.",
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


def _finding(
    reference: dict[str, object], finding_id: str, evaluator_id: str
) -> dict[str, object]:
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
                "reference_id": ARTICLE,
                "content_sha256": reference["content_sha256"],
                "locator": "bytes:0-4",
                "quote": "A source",
            }
        ],
        "evaluator": evaluator,
        "source_evaluators": [evaluator],
        "trace_id": TRACE,
        "confidence": "0.90",
        "disagreement": {"present": False, "peer_finding_ids": [], "summary": None},
        "created_at": NOW,
    }


def main() -> None:
    bundle, objects = _bundle()
    with tempfile.TemporaryDirectory(prefix="gigai-g15-wheel-") as temporary:
        root = Path(temporary)
        manifest = materialize_review_bundle(root, bundle, objects)
        if not validate_review_bundle(root, manifest).valid:
            raise SystemExit("installed G15 Bundle replay failed")
        contract = _contract()
        if not validate_review_contract(canonical_json_bytes(contract)).valid:
            raise SystemExit("installed G15 Contract validation failed")
        reference = bundle["references"][0]
        first = _finding(reference, FINDING, "evaluator_citations")
        second = _finding(
            reference,
            "finding_00000000-0000-4000-8000-000000000008",
            "evaluator_second",
        )
        if not validate_finding(canonical_json_bytes(first), bundle).valid:
            raise SystemExit("installed G15 Finding validation failed")
        merged = merge_findings([first, second])
        if len(merged) != 1 or not merged[0]["disagreement"]["present"]:
            raise SystemExit("installed G15 deterministic finding merge failed")
        feedback = {
            "schema_version": "1.0",
            "feedback_id": "feedback_00000000-0000-4000-8000-000000000009",
            "feedback_version": 1,
            "created_at": NOW,
            "actor": {"kind": "operator", "id": "local-user"},
            "finding_ids": [merged[0]["finding_id"]],
            "decision": "clarification_requested",
            "text": "Which source supports this claim?",
            "rationale": None,
        }
        if not validate_feedback(canonical_json_bytes(feedback)).valid:
            raise SystemExit("installed G15 Feedback validation failed")
        adjudication = {
            "schema_version": "1.0",
            "adjudication_id": "adjudication_00000000-0000-4000-8000-000000000010",
            "adjudication_version": 1,
            "created_at": NOW,
            "actor": {"kind": "operator", "id": "local-user"},
            "decisions": [
                {
                    "finding_id": merged[0]["finding_id"],
                    "decision": "deferred",
                    "rationale": "Needs clarification.",
                }
            ],
        }
        if not validate_adjudication(canonical_json_bytes(adjudication)).valid:
            raise SystemExit("installed G15 Adjudication validation failed")
        trace = {
            "schema_version": "1.0",
            "trace_id": TRACE,
            "trace_version": 1,
            "created_at": NOW,
            "bundle_id": BUNDLE,
            "contract_id": CONTRACT,
            "run_id": None,
            "goal_id": None,
            "invocation_id": None,
            "events": [
                {
                    "sequence": 1,
                    "kind": "deterministic_check",
                    "payload_sha256": ZERO,
                    "evaluator_id": "evaluator_citations",
                }
            ],
            "redaction_policy": "g15-redaction-1",
            "variable_fields": ["created_at"],
        }
        if not validate_trace(canonical_json_bytes(trace)).valid:
            raise SystemExit("installed G15 Trace validation failed")
        report = render_report(
            root,
            report_id="report_00000000-0000-4000-8000-000000000011",
            bundle_id=BUNDLE,
            contract_id=CONTRACT,
            trace_ids=[TRACE],
            finding_ids=[merged[0]["finding_id"]],
            feedback_ids=[feedback["feedback_id"]],
            adjudication_ids=[adjudication["adjudication_id"]],
            status="complete",
            created_at=NOW,
            human_text="Review contact: person@example.test",
            redactions=("person@example.test",),
        )
        if (
            b"person@example.test" in report
            or b"person@example.test"
            in (
                root / "reports/report_00000000-0000-4000-8000-000000000011.md"
            ).read_bytes()
        ):
            raise SystemExit("installed G15 report redaction failed")
    print("verified installed GigAI G15 Review Bundle and evaluator substrate")


if __name__ == "__main__":
    main()
