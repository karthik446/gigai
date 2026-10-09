"""Free search (0.1.11.7 FS1): typed titles over EVERY stored posting, newest first. No profile, no rank.

``gigai scout jobs search`` and ``GET /api/search`` are this module. A search
reads the stored company index (every board ``gigai scout sources update``
ever stored, not only the watchlist) and answers one page:

* TITLES: comma separated, each one a role, any of them enough. The STRICT
  rule decides (``search_index.strict_title_match``): the profile's whole-word
  rule AND every typed word in the title, so "senior engineer" lists only
  titles that say Senior (or Sr.). The deny words of the rule still apply
  ("Staff Training Engineer" is not a "Staff Engineer").
* COMPANY and LOCATION words: WHOLE words of the company name or the
  posting's location, case and accents aside (``search_index.words_match``):
  "ai" finds "Example AI", not "Maintain". Not a substring match.
* DEFAULT FILTERS: the default profile's countries, posted window and work
  mode, read from the effective shared config (``postings._shared_config``:
  the default profile never has search settings of its own). The work mode is
  read from the posting's location (the index holds no board field).
  ``show_all`` drops all three.
* US ONLY (0.1.11.8 N1, ``job_copies.place_of``): a switch of its own, by the
  posting's location. It hides a posting only when every place its location
  names is clearly outside the US; "Remote" alone, no location or a place
  Scout cannot read STAYS and its row says ``location_unclear``. ON by default
  for a US setup (the shared config's countries hold the US), OFF otherwise;
  ``us_only`` says either. EXACTLY ONE country rule decides a request: US
  only ON = that rule (whatever the config lists, with or without
  ``show_all``); OFF without ``show_all`` = the config's countries; OFF with
  ``show_all`` = any country. So ``show_all`` drops the window and the work
  mode, and the countries only when US only is off. "Show all N"
  (``total_all``) counts under the same US rule.
* COPIES (0.1.11.8 N2, ``job_copies.copy_key``): the same company, title AND
  description posted more than once (only the location differs: once per
  country) is ONE row. The row is ONE canonical job
  (``canonical_job.pick_canonical``: its US posting when it has one, else the
  earliest posted) and lists every location (``locations``, ``copies``,
  ``members``). A posting with another description, or with none stored, is
  never merged. Collapsed BEFORE the page and the count: 50 rows are 50
  jobs, ``total`` counts jobs, ``offset`` counts jobs; a row stands where its
  canonical job stands in the order. A row's labels are its copies' labels
  together (any copy applied: the row is applied). Only the copies the
  request selects are a row's copies. To collapse, every candidate of the
  search is read (one read; the description digest is in the index), so a
  collapsed page costs what its count costs. ``collapse=False`` lists every
  posting, as before.
* ORDER: newest posted first (published, else first seen), then the URL.
  PAGE: 50 rows, 200 at most, from ``offset``. Removed postings are left out
  unless ``include_removed``.

WHERE THE ROWS COME FROM. ``search_index.candidates`` when the local index
answers; the rule then decides on each candidate. When it does not (never
built, damaged, stale, another schema, busy, no FTS5: any reason) the SCAN
runs: every company file through the same predicates
(``filters.published_too_old``, ``filters.country_match``,
``work_mode.work_mode_fit``). Both give the same rows in the same order. A
search NEVER builds the index: ``gigai scout sources update`` does. On a home
whose index was never built every search is a scan (about 2 to 3 s on 290k
postings; the index answers in milliseconds).

PAGE FIRST, COUNT AFTER. The page does not need the total; counting the
broadest title over every posting costs more than its page. ``count=True``
(``count=1``) adds ``total`` and, when the default filters are on,
``total_all`` (the same search with no default filter: what "Show all N"
says). The scan knows both already, so it always gives them.

LABELS, joined by job identity from the stores as they are: the profiles
whose list holds the posting (the read model's rows, read as stored: nothing
is refreshed), its assessment state when it has one, its application status.
A store that cannot be read labels nothing; it never fails the search.

WRITES NOTHING: no ``last-search.json``, no anchor, no read-model row, no
assessment, no journal entry, no index build. SQLite files are opened to
read (the search index ``query_only``, the pipeline file ``mode=ro``). The
one trace on disk is SQLite's own: any reader of a WAL database maps its
``-shm`` file, whose mtime can move while its bytes stay the same.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING

from . import search_index
from .ats_board_clients import _words
from .filters import DEFAULT_COUNTRY
from .canonical_job import canonical_order
from .job_copies import (
    PLACE_OTHER,
    PLACE_UNCLEAR,
    PLACE_US,
    UNCLEAR_LABEL,
    US_ONLY_RULE,
    copy_key,
    distinct_locations,
    locations_text,
    place_of,
    us_only_default,
)
from .search_index import IndexFilters, IndexQuery, IndexRow, _stamp, strict_title_match, words_match

if TYPE_CHECKING:
    from .company_index import CompanyIndexEntry, IndexedPosting
    from .contracts import FindJobsConfig

SCHEMA_VERSION = "scout-free-search:1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

SOURCE_INDEX = "index"
SOURCE_SCAN = "scan"

NOT_RANKED_TEXT = "Not ranked. Save as a profile to rank."
ANY_SCOPE_TEXT = "any place, any date"
US_ONLY_SCOPE_TEXT = "US only, any date"
WORK_MODE_NOTE = "Work mode is read from the posting's location."

#: The first read of candidates is this many times the rows the page needs: the rule drops some.
_CHUNK_FACTOR = 2
_MIN_CHUNK = 200
#: The index moved between two reads of one page this many times: the scan answers instead.
_RESTARTS = 3
_NOT_ASSESSED = "not_assessed"


class FreeSearchError(ValueError):
    """A search that cannot be answered; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _split(value: Iterable[str] | str | None, separator: str | None) -> tuple[str, ...]:
    """Typed text as its parts (``separator`` ``None``: whitespace), each once, in order."""

    if value is None:
        return ()
    texts = [value] if isinstance(value, str) else list(value)
    parts: list[str] = []
    for text in texts:
        if type(text) is not str:
            raise FreeSearchError("invalid_value", "title, company and location are text")
        parts.extend(" ".join(part.split()) for part in text.split(separator))
    return tuple(dict.fromkeys(part for part in parts if part))


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """One free search. Build it with :meth:`typed` from what a person typed."""

    titles: tuple[str, ...] = ()
    company_words: tuple[str, ...] = ()
    location_words: tuple[str, ...] = ()
    #: Drop the default filters (countries, posted window, work mode).
    show_all: bool = False
    include_removed: bool = False
    limit: int = DEFAULT_LIMIT
    offset: int = 0
    #: Also count (``total``, ``total_all``): a second read on the index path.
    count: bool = False
    #: 0.1.11.8 N1: ``None`` is the setup's default (ON when the shared config's countries hold the US).
    us_only: bool | None = None
    #: 0.1.11.8 N2: the same company and title posted more than once is one row.
    collapse: bool = True

    @classmethod
    def typed(
        cls,
        title: Iterable[str] | str | None = None,
        *,
        company: Iterable[str] | str | None = None,
        location: Iterable[str] | str | None = None,
        show_all: bool = False,
        include_removed: bool = False,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        count: bool = False,
        us_only: bool | None = None,
        collapse: bool | None = None,
    ) -> "SearchRequest":
        """``title``: comma separated titles; ``company`` / ``location``: words. Raises :class:`FreeSearchError`.

        ``collapse=None`` is the default: on.
        """

        request = cls(
            titles=_split(title, ","), company_words=_split(company, None), location_words=_split(location, None),
            show_all=bool(show_all), include_removed=bool(include_removed), limit=limit, offset=offset, count=bool(count),
            us_only=None if us_only is None else bool(us_only), collapse=True if collapse is None else bool(collapse),
        )
        request.check()
        return request

    def check(self) -> None:
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_LIMIT or type(self.offset) is not int or self.offset < 0:
            raise FreeSearchError("invalid_value", f"limit must be 1..{MAX_LIMIT} and offset 0 or more")
        if not (self.titles or self.company_words or self.location_words):
            raise FreeSearchError("invalid_value", "type a title, a company word or a location word")
        for typed in self.titles:
            if "\x00" in typed or not _words(typed):
                raise FreeSearchError("invalid_value", f"a title needs a word the search can match: {typed!r} has none")
        for what, words in (("company", self.company_words), ("location", self.location_words)):
            for typed in words:
                if not search_index.plain_words(typed):
                    raise FreeSearchError("invalid_value", f"a {what} word needs a letter or a digit: {typed!r} has none")


