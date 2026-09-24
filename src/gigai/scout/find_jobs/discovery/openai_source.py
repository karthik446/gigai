"""OpenAI Responses API ``web_search`` discovery source.

Productionises ``research/discovery_bakeoff/run_openai_search.py`` (S24
Investigate §2, EXECUTED there against 3 live runs) -- not imported from
``research/``, rewritten against the product's own conventions
(``gigai.secrets_store`` env-first lookup like ``exa_client.py``,
``httpx`` only, no OpenAI SDK).

- Endpoint: ``POST https://api.openai.com/v1/responses``, synchronous.
- Model: ``gpt-6-luna`` (S24: cheapest model supporting ``web_search`` +
  strict structured output), overridable via ``OPENAI_DISCOVERY_MODEL`` env
  or ``model_override``.
- Pricing (S24 Investigate §2, read from
  https://developers.openai.com/api/docs/pricing 2026-09-24):
  ``gpt-6-luna`` $0.10/1M input, $0.50/1M output tokens; ``web_search``
  tool call $10.00/1000 calls ($0.01/call). Kept in one place
  (``_PRICE_TABLE``) per the task's "documented" requirement.
- Query: built from ``DiscoveryPrefs`` using S23 §3's template (the
  "board root, not a job posting" wording that moved usable-board yield
  20%->100%, S23 Investigate §3), extended with S23/S24's other interview
  fields (titles to avoid, work mode, sponsorship requirement, stack,
  industries) when set.
- Exclusions: interpolated as a plain-text company list (OpenAI's
  ``web_search`` tool has no structural exclusion field the way Exa's
  Agent API does -- S24 Recommendation point 6 flags this as a real
  limitation, not fixed here, just documented). The caller
  (``__init__.py``'s ``run_discovery``) assembles the exclusion set from
  the watchlist + ``prefs.exclude_companies`` + companies already found in
  this session's earlier runs and passes it in as ``exclusions``.
- Retry: 429 (TPM) backoff widened per S24's live finding (Investigate
  §2) -- ``retry-after + 45s``, up to 6 attempts, bounded.
- A missing key never raises: the source is recorded as skipped with the
  fix, matching this task's contract ("never raises for provider errors").
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from gigai import secrets_store

from .prefs import DiscoveryPrefs
from .types import Candidate, SourceRunOutcome

RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_API_KEY_ENV_VAR = "OPENAI_API_KEY"
MODEL_ENV_VAR = "OPENAI_DISCOVERY_MODEL"
DEFAULT_MODEL = "gpt-6-luna"

# Pricing, one place (S24 Investigate §2, read 2026-09-24 from
# https://developers.openai.com/api/docs/pricing). Update here if OpenAI's
# pricing page changes; nowhere else in this module hardcodes a rate.
_PRICE_TABLE: dict[str, dict[str, float]] = {
    "gpt-6-luna": {"input_per_token": 0.10 / 1_000_000, "output_per_token": 0.50 / 1_000_000},
}
_WEB_SEARCH_CALL_COST_USD = 0.01  # $10.00 / 1000 calls

_MAX_RETRY_ATTEMPTS = 6
_RETRY_BACKOFF_BUFFER_SECONDS = 45.0
_DEFAULT_RETRY_AFTER_SECONDS = 10.0
# Callers should build their httpx.Client with at least this timeout (S24
# Investigate §2: openai_run3 needed ~12min wall-clock under retry backoff
# for one call) -- not applied here since the client is injected for
# testability (MockTransport in tests never needs a real timeout).
REQUEST_TIMEOUT_SECONDS = 300.0

# S23/S24's strict schema. OpenAI's ``strict: true`` structured-output mode
# requires every property to be listed in ``required`` (S24 Investigate
# §2's own "every field in required" note) -- a field cannot be made
# optional under strict mode, only its *value* can be non-committal.
# Operator rule (2026-09-24): when ``prefs.visa_sponsorship_required`` is
# false, ``build_query`` simply doesn't ask the model to find sponsorship
# evidence, so it's expected/allowed to answer ``sponsorship: "unknown"`` /
# an empty ``sponsorship_evidence`` rather than being forced to fabricate
# something -- ``merge.py`` only verifies a ``source`` URL when present.
_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "careers_url": {"type": "string"},
                    "ats_provider": {"type": "string", "enum": ["greenhouse", "lever", "ashby", "other", "unknown"]},
                    "sponsorship": {"type": "string", "enum": ["yes", "no", "unknown"]},
                    "sponsorship_evidence": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["company", "careers_url", "ats_provider", "sponsorship", "sponsorship_evidence", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}


class OpenAISourceError(RuntimeError):
    """Raised only for a caller-side programming error; provider failures never raise."""


def _api_key() -> str | None:
    return os.environ.get(OPENAI_API_KEY_ENV_VAR) or secrets_store.get(OPENAI_API_KEY_ENV_VAR)


def resolve_model(model_override: str | None = None) -> str:
    return model_override or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL


def price_per_token(model: str) -> dict[str, float]:
    """Exposed for cost-estimation callers (budget guard); raises for an unpriced model."""

    prices = _PRICE_TABLE.get(model)
    if prices is None:
        raise OpenAISourceError(f"no price table entry for model {model!r}; add one to _PRICE_TABLE")
    return prices


def build_query(prefs: DiscoveryPrefs, exclusions: tuple[str, ...]) -> str:
    """S23 §3's template, filled from ``prefs`` (roles/countries/work-mode/etc)."""

    roles = " or ".join(prefs.roles) if prefs.roles else "the configured"
    countries = ", ".join(prefs.countries) if prefs.countries else "US"
    if prefs.work_mode == "remote":
        mode_clause = "remote"
    elif prefs.work_mode == "onsite" and prefs.city:
        mode_clause = f"onsite in {prefs.city}"
    elif prefs.work_mode == "hybrid" and prefs.city:
        mode_clause = f"hybrid in {prefs.city}"
    elif prefs.city:
        mode_clause = f"remote or {prefs.city}"
    else:
        mode_clause = "remote or any location"

    sponsorship_clause = (
        f" AND are documented to sponsor {countries} work visas (H-1B)"
        if prefs.visa_sponsorship_required
        else ""
    )
    avoid_clause = (
        f" Avoid titles matching: {', '.join(prefs.titles_to_avoid)}."
        if prefs.titles_to_avoid
        else ""
    )
    stage_clause = f" Prefer company stage/size: {prefs.company_stage_size}." if prefs.company_stage_size else ""
    industries_clause = ""
    if prefs.industries_include:
        industries_clause += f" Prefer industries: {', '.join(prefs.industries_include)}."
    if prefs.industries_exclude:
        industries_clause += f" Avoid industries: {', '.join(prefs.industries_exclude)}."
    stack_clause = ""
    if prefs.must_have_stack:
        stack_clause += f" Must use: {', '.join(prefs.must_have_stack)}."
    if prefs.dealbreaker_stack:
        stack_clause += f" Must NOT require: {', '.join(prefs.dealbreaker_stack)}."
    exclusion_text = ", ".join(exclusions) if exclusions else "(none)"

    # Operator rule (2026-09-24): only ask the model to find sponsorship
    # evidence when the operator actually requires it -- otherwise it's
    # just a source URL for the board-usability check, and asking for
    # sponsorship evidence the operator doesn't need invites the model to
    # fabricate something to satisfy the request.
    if prefs.visa_sponsorship_required:
        evidence_request = (
            "For each company give: the company name, the exact board URL, the ATS provider, and "
            "visa sponsorship evidence with a source URL (an H-1B filing record, careers page, or "
            "job posting)."
        )
    else:
        evidence_request = (
            "For each company give: the company name, the exact board URL, the ATS provider, and "
            "a source URL for the board (the board page itself, or a careers page listing it)."
        )

    return (
        f"Find {countries} companies hiring for {roles} roles ({mode_clause}) that use "
        "Greenhouse, Lever, or Ashby as their applicant tracking system"
        f"{sponsorship_clause}. Only return companies where you can identify the company's "
        "OWN job board base URL on one of these three platforms (e.g. "
        "https://boards.greenhouse.io/<company>, https://jobs.lever.co/<company>, or "
        "https://jobs.ashbyhq.com/<company> -- the board root, not a link to a specific job "
        "posting or a third-party aggregator like LinkedIn, Indeed, or another job site). Skip "
        "a company if you cannot find its own board URL on one of those three ATS platforms. "
        f"{evidence_request}{avoid_clause}{stage_clause}{industries_clause}{stack_clause} "
        f"Exclude: {exclusion_text}."
    )


