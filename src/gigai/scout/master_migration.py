"""The master resume's migration (0.1.10.9 master P3): the resumes the profiles hold, merged into one master.

Before the master, every profile owned a resume. ``plan_migration`` takes the
distinct ones (newest first), reads each as resume markdown, and merges them:

* an entry (a role, a project, a school) is the same entry when its heading
  matches and its role line matches (the same line, or the same years);
* a line seen before is FOLDED when it is the same line, and when it is a
  NEAR-DUPLICATE (``near_duplicate`` at ``NEAR_DUPLICATE`` or more) that
  states the same numbers: the newer wording is kept;
* a near-duplicate that states DIFFERENT NUMBERS is a conflict, and a
  conflict is ASKED, never guessed (``MigrationQuestion``: keep ``a``, keep
  ``b``, or keep ``both`` as two lines). A plan with an unanswered question
  is not written;
* Skills lines with the same label are joined: the union of their skills.

Every source's lines are recorded by the id they got in the master
(``SourceSelection``): a profile's first selection is its own old resume.

``read_resume`` reads a resume that is not in GigAI's own format where the
shape is plain: section names a resume commonly uses (``Work Experience``,
``Technical Skills``, ``Certifications``), sections as ``#`` / ``##``
headings, bold lines or lines in capitals, entries as deeper headings, bold
lines or plain lines, and bullets with other markers. What it cannot read is
refused by line number and rule, never by text.

This module is pure: no file, no journal, no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import re

from ..canonical import digest_imported_bytes
from .master_resume import (
    DraftEntry,
    DraftItem,
    DraftSection,
    Master,
    MasterDraft,
    MasterEntry,
    MasterResumeError,
    assign_ids,
    build_master,
    draft_master,
    skill_names,
)
from .master_selection import _words
from .tailor_no_loss import normalize
from .tailored_resume import ENTRY_SECTIONS, numeric_values

#: Two lines at or above this similarity are one line worded twice (the spike's value, set by hand on one pair of resumes).
NEAR_DUPLICATE = 0.62
CHOICES: tuple[str, ...] = ("a", "b", "both")

_SECTION_NAMES = {
    "summary": "summary", "professional summary": "summary", "profile": "summary", "about": "summary", "about me": "summary",
    "objective": "summary", "overview": "summary",
    "experience": "experience", "work experience": "experience", "professional experience": "experience", "employment": "experience",
    "employment history": "experience", "work history": "experience", "career": "experience",
    "skills": "skills", "technical skills": "skills", "core skills": "skills", "key skills": "skills", "technologies": "skills",
    "skills and technologies": "skills", "skills & technologies": "skills",
    "education": "education", "education and training": "education",
    "projects": "projects", "personal projects": "projects", "side projects": "projects", "open source": "projects",
    "selected projects": "projects",
    "other": "other", "certifications": "other", "certificates": "other", "awards": "other", "publications": "other", "talks": "other",
    "languages": "other", "volunteering": "other", "interests": "other", "additional": "other", "additional information": "other",
    "honors": "other", "patents": "other",
}
_HEADING = re.compile(r"\A(#{1,6})\s+(.*?)\s*#*\s*\Z")
_BOLD_LEAD = re.compile(r"\A(\*\*|__)(.+?)\1\s*(.*)\Z")
_OTHER_BULLET = re.compile(r"\A(?:[–—·>]|\d{1,2}[.)])\s+(.*)\Z")
_BULLET = re.compile(r"\A[-*•]\s+")
_COMMENT = re.compile(r"\s*<!--.*?-->\s*\Z")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _same(a: str, b: str) -> bool:
    return _flat(a).casefold() == _flat(b).casefold()


def _section_of(text: str) -> str | None:
    """The section a heading's words name, or ``None``."""

    return _SECTION_NAMES.get(_flat(text.strip().strip("*_").rstrip(":")).casefold())


def _hex(text: str) -> str:
    return digest_imported_bytes(text.encode("utf-8"))[len("sha256:"):]


def near_duplicate(a: str, b: str) -> float:
    """0..1: how much two lines are one line worded twice (token overlap blended with a character sequence ratio)."""

    words_a, words_b = _words(a), _words(b)
    overlap = len(words_a & words_b) / max(1, len(words_a | words_b))
    return 0.5 * overlap + 0.5 * difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _numbers(text: str) -> frozenset[str]:
    return frozenset(str(found.value) for found in numeric_values(text))


# --- reading one resume ----------------------------------------------------------------------