@dataclass(frozen=True, slots=True)
class ProfileLabel:
    profile_id: str
    #: The state the profile's list shows for the posting (the read model's row, as stored).
    state: str


@dataclass(frozen=True, slots=True)
class RowLabels:
    """What the stores hold for one posting, by job identity. Nothing here is computed by the search."""

    job_identity: str | None = None
    profiles: tuple[ProfileLabel, ...] = ()
    #: ``{state, profile_id, assessed_at}`` of the posting's assessment, or ``None``.
    assessment: Mapping[str, object] | None = None
    #: ``{status, since}`` of the latest application event, or ``None``.
    application: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SearchRow:
    #: The row's posting: of several copies, the canonical job (``canonical_job.pick_canonical``).
    posting: IndexRow
    #: The labels of the row: of several copies, theirs together.
    labels: RowLabels = field(default_factory=RowLabels)
    #: 0.1.11.8 N2: every copy the row stands for, the canonical job first (``posting``), each with its job identity.
    copies: tuple[tuple[IndexRow, str | None], ...] = ()


@dataclass(frozen=True, slots=True)
class SearchPage:
    request: SearchRequest
    rows: tuple[SearchRow, ...]
    #: Whether a row follows this page.
    more: bool
    #: ``index`` or ``scan``; ``index_reason`` says why the index did not answer (``None`` when it did).
    source: str
    index_reason: str | None = None
    #: Every row the search matches; ``None`` when it was not counted (the index path without ``count``).
    total: int | None = None
    #: The same search without the default filters; ``None`` when it was not counted.
    total_all: int | None = None
    #: The default filters that were applied (``None``: show all).
    filters: Mapping[str, object] | None = None
    #: ``profile_id -> {label, is_default}`` of the profiles the rows name.
    profiles: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    #: Whether the label stores were read (``False``: no gig here, or a store could not be read).
    labelled: bool = False
    checked_at: str | None = None
    #: 0.1.11.8 N1: whether US only applied, and what the setup's default is.
    us_only: bool = False
    us_only_default: bool = False

    @property
    def scope_text(self) -> str:
        """What the search was limited to, in words: the default filters, or what Show all leaves."""

        if self.filters is not None:
            return str(self.filters["text"])
        return US_ONLY_SCOPE_TEXT if self.us_only else ANY_SCOPE_TEXT

    @property
    def all_scope_text(self) -> str:
        """What "Show all N" would search: any date and work mode, and the US alone while US only is on."""

        return US_ONLY_SCOPE_TEXT if self.us_only else ANY_SCOPE_TEXT

    @property
    def hidden(self) -> int | None:
        """How many rows the default filters left out; ``None`` when not counted."""

        if self.total is None or self.total_all is None:
            return None
        return max(0, self.total_all - self.total)


# ---------------------------------------------------------------------------
# The default filters
# ---------------------------------------------------------------------------


def default_config(home_root: Path, target: Path) -> "FindJobsConfig":
    """The default profile's effective config: the shared ``find-jobs.json`` with the setup's saved work mode. A read."""

    from ..postings import PostingModelError, _shared_config

    try:
        return _shared_config(Path(home_root), Path(target))
    except PostingModelError as exc:
        raise FreeSearchError(
            "config_unavailable",
            "this folder has no readable find-jobs.json, so there are no default filters: finish the Scout setup, "
            "or search every posting with --all (the API: all=1)",
        ) from exc


