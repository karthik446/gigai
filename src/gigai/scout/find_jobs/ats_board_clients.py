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

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx


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
        content_bytes = _text_bytes(title, content if type(content) is str else None)
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
            )
        )
    return tuple(rows)


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
        content_bytes = _text_bytes(title, description if type(description) is str else None)
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
        content_bytes = _text_bytes(title, description if type(description) is str else None)
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
    "list_ashby_board",
    "list_greenhouse_board",
    "list_lever_board",
    "matches_roles",
    "parse_board_url",
]
