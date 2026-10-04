"""The master resume's format (0.1.10.9 master P1): parse, give ids, render.

One master resume per user holds every role, bullet, project and skill.
It is resume markdown in GigAI's own format (what the PDF renderer's
markdown parser reads) where every selectable line and
every entry heading carries a stable id in a trailing comment::

    <!-- gigai-master:1 -->

    ## Summary

    - Staff engineer ... <!-- id:sum-ai tags:ai,llm -->

    ## Experience

    ### Hexa Cloud <!-- id:r-hex -->
    Staff Software Engineer | Jun 2019 - Jan 2023
    - Led the migration ... <!-- id:b-hex-03 tags:delivery backed:story:helm-migration -->

    ## Skills

    - Cloud and infrastructure: Kubernetes, Docker, Terraform <!-- id:s-infra -->

The shipped renderer drops a trailing comment, so the stored master is also
a printable resume. Sections are the shipped six (Summary, Experience,
Skills, Education, Projects, Other; certifications are ``Other`` lines).

This module is pure: no file, no journal, no model. ``draft_master`` reads
markdown a person typed (ids optional, hard wraps, plain paragraphs),
``assign_ids`` gives every line without an id one (a line whose id comment
was deleted gets its id back from the previous master when its text still
matches), and ``Master.markdown`` writes the one canonical form that is
stored. ``parse_master`` reads that stored form and refuses a line without
an id. An id never changes when its text is edited.

A ``MasterResumeError`` names a line number and the rule, never the line's
text. The master holds no contact data: the import (``master_store``) runs
the resume import's own strip before anything here sees the text, and
whatever stands above the first ``## `` section is never kept.

Derived, never typed: an item's evidence ``strength`` (``backed`` when a
story or an answer is linked, ``quantified`` when the line states a number
by the shipped ``numeric_values``, else ``stated``) and its ``mark`` (16
hex characters over the id and the text, for targeted staleness).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from ..canonical import digest_imported_bytes
from .tailored_resume import ENTRY_SECTIONS, MAX_HEADING_LINES, SECTION_HEADINGS, numeric_values

#: The format version the first line names (``<!-- gigai-master:1 -->``).
MASTER_FORMAT = 1
MASTER_MARKER = f"<!-- gigai-master:{MASTER_FORMAT} -->"
#: The private reference import's own size limit; the stored master is one reference.
MASTER_MAX_BYTES = 1_048_576
MASTER_MAX_LINES = 20_000
MASTER_MAX_ITEMS = 5_000
MASTER_MAX_ITEM_CHARS = 2_000

KIND_SUMMARY = "summary"
KIND_BULLET = "bullet"
KIND_SKILLS = "skills"
KIND_OTHER = "other"
STRENGTHS: tuple[str, ...] = ("backed", "quantified", "stated")

_COMMENT = re.compile(r"\s*<!--(.*?)-->\s*\Z")
_MARKER = re.compile(r"\Agigai-master:(\d+)\Z")
_HASHES = re.compile(r"\A(#{1,6})\s+(.*)\Z")
_BULLET = re.compile(r"\A[-*•]\s+(.*)\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_TAG = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_+#.-]{0,39}\Z")
_BACKED = re.compile(r"\A(?:story|answer):[A-Za-z0-9][A-Za-z0-9_:.-]{0,119}\Z")
_YEARS = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")
_ONGOING = re.compile(r"\b(?:present|current|now)\b", re.IGNORECASE)
_SKILL_LABEL = re.compile(r"\A([^:,;|]{1,40}):\s*")
_SKILL_SEPARATORS = re.compile(r"\s*[·;]\s*|\s*,\s*(?![^()]*\))")

#: The id prefix a new line gets, by what it is.
_ENTRY_PREFIX = {"experience": "r", "projects": "p", "education": "e"}
_ITEM_PREFIX = {KIND_SUMMARY: "sum", KIND_BULLET: "b", KIND_SKILLS: "s", KIND_OTHER: "o"}
_ID_HEX_CHARS = 6


class MasterResumeError(ValueError):
    """The markdown is not a master resume; ``code`` is the CLI error code.

    Messages name a line number and the rule, never the line's text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _bad(number: int, message: str) -> None:
    raise MasterResumeError("master_markdown_invalid", f"line {number}: {message}")


