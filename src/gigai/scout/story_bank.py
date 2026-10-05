"""0.1.10.7 C: the user's answers -- every answered question, kept once and reused by every profile.

USER-LEVEL.  Since 0.1.10.7 an answer belongs to the user, not to a profile
(``story-bank-contract.md``): one answer per ``question_id`` for the whole
Scout project, read by every profile's assessment and tailoring. The
0.1.10.5 per-profile bank and its sharing setting are gone; ``migrate``
moves what they held (once, see below). Stories, the longer experiences, live
beside the answers in ``stories.py``.

WHERE.  The answer text stays where it always was, in the gig's
``experience_qa`` native records (``experience_answers.py``): the journal
keeps every revision. What the records cannot hold (their schema is closed)
is one small local file:

    <home>/scout/<project_id>/story_bank/answers.json

    {"schema_version": "scout-answers:1",
     "migration": {"at", "answers", "merged", "conflicts", "profiles", "from"},
     "answers": {"<question_id>": {
         "record_id": "<the record that holds THE answer>",
         "tag": "<tag>" | null,
         "created_at": "...", "updated_at": "...",
         "revision": 3, "written_by": "operator" | "agent",
         "source": "<free text: where the answer came from>" | absent,
         "history": [{"at", "by", "action", "answer"?}],     # newest last, capped
         "edited": true|false, "confirmed_from": "<question_id>" | null,
         "jobs": [{"job_identity", "title", "company", "url", "kind", "at"}],
         "v1_marks": ["<mark>", ...]}}}                       # see MIGRATION

An ``experience_qa`` row whose id has an entry naming ANOTHER record is not
the answer (a second profile's, superseded at migration): it is never read.
A row with no entry (written by an older command) is listed with revision 0.

AN ANSWER, as every surface returns it (``BankEntry.to_json``): ``question_id``,
``question``, ``answer``, ``tag``, ``jobs`` (the postings that asked or
reused it), ``written_by``, ``source``, ``created_at``, ``updated_at``,
``revision``, and ``history``.

TWO WRITERS.  The user's agent (API, CLI ``--as agent``) and the user
write the same answers. Every write bumps the answer's ``revision`` and
records who wrote it. 0110-10-04: a write that does not say who it is, is
the operator's on the CLI and in the Scout UI, and the AGENT's on the
loopback API outside the UI (``api/story_bank.py``); ``source`` is free text
the writer may add, where the answer came from ("from the user's repo, at
the user's request"). It describes the answer's text: a new text without a
source lets it go. It is never sent to a model. An edit or a delete names the ``revision`` it read
(``expected_revision``); when the answer has changed since, the write is
refused with ``revision_conflict`` and the current answer
(``StoryBankError.entry``), so neither silently overwrites the other.

PRIVACY.  ``personal_info_in_answer`` runs on every write (email, phone,
links, a street address: the ``resume_pii`` shapes; GigAI stores no name, so
a name is not caught). What goes to a model is redacted again
(``resume_privacy.redact_inline``) and an answer that still shows a contact
detail is left out of the summaries.

REUSE.  ``assess_bank`` is what every assessment reads: PRIOR ANSWERS (exact
``question_id`` reuse), the STORY BANK lines (id, the question as asked, a
one-line answer: a requirement worded differently reuses the answer in the
same call) and, per job (``AssessBank.for_job``), the few stories that match
the posting (``stories.relevant_stories``). ``near_match`` is the model-free
check behind "We already know: ..., use it?".

MIGRATION (``migrate``; runs once, by itself, on the first read or write).
The 0.1.10.5 file ``story_bank/bank.json`` kept answers per profile. Every
answer of every profile becomes a user-level answer. Two profiles that
answered the same question: the newest write is THE answer; the other is
counted as ``merged`` when it says the same thing and as a ``conflict`` when
it does not, and then its text is kept in the answer's ``history`` (and stays
in its own record in the journal, untouched). Jobs are unioned. Nothing is
stamped: ``revision`` and ``updated_at`` stay what they were, and
``v1_marks`` records the marks a 0.1.10.5 assessment sealed for the answer,
so migrating marks no assessment stale. The old file is kept as
``bank.v1.json``. A second run does nothing and counts 0.

Local and model-free: nothing here calls a model or the network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import threading

from ..private_records import PrivateRecordError
from ..workpad import one_operation
from . import experience_answers
from .experience_answers import AnswerScope, PriorAnswer
from .find_jobs.discovery.storage import atomic_write, project_id
from ..canonical import digest_imported_bytes
from .assessment_core import BankAnswer
from .assessment_core import PriorAnswer as CorePriorAnswer
from .question_ids import normalize_question_id
from .resume_pii import detect_contact_details
from .resume_privacy import guard_name, is_name_line, redact_inline

SCHEMA_VERSION = "scout-answers:1"
#: The 0.1.10.5 per-profile overlay ``migrate`` reads.
V1_SCHEMA_VERSION = "scout-story-bank:1"
#: ``AssessBank.profile_id`` (and the sealed stamp's) when no profile is named: the bank is the user's.
USER_SCOPE = "user"

#: The answers the assess prompt is offered as STORY BANK lines, at most.
MAX_PROMPT_SUMMARIES = 40
#: One summary line's answer part, at most.
MAX_SUMMARY_CHARS = 160
_MAX_SUMMARY_QUESTION_CHARS = 160
#: The question's own words, as kept with the answer (the record's ``prompt`` bound is 4,096).
MAX_QUESTION_CHARS = 700
MAX_TAG_CHARS = 40
MAX_SOURCE_CHARS = 300
_MAX_JOBS = 50
_MAX_HISTORY = 20

ACTOR_OPERATOR = "operator"
ACTOR_AGENT = "agent"
ACTORS: tuple[str, ...] = (ACTOR_OPERATOR, ACTOR_AGENT)
#: A near match needs at least this score (0..1); 1.0 is the same id.
NEAR_MATCH_THRESHOLD = 0.5

JOB_ANSWERED = "answered"
JOB_REUSED = "reused"
JOB_CONFIRMED = "confirmed"
#: A story an assessment cited as evidence (``stories.py``).
JOB_USED = "used"

_TAG = re.compile(r"\A[a-z0-9][a-z0-9 _-]{0,39}\Z")
_ANSWER_TRIGGER = "answer:"

_LOCK = threading.Lock()
#: Held across a whole write (the stale check and the write it guards) and the
#: one-time migration. In-process only: the server's threads. A CLI write
#: racing a server write can still slip between the check and the write.
_WRITE_LOCK = threading.RLock()


class StoryBankError(ValueError):
    """An answers or stories call was refused; ``code`` is the API/CLI error code."""

    def __init__(self, code: str, message: str, *, entry: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        #: ``revision_conflict`` / ``story_exists``: the answer or story as it is now (has ``to_json``).
        self.entry = entry


def actor_value(actor: str | None) -> str:
    """``operator`` (the default) or ``agent``; anything else is ``invalid_value``."""

    if actor is None or not str(actor).strip():
        return ACTOR_OPERATOR
    clean = str(actor).strip().lower()
    if clean not in ACTORS:
        raise StoryBankError("invalid_value", "actor must be operator or agent")
    return clean


def clean_source(source: object) -> str | None:
    """An answer's ``source`` as it is stored: one line of free text, or ``None`` for none.

    Free text, never an enum: where the answer came from, in the writer's
    words. Checked for contact shapes like every other text of an answer.
    """

    if source is None:
        return None
    if not isinstance(source, str):
        raise StoryBankError("invalid_value", "source must be a string")
    clean = " ".join(source.split())
    if len(clean) > MAX_SOURCE_CHARS:
        raise StoryBankError("invalid_value", f"source must be at most {MAX_SOURCE_CHARS} characters")
    if clean:
        refuse_personal_info(clean, what="this source")
    return clean or None


def revision_value(value: object, *, required: bool = False) -> int | None:
    """The ``revision`` a write names (an integer, or its decimal string); ``invalid_value`` otherwise."""

    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise StoryBankError("invalid_value", "revision is required: the revision of the answer or story you read")
        return None
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise StoryBankError("invalid_value", "revision must be a whole number: the revision you read")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- the contact-data check ------------------------------------------------------------


def personal_info_in_answer(text: str, *, names: Iterable[str] = ()) -> list[str]:
    """What looks like contact data in a text: ``[]`` when nothing does.

    Shape-only (operator decision Q1 of the agent-security spike): the paste
    path's contact shapes (``resume_pii.detect_contact_details``: email,
    phone, linkedin/github links, a street address) and any other link
    ``resume_privacy.redact_inline`` would remove. ``name`` only when the
    text holds one of ``names`` (a name the caller knows). 0110-046: GigAI
    stores no name, so the callers pass none and a name inside an answer is
    not caught (test_story_bank_name_check.py pins this); there is no
    name-shape fallback, because an answer that is two capitalised words
    ("Apache Kafka", "Google Cloud") is usually the answer.
    """

    found = list(detect_contact_details(text))
    if not found and any(redact_inline(line) != line for line in text.splitlines() or [text]):
        found.append("links")
    flat = " ".join(text.split()).casefold()
    known = [" ".join(name.split()).casefold() for name in names if isinstance(name, str) and name.strip()]
    if any(name in flat for name in known):
        found.append("name")
    return found


def refuse_personal_info(text: str, *, what: str, names: Iterable[str] = ()) -> None:
    """``personal_info_refused`` when ``text`` holds a contact shape. Every answer and story write calls it."""

    found = personal_info_in_answer(text, names=names)
    if found:
        raise StoryBankError(
            "personal_info_refused",
            f"{what} looks like it holds personal information ({', '.join(found)}); answers and stories hold experience, never "
            "a name or contact details (GigAI stores none: you type them only when you generate a PDF). Remove it and save again",
        )


# --- tags ------------------------------------------------------------------------------

#: Checked in order against the question's words (then its id): the first hit is the tag.
_KEYWORD_TAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("leadership", ("lead", "led", "leading", "leadership", "mentor", "mentored", "mentoring", "manage", "managed", "managing", "manager", "reports", "hiring")),
    ("conflict", ("conflict", "disagree", "disagreed", "disagreement", "pushback", "difficult")),
    ("failure", ("fail", "failed", "failure", "mistake", "outage", "incident", "postmortem")),
    ("collaboration", ("stakeholder", "stakeholders", "cross-functional", "collaborate", "collaborated", "collaboration", "partner", "partnered")),
    ("system-design", ("architecture", "architect", "architected", "scale", "scaled", "scaling", "distributed", "design", "designed")),
    ("delivery", ("shipped", "ship", "launch", "launched", "deadline", "delivered", "delivery", "roadmap")),
)
_CATEGORY_TAGS: dict[str, str] = {
    "years": "experience-level", "seniority": "experience-level",
    "cloud": "technical", "language": "technical", "framework": "technical", "tooling": "technical", "tool": "technical",
    "database": "technical", "skill": "skill", "pattern": "technical",
    "domain": "domain", "industry": "domain",
    "location": "eligibility", "authorization": "eligibility", "eligible": "eligibility", "auth": "eligibility",
    "visa": "eligibility", "sponsorship": "eligibility", "clearance": "eligibility", "remote": "eligibility",
    "education": "education",
}
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.-]*")


def tag_for(question_id: str, question: str = "") -> str:
    """The competency/theme tag of one question: model-free and stable.

    Behavioural themes first, from the question's words (leadership,
    conflict, failure, collaboration, system-design, delivery); else the
    theme of the id's category (``cloud:gcp`` -> ``technical``,
    ``years:python`` -> ``experience-level``, ``location:us_region`` ->
    ``eligibility``); else ``other``.
    """

    normalized = normalize_question_id(question_id)
    category = normalized.split(":", 1)[0] if ":" in normalized else ""
    by_category = next((tag for token in category.split("_") if (tag := _CATEGORY_TAGS.get(token))), None)
    if by_category in ("eligibility", "experience-level", "education"):
        return by_category
    words = set(_WORD.findall(f"{question} {normalized.replace(':', ' ').replace('_', ' ')}".lower()))
    for tag, keywords in _KEYWORD_TAGS:
        if words & set(keywords):
            return tag
    return by_category or "other"


# --- the files ---------------------------------------------------------------------------


def bank_dir(home_root: Path, target: Path) -> Path:
    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "story_bank"


def bank_path(home_root: Path, target: Path) -> Path:
    """``answers.json``: the user-level answers' dates, writers, tags and jobs."""

    return bank_dir(home_root, target) / "answers.json"


