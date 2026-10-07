"""0.1.11 N3 (SPEC section 2): the suggestion record of one job, the final-selection check and the stale list.

THE RECORD (2.1).  One file per (profile, job), beside the other per-job
stores: ``<home>/scout/<project>/suggestions/<profile_id>/<sha256(job
identity)>.json``, schema ``scout-job-suggestions:1``, a plain file written
atomically under its folder's lock.  It holds what the job resume's own record
(``TailorResponse.selection``: Picked / Left out, what was cut) has no place
for: what the assessment was made with (``basis``), the gate, each requirement
row reduced to ids with where its support is printed, the model's raw pick with
what validation found and what code added, the conflicts, and the suggestions.
Ids, codes, the assessment's own suggestion sentences and posting phrases of at
most 60 characters: never a resume line's text, never an answer's text, never
contact data.

A NEW ASSESSMENT OF THE SAME JOB (``merged``) replaces ``basis``, ``gate``,
``requirements`` and the assessment's own suggestions that are still open.  It
keeps every suggestion that is done or dismissed and every one an agent or the
operator added.  A suggestion id is never used twice in a record.

REQUIREMENT -> SOURCE IDS (2.2).  ``requirements[]`` is the assessment's matrix
reduced to ids: the row id, its class and status, and ``sources`` as the model
gave them after the boundary's cleaning.

THE FINAL-SELECTION CHECK (2.3), ``check_selection(requirements, printed)``:
run after every change of what the job resume prints.  For each ``met`` row:
``kept`` (a master-line source is printed), ``lost`` (it had master-line
sources and none is printed), ``answer_only`` (its only sources are answers or
stories: no line of the master states it) or ``none`` (it came back without a
usable source).  ``ready`` is false, with a reason per row, when a MANDATORY
row is ``lost`` or the selection has a conflict.  ``ready: false`` never
removes the resume.  THE PAGE COUNT IS NEVER A REASON (0.1.11.5 item 1c): a
resume that prints on 3 pages at its automatic spacing is ready, and the user
fits the page with the spacing of the job's preview.  A conflict a selection
stored before 0.1.11.5 holds because of the page limit
(``PAGE_CONFLICTS``) is kept in the record and is no reason either.  An assessment must not approve evidence the resume no
longer shows.

THE STALE LIST (2.4), ``stale``: derived when the records are READ.  Nothing
is recomputed and nothing is written by opening a job (``stale_for`` reads the
stored assessment, this record and the kept master, and writes no file).

==========================  ==================================================================
``assessment_stale:<why>``  the stored assessment's own stale reason (``assessment_basis``)
``picked_line_changed``     a printed line's mark is not the recorded one, or the line is retired
                            (``changed_lines``: only a line the stored resume STILL prints, a
                            reworded one only while it shows the master's old words)
``master_newer``            the master's revision is not the one the selection was made from, and
                            no printed line changed (a note, not a warning)
``selection_rules_changed`` ``pick_rules_version`` or ``selector_version`` is not the shipped one
``assessment_newer``        the selection was not made from the stored assessment's answer
==========================  ==================================================================

PROPOSED (2.4, A6).  A stored job resume that is the user's (edited, attached,
a line choice, or made by the 0.1.10 tailoring) is never replaced.  The new
selection waits in ``proposed`` (the shape of ``selection``, with the path of a
sibling file that holds the resume) until the user takes it or dismisses it
(``store_selection``, ``use_proposed``, ``dismiss_proposed``).

Pure except the store functions at the end (small files; the kept master read
of ``tailor_master.stored_master``).  No model call anywhere here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import re
from typing import Iterator

from ..canonical import digest_imported_bytes, parse_json_bytes
from .find_jobs.assess_contracts import (
    MAX_SUGGESTION_PHRASE_CHARS,
    MAX_SUGGESTION_WHY_CHARS,
    SUGGESTION_KINDS,
    AssessmentSuggestion,
)
from .find_jobs.contracts import is_row_id
from .find_jobs.discovery.storage import atomic_write, project_id

_logger = logging.getLogger("gigai.scout.server")

SCHEMA_VERSION = "scout-job-suggestions:1"

COVERAGE_KEPT = "kept"
COVERAGE_LOST = "lost"
COVERAGE_ANSWER_ONLY = "answer_only"
COVERAGE_NONE = "none"
COVERAGES: tuple[str, ...] = (COVERAGE_KEPT, COVERAGE_LOST, COVERAGE_ANSWER_ONLY, COVERAGE_NONE)

#: ``gate.reasons`` codes the final-selection check adds to the gate's own.
REASON_LOST_EVIDENCE = "lost_mandatory_evidence"
REASON_CONFLICT = "selection_conflict"
#: The conflict codes the page limit made in a selection picked before 0.1.11.5 (``pick``: the Skills cut for the page,
#: a role's heading line cut for it, still over the limit).  No selection makes one now, and one that is stored never
#: makes a resume "not ready": the page is the user's to fit.
PAGE_CONFLICTS: frozenset[str] = frozenset({"skills_do_not_fit", "earlier_roles_do_not_fit", "over_page_limit"})

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_DISMISSED = "dismissed"
STATUSES: tuple[str, ...] = (STATUS_OPEN, STATUS_DONE, STATUS_DISMISSED)
SOURCE_ASSESSMENT = "assessment"
SOURCE_AGENT = "agent"
SOURCE_OPERATOR = "operator"
SOURCES: tuple[str, ...] = (SOURCE_ASSESSMENT, SOURCE_AGENT, SOURCE_OPERATOR)
#: ``resolved.how``: what closed a suggestion.
HOW_JOB_RESUME_EDIT = "job_resume_edit"
HOW_MASTER_LINE = "master_line"
HOW_ANSWER = "answer"
HOW_DISMISSED = "dismissed"
HOWS: tuple[str, ...] = (HOW_JOB_RESUME_EDIT, HOW_MASTER_LINE, HOW_ANSWER, HOW_DISMISSED)

STALE_ASSESSMENT = "assessment_stale"
STALE_PICKED_LINE = "picked_line_changed"
STALE_MASTER_NEWER = "master_newer"
STALE_RULES = "selection_rules_changed"
STALE_ASSESSMENT_NEWER = "assessment_newer"

#: The sentence of the suggestion code writes for a ``met`` row no line of the master states.
ANSWER_ONLY_WHY = "An answer or a story supports this requirement, and no line of your master resume states it. Add a line to your master so a resume can show it."

_ANSWER_PREFIX = "A "
_SUGGESTION_ID = re.compile(r"\Asg-(\d+)\Z")
_MANDATORY = ("hard", "askable", None)


class SuggestionError(ValueError):
    """A suggestion record or a change to one that is refused; ``code`` is the API/CLI error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- the requirement rows, reduced to ids (2.2) ---------------------------------------------------------


