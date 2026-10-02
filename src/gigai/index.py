"""Disposable SQLite projection of one authoritative private Git journal."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from typing import Iterator

from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, parse_json_front_matter
from .workpad import (
    ensure_run_local_artifact_excludes,
    journal_head,
    read_git_blobs,
    scratch_cache_path,
    straight_history,
)


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
        # Repair first: an existing workpad from before RUN_LOCAL_ARTIFACT_EXCLUDES
        # existed (or one whose runs/*/raw or runs/*/progress predate this
        # exclude) must not be permanently stuck divergent. This only ever adds
        # missing lines to the untracked .git/info/exclude -- never rewrites the
        # tracked .gitignore contract, never touches journal/commit content --
        # and does no journal/database locking of its own, so it's safe ahead
        # of the clean-authority check below.
        #
        # Side effect callers should know about: every `read_index` call --
        # including `gigai doctor`'s journal.index check and the start of
        # every `run.py`/`occurrence.py` operation -- can now write to
        # `.git/info/exclude` on disk. `gigai doctor` is not a strictly
        # read-only command as a result; the only writes it (transitively)
        # performs are these additive exclude-line repairs.
        ensure_run_local_artifact_excludes(root)
        _require_clean_authority(root)
    # 0110-044: the entries are a function of the journal head alone. They
    # are kept for a head in ``scratch/`` and carried to a later head by one
    # listing of only the new commits; whatever that cannot settle, and every
    # refusal, is the commit-by-commit walk's.
    known_head = journal_head(root)
    entries = None if known_head is None else _kept_entries(root, project_id, gig_id, known_head)
    if entries is None or known_head is None:
        head, entries = _walked_entries(root, gig_id)
        _keep_entries(root, project_id, gig_id, head, entries)
    else:
        head = known_head
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


def _walked_entries(root: Path, gig_id: str) -> tuple[str, list[dict[str, object]]]:
    """The journal's head and its entries, read commit by commit (two git calls each): the authority, and every refusal."""

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
    return commits[-1], entries


# --- 0110-044: the journal's entries, kept at the journal head ---------------
#
# ``_walked_entries`` asks git twice for every journal commit, on every read:
# 6,000 calls and 37 s at 3,000 commits for one ``gigai status``. The entries
# depend on nothing but the commits reachable from the head, so
# ``scratch/journal-index-entries.json`` keeps them for one head (``scratch/``
# is ignored by git in both layouts and never transferred; deleting the file
# is always safe). It is a cache, not a defence, used only when all of this
# holds:
#
# * it is this schema's, this project's and this Gig's, its body has the
#   digest its first line names, and every entry is well formed, numbered
#   from 1 without a gap and ends at the head it names;
# * the journal head is that head, or descends from it in a straight line of
#   commits each of which added exactly one handoff and changed nothing else
#   under ``handoffs/`` (``straight_history``: one listing of only the new
#   commits). The new handoffs are read by one ``git cat-file --batch`` and
#   pass the walk's own checks (sequence, Gig, identity).
#
# With no file the same listing is made once from the first commit. Anything
# else (a merge, a rewritten history, a commit with no handoff or two, a
# removed handoff, a sequence or a Gig that diverges, a name git would quote)
# is settled by the walk, with its errors; a refusal is never kept.

ENTRIES_FILENAME = "journal-index-entries.json"
ENTRIES_SCHEMA = "journal-index-entries/1"
_ENTRIES_MAX_BYTES = 256 * 1024 * 1024
_ENTRY_KEYS = ("commit", "handoff_id", "path", "sequence", "transition")


