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
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import TYPE_CHECKING

from . import search_index
from .ats_board_clients import _words
from .company_index import CompanyIndex
from .filters import DEFAULT_MAX_AGE_DAYS, country_match, published_too_old
from .search_index import IndexFilters, IndexQuery, IndexRow, _stamp, strict_title_match, words_match
from .work_mode import work_mode_fit

if TYPE_CHECKING:
    from .contracts import FindJobsConfig

SCHEMA_VERSION = "scout-free-search:1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

SOURCE_INDEX = "index"
SOURCE_SCAN = "scan"

NOT_RANKED_TEXT = "Not ranked. Save as a profile to rank."
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
    ) -> "SearchRequest":
        """``title``: comma separated titles; ``company`` / ``location``: words. Raises :class:`FreeSearchError`."""

        request = cls(
            titles=_split(title, ","), company_words=_split(company, None), location_words=_split(location, None),
            show_all=bool(show_all), include_removed=bool(include_removed), limit=limit, offset=offset, count=bool(count),
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
    posting: IndexRow
    labels: RowLabels = field(default_factory=RowLabels)


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


# ---------------------------------------------------------------------------
# The two paths
# ---------------------------------------------------------------------------


def _rule(titles: Sequence[str]) -> Callable[[str], bool]:
    """Whether a title is any of the typed titles by the strict rule (no titles: every title). Decided once per title."""

    if not titles:
        return lambda _title: True
    decided: dict[str, bool] = {}

    def accepts(title: str) -> bool:
        found = decided.get(title)
        if found is None:
            found = decided[title] = any(strict_title_match(title, typed) for typed in titles)
        return found

    return accepts


def _query(request: SearchRequest, filters: IndexFilters | None) -> IndexQuery:
    return IndexQuery(
        titles=request.titles, strict=True, company_words=request.company_words, location_words=request.location_words,
        filters=filters, include_removed=request.include_removed,
    )


def _index_rows(home_root: Path, query: IndexQuery, accepts: Callable[[str], bool], need: int) -> tuple[list[IndexRow] | None, str | None]:
    """The first ``need`` rows the rule accepts, from the index; ``(None, reason)`` when it cannot answer."""

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
                return rows[:need], None
            start += chunk
            chunk *= 2
    return None, search_index.STALE


def _index_count(home_root: Path, query: IndexQuery, accepts: Callable[[str], bool]) -> int | None:
    found = search_index.title_counts(home_root, query)
    if not found.available:
        return None
    return sum(count for title, count in found.counts if accepts(title))


def _scan(
    home_root: Path, request: SearchRequest, config: "FindJobsConfig | None", accepts: Callable[[str], bool], now: datetime,
) -> tuple[list[IndexRow], int]:
    """Every company file through the same predicates: ``(the rows in order, how many match without the default filters)``."""

    index = CompanyIndex.for_home(home_root)
    places: dict[str, bool] = {}
    fits: dict[tuple[object, ...], bool] = {}
    rows: list[IndexRow] = []
    unfiltered = 0
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
            unfiltered += 1
            if config is not None:
                key = (posting.location, posting.countries, posting.published_at)
                fit = fits.get(key)
                if fit is None:
                    # The fields the three rules read, as ``index_search._indexed_row`` gives them (no board work mode).
                    row = SimpleNamespace(
                        location=posting.location, countries=posting.countries, published_at=posting.published_at, work_mode=None,
                    )
                    fit = fits[key] = not (
                        published_too_old(row, config, now=now)  # type: ignore[arg-type]
                        or (bool(config.countries) and country_match(row.location, config.countries, structured_countries=row.countries) is False)
                        or not work_mode_fit(row, config).passes  # type: ignore[arg-type]
                    )
                if not fit:
                    continue
            published = _stamp(posting.published_at)
            rows.append(IndexRow(
                board=entry.key, company=company, posting_id=posting_id, title=posting.title, location=posting.location,
                url=posting.url, posted=published or _stamp(posting.first_seen) or "", published_at=published,
                first_seen=posting.first_seen, changed_at=posting.changed_at, removed=bool(posting.removed),
            ))
    # The index's order: posted, then the URL, newest first. (Two postings with one instant AND one URL: by board and id here.)
    rows.sort(key=lambda row: (row.posted, row.url, row.board, row.posting_id), reverse=True)
    return rows, unfiltered


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


def _stored_assessment(home_root: Path, target: Path, job: str) -> dict[str, object] | None:
    """The newest stored assessment of a job no profile's list holds as assessed (assessed by its address)."""

    from ..postings import stamp
    from .api.agent_routes import job_quick_assessments
    from .job_state import derive_job_state, quick_assessment_fact

    for item in job_quick_assessments(home_root, target, job):
        profile_id = item.resume.profile_id
        if not profile_id:
            continue  # a pasted resume: not a profile's assessment
        state = derive_job_state(assessments=(quick_assessment_fact(item),)).state
        if state == _NOT_ASSESSED:
            continue
        return {"state": state, "profile_id": str(profile_id), "assessed_at": stamp(item.updated_at or item.created_at)}
    return None


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
    try:
        with committed_read_cache():
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
            held = _read_model_rows(home_root, target, jobs)
            applications = _applications(resolved)
            named = {profile_id for group in held.values() for profile_id, _state, _at in group}
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
                    assessment = _stored_assessment(home_root, target, identity)
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
                records = profile_records.list_profiles(resolved)
                default = profile_records.default_profile(records)
                for record in records:
                    if record.profile_id in named:
                        profiles[record.profile_id] = {
                            "label": record.label, "is_default": default is not None and record.profile_id == default.profile_id,
                        }
    except Exception:  # noqa: BLE001 - labels are a display read: a store that cannot be read labels nothing
        return bare, {}, False
    return labelled, profiles, True


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


def _prepare(
    home_root: Path, request: SearchRequest, target: Path | None, now: datetime | None,
) -> tuple[datetime, "FindJobsConfig | None", IndexFilters | None]:
    from .contracts import FindJobsContractError

    request.check()
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    if request.show_all:
        return moment, None, None
    if target is None:
        raise FreeSearchError("config_unavailable", "the default filters need the Scout folder; search every posting with --all (the API: all=1)")
    config = default_config(home_root, target)
    try:
        return moment, config, IndexFilters.from_config(config, now=moment)
    except FindJobsContractError as exc:
        raise FreeSearchError("config_unavailable", str(exc)) from exc


def _index_totals(home_root: Path, request: SearchRequest, filters: IndexFilters | None, accepts: Callable[[str], bool]) -> tuple[int, int] | None:
    """``(total, total_all)`` from the index's count path; ``None`` when it cannot answer."""

    total = _index_count(home_root, _query(request, filters), accepts)
    if total is None:
        return None
    total_all = total if filters is None else _index_count(home_root, _query(request, None), accepts)
    return None if total_all is None else (total, total_all)


def count(home_root: Path, request: SearchRequest, *, target: Path | None = None, now: datetime | None = None) -> tuple[int, int]:
    """``(total, total_all)`` of ``request``, with no page: what a caller asks AFTER it showed the page.

    ``total_all`` is the same search without the default filters (equal to
    ``total`` for a ``show_all`` search). From the index's count path, else
    from the scan. Pass the page's ``now`` so both judge one posted window.
    Writes nothing. Raises what :func:`search` raises.
    """

    home_root = Path(home_root)
    moment, config, filters = _prepare(home_root, request, None if target is None else Path(target), now)
    accepts = _rule(request.titles)
    totals = _index_totals(home_root, request, filters, accepts)
    if totals is None:
        every, unfiltered = _scan(home_root, request, config, accepts, moment)
        totals = (len(every), unfiltered)
    return totals


def search(home_root: Path, request: SearchRequest, *, target: Path | None = None, now: datetime | None = None) -> SearchPage:
    """One page of the free search. See the module docstring. Writes nothing.

    ``target`` is the Scout folder: the default filters are read from its
    ``find-jobs.json`` (so a search that is not ``show_all`` needs it) and
    the labels from its stores. ``now`` is injectable for tests.

    Raises :class:`FreeSearchError`: ``invalid_value``, ``config_unavailable``.
    """

    from ..postings import stamp

    home_root = Path(home_root)
    target = None if target is None else Path(target)
    moment, config, filters = _prepare(home_root, request, target, now)
    accepts = _rule(request.titles)
    need = request.offset + request.limit + 1

    totals: tuple[int, int] | None = None
    found, reason = _index_rows(home_root, _query(request, filters), accepts, need)
    if found is not None and request.count:
        totals = _index_totals(home_root, request, filters, accepts)
        if totals is None:
            found, reason = None, search_index.STALE  # the index went away between the page and the count: one answer, from the scan
    if found is None:
        source = SOURCE_SCAN
        every, unfiltered = _scan(home_root, request, config, accepts, moment)
        found, totals = every[:need], (len(every), unfiltered)
    else:
        source = SOURCE_INDEX
    page = found[request.offset:request.offset + request.limit]
    labels, profiles, labelled = _labels(home_root, target, page)
    return SearchPage(
        request=request,
        rows=tuple(SearchRow(row, label) for row, label in zip(page, labels, strict=True)),
        more=len(found) > request.offset + request.limit,
        source=source,
        index_reason=reason,
        total=None if totals is None else totals[0],
        total_all=None if totals is None else totals[1],
        filters=None if config is None or filters is None else _filters_json(config, filters),
        profiles=profiles,
        labelled=labelled,
        checked_at=stamp(moment),
    )


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
        lines.append(f"Show all {page.total_all:,} (any place, any date): --all")
    return lines


def to_json(page: SearchPage) -> dict[str, object]:
    """The ``scout-free-search:1`` response. Stable keys; ``counts.total`` / ``total_all`` are ``null`` when not counted."""

    from ..data_labels import ENVELOPE_KEY, PUBLIC_UNTRUSTED, UNTRUSTED_TEXT_RULE, labels_envelope
    from .company_names import slug_display_name

    request = page.request
    rows = []
    for row in page.rows:
        posting, labels = row.posting, row.labels
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
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": page.checked_at,
        "query": {
            "titles": list(request.titles), "company": list(request.company_words), "location": list(request.location_words),
            "all": request.show_all, "include_removed": request.include_removed, "limit": request.limit, "offset": request.offset,
            "count": request.count,
        },
        "filters": None if page.filters is None else dict(page.filters),
        "source": page.source,
        "index": {"used": page.source == SOURCE_INDEX, "reason": page.index_reason},
        "counts": {"shown": len(page.rows), "more": page.more, "total": page.total, "total_all": page.total_all, "hidden": page.hidden},
        "ranked": False,
        "order": "newest_posted",
        "postings": {
            ENVELOPE_KEY: labels_envelope({
                "/rows/*/title": PUBLIC_UNTRUSTED, "/rows/*/company": PUBLIC_UNTRUSTED, "/rows/*/company_slug": PUBLIC_UNTRUSTED,
                "/rows/*/company_name": PUBLIC_UNTRUSTED, "/rows/*/location": PUBLIC_UNTRUSTED,
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


def _row_tags(row: Mapping[str, object], names: Mapping[str, str]) -> str:
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
    return "".join(f" [{tag}]" for tag in tags)


def render_page(response: Mapping[str, object]) -> str:
    """The page as the terminal shows it: one line saying what was searched, then one line per posting. No total."""

    query, counts, filters = response["query"], response["counts"], response["filters"]
    assert isinstance(query, Mapping) and isinstance(counts, Mapping)
    request = SearchRequest(
        titles=tuple(query["titles"]), company_words=tuple(query["company"]), location_words=tuple(query["location"]),  # type: ignore[arg-type]
    )
    scope = str(filters["text"]) if isinstance(filters, Mapping) else "any place, any date"
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
    width = min(28, max(len(str(row["company"])) for row in rows))
    for row in rows:
        day = str(row["posted"] or "")[:10] or "no date   "
        place = f"  ({row['location']})" if row["location"] else ""
        lines.append(f"  {day}  {str(row['company']):<{width}}  {row['title']}{place}{_row_tags(row, names)}")
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
    page = search(home_root, request, target=target, now=moment)
    response = redact(to_json(page))
    if as_json:
        yield json.dumps(response, sort_keys=True, separators=(",", ":"))
        return
    yield render_page(response)
    if page.total is None:
        total, total_all = count(home_root, request, target=target, now=moment)
        response = redact(to_json(replace(page, total=total, total_all=total_all)))
    yield render_total(response)


__all__ = [
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