def _normalized_lines(text: str) -> list[str]:
    """``text`` line for line (numbers kept) in GigAI's resume format where its own shape is plain; see the module docstring."""

    raw = text.splitlines()
    stripped = [_COMMENT.sub("", line).strip() for line in raw]
    levels = [len(found.group(1)) for line in stripped if (found := _HEADING.match(line)) and _section_of(found.group(2)) is not None]
    section_level = min(levels) if levels else 0  # 0: no markdown heading names a section
    out: list[str] = []
    section: str | None = None
    explicit_entries: dict[int, bool] = {}  # section start index -> it has '###' / bold entry headings

    def classify(index: int) -> tuple[str, str]:
        """``(kind, text)``: section | entry | bullet | text | blank | drop."""

        line = stripped[index]
        if not line:
            return "blank", ""
        heading = _HEADING.match(line)
        if heading:
            level, words = len(heading.group(1)), heading.group(2)
            if section_level and level < section_level:
                return "drop", ""  # a title above the sections
            if (section_level and level == section_level) or (not section_level and _section_of(words) is not None):
                return "section", _section_of(words) or "other"
            return "entry", words
        bold = _BOLD_LEAD.match(line)
        if bold and not bold.group(3) and _section_of(bold.group(2)) is not None and not section_level:
            return "section", _section_of(bold.group(2)) or "other"
        if bold:
            return "entry", _flat(f"{bold.group(2)} {bold.group(3)}")
        blank_before = index == 0 or not stripped[index - 1]
        if not section_level and blank_before and _section_of(line) is not None and (line.isupper() or line.endswith(":") or _flat(line).istitle()):
            return "section", _section_of(line) or "other"
        other = _OTHER_BULLET.match(line)
        if other:
            return "bullet", other.group(1)
        if _BULLET.match(line):
            return "bullet", _BULLET.sub("", line, count=1)
        return "text", line

    kinds = [classify(index) for index in range(len(stripped))]
    start = -1
    for index, (kind, _text) in enumerate(kinds):
        if kind == "section":
            start = index
            explicit_entries[start] = False
        elif kind == "entry" and start >= 0:
            explicit_entries[start] = True
    start, bullets_in_entry, open_entry = -1, False, False
    for index, (kind, words) in enumerate(kinds):
        comment = raw[index][len(_COMMENT.sub("", raw[index])):].strip()
        tail = f" {comment}" if comment else ""
        if kind == "section":
            section, start, bullets_in_entry, open_entry = words, index, False, False
            out.append(f"## {words.capitalize()}")
        elif kind in ("blank", "drop") or section is None:
            out.append("" if kind in ("blank", "drop") else raw[index])
        elif kind == "entry":
            if section in ENTRY_SECTIONS:
                bullets_in_entry, open_entry = False, True
                out.append(f"### {words}{tail}")
            else:
                out.append(f"- {words}{tail}")  # a bold line in Summary, Skills or Other is a line of it
        elif kind == "bullet":
            bullets_in_entry = True
            out.append(f"- {words}{tail}")
        elif section in ENTRY_SECTIONS and not explicit_entries.get(start, False) and (
            not open_entry or (bullets_in_entry and (index == 0 or not stripped[index - 1]))
        ):
            # A section whose entries are plain lines: the first line, and a line after a blank line once the
            # entry above has its bullets, opens an entry.
            bullets_in_entry, open_entry = False, True
            out.append(f"### {words}{tail}")
        else:
            out.append(f"{words}{tail}")
    return out


def read_resume(text: str) -> MasterDraft:
    """``text`` read as a master draft (ids optional); ``MasterResumeError`` names a line of ``text`` and the rule."""

    lines = _normalized_lines(text)
    starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    merged: dict[str, DraftSection] = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        # Blank lines stand in for everything above, so a refusal names the line of the file.
        chunk = draft_master("\n".join([""] * start + lines[start:end]))
        for section in chunk.sections:
            kept = merged.setdefault(section.name, DraftSection(section.name))
            kept.entries += section.entries
            kept.items += section.items
    if not merged:
        raise MasterResumeError(
            "master_markdown_invalid",
            "no resume sections found: the resume needs section headings (Summary, Experience, Skills, Education, Projects, Other)",
        )
    return MasterDraft(list(merged.values()))


# --- the merge -------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceResume:
    """One resume to merge: ``key`` names it (its content digest), ``profiles`` the labels of the profiles that hold it."""

    key: str
    text: str
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationQuestion:
    """One conflict to ask about: two versions of a line that state different numbers."""

    question_id: str
    section: str
    entry: str
    #: ``(key, text, the profiles whose resume has it)`` for ``a`` (the newer resume's, or the master's) and ``b``.
    options: tuple[tuple[str, str, tuple[str, ...]], ...]
    answer: str | None = None

    @property
    def question(self) -> str:
        where = f"{self.section.capitalize()}{' / ' + self.entry if self.entry else ''}"
        return f"One line of {where} is worded twice, with different numbers. Which is right: a, b, or both (keep the two lines)?"

    def to_json(self) -> dict[str, object]:
        return {
            "question_id": self.question_id, "kind": "number_conflict", "question": self.question, "section": self.section, "entry": self.entry,
            "options": [{"key": key, "text": text, "profiles": list(profiles)} for key, text, profiles in self.options],
            "choices": list(CHOICES), "answer": self.answer,
        }


