"""Exa discovery client for the Scout ``find-jobs`` acquire node (A-1).

Implements ``scout_find_jobs_contracts.ExaSearchClient``.  This module only
issues one HTTP request per merged query against Exa's public search API and
maps the response into frozen :class:`PostingRow` DTOs; it never fetches a
board, writes a workpad, or touches private data.

Exa API shape verified against the documented ``/search`` reference
(https://docs.exa.ai/reference/search, OpenAPI ``SearchRequest`` schema
current as of 2026-09; no network access during development, so the field
list -- not live response bodies -- is what's confirmed):

- ``POST https://api.exa.ai/search``
- Header ``x-api-key: <EXA_API_KEY>``
- JSON body: ``{"query": str, "numResults": int, "includeDomains": [str, ...],
  "startPublishedDate": str | omitted, "userLocation": str | omitted,
  "contents": {"text": {"maxCharacters": int}}}``
- JSON response: ``{"results": [{"url": str, "title": str,
  "publishedDate": str | null, "text": str | omitted, ...}, ...]}``

C1 (v0.1.8.1, B1/B5 addendum): query shaping maps two ``FindJobsConfig``
fields onto documented Exa request params so fewer irrelevant boards come
back in the first place (selection/UI filtering, B1, still applies after):

- ``config.published_after`` -> ``startPublishedDate`` (already present
  before this change; ISO-8601 datetime string per the docs). Q1 (v0.1.9):
  now ALWAYS sent, as the effective window's cutoff from
  ``filters.published_cutoff`` -- the fixed ``published_after`` when set,
  else ``now - max_age_days`` (default 60 days) -- the same helper the
  post-fetch drop uses for every source, so the two never disagree.
- ``config.countries`` -> ``userLocation``, Exa's *only* documented location
  knob (a single two-letter ISO-3166-1 alpha-2 country code -- there is no
  free-text or multi-value location parameter in the schema). Sent only
  when ``countries`` has exactly one code: ``FindJobsConfig`` already
  validates each entry as ``[A-Z]{2}`` (contracts.py's ``_COUNTRY_CODE``),
  so no reformatting is needed. Left off the request when ``countries`` is
  empty (no constraint requested) or has more than one code (``userLocation``
  cannot express an OR of countries; inventing a multi-value encoding the
  docs don't define would violate the "don't invent params" instruction).
  Exa's search index has no per-posting country field to filter by
  server-side, so this is a soft geo signal, not a guarantee -- B1's hard
  country filter still runs downstream in market_acquisition.py.
- ``config.location`` (free-text, e.g. "Denver, CO") has no documented Exa
  request field at all (checked: no ``location`` key in the schema, only
  ``userLocation``'s two-letter country code). ``FindJobsConfig`` is
  operator-authored (``find-jobs.json``) and ``merged_queries`` is
  authored/derived elsewhere, not by this client, so this module cannot
  invent a query-text splice for ``location`` without duplicating logic
  that belongs to whatever builds ``merged_queries``. It is intentionally
  left out of the Exa request; only ``countries`` -> ``userLocation`` is
  wired here, matching a documented param.

C0/P1 (v0.1.8.1, U25, U19): the ``contents.text`` request option is Exa's
documented "get me the page's text along with search results" knob (assumed
shape below, not verified live -- same caveat as the rest of this module).
It's requested with a bounded ``maxCharacters`` so a very large job page
can't blow out the acquisition record; the returned ``text`` (when present)
is mapped straight into ``PostingRow.text`` so an Exa-only row (no ATS board
watchlisted yet) still carries a real posting body for assess and for the
country/visa filters in ``market_acquisition.py``, instead of the empty
string. When the ATS row for the same job exists, ``market_acquisition.py``
prefers it over this Exa row (fuller title/location/text; U20).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from gigai import secrets_store

from .contracts import (
    ATSProvider,
    FindJobsConfig,
    FindJobsContractError,
    PostingRow,
    SourceKind,
    normalize_url,
    parse_board_url,
)
from .filters import published_cutoff, sponsorship_from_text

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_API_KEY_ENV_VAR = "EXA_API_KEY"
NUM_RESULTS = 25
# Bounded page-text request size (U25): enough for a full job description,
# small enough that one Exa response can't balloon the raw-payload store
# (market_acquisition.py's U26 gzip cap) or the assess prompt (P2).
EXA_TEXT_MAX_CHARACTERS = 8000

ATS_INCLUDE_DOMAINS: tuple[str, ...] = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
)

_PROVIDER_BY_NAME = {
    "greenhouse": ATSProvider.GREENHOUSE,
    "lever": ATSProvider.LEVER,
    "ashby": ATSProvider.ASHBY,
}

# Short, redacted reasons for the status codes Exa is documented to return
# for auth/quota/availability failures. Never derived from the response
# body (which may echo request details) -- just the status code.
_EXA_HTTP_REASONS: dict[int, str] = {
    401: "invalid or revoked API key",
    402: "payment required / out of credits",
    403: "forbidden",
    404: "not found",
    429: "rate limited",
    500: "Exa server error",
    502: "Exa server error",
    503: "Exa unavailable",
    504: "Exa timed out",
}


def _exa_http_reason(status_code: int) -> str:
    return _EXA_HTTP_REASONS.get(status_code, "request failed")


class ExaClientError(FindJobsContractError):
    """Raised for Exa client failures whose message never carries key material.

    The frozen ``ExaSearchClient.search`` signature returns only
    ``tuple[PostingRow, ...]`` (no failures channel), so this client raises on
    any HTTP/JSON error instead of collecting partial failures. The caller
    (acquire node, A-6) catches this and converts it into a redacted
    ``FailureRow``.
    """


def _require_api_key(*, home_root: Path | None = None) -> str:
    api_key = os.environ.get(EXA_API_KEY_ENV_VAR) or secrets_store.get(
        EXA_API_KEY_ENV_VAR, home_root=home_root
    )
    if not api_key:
        raise ExaClientError(
            "exa_missing_key",
            "EXA_API_KEY is not set; run `gigai secrets add exa` (or export EXA_API_KEY)",
        )
    return api_key


def _company_from_token(board_token: str) -> str:
    """Same trivial mapping as ``scout_ats_board_clients._company_from_token``.

    Duplicated intentionally: that helper is private to its module, and
    board-token-as-company-name is the only signal Exa's result shape gives us.
    """

    return board_token


def _row_from_result(result: object, query: str) -> PostingRow | None:
    if type(result) is not dict:
        return None
    url = result.get("url")
    if type(url) is not str or not url:
        return None
    parsed = parse_board_url(url)
    if parsed is None:
        return None
    provider_name, board_token = parsed
    provider = _PROVIDER_BY_NAME.get(provider_name)
    if provider is None:
        return None
    try:
        normalized = normalize_url(url)
    except FindJobsContractError:
        return None
    title = result.get("title")
    published_at = result.get("publishedDate")
    text_value = result.get("text")
    text = text_value if type(text_value) is str and text_value else None
    return PostingRow(
        url=url,
        normalized_url=normalized,
        provider=provider,
        board_token=board_token,
        company=_company_from_token(board_token),
        title=title if type(title) is str and title else url,
        location="",
        published_at=published_at if type(published_at) is str and published_at else None,
        content_sha256=None,
        source_kind=SourceKind.EXA,
        query_key=query,
        text=text,
        sponsorship=sponsorship_from_text(text),
    )


def _exa_date(value: "datetime") -> str:
    """Render a tz-aware cutoff as the ``Z``-suffixed ISO-8601 string Exa's
    ``startPublishedDate`` takes (a fixed ``published_after`` of
    ``2026-09-15T00:00:00Z`` round-trips unchanged)."""

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ExaSearchClient:
    """Concrete ``ExaSearchClient`` protocol implementation backed by Exa."""

    def search(
        self,
        client: "httpx.Client",
        config: FindJobsConfig,
        *,
        home_root: Path | None = None,
    ) -> tuple[PostingRow, ...]:
        api_key = _require_api_key(home_root=home_root)
        rows: list[PostingRow] = []
        for query in config.merged_queries:
            body: dict[str, object] = {
                "query": query,
                "numResults": NUM_RESULTS,
                "includeDomains": list(ATS_INCLUDE_DOMAINS),
                "contents": {"text": {"maxCharacters": EXA_TEXT_MAX_CHARACTERS}},
            }
            # Q1 (v0.1.9): always send the effective window's cutoff --
            # the fixed `published_after` when set, else the rolling
            # `max_age_days` (default 60) -- from the SAME helper the
            # post-fetch drop uses (`filters.published_cutoff`), so Exa is
            # never asked for a wider range than acquire would keep.
            body["startPublishedDate"] = _exa_date(published_cutoff(config))
            if len(config.countries) == 1:
                body["userLocation"] = config.countries[0]
            try:
                response = client.post(
                    EXA_SEARCH_URL,
                    json=body,
                    headers={"x-api-key": api_key},
                )
            except Exception as exc:  # noqa: BLE001 - transport failures are redacted before re-raising
                raise ExaClientError(
                    "exa_transport",
                    f"Exa request failed: {type(exc).__name__}",
                ) from None
            if response.status_code >= 400:
                raise ExaClientError(
                    f"exa_http_{response.status_code}",
                    f"Exa returned {response.status_code} ({_exa_http_reason(response.status_code)})",
                )
            try:
                payload = response.json()
            except ValueError:
                raise ExaClientError("exa_bad_json", "Exa response was not valid JSON") from None
            if type(payload) is not dict or type(payload.get("results")) is not list:
                raise ExaClientError("exa_bad_json", "Exa response was missing a results array")
            for result in payload["results"]:
                row = _row_from_result(result, query)
                if row is not None:
                    rows.append(row)
        return tuple(rows)
