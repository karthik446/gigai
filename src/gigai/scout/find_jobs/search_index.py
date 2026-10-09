"""Local search index over the company index: titles, companies, locations (SQLite FTS5).

``<home>/cache/scout/search.sqlite``, next to ``text.sqlite`` and the company
files. This is a CACHE, not a record: delete the file, damage it, change
:data:`SCHEMA_VERSION`, or run on a SQLite without FTS5, and every read says
"unavailable" (the caller scans the company files instead) until
:func:`rebuild_from_index` fills it again (about 10 s for 300k postings in the
0.1.11.7 spike). A read never raises for those cases and never builds.

The index only NARROWS; the rule decides. :func:`candidates` returns a
guaranteed superset of what the title rule accepts, because the indexed title
words are the rule's own stems (``ats_board_clients._words``; ``+`` and ``#``
written as letters so the tokenizer keeps "c++" and "c#"), and a typed title's
needed stems are asked for as whole tokens. The caller then runs
``matches_roles`` (or :func:`strict_title_match`) on each candidate. Company
and location words are whole words of the folded text (:func:`words_match`),
indexed the same way, so the index is exact for them.

What is stored, one row per indexed posting (removed ones too, flagged):

* ``p``: the posting, its stems, and the facts the default filters need,
  precomputed with the product's own functions so the filters are SQL:
  ``posted`` (published, else first seen: the newest-first key),
  ``published_ts`` (``NULL`` when the board gave no parsable date: the window
  keeps those rows, as ``filters.published_too_old`` does), ``wm`` (the work
  mode read from the location, ``work_mode.derive_work_mode``) and ``ckind``
  (what ``filters.country_match`` would derive before the wanted countries are
  known), with the country codes in ``pc``. 0.1.11.8 (schema 2): ``us_place``
  (``job_copies.place_of``: ``us``, ``unclear`` or ``other``, what US only
  reads) and ``content`` (the company index's ``content_sha256``, the digest of
  the posting's title and description; ``NULL`` when no description is stored:
  what makes two postings copies of one job).
* ``ft``: external-content FTS5 (``unicode61 remove_diacritics 2``) over
  ``title_words``, ``company_words``, ``location_words``, kept by two
  triggers. No ``contentless_delete`` (Debian 12 ships SQLite 3.40).
* ``boards``: file name, mtime and size of every company file the build saw
  (the stamp of ``postings._index_files``); ``meta``: ``schema_version``,
  ``built`` and the digest of ``boards``.

Reads (:func:`candidates`, :func:`title_counts`, :func:`status`) open the file
read-write with ``PRAGMA query_only=1`` (a read-only connection cannot open a
WAL file before its ``-shm`` exists), reopen it when it was deleted or
replaced, and inside one read transaction check ``schema_version``, ``built``
and the digest against the company folder. ``PRAGMA quick_check`` runs once
per open and after every stamp change, never per query, and only when the
index was not verified at that stamp: the WRITERS verify (see below), because
the check takes 0.5 to 0.6 s on 158.7 MB and a cold CLI process or a server
that just saw an update must not pay it. The folder is scanned
on every read, except while its own mtime has not moved since a scan (the
company index replaces a file with ``os.replace``, which moves it): a file
edited in place by hand is seen at the next scan.

Writes, as in ``text_index.py``: WAL, one writer at a time (a process lock
plus SQLite's write lock), a connection per thread. A build or an update is
ONE ``BEGIN IMMEDIATE`` in place (never a temp file + ``os.replace``: an open
connection would keep the old file), followed by ``wal_checkpoint(TRUNCATE)``.
Readers keep the rows from before the write until it commits.

Verified stamp (0.1.11.7 SI2): after its commit a writer (:func:`rebuild_from_index`,
:func:`refresh`, and :func:`upsert_company` / :func:`remove_company` unless the
caller passes ``verify=False`` and calls :func:`verify` once at the end of a
burst) runs ``PRAGMA quick_check`` itself and records ``meta.verified`` = the
stamp it verified. A reader that finds ``verified == stamp`` skips the check;
any other value (a write that was not verified, a hand edit) makes it run the
check as before. ACCEPTED TRADE-OFF: a file damaged AFTER it was verified is
no longer found by the reader's own ``quick_check``; any query that touches
the damaged pages still raises, and that reads as ``damaged`` (the caller scans
and the next :func:`rebuild_from_index` repairs it). Damage in pages no query
touches is served around until the next write verifies again.

Nothing here makes a request or writes outside its own file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, TypeVar
from urllib.parse import unquote

from ...canonical import digest_imported_bytes
from .ats_board_clients import MATCH_ANY_TITLE_ROLE, _needed, _title_segments, _words, matches_roles
from .company_index import _PROVIDERS, CompanyIndex, CompanyIndexEntry, CompanyIndexError, company_key
from .job_copies import PLACE_OTHER, place_of
from .filters import (
    DEFAULT_COUNTRY,
    _fold,
    _is_bare_remote,
    _regions,
    _structured_countries,
    location_countries,
    published_cutoff,
)
from .work_mode import HYBRID, IN_PERSON, ONSITE, REMOTE, UNKNOWN, derive_work_mode, in_area, in_person_modes, parse_area

if TYPE_CHECKING:
    from .contracts import FindJobsConfig

#: Layout of the file itself. A mismatch reads as "unavailable" until a rebuild.
SCHEMA_VERSION = 2

_BUSY_TIMEOUT_MS = 5000
_TOKENIZE = "unicode61 remove_diacritics 2"
#: A folder mtime younger than this can hide a replace in the same clock tick: such a scan is not remembered.
_FOLDER_SETTLE_NS = 2_000_000_000

#: Why a read is unavailable (:attr:`IndexResult.reason`).
MISSING = "missing"
DAMAGED = "damaged"
OTHER_SCHEMA = "other_schema"
NOT_BUILT = "not_built"
STALE = "stale"
BUSY = "busy"
NO_FTS5 = "no_fts5"

#: ``ckind``: what ``filters.country_match`` derives for a posting before the wanted countries are known.
_STRUCTURED, _TEXT, _REGION_ONLY, _BARE_REMOTE, _UNKNOWN_PLACE, _NO_LOCATION = (
    "structured", "text", "region_only", "bare_remote", "unknown", "none",
)

#: One statement each: ``executescript`` would commit the surrounding transaction.
_TABLES_DDL = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE p (id INTEGER PRIMARY KEY, board TEXT NOT NULL, company TEXT NOT NULL, posting_id TEXT NOT NULL, "
    "title TEXT NOT NULL, title_words TEXT NOT NULL, company_words TEXT NOT NULL, location TEXT NOT NULL, "
    "location_words TEXT NOT NULL, url TEXT NOT NULL, posted TEXT NOT NULL, published_ts TEXT, first_seen TEXT NOT NULL, "
    "changed_at TEXT, removed INTEGER NOT NULL, wm TEXT NOT NULL, ckind TEXT NOT NULL, us_place TEXT NOT NULL, content TEXT)",
    "CREATE INDEX p_board ON p (board)",
    "CREATE INDEX p_posted ON p (posted)",
    "CREATE TABLE pc (id INTEGER NOT NULL, country TEXT NOT NULL, PRIMARY KEY (country, id)) WITHOUT ROWID",
    "CREATE TABLE boards (file TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL)",
)
_FTS_DDL = (
    "CREATE VIRTUAL TABLE ft USING fts5(title_words, company_words, location_words, content='p', content_rowid='id', "
    f"tokenize='{_TOKENIZE}')"
)
_FTS_FILL = "INSERT INTO ft(rowid, title_words, company_words, location_words) SELECT id, title_words, company_words, location_words FROM p"
_TRIGGERS_DDL = (
    "CREATE TRIGGER p_ai AFTER INSERT ON p BEGIN INSERT INTO ft(rowid, title_words, company_words, location_words) "
    "VALUES (new.id, new.title_words, new.company_words, new.location_words); END",
    "CREATE TRIGGER p_ad AFTER DELETE ON p BEGIN INSERT INTO ft(ft, rowid, title_words, company_words, location_words) "
    "VALUES ('delete', old.id, old.title_words, old.company_words, old.location_words); END",
)
_INSERT_ROW = (
    "INSERT INTO p (id, board, company, posting_id, title, title_words, company_words, location, location_words, url, "
    "posted, published_ts, first_seen, changed_at, removed, wm, ckind, us_place, content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
_ROW_COLUMNS = (
    "p.board, p.company, p.posting_id, p.title, p.location, p.url, p.posted, p.published_ts, p.first_seen, "
    "p.changed_at, p.removed, p.us_place, p.content"
)

_PLAIN_WORD_RE = re.compile(r"[a-z0-9]+")
_ERRORS = (sqlite3.Error, OSError, UnicodeError)
_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# The rules the index is exact for (pure)
# ---------------------------------------------------------------------------


def strict_title_match(title: str, typed: str) -> bool:
    """The strict free-search rule: the whole-word rule AND every typed word in the title.

    ``matches_roles`` does not ask for a role's seniority words ("Senior
    Engineer" lists every engineer); a typed search does. Same stems as the
    rule, so "Sr." is "senior". Lives here until ``free_search.py`` exists
    (0.1.11.7 SI1); the profile rule is untouched.
    """

    if not matches_roles(title, (typed,)):
        return False
    words, _segments = _title_segments(title)
    return set(_words(typed)) <= set(words)


def plain_words(text: str) -> list[str]:
    """``text``'s words for a company or location match: lowercase, no diacritics, letters and digits."""

    return _PLAIN_WORD_RE.findall(_fold(text)) if type(text) is str else []