def _filters_json(config: "FindJobsConfig", filters: IndexFilters) -> dict[str, object]:
    from .filters import DEFAULT_MAX_AGE_DAYS

    if config.published_after is not None:
        window = f"posted since {config.published_after[:10]}"
    else:
        window = f"last {config.max_age_days if config.max_age_days is not None else DEFAULT_MAX_AGE_DAYS} days"
    mode = filters.work_mode
    parts = [mode if mode != "any" else "any work mode"]
    if mode not in ("any", "remote") and filters.area:
        parts[0] = f"{mode} near {filters.area}"
    parts.append(", ".join(filters.countries) if filters.countries else "any country")
    parts.append(window)
    return {
        "work_mode": mode,
        "area": filters.area if mode not in ("any", "remote") else None,
        "countries": list(filters.countries),
        "posted_since": filters.cutoff,
        "text": ", ".join(parts),
        "work_mode_note": WORK_MODE_NOTE,
    }


@dataclass(frozen=True, slots=True)
class _Plan:
    """What one request applies, decided once: the window and work mode, and the ONE country rule."""

    moment: datetime
    #: The default config whose posted window and work mode apply; ``None`` for ``show_all``.
    config: "FindJobsConfig | None"
    #: The countries that decide when US only is off (``()``: any country).
    countries: tuple[str, ...]
    #: The same, as the index applies it (``None``: no filter at all).
    filters: IndexFilters | None
    #: The filters of "Show all N": nothing, or the US alone while US only is on.
    all_filters: IndexFilters | None
    us_only: bool
    us_only_default: bool


def _fits(plan: _Plan, row: object, *, defaults: bool) -> bool:
    """The scan's test of one posting. ``defaults``: the request's country rule, window and work mode; without it,
    what "Show all N" keeps (the US rule while US only is on, else everything)."""

    from .filters import country_match, published_too_old
    from .work_mode import work_mode_fit

    if plan.us_only:
        if place_of(row.location, row.countries) == PLACE_OTHER:  # type: ignore[attr-defined]
            return False
    elif defaults and plan.countries and country_match(row.location, plan.countries, structured_countries=row.countries) is False:  # type: ignore[attr-defined]
        return False
    if not defaults or plan.config is None:
        return True
    return not published_too_old(row, plan.config, now=plan.moment) and work_mode_fit(row, plan.config).passes  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The two paths
# ---------------------------------------------------------------------------


@lru_cache(maxsize=262144)
def _strict(title: str, typed: str) -> bool:
    """``strict_title_match``, remembered by this process: a pure rule, and the page, its count and the next page of
    one search ask it of the same titles (0.1.11.8: a page of jobs reads every candidate of the search)."""

    return strict_title_match(title, typed)


def _rule(titles: Sequence[str]) -> Callable[[str], bool]:
    """Whether a title is any of the typed titles by the strict rule (no titles: every title). Decided once per title."""

    if not titles:
        return lambda _title: True
    decided: dict[str, bool] = {}

    def accepts(title: str) -> bool:
        found = decided.get(title)
        if found is None:
            found = decided[title] = any(_strict(title, typed) for typed in titles)
        return found

    return accepts


def _query(request: SearchRequest, filters: IndexFilters | None) -> IndexQuery:
    return IndexQuery(
        titles=request.titles, strict=True, company_words=request.company_words, location_words=request.location_words,
        filters=filters, include_removed=request.include_removed,
    )


Group = tuple[IndexRow, ...]


def _key(row: IndexRow) -> tuple[str, str, str, str, bool] | None:
    return copy_key(row.board, row.company, row.title, row.content, row.removed)


def _order(row: IndexRow) -> tuple[str, str, str, str]:
    """The order's key (newest first when reversed): posted, then the URL; then the board and the id, so it is total."""

    return (row.posted, row.url, row.board, row.posting_id)


def _collapsed(rows: Iterable[IndexRow]) -> list[Group]:
    """``rows`` as rows of copies: each group with its canonical job first, the groups in the order of their canonical jobs.

    A posting with no stored description is a group of its own (``copy_key`` is ``None``).
    """

    groups: dict[object, list[IndexRow]] = {}
    for row in rows:
        key = _key(row)
        groups.setdefault(key if key is not None else (row.board, row.posting_id), []).append(row)
    found = [
        tuple(canonical_order(group, us=lambda row: row.place == PLACE_US, posted=lambda row: row.posted, posting_id=lambda row: row.posting_id))
        if len(group) > 1 else (group[0],)
        for group in groups.values()
    ]
    found.sort(key=lambda group: _order(group[0]), reverse=True)
    return found


def _index_rows(
    home_root: Path, request: SearchRequest, query: IndexQuery, accepts: Callable[[str], bool], need: int,
) -> tuple[list[Group] | None, str | None]:
    """The first ``need`` rows the rule accepts, from the index; ``(None, reason)`` when it cannot answer.

    With ``request.collapse`` a row is a job, and a job stands where its canonical posting stands, which is known
    only when every copy was seen: EVERY candidate is read, in one read (one index state, no restart).
    """

    if request.collapse:
        found = search_index.candidates(home_root, query, ordered=False)  # the rows are ordered by their canonical jobs, below
        if not found.available:
            return None, found.reason or search_index.DAMAGED
        return _collapsed(row for row in found.rows if accepts(row.title))[:need], None
    for _attempt in range(_RESTARTS):
        rows: list[IndexRow] = []
        stamp: str | None = None
        start, chunk = 0, max(_MIN_CHUNK, need * _CHUNK_FACTOR)
        while True:
            found = search_index.candidates(home_root, query, limit=chunk, offset=start)
            if not found.available:
                return None, found.reason or search_index.DAMAGED
            if stamp is None:
                stamp = found.stamp
            elif found.stamp != stamp:
                break  # the index was written between two reads: the offsets no longer name the same rows; read again
            rows.extend(row for row in found.rows if accepts(row.title))
            if len(rows) >= need or len(found.rows) < chunk:
                return [(row,) for row in rows[:need]], None
            start += chunk
            chunk *= 2
    return None, search_index.STALE


def _index_count(home_root: Path, request: SearchRequest, query: IndexQuery, accepts: Callable[[str], bool]) -> int | None:
    if not request.collapse:
        found = search_index.title_counts(home_root, query)
        if not found.available:
            return None
        return sum(count for title, count in found.counts if accepts(title))
    found = search_index.copy_counts(home_root, query)
    if not found.available:
        return None
    jobs: set[object] = set()
    alone = 0
    for title, board, company, removed, content, count in found.copies:
        if not accepts(title):
            continue
        key = copy_key(board, company, title, content, removed)
        if key is None:
            alone += count  # no stored description: each posting is its own row
        else:
            jobs.add(key)
    return len(jobs) + alone