def stories_path(home_root: Path, target: Path) -> Path:
    """``stories.json``: the user-level stories (``stories.py``)."""

    return bank_dir(home_root, target) / "stories.json"


def v1_path(home_root: Path, target: Path) -> Path:
    """The 0.1.10.5 per-profile overlay, before ``migrate``."""

    return bank_dir(home_root, target) / "bank.json"


def v1_kept_path(home_root: Path, target: Path) -> Path:
    """The 0.1.10.5 overlay as ``migrate`` keeps it."""

    return bank_dir(home_root, target) / "bank.v1.json"


def _empty_file() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "migration": None, "answers": {}}


def _read_file(home_root: Path, target: Path) -> dict[str, object] | None:
    """``answers.json``; ``None`` when it does not exist yet (not migrated); empty when it cannot be read."""

    try:
        path = bank_path(home_root, target)
    except Exception as exc:  # noqa: BLE001 - any failure to name the project is one typed refusal
        raise StoryBankError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return _empty_file()
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return _empty_file()
    answers = value.get("answers")
    return {
        "schema_version": SCHEMA_VERSION,
        "migration": value.get("migration") if isinstance(value.get("migration"), dict) else None,
        "answers": {str(k): v for k, v in answers.items() if isinstance(v, dict)} if isinstance(answers, dict) else {},
    }


def _write_file(home_root: Path, target: Path, data: Mapping[str, object]) -> None:
    atomic_write(bank_path(home_root, target), json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))


def _metas(data: Mapping[str, object]) -> dict[str, dict[str, object]]:
    answers = data.get("answers")
    assert isinstance(answers, dict)
    return answers


# --- the gig ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Gig:
    home_root: Path
    target: Path
    gig_id: str
    resolved: object


def _gig(home_root: Path, target: Path) -> _Gig:
    from ..workpad import resolve_workpad

    try:
        resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except Exception as exc:  # noqa: BLE001 - any failure to resolve the gig is one typed refusal
        raise StoryBankError("target_unavailable", "no Scout gig is available for this folder") from exc
    return _Gig(home_root=Path(home_root), target=Path(target), gig_id=resolved.gig_id, resolved=resolved)


@dataclass(frozen=True)
class _AnswerEvent:
    """One ``answer:<question_id>`` history entry of a stored assessment."""

    profile_id: str
    question_id: str
    at: str
    job_identity: str
    title: str
    company: str
    url: str | None


def _answer_events(gig: _Gig) -> list[_AnswerEvent]:
    """Every answer a stored assessment's history recorded, oldest first.

    This is how an answer knows the postings that asked it even when the
    write named none: ``POST /api/answers`` re-assessed the job with the
    trigger ``answer:<question_id>``.
    """

    from .quick_assess import list_quick_assessments

    events: list[_AnswerEvent] = []
    try:
        items = list_quick_assessments(gig.home_root, gig.target)
    except Exception:  # noqa: BLE001 - no readable store means no history, not an error
        return events
    for item in items:
        for entry in item.history:
            if not entry.trigger.startswith(_ANSWER_TRIGGER):
                continue
            events.append(
                _AnswerEvent(
                    profile_id=item.resume.profile_id or "",
                    question_id=normalize_question_id(entry.trigger[len(_ANSWER_TRIGGER):]),
                    at=entry.at,
                    job_identity=item.job.job_identity,
                    title=item.job.title,
                    company=item.job.company,
                    url=item.job.source_url,
                )
            )
    events.sort(key=lambda event: event.at)
    return events


