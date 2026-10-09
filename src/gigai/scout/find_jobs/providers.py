"""The provider registry (0.1.11.8): everything Scout knows per hiring system, in one table.

Before 0.1.11.8 the three providers (Greenhouse, Lever, Ashby) were enumerated in
ten places: the enum, ``parse_board_url``, the list URLs, the body shapes, the
row parsers, the posted-date fields, the catalog aliases and board URLs, the
host-to-source map, the posting_live board URLs and the company-name reader.
Adding a system meant touching all of them. Now each of those reads
:data:`PROVIDERS`; the enum and the board hosts stay in ``contracts.py`` (the
base module, which must not import this one).

A :class:`ProviderSpec` says, for one system: its public per-company list
endpoint, where the jobs sit in the payload, which keys name a job's id, title
and URL, the parser that turns the payload's jobs into ``PostingRow`` tuples
(``ats_board_clients`` for the older three, ``ats_feeds`` for the six new ones),
the field the posted date comes from (``published_field``: ``None`` when the feed
has no posted date, as Pinpoint), an optional per-job detail endpoint for a
two-phase provider (Greenhouse, Rippling), the public board and job page URL
templates, and the provider's own request interval when it should be slower
than the default pace (``None``: the default).

Greenhouse keeps two special paths (its one-time ``?content=true`` fill and the
``updated_at`` marker on its detail cache); ``company_index`` and
``ats_board_clients`` still branch on ``"greenhouse"`` for those two things only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .contracts import BOARD_PATH_HOSTS, BOARD_SUBDOMAIN_HOSTS, ATSProvider, PostingRow

RowsParser = Callable[..., tuple[PostingRow, ...]]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    #: The public, unauthenticated list endpoint; ``{token}`` is the board token.
    list_url: str
    #: The board's public page (the catalog's ``board_url``); ``{token}``.
    board_url: str
    #: Where the jobs sit in the list payload: ``None`` for a top-level array, else the envelope key.
    jobs_key: str | None
    #: The key of a job's stable id, title and public URL in the list.
    id_key: str
    title_key: str
    url_key: str
    #: ``rows(jobs, token, config, stats=None, detail_lookup=None) -> tuple[PostingRow, ...]``.
    rows: RowsParser
    #: The list (or detail) field the posted date is read from; ``None`` when the feed has none.
    published_field: str | None
    #: The list field that marks a change to a known posting; ``None`` when the feed has none.
    updated_field: str | None
    #: ``True`` when the list carries the description, so no detail request exists.
    list_has_text: bool
    #: A per-job detail endpoint (``{token}``, ``{id}``) for a two-phase provider, else ``None``.
    detail_url: str | None = None
    #: The public job page (``{token}``, ``{id}``) when the posting id alone names it (liveness checks).
    job_url: str | None = None
    #: Host substrings that mean "this URL is this provider's" (the acquisition source map).
    source_hosts: tuple[str, ...] = ()
    #: A slower pace than the default for this provider, in seconds between requests; ``None`` = default.
    min_interval_seconds: float | None = None
    #: Catalog spellings of the provider besides its name.
    aliases: tuple[str, ...] = ()

    @property
    def provider(self) -> ATSProvider:
        return ATSProvider(self.name)

    @property
    def published_kind(self) -> str:
        """What a ``published_at`` of this provider means: ``posted``, the day the posting went up (every feed Scout
        reads dates a posting that way when it dates it at all; Pinpoint gives no date, so its postings are undated)."""

        return "posted"


def _build() -> dict[str, ProviderSpec]:
    from . import ats_feeds
    from .ats_board_clients import (
        _ASHBY_URL,
        _GREENHOUSE_JOB_URL,
        _GREENHOUSE_LIST_URL,
        _LEVER_URL,
        _ashby_rows,
        _greenhouse_rows,
        _lever_rows,
    )

    specs = (
        ProviderSpec(
            name="greenhouse",
            list_url=_GREENHOUSE_LIST_URL,
            board_url="https://job-boards.greenhouse.io/{token}",
            jobs_key="jobs",
            id_key="id",
            title_key="title",
            url_key="absolute_url",
            rows=_greenhouse_rows,
            published_field="first_published",
            updated_field="updated_at",
            list_has_text=False,
            detail_url=_GREENHOUSE_JOB_URL,
            job_url="https://job-boards.greenhouse.io/{token}/jobs/{id}",
            source_hosts=("greenhouse.io",),
        ),
        ProviderSpec(
            name="lever",
            list_url=_LEVER_URL,
            board_url="https://jobs.lever.co/{token}",
            jobs_key=None,
            id_key="id",
            title_key="text",
            url_key="hostedUrl",
            rows=_lever_rows,
            published_field="createdAt",
            updated_field="updatedAt",
            list_has_text=True,
            job_url="https://jobs.lever.co/{token}/{id}",
            source_hosts=("lever.co",),
        ),
        ProviderSpec(
            name="ashby",
            list_url=_ASHBY_URL,
            board_url="https://jobs.ashbyhq.com/{token}",
            jobs_key="jobs",
            id_key="id",
            title_key="title",
            url_key="jobUrl",
            rows=_ashby_rows,
            published_field="publishedAt",
            updated_field="updatedAt",
            list_has_text=True,
            job_url="https://jobs.ashbyhq.com/{token}/{id}",
            source_hosts=("ashbyhq.com",),
            aliases=("ashbyhq",),
        ),
        ProviderSpec(
            name="workable",
            list_url="https://apply.workable.com/api/v1/widget/accounts/{token}?details=true",
            board_url="https://apply.workable.com/{token}",
            jobs_key="jobs",
            id_key="shortcode",
            title_key="title",
            url_key="url",
            rows=ats_feeds.workable_rows,
            published_field="published_on",
            updated_field=None,
            list_has_text=True,
            source_hosts=("workable.com",),
            # apply.workable.com rate-limits a reader: at 0.35 s between requests it answered its own 429 page
            # after ~900 requests, at 1.0 s it ran clean for ~3,400 (research/expand-reach/discovery/REPORT.md).
            min_interval_seconds=1.0,
        ),
        ProviderSpec(
            name="rippling",
            list_url="https://ats.rippling.com/api/v2/board/{token}/jobs?page=0&pageSize=1000",
            board_url="https://ats.rippling.com/{token}/jobs",
            jobs_key="items",
            id_key="id",
            title_key="name",
            url_key="url",
            rows=ats_feeds.rippling_rows,
            published_field="createdOn",
            updated_field=None,
            list_has_text=False,
            detail_url="https://ats.rippling.com/api/v1/board/{token}/jobs/{id}",
            job_url="https://ats.rippling.com/{token}/jobs/{id}",
            source_hosts=("rippling.com",),
        ),
        ProviderSpec(
            name="gem",
            list_url="https://api.gem.com/job_board/v0/{token}/job_posts/",
            board_url="https://jobs.gem.com/{token}",
            jobs_key=None,
            id_key="id",
            title_key="title",
            url_key="absolute_url",
            rows=ats_feeds.gem_rows,
            published_field="first_published_at",
            updated_field="updated_at",
            list_has_text=True,
            job_url="https://jobs.gem.com/{token}/{id}",
            source_hosts=("gem.com",),
        ),
        ProviderSpec(
            name="recruitee",
            list_url="https://{token}.recruitee.com/api/offers/",
            board_url="https://{token}.recruitee.com",
            jobs_key="offers",
            id_key="id",
            title_key="title",
            url_key="careers_url",
            rows=ats_feeds.recruitee_rows,
            published_field="published_at",
            updated_field="updated_at",
            list_has_text=True,
            source_hosts=("recruitee.com",),
            min_interval_seconds=1.0,
        ),
        ProviderSpec(
            name="pinpoint",
            list_url="https://{token}.pinpointhq.com/postings.json",
            board_url="https://{token}.pinpointhq.com",
            jobs_key="data",
            id_key="id",
            title_key="title",
            url_key="url",
            rows=ats_feeds.pinpoint_rows,
            published_field=None,
            updated_field=None,
            list_has_text=True,
            source_hosts=("pinpointhq.com",),
            min_interval_seconds=1.0,
        ),
        ProviderSpec(
            name="breezy",
            list_url="https://{token}.breezy.hr/json",
            board_url="https://{token}.breezy.hr",
            jobs_key=None,
            id_key="id",
            title_key="name",
            url_key="url",
            rows=ats_feeds.breezy_rows,
            published_field="published_date",
            updated_field=None,
            list_has_text=False,
            source_hosts=("breezy.hr",),
            min_interval_seconds=1.0,
        ),
    )
    table = {spec.name: spec for spec in specs}
    missing = {member.value for member in ATSProvider} - set(table)
    if missing:
        raise AssertionError(f"ATSProvider members without a ProviderSpec: {sorted(missing)}")
    return table


_TABLE: dict[str, ProviderSpec] | None = None


def registry() -> Mapping[str, ProviderSpec]:
    """Every provider, by name (built once; the parsers are imported lazily to avoid an import cycle)."""

    global _TABLE
    if _TABLE is None:
        _TABLE = _build()
    return _TABLE


def spec(provider: str) -> ProviderSpec | None:
    """The spec of ``provider`` (a name or an :class:`ATSProvider`), or ``None`` for an unknown one."""

    return registry().get(str(provider))


def provider_names() -> tuple[str, ...]:
    """The provider names in registry order (the older three first)."""

    return tuple(registry())


def list_url(provider: str, token: str) -> str | None:
    found = spec(provider)
    return None if found is None else found.list_url.format(token=token)


def board_url(provider: str, token: str) -> str | None:
    found = spec(provider)
    return None if found is None else found.board_url.format(token=token)


def jobs_from_payload(provider: str, payload: object) -> list[dict[str, object]] | None:
    """The dict jobs a decoded list payload carries, or ``None`` when it is not this provider's list shape."""

    found = spec(provider)
    if found is None:
        return None
    jobs = payload if found.jobs_key is None else (payload.get(found.jobs_key) if isinstance(payload, dict) else None)
    if type(jobs) is not list:
        return None
    return [job for job in jobs if type(job) is dict]