def _scan(
    home_root: Path, request: SearchRequest, plan: _Plan, accepts: Callable[[str], bool],
) -> tuple[list[Group], int]:
    """Every company file through the same predicates: ``(the rows in order, how many match without the default filters)``."""

    from .company_index import CompanyIndex

    index = CompanyIndex.for_home(home_root)
    places: dict[str, bool] = {}
    fits: dict[tuple[object, ...], tuple[bool, bool, str]] = {}
    rows: list[IndexRow] = []
    unfiltered = 0
    unfiltered_jobs: set[object] = set()
    for ats, slug in index.keys():
        entry = index.read(ats, slug)
        if entry is None:
            continue  # a torn file: the index holds no posting for it either
        company = entry.company if type(entry.company) is str else ""
        if request.company_words and not words_match(company, request.company_words):
            continue
        for posting_id, posting in entry.postings.items():
            if posting.removed and not request.include_removed:
                continue
            if not accepts(posting.title):
                continue
            if request.location_words:
                here = places.get(posting.location)
                if here is None:
                    here = places[posting.location] = words_match(posting.location, request.location_words)
                if not here:
                    continue
            key = (posting.location, posting.countries, posting.published_at)
            fit = fits.get(key)
            if fit is None:
                # The fields the three rules read, as ``index_search._indexed_row`` gives them (no board work mode).
                row = SimpleNamespace(
                    location=posting.location, countries=posting.countries, published_at=posting.published_at, work_mode=None,
                )
                fit = fits[key] = (_fits(plan, row, defaults=False), _fits(plan, row, defaults=True), place_of(posting.location, posting.countries))
            if not fit[0]:
                continue  # outside the country rule that also "Show all N" keeps (US only)
            unfiltered += 1
            content = posting.content_sha256 or None
            if request.collapse:
                job = copy_key(entry.key, company, posting.title, content, bool(posting.removed))
                unfiltered_jobs.add(job if job is not None else (entry.key, posting_id))
            if not fit[1]:
                continue
            published = _stamp(posting.published_at)
            rows.append(IndexRow(
                board=entry.key, company=company, posting_id=posting_id, title=posting.title, location=posting.location,
                url=posting.url, posted=published or _stamp(posting.first_seen) or "", published_at=published,
                first_seen=posting.first_seen, changed_at=posting.changed_at, removed=bool(posting.removed),
                place=fit[2], content=content,
            ))
    if request.collapse:
        return _collapsed(rows), len(unfiltered_jobs)
    # The index's order: posted, then the URL, newest first. (Two postings with one instant AND one URL: by board and id here.)
    rows.sort(key=_order, reverse=True)
    return [(row,) for row in rows], unfiltered


# ---------------------------------------------------------------------------
# Labels: what the stores hold for the page's postings (reads only)
# ---------------------------------------------------------------------------


def _job_identity(url: str) -> str | None:
    from .contracts import FindJobsContractError
    from .job_state import normalize_job_identity

    try:
        return normalize_job_identity(url)
    except FindJobsContractError:
        return None


def find_posting(home_root: Path, identity: str) -> "tuple[CompanyIndexEntry, IndexedPosting] | None":
    """``(entry, posting)`` of the company-index posting whose address is the job ``identity``, or ``None`` (FB1).

    The by-address read for a posting no profile holds. Read only, no model, no
    request, nothing created. The search index narrows by the address's path (or
    host, for a bare one) and the exact test is ``normalize_job_identity`` of the
    stored URL, so a tracking parameter or a trailing slash folds as it does
    everywhere else. The posting itself is read from its company file (the index
    keeps no removal stamp). An index that cannot answer (absent, stale, damaged)
    is a scan of the company files by the same test; an index that answers and
    holds no such address is ``None`` (the scan runs on that miss path only).
    A live posting wins over a removed one with the same address.
    """

    from urllib.parse import urlsplit

    from .company_index import CompanyIndex

    parts = urlsplit(identity)
    bare = parts.path in ("", "/")
    part = (parts.hostname or "") if bare else parts.path
    index = CompanyIndex.for_home(home_root)
    found = search_index.rows_with_url_part(home_root, part, fold_case=bare)
    if found.available:
        for row in found.rows:
            if _job_identity(row.url) != identity:
                continue
            ats, _, slug = row.board.partition(":")
            entry = index.read(ats, slug)
            posting = entry.postings.get(row.posting_id) if entry is not None else None
            if entry is not None and posting is not None and _job_identity(posting.url) == identity:
                return entry, posting
        return None
    best: tuple[CompanyIndexEntry, IndexedPosting] | None = None
    for ats, slug in index.keys():
        entry = index.read(ats, slug)
        if entry is None:
            continue
        for posting in entry.postings.values():
            if _job_identity(posting.url) == identity:
                if not posting.removed:
                    return entry, posting
                best = best or (entry, posting)
    return best


#: 0.1.11.8: path segments of a job page's address that are the system's route or an action on the posting
#: (``/jobs/<id>``, ``/j/<code>/apply``, ``/o/<slug>/c/new``, ``/en/postings/<id>``), never the posting's own name.
_ROUTE_SEGMENTS = frozenset({"jobs", "job", "j", "o", "p", "postings", "apply", "application", "c", "new", "en"})