def _value(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        return row.get(name)
    value = getattr(row, "requirement_class" if name == "class" else name, None)
    return getattr(value, "value", value)


def is_answer_source(source: str) -> bool:
    """``A <question_id>`` / ``A story:<slug>``: an answer or a story, not a line of the master."""

    return source.startswith(_ANSWER_PREFIX)


@dataclass(frozen=True)
class RequirementRow:
    """One row of the assessment's matrix, by id: its class, its status and the sources the model named."""

    id: str
    requirement_class: str | None
    status: str
    sources: tuple[str, ...] = ()

    @property
    def mandatory(self) -> bool:
        return self.requirement_class in _MANDATORY

    def master_sources(self) -> tuple[str, ...]:
        return tuple(source for source in self.sources if not is_answer_source(source))


def requirement_rows(matrix: Iterable[object]) -> tuple[RequirementRow, ...]:
    """An assessment's matrix as :class:`RequirementRow` items; a row without an id (made before 0.1.11) is ``r<n>``."""

    rows: list[RequirementRow] = []
    for place, row in enumerate(matrix, 1):
        row_id = _value(row, "id")
        klass = _value(row, "class")
        rows.append(RequirementRow(
            str(row_id) if row_id else f"r{place}", klass if isinstance(klass, str) else None, str(_value(row, "status") or ""),
            tuple(str(source) for source in (_value(row, "sources") or ())),  # type: ignore[union-attr]
        ))
    return tuple(rows)


# --- the final-selection check (2.3) ----------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageRow:
    """A requirement row with where its support is printed. ``coverage`` is ``None`` for a row that is not ``met``."""

    id: str
    requirement_class: str | None
    status: str
    sources: tuple[str, ...]
    in_resume: tuple[str, ...] = ()
    coverage: str | None = None

    @property
    def mandatory(self) -> bool:
        return self.requirement_class in _MANDATORY

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id, "class": self.requirement_class, "status": self.status, "sources": list(self.sources),
            "in_resume": list(self.in_resume), "coverage": self.coverage,
        }

    @classmethod
    def from_json(cls, obj: object) -> "CoverageRow":
        if type(obj) is not dict or set(obj) != {"id", "class", "status", "sources", "in_resume", "coverage"}:
            raise SuggestionError("invalid_value", "a suggestion record requirement holds id, class, status, sources, in_resume and coverage")
        if obj["coverage"] is not None and obj["coverage"] not in COVERAGES:
            raise SuggestionError("bad_enum", "a suggestion record requirement has an unknown coverage")
        return cls(
            _text(obj["id"], "requirement id"), None if obj["class"] is None else _text(obj["class"], "requirement class"),
            _text(obj["status"], "requirement status"), _texts(obj["sources"], "requirement sources"), _texts(obj["in_resume"], "requirement in_resume"),
            obj["coverage"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class CheckReason:
    """Why ``ready`` is false: a code, and the requirement row it is about (``None`` for a conflict with no row)."""

    code: str
    requirement: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "requirement": self.requirement}


@dataclass(frozen=True)
class SelectionCheck:
    """``check_selection``'s answer: every row with its coverage, whether the resume is ready, and why not."""

    rows: tuple[CoverageRow, ...]
    ready: bool
    reasons: tuple[CheckReason, ...] = ()

    def lost(self) -> tuple[str, ...]:
        """The MANDATORY rows whose master-line sources are all unprinted (SPEC 3.5 check 1)."""

        return tuple(row.id for row in self.rows if row.mandatory and row.coverage == COVERAGE_LOST)

    def answer_only(self) -> tuple[str, ...]:
        return tuple(row.id for row in self.rows if row.coverage == COVERAGE_ANSWER_ONLY)


def check_selection(requirements: Iterable[RequirementRow], printed: Iterable[str], *, conflicts: Sequence[object] = ()) -> SelectionCheck:
    """The final-selection check (the module text). Pure.

    ``requirements``: the assessment's rows by id (``requirement_rows``).
    ``printed``: the master line ids the job resume shows (a copied line's
    own id; a line changed in chat counts for every master id it cites).
    ``conflicts``: the selection's conflicts (``pick.PickConflict``); any one
    makes the resume not ready, named by its own code's row when it has one,
    except a page-driven one (``PAGE_CONFLICTS``), which is no reason.
    """

    shown = set(printed)
    rows: list[CoverageRow] = []
    reasons: list[CheckReason] = []
    for row in requirements:
        if row.status != "met":
            rows.append(CoverageRow(row.id, row.requirement_class, row.status, row.sources))
            continue
        lines = row.master_sources()
        kept = tuple(source for source in lines if source in shown)
        if kept:
            coverage = COVERAGE_KEPT
        elif lines:
            coverage = COVERAGE_LOST
        elif row.sources:
            coverage = COVERAGE_ANSWER_ONLY
        else:
            coverage = COVERAGE_NONE
        rows.append(CoverageRow(row.id, row.requirement_class, row.status, row.sources, kept, coverage))
        if coverage == COVERAGE_LOST and row.mandatory:
            reasons.append(CheckReason(REASON_LOST_EVIDENCE, row.id))
    for conflict in conflicts:
        if getattr(conflict, "code", None) in PAGE_CONFLICTS:
            continue
        requirement = getattr(conflict, "requirement", None)
        reasons.append(CheckReason(REASON_CONFLICT, requirement if isinstance(requirement, str) and requirement else None))
    return SelectionCheck(tuple(rows), not reasons, tuple(reasons))


def live_selection(selection: Mapping[str, object] | None, printed: Iterable[str]) -> tuple[Mapping[str, object] | None, tuple[CheckReason, ...]]:
    """``selection`` with the conflicts the resume STILL has, and those as check reasons (SPEC 2.3, 10.2 item 5). Pure.

    A conflict names the master lines the cap kept out; it is gone once every one of them prints again (a
    hand-back or an Add that brought the line back).  A page-driven conflict of a selection stored before 0.1.11.5
    (``PAGE_CONFLICTS``) stays in the record until a re-pick and is no reason: the page count never makes a resume
    "not ready".
    """

    if selection is None:
        return None, ()
    shown = set(printed)
    kept = [
        item for item in selection.get("conflicts", ())  # type: ignore[union-attr]
        if isinstance(item, Mapping) and (not item.get("lines") or any(line not in shown for line in item["lines"]))  # type: ignore[union-attr]
    ]
    reasons = tuple(CheckReason(REASON_CONFLICT, item.get("requirement")) for item in kept if item.get("code") not in PAGE_CONFLICTS)
    return {**selection, "conflicts": kept}, reasons


# --- a suggestion ----------------------------------------------------------------------------------------------


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise SuggestionError("wrong_type", f"{name} must be a non-empty string")
    return value


def _texts(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise SuggestionError("wrong_type", f"{name} must be an array of non-empty strings")
    return tuple(value)


@dataclass(frozen=True)
class Resolved:
    """How a suggestion was closed: by whom, when, how (:data:`HOWS`) and what it points at (a master id, a question id)."""

    by: str
    at: str
    how: str
    ref: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"by": self.by, "at": self.at, "how": self.how, "ref": self.ref}

    @classmethod
    def from_json(cls, obj: object) -> "Resolved":
        if type(obj) is not dict or set(obj) != {"by", "at", "how", "ref"} or obj["how"] not in HOWS:
            raise SuggestionError("invalid_value", "a resolved suggestion holds by, at, how and ref")
        return cls(_text(obj["by"], "resolved.by"), _text(obj["at"], "resolved.at"), obj["how"], None if obj["ref"] is None else _text(obj["ref"], "resolved.ref"))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Suggestion:
    """One suggestion of a job's record: what would help and why, who wrote it, and whether it is still open."""

    id: str
    kind: str
    why: str
    source: str
    created_at: str
    line: str | None = None
    requirement: str | None = None
    posting_phrase: str | None = None
    status: str = STATUS_OPEN
    resolved: Resolved | None = None

    def __post_init__(self) -> None:
        if not _SUGGESTION_ID.fullmatch(self.id):
            raise SuggestionError("invalid_value", "a suggestion id is sg-<n>")
        if self.kind not in SUGGESTION_KINDS or self.source not in SOURCES or self.status not in STATUSES:
            raise SuggestionError("bad_enum", "a suggestion has an unknown kind, source or status")
        if not self.why.strip() or len(self.why) > MAX_SUGGESTION_WHY_CHARS:
            raise SuggestionError("invalid_value", f"a suggestion's why is 1 to {MAX_SUGGESTION_WHY_CHARS} characters")
        if self.line is None and self.requirement is None:
            raise SuggestionError("invalid_value", "a suggestion names a line or a requirement")
        if self.requirement is not None and not is_row_id(self.requirement):
            raise SuggestionError("invalid_value", "a suggestion's requirement is a requirement row id")
        if self.posting_phrase is not None and len(self.posting_phrase) > MAX_SUGGESTION_PHRASE_CHARS:
            raise SuggestionError("invalid_value", f"a suggestion's posting phrase is at most {MAX_SUGGESTION_PHRASE_CHARS} characters")
        if (self.status == STATUS_OPEN) != (self.resolved is None):
            raise SuggestionError("invalid_value", "an open suggestion is not resolved, and a closed one says how")

    @property
    def number(self) -> int:
        return int(_SUGGESTION_ID.fullmatch(self.id).group(1))  # type: ignore[union-attr]

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id, "kind": self.kind, "line": self.line, "requirement": self.requirement, "posting_phrase": self.posting_phrase,
            "why": self.why, "source": self.source, "created_at": self.created_at, "status": self.status,
            "resolved": None if self.resolved is None else self.resolved.to_json(),
        }

    @classmethod
    def from_json(cls, obj: object) -> "Suggestion":
        keys = {"id", "kind", "line", "requirement", "posting_phrase", "why", "source", "created_at", "status", "resolved"}
        if type(obj) is not dict or set(obj) != keys:
            raise SuggestionError("invalid_value", "a suggestion holds " + ", ".join(sorted(keys)))
        optional = {name: (None if obj[name] is None else _text(obj[name], f"suggestion {name}")) for name in ("line", "requirement", "posting_phrase")}
        return cls(
            id=_text(obj["id"], "suggestion id"), kind=_text(obj["kind"], "suggestion kind"), why=_text(obj["why"], "suggestion why"),
            source=_text(obj["source"], "suggestion source"), created_at=_text(obj["created_at"], "suggestion created_at"),
            status=_text(obj["status"], "suggestion status"), resolved=None if obj["resolved"] is None else Resolved.from_json(obj["resolved"]),
            **optional,
        )


