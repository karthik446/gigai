from __future__ import annotations

import json
from types import SimpleNamespace

from gigai.canonical import digest_imported_bytes
from gigai.model_execution import SelectedReference
from gigai.provider_review import (
    _adjudicate_prompt,
    _adjudication_record,
    _json_object,
    _parse_findings,
    _review_prompt,
    _verification_record,
    _verify_prompt,
)


RUN_ID = "run_00000000-0000-4000-8000-000000000001"
GIG_ID = "gig_00000000-0000-4000-8000-000000000002"
BUNDLE_ID = "bundle_00000000-0000-4000-8000-000000000003"
CONTRACT_ID = "contract_00000000-0000-4000-8000-000000000004"
TRACE_ID = "trace_00000000-0000-4000-8000-000000000005"


def _framed(payload: object, *, prefix: str = "Review complete.", suffix: str = "") -> str:
    return f"{prefix}\n```json\n{json.dumps(payload)}\n```\n{suffix}"


def _references() -> tuple[tuple[SelectedReference, ...], dict[str, str]]:
    subject = b"# Subject\n"
    baseline = b"# Baseline\n"
    refs = (
        SelectedReference(
            "ref_00000000-0000-4000-8000-000000000011",
            "inputs/subject.md",
            subject,
            digest_imported_bytes(subject),
            "text/markdown",
        ),
        SelectedReference(
            "ref_00000000-0000-4000-8000-000000000012",
            "inputs/baseline.md",
            baseline,
            digest_imported_bytes(baseline),
            "text/markdown",
        ),
    )
    return refs, {refs[0].reference_id: "review_subject", refs[1].reference_id: "requirements_baseline"}


def _finding_payload(refs: tuple[SelectedReference, ...]) -> dict[str, object]:
    return {
        "findings": [
            {
                "criterion_id": "criterion_requirements",
                "severity": "medium",
                "title": "Boundary is underspecified",
                "description": "The subject does not state the required boundary.",
                "evidence": [
                    {"reference_id": refs[0].reference_id, "locator": "line 1"},
                    {"reference_id": refs[1].reference_id, "locator": "line 1"},
                ],
                "confidence": "0.8",
            }
        ]
    }


def test_json_object_accepts_raw_whole_fence_and_one_fence_with_prose() -> None:
    payload = {"findings": []}
    assert _json_object(json.dumps(payload)) == payload
    assert _json_object("```json\n{}\n```") == {}
    provider_reply = _framed(payload, suffix="No other payload follows.")
    original_reply = provider_reply
    assert _json_object(provider_reply) == payload
    assert provider_reply == original_reply


def test_json_object_rejects_ambiguous_or_non_object_responses() -> None:
    payload = {"findings": []}
    cases = (
        "```json\n{}\n```\n```json\n{}\n```",
        "```json\n{\"findings\":[\n```",
        "```json\n[1, 2]\n```",
        "```json\n{\"findings\":[]}\n```\n{\"other\":true}",
        '{"findings": [], "findings": []}',
        '{"outer": {"value": 1, "value": 2}}',
        "{\"findings\": []} {\"other\": true}",
        "```python\n{\"findings\": []}\n```",
        _framed(payload, suffix='Malformed candidate: {"findings":'),
        _framed(payload, suffix='Duplicate candidate: {"value": 1, "value": 2}'),
        _framed(payload, suffix="Malformed fence: ```"),
        _framed(payload, suffix="Bracketed prose: [not a payload]"),
        json.dumps({"findings": "\ud800"}),
        _framed({"findings": "\ud800"}),
    )
    assert all(_json_object(case) == {} for case in cases)
    assert _json_object(_framed(payload, prefix='The candidate is {"other": true}.')) == {}

    deeply_nested = '{"value":' + "[" * 2_000 + "0" + "]" * 2_000 + "}"
    assert _json_object(f"Review complete.\n```json\n{deeply_nested}\n```\nDone.") == {}


def test_reviewer_parses_framed_json_but_preserves_invalid_evidence_as_blocking() -> None:
    refs, roles = _references()
    contract = {"criteria": [{"criterion_id": "criterion_requirements"}]}
    valid, invalid = _parse_findings(
        _framed(_finding_payload(refs)), contract, refs, roles, TRACE_ID, {"participant_id": "participant_p1"}
    )
    assert invalid is False
    assert len(valid) == 1

    malformed = _finding_payload(refs)
    evidence = malformed["findings"][0]["evidence"]  # type: ignore[index]
    assert isinstance(evidence, list)
    evidence.pop()
    rejected, invalid = _parse_findings(
        _framed(malformed), contract, refs, roles, TRACE_ID, {"participant_id": "participant_p1"}
    )
    assert rejected == []
    assert invalid is True


def test_verifier_and_adjudicator_parse_framed_json() -> None:
    refs, roles = _references()
    parsed, invalid = _parse_findings(
        _framed(_finding_payload(refs)),
        {"criteria": [{"criterion_id": "criterion_requirements"}]},
        refs,
        roles,
        TRACE_ID,
        {"participant_id": "participant_p1"},
    )
    assert invalid is False
    finding_id = parsed[0]["finding_id"]
    evidence = [
        {
            "path": ref.path,
            "content_sha256": ref.content_sha256,
            "media_type": ref.media_type,
            "size_bytes": len(ref.content),
        }
        for ref in refs
    ]
    verifier_output = _framed({"outcomes": [{"finding_id": finding_id, "status": "verified", "reason": "Both sources support it."}]})
    verification, invalid = _verification_record(
        execution=SimpleNamespace(result=SimpleNamespace(output_text=verifier_output)),
        verifier={"participant_id": "participant_p2", "model_target_id": "target-verifier"},
        findings=parsed,
        run_id=RUN_ID,
        gig_id=GIG_ID,
        bundle_id=BUNDLE_ID,
        contract_id=CONTRACT_ID,
        evidence=evidence,
    )
    assert invalid is False
    assert verification["outcomes"][0]["status"] == "verified"  # type: ignore[index]

    adjudication, invalid = _adjudication_record(
        SimpleNamespace(
            result=SimpleNamespace(
                output_text=_framed(
                    {"decisions": [{"finding_id": finding_id, "decision": "accepted", "rationale": "The evidence is sufficient."}]}  # noqa: E501
                )
            )
        ),
        {"participant_id": "participant_p3", "model_target_id": "target-adjudicator"},
        parsed,
    )
    assert invalid is False
    assert adjudication["decisions"][0]["decision"] == "accepted"  # type: ignore[index]


def test_prompts_require_one_unfenced_json_object() -> None:
    contract = {"question": "Review this.", "criteria": []}
    review = _review_prompt(contract, {"participant_id": "participant_p1"}, {})
    verify = _verify_prompt([], {})
    adjudicate = _adjudicate_prompt([], [])
    for prompt in (review, verify, adjudicate):
        assert "exactly one JSON object" in prompt
        assert "Markdown fences" in prompt
        assert "prose" in prompt
