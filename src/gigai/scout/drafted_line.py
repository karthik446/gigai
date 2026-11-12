"""0.3.2 gyl: the master line drafted for a must-have row the user said yes to and no line of the master shows.

A row ``answered`` (``assess_strict`` T6, ``assessment_core.settle_answered_rows``) rests on the user's own answer or
story. The job's suggestions name it with a ``master_line`` suggestion (``suggestions.ANSWERED_WHY``); the line it
proposes is DRAFTED HERE WHEN THE SUGGESTIONS ARE READ and never stored (a suggestion record holds ids only, never an
answer's text). ``job_actions.add_line`` writes the same draft (or the user's own words) through ``master_edit``.

The draft is code, no model: the answer's first sentence in a resume's voice ("Yes, I have used Agile and TDD daily"
-> "Used Agile and TDD daily"), with the names the requirement lists added when the sentence names none of them, so
the strict read (T1) finds the row's words on the new line. It goes as a bullet under the newest role of the master,
backed by the answer (``answer:<question_id>``) or the story (``story:<slug>``) it comes from.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re

from .assess_strict import named_tools, practice_alternatives

#: At most this many characters of the answer's own words: a bullet, not the answer.
MAX_DRAFT_CHARS = 200

_ANSWER_PREFIX = "A "
_STORY_PREFIX = "story:"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_AFFIRMATIVE = re.compile(r"\A\W*(?:yes|yeah|yep|yup|sure|correct|absolutely|definitely|of course)\b[\s,.;:!—–-]*", re.IGNORECASE)
_FIRST_PERSON = re.compile(r"\A(?:i\s+have\s+|i['’]ve\s+|i\s+)", re.IGNORECASE)


@dataclass(frozen=True)
class Draft:
    """A drafted line: its text, the role it goes under (``entry_id``) and the evidence it is backed by.

    ``question_id`` / ``story_id``: what ``master_edit.evidence_for`` reads to back the line (one of them is set).
    """

    text: str
    entry_id: str | None
    backed: str
    question_id: str | None = None
    story_id: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"text": self.text, "entry_id": self.entry_id, "backed": self.backed}


def answer_ref(sources: Sequence[str]) -> tuple[str | None, str | None]:
    """``(question_id, story_id)`` of the first answer or story among a row's ``sources`` (``A <question_id>`` / ``A story:<slug>``: the slug)."""

    for source in sources:
        if not source.startswith(_ANSWER_PREFIX):
            continue
        ref = source[len(_ANSWER_PREFIX):].strip()
        if ref.startswith(_STORY_PREFIX):
            return None, ref[len(_STORY_PREFIX):] or None
        if ref:
            return ref, None
    return None, None


def _alternatives(requirement: str, alternatives: Sequence[str]) -> tuple[str, ...]:
    """The requirement's alternatives that name something (a tool or a practice; an open "or similar" one is dropped)."""

    row = {"requirement": requirement, "alternatives": list(alternatives)}
    return (*named_tools(row), *practice_alternatives(row))


def _listed(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _trimmed(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—–- ")
    return cut or text[:limit]


def drafted_text(requirement: str, alternatives: Sequence[str], said: str) -> str:
    """The line drafted from ``said`` (the answer, or the story's text) for the requirement. Pure."""

    # The first sentence that says something past its "yes": "Yes. I ran EKS for three years." drafts from the second.
    parts = (_FIRST_PERSON.sub("", _AFFIRMATIVE.sub("", part.strip())).strip().rstrip(".!?").strip() for part in _SENTENCE_END.split(said or ""))
    own = next((part for part in parts if part), "")
    own = _trimmed(own, MAX_DRAFT_CHARS)
    names = _alternatives(requirement, alternatives)
    folded = own.casefold()
    if own and (not names or any(name.casefold() in folded for name in names)):
        # Its own words name what the requirement does (or the requirement names nothing to add): the user's line.
        return own[0].upper() + own[1:] + "."
    if names:
        return f"Worked with {_listed(names)}: {own[0].lower() + own[1:]}." if own else f"Worked with {_listed(names)}."
    # A bare "Yes" to a requirement that names nothing: the words the user said yes to.
    words = " ".join(requirement.split()).rstrip(". ")
    return words[:1].upper() + words[1:] + "." if words else ""


def newest_role(master: object) -> str | None:
    """The id of the master's newest role (an ongoing one first, then the latest end year, then the file's order)."""

    roles = list(master.entries_in("experience"))  # type: ignore[attr-defined]
    if not roles:
        return None
    return max(roles, key=lambda entry: (entry.ongoing, entry.end or 0, -entry.order)).id


def draft_for(row: Mapping[str, object] | object, master: object | None, answer_text: str | None, *, question_id: str | None, story_id: str | None) -> Draft | None:
    """The draft for one ``answered`` row, or ``None``: no answer text to draft from, or nothing to say. Pure.

    ``row``: the stored matrix row (``RequirementMatrixRow`` or its JSON). ``master``: the stored master (``Master``).
    """

    def value(name: str) -> object:
        return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)

    if answer_text is None or (question_id is None and story_id is None):
        return None
    alternatives = [str(item) for item in (value("alternatives") or ())]  # type: ignore[union-attr]
    text = drafted_text(str(value("requirement") or ""), alternatives, answer_text)
    if not text:
        return None
    backed = f"answer:{question_id}" if question_id is not None else f"story:{story_id}"
    return Draft(text, newest_role(master) if master is not None else None, backed, question_id, story_id)


__all__ = ["MAX_DRAFT_CHARS", "Draft", "answer_ref", "draft_for", "drafted_text", "newest_role"]
