"""0.1.11.7 T1: ``gigai scout story prep --job-url URL``: the rehearsal list of ONE job. No model call, nothing fetched, nothing written.

It reads the job's STORED assessment and prints, per requirement row, the questions to rehearse and what answers each:

- a row with an open question (``structured_questions``): that question;
- a ``met`` row: a walkthrough, "Tell me about <the master line>", once per master line the row cites; a story or an answer
  the row cites (``A story:<slug>``, ``A <question_id>``) is told as it stands;
- what answers a question: the stories that answer it (``stories.answers_question``), the saved answer under its id
  (``story_bank.get_answer``) or the near match to it (``story_bank.near_match``, ``NEAR_MATCH_THRESHOLD``), and the master
  lines the row cites. A question nothing answers is ``uncovered``.

HONESTY (SPIKE-prep-and-pathways section 6). The product writes questions, never answers: nothing here is an answer. Every claim
carries an id the user can open (a master line, ``A <question_id>``, a story). A row that rests ONLY on personal lab lines
is marked ``lab`` (``job_brief._with_lab``'s rule): familiarity, never production. A rehearsal mark is not evidence, so none
is read or written. The posting is untrusted text: a row shows at most :data:`MAX_POSTING_CHARS` characters of it
(``posting_words``: the open question's words, else the requirement's), and nothing else of it.

The reply holds both labels, like the cover-letter brief: ``_labels`` says ``posting_words`` is ``public-untrusted`` and the
master line text is ``user-private``.

Pure builder (:func:`build`, :func:`render`) and one loader that only reads (:func:`load`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import job_brief
from .data_labels import ENVELOPE_KEY, PUBLIC_UNTRUSTED, USER_PRIVATE, labels_envelope, normalized
from .master_lab import is_lab_text

SCHEMA_VERSION = "scout-story-prep-job:1"
#: The most characters of the posting one row shows (the spike's rule 3). The ellipsis counts.
MAX_POSTING_CHARS = 60
MAX_LINE_CHARS = 160

KIND_OPEN = "open_question"
KIND_WALKTHROUGH = "walkthrough"
KIND_STORY = "story"
KIND_ANSWER = "answer"

#: The keys of the JSON, documented in ``data/cli/commands.yaml``.
TOP_KEYS = ("ok", "schema_version", "labels", "_labels", "job_identity", "profile_id", "rows", "summary")
ROW_KEYS = ("id", "class", "status", "lab", "posting_words", "questions")
QUESTION_KEYS = ("kind", "question_id", "line_id", "line_text", "lab", "story_id", "answered_by", "uncovered")
ANSWERED_BY_KEYS = ("stories", "answer_id", "near_answer", "master_lines")

_SPACE = re.compile(r"[\s\x00-\x1f\x7f]+")
_STORY_SOURCE = "A story:"
_ANSWER_SOURCE = "A "


def clip(text: object, limit: int = MAX_POSTING_CHARS) -> str:
    """One line of at most ``limit`` characters (control characters and runs of space become one space; an ellipsis marks a cut)."""

    one = _SPACE.sub(" ", str(text or "")).strip()
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class Line:
    """A master line a row cites: its id, its text (the user's own) and whether it is a personal lab line."""

    id: str
    text: str
    lab: bool = False


@dataclass(frozen=True)
class PrepRow:
    """One requirement row as the builder reads it. ``requirement`` and ``question`` are the posting's words (clipped on the way out)."""

    id: str
    requirement_class: str | None
    status: str
    requirement: str
    sources: tuple[str, ...] = ()
    question_id: str | None = None
    question: str | None = None
    lab: bool = False


@dataclass(frozen=True)
class Banks:
    """What may answer a question: ``stories`` (``stories.Story``), the saved ``answers`` (``BankEntry``) and the master's ``lines`` by id."""

    stories: tuple[object, ...] = ()
    answers: tuple[object, ...] = ()
    lines: Mapping[str, Line] | None = None


def _answered_by(*, question_id: str, question: str, banks: Banks, lines: Sequence[str] = (), story_ids: Sequence[str] = (), answer_id: str | None = None) -> dict[str, object]:
    from . import stories, story_bank

    found = [story.story_id for story in banks.stories if story.story_id in story_ids or stories.answers_question(story, question_id=question_id, question=question)]  # type: ignore[attr-defined]
    exact = answer_id or next((entry.question_id for entry in banks.answers if entry.question_id == story_bank.normalize_question_id(question_id)), None)  # type: ignore[attr-defined]
    near = None if exact else story_bank.near_match(banks.answers, question_id=question_id, question=question)  # type: ignore[arg-type]
    return {
        "stories": list(dict.fromkeys(found)),
        "answer_id": exact,
        "near_answer": None if near is None else {"answer_id": near.bank_question_id, "score": round(near.score, 2)},
        "master_lines": list(lines),
    }


def _uncovered(answered: Mapping[str, object]) -> bool:
    return not (answered["stories"] or answered["answer_id"] or answered["near_answer"] or answered["master_lines"])


def _row(row: PrepRow, banks: Banks) -> dict[str, object]:
    from .assessment_core import row_question_id

    lines = banks.lines or {}
    questions: list[dict[str, object]] = []
    base = {key: None for key in QUESTION_KEYS}
    cited_lines = [source for source in row.sources if source in lines]
    if row.question_id:
        answered = _answered_by(question_id=row.question_id, question=row.question or row.requirement, banks=banks, lines=cited_lines)
        questions.append({**base, "kind": KIND_OPEN, "question_id": row.question_id, "answered_by": answered, "uncovered": _uncovered(answered), "lab": row.lab})
    elif row.status == "met":
        asked = row_question_id(row.id)
        for source in cited_lines:
            answered = _answered_by(question_id=asked, question=row.requirement, banks=banks, lines=[source])
            line = lines[source]
            questions.append({**base, "kind": KIND_WALKTHROUGH, "line_id": source, "line_text": clip(line.text, MAX_LINE_CHARS), "lab": line.lab, "answered_by": answered, "uncovered": False})
        for source in row.sources:
            if source.startswith(_STORY_SOURCE):
                story_id = source[2:]
                known = [story.story_id for story in banks.stories if story.story_id == story_id]  # type: ignore[attr-defined]
                answered = _answered_by(question_id=asked, question=row.requirement, banks=banks, story_ids=known)
                answered["stories"] = known
                questions.append({**base, "kind": KIND_STORY, "story_id": story_id, "answered_by": answered, "uncovered": not known})
            elif source.startswith(_ANSWER_SOURCE) and source not in cited_lines and not source.startswith(_STORY_SOURCE):
                answer_id = source[len(_ANSWER_SOURCE):]
                known = next((entry.question_id for entry in banks.answers if entry.question_id == answer_id), None)  # type: ignore[attr-defined]
                answered = {"stories": [], "answer_id": known, "near_answer": None, "master_lines": []}
                questions.append({**base, "kind": KIND_ANSWER, "question_id": answer_id, "answered_by": answered, "uncovered": known is None})
    return {
        "id": row.id, "class": row.requirement_class, "status": row.status, "lab": row.lab,
        "posting_words": clip(row.question if row.question_id and row.question else row.requirement),
        "questions": questions,
    }


def build(job_identity: str, profile_id: str, rows: Sequence[PrepRow], banks: Banks) -> dict[str, object]:
    """The rehearsal list as one JSON object. Pure: every input is passed in, nothing is read, nothing is written."""

    built = [_row(row, banks) for row in rows]
    asked = [question for row in built for question in row["questions"]]  # type: ignore[union-attr]
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "labels": list(normalized([USER_PRIVATE, PUBLIC_UNTRUSTED])),
        "job_identity": job_identity,
        "profile_id": profile_id,
        "rows": built,
        "summary": {"rows": len(built), "questions": len(asked), "uncovered": sum(1 for question in asked if question["uncovered"])},
    }
    payload[ENVELOPE_KEY] = labels_envelope({"/rows/*/posting_words": PUBLIC_UNTRUSTED, "/rows/*/questions/*/line_text": USER_PRIVATE})
    return payload


