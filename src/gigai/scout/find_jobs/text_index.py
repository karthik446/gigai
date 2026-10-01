"""Full-text index over the stored posting descriptions (SQLite FTS5).

``<home>/cache/scout/text.sqlite``. This is a CACHE, not a record: delete the
file, corrupt it, change :data:`SCHEMA_VERSION`, or run on a SQLite without
FTS5, and the next use rebuilds it from the stored company bodies (about 10 s
for 290k postings in the search-index spike) or reports it unavailable. It
never raises for those cases.

* ``text`` is a contentless FTS5 table over ``(title, description)``: the
  words are indexed, the text itself is not stored (the board cache already
  holds it). The tokenizer is ``unicode61 remove_diacritics 2``; there is NO
  Porter stemmer ("agentic" must not match "agent").
* ``postings`` maps each FTS rowid to ``(company_key, posting_id)`` and
  records whether the posting had text. A posting without text (a Greenhouse
  posting whose detail is not cached yet) is never returned by a text query;
  :func:`search` counts it so the caller can say "text not checked".
* Deleting from a contentless table needs SQLite 3.43 (``contentless_delete``).
  Older SQLite still works: every replace/remove becomes a rebuild.

Concurrency, as in ``tag_store.py``: WAL, one writer at a time (a process
lock plus SQLite's write lock), a connection per thread, and a connection
whose file was deleted or replaced underneath it is reopened.

Nothing here makes a request or touches the company index; it only reads the
existing readers (:class:`CompanyIndex`, :func:`cached_posting_rows`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sqlite3
import threading

from .ats_board_clients import BoardCache
from .company_index import CompanyIndex, cached_posting_rows, company_key

#: Layout of the file itself. A mismatch drops and rebuilds the cache.
SCHEMA_VERSION = 1

_BUSY_TIMEOUT_MS = 5000
#: bm25 column weights: a title hit counts 10x a description hit (spike REPORT A1).
_TITLE_WEIGHT = 10.0
_DESCRIPTION_WEIGHT = 1.0
_TOKENIZE = "unicode61 remove_diacritics 2"

#: ``contentless_delete`` arrived in SQLite 3.43.
_SUPPORTS_CONTENTLESS_DELETE = sqlite3.sqlite_version_info >= (3, 43, 0)

_FTS_DDL = (
    "CREATE VIRTUAL TABLE text USING fts5(title, description, content='', contentless_delete=1, "
    f"tokenize='{_TOKENIZE}')"
)
_FTS_DDL_NO_DELETE = f"CREATE VIRTUAL TABLE text USING fts5(title, description, content='', tokenize='{_TOKENIZE}')"

#: One statement each: ``executescript`` would commit the surrounding transaction.
_TABLES_DDL = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS postings (rowid INTEGER PRIMARY KEY, company_key TEXT NOT NULL, "
    "posting_id TEXT NOT NULL, has_text INTEGER NOT NULL, UNIQUE (company_key, posting_id))",
)


@dataclass(frozen=True, slots=True)
class TextPosting:
    """One posting to index. ``text`` is the description, or ``None``/empty when it is not stored yet."""

    posting_id: str
    title: str
    text: str | None = None


@dataclass(frozen=True, slots=True)
class TextHit:
    company_key: str
    posting_id: str
    #: bm25 (title weighted 10:1); lower is a better match, hits come best first.
    rank: float


@dataclass(frozen=True, slots=True)
class TextSearchResult:
    """What a text query found.

    ``available`` is False when the index cannot be used (no FTS5, or the
    file cannot be built); ``hits`` is then empty and ``reason`` says why.
    ``unchecked`` counts indexed postings with no stored text (inside the
    searched companies): they cannot match, so the caller says "text not
    checked" for them. ``error`` is ``"bad_query"`` for a query with no usable terms.
    """

    available: bool
    hits: tuple[TextHit, ...] = ()
    unchecked: int = 0
    reason: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class TextIndexStats:
    available: bool
    postings: int = 0
    with_text: int = 0
    without_text: int = 0
    reason: str | None = None


class _Unavailable(Exception):
    """FTS5 cannot be used on this SQLite (or the file cannot be created)."""


class _Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self.write_lock = threading.Lock()

    # -- connections -----------------------------------------------------

    def _identity(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_dev, st.st_ino)

    def _discard_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(f"{self.path}{suffix}")
            except FileNotFoundError:
                pass

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (0, 1):
            conn = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                conn.execute("PRAGMA synchronous=NORMAL")
                self._ensure_schema(conn)
                return conn
            except _Unavailable:
                conn.close()
                raise
            except sqlite3.DatabaseError as exc:
                conn.close()
                if isinstance(exc, sqlite3.OperationalError):
                    # A locked/busy database is not a damaged one: never delete the file another process is writing.
                    raise _Unavailable("text index is busy or cannot be opened") from None
                if attempt:
                    raise _Unavailable("text index file cannot be created") from None
                self._discard_files()  # a cache: an unreadable file is rebuilt, not repaired
        raise AssertionError("unreachable")

    @staticmethod
    def _create(conn: sqlite3.Connection) -> None:
        """Create the tables; ``built`` stays ``0`` until a build fills them."""

        conn.execute("DROP TABLE IF EXISTS text")
        conn.execute("DROP TABLE IF EXISTS postings")
        conn.execute("DROP TABLE IF EXISTS meta")
        for statement in _TABLES_DDL:
            conn.execute(statement)
        can_delete = 1
        try:
            conn.execute(_FTS_DDL if _SUPPORTS_CONTENTLESS_DELETE else _FTS_DDL_NO_DELETE)
            can_delete = 1 if _SUPPORTS_CONTENTLESS_DELETE else 0
        except sqlite3.OperationalError as error:
            if "no such module" in str(error):
                raise _Unavailable("this SQLite has no FTS5") from None
            try:
                conn.execute(_FTS_DDL_NO_DELETE)
                can_delete = 0
            except sqlite3.OperationalError as fallback:
                raise _Unavailable(f"FTS5 table cannot be created: {fallback}") from None
        for key, value in (("schema_version", str(SCHEMA_VERSION)), ("built", "0"), ("can_delete", str(can_delete))):
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        has_text = conn.execute("SELECT 1 FROM sqlite_master WHERE name='text'").fetchone()
        version = None
        if has_meta:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            version = row[0] if row else None
        if has_meta and has_text and version == str(SCHEMA_VERSION):
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._create(conn)
            conn.execute("COMMIT")
        except BaseException:  # noqa: BLE001 - cleans up (rollback/undo) and re-raises: nothing is swallowed
            conn.execute("ROLLBACK")
            raise

    def conn(self) -> sqlite3.Connection:
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
        held = getattr(self._local, "held", None)
        if held is not None:
            held[0].close()
            self._local.held = None


_stores: dict[Path, _Store] = {}
_stores_lock = threading.Lock()


def text_index_path(home_root: Path) -> Path:
    """``<home>/cache/scout/text.sqlite`` (next to the board and company caches)."""

    return Path(home_root) / "cache" / "scout" / "text.sqlite"


def _store(home_root: Path) -> _Store:
    path = text_index_path(home_root)
    with _stores_lock:
        store = _stores.get(path)
        if store is None:
            store = _stores[path] = _Store(path)
        return store


def close(home_root: Path) -> None:
    """Close the calling thread's connection to this home's index (tests, shutdown)."""

    _store(home_root).close()


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _clean(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _delete_company(conn: sqlite3.Connection, key: str) -> None:
    rowids = [row[0] for row in conn.execute("SELECT rowid FROM postings WHERE company_key=?", (key,))]
    for rowid in rowids:
        conn.execute("DELETE FROM text WHERE rowid=?", (rowid,))
    conn.execute("DELETE FROM postings WHERE company_key=?", (key,))


def _insert_postings(conn: sqlite3.Connection, key: str, postings: Iterable[TextPosting]) -> None:
    seen: set[str] = set()
    for posting in postings:
        if not posting.posting_id or posting.posting_id in seen:
            continue
        seen.add(posting.posting_id)
        text = _clean(posting.text)
        cursor = conn.execute(
            "INSERT INTO postings (company_key, posting_id, has_text) VALUES (?, ?, ?)",
            (key, posting.posting_id, 1 if text else 0),
        )
        if text:
            conn.execute(
                "INSERT INTO text (rowid, title, description) VALUES (?, ?, ?)",
                (cursor.lastrowid, posting.title or "", text),
            )


def _cached_company_postings(home_root: Path) -> dict[str, list[TextPosting]]:
    """Every indexed company's live postings with the text the board cache still holds."""

    index = CompanyIndex.for_home(home_root)
    cache = BoardCache(Path(home_root) / "cache" / "scout" / "ats-boards")
    out: dict[str, list[TextPosting]] = {}
    for ats, slug in index.keys():
        entry = index.read(ats, slug)
        if entry is None:
            continue
        live = entry.live()
        cached = cached_posting_rows(cache, ats, slug, (posting.posting_id for posting in live))
        postings = []
        for posting in live:
            row = cached.rows.get(posting.posting_id)
            postings.append(TextPosting(posting.posting_id, posting.title, row.text if row is not None else None))
        out[company_key(ats, slug)] = postings
    return out