def _is_commit_id(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(character in "0123456789abcdef" for character in value)


def _read_kept_entries(root: Path, project_id: str, gig_id: str) -> tuple[str, list[dict[str, object]]] | None:
    """The kept ``(head, entries)`` when the file is this Gig's, undamaged and well formed."""

    path = scratch_cache_path(root, ENTRIES_FILENAME, create=False)
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            data = stream.read(_ENTRIES_MAX_BYTES + 1)
        first, newline, body = data.partition(b"\n")
        if not newline or len(data) > _ENTRIES_MAX_BYTES:
            return None
        header = json.loads(first)
        if not isinstance(header, dict) or set(header) != {"schema", "project_id", "gig_id", "head", "count", "body_sha256"}:
            return None
        if header["schema"] != ENTRIES_SCHEMA or header["project_id"] != project_id or header["gig_id"] != gig_id:
            return None
        if header["body_sha256"] != digest_imported_bytes(body):
            return None
        entries = json.loads(body)
    except (OSError, ValueError):
        return None
    head = header["head"]
    if not _is_commit_id(head) or not isinstance(entries, list) or not entries or type(header["count"]) is not int or header["count"] != len(entries):
        return None
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or tuple(entry) != _ENTRY_KEYS:
            return None
        if type(entry["sequence"]) is not int or entry["sequence"] != position or not _is_commit_id(entry["commit"]):
            return None
        if not all(isinstance(entry[key], str) for key in ("handoff_id", "path", "transition")):
            return None
    if entries[-1]["commit"] != head:
        return None
    return head, entries


def _listed_entries(
    root: Path, gig_id: str, old: str | None, new: str, first_sequence: int
) -> list[dict[str, object]] | None:
    """The entries of the commits after ``old`` up to ``new`` (``old`` ``None``: all of them); ``None``: walk."""

    chain = straight_history(root, old, new, "handoffs/")
    if chain is None:
        return None
    found: list[tuple[str, str]] = []
    for commit, changes in reversed(chain):
        if any(status != "A" or not _is_plain_name(name) for status, name in changes):
            return None
        handoffs = [name for _status, name in changes if name.startswith("handoffs/") and name.endswith(".txt")]
        if len(handoffs) != 1:
            return None
        found.append((commit, handoffs[0]))
    blobs = read_git_blobs(root, tuple(f"{commit}:{handoff}" for commit, handoff in found))
    if blobs is None or len(blobs) != len(found):
        return None
    entries: list[dict[str, object]] = []
    for sequence, ((commit, handoff), blob) in enumerate(zip(found, blobs), start=first_sequence):
        if blob is None:
            return None
        try:
            metadata, _body = parse_json_front_matter(blob[1])
        except ValueError:
            return None
        transition = metadata.get("transition")
        handoff_id = metadata.get("handoff_id")
        if (
            type(metadata.get("sequence")) is not int
            or metadata.get("sequence") != sequence
            or metadata.get("gig_id") != gig_id
            or not isinstance(transition, str)
            or not isinstance(handoff_id, str)
        ):
            return None
        entries.append({"commit": commit, "handoff_id": handoff_id, "path": handoff, "sequence": sequence, "transition": transition})
    return entries


def _is_plain_name(name: str) -> bool:
    """A path git prints as it is; any other is quoted by the walk's listing, which then reads it differently."""

    return all(" " <= character <= "~" and character not in '"\\' for character in name)


def _kept_entries(root: Path, project_id: str, gig_id: str, head: str) -> list[dict[str, object]] | None:
    """The journal's entries at ``head`` from the kept file, caught up or listed once; ``None``: walk."""

    kept = _read_kept_entries(root, project_id, gig_id)
    if kept is not None and kept[0] == head:
        return kept[1]
    entries: list[dict[str, object]] | None = None
    if kept is not None:
        added = _listed_entries(root, gig_id, kept[0], head, len(kept[1]) + 1)
        if added is not None:
            entries = kept[1] + added
    if entries is None:
        entries = _listed_entries(root, gig_id, None, head, 1)
    if entries is None:
        return None
    _keep_entries(root, project_id, gig_id, head, entries)
    return entries


def _keep_entries(root: Path, project_id: str, gig_id: str, head: str, entries: list[dict[str, object]]) -> None:
    """Record the entries for ``head``; a failure to write changes nothing but the next read."""

    path = scratch_cache_path(root, ENTRIES_FILENAME, create=True)
    if path is None or not entries or entries[-1].get("commit") != head:
        return
    body = json.dumps(entries, separators=(",", ":")).encode("utf-8")
    header = json.dumps(
        {
            "schema": ENTRIES_SCHEMA,
            "project_id": project_id,
            "gig_id": gig_id,
            "head": head,
            "count": len(entries),
            "body_sha256": digest_imported_bytes(body),
        },
        sort_keys=True,
    ).encode("utf-8")
    staged: str | None = None
    try:
        descriptor, staged = tempfile.mkstemp(prefix=f".{ENTRIES_FILENAME}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(header + b"\n" + body)
        os.replace(staged, path)
    except OSError:
        if staged is not None:
            try:
                os.unlink(staged)
            except OSError:
                pass


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