# --- the record ------------------------------------------------------------------------------------------------

_RECORD_KEYS = (
    "schema_version", "profile_id", "job_identity", "created_at", "updated_at", "stored_path", "basis", "gate", "requirements",
    "selection", "proposed", "suggestions",
)


@dataclass(frozen=True)
class SuggestionRecord:
    """One job's suggestion record (the module text). ``basis``, ``gate``, ``selection`` and ``proposed`` are JSON objects
    in the shapes of SPEC 2.1 (``basis_of``, ``gate_json``, ``pick.Settled.selection_json`` build them)."""

    profile_id: str
    job_identity: str
    created_at: str
    updated_at: str
    stored_path: str
    basis: Mapping[str, object]
    gate: Mapping[str, object]
    requirements: tuple[CoverageRow, ...] = ()
    selection: Mapping[str, object] | None = None
    proposed: Mapping[str, object] | None = None
    suggestions: tuple[Suggestion, ...] = ()
    #: Why no selection could be made although the gate suggests one: an error code.
    selection_error: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "job_identity": self.job_identity,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stored_path": self.stored_path,
            "basis": dict(self.basis),
            "gate": dict(self.gate),
            "requirements": [row.to_json() for row in self.requirements],
            "selection": None if self.selection is None else dict(self.selection),
            "proposed": None if self.proposed is None else dict(self.proposed),
            "suggestions": [item.to_json() for item in self.suggestions],
        }
        if self.selection_error is not None:
            value["selection_error"] = self.selection_error
        return value

    @classmethod
    def from_json(cls, obj: object) -> "SuggestionRecord":
        if type(obj) is not dict or obj.get("schema_version") != SCHEMA_VERSION:
            raise SuggestionError("bad_enum", "not a scout-job-suggestions:1 record")
        unknown = set(obj) - {*_RECORD_KEYS, "selection_error"}
        missing = set(_RECORD_KEYS) - set(obj)
        if unknown or missing:
            raise SuggestionError("invalid_value", "a suggestion record holds " + ", ".join(_RECORD_KEYS))
        for name in ("basis", "gate"):
            if type(obj[name]) is not dict:
                raise SuggestionError("wrong_type", f"a suggestion record's {name} is an object")
        for name in ("selection", "proposed"):
            if obj[name] is not None and type(obj[name]) is not dict:
                raise SuggestionError("wrong_type", f"a suggestion record's {name} is an object or null")
        if type(obj["requirements"]) is not list or type(obj["suggestions"]) is not list:
            raise SuggestionError("wrong_type", "a suggestion record's requirements and suggestions are arrays")
        suggestions = tuple(Suggestion.from_json(item) for item in obj["suggestions"])
        if len({item.id for item in suggestions}) != len(suggestions):
            raise SuggestionError("invalid_value", "a suggestion record holds each suggestion id once")
        error = obj.get("selection_error")
        return cls(
            profile_id=_text(obj["profile_id"], "profile_id"), job_identity=_text(obj["job_identity"], "job_identity"),
            created_at=_text(obj["created_at"], "created_at"), updated_at=_text(obj["updated_at"], "updated_at"),
            stored_path=_text(obj["stored_path"], "stored_path"), basis=obj["basis"], gate=obj["gate"],  # type: ignore[arg-type]
            requirements=tuple(CoverageRow.from_json(item) for item in obj["requirements"]),
            selection=obj["selection"], proposed=obj["proposed"], suggestions=suggestions,  # type: ignore[arg-type]
            selection_error=None if error is None else _text(error, "selection_error"),
        )

    def next_number(self) -> int:
        return max((item.number for item in self.suggestions), default=0) + 1

    def printed(self) -> tuple[str, ...]:
        """The master line ids the selection recorded as printed (``line_marks``); empty without a selection."""

        return tuple(recorded_marks(self.selection))


