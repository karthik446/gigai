"""0110-036: the applications rows of the Scout projection, without projecting everything.

``GET /api/applications`` answers ``projection_from_snapshot(...).applications``.
Built that way, the first read after a server start, and every read after a
recorded application, read every record family the projection knows and every
run's sealed outputs again, and validated every event again: 0.8 to 1.5 s at
300 events, growing with each one.

:func:`kept_application_rows` answers the same rows, value for value and key
for key, from two things that are kept:

* the committed events (``gigai.application_event_index``: proven and
  validated once, caught up by the commits since);
* the postings the runs acquired, by ``normalized_url`` (the join
  ``projection._link_external_refs`` makes). They change only when a commit
  touches a run's ``outputs/acquire.json``; until then they are the kept
  ones. Between two server starts they are in
  ``scratch/scout-application-links.sqlite`` in the workpad (ignored by git,
  never transferred), used only when the acquire outputs it was built from
  are exactly the ones in the tree at the head (their paths and blob ids:
  one ``git ls-tree``), and built again from the journal otherwise.

It answers ``None``, and the caller builds the projection as before, whenever
the rows depend on more than that: an event that names a document or a
Discover opportunity (its links are checked against other record families),
or anything the kept events refuse. The journal stays the authority: nothing
is served that a read of the journal at the same head would not serve
(``tests/behaviors/scout_find_jobs/test_applications_scale.py`` compares the
two over a mixed history).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

from ..application_event_index import (
    ApplicationEventIndexUnavailable,
    CommittedApplicationEvents,
    committed_application_events,
)
from ..canonical import digest_imported_bytes
from ..journal import JournalError, committed_blob_ids, read_committed_snapshot
from ..workpad import committed_read_cache, paths_committed_between
from .projection import _event_ref_value, _posting_by_normalized_url

LINKS_DIRECTORY = "scratch"
LINKS_FILENAME = "scout-application-links.sqlite"
LINKS_SCHEMA = "scout-application-links:1"
_RUNS_PREFIX = "runs/"
_SQLITE_TIMEOUT_SECONDS = 5.0
_LOOKUP_BATCH = 400
_ATTEMPTS = 3


@dataclass(frozen=True)
class KeptApplicationRows:
    """The projection's ``applications`` rows at ``head``.

    The rows are shared by every caller: copy a row before changing it.
    ``token`` is equal for as long as the rows are the same.
    """

    head: str
    rows: tuple[dict[str, object], ...]
    token: tuple[object, object]


class _Links:
    """The acquired postings by ``normalized_url``, as they are at ``head``."""

    def __init__(self, head: str, signature: str, postings: dict[str, dict[str, object] | None], complete: bool) -> None:
        self.head = head
        self.signature = signature
        self.postings = postings  # what was looked up so far; everything when ``complete``
        self.complete = complete
        self.generation = object()


_LOCK = threading.Lock()
_key_locks: dict[tuple[str, str, str], threading.Lock] = {}
_links: dict[tuple[str, str, str], _Links] = {}
_loaded: set[tuple[str, str, str]] = set()
_rows: dict[tuple[str, str, str], KeptApplicationRows] = {}


def forget_kept_rows() -> None:
    """As a process that just started: nothing kept in memory (the files stay)."""

    with _LOCK:
        _links.clear()
        _loaded.clear()
        _rows.clear()


def _is_acquire_output(path: str) -> bool:
    return path.startswith(_RUNS_PREFIX) and path.endswith("/outputs/acquire.json")


def _is_plain(event: dict[str, Any]) -> bool:
    """An event whose row reads nothing but the event and the acquired postings."""

    return "external_ref" in event and "opportunity_ref" not in event and event.get("document_refs") == []


def kept_application_rows(resolved: Any) -> KeptApplicationRows | None:
    """``projection_from_snapshot(...).applications`` at the journal head, or ``None``: build the projection."""

    key = (os.fspath(resolved.path), resolved.project_id, resolved.gig_id)
    try:
        with committed_read_cache():
            for _attempt in range(_ATTEMPTS):
                events = committed_application_events(resolved)
                if not all(_is_plain(event) for event in events.events.values()):
                    return None
                if not events.events:
                    return KeptApplicationRows(events.head, (), (events.token, None))
                with _LOCK:
                    lock = _key_locks.setdefault(key, threading.Lock())
                with lock:
                    links = _links_at(key, resolved, events.head)
                    if links is None:
                        continue  # the journal moved between the two reads: read the events again
                    token = (events.token, links.generation)
                    with _LOCK:
                        kept = _rows.get(key)
                    if kept is not None and kept.token == token:
                        return kept if kept.head == events.head else KeptApplicationRows(events.head, kept.rows, token)
                    built = KeptApplicationRows(events.head, _build_rows(events, _postings_for(resolved, links, events)), token)
                    with _LOCK:
                        _rows[key] = built
                    return built
    except (ApplicationEventIndexUnavailable, JournalError):
        return None
    return None


def _build_rows(events: CommittedApplicationEvents, postings: dict[str, dict[str, object] | None]) -> tuple[dict[str, object], ...]:
    """The rows ``projection._application_rows`` + ``_verify_opportunities`` + ``_link_external_refs`` build, key order included."""

    values: list[dict[str, object]] = [
        {**event, "opportunity_verified": False, "event_path": path, "journal_sequence": 0}
        for path, event in events.events.items()  # in path order
    ]
    values.sort(key=lambda item: (str(item.get("occurred_at", "")), 0, str(item.get("event_id", ""))))
    by_job: dict[str, list[dict[str, object]]] = {}
    for row in values:
        by_job.setdefault(str(_event_ref_value(row)), []).append(row)
    for group in by_job.values():
        superseded = {item.get("supersedes") for item in group}
        for item in group:
            item["current"] = item.get("event_id") not in superseded
            item["current_status"] = item.get("event_kind") if item["current"] else None
    for row in values:
        row["linked_posting"] = postings.get(str(row.get("external_ref")))
    return tuple(values)


# --- the acquired postings ---------------------------------------------------------------


def _links_at(key: tuple[str, str, str], resolved: Any, head: str) -> _Links | None:
    """The kept postings when they are the ones at ``head``, built again otherwise; ``None`` when the journal is past ``head``."""

    root = Path(resolved.path)
    with _LOCK:
        links = _links.get(key)
        first = key not in _loaded
        _loaded.add(key)
    if links is None and first:
        links = _load_links(key, resolved, head)
    if links is not None and links.head != head:
        touched = paths_committed_between(root, links.head, head)
        if touched is None or any(_is_acquire_output(path) for path in touched):
            links = None
        else:
            links.head = head  # nothing they were read from changed
    if links is None:
        snapshot = read_committed_snapshot(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, prefixes=(_RUNS_PREFIX,)
        )
        signature = _acquire_signature(resolved, snapshot.head)
        if signature is None:
            return None  # the journal moved under this read: the caller reads the events again
        postings: dict[str, dict[str, object] | None] = {**_posting_by_normalized_url(snapshot)}
        links = _Links(snapshot.head, signature, postings, complete=True)
        _save_links(root, key, links)
    with _LOCK:
        _links[key] = links
    return links if links.head == head else None


def _acquire_signature(resolved: Any, head: str) -> str | None:
    """The runs' acquire outputs in the tree at ``head``, as one digest of their paths and git blob ids."""

    committed = committed_blob_ids(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, head=head, prefix=_RUNS_PREFIX
    )
    if committed is None:
        return None
    outputs = sorted((name, blob) for name, blob in committed.items() if _is_acquire_output(name))
    return digest_imported_bytes(json.dumps(outputs).encode("utf-8"))