@dataclass(frozen=True)
class NearDuplicate:
    """A line folded into a line worded differently: the kept line's id, the two wordings, how close."""

    kept_id: str
    kept: str
    folded: str
    similarity: float
    profiles: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {"kept_id": self.kept_id, "kept": self.kept, "folded": self.folded, "similarity": self.similarity, "profiles": list(self.profiles)}


@dataclass(frozen=True)
class SourceSelection:
    """What one source resume shows, in the master's ids: its entries and lines in its own order, and its skills."""

    key: str
    item_ids: tuple[str, ...]
    skills: tuple[str, ...]


@dataclass(frozen=True)
class MigrationPlan:
    master: Master
    #: The master as it would be stored (ids on every line).
    selections: tuple[SourceSelection, ...]
    questions: tuple[MigrationQuestion, ...]
    near_duplicates: tuple[NearDuplicate, ...]
    lines_in: int
    exact_duplicates: int
    ids_assigned: int

    @property
    def unanswered(self) -> tuple[MigrationQuestion, ...]:
        return tuple(question for question in self.questions if question.answer is None)

    def selection(self, key: str) -> SourceSelection:
        return next(item for item in self.selections if item.key == key)

    def to_json(self) -> dict[str, object]:
        return {
            "resumes": len(self.selections), "lines_in": self.lines_in, "lines_out": len(self.master.items),
            "entries": len(self.master.entries), "exact_duplicates": self.exact_duplicates, "ids_assigned": self.ids_assigned,
            "near_duplicates": [item.to_json() for item in self.near_duplicates],
            "questions": [question.to_json() for question in self.questions],
            "unanswered": [question.question_id for question in self.unanswered],
        }


class MigrationResumeError(MasterResumeError):
    """A source resume that does not read as a resume; ``key`` names the source."""

    def __init__(self, key: str, error: MasterResumeError) -> None:
        super().__init__("migration_resume_unreadable", str(error))
        self.key = key


@dataclass
class _Merge:
    answers: dict[str, str]
    draft: MasterDraft = field(default_factory=MasterDraft)
    #: A merged line or entry -> the sources (by key) whose resume has it; the base master is ``""``.
    held_by: dict[int, list[str]] = field(default_factory=dict)
    labels: dict[str, tuple[str, ...]] = field(default_factory=dict)
    questions: list[MigrationQuestion] = field(default_factory=list)
    near: list[tuple[DraftItem, str, float, str]] = field(default_factory=list)
    lines_in: int = 0
    exact: int = 0

    def section(self, name: str) -> DraftSection:
        found = next((item for item in self.draft.sections if item.name == name), None)
        if found is None:
            found = DraftSection(name)
            self.draft.sections.append(found)
        return found

    def _profiles(self, item: object) -> tuple[str, ...]:
        return tuple(label for key in self.held_by.get(id(item), []) for label in self.labels.get(key, ()))

    def fold(self, existing: list[DraftItem], line: DraftItem, source: str, entry: str) -> DraftItem:
        """The merged line ``line`` becomes: one already there (the same line, or a near-duplicate) or a new one."""

        self.lines_in += 1
        best, score = None, 0.0
        for candidate in existing:
            if self.held_by.get(id(candidate)) == [source]:
                continue  # a resume's own two lines are never one line
            similarity = 1.0 if _same(candidate.text, line.text) else near_duplicate(candidate.text, line.text)
            if similarity > score:
                best, score = candidate, similarity
        if best is not None and score == 1.0:
            self.exact += 1
        elif best is not None and score >= NEAR_DUPLICATE and _numbers(best.text) != _numbers(line.text):
            question_id = "mq-" + _hex(f"{line.section}\n{entry}\n{_flat(best.text)}\n{_flat(line.text)}")[:12]
            answer = self.answers.get(question_id)
            self.questions.append(MigrationQuestion(
                question_id, line.section, entry,
                (("a", best.text, self._profiles(best)), ("b", line.text, self.labels.get(source, ()))),
                answer if answer in CHOICES else None,
            ))
            if answer == "both":
                best = None
            elif answer == "b":
                best.text = line.text
        elif best is not None and score >= NEAR_DUPLICATE:
            self.near.append((best, line.text, round(score, 2), source))
        else:
            best = None
        if best is None:
            best = DraftItem(0, line.section, line.text, line.id, line.tags, line.backed)
            existing.append(best)
        self.held_by.setdefault(id(best), []).append(source)
        return best

    def entry(self, section: DraftSection, entry: DraftEntry, source: str) -> DraftEntry:
        def years(item: DraftEntry) -> tuple[int | None, int | None, bool] | None:
            probe = MasterEntry("probe", item.section, item.heading, tuple(item.sublines))
            return None if probe.start is None else (probe.start, probe.end, probe.ongoing)

        for candidate in section.entries:
            if not _same(candidate.heading, entry.heading):
                continue
            same_lines = len(candidate.sublines) == len(entry.sublines) and all(_same(a, b) for a, b in zip(candidate.sublines, entry.sublines))
            if not candidate.sublines or not entry.sublines or same_lines or (years(candidate) is not None and years(candidate) == years(entry)):
                if not candidate.sublines:
                    candidate.sublines = list(entry.sublines)
                self.held_by.setdefault(id(candidate), []).append(source)
                return candidate
        made = DraftEntry(0, section.name, entry.heading, entry.id, list(entry.sublines))
        section.entries.append(made)
        self.held_by[id(made)] = [source]
        return made

    def skills(self, section: DraftSection, line: DraftItem) -> tuple[str, ...]:
        """Join ``line`` into the Skills line with its label (the union of both, in first-seen order); the skills ``line`` lists."""

        label, names = skill_names(line.text)
        for candidate in section.items:
            kept_label, kept = skill_names(candidate.text)
            if kept_label.casefold() == label.casefold() and (label or _same(candidate.text, line.text) or not kept):
                seen = {name.casefold() for name in kept}
                joined = [*kept, *(name for name in names if name.casefold() not in seen)]
                if len(joined) != len(kept):
                    candidate.text = (f"{kept_label}: " if kept_label else "") + ", ".join(joined)
                return names
        section.items.append(DraftItem(0, line.section, line.text, line.id, line.tags, line.backed))
        return names