def marks_json(marks: Mapping[str, str]) -> list[dict[str, str]]:
    """``line_marks`` as stored: a list of ``{id, mark}`` in print order.

    A list, not an object keyed by line id: ``b-5ce72c`` is not a canonical
    JSON member name (``canonical.parse_json_bytes``, the stored-file reader),
    the reason ``StoryBankStamp`` stores its entries as a list too.
    """

    return [{"id": item_id, "mark": mark} for item_id, mark in marks.items()]


def recorded_marks(selection: Mapping[str, object] | None) -> dict[str, str]:
    """A stored selection's ``line_marks`` as ``line id -> mark``; empty when it holds none."""

    items = (selection or {}).get("line_marks")
    if type(items) is not list:
        return {}
    return {str(item["id"]): str(item["mark"]) for item in items if type(item) is dict and "id" in item and "mark" in item}


def result_digest(assessment: object) -> str:
    """The digest of a stored assessment's ANSWER (``result``): what a selection says it was made from."""

    body = json.dumps(assessment.result.to_json(), sort_keys=True, separators=(",", ":"))  # type: ignore[attr-defined]
    return digest_imported_bytes(body.encode("utf-8"))


def basis_of(assessment: object, master_source: object | None = None) -> dict[str, object]:
    """``basis`` (2.1): what the assessment was made with, by path, digest and version; never a text.

    ``master_source``: the master revision the assessment read (``tailor_master.MasterSource``), or ``None``.
    """

    bank = getattr(assessment, "story_bank", None)
    ref = getattr(assessment, "requirements_ref", None)
    return {
        "assessment": {
            "stored_path": assessment.stored_path,  # type: ignore[attr-defined]
            "assessed_at": assessment.updated_at or assessment.created_at,  # type: ignore[attr-defined]
            "result_digest": result_digest(assessment),
            "prompt_version": assessment.prompt_version,  # type: ignore[attr-defined]
            "instructions_digest": assessment.instructions_digest,  # type: ignore[attr-defined]
            "model": assessment.model,  # type: ignore[attr-defined]
            "constraints_digest": assessment.constraints_digest,  # type: ignore[attr-defined]
            "story_bank_digest": None if bank is None else bank.bank_digest,
        },
        "posting_sha256": assessment.posting_sha256,  # type: ignore[attr-defined]
        "requirements": None if ref is None else {"rules_version": ref.rules_version, "digest": ref.rows_digest, "list": ref.kind},
        "master": None if master_source is None else master_source.to_json(),  # type: ignore[attr-defined]
    }


def gate_json(gate_record: object | None, check: SelectionCheck | None) -> dict[str, object]:
    """``gate`` (2.1): the gate's decision and reasons, and ``ready`` with the final-selection check's reasons.

    ``ready`` is true only for a ``suggest`` decision whose selection passed
    the check; without a selection nothing is ready.
    """

    decision = getattr(gate_record, "decision", None)
    reasons = [reason.to_json() for reason in getattr(gate_record, "reasons", ())]
    if check is not None:
        reasons += [reason.to_json() for reason in check.reasons]
    return {"decision": decision, "ready": bool(decision == "suggest" and check is not None and check.ready), "reasons": reasons}


def _assessment_suggestions(
    items: Iterable[AssessmentSuggestion], check_rows: Iterable[CoverageRow], *, start: int, created_at: str,
) -> list[Suggestion]:
    """The assessment's suggestions as record entries, plus one ``master_line`` per ``answer_only`` row it did not name."""

    out: list[Suggestion] = []
    number = start
    for item in items:
        out.append(Suggestion(
            id=f"sg-{number}", kind=item.kind, why=item.why, source=SOURCE_ASSESSMENT, created_at=created_at, line=item.line,
            requirement=item.requirement, posting_phrase=item.posting_phrase,
        ))
        number += 1
    named = {item.requirement for item in out if item.kind == "master_line"}
    for row in check_rows:
        if row.coverage == COVERAGE_ANSWER_ONLY and row.id not in named and is_row_id(row.id):
            out.append(Suggestion(id=f"sg-{number}", kind="master_line", why=ANSWER_ONLY_WHY, source=SOURCE_ASSESSMENT, created_at=created_at, requirement=row.id))
            number += 1
    return out


def merged(
    previous: SuggestionRecord | None, *, profile_id: str, job_identity: str, stored_path: str, now: str, basis: Mapping[str, object],
    gate: Mapping[str, object], requirements: Sequence[CoverageRow], suggested: Iterable[AssessmentSuggestion] = (),
    selection: Mapping[str, object] | None = None, proposed: Mapping[str, object] | None = None, selection_error: str | None = None,
) -> SuggestionRecord:
    """The record after a NEW ASSESSMENT of the job (the module text): what it replaces, what it keeps. Pure.

    ``selection`` / ``proposed`` are what the caller settled for this
    assessment (``None``: none); a ``proposed`` of an earlier assessment is
    dropped with it.
    """

    kept = [item for item in (previous.suggestions if previous is not None else ()) if item.source != SOURCE_ASSESSMENT or item.status != STATUS_OPEN]
    start = previous.next_number() if previous is not None else 1
    fresh = _assessment_suggestions(suggested, requirements, start=start, created_at=now)
    return SuggestionRecord(
        profile_id=profile_id, job_identity=job_identity, created_at=previous.created_at if previous is not None else now, updated_at=now,
        stored_path=stored_path, basis=dict(basis), gate=dict(gate), requirements=tuple(requirements), selection=selection, proposed=proposed,
        suggestions=(*kept, *fresh), selection_error=selection_error,
    )


def with_selection(
    record: SuggestionRecord, *, now: str, check: SelectionCheck, selection: Mapping[str, object] | None,
    proposed: Mapping[str, object] | None = None, selection_error: str | None = None,
) -> SuggestionRecord:
    """``record`` after a change of what the job resume prints with NO new assessment (a re-pick, a Restore, a hand-back).

    The gate's decision stays; ``ready`` and its reasons are the new
    check's, and every row carries its new coverage.
    """

    gate = dict(record.gate)
    own = [reason for reason in gate.get("reasons", ()) if isinstance(reason, Mapping) and reason.get("code") not in (REASON_LOST_EVIDENCE, REASON_CONFLICT)]  # type: ignore[union-attr]
    gate["reasons"] = [*own, *(reason.to_json() for reason in check.reasons)]
    gate["ready"] = bool(gate.get("decision") == "suggest" and selection is not None and check.ready)
    return replace(record, updated_at=now, gate=gate, requirements=check.rows, selection=selection, proposed=proposed, selection_error=selection_error)


