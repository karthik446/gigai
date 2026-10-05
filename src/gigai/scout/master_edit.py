"""Changes to the master resume, one line at a time (0.1.10.9 master P5 and P6).

``master_store.import_master`` stores a whole file. This module is the other
writer: ONE line, entry or skill is added, edited or removed by id. It has
two callers, and they call the same functions: the CLI the user's agent
writes with from a chat (``master_edit_cli``: ``gigai scout resume master
add | edit | remove``) and the routes of the Master page
(``find_jobs/api/master.py``: ``/api/master/lines``, ``/api/master/entries``).
Every function here reads the stored master, changes a copy and stores it as
the next revision (``master_store.revise_master``); nothing is rewritten.

THE RULES, the same for every write and for both callers:

* **The revision it read.** ``revision`` is checked before anything else; a
  master that moved on is ``revision_conflict`` with the revision it is at.
  ``edit`` and ``remove`` need it (they change what someone read); ``add``
  takes it when given and otherwise writes on top of the current revision
  (the routes ask for it on every write).
* **The privacy check on every new text.** A line, a heading, a skill or a
  source that looks like contact data is REFUSED (``personal_info_refused``:
  the check the answers and stories use), and the stored text is run through
  the import's own detector once more. Nothing is dropped silently and no
  refusal quotes the text.
* **Ids are stable.** A new line gets an id from its text
  (``master_resume.assign_ids``); an edit never changes an id.
* **A removed line is retired, never lost.** The new revision does not hold
  it, so no resume selects it; the revision before still does
  (``retired_lines`` lists every such line with its text and the revision
  that keeps it, ``restore`` puts it back under its own id).
* **A near-duplicate is asked, not stored.** An ``add`` whose text is a line
  the master already has, worded differently
  (``master_migration.near_duplicate``), writes nothing and returns the
  lines it looks like (``status: near_duplicate``), unless ``force``.
* **The profiles follow.** A write that made a revision ends with
  ``master_profiles_cli.after_master_write``: a profile that shows an edited
  or retired line gets its resume printed again, and lines the master gained
  are offered to the profiles (``MasterEdit.profiles``).
* **A note is guidance, never text** (0.1.11). ``edit`` sets or clears the
  note of a line or an entry (``clean_note``: one line, at most 300
  characters, the contact check). A note edit is a new revision; the line's
  ``mark`` stays, so no resume is printed again and no assessment goes stale.

EVIDENCE.  The master holds claims; answers and stories are their evidence.
``evidence_for`` reads a story or an answer and gives the ``backed`` link
(``story:<id>``, ``answer:<question_id>``) a promoted line carries. A number
the line states that its evidence does not is a warning, never a refusal:
the writer checks it with the user.

WHO WROTE IT.  The journal records who wrote a revision. What it cannot
hold is who wrote one LINE and where its evidence came from, so that lives
beside the answers in one small local file::

    <home>/scout/<project_id>/master/lines.json

    {"schema_version": "scout-master-lines:1",
     "lines": {"<id>": {"mark": "<16 hex>", "written_by": "operator" | "agent",
                        "source": "<free text>" | absent, "updated_at": "...", "revision": 7,
                        "skills": {"<name, casefolded>": {"name", "written_by", "source"?, "backed"?, "updated_at", "revision"}}}}}

``mark`` is the line's own mark (its id and text): an entry of the file
describes the text that write made, and is not shown once the text has
changed some other way (a file import). ``source`` is free text, checked for
contact shapes; it is never sent to a model.

Local and model-free: nothing here calls a model or the network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import threading

from ..canonical import digest_imported_bytes
from ..workpad import one_operation
from . import master_resume
from .master_migration import NEAR_DUPLICATE, near_duplicate
from .master_resume import (
    KIND_BULLET,
    KIND_SKILLS,
    KIND_SUMMARY,
    DraftEntry,
    DraftItem,
    DraftSection,
    Master,
    MasterChange,
    MasterDraft,
    MasterEntry,
    MasterResumeError,
    assign_ids,
    build_master,
    compare,
    parse_master,
    skill_names,
)
from .master_store import (
    ACTORS,
    MasterRevision,
    MasterStoreError,
    StoredMaster,
    earlier_masters,
    load_master,
    master_revisions,
    read_revisions,
    revise_master,
)
from .tailored_resume import ENTRY_SECTIONS, MAX_HEADING_LINES, SECTION_HEADINGS, numeric_values

LINES_SCHEMA = "scout-master-lines:1"
#: One line of the master as a chat or the Master page writes it: a bullet, an Other line (the file format itself allows more).
MAX_LINE_CHARS = 400
#: A summary variant is a short paragraph and a Skills line a list: both run longer than a bullet.
MAX_LONG_LINE_CHARS = 1000
MAX_HEADING_CHARS = 200
MAX_SKILL_CHARS = 60
MAX_TAGS = 12
#: The lines an ``add`` is told it looks like, at most.
MAX_NEAR = 5

ACTION_ADD = "add"
ACTION_EDIT = "edit"
ACTION_REMOVE = "remove"
ACTION_RESTORE = "restore"

_BULLET_MARK = re.compile(r"\A[-*•]\s+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LINE_RULE = re.compile(r"\Aline \d+: ")
_FILE_LOCK = threading.Lock()


class MasterEditError(MasterStoreError):
    """A change to the master that is refused; ``code`` is the CLI error code. No message quotes the text."""


# --- what a change returns -----------------------------------------------------------------------


@dataclass(frozen=True)
class NearLine:
    """A line of the master a new line looks like."""

    id: str
    text: str
    entry_id: str | None
    similarity: float
    #: False: the two state different numbers, so one of them is out of date.
    same_numbers: bool

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "text": self.text, "entry_id": self.entry_id, "similarity": self.similarity, "same_numbers": self.same_numbers}


@dataclass(frozen=True)
class Retired:
    """A line or an entry the master no longer holds, and the revision that still does."""

    id: str
    #: ``entry``, or the line's kind (``summary``, ``bullet``, ``skills``, ``other``).
    kind: str
    section: str
    entry_id: str | None
    #: The line's text; an entry's heading.
    text: str
    #: The last revision that holds it (``master show --revision N``).
    last_revision: int
    #: The revision that retired it, and who wrote that one.
    retired_in: int
    retired_by: str
    #: A line: the heading of its entry as that revision had it (``None`` for a line of a plain section).
    entry_heading: str | None = None
    #: An entry: the lines under its heading as that revision had them.
    sublines: tuple[str, ...] = ()
    #: A line that left together with its entry: ``restore`` of the entry brings it back.
    with_entry: bool = False

    @property
    def what(self) -> str:
        """``entry`` or ``line``."""

        return "entry" if self.kind == "entry" else "line"

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id, "kind": self.kind, "section": self.section, "entry_id": self.entry_id, "text": self.text,
            "last_revision": self.last_revision, "retired_in": self.retired_in, "retired_by": self.retired_by,
        }


@dataclass(frozen=True)
class Evidence:
    """A story or an answer a line is promoted from: the ``backed`` link and what it says."""

    ref: str
    kind: str
    text: str


@dataclass(frozen=True)
class MasterEdit:
    """What ``add`` / ``edit`` / ``remove`` / ``restore`` did."""

    action: str
    #: ``revised`` (a new revision), ``unchanged`` (the master already says this) or ``near_duplicate`` (asked; nothing written).
    status: str
    #: The master after the change, or as it is when nothing was written.
    stored: StoredMaster
    change: MasterChange = MasterChange()
    #: The lines and entries added, edited or restored.
    ids: tuple[str, ...] = ()
    retired: tuple[Retired, ...] = ()
    skills_line: str | None = None
    skills_added: tuple[str, ...] = ()
    skills_removed: tuple[str, ...] = ()
    #: Skills an ``add`` named that the master already lists.
    already_listed: tuple[str, ...] = ()
    near_duplicates: tuple[NearLine, ...] = ()
    warnings: tuple[str, ...] = ()
    #: What ``after_master_write`` did (``synced``, ``offers``); ``None`` when no revision was written.
    profiles: Mapping[str, object] | None = None
    #: P8: where the revision went in the resumes folder (``master_store.write_file``); ``None`` when no revision was written.
    file: Mapping[str, object] | None = None

    @property
    def written(self) -> bool:
        return self.status == "revised"

    @property
    def id(self) -> str | None:
        """The line or entry the change was about: the one added, edited or put back, else the one retired."""

        return self.ids[0] if self.ids else self.retired[0].id if self.retired else None

    def to_json(self) -> dict[str, object]:
        """What a route answers with (the CLI prints the same facts, with each changed line in full)."""

        return {
            "action": self.action, "status": self.status, "written": self.written, "id": self.id, "ids": list(self.ids),
            "changes": self.change.to_json(), "retired": [item.to_json() for item in self.retired],
            "skills": {
                "line": self.skills_line, "added": list(self.skills_added), "removed": list(self.skills_removed),
                "already_listed": list(self.already_listed),
            },
            "near_duplicates": [line.to_json() for line in self.near_duplicates], "warnings": list(self.warnings),
            "profiles": dict(self.profiles) if self.profiles is not None else {"synced": [], "offers": []},
            "file": dict(self.file) if self.file is not None else None,
        }


@dataclass
class _Plan:
    """What one change did to the draft: filled by the function that makes it."""

    #: The draft's new or changed lines and entries (their ids are known once ids are assigned).
    touched: list[DraftItem | DraftEntry]
    #: The ids of the lines and entries the change took out (an entry first, then its lines).
    retired: list[str]
    skills_item: DraftItem | None = None
    skills_added: tuple[str, ...] = ()
    skills_removed: tuple[str, ...] = ()
    already_listed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    backed: str | None = None

    @classmethod
    def empty(cls) -> "_Plan":
        return cls([], [])


class _Asked(Exception):
    """An ``add`` that looks like lines the master has: nothing is written."""

    def __init__(self, near: tuple[NearLine, ...]) -> None:
        super().__init__("near_duplicate")
        self.near = near


# --- text ---------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _numbers(text: str) -> frozenset[str]:
    return frozenset(str(found.value) for found in numeric_values(text))


def _refuse_contact(text: str, what: str) -> None:
    from .story_bank import personal_info_in_answer

    found = personal_info_in_answer(text)
    if found:
        raise MasterEditError(
            "personal_info_refused",
            f"{what} looks like it holds personal information ({', '.join(found)}); the master holds experience, never a name or "
            "contact details (GigAI stores none: you type them only when you generate a PDF). Remove it and write again",
        )


def _clean(value: object, *, what: str, limit: int, bullet: bool = False) -> str:
    """``value`` as one line of the master: whitespace collapsed, checked. ``bullet``: a pasted ``- `` mark is dropped."""

    if not isinstance(value, str):
        raise MasterEditError("master_text_invalid", f"{what} must be text")
    if _CONTROL.search(value):
        raise MasterEditError("master_text_invalid", f"{what} must be one line, without control characters")
    text = " ".join(value.split())
    if bullet:
        text = _BULLET_MARK.sub("", text, count=1).strip()
    if not text:
        raise MasterEditError("master_text_invalid", f"{what} is empty")
    if len(text) > limit:
        raise MasterEditError("master_text_invalid", f"{what} has {len(text)} characters; at most {limit} (one line)")
    if "<!--" in text or "-->" in text:
        raise MasterEditError("master_text_invalid", f"{what} holds a comment mark; ids, tags and links are set with options, never typed")
    if not bullet and text[0] in "#-*•":
        raise MasterEditError("master_text_invalid", f"{what} starts with a markdown mark; write the plain words")
    _refuse_contact(text, what)
    return text


def line_limit(kind: str) -> int:
    """How long a line of ``kind`` may be when it is written here."""

    return MAX_LONG_LINE_CHARS if kind in (KIND_SUMMARY, KIND_SKILLS) else MAX_LINE_CHARS


def _fits(text: str, kind: str) -> None:
    if len(text) > line_limit(kind):
        raise MasterEditError("master_text_invalid", f"the line has {len(text)} characters; at most {line_limit(kind)} (one line)")


def clean_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Tags as the master stores them (``tags:ai,llm``); one empty value means none."""

    found: list[str] = []
    for tag in tags:
        clean = str(tag).strip()
        if not clean:
            continue
        if not master_resume._TAG.fullmatch(clean):  # noqa: SLF001 - the format's own rule for a tag
            raise MasterEditError("master_tag_invalid", "a tag is one word of letters, digits and + # . - _ (at most 40 characters)")
        if clean not in found:
            found.append(clean)
    if len(found) > MAX_TAGS:
        raise MasterEditError("master_tag_invalid", f"a line has at most {MAX_TAGS} tags")
    return tuple(found)


