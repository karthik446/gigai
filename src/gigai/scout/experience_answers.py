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

Terra review, P2: ``record_answer`` also validates the NORMALIZED id against
the ``experience_question.question_id`` contract
(``^[A-Za-z0-9._:-]{1,128}$``) BEFORE any write is attempted, so an id like
``cloud:gcp/invalid`` fails fast as ``answer_invalid`` (422 at the API route,
the CLI's normal error shape) instead of reaching native-record schema
validation and surfacing as a generic conflict. Terra review, P1: writes are
retry-safe under the journal writer -- see ``record_answer``'s own docstring.

0110-034 / 0.1.10.7 C (``story_bank.py``): the bank on top of these records.
``experience_qa`` has no field for who wrote an answer, its dates or the jobs
that used it (the schema is closed), so ``story_bank.py`` keeps those and
passes an ``AnswerScope`` to ``record_answer``: the one record that already
holds THE answer to the question (if any) and the records it may append to.
In 0.1.10.5 a scope was one profile's records; since 0.1.10.7 answers are the
user's and the scope is every ``experience_qa`` record of the gig
(``experience_record_ids``). Without a scope every call behaves exactly as
before. ``list_answer_rows`` is the unjoined read the bank is a view over;
``remove_answer`` is the bank's delete (a new revision with the question
``not_applicable``: the journal keeps the older revision, the answer is never
read or reused again).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ..native_records import NativeRecordResult, create_native_record, list_native_records, read_native_record, update_native_record
from ..private_records import PrivateRecordError
from .question_ids import is_valid_question_id, normalize_question_id

#: The schema's own cap on ``experience.questions`` per record (C10);
#: rollover creates a new record rather than raising once a record is full.
_MAX_QUESTIONS_PER_RECORD = 32

#: Terra review P1: ``update_native_record``/``create_native_record`` snapshot
#: (record selection, duplicate detection, rollover choice) and publish
#: (``native_records._publish``) as two separate journal-writer transactions
#: -- there is no single-transaction native_records API that spans both. Two
#: concurrent answers can each select the same parent revision, then race:
#: one commits, the other's ``update_native_record`` raises ``stale_parent``
#: (``native_records.py``'s own parent-revision check). Bounded retry: catch
#: it, re-read the current state from scratch (a fresh snapshot re-derives
#: duplicate detection AND the rollover choice, since either can change
#: between the stale read and the retry), and republish. 3 attempts is enough
#: to absorb ordinary lock contention between a small number of concurrent
#: answers without masking a genuinely stuck writer.
_MAX_STALE_PARENT_RETRIES = 3

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
    # 0110-034: when the record's current revision was written (the newest
    # date known for an answer saved before the story bank kept its own).
    recorded_at: str = ""


@dataclass(frozen=True)
class AnswerScope:
    """Which records one profile's answer may touch (0110-034, ``story_bank.py``).

    ``existing_record``: the record that already holds this profile's answer
    to the question, updated in place; ``None`` when the profile has not
    answered it. ``append_records``: the records the profile owns, the newest
    with room takes a new question; none with room means a fresh record.
    ``owner``: the profile id, part of a fresh record's operation key so two
    profiles giving the same answer never share one record.
    """

    existing_record: str | None
    append_records: frozenset[str]
    owner: str = ""


def _validate_answer_text(answer: str) -> str:
    if not answer.strip() or len(answer) > 16_000:
        raise PrivateRecordError("answer_invalid", "answer text is empty or exceeds the bound")
    return answer


def _validate_question_id_contract(normalized_id: str) -> None:
    """Terra review P2: validate the NORMALIZED id against the
    ``experience_question.question_id`` contract (``^[A-Za-z0-9._:-]{1,128}$``)
    BEFORE any write is attempted -- every write surface (this module's
    ``record_answer``, the ``POST /api/answers`` route, and the ``scout
    answer`` CLI) shares this one check so an invalid id fails as a typed
    ``answer_invalid`` (422 at the API, the CLI's normal error shape) instead
    of reaching native-record schema validation and surfacing as a generic
    conflict (the review's concrete example: ``cloud:gcp/invalid``)."""

    if not is_valid_question_id(normalized_id):
        raise PrivateRecordError(
            "answer_invalid",
            "question_id must match ^[A-Za-z0-9._:-]{1,128}$ after normalization",
        )


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
    return _parsed_content(full.get("content"))