def rebuild_from_cache(home_root: Path) -> TextIndexStats:
    """Drop the index and refill it from the stored company files and board bodies.

    The old index stays readable until the new one commits. Returns the new
    stats, or an unavailable stats (never raises) when FTS5 cannot be used.
    """

    store = _store(home_root)
    try:
        with store.write_lock:
            conn = store.conn()
            companies = _cached_company_postings(home_root)
            conn.execute("BEGIN IMMEDIATE")
            try:
                store._create(conn)
                for key, postings in companies.items():
                    _insert_postings(conn, key, postings)
                conn.execute("UPDATE meta SET value='1' WHERE key='built'")
                conn.execute("COMMIT")
            except BaseException:  # noqa: BLE001 - cleans up (rollback/undo) and re-raises: nothing is swallowed
                conn.execute("ROLLBACK")
                raise
    except (_Unavailable, sqlite3.DatabaseError) as error:
        return TextIndexStats(False, reason=str(error))
    return stats(home_root)


build = rebuild_from_cache


def _ready(home_root: Path) -> sqlite3.Connection:
    """The connection, built from the cache first when the file is new or was reset."""

    store = _store(home_root)
    conn = store.conn()
    if _meta(conn, "built") != "1":
        result = rebuild_from_cache(home_root)
        if not result.available:
            raise _Unavailable(result.reason or "text index unavailable")
        conn = store.conn()
    return conn