def words_match(text: str, words: Iterable[str]) -> bool:
    """Whether every one of ``words`` is a whole word of ``text`` ("ai" is in "Example AI", not in "Maintain")."""

    wanted = {word for typed in words for word in plain_words(typed)}
    return wanted <= set(plain_words(text))


def _token(word: str) -> str:
    """A rule stem as an FTS token: ``+`` and ``#`` are not token characters, so they become letters."""

    return word.replace("+", "plus").replace("#", "sharp")


def _title_words(title: str) -> str:
    return " ".join(_token(word) for word in _words(title))


def _stamp(value: object) -> str | None:
    """``postings.stamp``: an ISO instant as a fixed-width UTC stamp (compares as a string); ``None`` when it is not one.

    Copied, not imported: ``postings.py`` imports this package. A test pins the two together. One difference: an
    instant that leaves the calendar when moved to UTC (year 1 or 9999 with an offset) is the first or the last
    stamp, so the window still reads it as "before" or "after" every cutoff, as ``published_too_old`` does.
    """

    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    try:
        return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except OverflowError:
        return "0001-01-01T00:00:00.000000Z" if moment.year < 5000 else "9999-12-31T23:59:59.999999Z"


def _country_facts(location: str, structured: tuple[str, ...] | None) -> tuple[str, frozenset[str]]:
    """What ``filters.country_match`` derives for a posting, the wanted countries aside: a kind and the codes."""

    found = _structured_countries(structured)
    if found is not None:
        return _STRUCTURED, frozenset(found)
    if not location:
        return _NO_LOCATION, frozenset()
    codes = location_countries(location)
    region_named, members = _regions(location)
    codes |= members
    if codes:
        return _TEXT, frozenset(codes)
    if region_named:
        return _REGION_ONLY, frozenset()
    if _is_bare_remote(location):
        return _BARE_REMOTE, frozenset()
    return _UNKNOWN_PLACE, frozenset()