def _hex(text: str) -> str:
    return digest_imported_bytes(text.encode("utf-8"))[len("sha256:"):]


def _kind(section: str) -> str:
    if section in ENTRY_SECTIONS:
        return KIND_BULLET
    return {"summary": KIND_SUMMARY, "skills": KIND_SKILLS}.get(section, KIND_OTHER)


def skill_names(text: str) -> tuple[str, tuple[str, ...]]:
    """``(label, names)`` of a Skills line: ``Cloud: Kubernetes, Docker`` is ``("Cloud", ("Kubernetes", "Docker"))``.

    Split as the PDF's skill chips are: on commas outside parentheses, semicolons and middle dots."""

    label = _SKILL_LABEL.match(text)
    names = [part.strip().rstrip(".").strip() for part in _SKILL_SEPARATORS.split(_SKILL_LABEL.sub("", text, count=1))]
    return (label.group(1).strip() if label else "", tuple(name for name in names if name))


@dataclass(frozen=True)
class MasterItem:
    """One selectable line: a summary variant, a bullet, a skills line or an Other line."""

    id: str
    section: str
    kind: str
    text: str
    tags: tuple[str, ...] = ()
    #: ``story:<id>`` / ``answer:<question_id>``: the evidence that backs the line.
    backed: tuple[str, ...] = ()
    entry_id: str | None = None
    order: int = 0

    @property
    def strength(self) -> str:
        if self.backed:
            return "backed"
        return "quantified" if numeric_values(self.text) else "stated"

    @property
    def mark(self) -> str:
        return _hex(f"master\n{self.id}\n{self.text}")[:16]

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {
            "id": self.id, "section": self.section, "kind": self.kind, "text": self.text, "tags": list(self.tags),
            "backed": list(self.backed), "entry_id": self.entry_id, "order": self.order, "strength": self.strength, "mark": self.mark,
        }
        if self.kind == KIND_SKILLS:
            label, names = skill_names(self.text)
            out["label"], out["skills"] = label, list(names)
        return out


@dataclass(frozen=True)
class MasterEntry:
    """A role, a project or a school: a heading, its lines (``Title | Jun 2019 - Jan 2023``) and its bullets' ids."""

    id: str
    section: str
    heading: str
    sublines: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    order: int = 0

    @property
    def ongoing(self) -> bool:
        return bool(_ONGOING.search(" ".join(self.sublines)))

    @property
    def start(self) -> int | None:
        years = [int(year) for year in _YEARS.findall(" ".join(self.sublines))]
        return min(years) if years else None

    @property
    def end(self) -> int | None:
        """The last year the entry names; ``None`` when it is ongoing or names no year."""

        years = [int(year) for year in _YEARS.findall(" ".join(self.sublines))]
        return None if self.ongoing or not years else max(years)

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id, "section": self.section, "heading": self.heading, "sublines": list(self.sublines),
            "start": self.start, "end": self.end, "ongoing": self.ongoing, "bullets": list(self.bullets), "order": self.order,
        }


def _comment(item_id: str, tags: tuple[str, ...] = (), backed: tuple[str, ...] = ()) -> str:
    parts = [f"id:{item_id}"]
    if tags:
        parts.append("tags:" + ",".join(tags))
    if backed:
        parts.append("backed:" + ",".join(backed))
    return f"<!-- {' '.join(parts)} -->"


