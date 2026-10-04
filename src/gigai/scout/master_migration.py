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
refused by line number and rule, never by text. Three rules of it:

* ABOVE THE FIRST SECTION a paragraph is the summary: a run of lines with no
  blank line in it that reads as a sentence (8 words or more, no ``|``
  between fields). A ``#`` title, a name, a headline, and any line that looks
  like contact data are never kept (``_above_first_section``);
* A TITLE LINE UNDER AN EMPLOYER is the employer's role line, not an entry of
  its own: a bold line (``**Senior Software Engineer** | 2021 - present``) or
  a deeper heading under a ``###`` employer, so the entry has its dates. A
  second dated title line after the employer's bullets is the next role at
  that employer. Dates an entry heading ends with move to a role line when
  no role line names a year (``_dates_to_role_line``);
* NOTHING IS LEFT OUT SILENTLY: every content line of a resume is in the
  master, folded into a line the master holds, or named as left out by line
  number and reason (``ResumeReading``, ``SourceLines``,
  ``LEFT_OUT_REASONS``), never by its text.

This module is pure: no file, no journal, no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import re

from ..canonical import digest_imported_bytes
from .master_resume import (
    MASTER_MAX_ITEM_CHARS,
    DraftEntry,
    DraftItem,
    DraftSection,
    Master,
    MasterDraft,
    MasterEntry,
    MasterResumeError,
    _CONTROL,
    _YEARS,
    assign_ids,
    build_master,
    draft_master,
    skill_names,
)
from .master_selection import _words
from .resume_pii import contact_findings
from .tailor_no_loss import normalize
from .tailored_resume import ENTRY_SECTIONS, MAX_HEADING_LINES, numeric_values

#: Two lines at or above this similarity are one line worded twice (the spike's value, set by hand on one pair of resumes).
NEAR_DUPLICATE = 0.62
CHOICES: tuple[str, ...] = ("a", "b", "both")

_SECTION_NAMES = {
    "summary": "summary", "professional summary": "summary", "profile": "summary", "about": "summary", "about me": "summary",
    "objective": "summary", "overview": "summary", "career summary": "summary", "executive summary": "summary",
    "professional profile": "summary", "summary of qualifications": "summary",
    "experience": "experience", "work experience": "experience", "professional experience": "experience", "employment": "experience",
    "employment history": "experience", "work history": "experience", "career": "experience", "career history": "experience",
    "relevant experience": "experience",
    "skills": "skills", "technical skills": "skills", "core skills": "skills", "key skills": "skills", "technologies": "skills",
    "skills and technologies": "skills", "skills & technologies": "skills", "skills and tools": "skills", "skills & tools": "skills",
    "tools": "skills", "tech stack": "skills", "core competencies": "skills", "competencies": "skills", "areas of expertise": "skills",
    "education": "education", "education and training": "education", "academic background": "education",
    "projects": "projects", "personal projects": "projects", "side projects": "projects", "open source": "projects",
    "selected projects": "projects", "key projects": "projects", "notable projects": "projects",
    "other": "other", "certifications": "other", "certificates": "other", "awards": "other", "publications": "other", "talks": "other",
    "languages": "other", "volunteering": "other", "interests": "other", "additional": "other", "additional information": "other",
    "honors": "other", "patents": "other", "licenses and certifications": "other", "licenses & certifications": "other",
    "certifications and awards": "other", "awards and honors": "other", "honors and awards": "other", "honors & awards": "other",
    "achievements": "other", "accomplishments": "other", "training": "other", "courses": "other", "activities": "other",
    "memberships": "other", "affiliations": "other", "volunteer experience": "other", "volunteer work": "other", "community": "other",
    "references": "other",
}
_HEADING = re.compile(r"\A(#{1,6})\s+(.*?)\s*#*\s*\Z")
_BOLD_LEAD = re.compile(r"\A(\*\*|__)(.+?)\1\s*(.*)\Z")
_OTHER_BULLET = re.compile(r"\A(?:[–—·>]|\d{1,2}[.)])\s+(.*)\Z")
_BULLET = re.compile(r"\A[-*•]\s+")
_COMMENT = re.compile(r"\s*<!--.*?-->\s*\Z")
_EMPHASIS = re.compile(r"(?<![\w*])(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1(?![\w*])")
#: A paragraph above the first section is the summary from this many words on; a name, a title or a headline is shorter.
_SUMMARY_MIN_WORDS = 8
#: Fields side by side (``Austin | Remote``): a header line, never a sentence.
_FIELDS = re.compile(r"\s[|·•]\s|\t")
_SKILLS_LINE = re.compile(r"\A[^:,;|]{1,40}:\s*\S")
#: A plain line this long that has a plain line right under it is taken as wrapped.
_WRAP_WIDTH = 60
_POINT = r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+|\d{1,2}/)?(?:19[5-9]\d|20\d\d)"
#: The dates an entry heading ends with: ``(2021 - Present)``, ``, Jun 2019 - Jan 2023``, ``| 2016``.
_HEADING_DATES = re.compile(
    rf"(?:\s|[,;:|(–—-])+\(?(?P<dates>{_POINT}(?:\s*(?:-|–|—|to|until)\s*(?:{_POINT}|present|current|now))?)\)?\s*\Z", re.IGNORECASE,
)

