"""One line or one entry of the master resume, changed by id (0.1.10.9 master P5).

The Master page (``/api/master...``) changes the master one line at a time:
edit a line's wording or tags, add a line under a role, add a role, retire a
line or a role, put a retired one back.  Each change is ONE new revision of
the master, made through ``master_store.import_master`` (the one write
path), so the canonical form, the ids and the journal's own stale-parent
check are the same as for an imported file.

* **The revision check.**  A write names the revision it read
  (``revision``); a master that moved on since is refused with
  ``revision_conflict`` and the revision it is at now, before anything else
  is looked at.
* **The contact check.**  The new master goes through the import's own
  detector (``master_store.strip_contact``).  The import leaves a flagged
  line out; an edit must never retire a line that way, so a write whose text
  the detector flags is refused whole (``personal_info_refused``, naming the
  kind, never the text) and nothing is written.
* **Retire** takes a line (or a role with its lines) out of the next
  revision.  Nothing is deleted: every earlier revision stays in the
  journal, ``retired`` lists what is gone with the revision that last held
  it, and ``restore`` puts it back under its own id, next to the lines it
  stood beside.
* **Every write ends with** ``master_profiles_cli.after_master_write``: a
  profile that shows an edited or retired line gets its resume printed again,
  and lines the master gained are offered to the profiles
  (``MasterWrite.profiles``).

No model, no network.  An error names an id, a number or a rule, never a
line's text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
import threading

from .master_migration import NEAR_DUPLICATE, near_duplicate
from .master_resume import (
    KIND_SKILLS,
    KIND_SUMMARY,
    Master,
    MasterChange,
    MasterDraft,
    MasterEntry,
    MasterItem,
    DraftEntry,
    DraftItem,
    DraftSection,
    _BACKED,
    _TAG,
    assign_ids,
    build_master,
    draft_master,
)
from .master_store import (
    ACTORS,
    MASTER_FILE_NAME,
    MasterRevision,
    MasterStoreError,
    StoredMaster,
    import_master,
    load_master,
    read_revisions,
    strip_contact,
)
from .tailored_resume import ENTRY_SECTIONS, MAX_HEADING_LINES, SECTION_HEADINGS

#: One line of the master as the Master page and the agent write it (a bullet, an Other line).
MAX_LINE_CHARS = 400
#: A summary variant is a short paragraph and a Skills line a list: both run longer than a bullet.
MAX_LONG_LINE_CHARS = 1000
MAX_HEADING_CHARS = 200
MAX_TAGS = 12

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKER = re.compile(r"\A(?:[-*•]|#{1,6})(?:\s+|\Z)")
_NO_MASTER = "there is no master resume yet; make one from your profiles' resumes first"


class MasterEditError(ValueError):
    """A change of the master that is refused; ``code`` is stable, the message is for a person (never a line's text)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MasterWrite:
    """What one change did.

    ``status``: ``revised`` (a new revision) or ``unchanged`` (the master
    already said this: nothing written).  ``id``: the line or entry the
    change was about (a new one: the id it got).  ``near_duplicate``: for a
    new line, the line of the same section it reads like (``{id,
    similarity}``), a warning and never a refusal.  ``profiles``: what
    ``after_master_write`` did (``synced``, ``offers``).
    """

    status: str
    stored: StoredMaster
    change: MasterChange
    id: str | None
    near_duplicate: dict[str, object] | None = None
    profiles: Mapping[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status, "id": self.id, "changes": self.change.to_json(), "near_duplicate": self.near_duplicate,
            "profiles": dict(self.profiles) if self.profiles is not None else {"synced": [], "offers": []},
        }


# --- what a caller sends ------------------------------------------------------------------------


def _one_line(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise MasterEditError("wrong_type", f"{name} must be a string")
    if _CONTROL.search(value):
        raise MasterEditError("invalid_value", f"{name} must be one line without control characters")
    clean = " ".join(_MARKER.sub("", value.strip()).split())
    if not clean:
        raise MasterEditError("invalid_value", f"{name} must not be empty")
    if len(clean) > limit:
        raise MasterEditError("invalid_value", f"{name} is longer than {limit} characters")
    if "<!--" in clean or "-->" in clean:
        raise MasterEditError("invalid_value", f"{name} must not hold an HTML comment")
    return clean


def line_text(value: object, *, kind: str) -> str:
    """One master line as it is stored: one line, trimmed, a leading bullet marker dropped, within the length bound."""

    return _one_line(value, "text", MAX_LONG_LINE_CHARS if kind in (KIND_SUMMARY, KIND_SKILLS) else MAX_LINE_CHARS)


def _words(value: object, name: str, pattern: re.Pattern[str], rule: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise MasterEditError("wrong_type", f"{name} must be an array of strings")
    out: list[str] = []
    for item in value:  # type: ignore[union-attr]
        word = item.strip()
        if not pattern.fullmatch(word):
            raise MasterEditError("invalid_value", f"{name}: {rule}")
        if word not in out:
            out.append(word)
    if len(out) > MAX_TAGS:
        raise MasterEditError("invalid_value", f"{name} holds at most {MAX_TAGS}")
    return tuple(out)


def tags_value(value: object) -> tuple[str, ...]:
    return _words(value, "tags", _TAG, "a tag is one word of letters, digits and + # . - _ (at most 40 characters)")


def backed_value(value: object) -> tuple[str, ...]:
    return _words(value, "backed", _BACKED, "names a story or an answer (story:<id> or answer:<question_id>)")


def sublines_value(value: object) -> list[str]:
    """An entry's lines under its heading (``Title | Jun 2019 - Jan 2023``): at most ``MAX_HEADING_LINES - 1``."""

    if type(value) is not list:
        raise MasterEditError("wrong_type", "sublines must be an array of strings")
    lines = [_one_line(item, "a subline", MAX_HEADING_CHARS) for item in value]  # type: ignore[union-attr]
    if len(lines) > MAX_HEADING_LINES - 1:
        raise MasterEditError("invalid_value", f"an entry has at most {MAX_HEADING_LINES - 1} lines under its heading")
    return lines


# --- the write -----------------------------------------------------------------------------------


def _current(home_root: Path, target: Path, revision: int | None, actor: str) -> StoredMaster:
    """The stored master, after the checks every change starts with: the writer, and the revision it read."""

    if actor not in ACTORS:
        raise MasterStoreError("master_actor_invalid", "the writer is operator or agent")
    stored = load_master(home_root=home_root, target=target)
    if stored is None:
        raise MasterStoreError("master_not_found", _NO_MASTER)
    if revision is None:
        raise MasterStoreError("revision_required", "revision is required: the revision of the master you read", current=stored.revision)
    if revision != stored.revision.revision:
        raise MasterStoreError(
            "revision_conflict",
            f"the master is at revision {stored.revision.revision}, not {revision}: read it again, then write on top of revision {stored.revision.revision}",
            current=stored.revision,
        )
    return stored


def _draft(master: Master) -> MasterDraft:
    return draft_master(master.markdown())


def _section(draft: MasterDraft, name: str) -> DraftSection:
    """``name``'s section of ``draft``, added in the shipped section order when the master has none yet."""

    for section in draft.sections:
        if section.name == name:
            return section
    made = DraftSection(name)
    place = SECTION_HEADINGS.index(name)
    index = next((i for i, section in enumerate(draft.sections) if SECTION_HEADINGS.index(section.name) > place), len(draft.sections))
    draft.sections.insert(index, made)
    return made


def _store(home_root: Path, target: Path, stored: StoredMaster, draft: MasterDraft, *, actor: str) -> tuple[str, StoredMaster, MasterChange]:
    """``draft`` as the next revision of the master: ``(status, the stored master, what changed)``."""

    draft.sections = [section for section in draft.sections if section.entries or section.items]
    if not draft.sections:
        raise MasterEditError("master_would_be_empty", "the master cannot be left with no line; retire nothing more, or add a line first")
    assign_ids(draft, stored.master)
    markdown = build_master(draft).markdown()
    _kept, removed = strip_contact(markdown)
    if removed.lines:
        kinds = ", ".join(sorted(kind.replace("_", " ") for kind in removed.counts))
        raise MasterEditError(
            "personal_info_refused",
            f"the text looks like contact data ({kinds}); the master holds none: you type your name and contact details in "
            "Scout's Generate PDF form when you make the PDF, never in a resume line",
        )
    # Resolved: the import refuses a source below a redirected (symlinked) parent, and the system temp directory is one on macOS.
    directory = Path(tempfile.mkdtemp(prefix="gigai-master-edit-")).resolve()
    try:
        source = directory / MASTER_FILE_NAME
        source.write_text(markdown, encoding="utf-8")
        written = import_master(home_root=home_root, target=target, source=source, actor=actor, revision=stored.revision.revision)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return written.status, written.stored, written.change


def _change(
    home_root: Path, target: Path, *, revision: int | None, actor: str, apply: Callable[[StoredMaster, MasterDraft], Callable[[], str | None]],
) -> MasterWrite:
    """One change: ``apply`` edits a draft of the current master and returns how to read the changed id once ids are assigned."""

    from .master_profiles_cli import after_master_write

    stored = _current(home_root, target, revision, actor)
    draft = _draft(stored.master)
    changed_id = apply(stored, draft)
    status, now, change = _store(home_root, target, stored, draft, actor=actor)
    profiles = after_master_write(home_root, target) if status != "unchanged" else None
    return MasterWrite(status, now, change, changed_id(), profiles=profiles)


def _find_item(draft: MasterDraft, item_id: str) -> tuple[list[DraftItem], DraftItem]:
    """The list that holds line ``item_id`` (an entry's bullets, or a section's lines) and the line."""

    for section in draft.sections:
        for holder in (*(entry.bullets for entry in section.entries), section.items):
            for item in holder:
                if item.id == item_id:
                    return holder, item
    raise MasterEditError("master_line_not_found", f"the master has no line {item_id!r}")


def _find_entry(draft: MasterDraft, entry_id: str) -> tuple[DraftSection, DraftEntry]:
    for section in draft.sections:
        for entry in section.entries:
            if entry.id == entry_id:
                return section, entry
    raise MasterEditError("master_entry_not_found", f"the master has no entry {entry_id!r}")


def edit_line(
    *, home_root: Path, target: Path, item_id: str, revision: int | None, actor: str = "operator",
    text: object = None, tags: object = None, backed: object = None,
) -> MasterWrite:
    """Change a line's wording, tags and/or backing; its id stays."""

    if text is None and tags is None and backed is None:
        raise MasterEditError("invalid_value", "give at least one of text, tags, backed")

    def apply(stored: StoredMaster, draft: MasterDraft) -> Callable[[], str | None]:
        _holder, item = _find_item(draft, item_id)
        if text is not None:
            item.text = line_text(text, kind=stored.master.items[item_id].kind)
        if tags is not None:
            item.tags = tags_value(tags)
        if backed is not None:
            item.backed = backed_value(backed)
        return lambda: item_id

    return _change(home_root, target, revision=revision, actor=actor, apply=apply)


def _closest(master: Master, section: str, text: str) -> dict[str, object] | None:
    """The line of ``section`` that ``text`` reads like (the migration's own measure and threshold), or ``None``."""

    best: tuple[float, str] | None = None
    for item in master.in_section(section):
        score = near_duplicate(item.text, text)
        if score >= NEAR_DUPLICATE and (best is None or score > best[0]):
            best = (score, item.id)
    return None if best is None else {"id": best[1], "similarity": round(best[0], 2)}


def add_line(
    *, home_root: Path, target: Path, revision: int | None, actor: str = "operator", text: object,
    entry_id: str | None = None, section: str | None = None, tags: object = None, backed: object = None,
) -> MasterWrite:
    """Add a line: under ``entry_id`` (a role, a project, a school), or to ``section`` (summary, skills, other)."""

    if (entry_id is None) == (section is None):
        raise MasterEditError("invalid_value", "name where the line goes: entry_id (a role, project or school) or section (summary, skills or other)")
    name = section.strip().lower() if section is not None else None
    if name is not None and (name not in SECTION_HEADINGS or name in ENTRY_SECTIONS):
        raise MasterEditError("invalid_value", "section is summary, skills or other; a line of a role, project or school names its entry_id")
    near: list[dict[str, object] | None] = [None]

    def apply(stored: StoredMaster, draft: MasterDraft) -> Callable[[], str | None]:
        if entry_id is not None:
            holder_section, entry = _find_entry(draft, entry_id)
            where, holder = holder_section.name, entry.bullets
        else:
            where, holder = str(name), _section(draft, str(name)).items
        kind = KIND_SUMMARY if where == "summary" else KIND_SKILLS if where == "skills" else ""
        clean = line_text(text, kind=kind)
        if any(item.text == clean for item in holder):
            raise MasterEditError("master_line_exists", "that line is already there, word for word")
        near[0] = _closest(stored.master, where, clean)
        made = DraftItem(0, where, clean, None, tags_value(tags) if tags is not None else (), backed_value(backed) if backed is not None else ())
        holder.append(made)
        return lambda: made.id

    write = _change(home_root, target, revision=revision, actor=actor, apply=apply)
    return MasterWrite(write.status, write.stored, write.change, write.id, near[0], write.profiles)


def add_entry(
    *, home_root: Path, target: Path, revision: int | None, actor: str = "operator", section: object, heading: object, sublines: object = None,
) -> MasterWrite:
    """Add a role, a project or a school (no lines yet), first in its section: a resume lists the newest first."""

    name = section.strip().lower() if isinstance(section, str) else ""
    if name not in ENTRY_SECTIONS:
        raise MasterEditError("invalid_value", "section is experience, projects or education")

    def apply(_stored: StoredMaster, draft: MasterDraft) -> Callable[[], str | None]:
        title = _one_line(heading, "heading", MAX_HEADING_CHARS)
        lines = sublines_value(sublines) if sublines is not None else []
        holder = _section(draft, name)
        if any(entry.heading == title and entry.sublines == lines for entry in holder.entries):
            raise MasterEditError("master_entry_exists", "that entry is already there")
        made = DraftEntry(0, name, title, None, lines)
        holder.entries.insert(0, made)
        return lambda: made.id

    return _change(home_root, target, revision=revision, actor=actor, apply=apply)


def edit_entry(
    *, home_root: Path, target: Path, entry_id: str, revision: int | None, actor: str = "operator", heading: object = None, sublines: object = None,
) -> MasterWrite:
    """Change an entry's heading and/or the lines under it; its id and its bullets stay."""

    if heading is None and sublines is None:
        raise MasterEditError("invalid_value", "give at least one of heading, sublines")

    def apply(_stored: StoredMaster, draft: MasterDraft) -> Callable[[], str | None]:
        _holder, entry = _find_entry(draft, entry_id)
        if heading is not None:
            entry.heading = _one_line(heading, "heading", MAX_HEADING_CHARS)
        if sublines is not None:
            entry.sublines = sublines_value(sublines)
        return lambda: entry_id

    return _change(home_root, target, revision=revision, actor=actor, apply=apply)


def retire(*, home_root: Path, target: Path, item_id: str, revision: int | None, actor: str = "operator") -> MasterWrite:
    """Take a line, or an entry with its lines, out of the master's next revision (``restore`` puts it back)."""

    def apply(stored: StoredMaster, draft: MasterDraft) -> Callable[[], str | None]:
        if item_id in stored.master.entries:
            section, entry = _find_entry(draft, item_id)
            section.entries.remove(entry)
        else:
            holder, item = _find_item(draft, item_id)
            holder.remove(item)
        return lambda: item_id

    return _change(home_root, target, revision=revision, actor=actor, apply=apply)


# --- history: what is retired, and putting it back ---------------------------------------------

_KEPT_LOCK = threading.Lock()
#: (workpad path, revision id) -> that revision's master. A revision's content never changes.
_kept: dict[tuple[str, str], Master] = {}
_KEPT_MAX = 256


def revisions(home_root: Path, target: Path) -> list[tuple[MasterRevision, Master]]:
    """Every revision of the master with its parsed content, oldest first; each revision is read once per process."""

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


@dataclass(frozen=True)
class Retired:
    """A line or an entry the master held and no longer does: what it was, and the last revision that held it."""

    id: str
    #: ``line`` or ``entry``
    what: str
    last_revision: int
    retired_in: int
    item: MasterItem | None = None
    entry: MasterEntry | None = None
    #: The heading of the line's entry as that revision had it (``None`` for a line of a plain section).
    entry_heading: str | None = None

    def to_json(self) -> dict[str, object]:
        body = self.item.to_json() if self.item is not None else self.entry.to_json()  # type: ignore[union-attr]
        return {**body, "what": self.what, "last_revision": self.last_revision, "retired_in": self.retired_in, "entry_heading": self.entry_heading}


def retired(chain: Sequence[tuple[MasterRevision, Master]]) -> list[Retired]:
    """What earlier revisions held and the current one does not, the most recently retired first.

    A line retired with its entry is listed under the entry only (``restore``
    of the entry brings its lines back)."""

    if not chain:
        return []
    current = chain[-1][1]
    found: dict[str, Retired] = {}
    with_entry: set[str] = set()  # lines that left with their entry: listed under it
    for index in range(len(chain) - 2, -1, -1):  # newest first: the wording a line had when it was retired
        revision, master = chain[index]
        for entry in master.entries.values():
            if entry.id not in current.entries and entry.id not in found:
                found[entry.id] = Retired(entry.id, "entry", revision.revision, revision.revision + 1, entry=entry)
                with_entry.update(entry.bullets)
        for item in master.items.values():
            if item.id in current.items or item.id in found or item.id in with_entry:
                continue
            heading = master.entries[item.entry_id].heading if item.entry_id in master.entries else None
            found[item.id] = Retired(item.id, "line", revision.revision, revision.revision + 1, item=item, entry_heading=heading)
    return sorted(found.values(), key=lambda gone: (-gone.last_revision, gone.item.order if gone.item is not None else gone.entry.order))  # type: ignore[union-attr]


def _after(old_order: Sequence[str], item_id: str, present: Sequence[str | None]) -> int:
    """Where ``item_id`` goes back among ``present``: after the nearest line it stood below that is still there, else first."""

    before = old_order[: old_order.index(item_id)] if item_id in old_order else ()
    for neighbour in reversed(before):
        if neighbour in present:
            return present.index(neighbour) + 1
    return 0


def restore(*, home_root: Path, target: Path, item_id: str, revision: int | None, actor: str = "operator") -> MasterWrite:
    """Put a retired line or entry back, under its own id, as the last revision that held it had it."""

    def apply(stored: StoredMaster, draft: MasterDraft) -> Callable[[], str | None]:
        if item_id in stored.master.items or item_id in stored.master.entries:
            raise MasterEditError("master_line_not_retired", f"{item_id!r} is in the master: there is nothing to restore")
        chain = revisions(home_root, target)
        gone = next((item for item in retired(chain) if item.id == item_id), None)
        old = chain[gone.last_revision - 1][1] if gone is not None else None
        if gone is None or old is None:
            # A line that went with its entry is listed under the entry: look for it in any earlier revision.
            old = next((master for _revision, master in reversed(chain[:-1]) if item_id in master.items or item_id in master.entries), None)
            if old is None:
                raise MasterEditError("master_line_not_found", f"no revision of the master holds {item_id!r}")

        def bullet(item: MasterItem) -> DraftItem:
            return DraftItem(0, item.section, item.text, item.id, item.tags, item.backed)

        def entry_back(entry: MasterEntry, bullets: Sequence[str]) -> DraftEntry:
            section = _section(draft, entry.section)
            made = DraftEntry(0, entry.section, entry.heading, entry.id, list(entry.sublines), [bullet(old.items[item]) for item in bullets])
            order = [item.id for item in old.entries_in(entry.section)]
            section.entries.insert(_after(order, entry.id, [item.id for item in section.entries]), made)
            return made

        if item_id in old.entries:
            entry = old.entries[item_id]
            taken = {item.id for item in draft.all_items()}
            entry_back(entry, [item for item in entry.bullets if item not in taken])
            return lambda: item_id
        item = old.items[item_id]
        if item.entry_id is not None:
            holder_entry = next((entry for entry in draft.all_entries() if entry.id == item.entry_id), None)
            if holder_entry is None:  # its entry is retired too: the entry comes back with this one line
                entry_back(old.entries[item.entry_id], [item_id])
                return lambda: item_id
            holder, order = holder_entry.bullets, list(old.entries[item.entry_id].bullets)
        else:
            holder, order = _section(draft, item.section).items, [line.id for line in old.in_section(item.section)]
        holder.insert(_after(order, item_id, [line.id for line in holder]), bullet(item))
        return lambda: item_id

    return _change(home_root, target, revision=revision, actor=actor, apply=apply)


__all__ = [
    "MAX_HEADING_CHARS",
    "MAX_LINE_CHARS",
    "MAX_LONG_LINE_CHARS",
    "MasterEditError",
    "MasterWrite",
    "Retired",
    "add_entry",
    "add_line",
    "backed_value",
    "edit_entry",
    "edit_line",
    "line_text",
    "restore",
    "retire",
    "retired",
    "revisions",
    "sublines_value",
    "tags_value",
]