# --- the view --------------------------------------------------------------------------


@dataclass(frozen=True)
class BankJob:
    """A posting an answer or a story is tied to: it asked, reused, confirmed or used it (``kind``)."""

    job_identity: str
    title: str
    company: str
    url: str | None
    kind: str
    at: str

    def to_json(self) -> dict[str, object]:
        return {"job_identity": self.job_identity, "title": self.title, "company": self.company, "url": self.url, "kind": self.kind, "at": self.at}


def stored_jobs(stored: object) -> list[BankJob]:
    """The ``jobs`` list of a stored answer or story, tolerantly."""

    out: list[BankJob] = []
    for item in stored if isinstance(stored, list) else ():
        if not isinstance(item, dict) or not isinstance(item.get("job_identity"), str):
            continue
        out.append(
            BankJob(
                item["job_identity"],
                str(item.get("title") or ""),
                str(item.get("company") or ""),
                item.get("url") if isinstance(item.get("url"), str) else None,
                str(item.get("kind") or JOB_ANSWERED),
                str(item.get("at") or ""),
            )
        )
    return out


def stored_history(stored: object) -> tuple[Mapping[str, str], ...]:
    """The ``history`` list of a stored answer or story, tolerantly: ``{"at", "by", "action"}`` (+ ``answer``)."""

    out: list[Mapping[str, str]] = []
    for item in stored if isinstance(stored, list) else ():
        if not isinstance(item, dict):
            continue
        entry = {"at": str(item.get("at") or ""), "by": str(item.get("by") or ACTOR_OPERATOR), "action": str(item.get("action") or "")}
        if isinstance(item.get("answer"), str):
            entry["answer"] = item["answer"]
        out.append(entry)
    return tuple(out)


@dataclass(frozen=True)
class BankEntry:
    """One answer, as every surface returns it (``to_json`` is the contract's Answer)."""

    question_id: str
    question: str
    answer: str
    tag: str
    created_at: str | None
    updated_at: str | None
    jobs: tuple[BankJob, ...]
    record_id: str
    revision_id: str
    #: How many times it was written (0: saved by a command older than the bank). A write names the value it read.
    revision: int = 0
    #: Who wrote it last: ``operator`` or ``agent``.
    written_by: str = ACTOR_OPERATOR
    #: Where the answer came from, in the writer's own words (free text); ``None`` when the writer said nothing.
    source: str | None = None
    #: The last writes, oldest first: ``{"at", "by", "action"}``; a text superseded at migration also carries ``answer``.
    history: tuple[Mapping[str, str], ...] = ()
    edited: bool = False
    confirmed_from: str | None = None
    #: The marks a 0.1.10.5 assessment sealed for this answer (``migrate``); empty once it is written again.
    v1_marks: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "answer": self.answer,
            "tag": self.tag,
            "jobs": [job.to_json() for job in self.jobs],
            "written_by": self.written_by,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "history": [dict(item) for item in self.history],
        }


def _jobs(meta: Mapping[str, object], events: Iterable[_AnswerEvent], question_id: str) -> tuple[BankJob, ...]:
    seen: dict[tuple[str, str], BankJob] = {}
    for event in events:
        if event.question_id == question_id:
            seen[(event.job_identity, JOB_ANSWERED)] = BankJob(event.job_identity, event.title, event.company, event.url, JOB_ANSWERED, event.at)
    for job in stored_jobs(meta.get("jobs")):
        seen[(job.job_identity, job.kind)] = job
    return tuple(sorted(seen.values(), key=lambda job: (job.at, job.job_identity, job.kind)))


def _entry(row: PriorAnswer, meta: Mapping[str, object], events: Iterable[_AnswerEvent]) -> BankEntry:
    question = row.prompt if row.prompt and row.prompt != row.question_id else row.question_id
    own_tag = meta.get("tag")
    jobs = _jobs(meta, events, row.question_id)
    created = meta.get("created_at") if isinstance(meta.get("created_at"), str) else None
    updated = meta.get("updated_at") if isinstance(meta.get("updated_at"), str) else None
    confirmed = meta.get("confirmed_from")
    marks = meta.get("v1_marks")
    return BankEntry(
        question_id=row.question_id,
        question=question,
        answer=row.answer,
        tag=own_tag if isinstance(own_tag, str) and own_tag else tag_for(row.question_id, question),
        created_at=created or (jobs[0].at if jobs else None),
        updated_at=updated or row.recorded_at or None,
        jobs=jobs,
        record_id=row.record_id,
        revision_id=row.revision_id,
        revision=meta["revision"] if type(meta.get("revision")) is int else 0,  # type: ignore[arg-type]
        written_by=meta["written_by"] if meta.get("written_by") in ACTORS else ACTOR_OPERATOR,  # type: ignore[arg-type]
        source=meta["source"] if isinstance(meta.get("source"), str) and meta["source"] else None,  # type: ignore[arg-type]
        history=stored_history(meta.get("history")),
        edited=bool(meta.get("edited")),
        confirmed_from=confirmed if isinstance(confirmed, str) else None,
        v1_marks=tuple(str(mark) for mark in marks) if isinstance(marks, list) else (),
    )


def _the_rows(gig: _Gig, metas: Mapping[str, Mapping[str, object]]) -> dict[str, PriorAnswer]:
    """``question_id -> the row that holds THE answer``: a row in another record than the entry names is not it."""

    rows = experience_answers.list_answer_rows(home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id)
    chosen: dict[str, PriorAnswer] = {}
    for row in rows:
        named = metas.get(row.question_id, {}).get("record_id")
        if isinstance(named, str) and named and named != row.record_id:
            continue
        chosen[row.question_id] = row
    return chosen


def _load(gig: _Gig) -> dict[str, object]:
    """``answers.json``, migrating the 0.1.10.5 bank first when it was never written."""

    data = _read_file(gig.home_root, gig.target)
    if data is not None:
        return data
    with _WRITE_LOCK:
        data = _read_file(gig.home_root, gig.target)
        if data is None:
            data = _migrate(gig)
        return data


def read_bank(*, home_root: Path, target: Path, with_jobs: bool = True) -> tuple[BankEntry, ...]:
    """Every answer of the user, sorted by question id.

    ``with_jobs=False`` skips reading the stored assessments (the assess and
    tailoring paths: they only need the answers); the jobs a write recorded
    are still there.
    """

    gig = _gig(Path(home_root), Path(target))
    metas = _metas(_load(gig))
    events = _answer_events(gig) if with_jobs else []
    rows = _the_rows(gig, metas)
    return tuple(_entry(rows[question_id], metas.get(question_id, {}), events) for question_id in sorted(rows))


def get_answer(*, home_root: Path, target: Path, question_id: str, with_jobs: bool = True) -> BankEntry | None:
    """One answer, or ``None``."""

    wanted = normalize_question_id(question_id)
    return next((entry for entry in read_bank(home_root=home_root, target=target, with_jobs=with_jobs) if entry.question_id == wanted), None)


def answers_for_reuse(*, home_root: Path, target: Path, names: Iterable[str] = (), strict: bool = False) -> dict[str, PriorAnswer]:
    """``question_id -> answer`` for everything the tailoring may cite.

    Contact details are redacted from the text and the words of ``names``
    (the candidate's name, when the caller knows it) removed: an answer saved
    before the contact-data check may hold some. ``strict`` raises
    ``StoryBankError`` (no gig) instead of answering ``{}``.
    """

    names = tuple(names)
    try:
        entries = read_bank(home_root=home_root, target=target, with_jobs=False)
    except (StoryBankError, PrivateRecordError):
        if strict:
            raise
        return {}
    return {
        entry.question_id: PriorAnswer(
            question_id=entry.question_id,
            prompt=entry.question,
            answer=_redacted(entry.answer, names),
            record_id=entry.record_id,
            revision_id=entry.revision_id,
            recorded_at=entry.updated_at or "",
        )
        for entry in entries
    }


