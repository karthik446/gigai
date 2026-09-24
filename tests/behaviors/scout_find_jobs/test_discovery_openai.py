"""Behavior tests for the OpenAI web_search discovery source (S2-A, no live calls)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai import secrets_store
from gigai.scout.find_jobs.discovery.openai_source import (
    OPENAI_API_KEY_ENV_VAR,
    build_query,
    run,
    worst_case_cost_usd,
)
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs


def _prefs(**overrides: object) -> DiscoveryPrefs:
    values: dict[str, object] = {
        "roles": ("staff backend", "senior backend"),
        "countries": ("US",),
        "work_mode": "remote",
        "city": "Denver, CO",
        "visa_sponsorship_required": True,
        "exclude_companies": ("Coupang", "ClickHouse"),
    }
    values.update(overrides)
    return DiscoveryPrefs(**values)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _responses_payload(*, companies: list[dict], input_tokens: int = 1000, output_tokens: int = 200) -> dict:
    return {
        "output": [
            {"type": "reasoning"},
            {"type": "web_search_call"},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"companies": companies})}],
            },
        ],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def test_build_query_includes_roles_countries_and_exclusions() -> None:
    query = build_query(_prefs(), ("Coupang", "ClickHouse"))
    assert "staff backend or senior backend" in query
    assert "US" in query
    assert "remote" in query
    assert "H-1B" in query
    assert "Coupang, ClickHouse" in query
    assert "boards.greenhouse.io" in query


def test_build_query_omits_visa_requirement_clause_when_not_required() -> None:
    query = build_query(_prefs(visa_sponsorship_required=False), ())
    assert "AND are documented to sponsor" not in query


def test_build_query_does_not_ask_for_sponsorship_evidence_when_not_required() -> None:
    """Operator rule (2026-09-24): don't invite the model to fabricate sponsorship evidence it wasn't asked for."""

    query = build_query(_prefs(visa_sponsorship_required=False), ())
    assert "sponsorship evidence" not in query.lower()
    assert "a source URL for the board" in query


def test_build_query_asks_for_sponsorship_evidence_when_required() -> None:
    query = build_query(_prefs(visa_sponsorship_required=True), ())
    assert "sponsorship evidence" in query.lower()


def test_missing_api_key_skips_without_raising(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(OPENAI_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request should be made without an API key")

    outcome = run(client=_client(handler), prefs=_prefs(), exclusions=(), runs=3)

    assert outcome.runs == 0
    assert outcome.cost_usd == 0.0
    assert outcome.candidates == ()
    assert outcome.skip_reason is not None
    assert "gigai secrets add openai" in outcome.skip_reason


def test_env_api_key_used_and_candidates_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    monkeypatch.setenv(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=_responses_payload(
                companies=[
                    {
                        "company": "Docker",
                        "careers_url": "https://boards.greenhouse.io/docker",
                        "ats_provider": "greenhouse",
                        "sponsorship": "yes",
                        "sponsorship_evidence": "64 LCA filings",
                        "source": "https://example.test/docker-h1b",
                    }
                ]
            ),
        )

    outcome = run(client=_client(handler), prefs=_prefs(), exclusions=("Coupang",), runs=1)

    assert len(captured) == 1
    assert captured[0].headers["authorization"] == "Bearer sk-test-key"
    assert outcome.runs == 1
    assert outcome.error is None
    assert outcome.skip_reason is None
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert candidate.company == "Docker"
    assert candidate.found_by == "openai_web_search"
    assert outcome.cost_usd > 0


def test_secrets_store_used_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(OPENAI_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(OPENAI_API_KEY_ENV_VAR, "dotenv-openai-key")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_responses_payload(companies=[]))

    run(client=_client(handler), prefs=_prefs(), exclusions=(), runs=1)

    assert captured[0].headers["authorization"] == "Bearer dotenv-openai-key"


def test_429_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    monkeypatch.setenv(OPENAI_API_KEY_ENV_VAR, "sk-test-key")
    monkeypatch.setattr("gigai.scout.find_jobs.discovery.openai_source.time.sleep", lambda _seconds: None)

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rate limited"})
        return httpx.Response(200, json=_responses_payload(companies=[]))

    outcome = run(client=_client(handler), prefs=_prefs(), exclusions=(), runs=1)

    assert attempts["n"] == 3
    assert outcome.runs == 1
    assert outcome.error is None


def test_429_exhausts_retries_records_error_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    monkeypatch.setenv(OPENAI_API_KEY_ENV_VAR, "sk-test-key")
    monkeypatch.setattr("gigai.scout.find_jobs.discovery.openai_source.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rate limited"})

    outcome = run(client=_client(handler), prefs=_prefs(), exclusions=(), runs=1)

    assert outcome.runs == 0
    assert outcome.error is not None
    assert outcome.candidates == ()


def test_multiple_runs_grow_exclusion_list_from_prior_finds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    monkeypatch.setenv(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_queries.append(body["input"])
        call_index = len(seen_queries)
        if call_index == 1:
            companies = [
                {
                    "company": "Docker",
                    "careers_url": "https://boards.greenhouse.io/docker",
                    "ats_provider": "greenhouse",
                    "sponsorship": "yes",
                    "sponsorship_evidence": "e",
                    "source": "https://example.test/docker",
                }
            ]
        else:
            companies = []
        return httpx.Response(200, json=_responses_payload(companies=companies))

    run(client=_client(handler), prefs=_prefs(), exclusions=(), runs=2)

    assert "Docker" not in seen_queries[0]
    assert "Docker" in seen_queries[1]


def test_worst_case_cost_usd_is_positive_and_bounded() -> None:
    worst_case = worst_case_cost_usd("gpt-6-luna")
    assert 0 < worst_case < 1.0  # sanity bound; S24's observed real cost was ~$0.06/call