@lru_cache(maxsize=65536)
def _mode_fits(mode: str, location: str, preference: str, area_text: str | None) -> int:
    """``work_mode.work_mode_fit(...).passes`` for a Hybrid or Onsite preference, from the stored ``wm`` (a SQL function)."""

    area = parse_area(area_text)
    matched = in_area(location, area) if area is not None and mode in (HYBRID, ONSITE, IN_PERSON) else None
    if mode in (REMOTE, UNKNOWN):
        return 1
    if preference == HYBRID and mode == ONSITE:
        return int(ONSITE in in_person_modes(preference) and matched is True)
    return int(matched is not False)


# ---------------------------------------------------------------------------
# What a caller asks and gets
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexFilters:
    """The default filters, as the index applies them (exactly the product's three functions on the index fields).

    ``cutoff``: a posting published before it is out, an undated one stays
    (``filters.published_too_old``). ``countries``: ``filters.country_match``
    is not ``False``. ``work_mode`` + ``area``: ``work_mode.work_mode_fit``
    with the work mode read from the location (the index holds no board field).
    """

    cutoff: str | None = None
    countries: tuple[str, ...] = ()
    work_mode: str = "any"
    area: str | None = None
    #: 0.1.11.8 N1: US only decides the country instead of ``countries``: every posting but one whose location is
    #: clearly outside the US (``job_copies.place_of``, stored as ``us_place``).
    us_only: bool = False

    @classmethod
    def from_config(cls, config: "FindJobsConfig", *, now: datetime | None = None) -> "IndexFilters":
        """``config``'s window, countries and work mode. Raises what ``published_cutoff`` raises for a bad fixed date."""

        cutoff = published_cutoff(config, now=now)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        return cls(
            cutoff=cutoff.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            countries=tuple(config.countries),
            work_mode=config.effective_work_mode.value,
            area=config.location,
        )


@dataclass(frozen=True, slots=True)
class IndexQuery:
    """One search. ``titles``: any of them (none = every posting); ``filters=None`` is "show all"."""

    titles: tuple[str, ...] = ()
    #: Narrow by every typed word (:func:`strict_title_match`) instead of the rule's needed words.
    strict: bool = False
    company_words: tuple[str, ...] = ()
    location_words: tuple[str, ...] = ()
    filters: IndexFilters | None = None
    include_removed: bool = False


@dataclass(frozen=True, slots=True)
class IndexRow:
    #: ``company_index.company_key``.
    board: str
    company: str
    posting_id: str
    title: str
    location: str
    url: str
    #: Published, else first seen, as a UTC stamp: the newest-first key.
    posted: str
    published_at: str | None
    first_seen: str
    changed_at: str | None
    removed: bool
    #: 0.1.11.8: ``job_copies.place_of`` of the posting (``us``, ``unclear``, ``other``).
    place: str = ""
    #: 0.1.11.8: the digest of the posting's title and description (``None``: no description stored).
    content: str | None = None


