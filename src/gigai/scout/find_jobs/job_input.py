"""Resolve a quick-assessment job input (URL or pasted text) to its text (P4).

One public URL is fetched once and stripped to plain text.  Order, per the
v0.1.9 plan (§P4, operator answer 6):

1. ``job_text`` given: no network; identity is ``text:<sha256 digest>``.
2. Greenhouse URL with a numeric job id: the public single-job endpoint
   ``boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?content=true``
   (proven live by S29 r1's fetch, 15/15).
3. Otherwise GET the page itself (redirects followed, 2 MiB body cap) and
   run ``html_to_text`` on it.
4. If the URL is on an ATS host and the page had (almost) no text -- a
   JavaScript shell -- list the board through the existing
   ``ATSBoardClients`` and match the row by normalized URL (or, for
   Greenhouse, by job id).  Lever/Ashby always take this path when their
   pages are shells; there is no known single-job JSON for them (S30 Q1).
5. A page with no posting text fails ``job_text_unavailable`` (the caller is
   told to pass ``--job-text``); a network/HTTP failure with nothing to fall
   back on fails ``job_fetch_failed``.  Messages are redacted like
   ``ATSBoardClientError``: host, provider, status -- never a body or query.

Reused, never re-derived: ``normalize_url``/``parse_board_url``
(``contracts.py``), ``job_id_from_url`` (``market_acquisition.py``),
``html_to_text``/``ATSBoardClients`` (``ats_board_clients.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
import html as _html_entities
import json
import re
from typing import TYPE_CHECKING

from ...canonical import digest_imported_bytes
from .assess_contracts import AssessJobInput, ResolvedJob, text_identity
from .ats_board_clients import ATSBoardClientError, ATSBoardClients, html_to_text
from .contracts import (
    FindJobsConfig,
    FindJobsContractError,
    PostingRow,
    SourceToggles,
    normalize_url,
    parse_board_url,
)
from .market_acquisition import job_id_from_url

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx


#: Hard cap on a fetched page body; anything past it is dropped, not read.
MAX_BODY_BYTES = 2 * 1024 * 1024

#: A fetched page whose visible text is shorter than this carries no posting
#: (an ATS JavaScript shell renders ~200 chars of script and nothing else; a
#: real posting is never this short).  Pasted text is never held to this.
MIN_POSTING_TEXT_CHARS = 400

_MAX_REDIRECTS = 5
_GREENHOUSE_JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?content=true"
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# ``job_id_from_url`` is a dedupe heuristic that only trusts 4+ digit path
# segments; a Greenhouse board URL's own ``/<token>/jobs/<id>`` shape is
# unambiguous, so any numeric id there is accepted for the single-job call.
_GREENHOUSE_PATH_JOB_ID = re.compile(r"/jobs/(\d+)/?\Z")

# ``matches_roles`` fails closed on an empty role list, and a board lister
# drops every row whose title matches no role.  When we match a board row by
# URL we do not know the title yet, so the permissive config lists roles that
# together match any title containing at least one letter or digit.
_WILDCARD_ROLES: tuple[str, ...] = tuple("abcdefghijklmnopqrstuvwxyz0123456789")
_PERMISSIVE_CONFIG = FindJobsConfig(
    roles=_WILDCARD_ROLES,
    merged_queries=_WILDCARD_ROLES,
    location=None,
    remote=True,
    published_after=None,
    sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
)


class _FetchFailure(Exception):
    """A redacted single-request failure (host + status/network, no body)."""

    def __init__(self, code: str, host: str, detail: str) -> None:
        super().__init__(f"{host}: {detail}")
        self.code = code
        self.host = host


@dataclass(frozen=True)
class _Page:
    title: str
    text: str


def job_fetch_client() -> "httpx.Client":
    """An ``httpx.Client`` suitable for ``resolve_job``.

    Same timeout/no-proxy posture as ``bindings._http_client`` and the same
    ``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP`` fixture transport, but following up
    to five redirects (career pages redirect freely; board listers never
    need to).  The caller owns the client's lifetime.
    """

    import httpx

    from .bindings import _TIMEOUT, _test_http_enabled, _test_provider_handler

    transport = httpx.MockTransport(_test_provider_handler) if _test_http_enabled() else None
    return httpx.Client(
        timeout=_TIMEOUT,
        transport=transport,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        trust_env=False,
    )


def resolve_job(job: AssessJobInput, *, client: "httpx.Client") -> ResolvedJob:
    """Resolve ``job`` to its plain text and identity; see the module docstring.

    Raises ``FindJobsContractError`` with code ``job_text_unavailable`` (the
    URL was reached but carries no posting text), ``job_fetch_failed`` (every
    fetch attempt failed), or ``normalize_url``'s own ``invalid_value`` for a
    malformed URL.
    """

    if job.job_text is not None:
        return _pasted(job.job_text)
    assert job.job_url is not None  # AssessJobInput guarantees exactly one
    url = job.job_url
    normalized = normalize_url(url)
    board = parse_board_url(url)
    job_id = job_id_from_url(url)
    if job_id is None and board is not None and board[0] == "greenhouse":
        job_id = _greenhouse_path_job_id(url)
    failures: list[str] = []

    if board is not None and board[0] == "greenhouse" and job_id is not None:
        try:
            return _greenhouse_single_job(client, board[1], job_id, source_url=url, normalized_url=normalized)
        except _FetchFailure as exc:
            failures.append(f"greenhouse single-job endpoint ({exc})")

    page: _Page | None = None
    try:
        page = _fetch_page(client, url)
    except _FetchFailure as exc:
        failures.append(f"page fetch ({exc})")

    if board is not None and (page is None or len(page.text) < MIN_POSTING_TEXT_CHARS):
        provider, token = board
        try:
            row = _match_board_row(client, provider, token, job_id=job_id, normalized_url=normalized)
        except ATSBoardClientError as exc:
            failures.append(f"{provider} board listing ({exc.code})")
        else:
            if row is not None:
                return _from_board_row(row, source_url=url, normalized_url=normalized)
            failures.append(f"{provider} board {token!r} has no row for this URL")

    if page is not None and len(page.text) >= MIN_POSTING_TEXT_CHARS:
        return ResolvedJob(
            job_identity=normalized,
            source_url=url,
            normalized_url=normalized,
            fetch_kind="generic",
            title=page.title,
            company="",
            location="",
            text=page.text,
            text_sha256=_text_digest(page.text),
        )
    if page is not None:
        raise FindJobsContractError(
            "job_text_unavailable",
            "the page at this URL carries no posting text (a script-only page, or a posting that is no longer listed); pass --job-text with the posting text instead",
        )
    raise FindJobsContractError(
        "job_fetch_failed",
        "fetching the job failed: " + "; ".join(failures) + "; pass --job-text with the posting text instead",
    )


def _pasted(text: str) -> ResolvedJob:
    stripped = text.strip()
    if not stripped:
        raise FindJobsContractError("job_text_unavailable", "job_text is empty; pass --job-text with the posting text")
    digest = _text_digest(stripped)
    return ResolvedJob(
        job_identity=text_identity(digest),
        source_url=None,
        normalized_url=None,
        fetch_kind="pasted",
        title="",
        company="",
        location="",
        text=stripped,
        text_sha256=digest,
    )


def _greenhouse_path_job_id(url: str) -> str | None:
    import httpx

    match = _GREENHOUSE_PATH_JOB_ID.search(httpx.URL(url).path)
    return None if match is None else match.group(1)


def _text_digest(text: str) -> str:
    return digest_imported_bytes(text.encode("utf-8"))


def _host_of(url: str) -> str:
    import httpx

    try:
        return httpx.URL(url).host or "<unknown host>"
    except Exception:  # pragma: no cover - normalize_url already validated the URL
        return "<unknown host>"


def _read_capped(client: "httpx.Client", url: str) -> tuple[bytes, str | None]:
    """GET ``url`` reading at most ``MAX_BODY_BYTES``; returns (body, charset)."""

    import httpx

    host = _host_of(url)
    try:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise _FetchFailure("http_error", host, f"HTTP {response.status_code}")
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes():
                room = MAX_BODY_BYTES - received
                if room <= 0:
                    break
                piece = chunk[:room]
                chunks.append(piece)
                received += len(piece)
            return b"".join(chunks), response.charset_encoding
    except httpx.TooManyRedirects:
        raise _FetchFailure("too_many_redirects", host, "too many redirects") from None
    except httpx.HTTPError as exc:
        raise _FetchFailure("network_error", host, f"network error ({type(exc).__name__})") from None


def _decode(body: bytes, charset: str | None) -> str:
    for encoding in (charset, "utf-8"):
        if not encoding:
            continue
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def _page_title(html: str) -> str:
    match = _TITLE_TAG.search(html)
    if match is None:
        return ""
    return re.sub(r"\s+", " ", _html_entities.unescape(match.group(1))).strip()


def _fetch_page(client: "httpx.Client", url: str) -> _Page:
    body, charset = _read_capped(client, url)
    html = _decode(body, charset)
    return _Page(title=_page_title(html), text=html_to_text(html))


def _greenhouse_single_job(
    client: "httpx.Client", token: str, job_id: str, *, source_url: str, normalized_url: str
) -> ResolvedJob:
    endpoint = _GREENHOUSE_JOB_URL.format(token=token, job_id=job_id)
    body, charset = _read_capped(client, endpoint)
    host = _host_of(endpoint)
    try:
        payload = json.loads(_decode(body, charset))
    except ValueError:
        raise _FetchFailure("bad_json", host, "response was not JSON") from None
    if type(payload) is not dict or type(payload.get("title")) is not str or type(payload.get("content")) is not str:
        raise _FetchFailure("bad_json", host, "response was not a job object")
    text = html_to_text(payload["content"])
    if not text.strip():
        raise _FetchFailure("empty_content", host, "job object carried no posting text")
    location = payload.get("location")
    location_name = location["name"] if type(location) is dict and type(location.get("name")) is str else ""
    company = payload.get("company_name")
    return ResolvedJob(
        job_identity=normalized_url,
        source_url=source_url,
        normalized_url=normalized_url,
        fetch_kind="ats_single",
        title=payload["title"],
        company=company if type(company) is str and company else token,
        location=location_name,
        text=text,
        text_sha256=_text_digest(text),
    )


def _match_board_row(
    client: "httpx.Client", provider: str, token: str, *, job_id: str | None, normalized_url: str
) -> PostingRow | None:
    rows = ATSBoardClients().list_board(client, provider, token, _PERMISSIVE_CONFIG)
    for row in rows:
        if row.normalized_url == normalized_url:
            return row
    if job_id is not None:
        for row in rows:
            if row.board_token == token and _row_job_id(row) == job_id:
                return row
    return None


def _row_job_id(row: PostingRow) -> str | None:
    found = job_id_from_url(row.url)
    if found is None and row.provider.value == "greenhouse":
        found = _greenhouse_path_job_id(row.url)
    return found


def _from_board_row(row: PostingRow, *, source_url: str, normalized_url: str) -> ResolvedJob:
    text = row.text or ""
    if not text.strip():
        raise FindJobsContractError(
            "job_text_unavailable",
            f"the {row.provider.value} board row for this URL carries no posting text; pass --job-text with the posting text instead",
        )
    return ResolvedJob(
        job_identity=normalized_url,
        source_url=source_url,
        normalized_url=normalized_url,
        fetch_kind="ats_board",
        title=row.title,
        company=row.company,
        location=row.location,
        text=text,
        text_sha256=_text_digest(text),
    )


__all__ = ["MAX_BODY_BYTES", "MIN_POSTING_TEXT_CHARS", "job_fetch_client", "resolve_job"]