# --- migration from the 0.1.10.5 per-profile bank ---------------------------------------------


def _read_v1(home_root: Path, target: Path) -> dict[str, object] | None:
    """The 0.1.10.5 overlay (``bank.json``, else the kept ``bank.v1.json``); ``None`` when there is none."""

    for path in (v1_path(home_root, target), v1_kept_path(home_root, target)):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            value = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("schema_version") != V1_SCHEMA_VERSION:
            continue
        records = value.get("records")
        profiles = value.get("profiles")
        return {
            "records": {str(k): str(v) for k, v in records.items()} if isinstance(records, dict) else {},
            "profiles": {str(k): v for k, v in profiles.items() if isinstance(v, dict)} if isinstance(profiles, dict) else {},
        }
    return None


def _v1_meta(v1: Mapping[str, object], profile_id: str, question_id: str) -> dict[str, object]:
    profiles = v1.get("profiles")
    block = profiles.get(profile_id) if isinstance(profiles, dict) else None
    entries = block.get("entries") if isinstance(block, dict) else None
    meta = entries.get(question_id) if isinstance(entries, dict) else None
    return meta if isinstance(meta, dict) else {}


def _v1_marks(owner: str, row: PriorAnswer, meta: Mapping[str, object]) -> list[str]:
    """The two marks 0.1.10.5 sealed for one profile's entry (its own, and the pre-0110-041 record mark)."""

    revision = meta["revision"] if type(meta.get("revision")) is int else 0
    updated = meta.get("updated_at") if isinstance(meta.get("updated_at"), str) else None
    written = f"{revision}\n{updated or row.recorded_at or ''}" if revision > 0 else "0\n"  # type: ignore[operator]
    entry_mark = digest_imported_bytes(f"entry\n{owner}\n{row.record_id}\n{row.question_id}\n{written}".encode("utf-8"))[len("sha256:"):][:16]
    record_mark = digest_imported_bytes(f"{owner}\n{row.record_id}\n{row.revision_id}".encode("utf-8"))[len("sha256:"):][:16]
    return [entry_mark, record_mark]


def _migrate(gig: _Gig) -> dict[str, object]:
    """Write ``answers.json`` from the 0.1.10.5 bank (see the module docstring's MIGRATION); the data written."""

    from . import profile_records

    v1 = _read_v1(gig.home_root, gig.target)
    overlay: Mapping[str, object] = v1 or {"records": {}, "profiles": {}}
    records = overlay["records"]
    assert isinstance(records, dict)
    try:
        profiles = profile_records.list_profiles(gig.resolved)  # type: ignore[arg-type]  # deleted (archived) profiles too
    except Exception:  # noqa: BLE001 - a gig with no readable profiles still has its answers
        profiles = ()
    profile_ids = [item.profile_id for item in profiles]
    labels = {item.profile_id: item.label for item in profiles}
    try:
        default = profile_records.default_profile(profiles)
    except Exception:  # noqa: BLE001 - no default profile readable: the answers still migrate, unowned
        default = None
    default_id = "" if default is None else default.profile_id

    rows = experience_answers.list_answer_rows(home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id)
    # A record the 0.1.10.5 bank did not name was written before it: its
    # answers belonged to the profile whose assessment recorded the answer,
    # else to the default profile (what 0.1.10.5 read; only matters for marks).
    legacy_owner: dict[str, str] = {}
    if len(profile_ids) > 1 and any(row.record_id not in records for row in rows):
        for event in _answer_events(gig):
            if event.profile_id in profile_ids:
                legacy_owner[event.question_id] = event.profile_id

    held: dict[str, list[tuple[str, PriorAnswer, dict[str, object]]]] = {}
    for row in rows:
        owner = records.get(row.record_id) or legacy_owner.get(row.question_id) or default_id
        held.setdefault(row.question_id, []).append((owner, row, _v1_meta(overlay, owner, row.question_id)))

    def written_at(item: tuple[str, PriorAnswer, dict[str, object]]) -> tuple[float, bool, str]:
        owner, row, meta = item
        updated = meta.get("updated_at") if isinstance(meta.get("updated_at"), str) else None
        return (_sort_time(updated or row.recorded_at), owner == default_id, owner)

    answers: dict[str, dict[str, object]] = {}
    merged = conflicts = 0
    at = _now()
    for question_id, items in held.items():
        items.sort(key=written_at)
        owner, row, meta = items[-1]
        entry: dict[str, object] = {"record_id": row.record_id, "v1_marks": _v1_marks(owner, row, meta)}
        for key in ("tag", "updated_at", "revision", "written_by", "edited", "confirmed_from"):
            if meta.get(key) is not None:
                entry[key] = meta[key]
        created = [str(m["first_answered_at"]) for _o, _r, m in items if isinstance(m.get("first_answered_at"), str)]
        if created:
            entry["created_at"] = min(created)
        history = [dict(item) for item in stored_history(meta.get("history"))]
        jobs = {(job.job_identity, job.kind): job for _o, _r, m in items for job in stored_jobs(m.get("postings"))}
        for other_owner, other_row, other_meta in items[:-1]:
            if other_row.answer.strip() == row.answer.strip():
                merged += 1
                entry["v1_marks"] = [*entry["v1_marks"], *_v1_marks(other_owner, other_row, other_meta)]  # type: ignore[misc]
                continue
            conflicts += 1
            said = other_meta.get("updated_at") if isinstance(other_meta.get("updated_at"), str) else None
            history.append({
                "at": said or other_row.recorded_at or at,
                "by": other_meta["written_by"] if other_meta.get("written_by") in ACTORS else ACTOR_OPERATOR,
                "action": f"earlier answer from profile {labels.get(other_owner, other_owner) or 'unknown'}, replaced when answers became shared",
                "answer": other_row.answer,
            })
        if history:
            entry["history"] = sorted(history, key=lambda item: str(item.get("at") or ""))[-_MAX_HISTORY:]
        if jobs:
            entry["jobs"] = [job.to_json() for job in sorted(jobs.values(), key=lambda job: (job.at, job.job_identity, job.kind))][-_MAX_JOBS:]
        answers[question_id] = entry

    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "migration": {
            "at": at,
            "answers": len(answers),
            "merged": merged,
            "conflicts": conflicts,
            "profiles": len({owner for items in held.values() for owner, _r, _m in items if owner}),
            "from": V1_SCHEMA_VERSION if v1 is not None else None,
        },
        "answers": answers,
    }
    _write_file(gig.home_root, gig.target, data)
    old = v1_path(gig.home_root, gig.target)
    if old.is_file() and not old.is_symlink():
        try:
            os.replace(old, v1_kept_path(gig.home_root, gig.target))
        except OSError:
            pass
    return data


def migrate(*, home_root: Path, target: Path) -> dict[str, object]:
    """Move the 0.1.10.5 per-profile bank to user-level answers, once. The report.

    ``{"migrated": true, "answers", "merged", "conflicts", "profiles"}`` on
    the run that did it; afterwards ``{"migrated": false, "answers": 0,
    "merged": 0, "conflicts": 0, "first_run": {...}}`` (the report of the run
    that did). Every read and write runs it by itself; this is the explicit
    form (``gigai scout answers migrate``).
    """

    gig = _gig(Path(home_root), Path(target))
    with _WRITE_LOCK:
        data = _read_file(gig.home_root, gig.target)
        if data is not None:
            return {"migrated": False, "answers": 0, "merged": 0, "conflicts": 0, "profiles": 0, "first_run": data.get("migration")}
        report = _migrate(gig)["migration"]
        assert isinstance(report, dict)
        return {"migrated": True, **{key: report[key] for key in ("answers", "merged", "conflicts", "profiles")}, "first_run": report}


# --- writes --------------------------------------------------------------------------------