def add_suggestion(
    record: SuggestionRecord, *, kind: str, why: str, source: str, now: str, line: str | None = None, requirement: str | None = None,
    posting_phrase: str | None = None,
) -> SuggestionRecord:
    """``record`` with one more open suggestion, written by an agent or the operator (``source``). Pure.

    Refused (``personal_info_refused``) when ``why`` or the posting phrase
    holds a contact detail: this record never holds one.
    """

    from .tailored_resume import personal_info_found

    if source not in (SOURCE_AGENT, SOURCE_OPERATOR):
        raise SuggestionError("bad_enum", "a suggestion is added by agent or operator")
    why = " ".join(str(why).split())
    for text in (why, posting_phrase or ""):
        if text and personal_info_found(text):
            raise SuggestionError("personal_info_refused", "a suggestion holds no name and no contact detail")
    added = Suggestion(
        id=f"sg-{record.next_number()}", kind=kind, why=why, source=source, created_at=now, line=line, requirement=requirement,
        posting_phrase=posting_phrase,
    )
    return replace(record, updated_at=now, suggestions=(*record.suggestions, added))


def resolve_suggestion(record: SuggestionRecord, suggestion_id: str, *, by: str, how: str, now: str, ref: str | None = None) -> SuggestionRecord:
    """``record`` with one suggestion closed: ``done`` (``how`` says by what) or ``dismissed``. Recorded, never deleted."""

    if how not in HOWS:
        raise SuggestionError("bad_enum", "how must be one of " + ", ".join(HOWS))
    if not any(item.id == suggestion_id for item in record.suggestions):
        raise SuggestionError("suggestion_not_found", f"this job has no suggestion {suggestion_id}")
    status = STATUS_DISMISSED if how == HOW_DISMISSED else STATUS_DONE
    changed = tuple(
        replace(item, status=status, resolved=Resolved(by, now, how, ref)) if item.id == suggestion_id else item for item in record.suggestions
    )
    return replace(record, updated_at=now, suggestions=changed)


# --- the stale list (2.4) ------------------------------------------------------------------------------------------


#: ``changed_lines``'s ``change``: the master no longer holds the line, or holds it with other words.
CHANGE_RETIRED = "retired"
CHANGE_REWORDED = "reworded"


def printed_copies(result: object) -> dict[str, bool]:
    """Master line id -> whether the stored job resume prints it in the MASTER'S words (a copy), for each line it shows.

    ``False``: the resume shows the line only in the user's own words (a point they edited).  Pure.
    """

    from .tailored_resume import replaced_line

    out: dict[str, bool] = {}
    for section in result.sections:  # type: ignore[attr-defined]
        for line in section.body_lines():
            own = line.kind == "custom"
            for ref in (*line.refs, *replaced_line(line).refs):
                item_id = getattr(ref, "item_id", None)
                if ref.kind == "resume" and item_id is not None:
                    out[item_id] = out.get(item_id, False) or not own
    return out


def changed_lines(
    selection: Mapping[str, object] | None, marks_now: Mapping[str, str] | None, printed: Mapping[str, bool] | None = None,
) -> tuple[dict[str, str], ...]:
    """The picked lines the master changed since the pick: ``{"id", "change": retired | reworded}`` each. Pure.

    ``marks_now``: the master as it is (line id -> mark; a missing id is a retired line); ``None``: nothing is compared.
    ``printed`` (``printed_copies`` of the job resume as it is STORED now): only a line the resume still prints
    counts, a reworded one only while the resume shows the master's old words.  So taking a retired line off the
    resume, or putting the new wording on it, settles the line with no new pick.  ``None``: every recorded line counts.
    """

    if marks_now is None:
        return ()
    found: list[dict[str, str]] = []
    for item_id, mark in recorded_marks(selection).items():
        now = marks_now.get(item_id)
        if now == mark:
            continue
        if printed is not None and (item_id not in printed or (now is not None and not printed[item_id])):
            continue
        found.append({"id": item_id, "change": CHANGE_RETIRED if now is None else CHANGE_REWORDED})
    return tuple(found)


def stale(
    record: SuggestionRecord | None, *, assessment_stale: str | None = None, result_digest_now: str | None = None,
    master_revision_id: str | None = None, marks_now: Mapping[str, str] | None = None, pick_rules_version: str | None = None,
    selector_version: str | None = None, printed: Mapping[str, bool] | None = None,
) -> tuple[str, ...]:
    """The stale codes of one job, from what is STORED and what is shipped now (the module text's table). Pure.

    ``assessment_stale``: the stored assessment's own reason
    (``assessment_basis``), or ``None``.  ``result_digest_now``: the stored
    assessment's answer digest.  ``master_revision_id`` / ``marks_now``: the
    master as it is (``marks_now``: line id -> mark; a missing id is a retired
    line).  ``pick_rules_version`` / ``selector_version``: the shipped ones.
    A value left ``None`` is not compared.  ``printed``: what the stored job
    resume prints now (``changed_lines``); ``None``: every recorded line counts.
    """

    found: list[str] = []
    if assessment_stale:
        found.append(f"{STALE_ASSESSMENT}:{assessment_stale}")
    selection = record.selection if record is not None else None
    if not selection:
        return tuple(found)
    made_from = selection.get("made_from")
    made_from = made_from if isinstance(made_from, Mapping) else {}
    if changed_lines(selection, marks_now, printed):
        found.append(STALE_PICKED_LINE)
    elif master_revision_id is not None and made_from.get("master_revision_id") not in (None, master_revision_id):
        found.append(STALE_MASTER_NEWER)
    if (pick_rules_version is not None and selection.get("pick_rules_version") != pick_rules_version) or (
        selector_version is not None and selection.get("selector_version") != selector_version
    ):
        found.append(STALE_RULES)
    if result_digest_now is not None and made_from.get("result_digest") not in (None, result_digest_now):
        found.append(STALE_ASSESSMENT_NEWER)
    return tuple(found)


# --- the store ---------------------------------------------------------------------------------------------------------


def suggestions_dir(home_root: Path, target: Path) -> Path:
    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "suggestions"


def _job_digest(job_identity: str) -> str:
    return digest_imported_bytes(job_identity.encode("utf-8")).removeprefix("sha256:")


def suggestions_path(home_root: Path, target: Path, profile_id: str | None, job_identity: str) -> Path:
    """Where one job's record is stored: the path rule of ``quick_assess.quick_assess_path``."""

    from .quick_assess import resume_key

    return suggestions_dir(home_root, target) / resume_key(profile_id) / f"{_job_digest(job_identity)}.json"


def proposed_resume_path(record_path: Path) -> Path:
    """The sibling file that holds a ``proposed`` selection's resume (a ``TailorResponse``), beside the record."""

    return record_path.with_name(f"{record_path.stem}.proposed-resume.json")


