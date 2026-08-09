from __future__ import annotations

from http.client import HTTPResponse
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.proposal_interview import (
    InterviewHTTPServer,
    ProposalInterviewError,
    ReferenceDecision,
    answer_question,
    approve_session,
    build_session,
    load_trace,
    persist_trace,
    request_clarification,
    session_record,
)
from gigai.validators import validate_serialized_contract


SESSION = "session_00000000-0000-4000-8000-000000000001"
PROJECT = "project_00000000-0000-4000-8000-000000000002"
GIG = "gig_00000000-0000-4000-8000-000000000003"
PROPOSAL = "gp_00000000-0000-4000-8000-000000000004"
SHA = "sha256:" + "1" * 64
NOW = "2026-08-09T00:00:00Z"


def _session(*, max_rounds: int = 3):
    return build_session(
        session_id=SESSION,
        project_id=PROJECT,
        gig_id=GIG,
        request_kind="repository-feature",
        request_artifact={
            "path": "draft/request.txt",
            "content_sha256": SHA,
            "media_type": "text/plain",
            "size_bytes": 12,
        },
        request_sha256=SHA,
        references=(ReferenceDecision("ref_00000000-0000-4000-8000-000000000005", SHA),),
        max_rounds=max_rounds,
        now=NOW,
    )


def _ready(session):
    session = answer_question(session, "scope", "Review the selected feature.", now=NOW)
    session = answer_question(session, "references", [session.references[0].reference_id], now=NOW)
    session = answer_question(session, "effect", "write_workpad", now=NOW)
    session = answer_question(session, "privacy", "local_only", now=NOW)
    return answer_question(session, "capability", "none", now=NOW)


def test_protocol_reaches_ready_and_operator_approval_only() -> None:
    session = _ready(_session())
    assert session.state == "proposal_ready"
    approved = approve_session(session, proposal_id=PROPOSAL, proposal_sha256=SHA, now=NOW)
    assert approved.state == "approved"
    assert approved.approval == {
        "decision": "approved",
        "approved_at": NOW,
        "approved_by": {"kind": "operator", "id": "local-user"},
        "proposal_sha256": SHA,
    }
    with pytest.raises(ProposalInterviewError, match="terminal state"):
        answer_question(approved, "scope", "try again", now=NOW)


def test_protocol_rejects_wrong_types_unknown_questions_and_unselected_references() -> None:
    session = _session()
    with pytest.raises(ProposalInterviewError, match="text answer"):
        answer_question(session, "scope", True, now=NOW)
    with pytest.raises(ProposalInterviewError, match="unknown question"):
        answer_question(session, "missing", "value", now=NOW)
    with pytest.raises(ProposalInterviewError, match="reference selection"):
        answer_question(session, "references", [], now=NOW)
    with pytest.raises(ProposalInterviewError, match="reference selection"):
        answer_question(session, "references", ["ref_00000000-0000-4000-8000-000000000099"], now=NOW)


def test_changed_answer_creates_explicit_parent_revision() -> None:
    first = answer_question(_session(), "scope", "first scope", now=NOW)
    revised = answer_question(first, "scope", "revised scope", now=NOW)
    assert revised.revision == 2
    assert revised.parent_revision == 1
    assert revised.events[-1]["event"] == "revision_created"
    assert validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(session_record(revised))
    ).valid


def test_clarification_round_cap_blocks_without_approval() -> None:
    session = request_clarification(_session(max_rounds=1), reason="scope is ambiguous", now=NOW)
    assert session.state == "blocked"
    assert session.terminal_reason == "question_round_cap_exhausted"
    with pytest.raises(ProposalInterviewError, match="proposal_ready"):
        approve_session(session, proposal_id=PROPOSAL, proposal_sha256=SHA, now=NOW)


def test_sqlite_trace_contains_only_ordered_redacted_event_metadata() -> None:
    connection = sqlite3.connect(":memory:")
    session = _ready(_session())
    persist_trace(connection, session)
    trace = load_trace(connection, SESSION)
    assert [item["sequence"] for item in trace] == list(range(1, len(trace) + 1))
    raw = json.dumps(trace, sort_keys=True)
    assert "Review the selected feature" not in raw
    assert "write_workpad" not in raw
    assert all(str(item["payload_sha256"]).startswith("sha256:") for item in trace)
    assert validate_serialized_contract(
        "proposal-interview.schema.json", canonical_json_bytes(session_record(session))
    ).valid


def test_loopback_http_requires_token_and_preserves_session_boundary() -> None:
    server = InterviewHTTPServer(_session()).start()
    try:
        with urlopen(server.url, timeout=2) as response:
            assert response.status == 200
            assert response.headers["X-GigAI-State"] == "questions_pending"
            body = response.read()
            assert b"GigAI proposal interview" in body
            assert b"hx-post" in body
            assert b"scope" in body

        request = Request(
            f"{server.url}/events",
            data=json.dumps({"event": "answer", "question_id": "scope", "value": "local review"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            assert json.loads(response.read()) == {
                "session_id": SESSION,
                "state": "questions_pending",
            }

        with pytest.raises(HTTPError) as error:
            urlopen(server.url.replace(server.token, "wrong"), timeout=2)
        assert error.value.code == 404
    finally:
        server.close()