#: Why a content line of a resume is not in the master: the reason -> what it says, in the order a report lists them.
LEFT_OUT_REASONS: dict[str, str] = {
    "contact": "looks like contact data, which GigAI never stores",
    "above_first_section": "above the first section and not a summary paragraph (a name, a title or a headline)",
    "title_heading": "a title heading above the section headings",
    "unknown_section": "a section heading this reader does not know: its lines are in Other, its own words are not kept",
    "empty_section": "a section heading with nothing under it",
    "unread": "not read",
}
#: Why a content line that was read is not a line of its own in the master: the master holds it already.
FOLDED_REASONS: tuple[str, ...] = ("exact_duplicate", "near_duplicate", "conflict", "same_entry", "role_line", "skills_joined")


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


@dataclass(frozen=True)
class ResumeReading:
    """One resume read as a master draft, and what became of every content line of the file.

    A content line is a line that is not blank and not only a comment. Each
    one is a section heading of the draft (``section_lines``), part of an
    entry or a line of the draft (``spans``: the element -> its file lines),
    or left out and named in ``left_out`` with the reason. Nothing else:
    ``lines == section_lines + sum(spans.values()) + len(left_out)``.
    """

    draft: MasterDraft
    lines: int
    section_lines: int
    #: ``id()`` of a ``DraftEntry`` (its heading and role lines) or a ``DraftItem`` (its line and its wraps) -> file lines.
    spans: dict[int, int]
    #: ``(reason, 1-based file line)`` in the file's order; the reasons are ``LEFT_OUT_REASONS``.
    left_out: tuple[tuple[str, int], ...]


@dataclass
class _Normalized:
    #: One line per line of the file (the same index), in GigAI's resume format; "" where the file's line is not passed on.
    lines: list[str]
    #: Index -> the content line there (comment and whitespace stripped); only content lines.
    content: dict[int, str]
    #: Index -> why the content line there is left out.
    left: dict[int, str] = field(default_factory=dict)
    #: The paragraphs above the first section that are the summary: ``(the indexes of its lines, its text)``.
    summary: list[tuple[list[int], str]] = field(default_factory=list)
    #: Index of a '### ' line the reader made from a bold title under an employer that already has bullets -> that employer.
    employers: dict[int, str] = field(default_factory=dict)


def _dated(text: str) -> bool:
    return bool(_YEARS.search(text))


def _plain(text: str) -> str:
    """``text`` without emphasis marks around its words (``**Acme**``, ``*Staff Engineer*``)."""

    return _flat(_EMPHASIS.sub(r"\2", text))


def _wraps(above: str, line: str) -> bool:
    """A plain line of Skills or Other right under a plain line: the same line, wrapped, rather than the next one of a list."""

    return above.endswith(",") or len(above) >= _WRAP_WIDTH or line[:1].islower()


