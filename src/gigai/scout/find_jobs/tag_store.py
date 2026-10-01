"""Title-keyed SQLite store for posting tags (level + coarse function).

``<home>/cache/scout/tags.sqlite``. This is a CACHE, not a record: delete the
file and the next use recreates it empty (tags are re-derived from titles).
Rows are keyed by the normalized title; a row written by an older tagger
version reads as "not tagged" and is re-tagged on the next pass.

Concurrency: one writer at a time (a process-local lock plus SQLite's own
write lock, WAL mode) and any number of readers. Each thread gets its own
connection; a connection whose file was deleted or replaced underneath it is
dropped and reopened, so a deleted store rebuilds in place.

No model call lives here. ``function`` may later be filled by a model
(``function_source`` = ``"model"``); ``titles_lacking_function`` lists what is
still waiting for one. A model that was asked and found no family leaves
``function`` empty with ``function_source`` = ``"model"``: such a title is
not asked again (``titles_awaiting_model`` leaves it out).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import threading
from typing import Iterable

#: Layout of the file itself. A mismatch drops and recreates the cache.
SCHEMA_VERSION = 1

SOURCE_RULES = "rules"
SOURCE_MODEL = "model"

_BUSY_TIMEOUT_MS = 5000

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS title_tags (
    title_key TEXT PRIMARY KEY,
    tagger_version INTEGER NOT NULL,
    level TEXT NOT NULL,
    level_source TEXT NOT NULL,
    function TEXT,
    function_source TEXT,
    model TEXT,
    prompt_version TEXT,
    tagged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS title_tags_no_function ON title_tags (function_source) WHERE function IS NULL;
"""


@dataclass(frozen=True, slots=True)
class TitleTag:
    """One stored tag. ``title_key`` is the normalized title."""

    title_key: str
    level: str
    level_source: str
    function: str | None
    function_source: str | None
    tagger_version: int
    model: str | None = None
    prompt_version: str | None = None


_OPEN_LOCK = threading.Lock()


