"""Company-keyed posting index (N11-C): what each company's board listed, and when it changed.

Operator direction 2026-09-27: refreshing sources and searching are two
steps. ``gigai scout sources update`` fetches the boards (the acquire
rotation is its engine) and writes this index; the search side
(``index_search.py``) reads the index only and makes no board request.

One plain JSON file per company under the GigAI home::

    <home>/cache/scout/companies/<ats>:<slug>.json
    {"schema_version", "company", "ats", "slug", "checked_at", "changed_at",
     "etag", "last_modified", "body_sha256",
     "postings": {"<id>": {"title", "location", "url", "updated_at",
                           "content_sha256", "first_seen", "last_seen",
                           "changed_at"?, "removed_at"?, "published_at"?,
                           "countries"?}}}

This is a CACHE, not a record: it lives next to the board response cache
(``<home>/cache/scout/ats-boards``), never in a workpad or the journal, every
write is temp-file + ``os.replace``, and a deleted, corrupt or foreign-schema
file reads as "not indexed" and is rebuilt from the cached board body
(:func:`refresh_company`) without a request.

Change detection, per company (:func:`observe_company`):

* a ``304`` or an unchanged body digest -> the company is untouched, only
  ``checked_at`` moves;
* a new posting id -> ``first_seen``;
* a known id whose ``updated_at`` (Greenhouse, and Lever/Ashby when they
  send one), ``content_sha256`` or title differs -> ``changed_at``;
* a listed id that is gone -> ``removed_at`` (kept
  :data:`REMOVED_RETENTION_DAYS`, then dropped; an id that comes back counts
  as changed and keeps its ``first_seen``).

The postings are parsed with the existing ATS parsers
(``ats_board_clients``), so a posting's ``content_sha256``, ``published_at``
and ``countries`` are exactly what the acquire path computes for the same
payload. The index is role-independent: every listed posting is indexed, not
only the titles the current config matches. Greenhouse's list carries no
description, so a Greenhouse posting's ``content_sha256`` is ``null`` until
its detail is in the board cache; ``updated_at`` is its change signal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
from urllib.parse import quote, unquote

from ...canonical import digest_imported_bytes
from .ats_board_clients import (
    _ASHBY_URL,
    _GREENHOUSE_JOB_URL,
    _GREENHOUSE_LIST_URL,
    _LEVER_URL,
    BoardCache,
    _ashby_rows,
    _greenhouse_row,
    _lever_rows,
    _published_at_from_epoch_ms,
    _published_at_from_iso,
)
from .contracts import PostingRow

COMPANY_INDEX_SCHEMA = "scout-company-index:1"
UPDATE_SUMMARY_SCHEMA = "scout-sources-update:1"
_UPDATE_SUMMARY_FILENAME = "last-update.json"
#: A removed posting stays in its company file this long (so a search can
#: still say "removed"), then the next body change drops it.
REMOVED_RETENTION_DAYS = 90
#: An index whose newest check is older than this reads as stale: the search
#: says "run Update sources" instead of presenting old postings as current.
DEFAULT_STALE_AFTER_HOURS = 24.0

_PROVIDERS = ("greenhouse", "lever", "ashby")
_LIST_URLS = {"greenhouse": _GREENHOUSE_LIST_URL, "lever": _LEVER_URL, "ashby": _ASHBY_URL}

#: ``CompanyChange.status`` values.
STATUS_INDEXED = "indexed"  # first time this company is written (also a rebuild)
STATUS_UPDATED = "updated"  # the body changed and was re-read
STATUS_UNTOUCHED = "untouched"  # 304 / same body digest: only checked_at moved
STATUS_MISSING = "missing"  # no cached body to read (never fetched, or the fetch failed)
STATUS_UNREADABLE = "unreadable"  # a cached body that is not the provider's list shape


class CompanyIndexError(ValueError):
    """A redacted index failure: a stable ``code`` plus provider and slug, never a body."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def index_stamp(moment: datetime | None = None) -> str:
    """Fixed-width UTC stamp (milliseconds), so index stamps compare as strings."""

    value = datetime.now(timezone.utc) if moment is None else moment.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def body_digest(body: bytes) -> str:
    """The digest ``body_sha256`` holds: the board cache's own digest of the exact response bytes."""

    return digest_imported_bytes(body)


