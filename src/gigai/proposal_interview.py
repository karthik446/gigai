"""G22 local proposal-interview protocol and loopback transport."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import html
import http.server
import json
import secrets
import sqlite3
import threading
from typing import Callable, Mapping
import uuid

from .canonical import canonical_json_digest


STATES = frozenset(
    {"questions_pending", "clarification_required", "proposal_ready", "approved", "blocked"}
)
QUESTION_TYPES = frozenset({"text", "choice", "multiselect", "confirmation"})
EFFECT_CHOICES = ("read_local", "write_workpad")
PRIVACY_CHOICES = ("local_only", "redact_before_share")
CAPABILITY_CHOICES = ("none", "local_read")
DEFAULT_EFFECT = "write_workpad"
DEFAULT_PRIVACY = "local_only"
DEFAULT_CAPABILITY = "local_read"


class ProposalInterviewError(ValueError):
    """A G22 protocol event cannot be accepted."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: object) -> str:
    return canonical_json_digest(value)


def _id(value: str, prefix: str) -> str:
    try:
        parsed = uuid.UUID(value.removeprefix(prefix))
    except (ValueError, AttributeError):
        raise ProposalInterviewError(f"{prefix} ID is invalid") from None
    if value != f"{prefix}{parsed}" or parsed.version != 4:
        raise ProposalInterviewError(f"{prefix} ID is not canonical")
    return value


@dataclass(frozen=True)
class Question:
    question_id: str
    answer_type: str
    required: bool
    options: tuple[str, ...]
    depends_on: tuple[str, ...]
    rationale: str
    provenance: str

    def __post_init__(self) -> None:
        if not self.question_id or not self.question_id.replace("-", "").replace("_", "").isalnum():
            raise ProposalInterviewError("question ID must be a non-empty identifier")
        if self.answer_type not in QUESTION_TYPES:
            raise ProposalInterviewError("unsupported question answer type")
        if len(set(self.options)) != len(self.options):
            raise ProposalInterviewError("question options must be unique")
        if not self.rationale.strip() or not self.provenance.strip():
            raise ProposalInterviewError("question rationale and provenance are required")


@dataclass(frozen=True)
class ReferenceDecision:
    reference_id: str
    content_sha256: str
    decision: str = "excluded"

    def __post_init__(self) -> None:
        _id(self.reference_id, "ref_")
        if not self.content_sha256.startswith("sha256:") or len(self.content_sha256) != 71:
            raise ProposalInterviewError("reference content digest is invalid")
        if self.decision not in {"selected", "excluded"}:
            raise ProposalInterviewError("reference decision is invalid")


@dataclass(frozen=True)
class Answer:
    question_id: str
    answer_type: str
    value: object
    answered_at: str


@dataclass(frozen=True)
class InterviewSession:
    session_id: str
    project_id: str
    gig_id: str
    proposal_id: str | None
    revision: int
    parent_revision: int | None
    request_kind: str
    request_artifact: Mapping[str, object]
    request_sha256: str
    state: str
    round: int
    max_rounds: int
    references: tuple[ReferenceDecision, ...]
    questions: tuple[Question, ...]
    answers: tuple[Answer, ...] = ()
    privacy_choice: str | None = None
    capability_choice: str | None = None
    effect_choice: str | None = None
    events: tuple[Mapping[str, object], ...] = ()
    approval: Mapping[str, object] | None = None
    terminal_reason: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def selected_reference_ids(self) -> tuple[str, ...]:
        return tuple(item.reference_id for item in self.references if item.decision == "selected")


def build_session(
    *,
    session_id: str,
    project_id: str,
    gig_id: str,
    request_kind: str,
    request_artifact: Mapping[str, object],
    request_sha256: str,
    references: tuple[ReferenceDecision, ...],
    max_rounds: int = 3,
    now: str | None = None,
) -> InterviewSession:
    """Create a protocol session that collects explicit reference choices."""

    _id(session_id, "session_")
    _id(project_id, "project_")
    _id(gig_id, "gig_")
    if not request_kind.strip() or max_rounds < 1:
        raise ProposalInterviewError("request kind and positive round cap are required")
    if len({item.reference_id for item in references}) != len(references):
        raise ProposalInterviewError("reference IDs must be unique")
    if not request_sha256.startswith("sha256:") or len(request_sha256) != 71:
        raise ProposalInterviewError("request digest is invalid")
    reference_ids = tuple(item.reference_id for item in references)
    reference_question = (
        Question("references", "multiselect", False, reference_ids, (), "Add exact local references only when this Gig needs local context. You can skip this.", "g22://references")
        if reference_ids
        else Question("references", "text", False, (), (), "Add exact local paths only when this Gig needs local context. You can skip this.", "g22://references")
    )
    questions = (
        Question("scope", "text", True, (), (), "Describe the outcome this Gig should own.", "g22://scope"),
        reference_question,
        Question("effect", "choice", False, EFFECT_CHOICES, (), "GigAI writes proposal artifacts to its private workpad.", "g22://effect"),
        Question("privacy", "choice", False, PRIVACY_CHOICES, (), "The interview stays on this machine by default.", "g22://privacy"),
        Question("capability", "choice", False, CAPABILITY_CHOICES, (), "Local reading is available only for references you explicitly add.", "g22://capability"),
    )
    timestamp = now or _now()
    session = InterviewSession(
        session_id=session_id,
        project_id=project_id,
        gig_id=gig_id,
        proposal_id=None,
        revision=1,
        parent_revision=None,
        request_kind=request_kind,
        request_artifact=dict(request_artifact),
        request_sha256=request_sha256,
        state="questions_pending",
        round=1,
        max_rounds=max_rounds,
        references=references,
        questions=questions,
        privacy_choice=DEFAULT_PRIVACY,
        capability_choice=DEFAULT_CAPABILITY,
        effect_choice=DEFAULT_EFFECT,
        created_at=timestamp,
        updated_at=timestamp,
    )
    return _event(session, "session_created", actor={"kind": "gigai", "id": "g22"}, now=timestamp)