@dataclass(frozen=True)
class Master:
    """A parsed master: sections in the file's order, entries and items by id (both in the file's order)."""

    sections: tuple[str, ...]
    entries: dict[str, MasterEntry]
    items: dict[str, MasterItem]

    def in_section(self, section: str) -> list[MasterItem]:
        return [item for item in self.items.values() if item.section == section]

    def entries_in(self, section: str) -> list[MasterEntry]:
        return [entry for entry in self.entries.values() if entry.section == section]

    def skills(self) -> list[str]:
        """Every skill the master lists, once (case-insensitive), in the file's order."""

        seen: set[str] = set()
        names: list[str] = []
        for item in self.items.values():
            if item.kind != KIND_SKILLS:
                continue
            for name in skill_names(item.text)[1]:
                if name.casefold() not in seen:
                    seen.add(name.casefold())
                    names.append(name)
        return names

    def counts(self) -> dict[str, object]:
        strengths = {name: 0 for name in STRENGTHS}
        for item in self.items.values():
            strengths[item.strength] += 1
        return {
            "ids": len(self.items) + len(self.entries),
            "items": len(self.items),
            "entries": len(self.entries),
            "by_kind": {kind: sum(item.kind == kind for item in self.items.values()) for kind in _ITEM_PREFIX},
            "by_strength": strengths,
            "skills": len(self.skills()),
        }

    def markdown(self, *, ids: bool = True) -> str:
        """The canonical markdown (what is stored); ``ids=False`` leaves the id comments and the marker out."""

        def mark(item_id: str, tags: tuple[str, ...] = (), backed: tuple[str, ...] = ()) -> str:
            return f" {_comment(item_id, tags, backed)}" if ids else ""

        out: list[str] = [MASTER_MARKER, ""] if ids else []
        for section in self.sections:
            out += [f"## {section.capitalize()}", ""]
            if section in ENTRY_SECTIONS:
                for entry in self.entries_in(section):
                    out += [f"### {entry.heading}{mark(entry.id)}", *entry.sublines]
                    out += [f"- {self.items[i].text}{mark(i, self.items[i].tags, self.items[i].backed)}" for i in entry.bullets]
                    out.append("")
            else:
                out += [f"- {item.text}{mark(item.id, item.tags, item.backed)}" for item in self.in_section(section)]
                out.append("")
        return "\n".join(out).rstrip("\n") + "\n"

    def to_json(self) -> dict[str, object]:
        return {
            "format": MASTER_FORMAT,
            "sections": list(self.sections),
            "counts": self.counts(),
            "entries": [entry.to_json() for entry in self.entries.values()],
            "items": [item.to_json() for item in self.items.values()],
        }


# --- reading markdown a person typed ------------------------------------------------------------


@dataclass
class _Fields:
    id: str | None = None
    tags: tuple[str, ...] = ()
    backed: tuple[str, ...] = ()
    marker: int | None = None

    def merge(self, other: "_Fields", number: int) -> None:
        if other.id is not None:
            if self.id is not None and self.id != other.id:
                _bad(number, "one line has two ids")
            self.id = other.id
        self.tags += tuple(tag for tag in other.tags if tag not in self.tags)
        self.backed += tuple(ref for ref in other.backed if ref not in self.backed)


def _split(raw: str, number: int) -> tuple[str, _Fields]:
    """``(the line without its trailing comments, what the comments say)``.

    A comment's ``id:``, ``tags:`` and ``backed:`` words are read; anything else in it is a note and is dropped,
    as the shipped renderer drops a trailing comment."""

    fields = _Fields()
    line = raw
    while True:
        found = _COMMENT.search(line)
        if found is None:
            break
        line = line[: found.start()]
        for word in found.group(1).split():
            key, colon, value = word.partition(":")
            if not colon:
                continue  # a note's word
            if key == "id":
                if not _ID.fullmatch(value):
                    _bad(number, "an id holds letters, digits, '-' and '_' (at most 64) and starts with a letter or digit")
                if fields.id is not None and fields.id != value:
                    _bad(number, "one line has two ids")
                fields.id = value
            elif key == "tags":
                tags = tuple(tag for tag in value.split(",") if tag)
                if not tags or not all(_TAG.fullmatch(tag) for tag in tags):
                    _bad(number, "tags are comma-separated words of letters, digits and + # . - _ (tags:ai,llm)")
                fields.tags += tuple(tag for tag in tags if tag not in fields.tags)
            elif key == "backed":
                refs = tuple(ref for ref in value.split(",") if ref)
                if not refs or not all(_BACKED.fullmatch(ref) for ref in refs):
                    _bad(number, "backed names a story or an answer (backed:story:<id> or backed:answer:<question_id>)")
                fields.backed += tuple(ref for ref in refs if ref not in fields.backed)
            elif _MARKER.fullmatch(word):
                fields.marker = int(_MARKER.fullmatch(word).group(1))  # type: ignore[union-attr]
    return line.strip(), fields


def _flat(text: str) -> str:
    return " ".join(text.split())