def company_key(ats: str, slug: str) -> str:
    """``<ats>:<slug>``: the same key the rotation's last-fetched index uses."""

    return f"{ats}:{slug}"


def board_list_url(ats: str, slug: str) -> str:
    """The list URL the acquire path fetches (and caches) for this company's board."""

    template = _LIST_URLS.get(ats)
    if template is None:
        raise CompanyIndexError("unsupported_provider", f"unsupported ATS provider {ats!r}")
    return template.format(token=slug)


@dataclass(frozen=True)
class ObservedPosting:
    """One posting as a board body lists it right now (no history)."""

    posting_id: str
    title: str
    location: str
    url: str
    updated_at: str | None
    content_sha256: str | None
    published_at: str | None = None
    countries: tuple[str, ...] | None = None


@dataclass(frozen=True)
class IndexedPosting:
    """One posting in a company's index file: what was listed plus its history."""

    posting_id: str
    title: str
    location: str
    url: str
    updated_at: str | None
    content_sha256: str | None
    first_seen: str
    last_seen: str
    changed_at: str | None = None
    removed_at: str | None = None
    published_at: str | None = None
    countries: tuple[str, ...] | None = None

    @property
    def removed(self) -> bool:
        return self.removed_at is not None

    @property
    def touched_at(self) -> str:
        """When this posting last became news: first seen, or changed since."""

        return max(self.first_seen, self.changed_at or "")

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "title": self.title,
            "location": self.location,
            "url": self.url,
            "updated_at": self.updated_at,
            "content_sha256": self.content_sha256,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }
        if self.changed_at is not None:
            value["changed_at"] = self.changed_at
        if self.removed_at is not None:
            value["removed_at"] = self.removed_at
        if self.published_at is not None:
            value["published_at"] = self.published_at
        if self.countries is not None:
            value["countries"] = list(self.countries)
        return value

    @classmethod
    def from_json(cls, posting_id: str, payload: object) -> "IndexedPosting | None":
        """``None`` for an entry that is not a posting (a hand-edited or torn file)."""

        if not isinstance(payload, dict):
            return None
        title, url = payload.get("title"), payload.get("url")
        first_seen, last_seen = payload.get("first_seen"), payload.get("last_seen")
        if not all(type(item) is str and item for item in (title, url, first_seen, last_seen)):
            return None
        location = payload.get("location")
        raw_countries = payload.get("countries")
        countries = (
            tuple(item for item in raw_countries if type(item) is str) if isinstance(raw_countries, list) else None
        )
        return cls(
            posting_id=posting_id,
            title=title,  # type: ignore[arg-type]
            location=location if type(location) is str else "",
            url=url,  # type: ignore[arg-type]
            updated_at=_optional_str(payload.get("updated_at")),
            content_sha256=_optional_str(payload.get("content_sha256")),
            first_seen=first_seen,  # type: ignore[arg-type]
            last_seen=last_seen,  # type: ignore[arg-type]
            changed_at=_optional_str(payload.get("changed_at")),
            removed_at=_optional_str(payload.get("removed_at")),
            published_at=_optional_str(payload.get("published_at")),
            countries=countries,
        )


def _optional_str(value: object) -> str | None:
    return value if type(value) is str and value else None