def _answers_text(answered: Mapping[str, object]) -> str:
    found = [f"story {story_id}" for story_id in answered["stories"]]  # type: ignore[union-attr]
    if answered["answer_id"]:
        found.append(f"answer {answered['answer_id']}")
    elif answered["near_answer"]:
        found.append(f"near answer {answered['near_answer']['answer_id']}")  # type: ignore[index]
    found += [f"line {line_id}" for line_id in answered["master_lines"]]  # type: ignore[union-attr]
    return ", ".join(found)


def render(payload: Mapping[str, object]) -> str:
    """The list as text. The posting's words are quoted (and clipped); the master line text is the user's own."""

    summary = payload["summary"]
    out = [
        f"Rehearsal list for {payload['job_identity']} (profile {payload['profile_id']}): {summary['rows']} row(s), "  # type: ignore[index]
        f"{summary['questions']} question(s), {summary['uncovered']} uncovered. No model call; nothing was written.",  # type: ignore[index]
    ]
    for row in payload["rows"]:  # type: ignore[union-attr]
        out.append(f"\n{row['id']} [{row['status']}]{' [lab: personal lab only, not production]' if row['lab'] else ''} \"{row['posting_words']}\"")
        if not row["questions"]:
            out.append("  (no question to rehearse)")
        for question in row["questions"]:
            kind = question["kind"]
            if kind == KIND_OPEN:
                head = f"Open question {question['question_id']}"
            elif kind == KIND_WALKTHROUGH:
                head = f"Tell me about: {question['line_text']}{' [lab]' if question['lab'] else ''}"
            elif kind == KIND_STORY:
                head = f"Tell the story {question['story_id']}"
            else:
                head = f"Tell me about your answer {question['question_id']}"
            tail = "uncovered" if question["uncovered"] else _answers_text(question["answered_by"])
            out.append(f"  - {head}\n      answered by: {tail}")
    return "\n".join(out) + "\n"


