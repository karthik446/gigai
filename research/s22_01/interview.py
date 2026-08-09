"""Offline local proposal-interview protocol and persistence evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import sqlite3
from typing import Any, Iterable, Mapping


STATES = {"questions_pending", "clarification_required", "proposal_ready", "approved", "blocked"}
EFFECT_CHOICES = ("read_local", "write_workpad")
QUESTION_TYPES = {"text", "choice", "multiselect", "confirmation"}

EVALUATION_CORPUS = (
    {
        "case_id": "repository-feature",
        "request": "Review a repository feature proposal against selected local source files.",
        "reference_kinds": ("repository_snapshot", "primary_source"),
        "expected_questions": ("scope", "references", "effect", "privacy", "capability"),
        "required_outcome": "approved_or_clarification_blocked",
    },
    {
        "case_id": "resume-tailoring",
        "request": "Tailor a resume draft to a selected job description.",
        "reference_kinds": ("resume", "job_description"),
        "expected_questions": ("scope", "references", "effect", "privacy", "capability"),
        "required_outcome": "approved_or_clarification_blocked",
    },
    {
        "case_id": "reference-synchronization",
        "request": "Compare two selected references and prepare a synchronized workpad draft.",
        "reference_kinds": ("source_a", "source_b"),
        "expected_questions": ("scope", "references", "effect", "privacy", "capability"),
        "required_outcome": "approved_or_clarification_blocked",
    },
    {
        "case_id": "tabular-finance",
        "request": "Analyze a local tabular finance fixture without sending it to a provider.",
        "reference_kinds": ("tabular_data",),
        "expected_questions": ("scope", "references", "effect", "privacy", "capability"),
        "required_outcome": "approved_or_clarification_blocked",
    },
)


@dataclass(frozen=True)
class Question:
    question_id: str
    answer_type: str
    required: bool
    options: tuple[str, ...]
    depends_on: tuple[str, ...]
    rationale: str
    provenance: str


@dataclass(frozen=True)
class InterviewSession:
    session_id: str
    request_kind: str
    state: str
    round: int
    max_rounds: int
    reference_ids: tuple[str, ...]
    questions: tuple[Question, ...]
    answers: Mapping[str, object]
    selected_reference_ids: tuple[str, ...]
    capability_choice: str | None
    privacy_choice: str | None
    effect_choice: str | None
    terminal_reason: str | None = None


def build_session(
    session_id: str,
    *,
    request_kind: str,
    reference_ids: tuple[str, ...],
    max_rounds: int = 3,
) -> InterviewSession:
    if not session_id or not request_kind or max_rounds <= 0:
        raise ValueError("session identity, request kind, and positive round cap are required")
    questions = (
        Question("scope", "text", True, (), (), "Define the requested outcome.", "fixture://s22-01/scope"),
        Question("references", "multiselect", True, reference_ids, (), "Select exact local references.", "fixture://s22-01/references"),
        Question("effect", "choice", True, EFFECT_CHOICES, (), "Choose a bounded local effect.", "fixture://s22-01/effect"),
        Question("privacy", "choice", True, ("local_only", "redact_before_share"), (), "Choose the privacy boundary.", "fixture://s22-01/privacy"),
        Question("capability", "choice", True, ("none", "local_read"), (), "Choose whether local read capability is needed.", "fixture://s22-01/capability"),
    )
    return InterviewSession(session_id, request_kind, "questions_pending", 1, max_rounds, reference_ids, questions, {}, (), None, None, None)


def answer_question(session: InterviewSession, question_id: str, value: object) -> InterviewSession:
    if session.state not in {"questions_pending", "clarification_required"}:
        raise ValueError(f"cannot answer in state {session.state!r}")
    question = next((item for item in session.questions if item.question_id == question_id), None)
    if question is None:
        raise ValueError(f"unknown question {question_id!r}")
    _validate_answer(question, value, session.reference_ids)
    answers = dict(session.answers)
    answers[question_id] = value
    state = "proposal_ready" if all(not item.required or item.question_id in answers for item in session.questions) else "questions_pending"
    return replace(
        session,
        state=state,
        answers=answers,
        selected_reference_ids=tuple(value) if question_id == "references" else session.selected_reference_ids,
        capability_choice=value if question_id == "capability" else session.capability_choice,
        privacy_choice=value if question_id == "privacy" else session.privacy_choice,
        effect_choice=value if question_id == "effect" else session.effect_choice,
        terminal_reason=None,
    )


def request_clarification(session: InterviewSession, *, reason: str) -> InterviewSession:
    if session.state not in {"questions_pending", "clarification_required"}:
        raise ValueError(f"cannot clarify in state {session.state!r}")
    if session.round >= session.max_rounds:
        return replace(session, state="blocked", terminal_reason="question_round_cap_exhausted")
    question = Question(
        f"clarification-{session.round + 1}",
        "text",
        True,
        (),
        tuple(session.answers),
        reason,
        "fixture://s22-01/clarification",
    )
    return replace(session, state="clarification_required", round=session.round + 1, questions=session.questions + (question,), terminal_reason=None)


def approve_session(session: InterviewSession) -> InterviewSession:
    if session.state != "proposal_ready":
        raise ValueError("only a proposal_ready session can be approved")
    if session.effect_choice not in EFFECT_CHOICES:
        return replace(session, state="blocked", terminal_reason="effect_choice_not_allowed")
    if not session.selected_reference_ids or session.privacy_choice not in {"local_only", "redact_before_share"}:
        return replace(session, state="blocked", terminal_reason="required_boundary_choice_missing")
    return replace(session, state="approved", terminal_reason="operator_approved")


def persist_trace(connection: sqlite3.Connection, session: InterviewSession, event: str) -> None:
    """Persist one redacted protocol event in a disposable SQLite trace."""

    connection.execute(
        "CREATE TABLE IF NOT EXISTS interview_events (session_id TEXT, sequence INTEGER, event TEXT, payload TEXT, PRIMARY KEY(session_id, sequence))"
    )
    sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM interview_events WHERE session_id = ?", (session.session_id,)).fetchone()[0]
    payload = json.dumps({"state": session.state, "round": session.round, "reason": session.terminal_reason}, sort_keys=True)
    connection.execute("INSERT INTO interview_events VALUES (?, ?, ?, ?)", (session.session_id, sequence, event, payload))
    connection.commit()


def load_trace(connection: sqlite3.Connection, session_id: str) -> list[dict[str, object]]:
    rows = connection.execute("SELECT sequence, event, payload FROM interview_events WHERE session_id = ? ORDER BY sequence", (session_id,)).fetchall()
    return [{"sequence": row[0], "event": row[1], "payload": json.loads(row[2])} for row in rows]


def _validate_answer(question: Question, value: object, reference_ids: tuple[str, ...]) -> None:
    if question.answer_type not in QUESTION_TYPES:
        raise ValueError("unsupported answer type")
    if question.answer_type == "text" and (not isinstance(value, str) or not value.strip()):
        raise ValueError("text answer must be non-empty")
    if question.answer_type == "choice" and value not in question.options:
        raise ValueError("choice answer is not one of the allowed options")
    if question.answer_type == "multiselect":
        if not isinstance(value, (list, tuple)) or not value or any(item not in reference_ids for item in value):
            raise ValueError("reference selection must contain allowed IDs")


__all__ = [
    "EFFECT_CHOICES",
    "EVALUATION_CORPUS",
    "InterviewSession",
    "approve_session",
    "answer_question",
    "build_session",
    "load_trace",
    "persist_trace",
    "request_clarification",
]