@dataclass(frozen=True)
class CompanyIndexEntry:
    """One company's index file."""

    company: str
    ats: str
    slug: str
    checked_at: str | None
    etag: str | None
    body_sha256: str | None
    postings: Mapping[str, IndexedPosting] = field(default_factory=dict)
    last_modified: str | None = None
    #: When the posting set last changed (``None`` until a change after the first build).
    changed_at: str | None = None

    @property
    def key(self) -> str:
        return company_key(self.ats, self.slug)

    def live(self) -> tuple[IndexedPosting, ...]:
        """The postings the board listed at the last check, in id order."""

        return tuple(self.postings[posting_id] for posting_id in sorted(self.postings) if not self.postings[posting_id].removed)

    def touched_since(self, since: str | None) -> tuple[IndexedPosting, ...]:
        """Live postings first seen or changed after ``since`` (all of them when ``since`` is ``None``)."""

        return tuple(posting for posting in self.live() if since is None or posting.touched_at > since)

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": COMPANY_INDEX_SCHEMA,
            "company": self.company,
            "ats": self.ats,
            "slug": self.slug,
            "checked_at": self.checked_at,
            "changed_at": self.changed_at,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "body_sha256": self.body_sha256,
            "postings": {posting_id: posting.to_json() for posting_id, posting in sorted(self.postings.items())},
        }

    @classmethod
    def from_json(cls, payload: object) -> "CompanyIndexEntry | None":
        """A missing, corrupt or foreign-schema payload reads as ``None`` (not indexed)."""

        if not isinstance(payload, dict) or payload.get("schema_version") != COMPANY_INDEX_SCHEMA:
            return None
        ats, slug = payload.get("ats"), payload.get("slug")
        if ats not in _PROVIDERS or type(slug) is not str or not slug:
            return None
        raw_postings = payload.get("postings")
        if not isinstance(raw_postings, dict):
            return None
        postings: dict[str, IndexedPosting] = {}
        for posting_id, raw in raw_postings.items():
            posting = IndexedPosting.from_json(posting_id, raw) if type(posting_id) is str and posting_id else None
            if posting is not None:
                postings[posting_id] = posting
        company = payload.get("company")
        return cls(
            company=company if type(company) is str and company else slug,
            ats=ats,  # type: ignore[arg-type]
            slug=slug,
            checked_at=_optional_str(payload.get("checked_at")),
            etag=_optional_str(payload.get("etag")),
            body_sha256=_optional_str(payload.get("body_sha256")),
            postings=postings,
            last_modified=_optional_str(payload.get("last_modified")),
            changed_at=_optional_str(payload.get("changed_at")),
        )


@dataclass(frozen=True)
class CompanyChange:
    """What one check found for one company."""

    ats: str
    slug: str
    status: str
    new: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    live: int = 0
    code: str | None = None

    @property
    def key(self) -> str:
        return company_key(self.ats, self.slug)

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.changed or self.removed)

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "company": self.key,
            "status": self.status,
            "new": len(self.new),
            "changed": len(self.changed),
            "removed": len(self.removed),
            "live": self.live,
        }
        if self.code is not None:
            value["code"] = self.code
        return value


# ---------------------------------------------------------------------------
# Change detection (pure: no filesystem, no clock)
# ---------------------------------------------------------------------------


def _differs(previous: IndexedPosting, observed: ObservedPosting) -> bool:
    if previous.updated_at is not None and observed.updated_at is not None and previous.updated_at != observed.updated_at:
        return True
    if (
        previous.content_sha256 is not None
        and observed.content_sha256 is not None
        and previous.content_sha256 != observed.content_sha256
    ):
        return True
    return previous.title != observed.title


