"""The master resume's store (0.1.10.9 master P1): one journal record, a revision per change.

The master (``master_resume``: resume markdown with an id on every line) is
kept as ONE private record whose id is derived from the project and the gig
(``master_record_id``), so there is exactly one per Scout home and it is
found without a search. Every change is a new revision of that record
(``private_records.create_record(record_id=..., parent_revision=...)``):
the content of a revision is a sealed reference holding the canonical
master markdown, and the chain is the history. Nothing is ever rewritten.

The reference's kind is ``role_history``, not ``resume``: every existing
resume reader (the newest-resume resolution, the profile migration, the
contact cleanup) lists the references of kind ``resume``, and the master
must not become "the newest resume" of any of them. P1 changes no reader.

``import_master`` is the one write path:

1. the file is read (``.md``/``.markdown``/``.txt``, UTF-8, at most 1 MiB);
2. the privacy strip runs, with the resume import's own detector
   (``resume_pii.contact_findings``: the name line, contact lines, and any
   line holding an email, a phone number, a link, an address). A flagged
   line is not imported; it is reported by kind and line number, never by
   value, and never stored;
3. the text is read as a master and every line without an id gets one (a
   line whose id comment was deleted gets its id back when the previous
   revision has the same text);
4. the canonical markdown becomes a new revision. A write names the
   revision it read (``revision``): a master that moved on since is refused
   with ``revision_conflict``, and replacing an existing master without
   naming its revision with ``master_exists``. The same content again
   writes nothing.

A file that does not parse leaves the last good revision in place: nothing
is written before the whole file is accepted. Nothing here logs, prints or
returns a line of the master in an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import tempfile

from ..canonical import EntityPrefix, derive_deterministic_id, digest_imported_bytes
from ..private_records import PrivateRecordError, create_record, import_reference, list_revisions, read_record
from ..workpad import ResolvedWorkpad, WorkpadError, resolve_workpad
from .master_resume import (
    MASTER_MAX_BYTES,
    Master,
    MasterChange,
    MasterResumeError,
    assign_ids,
    build_master,
    compare,
    draft_master,
    parse_master,
)
from .resume_import import RESUME_MEDIA_TYPE_MESSAGE, RESUME_SUFFIXES
from .resume_pii import REMOVED_MESSAGE, contact_findings

#: The reference kind the master's content is sealed under (see the module docstring: not ``resume``).
MASTER_REFERENCE_KIND = "role_history"
#: The stored reference's label and the name of the file it is imported from.
MASTER_FILE_NAME = "master.md"
ACTORS: tuple[str, ...] = ("operator", "agent")
_ACTOR_IDS = {"operator": "local-user", "agent": "local-agent"}
#: How often the detector runs again after its findings were blanked (a line can only be flagged once the one above it went).
_STRIP_PASSES = 4


class MasterStoreError(ValueError):
    """A master that cannot be read or written; ``code`` is stable, the message is for a person.

    ``current`` is set for ``revision_conflict`` and ``master_exists``: the revision the master is at now."""

    def __init__(self, code: str, message: str, *, current: "MasterRevision | None" = None) -> None:
        super().__init__(message)
        self.code = code
        self.current = current


@dataclass(frozen=True)
class MasterRevision:
    """One revision of the master: its number (1 is the first), who wrote it and when."""

    revision: int
    revision_id: str
    parent_revision: str | None
    written_by: str
    updated_at: str
    content_sha256: str

    def to_json(self) -> dict[str, object]:
        return {
            "revision": self.revision, "revision_id": self.revision_id, "parent_revision": self.parent_revision,
            "written_by": self.written_by, "updated_at": self.updated_at, "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class StoredMaster:
    record_id: str
    revision: MasterRevision
    #: How many revisions the record has; ``revision.revision`` equals it for the current one.
    revisions: int
    master: Master


@dataclass(frozen=True)
class ContactRemoved:
    """What the import's privacy strip took out: kinds and file line numbers, never a value."""

    lines: tuple[tuple[str, int], ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for kind, _line in self.lines:
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def to_json(self) -> dict[str, object] | None:
        if not self.lines:
            return None
        return {"removed": self.counts, "lines": [{"kind": kind, "line": line} for kind, line in self.lines], "message": REMOVED_MESSAGE}


@dataclass(frozen=True)
class MasterImport:
    #: ``created`` (the first revision), ``revised`` (a new revision) or ``unchanged`` (nothing written).
    status: str
    stored: StoredMaster
    change: MasterChange
    ids_assigned: int
    ids_restored: int
    contact_removed: ContactRemoved = field(default_factory=ContactRemoved)


def master_record_id(resolved: ResolvedWorkpad) -> str:
    """The master's record id: derived from the project and the gig, so a home has one master and it is found without a search."""

    return derive_deterministic_id(
        EntityPrefix.RECORD.value, {"family": "scout-master-resume", "project_id": resolved.project_id, "gig_id": resolved.gig_id}
    )


def _resolve(home_root: Path, target: Path | None, gig_id: str | None) -> ResolvedWorkpad:
    return resolve_workpad(home_root=home_root, requested_target=target, gig_id=gig_id, allow_semantic_state=True)


def _resolve_for_read(home_root: Path, target: Path | None, gig_id: str | None) -> ResolvedWorkpad | None:
    """The workpad to read from; ``None`` where Scout is not installed yet (so there is no master either)."""

    try:
        return _resolve(home_root, target, gig_id)
    except WorkpadError as exc:
        if exc.code == "no_active_gig" and gig_id is None:
            return None
        raise


def _revision(number: int, raw: dict[str, object]) -> MasterRevision:
    content = raw.get("content")
    snapshot = content.get("snapshot_ref") if isinstance(content, dict) else None
    actor = raw.get("actor")
    return MasterRevision(
        revision=number,
        revision_id=str(raw["revision_id"]),
        parent_revision=raw["parent_revision"] if isinstance(raw.get("parent_revision"), str) else None,  # type: ignore[arg-type]
        written_by=str(actor.get("kind", "")) if isinstance(actor, dict) else "",
        updated_at=str(raw.get("created_at", "")),
        content_sha256=str(snapshot.get("content_sha256", "")) if isinstance(snapshot, dict) else "",
    )


def _chain(resolved: ResolvedWorkpad) -> list[MasterRevision]:
    return [_revision(number, raw) for number, raw in enumerate(list_revisions(resolved=resolved, record_id=master_record_id(resolved)), 1)]


def _read_master(home_root: Path, target: Path | None, resolved: ResolvedWorkpad, revision: MasterRevision) -> Master:
    """One revision's stored markdown, parsed. The text is contact-free (stripped at import) and stays local."""

    stored = read_record(
        home_root=home_root, requested_target=target, record_id=master_record_id(resolved),
        revision_id=revision.revision_id, content=True, gig_id=resolved.gig_id,
    )
    data = stored.get("content")
    if not isinstance(data, bytes):
        raise MasterStoreError("master_unreadable", f"revision {revision.revision} of the master holds no content")
    try:
        return parse_master(data.decode("utf-8"))
    except (UnicodeDecodeError, MasterResumeError) as exc:
        raise MasterStoreError("master_unreadable", f"revision {revision.revision} of the master does not read as a master") from exc


def load_master(*, home_root: Path, target: Path | None, gig_id: str | None = None, revision: int | None = None) -> StoredMaster | None:
    """The stored master (the current revision, or ``revision``), or ``None`` when there is none yet."""

    resolved = _resolve_for_read(home_root, target, gig_id)
    chain = _chain(resolved) if resolved is not None else []
    if resolved is None or not chain:
        return None
    if revision is not None and not 1 <= revision <= len(chain):
        raise MasterStoreError("master_revision_not_found", f"the master has no revision {revision} (it is at revision {len(chain)})", current=chain[-1])
    chosen = chain[-1] if revision is None else chain[revision - 1]
    return StoredMaster(master_record_id(resolved), chosen, len(chain), _read_master(home_root, target, resolved, chosen))


@dataclass(frozen=True)
class HistoryEntry:
    revision: MasterRevision
    items: int
    entries: int
    change: MasterChange

    def to_json(self) -> dict[str, object]:
        return {**self.revision.to_json(), "items": self.items, "entries": self.entries, **self.change.to_json()}


def master_history(*, home_root: Path, target: Path | None, gig_id: str | None = None) -> list[HistoryEntry]:
    """Every revision, oldest first, with what it changed against the one before (ids added, removed, changed)."""

    resolved = _resolve_for_read(home_root, target, gig_id)
    if resolved is None:
        return []
    history: list[HistoryEntry] = []
    previous: Master | None = None
    for revision in _chain(resolved):
        master = _read_master(home_root, target, resolved, revision)
        history.append(HistoryEntry(revision, len(master.items), len(master.entries), compare(previous, master)))
        previous = master
    return history


# --- the import ---------------------------------------------------------------------------------


def _read_source(source: Path) -> str:
    if source.suffix.lower() not in RESUME_SUFFIXES:
        raise MasterStoreError("resume_media_type_unsupported", RESUME_MEDIA_TYPE_MESSAGE)
    try:
        data = source.read_bytes()
    except FileNotFoundError:
        raise MasterStoreError("master_file_missing", f"{source} does not exist") from None
    except OSError as exc:
        raise MasterStoreError("master_file_unreadable", f"{source} cannot be read ({exc.strerror or type(exc).__name__})") from None
    if len(data) > MASTER_MAX_BYTES:
        raise MasterStoreError("master_too_large", f"{source} is larger than {MASTER_MAX_BYTES} bytes")
    if b"\0" in data:
        raise MasterStoreError("master_file_binary", f"{source} is not a text file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise MasterStoreError("master_file_binary", f"{source} is not UTF-8 text") from None


def strip_contact(text: str) -> tuple[str, ContactRemoved]:
    """``text`` with every line that looks like contact data blanked, and what went (kinds and line numbers).

    The detector is the resume import's own (``resume_pii.contact_findings``: what its strip removes, plus
    the heads-up shapes). A flagged line is not imported at all: blanking it keeps the file's line numbers,
    so a later format error still names the line the person sees. A leading ``<!-- gigai-master:1 -->``
    line is set aside while the detector runs, so a name line right under it is the first line it sees, as
    in a resume."""

    lines = text.splitlines()
    marker: tuple[int, str] | None = None
    for index, line in enumerate(lines):
        if line.strip():
            if line.strip().startswith("<!--") and line.strip().endswith("-->") and "gigai-master:" in line:
                marker, lines[index] = (index, line), ""
            break
    removed: set[tuple[str, int]] = set()
    for _ in range(_STRIP_PASSES):
        findings = contact_findings("\n".join(lines))
        if not findings:
            break
        for finding in findings:
            removed.add((finding.kind, finding.line))
            lines[finding.line - 1] = ""
    else:
        left = contact_findings("\n".join(lines))
        if left:
            raise MasterStoreError(
                "master_contact_data",
                "the file still holds what looks like contact data on line " + ", ".join(str(finding.line) for finding in left) + "; remove it and import again",
            )
    if not removed:
        return text, ContactRemoved()
    if marker is not None:
        lines[marker[0]] = marker[1]
    return "\n".join(lines) + "\n", ContactRemoved(tuple(sorted(removed, key=lambda item: (item[1], item[0]))))


def _still_contact(master: Master) -> None:
    """Refuse a master whose canonical text the detector flags (a wrapped line that reads as contact data once joined)."""

    kinds = sorted({finding.kind for finding in contact_findings(master.markdown(ids=False))})
    if kinds:
        raise MasterStoreError(
            "master_contact_data",
            "the master would still hold what looks like contact data (" + ", ".join(kind.replace("_", " ") for kind in kinds) + "); remove it from the file and import again",
        )


def import_master(
    *,
    home_root: Path,
    target: Path | None,
    source: Path,
    actor: str = "operator",
    revision: int | None = None,
    gig_id: str | None = None,
) -> MasterImport:
    """Store ``source`` as the master: the first revision, or a new one on top of ``revision``."""

    if actor not in ACTORS:
        raise MasterStoreError("master_actor_invalid", "the writer is operator or agent")
    text, removed = strip_contact(_read_source(source))
    resolved = _resolve(home_root, target, gig_id)
    chain = _chain(resolved)
    current = chain[-1] if chain else None
    previous = _read_master(home_root, target, resolved, current) if current is not None else None

    draft = draft_master(text)
    assignment = assign_ids(draft, previous)
    master = build_master(draft)
    _still_contact(master)
    markdown = master.markdown()
    encoded = markdown.encode("utf-8")
    if len(encoded) > MASTER_MAX_BYTES:
        raise MasterResumeError("master_too_large", f"the master is larger than {MASTER_MAX_BYTES} bytes")
    change = compare(previous, master)
    record_id = master_record_id(resolved)
    if current is not None and previous is not None and previous.markdown() == markdown:
        return MasterImport("unchanged", StoredMaster(record_id, current, len(chain), previous), change, 0, assignment.restored, removed)
    if current is None and revision not in (None, 0):
        raise MasterStoreError("revision_conflict", f"there is no master yet, so there is no revision {revision}")
    if current is not None and revision is None:
        raise MasterStoreError(
            "master_exists",
            f"a master exists at revision {current.revision}; pass --revision {current.revision} to store this file as its next revision",
            current=current,
        )
    if current is not None and revision != current.revision:
        raise MasterStoreError(
            "revision_conflict",
            f"the master is at revision {current.revision}, not {revision}: read it again (master show), then write on top of revision {current.revision}",
            current=current,
        )

    content_hex = digest_imported_bytes(encoded)[len("sha256:"):]
    # Resolved: the import refuses a source below a redirected (symlinked) parent, and the
    # system temp directory is one on macOS (/var).
    directory = Path(tempfile.mkdtemp(prefix="gigai-master-")).resolve()
    try:
        clean = directory / MASTER_FILE_NAME
        clean.write_bytes(encoded)
        reference = import_reference(
            home_root=home_root, requested_target=target, gig_id=resolved.gig_id, kind=MASTER_REFERENCE_KIND,
            source=clean, label=MASTER_FILE_NAME, operation_key=f"scout-master-resume:content:{content_hex}",
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    parent = current.revision_id if current is not None else None
    try:
        written = create_record(
            home_root=home_root, requested_target=target, gig_id=resolved.gig_id,
            kind="imported_reference", content_family="g45_reference", content_id=reference.item_id,
            actor={"kind": actor, "id": _ACTOR_IDS[actor]}, origin="imported",
            operation_key=f"scout-master-resume:{parent or 'first'}:{content_hex}",
            record_id=record_id, parent_revision=parent,
        )
    except PrivateRecordError as exc:
        if exc.code != "stale_parent":
            raise
        # Another writer added a revision between the read above and this write.
        moved = _chain(resolved)
        raise MasterStoreError(
            "revision_conflict", "the master changed while this file was being stored: read it again (master show), then retry",
            current=moved[-1] if moved else None,
        ) from exc
    stored = StoredMaster(record_id, _revision(len(chain) + 1, written.revision), len(chain) + 1, master)
    return MasterImport("created" if current is None else "revised", stored, change, assignment.assigned, assignment.restored, removed)


__all__ = [
    "ACTORS",
    "ContactRemoved",
    "HistoryEntry",
    "MASTER_FILE_NAME",
    "MASTER_REFERENCE_KIND",
    "MasterImport",
    "MasterRevision",
    "MasterStoreError",
    "StoredMaster",
    "import_master",
    "load_master",
    "master_history",
    "master_record_id",
    "strip_contact",
]
