"""0110-036: the committed application events of a Gig, kept at the journal head.

The journal is the authority and stays it: one commit publishes one
application event, and nothing here is ever read instead of the journal's
answer. What this module keeps is what a read of the events family proved:
every committed ``records/applications/events/<event>.json`` (each one passed
the journal's publication checks and ``application_events._validate_event``)
at one journal head, filed under the blob id git names its bytes by.

Why: a snapshot of the family walks every commit that published into it and
validates every event against its schema, so "Mark applied" and the first
applications read got slower with every application (2.7 s a write at 300
events). Kept at a head, the family is brought to a newer head by the commits
in between alone (``journal.read_committed_additions``: two git subprocesses
whatever the journal holds), and an event is validated once.

The rules, in the order a read applies them:

* **The head decides.** A read takes the journal head and answers at that
  head. The kept events are used as they are only when they were kept at
  that same head.
* **A head that moved is caught up, or read again.** When the head descends
  in a straight line from the kept one and the commits in between only added
  events (each new path published once, never a path that had a publisher
  before), those events are proven and validated and added. Anything else (a
  reset or rewritten history, an event removed or published twice, a file in
  the working tree that is not committed, an event that fails validation)
  takes a full snapshot of the family, exactly as before; a refusal comes
  from there, with the error it always had.
* **A full read validates an event once.** An event whose path and blob id
  were validated before (in this process, or in the file below) is not
  validated against its schema again.
* **The file is a cache.** ``scratch/application-events-index.sqlite`` in the
  workpad (ignored by git, never transferred) holds the same thing between
  two server starts: for each event its path, its blob id, its bytes, and the
  size, change time and identity its working file had when it was proven. It
  is used the way git uses its own index, and only when all of this holds:
  its schema, its validator digest and its Gig are the ones of this process;
  the paths and blob ids it holds are exactly the tree of its own head (one
  ``git ls-tree``); every working file still has the size, change time and
  identity it was proven with; and every event's bytes still have the digest
  they were stored with. It is then caught up like the kept events. Deleted,
  unreadable, written by another version, or failing any of those, it is
  built again from the journal. A failure to write it changes nothing but
  the next start. It is a cache, not a defence: someone who can write the
  workpad can write this file.

Every blob id here is git's own answer (``ls-tree``, ``cat-file``).

Readers take no journal writer lock on this path. A writer passes its
:class:`~gigai.journal.JournalWriter`: the same steps then run under the lock
it holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from types import MappingProxyType
from typing import Any

from .application_events import ApplicationEventError, _validate_event
from .canonical import digest_imported_bytes, parse_json_bytes
from .journal import (
    JournalError,
    JournalSnapshot,
    JournalWriter,
    committed_blob_ids,
    committed_head,
    read_committed_additions,
    read_committed_family,
)
from .workpad import committed_read_cache

FAMILY_PREFIX = "records/applications/"
EVENTS_PREFIX = "records/applications/events/"
INDEX_DIRECTORY = "scratch"
INDEX_FILENAME = "application-events-index.sqlite"
INDEX_SCHEMA = "application-events-index:1"
_EVENT_SCHEMA_NAME = "application-event.schema.json"
_EVENT_PATH = re.compile(r"\Arecords/applications/events/[^/\n]+\.json\Z")
_SQLITE_TIMEOUT_SECONDS = 5.0
_ARTIFACTS_TABLE = (
    "CREATE TABLE IF NOT EXISTS artifacts (path TEXT PRIMARY KEY, blob_id TEXT NOT NULL, data BLOB NOT NULL, "
    "digest TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, inode INTEGER NOT NULL)"
)
_ARTIFACT_INSERT = "INSERT INTO artifacts(path, blob_id, data, digest, size, mtime_ns, inode) VALUES (?, ?, ?, ?, ?, ?, ?)"


class ApplicationEventIndexUnavailable(RuntimeError):
    """The kept events cannot answer; the caller reads the journal the way it did before."""


@dataclass(frozen=True)
class CommittedApplicationEvents:
    """Every committed application event at ``head``: ``path -> event``, in path order.

    The events are shared by every caller: read them, never change them.
    ``token`` is the same object for as long as the events are the same.
    """

    head: str
    events: Mapping[str, dict[str, Any]]
    token: object


@dataclass(frozen=True)
class _State:
    head: str
    events: dict[str, dict[str, Any]]  # in path order
    blob_ids: dict[str, str]
    ghosts: frozenset[str]  # paths of the family with a publisher and no file at the head
    token: object


_LOCK = threading.Lock()
_states: dict[tuple[str, str, str], _State] = {}
_key_locks: dict[tuple[object, ...], threading.Lock] = {}
# What the index file held when it could not be used as it was: path -> blob id. Still "validated once".
_validated: dict[tuple[str, str, str], dict[str, str]] = {}
_loaded: set[tuple[str, str, str]] = set()
# The head at which the family could not be kept (something in it is not a valid event).
_refused: dict[tuple[str, str, str], str] = {}


def _key_lock(*key: object) -> threading.Lock:
    with _LOCK:
        return _key_locks.setdefault(key, threading.Lock())


def forget_kept_events() -> None:
    """As a process that just started: nothing kept in memory (the files stay)."""

    with _LOCK:
        _states.clear()
        _validated.clear()
        _loaded.clear()
        _refused.clear()


_validator_digest_kept: tuple[tuple[int, int, int], str] | None = None


def _validator_digest() -> str:
    """What a kept "this event is valid" depends on beyond the event: the packaged event schema."""

    global _validator_digest_kept
    source = resources.files("gigai.schemas").joinpath(_EVENT_SCHEMA_NAME)
    try:
        found = os.stat(os.fspath(source))  # type: ignore[call-overload]
        signature = (found.st_ino, found.st_size, found.st_mtime_ns)
    except (TypeError, OSError):
        signature = None
    kept = _validator_digest_kept
    if signature is not None and kept is not None and kept[0] == signature:
        return kept[1]
    value = digest_imported_bytes(source.read_bytes())
    if signature is not None:
        _validator_digest_kept = (signature, value)
    return value


class _LockFree:
    """The family, read without the journal writer lock."""

    def __init__(self, resolved: Any) -> None:
        self._where = dict(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id)

    def head(self) -> str | None:
        return committed_head(**self._where)

    def family(self) -> tuple[JournalSnapshot, frozenset[str]]:
        return read_committed_family(prefix=FAMILY_PREFIX, **self._where)

    def additions(self, old_head: str, new_head: str, known: frozenset[str]) -> dict[str, tuple[str, bytes]] | None:
        return read_committed_additions(old_head=old_head, new_head=new_head, prefix=FAMILY_PREFIX, known=known, **self._where)

    def blob_ids(self, head: str) -> dict[str, str] | None:
        return committed_blob_ids(head=head, prefix=FAMILY_PREFIX, **self._where)


class _Locked:
    """The family, read by the writer that holds the lock."""

    def __init__(self, writer: JournalWriter) -> None:
        self._writer = writer

    def head(self) -> str | None:
        return self._writer.head()

    def family(self) -> tuple[JournalSnapshot, frozenset[str]]:
        return self._writer.family(FAMILY_PREFIX)

    def additions(self, old_head: str, new_head: str, known: frozenset[str]) -> dict[str, tuple[str, bytes]] | None:
        del new_head  # the head cannot move under the lock
        return self._writer.additions(old_head, FAMILY_PREFIX, known)

    def blob_ids(self, head: str) -> dict[str, str] | None:
        return self._writer.blob_ids(head, FAMILY_PREFIX)


def committed_application_events(resolved: Any, *, writer: JournalWriter | None = None) -> CommittedApplicationEvents:
    """Every committed application event of ``resolved``'s Gig, at the journal head now.

    Raises :class:`ApplicationEventIndexUnavailable` when the family holds
    anything but valid ``events/<event>.json`` files; a journal refusal is
    raised as it is. ``writer``: the caller holds the journal writer lock.
    """

    key = (os.fspath(resolved.path), resolved.project_id, resolved.gig_id)
    root = Path(resolved.path)
    if writer is not None:
        state = _at_head(key, root, _Locked(writer)) or _build(key, root, _Locked(writer))
    else:
        reader = _LockFree(resolved)
        # This is a read: its workpad checks are the kept ones while the workpad is unchanged.
        with committed_read_cache():
            state = _at_head(key, root, reader)
            if state is None:
                # One full read for the readers that arrive together. It may wait
                # for the writer lock, so no lock a writer waits for is held here.
                with _key_lock("build", *key):
                    state = _at_head(key, root, reader) or _build(key, root, reader)
    return CommittedApplicationEvents(state.head, MappingProxyType(state.events), state.token)


def _at_head(key: tuple[str, str, str], root: Path, reader: Any) -> _State | None:
    """The kept events brought to the head now, or ``None`` when only a full read can tell."""

    with _key_lock("state", *key):
        head = reader.head()
        if head is None:
            raise ApplicationEventIndexUnavailable("the journal has no head")
        with _LOCK:
            state = _states.get(key)
            first = key not in _loaded
            _loaded.add(key)
            refused = _refused.get(key)
        if refused == head:
            raise ApplicationEventIndexUnavailable("the applications family at this head cannot be kept")
        from_file = state is None and first
        if from_file:
            state = _load(key, root, reader)
        if state is None:
            return None
        if state.head != head:
            state = _advance(key, root, reader, state, head)
            if state is None:
                return None
        elif from_file and not _working_files_are(root, state.events):
            return None  # a working file nobody committed: the snapshot refuses it
        with _LOCK:
            _states[key] = state
        return state


def _checked_event(path: str, data: bytes, key: tuple[str, str, str]) -> dict[str, Any]:
    if _EVENT_PATH.fullmatch(path) is None:
        raise ApplicationEventIndexUnavailable("the applications family holds something that is not an event")
    try:
        return _validate_event(parse_json_bytes(data), path, key[1], key[2])
    except (ApplicationEventError, ValueError, AttributeError, TypeError) as exc:
        raise ApplicationEventIndexUnavailable("a committed application event is invalid") from exc


def _working_files_are(root: Path, paths: Mapping[str, object]) -> bool:
    """Whether the family's working directory holds exactly ``paths``: plain files, nothing redirected, nothing extra."""

    current = root
    for component in Path(FAMILY_PREFIX).parts:
        current = current / component
        if current.is_symlink():
            return False
    if not current.exists():
        return not paths
    found: set[str] = set()

    def listed(directory: str, relative: str) -> bool:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    return False
                name = f"{relative}/{entry.name}"
                if entry.is_dir(follow_symlinks=False):
                    if not listed(entry.path, name):
                        return False
                else:
                    found.add(name)
        return True

    try:
        if not current.is_dir() or not listed(os.fspath(current), FAMILY_PREFIX.rstrip("/")):
            return False
    except OSError:
        return False
    return found == set(paths)