@dataclass
class DraftItem:
    line: int
    section: str
    text: str
    id: str | None = None
    tags: tuple[str, ...] = ()
    backed: tuple[str, ...] = ()


@dataclass
class DraftEntry:
    line: int
    section: str
    heading: str
    id: str | None = None
    sublines: list[str] = field(default_factory=list)
    bullets: list[DraftItem] = field(default_factory=list)


@dataclass
class DraftSection:
    name: str
    entries: list[DraftEntry] = field(default_factory=list)
    items: list[DraftItem] = field(default_factory=list)


@dataclass
class MasterDraft:
    """Markdown read as a master, ids still optional."""

    sections: list[DraftSection] = field(default_factory=list)

    def all_entries(self) -> list[DraftEntry]:
        return [entry for section in self.sections for entry in section.entries]

    def all_items(self) -> list[DraftItem]:
        return [item for section in self.sections for item in (*(bullet for entry in section.entries for bullet in entry.bullets), *section.items)]


def draft_master(markdown: str) -> MasterDraft:
    """``markdown`` read as a master in the shipped resume format; ``MasterResumeError`` otherwise.

    * ``## Summary|Experience|Skills|Education|Projects|Other`` opens a section (each at most once);
    * in Experience, Projects and Education ``### <heading>`` opens an entry, plain lines under it
      belong to the heading and ``- `` lines are its bullets;
    * in Summary, Skills and Other every ``- `` line is one item (in Summary: one variant), and a
      plain paragraph is one item too;
    * a plain line right under an item continues it (a hard wrap);
    * whatever stands above the first ``## `` is not part of the master and is dropped.
    """

    if len(markdown.encode("utf-8")) > MASTER_MAX_BYTES:
        raise MasterResumeError("master_too_large", f"the master is larger than {MASTER_MAX_BYTES} bytes")
    raw_lines = markdown.splitlines()
    if len(raw_lines) > MASTER_MAX_LINES:
        raise MasterResumeError("master_too_large", f"the master has more than {MASTER_MAX_LINES} lines")
    draft = MasterDraft()
    section: DraftSection | None = None
    after_blank = True
    last: DraftItem | None = None  # the item a plain line right below would continue
    last_fields: _Fields | None = None

    def close_item() -> None:
        if last is not None and last_fields is not None:
            last.id, last.tags, last.backed = last_fields.id, last_fields.tags, last_fields.backed
            if len(last.text) > MASTER_MAX_ITEM_CHARS:
                _bad(last.line, f"a line has at most {MASTER_MAX_ITEM_CHARS} characters")
            if "<!--" in last.text or "-->" in last.text:
                _bad(last.line, "a comment belongs at the end of the line")

    for number, raw in enumerate(raw_lines, 1):
        if _CONTROL.search(raw):
            _bad(number, "control characters are not allowed")
        line, fields = _split(raw, number)
        if not line:
            if fields.marker is not None and fields.marker != MASTER_FORMAT:
                raise MasterResumeError(
                    "master_format_unsupported",
                    f"line {number}: this is master format {fields.marker}; this GigAI reads format {MASTER_FORMAT}",
                )
            after_blank = True
            continue
        blank_before, after_blank = after_blank, False
        hashes = _HASHES.match(line)
        if hashes and len(hashes.group(1)) == 2:
            close_item()
            last = last_fields = None
            name = _flat(hashes.group(2)).rstrip(":").lower()
            if name not in SECTION_HEADINGS:
                _bad(number, "unknown section; use ## " + ", ## ".join(item.capitalize() for item in SECTION_HEADINGS))
            if any(item.name == name for item in draft.sections):
                _bad(number, f"the {name.capitalize()} section appears twice")
            section = DraftSection(name)
            draft.sections.append(section)
            continue
        if section is None:
            continue  # above the first section: never part of the master
        if hashes and len(hashes.group(1)) == 1:
            _bad(number, "a '# ' title belongs above the first section")
        in_entries = section.name in ENTRY_SECTIONS
        if hashes:
            if not in_entries:
                _bad(number, f"'{hashes.group(1)} ' entry headings belong in Experience, Projects or Education")
            if fields.tags or fields.backed:
                _bad(number, "an entry heading takes an id only; tags and backed belong on its lines")
            close_item()
            last = last_fields = None
            heading = _flat(hashes.group(2))
            if not heading:
                _bad(number, "an entry heading needs a name")
            section.entries.append(DraftEntry(number, section.name, heading, fields.id))
            continue
        bullet = _BULLET.match(line)
        text = _flat(bullet.group(1) if bullet else line)
        if not text:
            continue
        if in_entries:
            if not section.entries:
                _bad(number, "start the entry with '### <employer, project or school>' first")
            entry = section.entries[-1]
            if bullet:
                close_item()
                last, last_fields = DraftItem(number, section.name, text), fields
                entry.bullets.append(last)
            elif not entry.bullets:
                if len(entry.sublines) + 1 >= MAX_HEADING_LINES:
                    _bad(number, f"an entry heading has at most {MAX_HEADING_LINES} lines; bullets start with '- '")
                if fields.id or fields.tags or fields.backed:
                    _bad(number, "a heading line takes no id; the id belongs on the '### ' line")
                entry.sublines.append(text)
            elif last is not None and last_fields is not None and not blank_before:
                last.text = f"{last.text} {text}"
                last_fields.merge(fields, number)
            else:
                _bad(number, "text after an entry's bullets; start a bullet with '- ' or a new entry with '### '")
            continue
        if not bullet and last is not None and last_fields is not None and not blank_before:
            last.text = f"{last.text} {text}"  # a hard wrap, or the same paragraph
            last_fields.merge(fields, number)
            continue
        close_item()
        last, last_fields = DraftItem(number, section.name, text), fields
        section.items.append(last)
    close_item()
    draft.sections = [item for item in draft.sections if item.entries or item.items]
    if not draft.sections:
        raise MasterResumeError(
            "master_markdown_invalid",
            "no resume content: add at least one '## ' section (" + ", ".join(item.capitalize() for item in SECTION_HEADINGS) + ") with lines under it",
        )
    if len(draft.all_items()) > MASTER_MAX_ITEMS:
        raise MasterResumeError("master_too_large", f"the master has more than {MASTER_MAX_ITEMS} lines with an id")
    return draft