def _actual_cost(model: str, usage: dict[str, object], web_search_calls: int) -> float:
    prices = price_per_token(model)
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    input_tokens = input_tokens if isinstance(input_tokens, (int, float)) else 0
    output_tokens = output_tokens if isinstance(output_tokens, (int, float)) else 0
    token_cost = input_tokens * prices["input_per_token"] + output_tokens * prices["output_per_token"]
    return token_cost + web_search_calls * _WEB_SEARCH_CALL_COST_USD


def worst_case_cost_usd(model: str, *, max_input_tokens: int = 80_000, max_output_tokens: int = 4_000, max_web_search_calls: int = 3) -> float:
    """Conservative pre-call reservation (S24's observed 45k-75k input tokens/call)."""

    prices = price_per_token(model)
    return (
        max_input_tokens * prices["input_per_token"]
        + max_output_tokens * prices["output_per_token"]
        + max_web_search_calls * _WEB_SEARCH_CALL_COST_USD
    )


def _parse_candidates(structured_text: str | None) -> tuple[Candidate, ...]:
    if not structured_text:
        return ()
    try:
        parsed = json.loads(structured_text)
    except ValueError:
        return ()
    if type(parsed) is not dict:
        return ()
    companies = parsed.get("companies")
    if type(companies) is not list:
        return ()
    candidates: list[Candidate] = []
    for entry in companies:
        if type(entry) is not dict:
            continue
        try:
            candidates.append(
                Candidate(
                    company=str(entry["company"]),
                    careers_url=str(entry["careers_url"]),
                    ats_provider=str(entry.get("ats_provider", "unknown")),
                    sponsorship=str(entry.get("sponsorship", "unknown")),
                    sponsorship_evidence=str(entry.get("sponsorship_evidence", "")),
                    source_url=str(entry.get("source", "")),
                    found_by="openai_web_search",
                )
            )
        except KeyError:
            continue
    return tuple(candidates)