def upsert_company(home_root: Path, key: str, postings: Sequence[TextPosting]) -> bool:
    """Replace one company's postings in the index. ``False`` when the index is unavailable.

    ``key`` is :func:`company_index.company_key`. Without SQLite
    ``contentless_delete`` the whole index is rebuilt from the cache instead.
    """

    store = _store(home_root)
    try:
        conn = _ready(home_root)
        if _meta(conn, "can_delete") != "1":
            return rebuild_from_cache(home_root).available
        with store.write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _delete_company(conn, key)
                _insert_postings(conn, key, postings)
                conn.execute("COMMIT")
            except BaseException:  # noqa: BLE001 - cleans up (rollback/undo) and re-raises: nothing is swallowed
                conn.execute("ROLLBACK")
                raise
    except (_Unavailable, sqlite3.DatabaseError):
        return False
    return True


def remove_company(home_root: Path, key: str) -> bool:
    """Drop one company from the index. ``False`` when the index is unavailable."""

    store = _store(home_root)
    try:
        conn = _ready(home_root)
        if _meta(conn, "can_delete") != "1":
            return rebuild_from_cache(home_root).available
        with store.write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _delete_company(conn, key)
                conn.execute("COMMIT")
            except BaseException:  # noqa: BLE001 - cleans up (rollback/undo) and re-raises: nothing is swallowed
                conn.execute("ROLLBACK")
                raise
    except (_Unavailable, sqlite3.DatabaseError):
        return False
    return True


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