def published_fields() -> dict[str, str]:
    """``provider -> the field its posted date comes from`` (only providers with one)."""

    return {name: item.published_field for name, item in registry().items() if item.published_field is not None}


def published_kinds() -> dict[str, str]:
    """``provider -> "posted"`` for every provider (``scout_new.PUBLISHED_KINDS`` is this table, pinned)."""

    return {name: item.published_kind for name, item in registry().items()}


def catalog_aliases() -> dict[str, ATSProvider]:
    """Every spelling the catalog may use for a provider -> the enum member."""

    aliases: dict[str, ATSProvider] = {}
    for name, item in registry().items():
        aliases[name] = item.provider
        for alias in item.aliases:
            aliases[alias] = item.provider
    return aliases


def source_from_host(host: str) -> str | None:
    """The provider whose ``source_hosts`` substring the host carries, else ``None``."""

    lowered = host.lower()
    for name, item in registry().items():
        if any(fragment in lowered for fragment in item.source_hosts):
            return name
    return None


def board_hosts() -> tuple[str, ...]:
    """Every host (or host suffix) that is a board's own page, not a company site."""

    return tuple(BOARD_PATH_HOSTS) + tuple(BOARD_SUBDOMAIN_HOSTS)


def interval_for(provider: str, default: float) -> float:
    """The request interval for ``provider``: its own when slower than ``default``, else ``default``."""

    found = spec(provider)
    if found is None or found.min_interval_seconds is None:
        return default
    return max(default, found.min_interval_seconds)


__all__ = [
    "ProviderSpec",
    "board_hosts",
    "board_url",
    "catalog_aliases",
    "interval_for",
    "jobs_from_payload",
    "list_url",
    "provider_names",
    "published_fields",
    "published_kinds",
    "registry",
    "source_from_host",
    "spec",
]