def find_posting_by_address(home_root: Path, identity: str) -> "tuple[CompanyIndexEntry, IndexedPosting] | None":
    """``find_posting``, and when no stored URL is ``identity``: the posting the address is the PUBLIC JOB PAGE of.

    0.1.11.8 (`gigai scout jobs assess <URL>`): the stored URL is the board feed's own spelling, and a system's
    public page can be another address of the same posting: Workable stores ``apply.workable.com/j/<code>`` and
    shows ``apply.workable.com/<board>/j/<code>/``; Recruitee stores the company's own careers domain and also
    serves ``<board>.recruitee.com/o/<slug>``; Pinpoint stores ``/en/postings/<id>``; Greenhouse has two board
    hosts; an apply page is one segment more. So: the address names a board (``parse_board_url``) the index holds,
    and exactly ONE of that board's postings has its id, or the last segment of its stored URL, among the address's
    own path segments (the board token and the route words aside). Two candidates is no answer. A live posting wins
    over a removed one. Read only: one company file, no request. The identity to use from then on is the stored
    URL's (``normalize_job_identity(posting.url)``).
    """

    from urllib.parse import urlsplit

    from .company_index import CompanyIndex
    from .contracts import parse_board_url

    found = find_posting(home_root, identity)
    if found is not None:
        return found
    where = parse_board_url(identity)
    if where is None:
        return None
    ats, token = where
    names = {part.casefold() for part in urlsplit(identity).path.split("/") if part} - _ROUTE_SEGMENTS - {token.casefold()}
    if not names:
        return None
    index = CompanyIndex.for_home(home_root)
    try:
        entry = index.read(ats, token) or (index.read(ats, token.lower()) if token != token.lower() else None)
    except ValueError:
        return None  # not a board the index can name
    if entry is None:
        return None

    def named(posting: "IndexedPosting") -> bool:
        if posting.posting_id.casefold() in names:
            return True
        last = next((part for part in reversed(urlsplit(posting.url).path.split("/")) if part), "")
        return bool(last) and last.casefold() in names and _job_identity(posting.url) is not None

    matches = [posting for posting in entry.postings.values() if named(posting)]
    live = [posting for posting in matches if not posting.removed]
    chosen = live or matches
    return (entry, chosen[0]) if len(chosen) == 1 else None


def _read_model_rows(home_root: Path, target: Path, jobs: Sequence[str]) -> dict[str, list[tuple[str, str, str | None]]]:
    """``job -> [(profile_id, state, assessed_at)]``, best profile first, from the read model AS STORED (``mode=ro``)."""

    from ..pipeline.store import pipeline_path

    path = pipeline_path(home_root, target)
    if not path.is_file():
        return {}
    found: dict[str, list[tuple[str, str, str | None]]] = {}
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    try:
        for start in range(0, len(jobs), 500):
            chunk = list(jobs[start:start + 500])
            for job, profile_id, state, assessed_at in conn.execute(
                f"SELECT job, profile_id, state, assessed_at FROM posting WHERE job IN ({','.join('?' * len(chunk))}) "
                "ORDER BY job, match_rank, profile_id",
                chunk,
            ):
                found.setdefault(job, []).append((profile_id, state, assessed_at))
    finally:
        conn.close()
    return found


def _stored_assessments(home_root: Path, target: Path, jobs: Sequence[str]) -> dict[str, dict[str, object]]:
    """``job -> the job's stored assessment`` of each job no profile's list holds as assessed (assessed by its address).

    ONE project lookup and ONE listing of the store's folders for all the jobs; then one file per job, read only when
    it exists. Jobs with no assessment are left out (a pasted resume's is not the job's; an unassessed one is skipped).
    """

    if not jobs:
        return {}
    from ...canonical import digest_imported_bytes
    from ..job_store_layout import JOB_FOLDER, is_profile_folder
    from ..postings import stamp
    from ..quick_assess import _read_stored, quick_assess_path
    from .job_state import derive_job_state, quick_assessment_fact

    store = quick_assess_path(home_root, target, None, jobs[0]).parent.parent  # raises when the folder is not bound: labels read nothing
    try:
        # 0.1.11.9: a job's ONE assessment is in the store's per-job folder. A role's folder still holds it only on a
        # home whose stores were not migrated, for a job nothing has written since.
        roles = sorted(folder for folder in store.iterdir() if folder.is_dir() and is_profile_folder(folder.name))
    except OSError:
        return {}
    found: dict[str, dict[str, object]] = {}
    for job in jobs:
        name = digest_imported_bytes(job.encode("utf-8")).removeprefix("sha256:") + ".json"
        path = store / JOB_FOLDER / name
        if not path.is_file() and any((folder / name).is_file() for folder in roles):
            from ..job_store_migration import role_record

            path = role_record(store, job, home_root=home_root, target=target) or path
        item = _read_stored(path)
        if item is None or item.job.job_identity != job or not item.resume.profile_id:
            continue
        state = derive_job_state(assessments=(quick_assessment_fact(item),)).state
        if state == _NOT_ASSESSED:
            continue
        found[job] = {"state": state, "profile_id": str(item.resume.profile_id), "assessed_at": stamp(item.updated_at or item.created_at)}
    return found


class _ProfilesAhead:
    """The profile list read on a thread that starts as soon as a row of the page is known to be in a profile's list.

    Its mount probe and git reads then overlap the workpad, events and assessment reads of the other labels. The
    caller always ``wait()``s (a thread left at exit would die inside its probe); ``result()`` raises what the read
    raised (the labels turn that into "no label").
    """

    def __init__(self, home_root: Path, target: Path) -> None:
        self._outcome: tuple[object, BaseException | None] = (None, None)
        self._thread = threading.Thread(target=self._run, args=(home_root, target), name="free-search-profiles", daemon=True)
        self._thread.start()

    def _run(self, home_root: Path, target: Path) -> None:
        try:
            from ...workpad import committed_read_cache, resolve_workpad
            from .. import profile_records

            with committed_read_cache():
                resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
                records = profile_records.list_profiles(resolved)
                self._outcome = ((records, profile_records.default_profile(records)), None)
        except BaseException as exc:  # noqa: BLE001 - handed to the caller of result(), which decides what it means
            self._outcome = (None, exc)

    def wait(self) -> None:
        self._thread.join()

    def result(self):
        self.wait()
        value, error = self._outcome
        if error is not None:
            raise error
        return value