# --- ids ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IdAssignment:
    #: Lines and entries that got a NEW id.
    assigned: int = 0
    #: Lines and entries that came without an id and got back the one the previous master has for the same text.
    restored: int = 0


def _new_id(prefix: str, seed: str, taken: set[str]) -> str:
    base = f"{prefix}-{_hex(seed)[:_ID_HEX_CHARS]}"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def assign_ids(draft: MasterDraft, previous: Master | None = None) -> IdAssignment:
    """Give every entry and line of ``draft`` without an id one, in place.

    A line that lost its id comment gets its id back when ``previous`` has a line with the same text (in
    the same entry first, then anywhere in the same section) that ``draft`` does not show under another
    id. Every other one gets a new id, derived from its text (the same file gets the same ids) and never
    one ``previous`` used for something else."""

    explicit: dict[str, int] = {}
    for entry in draft.all_entries():
        if entry.id is not None:
            if entry.id in explicit:
                _bad(entry.line, f"this id is already used on line {explicit[entry.id]}")
            explicit[entry.id] = entry.line
    for item in draft.all_items():
        if item.id is not None:
            if item.id in explicit:
                _bad(item.line, f"this id is already used on line {explicit[item.id]}")
            explicit[item.id] = item.line
    used = set(explicit)
    taken = set(used) | (set(previous.entries) | set(previous.items) if previous is not None else set())
    assigned = restored = 0
    for section in draft.sections:
        for entry in section.entries:
            if entry.id is None:
                old = next(
                    (item for item in (previous.entries_in(section.name) if previous is not None else []) if item.heading == entry.heading and item.id not in used),
                    None,
                )
                if old is not None:
                    entry.id, restored = old.id, restored + 1
                else:
                    entry.id, assigned = _new_id(_ENTRY_PREFIX[section.name], f"{section.name}\n{entry.heading}\n{' '.join(entry.sublines)}", taken), assigned + 1
                used.add(entry.id)
            for bullet in entry.bullets:
                if bullet.id is not None:
                    continue
                same = [item for item in (previous.in_section(section.name) if previous is not None else []) if item.text == bullet.text and item.id not in used]
                old_item = next((item for item in same if item.entry_id == entry.id), same[0] if same else None)
                if old_item is not None:
                    bullet.id, restored = old_item.id, restored + 1
                else:
                    bullet.id, assigned = _new_id(_ITEM_PREFIX[KIND_BULLET], f"{section.name}\n{entry.heading}\n{bullet.text}", taken), assigned + 1
                used.add(bullet.id)
        for item in section.items:
            if item.id is not None:
                continue
            old_line = next(
                (line for line in (previous.in_section(section.name) if previous is not None else []) if line.text == item.text and line.id not in used),
                None,
            )
            if old_line is not None:
                item.id, restored = old_line.id, restored + 1
            else:
                item.id, assigned = _new_id(_ITEM_PREFIX[_kind(section.name)], f"{section.name}\n{item.text}", taken), assigned + 1
            used.add(item.id)
    return IdAssignment(assigned, restored)


