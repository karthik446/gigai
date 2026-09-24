from __future__ import annotations

import httpx
import pytest

from gigai.scout.find_jobs.ats_board_clients import (
    ATSBoardClientError,
    ATSBoardClients,
    html_to_text,
    list_ashby_board,
    list_greenhouse_board,
    list_lever_board,
    matches_roles,
)
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    FindJobsConfig,
    SourceKind,
    SourceToggles,
    SponsorshipStatus,
    content_hash,
    normalize_url,
    parse_board_url,
)


def _config(roles: tuple[str, ...] = ("software engineer",)) -> FindJobsConfig:
    return FindJobsConfig(
        roles=roles,
        merged_queries=("software engineer",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- html_to_text (U25) -------------------------------------------------


def test_html_to_text_strips_tags_and_keeps_line_breaks() -> None:
    html = "<div><p>Intro paragraph.</p><ul><li>Item one</li><li>Item two</li></ul></div>"
    assert html_to_text(html) == "Intro paragraph.\nItem one\nItem two"


def test_html_to_text_decodes_entities() -> None:
    assert html_to_text("<p>Q&amp;A and R&amp;D</p>") == "Q&A and R&D"


def test_html_to_text_drops_script_and_style_bodies() -> None:
    html = "<p>Visible</p><script>var x = 'secret';</script><style>.a{color:red}</style><p>Also visible</p>"
    assert html_to_text(html) == "Visible\nAlso visible"


def test_html_to_text_passes_through_plain_text_unchanged() -> None:
    assert html_to_text("Already plain text, no markup here.") == "Already plain text, no markup here."


def test_html_to_text_handles_none_and_empty() -> None:
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


def test_html_to_text_tolerates_malformed_markup() -> None:
    html = "<p>Unclosed paragraph <b>bold text"
    # Never raises; degrades to the best-effort extracted text.
    assert "Unclosed paragraph" in html_to_text(html)
    assert "bold text" in html_to_text(html)


# --- matches_roles -----------------------------------------------------


def test_matches_roles_case_insensitive_token_containment() -> None:
    assert matches_roles("Senior Software Engineer", ("software engineer",))
    assert matches_roles("SOFTWARE ENGINEER II", ("software engineer",))
    assert not matches_roles("Data Analyst", ("software engineer",))


def test_matches_roles_requires_all_tokens_of_some_role() -> None:
    assert not matches_roles("Software Manager", ("software engineer",))
    assert matches_roles("Engineer, Software Platform", ("software engineer",))


def test_matches_roles_any_role_matches() -> None:
    roles = ("data engineer", "platform engineer")
    assert matches_roles("Staff Platform Engineer", roles)
    assert matches_roles("Senior Data Engineer", roles)
    assert not matches_roles("Product Manager", roles)


def test_matches_roles_empty_roles_matches_nothing() -> None:
    assert not matches_roles("Software Engineer", ())


def test_matches_roles_non_string_title_is_false() -> None:
    assert not matches_roles(None, ("software engineer",))  # type: ignore[arg-type]


# --- Greenhouse ----------------------------------------------------------


def test_greenhouse_url_and_mapping() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 101,
                        "title": "Software Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
                        "location": {"name": "Denver, CO"},
                        "updated_at": "2026-09-20T00:00:00Z",
                        "content": "<p>Build things.</p>",
                    },
                    {
                        "id": 102,
                        "title": "Marketing Manager",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
                        "location": {"name": "Remote"},
                        "updated_at": "2026-09-20T00:00:00Z",
                        "content": "<p>Not a match.</p>",
                    },
                ]
            },
        )

    with _client(handler) as client:
        rows = list_greenhouse_board(client, "acme", _config())

    assert seen_urls == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"]
    assert len(rows) == 1
    row = rows[0]
    assert row.provider is ATSProvider.GREENHOUSE
    assert row.board_token == "acme"
    assert row.title == "Software Engineer"
    assert row.url == "https://boards.greenhouse.io/acme/jobs/101"
    assert row.normalized_url == normalize_url(row.url)
    assert row.location == "Denver, CO"
    assert row.published_at == "2026-09-20T00:00:00Z"
    assert row.source_kind is SourceKind.ATS
    # U25: the stored text is HTML converted to plain text, and the digest
    # hashes that plain text (not the raw HTML markup).
    assert row.text == "Build things."
    assert row.content_sha256 == content_hash("Software Engineer\nBuild things.".encode("utf-8"))
    assert row.sponsorship is SponsorshipStatus.UNKNOWN


