"""Public ATS board clients for Greenhouse, Lever, and Ashby (A-3).

Implements ``ATSBoardClient`` from ``scout_find_jobs_contracts`` against the
three providers' public, unauthenticated job-board endpoints. Board-token
extraction is not reimplemented here: callers (and this module's own helpers)
use the frozen ``parse_board_url`` for URL -> ``(provider, token)``.

Assumed public API shapes (no auth, read as of 2026-09; each provider may
change its response shape without notice, so failures are treated as
``bad_json``/``http_error`` rather than asserted forever):

* Greenhouse ``GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true``
  -> ``{"jobs": [{"id", "title", "absolute_url", "location": {"name"},
  "updated_at", "content"}]}``.
* Lever ``GET https://api.lever.co/v0/postings/{token}?mode=json``
  -> ``[{"id", "text", "hostedUrl", "categories": {"location"},
  "createdAt" (epoch ms), "descriptionPlain"}]``.
* Ashby ``GET https://api.ashbyhq.com/posting-api/job-board/{token}``
  -> ``{"jobs": [{"id", "title", "location", "jobUrl", "publishedAt",
  "descriptionPlain"}]}``.

Q2 (acquire at scale) adds a second entry point next to ``list_board``:
``ATSBoardClients.fetch_board`` returns the same ``PostingRow`` tuple plus a
``BoardFetchStats`` and, given a ``BoardCache``, makes conditional GETs
(``If-None-Match``/``If-Modified-Since`` from the cached response; a ``304``
or an unchanged body digest is a cache hit). Greenhouse is fetched in two
phases there -- the list WITHOUT ``?content=true`` (small), a title prefilter
(``matches_roles``), then ``GET /v1/boards/{token}/jobs/{id}`` only for the
matching jobs, and only when the job's ``updated_at`` changed since the
cached detail -- so a 1,000-posting board with two matching titles costs one
small list request (or a 304) plus at most two detail requests. Lever and
Ashby lists already carry the description, so their prefilter is applied
in-list and no detail request exists. ``list_board`` (single ``?content=true``
request, no cache) is unchanged: ``job_input.py``'s one-posting fallback and
the older tests still use it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import html as _html_entities
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING

import pycountry

from ...canonical import digest_imported_bytes
from .contracts import (
    ATSProvider,
    FindJobsConfig,
    PayPeriod,
    PostingPay,
    PostingRow,
    SourceKind,
    WorkMode,
    content_hash,
    normalize_url,
    parse_board_url,
)
from .filters import sponsorship_from_text

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx


# Block-level tags that should force a line break in the extracted text so
# paragraphs/list items/headings don't get glued together (U25: keep the
# posting text readable, not a wall of words).
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "table", "blockquote", "section", "article", "header", "footer",
    }
)


class _HTMLTextExtractor(HTMLParser):
    """Minimal stdlib HTML -> plain text, no new dependency (U25).

    Greenhouse's ``content`` field is HTML-escaped HTML (entities decoded by
    ``html.parser`` automatically); this collapses tags to line breaks and
    drops ``<script>``/``<style>`` bodies, keeping only visible text.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        # Collapse runs of horizontal whitespace, but keep line structure.
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in joined.splitlines()]
        collapsed = "\n".join(line for line in lines if line)
        return collapsed.strip()


def html_to_text(html: str | None) -> str:
    """Convert an HTML posting body to readable plain text.

    Uses only :mod:`html.parser` from the standard library (no new
    dependency). A malformed fragment degrades gracefully: ``HTMLParser``
    tolerates unclosed/invalid tags rather than raising, so worst case is
    imperfect line breaks, never an exception. Falls through unchanged when
    ``html`` doesn't look like markup at all (Lever/Ashby's ``descriptionPlain``
    is already plain text).

    0.1.8.1 B3 fix: Greenhouse's ``content`` field is *HTML-escaped HTML* --
    real tags encoded as text, e.g. ``"&lt;p&gt;...&lt;/p&gt;"`` rather than
    ``"<p>...</p>"`` (confirmed against a live evidence run's raw payload:
    ``ats_board_clients.py``'s own module docstring already said this, but
    the code never actually decoded that outer layer of escaping). The old
    ``"<" not in html`` check saw no literal ``<`` in that escaped string,
    took the "already plain text" branch, and returned the raw
    entity-escaped soup completely unprocessed -- which broke
    ``sponsorship_from_text``'s phrase matching for every Greenhouse
    posting (0.1.8.1 B3: acquire.json showed 69/69 "sponsorship unknown";
    the Greenhouse share of those rows all had this exact symptom, still
    carrying literal ``"&amp;nbsp;"``/``"&lt;li&gt;"`` in their stored
    ``text``). Decoding entities *before* checking for ``<`` reveals the
    real tags underneath (or, for genuinely plain text -- Lever/Ashby's
    ``descriptionPlain`` -- is a safe no-op/idempotent pass that only
    resolves any literal ``&amp;``-style entities that plain text might
    itself contain, which is the correct display form either way).
    """

    if not html:
        return ""
    decoded = _html_entities.unescape(html)
    if "<" not in decoded:
        # Already plain text (e.g. descriptionPlain) -- nothing to strip.
        return decoded.strip()
    parser = _HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    return parser.text()