@dataclass(frozen=True)
class _OneCallResult:
    candidates: tuple[Candidate, ...]
    cost_usd: float
    latency_seconds: float


def _call_once(
    client: httpx.Client,
    api_key: str,
    model: str,
    query: str,
    *,
    on_progress: Callable[[dict], None] | None,
) -> _OneCallResult:
    body = {
        "model": model,
        "input": query,
        "tools": [{"type": "web_search"}],
        "text": {"format": {"type": "json_schema", "name": "companies_result", "schema": _SCHEMA, "strict": True}},
    }
    started = time.monotonic()
    response: httpx.Response | None = None
    for attempt in range(_MAX_RETRY_ATTEMPTS):
        response = client.post(
            RESPONSES_URL,
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        if response.status_code != 429:
            break
        retry_after = float(response.headers.get("retry-after", _DEFAULT_RETRY_AFTER_SECONDS))
        wait_seconds = retry_after + _RETRY_BACKOFF_BUFFER_SECONDS
        if on_progress is not None:
            on_progress({"stage": "openai_retry", "attempt": attempt + 1, "wait_seconds": wait_seconds})
        time.sleep(wait_seconds)
    assert response is not None
    response.raise_for_status()
    result = response.json()
    latency_seconds = time.monotonic() - started

    web_search_calls = sum(1 for o in result.get("output", []) if o.get("type") == "web_search_call")
    usage = result.get("usage", {}) if type(result.get("usage")) is dict else {}
    actual_cost = _actual_cost(model, usage, web_search_calls)

    structured_text = None
    for output_item in result.get("output", []):
        if output_item.get("type") == "message":
            for content_item in output_item.get("content", []):
                if content_item.get("type") == "output_text":
                    structured_text = content_item.get("text")
    candidates = _parse_candidates(structured_text)
    return _OneCallResult(candidates=candidates, cost_usd=actual_cost, latency_seconds=latency_seconds)


def run(
    *,
    client: httpx.Client,
    prefs: DiscoveryPrefs,
    exclusions: tuple[str, ...],
    runs: int,
    model_override: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> SourceRunOutcome:
    """Run ``runs`` OpenAI ``web_search`` calls; never raises for provider errors.

    Each call's exclusion list grows with companies the *previous* call in
    this same session already found (S24's "week 2 simulation" pattern),
    so N runs in one session don't just repeat the same result.
    """

    api_key = _api_key()
    if not api_key:
        return SourceRunOutcome(
            name="openai_web_search",
            runs=0,
            cost_usd=0.0,
            candidates=(),
            skip_reason=f"{OPENAI_API_KEY_ENV_VAR} is not set; run `gigai secrets add openai`",
        )

    model = resolve_model(model_override)
    seen_exclusions = list(exclusions)
    all_candidates: list[Candidate] = []
    total_cost = 0.0
    completed_runs = 0
    last_error: str | None = None

    for run_index in range(runs):
        query = build_query(prefs, tuple(seen_exclusions))
        if on_progress is not None:
            on_progress({"stage": "openai_call", "run_index": run_index, "of": runs})
        try:
            one = _call_once(client, api_key, model, query, on_progress=on_progress)
        except httpx.HTTPStatusError as exc:
            last_error = f"openai_http_{exc.response.status_code}"
            continue
        except httpx.HTTPError as exc:
            last_error = f"openai_transport_{type(exc).__name__}"
            continue
        completed_runs += 1
        total_cost += one.cost_usd
        all_candidates.extend(one.candidates)
        seen_exclusions.extend(c.company for c in one.candidates)

    if completed_runs == 0:
        return SourceRunOutcome(
            name="openai_web_search",
            runs=0,
            cost_usd=total_cost,
            candidates=(),
            error=last_error or "openai_no_successful_runs",
        )

    return SourceRunOutcome(
        name="openai_web_search",
        runs=completed_runs,
        cost_usd=total_cost,
        candidates=tuple(all_candidates),
        error=last_error if completed_runs < runs else None,
    )


__all__ = [
    "DEFAULT_MODEL",
    "MODEL_ENV_VAR",
    "OPENAI_API_KEY_ENV_VAR",
    "REQUEST_TIMEOUT_SECONDS",
    "OpenAISourceError",
    "build_query",
    "price_per_token",
    "resolve_model",
    "run",
    "worst_case_cost_usd",
]