def _row(row: Sequence[object]) -> IndexRow:
    return IndexRow(*row[:10], removed=bool(row[10]), place=row[11], content=row[12])  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class IndexResult:
    """What the index holds for a query, or ``available=False`` with a ``reason``: then the caller scans.

    ``rows`` (:func:`candidates`) are newest first; ``counts``
    (:func:`title_counts`) are ``(title, postings)`` pairs; ``copies``
    (:func:`copy_counts`) are ``(title, board, company, removed, content, postings)``.
    All are CANDIDATES: the caller applies the title rule. ``stamp`` names the
    index state that answered, so two pages can be told to come from the same one.
    """

    available: bool
    rows: tuple[IndexRow, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()
    copies: tuple[tuple[str, str, str, bool, str | None, int], ...] = ()
    reason: str | None = None
    detail: str | None = None
    stamp: str | None = None


@dataclass(frozen=True, slots=True)
class IndexStatus:
    available: bool
    postings: int = 0
    live: int = 0
    boards: int = 0
    reason: str | None = None
    detail: str | None = None
    stamp: str | None = None


class _Unavailable(Exception):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _why(error: BaseException) -> tuple[str, str]:
    if isinstance(error, _Unavailable):
        return error.reason, error.detail or error.reason
    text = str(error)
    if "no such module" in text:
        return NO_FTS5, text
    if "locked" in text or "busy" in text:
        return BUSY, text
    if "no such table" in text:
        return NOT_BUILT, text
    return DAMAGED, f"{type(error).__name__}: {text}"


# ---------------------------------------------------------------------------
# The file and its connections
# ---------------------------------------------------------------------------


def search_index_path(home_root: Path) -> Path:
    """``<home>/cache/scout/search.sqlite`` (next to the company files and ``text.sqlite``)."""

    return Path(home_root) / "cache" / "scout" / "search.sqlite"


def _folder_files(root: Path) -> dict[str, tuple[int, int]]:
    """``postings._index_files``: ``file name -> (mtime, size)`` of every company index file. No file is opened."""

    files: dict[str, tuple[int, int]] = {}
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.endswith(".json") and ":" in entry.name:
                    found = entry.stat()
                    files[entry.name] = (found.st_mtime_ns, found.st_size)
    except OSError:
        pass
    return files


def _digest(files: Iterable[tuple[str, int, int]]) -> str:
    return digest_imported_bytes(json.dumps(sorted(files)).encode("utf-8"))


class _Store:
    def __init__(self, home_root: Path) -> None:
        self.path = search_index_path(home_root)
        self.companies = CompanyIndex.for_home(home_root)
        self._local = threading.local()
        self.write_lock = threading.Lock()
        #: ``(the folder's mtime, the digest scanned at it)``: lets a server skip the scan while the folder is still.
        self.folder_seen: tuple[int, str] | None = None

    def _identity(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    def discard_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(f"{self.path}{suffix}")
            except FileNotFoundError:
                pass

    def _held(self, name: str) -> sqlite3.Connection | None:
        """This thread's ``name`` connection, unless the file was deleted or replaced under it."""

        held = getattr(self._local, name, None)
        if held is None:
            return None
        conn, opened_identity = held
        identity = self._identity()
        if identity is not None and identity == opened_identity:
            return conn
        self.drop(name)
        return None

    def drop(self, name: str) -> None:
        held = getattr(self._local, name, None)
        if held is not None:
            setattr(self._local, name, None)
            try:
                held[0].close()
            except sqlite3.Error:
                pass
        if name == "reader":
            self._local.checked = None

    def writer(self) -> sqlite3.Connection:
        conn = self._held("writer")
        if conn is not None:
            return conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except BaseException:  # noqa: BLE001 - closes and re-raises: nothing is swallowed
            conn.close()
            raise
        self._local.writer = (conn, self._identity())
        return conn

    def reader(self) -> sqlite3.Connection:
        """Read-write, ``query_only``: never creates the file, never changes it."""

        conn = self._held("reader")
        if conn is not None:
            return conn
        if not self.path.is_file():
            raise _Unavailable(MISSING)
        identity = self._identity()
        conn = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=rw", uri=True, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
        try:
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA query_only=1")
            conn.create_function("scout_mode_fits", 4, _mode_fits, deterministic=True)
        except BaseException:  # noqa: BLE001 - closes and re-raises: nothing is swallowed
            conn.close()
            raise
        self._local.reader = (conn, identity)
        self._local.checked = None
        return conn

    def folder_digest(self) -> str:
        """The stamp of the company folder now; scanned unless the folder has not moved since a scan."""

        try:
            moved = os.stat(self.companies.root).st_mtime_ns
        except OSError:
            moved = None
        seen = self.folder_seen
        if seen is not None and seen[0] == moved:
            return seen[1]
        began = time.time_ns()
        digest = _digest((name, mtime, size) for name, (mtime, size) in _folder_files(self.companies.root).items())
        self.folder_seen = (moved, digest) if moved is not None and began - moved > _FOLDER_SETTLE_NS else None
        return digest

    def close(self) -> None:
        self.drop("writer")
        self.drop("reader")
        self.folder_seen = None


_stores: dict[Path, _Store] = {}
_stores_lock = threading.Lock()


def _store(home_root: Path) -> _Store:
    path = search_index_path(home_root)
    with _stores_lock:
        store = _stores.get(path)
        if store is None:
            store = _stores[path] = _Store(Path(home_root))
        return store


def close(home_root: Path) -> None:
    """Close the calling thread's connections to this home's index (tests, shutdown)."""

    _store(home_root).close()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _file_key(name: str) -> tuple[str, str] | None:
    """``(ats, slug)`` of a company file name, as ``CompanyIndex.keys`` reads it; ``None`` for any other file."""

    ats, _, encoded = name[: -len(".json")].partition(":")
    if ats not in _PROVIDERS or not encoded:
        return None
    return ats, unquote(encoded)


def _read_file(index: CompanyIndex, name: str) -> CompanyIndexEntry | None:
    key = _file_key(name)
    if key is None:
        return None
    try:
        if index.path(*key).name != name:
            return None
    except CompanyIndexError:
        return None
    return index.read(*key)


class _Facts:
    """Per-text caches of the precomputed facts: titles and locations repeat across boards (pure functions)."""

    def __init__(self) -> None:
        self.words: dict[str, str] = {}
        self.places: dict[str, tuple[str, str]] = {}
        self.countries: dict[tuple[str, tuple[str, ...] | None], tuple[str, frozenset[str]]] = {}
        self.us_places: dict[tuple[str, tuple[str, ...] | None], str] = {}

    def title(self, title: str) -> str:
        found = self.words.get(title)
        if found is None:
            found = self.words[title] = _title_words(title)
        return found

    def place(self, location: str) -> tuple[str, str]:
        found = self.places.get(location)
        if found is None:
            found = self.places[location] = (" ".join(plain_words(location)), derive_work_mode(location, None).mode)
        return found

    def us_place(self, location: str, structured: tuple[str, ...] | None) -> str:
        key = (location, structured)
        found = self.us_places.get(key)
        if found is None:
            found = self.us_places[key] = place_of(location, structured)
        return found

    def country(self, location: str, structured: tuple[str, ...] | None) -> tuple[str, frozenset[str]]:
        key = (location, structured)
        found = self.countries.get(key)
        if found is None:
            found = self.countries[key] = _country_facts(location, structured)
        return found


def _index_file(conn: sqlite3.Connection, index: CompanyIndex, name: str, next_id: int, facts: _Facts) -> int:
    """Insert one company file's postings and its stamp (the caller dropped the old rows). Returns the next free id."""

    try:
        # The stamp is taken BEFORE the read: a file replaced in between is then seen as changed, never as current.
        found = os.stat(index.root / name)
    except OSError:
        return next_id
    entry = _read_file(index, name)
    conn.execute("INSERT OR REPLACE INTO boards (file, mtime_ns, size) VALUES (?, ?, ?)", (name, found.st_mtime_ns, found.st_size))
    if entry is None:
        return next_id  # unreadable or not a company file: stamped (so the folder compares equal), no postings
    company = entry.company if type(entry.company) is str else ""
    company_words = " ".join(plain_words(company))
    rows = []
    countries = []
    for posting_id, posting in entry.postings.items():
        location_words, mode = facts.place(posting.location)
        kind, codes = facts.country(posting.location, posting.countries)
        published = _stamp(posting.published_at)
        rows.append((
            next_id, entry.key, company, posting_id, posting.title, facts.title(posting.title), company_words,
            posting.location, location_words, posting.url, published or _stamp(posting.first_seen) or "", published,
            posting.first_seen, posting.changed_at, 1 if posting.removed else 0, mode, kind,
            facts.us_place(posting.location, posting.countries), posting.content_sha256 or None,
        ))
        countries.extend((next_id, code) for code in codes)
        next_id += 1
    conn.executemany(_INSERT_ROW, rows)
    conn.executemany("INSERT OR IGNORE INTO pc (id, country) VALUES (?, ?)", countries)
    return next_id


def _drop_file(conn: sqlite3.Connection, name: str) -> None:
    key = _file_key(name)
    if key is not None:
        board = company_key(*key)
        conn.execute("DELETE FROM pc WHERE id IN (SELECT id FROM p WHERE board = ?)", (board,))
        conn.execute("DELETE FROM p WHERE board = ?", (board,))  # the trigger drops the FTS rows
    conn.execute("DELETE FROM boards WHERE file = ?", (name,))


def _set_stamp(conn: sqlite3.Connection) -> None:
    digest = _digest(conn.execute("SELECT file, mtime_ns, size FROM boards").fetchall())
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('stamp', ?)", (digest,))


def _drop_everything(conn: sqlite3.Connection) -> None:
    """Empty the file whatever its layout was (another schema version included)."""

    for kind, name in conn.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchall():
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    # Virtual tables first: dropping one drops its shadow tables.
    for virtual in (1, 0):
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "AND (sql LIKE 'CREATE VIRTUAL TABLE%') = ?",
            (virtual,),
        ).fetchall()
        for (name,) in tables:
            conn.execute(f'DROP TABLE IF EXISTS "{name}"')


def _checkpoint(conn: sqlite3.Connection, *, truncate: bool = True) -> None:
    """Fold the WAL back into the file. A reader mid-query can make it partial: the next one finishes it.

    ``truncate=False`` is PASSIVE: it never waits for a reader (TRUNCATE waits up to the busy timeout), for the
    unverified writes of an update's burst; the verify at the end of the burst truncates.
    """

    try:
        conn.execute(f"PRAGMA wal_checkpoint({'TRUNCATE' if truncate else 'PASSIVE'})").fetchall()
    except sqlite3.Error:
        pass


def _is_current(conn: sqlite3.Connection) -> bool:
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('schema_version', 'built')").fetchall())
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            return False
        raise
    return meta.get("schema_version") == str(SCHEMA_VERSION) and meta.get("built") == "1"


def _counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    postings, live = conn.execute("SELECT COUNT(*), COALESCE(SUM(1 - removed), 0) FROM p").fetchone()
    return postings, live, conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0]


