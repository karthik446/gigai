"""Disposable SQLite projection of one authoritative private Git journal."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from typing import Iterator

from .canonical import canonical_json_bytes, parse_json_bytes, parse_json_front_matter


class JournalIndexError(RuntimeError):
    """The index cannot truthfully represent the authoritative journal."""


_INTERVIEW_EVENTS_COLUMNS = (
    ("session_id", "TEXT", 1, 1),
    ("sequence", "INTEGER", 1, 2),
    ("event", "TEXT", 1, 0),
    ("state", "TEXT", 1, 0),
    ("payload_sha256", "TEXT", 1, 0),
    ("occurred_at", "TEXT", 1, 0),
)


@dataclass(frozen=True)
class JournalProjection:
    project_id: str
    gig_id: str
    head: str
    entries: tuple[dict[str, object], ...]
    proposal: dict[str, object] | None
    active_version: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active_version": self.active_version,
            "entries": list(self.entries),
            "gig_id": self.gig_id,
            "head": self.head,
            "project_id": self.project_id,
            "proposal": self.proposal,
        }


def rebuild_index(*, workpad: Path, project_id: str, gig_id: str) -> JournalProjection:
    """Rebuild the ignored projection from committed workpad history only."""

    root = _root(workpad)
    projection = _authoritative_projection(
        root=root, project_id=project_id, gig_id=gig_id
    )
    with database_lock(root):
        _write_projection(root / "state.sqlite", projection)
    return projection


def _authoritative_projection(
    *, root: Path, project_id: str, gig_id: str,
    tolerate_manifest_errors: bool = False,
    require_clean: bool = True,
) -> JournalProjection:
    """Replay committed journal authority into one deterministic projection."""

    if require_clean:
        _require_clean_authority(root)
    commits = tuple(
        line
        for line in _git(root, "rev-list", "--reverse", "HEAD").splitlines()
        if line
    )
    if not commits:
        raise JournalIndexError("authoritative journal has no committed handoffs")
    entries: list[dict[str, object]] = []
    expected_sequence = 1
    for commit in commits:
        names = _git(root, "show", "--format=", "--name-only", commit).splitlines()
        handoffs = [
            name
            for name in names
            if name.startswith("handoffs/") and name.endswith(".txt")
        ]
        if len(handoffs) != 1:
            raise JournalIndexError(
                "authoritative journal commit does not contain exactly one handoff"
            )
        handoff = handoffs[0]
        metadata, _body = parse_json_front_matter(
            _git_bytes(root, "show", f"{commit}:{handoff}")
        )
        if metadata.get("sequence") != expected_sequence:
            raise JournalIndexError("authoritative journal sequence diverges")
        if metadata.get("gig_id") != gig_id:
            raise JournalIndexError("authoritative journal Gig identity diverges")
        transition = metadata.get("transition")
        handoff_id = metadata.get("handoff_id")
        if not isinstance(transition, str) or not isinstance(handoff_id, str):
            raise JournalIndexError("authoritative journal handoff lacks identity")
        entries.append(
            {
                "commit": commit,
                "handoff_id": handoff_id,
                "path": handoff,
                "sequence": expected_sequence,
                "transition": transition,
            }
        )
        expected_sequence += 1
    head = commits[-1]
    proposal = _json_at(
        root,
        head,
        "manifests/gig-proposal.json",
        tolerate_invalid=tolerate_manifest_errors,
    )
    active_version = _json_at(
        root,
        head,
        "manifests/active-gig-version.json",
        tolerate_invalid=tolerate_manifest_errors,
    )
    return JournalProjection(
        project_id, gig_id, head, tuple(entries), proposal, active_version
    )


def read_index(*, workpad: Path, project_id: str, gig_id: str) -> JournalProjection:
    """Return a journal-matching projection, repairing any disposable divergence."""

    root = _root(workpad)
    authoritative = _authoritative_projection(
        root=root, project_id=project_id, gig_id=gig_id
    )
    with database_lock(root):
        try:
            projection = _read_projection(root / "state.sqlite")
            matches_authority = canonical_json_bytes(
                projection.as_dict()
            ) == canonical_json_bytes(authoritative.as_dict())
        except (JournalIndexError, OSError, sqlite3.Error, ValueError):
            matches_authority = False
        if not matches_authority:
            _write_projection(root / "state.sqlite", authoritative)
    return authoritative


def read_authoritative_index(
    *, workpad: Path, project_id: str, gig_id: str,
    tolerate_manifest_errors: bool = False,
) -> JournalProjection:
    """Read committed journal/manifests without touching the disposable index."""

    return _authoritative_projection(
        root=_root(workpad),
        project_id=project_id,
        gig_id=gig_id,
        tolerate_manifest_errors=tolerate_manifest_errors,
        require_clean=False,
    )


def _write_projection(path: Path, projection: JournalProjection) -> None:
    """Replace only managed rows in the existing database inode.

    Long-lived G22 HTTP connections keep referring to this inode.  The common
    lock serializes them with this transaction, so an index rebuild cannot
    orphan a successful trace write on a replaced temporary database.
    """
    interview_events = _read_interview_events(path)
    _read_scout_tables(path)
    if path.is_symlink():
        raise JournalIndexError("index is redirected")
    try:
        connection = sqlite3.connect(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TABLE IF EXISTS projection")
            connection.execute("CREATE TABLE projection (payload BLOB NOT NULL)")
            connection.execute(
                "INSERT INTO projection(payload) VALUES (?)",
                (canonical_json_bytes(projection.as_dict()),),
            )
            # Existing trace rows remain in-place.  The read above validates
            # the schema before any managed Scout mutation begins.
            del interview_events
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise JournalIndexError("state database cannot be transactionally rebuilt") from exc


def validate_state_database(path: Path) -> None:
    """Refuse malformed/unknown shared state before any Scout-table writer."""

    _read_interview_events(path)
    _read_scout_tables(path)


def _read_interview_events(
    path: Path,
) -> list[tuple[object, ...]] | None:
    """Read the G22 trace before replacing the disposable projection database.

    ``state.sqlite`` is currently shared by the rebuildable projection and the
    append-only interview trace.  A malformed database can be safely replaced,
    but a recognized trace table must never be silently discarded.
    """

    if path.is_symlink():
        raise JournalIndexError("state database is redirected")
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise JournalIndexError("state database is malformed") from exc
    try:
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'interview_events'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise JournalIndexError("state database is malformed") from exc
        if table is None:
            return None
        columns = tuple(
            (row[1], row[2], row[3], row[5])
            for row in connection.execute("PRAGMA table_info(interview_events)")
        )
        if columns != _INTERVIEW_EVENTS_COLUMNS:
            raise JournalIndexError("interview trace table schema is invalid")
        return connection.execute(
            "SELECT session_id, sequence, event, state, payload_sha256, occurred_at "
            "FROM interview_events ORDER BY session_id, sequence"
        ).fetchall()
    finally:
        connection.close()


def _read_scout_tables(path: Path) -> list[tuple[str, str, list[tuple[object, object]]]]:
    """Preserve only the closed SCOUT-03 projection family across rebuilds."""

    if path.is_symlink():
        raise JournalIndexError("state database is redirected")
    if not path.exists():
        return []
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise JournalIndexError("state database is malformed") from exc
    try:
        try:
            objects = connection.execute(
                "SELECT type, name, tbl_name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_autoindex_%'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JournalIndexError("state database is malformed") from exc
        unexpected_objects = [
            item for item in objects
            if item[0] != "table" or item[1] not in {
                "projection", "interview_events", "scout_records", "scout_operations", "scout_meta"
            }
        ]
        if unexpected_objects:
            raise JournalIndexError("state database has unsupported executable objects")
        names = {item[1] for item in objects}
        allowed = {"projection", "interview_events", "scout_records", "scout_operations", "scout_meta"}
        unknown = names - allowed
        if unknown:
            raise JournalIndexError("state database has unsupported tables")
        result = []
        for name in ("scout_records", "scout_operations", "scout_meta"):
            if name not in names:
                continue
            columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({name})"))
            if columns != ("key", "payload"):
                raise JournalIndexError("Scout projection table schema is invalid")
            rows = connection.execute(f"SELECT key, payload FROM {name} ORDER BY key").fetchall()
            result.append((name, f"CREATE TABLE {name} (key TEXT PRIMARY KEY, payload BLOB NOT NULL)", rows))
        return result
    finally:
        connection.close()


@contextmanager
def database_lock(root: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    """Serialize projection rebuilds and G22 trace writes after journal publication.

    Lock order is journal writer lock first, then this database lock.  Code that
    only writes the G22 projection takes this lock alone and never takes the
    journal lock, avoiding an inverse lock order.
    """

    if os.name != "posix":
        raise JournalIndexError("interprocess database locking requires POSIX flock")
    import fcntl
    path = root / ".git" / "gigai-state.lock"
    deadline = time.monotonic() + timeout_seconds
    with path.open("a+b") as stream:
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise JournalIndexError("state database lock is unavailable") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_projection(path: Path) -> JournalProjection:
    if path.is_symlink() or not path.is_file():
        raise JournalIndexError("index is unavailable")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT payload FROM projection").fetchall()
    finally:
        connection.close()
    if len(rows) != 1 or type(rows[0][0]) is not bytes:
        raise JournalIndexError("index contents are invalid")
    payload = parse_json_bytes(rows[0][0])
    if not isinstance(payload, dict):
        raise JournalIndexError("index payload is invalid")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise JournalIndexError("index entries are invalid")
    required = {"project_id", "gig_id", "head", "proposal", "active_version"}
    if not required.issubset(payload):
        raise JournalIndexError("index payload is incomplete")
    return JournalProjection(
        payload["project_id"],
        payload["gig_id"],
        payload["head"],
        tuple(entries),
        payload["proposal"],
        payload["active_version"],
    )


def _json_at(
    root: Path,
    commit: str,
    path: str,
    *,
    tolerate_invalid: bool = False,
) -> dict[str, object] | None:
    result = _git_process(root, "show", f"{commit}:{path}", check=False)
    if result.returncode != 0:
        return None
    try:
        payload = parse_json_bytes(result.stdout.encode("utf-8"))
    except (TypeError, ValueError):
        if tolerate_invalid:
            return None
        raise JournalIndexError(f"authoritative {path} is not valid JSON") from None
    if not isinstance(payload, dict):
        if tolerate_invalid:
            return None
        raise JournalIndexError(f"authoritative {path} is not an object")
    return payload


def _root(workpad: Path) -> Path:
    root = workpad.resolve(strict=True)
    if root != workpad or root.is_symlink() or not root.is_dir():
        raise JournalIndexError("workpad is unavailable or redirected")
    return root


def _require_clean_authority(root: Path) -> None:
    if _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise JournalIndexError("authoritative workpad has uncommitted divergence")


def _git(root: Path, *args: str) -> str:
    return _git_process(root, *args).stdout


def _git_bytes(root: Path, *args: str) -> bytes:
    return _git_process(root, *args, text=False).stdout


def _git_process(
    root: Path, *args: str, check: bool = True, text: bool = True
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        text=text,
        check=False,
        shell=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    if check and result.returncode != 0:
        raise JournalIndexError(f"authoritative Git read failed: {result.stderr}")
    return result


__all__ = [
    "JournalIndexError",
    "JournalProjection",
    "database_lock",
    "read_authoritative_index",
    "read_index",
    "rebuild_index",
    "validate_state_database",
]