def clean_backed(backed: Iterable[str]) -> tuple[str, ...]:
    """The links a line carries, set as a whole (``story:<id>``, ``answer:<question_id>``): the format's own rule.

    ``evidence_for`` is the checked way to link a line (the story or answer is read); this one only checks the
    shape, as a file import does."""

    found: list[str] = []
    for ref in backed:
        clean = str(ref).strip()
        if not master_resume._BACKED.fullmatch(clean):  # noqa: SLF001 - the format's own rule for a backed link
            raise MasterEditError("master_backed_invalid", "a link names a story or an answer: story:<id> or answer:<question_id>")
        if clean not in found:
            found.append(clean)
    if len(found) > MAX_TAGS:
        raise MasterEditError("master_backed_invalid", f"a line has at most {MAX_TAGS} links")
    return tuple(found)


def clean_source(source: object) -> str | None:
    """A line's ``source`` as it is kept: one line of free text (the answers' rule), or ``None``."""

    from .story_bank import StoryBankError
    from .story_bank import clean_source as clean

    try:
        return clean(source)
    except StoryBankError as exc:
        raise MasterEditError(exc.code if exc.code == "personal_info_refused" else "master_source_invalid", str(exc)) from None


def clean_note(note: object) -> str:
    """A note as the master stores it: one line of free text by the format's own rule (``master_resume.note_rule``),
    checked for contact data like every other text of the master."""

    if not isinstance(note, str):
        raise MasterEditError("master_note_invalid", "the note must be text")
    if _CONTROL.search(note):
        raise MasterEditError("master_note_invalid", "the note must be one line, without control characters")
    text = " ".join(note.split())
    if not text:
        raise MasterEditError("master_note_invalid", "the note is empty; a note is removed with --clear-note")
    rule = master_resume.note_rule(text)
    if rule is not None:
        raise MasterEditError("master_note_invalid", rule)
    _refuse_contact(text, "the note")
    return text