def _parsed_content(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, bytes):
        return None
    try:
        value = parse_json_bytes(raw)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def list_answer_rows(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None) -> list[PriorAnswer]:
    """Every ANSWERED question of every ``experience_qa`` record, in listing
    order, NOT joined by id: two records may each answer the same normalized
    ``question_id`` (two profiles, 0110-034). One committed read."""

    rows: list[PriorAnswer] = []
    # One committed read for the listing AND every record's content: a
    # separate read per record would repeat the whole snapshot each time.
    for row in list_native_records(home_root=home_root, requested_target=requested_target, gig_id=gig_id, content=True):
        if row["kind"] != "experience_qa":
            continue
        record_id = str(row["record_id"])
        revision_id = str(row["revision_id"])
        value = _parsed_content(row.get("content"))
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
            rows.append(
                PriorAnswer(
                    question_id=normalize_question_id(str(question.get("question_id", ""))),
                    prompt=str(question.get("prompt", "")),
                    answer=answer,
                    record_id=record_id,
                    revision_id=revision_id,
                    recorded_at=str(row.get("created_at", "")),
                )
            )
    return rows


def experience_record_ids(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None) -> frozenset[str]:
    """Every ``experience_qa`` record of the gig (0.1.10.7: the user-level answers append to any of them)."""

    return frozenset(
        str(row["record_id"])
        for row in list_native_records(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
        if row["kind"] == "experience_qa"
    )


def read_answers(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None) -> dict[str, PriorAnswer]:
    """Every ANSWERED question across this gig's ``experience_qa`` records,
    keyed by normalized ``question_id`` (a record's own questions never
    repeat an id -- ``_semantic_content`` enforces that -- but two different
    records can each answer the same real-world fact under a differently
    worded raw id, and normalization is what joins them here; the record
    read LAST in listing order wins a collision, which is stable but
    arbitrary -- a genuine re-answer instead updates the SAME record via
    ``record_answer``, so a same-fact collision across two records is not
    the expected steady state).

    Gig-wide and unjoined with the bank's own file. What an assessment or
    the tailoring reads is ``story_bank.read_bank`` / ``answers_for_reuse``."""

    return {
        item.question_id: item
        for item in list_answer_rows(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    }


def _newest_experience_record(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None, among: frozenset[str] | None = None
) -> tuple[str, str, dict[str, object]] | None:
    """``(record_id, revision_id, content)`` of the newest active
    ``experience_qa`` record with ``saved_default`` scope, or ``None``.
    ``among`` (0110-034) narrows to those record ids."""

    rows = [
        row
        for row in list_native_records(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
        if row["kind"] == "experience_qa"
        and row.get("scope") == _SAVED_DEFAULT_SCOPE
        and (among is None or str(row["record_id"]) in among)
    ]
    if not rows:
        return None
    newest = max(rows, key=lambda row: str(row["created_at"]))
    record_id, revision_id = str(newest["record_id"]), str(newest["revision_id"])
    value = _read_content(home_root=home_root, requested_target=requested_target, gig_id=gig_id, record_id=record_id, revision_id=revision_id)
    if value is None:
        return None
    return record_id, revision_id, value


def _find_existing_question(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None, normalized_id: str, among: frozenset[str] | None = None
) -> tuple[str, str, dict[str, object]] | None:
    """``(record_id, revision_id, content)`` of the record that already
    answers ``normalized_id``, if any -- a fresh read, never cached across a
    retry (see ``record_answer``). ``among`` (0110-034) narrows to those
    record ids."""

    for row in list_native_records(home_root=home_root, requested_target=requested_target, gig_id=gig_id):
        if row["kind"] != "experience_qa":
            continue
        record_id = str(row["record_id"])
        if among is not None and record_id not in among:
            continue
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
        if match is not None:
            return record_id, revision_id, value
    return None


def _attempt_record_answer(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str | None,
    normalized_id: str,
    prompt: str,
    answer: str,
    scope: AnswerScope | None = None,
) -> NativeRecordResult:
    """One attempt: re-derive record selection, duplicate detection, AND the
    capacity/rollover choice from a FRESH read, then publish. Every input to
    the publish call is stable across retries of the SAME logical answer
    (``normalized_id`` + ``answer`` text) except the record/parent revision
    this attempt just re-read -- so a retry after ``stale_parent`` re-decides
    append-vs-rollover-vs-update against current state rather than replaying
    a decision made against a snapshot that already lost the race."""

    digest = _answer_digest(normalized_id, answer)

    existing = _find_existing_question(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id, normalized_id=normalized_id,
        # 0110-034: the profile's answered question, else the same id in one
        # of its own records in any state (a deleted answer given again is
        # the same question, never a second one in the record).
        among=None if scope is None else (frozenset({scope.existing_record}) if scope.existing_record else scope.append_records),
    )
    if existing is not None:
        record_id, revision_id, value = existing
        payload = value.get("payload")
        current = next(
            (
                item
                for item in payload.get("questions", [])  # type: ignore[union-attr]
                if isinstance(item, dict) and normalize_question_id(str(item.get("question_id", ""))) == normalized_id
            ),
            None,
        )
        if isinstance(current, dict) and current.get("state") == "answered" and current.get("answer") == answer:
            # Already exactly this answer: an identical retried call (e.g.
            # after a client-visible timeout/ambiguous response whose actual
            # write DID commit) is a true no-op, not a fresh update. Return
            # the already-committed record/revision rather than publishing a
            # redundant revision or risking a stale_parent/payload-mismatch
            # against a parent_revision this attempt did not originate from.
            if scope is None or current.get("prompt") == (prompt.strip() or current.get("prompt")):
                return NativeRecordResult(record_id, revision_id, False, "active", None, None)
        updated = copy.deepcopy(value)
        updated_questions = updated["payload"]["questions"]  # type: ignore[index]
        assert isinstance(updated_questions, list)
        target_question = next(item for item in updated_questions if normalize_question_id(str(item.get("question_id", ""))) == normalized_id)
        target_question["state"] = "answered"
        target_question["answer"] = answer
        target_question["provenance"] = dict(_PROVENANCE)
        if scope is not None and prompt.strip():
            # 0110-034: the bank keeps the question's own words; an answer
            # saved before it only had the id as its prompt.
            target_question["prompt"] = prompt.strip()
        if scope is not None:
            # 0110-034: keyed by the parent too. An edit back to an earlier
            # answer (A, B, A) is a new revision, not a replay of the first
            # one; an identical retry never gets here (the no-op above).
            digest = _answer_digest(normalized_id, "\x00".join((answer, prompt.strip(), revision_id)))
        return update_native_record(
            home_root=home_root, requested_target=requested_target, gig_id=gig_id,
            record_id=record_id, parent_revision=revision_id, content=updated,
            actor=_ACTOR, origin=_ORIGIN,
            operation_key=_operation_key("update", record_id, normalized_id, digest),
        )

    new_question = {
        "question_id": normalized_id,
        "prompt": prompt.strip() or normalized_id,
        "state": "answered",
        "answer": answer,
        "provenance": dict(_PROVENANCE),
    }

    newest = _newest_experience_record(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id,
        among=None if scope is None else scope.append_records,
    )
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
        # 0110-034: a scoped (per-profile) create is keyed by what the profile
        # already owns, so two profiles giving the same answer to the same
        # question each get their own record.
        operation_key=_operation_key(
            "create",
            "new" if scope is None else _answer_digest("scope", "|".join((scope.owner, *sorted(scope.append_records))))[:24],
            normalized_id,
            digest,
        ),
    )


def record_answer(
    *,
    home_root: Path,
    requested_target: Path | None,
    question_id: str,
    prompt: str,
    answer: str,
    gig_id: str | None = None,
    scope: AnswerScope | None = None,
) -> NativeRecordResult:
    """Upsert one answered question into ``experience_qa``.

    ``scope`` (0110-034, ``story_bank.save_answer``): the records of ONE
    profile. Given, the question is looked up and appended only there, and a
    non-empty ``prompt`` replaces the stored one; ``None`` keeps the gig-wide
    behaviour below.

    Appends to the newest record with room (< 32 questions); rolls over to a
    fresh record once the newest one is full (operator answer 4). If the
    (normalized) question_id already exists ANYWHERE, its answer is
    overwritten in place via a revision to that same record instead of being
    duplicated into a second one -- a re-answer stays a single fact, not two.

    Retry-safe under the journal writer (Terra review P1): record selection,
    duplicate detection, and the capacity/rollover choice all happen inside
    ``_attempt_record_answer``'s single fresh read, immediately before the
    publish call that reads (and checks) the current parent revision itself.
    ``native_records`` has no API that spans both a snapshot and a publish in
    one transaction, so two concurrent answers CAN still both select the same
    parent revision and race at publish time; the loser's ``update_native_record``
    raises ``PrivateRecordError("stale_parent", ...)`` rather than losing an
    overwrite. Caught here: re-read from scratch (record selection, duplicate
    detection, and rollover choice can all differ after the winner committed)
    and retry, bounded at ``_MAX_STALE_PARENT_RETRIES``. The operation_key
    passed to publish is built from ``(normalized_id, answer digest)`` only --
    stable across every retry of one logical answer, so an identical retried
    call (e.g. after a client-visible timeout/ambiguous response) that lands
    on the SAME record+parent a second time hits ``_existing_receipt``'s
    dedup in ``native_records._publish`` and returns the already-committed
    result instead of erroring or duplicating the fact.
    """

    _validate_answer_text(answer)
    normalized_id = normalize_question_id(question_id)
    _validate_question_id_contract(normalized_id)

    attempts = 0
    while True:
        try:
            return _attempt_record_answer(
                home_root=home_root, requested_target=requested_target, gig_id=gig_id,
                normalized_id=normalized_id, prompt=prompt, answer=answer, scope=scope,
            )
        except PrivateRecordError as exc:
            attempts += 1
            if exc.code != "stale_parent" or attempts >= _MAX_STALE_PARENT_RETRIES:
                raise


def remove_answer(
    *, home_root: Path, requested_target: Path | None, record_id: str, question_id: str, gig_id: str | None = None
) -> NativeRecordResult | None:
    """Take one answered question out of use (0110-034, the story bank's delete).

    Writes a new revision of ``record_id`` with the question ``not_applicable``
    and no answer text: ``list_answer_rows``/``read_answers`` only read
    ``answered`` questions, so it is never listed, offered or sent again. The
    journal keeps the older revision (records are append-only). ``None`` when
    the record holds no answered question with that id. Retried on
    ``stale_parent`` like ``record_answer``.
    """

    normalized_id = normalize_question_id(question_id)
    attempts = 0
    while True:
        existing = _find_existing_question(
            home_root=home_root, requested_target=requested_target, gig_id=gig_id,
            normalized_id=normalized_id, among=frozenset({record_id}),
        )
        if existing is None:
            return None
        _record_id, revision_id, value = existing
        updated = copy.deepcopy(value)
        questions = updated["payload"]["questions"]  # type: ignore[index]
        assert isinstance(questions, list)
        target_question = next(item for item in questions if normalize_question_id(str(item.get("question_id", ""))) == normalized_id)
        if target_question.get("state") != "answered":
            return None
        target_question["state"] = "not_applicable"
        target_question["answer"] = None
        target_question["provenance"] = dict(_PROVENANCE)
        try:
            return update_native_record(
                home_root=home_root, requested_target=requested_target, gig_id=gig_id,
                record_id=record_id, parent_revision=revision_id, content=updated,
                actor=_ACTOR, origin=_ORIGIN,
                operation_key=_operation_key("remove", record_id, normalized_id, revision_id.removeprefix("revision_")[:40]),
            )
        except PrivateRecordError as exc:
            attempts += 1
            if exc.code != "stale_parent" or attempts >= _MAX_STALE_PARENT_RETRIES:
                raise


__all__ = ["AnswerScope", "PriorAnswer", "experience_record_ids", "list_answer_rows", "read_answers", "record_answer", "remove_answer"]