def _labels(home_root: Path, target: Path | None, rows: Sequence[IndexRow]) -> tuple[list[RowLabels], dict[str, dict[str, object]], bool]:
    """Each row's labels, the profiles they name, and whether the stores were read. Never raises, never writes."""

    identities = [_job_identity(row.url) for row in rows]
    bare = [RowLabels(job_identity=identity) for identity in identities]
    if target is None or not rows:
        return bare, {}, False
    from ...workpad import committed_read_cache, resolve_workpad
    from .. import profile_records
    from ..posting_search import _applications

    jobs = [identity for identity in dict.fromkeys(identities) if identity is not None]
    ahead: _ProfilesAhead | None = None
    try:
        held = _read_model_rows(home_root, target, jobs)
        if held:
            ahead = _ProfilesAhead(home_root, target)  # the profile list is read while the rest of the labels are
        with committed_read_cache():
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
            applications = _applications(resolved)
            named = {profile_id for group in held.values() for profile_id, _state, _at in group}
            unassessed = [job for job in jobs if next((1 for _p, state, _at in held.get(job, ()) if state != _NOT_ASSESSED), None) is None]
            stored = _stored_assessments(home_root, target, unassessed)
            labelled: list[RowLabels] = []
            for identity in identities:
                if identity is None:
                    labelled.append(RowLabels())
                    continue
                group = held.get(identity, [])
                assessed = next(((p, s, at) for p, s, at in group if s != _NOT_ASSESSED), None)
                assessment: dict[str, object] | None
                if assessed is not None:
                    assessment = {"state": assessed[1], "profile_id": assessed[0], "assessed_at": assessed[2]}
                else:
                    assessment = stored.get(identity)
                    if assessment is not None:
                        named.add(str(assessment["profile_id"]))
                applied = applications.get(identity)
                labelled.append(RowLabels(
                    job_identity=identity,
                    profiles=tuple(ProfileLabel(profile_id, state) for profile_id, state, _at in group),
                    assessment=assessment,
                    application=None if applied is None else dict(applied),
                ))
            profiles: dict[str, dict[str, object]] = {}
            if named:
                if ahead is not None:
                    records, default = ahead.result()
                else:
                    records = profile_records.list_profiles(resolved)
                    default = profile_records.default_profile(records)
                for record in records:
                    if record.profile_id in named:
                        profiles[record.profile_id] = {
                            "label": record.label, "is_default": default is not None and record.profile_id == default.profile_id,
                        }
    except Exception:  # noqa: BLE001 - labels are a display read: a store that cannot be read labels nothing
        return bare, {}, False
    finally:
        if ahead is not None:
            ahead.wait()  # a thread left running at exit would die inside its mount probe and leave the probe file behind
    return labelled, profiles, True


def _together(labels: Sequence[RowLabels]) -> RowLabels:
    """The labels of a row of copies: every profile whose list holds a copy, the first copy's assessment and application."""

    if len(labels) == 1:
        return labels[0]
    profiles: dict[str, ProfileLabel] = {}
    for item in labels:
        for profile in item.profiles:
            profiles.setdefault(profile.profile_id, profile)
    return RowLabels(
        job_identity=labels[0].job_identity,
        profiles=tuple(profiles.values()),
        assessment=next((item.assessment for item in labels if item.assessment is not None), None),
        application=next((item.application for item in labels if item.application is not None), None),
    )


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


def _prepare(home_root: Path, request: SearchRequest, target: Path | None, now: datetime | None) -> _Plan:
    from dataclasses import replace

    from .contracts import FindJobsContractError

    request.check()
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    config: "FindJobsConfig | None" = None
    default = False
    if not request.show_all:
        if target is None:
            raise FreeSearchError("config_unavailable", "the default filters need the Scout folder; search every posting with --all (the API: all=1)")
        config = default_config(home_root, target)
        default = us_only_default(config.countries)
    elif target is not None:
        default = _us_default_or_off(home_root, target)
    us_only = default if request.us_only is None else request.us_only
    us = IndexFilters(countries=(DEFAULT_COUNTRY,), us_only=True)
    if config is None:
        filters = us if us_only else None
    else:
        try:
            filters = IndexFilters.from_config(config, now=moment)
        except FindJobsContractError as exc:
            raise FreeSearchError("config_unavailable", str(exc)) from exc
        if us_only:
            filters = replace(filters, countries=(DEFAULT_COUNTRY,), us_only=True)
    return _Plan(
        moment=moment, config=config, countries=() if filters is None else filters.countries, filters=filters,
        all_filters=us if us_only else None, us_only=us_only, us_only_default=default,
    )


def _us_default_or_off(home_root: Path, target: Path) -> bool:
    """The setup's US-only default for a ``show_all`` search, which needs no config: no readable setup is "off"."""

    try:
        return us_only_default(default_config(home_root, target).countries)
    except (FreeSearchError, ValueError, OSError):
        return False


def _index_totals(home_root: Path, request: SearchRequest, plan: _Plan, accepts: Callable[[str], bool]) -> tuple[int, int] | None:
    """``(total, total_all)`` from the index's count path; ``None`` when it cannot answer."""

    total = _index_count(home_root, request, _query(request, plan.filters), accepts)
    if total is None:
        return None
    total_all = total if plan.filters == plan.all_filters else _index_count(home_root, request, _query(request, plan.all_filters), accepts)
    return None if total_all is None else (total, total_all)


def count(home_root: Path, request: SearchRequest, *, target: Path | None = None, now: datetime | None = None) -> tuple[int, int]:
    """``(total, total_all)`` of ``request``, with no page: what a caller asks AFTER it showed the page.

    ``total_all`` is the same search without the default filters (equal to
    ``total`` for a ``show_all`` search); the US rule stays in it while US
    only is on. With ``collapse`` both count jobs, not copies. From the
    index's count path, else from the scan. Pass the page's ``now`` so both
    judge one posted window. Writes nothing. Raises what :func:`search` raises.
    """

    home_root = Path(home_root)
    return _count_prepared(home_root, request, _prepare(home_root, request, None if target is None else Path(target), now))


def _count_prepared(home_root: Path, request: SearchRequest, plan: _Plan) -> tuple[int, int]:
    accepts = _rule(request.titles)
    totals = _index_totals(home_root, request, plan, accepts)
    if totals is None:
        every, unfiltered = _scan(home_root, request, plan, accepts)
        totals = (len(every), unfiltered)
    return totals


def search(home_root: Path, request: SearchRequest, *, target: Path | None = None, now: datetime | None = None) -> SearchPage:
    """One page of the free search (see :func:`_search`). Writes nothing."""

    return _search(home_root, request, target, now)[0]