def test_greenhouse_both_hostnames_parse_via_contracts() -> None:
    assert parse_board_url("https://boards.greenhouse.io/acme/jobs/101") == ("greenhouse", "acme")
    assert parse_board_url("https://job-boards.greenhouse.io/acme") == ("greenhouse", "acme")


def test_greenhouse_http_error_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal secret trace")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_greenhouse_board(client, "acme", _config())

    assert exc_info.value.code == "http_error"
    assert "internal secret trace" not in str(exc_info.value)
    assert "acme" in str(exc_info.value)


def test_greenhouse_404_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_greenhouse_board(client, "missing", _config())

    assert exc_info.value.code == "http_error"


def test_greenhouse_bad_json_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{{{")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_greenhouse_board(client, "acme", _config())

    assert exc_info.value.code == "bad_json"


def test_greenhouse_unexpected_shape_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_greenhouse_board(client, "acme", _config())

    assert exc_info.value.code == "bad_json"


def test_greenhouse_sponsorship_derived_from_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 201,
                        "title": "Software Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/201",
                        "location": {"name": "Denver, CO"},
                        "updated_at": "2026-09-20T00:00:00Z",
                        "content": "<p>We are unable to sponsor work visas for this role.</p>",
                    }
                ]
            },
        )

    with _client(handler) as client:
        rows = list_greenhouse_board(client, "acme", _config())

    assert rows[0].sponsorship is SponsorshipStatus.NOT_OFFERED


# --- Lever -----------------------------------------------------------------


def test_lever_url_and_mapping() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Data Engineer",
                    "hostedUrl": "https://jobs.lever.co/bright/202",
                    "categories": {"location": "Remote"},
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Own the pipeline.",
                },
                {
                    "id": "def",
                    "text": "Sales Rep",
                    "hostedUrl": "https://jobs.lever.co/bright/203",
                    "categories": {"location": "Remote"},
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Not a match.",
                },
            ],
        )

    with _client(handler) as client:
        rows = list_lever_board(client, "bright", _config(("data engineer",)))

    assert seen_urls == ["https://api.lever.co/v0/postings/bright?mode=json"]
    assert len(rows) == 1
    row = rows[0]
    assert row.provider is ATSProvider.LEVER
    assert row.board_token == "bright"
    assert row.title == "Data Engineer"
    assert row.url == "https://jobs.lever.co/bright/202"
    assert row.normalized_url == normalize_url(row.url)
    assert row.location == "Remote"
    assert row.published_at == "2025-09-20T00:00:00Z"
    assert row.text == "Own the pipeline."
    assert row.content_sha256 == content_hash("Data Engineer\nOwn the pipeline.".encode("utf-8"))


def test_lever_lists_are_appended_to_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Data Engineer",
                    "hostedUrl": "https://jobs.lever.co/bright/202",
                    "categories": {"location": "Remote"},
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Own the pipeline.",
                    "lists": [
                        {"text": "Requirements", "content": "<ul><li>5+ years</li><li>Python</li></ul>"},
                        {"text": "Benefits", "content": "<p>Visa sponsorship available.</p>"},
                    ],
                }
            ],
        )

    with _client(handler) as client:
        rows = list_lever_board(client, "bright", _config(("data engineer",)))

    row = rows[0]
    assert row.text is not None
    assert "Own the pipeline." in row.text
    assert "Requirements" in row.text
    assert "5+ years" in row.text
    assert "Benefits" in row.text
    assert row.sponsorship is SponsorshipStatus.OFFERED


def test_lever_missing_lists_is_fine() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Data Engineer",
                    "hostedUrl": "https://jobs.lever.co/bright/202",
                    "categories": {"location": "Remote"},
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Own the pipeline.",
                }
            ],
        )

    with _client(handler) as client:
        rows = list_lever_board(client, "bright", _config(("data engineer",)))

    assert rows[0].text == "Own the pipeline."