def job_json(job: Mapping[str, object] | None, kind: str, at: str) -> dict[str, object] | None:
    if not job or not isinstance(job.get("job_identity"), str) or not job["job_identity"]:
        return None
    return {
        "job_identity": job["job_identity"],
        "title": str(job.get("title") or "")[:300],
        "company": str(job.get("company") or "")[:300],
        "url": job.get("url") if isinstance(job.get("url"), str) else None,
        "kind": kind,
        "at": at,
    }


def add_job(meta: dict[str, object], item: dict[str, object] | None) -> None:
    if item is None:
        return
    jobs = [
        existing
        for existing in (meta.get("jobs") if isinstance(meta.get("jobs"), list) else [])
        if isinstance(existing, dict) and (existing.get("job_identity"), existing.get("kind")) != (item["job_identity"], item["kind"])
    ]
    jobs.append(item)
    meta["jobs"] = jobs[-_MAX_JOBS:]


def stamp(meta: dict[str, object], *, at: str, actor: str, action: str) -> None:
    """One more write of this answer or story: revision, when, who, what."""

    meta["revision"] = (meta["revision"] if type(meta.get("revision")) is int else 0) + 1  # type: ignore[operator]
    meta["updated_at"] = at
    meta["written_by"] = actor
    history = [item for item in (meta.get("history") if isinstance(meta.get("history"), list) else []) if isinstance(item, dict)]
    history.append({"at": at, "by": actor, "action": action})
    meta["history"] = history[-_MAX_HISTORY:]
    # Written again: no 0.1.10.5 assessment saw it as it is now.
    meta.pop("v1_marks", None)


def refuse_stale(current: object | None, expected_revision: int | None, *, what: str) -> None:
    """``revision_conflict`` (with the current answer or story) when it moved on since it was read."""

    if expected_revision is None or current is None:
        return
    if getattr(current, "revision") != expected_revision:
        raise StoryBankError(
            "revision_conflict",
            f"this {what} changed since you read it (now revision {getattr(current, 'revision')}, last written by "
            f"{getattr(current, 'written_by')}); read it again and retry",
            entry=current,
        )


def _clean_question(question: str | None) -> str:
    if question is None:
        return ""
    return " ".join(question.split())[:MAX_QUESTION_CHARS]


def clean_tag(tag: str) -> str:
    clean = " ".join(tag.split()).lower()
    if not _TAG.fullmatch(clean):
        raise StoryBankError("invalid_value", f"a tag must be 1 to {MAX_TAG_CHARS} lowercase letters, digits, spaces, '-' or '_'")
    return clean


@one_operation()
def save_answer(
    *,
    home_root: Path,
    target: Path,
    question_id: str,
    answer: str,
    question: str | None = None,
    job: Mapping[str, object] | None = None,
    confirmed_from: str | None = None,
    edited: bool = False,
    actor: str | None = None,
    tag: str | None = None,
    action: str | None = None,
    expected_revision: int | None = None,
    source: str | None = None,
) -> BankEntry:
    """Save one answer: the one write path for every surface. The answer as it now is.

    ``source`` (0110-10-04): where the answer came from, free text. It is
    about the answer's text, so a write that changes the text and names no
    source drops the one stored.

    The text goes through ``experience_answers.record_answer`` (a new
    revision of the record that holds this answer, else an append); the
    answers file gets the dates, the writer, the posting that asked (``job``:
    ``job_identity``, ``title``, ``company``, ``url``) and, for a confirmed
    near match, the question it came from. ``expected_revision``: the
    ``revision`` the caller read; ``revision_conflict`` when the answer has
    moved on, ``not_found`` when it is gone. Raises ``StoryBankError``
    (``personal_info_refused``, ``target_unavailable``...) or
    ``PrivateRecordError`` (``answer_invalid``...).
    """

    writer = actor_value(actor)
    chosen_source = clean_source(source)
    chosen_tag = None if tag is None or not tag.strip() else clean_tag(tag)
    refuse_personal_info(answer, what="this answer")
    clean_question = _clean_question(question)
    if clean_question:
        refuse_personal_info(clean_question, what="this question")
    normalized = normalize_question_id(question_id)
    with _WRITE_LOCK:
        gig = _gig(Path(home_root), Path(target))
        current = get_answer(home_root=home_root, target=target, question_id=normalized, with_jobs=False)
        if expected_revision is not None and current is None:
            raise StoryBankError("not_found", f"there is no answer for {normalized!r} (it was deleted since you read it)")
        refuse_stale(current, expected_revision, what="answer")
        result = experience_answers.record_answer(
            home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id,
            question_id=normalized, prompt=clean_question, answer=answer,
            scope=AnswerScope(
                existing_record=None if current is None else current.record_id,
                append_records=experience_answers.experience_record_ids(
                    home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id
                ),
            ),
        )
        at = _now()
        with _LOCK:
            data = _load(gig)
            meta = _metas(data).setdefault(normalized, {})
            meta["record_id"] = result.record_id
            if current is None:
                meta["created_at"] = at
            stamp(meta, at=at, actor=writer, action=action or ("answered" if current is None else "answer changed"))
            if chosen_source is not None:
                meta["source"] = chosen_source
            elif current is None or current.answer.strip() != answer.strip():
                meta.pop("source", None)  # another text: the stored source was about the one before
            if chosen_tag is not None:
                meta["tag"] = chosen_tag
            if edited:
                meta["edited"] = True
            if confirmed_from:
                meta["confirmed_from"] = normalize_question_id(confirmed_from)
            add_job(meta, job_json(job, JOB_CONFIRMED if confirmed_from else JOB_ANSWERED, at))
            _write_file(gig.home_root, gig.target, data)
        saved = get_answer(home_root=home_root, target=target, question_id=normalized)
    assert saved is not None
    return saved


def edit_answer(
    *,
    home_root: Path,
    target: Path,
    question_id: str,
    answer: str | None = None,
    question: str | None = None,
    tag: str | None = None,
    actor: str | None = None,
    expected_revision: int | None = None,
    source: str | None = None,
) -> BankEntry:
    """Change an answer's text, question words, tag and/or source; the updated answer.

    A new answer or question is a new revision through the answer path; a tag
    and a source are in the answers file only (``""`` puts the automatic tag
    back, and removes the source). ``expected_revision``: the ``revision``
    the caller read; ``revision_conflict`` when it is stale.
    """

    if answer is None and question is None and tag is None and source is None:
        raise StoryBankError("invalid_value", "give at least one of answer, question, tag or source")
    writer = actor_value(actor)
    chosen_source = clean_source(source)
    chosen_tag = None if tag is None else ("" if not tag.strip() else clean_tag(tag))
    if question is not None and not _clean_question(question):
        raise StoryBankError("invalid_value", "question must not be empty")
    normalized = normalize_question_id(question_id)
    with _WRITE_LOCK:
        current = get_answer(home_root=home_root, target=target, question_id=normalized, with_jobs=False)
        if current is None:
            raise StoryBankError("not_found", f"there is no answer for {normalized!r}")
        refuse_stale(current, expected_revision, what="answer")
        if answer is not None or question is not None:
            save_answer(
                home_root=home_root, target=target, question_id=normalized,
                answer=current.answer if answer is None else answer,
                question=question, edited=True, actor=writer, source=chosen_source,
                action="answer changed" if answer is not None else "question changed",
            )
        if chosen_tag is not None or (source is not None and answer is None and question is None):
            gig = _gig(Path(home_root), Path(target))
            with _LOCK:
                data = _load(gig)
                meta = _metas(data).setdefault(normalized, {})
                changed: list[str] = []
                if chosen_tag is not None:
                    meta["tag"] = chosen_tag or None
                    changed.append("tag")
                if source is not None and answer is None and question is None:
                    if chosen_source is None:
                        meta.pop("source", None)
                    else:
                        meta["source"] = chosen_source
                    changed.append("source")
                if answer is None and question is None:
                    meta.setdefault("record_id", current.record_id)
                    stamp(meta, at=_now(), actor=writer, action=" and ".join(changed) + " changed")
                _write_file(gig.home_root, gig.target, data)
        updated = get_answer(home_root=home_root, target=target, question_id=normalized)
    assert updated is not None
    return updated


