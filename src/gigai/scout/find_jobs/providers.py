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

0.1.11.12: Workday is the first provider whose board token has three parts
(``<tenant>.wd<N>.<site>``), so one ``{token}`` template cannot build its URLs;
its spec carries :class:`BoardUrlRules`, and :func:`list_url`, :func:`board_url`
and :func:`token_from_list_url` go through the rules when a spec has them. Every
other spec keeps its templates (``url_rules`` is ``None``). The Workday list is a
POST, so its ``token_from_list_url`` is always ``None``: no validator is ever
seeded for it.

Greenhouse keeps two special paths (its one-time ``?content=true`` fill and the
``updated_at`` marker on its detail cache); ``company_index`` and
``ats_board_clients`` still branch on ``"greenhouse"`` for those two things only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .contracts import BOARD_PATH_HOSTS, BOARD_SUBDOMAIN_HOSTS, WORKDAY_BOARD_SUFFIX, ATSProvider, PostingRow, split_workday_token

RowsParser = Callable[..., tuple[PostingRow, ...]]


@dataclass(frozen=True)
class BoardUrlRules:
    """The URLs of a provider whose board token is more than one part (``str.format(token=...)`` cannot split it).

    Each callable answers ``None`` for a token that is not the provider's shape. ``detail_url`` takes the LIST's job
    dict (the posting), because the detail address is a field of it, not the posting id.
    """

    list_url: Callable[[str], str | None]
    board_url: Callable[[str], str | None]
    detail_url: Callable[[str, Mapping[str, object]], str | None]
    #: The board token when ``url`` is this provider's list URL; ``None`` otherwise, and ALWAYS ``None`` when the list is
    #: a POST (``company_index.validators_for_url`` must never seed ``If-None-Match`` for it).
    token_from_list_url: Callable[[str], str | None]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    #: The public, unauthenticated list endpoint; ``{token}`` is the board token. With ``url_rules`` set the template
    #: is the shape only (``{tenant}``/``{pod}``/``{site}``) and ``.format(token=...)`` raises: use :func:`list_url`.
    list_url: str
    #: The board's public page (the catalog's ``board_url``); ``{token}`` (or the shape, as above).
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
    #: ``True`` when a board can run past one list page (``page=0`` in ``list_url``; the payload's ``totalItems``).
    paged: bool = False
    #: 0.1.11.12: how the URLs are built when the token is more than one part; ``None`` = the ``{token}`` templates.
    url_rules: BoardUrlRules | None = None
    #: 0.1.11.12: the board's public page paths, as ``{site}`` templates of the three-part token (``"/{site}/"``), a
    #: robots.txt must allow before its API is asked (``robots_guard``, the stricter scope); ``None`` = the per-URL check only.
    public_paths: tuple[str, ...] | None = None
    #: 0.1.11.12: at most this many requests in flight per provider (``market_acquisition._fetch_boards``); ``None`` = the limit's.
    concurrency: int | None = None

    @property
    def provider(self) -> ATSProvider:
        return ATSProvider(self.name)

    @property
    def published_kind(self) -> str:
        """What a ``published_at`` of this provider means: ``posted``, the day the posting went up (every feed Scout
        reads dates a posting that way when it dates it at all; Pinpoint gives no date, so its postings are undated)."""

        return "posted"


def _workday_host(tenant: str, pod: str) -> str:
    return f"https://{tenant}.{pod}.{WORKDAY_BOARD_SUFFIX}"


def _workday_list_url(token: str) -> str | None:
    parts = split_workday_token(token)
    if parts is None:
        return None
    tenant, pod, site = parts
    return f"{_workday_host(tenant, pod)}/wday/cxs/{tenant}/{site}/jobs"


def _workday_board_url(token: str) -> str | None:
    parts = split_workday_token(token)
    if parts is None:
        return None
    tenant, pod, site = parts
    return f"{_workday_host(tenant, pod)}/{site}"


def _workday_detail_url(token: str, posting: Mapping[str, object]) -> str | None:
    """``.../wday/cxs/<tenant>/<site><externalPath>``: the list row's ``externalPath`` (``/job/<location>/<title>_<reqid>``)."""

    parts = split_workday_token(token)
    path = posting.get("externalPath")
    if parts is None or type(path) is not str or not path.startswith("/") or "//" in path or ".." in path:
        return None
    tenant, pod, site = parts
    return f"{_workday_host(tenant, pod)}/wday/cxs/{tenant}/{site}{path}"