def observe_company(
    previous: CompanyIndexEntry | None,
    *,
    company: str,
    ats: str,
    slug: str,
    observed_at: str,
    observed: Mapping[str, ObservedPosting] | None = None,
    body_sha256: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    not_modified: bool = False,
) -> tuple[CompanyIndexEntry, CompanyChange]:
    """One check of one company -> its next index entry and what changed.

    ``not_modified`` (the board answered ``304``) or a ``body_sha256`` equal
    to the indexed one leaves the company untouched: the postings, their
    stamps, the digest and the validators all stay; only ``checked_at``
    moves. Otherwise ``observed`` (the parsed body) is diffed against the
    indexed postings. With no ``previous`` entry every posting is new.
    """

    if previous is not None and (not_modified or (body_sha256 is not None and body_sha256 == previous.body_sha256)):
        entry = replace(previous, checked_at=observed_at)
        return entry, CompanyChange(ats, slug, STATUS_UNTOUCHED, live=len(entry.live()))
    if observed is None:
        raise CompanyIndexError("body_required", f"{ats} company {slug!r} has no indexed postings to keep and no body to read")

    before = dict(previous.postings) if previous is not None else {}
    postings: dict[str, IndexedPosting] = {}
    new: list[str] = []
    changed: list[str] = []
    removed: list[str] = []
    for posting_id, item in observed.items():
        known = before.get(posting_id)
        if known is None:
            new.append(posting_id)
            postings[posting_id] = IndexedPosting(
                posting_id=posting_id,
                title=item.title,
                location=item.location,
                url=item.url,
                updated_at=item.updated_at,
                content_sha256=item.content_sha256,
                first_seen=observed_at,
                last_seen=observed_at,
                published_at=item.published_at,
                countries=item.countries,
            )
            continue
        is_change = known.removed or _differs(known, item)
        if is_change:
            changed.append(posting_id)
        postings[posting_id] = IndexedPosting(
            posting_id=posting_id,
            title=item.title,
            location=item.location,
            url=item.url,
            updated_at=item.updated_at if item.updated_at is not None else known.updated_at,
            # A digest once known is never dropped back to null by a list
            # body that carries no description (Greenhouse).
            content_sha256=item.content_sha256 if item.content_sha256 is not None else (None if is_change else known.content_sha256),
            first_seen=known.first_seen,
            last_seen=observed_at,
            changed_at=observed_at if is_change else known.changed_at,
            removed_at=None,
            published_at=item.published_at if item.published_at is not None else known.published_at,
            countries=item.countries if item.countries is not None else known.countries,
        )
    cutoff = _retention_cutoff(observed_at)
    for posting_id, known in before.items():
        if posting_id in postings:
            continue
        if not known.removed:
            removed.append(posting_id)
            postings[posting_id] = replace(known, removed_at=observed_at)
        elif cutoff is None or known.removed_at is None or known.removed_at >= cutoff:
            postings[posting_id] = known

    has_changes = bool(new or changed or removed)
    entry = CompanyIndexEntry(
        company=company,
        ats=ats,
        slug=slug,
        checked_at=observed_at,
        etag=etag,
        body_sha256=body_sha256,
        postings=postings,
        last_modified=last_modified,
        changed_at=(observed_at if has_changes else previous.changed_at) if previous is not None else None,
    )
    return entry, CompanyChange(
        ats,
        slug,
        STATUS_INDEXED if previous is None else STATUS_UPDATED,
        new=tuple(sorted(new)),
        changed=tuple(sorted(changed)),
        removed=tuple(sorted(removed)),
        live=len(observed),
    )


def _retention_cutoff(observed_at: str) -> str | None:
    moment = _parse_stamp(observed_at)
    if moment is None:
        return None
    return index_stamp(moment - timedelta(days=REMOVED_RETENTION_DAYS))


# ---------------------------------------------------------------------------
# Reading a board body with the existing parsers
# ---------------------------------------------------------------------------


class _EveryTitle:
    """Stands in for ``FindJobsConfig`` where a parser reads only ``.roles``.

    The Lever/Ashby row parsers filter by ``matches_roles(title,
    config.roles)``; the index wants every posting, so each job is parsed
    with its own title as the one role (a title always contains its own
    words).
    """

    __slots__ = ("roles",)

    def __init__(self, title: str) -> None:
        self.roles = (title,)


#: ``(job id, updated_at marker) -> the cached detail payload`` or ``None``.
DetailLookup = Callable[[str, "str | None"], "Mapping[str, object] | None"]