def delete_answer(*, home_root: Path, target: Path, question_id: str, expected_revision: int | None = None) -> str:
    """Remove an answer: it is never listed, offered or sent again. The id removed.

    ``expected_revision`` as in ``edit_answer``: a stale delete is refused.
    Every record that holds an answer to this question lets go of it (a
    second profile's superseded one too): the journal keeps the older
    revisions, nothing reads them.
    """

    normalized = normalize_question_id(question_id)
    with _WRITE_LOCK:
        gig = _gig(Path(home_root), Path(target))
        current = get_answer(home_root=home_root, target=target, question_id=normalized, with_jobs=False)
        if current is None:
            raise StoryBankError("not_found", f"there is no answer for {normalized!r}")
        refuse_stale(current, expected_revision, what="answer")
        rows = experience_answers.list_answer_rows(home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id)
        for record_id in sorted({row.record_id for row in rows if row.question_id == normalized}):
            experience_answers.remove_answer(
                home_root=gig.home_root, requested_target=gig.target, gig_id=gig.gig_id, record_id=record_id, question_id=normalized,
            )
        with _LOCK:
            data = _load(gig)
            _metas(data).pop(normalized, None)
            _write_file(gig.home_root, gig.target, data)
        return normalized


def record_reuse(
    *, home_root: Path, target: Path, entries: Iterable[BankEntry], evidence: Iterable[str], posting: Mapping[str, object],
    stories: Iterable[object] = (),
) -> list[str]:
    """Note which answers and stories an assessment cited, on the posting it assessed.

    The assess prompt asks for ``Story bank <id>: ...`` in a row's evidence
    when it reuses one; every answer id found in ``evidence`` gets
    ``posting`` added to its jobs as ``reused``, every story id as ``used``.
    Returns the ids noted. Never raises: a failed note must not fail an
    assessment.
    """

    try:
        cited = cited_ids(evidence)
        if not cited:
            return []
        answer_ids = {entry.question_id for entry in entries}
        story_ids = {str(getattr(story, "story_id")) for story in stories}
        noted = [question_id for question_id in cited if question_id in answer_ids]
        at = _now()
        if noted:
            gig = _gig(Path(home_root), Path(target))
            _load(gig)  # migrated before the lock below is taken
            with _LOCK:
                data = _load(gig)
                for question_id in noted:
                    add_job(_metas(data).setdefault(question_id, {}), job_json(posting, JOB_REUSED, at))
                _write_file(Path(home_root), Path(target), data)
        used = [story_id for story_id in cited if story_id in story_ids and story_id not in answer_ids]
        if used:
            from . import stories as stories_module

            stories_module.record_use(home_root=Path(home_root), target=Path(target), story_ids=used, posting=posting)
        return [*noted, *used]
    except Exception:  # noqa: BLE001 - bookkeeping only
        return []


_BANK_CITATION = re.compile(r"story bank\s+([a-z0-9._-]+:[a-z0-9._-]+)")


def cited_ids(evidence: Iterable[str]) -> list[str]:
    """The ids a matrix's evidence cites (``Story bank <id>: ...``), in order, once each."""

    cited: list[str] = []
    for text in evidence:
        for token in _BANK_CITATION.findall(text.lower()):
            normalized = normalize_question_id(token)
            if normalized not in cited:
                cited.append(normalized)
    return cited


# --- what one assessment reads (quick assess and a find-jobs run alike) ----------------------


@dataclass(frozen=True)
class AssessBank:
    """The user's answers and stories as ONE assessment reads them.

    Built once by ``assess_bank`` for every path that renders the assess
    prompt (the job page's quick assessment, assess-all, a find-jobs run's
    assess node), so they offer the model the same thing: ``prior_answers``
    (PRIOR ANSWERS, exact ``question_id`` reuse) and ``bank_answers`` (the
    STORY BANK paragraph: at most ``MAX_PROMPT_SUMMARIES`` redacted one-line
    answers and, after ``for_job``, the few stories that match that job).
    ``entries`` are the answers and ``stories`` every story (``stories.Story``),
    for the reuse note, the near match and the staleness rule. ``marks`` maps
    every answer's and story's id to an opaque revision mark (never text) and
    ``digest`` is the digest of that map: what a run seals, and what the next
    run compares against. ``v1_marks`` are the marks a 0.1.10.5 assessment
    sealed for an answer that has not been written since (``migrate``), only
    so a basis recorded then still compares. ``profile_id`` is the profile
    being assessed (``USER_SCOPE`` when none is named): the bank itself is
    the user's and does not depend on it; ``None`` means no bank could be read.
    """

    profile_id: str | None
    entries: tuple[BankEntry, ...] = ()
    prior_answers: tuple[CorePriorAnswer, ...] = ()
    bank_answers: tuple[BankAnswer, ...] = ()
    marks: Mapping[str, str] = field(default_factory=dict)
    v1_marks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    stories: tuple[object, ...] = ()
    #: After ``for_job``: the ids of the stories put into that job's prompt.
    job_stories: tuple[str, ...] = ()
    #: The resume's own name line, when it has one: its words are removed from every line a model is offered.
    names: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return digest_imported_bytes("".join(f"{key}\t{self.marks[key]}\n" for key in sorted(self.marks)).encode("utf-8"))

    def for_job(self, *, title: str = "", text: str = "") -> "AssessBank":
        """This bank with the stories that match ONE job added to the STORY BANK lines.

        The stories are searched locally (``stories.relevant_stories``: an
        in-memory FTS5 table, the posting's title and keywords as the query)
        and only the few that match go into the prompt, as evidence the model
        may cite (``Story bank <story_id>: ...``). No story, or none that
        matches: this bank, unchanged. Never raises.
        """

        if not self.stories:
            return self
        try:
            from . import stories as stories_module

            found = stories_module.relevant_stories(self.stories, title=title, text=text)  # type: ignore[arg-type]
            lines = [(story, stories_module.prompt_line(story, names=self.names)) for story in found]
        except Exception:  # noqa: BLE001 - a search that fails offers no story; it never fails an assessment
            return self
        kept = [(story, line) for story, line in lines if line is not None]
        if not kept:
            return self
        return replace(
            self,
            bank_answers=(*self.bank_answers, *(line for _story, line in kept)),
            job_stories=tuple(story.story_id for story, _line in kept),
        )


def _mark(entry: BankEntry) -> str:
    """An opaque mark that changes when THIS answer is edited or answered again.

    Built from the answer's own write count and time (``revision`` /
    ``updated_at``, bumped by every write of this answer and by no other)
    and its record id. Not from the record's revision id: answers share a
    record, so that id changes for EVERY answer when any one of them is
    written. An answer never written through the bank (revision 0) keeps one
    mark until it is. No answer text and nothing derived from it.
    """

    written = f"{entry.revision}\n{entry.updated_at or ''}" if entry.revision > 0 else "0\n"
    return digest_imported_bytes(f"answer\n{entry.record_id}\n{entry.question_id}\n{written}".encode("utf-8"))[len("sha256:"):][:16]