# --- the loader: what is STORED, read and never changed -------------------------------------------------------------------


def _one_profile(home_root: Path, target: Path, job_url: str, profile_id: str | None) -> None:
    """Refuse (``profile_ambiguous``) when the job is assessed for more than one profile and none was named."""

    if profile_id:
        return
    from .find_jobs.api.agent_routes import job_quick_assessments
    from .find_jobs.contracts import FindJobsContractError
    from .find_jobs.job_state import normalize_job_identity
    from .quick_assess import QuickAssessError

    try:
        identity = normalize_job_identity(job_url)
        profiles = sorted({str(item.resume.profile_id) for item in job_quick_assessments(home_root, target, identity) if item.resume.profile_id})
    except FindJobsContractError as exc:
        raise job_brief.BriefError("invalid_value", "the job is named by its posting URL") from exc
    except QuickAssessError as exc:
        raise job_brief.BriefError(exc.code, str(exc)) from exc
    if len(profiles) > 1:
        raise job_brief.BriefError(
            "profile_ambiguous",
            f"profiles {' and '.join(profiles)} each have an assessment of this job: name one with `--profile ID`",
        )


def _question_rows(assessment: object) -> dict[str, tuple[str, str]]:
    """``row id -> (question id, question words)`` for the assessment's own questions (answered or not)."""

    from .assessment_core import row_question_id
    from .question_ids import normalize_question_id
    from .requirements_list import fold

    matrix = [(job_brief._row_id(row, place), row) for place, row in enumerate(job_brief._matrix(assessment), 1)]  # noqa: SLF001
    out: dict[str, tuple[str, str]] = {}
    for question in tuple(getattr(getattr(assessment, "result", None), "structured_questions", ()) or ()):
        question_id = normalize_question_id(str(question.question_id))
        words = fold(question.requirement) if question.requirement else None
        row_id = next(
            (
                row_id for row_id, item in matrix
                if normalize_question_id(row_question_id(row_id)) == question_id or (words is not None and fold(getattr(item, "requirement", "")) == words)
            ),
            None,
        )
        if row_id is not None and row_id not in out:
            out[row_id] = (question_id, str(question.question))
    return out


def load(home_root: Path, target: Path, job_url: str, profile_id: str | None = None) -> dict[str, object]:
    """The rehearsal list of one job, from the stores as they are. Reads only; ``job_brief.BriefError`` otherwise."""

    from .. import workpad
    from . import stories, story_bank
    from .tailor_master import stored_master

    home_root, target = Path(home_root), Path(target)
    profile_id = profile_id or None
    _one_profile(home_root, target, job_url, profile_id)
    job = job_brief.stored_job(home_root, target, job_url, profile_id)
    try:
        resolved = workpad.resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
    except workpad.WorkpadError as exc:
        raise job_brief.BriefError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    stored = stored_master(home_root, target, resolved=resolved)
    master = None if stored is None else stored.master  # type: ignore[attr-defined]
    ids = job_brief._with_lab(job_brief.row_ids(job, frozenset()), master)  # noqa: SLF001
    words = {job_brief._row_id(row, place): str(getattr(row, "requirement", "")) for place, row in enumerate(job_brief._matrix(job.assessment), 1)}  # noqa: SLF001
    asked = _question_rows(job.assessment)
    rows = tuple(
        PrepRow(
            row.id, row.requirement_class, row.status, words.get(row.id, ""), row.sources,
            asked[row.id][0] if row.id in asked else None, asked[row.id][1] if row.id in asked else None, row.lab,
        )
        for row in ids
    )
    lines = {} if master is None else {item.id: Line(item.id, item.text, bool(item.lab or is_lab_text(item.text))) for item in master.items.values()}
    # An answers file that was never written is not read: reading it would migrate the old bank, which is a write.
    answers = story_bank.read_bank(home_root=home_root, target=target, with_jobs=False) if story_bank._read_file(home_root, target) is not None else ()  # noqa: SLF001
    banks = Banks(stories.list_stories(home_root=home_root, target=target), answers, lines)
    return build(job.job_identity, job.profile_id, rows, banks)


__all__ = ["MAX_POSTING_CHARS", "SCHEMA_VERSION", "Banks", "Line", "PrepRow", "build", "clip", "load", "render"]