def _decode(body: bytes, ats: str, slug: str) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CompanyIndexError("bad_json", f"{ats} company {slug!r} has an unreadable board body") from None


def _job_id(job: Mapping[str, object], url: str) -> str:
    value = job.get("id")
    if isinstance(value, (int, str)) and not isinstance(value, bool) and str(value):
        return str(value)
    return f"url:{url}"


def _jobs(ats: str, slug: str, body: bytes) -> list[dict[str, object]]:
    payload = _decode(body, ats, slug)
    if ats == "lever":
        jobs = payload
    elif ats in ("greenhouse", "ashby"):
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
    else:
        raise CompanyIndexError("unsupported_provider", f"unsupported ATS provider {ats!r}")
    if type(jobs) is not list:
        raise CompanyIndexError("bad_json", f"{ats} company {slug!r} has an unreadable board body")
    return [job for job in jobs if type(job) is dict]


def _updated_at(ats: str, job: Mapping[str, object]) -> str | None:
    if ats == "greenhouse":
        value = job.get("updated_at")
        return value if type(value) is str and value else None
    value = job.get("updatedAt")
    if type(value) is str:
        return _published_at_from_iso(value)
    return _published_at_from_epoch_ms(value)


def _row(ats: str, slug: str, job: dict[str, object], detail_lookup: DetailLookup | None) -> tuple[str, PostingRow, bool] | None:
    """``(posting id, row, has the description)`` for one job, or ``None`` when it has no title/URL."""

    if ats == "greenhouse":
        title, url = job.get("title"), job.get("absolute_url")
        if type(title) is not str or not title.strip() or type(url) is not str or not url:
            return None
        posting_id = _job_id(job, url)
        inline = job.get("content")
        content: str | None = inline if isinstance(inline, str) else None
        detail: dict[str, object] | None = None
        if detail_lookup is not None and not posting_id.startswith("url:"):
            found = detail_lookup(posting_id, _updated_at(ats, job))
            detail_content = found.get("content") if found is not None else None
            if found is not None and isinstance(detail_content, str):
                content = detail_content
                detail = dict(found)
        return posting_id, _greenhouse_row(job, title, url, content, slug, detail), content is not None
    title = job.get("text") if ats == "lever" else job.get("title")
    if type(title) is not str:
        return None
    parser = _lever_rows if ats == "lever" else _ashby_rows
    rows = parser([job], slug, _EveryTitle(title))  # type: ignore[arg-type]
    if not rows:
        return None
    return _job_id(job, rows[0].url), rows[0], True


def parse_board_body(
    ats: str,
    slug: str,
    body: bytes,
    *,
    detail_lookup: DetailLookup | None = None,
) -> dict[str, ObservedPosting]:
    """Every posting a cached board body lists, keyed by the provider's posting id.

    Raises :class:`CompanyIndexError` (``bad_json``) for a body that is not
    the provider's list shape. A job without a title or URL is skipped, as
    the acquire parsers skip it.
    """

    observed: dict[str, ObservedPosting] = {}
    for job in _jobs(ats, slug, body):
        parsed = _row(ats, slug, job, detail_lookup)
        if parsed is None:
            continue
        posting_id, row, has_text = parsed
        observed[posting_id] = ObservedPosting(
            posting_id=posting_id,
            title=row.title,
            location=row.location,
            url=row.url,
            updated_at=_updated_at(ats, job),
            content_sha256=row.content_sha256 if has_text else None,
            published_at=row.published_at,
            countries=row.countries,
        )
    return observed


