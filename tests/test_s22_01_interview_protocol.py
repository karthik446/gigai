from __future__ import annotations

import ast
from pathlib import Path
import sqlite3

from research.s22_01.interview import (
    EFFECT_CHOICES,
    EVALUATION_CORPUS,
    approve_session,
    answer_question,
    build_session,
    load_trace,
    persist_trace,
    request_clarification,
)


def _ready_session():
    session = build_session("session-1", request_kind="repository-feature", reference_ids=("ref-a", "ref-b"))
    for question_id, value in (
        ("scope", "Review the selected feature."),
        ("references", ["ref-a"]),
        ("effect", EFFECT_CHOICES[0]),
        ("privacy", "local_only"),
        ("capability", "local_read"),
    ):
        session = answer_question(session, question_id, value)
    return session


def test_structured_answers_reach_approved_without_unsafe_effects() -> None:
    session = _ready_session()
    assert session.state == "proposal_ready"
    approved = approve_session(session)
    assert approved.state == "approved"
    assert approved.effect_choice in EFFECT_CHOICES
    assert approved.selected_reference_ids == ("ref-a",)


def test_invalid_reference_and_unsafe_effect_fail_closed() -> None:
    session = build_session("session-1", request_kind="resume-tailoring", reference_ids=("resume", "job"))
    try:
        answer_question(session, "references", ["unknown"])
    except ValueError as error:
        assert "allowed IDs" in str(error)
    else:
        raise AssertionError("unknown reference was accepted")
    session = _ready_session()
    unsafe = session.__class__(**{**session.__dict__, "effect_choice": "write_target"})
    assert approve_session(unsafe).terminal_reason == "effect_choice_not_allowed"


def test_clarification_rounds_are_bounded() -> None:
    session = build_session("session-1", request_kind="tabular-finance", reference_ids=("data",), max_rounds=2)
    session = request_clarification(session, reason="The requested metric is ambiguous.")
    assert session.state == "clarification_required"
    session = request_clarification(session, reason="The ambiguity remains.")
    assert session.state == "blocked"
    assert session.terminal_reason == "question_round_cap_exhausted"


def test_clarification_preserves_unanswered_boundary_questions() -> None:
    session = build_session("session-1", request_kind="repository-feature", reference_ids=("ref-a",))
    session = answer_question(session, "scope", "Review the feature.")
    session = request_clarification(session, reason="Which acceptance condition is intended?")
    clarification_id = session.questions[-1].question_id
    session = answer_question(session, clarification_id, "The local acceptance condition.")
    assert session.state == "questions_pending"
    assert {question.question_id for question in session.questions} >= {"references", "effect", "privacy", "capability"}


def test_sqlite_trace_is_ordered_and_replayable() -> None:
    connection = sqlite3.connect(":memory:")
    session = _ready_session()
    persist_trace(connection, session, "proposal_ready")
    approved = approve_session(session)
    persist_trace(connection, approved, "approved")
    trace = load_trace(connection, "session-1")
    assert [row["event"] for row in trace] == ["proposal_ready", "approved"]
    assert trace[1]["payload"]["state"] == "approved"


def test_evaluation_corpus_covers_required_gig_shapes() -> None:
    assert {case["case_id"] for case in EVALUATION_CORPUS} == {
        "repository-feature",
        "resume-tailoring",
        "reference-synchronization",
        "tabular-finance",
    }
    assert all(case["required_outcome"] == "approved_or_clarification_blocked" for case in EVALUATION_CORPUS)


def test_research_protocol_has_no_server_provider_or_process_imports() -> None:
    source = Path(__file__).parents[1].joinpath("research/s22_01/interview.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported.intersection({"socket", "subprocess", "httpx", "urllib", "requests", "fastapi", "flask"})