def _search(home_root: Path, request: SearchRequest, target: Path | None, now: datetime | None) -> tuple[SearchPage, _Plan]:
    """One page of the free search. See the module docstring. Writes nothing.

    ``target`` is the Scout folder: the default filters are read from its
    ``find-jobs.json`` (so a search that is not ``show_all`` needs it) and
    the labels from its stores. ``now`` is injectable for tests.

    Raises :class:`FreeSearchError`: ``invalid_value``, ``config_unavailable``.
    """

    from ..postings import stamp

    home_root = Path(home_root)
    target = None if target is None else Path(target)
    plan = _prepare(home_root, request, target, now)
    accepts = _rule(request.titles)
    need = request.offset + request.limit + 1

    totals: tuple[int, int] | None = None
    found, reason = _index_rows(home_root, request, _query(request, plan.filters), accepts, need)
    if found is not None and request.count:
        totals = _index_totals(home_root, request, plan, accepts)
        if totals is None:
            found, reason = None, search_index.STALE  # the index went away between the page and the count: one answer, from the scan
    if found is None:
        source = SOURCE_SCAN
        every, unfiltered = _scan(home_root, request, plan, accepts)
        found, totals = every[:need], (len(every), unfiltered)
    else:
        source = SOURCE_INDEX
    page = found[request.offset:request.offset + request.limit]
    # Every copy of the page's rows is labelled in one read; a row's labels are its copies' together.
    labels, profiles, labelled = _labels(home_root, target, [row for group in page for row in group])
    rows: list[SearchRow] = []
    at = 0
    for group in page:
        mine = labels[at:at + len(group)]
        at += len(group)
        rows.append(SearchRow(group[0], _together(mine), tuple((row, label.job_identity) for row, label in zip(group, mine, strict=True))))

    result = SearchPage(
        request=request,
        rows=tuple(rows),
        more=len(found) > request.offset + request.limit,
        source=source,
        index_reason=reason,
        total=None if totals is None else totals[0],
        total_all=None if totals is None else totals[1],
        filters=None if plan.config is None or plan.filters is None else _filters_json(plan.config, plan.filters),
        profiles=profiles,
        labelled=labelled,
        checked_at=stamp(plan.moment),
        us_only=plan.us_only,
        us_only_default=plan.us_only_default,
    )
    return result, plan


# ---------------------------------------------------------------------------
# What a caller prints
# ---------------------------------------------------------------------------


def _typed_text(request: SearchRequest) -> str:
    parts = [", ".join(f'"{typed}"' for typed in request.titles)] if request.titles else []
    if request.company_words:
        parts.append("company " + " ".join(request.company_words))
    if request.location_words:
        parts.append("location " + " ".join(request.location_words))
    return ", ".join(parts)


def footer_lines(page: SearchPage) -> list[str]:
    """The sentences under the list: not ranked, and what the default filters hid (when it was counted)."""

    lines = [NOT_RANKED_TEXT]
    hidden = page.hidden
    if page.filters is not None and hidden:
        lines.append(f"Show all {page.total_all:,} ({page.all_scope_text}): --all")
    return lines