def without_page_reasons(record: SuggestionRecord) -> SuggestionRecord:
    """``record`` with a gate the page limit is no reason of (0.1.11.5 item 1c). Pure; nothing is written.

    A selection picked before 0.1.11.5 may hold page-driven conflicts (``PAGE_CONFLICTS``), and its stored gate then
    says ``ready: false`` with one ``selection_conflict`` reason for each.  Those reasons are taken off here, where a
    record is READ, and ``ready`` is true again when no other reason of the check is left: the page count never makes
    a resume "not ready".  The selection keeps its conflicts as they were stored (a re-pick drops them).
    """

    selection, gate = record.selection, record.gate
    if not isinstance(selection, Mapping) or gate.get("decision") != "suggest" or gate.get("ready") is not False:
        return record
    found = selection.get("conflicts")
    page = sum(1 for item in (found if isinstance(found, list) else ()) if isinstance(item, Mapping) and item.get("code") in PAGE_CONFLICTS)
    if not page:
        return record
    reasons: list[object] = []
    for reason in gate.get("reasons", ()):  # type: ignore[union-attr]
        if page and isinstance(reason, Mapping) and reason.get("code") == REASON_CONFLICT and not reason.get("requirement"):
            page -= 1  # a page-driven conflict names no requirement row
            continue
        reasons.append(reason)
    check = [reason for reason in reasons if isinstance(reason, Mapping) and reason.get("code") in (REASON_LOST_EVIDENCE, REASON_CONFLICT)]
    return replace(record, gate={**gate, "reasons": reasons, "ready": not check})


def read_record(path: Path) -> SuggestionRecord | None:
    """The record stored at ``path``, or ``None`` (no file, or one that no longer parses).

    Read through ``without_page_reasons``: every reader (the job page, the CLI, an agent's brief, the pipeline) sees a
    gate that the page limit of an older pick does not hold."""

    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return without_page_reasons(SuggestionRecord.from_json(parse_json_bytes(path.read_bytes())))
    except (OSError, ValueError):
        return None


def read_suggestions(home_root: Path, target: Path, profile_id: str | None, job_identity: str) -> SuggestionRecord | None:
    return read_record(suggestions_path(home_root, target, profile_id, job_identity))


@contextmanager
def record_write_lock(path: Path) -> Iterator[None]:
    """One writer at a time for the records beside ``path``: a lock on the folder, held for file reads and writes only."""

    folder = Path(path).parent
    folder.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def save_record(record: SuggestionRecord) -> None:
    """Write ``record`` at the path it names, atomically. The caller holds ``record_write_lock`` around its read and this."""

    atomic_write(Path(record.stored_path), json.dumps(record.to_json(), indent=2, sort_keys=True).encode("utf-8"))