def _advance(key: tuple[str, str, str], root: Path, reader: Any, state: _State, head: str) -> _State | None:
    try:
        added = reader.additions(state.head, head, frozenset(state.events) | state.ghosts)
    except JournalError:
        return None
    if added is None:
        return None
    events = state.events
    blob_ids = state.blob_ids
    token = state.token
    if added:
        try:
            checked = {path: _checked_event(path, data, key) for path, (_blob_id, data) in added.items()}
        except ApplicationEventIndexUnavailable:
            return None
        events = dict(sorted({**events, **checked}.items()))
        blob_ids = {**blob_ids, **{path: blob_id for path, (blob_id, _data) in added.items()}}
        token = object()
    if not _working_files_are(root, events):
        return None
    advanced = _State(head, events, blob_ids, state.ghosts, token)
    _save_added(root, key, state.head, head, added)
    return advanced


def _build(key: tuple[str, str, str], root: Path, reader: Any) -> _State:
    """A full, proven read of the family; an event validated before is not validated again."""

    snapshot, ever = reader.family()
    committed = reader.blob_ids(snapshot.head) if snapshot.artifacts else {}
    if committed is None or set(committed) != set(snapshot.artifacts):
        raise JournalError("journal head moved during an applications read")
    with _LOCK:
        kept = _states.get(key)
        validated = dict(_validated.get(key, {}))
    if kept is not None:
        validated.update(kept.blob_ids)
    events: dict[str, dict[str, Any]] = {}
    try:
        for path in sorted(snapshot.artifacts):
            if validated.get(path) == committed[path] and _EVENT_PATH.fullmatch(path) is not None:
                # These bytes at this path were validated: the kept event, or the bytes parsed again.
                events[path] = kept.events[path] if kept is not None and kept.blob_ids.get(path) == committed[path] else _parsed(path, snapshot.artifacts[path])
            else:
                events[path] = _checked_event(path, snapshot.artifacts[path], key)
    except ApplicationEventIndexUnavailable:
        # Asked once a head: the caller's own read says what is wrong, every time.
        with _LOCK:
            _states.pop(key, None)
            _refused[key] = snapshot.head
        raise
    ghosts = ever - frozenset(events)
    same = kept is not None and kept.blob_ids == committed
    state = _State(snapshot.head, events, dict(committed), ghosts, kept.token if same and kept is not None else object())
    with _key_lock("state", *key):
        with _LOCK:
            _states[key] = state
            _validated.pop(key, None)
            _refused.pop(key, None)
        _save_all(root, key, state, snapshot.artifacts)
    return state