def attach_reference_choices(
    session: InterviewSession,
    references: tuple[ReferenceDecision, ...],
) -> InterviewSession:
    """Replace the initial path-entry question with explicit reference choices."""

    if session.references:
        raise ProposalInterviewError("reference choices are already attached")
    if not references or len({item.reference_id for item in references}) != len(references):
        raise ProposalInterviewError("at least one unique reference choice is required")
    updated_questions = tuple(
        replace(
            item,
            answer_type="multiselect",
            options=tuple(reference.reference_id for reference in references),
            rationale="Select the exact local references for this proposal.",
        )
        if item.question_id == "references"
        else item
        for item in session.questions
    )
    return replace(session, references=references, questions=updated_questions)


def answer_question(
    session: InterviewSession,
    question_id: str,
    value: object,
    *,
    now: str | None = None,
) -> InterviewSession:
    question = next((item for item in session.questions if item.question_id == question_id), None)
    if question is None:
        raise ProposalInterviewError(f"unknown question {question_id!r}")
    if session.state not in {"questions_pending", "clarification_required"} and not (
        session.state == "proposal_ready" and (not question.required or question_id == "scope")
    ):
        raise ProposalInterviewError(f"cannot answer in terminal state {session.state!r}")
    _validate_answer(question, value)
    timestamp = now or _now()
    answers = tuple(item for item in session.answers if item.question_id != question_id)
    answer = Answer(question_id, question.answer_type, value, timestamp)
    answers += (answer,)
    previous = next((item for item in session.answers if item.question_id == question_id), None)
    changed = previous is not None and previous.value != value
    selected = session.references
    privacy = session.privacy_choice
    capability = session.capability_choice
    effect = session.effect_choice
    if question_id == "references" and question.answer_type == "multiselect":
        selected_ids = tuple(value)  # type: ignore[arg-type]
        selected = tuple(
            replace(item, decision="selected" if item.reference_id in selected_ids else "excluded")
            for item in session.references
        )
    elif question_id == "privacy":
        privacy = str(value)
    elif question_id == "capability":
        capability = str(value)
    elif question_id == "effect":
        effect = str(value)
    required_ids = {item.question_id for item in session.questions if item.required}
    answered_ids = {item.question_id for item in answers}
    state = "proposal_ready" if required_ids.issubset(answered_ids) else "questions_pending"
    updated = replace(
        session,
        answers=answers,
        references=selected,
        privacy_choice=privacy,
        capability_choice=capability,
        effect_choice=effect,
        state=state,
        terminal_reason=None,
        approval=None,
        updated_at=timestamp,
        revision=session.revision + 1 if changed else session.revision,
        parent_revision=session.revision if changed else session.parent_revision,
    )
    return _event(
        updated,
        "revision_created" if changed else "answer_recorded",
        actor={"kind": "operator", "id": "local-user"},
        details={"question_id": question_id, "answer_sha256": _sha256(value)},
        now=timestamp,
    )


def request_clarification(
    session: InterviewSession,
    *,
    reason: str,
    now: str | None = None,
) -> InterviewSession:
    if session.state not in {"questions_pending", "clarification_required"}:
        raise ProposalInterviewError(f"cannot clarify in terminal state {session.state!r}")
    if not reason.strip() or "\0" in reason:
        raise ProposalInterviewError("clarification reason must be non-empty and NUL-free")
    timestamp = now or _now()
    if session.round >= session.max_rounds:
        blocked = replace(session, state="blocked", terminal_reason="question_round_cap_exhausted", updated_at=timestamp)
        return _event(
            blocked,
            "blocked",
            actor={"kind": "gigai", "id": "g22"},
            details={"reason_sha256": _sha256(reason)},
            now=timestamp,
        )
    question = Question(
        f"clarification-{session.round + 1}",
        "text",
        True,
        (),
        tuple(item.question_id for item in session.questions),
        reason,
        "g22://clarification",
    )
    clarified = replace(
        session,
        state="clarification_required",
        round=session.round + 1,
        questions=session.questions + (question,),
        updated_at=timestamp,
        revision=session.revision + 1,
        parent_revision=session.revision,
    )
    return _event(
        clarified,
        "clarification_requested",
        actor={"kind": "model", "id": "g22-questioner"},
        details={"reason_sha256": _sha256(reason)},
        now=timestamp,
    )


