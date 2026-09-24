"""Company research: budget guard, missing key -> partial, source verification,
never sends the resume (no live calls, MockTransport only)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai import secrets_store
from gigai.scout.find_jobs.discovery.openai_source import OPENAI_API_KEY_ENV_VAR
from gigai.scout.interview_prep.company_research import build_query, research_company
from gigai.scout.interview_prep import websearch


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _responses_payload(*, claims: list[dict], input_tokens: int = 1000, output_tokens: int = 200) -> dict:
    return {
        "output": [
            {"type": "reasoning"},
            {"type": "web_search_call"},
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"claims": claims})}]},
        ],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def test_build_query_never_includes_resume_text() -> None:
    """Privacy (packet requirement): the query is company + title only."""

    query = build_query(company="Acme Corp", title="Staff Backend Engineer")
    assert "Acme Corp" in query
    assert "Staff Backend Engineer" in query
    # build_query has no resume parameter at all -- this asserts the
    # function's own signature can't smuggle resume text in.
    import inspect
    assert "resume" not in inspect.signature(build_query).parameters


def test_missing_api_key_skips_without_raising_and_no_http_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(OPENAI_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request should be made without an API key")

    client = _client(handler)
    result = research_company(company="Acme Corp", title="Staff Backend Engineer", budget_usd=0.50, search_client=client, verify_client=client)

    assert result.claims == ()
    assert result.cost_usd == 0.0
    assert result.skipped is not None
    assert "gigai secrets add openai" in result.skipped


def test_budget_guard_skips_before_any_spend_when_worst_case_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request should be made when the budget guard refuses first")

    # worst_case_cost_usd() for the default model is well above a $0.0001 budget.
    client = _client(handler)
    result = research_company(company="Acme Corp", title="Staff Backend Engineer", budget_usd=0.0001, search_client=client, verify_client=client)

    assert result.cost_usd == 0.0
    assert result.skipped is not None
    assert "budget" in result.skipped.lower()


def test_claims_carry_source_url_and_are_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_responses_payload(claims=[
                {"claim": "Acme uses Go and Kubernetes across its backend.", "source_url": "https://acme.example/engineering"},
                {"claim": "Acme raised a Series C in 2025.", "source_url": "https://acme.example/news/series-c"},
            ]))
        # HEAD/GET verification calls both resolve.
        return httpx.Response(200)

    client = _client(handler)
    result = research_company(company="Acme Corp", title="Staff Backend Engineer", budget_usd=0.50, search_client=client, verify_client=client)

    assert result.skipped is None
    assert len(result.claims) == 2
    for claim in result.claims:
        assert claim.source_url.startswith("https://")
        assert claim.verified is True
    assert result.cost_usd > 0


def test_unverifiable_source_urls_are_dropped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_responses_payload(claims=[
                {"claim": "Acme has a broken source link.", "source_url": "https://acme.example/dead-link"},
            ]))
        return httpx.Response(404)  # HEAD and GET both 404.

    client = _client(handler)
    result = research_company(company="Acme Corp", title="Staff Backend Engineer", budget_usd=0.50, search_client=client, verify_client=client)

    assert result.claims == ()
    assert result.skipped == "no_claims_had_a_verifiable_source"


def test_verify_source_url_head_then_get_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(403)  # some sites reject HEAD
        return httpx.Response(200)

    client = _client(handler)
    assert websearch.verify_source_url(client, "https://example.test/page") is True
    assert calls == ["HEAD", "GET"]
