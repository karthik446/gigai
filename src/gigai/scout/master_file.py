"""0.1.10.9 master P8: the visible master file -- ``master.md`` in the resumes folder -- and its explicit import.

The master resume is stored in the hidden home (``master_store``). After
every change of it the store writes it into the resumes folder
(``~/Documents/GigAI/resumes/master.md`` by default) so the user can read it
and edit it in their own editor. That file is the stored master's own
markdown, ids and all, and never holds contact data.

**Explicit sync** (the operator's decision 6). GigAI never reads the file by
itself. An edit made there becomes part of the master only when the user says
so: ``gigai scout resume master sync``, or **Import the file** on Scout's
Master page (``POST /api/master/sync``). Until then Scout says "master.md has
changes not imported yet" (``file_status``: the CLI's ``scout status`` and
``master show``, ``GET /api/master``'s ``file``, the page's line).

**What an import does** (``sync``). The file is stored as the master's next
revision with the rules of ``master init --from FILE``: a line without an id
gets one, a line whose id comment was deleted gets its id back when its text
is unchanged, a line the file no longer holds is retired (the revision before
still holds it; it can be restored). A file that does not parse changes
nothing and the error names the line. Two things are stricter than ``init
--from``:

* **contact data is refused**, not dropped: a name line, an email, a phone
  number, a link or an address in the file refuses the whole import by line
  number and kind, so nothing leaves the file silently;
* **the revision check**: the file is imported on top of the revision GigAI
  wrote it from. When the master changed in Scout (or through the agent)
  since, the import is refused with ``revision_conflict`` and the current
  revision: importing would retire what was added since. ``revision`` (the
  revision the user has read) says to do it anyway. A ``master.md`` GigAI
  never wrote has no known revision: ``revision_required``.

After an import GigAI writes the file again (now with every id) only when its
bytes are still the ones it imported; the import ends with
``master_profiles_cli.after_master_write`` as every write of the master does.

**Never the user's file.** While ``master.md`` holds changes that are not
imported, a change of the master in Scout leaves it alone and writes the new
revision beside it (``master-2.md``; ``resumes_folder.save_master``).

All local: no model, no network. Nothing here logs or returns a line of the
file in an error.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import resumes_folder
from .master_resume import Master, MasterChange
from .master_store import ACTORS, MasterStoreError, StoredMaster, import_master, load_master, write_file

MASTER_FILE_SCHEMA = "scout-master-file:1"
SYNC_COMMAND = "gigai scout resume master sync"

#: What ``sync`` did: a new revision from the file; the file says what is stored; the file was missing or behind and was written.
SYNC_IMPORTED = "imported"
SYNC_UNCHANGED = "unchanged"
SYNC_WRITTEN = "written"

#: What ``sync`` (or the page's button) would do now: import the file's changes, or write the file.
ACTION_IMPORT = "import"
ACTION_WRITE = "write"

_NO_MASTER = (
    "there is no master resume yet; make one from your profiles' resumes with `gigai scout resume master init`, "
    "or from a file with `gigai scout resume master init --from FILE`"
)


def file_status(home_root: Path, stored: StoredMaster | None) -> dict[str, object]:
    """How ``master.md`` stands against the stored master. Reads the folder's index and the one file; writes nothing.

    ``state``: ``missing``, ``current`` (exactly what GigAI last wrote there) or ``changed``. ``not_imported``: the
    file has changes that are not in the master. ``revision``: the revision GigAI last wrote into the file.
    ``behind``: the master has moved on since that revision. ``beside``: GigAI's own file that holds the newer
    revision meanwhile. ``action``: what a sync would do now (``import``, ``write``) or ``None``."""

    folder = resumes_folder.resumes_folder(home_root)
    file = resumes_folder.master_file(home_root)
    behind = stored is not None and file.revision_id is not None and file.revision_id != stored.revision.revision_id
    if file.state == resumes_folder.MASTER_CHANGED:
        action: str | None = ACTION_IMPORT
    elif stored is not None and (file.state == resumes_folder.MASTER_MISSING or behind):
        action = ACTION_WRITE
    else:
        action = None
    return {
        "schema_version": MASTER_FILE_SCHEMA, **file.to_json(), "folder": folder.shown, "behind": behind,
        "master_revision": stored.revision.revision if stored is not None else None, "action": action,
    }


def status_line(status: Mapping[str, object]) -> str | None:
    """What the CLI says about the file in one sentence; ``None`` when the file is there and says what is stored."""

    name, path = status["name"], status["path"]
    if status["not_imported"]:
        moved = ""
        if status["behind"]:
            where = f"; revision {status['master_revision']} is in {status['beside']}" if status["beside"] else ""
            moved = f" The master changed since that file was written (revision {status['revision']}, now {status['master_revision']}{where})."
        return f"{name} has changes not imported yet ({path}).{moved} Import them: `{SYNC_COMMAND}`."
    if status["action"] == ACTION_WRITE:
        what = "is not in your resumes folder" if status["state"] == resumes_folder.MASTER_MISSING else f"holds revision {status['revision']}, not {status['master_revision']}"
        return f"{name} {what} ({path}). Write it: `{SYNC_COMMAND}`."
    return None


def write_line(file: Mapping[str, object] | None) -> str | None:
    """What a write of the master says about the file it wrote, when that needs saying (``master_store.write_file``)."""

    if file is None:
        return None
    if file.get("error"):
        return (
            f"The master resume could not be written into your resumes folder ({file['error']}); it is stored. "
            f"`{SYNC_COMMAND}` writes the file."
        )
    if file.get("not_imported"):
        return (
            f"{file['name']} in your resumes folder has changes not imported yet, so it was left as it is: this revision is in "
            f"{file['wrote']} beside it. Import your changes: `{SYNC_COMMAND}`."
        )
    return None


@dataclass(frozen=True)
class SyncLine:
    """One line or entry an import touched: its id, what it is and its text (an entry: its heading)."""

    id: str
    what: str
    section: str
    entry_id: str | None
    text: str

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "what": self.what, "section": self.section, "entry_id": self.entry_id, "text": self.text}


@dataclass(frozen=True)
class MasterSync:
    """What ``sync`` did."""

    #: ``imported``, ``unchanged`` or ``written``.
    status: str
    #: The master after the sync.
    stored: StoredMaster
    #: Where the file stands now (``master_store.write_file``), or as it was when nothing was written.
    file: Mapping[str, object]
    change: MasterChange = MasterChange()
    added: tuple[SyncLine, ...] = ()
    changed: tuple[SyncLine, ...] = ()
    #: What the file no longer holds: retired, as the revision before had it.
    retired: tuple[SyncLine, ...] = ()
    ids_kept: int = 0
    ids_assigned: int = 0
    ids_restored: int = 0
    #: What ``after_master_write`` did; ``None`` when no revision was written.
    profiles: Mapping[str, object] | None = None

    @property
    def written(self) -> bool:
        """A revision was written."""

        return self.status == SYNC_IMPORTED

    def to_json(self) -> dict[str, object]:
        return {
            "action": "sync", "status": self.status, "written": self.written, "changes": self.change.to_json(),
            "added": [line.to_json() for line in self.added], "changed": [line.to_json() for line in self.changed],
            "retired": [line.to_json() for line in self.retired],
            "ids": {"kept": self.ids_kept, "assigned": self.ids_assigned, "restored": self.ids_restored},
            "file": dict(self.file),
            "profiles": dict(self.profiles) if self.profiles is not None else {"synced": [], "offers": []},
        }


def _facts(master: Master) -> dict[str, tuple[object, ...]]:
    """What ``master_resume.compare`` compares, by id."""

    out: dict[str, tuple[object, ...]] = {entry.id: ("entry", entry.section, entry.heading, entry.sublines) for entry in master.entries.values()}
    out.update({item.id: ("item", item.section, item.entry_id, item.text, item.tags, item.backed) for item in master.items.values()})
    return out


def _line(master: Master, item_id: str) -> SyncLine:
    if item_id in master.entries:
        entry = master.entries[item_id]
        return SyncLine(item_id, "entry", entry.section, None, entry.heading)
    item = master.items[item_id]
    return SyncLine(item_id, "line", item.section, item.entry_id, item.text)


def _status_of(home_root: Path, stored: StoredMaster) -> dict[str, object]:
    status = file_status(home_root, stored)
    return {key: status[key] for key in ("name", "path", "state", "not_imported", "revision", "beside")} | {"written": False, "wrote": None}


def sync(*, home_root: Path, target: Path, actor: str = "operator", revision: int | None = None) -> MasterSync:
    """Import the resumes folder's ``master.md`` as the master's next revision (the module docstring has the rules).

    Raises ``MasterStoreError`` (``master_not_found``, ``revision_required``, ``revision_conflict`` with
    ``current``, ``master_contact_data``, ``master_file_unreadable``, ``master_file_changed``) and
    ``MasterResumeError`` for a file that does not read as a master (by line number)."""

    from .master_profiles_cli import after_master_write

    if actor not in ACTORS:
        raise MasterStoreError("master_actor_invalid", "the writer is operator or agent")
    stored = load_master(home_root=home_root, target=target)
    if stored is None:
        raise MasterStoreError("master_not_found", _NO_MASTER)
    file = resumes_folder.master_file(home_root)
    number = stored.revision.revision
    if file.state == resumes_folder.MASTER_MISSING:
        return MasterSync(SYNC_WRITTEN, stored, write_file(home_root, stored))
    if file.sha256 is None:
        raise MasterStoreError(
            "master_file_unreadable",
            f"{file.name} in your resumes folder is not a plain file (a link or a folder); GigAI reads and writes only a plain file there",
        )
    if file.state == resumes_folder.MASTER_CURRENT:
        if file.revision_id == stored.revision.revision_id:
            return MasterSync(SYNC_UNCHANGED, stored, _status_of(home_root, stored))
        # GigAI's own file, untouched, from an earlier revision: a write could not reach the folder then.
        return MasterSync(SYNC_WRITTEN, stored, write_file(home_root, stored))
    if revision is None:
        anyway = f"To import it as it is, pass --revision {number}: what the file does not hold is retired (and can be restored)."
        if file.revision_id is None:
            raise MasterStoreError(
                "revision_required",
                f"GigAI did not write this {file.name}, so it is not known which revision of the master it was written from. {anyway}",
                current=stored.revision,
            )
        if file.revision_id != stored.revision.revision_id:
            beside = f" The master as it is now is in {file.beside.name}." if file.beside is not None else ""
            raise MasterStoreError(
                "revision_conflict",
                f"{file.name} was written from revision {file.revision} and the master is at revision {number} now: it was changed in Scout "
                f"or by your agent since, and importing the file would retire what was added.{beside} {anyway}",
                current=stored.revision,
            )
        revision = number
    written = import_master(
        home_root=home_root, target=target, source=file.path, actor=actor, revision=revision, refuse_contact=True, visible_sha256=file.sha256,
    )
    before, after = _facts(stored.master), _facts(written.stored.master)
    wrote = written.status in ("created", "revised")
    return MasterSync(
        SYNC_IMPORTED if wrote else SYNC_UNCHANGED, written.stored,
        written.file if written.file is not None else _status_of(home_root, written.stored), written.change,
        added=tuple(_line(written.stored.master, item_id) for item_id in after if item_id not in before),
        changed=tuple(_line(written.stored.master, item_id) for item_id in after if item_id in before and before[item_id] != after[item_id]),
        retired=tuple(_line(stored.master, item_id) for item_id in before if item_id not in after),
        ids_kept=sum(item_id in before for item_id in after), ids_assigned=written.ids_assigned, ids_restored=written.ids_restored,
        # A profile that shows an edited or retired line gets its resume printed again; new lines are only offered.
        profiles=after_master_write(home_root, target) if wrote else None,
    )


__all__ = [
    "ACTION_IMPORT",
    "ACTION_WRITE",
    "MASTER_FILE_SCHEMA",
    "MasterSync",
    "SYNC_COMMAND",
    "SYNC_IMPORTED",
    "SYNC_UNCHANGED",
    "SYNC_WRITTEN",
    "SyncLine",
    "file_status",
    "status_line",
    "sync",
    "write_line",
]
