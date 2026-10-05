"""0.1.11 C2 + C3 (orchestrator #51): what code guarantees about an assessment's evidence and suggestions. Pure; no model call.

C2, EVIDENCE IS VERBATIM.  For a ``met`` row with sources, the evidence GigAI stores and shows is the cited master
line(s) themselves, taken by id (:func:`verbatim_evidence`), in the order the row cites them, never the model's
paraphrase of them. An answer a row cites is shown as the answer's own text (a story as ``Story bank <id>: <summary>``).
A row that is not ``met``, one without a usable source, and a prompt that showed no ids keep what the model wrote.

C3, THE SUGGESTION CHECK (:func:`check_suggestions`).  A dropped suggestion costs nothing and a stretched one fails the
job, so the check keeps only what its own premises allow:

* ``reword`` / ``keyword`` / ``order`` name a line of the master, and the row they name is one that line is a source of;
* ``reword`` / ``keyword``: the ``posting_phrase`` is made of words the cited line (or an answer the user gave) already
  contains; a phrase that brings a word the line lacks would write a new claim, so the suggestion is dropped;
* ``order`` moves a line inside its own role: one that asks for a line of a later role to lead the whole Experience
  section, or a line that is in no role (the summary, the skills), is dropped;
* ``master_line`` is for a fact an answer or a story holds: the row it names cites one. Without that it is a ``gap``
  (an invitation to answer) on a row that is not met, and dropped on a met row (nothing is missing there);
* a ``gap`` that carries a ``posting_phrase`` names a requirement the phrase belongs to: a phrase of other words than the
  row's own is dropped.

An empty list is a good answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import re

from .find_jobs.assess_contracts import AssessmentSuggestion

_ID_COMMENT = re.compile(r"<!--\s*id:\s*(\S+?)\s*-->")
_ANY_COMMENT = re.compile(r"\s*<!--.*?-->")
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*")
_ROLE_ID = re.compile(r"\Ar-")

#: The longest evidence string the proposals validator takes (``proposals._MAX_TEXT``).
_MAX_EVIDENCE = 1_200

_STOP = frozenset(
    "a an the and or of in on to for with across at by as from is are be being been that this these those our your you we it its "
    "into over per via using use used their them they than then so such".split()
)

#: An order suggestion that asks for a line to lead the whole Experience section / the resume (only the first role can).
_WHOLE_SECTION = re.compile(r"\b(?:experience section|top of (?:the )?(?:resume|experience|page)|whole resume|page one|first page)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MasterLine:
    """One bullet of the master: its id, its text (no id comment) and the role or project header it is under."""

    id: str
    text: str
    role: str | None = None
    first_role: bool = False


def parse_master_lines(resume_text: str) -> dict[str, MasterLine]:
    """The bullets of ``resume_text`` that carry an id comment, by id. Role and project headers are not lines.

    ``role`` is the id of the ``###`` header above the bullet (``None`` for the summary and the skills);
    ``first_role`` is true for the first role header of the Experience section (the current role).
    """

    lines: dict[str, MasterLine] = {}
    role: str | None = None
    seen_role = False
    first: str | None = None
    for raw in resume_text.splitlines():
        stripped = raw.strip()
        found = _ID_COMMENT.search(stripped)
        if stripped.startswith("#"):
            if stripped.startswith("###"):
                role = found.group(1) if found else None
                if role is not None and _ROLE_ID.match(role) and not seen_role:
                    seen_role, first = True, role
            else:
                role = None
            continue
        if not found:
            continue
        text = _ANY_COMMENT.sub("", stripped).strip()
        if text.startswith(("- ", "* ")):
            text = text[2:].strip()
        if text:
            lines[found.group(1)] = MasterLine(found.group(1), text, role, role is not None and role == first)
    return lines


# --- words ----------------------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return [word.strip(".") for word in _WORD.findall(text.lower().replace("-", " ").replace("/", " ")) if word.strip(".") and word.strip(".") not in _STOP]


def _same(left: str, right: str) -> bool:
    """Two words are one word when equal or when they share a stem: a common prefix of at least four letters, all but two of the shorter."""

    if left == right:
        return True
    common = 0
    for a, b in zip(left, right):
        if a != b:
            break
        common += 1
    return common >= max(4, min(len(left), len(right)) - 2)


def phrase_in(phrase: str, *texts: str) -> bool:
    """Whether every content word of ``phrase`` is in one of ``texts`` (stemmed). A phrase with no content word is in any text."""

    pool = [word for text in texts for word in _words(text)]
    return all(any(_same(word, other) for other in pool) for word in _words(phrase))


# --- C2: the evidence is the line -----------------------------------------------------------------------


def verbatim_evidence(
    sources: Sequence[str], lines: Mapping[str, MasterLine], answers: Mapping[str, str], stories: Mapping[str, str],
) -> list[str] | None:
    """The cited master line(s) of a row by id (then the cited answers, then the cited stories), or ``None`` when no source is known.

    ``answers`` / ``stories``: ``question_id`` -> the answer's text / the story's one-line summary.
    """

    out: list[str] = []
    for source in sources:
        if source in lines:
            text = lines[source].text[:_MAX_EVIDENCE]
        elif source.startswith("A "):
            question_id = source[2:].strip()
            if question_id in stories:
                text = f"Story bank {question_id}: {stories[question_id]}"[:_MAX_EVIDENCE]
            elif question_id in answers:
                text = f"Your answer: {answers[question_id]}"[:_MAX_EVIDENCE]
            else:
                continue
        else:
            continue
        if text not in out:
            out.append(text)
    return out or None


def with_verbatim_evidence(
    matrix: Iterable[dict[str, object]], lines: Mapping[str, MasterLine], answers: Mapping[str, str], stories: Mapping[str, str],
) -> None:
    """Each ``met`` row of ``matrix`` that cites sources shows the cited lines as its evidence. Other rows are untouched.

    IN PLACE: the rows are the boundary's own fresh dicts, and the places that already hold them (the question and
    suggestion row lookups) must keep seeing the same objects.
    """

    for row in matrix:
        sources = row.get("sources")
        if row.get("status") == "met" and isinstance(sources, list) and sources:
            taken = verbatim_evidence([str(source) for source in sources], lines, answers, stories)
            if taken is not None:
                row["resume_evidence"] = taken


# --- C3: the suggestion check ---------------------------------------------------------------------------


def _lookup(rows: Iterable[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    return {str(row["id"]): row for row in rows if row.get("id")}


def _row_sources(row: Mapping[str, object]) -> list[str]:
    sources = row.get("sources")
    return [str(source) for source in sources] if isinstance(sources, list) else []


def _row_text(row: Mapping[str, object]) -> str:
    alternatives = row.get("alternatives")
    extra = " ".join(str(item) for item in alternatives) if isinstance(alternatives, list) else ""
    return f"{row.get('requirement') or ''} {row.get('class_basis') or ''} {extra}"


def check_suggestion(
    item: AssessmentSuggestion, rows: Mapping[str, Mapping[str, object]], lines: Mapping[str, MasterLine], answers: Mapping[str, str],
) -> tuple[AssessmentSuggestion | None, str]:
    """``(kept, why)``: the suggestion as it may stay (possibly turned into a ``gap``), or ``None`` and the reason it goes."""

    row = rows.get(item.requirement) if item.requirement else None
    sources = _row_sources(row) if row is not None else []
    line = lines.get(item.line) if item.line else None
    cited_answers = " ".join(answers.values())  # the user's own words: any answer the prompt listed may back a phrase

    if item.kind in ("reword", "keyword", "order"):
        if item.line is None or line is None:
            return None, "no_master_line"
        if row is not None and item.line not in sources:
            return None, "line_not_a_source_of_row"
    if item.kind in ("reword", "keyword") and item.posting_phrase and not phrase_in(item.posting_phrase, line.text, cited_answers):  # type: ignore[union-attr]
        return None, "phrase_not_in_line"
    if item.kind == "order" and line is not None:
        if line.role is None:
            return None, "order_line_in_no_role"
        if not line.first_role and _WHOLE_SECTION.search(item.why):
            return None, "order_across_roles"
    if item.kind == "master_line":
        if any(source.startswith("A ") for source in sources):
            return item, "ok"
        if row is None or row.get("status") == "met" or item.requirement is None:
            return None, "master_line_without_answer"
        return replace(item, kind="gap", line=None), "master_line_to_gap"
    if item.kind == "gap" and item.posting_phrase and row is not None and not phrase_in(item.posting_phrase, _row_text(row)):
        return None, "gap_phrase_not_of_row"
    return item, "ok"


def check_suggestions(
    suggestions: Sequence[AssessmentSuggestion], matrix: Iterable[Mapping[str, object]], lines: Mapping[str, MasterLine],
    answers: Mapping[str, str],
) -> tuple[list[AssessmentSuggestion], list[tuple[AssessmentSuggestion, str]]]:
    """``(kept, dropped)``: the suggestions that pass :func:`check_suggestion`, and each other one with its reason."""

    rows = _lookup(matrix)
    kept: list[AssessmentSuggestion] = []
    dropped: list[tuple[AssessmentSuggestion, str]] = []
    for item in suggestions:
        result, why = check_suggestion(item, rows, lines, answers)
        if result is None:
            dropped.append((item, why))
        else:
            kept.append(result)
    return kept, dropped


__all__ = [
    "MasterLine",
    "check_suggestion",
    "check_suggestions",
    "parse_master_lines",
    "phrase_in",
    "verbatim_evidence",
    "with_verbatim_evidence",
]