def _rebuild(conn: sqlite3.Connection, index: CompanyIndex, progress: Callable[[int, int], None] | None) -> None:
    names = sorted(_folder_files(index.root))
    if progress is not None:
        progress(0, len(names))
    conn.execute("BEGIN IMMEDIATE")
    try:
        _drop_everything(conn)
        for statement in _TABLES_DDL:
            conn.execute(statement)
        facts = _Facts()
        next_id = 1
        for done, name in enumerate(names, start=1):
            next_id = _index_file(conn, index, name, next_id, facts)
            if progress is not None:
                progress(done, len(names))
        # The FTS table is filled in one pass and the triggers come after: a row-by-row fill is slower.
        conn.execute(_FTS_DDL)
        conn.execute(_FTS_FILL)
        for statement in _TRIGGERS_DDL:
            conn.execute(statement)
        conn.execute("INSERT INTO ft(ft) VALUES ('optimize')")
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        _set_stamp(conn)
        conn.execute("INSERT INTO meta (key, value) VALUES ('built', '1')")
        conn.execute("COMMIT")
    except BaseException:  # noqa: BLE001 - cleans up (rollback) and re-raises: nothing is swallowed
        conn.execute("ROLLBACK")
        raise


def _quick_check_ok(conn: sqlite3.Connection) -> bool:
    # One FTS read first: see the reader (a connection's first FTS read after another connection's write).
    conn.execute("SELECT rowid FROM ft WHERE ft MATCH '\"0\"' LIMIT 1").fetchall()
    return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def _verify_conn(conn: sqlite3.Connection) -> bool:
    """``quick_check`` the committed state and record ``meta.verified`` = its stamp. ``False``: not verified (nothing raised)."""

    if not _is_current(conn):
        return False
    row = conn.execute("SELECT value FROM meta WHERE key = 'stamp'").fetchone()
    if row is None or not _quick_check_ok(conn):
        return False
    conn.execute("BEGIN IMMEDIATE")
    try:
        now = conn.execute("SELECT value FROM meta WHERE key = 'stamp'").fetchone()
        if now is None or now[0] != row[0]:
            conn.execute("ROLLBACK")  # another process wrote in between: its own verify (or a reader) covers that state
            return False
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('verified', ?)", (row[0],))
        conn.execute("COMMIT")
    except BaseException:  # noqa: BLE001 - cleans up (rollback) and re-raises: nothing is swallowed
        conn.execute("ROLLBACK")
        raise
    _checkpoint(conn)
    return True