def _above_first_section(result: _Normalized, kinds: list[tuple[str, str]], first: int, contact: set[int]) -> None:
    """What stands above the first section: a paragraph is the summary; a name, a title, a headline and contact data never are.

    A paragraph is a run of lines with no blank line, no ``#`` heading and no
    line that looks like contact data in it (those are left out and end it).
    It is the summary when it reads as a sentence: ``_SUMMARY_MIN_WORDS``
    words or more and no ``|`` or dot between fields (a header line).
    Everything else above the first section is left out, as it always was.
    """

    block: list[int] = []
    for index in range(first + 1):
        kind = kinds[index][0] if index < first else "blank"
        if kind != "blank" and index + 1 in contact:
            result.left[index] = "contact"
        elif kind in ("drop", "section", "entry"):
            result.left[index] = "above_first_section"
        elif kind != "blank":
            block.append(index)
            continue
        words = _flat(" ".join(_plain(kinds[item][1]) for item in block))
        if block and len(words.split()) >= _SUMMARY_MIN_WORDS and not _FIELDS.search(words):
            result.summary.append((block, words))
        else:
            result.left.update({item: "above_first_section" for item in block})
        block = []


def _normalized_lines(text: str) -> _Normalized:
    """``text`` line for line (numbers kept) in GigAI's resume format where its own shape is plain; see the module docstring."""

    raw = text.splitlines()
    stripped = [_COMMENT.sub("", line).strip() for line in raw]
    levels = [len(found.group(1)) for line in stripped if (found := _HEADING.match(line)) and _section_of(found.group(2)) is not None]
    section_level = min(levels) if levels else 0  # 0: no markdown heading names a section
    result = _Normalized([], {index: line for index, line in enumerate(stripped) if line})
    out = result.lines
    section: str | None = None
    explicit_entries: dict[int, bool] = {}  # section start index -> it has '###' / bold entry headings
    depth: dict[int, int] = {}  # the index of a heading below the section headings -> its level

    def classify(index: int) -> tuple[str, str]:
        """``(kind, text)``: section | entry | bold | bullet | text | blank | drop."""

        line = stripped[index]
        if not line:
            return "blank", ""
        heading = _HEADING.match(line)
        if heading:
            level, words = len(heading.group(1)), heading.group(2)
            if section_level and level < section_level:
                return "drop", ""  # a title above the sections
            if (section_level and level == section_level) or (not section_level and _section_of(words) is not None):
                return "section", _section_of(words) or ""
            depth[index] = level
            return "entry", _plain(words)
        bold = _BOLD_LEAD.match(line)
        if bold and not bold.group(3) and _section_of(bold.group(2)) is not None and not section_level:
            return "section", _section_of(bold.group(2)) or ""
        if bold:
            rest = bold.group(3)
            return "bold", _flat(f"{bold.group(2)}{'' if rest[:1] in ':,;.' else ' '}{rest}")
        blank_before = index == 0 or not stripped[index - 1]
        if not section_level and blank_before and _section_of(line) is not None and (line.isupper() or line.endswith(":") or _flat(line).istitle()):
            return "section", _section_of(line) or ""
        other = _OTHER_BULLET.match(line)
        if other:
            return "bullet", other.group(1)
        if _BULLET.match(line):
            return "bullet", _BULLET.sub("", line, count=1)
        return "text", line

    kinds = [classify(index) for index in range(len(stripped))]
    first = next((index for index, (kind, _text) in enumerate(kinds) if kind == "section"), len(kinds))
    if first < len(kinds):
        # Only the lines above the first section are looked at: the caller's strip took the contact data out of the rest.
        contact = {finding.line for finding in contact_findings(text)}
        _above_first_section(result, kinds, first, contact)
    start = -1
    for index, (kind, _text) in enumerate(kinds):
        if kind == "section":
            start = index
            explicit_entries[start] = False
        elif kind in ("entry", "bold") and start >= 0:
            explicit_entries[start] = True
    # The entry that is open: how it was opened (heading | bold | plain), its heading, its role lines, whether it names a year.
    start, opened, employer, level, role_lines, dated, bullets_in_entry = -1, "", "", 0, 0, False, False
    above = ""  # in Summary, Skills and Other: what the line right above is part of (marked: a bullet; plain: a plain line)

    def open_entry(how: str, words: str, tail: str, *, of_employer: str = "") -> None:
        nonlocal opened, employer, level, role_lines, dated, bullets_in_entry
        if of_employer:
            result.employers[len(out)] = of_employer
            opened, role_lines, dated, bullets_in_entry = "heading", 1, True, False
        else:
            opened, employer, level, role_lines, dated, bullets_in_entry = how, words, depth.get(len(out), 0), 0, _dated(words), False
        out.append(f"### {words}{tail}")

    for index, (kind, words) in enumerate(kinds):
        comment = raw[index][len(_COMMENT.sub("", raw[index])):].strip()
        tail = f" {comment}" if comment else ""
        blank_before = index == 0 or not stripped[index - 1]
        if index < first or kind in ("blank", "drop"):
            if kind == "drop" and index > first:
                result.left[index] = "title_heading"
            out.append("")
            above = above if kind == "drop" else ""
        elif kind == "section":
            if not words:
                result.left[index] = "unknown_section"
            section, start, opened, above = words or "other", index, "", ""
            out.append(f"## {section.capitalize()}")
        elif section not in ENTRY_SECTIONS:
            listed = section in ("skills", "other") and above == "plain" and not _wraps(stripped[index - 1], words)
            if kind != "text":
                out.append(f"- {words}{tail}")  # a bold line or a deeper heading in Summary, Skills or Other is a line of it
                above = "marked"
            elif listed or (section == "skills" and _SKILLS_LINE.match(words)):
                # 'Languages: Python, Go' and the lines of a plain list (certifications): a line each, not the line above wrapped.
                out.append(f"- {words}{tail}")
                above = "plain"
            else:
                out.append(f"{words}{tail}")
                above = above or "plain"
        elif kind in ("entry", "bold"):
            under = opened == "heading" and (kind == "bold" or depth[index] > level)  # a bold line or a deeper heading under an employer
            pair = kind == "bold" and opened == "bold" and not blank_before and not dated and _dated(words)
            if (under or pair) and not bullets_in_entry and role_lines + 1 < MAX_HEADING_LINES:
                # '**Senior Software Engineer** | 2021 - present' under its employer: the entry's role line.
                role_lines, dated = role_lines + 1, dated or _dated(words)
                out.append(f"{words}{tail}")
            elif under and bullets_in_entry and _dated(words):
                open_entry("heading", words, tail, of_employer=employer)  # the next role at the same employer
            else:
                open_entry("heading" if kind == "entry" else "bold", words, tail)
        elif kind == "bullet":
            bullets_in_entry = True
            out.append(f"- {words}{tail}")
        elif not explicit_entries.get(start, False) and (not opened or (blank_before and (bullets_in_entry or dated))):
            # A section whose entries are plain lines: the first line, and a line after a blank line once the
            # entry above has its bullets or its dates, opens an entry.
            open_entry("plain", _plain(words), tail)
        elif opened and not bullets_in_entry:
            role_lines, dated = role_lines + 1, dated or _dated(words)
            out.append(f"{_plain(words)}{tail}")
        else:
            out.append(f"{words}{tail}")
    return result