# --- evidence: a story or an answer becomes a line ----------------------------------------------


def evidence_for(*, home_root: Path, target: Path, story_id: str | None = None, question_id: str | None = None) -> tuple[Evidence, ...]:
    """The story and/or the answer a line is promoted from; ``story_not_found`` / ``answer_not_found`` when there is none."""

    from . import stories, story_bank
    from ..private_records import PrivateRecordError

    found: list[Evidence] = []
    try:
        if story_id is not None:
            story = stories.get_story(home_root=home_root, target=target, story_id=story_id)
            if story is None:
                raise MasterEditError("story_not_found", f"there is no story {story_id!r} (see `gigai scout story list`)")
            ref = story.story_id if story.story_id.startswith("story:") else f"story:{story.story_id}"
            found.append(Evidence(ref, "story", "\n".join((story.title, story.text()))))
        if question_id is not None:
            answer = story_bank.get_answer(home_root=home_root, target=target, question_id=question_id, with_jobs=False)
            if answer is None:
                raise MasterEditError("answer_not_found", f"there is no answer for {question_id!r} (see `gigai scout answers list`)")
            found.append(Evidence(f"answer:{answer.question_id}", "answer", "\n".join((answer.question, answer.answer))))
    except (story_bank.StoryBankError, PrivateRecordError) as exc:
        raise MasterEditError(getattr(exc, "code", "master_evidence_unavailable"), str(exc)) from None
    for item in found:
        if not master_resume._BACKED.fullmatch(item.ref):  # noqa: SLF001 - the format's own rule for a backed link
            raise MasterEditError("master_backed_invalid", f"the {item.kind}'s id cannot be written as a link of the master")
    return tuple(found)


def _number_warnings(text: str, evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    """One warning per piece of evidence the line states a number beyond: never a refusal."""

    warnings: list[str] = []
    for item in evidence:
        stated = _numbers(item.text)
        extra = [found.span for found in numeric_values(text) if str(found.value) not in stated]
        if extra:
            warnings.append(
                f"the line states a number its {item.kind} ({item.ref}) does not: {', '.join(dict.fromkeys(extra))}. "
                "Every number comes from the user: check it with them"
            )
    return tuple(warnings)


# --- the master as a draft that can be changed --------------------------------------------------


def _draft(master: Master) -> MasterDraft:
    draft = MasterDraft()
    for name in master.sections:
        section = DraftSection(name)
        for entry in master.entries_in(name):
            section.entries.append(DraftEntry(
                0, name, entry.heading, entry.id, list(entry.sublines),
                [DraftItem(0, name, master.items[i].text, i, master.items[i].tags, master.items[i].backed, master.items[i].note) for i in entry.bullets],
                entry.note,
            ))
        section.items = [
            DraftItem(0, name, item.text, item.id, item.tags, item.backed, item.note) for item in master.in_section(name) if item.entry_id is None
        ]
        draft.sections.append(section)
    return draft


def _section(draft: MasterDraft, name: str, *, create: bool = False) -> DraftSection | None:
    found = next((section for section in draft.sections if section.name == name), None)
    if found is None and create:
        found = DraftSection(name)
        later = SECTION_HEADINGS[SECTION_HEADINGS.index(name) + 1:]
        index = next((i for i, section in enumerate(draft.sections) if section.name in later), len(draft.sections))
        draft.sections.insert(index, found)
    return found


def _entry(draft: MasterDraft, entry_id: str) -> DraftEntry | None:
    return next((entry for entry in draft.all_entries() if entry.id == entry_id), None)


def _item(draft: MasterDraft, item_id: str) -> DraftItem | None:
    return next((item for item in draft.all_items() if item.id == item_id), None)


def _finish(draft: MasterDraft, previous: Master) -> Master:
    """The changed draft as a master: ids assigned, and read back from its own markdown as a check."""

    draft.sections = [section for section in draft.sections if section.entries or section.items]
    if not draft.sections:
        raise MasterEditError("master_empty", "this would leave the master empty; a master holds at least one line")
    try:
        assign_ids(draft, previous)
        master = build_master(draft)
        again = parse_master(master.markdown())
    except MasterResumeError as exc:
        # The parser names a line of the canonical text; the writer typed one option, so only the rule is said.
        raise MasterEditError("master_text_invalid", "the master would not read back: " + _LINE_RULE.sub("", str(exc))) from None
    if again.markdown() != master.markdown() or compare(master, again) != MasterChange():
        raise MasterEditError("master_text_invalid", "the text would not read back as the one line it is: write plain words, no markdown marks")
    return master


def _entry_key(start: int | None, end: int | None, ongoing: bool) -> tuple[int, int]:
    return (9999 if ongoing else (end or 0), start or 0)


def _dates(sublines: Iterable[str]) -> tuple[int, int]:
    entry = MasterEntry("x", "experience", "x", tuple(sublines))
    return _entry_key(entry.start, entry.end, entry.ongoing)


def near_lines(master: Master, text: str, *, kind: str) -> tuple[NearLine, ...]:
    """The lines of ``kind`` the master has that ``text`` looks like, the closest first (at most ``MAX_NEAR``)."""

    found: list[NearLine] = []
    numbers = _numbers(text)
    for item in master.items.values():
        if item.kind != kind:
            continue
        similarity = 1.0 if _same(item.text, text) else near_duplicate(item.text, text)
        if similarity >= NEAR_DUPLICATE:
            found.append(NearLine(item.id, item.text, item.entry_id, round(similarity, 2), _numbers(item.text) == numbers))
    return tuple(sorted(found, key=lambda line: (-line.similarity, line.id))[:MAX_NEAR])


# --- who wrote a line, and where its evidence came from ----------------------------------------


def lines_path(home_root: Path, target: Path) -> Path:
    from .find_jobs.discovery.storage import project_id

    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "master" / "lines.json"


def entry_mark(entry: MasterEntry) -> str:
    """An entry's mark: 16 hex characters over its id, heading and heading lines (as ``MasterItem.mark`` for a line)."""

    text = "\n".join(("master-entry", entry.id, entry.heading, *entry.sublines))
    return digest_imported_bytes(text.encode("utf-8"))[len("sha256:"):][:16]


def _read_lines(home_root: Path, target: Path) -> dict[str, dict[str, object]]:
    """``id -> what is kept about it``, tolerantly: missing, unreadable or another schema is none."""

    try:
        path = lines_path(home_root, target)
        if path.is_symlink() or not path.is_file():
            return {}
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, RuntimeError):
        return {}
    lines = value.get("lines") if isinstance(value, dict) and value.get("schema_version") == LINES_SCHEMA else None
    return {str(key): item for key, item in lines.items() if isinstance(item, dict)} if isinstance(lines, dict) else {}