def _postings_for(resolved: Any, links: _Links, events: CommittedApplicationEvents) -> dict[str, dict[str, object] | None]:
    """``links.postings`` with every job these events name looked up."""

    if links.complete:
        return links.postings
    wanted = sorted({str(event.get("external_ref")) for event in events.events.values()} - set(links.postings))
    if wanted:
        path = _links_path(Path(resolved.path), create=False)
        found: dict[str, dict[str, object]] = {}
        try:
            if path is None:
                raise OSError("the links file is gone")
            connection = _connect(path)
            try:
                for start in range(0, len(wanted), _LOOKUP_BATCH):
                    batch = wanted[start:start + _LOOKUP_BATCH]
                    marks = ",".join("?" * len(batch))
                    for url, posting in connection.execute(f"SELECT url, posting FROM postings WHERE url IN ({marks})", batch):  # noqa: S608 - only placeholders are formatted in
                        found[str(url)] = json.loads(posting)
            finally:
                connection.close()
        except (sqlite3.Error, OSError, ValueError):
            # The file went away or broke under a running server: the journal answers.
            snapshot = read_committed_snapshot(
                workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, prefixes=(_RUNS_PREFIX,)
            )
            if snapshot.head != links.head:
                raise JournalError("journal head moved during an applications read") from None
            links.postings = {url: posting for url, posting in _posting_by_normalized_url(snapshot).items()}
            links.complete = True
            _save_links(Path(resolved.path), (os.fspath(resolved.path), resolved.project_id, resolved.gig_id), links)
            return links.postings
        for url in wanted:
            links.postings[url] = found.get(url)
    return links.postings


def _links_path(root: Path, *, create: bool) -> Path | None:
    directory = root / LINKS_DIRECTORY
    try:
        if directory.is_symlink():
            return None
        if not directory.is_dir():
            if not create or directory.exists():
                return None
            directory.mkdir(mode=0o700)
        path = directory / LINKS_FILENAME
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


def _links_meta(key: tuple[str, str, str], signature: str) -> dict[str, str]:
    return {"schema": LINKS_SCHEMA, "project_id": key[1], "gig_id": key[2], "signature": signature}


def _load_links(key: tuple[str, str, str], resolved: Any, head: str) -> _Links | None:
    """The file's postings, when it was built from exactly the acquire outputs in the tree at ``head``."""

    path = _links_path(Path(resolved.path), create=False)
    if path is None:
        return None
    try:
        connection = _connect(path)
        try:
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            connection.execute("SELECT url, posting FROM postings LIMIT 1").fetchall()
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None
    signature = meta.get("signature")
    if not isinstance(signature, str) or meta != _links_meta(key, signature):
        return None
    if _acquire_signature(resolved, head) != signature:
        return None
    return _Links(head, signature, {}, complete=False)


def _save_links(root: Path, key: tuple[str, str, str], links: _Links) -> None:
    path = _links_path(root, create=True)
    if path is None:
        return
    for attempt in (0, 1):
        try:
            connection = _connect(path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS postings (url TEXT PRIMARY KEY, posting TEXT NOT NULL)")
                connection.execute("DELETE FROM meta")
                connection.execute("DELETE FROM postings")
                connection.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", list(_links_meta(key, links.signature).items()))
                connection.executemany(
                    "INSERT INTO postings(url, posting) VALUES (?, ?)",
                    [(url, json.dumps(posting)) for url, posting in links.postings.items() if posting is not None],
                )
                connection.execute("COMMIT")
                return
            finally:
                connection.close()
        except sqlite3.DatabaseError:
            if attempt == 0:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    return
        except OSError:
            return


__all__ = ["KeptApplicationRows", "LINKS_DIRECTORY", "LINKS_FILENAME", "forget_kept_rows", "kept_application_rows"]
