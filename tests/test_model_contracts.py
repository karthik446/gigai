from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes, canonical_json_digest
from gigai.validators import (
    validate_model_exchange,
    validate_model_invocation,
    validate_serialized_contract,
)


ZERO = "sha256:" + "0" * 64
NOW = "2026-08-09T00:00:00Z"
RUN = "run_00000000-0000-4000-8000-000000000001"
SOURCE_GOAL = "goal_00000000-0000-4000-8000-000000000002"
RECEIVER_GOAL = "goal_00000000-0000-4000-8000-000000000003"
EDGE = "edge_00000000-0000-4000-8000-000000000004"
INVOCATION = "inv_00000000-0000-4000-8000-000000000005"
SECOND_INVOCATION = "inv_00000000-0000-4000-8000-000000000007"
REFERENCE = "ref_00000000-0000-4000-8000-000000000006"


def _artifact(path: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": ZERO,
        "canonical_sha256": ZERO,
        "media_type": "application/json",
        "size_bytes": 1,
    }


def _usage() -> dict[str, object]:
    return {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "cost": "0.01",
        "currency": "USD",
        "cost_status": "provider_reported",
    }


def _invocation(
    *,
    outcome: str = "succeeded",
    finish: str = "completed",
    redaction_result: str = "passed",
    network_result: str = "permitted",
    cancellation: str = "not_applicable",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "record_version": 1,
        "run_id": RUN,
        "goal_id": SOURCE_GOAL,
        "invocation_id": INVOCATION,
        "role": "reviewer",
        "provider_family": "fixture_provider",
        "configured_selector": "fixture-target",
        "endpoint_identity": "fixture-endpoint",
        "resolved_model": "fixture-model",
        "adapter_identity": "fixture-adapter@1",
        "request": {
            "selected_references": [{"reference_id": REFERENCE, "content_sha256": ZERO}],
            "request_artifact": _artifact("invocations/0005/request.json"),
            "request_sha256": ZERO,
        },
        "outcome": outcome,
        "finish": finish,
        "cancellation": cancellation,
        "error": None,
        "usage": _usage(),
        "boundary": {
            "redaction": {"policy_version": "fixture-boundary-1", "result": redaction_result},
            "credential": {"reference": None, "lookup": "not_requested"},
            "network": {"policy": "explicit_permission", "result": network_result},
            "check_order_version": "s18-05-1",
        },
        "extensions": [],
        "replay": {"stable_sha256": ZERO, "variable_fields": ["terminal_committed_at"]},
        "terminal_committed_at": NOW,
    }


def _exchange(*, kind: str = "handoff", status: str = "received") -> dict[str, object]:
    source = {"artifact": _artifact("outputs/source.json"), "invocation_id": INVOCATION, "goal_id": SOURCE_GOAL}
    record: dict[str, object] = {
        "schema_version": "1.0",
        "record_version": 1,
        "record_sha256": ZERO,
        "run_id": RUN,
        "edge_id": EDGE,
        "source_goal_id": SOURCE_GOAL,
        "receiver_goal_id": RECEIVER_GOAL,
        "kind": kind,
        "source_invocation_ids": [INVOCATION],
        "source_artifacts": [source],
        "handoff": {
            "index": 1,
            "cap": 1,
            "input_artifact": _artifact("handoffs/received.json"),
            "parent_artifact": _artifact("outputs/source.json"),
            "hidden_context": False,
        }
        if kind == "handoff"
        else None,
        "comparison": None,
        "status": status,
        "automatic_fallback": False,
        "retry_count": 0,
        "created_at": NOW,
    }
    if kind == "comparison":
        right = {"artifact": _artifact("outputs/right.json"), "invocation_id": INVOCATION, "goal_id": RECEIVER_GOAL}
        record["source_artifacts"] = [source, right]
        record["source_invocation_ids"] = [INVOCATION, SECOND_INVOCATION]
        right["invocation_id"] = SECOND_INVOCATION
        record["comparison"] = {
            "independent_artifacts": [source, right],
            "requires_human_adjudication": status == "disagreement",
            "selected_winner": None,
            "adjudication_input": _artifact("comparisons/adjudication.json") if status == "disagreement" else None,
        }
    return record


@pytest.mark.parametrize(
    ("case_id", "record"),
    [
        ("success", _invocation()),
        ("blocked", _invocation(outcome="blocked", finish="blocked", network_result="denied")),
        ("timeout", _invocation(outcome="timeout", finish="timeout")),
        ("cancellation", _invocation(outcome="cancelled", finish="cancelled", cancellation="acknowledged")),
        ("unavailable", _invocation(outcome="unavailable", finish="unavailable")),
    ],
)
def test_model_invocation_terminal_cases_are_valid(case_id: str, record: dict[str, object]) -> None:
    assert validate_model_invocation(record).valid, case_id


def test_handoff_cap_is_semantically_enforced() -> None:
    record = _exchange()
    record["handoff"]["index"] = 2  # type: ignore[index]
    report = validate_model_exchange(record)
    assert "handoff_limit_exhausted" in {finding.code for finding in report.findings}


def test_disagreement_requires_adjudication_and_no_winner() -> None:
    record = _exchange(kind="comparison", status="disagreement")
    assert validate_model_exchange(record).valid
    assert record["comparison"]["requires_human_adjudication"]  # type: ignore[index]
    assert record["comparison"]["selected_winner"] is None  # type: ignore[index]


def test_comparison_rejects_reused_invocation_identity() -> None:
    record = _exchange(kind="comparison", status="disagreement")
    record["comparison"]["independent_artifacts"][1]["invocation_id"] = INVOCATION  # type: ignore[index]
    report = validate_model_exchange(record)
    assert "comparison_not_independent" in {finding.code for finding in report.findings}


def test_redaction_failure_is_a_blocked_terminal_case() -> None:
    record = _invocation(outcome="blocked", finish="blocked", redaction_result="failed", network_result="not_checked")
    assert validate_model_invocation(record).valid
    invalid = copy.deepcopy(record)
    invalid["outcome"] = "succeeded"
    invalid["finish"] = "completed"
    report = validate_model_invocation(invalid)
    assert "redaction_failure_not_blocked" in {finding.code for finding in report.findings}


def test_canonical_vectors_match_the_production_identity_api() -> None:
    fixture = json.loads(
        Path(__file__).parents[1]
        .joinpath("research/contract_spike/fixtures/model-contract-vectors.json")
        .read_text(encoding="utf-8")
    )
    for vector in fixture["vectors"]:
        assert canonical_json_bytes(vector["input"]).decode("utf-8") == vector["canonical_utf8"]
        assert canonical_json_digest(vector["input"]) == vector["sha256"]


def test_model_schemas_are_registered_and_strict() -> None:
    invocation = _invocation()
    exchange = _exchange()
    assert validate_serialized_contract("model-invocation.schema.json", canonical_json_bytes(invocation)).valid
    assert validate_serialized_contract("model-exchange.schema.json", canonical_json_bytes(exchange)).valid
    unknown = copy.deepcopy(invocation)
    unknown["surprise"] = True
    assert not validate_serialized_contract("model-invocation.schema.json", canonical_json_bytes(unknown)).valid