def _stamp(actor: str, source: str | None, revision: int, at: str) -> dict[str, object]:
    stamp: dict[str, object] = {"written_by": actor, "updated_at": at, "revision": revision}
    if source:
        stamp["source"] = source
    return stamp


def _record(
    home_root: Path, target: Path, before: Master, master: Master, plan: _Plan, *, actor: str, source: str | None, revision: int,
) -> None:
    """Keep who wrote the lines of ``plan`` and where their evidence came from. A failure to write it never fails the change.

    What is kept describes a line's TEXT. A line whose text this write made (it is new, or its mark moved) is
    stamped with the writer and the source; a line whose text stayed (tags, a link, a source alone) keeps its
    writer and only takes the source when one is named. A Skills line is kept per skill."""

    from .find_jobs.discovery.storage import atomic_write

    at = _now()
    with _FILE_LOCK:
        lines = _read_lines(home_root, target)
        for touched in plan.touched:
            if touched.id in master.entries:
                mark = entry_mark(master.entries[touched.id])
                was = entry_mark(before.entries[touched.id]) if touched.id in before.entries else None
            elif touched.id in master.items and master.items[touched.id].kind != KIND_SKILLS:
                mark = master.items[touched.id].mark
                was = before.items[touched.id].mark if touched.id in before.items else None
            else:
                continue
            kept = lines.get(touched.id, {})
            if mark != was:
                lines[touched.id] = {"mark": mark, **_stamp(actor, source, revision, at)}
            elif source:
                lines[touched.id] = {**(kept if kept.get("mark") == mark else {"mark": mark}), "source": source, "updated_at": at}
        line = plan.skills_item
        if line is not None and line.id is not None and plan.skills_added:
            entry = dict(lines.get(line.id, {}))
            kept_skills = entry.get("skills")
            skills: dict[str, object] = dict(kept_skills) if isinstance(kept_skills, dict) else {}
            for name in plan.skills_added:
                skills[name.casefold()] = {"name": name, **_stamp(actor, source, revision, at), **({"backed": plan.backed} if plan.backed else {})}
            entry["skills"] = skills
            lines[line.id] = entry
        try:
            atomic_write(
                lines_path(home_root, target),
                json.dumps({"schema_version": LINES_SCHEMA, "lines": lines}, indent=2, sort_keys=True).encode("utf-8"),
            )
        except (OSError, ValueError, RuntimeError):
            return


def _shown(stamp: Mapping[str, object]) -> dict[str, object]:
    return {
        "written_by": stamp["written_by"] if stamp.get("written_by") in ACTORS else None,
        "source": stamp["source"] if isinstance(stamp.get("source"), str) and stamp["source"] else None,
        "updated_at": stamp["updated_at"] if isinstance(stamp.get("updated_at"), str) else None,
    }


def provenance(*, home_root: Path, target: Path, master: Master) -> dict[str, dict[str, object]]:
    """``id -> {written_by, source, updated_at}`` for the lines and entries whose text is still what that write made.

    A Skills line carries ``skills`` instead: one ``{name, written_by, source, updated_at, backed}`` per skill it
    still lists that a write here added. An id with nothing known is absent."""

    found: dict[str, dict[str, object]] = {}
    for item_id, kept in _read_lines(home_root, target).items():
        if item_id in master.entries:
            if kept.get("mark") == entry_mark(master.entries[item_id]):
                found[item_id] = _shown(kept)
            continue
        item = master.items.get(item_id)
        if item is None:
            continue
        if item.kind != KIND_SKILLS:
            if kept.get("mark") == item.mark:
                found[item_id] = _shown(kept)
            continue
        listed = {name.casefold(): name for name in skill_names(item.text)[1]}
        kept_skills = kept.get("skills")
        skills = kept_skills if isinstance(kept_skills, dict) else {}
        shown = [
            {"name": listed[key], **_shown(stamp), "backed": stamp["backed"] if isinstance(stamp.get("backed"), str) else None}
            for key, stamp in sorted(skills.items()) if key in listed and isinstance(stamp, dict)
        ]
        if shown:
            found[item_id] = {"skills": shown}
    return found