def to_json(page: SearchPage) -> dict[str, object]:
    """The ``scout-free-search:1`` response. Stable keys; ``counts.total`` / ``total_all`` are ``null`` when not counted."""

    from ..data_labels import ENVELOPE_KEY, PUBLIC_UNTRUSTED, UNTRUSTED_TEXT_RULE, labels_envelope
    from .company_names import slug_display_name

    request = page.request
    rows = []
    for row in page.rows:
        posting, labels = row.posting, row.labels
        copies = row.copies or ((posting, labels.job_identity),)
        places = distinct_locations(copy.location for copy, _identity in copies)
        slug = posting.board.partition(":")[2]
        name = slug_display_name(posting.company or slug)
        rows.append({
            "job_identity": labels.job_identity,
            "job_url": posting.url,
            # As every response names a company (``company_names``): the name, the board token, the name again.
            "company": name,
            "company_slug": slug or None,
            "company_name": name,
            "company_key": posting.board,
            "title": posting.title,
            "location": posting.location,
            "posted": posting.posted or None,
            "published_at": posting.published_at,
            "first_seen": posting.first_seen,
            "removed": posting.removed,
            "profiles": [{"profile_id": item.profile_id, "state": item.state} for item in labels.profiles],
            "assessment": None if labels.assessment is None else dict(labels.assessment),
            "application": None if labels.application is None else dict(labels.application),
            # 0.1.11.8 N1 (additive): Scout cannot tell where the posting is ("Remote" alone, no location, an unknown place).
            "location_unclear": posting.place == PLACE_UNCLEAR,
            # 0.1.11.8 N2 (additive): the copies this row stands for (1: no other copy), their locations, and each one.
            # The row itself (job_identity, job_url, location, the dates) is the canonical job, the first member.
            "copies": len(copies),
            "locations": places,
            "locations_text": locations_text(places),
            "members": [
                {"job_identity": identity, "job_url": copy.url, "location": copy.location, "posted": copy.posted or None}
                for copy, identity in copies
            ],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": page.checked_at,
        "query": {
            "titles": list(request.titles), "company": list(request.company_words), "location": list(request.location_words),
            "all": request.show_all, "include_removed": request.include_removed, "limit": request.limit, "offset": request.offset,
            "count": request.count, "us_only": page.us_only, "collapse": request.collapse,
        },
        "filters": None if page.filters is None else dict(page.filters),
        # 0.1.11.8 N1 (additive): the switch as it applied, the setup's default, the rule in a sentence, the scope in words.
        "us_only": {"on": page.us_only, "default": page.us_only_default, "rule": US_ONLY_RULE},
        "scope_text": page.scope_text,
        "all_scope_text": page.all_scope_text,
        "source": page.source,
        "index": {"used": page.source == SOURCE_INDEX, "reason": page.index_reason},
        "counts": {"shown": len(page.rows), "more": page.more, "total": page.total, "total_all": page.total_all, "hidden": page.hidden},
        "ranked": False,
        "order": "newest_posted",
        "postings": {
            ENVELOPE_KEY: labels_envelope({
                "/rows/*/title": PUBLIC_UNTRUSTED, "/rows/*/company": PUBLIC_UNTRUSTED, "/rows/*/company_slug": PUBLIC_UNTRUSTED,
                "/rows/*/company_name": PUBLIC_UNTRUSTED, "/rows/*/location": PUBLIC_UNTRUSTED,
                "/rows/*/locations/*": PUBLIC_UNTRUSTED, "/rows/*/locations_text": PUBLIC_UNTRUSTED,
                "/rows/*/members/*/location": PUBLIC_UNTRUSTED,
            }),
            "rule": UNTRUSTED_TEXT_RULE,
            "rows": rows,
        },
        "profiles": [
            {"profile_id": profile_id, "label": item["label"], "is_default": item["is_default"]}
            for profile_id, item in sorted(page.profiles.items(), key=lambda pair: (not pair[1]["is_default"], str(pair[1]["label"]).casefold(), pair[0]))
        ],
        "labels_read": page.labelled,
        "footer": footer_lines(page),
    }


def _row_tags(row: Mapping[str, object], names: Mapping[str, str], us_only: bool = False) -> str:
    tags: list[str] = []
    held = [str(names.get(item["profile_id"], item["profile_id"])) for item in row["profiles"]]  # type: ignore[index,union-attr]
    if held:
        tags.append("in: " + ", ".join(held))
    assessment = row.get("assessment")
    if isinstance(assessment, Mapping):
        tags.append("assessed: " + str(assessment["state"]).replace("_", " "))
    application = row.get("application")
    if isinstance(application, Mapping):
        tags.append(str(application["status"]).replace("_", " "))
    if row.get("removed"):
        tags.append("removed")
    copies = row.get("copies")
    if type(copies) is int and copies > 1:
        tags.append(f"{copies} copies")
    if row.get("location_unclear") and us_only:
        tags.append(UNCLEAR_LABEL)
    return "".join(f" [{tag}]" for tag in tags)


def render_page(response: Mapping[str, object]) -> str:
    """The page as the terminal shows it: one line saying what was searched, then one line per posting. No total."""

    query, counts, filters = response["query"], response["counts"], response["filters"]
    assert isinstance(query, Mapping) and isinstance(counts, Mapping)
    request = SearchRequest(
        titles=tuple(query["titles"]), company_words=tuple(query["company"]), location_words=tuple(query["location"]),  # type: ignore[arg-type]
    )
    scope = str(response.get("scope_text") or (filters["text"] if isinstance(filters, Mapping) else ANY_SCOPE_TEXT))
    what = f"{_typed_text(request)} ({scope})"
    lines: list[str] = []
    if response["source"] == SOURCE_SCAN:
        index = response["index"]
        reason = index.get("reason") if isinstance(index, Mapping) else None
        lines.append(
            f"The search index did not answer ({reason}): every company file was read instead, which is slower. "
            "`gigai scout sources update` builds the index."
        )
    listing = response["postings"]
    assert isinstance(listing, Mapping)
    rows = listing["rows"]
    assert isinstance(rows, list)
    if not rows:
        offset = int(query["offset"])  # type: ignore[call-overload]
        lines.append(f"No stored posting matches {what}." if not offset else f"No more postings match {what} from row {offset + 1}.")
        return "\n".join(lines)
    first = int(query["offset"]) + 1  # type: ignore[call-overload]
    lines.append(f"Showing {first}-{first + len(rows) - 1}, newest first: {what}.")
    names = {str(item["profile_id"]): str(item["label"]) for item in response["profiles"]}  # type: ignore[union-attr]
    switch = response.get("us_only")
    us_only = isinstance(switch, Mapping) and switch.get("on") is True  # the label is said where US only kept the row
    width = min(28, max(len(str(row["company"])) for row in rows))
    for row in rows:
        day = str(row["posted"] or "")[:10] or "no date   "
        where = row.get("locations_text") or row["location"]
        place = f"  ({where})" if where else ""
        lines.append(f"  {day}  {str(row['company']):<{width}}  {row['title']}{place}{_row_tags(row, names, us_only)}")
        lines.append(f"      {row['job_url']}")
    return "\n".join(lines)


def render_total(response: Mapping[str, object]) -> str:
    """The lines after the page: the total (when counted), "Not ranked", and what the default filters hid."""

    counts, query = response["counts"], response["query"]
    assert isinstance(counts, Mapping) and isinstance(query, Mapping)
    lines: list[str] = []
    total = counts.get("total")
    if type(total) is int and total:
        lines.append(f"{total:,} posting{'' if total == 1 else 's'} match." + (" More: --offset " + str(int(query["offset"]) + int(query["limit"])) if counts.get("more") else ""))  # type: ignore[call-overload]
    footer = response["footer"]
    assert isinstance(footer, list)
    if type(total) is int and not total:
        footer = [line for line in footer if line != NOT_RANKED_TEXT]  # nothing is listed: nothing to rank
    lines.extend(str(line) for line in footer)
    if response.get("filters") is not None:
        lines.append(WORK_MODE_NOTE)
    return "\n".join(lines)


def answer_lines(
    home_root: Path, request: SearchRequest, *, target: Path | None, as_json: bool,
    redact: Callable[[dict[str, object]], dict[str, object]], now: datetime | None = None,
) -> Iterable[str]:
    """What ``gigai scout jobs search`` prints, a piece at a time: the page FIRST, the total after it.

    ``as_json``: one line, the whole response (counted when ``request.count``).
    Text: the page as soon as it is read, then the count (a second read on the
    index path; the scan knows it already). ``redact`` is the outbound check
    every response an agent reads goes through.
    """

    import json
    from dataclasses import replace

    moment = now or datetime.now(UTC)
    page, plan = _search(home_root, request, None if target is None else Path(target), moment)
    response = redact(to_json(page))
    if as_json:
        yield json.dumps(response, sort_keys=True, separators=(",", ":"))
        return
    yield render_page(response)
    if page.total is None:
        total, total_all = _count_prepared(Path(home_root), request, plan)
        response = redact(to_json(replace(page, total=total, total_all=total_all)))
    yield render_total(response)


__all__ = [
    "find_posting",
    "DEFAULT_LIMIT",
    "FreeSearchError",
    "MAX_LIMIT",
    "NOT_RANKED_TEXT",
    "ProfileLabel",
    "RowLabels",
    "SCHEMA_VERSION",
    "SOURCE_INDEX",
    "SOURCE_SCAN",
    "SearchPage",
    "SearchRequest",
    "SearchRow",
    "answer_lines",
    "count",
    "default_config",
    "footer_lines",
    "render_page",
    "render_total",
    "search",
    "to_json",
]