def cached_detail_lookup(cache: BoardCache, slug: str) -> DetailLookup:
    """Greenhouse job details from the board cache, only when they match the listed ``updated_at``."""

    def lookup(job_id: str, marker: str | None) -> dict[str, object] | None:
        entry = cache.lookup("greenhouse", _GREENHOUSE_JOB_URL.format(token=slug, job_id=job_id))
        if entry is None or entry.marker != marker:
            return None
        try:
            payload = json.loads(entry.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    return lookup


@dataclass(frozen=True)
class CachedRows:
    """Posting rows read back from the board cache for a search (no request)."""

    rows: Mapping[str, PostingRow]
    #: Ids whose row has no description: a Greenhouse posting whose detail
    #: is not cached (or is older than the listed ``updated_at``).
    without_text: tuple[str, ...] = ()
    #: Ids the cached body no longer lists (or there is no cached body).
    missing: tuple[str, ...] = ()


def cached_posting_rows(cache: BoardCache, ats: str, slug: str, posting_ids: Iterable[str]) -> CachedRows:
    """The acquire-shaped ``PostingRow`` for each wanted posting, from cached bodies only."""

    wanted = tuple(dict.fromkeys(posting_ids))
    entry = cache.lookup(ats, board_list_url(ats, slug))
    if entry is None:
        return CachedRows({}, missing=wanted)
    try:
        jobs = _jobs(ats, slug, entry.body)
    except CompanyIndexError:
        return CachedRows({}, missing=wanted)
    lookup = cached_detail_lookup(cache, slug) if ats == "greenhouse" else None
    remaining = set(wanted)
    found: dict[str, PostingRow] = {}
    without_text: list[str] = []
    for job in jobs:
        if not remaining:
            break
        url = job.get("absolute_url") if ats == "greenhouse" else job.get("hostedUrl") if ats == "lever" else job.get("jobUrl")
        if type(url) is not str or not url or _job_id(job, url) not in remaining:
            continue
        parsed = _row(ats, slug, job, lookup)
        if parsed is None:
            continue
        posting_id, row, has_text = parsed
        remaining.discard(posting_id)
        found[posting_id] = row
        if not has_text:
            without_text.append(posting_id)
    return CachedRows(
        {posting_id: found[posting_id] for posting_id in wanted if posting_id in found},
        without_text=tuple(without_text),
        missing=tuple(posting_id for posting_id in wanted if posting_id in remaining),
    )


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class CompanyIndex:
    """``<root>/<ats>:<slug>.json``, one file per company; atomic writes; deletable."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @classmethod
    def for_home(cls, home_root: Path) -> "CompanyIndex":
        """``<home>/cache/scout/companies`` (next to the board response cache)."""

        return cls(Path(home_root) / "cache" / "scout" / "companies")

    def path(self, ats: str, slug: str) -> Path:
        if ats not in _PROVIDERS:
            raise CompanyIndexError("unsupported_provider", f"unsupported ATS provider {ats!r}")
        if type(slug) is not str or not slug:
            raise CompanyIndexError("bad_slug", f"{ats} company slug is empty")
        # A slug is one URL path segment; percent-encode anything that is not
        # a plain filename character so it can never name another directory.
        return self.root / f"{ats}:{quote(slug, safe='')}.json"

    def read(self, ats: str, slug: str) -> CompanyIndexEntry | None:
        """The company's entry, or ``None`` when it is missing, corrupt or another company's."""

        try:
            payload = json.loads(self.path(ats, slug).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        entry = CompanyIndexEntry.from_json(payload)
        if entry is None or entry.ats != ats or entry.slug != slug:
            return None
        return entry

    def write(self, entry: CompanyIndexEntry) -> Path:
        """Replace the company's file atomically (temp file + ``os.replace``)."""

        path = self.path(entry.ats, entry.slug)
        _atomic_write_json(path, entry.to_json())
        return path

    def delete(self, ats: str, slug: str) -> bool:
        try:
            self.path(ats, slug).unlink()
        except FileNotFoundError:
            return False
        return True

    def keys(self) -> Iterator[tuple[str, str]]:
        """``(ats, slug)`` for every company file present, in filename order."""

        try:
            names = sorted(item.name for item in self.root.iterdir())
        except OSError:
            return
        for name in names:
            if not name.endswith(".json") or ":" not in name:
                continue
            ats, _, encoded = name[: -len(".json")].partition(":")
            if ats in _PROVIDERS and encoded:
                yield ats, unquote(encoded)

    # -- the last `sources update` run -------------------------------------

    @property
    def update_summary_path(self) -> Path:
        return self.root / _UPDATE_SUMMARY_FILENAME

    def read_update_summary(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self.update_summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != UPDATE_SUMMARY_SCHEMA:
            return None
        return payload

    def write_update_summary(self, summary: Mapping[str, object]) -> Path:
        _atomic_write_json(self.update_summary_path, {**summary, "schema_version": UPDATE_SUMMARY_SCHEMA})
        return self.update_summary_path


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}-{threading.get_ident()}")
    try:
        tmp.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def refresh_company(
    index: CompanyIndex,
    cache: BoardCache,
    *,
    ats: str,
    slug: str,
    company: str | None = None,
    observed_at: str | None = None,
    details: bool = True,
) -> CompanyChange:
    """Bring one company's index file in step with its cached board body. No request.

    The acquire path has already made the (conditional) request and left
    the current body in ``cache``; a ``304`` leaves that body as it was, so
    "the same body digest" covers both. Called after a board fetch this is
    the update; called with no index file it is the rebuild.

    * no cached list body -> ``missing`` (nothing written);
    * the indexed digest equals the cached body's -> ``untouched``, only
      ``checked_at`` moves;
    * otherwise the body is parsed and diffed (``indexed`` the first time,
      ``updated`` after), and the file is replaced atomically.

    A cached body that is not the provider's list shape is ``unreadable``
    and leaves the existing file alone.
    """

    stamp = observed_at if observed_at is not None else index_stamp()
    entry = cache.lookup(ats, board_list_url(ats, slug))
    if entry is None:
        return CompanyChange(ats, slug, STATUS_MISSING, code="not_cached")
    previous = index.read(ats, slug)
    name = company or (previous.company if previous is not None else slug)
    observed: dict[str, ObservedPosting] | None = None
    if previous is None or previous.body_sha256 != entry.sha256:
        lookup = cached_detail_lookup(cache, slug) if details and ats == "greenhouse" else None
        try:
            observed = parse_board_body(ats, slug, entry.body, detail_lookup=lookup)
        except CompanyIndexError as exc:
            return CompanyChange(ats, slug, STATUS_UNREADABLE, code=exc.code)
    updated, change = observe_company(
        previous,
        company=name,
        ats=ats,
        slug=slug,
        observed_at=stamp,
        observed=observed,
        body_sha256=entry.sha256,
        etag=entry.etag,
        last_modified=entry.last_modified,
    )
    index.write(updated)
    return change


# ---------------------------------------------------------------------------
# Totals for one `sources update` run, and the index's state for a search
# ---------------------------------------------------------------------------


@dataclass
class UpdateTotals:
    """Running totals over the companies one ``sources update`` run checked."""

    companies: int = 0
    indexed: int = 0
    updated: int = 0
    untouched: int = 0
    missing: int = 0
    unreadable: int = 0
    companies_with_new: int = 0
    companies_with_changes: int = 0
    new: int = 0
    changed: int = 0
    removed: int = 0
    live: int = 0

    def add(self, change: CompanyChange) -> None:
        self.companies += 1
        if change.status == STATUS_INDEXED:
            self.indexed += 1
        elif change.status == STATUS_UPDATED:
            self.updated += 1
        elif change.status == STATUS_UNTOUCHED:
            self.untouched += 1
        elif change.status == STATUS_MISSING:
            self.missing += 1
        else:
            self.unreadable += 1
        self.live += change.live
        if change.new:
            self.companies_with_new += 1
        if change.has_changes:
            self.companies_with_changes += 1
        self.new += len(change.new)
        self.changed += len(change.changed)
        self.removed += len(change.removed)

    def to_json(self) -> dict[str, object]:
        return {
            "companies": self.companies,
            "indexed": self.indexed,
            "updated": self.updated,
            "untouched": self.untouched,
            "missing": self.missing,
            "unreadable": self.unreadable,
            "companies_with_new": self.companies_with_new,
            "companies_with_changes": self.companies_with_changes,
            "new": self.new,
            "changed": self.changed,
            "removed": self.removed,
            "live": self.live,
        }

    def summary_line(self) -> str:
        """``N companies with new postings: X new, Y changed, Z removed``."""

        noun = "company" if self.companies_with_new == 1 else "companies"
        return f"{self.companies_with_new} {noun} with new postings: {self.new} new, {self.changed} changed, {self.removed} removed"


INDEX_READY = "ready"
INDEX_EMPTY = "empty"
INDEX_STALE = "stale"


@dataclass(frozen=True)
class IndexState:
    """Whether a search can read the index, and what to tell the user when it cannot."""

    status: str  # "ready" | "empty" | "stale"
    companies: int
    indexed: int
    not_indexed: int
    oldest_checked_at: str | None
    newest_checked_at: str | None
    stale_after_hours: float

    @property
    def needs_update(self) -> bool:
        return self.status != INDEX_READY

    @property
    def message(self) -> str | None:
        if self.status == INDEX_EMPTY:
            return "No company postings are stored on this machine yet. Run Update sources, then search again."
        if self.status == INDEX_STALE:
            return "The stored company postings are out of date. Run Update sources, then search again."
        return None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "needs_update": self.needs_update,
            "message": self.message,
            "companies": self.companies,
            "indexed": self.indexed,
            "not_indexed": self.not_indexed,
            "oldest_checked_at": self.oldest_checked_at,
            "newest_checked_at": self.newest_checked_at,
            "stale_after_hours": self.stale_after_hours,
        }


