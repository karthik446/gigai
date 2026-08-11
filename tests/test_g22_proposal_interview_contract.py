from __future__ import annotations

import copy

from gigai.canonical import canonical_json_bytes
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


SESSION = "session_00000000-0000-4000-8000-000000000001"
PROJECT = "project_00000000-0000-4000-8000-000000000002"
GIG = "gig_00000000-0000-4000-8000-000000000003"
PROPOSAL = "gp_00000000-0000-4000-8000-000000000004"
REFERENCE = "ref_00000000-0000-4000-8000-000000000005"
SHA = "sha256:" + "1" * 64
NOW = "2026-08-09T00:00:00Z"


def _record(*, state: str = "approved") -> dict[str, object]:
    approval = {
        "decision": "approved",
        "approved_at": NOW,
        "approved_by": {"kind": "operator", "id": "local-user"},
        "proposal_sha256": SHA,
    }
    return {
        "schema_version": "1.0",
        "record_version": 1,
        "revision": 1,
        "parent_revision": None,
        "session_id": SESSION,
        "project_id": PROJECT,
        "gig_id": GIG,
        "proposal_id": PROPOSAL if state == "approved" else None,
        "request": {
            "kind": "repository-feature",
            "artifact": {
                "path": "draft/request.txt",
                "content_sha256": SHA,
                "media_type": "text/plain",
                "size_bytes": 12,
            },
            "content_sha256": SHA,
        },
        "state": state,
        "round": 1,
        "max_rounds": 3,
        "references": [
            {"reference_id": REFERENCE, "content_sha256": SHA, "decision": "selected"}
        ],
        "selected_reference_ids": [REFERENCE],
        "questions": [
            {
                "question_id": "effect",
                "answer_type": "choice",
                "required": True,
                "options": ["read_local", "write_workpad"],
                "depends_on": [],
                "rationale": "Choose the bounded effect.",
                "provenance": "fixture://s22-01/effect",
            },
            {
                "question_id": "confirm",
                "answer_type": "confirmation",
                "required": True,
                "options": [],
                "depends_on": ["effect"],
                "rationale": "Confirm the proposal boundary.",
                "provenance": "fixture://s22-01/confirm",
            },
        ],
        "answers": [
            {
                "question_id": "effect",
                "answer_type": "choice",
                "value": "write_workpad",
                "answered_at": NOW,
            },
            {
                "question_id": "confirm",
                "answer_type": "confirmation",
                "value": True,
                "answered_at": NOW,
            },
        ],
        "boundary": {
            "privacy": "local_only",
            "capability": "none",
            "effect": "write_workpad",
        },
        "events": [
            {
                "sequence": 1,
                "event": "session_created",
                "state": "questions_pending",
                "actor": {"kind": "gigai", "id": "g22-fixture"},
                "payload_sha256": SHA,
                "occurred_at": NOW,
            },
            {
                "sequence": 2,
                "event": "approved",
                "state": "approved",
                "actor": {"kind": "operator", "id": "local-user"},
                "payload_sha256": SHA,
                "occurred_at": NOW,
            },
        ],
        "approval": approval if state == "approved" else None,
        "terminal_reason": "operator_approved" if state == "approved" else None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_g22_schema_is_additive_and_validates_approved_snapshot() -> None:
    assert len(SCHEMA_NAMES) == 25
    assert "proposal-interview.schema.json" in SCHEMA_NAMES
    assert validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(_record())
    ).valid


def test_g22_schema_validates_blocked_snapshot_and_requires_reason() -> None:
    blocked = _record(state="blocked")
    blocked["approval"] = None
    blocked["terminal_reason"] = "question_round_cap_exhausted"
    blocked["events"] = [
        {
            "sequence": 1,
            "event": "blocked",
            "state": "blocked",
            "actor": {"kind": "gigai", "id": "g22-fixture"},
            "payload_sha256": SHA,
            "occurred_at": NOW,
        }
    ]
    assert validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(blocked)
    ).valid

    missing_reason = copy.deepcopy(blocked)
    missing_reason["terminal_reason"] = None
    assert not validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(missing_reason)
    ).valid


def test_g22_schema_allows_pending_reference_selection_but_not_approval() -> None:
    pending = _record(state="proposal_ready")
    pending["selected_reference_ids"] = []
    pending["approval"] = None
    assert validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(pending)
    ).valid

    approved = _record()
    approved["selected_reference_ids"] = []
    assert not validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(approved)
    ).valid


def test_g22_schema_rejects_wrong_answer_type_approval_and_effect() -> None:
    wrong_answer = _record()
    wrong_answer["answers"][1]["value"] = "yes"  # type: ignore[index]
    assert not validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(wrong_answer)
    ).valid

    pending_approval = _record(state="proposal_ready")
    pending_approval["approval"] = _record()["approval"]
    assert not validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(pending_approval)
    ).valid

    forbidden_effect = _record()
    forbidden_effect["boundary"]["effect"] = "write_target"  # type: ignore[index]
    assert not validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(forbidden_effect)
    ).valid