def plan_migration(sources: list[SourceResume], *, base: Master | None = None, answers: dict[str, str] | None = None) -> MigrationPlan:
    """The merge of ``sources`` (newest first) on top of ``base`` (a master already stored, or none).

    ``answers`` maps a question's id to ``a``, ``b`` or ``both``; an id that
    is not one of the plan's questions is refused (the resumes changed since
    the questions were read). A question without an answer is planned as
    ``a`` and listed in ``unanswered``: such a plan is a preview only.
    """

    merge = _Merge(dict(answers or {}), labels={source.key: source.profiles for source in sources})
    shown: dict[str, list[object]] = {}
    listed: dict[str, list[str]] = {}

    def take(draft: MasterDraft, source: str) -> None:
        held, names = shown.setdefault(source, []), listed.setdefault(source, [])
        for section in draft.sections:
            target = merge.section(section.name)
            for entry in section.entries:
                kept = merge.entry(target, entry, source)
                held.append(kept)
                held.extend(merge.fold(kept.bullets, bullet, source, kept.heading) for bullet in entry.bullets)
            for item in section.items:
                if section.name == "skills":
                    names.extend(name for name in merge.skills(target, item) if name.casefold() not in {known.casefold() for known in names})
                else:
                    held.append(merge.fold(target.items, item, source, ""))

    if base is not None:
        take(draft_master(base.markdown()), "")
    merge.lines_in = merge.exact = 0  # the base master's own lines are not lines coming in
    for source in sources:
        try:
            draft = read_resume(source.text)
        except MasterResumeError as exc:
            raise MigrationResumeError(source.key, exc) from exc
        take(draft, source.key)
    unknown = sorted(set(merge.answers) - {question.question_id for question in merge.questions})
    if unknown:
        raise MasterResumeError("migration_answer_unknown", "no question has the id " + ", ".join(unknown) + "; run it again without --answer to read the questions")
    wrong = sorted(key for key, value in merge.answers.items() if value not in CHOICES)
    if wrong:
        raise MasterResumeError("migration_answer_invalid", "an answer is a, b or both (" + ", ".join(wrong) + ")")
    assignment = assign_ids(merge.draft, base)
    master = build_master(merge.draft)

    def ids(held: list[object]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(item.id) for item in held))  # type: ignore[attr-defined]

    return MigrationPlan(
        master=master,
        selections=tuple(SourceSelection(source.key, ids(shown.get(source.key, [])), tuple(listed.get(source.key, []))) for source in sources),
        questions=tuple(merge.questions),
        near_duplicates=tuple(
            NearDuplicate(str(kept.id), kept.text, folded, score, merge.labels.get(source, ())) for kept, folded, score, source in merge.near
        ),
        lines_in=merge.lines_in,
        exact_duplicates=merge.exact,
        ids_assigned=assignment.assigned,
    )


__all__ = [
    "CHOICES",
    "MigrationPlan",
    "MigrationQuestion",
    "MigrationResumeError",
    "NEAR_DUPLICATE",
    "NearDuplicate",
    "SourceResume",
    "SourceSelection",
    "near_duplicate",
    "plan_migration",
    "read_resume",
]