def stale_view(
    home_root: Path, target: Path, profile_id: str | None, job_identity: str, *, assessment_stale: str | None = None, resolved: object | None = None,
    resume: object | None = None,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """``(stale list, changed lines)`` of one job as it is stored NOW (2.4): three reads, nothing recomputed, nothing written.

    ``assessment_stale``: the stored assessment's own stale reason when the
    caller has it (``assessment_basis.BasisCheck.reason``).  ``resume``: the
    stored job resume when the caller has read it (nothing is read for it
    here): ``picked_line_changed`` and the changed lines are then about the
    lines it still prints (``changed_lines``).
    """

    from .master_selection import SELECTOR_VERSION
    from .pick import PICK_RULES_VERSION
    from .quick_assess import read_quick_assessment
    from .tailor_master import stored_master

    record = read_suggestions(home_root, target, profile_id, job_identity)
    assessment = read_quick_assessment(home_root, target, profile_id, job_identity)
    master = stored_master(Path(home_root), Path(target), resolved=resolved)
    marks = None if master is None else {item.id: item.mark for item in master.master.items.values()}  # type: ignore[attr-defined]
    printed = None if resume is None else printed_copies(resume.result)  # type: ignore[attr-defined]
    codes = stale(
        record, assessment_stale=assessment_stale, result_digest_now=None if assessment is None else result_digest(assessment),
        master_revision_id=None if master is None else master.revision.revision_id, marks_now=marks,  # type: ignore[attr-defined]
        pick_rules_version=PICK_RULES_VERSION, selector_version=SELECTOR_VERSION, printed=printed,
    )
    return codes, changed_lines(record.selection if record is not None else None, marks, printed)


def stale_for(
    home_root: Path, target: Path, profile_id: str | None, job_identity: str, *, assessment_stale: str | None = None, resolved: object | None = None,
    resume: object | None = None,
) -> tuple[str, ...]:
    """The stale list of one job as it is stored NOW: ``stale_view``'s first part."""

    return stale_view(home_root, target, profile_id, job_identity, assessment_stale=assessment_stale, resolved=resolved, resume=resume)[0]


# --- the job resume and the record, written together (1.7 step 4; 2.4 "user edits are preserved") ----------------------


def printed_ids(result: object) -> tuple[str, ...]:
    """The master line ids a stored job resume shows: a copied line's own id, and every master id a changed line cites."""

    from .tailored_resume import replaced_line

    out: list[str] = []
    for section in result.sections:  # type: ignore[attr-defined]
        for line in section.body_lines():
            for ref in (*line.refs, *replaced_line(line).refs):
                item_id = getattr(ref, "item_id", None)
                if ref.kind == "resume" and item_id is not None and item_id not in out:
                    out.append(item_id)
    return tuple(out)


def _markdown_digest(markdown: str) -> str:
    return digest_imported_bytes(markdown.encode("utf-8"))


#: What a person is told when a proposal was made against another resume than the one stored now (``proposal_stale``).
PROPOSAL_STALE = "the resume changed after this suggestion was made; pick again"
#: What a person is told when the stored job resume is a file that cannot be read (``stored_resume_unreadable``).
STORED_UNREADABLE = "the stored resume could not be read; it was left as it is"


def stored_unreadable(path: Path) -> bool:
    """Whether a file IS stored at ``path`` and cannot be read as a job resume (``read_tailored_resume`` answers ``None``
    for it, as for no file).  Such a file is the user's: nothing writes over it but their own hand-back with ``--force``."""

    from .tailored_resume import read_tailored_resume

    path = Path(path)
    return (path.is_symlink() or path.exists()) and read_tailored_resume(path) is None


def revision_of(stored: object) -> dict[str, object]:
    """The revision of a stored job resume: when it was written and the digest of its markdown."""

    return {"updated_at": stored.updated_at, "markdown_sha256": _markdown_digest(stored.markdown)}  # type: ignore[attr-defined]


def _instant(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        return None


def proposal_is_stale(proposed: Mapping[str, object], stored: object) -> bool:
    """Whether ``proposed`` was made against another resume than ``stored`` (the job resume as it is stored NOW).

    A proposal records the revision it was made beside (``against``, ``revision_of``).  One written before that was
    recorded is stale when the stored resume was written after it was made, or when that cannot be told."""

    against = proposed.get("against")
    if isinstance(against, Mapping):
        return dict(against) != revision_of(stored)
    made, written = _instant(proposed.get("made_at")), _instant(getattr(stored, "updated_at", None))
    if made is None or written is None or (made.tzinfo is None) != (written.tzinfo is None):
        return True
    return written > made


def drop_proposal_after_edit(home_root: Path, target: Path, response: object) -> None:
    """An EDIT of the stored job resume (``response``: what was just stored) drops the selection that waited beside the
    resume as it was: a proposal is never taken over a resume it was not made against.  Never raises: ``use_proposed``
    refuses a stale proposal itself (``proposal_stale``)."""

    try:
        dismiss_proposed(
            Path(home_root), Path(target), response.resume.profile_id, response.job.job_identity, now=response.updated_at,  # type: ignore[attr-defined]
        )
    except (OSError, ValueError):
        _logger.warning("a waiting suggested resume could not be dropped after an edit", exc_info=True)


def is_replaceable(stored: object | None, record: SuggestionRecord | None, *, path: Path | None = None) -> bool:
    """Whether a new selection may REPLACE the stored job resume: there is none, or it is the pick's own, untouched.

    ``path``: where the job resume is stored.  With it, "there is none" means NO FILE: a file that cannot be read
    (``stored_unreadable``) is the user's and is never replaced.

    The pick's own: ``producer.callable`` is ``scout.pick``, it carries no
    ``edited`` mark, and its markdown is byte for byte what the record says
    the pick wrote (a line choice, an Add or Remove, a Restore or a cut all
    change it).  Anything else is the user's (or the 0.1.10 tailoring's) and
    is never replaced: the new selection waits as ``proposed`` (A6).
    """

    if stored is None:
        return path is None or not stored_unreadable(path)
    if getattr(stored.producer, "callable", None) != "scout.pick" or getattr(stored, "edited", None) is not None:  # type: ignore[attr-defined]
        return False
    resume = (record.selection or {}).get("resume") if record is not None else None
    return isinstance(resume, Mapping) and resume.get("markdown_sha256") == _markdown_digest(stored.markdown)  # type: ignore[attr-defined]


def job_resume(
    settled: object, *, job: object, resume: object, assessment: object, master_source: object | None, answers: Mapping[str, object] | None,
    path: Path, created_at: str, now: str,
) -> object:
    """The job resume of a selection, in the tailored-resume store's own format (``TailorResponse``).

    ``producer.callable`` is ``scout.pick`` (a 0.1.10 tailoring says
    ``scout.tailor``); it carries the ASSESS instructions digest, the
    assessment's model target, no usage (no call was made for it), and
    ``selection``: Picked / Left out.
    """

    from .find_jobs.contracts import Producer
    from .pick import PRODUCER_ACTOR, PRODUCER_CALLABLE, PRODUCER_VERSION
    from .tailored_resume import TailorResponse, TailorSources

    made_by = assessment.producer  # type: ignore[attr-defined]
    return TailorResponse(
        job=job,  # type: ignore[arg-type]
        resume=resume,  # type: ignore[arg-type]
        sources=TailorSources(
            resume_content_sha256=resume.content_sha256,  # type: ignore[attr-defined]
            resume_line_count=len(settled.context.resume_lines),  # type: ignore[attr-defined]
            answers={key: item.revision_id for key, item in (answers or {}).items()},  # type: ignore[attr-defined]
            assessment_stored_path=assessment.stored_path,  # type: ignore[attr-defined]
            master=master_source,  # type: ignore[arg-type]
        ),
        result=settled.result,  # type: ignore[attr-defined]
        markdown=settled.markdown,  # type: ignore[attr-defined]
        producer=Producer(PRODUCER_CALLABLE, PRODUCER_VERSION, PRODUCER_ACTOR, made_by.model_target, made_by.adapter),
        usage=None,
        instructions_digest=assessment.instructions_digest,  # type: ignore[attr-defined]
        created_at=created_at,
        updated_at=now,
        stored_path=os.fspath(path),
        markdown_path=os.fspath(Path(path).with_suffix(".md")),
        selection=settled.record,  # type: ignore[attr-defined]
    )


def store_assessed(
    home_root: Path, target: Path, *, assessment: object, job: object, resume: object, gate_record: object | None, now: str,
    suggested: Iterable[AssessmentSuggestion] = (), settled: object | None = None, selection_error: str | None = None,
    master_source: object | None = None, answers: Mapping[str, object] | None = None, repick: bool = False,
) -> SuggestionRecord:
    """Write the suggestion record of a NEW assessment and, with a selection, the job resume: one function, one lock.

    ``repick`` (0.1.11.3, ``pick.settle_stored``): the assessment is the STORED one and only its selection is made
    again. A record that exists keeps its suggestions, its ids and its basis (``with_selection``); a job with no
    record yet gets the one its assessment would have written.

    ``settled`` (``pick.Settled``; ``None``: the gate holds, or no selection
    could be made, ``selection_error`` says why).  With one: when the stored
    job resume may be replaced (``is_replaceable``) it is written and the
    record's ``selection`` names it; otherwise the stored resume stays, the
    new selection goes to ``proposed`` and its resume to a sibling file.  The
    final-selection check is made on what the job resume PRINTS after this.
    The lock is the resumes folder's own (``tailored_resume_write_lock``:
    whoever changes a stored resume holds it), held for these file reads and
    writes only.
    """

    from .tailored_resume import read_tailored_resume, save_tailor_response, tailored_resume_path, tailored_resume_write_lock

    profile_id = resume.profile_id  # type: ignore[attr-defined]
    identity = job.job_identity  # type: ignore[attr-defined]
    record_path = suggestions_path(home_root, target, profile_id, identity)
    resume_path = tailored_resume_path(Path(home_root), Path(target), profile_id, identity)
    rows = requirement_rows(assessment.result.matrix)  # type: ignore[attr-defined]
    digest = result_digest(assessment)
    revision_id = getattr(master_source, "revision_id", None)
    with tailored_resume_write_lock(resume_path), record_write_lock(record_path):
        previous = read_record(record_path)
        stored = read_tailored_resume(resume_path)
        selection = previous.selection if previous is not None else None
        proposed: Mapping[str, object] | None = None
        sibling = proposed_resume_path(record_path)
        if settled is not None:
            response = job_resume(
                settled, job=job, resume=resume, assessment=assessment, master_source=master_source, answers=answers, path=resume_path,
                created_at=stored.created_at if stored is not None else now, now=now,
            )
            names = {"markdown_sha256": _markdown_digest(response.markdown), "origin": "pick"}  # type: ignore[attr-defined]
            if is_replaceable(stored, previous, path=resume_path):
                save_tailor_response(response, home_root=Path(home_root))  # type: ignore[arg-type]
                stored = response
                selection = settled.selection_json(  # type: ignore[attr-defined]
                    made_at=now, result_digest=digest, master_revision_id=revision_id, resume={"stored_path": os.fspath(resume_path), **names},
                )
            else:
                atomic_write(sibling, json.dumps(response.to_json(), indent=2, sort_keys=True).encode("utf-8"))  # type: ignore[attr-defined]
                proposed = settled.selection_json(  # type: ignore[attr-defined]
                    made_at=now, result_digest=digest, master_revision_id=revision_id, resume={"stored_path": os.fspath(sibling), **names},
                )
                # The resume it waits beside, as it is now: ``use_proposed`` takes it over this revision only.
                proposed["against"] = None if stored is None else revision_of(stored)
        if proposed is None:
            sibling.unlink(missing_ok=True)  # a proposal of an earlier assessment goes with it
        # What the job resume prints NOW: the new selection when it was written, else the stored resume as it is.
        conflicts = getattr(settled, "conflicts", ()) if settled is not None and proposed is None else ()
        check = check_selection(rows, printed_ids(stored.result) if stored is not None else (), conflicts=conflicts) if stored is not None else None  # type: ignore[attr-defined]
        if repick and previous is not None:
            if check is not None:
                record = with_selection(previous, now=now, check=check, selection=selection, proposed=proposed, selection_error=selection_error)
            else:  # no job resume, before and after: only why no selection could be made changes
                record = replace(previous, updated_at=now, selection_error=selection_error)
        else:
            record = merged(
                previous, profile_id=str(profile_id or "ephemeral"), job_identity=identity, stored_path=os.fspath(record_path), now=now,
                basis=basis_of(assessment, master_source), gate=gate_json(gate_record, check if selection is not None else None),
                # With no job resume there is nothing printed to check a row against: the rows are stored without a coverage.
                requirements=check.rows if check is not None else tuple(CoverageRow(row.id, row.requirement_class, row.status, row.sources) for row in rows),
                suggested=suggested, selection=selection, proposed=proposed, selection_error=selection_error,
            )
        save_record(record)
    return record


def use_proposed(home_root: Path, target: Path, profile_id: str | None, job_identity: str, *, now: str) -> SuggestionRecord:
    """Take the proposed selection: its resume REPLACES the stored job resume (the one explicit step that does), and
    ``proposed`` becomes ``selection``. Raises ``SuggestionError``: ``no_proposed_resume`` when nothing is proposed,
    ``proposal_stale`` when the stored resume is not the one the proposal was made beside (an edit since: nothing is
    replaced), ``stored_resume_unreadable`` when the stored file cannot be read (it is left as it is)."""

    from .tailored_resume import TailorResponse, read_tailored_resume, save_tailor_response, tailored_resume_path, tailored_resume_write_lock

    record_path = suggestions_path(home_root, target, profile_id, job_identity)
    resume_path = tailored_resume_path(Path(home_root), Path(target), profile_id, job_identity)
    sibling = proposed_resume_path(record_path)
    with tailored_resume_write_lock(resume_path), record_write_lock(record_path):
        record = read_record(record_path)
        if record is None or record.proposed is None or sibling.is_symlink() or not sibling.is_file():
            raise SuggestionError("no_proposed_resume", "no new suggested resume is waiting for this job")
        stored = read_tailored_resume(resume_path)
        if stored is None and stored_unreadable(resume_path):
            raise SuggestionError("stored_resume_unreadable", STORED_UNREADABLE)
        if stored is not None and proposal_is_stale(record.proposed, stored):
            raise SuggestionError("proposal_stale", PROPOSAL_STALE)
        response = replace(TailorResponse.from_json(parse_json_bytes(sibling.read_bytes())), updated_at=now)
        save_tailor_response(response, home_root=Path(home_root))
        taken = {key: value for key, value in record.proposed.items() if key != "against"}
        taken["resume"] = {"stored_path": os.fspath(resume_path), "markdown_sha256": _markdown_digest(response.markdown), "origin": "pick"}
        rows = [RequirementRow(row.id, row.requirement_class, row.status, row.sources) for row in record.requirements]
        printed = printed_ids(response.result)
        taken, conflicts = live_selection(taken, printed)  # type: ignore[assignment]
        check = check_selection(rows, printed, conflicts=conflicts)
        record = with_selection(record, now=now, check=check, selection=taken, proposed=None)
        save_record(record)
        sibling.unlink(missing_ok=True)
    return record


def dismiss_proposed(home_root: Path, target: Path, profile_id: str | None, job_identity: str, *, now: str) -> SuggestionRecord | None:
    """Drop the proposed selection and its sibling file; the stored job resume is not touched. ``None``: no record."""

    record_path = suggestions_path(home_root, target, profile_id, job_identity)
    with record_write_lock(record_path):
        record = read_record(record_path)
        if record is None:
            return None
        proposed_resume_path(record_path).unlink(missing_ok=True)
        if record.proposed is not None:
            record = replace(record, updated_at=now, proposed=None)
            save_record(record)
    return record


__all__ = [
    "ANSWER_ONLY_WHY",
    "CHANGE_RETIRED",
    "CHANGE_REWORDED",
    "COVERAGES",
    "COVERAGE_ANSWER_ONLY",
    "COVERAGE_KEPT",
    "COVERAGE_LOST",
    "COVERAGE_NONE",
    "HOWS",
    "PROPOSAL_STALE",
    "REASON_CONFLICT",
    "REASON_LOST_EVIDENCE",
    "SCHEMA_VERSION",
    "SOURCES",
    "STALE_ASSESSMENT",
    "STALE_ASSESSMENT_NEWER",
    "STALE_MASTER_NEWER",
    "STALE_PICKED_LINE",
    "STALE_RULES",
    "STATUSES",
    "STORED_UNREADABLE",
    "CheckReason",
    "CoverageRow",
    "RequirementRow",
    "Resolved",
    "SelectionCheck",
    "Suggestion",
    "SuggestionError",
    "SuggestionRecord",
    "add_suggestion",
    "basis_of",
    "changed_lines",
    "check_selection",
    "dismiss_proposed",
    "drop_proposal_after_edit",
    "gate_json",
    "is_answer_source",
    "is_replaceable",
    "job_resume",
    "live_selection",
    "PAGE_CONFLICTS",
    "without_page_reasons",
    "marks_json",
    "merged",
    "printed_copies",
    "printed_ids",
    "proposal_is_stale",
    "proposed_resume_path",
    "read_record",
    "read_suggestions",
    "record_write_lock",
    "recorded_marks",
    "requirement_rows",
    "resolve_suggestion",
    "result_digest",
    "revision_of",
    "save_record",
    "stale",
    "stale_for",
    "stale_view",
    "store_assessed",
    "stored_unreadable",
    "suggestions_dir",
    "suggestions_path",
    "use_proposed",
    "with_selection",
]
