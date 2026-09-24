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
from html.parser import HTMLParser
import re
from typing import TYPE_CHECKING

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
    """

    if not html:
        return ""
    if "<" not in html:
        # Already plain text (e.g. descriptionPlain) -- nothing to strip.
        return html.strip()
    parser = _HTMLTextExtractor()
    parser.feed(html)
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
            )
        )
    return tuple(rows)


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
