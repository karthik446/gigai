"""0110-10-05 C: what a settled tailoring's Skills section owes the reader.

Two rules, both applied by ``finish_tailoring`` right after
``tailored_resume.apply_no_loss`` (inside ``tailor_once``'s validate step,
so neither spends the retry):

1. NO BULLET INSIDE ANOTHER (``collapse_duplicate_skills``).  A Skills line
   whose every skill another shown Skills line already lists is dropped
   (equal lines: the first stays).  It happens when a resume's skills lines
   wrap into one another (a copy is stored with the lines it runs on into)
   and the model then lists one of those lines again, copied or reordered.
   Nothing is lost: the line that stays shows every skill of the one that
   goes.  A dropped REWRITE (shown, or the rejected one a dropped fallback
   copy stood in for) is recorded in the section's ``dropped`` list
   (``dropped_duplicate``); a line the user typed is never dropped.

2. AN ANSWER THAT SATISFIES A POSTING SKILL IS SHOWN (``add_answer_skills``).
   A posting keyword (``posting_keywords.extract_keywords``: the list the
   Scout ATS score checks and reports as "missing") that no resume line
   names, that one of the candidate's answers states, and that no line of
   the tailoring shows yet, is added to Skills as its own line.  Its ONLY
   source is the answer: ``kind: rewritten``, one answer ref, ``reason``
   ``answer``, ``origin: "answer"`` (GigAI added it, not the model).  The
   text is the skill's name alone, as the keyword table spells it when the
   answer or its question id says that word, else as the answer spells it;
   never a level, a number or a detail.  The line passes the same numeric and
   posting-term guards a model's line does, or it is not added.

   An answer does NOT satisfy a skill when it says the candidate lacks it
   (``_denies``: a "no" answer, or a negation in the sentence that names the
   skill), or when the stored assessment marks every requirement naming the
   skill ``unmet``.  On any doubt nothing is added: the prompt asks the model
   for the same thing with judgment, this rule only makes the plain case
   certain.

Pure code: no I/O beyond the packaged keyword table, no model call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import re

from .posting_keywords import aliases_for, extract_keywords, mentions, term_regex
from .tailor_no_loss import _FUNCTION_WORDS, _skill_items
from .tailored_resume import (
    LineAlternative,
    LineReason,
    MAX_POSTING_PHRASE_CHARS,
    SourceRef,
    TailorContext,
    TailorJob,
    TailorValidationError,
    TailoredLine,
    TailoredResume,
    TailoredSection,
    _flat,
    _term_variants,
    check_rewritten_line,
    guard_terms,
    shown_text,
    source_terms,
    text_terms,
)

SKILLS_SECTION = "skills"
#: ``TailoredLine.origin`` of a line GigAI added from an answer.
ORIGIN_ANSWER = "answer"

_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_NEGATION = re.compile(
    r"\b(?:no|not|never|none|nope|without|lack|lacks|lacking|limited|unfamiliar|cannot)\b|n['’]t\b",
    re.IGNORECASE,
)
_OPENS_NEGATIVE = re.compile(r"\A\W*(?:no|nope|none|never|not|n/?a)\b", re.IGNORECASE)


# --- 1. no Skills bullet inside another ------------------------------------------------------


def _item_terms(text: str) -> list[set[str]]:
    """Each skill item of one Skills line as its set of words (function words dropped; empty items skipped)."""

    out: list[set[str]] = []
    for item in _skill_items(text):
        wanted = {term for term in text_terms(item) if term not in _FUNCTION_WORDS}
        if wanted:
            out.append(wanted)
    return out


def _holds(holder: str, items: Sequence[set[str]]) -> bool:
    """True when ``holder`` lists every one of ``items`` (each word of each item, aliases and plurals included)."""

    have = source_terms(holder)
    return all(all(_term_variants(term) & have for term in item) for item in items)


def collapse_duplicate_skills(lines: Sequence[TailoredLine]) -> tuple[tuple[TailoredLine, ...], list[LineAlternative]]:
    """The Skills lines without the ones another line already holds, and the dropped rewrites to record.

    A line is dropped when another line lists all of its skills and it does
    not list all of that line's (it is the smaller one), or when the two are
    equal and it comes later.  ``kind: custom`` lines (the user's own text)
    always stay and never cause a drop of the line they repeat.
    """

    shown = [shown_text(line) for line in lines]
    items = [_item_terms(text) for text in shown]
    kept: list[TailoredLine] = []
    dropped: list[LineAlternative] = []
    for index, line in enumerate(lines):
        duplicate = False
        if line.kind != "custom" and items[index]:
            for other, holder in enumerate(lines):
                if other == index or holder.kind == "custom" or not items[other]:
                    continue
                if not _holds(shown[other], items[index]) and _flat(shown[index]) not in _flat(shown[other]):
                    continue
                equal = _holds(shown[index], items[other])
                if not equal or other < index:
                    duplicate = True
                    break
        if not duplicate:
            kept.append(line)
        elif line.kind == "rewritten":
            dropped.append(LineAlternative("rewritten", line.text, line.refs, line.reason, dropped_duplicate=True))
        elif line.alternative is not None and line.alternative.kind == "rewritten":
            # A fallback copy goes, the rewrite it stood in for stays on record.
            dropped.append(replace(line.alternative, dropped_duplicate=True))
    return tuple(kept), dropped


# --- 2. an answer that satisfies a posting skill ---------------------------------------------


@dataclass(frozen=True)
class AnswerSkill:
    """One posting keyword an answer satisfies and the resume lacks."""

    keyword: str  # the keyword table's name ("Helm")
    text: str  # what the added line shows
    question_id: str
    requirement: str | None  # "M<n>" of the matrix row that names it, when one does
    posting_phrase: str | None  # the keyword as the posting spells it


def _denies(answer: str, keyword: str) -> bool:
    """True when the answer reads as "I do not have ``keyword``" (see the module text); doubt counts as a denial."""

    if _OPENS_NEGATIVE.search(answer):
        return True
    sentences = [part for part in _SENTENCE.split(answer) if part.strip()]
    naming = [part for part in sentences if mentions(part, keyword)]
    return any(_NEGATION.search(part) for part in (naming or sentences))


def _spelled(text: str, keyword: str) -> str | None:
    """``keyword`` as ``text`` spells it: the table's name when ``text`` says that word, else the alias as written."""

    if term_regex(keyword).search(text):
        return keyword
    for spelling in aliases_for(keyword)[1:]:
        found = term_regex(spelling).search(text)
        if found:
            return found.group(0)
    return None


def answer_skills(job: TailorJob, ctx: TailorContext) -> tuple[AnswerSkill, ...]:
    """The posting keywords an answer satisfies and no resume line names, in the posting's order."""

    if not ctx.answers:
        return ()
    assert ctx.model is not None
    resume = "\n".join(line for _number, line in ctx.model.lines)
    keywords = extract_keywords(job.posting_text, title=job.title)
    found: list[AnswerSkill] = []
    for keyword in (*keywords.must, *keywords.nice):
        if mentions(resume, keyword):
            continue
        rows = [(number, row) for number, row in enumerate(ctx.matrix, 1) if mentions(row.requirement, keyword)]
        if rows and all(row.status == "unmet" for _number, row in rows):
            continue  # the assessment says the candidate does not have it
        for source in ctx.answers.values():
            if not mentions(source.guard_text, keyword) or _denies(source.answer, keyword):
                continue
            text = _spelled(source.answer, keyword) or keyword
            phrase = _spelled(job.posting_text, keyword)
            found.append(
                AnswerSkill(
                    keyword, text, source.question_id,
                    next((f"M{number}" for number, row in rows if row.status != "unmet"), None),
                    phrase if phrase is not None and len(phrase) <= MAX_POSTING_PHRASE_CHARS else None,
                )
            )
            break
    return tuple(found)


def _shows(result: TailoredResume, keyword: str) -> bool:
    return any(mentions(shown_text(line), keyword) for section in result.sections for line in section.all_lines())


def _next_ids(result: TailoredResume):
    numbers = [
        int(line.id[1:])
        for section in result.sections
        for line in section.all_lines()
        if line.id is not None and line.id[:1] == "L" and line.id[1:].isdigit()
    ]
    counter = max(numbers, default=0)
    while True:
        counter += 1
        yield f"L{counter}"


def add_answer_skills(result: TailoredResume, job: TailorJob, ctx: TailorContext) -> TailoredResume:
    """``result`` with one Skills line per ``answer_skills`` keyword no shown line names yet (see the module text)."""

    wanted = [skill for skill in answer_skills(job, ctx) if not _shows(result, skill.keyword)]
    if not wanted:
        return result
    terms = guard_terms(job, ctx)
    ids = _next_ids(result)
    added: list[TailoredLine] = []
    for skill in wanted:
        source = ctx.answers[skill.question_id]
        ref = SourceRef("answer", None, skill.question_id, source.guard_text)
        try:
            check_rewritten_line("skills line", skill.text, (ref,), terms)
        except TailorValidationError:
            continue  # the answer does not carry the word the line would show: nothing is added
        reason = LineReason("answer", skill.requirement, skill.posting_phrase)
        added.append(TailoredLine("rewritten", skill.text, (ref,), next(ids), reason, ORIGIN_ANSWER))
    if not added:
        return result
    sections = list(result.sections)
    for index, section in enumerate(sections):
        if section.heading == SKILLS_SECTION:
            sections[index] = replace(section, lines=(*section.lines, *added))
            break
    else:
        sections.append(TailoredSection(SKILLS_SECTION, tuple(added)))
    return replace(result, sections=tuple(sections))


def finish_tailoring(result: TailoredResume, job: TailorJob, ctx: TailorContext) -> TailoredResume:
    """A settled result (``apply_no_loss``) with its Skills collapsed, then the answers' skills added."""

    sections = []
    for section in result.sections:
        if section.heading == SKILLS_SECTION:
            lines, dropped = collapse_duplicate_skills(section.lines)
            section = replace(section, lines=lines, dropped=(*section.dropped, *dropped))
        sections.append(section)
    return add_answer_skills(replace(result, sections=tuple(sections)), job, ctx)


__all__ = [
    "ORIGIN_ANSWER",
    "SKILLS_SECTION",
    "AnswerSkill",
    "add_answer_skills",
    "answer_skills",
    "collapse_duplicate_skills",
    "finish_tailoring",
]
