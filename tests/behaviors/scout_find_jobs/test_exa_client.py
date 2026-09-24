"""Behavior tests for the Exa discovery client (A-1)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai import secrets_store
from gigai.scout.find_jobs.exa_client import (
    ATS_INCLUDE_DOMAINS,
    EXA_API_KEY_ENV_VAR,
    EXA_SEARCH_URL,
    EXA_TEXT_MAX_CHARACTERS,
    NUM_RESULTS,
    ExaClientError,
    ExaSearchClient,
)
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    FindJobsConfig,
    PostingRow,
    SourceToggles,
    SponsorshipStatus,
)


def _config(**overrides: object) -> FindJobsConfig:
    values: dict[str, object] = {
        "roles": ("software engineer",),
        "merged_queries": ("software engineer OR data engineer",),
        "location": "Denver, CO",
        "remote": True,
        "published_after": "2026-09-15T00:00:00Z",
        "sources": SourceToggles(exa=True, ats=True, hiringcafe=False),
        "countries": ("US",),
    }
    values.update(overrides)
    return FindJobsConfig(**values)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_missing_api_key_raises_without_leaking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(EXA_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request should be made without an API key")

    with pytest.raises(ExaClientError) as excinfo:
        ExaSearchClient().search(_client(handler), _config())

    assert excinfo.value.code == "exa_missing_key"
    assert EXA_API_KEY_ENV_VAR in str(excinfo.value)
    assert "gigai secrets add exa" in str(excinfo.value)
    assert "sk-" not in str(excinfo.value)


def test_api_key_used_from_secrets_store_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(EXA_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(EXA_API_KEY_ENV_VAR, "dotenv-exa-key")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(_client(handler), _config())

    assert len(captured) == 1
    assert captured[0].headers["x-api-key"] == "dotenv-exa-key"


def test_env_api_key_wins_over_secrets_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(EXA_API_KEY_ENV_VAR, "dotenv-exa-key")
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "env-exa-key")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(_client(handler), _config())

    assert len(captured) == 1
    assert captured[0].headers["x-api-key"] == "env-exa-key"


def test_empty_env_api_key_falls_back_to_secrets_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path))
    secrets_store.set(EXA_API_KEY_ENV_VAR, "dotenv-exa-key")
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(_client(handler), _config())

    assert len(captured) == 1
    assert captured[0].headers["x-api-key"] == "dotenv-exa-key"


def test_request_shape_and_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://boards.greenhouse.io/acme/jobs/12345",
                        "title": "Software Engineer",
                        "publishedDate": "2026-09-20T00:00:00Z",
                    },
                    {
                        "url": "https://jobs.lever.co/beta/abcde",
                        "title": "Data Engineer",
                        "publishedDate": None,
                    },
                    {
                        "url": "https://example.com/not-an-ats-posting",
                        "title": "Ignored - not an ATS domain",
                        "publishedDate": "2026-09-20T00:00:00Z",
                    },
                ]
            },
        )

    rows = ExaSearchClient().search(_client(handler), _config())

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == EXA_SEARCH_URL
    assert request.headers["x-api-key"] == "secret-exa-key"
    body = json.loads(request.content)
    assert body["query"] == "software engineer OR data engineer"
    assert body["numResults"] == NUM_RESULTS
    assert body["includeDomains"] == list(ATS_INCLUDE_DOMAINS)
    assert body["startPublishedDate"] == "2026-09-15T00:00:00Z"
    assert body["userLocation"] == "US"
    assert body["contents"] == {"text": {"maxCharacters": EXA_TEXT_MAX_CHARACTERS}}

    assert len(rows) == 2
    greenhouse_row = next(row for row in rows if row.provider is ATSProvider.GREENHOUSE)
    assert greenhouse_row.board_token == "acme"
    assert greenhouse_row.company == "acme"
    assert greenhouse_row.url == "https://boards.greenhouse.io/acme/jobs/12345"
    assert greenhouse_row.normalized_url == "https://boards.greenhouse.io/acme/jobs/12345"
    assert greenhouse_row.title == "Software Engineer"
    assert greenhouse_row.published_at == "2026-09-20T00:00:00Z"
    assert greenhouse_row.source_kind.value == "exa"
    assert greenhouse_row.query_key == "software engineer OR data engineer"
    assert greenhouse_row.content_sha256 is None

    lever_row = next(row for row in rows if row.provider is ATSProvider.LEVER)
    assert lever_row.board_token == "beta"
    assert lever_row.company == "beta"
    assert lever_row.published_at is None

    assert all("example.com" not in row.url for row in rows)


def test_result_text_is_mapped_onto_posting_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://boards.greenhouse.io/acme/jobs/12345",
                        "title": "Software Engineer",
                        "publishedDate": "2026-09-20T00:00:00Z",
                        "text": "We are unable to sponsor work visas for this role.",
                    },
                    {
                        "url": "https://jobs.lever.co/beta/abcde",
                        "title": "Data Engineer",
                        "publishedDate": None,
                        # no `text` key at all -- Exa didn't have page text for this result
                    },
                ]
            },
        )

    rows = ExaSearchClient().search(_client(handler), _config())

    greenhouse_row = next(row for row in rows if row.provider is ATSProvider.GREENHOUSE)
    assert greenhouse_row.text == "We are unable to sponsor work visas for this role."
    assert greenhouse_row.sponsorship is SponsorshipStatus.NOT_OFFERED

    lever_row = next(row for row in rows if row.provider is ATSProvider.LEVER)
    assert lever_row.text is None
    assert lever_row.sponsorship is SponsorshipStatus.UNKNOWN


def test_result_blank_text_is_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"url": "https://boards.greenhouse.io/acme/jobs/1", "title": "SE", "text": ""}]},
        )

    rows = ExaSearchClient().search(_client(handler), _config())
    assert rows[0].text is None


def test_every_emitted_row_round_trips_through_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://boards.greenhouse.io/acme/jobs/12345",
                        "title": "Software Engineer",
                        "publishedDate": "2026-09-20T00:00:00Z",
                    },
                    {
                        "url": "https://jobs.lever.co/beta/abcde",
                        "title": None,
                        "publishedDate": None,
                    },
                    {
                        "url": "https://jobs.ashbyhq.com/gamma/role-1",
                        "title": "Platform Engineer",
                        "publishedDate": "2026-09-18T00:00:00Z",
                    },
                ]
            },
        )

    rows = ExaSearchClient().search(_client(handler), _config())

    assert len(rows) == 3
    for row in rows:
        assert row.company
        assert row.title
        round_tripped = PostingRow.from_json(row.to_json())
        assert round_tripped == row

    lever_row = next(row for row in rows if row.provider is ATSProvider.LEVER)
    assert lever_row.title == "https://jobs.lever.co/beta/abcde"


def test_omits_start_published_date_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(_client(handler), _config(published_after=None))

    body = json.loads(captured[0].content)
    assert "startPublishedDate" not in body


def test_omits_user_location_when_countries_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(_client(handler), _config(countries=()))

    body = json.loads(captured[0].content)
    assert "userLocation" not in body


def test_omits_user_location_when_multiple_countries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(_client(handler), _config(countries=("US", "CA")))

    body = json.loads(captured[0].content)
    assert "userLocation" not in body


def test_sends_user_location_for_single_country(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(_client(handler), _config(countries=("GB",)))

    body = json.loads(captured[0].content)
    assert body["userLocation"] == "GB"


def test_multiple_merged_queries_issue_one_request_each(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(
        _client(handler),
        _config(merged_queries=("software engineer", "platform engineer")),
    )

    assert len(captured) == 2
    queries = {json.loads(request.content)["query"] for request in captured}
    assert queries == {"software engineer", "platform engineer"}


@pytest.mark.parametrize("status_code", [400, 401, 402, 429, 500, 503])
def test_http_error_status_raises_redacted_error(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "super-secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "nope"}, headers={"x-api-key": "super-secret-value"})

    with pytest.raises(ExaClientError) as excinfo:
        ExaSearchClient().search(_client(handler), _config())

    assert excinfo.value.code == f"exa_http_{status_code}"
    assert str(status_code) in str(excinfo.value)
    assert "super-secret-value" not in str(excinfo.value)
    assert "x-api-key" not in str(excinfo.value).lower()


def test_402_error_names_payment_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "super-secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "payment required"})

    with pytest.raises(ExaClientError) as excinfo:
        ExaSearchClient().search(_client(handler), _config())

    assert excinfo.value.code == "exa_http_402"
    message = str(excinfo.value)
    assert "402" in message
    assert "payment required" in message.lower()
    assert "out of credits" in message.lower()
    assert "super-secret-value" not in message


def test_429_error_names_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "super-secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down"})

    with pytest.raises(ExaClientError) as excinfo:
        ExaSearchClient().search(_client(handler), _config())

    assert excinfo.value.code == "exa_http_429"
    message = str(excinfo.value)
    assert "429" in message
    assert "rate limited" in message.lower()
    assert "super-secret-value" not in message


def test_bad_json_response_raises_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "super-secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json", headers={"content-type": "application/json"})

    with pytest.raises(ExaClientError) as excinfo:
        ExaSearchClient().search(_client(handler), _config())

    assert excinfo.value.code == "exa_bad_json"
    assert "super-secret-value" not in str(excinfo.value)


def test_missing_results_key_raises_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "super-secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(ExaClientError) as excinfo:
        ExaSearchClient().search(_client(handler), _config())

    assert excinfo.value.code == "exa_bad_json"
    assert "super-secret-value" not in str(excinfo.value)


def test_transport_failure_raises_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "super-secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ExaClientError) as excinfo:
        ExaSearchClient().search(_client(handler), _config())

    assert excinfo.value.code == "exa_transport"
    assert "super-secret-value" not in str(excinfo.value)
