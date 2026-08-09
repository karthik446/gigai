from __future__ import annotations

from http.client import HTTPResponse
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
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
from gigai.question_generation import QuestionGenerationError, generate_model_questions
import gigai.question_generation as question_generation
from gigai.setup import build_config, run_setup
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


def test_deterministic_question_generation_uses_g18_factory_and_adds_typed_question(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)
    session = answer_question(_session(), "references", [_session().references[0].reference_id], now=NOW)
    generated = generate_model_questions(
        config=config,
        model_target="offline-default",
        session=session,
        reference_bytes={session.references[0].reference_id: b"selected bytes\n"},
    )
    question = next(item for item in generated.questions if item.question_id == "operator-confirmation")
    assert question.answer_type == "confirmation"
    assert generated.events[-1]["event"] == "question_presented"


def test_remote_question_generation_is_explicit_and_selected_bytes_only(monkeypatch, tmp_path: Path) -> None:
    class FakePort:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def invoke(self, request):
            self.prompts.append(request.prompt)
            from gigai.adapters.port import InvocationResult, NormalizedUsage

            return InvocationResult(
                status="success",
                output_text=json.dumps({
                    "questions": [{
                        "question_id": "remote-confirmation",
                        "answer_type": "confirmation",
                        "required": True,
                        "options": [],
                        "depends_on": ["references"],
                        "rationale": "Confirm the selected material.",
                        "provenance": "model://fake/g22",
                    }]
                }),
                resolved_model="fake",
                raw_usage={},
                normalized_usage=NormalizedUsage(None, None, None),
                cost_status="unavailable",
            )

    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)
    selected_content = b"selected material"
    unselected_content = b"unselected material"
    selected_reference = ReferenceDecision(
        "ref_00000000-0000-4000-8000-000000000005",
        digest_imported_bytes(selected_content),
    )
    second = ReferenceDecision(
        "ref_00000000-0000-4000-8000-000000000006",
        digest_imported_bytes(unselected_content),
    )
    selected = selected_reference.reference_id
    session = _session()
    session = build_session(
        session_id=SESSION,
        project_id=PROJECT,
        gig_id=GIG,
        request_kind="repository-feature",
        request_artifact=session.request_artifact,
        request_sha256=SHA,
        references=(selected_reference, second),
        now=NOW,
    )
    session = answer_question(session, "references", [selected], now=NOW)
    port = FakePort()
    binding = SimpleNamespace(
        current=SimpleNamespace(endpoint=SimpleNamespace(adapter="openai_api")),
        port=port,
        request=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(question_generation, "resolve_model_adapter", lambda *_args: binding)

    generated = generate_model_questions(
        config=config,
        model_target="remote-test",
        session=session,
        reference_bytes={selected: selected_content, second.reference_id: unselected_content},
        network_allowed=True,
    )
    assert generated.questions[-1].question_id == "remote-confirmation"
    assert "selected material" in port.prompts[0]
    assert "unselected material" not in port.prompts[0]


def test_remote_question_generation_fails_closed_without_network_permission(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)
    binding = SimpleNamespace(current=SimpleNamespace(endpoint=SimpleNamespace(adapter="openai_api")))
    monkeypatch.setattr(question_generation, "resolve_model_adapter", lambda *_args: binding)
    with pytest.raises(QuestionGenerationError, match="network permission is denied"):
        generate_model_questions(
            config=config,
            model_target="remote-test",
            session=answer_question(_session(), "references", ["ref_00000000-0000-4000-8000-000000000005"], now=NOW),
            reference_bytes={"ref_00000000-0000-4000-8000-000000000005": b"selected"},
        )


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


def test_sqlite_trace_rejects_divergent_or_stale_snapshots() -> None:
    connection = sqlite3.connect(":memory:")
    first = answer_question(_session(), "scope", "first", now=NOW)
    persist_trace(connection, first)
    persist_trace(connection, first)
    divergent = answer_question(_session(), "scope", "other", now=NOW)
    with pytest.raises(ProposalInterviewError, match="conflicts"):
        persist_trace(connection, divergent)
    with pytest.raises(ProposalInterviewError, match="truncate"):
        persist_trace(connection, _session())


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
            data=json.dumps({
                "event": "answer",
                "question_id": "scope",
                "value": "local review",
                "revision": 1,
                "sequence": 2,
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            assert json.loads(response.read()) == {
                "session_id": SESSION,
                "state": "questions_pending",
            }

        stale = Request(
            f"{server.url}/events",
            data=json.dumps({
                "event": "answer",
                "question_id": "references",
                "value": ["ref_00000000-0000-4000-8000-000000000005"],
                "revision": 1,
                "sequence": 2,
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(stale, timeout=2)
        assert error.value.code == 409

        with pytest.raises(HTTPError) as error:
            urlopen(server.url.replace(server.token, "wrong"), timeout=2)
        assert error.value.code == 404
    finally:
        server.close()


def test_loopback_http_rejects_malformed_payload_and_expires() -> None:
    server = InterviewHTTPServer(_session(), lifetime_seconds=0.05).start()
    try:
        request = Request(
            f"{server.url}/events",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == 409
        assert server.wait(timeout=2).state == "questions_pending"
        with pytest.raises((HTTPError, OSError)):
            urlopen(server.url, timeout=2)
    finally:
        server.close()