def assess_bank(*, home_root: Path, target: Path, profile_id: str | None = None, resume_text: str = "") -> AssessBank:
    """The answers and stories one assessment reads. Never raises.

    User-level: every profile, and a pasted resume, reads the same bank.
    ``profile_id`` only names who is being assessed (it is sealed with the
    marks). No gig or an unreadable bank reads as no bank
    (``AssessBank(None)``): the prompt then renders as it did before the bank
    existed. ``resume_text``'s own name line, when it has one, has its words
    removed from every line a model is offered (as ``model_resume`` removes
    them from the resume); contact details are always redacted.
    """

    first_line = next((line.strip() for line in resume_text.splitlines() if line.strip()), "")
    names = (first_line,) if is_name_line(first_line) else ()
    try:
        entries = read_bank(home_root=home_root, target=target, with_jobs=False)
    except Exception:  # noqa: BLE001 - a bank that cannot be read answers nothing; it never fails an assessment
        return AssessBank(None)
    try:
        from . import stories as stories_module

        found = stories_module.list_stories(home_root=home_root, target=target)
        story_marks = {story.story_id: stories_module.mark(story) for story in found}
    except Exception:  # noqa: BLE001 - unreadable stories offer none
        found, story_marks = (), {}
    return AssessBank(
        profile_id=profile_id or USER_SCOPE,
        entries=entries,
        prior_answers=tuple(
            CorePriorAnswer(question_id=entry.question_id, prompt=entry.question, answer=_redacted(entry.answer, names)) for entry in entries
        ),
        bank_answers=tuple(
            BankAnswer(question_id=item.question_id, question=item.question, summary=item.summary)
            for item in prompt_summaries(entries, names=names)
        ),
        marks={**story_marks, **{entry.question_id: _mark(entry) for entry in entries}},
        v1_marks={entry.question_id: entry.v1_marks for entry in entries if entry.v1_marks},
        stories=tuple(found),
        names=names,
    )


#: ``BankMatch.match``: how a changed answer or story concerns one assessment.
MATCH_EXACT = "exact"
MATCH_NEAR = "near"
MATCH_CITED = "cited"


@dataclass(frozen=True)
class BankMatch:
    """Why the bank makes ONE assessment stale: a changed answer or story that concerns it.

    ``exact`` / ``near``: ``bank_question_id`` (an answer's ``question_id``,
    or a ``story_id``) was added or edited since the assessment's basis and
    answers its open ``question_id`` (the same id, the model-free
    ``near_match``, or a story about it). ``cited``: the assessment's
    evidence cites ``bank_question_id`` and it was edited or deleted
    (``question_id`` is then ``None``; ``bank_question`` is empty when it is
    gone). Ids and question words only, never an answer.
    """

    match: str
    bank_question_id: str
    bank_question: str = ""
    question_id: str | None = None
    question: str = ""

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"match": self.match, "bank_question_id": self.bank_question_id}
        if self.bank_question:
            value["bank_question"] = self.bank_question
        if self.question_id is not None:
            value["question_id"] = self.question_id
            if self.question:
                value["question"] = self.question
        return value


def changed_entries(sealed_marks: Mapping[str, str] | None, bank: AssessBank) -> tuple[BankEntry, ...]:
    """The answers of ``bank`` an assessment with ``sealed_marks`` never saw as they are now (added, or edited since)."""

    sealed = sealed_marks or {}
    return tuple(entry for entry in bank.entries if not _as_sealed(sealed, bank, entry.question_id))


def _as_sealed(sealed: Mapping[str, str], bank: AssessBank, key: str) -> bool:
    """Whether the bank's answer or story ``key`` is the one ``sealed`` recorded (both absent: nothing changed)."""

    mark = sealed.get(key)
    if mark is None:
        return key not in bank.marks
    return mark == bank.marks.get(key) or mark in bank.v1_marks.get(key, ())


def bank_matches(
    *,
    questions: Iterable[object],
    evidence: Iterable[str],
    sealed_marks: Mapping[str, str] | None,
    bank: AssessBank,
) -> tuple[BankMatch, ...]:
    """The changed answers and stories that concern ONE assessment; empty: the bank leaves it current.

    ``questions`` are the questions it left open (anything with a
    ``question_id`` and a ``question``, e.g. ``AssessmentQuestion``),
    ``evidence`` its matrix evidence, ``sealed_marks`` the bank it was made
    with (``None``: made before the bank was recorded, read as an empty bank)
    and ``bank`` the bank now. Targeted (0110-041, the 0110-039 basis):
    answering one question concerns the assessments that asked it, not every
    assessment that asked anything.

    - An answer ADDED or EDITED since (``changed_entries``) that answers one
      of ITS OWN open questions: the same id (``exact``), or the model-free
      ``near_match`` behind ``bank_suggestions`` (``near``).
    - A story ADDED or EDITED since that is about one of its open questions
      (``stories.answers_question``): ``near``.
    - It cites ``Story bank <id>`` and that answer or story was edited or
      deleted: ``cited``.

    An assessment with no open question that cites nothing is never stale,
    neither is any assessment while the bank is unchanged, and neither is one
    whose questions the changed answers and stories do not answer. In
    memory: changed entries x open questions, no read.
    """

    sealed = sealed_marks or {}
    found: list[BankMatch] = []
    asked = [(normalize_question_id(str(getattr(item, "question_id", "") or "")), str(getattr(item, "question", "") or "")) for item in questions]
    asked = [(question_id, question) for question_id, question in asked if question_id]
    if asked:
        for entry in changed_entries(sealed, bank):
            for question_id, question in asked:
                if entry.question_id == question_id:
                    kind = MATCH_EXACT
                elif near_match((entry,), question_id=question_id, question=question) is not None:
                    kind = MATCH_NEAR
                else:
                    continue
                found.append(BankMatch(kind, entry.question_id, _shown_question(entry), question_id, question))
        changed_stories = [story for story in bank.stories if not _as_sealed(sealed, bank, story.story_id)]  # type: ignore[attr-defined]
        if changed_stories:
            from . import stories as stories_module

            for story in changed_stories:
                for question_id, question in asked:
                    if stories_module.answers_question(story, question_id=question_id, question=question):  # type: ignore[arg-type]
                        found.append(BankMatch(MATCH_NEAR, story.story_id, _shown_text(story.title), question_id, question))  # type: ignore[attr-defined]
    by_id: dict[str, str] | None = None
    for cited in cited_ids(evidence):
        if _as_sealed(sealed, bank, cited):
            continue
        if by_id is None:
            by_id = {story.story_id: _shown_text(story.title) for story in bank.stories}  # type: ignore[attr-defined]
            by_id.update({entry.question_id: _shown_question(entry) for entry in bank.entries})
        found.append(BankMatch(MATCH_CITED, cited, by_id.get(cited, "")))
    return tuple(found)


def _shown_text(text: str) -> str:
    """Words for a reason line: contact details out, one line."""

    return one_line(_redacted(text), _MAX_SUMMARY_QUESTION_CHARS)


def _shown_question(entry: BankEntry) -> str:
    """The answer's question words for a reason line ("answered in your answers: ...")."""

    return _shown_text(entry.question or entry.question_id)


def bank_makes_stale(
    *,
    questions: Iterable[object],
    evidence: Iterable[str],
    sealed_marks: Mapping[str, str] | None,
    bank: AssessBank,
) -> bool:
    """Whether an assessment must be made again because the bank changed: ``bank_matches`` found something.

    One rule for a stored assessment (``assessment_basis``) and a run's
    unchanged skip (``proposal_execution._basis_stale``).
    """

    return bool(bank_matches(questions=questions, evidence=evidence, sealed_marks=sealed_marks, bank=bank))


# --- reuse: the prompt summaries and the near match ------------------------------------------


@dataclass(frozen=True)
class BankSummary:
    """One answer as the assess prompt gets it: id, question, one-line answer."""

    question_id: str
    question: str
    summary: str


def _redacted(text: str, names: Iterable[str] = ()) -> str:
    """``text`` as a model may see it: contact details out (``redact_inline``), name words out (``guard_name``)."""

    tokens = {word.lower() for name in names if isinstance(name, str) for word in re.findall(r"[^\W\d_]+", name) if len(word) > 1}
    out = "\n".join(redact_inline(line) for line in text.split("\n"))
    return guard_name(out, tokens) if tokens else out


def one_line(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """``text`` on one line, cut at a word boundary with ``...`` when over ``limit``."""

    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 3].rsplit(" ", 1)[0] or flat[: limit - 3]
    return cut.rstrip(" ,;:") + "..."