def add_questions(
    session: InterviewSession,
    questions: tuple[Question, ...],
    *,
    now: str | None = None,
) -> InterviewSession:
    """Add model-authored questions without granting them approval authority."""

    if session.state not in {"questions_pending", "clarification_required", "proposal_ready"}:
        raise ProposalInterviewError("cannot add questions to a terminal session")
    known = {item.question_id for item in session.questions}
    if not questions or any(item.question_id in known for item in questions):
        raise ProposalInterviewError("model question IDs must be new and non-empty")
    if any(set(item.depends_on) - known for item in questions):
        raise ProposalInterviewError("model question dependency is unknown")
    timestamp = now or _now()
    updated = replace(
        session,
        questions=session.questions + questions,
        state="questions_pending",
        approval=None,
        terminal_reason=None,
        revision=session.revision + 1,
        parent_revision=session.revision,
        updated_at=timestamp,
    )
    return _event(
        updated,
        "question_presented",
        actor={"kind": "model", "id": "g22-questioner"},
        details={"question_ids": [item.question_id for item in questions]},
        now=timestamp,
    )


def approve_session(
    session: InterviewSession,
    *,
    proposal_id: str,
    proposal_sha256: str,
    now: str | None = None,
) -> InterviewSession:
    if session.state != "proposal_ready":
        raise ProposalInterviewError("only a proposal_ready session can be approved")
    _id(proposal_id, "gp_")
    if not proposal_sha256.startswith("sha256:") or len(proposal_sha256) != 71:
        raise ProposalInterviewError("proposal digest is invalid")
    if session.privacy_choice not in PRIVACY_CHOICES:
        raise ProposalInterviewError("approval requires a privacy choice")
    if session.capability_choice not in CAPABILITY_CHOICES:
        raise ProposalInterviewError("approval requires a capability choice")
    if session.effect_choice not in EFFECT_CHOICES:
        raise ProposalInterviewError("approval effect is not allowed")
    timestamp = now or _now()
    approval = {
        "decision": "approved",
        "approved_at": timestamp,
        "approved_by": {"kind": "operator", "id": "local-user"},
        "proposal_sha256": proposal_sha256,
    }
    approved = replace(
        session,
        proposal_id=proposal_id,
        state="approved",
        approval=approval,
        terminal_reason="operator_approved",
        updated_at=timestamp,
    )
    return _event(
        approved,
        "approved",
        actor={"kind": "operator", "id": "local-user"},
        details={"proposal_sha256": proposal_sha256},
        now=timestamp,
    )


def request_revision(session: InterviewSession, *, now: str | None = None) -> InterviewSession:
    """Return a reviewed draft to answerable clarification without approval."""

    if session.state != "proposal_ready":
        raise ProposalInterviewError("only a reviewable proposal can be revised")
    timestamp = now or _now()
    revised = replace(
        session,
        state="questions_pending",
        approval=None,
        terminal_reason=None,
        revision=session.revision + 1,
        parent_revision=session.revision,
        updated_at=timestamp,
    )
    return _event(
        revised,
        "revision_requested",
        actor={"kind": "operator", "id": "local-user"},
        now=timestamp,
    )


def block_session(session: InterviewSession, reason: str, *, now: str | None = None) -> InterviewSession:
    if not reason.strip() or "\0" in reason:
        raise ProposalInterviewError("block reason must be non-empty and NUL-free")
    timestamp = now or _now()
    return _event(replace(session, state="blocked", terminal_reason=reason, updated_at=timestamp), "blocked", actor={"kind": "gigai", "id": "g22"}, now=timestamp)


