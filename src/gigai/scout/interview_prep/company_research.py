"""Company research for interview prep: OpenAI ``web_search``, sources verified.

Every claim carries a source URL; sources are verified (resolve) the same
way discovery does (``find_jobs/discovery/merge.py``'s HEAD-then-GET check,
reused via ``websearch.verify_source_url``). Budget: at most $0.50 per prep
(the packet's own cap; enforced here as the ``budget_usd`` ceiling passed to
``websearch.web_search_structured``, itself reusing discovery's worst-case
pre-call reservation so a call that could exceed the budget never starts).

Privacy (packet requirement, restated in code per the packet's own
instruction): **the resume never goes into this module's query.** The query
is built from company name + role title only -- see ``build_query`` below;
no caller of this module may pass resume text in.

A missing OpenAI key never raises: this section is skipped with the fix
named, and the rest of the prep still builds (packet CHANGE 2a).
"""

from __future__ import annotations

from typing import Callable

import httpx

from . import websearch
from .types import CompanyResearch, SourceClaim

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["claim", "source_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

_MAX_CLAIMS = 12


def build_query(*, company: str, title: str) -> str:
    """Company name + role title only -- never the resume or posting body."""

    return (
        f"Research {company!r} for a candidate interviewing for a {title!r} role. "
        "Find: the engineering blog or tech stack, main product(s), recent news "
        "(last 12 months), and any publicly reported interview-process details "
        "(stages, format, what past candidates said). For each fact, give one "
        "concise claim and the exact source URL it came from. Only include "
        "claims you can attribute to a real, checkable URL."
    )


def research_company(
    *,
    company: str,
    title: str,
    budget_usd: float,
    search_client: httpx.Client | None = None,
    verify_client: httpx.Client | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> CompanyResearch:
    """Research one company; never raises for a missing key or provider error.

    ``search_client`` (optional, for tests) is used for the ``web_search``
    call itself -- when omitted, ``websearch.web_search_structured`` opens
    its own client at its own long timeout (a ``web_search`` call can take
    minutes under rate-limit backoff, per ``openai_source.py``). This is
    deliberately a *different* client than ``verify_client`` (a short,
    30s-timeout client for the HEAD/GET source-URL checks below) -- sharing
    one short-timeout client between both calls previously made every
    ``web_search`` call time out at 30s even when the provider was healthy.
    """

    if not websearch.api_key_present():
        return CompanyResearch(skipped=f"{websearch.OPENAI_API_KEY_ENV_VAR} is not set; run `gigai secrets add openai`")

    query = build_query(company=company, title=title)
    result = websearch.web_search_structured(
        query, _SCHEMA, budget_usd=budget_usd, client=search_client, on_progress=on_progress,
    )
    if not result.ok:
        return CompanyResearch(cost_usd=result.cost_usd, skipped=result.skip_reason or result.error or "company_research_unavailable")

    raw_claims = result.parsed.get("claims") if isinstance(result.parsed, dict) else None
    if not isinstance(raw_claims, list):
        return CompanyResearch(cost_usd=result.cost_usd, skipped="openai_no_claims_returned")

    owns_client = verify_client is None
    active_verify_client = verify_client or httpx.Client(timeout=30.0)
    try:
        claims: list[SourceClaim] = []
        for entry in raw_claims[:_MAX_CLAIMS]:
            if not isinstance(entry, dict):
                continue
            claim_text, source_url = entry.get("claim"), entry.get("source_url")
            if not isinstance(claim_text, str) or not claim_text.strip() or not isinstance(source_url, str) or not source_url.strip():
                continue
            verified = websearch.verify_source_url(active_verify_client, source_url)
            if not verified:
                continue
            claims.append(SourceClaim(claim=claim_text, source_url=source_url, verified=True))
    finally:
        if owns_client:
            active_verify_client.close()

    if not claims:
        return CompanyResearch(cost_usd=result.cost_usd, skipped="no_claims_had_a_verifiable_source")
    return CompanyResearch(claims=tuple(claims), cost_usd=result.cost_usd)


__all__ = ["build_query", "research_company"]