_TERM = re.compile(r"\w+", re.UNICODE)


def _quoted_terms(query: str) -> str:
    """Every word of the query as a quoted token: always valid FTS5, no operators."""

    return " ".join(f'"{term}"' for term in _TERM.findall(query))


def stats(home_root: Path) -> TextIndexStats:
    try:
        conn = _store(home_root).conn()
        with_text, without_text = conn.execute(
            "SELECT COALESCE(SUM(has_text), 0), COALESCE(SUM(1 - has_text), 0) FROM postings"
        ).fetchone()
    except (_Unavailable, sqlite3.DatabaseError) as error:
        return TextIndexStats(False, reason=str(error))
    return TextIndexStats(True, with_text + without_text, with_text, without_text)


def postings_with_text(home_root: Path, key: str) -> frozenset[str] | None:
    """The ids of one company's indexed postings that have stored text; ``None`` when the index is unavailable.

    What a text query could have matched: a posting of the company that is
    not in this set was not checked (:class:`TextSearchResult.unchecked`
    counts them; this says which ones).
    """

    try:
        conn = _ready(home_root)
        rows = conn.execute("SELECT posting_id FROM postings WHERE company_key=? AND has_text=1", (key,)).fetchall()
    except (_Unavailable, sqlite3.DatabaseError):
        return None
    return frozenset(row[0] for row in rows)


def search(
    home_root: Path,
    query: str,
    *,
    company_keys: Iterable[str] | None = None,
    limit: int = 50,
) -> TextSearchResult:
    """Postings whose title or description match ``query``, best first, plus the unchecked count.

    ``query`` is FTS5 syntax (words, ``"phrases"``, ``AND``/``OR``/``NOT``,
    ``prefix*``); a query FTS5 rejects is retried as plain words. Titles
    weigh 10x descriptions. ``company_keys`` restricts hits and the unchecked
    count to those companies.
    """

    if not _TERM.search(query or ""):
        return TextSearchResult(True, error="bad_query")
    keys = list(dict.fromkeys(company_keys)) if company_keys is not None else None
    try:
        conn = _ready(home_root)
        scope = ""
        params: list[object] = []
        if keys is not None:
            scope = f" AND p.company_key IN ({','.join('?' * len(keys))})"
            params = list(keys)
        sql = (
            "SELECT p.company_key, p.posting_id, bm25(text, ?, ?) AS rank FROM text "
            f"JOIN postings p ON p.rowid = text.rowid WHERE text MATCH ?{scope} ORDER BY rank, p.rowid LIMIT ?"
        )
        rows = None
        for match in (query, _quoted_terms(query)):
            try:
                rows = conn.execute(sql, (_TITLE_WEIGHT, _DESCRIPTION_WEIGHT, match, *params, max(1, limit))).fetchall()
                break
            except sqlite3.OperationalError:
                continue
        if rows is None:
            return TextSearchResult(True, error="bad_query")
        unchecked = conn.execute(
            f"SELECT COUNT(*) FROM postings p WHERE p.has_text = 0{scope}", params
        ).fetchone()[0]
    except (_Unavailable, sqlite3.DatabaseError) as error:
        return TextSearchResult(False, reason=str(error))
    return TextSearchResult(True, tuple(TextHit(*row) for row in rows), unchecked)