def _workday_token_from_list_url(url: str) -> None:
    """Always ``None``: the Workday list is a POST, which never carries validators."""

    return None


_WORKDAY_URL_RULES = BoardUrlRules(
    list_url=_workday_list_url,
    board_url=_workday_board_url,
    detail_url=_workday_detail_url,
    token_from_list_url=_workday_token_from_list_url,
)


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
            # after ~900 requests, at 1.0 s it ran clean for ~3,400 and was then refused for ten minutes, three
            # times in a row (research/expand-reach/discovery/REPORT.md). 2.0 s is half the rate that was refused:
            # at most 1,800 requests an hour. No setting asks faster (``market_acquisition.provider_interval``).
            min_interval_seconds=2.0,
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
            paged=True,
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
        ),
        ProviderSpec(
            name="workday",
            # The shapes only: the real URLs come from ``url_rules`` (a three-part token; see ``BoardUrlRules``).
            list_url="https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
            board_url="https://{tenant}.{pod}.myworkdayjobs.com/{site}",
            jobs_key="jobPostings",
            # The list row has no scalar id: the requisition id is the ``_<reqid>`` suffix of ``externalPath`` (and
            # ``bulletFields`` is a list), so ``externalPath`` is both keys; ``ats_feeds.workday_rows`` (packet 2)
            # derives the id from it. It is a path, not a URL: ``url_rules.detail_url`` makes the detail address.
            id_key="externalPath",
            title_key="title",
            url_key="externalPath",
            rows=ats_feeds.workday_rows,
            published_field="postedOn",
            updated_field=None,
            list_has_text=False,
            source_hosts=(WORKDAY_BOARD_SUFFIX,),
            min_interval_seconds=1.0,
            aliases=("myworkdayjobs",),
            url_rules=_WORKDAY_URL_RULES,
            # The board's public pages (the shape, as above): a tenant that disallows its site (Zoom, ``Disallow: /Zoom/``)
            # is not asked through ``/wday/cxs/`` either (``robots_guard.RobotsGuard.check_paths``). The site's own
            # paths only: a Disallow on another site of the tenant (its private or invite-only ones) is not a block.
            public_paths=("/{site}/", "/{site}/job/"),
            concurrency=1,
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
    """The list endpoint of ``token``'s board, or ``None`` for an unknown provider or a token not of its shape."""

    found = spec(provider)
    if found is None:
        return None
    if found.url_rules is not None:
        return found.url_rules.list_url(token)
    return found.list_url.format(token=token)


def board_url(provider: str, token: str) -> str | None:
    """The public page of ``token``'s board, or ``None`` for an unknown provider or a token not of its shape."""

    found = spec(provider)
    if found is None:
        return None
    if found.url_rules is not None:
        return found.url_rules.board_url(token)
    return found.board_url.format(token=token)


def token_from_list_url(provider: str, url: str) -> str | None:
    """The board token when ``url`` is ``provider``'s list URL, else ``None`` (a detail URL, another provider's, a POST list)."""

    found = spec(provider)
    if found is None:
        return None
    if found.url_rules is not None:
        return found.url_rules.token_from_list_url(url)
    prefix, _, suffix = found.list_url.partition("{token}")
    if not url.startswith(prefix) or not url.endswith(suffix) or len(url) <= len(prefix) + len(suffix):
        return None
    token = url[len(prefix) : len(url) - len(suffix)]
    return token if token and "/" not in token and "?" not in token else None


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
    """The provider whose ``source_hosts`` domain the host is or sits under, else ``None``.

    A suffix match on whole labels: ``jobs.lever.co`` and ``lever.co`` are Lever's; ``clever.com`` is not ``lever.co``
    and ``jobs.stratagem.com`` is not ``gem.com``.
    """

    lowered = host.lower().rstrip(".")
    for name, item in registry().items():
        if any(lowered == domain or lowered.endswith("." + domain) for domain in item.source_hosts):
            return name
    return None


def board_hosts() -> tuple[str, ...]:
    """Every host (or host suffix) that is a board's own page, not a company site."""

    return tuple(BOARD_PATH_HOSTS) + tuple(BOARD_SUBDOMAIN_HOSTS) + (WORKDAY_BOARD_SUFFIX,)


def interval_for(provider: str, default: float) -> float:
    """The request interval for ``provider``: its own when slower than ``default``, else ``default``."""

    found = spec(provider)
    if found is None or found.min_interval_seconds is None:
        return default
    return max(default, found.min_interval_seconds)


__all__ = [
    "BoardUrlRules",
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
    "token_from_list_url",
]
