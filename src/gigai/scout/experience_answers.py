"""P3 (v0.1.9): read/record answered ``experience_qa`` questions for the Q&A loop.

Operator answer 3: a question is written to ``experience_qa`` ONLY when
answered (never at assess time); the pending list the UI/CLI shows is
derived from stored quick-assess results minus the answered ids, never a
separately churned record. Operator answer 4: at 32 answered questions per
record (the schema's own ``experience.questions`` bound, C10), roll over to
a NEW record automatically rather than refuse.

``question_id`` is cross-posting by construction (assess.md rule 2: "the
same real-world fact asked the same way across different postings should
reuse the same question_id") -- P3 additionally runs every id through
``question_ids.normalize_question_id`` (both on write, in ``record_answer``,
and on read, in ``read_answers``) so an id that drifted across two model
calls for the SAME fact (S29 r1) still joins to one answer.

Provenance and validation follow ``answer_cli.save_answer_command`` exactly
(the plan's OWNED-files line): non-empty answer text, ``<=16,000`` chars,
``provenance = {"kind": "user_reported", "source_refs": []}``, actor
``{"kind": "operator", "id": "local-user"}``, origin ``"user_reported"``.
Unlike ``scout-answer save``, this module never requires the question to
already exist in a committed revision -- ``record_answer`` upserts: it
appends the question to the newest record with room, or creates a fresh
record (or a rollover record once the newest is full), so the operator can
pre-answer a question no assessment has asked yet (plan edge: "a
question_id not seen in any result is still accepted").
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ..native_records import NativeRecordResult, create_native_record, list_native_records, read_native_record, update_native_record
from ..private_records import PrivateRecordError
from .question_ids import normalize_question_id

#: The schema's own cap on ``experience.questions`` per record (C10);
#: rollover creates a new record rather than raising once a record is full.
_MAX_QUESTIONS_PER_RECORD = 32

_ACTOR = {"kind": "operator", "id": "local-user"}
_ORIGIN = "user_reported"
_PROVENANCE = {"kind": "user_reported", "source_refs": []}
_SAVED_DEFAULT_SCOPE = {"mode": "saved_default", "task_context_id": None, "base": None}


@dataclass(frozen=True)
class PriorAnswer:
    """One answered question, keyed by its NORMALIZED ``question_id``."""

    question_id: str
    prompt: str
    answer: str
    record_id: str
    revision_id: str


def _validate_answer_text(answer: str) -> str:
    if not answer.strip() or len(answer) > 16_000:
        raise PrivateRecordError("answer_invalid", "answer text is empty or exceeds the bound")
    return answer


def _empty_experience_content() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "experience_qa",
        "scope": dict(_SAVED_DEFAULT_SCOPE),
        "payload": {"questions": []},
    }


def _operation_key(kind: str, record_id: str, question_id: str, digest: str) -> str:
    """Deterministic per-(record, question, answer) key so a retried call
    never double-applies the same answer as a second native revision."""

    return f"scout-experience-answer:{kind}:{record_id}:{question_id}:{digest}"[:160]


def _answer_digest(question_id: str, answer: str) -> str:
    return digest_imported_bytes(canonical_json_bytes({"question_id": question_id, "answer": answer})).removeprefix("sha256:")


def _read_content(*, home_root: Path, requested_target: Path | None, gig_id: str | None, record_id: str, revision_id: str) -> dict[str, object] | None:
    full = read_native_record(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id,
        record_id=record_id, revision_id=revision_id, content=True,
    )
    raw = full.get("content")
    if not isinstance(raw, bytes):
        return None
    try:
        value = parse_json_bytes(raw)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def read_answers(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None) -> dict[str, PriorAnswer]:
    """Every ANSWERED question across this gig's ``experience_qa`` records,
    keyed by normalized ``question_id`` (a record's own questions never
    repeat an id -- ``_semantic_content`` enforces that -- but two different
    records can each answer the same real-world fact under a differently
    worded raw id, and normalization is what joins them here; the record
    read LAST in listing order wins a collision, which is stable but
    arbitrary -- a genuine re-answer instead updates the SAME record via
    ``record_answer``, so a same-fact collision across two records is not
    the expected steady state)."""

    answers: dict[str, PriorAnswer] = {}
    for row in list_native_records(home_root=home_root, requested_target=requested_target, gig_id=gig_id):
        if row["kind"] != "experience_qa":
            continue
        record_id = str(row["record_id"])
        revision_id = str(row["revision_id"])
        value = _read_content(home_root=home_root, requested_target=requested_target, gig_id=gig_id, record_id=record_id, revision_id=revision_id)
        if value is None:
            continue
        payload = value.get("payload")
        if not isinstance(payload, dict):
            continue
        for question in payload.get("questions", []):
            if not isinstance(question, dict) or question.get("state") != "answered":
                continue
            answer = question.get("answer")
            if not isinstance(answer, str):
                continue
            normalized = normalize_question_id(str(question.get("question_id", "")))
            answers[normalized] = PriorAnswer(
                question_id=normalized,
                prompt=str(question.get("prompt", "")),
                answer=answer,
                record_id=record_id,
                revision_id=revision_id,
            )
    return answers


def _newest_experience_record(*, home_root: Path, requested_target: Path | None, gig_id: str | None) -> tuple[str, str, dict[str, object]] | None:
    """``(record_id, revision_id, content)`` of the newest active
    ``experience_qa`` record with ``saved_default`` scope, or ``None``."""

    rows = [
        row
        for row in list_native_records(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
        if row["kind"] == "experience_qa" and row.get("scope") == _SAVED_DEFAULT_SCOPE
    ]
    if not rows:
        return None
    newest = max(rows, key=lambda row: str(row["created_at"]))
    record_id, revision_id = str(newest["record_id"]), str(newest["revision_id"])
    value = _read_content(home_root=home_root, requested_target=requested_target, gig_id=gig_id, record_id=record_id, revision_id=revision_id)
    if value is None:
        return None
    return record_id, revision_id, value


def record_answer(
    *,
    home_root: Path,
    requested_target: Path | None,
    question_id: str,
    prompt: str,
    answer: str,
    gig_id: str | None = None,
) -> NativeRecordResult:
    """Upsert one answered question into ``experience_qa``.

    Appends to the newest record with room (< 32 questions); rolls over to a
    fresh record once the newest one is full (operator answer 4). If the
    (normalized) question_id already exists ANYWHERE, its answer is
    overwritten in place via a revision to that same record instead of being
    duplicated into a second one -- a re-answer stays a single fact, not two.
    """

    _validate_answer_text(answer)
    normalized_id = normalize_question_id(question_id)

    for row in list_native_records(home_root=home_root, requested_target=requested_target, gig_id=gig_id):
        if row["kind"] != "experience_qa":
            continue
        record_id = str(row["record_id"])
        revision_id = str(row["revision_id"])
        value = _read_content(home_root=home_root, requested_target=requested_target, gig_id=gig_id, record_id=record_id, revision_id=revision_id)
        if value is None:
            continue
        payload = value.get("payload")
        if not isinstance(payload, dict):
            continue
        questions = payload.get("questions", [])
        if not isinstance(questions, list):
            continue
        match = next(
            (item for item in questions if isinstance(item, dict) and normalize_question_id(str(item.get("question_id", ""))) == normalized_id),
            None,
        )
        if match is None:
            continue

        updated = copy.deepcopy(value)
        updated_questions = updated["payload"]["questions"]  # type: ignore[index]
        assert isinstance(updated_questions, list)
        target_question = next(item for item in updated_questions if normalize_question_id(str(item.get("question_id", ""))) == normalized_id)
        target_question["state"] = "answered"
        target_question["answer"] = answer
        target_question["provenance"] = dict(_PROVENANCE)
        return update_native_record(
            home_root=home_root, requested_target=requested_target, gig_id=gig_id,
            record_id=record_id, parent_revision=revision_id, content=updated,
            actor=_ACTOR, origin=_ORIGIN,
            operation_key=_operation_key("update", record_id, normalized_id, _answer_digest(normalized_id, answer)),
        )

    newest = _newest_experience_record(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    new_question = {
        "question_id": normalized_id,
        "prompt": prompt.strip() or normalized_id,
        "state": "answered",
        "answer": answer,
        "provenance": dict(_PROVENANCE),
    }
    digest = _answer_digest(normalized_id, answer)

    if newest is not None:
        record_id, revision_id, content = newest
        payload = content.get("payload")
        questions = payload.get("questions") if isinstance(payload, dict) else None
        if isinstance(questions, list) and len(questions) < _MAX_QUESTIONS_PER_RECORD:
            updated = copy.deepcopy(content)
            updated_questions = updated["payload"]["questions"]  # type: ignore[index]
            assert isinstance(updated_questions, list)
            updated_questions.append(new_question)
            return update_native_record(
                home_root=home_root, requested_target=requested_target, gig_id=gig_id,
                record_id=record_id, parent_revision=revision_id, content=updated,
                actor=_ACTOR, origin=_ORIGIN,
                operation_key=_operation_key("append", record_id, normalized_id, digest),
            )

    # No experience_qa record yet, or the newest one is full: create a new
    # record (a rollover when one already existed -- operator answer 4).
    fresh = _empty_experience_content()
    fresh_payload = fresh["payload"]  # type: ignore[index]
    assert isinstance(fresh_payload, dict)
    fresh_payload["questions"] = [new_question]
    return create_native_record(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id,
        content=fresh, actor=_ACTOR, origin=_ORIGIN,
        operation_key=_operation_key("create", "new", normalized_id, digest),
    )


__all__ = ["PriorAnswer", "read_answers", "record_answer"]