# --- the file --------------------------------------------------------------------------


def _index_path(root: Path, *, create: bool) -> Path | None:
    directory = root / INDEX_DIRECTORY
    try:
        if directory.is_symlink():
            return None
        if not directory.is_dir():
            if not create or directory.exists():
                return None
            directory.mkdir(mode=0o700)
        path = directory / INDEX_FILENAME
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return None
        if not create and not path.exists():
            return None
    except OSError:
        return None
    return path


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=_SQLITE_TIMEOUT_SECONDS, isolation_level=None)
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _meta(key: tuple[str, str, str], head: str) -> dict[str, str]:
    return {"schema": INDEX_SCHEMA, "validator": _validator_digest(), "project_id": key[1], "gig_id": key[2], "head": head}


def _working_signature(root: Path, path: str) -> tuple[int, int, int] | None:
    """Size, change time and identity of the plain working file at ``path``; ``None`` when it is not one."""

    try:
        found = os.lstat(os.path.join(root, path))
    except OSError:
        return None
    if not stat.S_ISREG(found.st_mode):
        return None
    return (found.st_size, found.st_mtime_ns, found.st_ino)


def _rows(root: Path, blob_ids: Mapping[str, str], artifacts: Mapping[str, bytes]) -> list[tuple[object, ...]] | None:
    """What the file keeps for each event; ``None`` when a working file is not there to sign."""

    rows: list[tuple[object, ...]] = []
    for path in sorted(blob_ids):
        signature = _working_signature(root, path)
        if signature is None:
            return None
        rows.append((path, blob_ids[path], artifacts[path], digest_imported_bytes(artifacts[path]), *signature))
    return rows