class TagStore:
    """Thread-safe (one writer, many readers) title-tag cache."""

    def __init__(self, path: Path, *, tagger_version: int) -> None:
        self.path = Path(path)
        self.tagger_version = tagger_version
        self._local = threading.local()
        self._write_lock = threading.Lock()

    @classmethod
    def for_home(cls, home_root: Path, *, tagger_version: int) -> "TagStore":
        """``<home>/cache/scout/tags.sqlite`` (next to the board and company caches)."""

        return cls(Path(home_root) / "cache" / "scout" / "tags.sqlite", tagger_version=tagger_version)

    # -- connections -----------------------------------------------------

    def _identity(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_dev, st.st_ino)

    def _open(self) -> sqlite3.Connection:
        # Opening a brand-new store from two threads at once made one of them see a transient error and
        # delete the file the other was writing: opens (and the rebuild) are serialised process-wide.
        with _OPEN_LOCK:
            return self._open_locked()

    def _open_locked(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (0, 1):
            conn = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                conn.execute("PRAGMA synchronous=NORMAL")
                self._ensure_schema(conn)
                return conn
            except sqlite3.DatabaseError as exc:
                conn.close()
                # A locked/busy database is not a damaged one: never discard the file for it.
                if attempt or isinstance(exc, sqlite3.OperationalError):
                    raise
                self._discard_files()  # a cache: an unreadable file is rebuilt, not repaired
        raise AssertionError("unreachable")

    def _discard_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(f"{self.path}{suffix}")
            except FileNotFoundError:
                pass

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        if has_meta:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is not None and row[0] != str(SCHEMA_VERSION):
                conn.execute("DROP TABLE IF EXISTS title_tags")
                conn.execute("DROP TABLE IF EXISTS meta")
        conn.executescript(_DDL)
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def _conn(self) -> sqlite3.Connection:
        held = getattr(self._local, "held", None)
        identity = self._identity()
        if held is not None:
            conn, opened_identity = held
            if identity is not None and identity == opened_identity:
                return conn
            conn.close()  # file deleted or replaced under this thread: rebuild
            self._local.held = None
        conn = self._open()
        self._local.held = (conn, self._identity())
        return conn

    def close(self) -> None:
        """Close the calling thread's connection (other threads close theirs)."""

        held = getattr(self._local, "held", None)
        if held is not None:
            held[0].close()
            self._local.held = None

    # -- reads -----------------------------------------------------------

    def get_many(self, title_keys: Iterable[str]) -> dict[str, TitleTag]:
        """Current-version tags for these keys; stale-version rows are absent."""

        keys = list(dict.fromkeys(title_keys))
        found: dict[str, TitleTag] = {}
        conn = self._conn()
        for start in range(0, len(keys), 500):
            chunk = keys[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT title_key, level, level_source, function, function_source, tagger_version, model, prompt_version "
                f"FROM title_tags WHERE tagger_version = ? AND title_key IN ({marks})",
                (self.tagger_version, *chunk),
            ).fetchall()
            for row in rows:
                found[row[0]] = TitleTag(*row)
        return found

    def get(self, title_key: str) -> TitleTag | None:
        return self.get_many([title_key]).get(title_key)

    def count(self) -> int:
        """Current-version rows."""

        return self._conn().execute(
            "SELECT COUNT(*) FROM title_tags WHERE tagger_version = ?", (self.tagger_version,)
        ).fetchone()[0]

    def titles_lacking_function(self, limit: int = 1000, *, include_rules: bool = False) -> list[str]:
        """Normalized titles still waiting for a function tag, oldest first.

        Default: rows with no function at all. ``include_rules=True`` also
        lists rows whose function came from the rules (model-upgrade candidates).
        """

        where = "function IS NULL"
        if include_rules:
            where = f"(function IS NULL OR function_source = '{SOURCE_RULES}')"
        rows = self._conn().execute(
            f"SELECT title_key FROM title_tags WHERE tagger_version = ? AND {where} ORDER BY tagged_at, title_key LIMIT ?",
            (self.tagger_version, limit),
        ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _level_filter(levels: Iterable[str] | None, exclude_levels: Iterable[str] | None) -> tuple[str, list[str]]:
        clause, values = "", []
        for names, operator in ((levels, "IN"), (exclude_levels, "NOT IN")):
            if names is None:
                continue
            listed = list(dict.fromkeys(names))
            if not listed:
                if operator == "IN":
                    clause += " AND 0"  # no level asked for: nothing matches
                continue
            clause += f" AND level {operator} ({','.join('?' * len(listed))})"
            values.extend(listed)
        return clause, values

    def titles_awaiting_model(
        self,
        limit: int = 1000,
        *,
        levels: Iterable[str] | None = None,
        exclude_levels: Iterable[str] | None = None,
    ) -> list[str]:
        """Normalized titles no rule and no model has given a function, oldest first.

        ``levels`` keeps only titles at those rules levels (the demand set:
        the levels the active profiles ask for); ``exclude_levels`` leaves
        those out (the backfill: everything else).
        """

        clause, values = self._level_filter(levels, exclude_levels)
        rows = self._conn().execute(
            "SELECT title_key FROM title_tags WHERE tagger_version = ? AND function IS NULL AND function_source IS NULL"
            f"{clause} ORDER BY tagged_at, title_key LIMIT ?",
            (self.tagger_version, *values, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def count_awaiting_model(self, *, levels: Iterable[str] | None = None, exclude_levels: Iterable[str] | None = None) -> int:
        """How many titles ``titles_awaiting_model`` would list with no limit."""

        clause, values = self._level_filter(levels, exclude_levels)
        return self._conn().execute(
            f"SELECT COUNT(*) FROM title_tags WHERE tagger_version = ? AND function IS NULL AND function_source IS NULL{clause}",
            (self.tagger_version, *values),
        ).fetchone()[0]

    # -- writes ----------------------------------------------------------

    def write_rules(self, tags: Iterable[TitleTag]) -> int:
        """Insert rules tags; returns rows written.

        A key already present at the current version is left alone. A stale
        version row is replaced (level re-derived) but keeps a model-supplied
        function. Re-writing unchanged titles writes nothing.
        """

        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        written = 0
        with self._write_lock:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                for tag in tags:
                    existing = conn.execute(
                        "SELECT tagger_version, function, function_source, model, prompt_version FROM title_tags WHERE title_key = ?",
                        (tag.title_key,),
                    ).fetchone()
                    function, function_source = tag.function, tag.function_source
                    model, prompt_version = None, None
                    if existing is not None:
                        if existing[0] == self.tagger_version:
                            continue
                        if existing[2] == SOURCE_MODEL:  # a model's function outlives a rules-version bump
                            function, function_source, model, prompt_version = existing[1], existing[2], existing[3], existing[4]
                    conn.execute(
                        "INSERT INTO title_tags (title_key, tagger_version, level, level_source, function, function_source, model, prompt_version, tagged_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(title_key) DO UPDATE SET tagger_version=excluded.tagger_version, level=excluded.level, "
                        "level_source=excluded.level_source, function=excluded.function, function_source=excluded.function_source, "
                        "model=excluded.model, prompt_version=excluded.prompt_version, tagged_at=excluded.tagged_at",
                        (tag.title_key, self.tagger_version, tag.level, tag.level_source, function, function_source, model, prompt_version, stamp),
                    )
                    written += 1
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return written

    def set_model_function(self, title_key: str, function: str | None, *, model: str, prompt_version: str) -> bool:
        """Record a model's function for an already-tagged title; False if the title is not stored.

        ``function`` ``None`` records that the model was asked and found no family.
        """

        with self._write_lock:
            cur = self._conn().execute(
                "UPDATE title_tags SET function = ?, function_source = ?, model = ?, prompt_version = ? "
                "WHERE title_key = ? AND tagger_version = ?",
                (function, SOURCE_MODEL, model, prompt_version, title_key, self.tagger_version),
            )
            return cur.rowcount > 0