def session_record(session: InterviewSession) -> dict[str, object]:
    """Render the schema-shaped snapshot without raw tokens or event payloads."""

    return {
        "schema_version": "1.0",
        "record_version": 1,
        "revision": session.revision,
        "parent_revision": session.parent_revision,
        "session_id": session.session_id,
        "project_id": session.project_id,
        "gig_id": session.gig_id,
        "proposal_id": session.proposal_id,
        "request": {
            "kind": session.request_kind,
            "artifact": dict(session.request_artifact),
            "content_sha256": session.request_sha256,
        },
        "state": session.state,
        "round": session.round,
        "max_rounds": session.max_rounds,
        "references": [
            {"reference_id": item.reference_id, "content_sha256": item.content_sha256, "decision": item.decision}
            for item in session.references
        ],
        "selected_reference_ids": list(session.selected_reference_ids),
        "questions": [
            {
                "question_id": item.question_id,
                "answer_type": item.answer_type,
                "required": item.required,
                "options": list(item.options),
                "depends_on": list(item.depends_on),
                "rationale": item.rationale,
                "provenance": item.provenance,
            }
            for item in session.questions
        ],
        "answers": [
            {"question_id": item.question_id, "answer_type": item.answer_type, "value": item.value, "answered_at": item.answered_at}
            for item in session.answers
        ],
        "boundary": {
            "privacy": session.privacy_choice or "local_only",
            "capability": session.capability_choice or "none",
            "effect": session.effect_choice or "read_local",
        },
        "events": list(session.events),
        "approval": session.approval,
        "terminal_reason": session.terminal_reason,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def session_from_record(payload: Mapping[str, object]) -> InterviewSession:
    """Rehydrate a validated workpad snapshot without trusting SQLite state."""

    try:
        references = tuple(
            ReferenceDecision(
                str(item["reference_id"]),
                str(item["content_sha256"]),
                str(item["decision"]),
            )
            for item in payload["references"]  # type: ignore[index]
        )
        questions = tuple(
            Question(
                str(item["question_id"]),
                str(item["answer_type"]),
                bool(item["required"]),
                tuple(str(value) for value in item["options"]),
                tuple(str(value) for value in item["depends_on"]),
                str(item["rationale"]),
                str(item["provenance"]),
            )
            for item in payload["questions"]  # type: ignore[index]
        )
        answers = tuple(
            Answer(
                str(item["question_id"]),
                str(item["answer_type"]),
                item["value"],
                str(item["answered_at"]),
            )
            for item in payload["answers"]  # type: ignore[index]
        )
        boundary = payload["boundary"]  # type: ignore[index]
        request = payload["request"]  # type: ignore[index]
        if not isinstance(boundary, Mapping) or not isinstance(request, Mapping):
            raise TypeError
        session = InterviewSession(
            session_id=_id(str(payload["session_id"]), "session_"),
            project_id=_id(str(payload["project_id"]), "project_"),
            gig_id=_id(str(payload["gig_id"]), "gig_"),
            proposal_id=(str(payload["proposal_id"]) if payload["proposal_id"] is not None else None),
            revision=int(payload["revision"]),
            parent_revision=(int(payload["parent_revision"]) if payload["parent_revision"] is not None else None),
            request_kind=str(request["kind"]),
            request_artifact=dict(request["artifact"]),
            request_sha256=str(request["content_sha256"]),
            state=str(payload["state"]),
            round=int(payload["round"]),
            max_rounds=int(payload["max_rounds"]),
            references=references,
            questions=questions,
            answers=answers,
            privacy_choice=str(boundary["privacy"]),
            capability_choice=str(boundary["capability"]),
            effect_choice=str(boundary["effect"]),
            events=tuple(dict(item) for item in payload["events"]),  # type: ignore[index]
            approval=(dict(payload["approval"]) if payload["approval"] is not None else None),
            terminal_reason=(str(payload["terminal_reason"]) if payload["terminal_reason"] is not None else None),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )
    except (KeyError, TypeError, ValueError, ProposalInterviewError) as exc:
        raise ProposalInterviewError("proposal-interview snapshot cannot be recovered") from exc
    if session.state not in STATES:
        raise ProposalInterviewError("proposal-interview snapshot has an invalid state")
    expected_selected = set(session.selected_reference_ids)
    observed_selected = set(payload.get("selected_reference_ids", ()))
    if expected_selected != observed_selected:
        raise ProposalInterviewError("proposal-interview reference selection is inconsistent")
    return session


def persist_trace(connection: sqlite3.Connection, session: InterviewSession) -> None:
    """Persist only ordered event identities and redacted state metadata."""

    connection.execute(
        "CREATE TABLE IF NOT EXISTS interview_events ("
        "session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL, "
        "state TEXT NOT NULL, payload_sha256 TEXT NOT NULL, occurred_at TEXT NOT NULL, "
        "PRIMARY KEY(session_id, sequence))"
    )
    if any(
        item.get("sequence") != index
        for index, item in enumerate(session.events, start=1)
    ):
        raise ProposalInterviewError("interview event sequence is not contiguous")
    existing = connection.execute(
        "SELECT sequence, event, state, payload_sha256, occurred_at FROM interview_events "
        "WHERE session_id = ? ORDER BY sequence",
        (session.session_id,),
    ).fetchall()
    if len(existing) > len(session.events):
        raise ProposalInterviewError("stale interview snapshot cannot truncate trace")
    for row, event in zip(existing, session.events):
        observed = (row[0], row[1], row[2], row[3], row[4])
        expected = (
            event["sequence"],
            event["event"],
            event["state"],
            event["payload_sha256"],
            event["occurred_at"],
        )
        if observed != expected:
            raise ProposalInterviewError("interview trace conflicts with snapshot")
    for event in session.events[len(existing) :]:
        connection.execute(
            "INSERT INTO interview_events "
            "(session_id, sequence, event, state, payload_sha256, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session.session_id, event["sequence"], event["event"], event["state"], event["payload_sha256"], event["occurred_at"]),
        )
    connection.commit()


def load_trace(connection: sqlite3.Connection, session_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT sequence, event, state, payload_sha256, occurred_at FROM interview_events "
        "WHERE session_id = ? ORDER BY sequence",
        (session_id,),
    ).fetchall()
    return [
        {"sequence": row[0], "event": row[1], "state": row[2], "payload_sha256": row[3], "occurred_at": row[4]}
        for row in rows
    ]


class InterviewHTTPServer:
    """A short-lived, token-bound server that can only bind to loopback."""

    def __init__(
        self,
        session: InterviewSession,
        *,
        connection: sqlite3.Connection | None = None,
        host: str = "127.0.0.1",
        on_session: Callable[[InterviewSession], None] | None = None,
        on_questions: Callable[[InterviewSession], InterviewSession] | None = None,
        on_reference_paths: Callable[
            [InterviewSession, tuple[str, ...]],
            tuple[InterviewSession, tuple[str, ...], Mapping[str, str]],
        ]
        | None = None,
        reference_labels: Mapping[str, str] | None = None,
        on_approval: Callable[[InterviewSession], InterviewSession] | None = None,
        on_build: Callable[[InterviewSession], InterviewSession] | None = None,
        on_revision: Callable[[InterviewSession], InterviewSession] | None = None,
        on_rejection: Callable[[InterviewSession], InterviewSession] | None = None,
        builder_review: Mapping[str, object] | None = None,
        builder_mode: bool = False,
        builder_ready: bool = False,
        lifetime_seconds: float = 600.0,
    ) -> None:
        if host != "127.0.0.1":
            raise ProposalInterviewError("G22 server must bind to loopback")
        if lifetime_seconds <= 0:
            raise ProposalInterviewError("G22 server lifetime must be positive")
        self.session = session
        self.connection = connection
        self.on_session = on_session
        self.on_questions = on_questions
        self.on_reference_paths = on_reference_paths
        self.reference_labels = dict(reference_labels or {})
        self.on_approval = on_approval
        self.on_build = on_build
        self.on_revision = on_revision
        self.on_rejection = on_rejection
        self.builder_review = builder_review if builder_review is not None else {}
        self.builder_mode = builder_mode
        self.builder_ready = builder_ready
        self.lifetime_seconds = lifetime_seconds
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.RLock()
        self._terminal = threading.Event()
        self._timer: threading.Timer | None = None
        self._closed = False
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _authorized(self, suffix: str) -> bool:
                return self.path == f"/session/{owner.token}{suffix}"

            def _json(self, status: int, payload: Mapping[str, object]) -> None:
                body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized(""):
                    self._json(404, {"error": "not_found"})
                    return
                with owner._lock:
                    snapshot = session_record(owner.session)
                    body = owner._render_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-GigAI-State", str(snapshot["state"]))
                self.send_header("X-GigAI-Loopback", "127.0.0.1")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized("/events"):
                    self._json(404, {"error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 65536:
                        raise ProposalInterviewError("event body size is invalid")
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ProposalInterviewError("event payload must be an object")
                    with owner._lock:
                        expected_sequence = len(owner.session.events) + 1
                        if payload.get("sequence") != expected_sequence:
                            raise ProposalInterviewError(
                                "stale or duplicate event sequence"
                            )
                        if payload.get("revision") != owner.session.revision:
                            raise ProposalInterviewError("stale interview revision")
                        apply_payload = payload
                        if (
                            payload.get("event") == "answer"
                            and payload.get("question_id") == "references"
                            and not owner.session.references
                        ):
                            value = payload.get("value")
                            if not isinstance(value, str):
                                raise ProposalInterviewError(
                                    "reference selection requires one path per line"
                                )
                            paths = tuple(
                                line.strip()
                                for line in value.splitlines()
                                if line.strip()
                            )
                            if owner.on_reference_paths is None:
                                raise ProposalInterviewError(
                                    "reference selection is not configured"
                                )
                            owner.session, selected_ids, labels = owner.on_reference_paths(
                                owner.session, paths
                            )
                            owner.reference_labels.update(labels)
                            apply_payload = dict(payload)
                            apply_payload["value"] = list(selected_ids)
                        next_session = owner._apply(apply_payload)
                        if (
                            payload.get("event") == "answer"
                            and owner.on_questions is not None
                            and (
                                owner.builder_mode
                                or payload.get("question_id") == "references"
                            )
                        ):
                            next_session = owner.on_questions(next_session)
                        if payload.get("event") == "build":
                            owner.builder_ready = True
                        elif payload.get("event") == "revise":
                            owner.builder_ready = False
                        owner.session = next_session
                        if payload.get("event") != "approve" and owner.on_session is not None:
                            owner.on_session(owner.session)
                        if owner.connection is not None:
                            persist_trace(owner.connection, owner.session)
                        snapshot = session_record(owner.session)
                        if snapshot["state"] in {"approved", "blocked"}:
                            owner._terminal.set()
                    self._json(200, {"state": snapshot["state"], "session_id": snapshot["session_id"]})
                except (ProposalInterviewError, json.JSONDecodeError, TypeError, ValueError, OSError, RuntimeError) as exc:
                    self._json(409, {"error": str(exc)})

        self._server = http.server.ThreadingHTTPServer((host, 0), Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self._server.server_address[0], int(self._server.server_address[1])

    @property
    def url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}/session/{self.token}"

    def start(self) -> "InterviewHTTPServer":
        if self._thread is not None or self._closed:
            raise ProposalInterviewError("interview server is already started")
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._timer = threading.Timer(self.lifetime_seconds, self.close)
        self._timer.daemon = True
        self._timer.start()
        return self

    def wait(self, timeout: float | None = None) -> InterviewSession:
        """Wait for approval/blocking or lifetime expiry, then return the snapshot."""

        self._terminal.wait(timeout)
        with self._lock:
            return self.session

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._terminal.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _render_html(self) -> bytes:
        session = self.session
        forms: list[str] = []
        endpoint = f"/session/{self.token}/events"
        titles = {
            "scope": "What should this Gig do?",
            "references": "Add local context (optional)",
            "effect": "What may this Gig change?",
            "privacy": "Where may information be used?",
            "capability": "What local capability is allowed?",
            "operator-confirmation": "Are these choices correct?",
        }
        explanations = {
            "read_local": "Read selected local files only; do not write to the target.",
            "write_workpad": "Write proposal artifacts to GigAI's private workpad only.",
            "local_only": "Keep this interview and its evidence on this machine.",
            "redact_before_share": "Redact selected content before any configured sharing.",
            "none": "Do not grant a local capability beyond the interview itself.",
            "local_read": "Permit reading the references you explicitly select.",
        }
        editable = session.state in {"questions_pending", "clarification_required"}
        answered = {item.question_id for item in session.answers}
        total_required = sum(1 for item in session.questions if item.required)
        completed_required = sum(1 for item in session.questions if item.required and item.question_id in answered)
        for question in session.questions:
            if question.question_id in {"effect", "privacy", "capability"}:
                continue
            title = html.escape(titles.get(question.question_id, question.question_id.replace("-", " ").capitalize()))
            rationale = html.escape(question.rationale)
            question_id = html.escape(question.question_id)
            required = "required" if question.required else ""
            disabled = "disabled" if not editable else ""
            if question.answer_type == "text":
                control = f"<input name='value' type='text' aria-label='{title}' {required} {disabled}>"
            elif question.answer_type == "confirmation":
                control = f"<label class='checkbox'><input name='value' type='checkbox' {disabled}> <span>Yes, continue</span></label>"
            elif question.answer_type == "multiselect":
                control = "".join(
                    f"<label class='checkbox'><input name='value' type='checkbox' value='{html.escape(option)}' {disabled}> <span>{html.escape(self.reference_labels.get(option, f'Reference {index}'))}</span></label>"
                    for index, option in enumerate(question.options, start=1)
                )
            else:
                options = "".join(
                    f"<option value='{html.escape(option)}'>{html.escape(option.replace('_', ' ').capitalize())}</option>"
                    for option in question.options
                )
                descriptions = "".join(
                    f"<li><strong>{html.escape(option.replace('_', ' ').capitalize())}</strong>: {html.escape(explanations[option])}</li>"
                    for option in question.options if option in explanations
                )
                control = f"<select name='value' aria-label='{title}' {disabled}>{options}</select>"
                if descriptions:
                    control += f"<ul class='help'>{descriptions}</ul>"
            card_class = "question optional-context" if question.question_id == "references" else "question"
            button_label = "Continue" if question.question_id == "scope" else "Save answer"
            forms.append(
                f"<form class='{card_class}' data-question-id='{question_id}' data-revision='{session.revision}' data-sequence='{len(session.events) + 1}' hx-post='{endpoint}'>"
                f"<div class='question-heading'><h2>{title}</h2><span class='required'>{'Required' if question.required else 'Optional'}</span></div>"
                f"<p class='rationale'>{rationale}</p>{control}"
                f"<button type='submit' {disabled}>{button_label}</button></form>"
            )
        if session.state == "proposal_ready" and self.builder_mode and not self.builder_ready:
            forms.append(
                f"<section class='approval'><h2>Ready to build the proposal</h2><p>Your Gig definition is complete. Build proposal asks the selected model to research the request and prepare a draft for review. Nothing is approved or run yet.</p>"
                f"<form class='build' data-revision='{session.revision}' data-sequence='{len(session.events) + 1}' hx-post='{endpoint}'><button class='primary' type='submit'>Build proposal</button></form></section>"
            )
        elif session.state == "proposal_ready" and self.builder_mode and self.builder_ready:
            summary = html.escape(str(self.builder_review.get("summary", "Draft summary is available in the workpad.")))
            assumptions = self.builder_review.get("assumptions", ())
            unresolved = self.builder_review.get("unresolved_questions", ())
            assumption_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in assumptions) or "<li>None recorded.</li>"
            unresolved_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in unresolved) or "<li>None recorded.</li>"
            forms.append(
                f"<section class='approval'><h2>Proposal draft ready</h2><p><strong>Summary:</strong> {summary}</p><p><strong>Assumptions</strong></p><ul>{assumption_html}</ul><p><strong>Unresolved questions</strong></p><ul>{unresolved_html}</ul><p>Review citations and boundaries in the workpad before deciding.</p>"
                f"<form class='review-action' data-event='revise' data-revision='{session.revision}' data-sequence='{len(session.events) + 1}' hx-post='{endpoint}'><button type='submit'>Revise answers</button></form>"
                f"<form class='review-action' data-event='reject' data-revision='{session.revision}' data-sequence='{len(session.events) + 1}' hx-post='{endpoint}'><button type='submit'>Reject draft</button></form>"
                f"<form class='approve' data-revision='{session.revision}' data-sequence='{len(session.events) + 1}' hx-post='{endpoint}'><button class='primary' type='submit'>Approve proposal</button></form></section>"
            )
        elif session.state == "proposal_ready":
            forms.append(
                f"<section class='approval'><h2>Proposal draft ready</h2><p>Review the model-facilitated Gig definition before approval. GigAI will write proposal artifacts to its private workpad, stay local-only, and read only references you explicitly add. Approval does not run work or modify the target repository.</p>"
                f"<form class='approve' data-revision='{session.revision}' data-sequence='{len(session.events) + 1}' hx-post='{endpoint}'><button class='primary' type='submit'>Approve proposal</button></form></section>"
            )
        body = (
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Define a Gig</title><style>"
            ":root{font:16px system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#172033;background:#f5f7fb}"
            "body{margin:0}.shell{max-width:760px;margin:0 auto;padding:32px 20px 64px}"
            "header{background:#172033;color:white;border-radius:16px;padding:24px;margin-bottom:20px}"
            "h1{font-size:1.65rem;margin:0 0 8px}h2{font-size:1rem;margin:0}p{line-height:1.5}"
            ".intro{color:#dbe4f5;margin:0}.status{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-top:18px;font-size:.9rem}"
            ".badge,.required{border-radius:999px;padding:4px 9px;font-size:.75rem;font-weight:700;background:#dbeafe;color:#1e40af}"
            ".required{background:#eef2ff;color:#4338ca;float:right}.question,.approval{background:white;border:1px solid #dbe2ef;border-radius:12px;padding:20px;margin:14px 0;box-shadow:0 2px 8px #1720330d}.optional-context{border-style:dashed}"
            ".question-heading{display:flex;justify-content:space-between;gap:12px}.rationale{color:#526079;margin:8px 0 14px}.help{color:#526079;font-size:.88rem;margin:10px 0 0;padding-left:20px}"
            "input[type=text],select{box-sizing:border-box;width:100%;border:1px solid #aab6cc;border-radius:7px;padding:10px;font:inherit;background:white}"
            ".checkbox{display:block;margin:10px 0;color:#26334d}.checkbox input{margin-right:8px}"
            "button{border:1px solid #9aa8bf;border-radius:7px;background:#f8fafc;padding:9px 14px;font:inherit;font-weight:600;cursor:pointer;margin-top:14px}button:hover{background:#e8eef8}button:disabled{cursor:not-allowed;opacity:.55}button.primary{background:#2563eb;color:white;border-color:#2563eb}"
            ".approval{border-color:#93c5fd;background:#eff6ff}.approval p{color:#274060}.footer{color:#61708a;font-size:.82rem;margin-top:18px}"
            "</style><main class='shell'><header><h1>Define a Gig</h1>"
            "<p class='intro'>Describe the Gig you want to create. GigAI may ask a follow-up only when it needs more context. Nothing runs and the target is not modified during this interview.</p>"
            f"<div class='status'><span data-state='{html.escape(session.state)}'>State: {html.escape(session.state.replace('_', ' ').capitalize())}</span><span class='badge'>{completed_required}/{total_required} required answers</span></div></header>"
            + "".join(forms)
            + "<p class='footer'>This local interview expires automatically. You can stop before approval at any time.</p></main><script>"
            "for (const form of document.querySelectorAll('form.question')) {"
            "form.addEventListener('submit', async (event) => {event.preventDefault();"
            "const values=[...form.querySelectorAll('[name=value]')];"
            "let value; const first=values[0];"
            "if(first.type==='checkbox' && values.length===1){value=first.checked;}"
            "else if(first.type==='checkbox'){value=values.filter(x=>x.checked).map(x=>x.value);}"
            "else if(first.tagName==='SELECT'){value=first.value;} else {value=first.value;}"
            "const response=await fetch(form.getAttribute('hx-post'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'answer',question_id:form.dataset.questionId,value,revision:Number(form.dataset.revision),sequence:Number(form.dataset.sequence)})});"
            "if(!response.ok){alert((await response.json()).error);} else {location.reload();}});}"
            "const build=document.querySelector('form.build'); if(build){build.addEventListener('submit', async (event)=>{event.preventDefault();"
            "const response=await fetch(build.getAttribute('hx-post'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'build',revision:Number(build.dataset.revision),sequence:Number(build.dataset.sequence)})});"
            "if(!response.ok){alert((await response.json()).error);} else {location.reload();}});}"
            "for (const form of document.querySelectorAll('form.review-action')) {form.addEventListener('submit', async (event)=>{event.preventDefault();"
            "const response=await fetch(form.getAttribute('hx-post'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:form.dataset.event,revision:Number(form.dataset.revision),sequence:Number(form.dataset.sequence)})});"
            "if(!response.ok){alert((await response.json()).error);} else {location.reload();}});}"
            "const approval=document.querySelector('form.approve'); if(approval){approval.addEventListener('submit', async (event)=>{event.preventDefault();"
            "const response=await fetch(approval.getAttribute('hx-post'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'approve',revision:Number(approval.dataset.revision),sequence:Number(approval.dataset.sequence)})});"
            "if(!response.ok){alert((await response.json()).error);} else {location.reload();}});}"
            "</script>"
        ).encode("utf-8")
        return body

    def _apply(self, payload: Mapping[str, object]) -> InterviewSession:
        event = payload.get("event")
        if event == "answer":
            question_id = payload.get("question_id")
            if not isinstance(question_id, str) or "value" not in payload:
                raise ProposalInterviewError("answer requires question_id and value")
            return answer_question(self.session, question_id, payload["value"])
        if event == "clarification":
            reason = payload.get("reason")
            if not isinstance(reason, str):
                raise ProposalInterviewError("clarification requires a reason")
            return request_clarification(self.session, reason=reason)
        if event == "approve":
            if self.on_approval is None:
                raise ProposalInterviewError("operator approval is not configured")
            return self.on_approval(self.session)
        if event == "build":
            if not self.builder_mode or self.on_build is None:
                raise ProposalInterviewError("proposal build is not configured")
            if self.session.state != "proposal_ready":
                raise ProposalInterviewError("proposal build requires a complete Gig definition")
            return self.on_build(self.session)
        if event == "revise":
            if not self.builder_mode or self.on_revision is None:
                raise ProposalInterviewError("proposal revision is not configured")
            return self.on_revision(self.session)
        if event == "reject":
            if not self.builder_mode or self.on_rejection is None:
                raise ProposalInterviewError("proposal rejection is not configured")
            return self.on_rejection(self.session)
        raise ProposalInterviewError("unsupported interview event")


__all__ = [
    "Answer",
    "CAPABILITY_CHOICES",
    "EFFECT_CHOICES",
    "InterviewHTTPServer",
    "InterviewSession",
    "PRIVACY_CHOICES",
    "ProposalInterviewError",
    "Question",
    "ReferenceDecision",
    "STATES",
    "answer_question",
    "add_questions",
    "approve_session",
    "block_session",
    "build_session",
    "load_trace",
    "persist_trace",
    "request_clarification",
    "request_revision",
    "session_record",
    "session_from_record",
]


def _event(
    session: InterviewSession,
    event: str,
    *,
    actor: Mapping[str, object],
    details: Mapping[str, object] | None = None,
    now: str,
) -> InterviewSession:
    sequence = len(session.events) + 1
    payload = {
        "event": event,
        "state": session.state,
        "round": session.round,
        "actor": dict(actor),
    }
    if details:
        payload.update(details)
    item = {
        "sequence": sequence,
        "event": event,
        "state": session.state,
        "actor": dict(actor),
        "payload_sha256": _sha256(payload),
        "occurred_at": now,
    }
    return replace(session, events=session.events + (item,), updated_at=now)


def _validate_answer(question: Question, value: object) -> None:
    if question.answer_type == "text":
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ProposalInterviewError("text answer must be non-empty and NUL-free")
    elif question.answer_type == "choice":
        if not isinstance(value, str) or value not in question.options:
            raise ProposalInterviewError("choice answer is not allowed")
    elif question.answer_type == "multiselect":
        if (
            not isinstance(value, (list, tuple))
            or not value
            or len(set(value)) != len(value)
            or any(not isinstance(item, str) or item not in question.options for item in value)
        ):
            raise ProposalInterviewError("reference selection must contain unique allowed IDs")
    elif question.answer_type == "confirmation" and type(value) is not bool:
        raise ProposalInterviewError("confirmation answer must be boolean")