def index_state(
    entries: Iterable[CompanyIndexEntry | None],
    *,
    now: datetime | None = None,
    stale_after_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> IndexState:
    """The index's state over the companies a search wants (``None`` = not indexed).

    ``empty``: none of them is indexed. ``stale``: the NEWEST check is older
    than ``stale_after_hours`` (no update has run lately); a rotation that
    is still working through the catalog leaves older companies behind
    without making the whole index stale. Otherwise ``ready``.
    """

    total = indexed = 0
    oldest: str | None = None
    newest: str | None = None
    for entry in entries:
        total += 1
        if entry is None or entry.checked_at is None:
            continue
        indexed += 1
        oldest = entry.checked_at if oldest is None else min(oldest, entry.checked_at)
        newest = entry.checked_at if newest is None else max(newest, entry.checked_at)
    status = INDEX_READY
    if indexed == 0:
        status = INDEX_EMPTY
    else:
        checked = _parse_stamp(newest)
        moment = datetime.now(timezone.utc) if now is None else now
        if checked is None or moment - checked > timedelta(hours=stale_after_hours):
            status = INDEX_STALE
    return IndexState(status, total, indexed, total - indexed, oldest, newest, stale_after_hours)


__all__ = [
    "COMPANY_INDEX_SCHEMA",
    "DEFAULT_STALE_AFTER_HOURS",
    "INDEX_EMPTY",
    "INDEX_READY",
    "INDEX_STALE",
    "REMOVED_RETENTION_DAYS",
    "STATUS_INDEXED",
    "STATUS_MISSING",
    "STATUS_UNREADABLE",
    "STATUS_UNTOUCHED",
    "STATUS_UPDATED",
    "UPDATE_SUMMARY_SCHEMA",
    "CachedRows",
    "CompanyChange",
    "CompanyIndex",
    "CompanyIndexEntry",
    "CompanyIndexError",
    "IndexState",
    "IndexedPosting",
    "ObservedPosting",
    "UpdateTotals",
    "board_list_url",
    "body_digest",
    "cached_detail_lookup",
    "cached_posting_rows",
    "company_key",
    "index_stamp",
    "index_state",
    "observe_company",
    "parse_board_body",
    "refresh_company",
]