def verify(home_root: Path) -> bool:
    """Run ``quick_check`` on the index as it is and record it as verified, so readers skip their own.

    For the end of a burst of ``upsert_company(..., verify=False)`` calls.
    ``True`` when the state was checked and recorded; ``False`` for a missing,
    unbuilt, other-version or failing file, or one written meanwhile (then a
    reader runs the check itself). Never raises, never builds.
    """

    store = _store(home_root)
    try:
        if not store.path.is_file():
            return False
        with store.write_lock:
            return _verify_conn(store.writer())
    except _ERRORS:
        return False


def rebuild_from_index(home_root: Path, *, progress: Callable[[int, int], None] | None = None) -> IndexStatus:
    """Drop the index and refill it from the company files. The ONLY full build.

    In place, inside one transaction: a reader keeps the old rows until the
    new ones commit, and a failed build leaves the old index as it was. A
    file SQLite cannot read (damaged, not a database) is deleted and built
    anew. Returns what was built (a read then checks it against the company
    folder), or an unavailable status (never raises) when FTS5 cannot be
    used or another process holds the write lock too long.

    ``progress(done, total)`` is called once before the first company file
    and after each one, on the calling thread; an exception it raises ends
    the build (the old index is kept) and propagates.
    """

    store = _store(home_root)
    built = (0, 0, 0)
    try:
        with store.write_lock:
            for attempt in (0, 1):
                try:
                    conn = store.writer()
                    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise sqlite3.DatabaseError("quick_check failed")
                    _rebuild(conn, store.companies, progress)
                    _checkpoint(conn)
                    built = _counts(conn)
                    _verify_conn(conn)  # a failure leaves no stamp: the first reader runs the check and says "damaged"
                    break
                except sqlite3.OperationalError:
                    raise  # locked, no FTS5, disk: not a damaged file, never delete what another process is writing
                except sqlite3.DatabaseError:
                    if attempt:
                        raise
                    store.drop("writer")
                    store.discard_files()  # a cache: an unreadable file is rebuilt, not repaired
            store.folder_seen = None
    except _ERRORS as error:
        reason, detail = _why(error)
        return IndexStatus(False, reason=reason, detail=detail)
    return IndexStatus(True, *built)


def _sync_files(home_root: Path, names: Callable[[sqlite3.Connection], Sequence[str]], *, verify: bool = True) -> bool:
    """Make the index hold what the folder holds for the named company files, in one transaction."""

    store = _store(home_root)
    try:
        if not store.path.is_file():
            return False
        with store.write_lock:
            conn = store.writer()
            if not _is_current(conn):
                return False
            conn.execute("BEGIN IMMEDIATE")
            try:
                if not _is_current(conn):
                    conn.execute("ROLLBACK")
                    return False
                facts = _Facts()
                next_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM p").fetchone()[0]
                for name in names(conn):
                    _drop_file(conn, name)
                    next_id = _index_file(conn, store.companies, name, next_id, facts)
                _set_stamp(conn)
                conn.execute("COMMIT")
            except BaseException:  # noqa: BLE001 - cleans up (rollback) and re-raises: nothing is swallowed
                conn.execute("ROLLBACK")
                raise
            _checkpoint(conn, truncate=verify)
            store.folder_seen = None
            if verify:
                _verify_conn(conn)
    except _ERRORS:
        return False
    return True


def upsert_company(home_root: Path, key: str, *, verify: bool = True) -> bool:
    """Replace one company's postings with what its company file holds now. ``False`` when nothing was written.

    ``key`` is ``company_index.company_key``. Call it AFTER the company
    file was written. A company whose file is gone or unreadable loses its
    postings. Never builds: on a missing, unbuilt or other-version index the
    result is ``False`` (:func:`is_built` tells why) and the caller runs
    :func:`rebuild_from_index` once, off the hot path. ``verify=False`` skips
    the ``quick_check`` after the commit (0.5 s at real size): a caller writing
    many companies calls :func:`verify` once at the end.
    """

    ats, _, slug = key.partition(":")
    try:
        name = CompanyIndex.for_home(home_root).path(ats, slug).name
    except CompanyIndexError:
        return False
    return _sync_files(home_root, lambda _conn: (name,), verify=verify)


def remove_company(home_root: Path, key: str, *, verify: bool = True) -> bool:
    """Drop one company after its company file was deleted (``False`` as in :func:`upsert_company`).

    The index mirrors the folder: while the file is still there this is :func:`upsert_company`.
    """

    return upsert_company(home_root, key, verify=verify)


def refresh(home_root: Path) -> bool:
    """Re-index every company file that differs from its stamp (new, changed, gone), in one transaction.

    What a caller runs after a read said ``stale``. ``False`` when nothing
    could be written (see :func:`upsert_company`).
    """

    root = CompanyIndex.for_home(home_root).root

    def changed(conn: sqlite3.Connection) -> list[str]:
        stamped = {name: (mtime, size) for name, mtime, size in conn.execute("SELECT file, mtime_ns, size FROM boards")}
        current = _folder_files(root)
        return sorted({name for name, found in current.items() if stamped.get(name) != found} | (stamped.keys() - current.keys()))

    return _sync_files(home_root, changed)