def item_json(item: master_resume.MasterItem, known: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    """A line as ``master show`` returns it: its own JSON, who wrote its text and where its evidence came from (null: not known)."""

    kept = known.get(item.id, {})
    out: dict[str, object] = {**item.to_json(), "written_by": kept.get("written_by"), "source": kept.get("source")}
    if item.kind == KIND_SKILLS:
        out["skill_sources"] = kept.get("skills", [])
    return out


def entry_json(entry: MasterEntry, known: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    kept = known.get(entry.id, {})
    return {**entry.to_json(), "written_by": kept.get("written_by"), "source": kept.get("source")}


# --- the one way a change is stored --------------------------------------------------------------


@one_operation()
def _revise(
    action: str, *, home_root: Path, target: Path, actor: str, revision: int | None, source: str | None,
    apply: Callable[[StoredMaster, MasterDraft], _Plan], record: bool = True,
) -> MasterEdit:
    """Run ``apply`` on a draft of the stored master and store the result as the next revision.

    The one way a change is stored, for the CLI and for the routes: the revision check, the contact check, who wrote
    the line (``lines.json``), and last the profiles (``after_master_write``) when a revision was written."""

    from .master_profiles_cli import after_master_write

    seen: dict[str, object] = {}

    def change(stored: StoredMaster) -> Master:
        seen["stored"] = stored
        draft = _draft(stored.master)
        seen["plan"] = apply(stored, draft)
        return _finish(draft, stored.master)

    try:
        written = revise_master(home_root=home_root, target=target, change=change, actor=actor, revision=revision)
    except _Asked as asked:
        return MasterEdit(action, "near_duplicate", seen["stored"], near_duplicates=asked.near)  # type: ignore[arg-type]
    except MasterStoreError as exc:
        if exc.code == "master_contact_data":
            raise MasterEditError("personal_info_refused", str(exc)) from None
        raise
    plan: _Plan = seen["plan"]  # type: ignore[assignment]
    before: StoredMaster = seen["stored"]  # type: ignore[assignment]
    master = written.stored.master
    revised = written.status == "revised"
    if record and (revised or source is not None):
        _record(home_root, target, before.master, master, plan, actor=actor, source=source, revision=written.stored.revision.revision)
    gone = set(plan.retired)
    return MasterEdit(
        action, written.status, written.stored, written.change,
        ids=tuple(touched.id for touched in plan.touched if touched.id is not None and touched.id in {**master.entries, **master.items}),
        retired=tuple(
            _retired(before.master, item_id, before.revision.revision, written.stored.revision.revision, actor, gone) for item_id in plan.retired
        ) if revised else (),
        skills_line=plan.skills_item.id if plan.skills_item is not None else None,
        skills_added=plan.skills_added, skills_removed=plan.skills_removed, already_listed=plan.already_listed,
        warnings=plan.warnings,
        # P3: a profile that shows an edited or retired line gets its resume printed again; new lines are only offered.
        profiles=after_master_write(home_root, target) if revised else None,
        file=written.file,
    )


def _required(revision: int | None, home_root: Path, target: Path) -> None:
    """``edit`` and ``remove`` change what someone read: they name the revision they read."""

    if revision is not None:
        return
    chain = master_revisions(home_root=home_root, target=target)
    if not chain:
        raise MasterEditError("master_not_found", "there is no master resume yet")
    raise MasterEditError(
        "revision_required",
        f"pass --revision {chain[-1].revision}: the revision of the master you read (`gigai scout resume master show --json`)",
        current=chain[-1],
    )


def _backed(current: tuple[str, ...], evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    return current + tuple(item.ref for item in evidence if item.ref not in current)


# --- add ----------------------------------------------------------------------------------------


def add_line(
    *, home_root: Path, target: Path, text: str, entry_id: str | None = None, section: str | None = None, tags: Iterable[str] = (),
    backed: Iterable[str] = (), evidence: tuple[Evidence, ...] = (), actor: str = "operator", revision: int | None = None,
    source: str | None = None, force: bool = False,
) -> MasterEdit:
    """Add one line: a bullet under ``entry_id``, or a line of ``section`` (summary, skills, other).

    A line the master already has in that place, word for word, is refused
    (``master_line_exists``); one it has worded differently is asked about
    (``status: near_duplicate``, nothing written) unless ``force``.
    ``evidence`` (``evidence_for``) links the line to a story or an answer
    that is stored; ``backed`` sets links by name."""

    # A bullet when it names an entry; the place itself is checked below.
    kind = KIND_BULLET if entry_id is not None or section is None else master_resume._kind(section)  # noqa: SLF001 - the format's own rule
    clean = _clean(text, what="the line", limit=line_limit(kind), bullet=True)
    wanted_tags, wanted_backed, kept_source = clean_tags(tags), clean_backed(backed), clean_source(source)
    if (entry_id is None) == (section is None):
        raise MasterEditError("master_place_invalid", "say where the line goes: --entry ENTRY_ID (a bullet) or --section summary, skills or other")
    if section is not None and (section not in SECTION_HEADINGS or section in ENTRY_SECTIONS):
        raise MasterEditError(
            "master_place_invalid",
            "a line goes under an entry (--entry ENTRY_ID) or into --section summary, skills or other; a role, project or school is added with --heading",
        )

    def apply(stored: StoredMaster, draft: MasterDraft) -> _Plan:
        master = stored.master
        if entry_id is not None:
            entry = _entry(draft, entry_id)
            if entry is None:
                raise MasterEditError("master_entry_not_found", f"the master has no entry {entry_id!r} (see `gigai scout resume master show`)")
            siblings, name = entry.bullets, entry.section
        else:
            assert section is not None
            siblings, name = _section(draft, section, create=True).items, section  # type: ignore[union-attr]
        same = next((item for item in siblings if _same(item.text, clean)), None)
        if same is not None:
            raise MasterEditError("master_line_exists", f"the master already has this line there, word for word: {same.id}")
        kind = master_resume._kind(name)  # noqa: SLF001 - the format's own rule for what a line of a section is
        plan = _Plan.empty()
        if kind == KIND_SKILLS:
            label, names = skill_names(clean)
            listed = {skill.casefold() for skill in master.skills()}
            plan.already_listed = tuple(skill for skill in names if skill.casefold() in listed)
            plan.skills_added = tuple(skill for skill in names if skill.casefold() not in listed)
            if not plan.skills_added:
                return plan  # every skill is listed already: nothing to write
            taken = next((item for item in siblings if label and skill_names(item.text)[0].casefold() == label.casefold()), None)
            if taken is not None:
                raise MasterEditError("master_line_exists", f"the Skills line {taken.id} has this label: add to it with --skill NAME --to {taken.id}")
        elif not force:
            near = near_lines(master, clean, kind=kind)
            if near:
                raise _Asked(near)
        # A new Skills line lists only what the master does not list yet.
        said = _skills_text(skill_names(clean)[0], plan.skills_added) if kind == KIND_SKILLS else clean
        item = DraftItem(0, name, said, None, wanted_tags, _backed(wanted_backed, evidence))
        siblings.append(item)
        plan.touched.append(item)
        if kind == KIND_SKILLS:
            plan.skills_item, plan.backed = item, next((found.ref for found in evidence), None)
        else:
            plan.warnings = _number_warnings(clean, evidence)
        return plan

    return _revise(ACTION_ADD, home_root=home_root, target=target, actor=actor, revision=revision, source=kept_source, apply=apply)


def add_entry(
    *, home_root: Path, target: Path, section: str, heading: str, sublines: Iterable[str] = (), actor: str = "operator",
    revision: int | None = None, source: str | None = None,
) -> MasterEdit:
    """Add a role, a project or a school: its heading and the lines under it (``Title | Jun 2022 - Present``).

    It is placed by its dates among the section's entries (the newest first); one that names no year goes last."""

    if section not in ENTRY_SECTIONS:
        raise MasterEditError("master_place_invalid", "an entry (--heading) goes into --section experience, projects or education")
    clean = _clean(heading, what="the heading", limit=MAX_HEADING_CHARS)
    lines = [_clean(line, what="a heading line", limit=MAX_HEADING_CHARS) for line in sublines]
    if len(lines) >= MAX_HEADING_LINES:
        raise MasterEditError("master_text_invalid", f"an entry has at most {MAX_HEADING_LINES - 1} lines under its heading")
    kept_source = clean_source(source)

    def apply(_stored: StoredMaster, draft: MasterDraft) -> _Plan:
        entries = _section(draft, section, create=True).entries  # type: ignore[union-attr]
        same = next((entry for entry in entries if _same(entry.heading, clean) and [*map(str.casefold, entry.sublines)] == [*map(str.casefold, lines)]), None)
        if same is not None:
            raise MasterEditError("master_entry_exists", f"the master already has this entry: {same.id}")
        entry = DraftEntry(0, section, clean, None, lines, [])
        key = _dates(lines)
        index = len(entries) if key == (0, 0) else next((i for i, other in enumerate(entries) if _dates(other.sublines) < key), len(entries))
        entries.insert(index, entry)
        return _Plan([entry], [])

    return _revise(ACTION_ADD, home_root=home_root, target=target, actor=actor, revision=revision, source=kept_source, apply=apply)


def _skill(value: str) -> str:
    name = _clean(value, what="a skill", limit=MAX_SKILL_CHARS).rstrip(".").strip()
    if skill_names(name) != ("", (name,)):
        raise MasterEditError("master_skill_invalid", "a skill is one name: no comma, semicolon or colon (pass --skill once per skill)")
    return name


def _skills_text(label: str, names: Iterable[str]) -> str:
    joined = ", ".join(names)
    return f"{label}: {joined}" if label else joined


def add_skills(
    *, home_root: Path, target: Path, names: Iterable[str], line_id: str | None = None, label: str | None = None,
    evidence: tuple[Evidence, ...] = (), actor: str = "operator", revision: int | None = None, source: str | None = None,
) -> MasterEdit:
    """Add skills to a Skills line: ``line_id``, else the line labelled ``label`` (made when there is none), else the only one.

    A skill the master already lists anywhere is not added again (``already_listed``)."""

    wanted = list(dict.fromkeys(_skill(name) for name in names))
    if not wanted:
        raise MasterEditError("master_skill_invalid", "name at least one skill")
    if line_id is not None and label is not None:
        raise MasterEditError("master_place_invalid", "pass --to LINE_ID or --label LABEL, not both")
    wanted_label = _clean(label, what="the label", limit=40) if label is not None else None
    if wanted_label is not None and skill_names(f"{wanted_label}: x")[0] != wanted_label:
        raise MasterEditError("master_skill_invalid", "a label is a few plain words: no comma, semicolon, colon or |")
    kept_source = clean_source(source)

    def apply(stored: StoredMaster, draft: MasterDraft) -> _Plan:
        listed = {skill.casefold() for skill in stored.master.skills()}
        plan = _Plan.empty()
        plan.already_listed = tuple(name for name in wanted if name.casefold() in listed)
        plan.skills_added = tuple(name for name in wanted if name.casefold() not in listed)
        plan.backed = next((found.ref for found in evidence), None)
        lines = _section(draft, "skills", create=True).items  # type: ignore[union-attr]
        if line_id is not None:
            line = next((item for item in lines if item.id == line_id), None)
            if line is None:
                raise MasterEditError("master_item_not_found", f"the master has no Skills line {line_id!r} (see `gigai scout resume master show --section skills`)")
        elif wanted_label is not None:
            line = next((item for item in lines if skill_names(item.text)[0].casefold() == wanted_label.casefold()), None)
        elif len(lines) > 1:
            raise MasterEditError(
                "master_skills_line_required",
                "the master has several Skills lines: say which with --to LINE_ID (" + ", ".join(str(item.id) for item in lines) + ") or --label LABEL",
            )
        else:
            line = lines[0] if lines else None
        if line is None:
            if not plan.skills_added:
                return plan
            line = DraftItem(0, "skills", _skills_text(wanted_label or "", plan.skills_added), None, (), _backed((), evidence))
            lines.append(line)
        else:
            current_label, current = skill_names(line.text)
            if plan.skills_added:
                line.text = _skills_text(current_label, (*current, *plan.skills_added))
            line.backed = _backed(line.backed, evidence)
        plan.skills_item = line
        plan.touched.append(line)
        return plan

    return _revise(ACTION_ADD, home_root=home_root, target=target, actor=actor, revision=revision, source=kept_source, apply=apply)


# --- edit ---------------------------------------------------------------------------------------


def edit(
    *, home_root: Path, target: Path, item_id: str, revision: int | None, text: str | None = None, tags: Iterable[str] | None = None,
    backed: Iterable[str] | None = None, heading: str | None = None, sublines: Iterable[str] | None = None,
    evidence: tuple[Evidence, ...] = (), actor: str = "operator", source: str | None = None,
    note: str | None = None, clear_note: bool = False,
) -> MasterEdit:
    """Change a line (its text, its tags, its evidence) or an entry (its heading, the lines under it). The id stays.

    Only what is given changes. ``backed`` replaces the line's links; ``evidence`` adds one. ``source`` alone says
    where the line's evidence came from without changing the line. ``note`` sets the note of a line or an entry
    and ``clear_note`` removes it (0.1.11): a new revision, and the line's mark stays."""

    _required(revision, home_root, target)
    # The longest a line of any kind may be; the line's own kind is known once the master is read (``_fits``).
    clean = _clean(text, what="the line", limit=MAX_LONG_LINE_CHARS, bullet=True) if text is not None else None
    wanted_tags = clean_tags(tags) if tags is not None else None
    wanted_backed = clean_backed(backed) if backed is not None else None
    clean_heading = _clean(heading, what="the heading", limit=MAX_HEADING_CHARS) if heading is not None else None
    lines = [_clean(line, what="a heading line", limit=MAX_HEADING_CHARS) for line in sublines] if sublines is not None else None
    kept_source = clean_source(source)
    if note is not None and clear_note:
        raise MasterEditError("master_edit_invalid", "pass --note or --clear-note, not both")
    wanted_note = clean_note(note) if note is not None else None
    noted = wanted_note is not None or clear_note
    if (
        clean is None and wanted_tags is None and wanted_backed is None and clean_heading is None and lines is None and not evidence
        and kept_source is None and not noted
    ):
        raise MasterEditError(
            "master_edit_empty",
            "nothing to change: pass --text, --tag, --heading, --role, --note, --clear-note, --from-story, --from-answer or --source",
        )

    def apply(stored: StoredMaster, draft: MasterDraft) -> _Plan:
        entry = _entry(draft, item_id)
        if entry is not None:
            if clean is not None or wanted_tags is not None or wanted_backed is not None or evidence:
                raise MasterEditError(
                    "master_edit_invalid", f"{item_id} is an entry: it takes --heading, --role and --note; text, tags and evidence belong to its lines",
                )
            if lines is not None and len(lines) >= MAX_HEADING_LINES:
                raise MasterEditError("master_text_invalid", f"an entry has at most {MAX_HEADING_LINES - 1} lines under its heading")
            entry.heading = clean_heading if clean_heading is not None else entry.heading
            entry.sublines = lines if lines is not None else entry.sublines
            entry.note = wanted_note if noted else entry.note
            return _Plan([entry], [])
        item = _item(draft, item_id)
        if item is None:
            raise MasterEditError(
                "master_item_not_found",
                f"the master has no line or entry {item_id!r} (see `gigai scout resume master show`; a retired one: `gigai scout resume master show --retired`)",
            )
        if clean_heading is not None or lines is not None:
            raise MasterEditError("master_edit_invalid", f"{item_id} is a line: it takes --text, --tag, --note and evidence; --heading and --role belong to an entry")
        plan = _Plan([item], [])
        item.note = wanted_note if noted else item.note
        if clean is not None:
            _fits(clean, master_resume._kind(item.section))  # noqa: SLF001 - the format's own rule for what a line of a section is
            if item.section == "skills":
                before = {name.casefold() for name in stored.master.skills()}
                plan.skills_item = item
                plan.skills_added = tuple(name for name in skill_names(clean)[1] if name.casefold() not in before)
                plan.skills_removed = tuple(name for name in skill_names(item.text)[1] if name.casefold() not in {n.casefold() for n in skill_names(clean)[1]})
                plan.backed = next((found.ref for found in evidence), None)
            item.text = clean
            plan.warnings = _number_warnings(clean, evidence)
        if wanted_tags is not None:
            item.tags = wanted_tags
        item.backed = _backed(wanted_backed if wanted_backed is not None else item.backed, evidence)
        return plan

    return _revise(ACTION_EDIT, home_root=home_root, target=target, actor=actor, revision=revision, source=kept_source, apply=apply)


# --- remove: a line is retired, never lost ------------------------------------------------------


def remove(
    *, home_root: Path, target: Path, revision: int | None, item_id: str | None = None, skills: Iterable[str] = (), actor: str = "operator",
) -> MasterEdit:
    """Retire a line, an entry with its lines, or skills of a Skills line (``item_id``, or wherever they are listed).

    The new revision does not hold them; the revision before does, and ``restore`` brings a line or entry back."""

    _required(revision, home_root, target)
    names = list(dict.fromkeys(" ".join(str(name).split()) for name in skills if str(name).strip()))
    if item_id is None and not names:
        raise MasterEditError("master_item_not_found", "say what to remove: a line or entry id, or --skill NAME")

    def apply(_stored: StoredMaster, draft: MasterDraft) -> _Plan:
        plan = _Plan.empty()
        if names:
            lines = [item for section in draft.sections if section.name == "skills" for item in section.items]
            if item_id is not None:
                lines = [item for item in lines if item.id == item_id]
                if not lines:
                    raise MasterEditError("master_item_not_found", f"the master has no Skills line {item_id!r} (see `gigai scout resume master show --section skills`)")
            wanted = {name.casefold() for name in names}
            gone: list[str] = []
            for line in lines:
                label, listed = skill_names(line.text)
                kept = [name for name in listed if name.casefold() not in wanted]
                if len(kept) == len(listed):
                    continue
                gone += [name for name in listed if name.casefold() in wanted]
                plan.skills_item = line
                if kept:
                    line.text = _skills_text(label, kept)
                    plan.touched.append(line)
                else:
                    plan.retired.append(str(line.id))
            missing = [name for name in names if name.casefold() not in {found.casefold() for found in gone}]
            if missing:
                raise MasterEditError("master_skill_not_found", "the master does not list: " + ", ".join(missing) + " (see `gigai scout resume master show --section skills`)")
            plan.skills_removed = tuple(gone)
            emptied = set(plan.retired)
            for section in draft.sections:
                section.items = [item for item in section.items if item.id not in emptied]
            return plan
        for section in draft.sections:
            for entry in section.entries:
                if entry.id == item_id:
                    plan.retired += [str(entry.id), *(str(bullet.id) for bullet in entry.bullets)]
                    section.entries.remove(entry)
                    return plan
                for bullet in entry.bullets:
                    if bullet.id == item_id:
                        plan.retired.append(str(bullet.id))
                        entry.bullets.remove(bullet)
                        return plan
            for item in section.items:
                if item.id == item_id:
                    plan.retired.append(str(item.id))
                    section.items.remove(item)
                    return plan
        raise MasterEditError(
            "master_item_not_found",
            f"the master has no line or entry {item_id!r} (see `gigai scout resume master show`; already retired: `gigai scout resume master show --retired`)",
        )

    return _revise(ACTION_REMOVE, home_root=home_root, target=target, actor=actor, revision=revision, source=None, apply=apply)


# --- retired lines, and putting one back --------------------------------------------------------


def _retired(master: Master, item_id: str, last: int, following: int, by: str, gone: Iterable[str]) -> Retired:
    """``item_id`` as ``master`` (revision ``last``, the last that holds it) has it; ``gone``: every id that left in that step."""

    if item_id in master.entries:
        entry = master.entries[item_id]
        return Retired(entry.id, "entry", entry.section, None, entry.heading, last, following, by, sublines=tuple(entry.sublines))
    item = master.items[item_id]
    heading = master.entries[item.entry_id].heading if item.entry_id in master.entries else None
    return Retired(
        item.id, item.kind, item.section, item.entry_id, item.text, last, following, by,
        entry_heading=heading, with_entry=item.entry_id is not None and item.entry_id in gone,
    )


def _retired_scan(live: Master, earlier: Iterable[tuple[MasterRevision, MasterRevision, Master]]) -> list[Retired]:
    """What the revisions of ``earlier`` (newest first: the revision, the one that followed it, its master) hold and ``live`` does not.

    Each is listed once, as the last revision that holds it has it: an entry first, then its lines."""

    held = set(live.items) | set(live.entries)
    seen: set[str] = set()
    found: list[Retired] = []
    for revision, following, master in earlier:
        # `here`: what this revision is the last to hold. A line whose entry is in it too left with its entry; a line
        # retired before its entry is met in an earlier revision, where the entry is already `seen`.
        here = {item_id: None for item_id in (*master.entries, *master.items) if item_id not in held and item_id not in seen}
        found += [_retired(master, item_id, revision.revision, following.revision, following.written_by, here) for item_id in here]
        seen.update(here)
    return found


def retired_lines(*, home_root: Path, target: Path) -> list[Retired]:
    """Every line and entry an earlier revision holds and the master does not, the last retired first.

    Read from the journal's revisions, so a line a file import dropped is listed like one ``remove`` retired."""

    stored = load_master(home_root=home_root, target=target)
    if stored is None:
        raise MasterEditError("master_not_found", "there is no master resume yet")
    return _retired_scan(stored.master, earlier_masters(home_root=home_root, target=target))


_KEPT_LOCK = threading.Lock()
#: (workpad path, revision id) -> that revision's master. A revision's content never changes.
_kept: dict[tuple[str, str], Master] = {}
_KEPT_MAX = 256


def revisions(home_root: Path, target: Path) -> list[tuple[MasterRevision, Master]]:
    """Every revision of the master with its parsed content, oldest first; each revision is read once per process.

    For a caller that stays up (the API's history): ``retired_lines`` reads the journal each time."""

    key = str(Path(target))
    with _KEPT_LOCK:
        known = {revision_id: master for (where, revision_id), master in _kept.items() if where == key}
    chain = read_revisions(home_root=home_root, target=target, known=known)
    with _KEPT_LOCK:
        if len(_kept) + len(chain) > _KEPT_MAX:
            _kept.clear()
        for revision, master in chain:
            _kept[(key, revision.revision_id)] = master
    return chain


def retired(chain: Sequence[tuple[MasterRevision, Master]]) -> list[Retired]:
    """``retired_lines`` over a chain already read (``revisions``), as the Master page lists it.

    A line that left with its entry is not listed on its own: it is under the entry, and ``restore`` of the
    entry brings it back."""

    if not chain:
        return []
    earlier = ((chain[index][0], chain[index + 1][0], chain[index][1]) for index in range(len(chain) - 2, -1, -1))
    return [gone for gone in _retired_scan(chain[-1][1], earlier) if not gone.with_entry]


def _place(sequence: list, old_ids: tuple[str, ...], item_id: str, new) -> None:  # noqa: ANN001 - draft items or entries
    """Insert ``new`` into ``sequence`` where it stood: before the first of the ids that followed it that is still
    there, else right after the last of the ids that came before it, else at the end."""

    at = old_ids.index(item_id) if item_id in old_ids else len(old_ids)
    before, after = old_ids[:at], old_ids[at + 1:]
    index = next((i for i, other in enumerate(sequence) if other.id in after), None)
    if index is None:
        index = next((i + 1 for i in range(len(sequence) - 1, -1, -1) if sequence[i].id in before), len(sequence))
    sequence.insert(index, new)


def restore(*, home_root: Path, target: Path, item_id: str, actor: str = "operator", revision: int | None = None) -> MasterEdit:
    """Put a retired line or entry back, under its own id, with the text, tags, evidence and note it had.

    An entry comes back with the lines it had that the master does not hold. A bullet needs its entry: restore that first."""

    def apply(stored: StoredMaster, draft: MasterDraft) -> _Plan:
        live = stored.master
        if item_id in live.items or item_id in live.entries:
            raise MasterEditError("master_line_exists", f"{item_id} is in the master: there is nothing to restore")
        old = next((master for _revision, _following, master in earlier_masters(home_root=home_root, target=target) if item_id in master.items or item_id in master.entries), None)
        if old is None:
            raise MasterEditError("master_item_not_found", f"no revision of the master holds {item_id!r} (see `gigai scout resume master show --retired`)")
        if item_id in old.entries:
            was = old.entries[item_id]
            bullets = [
                DraftItem(0, was.section, old.items[i].text, i, old.items[i].tags, old.items[i].backed, old.items[i].note)
                for i in was.bullets if i not in live.items
            ]
            entry = DraftEntry(0, was.section, was.heading, was.id, list(was.sublines), bullets, was.note)
            _place(_section(draft, was.section, create=True).entries, tuple(e.id for e in old.entries_in(was.section)), item_id, entry)  # type: ignore[union-attr]
            return _Plan([entry, *bullets], [])
        item = old.items[item_id]
        back = DraftItem(0, item.section, item.text, item.id, item.tags, item.backed, item.note)
        if item.entry_id is not None:
            entry = _entry(draft, item.entry_id)
            if entry is None:
                raise MasterEditError(
                    "master_entry_not_found",
                    f"{item_id} is a line of {item.entry_id}, which the master does not hold: restore the entry first (--restore {item.entry_id})",
                )
            _place(entry.bullets, old.entries[item.entry_id].bullets, item_id, back)
        else:
            _place(_section(draft, item.section, create=True).items, tuple(i.id for i in old.in_section(item.section)), item_id, back)  # type: ignore[union-attr]
        return _Plan([back], [])

    # A restored line is the text it was: who wrote it is what was kept for that text, so nothing new is recorded.
    return _revise(ACTION_RESTORE, home_root=home_root, target=target, actor=actor, revision=revision, source=None, apply=apply, record=False)


__all__ = [
    "ACTION_ADD",
    "ACTION_EDIT",
    "ACTION_REMOVE",
    "ACTION_RESTORE",
    "Evidence",
    "LINES_SCHEMA",
    "MAX_LINE_CHARS",
    "MAX_LONG_LINE_CHARS",
    "MasterEdit",
    "MasterEditError",
    "NearLine",
    "Retired",
    "add_entry",
    "add_line",
    "add_skills",
    "clean_backed",
    "clean_note",
    "clean_source",
    "clean_tags",
    "edit",
    "entry_json",
    "entry_mark",
    "evidence_for",
    "item_json",
    "line_limit",
    "lines_path",
    "near_lines",
    "provenance",
    "remove",
    "restore",
    "retired",
    "retired_lines",
    "revisions",
]