def _dates_to_role_line(entry: DraftEntry) -> None:
    """An entry whose heading ends with its dates and that has no dated role line: the dates become a role line.

    ``Staff Engineer, Acme Cloud (2021 - Present)`` is the heading ``Staff
    Engineer, Acme Cloud`` and the role line ``2021 - Present``: an entry's
    years are read from its role lines (``MasterEntry.start``). The words
    before the dates stay as they are written and the dates as they are
    written; only what stood between them goes (a comma, a bar, a dash, the
    brackets around the dates). A heading that would be left with an open
    bracket (``Acme (Remote, 2021 - Present)``) is not touched.
    """

    found = _HEADING_DATES.search(entry.heading)
    if found is None or any(_dated(line) for line in entry.sublines) or len(entry.sublines) + 2 > MAX_HEADING_LINES:
        return
    heading = entry.heading[: found.start()].rstrip(" ,;:|(–—-")
    if heading and heading.count("(") == heading.count(")"):
        entry.heading = heading
        entry.sublines.insert(0, found.group("dates"))


def read_resume_lines(text: str) -> ResumeReading:
    """``text`` read as a master draft (ids optional), with every content line accounted for.

    ``MasterResumeError`` names a line of ``text`` and the rule, never the text."""

    normal = _normalized_lines(text)
    lines = normal.lines
    starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    merged: dict[str, DraftSection] = {}
    spans: dict[int, int] = {}
    read: set[int] = set()  # the content lines that are in the draft
    section_lines = 0
    if starts and normal.summary:
        summary = merged.setdefault("summary", DraftSection("summary"))
        for indexes, words in normal.summary:
            for rule, broken in (
                (f"a line has at most {MASTER_MAX_ITEM_CHARS} characters", len(words) > MASTER_MAX_ITEM_CHARS),
                ("control characters are not allowed", bool(_CONTROL.search(words))),
                ("a comment belongs at the end of the line", "<!--" in words or "-->" in words),
            ):
                if broken:
                    raise MasterResumeError("master_markdown_invalid", f"line {indexes[0] + 1}: {rule}")
            item = DraftItem(indexes[0] + 1, "summary", words)
            summary.items.append(item)
            spans[id(item)] = len(indexes)
            read.update(indexes)
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        body = [index for index in range(start + 1, end) if lines[index]]
        if not body:
            normal.left[start] = "empty_section"
            continue
        # Blank lines stand in for everything above, so a refusal names the line of the file.
        chunk = draft_master("\n".join([""] * start + lines[start:end]))
        if start not in normal.left:
            section_lines += 1
            read.add(start)
        for section in chunk.sections:
            kept = merged.setdefault(section.name, DraftSection(section.name))
            # Every line under the heading belongs to the entry or the line that starts at or above it.
            owners = sorted(
                [*section.entries, *(bullet for entry in section.entries for bullet in entry.bullets), *section.items], key=lambda item: item.line,
            )
            for index in body:
                owner = next((item for item in reversed(owners) if item.line <= index + 1), None)
                if owner is not None:
                    spans[id(owner)] = spans.get(id(owner), 0) + 1
                    read.add(index)
            for entry in section.entries:
                if entry.line - 1 in normal.employers:
                    entry.heading, entry.sublines = normal.employers[entry.line - 1], [entry.heading, *entry.sublines]
                _dates_to_role_line(entry)
            kept.entries += section.entries
            kept.items += section.items
    if not merged:
        raise MasterResumeError(
            "master_markdown_invalid",
            "no resume sections found: the resume needs section headings (Summary, Experience, Skills, Education, Projects, Other)",
        )
    draft = MasterDraft(list(merged.values()))
    # Never silent: a content line that is in nothing above is named too.
    left = {index: normal.left.get(index, "unread") for index in normal.content if index not in read}
    return ResumeReading(draft, len(normal.content), section_lines, spans, tuple((left[index], index + 1) for index in sorted(left)))


