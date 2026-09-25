"""Behavior tests for the Jev pre-rank HTTP client (P6).

Mirrors ``test_exa_client.py``'s shape: ``httpx.MockTransport``, no live
network. Response fixtures match ``jev-api-notes.md``'s EXECUTED shape
exactly (``answers.<key>.{choice|score|noul}``, ``usage.cost_usd``).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai import secrets_store
from gigai.scout.find_jobs.jev_client import (
    JEV_API_KEY_ENV_VAR,
    JEV_DECIDE_URL,
    JevClient,
    JevClientError,
    JevMissingKeyError,
    has_api_key,
    require_api_key,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _good_response(request: httpx.Request, *, fit: str = "strong", score: float = 8, cost_usd: float = 0.0005) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "jev-1.13.0",
            "answers": {
                "fit": {"choice": fit, "confidence": 0.9},
                "score": {"score": score},
                "top_reason": {"choice": "stack_match"},
                "flag_domain": {"noul": 0.1},
                "flag_seniority": {"noul": 0.9},
                "flag_stack": {"noul": 0.0},
                "flag_location": {"noul": 0.0},
                "flag_sponsorship": {"noul": 0.0},
            },
            "usage": {"input_tokens": 1200, "cost_usd": cost_usd, "credits_remaining_usd": 9.9},
        },
        request=request,
    )


def test_missing_api_key_raises_without_leaking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(JEV_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))

    with pytest.raises(JevMissingKeyError) as excinfo:
        require_api_key()

    assert excinfo.value.code == "jev_missing_key"
    assert JEV_API_KEY_ENV_VAR in str(excinfo.value)
    assert "gigai secrets add jev" in str(excinfo.value)


def test_has_api_key_false_without_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(JEV_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    assert has_api_key() is False


def test_has_api_key_true_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    assert has_api_key() is True


def test_api_key_used_from_secrets_store_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(JEV_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(JEV_API_KEY_ENV_VAR, "dotenv-jev-key")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _good_response(request)

    client = JevClient(require_api_key(), _client(handler))
    client.score({"resume": "x"})

    assert len(captured) == 1
    assert captured[0].headers["authorization"] == "Bearer dotenv-jev-key"


def test_env_api_key_wins_over_secrets_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(JEV_API_KEY_ENV_VAR, "dotenv-jev-key")
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "env-jev-key")

    assert require_api_key() == "env-jev-key"


def test_request_shape_and_response_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_secret")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _good_response(request, fit="strong", score=7.5, cost_usd=0.000504)

    client = JevClient(require_api_key(), _client(handler))
    score = client.score({"resume": "resume text", "posting": {"title": "SWE"}})

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == JEV_DECIDE_URL
    assert request.headers["authorization"] == "Bearer jv_live_secret"
    assert request.headers["content-type"] == "application/json"

    import json

    body = json.loads(request.content)
    assert body["model"] == "jev-latest"
    assert body["state"] == {"resume": "resume text", "posting": {"title": "SWE"}}
    assert set(body["questions"]) == {
        "fit",
        "score",
        "top_reason",
        "flag_domain",
        "flag_seniority",
        "flag_stack",
        "flag_location",
        "flag_sponsorship",
    }
    assert body["questions"]["fit"]["type"] == "choice"
    assert body["questions"]["score"]["type"] == "score"
    assert body["questions"]["flag_domain"]["type"] == "noul"

    assert score.fit == "strong"
    # 7.5 / 9 * 100, rounded -- same mapping S28's jev_classify.py used.
    assert score.score == round(7.5 * 100 / 9)
    assert score.reasons == ("stack_match",)
    # flag_seniority=0.9 >= 0.5 -> flagged; flag_domain=0.1 -> not flagged.
    assert score.mismatch_flags == ("seniority",)
    assert score.cost_usd == "0.000504"


def test_no_mismatch_flags_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "fit": {"choice": "no"},
                    "score": {"score": 0},
                    "top_reason": {"choice": "domain_mismatch"},
                    "flag_domain": {"noul": 0.49},
                    "flag_seniority": {"noul": 0.0},
                    "flag_stack": {"noul": 0.0},
                    "flag_location": {"noul": 0.0},
                    "flag_sponsorship": {"noul": 0.0},
                },
                "usage": {"cost_usd": 0.0005},
            },
            request=request,
        )

    client = JevClient(require_api_key(), _client(handler))
    score = client.score({"resume": "x"})
    assert score.mismatch_flags == ()
    assert score.score == 0


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 404, 502])
def test_http_error_status_raises_redacted_error(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_super_secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "nope"}, request=request)

    client = JevClient(require_api_key(), _client(handler))
    with pytest.raises(JevClientError) as excinfo:
        client.score({"resume": "x"})

    assert excinfo.value.code == f"jev_http_{status_code}"
    assert str(status_code) in str(excinfo.value)
    assert "jv_live_super_secret" not in str(excinfo.value)


def test_401_error_names_invalid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"}, request=request)

    client = JevClient(require_api_key(), _client(handler))
    with pytest.raises(JevClientError) as excinfo:
        client.score({"resume": "x"})
    assert "invalid or missing api key" in str(excinfo.value).lower()


def test_402_error_names_insufficient_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "no credits"}, request=request)

    client = JevClient(require_api_key(), _client(handler))
    with pytest.raises(JevClientError) as excinfo:
        client.score({"resume": "x"})
    assert "insufficient credits" in str(excinfo.value).lower()


def test_502_error_names_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "bad gateway"}, request=request)

    client = JevClient(require_api_key(), _client(handler))
    with pytest.raises(JevClientError) as excinfo:
        client.score({"resume": "x"})
    assert "upstream error" in str(excinfo.value).lower()


def test_transport_failure_raises_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_super_secret")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = JevClient(require_api_key(), _client(handler))
    with pytest.raises(JevClientError) as excinfo:
        client.score({"resume": "x"})
    assert excinfo.value.code == "jev_transport"
    assert "jv_live_super_secret" not in str(excinfo.value)


def test_bad_json_response_raises_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_super_secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json", headers={"content-type": "application/json"}, request=request)

    client = JevClient(require_api_key(), _client(handler))
    with pytest.raises(JevClientError) as excinfo:
        client.score({"resume": "x"})
    assert excinfo.value.code == "jev_bad_json"


def test_missing_fit_answer_raises_bad_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {}, "usage": {}}, request=request)

    client = JevClient(require_api_key(), _client(handler))
    with pytest.raises(JevClientError) as excinfo:
        client.score({"resume": "x"})
    assert excinfo.value.code == "jev_bad_response"
