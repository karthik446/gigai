"""Exa discovery client for the Scout ``find-jobs`` acquire node (A-1).

Implements ``scout_find_jobs_contracts.ExaSearchClient``.  This module only
issues one HTTP request per merged query against Exa's public search API and
maps the response into frozen :class:`PostingRow` DTOs; it never fetches a
board, writes a workpad, or touches private data.

Exa API shape assumed (not verified live; no network access during
development). Documented here since it cannot be pinned by a fixture:

- ``POST https://api.exa.ai/search``
- Header ``x-api-key: <EXA_API_KEY>``
- JSON body: ``{"query": str, "numResults": int, "includeDomains": [str, ...],
  "startPublishedDate": str | omitted}``
- JSON response: ``{"results": [{"url": str, "title": str,
  "publishedDate": str | null, ...}, ...]}``
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .contracts import (
    ATSProvider,
    FindJobsConfig,
    FindJobsContractError,
    PostingRow,
    SourceKind,
    normalize_url,
    parse_board_url,
)

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_API_KEY_ENV_VAR = "EXA_API_KEY"
NUM_RESULTS = 25

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


class ExaClientError(FindJobsContractError):
    """Raised for Exa client failures whose message never carries key material.

    The frozen ``ExaSearchClient.search`` signature returns only
    ``tuple[PostingRow, ...]`` (no failures channel), so this client raises on
    any HTTP/JSON error instead of collecting partial failures. The caller
    (acquire node, A-6) catches this and converts it into a redacted
    ``FailureRow``.
    """


def _require_api_key() -> str:
    api_key = os.environ.get(EXA_API_KEY_ENV_VAR)
    if not api_key:
        raise ExaClientError(
            "exa_missing_key",
            f"{EXA_API_KEY_ENV_VAR} is not set in the environment; Exa discovery cannot run",
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
    )


class ExaSearchClient:
    """Concrete ``ExaSearchClient`` protocol implementation backed by Exa."""

    def search(self, client: "httpx.Client", config: FindJobsConfig) -> tuple[PostingRow, ...]:
        api_key = _require_api_key()
        rows: list[PostingRow] = []
        for query in config.merged_queries:
            body: dict[str, object] = {
                "query": query,
                "numResults": NUM_RESULTS,
                "includeDomains": list(ATS_INCLUDE_DOMAINS),
            }
            if config.published_after is not None:
                body["startPublishedDate"] = config.published_after
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
                    f"Exa request returned HTTP {response.status_code}",
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
