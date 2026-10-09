"""Row parsers for the 0.1.11.8 feeds: Workable, Rippling, Gem, Recruitee, Pinpoint, Breezy.

Each provider's public, unauthenticated per-company feed was probed on 2026-10-08
(docs/followups/SPIKE-expand-reach.md section 3; the response shapes are in
``research/expand-reach/probes/<system>/``). Every parser here has the same
signature as ``ats_board_clients._lever_rows``::

    rows(jobs, board_token, config, stats=None, detail_lookup=None) -> tuple[PostingRow, ...]

``jobs`` is the list the provider's payload carries (``providers.jobs_from_payload``
takes it out of the envelope), the title prefilter is ``matches_roles``, and a
job without a title or a URL is skipped, as the three older parsers do.
``detail_lookup(job_id) -> payload | None`` is only read by a two-phase provider
(Rippling: the list has no date and no description; the per-job detail has both).

Assumed shapes (read as of 2026-10-08; a provider may change them, so a failure is
``bad_json`` / a skipped row, never an assertion):

* Workable ``GET https://apply.workable.com/api/v1/widget/accounts/{token}?details=true``
  -> ``{"name", "jobs": [{"title", "shortcode", "url", "published_on" (date),
  "created_at", "telecommuting", "country", "city", "state", "locations":
  [{"country", "countryCode", "city", "region"}], "department", "description" (HTML)}]}``.
* Rippling ``GET https://ats.rippling.com/api/v2/board/{token}/jobs?page=0&pageSize=1000``
  -> ``{"items": [{"id", "name", "url", "department": {"name"}, "locations":
  [{"name", "country", "countryCode", "state", "city", "workplaceType"}]}], "totalItems"}``;
  detail ``GET https://ats.rippling.com/api/v1/board/{token}/jobs/{id}`` ->
  ``{"uuid", "name", "description": {"company": HTML, "role": HTML, ...}, "createdOn", "employmentType"}``.
* Gem ``GET https://api.gem.com/job_board/v0/{token}/job_posts/`` -> ``[{"id", "title",
  "absolute_url", "content" (HTML), "content_plain", "first_published_at", "updated_at",
  "location": {"name"}, "location_type", "departments"}]``.
* Recruitee ``GET https://{token}.recruitee.com/api/offers/`` -> ``{"offers": [{"id", "title",
  "careers_url", "location", "city", "country", "country_code", "locations": [{"country_code"}],
  "remote", "hybrid", "on_site", "published_at", "updated_at", "description", "requirements"}]}``.
* Pinpoint ``GET https://{token}.pinpointhq.com/postings.json`` -> ``{"data": [{"id", "title",
  "url", "location": {"name", "city", "province", "country"?}, "workplace_type", "department",
  "description", "key_responsibilities", "skills_knowledge_expertise", "benefits"}]}`` (no posted date).
* Breezy ``GET https://{token}.breezy.hr/json`` -> ``[{"id", "name", "url", "published_date",
  "location": {"name", "country": {"name", "id"}, "city", "is_remote"}, "department"}]``
  (no description in the list; the job page carries it).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import json
import re

from ...canonical import digest_imported_bytes
from .ats_board_clients import (
    ATSBoardClientError,
    BoardFetchStats,
    _cached_request,
    _company_from_token,
    _normalize_country,
    _published_at_from_iso,
    html_to_text,
    matches_roles,
    posting_content_digest,
    work_mode_from_label,
)
from .contracts import ATSProvider, FindJobsConfig, PostingRow, SourceKind, WorkMode, normalize_url
from .filters import sponsorship_from_text

DetailLookup = Callable[[str], Mapping[str, object] | None]

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPACE_UTC = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC$")


def published_at_from_text(value: object) -> str | None:
    """A provider's date in any of its spellings -> ISO-8601 with a zone, or ``None``.

    Accepts an ISO datetime with a zone (``2026-05-29T10:00:00Z``), a date alone
    (``2026-05-29``: Workable's ``published_on``; read as midnight UTC) and
    Recruitee's ``2026-10-05 15:45:18 UTC``. A naive datetime is not a date.
    """

    if type(value) is not str or not value.strip():
        return None
    text = value.strip()
    if _DATE_ONLY.match(text):
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return f"{text}T00:00:00Z"
    matched = _SPACE_UTC.match(text)
    if matched:
        return f"{matched.group(1)}T{matched.group(2)}Z"
    return _published_at_from_iso(text)


def _str(value: object) -> str | None:
    return value if type(value) is str and value.strip() else None


def _countries(*values: object) -> tuple[str, ...] | None:
    """ISO alpha-2 codes from the structured country values a job carries; ``None`` when none resolves."""

    found: set[str] = set()
    for value in values:
        code = _normalize_country(value)
        if code is not None:
            found.add(code)
    return tuple(sorted(found)) if found else None


def _mode_from_words(value: object) -> WorkMode | None:
    """A workplace label in a provider's own words (``REMOTE``, ``on_site``, ``Fully remote``, ``in_office``)."""

    if type(value) is not str:
        return None
    letters = re.sub(r"[^a-z]", "", value.lower())
    if not letters:
        return None
    known = work_mode_from_label(letters)
    if known is not None:
        return known
    if "remote" in letters:
        return WorkMode.REMOTE
    if "hybrid" in letters:
        return WorkMode.HYBRID
    if "onsite" in letters or "office" in letters:
        return WorkMode.ONSITE
    return None


def _row(
    *,
    provider: ATSProvider,
    board_token: str,
    url: str,
    title: str,
    location: str,
    published_at: str | None,
    text: str | None,
    countries: tuple[str, ...] | None,
    work_mode: WorkMode | None,
) -> PostingRow:
    text = text or None
    return PostingRow(
        url=url,
        normalized_url=normalize_url(url),
        provider=provider,
        board_token=board_token,
        company=_company_from_token(board_token),
        title=title,
        location=location,
        published_at=published_at,
        content_sha256=posting_content_digest(title, text),
        source_kind=SourceKind.ATS,
        query_key=f"ats:{provider.value}:{board_token}",
        text=text,
        sponsorship=sponsorship_from_text(text or ""),
        countries=countries,
        work_mode=work_mode,
    )


def _each(jobs: list, config: FindJobsConfig, stats: BoardFetchStats | None, title_key: str):
    """Yield ``(job, title)`` for the dict jobs whose title passes the prefilter; count the rest."""

    for job in jobs:
        if type(job) is not dict:
            continue
        if stats is not None:
            stats.listed += 1
        title = job.get(title_key)
        if type(title) is not str or not title.strip() or not matches_roles(title, config.roles):
            if stats is not None:
                stats.prefiltered_out += 1
            continue
        yield job, title


# ---------------------------------------------------------------------------
# Workable
# ---------------------------------------------------------------------------


def _workable_location(job: Mapping[str, object]) -> str:
    parts = [job.get("city"), job.get("state"), job.get("country")]
    text = ", ".join(part for part in parts if type(part) is str and part.strip())
    if job.get("telecommuting") is True:
        return f"Remote ({text})" if text else "Remote"
    return text


def workable_rows(
    jobs: list, board_token: str, config: FindJobsConfig, stats: BoardFetchStats | None = None, detail_lookup: DetailLookup | None = None
) -> tuple[PostingRow, ...]:
    rows: list[PostingRow] = []
    for job, title in _each(jobs, config, stats, "title"):
        url = _str(job.get("url")) or _str(job.get("shortlink"))
        if url is None:
            continue
        locations = job.get("locations")
        codes: list[object] = [job.get("country")]
        if type(locations) is list:
            for entry in locations:
                if type(entry) is dict:
                    codes.append(entry.get("countryCode") or entry.get("country"))
        mode = _mode_from_words(job.get("workplace"))
        if mode is None and job.get("telecommuting") is True:
            mode = WorkMode.REMOTE
        rows.append(
            _row(
                provider=ATSProvider.WORKABLE,
                board_token=board_token,
                url=url,
                title=title,
                location=_workable_location(job),
                published_at=published_at_from_text(job.get("published_on")) or published_at_from_text(job.get("published")),
                text=html_to_text(_str(job.get("description"))),
                countries=_countries(*codes),
                work_mode=mode,
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Rippling (two-phase: the list has no date and no description)
# ---------------------------------------------------------------------------


def _rippling_text(detail: Mapping[str, object] | None) -> str | None:
    if detail is None:
        return None
    description = detail.get("description")
    if type(description) is str:
        return html_to_text(description) or None
    if type(description) is not dict:
        return None
    parts = [html_to_text(value) for value in description.values() if type(value) is str]
    joined = "\n".join(part for part in parts if part)
    return joined or None


def rippling_rows(
    jobs: list, board_token: str, config: FindJobsConfig, stats: BoardFetchStats | None = None, detail_lookup: DetailLookup | None = None
) -> tuple[PostingRow, ...]:
    rows: list[PostingRow] = []
    for job, title in _each(jobs, config, stats, "name"):
        url = _str(job.get("url"))
        if url is None:
            continue
        locations = job.get("locations")
        names: list[str] = []
        codes: list[object] = []
        modes: list[WorkMode | None] = []
        if type(locations) is list:
            for entry in locations:
                if type(entry) is not dict:
                    continue
                name = _str(entry.get("name"))
                if name:
                    names.append(name)
                codes.append(entry.get("countryCode") or entry.get("country"))
                modes.append(_mode_from_words(entry.get("workplaceType")))
        stated = [mode for mode in modes if mode is not None]
        mode = stated[0] if stated and all(item == stated[0] for item in stated) else None
        job_id = job.get("id")
        detail = detail_lookup(str(job_id)) if detail_lookup is not None and type(job_id) is str and job_id else None
        published_at = published_at_from_text(detail.get("createdOn")) if detail is not None else None
        rows.append(
            _row(
                provider=ATSProvider.RIPPLING,
                board_token=board_token,
                url=url,
                title=title,
                location="; ".join(dict.fromkeys(names)),
                published_at=published_at,
                text=_rippling_text(detail),
                countries=_countries(*codes),
                work_mode=mode,
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Gem
# ---------------------------------------------------------------------------


def _gem_location(job: Mapping[str, object]) -> tuple[str, tuple[str, ...] | None]:
    location = job.get("location")
    name = _str(location.get("name")) if type(location) is dict else None
    codes: list[object] = []
    if name:
        # "San Francisco, United States": the country is the last comma part when it names one.
        codes.append(name.rsplit(",", 1)[-1].strip())
    offices = job.get("offices")
    if type(offices) is list:
        for office in offices:
            if type(office) is dict:
                codes.append(office.get("country") or office.get("iso_country"))
    return name or "", _countries(*codes)


def gem_rows(
    jobs: list, board_token: str, config: FindJobsConfig, stats: BoardFetchStats | None = None, detail_lookup: DetailLookup | None = None
) -> tuple[PostingRow, ...]:
    rows: list[PostingRow] = []
    for job, title in _each(jobs, config, stats, "title"):
        url = _str(job.get("absolute_url"))
        if url is None:
            continue
        location, countries = _gem_location(job)
        text = _str(job.get("content_plain"))
        if text is None:
            text = html_to_text(_str(job.get("content")))
        rows.append(
            _row(
                provider=ATSProvider.GEM,
                board_token=board_token,
                url=url,
                title=title,
                location=location,
                published_at=published_at_from_text(job.get("first_published_at")) or published_at_from_text(job.get("created_at")),
                text=text,
                countries=countries,
                work_mode=_mode_from_words(job.get("location_type")),
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Recruitee
# ---------------------------------------------------------------------------


def recruitee_rows(
    jobs: list, board_token: str, config: FindJobsConfig, stats: BoardFetchStats | None = None, detail_lookup: DetailLookup | None = None
) -> tuple[PostingRow, ...]:
    rows: list[PostingRow] = []
    for job, title in _each(jobs, config, stats, "title"):
        url = _str(job.get("careers_url"))
        if url is None:
            continue
        codes: list[object] = [job.get("country_code"), job.get("country")]
        locations = job.get("locations")
        if type(locations) is list:
            for entry in locations:
                if type(entry) is dict:
                    codes.append(entry.get("country_code") or entry.get("country"))
        if job.get("remote") is True:
            mode: WorkMode | None = WorkMode.REMOTE
        elif job.get("hybrid") is True:
            mode = WorkMode.HYBRID
        elif job.get("on_site") is True:
            mode = WorkMode.ONSITE
        else:
            mode = None
        text = "\n".join(part for part in (html_to_text(_str(job.get("description"))), html_to_text(_str(job.get("requirements")))) if part)
        rows.append(
            _row(
                provider=ATSProvider.RECRUITEE,
                board_token=board_token,
                url=url,
                title=title,
                location=_str(job.get("location")) or "",
                published_at=published_at_from_text(job.get("published_at")) or published_at_from_text(job.get("created_at")),
                text=text,
                countries=_countries(*codes),
                work_mode=mode,
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Pinpoint (no posted date in the feed)
# ---------------------------------------------------------------------------

_PINPOINT_TEXT_KEYS = ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")


def pinpoint_rows(
    jobs: list, board_token: str, config: FindJobsConfig, stats: BoardFetchStats | None = None, detail_lookup: DetailLookup | None = None
) -> tuple[PostingRow, ...]:
    rows: list[PostingRow] = []
    for job, title in _each(jobs, config, stats, "title"):
        url = _str(job.get("url"))
        if url is None:
            continue
        location = job.get("location")
        name = ""
        codes: list[object] = []
        if type(location) is dict:
            name = _str(location.get("name")) or ", ".join(
                part for part in (_str(location.get("city")), _str(location.get("province"))) if part
            )
            codes.extend((location.get("country_code"), location.get("country")))
        mode = _mode_from_words(job.get("workplace_type")) or _mode_from_words(job.get("workplace_type_text"))
        text = "\n".join(part for part in (html_to_text(_str(job.get(key))) for key in _PINPOINT_TEXT_KEYS) if part)
        rows.append(
            _row(
                provider=ATSProvider.PINPOINT,
                board_token=board_token,
                url=url,
                title=title,
                location=name,
                published_at=None,
                text=text,
                countries=_countries(*codes),
                work_mode=mode,
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Breezy (the list has no description; the job page carries it)
# ---------------------------------------------------------------------------


def breezy_rows(
    jobs: list, board_token: str, config: FindJobsConfig, stats: BoardFetchStats | None = None, detail_lookup: DetailLookup | None = None
) -> tuple[PostingRow, ...]:
    rows: list[PostingRow] = []
    for job, title in _each(jobs, config, stats, "name"):
        url = _str(job.get("url"))
        if url is None:
            continue
        location = job.get("location")
        name = ""
        codes: list[object] = []
        mode: WorkMode | None = None
        if type(location) is dict:
            name = _str(location.get("name")) or ""
            country = location.get("country")
            if type(country) is dict:
                codes.extend((country.get("id"), country.get("name")))
            if location.get("is_remote") is True:
                mode = WorkMode.REMOTE
        rows.append(
            _row(
                provider=ATSProvider.BREEZY,
                board_token=board_token,
                url=url,
                title=title,
                location=name,
                published_at=published_at_from_text(job.get("published_date")),
                text=None,
                countries=_countries(*codes),
                work_mode=mode,
            )
        )
    return tuple(rows)



def detail_text(provider: str, detail: Mapping[str, object]) -> str | None:
    """The posting text a two-phase provider's single detail payload carries (``None`` when it has none)."""

    if provider == "rippling":
        return _rippling_text(detail)
    return None


def detail_title(provider: str, detail: Mapping[str, object]) -> str | None:
    """The posting title a two-phase provider's single detail payload carries."""

    return _str(detail.get("name")) if provider == "rippling" else None


# ---------------------------------------------------------------------------
# Paged lists (Rippling: a board over one page of postings)
# ---------------------------------------------------------------------------

#: 0.1.11.8 S8: a paged list is read for at most this many pages (``page=0`` .. ``page=4``).
MAX_LIST_PAGES = 5


def _page_url(list_url: str, page: int) -> str:
    return list_url.replace("page=0", f"page={page}", 1)


def merge_pages(first: bytes, extra: list[bytes], jobs_key: str) -> bytes:
    """The first page's body with every later page's jobs appended under ``jobs_key`` (one body, one digest)."""

    payload = json.loads(first)
    merged = list(payload[jobs_key])
    for body in extra:
        more = json.loads(body).get(jobs_key)
        if type(more) is list:
            merged.extend(more)
    payload[jobs_key] = merged
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def fetch_paged_list(
    client, provider: str, board_token: str, list_url: str, jobs_key: str, *, cache, stats: BoardFetchStats
) -> tuple[bytes, str]:
    """The list of a provider whose board can run past one page: ``(body, cache_status)`` like ``_cached_request``.

    Page 0 goes through the cache as any list does. When its ``totalItems`` is larger than the items it holds, the next
    pages are asked (each one request, through the client's pace; at most :data:`MAX_LIST_PAGES` pages in all) and the
    items are joined into ONE body that is stored under the first page's URL, so the cache, the index and the digest
    read the whole board. A page that cannot be read fails the board (nothing is stored from a partial read).
    """

    prior = cache.lookup(provider, list_url) if cache is not None else None
    body, status = _cached_request(client, list_url, provider, board_token, cache=cache, stats=stats)
    if status in ("hit", "unchanged"):
        return body, status
    try:
        payload = json.loads(body)
    except ValueError:
        return body, status
    items = payload.get(jobs_key) if type(payload) is dict else None
    total = payload.get("totalItems") if type(payload) is dict else None
    if type(items) is not list or type(total) is not int or isinstance(total, bool) or len(items) >= total or not items:
        return body, status
    extra: list[bytes] = []
    held = len(items)
    for page in range(1, MAX_LIST_PAGES):
        if held >= total:
            break
        try:
            more, _more_status = _cached_request(client, _page_url(list_url, page), provider, board_token, cache=None, stats=stats)
        except ATSBoardClientError:
            if cache is not None:
                # Page 0 is stored but the board is not whole: drop its validators so the next check asks for everything.
                cache.store(provider, list_url, body=body, etag=None, last_modified=None, marker=None)
            raise
        try:
            page_items = json.loads(more).get(jobs_key)
        except (ValueError, AttributeError):
            break
        if type(page_items) is not list or not page_items:
            break
        extra.append(more)
        held += len(page_items)
    if not extra:
        return body, status
    merged = merge_pages(body, extra, jobs_key)
    if cache is not None:
        entry = cache.lookup(provider, list_url)
        cache.store(
            provider,
            list_url,
            body=merged,
            etag=entry.etag if entry is not None else None,
            last_modified=entry.last_modified if entry is not None else None,
            marker=None,
        )
        status = "revalidated" if prior is not None and prior.sha256 == digest_imported_bytes(merged) else "miss"
    return merged, status


# ---------------------------------------------------------------------------
# Job-page JSON-LD (Breezy)
# ---------------------------------------------------------------------------

_LD_JSON = re.compile(r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def _postings_in(node: object):
    if type(node) is list:
        for item in node:
            yield from _postings_in(item)
    elif type(node) is dict:
        kind = node.get("@type")
        kinds = kind if type(kind) is list else [kind]
        if "JobPosting" in kinds:
            yield node
        graph = node.get("@graph")
        if graph is not None:
            yield from _postings_in(graph)


def jobposting_from_page(html: str) -> tuple[str | None, str] | None:
    """``(title, description)`` of the first JSON-LD ``JobPosting`` with a description in a job page, else ``None``."""

    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for posting in _postings_in(data):
            description = posting.get("description")
            if type(description) is str and description.strip():
                return _str(posting.get("title")), description
    return None


__all__ = [
    "DetailLookup",
    "MAX_LIST_PAGES",
    "detail_text",
    "detail_title",
    "fetch_paged_list",
    "jobposting_from_page",
    "merge_pages",
    "breezy_rows",
    "gem_rows",
    "pinpoint_rows",
    "published_at_from_text",
    "recruitee_rows",
    "rippling_rows",
    "workable_rows",
]