class ATSBoardClientError(ValueError):
    """A redacted ATS board-listing failure.

    Messages never include response bodies, headers, or credentials -- only
    the provider, board token, and a stable ``code``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
# Q2 two-phase Greenhouse: the content-free list, then one detail per match.
_GREENHOUSE_LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
_GREENHOUSE_JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
_LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
# Q4b-data: Ashby's public board API omits ``compensation`` unless asked
# (``includeCompensation=true``); it is the same single request, so the pay
# range costs no extra call. The param is part of the cached URL, so every
# Ashby board misses its per-board cache exactly once after this change.
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def matches_roles(title: str, roles: tuple[str, ...]) -> bool:
    """Case-insensitive token containment: any configured role's words all
    appear (as substrings) in the title.

    A role matches when every whitespace-separated token in that role string
    appears as a substring of the lowercased title. An empty ``roles`` tuple
    matches nothing (fail closed, not fail open).
    """

    if type(title) is not str:
        return False
    title_lower = title.lower()
    for role in roles:
        tokens = [token for token in role.lower().split() if token]
        if tokens and all(token in title_lower for token in tokens):
            return True
    return False


def _redacted_fail(code: str, provider: str, board_token: str) -> None:
    raise ATSBoardClientError(code, f"{provider} board {board_token!r} request failed")


def _request(client: "httpx.Client", url: str, provider: str, board_token: str) -> object:
    import httpx

    try:
        response = client.get(url)
    except httpx.HTTPError:
        _redacted_fail("network_error", provider, board_token)
        raise AssertionError("unreachable")
    if response.status_code != 200:
        _redacted_fail("http_error", provider, board_token)
    try:
        return response.json()
    except ValueError:
        _redacted_fail("bad_json", provider, board_token)
        raise AssertionError("unreachable")


def _company_from_token(board_token: str) -> str:
    return board_token


def _text_bytes(*parts: str | None) -> bytes:
    return "\n".join(part for part in parts if part).encode("utf-8")


def _published_at_from_iso(value: object) -> str | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value


def _normalize_country(value: object) -> str | None:
    """Normalize a provider's own country string/code to ISO alpha-2.

    Only exact matches against :mod:`pycountry`'s alpha-2/alpha-3/name/
    official_name/common_name index (``pycountry.countries.lookup``) are
    accepted -- never ``search_fuzzy``, which fuzzy-matches short/garbage
    tokens like "AMER" to an unrelated country (confirmed against
    ``pycountry`` directly: ``search_fuzzy("AMER")`` returns American Samoa/
    Cameroon/the US) and would silently turn a region code into a false
    country match, exactly the 0.1.8.1 B1 bug. A leading "The " (Ashby's
    ``secondaryLocations`` sometimes sends "The Netherlands") is stripped
    once and retried, since pycountry's own name for that country is just
    "Netherlands". Anything else -- a region token, "Remote", an internal
    label like "z-Test & Templates Only", or an already-ISO alpha-2 code --
    either resolves deterministically or returns ``None`` (never a guess).
    """

    if type(value) is not str or not value.strip():
        return None
    candidate = value.strip()
    try:
        return pycountry.countries.lookup(candidate).alpha_2
    except LookupError:
        pass
    if candidate.lower().startswith("the "):
        try:
            return pycountry.countries.lookup(candidate[4:].strip()).alpha_2
        except LookupError:
            pass
    return None


def _published_at_from_epoch_ms(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return parsed.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Q4b-data: work mode + pay, read ONLY from each provider's structured fields
# (never from the location or description text). Every helper returns None
# when the payload does not state the value, so the row omits the field.
# ---------------------------------------------------------------------------

_WORK_MODE_LABELS = {
    "remote": WorkMode.REMOTE,
    "hybrid": WorkMode.HYBRID,
    "onsite": WorkMode.ONSITE,
}
# Lever ``salaryRange.interval`` -> PayPeriod. Lever also quotes
# ``per-week-salary``/``semi-month-salary``/``bi-week-salary``/``per-day-wage``/
# ``one-time``: an interval the contract cannot express is a STATED interval
# we would misreport as "no period", so such a range is skipped entirely.
_LEVER_INTERVALS = {
    "per-year-salary": PayPeriod.YEAR,
    "per-month-salary": PayPeriod.MONTH,
    "per-hour-wage": PayPeriod.HOUR,
}
# Ashby compensation component ``interval`` -> PayPeriod (same skip rule for
# an interval outside this table, e.g. ``NONE`` on a one-time component).
_ASHBY_INTERVALS = {
    "1 YEAR": PayPeriod.YEAR,
    "1 MONTH": PayPeriod.MONTH,
    "1 HOUR": PayPeriod.HOUR,
}


def work_mode_from_label(value: object) -> WorkMode | None:
    """A provider's workplace-type label -> :class:`WorkMode`, or ``None``.

    Accepts Ashby's ``Remote``/``Hybrid``/``OnSite`` and Lever's
    ``remote``/``hybrid``/``onsite``/``on-site`` spellings (case and
    punctuation ignored). ``unspecified``, ``null``, a free-text label or a
    non-string all mean "not stated" -> ``None``.
    """

    if type(value) is not str:
        return None
    return _WORK_MODE_LABELS.get(re.sub(r"[^a-z]", "", value.lower()))


def _amount(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0:  # NaN / negative
        return None
    return value


def _cents_to_units(value: object) -> int | float | None:
    amount = _amount(value)
    if amount is None:
        return None
    units = amount / 100
    return int(units) if float(units).is_integer() else units


def _pay(minimum: object, maximum: object, currency: object, period: PayPeriod | None) -> PostingPay | None:
    """A :class:`PostingPay` when at least one bound and a currency are stated."""

    low, high = _amount(minimum), _amount(maximum)
    if low is None and high is None:
        return None
    if type(currency) is not str or not currency.strip():
        return None
    if low is not None and high is not None and low > high:
        return None
    return PostingPay(low, high, currency.strip().upper(), period)


def lever_work_mode(job: dict[str, object]) -> WorkMode | None:
    """Lever ``workplaceType`` (``remote``/``hybrid``/``onsite``/``unspecified``)."""

    return work_mode_from_label(job.get("workplaceType"))


def lever_pay(job: dict[str, object]) -> PostingPay | None:
    """Lever ``salaryRange: {min, max, currency, interval}`` -> pay, when stated."""

    salary_range = job.get("salaryRange")
    if type(salary_range) is not dict:
        return None
    interval = salary_range.get("interval")
    period: PayPeriod | None = None
    if type(interval) is str and interval.strip():
        period = _LEVER_INTERVALS.get(interval.strip().lower())
        if period is None:
            return None
    return _pay(salary_range.get("min"), salary_range.get("max"), salary_range.get("currency"), period)


def ashby_work_mode(job: dict[str, object]) -> WorkMode | None:
    """Ashby ``workplaceType`` (``Remote``/``Hybrid``/``OnSite``), else ``isRemote: true``.

    ``isRemote: false`` alone says nothing about hybrid vs. on-site, so it
    never sets a mode.
    """

    mode = work_mode_from_label(job.get("workplaceType"))
    if mode is not None:
        return mode
    return WorkMode.REMOTE if job.get("isRemote") is True else None


def ashby_pay(job: dict[str, object]) -> PostingPay | None:
    """Ashby ``compensation`` -> the first stated ``Salary`` component's range.

    Reads ``compensation.summaryComponents`` (the board API's flattened
    view), falling back to the first tier's ``components``. Only the
    ``Salary`` component is a pay range (``Bonus``/``Commission``/equity
    components are not); its ``interval`` (``1 YEAR``/``1 MONTH``/``1 HOUR``)
    is the period. The compensation object arrives only when the board was
    fetched with ``includeCompensation=true`` (see ``_ASHBY_URL``).
    """

    compensation = job.get("compensation")
    if type(compensation) is not dict:
        return None
    components: object = compensation.get("summaryComponents")
    if type(components) is not list or not components:
        tiers = compensation.get("compensationTiers")
        components = None
        if type(tiers) is list and tiers and type(tiers[0]) is dict:
            components = tiers[0].get("components")
    if type(components) is not list:
        return None
    for component in components:
        if type(component) is not dict:
            continue
        kind = component.get("compensationType")
        if type(kind) is not str or kind.strip().lower() != "salary":
            continue
        interval = component.get("interval")
        period: PayPeriod | None = None
        if type(interval) is str and interval.strip():
            period = _ASHBY_INTERVALS.get(" ".join(interval.upper().split()))
            if period is None:
                continue
        pay = _pay(component.get("minValue"), component.get("maxValue"), component.get("currencyCode"), period)
        if pay is not None:
            return pay
    return None


def greenhouse_pay(job: dict[str, object]) -> PostingPay | None:
    """Greenhouse ``pay_input_ranges[] {min_cents, max_cents, currency_type}`` -> pay.

    Only what the already-fetched list/detail payload carries (no extra
    request): the first usable range, cents converted to units. Greenhouse
    states no interval, so ``period`` is ``None`` -- never inferred
    (coordinator answer, 2026-09-25). Greenhouse has no workplace-type
    field either, so its rows never carry ``work_mode``.
    """

    ranges = job.get("pay_input_ranges")
    if type(ranges) is not list:
        return None
    for item in ranges:
        if type(item) is not dict:
            continue
        pay = _pay(_cents_to_units(item.get("min_cents")), _cents_to_units(item.get("max_cents")), item.get("currency_type"), None)
        if pay is not None:
            return pay
    return None


def list_greenhouse_board(client: "httpx.Client", board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
    url = _GREENHOUSE_URL.format(token=board_token)
    payload = _request(client, url, "greenhouse", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "greenhouse", board_token)
    rows: list[PostingRow] = []
    for job in payload["jobs"]:  # type: ignore[index]
        if type(job) is not dict:
            continue
        title = job.get("title")
        if type(title) is not str or not matches_roles(title, config.roles):
            continue
        absolute_url = job.get("absolute_url")
        if type(absolute_url) is not str or not absolute_url:
            continue
        content = job.get("content")
        rows.append(_greenhouse_row(job, title, absolute_url, content if type(content) is str else None, board_token))
    return tuple(rows)


def _greenhouse_row(
    job: dict[str, object],
    title: str,
    absolute_url: str,
    content: str | None,
    board_token: str,
    detail: dict[str, object] | None = None,
) -> PostingRow:
    """One Greenhouse job (+ its HTML ``content``, from the list or a detail call) -> ``PostingRow``.

    ``detail`` is the already-fetched detail payload when the two-phase
    fetch made one; its ``pay_input_ranges`` win over the list item's.
    """

    pay = greenhouse_pay(detail) if detail is not None else None
    if pay is None:
        pay = greenhouse_pay(job)
    location = job.get("location")
    location_name = ""
    if type(location) is dict and type(location.get("name")) is str:
        location_name = location["name"]
    # Greenhouse's `content` is HTML-escaped HTML; keep the readable plain
    # text (U25) and hash *that*, not the raw markup, so the digest tracks
    # the posting's actual wording.
    text = html_to_text(content)
    content_bytes = _text_bytes(title, text or None)
    return PostingRow(
        url=absolute_url,
        normalized_url=normalize_url(absolute_url),
        provider=ATSProvider.GREENHOUSE,
        board_token=board_token,
        company=_company_from_token(board_token),
        title=title,
        location=location_name,
        published_at=_published_at_from_iso(job.get("updated_at")),
        content_sha256=content_hash(content_bytes),
        source_kind=SourceKind.ATS,
        query_key=f"ats:greenhouse:{board_token}",
        text=text or None,
        sponsorship=sponsorship_from_text(text),
        pay=pay,
    )


def _lever_lists_text(lists: object) -> str:
    """Flatten Lever's ``lists`` array (structured sections) into text.

    Each item is ``{"text": <section heading>, "content": <HTML>}``
    (Requirements, Responsibilities, Benefits, ...). Not every board uses
    this; a missing/malformed value yields an empty string.
    """

    if type(lists) is not list:
        return ""
    sections: list[str] = []
    for item in lists:
        if type(item) is not dict:
            continue
        heading = item.get("text")
        body = html_to_text(item.get("content") if type(item.get("content")) is str else None)
        section = "\n".join(part for part in (heading if type(heading) is str else None, body) if part)
        if section:
            sections.append(section)
    return "\n\n".join(sections)


def _lever_countries(job: dict[str, object]) -> tuple[str, ...] | None:
    """Lever's structured country signal: ``country`` + ``categories.allLocations``.

    Lever's own docs (``.orchestrator/research/country-data.md`` §2): a plain
    JSON object field ``"country"`` -- an ISO alpha-2 code, or ``null`` for
    "unknown country" -- present on every posting in the exact ``?mode=json``
    list response Scout already requests. When present and non-null, this
    *is* the trusted answer (normalized/validated through
    :func:`_normalize_country` since Lever's value is already ISO-2, so this
    is a validation pass, not a guess). ``categories.allLocations`` is a
    structured array covering multi-location postings the same way
    ``categories.location``'s free text does today; each entry is itself
    free text (no per-location country code), so it's normalized the same
    way as the primary ``location_name`` string would be, via
    :func:`_normalize_country` on each entry, adding to the primary
    ``country`` signal rather than replacing it. Returns ``None`` (no
    structured signal at all) only when neither ``country`` nor any
    ``allLocations`` entry resolves -- callers then fall back to parsing the
    free-text ``location`` string, same as before this packet.
    """

    found: set[str] = set()
    primary = _normalize_country(job.get("country"))
    if primary is not None:
        found.add(primary)
    categories = job.get("categories")
    all_locations = categories.get("allLocations") if type(categories) is dict else None
    if type(all_locations) is list:
        for entry in all_locations:
            code = _normalize_country(entry)
            if code is not None:
                found.add(code)
    if not found:
        return None
    return tuple(sorted(found))


def list_lever_board(client: "httpx.Client", board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
    url = _LEVER_URL.format(token=board_token)
    payload = _request(client, url, "lever", board_token)
    if type(payload) is not list:
        _redacted_fail("bad_json", "lever", board_token)
    return _lever_rows(payload, board_token, config)  # type: ignore[arg-type]


def _lever_rows(payload: list, board_token: str, config: FindJobsConfig, stats: "BoardFetchStats | None" = None) -> tuple[PostingRow, ...]:
    company = _company_from_token(board_token)
    rows: list[PostingRow] = []
    for job in payload:
        if type(job) is not dict:
            continue
        if stats is not None:
            stats.listed += 1
        title = job.get("text")
        if type(title) is not str or not matches_roles(title, config.roles):
            if stats is not None:
                stats.prefiltered_out += 1
            continue
        hosted_url = job.get("hostedUrl")
        if type(hosted_url) is not str or not hosted_url:
            continue
        categories = job.get("categories")
        location_name = ""
        if type(categories) is dict and type(categories.get("location")) is str:
            location_name = categories["location"]
        countries = _lever_countries(job)
        description = job.get("descriptionPlain")
        # Lever's descriptionPlain is already plain text (occasionally with
        # simple list markup); html_to_text is a no-op on text with no tags
        # and still normalizes the rare HTML fragment. `lists` is a separate
        # array of structured sections (e.g. Requirements/Benefits), each
        # with its own `text` heading and HTML `content`; append them so the
        # full posting body (not just the intro paragraph) reaches assess.
        text = html_to_text(description if type(description) is str else None)
        text = "\n\n".join(part for part in (text, _lever_lists_text(job.get("lists"))) if part)
        content_bytes = _text_bytes(title, text or None)
        rows.append(
            PostingRow(
                url=hosted_url,
                normalized_url=normalize_url(hosted_url),
                provider=ATSProvider.LEVER,
                board_token=board_token,
                company=company,
                title=title,
                location=location_name,
                published_at=_published_at_from_epoch_ms(job.get("createdAt")),
                content_sha256=content_hash(content_bytes),
                source_kind=SourceKind.ATS,
                query_key=f"ats:lever:{board_token}",
                text=text or None,
                sponsorship=sponsorship_from_text(text),
                countries=countries,
                work_mode=lever_work_mode(job),
                pay=lever_pay(job),
            )
        )
    return tuple(rows)


def _ashby_address_country(address: object) -> str | None:
    """Normalize one Ashby ``address`` object's ``postalAddress.addressCountry``.

    Ashby's own doc shape (`.orchestrator/research/country-data.md` §2):
    ``address`` -> ``postalAddress`` -> ``addressCountry``, all optionally
    ``null``/missing (confirmed against the evidence run's raw payload: a
    fully remote/region-labelled posting has ``"address": null``; a
    located one sends a country *name* like ``"United States"``, not an
    ISO code -- so this always goes through :func:`_normalize_country`,
    never trusted as already-ISO the way Lever's ``country`` is).
    """

    if type(address) is not dict:
        return None
    postal = address.get("postalAddress")
    if type(postal) is not dict:
        return None
    return _normalize_country(postal.get("addressCountry"))


def _ashby_countries(job: dict[str, object]) -> tuple[str, ...] | None:
    """Ashby's structured country signal: ``address`` + ``secondaryLocations``.

    The primary ``address.postalAddress.addressCountry`` covers the job's
    main location; ``secondaryLocations`` (each with its own optional
    ``address`` of the identical shape, confirmed in the evidence run) covers
    additional locations the same way Lever's ``categories.allLocations``
    does -- structured where present, but each entry can itself have a
    ``null`` address (evidence run: several ``secondaryLocations`` entries
    carry only a free-text ``location`` name with no ``address`` at all), in
    which case that one entry contributes nothing and the row falls back to
    whatever the primary signal (or free-text ``location`` parsing) found.
    Returns ``None`` when nothing structured resolves at all.
    """

    found: set[str] = set()
    primary = _ashby_address_country(job.get("address"))
    if primary is not None:
        found.add(primary)
    secondary = job.get("secondaryLocations")
    if type(secondary) is list:
        for entry in secondary:
            if type(entry) is not dict:
                continue
            code = _ashby_address_country(entry.get("address"))
            if code is not None:
                found.add(code)
    if not found:
        return None
    return tuple(sorted(found))


def list_ashby_board(client: "httpx.Client", board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
    url = _ASHBY_URL.format(token=board_token)
    payload = _request(client, url, "ashby", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "ashby", board_token)
    return _ashby_rows(payload["jobs"], board_token, config)  # type: ignore[index]


def _ashby_rows(jobs: list, board_token: str, config: FindJobsConfig, stats: "BoardFetchStats | None" = None) -> tuple[PostingRow, ...]:
    company = _company_from_token(board_token)
    rows: list[PostingRow] = []
    for job in jobs:
        if type(job) is not dict:
            continue
        if stats is not None:
            stats.listed += 1
        title = job.get("title")
        if type(title) is not str or not matches_roles(title, config.roles):
            if stats is not None:
                stats.prefiltered_out += 1
            continue
        job_url = job.get("jobUrl")
        if type(job_url) is not str or not job_url:
            continue
        location = job.get("location")
        location_name = location if type(location) is str else ""
        countries = _ashby_countries(job)
        description = job.get("descriptionPlain")
        text = html_to_text(description if type(description) is str else None)
        content_bytes = _text_bytes(title, text or None)
        rows.append(
            PostingRow(
                url=job_url,
                normalized_url=normalize_url(job_url),
                provider=ATSProvider.ASHBY,
                board_token=board_token,
                company=company,
                title=title,
                location=location_name,
                published_at=_published_at_from_iso(job.get("publishedAt")),
                content_sha256=content_hash(content_bytes),
                source_kind=SourceKind.ATS,
                query_key=f"ats:ashby:{board_token}",
                countries=countries,
                text=text or None,
                sponsorship=sponsorship_from_text(text),
                work_mode=ashby_work_mode(job),
                pay=ashby_pay(job),
            )
        )
    return tuple(rows)


_LISTERS = {
    "greenhouse": list_greenhouse_board,
    "lever": list_lever_board,
    "ashby": list_ashby_board,
}


# ---------------------------------------------------------------------------
# Q2: acquire at scale -- per-board cache, conditional GETs, two-phase
# Greenhouse with a title prefilter before any detail request.
# ---------------------------------------------------------------------------


@dataclass
class BoardFetchStats:
    """What one ``fetch_board`` call cost; summed per run for progress/measurement."""

    requests: int = 0
    #: "miss" (fresh body stored), "hit" (304 / unchanged marker, no body
    #: transferred), "revalidated" (200 with an unchanged digest), or
    #: "bypass" (no cache given).
    cache: str = "bypass"
    listed: int = 0
    prefiltered_out: int = 0
    detail_fetched: int = 0
    detail_cached: int = 0
    detail_failed: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "cache": self.cache,
            "listed": self.listed,
            "prefiltered_out": self.prefiltered_out,
            "detail_fetched": self.detail_fetched,
            "detail_cached": self.detail_cached,
            "detail_failed": self.detail_failed,
        }


@dataclass(frozen=True)
class BoardFetchResult:
    rows: tuple[PostingRow, ...]
    stats: BoardFetchStats


@dataclass(frozen=True)
class CachedResponse:
    etag: str | None
    last_modified: str | None
    sha256: str
    marker: str | None
    body: bytes


# acquire-rotation: the per-board "last fetch attempt" index + rotation cursor.
LAST_FETCHED_SCHEMA = "scout-ats-last-fetched:1"
_LAST_FETCHED_FILENAME = "last-fetched.json"


@dataclass(frozen=True)
class BoardFetchIndex:
    """When each watchlist board was last *attempted*, plus the rotation cursor.

    ``boards`` maps ``"<provider>:<board token>"`` to the UTC ISO-8601 stamp
    of the run that last attempted it (fetched, served from the cache by a
    ``304``, or failed -- a dead board must rotate like a live one, or it
    would lead every run forever). ``cycle``/``cycle_started_at`` is the
    rotation cursor: a board stamped at or after ``cycle_started_at`` has
    been covered in the current rotation; when every planned board has,
    the next run starts a new cycle. ``page_sizes`` is how many boards per
    provider the previous run attempted, so the next run can say "full
    rotation every ~K runs" before its own page is measured.

    This is a CACHE, not a record: it lives next to the response cache
    under the GigAI home, never in a workpad or the journal, and losing it
    merely restarts the rotation from "nothing fetched yet".
    """

    boards: dict[str, str] = field(default_factory=dict)
    cycle: int = 1
    cycle_started_at: str | None = None
    page_sizes: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def key(provider: str, board_token: str) -> str:
        return f"{provider}:{board_token}"

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": LAST_FETCHED_SCHEMA,
            "cycle": self.cycle,
            "cycle_started_at": self.cycle_started_at,
            "page_sizes": dict(self.page_sizes),
            "boards": dict(self.boards),
        }

    @classmethod
    def from_json(cls, payload: object) -> "BoardFetchIndex":
        """A missing, corrupt or foreign-schema payload reads as the empty index."""

        if not isinstance(payload, dict) or payload.get("schema_version") != LAST_FETCHED_SCHEMA:
            return cls()
        raw_boards = payload.get("boards")
        boards = (
            {key: value for key, value in raw_boards.items() if isinstance(key, str) and isinstance(value, str)}
            if isinstance(raw_boards, dict)
            else {}
        )
        raw_cycle = payload.get("cycle")
        cycle = raw_cycle if isinstance(raw_cycle, int) and not isinstance(raw_cycle, bool) and raw_cycle >= 1 else 1
        started = payload.get("cycle_started_at")
        raw_pages = payload.get("page_sizes")
        page_sizes = (
            {
                key: value
                for key, value in raw_pages.items()
                if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0
            }
            if isinstance(raw_pages, dict)
            else {}
        )
        return cls(boards=boards, cycle=cycle, cycle_started_at=started if isinstance(started, str) else None, page_sizes=page_sizes)


class BoardCache:
    """A per-URL response cache for the public board endpoints.

    Layout: ``<root>/<provider>/<sha256(url)[:40]>.json`` (etag,
    last-modified, body digest, an optional caller ``marker`` such as a
    Greenhouse job's ``updated_at``, the URL) next to ``...body.gz``. Every
    write is temp-file + ``os.replace``; a missing or corrupt entry reads as
    a miss, never an error. Lives under the GigAI home (``<home>/cache/scout/
    ats-boards``, next to the H-1B cache), never inside a managed workpad,
    so it can be deleted freely and never dirties a journal. Response
    *headers* other than ``ETag``/``Last-Modified`` are never stored.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _paths(self, provider: str, url: str) -> tuple[Path, Path]:
        name = digest_imported_bytes(url.encode("utf-8")).removeprefix("sha256:")[:40]
        base = self.root / re.sub(r"[^a-z0-9_-]+", "_", provider.lower() or "other")
        return base / f"{name}.json", base / f"{name}.body.gz"

    def lookup(self, provider: str, url: str) -> CachedResponse | None:
        meta_path, body_path = self._paths(provider, url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = gzip.decompress(body_path.read_bytes())
        except (OSError, ValueError, EOFError):
            return None
        if not isinstance(meta, dict) or meta.get("url") != url:
            return None
        sha = meta.get("sha256")
        if not isinstance(sha, str) or digest_imported_bytes(body) != sha:
            return None
        etag = meta.get("etag")
        last_modified = meta.get("last_modified")
        marker = meta.get("marker")
        return CachedResponse(
            etag if isinstance(etag, str) else None,
            last_modified if isinstance(last_modified, str) else None,
            sha,
            marker if isinstance(marker, str) else None,
            body,
        )

    def store(
        self,
        provider: str,
        url: str,
        *,
        body: bytes,
        etag: str | None,
        last_modified: str | None,
        marker: str | None,
    ) -> None:
        meta_path, body_path = self._paths(provider, url)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "url": url,
            "sha256": digest_imported_bytes(body),
            "etag": etag,
            "last_modified": last_modified,
            "marker": marker,
            "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        pid = os.getpid()
        tmp_body = body_path.with_name(body_path.name + f".tmp{pid}")
        tmp_body.write_bytes(gzip.compress(body, compresslevel=6))
        os.replace(tmp_body, body_path)
        tmp_meta = meta_path.with_name(meta_path.name + f".tmp{pid}")
        tmp_meta.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp_meta, meta_path)

    # -- acquire-rotation: the last-fetched index ---------------------------

    @property
    def fetch_index_path(self) -> Path:
        return self.root / _LAST_FETCHED_FILENAME

    def load_fetch_index(self) -> BoardFetchIndex:
        """Read ``<root>/last-fetched.json``; missing or corrupt reads as empty (a fresh rotation)."""

        try:
            payload = json.loads(self.fetch_index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return BoardFetchIndex()
        return BoardFetchIndex.from_json(payload)

    def store_fetch_index(self, index: BoardFetchIndex) -> None:
        """Replace the index atomically (temp file + ``os.replace``); one write per call, never per board."""

        path = self.fetch_index_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(index.to_json(), separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def conditional_headers(entry: CachedResponse | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if entry is None:
            return headers
        if entry.etag:
            headers["If-None-Match"] = entry.etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        return headers


def _cached_request(
    client: "httpx.Client",
    url: str,
    provider: str,
    board_token: str,
    *,
    cache: BoardCache | None,
    stats: BoardFetchStats,
    marker: str | None = None,
) -> tuple[bytes, str]:
    """GET ``url`` through the cache; returns ``(body, cache_status)``.

    ``marker`` (Greenhouse: the job's ``updated_at`` from the list) short-
    circuits without any request when the cached entry carries the same
    marker. Otherwise a conditional GET: ``304`` -> the cached body
    (``hit``); ``200`` with an unchanged digest -> ``revalidated``; a new
    body -> ``miss`` and the entry is rewritten. Failure codes are exactly
    ``_request``'s (``network_error``/``http_error``), redacted the same way.
    """

    import httpx

    entry = cache.lookup(provider, url) if cache is not None else None
    if entry is not None and marker is not None and entry.marker == marker:
        return entry.body, "hit"
    headers = BoardCache.conditional_headers(entry)
    try:
        response = client.get(url, headers=headers) if headers else client.get(url)
    except httpx.HTTPError:
        _redacted_fail("network_error", provider, board_token)
        raise AssertionError("unreachable")
    stats.requests += 1
    if response.status_code == 304 and entry is not None:
        if cache is not None and marker != entry.marker:
            cache.store(provider, url, body=entry.body, etag=entry.etag, last_modified=entry.last_modified, marker=marker)
        return entry.body, "hit"
    if response.status_code != 200:
        _redacted_fail("http_error", provider, board_token)
    body = bytes(response.content)
    status = "revalidated" if entry is not None and entry.sha256 == digest_imported_bytes(body) else "miss"
    if cache is not None:
        cache.store(
            provider,
            url,
            body=body,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            marker=marker,
        )
    elif entry is None:
        status = "bypass"
    return body, status


def _decode_json(body: bytes, provider: str, board_token: str) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _redacted_fail("bad_json", provider, board_token)
        raise AssertionError("unreachable")


def fetch_greenhouse_board(
    client: "httpx.Client",
    board_token: str,
    config: FindJobsConfig,
    *,
    cache: BoardCache | None = None,
    stats: BoardFetchStats | None = None,
) -> BoardFetchResult:
    """Two-phase Greenhouse: content-free list -> title prefilter -> detail per match.

    A detail request is made only for a job whose title matches the roles
    AND whose ``updated_at`` differs from the cached detail's marker. A
    failed detail call (a 404, a shape change) falls back to the list's
    inline ``content`` when it carries one, else the row ships without text
    (sponsorship unknown) -- one bad posting never fails the board.
    """

    stats = stats if stats is not None else BoardFetchStats()
    list_url = _GREENHOUSE_LIST_URL.format(token=board_token)
    body, status = _cached_request(client, list_url, "greenhouse", board_token, cache=cache, stats=stats)
    stats.cache = status
    payload = _decode_json(body, "greenhouse", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "greenhouse", board_token)
    rows: list[PostingRow] = []
    for job in payload["jobs"]:  # type: ignore[index]
        if type(job) is not dict:
            continue
        stats.listed += 1
        title = job.get("title")
        if type(title) is not str or not matches_roles(title, config.roles):
            stats.prefiltered_out += 1
            continue
        absolute_url = job.get("absolute_url")
        if type(absolute_url) is not str or not absolute_url:
            continue
        inline = job.get("content")
        content: str | None = inline if type(inline) is str else None
        detail: dict[str, object] | None = None
        job_id = job.get("id")
        if isinstance(job_id, (int, str)) and not isinstance(job_id, bool) and str(job_id):
            detail_url = _GREENHOUSE_JOB_URL.format(token=board_token, job_id=job_id)
            updated_at = job.get("updated_at")
            marker = updated_at if type(updated_at) is str and updated_at else None
            before = stats.requests
            try:
                detail_body, _detail_status = _cached_request(
                    client, detail_url, "greenhouse", board_token, cache=cache, stats=stats, marker=marker
                )
                detail = json.loads(detail_body.decode("utf-8"))
                if type(detail) is not dict or type(detail.get("content")) is not str:
                    detail = None
                    raise ValueError("greenhouse detail has no content")
                content = detail["content"]
                if stats.requests == before:
                    stats.detail_cached += 1
                else:
                    stats.detail_fetched += 1
            except (ATSBoardClientError, ValueError, UnicodeDecodeError):
                stats.detail_failed += 1
        rows.append(_greenhouse_row(job, title, absolute_url, content, board_token, detail))
    return BoardFetchResult(tuple(rows), stats)


def fetch_lever_board(
    client: "httpx.Client",
    board_token: str,
    config: FindJobsConfig,
    *,
    cache: BoardCache | None = None,
    stats: BoardFetchStats | None = None,
) -> BoardFetchResult:
    stats = stats if stats is not None else BoardFetchStats()
    body, status = _cached_request(client, _LEVER_URL.format(token=board_token), "lever", board_token, cache=cache, stats=stats)
    stats.cache = status
    payload = _decode_json(body, "lever", board_token)
    if type(payload) is not list:
        _redacted_fail("bad_json", "lever", board_token)
    return BoardFetchResult(_lever_rows(payload, board_token, config, stats), stats)  # type: ignore[arg-type]


def fetch_ashby_board(
    client: "httpx.Client",
    board_token: str,
    config: FindJobsConfig,
    *,
    cache: BoardCache | None = None,
    stats: BoardFetchStats | None = None,
) -> BoardFetchResult:
    stats = stats if stats is not None else BoardFetchStats()
    body, status = _cached_request(client, _ASHBY_URL.format(token=board_token), "ashby", board_token, cache=cache, stats=stats)
    stats.cache = status
    payload = _decode_json(body, "ashby", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "ashby", board_token)
    return BoardFetchResult(_ashby_rows(payload["jobs"], board_token, config, stats), stats)  # type: ignore[index]


_FETCHERS = {
    "greenhouse": fetch_greenhouse_board,
    "lever": fetch_lever_board,
    "ashby": fetch_ashby_board,
}


class ATSBoardClients:
    """Concrete ``ATSBoardClient`` implementing all three public providers."""

    def list_board(self, client: "httpx.Client", provider: str, board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
        lister = _LISTERS.get(provider)
        if lister is None:
            raise ATSBoardClientError("unsupported_provider", f"unsupported ATS provider {provider!r}")
        return lister(client, board_token, config)

    def fetch_board(
        self,
        client: "httpx.Client",
        provider: str,
        board_token: str,
        config: FindJobsConfig,
        *,
        cache: BoardCache | None = None,
    ) -> BoardFetchResult:
        """Q2: the cached, prefiltered path acquire uses (see module docstring)."""

        fetcher = _FETCHERS.get(provider)
        if fetcher is None:
            raise ATSBoardClientError("unsupported_provider", f"unsupported ATS provider {provider!r}")
        return fetcher(client, board_token, config, cache=cache)


__all__ = [
    "ATSBoardClientError",
    "ATSBoardClients",
    "BoardCache",
    "BoardFetchIndex",
    "BoardFetchResult",
    "BoardFetchStats",
    "CachedResponse",
    "LAST_FETCHED_SCHEMA",
    "ashby_pay",
    "ashby_work_mode",
    "fetch_ashby_board",
    "fetch_greenhouse_board",
    "fetch_lever_board",
    "greenhouse_pay",
    "html_to_text",
    "lever_pay",
    "lever_work_mode",
    "list_ashby_board",
    "list_greenhouse_board",
    "list_lever_board",
    "matches_roles",
    "parse_board_url",
    "work_mode_from_label",
]
