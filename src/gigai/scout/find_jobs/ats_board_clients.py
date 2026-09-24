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
"""

from __future__ import annotations

from datetime import datetime, timezone
import html as _html_entities
from html.parser import HTMLParser
import re
from typing import TYPE_CHECKING

import pycountry

from .contracts import (
    ATSProvider,
    FindJobsConfig,
    PostingRow,
    SourceKind,
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
_LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


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


def list_greenhouse_board(client: "httpx.Client", board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
    url = _GREENHOUSE_URL.format(token=board_token)
    payload = _request(client, url, "greenhouse", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "greenhouse", board_token)
    company = _company_from_token(board_token)
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
        location = job.get("location")
        location_name = ""
        if type(location) is dict and type(location.get("name")) is str:
            location_name = location["name"]
        content = job.get("content")
        # Greenhouse's `content` is HTML-escaped HTML; keep the readable
        # plain text (U25) and hash *that*, not the raw markup, so the
        # digest tracks the posting's actual wording.
        text = html_to_text(content if type(content) is str else None)
        content_bytes = _text_bytes(title, text or None)
        rows.append(
            PostingRow(
                url=absolute_url,
                normalized_url=normalize_url(absolute_url),
                provider=ATSProvider.GREENHOUSE,
                board_token=board_token,
                company=company,
                title=title,
                location=location_name,
                published_at=_published_at_from_iso(job.get("updated_at")),
                content_sha256=content_hash(content_bytes),
                source_kind=SourceKind.ATS,
                query_key=f"ats:greenhouse:{board_token}",
                text=text or None,
                sponsorship=sponsorship_from_text(text),
            )
        )
    return tuple(rows)


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
    company = _company_from_token(board_token)
    rows: list[PostingRow] = []
    for job in payload:  # type: ignore[union-attr]
        if type(job) is not dict:
            continue
        title = job.get("text")
        if type(title) is not str or not matches_roles(title, config.roles):
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
    company = _company_from_token(board_token)
    rows: list[PostingRow] = []
    for job in payload["jobs"]:  # type: ignore[index]
        if type(job) is not dict:
            continue
        title = job.get("title")
        if type(title) is not str or not matches_roles(title, config.roles):
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
            )
        )
    return tuple(rows)


_LISTERS = {
    "greenhouse": list_greenhouse_board,
    "lever": list_lever_board,
    "ashby": list_ashby_board,
}


class ATSBoardClients:
    """Concrete ``ATSBoardClient`` implementing all three public providers."""

    def list_board(self, client: "httpx.Client", provider: str, board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
        lister = _LISTERS.get(provider)
        if lister is None:
            raise ATSBoardClientError("unsupported_provider", f"unsupported ATS provider {provider!r}")
        return lister(client, board_token, config)


__all__ = [
    "ATSBoardClientError",
    "ATSBoardClients",
    "html_to_text",
    "list_ashby_board",
    "list_greenhouse_board",
    "list_lever_board",
    "matches_roles",
    "parse_board_url",
]