def _load(key: tuple[str, str, str], root: Path, reader: Any) -> _State | None:
    """The file's events, when the file is this Gig's, is exactly the tree of its own head, and its working files are unchanged."""

    path = _index_path(root, create=False)
    if path is None:
        return None
    try:
        connection = _connect(path)
        try:
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            stored = connection.execute("SELECT path, blob_id, data, digest, size, mtime_ns, inode FROM artifacts ORDER BY path").fetchall()
            ghosts = frozenset(str(name) for (name,) in connection.execute("SELECT path FROM ghosts"))
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None
    head = meta.get("head")
    if not isinstance(head, str) or meta != _meta(key, head):
        return None
    blob_ids: dict[str, str] = {}
    events: dict[str, dict[str, Any]] = {}
    signatures: dict[str, tuple[object, ...]] = {}
    try:
        for name, blob_id, data, digest, *signature in stored:
            if not isinstance(name, str) or not isinstance(blob_id, str) or not isinstance(data, bytes):
                return None
            if digest_imported_bytes(data) != digest:
                return None  # the stored bytes are not the ones that were stored
            events[name] = _parsed(name, data)
            blob_ids[name] = blob_id
            signatures[name] = tuple(signature)
    except ApplicationEventIndexUnavailable:
        return None
    # Kept even when the file cannot be used as it is: these paths under these blob ids were validated.
    with _LOCK:
        _validated[key] = dict(blob_ids)
    try:
        if reader.blob_ids(head) != blob_ids:
            return None  # not the tree of its own head
    except JournalError:
        return None
    if any(_working_signature(root, name) != signatures[name] for name in blob_ids):
        return None  # a working file changed since it was proven: the snapshot says what it is now
    return _State(head, events, blob_ids, ghosts, object())