def prompt_summaries(entries: Iterable[BankEntry], *, names: Iterable[str] = (), limit: int = MAX_PROMPT_SUMMARIES) -> tuple[BankSummary, ...]:
    """What the assess prompt is offered: at most ``limit`` entries, privacy-checked.

    Each answer is redacted (contact details, and the words of ``names``)
    and cut to one line; an entry whose line still shows a contact detail is
    left out. Newest first.
    """

    names = tuple(names)
    ordered = sorted(entries, key=lambda entry: (-_sort_time(entry.updated_at), entry.question_id))
    out: list[BankSummary] = []
    for entry in ordered:
        if len(out) >= limit:
            break
        summary = one_line(_redacted(entry.answer, names))
        question = "" if entry.question == entry.question_id else one_line(_redacted(entry.question, names), _MAX_SUMMARY_QUESTION_CHARS)
        if not summary or personal_info_in_answer(f"{question} {summary}"):
            continue
        out.append(BankSummary(entry.question_id, question, summary))
    return tuple(out)


def _sort_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


_STOPWORDS = frozenset(
    "a an and any are as at be been being but by can could did do does for from had has have how i if in into is it its "
    "many me much my of on or our role so some than that the their them then there these they this those to us was we "
    "were what when where which while who whom why will with would you your experience experienced hands worked working "
    "work used using use familiar knowledge know background years year professional production prior please describe "
    "tell about other".split()
)
#: One word for one thing, so two wordings of it overlap.
_WORD_ALIASES: dict[str, str] = {
    "k8s": "kubernetes", "postgres": "postgresql", "golang": "go", "javascript": "js", "typescript": "ts",
    "node.js": "node", "nodejs": "node",
}
#: A product named by several words (in any order, as a normalized id sorts them) -> its one token.
_WORD_SETS: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (frozenset({"google", "cloud"}), frozenset({"platform"}), "gcp"),
    (frozenset({"amazon", "web", "service"}), frozenset(), "aws"),
    (frozenset({"microsoft", "azure"}), frozenset(), "azure"),
    (frozenset({"machine", "learning"}), frozenset(), "ml"),
    (frozenset({"continuous", "integration"}), frozenset(), "cicd"),
)
_FAMILY: dict[str, str] = {
    **{name: "technical" for name in ("cloud", "language", "framework", "tooling", "tool", "database", "skill", "pattern", "other")},
    **{name: "level" for name in ("years", "seniority")},
    **{name: "domain" for name in ("domain", "industry")},
}


@lru_cache(maxsize=4096)
def _tokens(text: str) -> frozenset[str]:
    flat = text.lower().replace("ci/cd", "cicd")
    for separator in "_:/-":
        flat = flat.replace(separator, " ")
    out: set[str] = set()
    for word in _WORD.findall(flat):
        word = _WORD_ALIASES.get(word.strip("."), word.strip("."))
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if word and word not in _STOPWORDS:
            out.add(word)
    for needed, also_dropped, token in _WORD_SETS:
        if needed <= out:
            out = (out - needed - also_dropped) | {token}
    return frozenset(out)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _id_parts(question_id: str) -> tuple[str, frozenset[str]]:
    normalized = normalize_question_id(question_id)
    category, _, value = normalized.partition(":")
    return (category, _tokens(value)) if value else ("", _tokens(normalized))


def similarity(question_id: str, question: str, entry: BankEntry) -> float:
    """How alike a new question and a bank entry are: 0..1, model-free.

    The larger of two overlaps (Jaccard over word tokens, stop words and
    plurals removed, a few product names unified): the two ids' value parts
    (scaled by 0.8 when their categories are not of one family: ``cloud`` and
    ``tooling`` are, ``years`` and ``language`` are not), and everything known
    about each question (id value plus its words). 1.0 for the same id.
    """

    if normalize_question_id(question_id) == entry.question_id:
        return 1.0
    category, value = _id_parts(question_id)
    entry_category, entry_value = _id_parts(entry.question_id)
    same_family = category == entry_category or (_FAMILY.get(category) is not None and _FAMILY.get(category) == _FAMILY.get(entry_category))
    id_score = _jaccard(value, entry_value) * (1.0 if same_family else 0.8)
    entry_words = entry_value | (_tokens(entry.question) if entry.question != entry.question_id else frozenset())
    text_score = _jaccard(value | _tokens(question), entry_words)
    return round(max(id_score, text_score), 3)


@dataclass(frozen=True)
class BankSuggestion:
    """A near match: "We already know: <answer>, use it?"."""

    question_id: str
    bank_question_id: str
    bank_question: str
    answer: str
    score: float

    def to_json(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "bank_question_id": self.bank_question_id,
            "bank_question": self.bank_question,
            "answer": self.answer,
            "score": self.score,
        }


def near_match(entries: Iterable[BankEntry], *, question_id: str, question: str = "") -> BankSuggestion | None:
    """The bank entry closest to this question, when it is close enough.

    ``None`` when the bank already answers this exact id (that is plain
    reuse, not a suggestion) or nothing scores ``NEAR_MATCH_THRESHOLD``.
    """

    normalized = normalize_question_id(question_id)
    best: tuple[float, BankEntry] | None = None
    for entry in entries:
        if entry.question_id == normalized:
            return None
        score = similarity(normalized, question, entry)
        if score >= NEAR_MATCH_THRESHOLD and (best is None or score > best[0]):
            best = (score, entry)
    if best is None:
        return None
    score, entry = best
    return BankSuggestion(normalized, entry.question_id, entry.question, entry.answer, score)


def suggestions_for(questions: Iterable[Mapping[str, object]], entries: Iterable[BankEntry]) -> list[dict[str, object]]:
    """One near match per open question that has one (``structured_questions`` items)."""

    bank = tuple(entries)
    out: list[dict[str, object]] = []
    for item in questions:
        question_id = item.get("question_id")
        if not isinstance(question_id, str):
            continue
        match = near_match(bank, question_id=question_id, question=str(item.get("question") or ""))
        if match is not None:
            out.append(match.to_json())
    return out


def attach_suggestions(payload: dict[str, object], *, home_root: Path, target: Path, cache: dict[str, tuple[BankEntry, ...]] | None = None) -> dict[str, object]:
    """Add ``bank_suggestions`` to one assess-response JSON, in place; returns it.

    Computed when the response is read, never stored: a deleted answer stops
    showing at once. Only added when a question has a near match. ``cache``
    (one key, the user's answers) lets a list read them once.
    """

    try:
        result = payload.get("result")
        questions = result.get("structured_questions") if isinstance(result, dict) else None
        if not questions:
            return payload
        bank = None if cache is None else cache.get(USER_SCOPE)
        if bank is None:
            bank = read_bank(home_root=home_root, target=target, with_jobs=False)
            if cache is not None:
                cache[USER_SCOPE] = bank
        found = suggestions_for(questions, bank)
        if found:
            payload["bank_suggestions"] = found
    except (StoryBankError, PrivateRecordError):
        pass
    return payload


__all__ = [
    "ACTORS",
    "ACTOR_AGENT",
    "ACTOR_OPERATOR",
    "MAX_PROMPT_SUMMARIES",
    "MAX_SOURCE_CHARS",
    "MAX_SUMMARY_CHARS",
    "NEAR_MATCH_THRESHOLD",
    "SCHEMA_VERSION",
    "USER_SCOPE",
    "AssessBank",
    "BankEntry",
    "BankJob",
    "BankMatch",
    "BankSuggestion",
    "BankSummary",
    "StoryBankError",
    "actor_value",
    "clean_source",
    "answers_for_reuse",
    "assess_bank",
    "attach_suggestions",
    "bank_makes_stale",
    "bank_matches",
    "bank_path",
    "changed_entries",
    "cited_ids",
    "delete_answer",
    "edit_answer",
    "get_answer",
    "migrate",
    "near_match",
    "one_line",
    "personal_info_in_answer",
    "prompt_summaries",
    "read_bank",
    "record_reuse",
    "refuse_personal_info",
    "revision_value",
    "save_answer",
    "similarity",
    "stories_path",
    "suggestions_for",
    "tag_for",
]