def test_lever_url_parses_via_contracts() -> None:
    assert parse_board_url("https://jobs.lever.co/bright/202") == ("lever", "bright")


def test_lever_5xx_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream secret detail")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_lever_board(client, "bright", _config())

    assert exc_info.value.code == "http_error"
    assert "upstream secret detail" not in str(exc_info.value)


def test_lever_bad_json_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_lever_board(client, "bright", _config())

    assert exc_info.value.code == "bad_json"


def test_lever_unexpected_shape_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a list"})

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_lever_board(client, "bright", _config())

    assert exc_info.value.code == "bad_json"


# --- Ashby -------------------------------------------------------------


def test_ashby_url_and_mapping() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "j1",
                        "title": "Platform Engineer",
                        "location": "Remote",
                        "jobUrl": "https://jobs.ashbyhq.com/orbit/303",
                        "publishedAt": "2026-09-18T12:00:00Z",
                        "descriptionPlain": "Build the platform.",
                    },
                    {
                        "id": "j2",
                        "title": "Recruiter",
                        "location": "Remote",
                        "jobUrl": "https://jobs.ashbyhq.com/orbit/304",
                        "publishedAt": "2026-09-18T12:00:00Z",
                        "descriptionPlain": "Not a match.",
                    },
                ]
            },
        )

    with _client(handler) as client:
        rows = list_ashby_board(client, "orbit", _config(("platform engineer",)))

    assert seen_urls == ["https://api.ashbyhq.com/posting-api/job-board/orbit"]
    assert len(rows) == 1
    row = rows[0]
    assert row.provider is ATSProvider.ASHBY
    assert row.board_token == "orbit"
    assert row.title == "Platform Engineer"
    assert row.url == "https://jobs.ashbyhq.com/orbit/303"
    assert row.normalized_url == normalize_url(row.url)
    assert row.location == "Remote"
    assert row.published_at == "2026-09-18T12:00:00Z"
    assert row.text == "Build the platform."
    assert row.content_sha256 == content_hash("Platform Engineer\nBuild the platform.".encode("utf-8"))


def test_ashby_sponsorship_derived_from_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "j3",
                        "title": "Platform Engineer",
                        "location": "Remote",
                        "jobUrl": "https://jobs.ashbyhq.com/orbit/305",
                        "publishedAt": "2026-09-18T12:00:00Z",
                        "descriptionPlain": "You must be authorized to work without sponsorship.",
                    }
                ]
            },
        )

    with _client(handler) as client:
        rows = list_ashby_board(client, "orbit", _config(("platform engineer",)))

    assert rows[0].sponsorship is SponsorshipStatus.NOT_OFFERED


def test_ashby_url_parses_via_contracts() -> None:
    assert parse_board_url("https://jobs.ashbyhq.com/orbit/303") == ("ashby", "orbit")


def test_ashby_404_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="gone")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_ashby_board(client, "missing", _config())

    assert exc_info.value.code == "http_error"


def test_ashby_bad_json_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_ashby_board(client, "orbit", _config())

    assert exc_info.value.code == "bad_json"


def test_ashby_unexpected_shape_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nope": []})

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_ashby_board(client, "orbit", _config())

    assert exc_info.value.code == "bad_json"


# --- ATSBoardClients (protocol implementation) --------------------------


def test_ats_board_clients_dispatches_by_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    client_impl = ATSBoardClients()
    with _client(handler) as http_client:
        rows = client_impl.list_board(http_client, "greenhouse", "acme", _config())

    assert rows == ()


def test_ats_board_clients_unsupported_provider() -> None:
    client_impl = ATSBoardClients()
    with _client(lambda request: httpx.Response(200, json={})) as http_client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            client_impl.list_board(http_client, "workday", "acme", _config())

    assert exc_info.value.code == "unsupported_provider"


def test_network_error_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.5 with secret token abc123")

    with _client(handler) as client:
        with pytest.raises(ATSBoardClientError) as exc_info:
            list_greenhouse_board(client, "acme", _config())

    assert exc_info.value.code == "network_error"
    assert "secret token" not in str(exc_info.value)
    assert "10.0.0.5" not in str(exc_info.value)