def _parsed(path: str, data: bytes) -> dict[str, Any]:
    """Bytes that were validated before, as the event they are."""

    if _EVENT_PATH.fullmatch(path) is None:
        raise ApplicationEventIndexUnavailable("the applications family holds something that is not an event")
    try:
        # Plain JSON: these bytes passed the strict parse when they were validated.
        value = json.loads(data)
    except ValueError as exc:
        raise ApplicationEventIndexUnavailable("a kept application event is not JSON") from exc
    if not isinstance(value, dict):
        raise ApplicationEventIndexUnavailable("a kept application event is not an object")
    return value


def _save_all(root: Path, key: tuple[str, str, str], state: _State, artifacts: Mapping[str, bytes]) -> None:
    path = _index_path(root, create=True)
    rows = _rows(root, state.blob_ids, artifacts)
    if path is None or rows is None:
        return
    for attempt in (0, 1):
        try:
            connection = _connect(path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute(_ARTIFACTS_TABLE)
                connection.execute("CREATE TABLE IF NOT EXISTS ghosts (path TEXT PRIMARY KEY)")
                connection.execute("DELETE FROM meta")
                connection.execute("DELETE FROM artifacts")
                connection.execute("DELETE FROM ghosts")
                connection.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", list(_meta(key, state.head).items()))
                connection.executemany(_ARTIFACT_INSERT, rows)
                connection.executemany("INSERT INTO ghosts(path) VALUES (?)", [(name,) for name in sorted(state.ghosts)])
                connection.execute("COMMIT")
                return
            finally:
                connection.close()
        except sqlite3.DatabaseError:
            # A file that is not a database (or not this one) is replaced once; anything else is left for the next start.
            if attempt == 0:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    return
        except OSError:
            return


def _save_added(root: Path, key: tuple[str, str, str], old_head: str, head: str, added: Mapping[str, tuple[str, bytes]]) -> None:
    """The caught-up events into the file, when the file is the one this process caught up from."""

    path = _index_path(root, create=False)
    rows = _rows(root, {name: blob_id for name, (blob_id, _data) in added.items()}, {name: data for name, (_blob_id, data) in added.items()})
    if path is None or rows is None:
        return
    try:
        connection = _connect(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            if meta != _meta(key, old_head):
                connection.execute("ROLLBACK")  # another process moved it; the next start checks it against the tree
                return
            connection.executemany(_ARTIFACT_INSERT, rows)
            connection.execute("UPDATE meta SET value = ? WHERE key = 'head'", (head,))
            connection.execute("COMMIT")
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return


__all__ = [
    "ApplicationEventIndexUnavailable",
    "CommittedApplicationEvents",
    "EVENTS_PREFIX",
    "FAMILY_PREFIX",
    "INDEX_DIRECTORY",
    "INDEX_FILENAME",
    "committed_application_events",
    "forget_kept_events",
]