def build_master(draft: MasterDraft) -> Master:
    """``draft`` as a ``Master``; every entry and line must have its id (``assign_ids`` first)."""

    entries: dict[str, MasterEntry] = {}
    items: dict[str, MasterItem] = {}
    seen: dict[str, int] = {}
    order = 0

    def claim(item_id: str | None, line: int) -> str:
        if item_id is None:
            _bad(line, "this line has no id")
        assert item_id is not None
        if item_id in seen:
            _bad(line, f"this id is already used on line {seen[item_id]}")
        seen[item_id] = line
        return item_id

    for section in draft.sections:
        for entry in section.entries:
            order += 1
            entry_id, entry_order = claim(entry.id, entry.line), order
            bullets: list[str] = []
            for bullet in entry.bullets:
                order += 1
                bullet_id = claim(bullet.id, bullet.line)
                items[bullet_id] = MasterItem(bullet_id, section.name, KIND_BULLET, bullet.text, bullet.tags, bullet.backed, entry_id, order)
                bullets.append(bullet_id)
            entries[entry_id] = MasterEntry(entry_id, section.name, entry.heading, tuple(entry.sublines), tuple(bullets), entry_order)
        for item in section.items:
            order += 1
            item_id = claim(item.id, item.line)
            items[item_id] = MasterItem(item_id, section.name, _kind(section.name), item.text, item.tags, item.backed, None, order)
    return Master(tuple(section.name for section in draft.sections), entries, items)


def parse_master(markdown: str) -> Master:
    """The stored form read back: every entry and line has its id, or ``MasterResumeError``."""

    return build_master(draft_master(markdown))


@dataclass(frozen=True)
class MasterChange:
    """What differs between two masters, by id (entries and lines counted together)."""

    added: int = 0
    removed: int = 0
    changed: int = 0

    def to_json(self) -> dict[str, int]:
        return {"added": self.added, "removed": self.removed, "changed": self.changed}


def compare(previous: Master | None, current: Master) -> MasterChange:
    """``current`` against ``previous``: ids that are new, ids that are gone, ids whose content differs."""

    def facts(master: Master) -> dict[str, object]:
        out: dict[str, object] = {entry.id: ("entry", entry.section, entry.heading, entry.sublines) for entry in master.entries.values()}
        out.update({item.id: ("item", item.section, item.entry_id, item.text, item.tags, item.backed) for item in master.items.values()})
        return out

    now = facts(current)
    before = facts(previous) if previous is not None else {}
    return MasterChange(
        added=sum(key not in before for key in now),
        removed=sum(key not in now for key in before),
        changed=sum(key in before and before[key] != value for key, value in now.items()),
    )


__all__ = [
    "IdAssignment",
    "KIND_BULLET",
    "KIND_OTHER",
    "KIND_SKILLS",
    "KIND_SUMMARY",
    "MASTER_FORMAT",
    "MASTER_MARKER",
    "MASTER_MAX_BYTES",
    "Master",
    "MasterChange",
    "MasterDraft",
    "MasterEntry",
    "MasterItem",
    "MasterResumeError",
    "STRENGTHS",
    "assign_ids",
    "build_master",
    "compare",
    "draft_master",
    "parse_master",
    "skill_names",
]