def is_built(home_root: Path) -> bool:
    """Whether the index file exists and a build of this schema version filled it. No side effects.

    Never creates, repairs or replaces the file (it opens it read-only),
    never builds, never raises. ``False`` for a missing, unreadable,
    half-built or other-version file, and for a WAL file a read-only
    connection cannot open yet ("unable to open database file", before its
    ``-shm`` exists): all of them are answered by :func:`rebuild_from_index`.
    It does not say the index is CURRENT: a read does (``stale``).
    """

    path = search_index_path(home_root)
    if not path.is_file():
        return False
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_MS / 1000)
    except sqlite3.Error:
        return False
    try:
        return _is_current(conn)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _read(home_root: Path, run: Callable[[sqlite3.Connection], _T], *, reopen: bool = True) -> tuple[_T, str]:
    """``run`` on a checked index, inside ONE read transaction (the checks and the rows are the same state)."""

    store = _store(home_root)
    again = False
    try:
        conn = store.reader()
        folder = store.folder_digest()
        conn.execute("BEGIN")
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            if meta.get("schema_version") != str(SCHEMA_VERSION):
                raise _Unavailable(OTHER_SCHEMA, f"schema_version {meta.get('schema_version')}")
            if meta.get("built") != "1":
                raise _Unavailable(NOT_BUILT)
            stamp = meta.get("stamp")
            if type(stamp) is not str or stamp != folder:
                raise _Unavailable(STALE, "the company files changed since the index was written")
            if meta.get("verified") == stamp:
                store._local.checked = stamp  # a writer ran quick_check at exactly this state
            elif getattr(store._local, "checked", None) != stamp:
                # Once per open and per stamp: a query alone does not notice damage in pages it does not touch.
                # A connection that read the FTS table before another connection wrote it reports a false "malformed
                # inverted index" when the check is its first FTS read since (SQLite 3.47, EXECUTED): one FTS read
                # first, and a failed check on a connection held from before is repeated on a new one.
                held = getattr(store._local, "checked", None) is not None
                conn.execute("SELECT rowid FROM ft WHERE ft MATCH '\"0\"' LIMIT 1").fetchall()
                verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
                if verdict != "ok":
                    again = held and reopen
                    raise _Unavailable(DAMAGED, f"quick_check: {str(verdict)[:120]}")
                store._local.checked = stamp
            return run(conn), stamp
        finally:
            conn.execute("ROLLBACK")
    except _Unavailable as error:
        if error.reason != DAMAGED:
            raise
        store.drop("reader")
        if not again:
            raise
    except _ERRORS as error:
        store.drop("reader")  # the next read opens and checks again
        raise _Unavailable(*_why(error)) from None
    return _read(home_root, run, reopen=False)


def _match(query: IndexQuery) -> str | None:
    """The FTS5 query: any typed title's stems, and every company and location word. ``""`` narrows nothing;
    ``None`` when no posting can match (titles were typed and none of them has a word the rule asks for)."""

    groups: list[str] = []
    any_title = not query.titles
    for typed in query.titles:
        if typed == MATCH_ANY_TITLE_ROLE:
            any_title = True
            continue
        if type(typed) is not str or not _needed(typed):
            continue  # the rule matches no title for it
        words = _words(typed) if query.strict else _needed(typed)
        groups.append("(" + " AND ".join(f'title_words:"{_token(word)}"' for word in dict.fromkeys(words)) + ")")
    if not groups and not any_title:
        return None
    parts = [] if any_title or not groups else ["(" + " OR ".join(groups) + ")"]
    parts += [f'company_words:"{word}"' for typed in query.company_words for word in plain_words(typed)]
    parts += [f'location_words:"{word}"' for typed in query.location_words for word in plain_words(typed)]
    return " AND ".join(parts)