def read_resume(text: str) -> MasterDraft:
    """``text`` read as a master draft (ids optional); ``MasterResumeError`` names a line of ``text`` and the rule."""

    return read_resume_lines(text).draft


# --- the merge -------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceResume:
    """One resume to merge: ``key`` names it (its content digest), ``profiles`` the labels of the profiles that hold it."""

    key: str
    text: str
    profiles: tuple[str, ...] = ()
    #: The file lines the caller's privacy strip blanked in ``text`` before the merge saw it: counted as left out (contact).
    contact_lines: tuple[int, ...] = ()


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
class SourceLines:
    """What became of every content line of the resumes (a line that is not blank and not only a comment).

    ``lines == kept + sum(folded) + the lines of left_out``: a line is in
    the master (``kept``: as a line, a part of a wrapped line, a heading or
    a role line), or the master holds it already (``folded``, by
    ``FOLDED_REASONS``), or it is left out and named by resume, line number
    and reason (``LEFT_OUT_REASONS``), never by its text.
    """

    lines: int = 0
    kept: int = 0
    folded: tuple[tuple[str, int], ...] = ()
    #: ``(the source's key, its profiles, its content lines, ((reason, file line), ...))`` per resume, in the sources' order.
    resumes: tuple[tuple[str, tuple[str, ...], int, tuple[tuple[str, int], ...]], ...] = ()

    @property
    def left_out(self) -> dict[str, int]:
        found = [reason for _key, _profiles, _lines, left in self.resumes for reason, _line in left]
        return {reason: found.count(reason) for reason in LEFT_OUT_REASONS}

    def to_json(self) -> dict[str, object]:
        folded = dict(self.folded)
        return {
            "in": self.lines, "kept": self.kept, "folded": sum(folded.values()), "left_out": sum(self.left_out.values()),
            "folded_by_reason": {reason: folded.get(reason, 0) for reason in FOLDED_REASONS},
            "left_out_by_reason": self.left_out,
            "resumes": [
                {
                    "profiles": list(profiles), "in": lines,
                    "left_out": [
                        {"reason": reason, "why": LEFT_OUT_REASONS[reason], "lines": [line for found, line in left if found == reason]}
                        for reason in LEFT_OUT_REASONS if any(found == reason for found, _line in left)
                    ],
                }
                for _key, profiles, lines, left in self.resumes
            ],
        }


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
    source_lines: SourceLines = SourceLines()

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
            # Every content line of the resumes: kept, folded or left out (by resume, line number and reason).
            "source_lines": self.source_lines.to_json(),
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
    #: The file lines of the resume being merged, by ``id()`` of what was read from them (``ResumeReading.spans``).
    spans: dict[int, int] = field(default_factory=dict)
    #: The file lines that are in the master, in all and by ``id()`` of the merged line they are.
    kept: int = 0
    kept_as: dict[int, int] = field(default_factory=dict)
    folded: dict[str, int] = field(default_factory=dict)

    def _lines(self, read: object) -> int:
        return self.spans.get(id(read), 0)

    def _keep(self, merged: object, lines: int) -> None:
        self.kept += lines
        self.kept_as[id(merged)] = self.kept_as.get(id(merged), 0) + lines

    def _fold(self, reason: str, lines: int) -> None:
        if lines:
            self.folded[reason] = self.folded.get(reason, 0) + lines

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
            self._fold("exact_duplicate", self._lines(line))
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
                # The other wording goes: its lines are the ones the conflict folded.
                was = self.kept_as.pop(id(best), 0)
                self.kept -= was
                self._fold("conflict", was)
                best.text = line.text
                self._keep(best, self._lines(line))
            else:
                self._fold("conflict", self._lines(line))  # planned as a until it is answered
        elif best is not None and score >= NEAR_DUPLICATE:
            self.near.append((best, line.text, round(score, 2), source))
            self._fold("near_duplicate", self._lines(line))
        else:
            best = None
        if best is None:
            best = DraftItem(0, line.section, line.text, line.id, line.tags, line.backed)
            existing.append(best)
            self._keep(best, self._lines(line))
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
                # The heading is the one the master has; the role lines are taken, the same, or the newer resume's stay.
                lines = self._lines(entry)
                if not candidate.sublines and entry.sublines:
                    candidate.sublines = list(entry.sublines)
                    self._keep(candidate, max(0, lines - 1))
                elif not same_lines and entry.sublines:
                    self._fold("role_line", max(0, lines - 1))
                else:
                    self._fold("same_entry", max(0, lines - 1))
                self._fold("same_entry", min(1, lines))
                self.held_by.setdefault(id(candidate), []).append(source)
                return candidate
        made = DraftEntry(0, section.name, entry.heading, entry.id, list(entry.sublines))
        section.entries.append(made)
        self.held_by[id(made)] = [source]
        self._keep(made, self._lines(entry))
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
                self._fold("skills_joined", self._lines(line))
                return names
        made = DraftItem(0, line.section, line.text, line.id, line.tags, line.backed)
        section.items.append(made)
        self._keep(made, self._lines(line))
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
    read: list[tuple[str, tuple[str, ...], int, tuple[tuple[str, int], ...]]] = []
    for source in sources:
        try:
            reading = read_resume_lines(source.text)
        except MasterResumeError as exc:
            raise MigrationResumeError(source.key, exc) from exc
        merge.spans = dict(reading.spans)
        merge.kept += reading.section_lines
        take(reading.draft, source.key)
        left = sorted([*reading.left_out, *(("contact", line) for line in source.contact_lines)], key=lambda item: item[1])
        read.append((source.key, source.profiles, reading.lines + len(source.contact_lines), tuple(left)))
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
        source_lines=SourceLines(sum(lines for _key, _profiles, lines, _left in read), merge.kept, tuple(sorted(merge.folded.items())), tuple(read)),
    )


__all__ = [
    "CHOICES",
    "FOLDED_REASONS",
    "LEFT_OUT_REASONS",
    "MigrationPlan",
    "MigrationQuestion",
    "MigrationResumeError",
    "NEAR_DUPLICATE",
    "NearDuplicate",
    "ResumeReading",
    "SourceLines",
    "SourceResume",
    "SourceSelection",
    "near_duplicate",
    "plan_migration",
    "read_resume",
    "read_resume_lines",
]
