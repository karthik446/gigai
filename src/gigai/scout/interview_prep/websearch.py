"""Thin OpenAI ``web_search`` structured-output call for interview prep.

ADDENDUM (dispatched early, before S2-A commits): reuses discovery's
``openai_source`` helpers READ-ONLY -- its key lookup (``_api_key``,
reused indirectly by re-checking the same env var/secrets_store path),
``resolve_model``, the price table / ``_actual_cost`` / ``worst_case_cost_usd``,
and its budget-guard shape. ``openai_source.build_query``/``_parse_candidates``
are discovery-specific (``DiscoveryPrefs``-shaped) and are NOT reused here;
this module writes its own query text and its own structured-schema parse,
per the addendum's instruction ("write a thin ``web_search_structured``...
using those helpers").

Company-research bytes never carry the resume: only the query text this
module builds is sent to OpenAI, and that query is built from company name
+ role title alone (see ``prep.py``'s caller) -- never resume content. This
mirrors assess's own privacy line (resume goes only to the configured
assess model call, never to a web-search request).

Follow-up debt (recorded per the addendum): a shared ``scout/websearch.py``
should replace this and discovery's ``openai_source.py`` once both land --
not done here to stay inside this packet's owned files.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from ..find_jobs.discovery import openai_source
from ..find_jobs.discovery.openai_source import OPENAI_API_KEY_ENV_VAR, RESPONSES_URL, resolve_model
from ..find_jobs.discovery.merge import _verify_source_url

REQUEST_TIMEOUT_SECONDS = 300.0
_MAX_RETRY_ATTEMPTS = 6
_RETRY_BACKOFF_BUFFER_SECONDS = 45.0
_DEFAULT_RETRY_AFTER_SECONDS = 10.0


class WebSearchBudgetExceeded(RuntimeError):
    """Raised before any spend when the worst case exceeds the supplied budget."""


class WebSearchError(RuntimeError):
    """A caller-side programming error; provider failures are returned, not raised."""


@dataclass(frozen=True)
class WebSearchResult:
    """One structured ``web_search`` call's outcome. Never raises for a provider error."""

    ok: bool
    parsed: dict[str, object] | None
    cost_usd: float
    skip_reason: str | None = None
    error: str | None = None


def api_key_present(*, home_root: Path | None = None) -> bool:
    """Cheap pre-check so a caller can skip the section without a client/budget dance."""

    return bool(openai_source._api_key(home_root=home_root))


def worst_case_cost_usd(model_override: str | None = None) -> float:
    """Reuses discovery's conservative pre-call reservation for one call."""

    model = resolve_model(model_override)
    return openai_source.worst_case_cost_usd(model)


def verify_source_url(client: httpx.Client, url: str) -> bool:
    """Reuses discovery merge's HEAD-then-GET source verification."""

    return _verify_source_url(client, url)


def web_search_structured(
    query: str,
    json_schema: dict[str, object],
    *,
    budget_usd: float,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    model_override: str | None = None,
    client: httpx.Client | None = None,
    on_progress: Callable[[dict], None] | None = None,
    home_root: Path | None = None,
) -> WebSearchResult:
    """One OpenAI ``web_search`` call with a caller-supplied strict JSON schema.

    Never raises for a provider error (missing key, rate limit, transport
    failure, budget) -- every failure comes back in the result's
    ``skip_reason``/``error`` so a caller can build a partial prep. Raises
    :class:`WebSearchError` only for a caller programming error (a schema
    the price table can't be resolved for).

    ``home_root`` (P1-8) selects which operator home's secrets store the key
    lookup falls back to when the environment variable isn't set; omitting
    it keeps today's behavior (env, then the default home).
    """

    if not api_key_present(home_root=home_root):
        return WebSearchResult(
            ok=False, parsed=None, cost_usd=0.0,
            skip_reason=f"{OPENAI_API_KEY_ENV_VAR} is not set; run `gigai secrets add openai`",
        )
    model = resolve_model(model_override)
    try:
        worst_case = openai_source.worst_case_cost_usd(model)
    except openai_source.OpenAISourceError as exc:
        raise WebSearchError(str(exc)) from exc
    if worst_case > budget_usd:
        return WebSearchResult(
            ok=False, parsed=None, cost_usd=0.0,
            skip_reason=f"worst-case cost ${worst_case:.4f} exceeds budget ${budget_usd:.4f}",
        )

    api_key = openai_source._api_key(home_root=home_root)
    assert api_key is not None
    body = {
        "model": model,
        "input": query,
        "tools": [{"type": "web_search"}],
        "text": {"format": {"type": "json_schema", "name": "prep_result", "schema": json_schema, "strict": True}},
    }
    owns_client = client is None
    active_client = client or httpx.Client(timeout=timeout)
    try:
        response: httpx.Response | None = None
        for attempt in range(_MAX_RETRY_ATTEMPTS):
            try:
                response = active_client.post(
                    RESPONSES_URL, json=body,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
            except httpx.HTTPError as exc:
                return WebSearchResult(ok=False, parsed=None, cost_usd=0.0, error=f"transport_{type(exc).__name__}")
            if response.status_code != 429:
                break
            retry_after = float(response.headers.get("retry-after", _DEFAULT_RETRY_AFTER_SECONDS))
            wait_seconds = retry_after + _RETRY_BACKOFF_BUFFER_SECONDS
            if on_progress is not None:
                on_progress({"stage": "openai_retry", "attempt": attempt + 1, "wait_seconds": wait_seconds})
            time.sleep(wait_seconds)
        assert response is not None
        if response.status_code >= 400:
            return WebSearchResult(ok=False, parsed=None, cost_usd=0.0, error=f"http_{response.status_code}")
        result = response.json()
    finally:
        if owns_client:
            active_client.close()

    web_search_calls = sum(1 for item in result.get("output", []) if item.get("type") == "web_search_call")
    usage = result.get("usage", {}) if type(result.get("usage")) is dict else {}
    cost_usd = openai_source._actual_cost(model, usage, web_search_calls)

    structured_text = None
    for output_item in result.get("output", []):
        if output_item.get("type") == "message":
            for content_item in output_item.get("content", []):
                if content_item.get("type") == "output_text":
                    structured_text = content_item.get("text")
    if not structured_text:
        return WebSearchResult(ok=False, parsed=None, cost_usd=cost_usd, error="openai_no_structured_output")
    try:
        parsed = json.loads(structured_text)
    except ValueError:
        return WebSearchResult(ok=False, parsed=None, cost_usd=cost_usd, error="openai_unparsable_output")
    if not isinstance(parsed, dict):
        return WebSearchResult(ok=False, parsed=None, cost_usd=cost_usd, error="openai_output_not_object")
    return WebSearchResult(ok=True, parsed=parsed, cost_usd=cost_usd)


__all__ = [
    "OPENAI_API_KEY_ENV_VAR",
    "REQUEST_TIMEOUT_SECONDS",
    "WebSearchBudgetExceeded",
    "WebSearchError",
    "WebSearchResult",
    "api_key_present",
    "verify_source_url",
    "web_search_structured",
    "worst_case_cost_usd",
]