def _sql(query: IndexQuery) -> tuple[str, list[object]] | None:
    """``FROM ... WHERE ...`` and its parameters; ``None`` when no posting can match."""

    match = _match(query)
    if match is None:
        return None
    clauses: list[str] = []
    params: list[object] = []
    source = "FROM p"
    if match:
        source = "FROM ft JOIN p ON p.id = ft.rowid"
        clauses.append("ft MATCH ?")
        params.append(match)
    if not query.include_removed:
        clauses.append("p.removed = 0")
    filters = query.filters
    if filters is not None:
        if filters.cutoff is not None:
            clauses.append("(p.published_ts IS NULL OR p.published_ts >= ?)")
            params.append(filters.cutoff)
        wanted = sorted({code.upper() for code in filters.countries})
        if filters.us_only:
            clauses.append(f"p.us_place != '{PLACE_OTHER}'")  # ONE country rule: US only, instead of the countries
        elif wanted:
            # filters.country_match is not False: no place / an unknown place stays, "Remote" alone is the default
            # country, a region alone is out, structured or parsed codes decide (structured with none: out).
            clauses.append(
                f"(p.ckind IN ('{_NO_LOCATION}', '{_UNKNOWN_PLACE}') OR (p.ckind = '{_BARE_REMOTE}' AND ?) "
                f"OR EXISTS (SELECT 1 FROM pc WHERE pc.id = p.id AND pc.country IN ({','.join('?' * len(wanted))})))"
            )
            params.append(1 if DEFAULT_COUNTRY in wanted else 0)
            params.extend(wanted)
        if filters.work_mode == REMOTE:
            clauses.append(f"p.wm IN ('{REMOTE}', '{UNKNOWN}')")
        elif filters.work_mode != "any":
            clauses.append(f"(p.wm IN ('{REMOTE}', '{UNKNOWN}') OR scout_mode_fits(p.wm, p.location, ?, ?))")
            params.extend((filters.work_mode, filters.area))
    return source + (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def _unavailable(error: _Unavailable) -> IndexResult:
    return IndexResult(False, reason=error.reason, detail=error.detail)


def candidates(home_root: Path, query: IndexQuery, *, limit: int | None = None, offset: int = 0, ordered: bool = True) -> IndexResult:
    """The postings the index narrows ``query`` to, newest first (``posted``, then URL), ``limit`` from ``offset``.

    ``ordered=False`` (0.1.11.8, with no ``limit``): every candidate in no particular order, for a caller that
    orders them itself (the copies of one job as one row): SQLite then sorts nothing.

    A superset of the rule's rows for the titles (the caller applies
    ``matches_roles`` or :func:`strict_title_match`), exact for the words and
    the filters. ``available=False`` when the index cannot answer: then
    there are no rows at all (never some of them) and the caller scans.
    """

    built = _sql(query)
    try:
        if built is None:
            _rows, stamp = _read(home_root, lambda _conn: ())
            return IndexResult(True, stamp=stamp)
        where, params = built
        order = "ORDER BY p.posted DESC, p.url DESC, p.id DESC " if ordered or limit is not None or offset else ""
        sql = f"SELECT {_ROW_COLUMNS} {where} {order}LIMIT ? OFFSET ?"
        page = (-1 if limit is None else max(0, limit), max(0, offset))
        rows, stamp = _read(home_root, lambda conn: conn.execute(sql, (*params, *page)).fetchall())
    except _Unavailable as error:
        return _unavailable(error)
    return IndexResult(True, rows=tuple(_row(row) for row in rows), stamp=stamp)


def copy_counts(home_root: Path, query: IndexQuery) -> IndexResult:
    """The count path when copies are one row (0.1.11.8 N2): ``(title, board, company, removed, content, candidates)``.

    One row per title and description of a board, so the caller runs the
    title rule once per title and counts each job once (``job_copies.copy_key``;
    the postings with no stored description, ``content`` null, each count).
    """

    built = _sql(query)
    try:
        if built is None:
            _rows, stamp = _read(home_root, lambda _conn: ())
            return IndexResult(True, stamp=stamp)
        where, params = built
        sql = f"SELECT p.title, p.board, MIN(p.company), p.removed, p.content, COUNT(*) {where} GROUP BY p.board, p.title, p.removed, p.content"
        rows, stamp = _read(home_root, lambda conn: conn.execute(sql, params).fetchall())
    except _Unavailable as error:
        return _unavailable(error)
    found = tuple((title, board, company, bool(removed), content, count) for title, board, company, removed, content, count in rows)
    return IndexResult(True, copies=found, stamp=stamp)


def rows_with_url_part(home_root: Path, part: str, *, fold_case: bool = False) -> IndexResult:
    """Every posting whose stored URL contains ``part`` (``fold_case``: whatever its case), live ones first, then newest.

    FB1: the by-address job read. The row table has no URL index (a schema bump
    for one keyed read is not worth it), so this is one scan of ``p.url`` inside
    SQLite (no row is built for a non-match). The caller names ``part`` from the
    address it looks for (its path, or its host for a bare one) and applies the
    exact test (``normalize_job_identity`` of the row's URL): the stored URL is
    the board's own spelling, which the identity folds. ``available=False`` when
    the index cannot answer: then the caller scans the company files.
    """

    if not part:
        return IndexResult(True)
    test = "instr(lower(p.url), lower(?))" if fold_case else "instr(p.url, ?)"  # a path keeps its case: the plain test is faster
    sql = f"SELECT {_ROW_COLUMNS} FROM p AS p WHERE {test} > 0 ORDER BY p.removed, p.posted DESC, p.id DESC"
    try:
        rows, stamp = _read(home_root, lambda conn: conn.execute(sql, (part,)).fetchall())
    except _Unavailable as error:
        return _unavailable(error)
    return IndexResult(True, rows=tuple(_row(row) for row in rows), stamp=stamp)


def title_counts(home_root: Path, query: IndexQuery) -> IndexResult:
    """The count path: ``(title, candidates with that title)`` for ``query``, so the rule runs once per title."""

    built = _sql(query)
    try:
        if built is None:
            _rows, stamp = _read(home_root, lambda _conn: ())
            return IndexResult(True, stamp=stamp)
        where, params = built
        sql = f"SELECT p.title, COUNT(*) {where} GROUP BY p.title"
        rows, stamp = _read(home_root, lambda conn: conn.execute(sql, params).fetchall())
    except _Unavailable as error:
        return _unavailable(error)
    return IndexResult(True, counts=tuple((title, count) for title, count in rows), stamp=stamp)


def status(home_root: Path) -> IndexStatus:
    """Whether a read would be answered now, and what the index holds. Never raises, never builds."""

    try:
        (postings, live, boards), stamp = _read(home_root, _counts)
    except _Unavailable as error:
        return IndexStatus(False, reason=error.reason, detail=error.detail)
    return IndexStatus(True, postings=postings, live=live, boards=boards, stamp=stamp)


__all__ = [
    "IndexFilters",
    "IndexQuery",
    "IndexResult",
    "IndexRow",
    "IndexStatus",
    "SCHEMA_VERSION",
    "candidates",
    "close",
    "copy_counts",
    "is_built",
    "plain_words",
    "rebuild_from_index",
    "refresh",
    "rows_with_url_part",
    "remove_company",
    "search_index_path",
    "status",
    "strict_title_match",
    "title_counts",
    "upsert_company",
    "verify",
    "words_match",
]
