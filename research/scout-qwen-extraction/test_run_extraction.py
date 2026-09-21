"""Offline evaluator-contract checks for the synthetic Qwen harness."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    "scout_qwen_extraction", ROOT / "run_extraction.py"
)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def _fixtures() -> list[dict[str, object]]:
    return json.loads((ROOT / "fixtures" / "postings.json").read_text())


def _output(fixture: dict[str, object], **changes: object) -> dict[str, object]:
    expected = fixture["expected"]
    assert isinstance(expected, dict)
    output = {
        "salary_min_usd_yearly": expected["salary_min_usd_yearly"],
        "salary_max_usd_yearly": expected["salary_max_usd_yearly"],
        "location_mode": HARNESS._expected_location(expected["location_mode"]),
        "sponsorship_status": expected["sponsorship_status"],
        "evidence": {
            "salary": "Compensation and employer sponsorship are not stated."
            if expected["salary_min_usd_yearly"] is None
            else "USD 140000-180000 yearly",
            "location": "remote within the United States only"
            if expected["location_mode"] == "remote_us_only"
            else "Denver, CO; hybrid schedule",
            "sponsorship": "Compensation and employer sponsorship are not stated."
            if expected["sponsorship_status"] == "unknown"
            else "Employer sponsorship is explicitly unavailable",
        },
    }
    output.update(changes)
    return output


def test_existing_fixture_meanings_are_explicit_and_legacy_bytes_survive() -> None:
    first, second = _fixtures()
    assert HARNESS._schema()["properties"]["location_mode"]["enum"] == [
        None,
        "hybrid",
        "remote",
        "onsite",
    ]
    assert "hybrid (a hybrid schedule)" in HARNESS._request_prompt(first["posting"])
    assert (
        HARNESS._validate_extraction(
            _output(first), first["expected"], first["posting"]
        )
        == []
    )
    assert (
        HARNESS._validate_extraction(
            _output(second), second["expected"], second["posting"]
        )
        == []
    )


def test_evidence_accepts_variable_exact_absence_sentence() -> None:
    fixture = _fixtures()[1]
    output = _output(fixture)
    output["evidence"] = {
        "salary": "Compensation and employer sponsorship are not stated.",
        "location": "Location: remote within the United States only.",
        "sponsorship": "employer sponsorship are not stated",
    }
    assert (
        HARNESS._validate_extraction(output, fixture["expected"], fixture["posting"])
        == []
    )


def test_evidence_rejects_unrelated_or_fabricated_quotes() -> None:
    fixture = _fixtures()[1]
    output = _output(fixture)
    output["evidence"] = {
        "salary": "Location: remote within the United States only",
        "location": "made up remote source sentence",
        "sponsorship": "Location: remote within the United States only",
    }
    findings = HARNESS._validate_extraction(
        output, fixture["expected"], fixture["posting"]
    )
    assert "evidence.salary is not an exact relevant posting quote" in findings
    assert "evidence.location is not an exact relevant posting quote" in findings
    assert "evidence.sponsorship is not an exact relevant posting quote" in findings


def test_null_evidence_does_not_hide_an_expected_absence() -> None:
    fixture = _fixtures()[1]
    output = _output(fixture)
    output["evidence"]["salary"] = None
    output["evidence"]["sponsorship"] = None
    findings = HARNESS._validate_extraction(
        output, fixture["expected"], fixture["posting"]
    )
    assert "evidence.salary is required for the expected fact" in findings
    assert "evidence.sponsorship is required for the expected fact" in findings


def test_invalid_role_and_truncation_keep_raw_metadata() -> None:
    fixture = _fixtures()[0]
    response = {
        "model": HARNESS.MODEL,
        "done": True,
        "done_reason": "stop",
        "eval_count": 4,
        "message": {"role": "tool", "content": "{}"},
    }
    attempt = HARNESS._evaluate_response(response, fixture, 123, 0.125)
    assert attempt["status"] == "failure"
    assert attempt["assistant_role"] == "tool"
    assert attempt["raw_response"] == response
    truncated = dict(response)
    truncated["done_reason"] = "length"
    truncated_attempt = HARNESS._evaluate_response(truncated, fixture, 124, 0.25)
    assert truncated_attempt["status"] == "truncated"
    assert truncated_attempt["completion_status"] == "truncated"
    assert truncated_attempt["raw_response"] == truncated
