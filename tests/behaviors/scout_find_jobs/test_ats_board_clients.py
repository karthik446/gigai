from __future__ import annotations

import httpx
import pytest

from gigai.scout.find_jobs.ats_board_clients import (
    ATSBoardClientError,
    ATSBoardClients,
    ashby_pay,
    ashby_work_mode,
    fetch_greenhouse_board,
    greenhouse_pay,
    html_to_text,
    lever_pay,
    lever_work_mode,
    list_ashby_board,
    list_greenhouse_board,
    list_lever_board,
    matches_roles,
    work_mode_from_label,
)
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    FindJobsConfig,
    PayPeriod,
    PostingPay,
    SourceKind,
    SourceToggles,
    SponsorshipStatus,
    WorkMode,
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


# --- 0.1.8.1 B3: Greenhouse's `content` is *HTML-escaped HTML* -- real tags
# encoded as text ("&lt;p&gt;" not "<p>"), confirmed against a live evidence
# run's raw payload. The old "<" not in html check saw no literal "<" in
# that escaped string and returned it completely unprocessed, which broke
# sponsorship_from_text's phrase matching for every Greenhouse posting. -----


def test_html_to_text_decodes_double_escaped_greenhouse_style_markup() -> None:
    escaped = "&lt;p&gt;&lt;strong&gt;Team&lt;/strong&gt; intro. No visa sponsorship available.&lt;/p&gt;"
    assert html_to_text(escaped) == "Team intro. No visa sponsorship available."


def test_html_to_text_double_escaped_entities_still_decode_within_real_tags() -> None:
    # A field that's genuinely single-escaped HTML (the common case, already
    # covered by test_html_to_text_decodes_entities) must keep working
    # unchanged after adding the outer-unescape pass.
    assert html_to_text("<p>Q&amp;A and R&amp;D</p>") == "Q&A and R&D"


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


def test_greenhouse_sponsorship_derived_from_double_escaped_content() -> None:
    # 0.1.8.1 B3: Greenhouse's real `content` field is HTML-escaped HTML
    # ("&lt;p&gt;" not "<p>", confirmed against a live evidence run's raw
    # payload) -- this is the shape that actually broke sponsorship
    # detection for every Greenhouse row before the html_to_text fix.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 202,
                        "title": "Software Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/202",
                        "location": {"name": "Singapore"},
                        "updated_at": "2026-09-20T00:00:00Z",
                        "content": "&lt;p&gt;Candidates do not require company sponsorship. We will not sponsor visas for this role.&lt;/p&gt;",
                    }
                ]
            },
        )

    with _client(handler) as client:
        rows = list_greenhouse_board(client, "acme", _config())

    assert "&lt;" not in (rows[0].text or "")
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


# --- 0.1.8.1 B1: Lever's structured `country` + `categories.allLocations`
# (`.orchestrator/research/country-data.md` §2: Lever's own postings-api
# README documents `country` as "An ISO 3166-1 alpha-2 code ... or null"). --


def test_lever_structured_country_read_from_country_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Data Engineer",
                    "hostedUrl": "https://jobs.lever.co/bright/202",
                    "categories": {"location": "Denver, CO"},
                    "country": "US",
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Own the pipeline.",
                }
            ],
        )

    with _client(handler) as client:
        rows = list_lever_board(client, "bright", _config(("data engineer",)))

    assert rows[0].countries == ("US",)


def test_lever_structured_country_null_falls_back_to_none() -> None:
    # Lever's own doc: `country` may be null "to indicate an unknown
    # country" -- the row must carry `countries=None` (no structured signal)
    # so callers fall back to parsing the free-text `location`, not a
    # trusted-but-empty result.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Data Engineer",
                    "hostedUrl": "https://jobs.lever.co/bright/202",
                    "categories": {"location": "Denver, CO"},
                    "country": None,
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Own the pipeline.",
                }
            ],
        )

    with _client(handler) as client:
        rows = list_lever_board(client, "bright", _config(("data engineer",)))

    assert rows[0].countries is None


def test_lever_structured_country_missing_field_falls_back_to_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Data Engineer",
                    "hostedUrl": "https://jobs.lever.co/bright/202",
                    "categories": {"location": "Denver, CO"},
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Own the pipeline.",
                }
            ],
        )

    with _client(handler) as client:
        rows = list_lever_board(client, "bright", _config(("data engineer",)))

    assert rows[0].countries is None


def test_lever_structured_all_locations_adds_to_country_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Data Engineer",
                    "hostedUrl": "https://jobs.lever.co/bright/202",
                    "categories": {"location": "Denver, CO", "allLocations": ["United States", "Canada"]},
                    "country": "US",
                    "createdAt": 1758326400000,
                    "descriptionPlain": "Own the pipeline.",
                }
            ],
        )

    with _client(handler) as client:
        rows = list_lever_board(client, "bright", _config(("data engineer",)))

    assert rows[0].countries == ("CA", "US")


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

    # Q4b-data: compensation is served only when asked for; same single request.
    assert seen_urls == ["https://api.ashbyhq.com/posting-api/job-board/orbit?includeCompensation=true"]
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


# --- 0.1.8.1 B1: Ashby's structured `address.postalAddress.addressCountry`
# + `secondaryLocations` (shapes confirmed against the evidence run's real
# raw payload -- `.orchestrator/research/country-data.md` §2, and this
# packet's own worker read of `raw/ashby/*.json.gz` in that run: a fully
# region-labelled posting sends `"address": null`; a located one sends
# `addressCountry` as a full country *name* like "United States", never an
# already-ISO code, so this always normalizes through pycountry). ----------


def test_ashby_structured_country_read_from_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "j1",
                        "title": "Platform Engineer",
                        "location": "San Francisco",
                        "jobUrl": "https://jobs.ashbyhq.com/orbit/303",
                        "publishedAt": "2026-09-18T12:00:00Z",
                        "descriptionPlain": "Build the platform.",
                        "address": {"postalAddress": {"addressCountry": "United States", "addressLocality": "San Francisco"}},
                    }
                ]
            },
        )

    with _client(handler) as client:
        rows = list_ashby_board(client, "orbit", _config(("platform engineer",)))

    assert rows[0].countries == ("US",)


def test_ashby_structured_country_null_address_falls_back_to_none() -> None:
    # Evidence run: a region-labelled posting ("AMER") sends `"address":
    # null` -- must not be misread as a trusted-empty structured result;
    # `countries` stays None so callers fall back to parsing the free-text
    # `location` string ("AMER"), which itself correctly stays ambiguous.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "j1",
                        "title": "Platform Engineer",
                        "location": "AMER",
                        "jobUrl": "https://jobs.ashbyhq.com/orbit/303",
                        "publishedAt": "2026-09-18T12:00:00Z",
                        "descriptionPlain": "Build the platform.",
                        "address": None,
                        "secondaryLocations": [],
                    }
                ]
            },
        )

    with _client(handler) as client:
        rows = list_ashby_board(client, "orbit", _config(("platform engineer",)))

    assert rows[0].countries is None
    assert rows[0].location == "AMER"


def test_ashby_structured_secondary_locations_add_to_country_field() -> None:
    # Evidence run's own secondaryLocations shape: entries can mix a
    # populated address with a null one in the same list (only the country
    # name string is sent, not an ISO code either).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "j1",
                        "title": "Platform Engineer",
                        "location": "United States",
                        "jobUrl": "https://jobs.ashbyhq.com/orbit/303",
                        "publishedAt": "2026-09-18T12:00:00Z",
                        "descriptionPlain": "Build the platform.",
                        "address": {"postalAddress": {"addressCountry": "United States"}},
                        "secondaryLocations": [
                            {"location": "Germany", "address": None},
                            {"location": "The Netherlands", "address": {"postalAddress": {"addressCountry": "The Netherlands"}}},
                        ],
                    }
                ]
            },
        )

    with _client(handler) as client:
        rows = list_ashby_board(client, "orbit", _config(("platform engineer",)))

    assert rows[0].countries == ("NL", "US")


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


# --- Q4b-data: work_mode + pay from the providers' structured fields only ---


def _ashby_job(**extra: object) -> dict[str, object]:
    return {
        "id": "j1",
        "title": "Platform Engineer",
        "location": "Remote",
        "jobUrl": "https://jobs.ashbyhq.com/orbit/303",
        "publishedAt": "2026-09-18T12:00:00Z",
        "descriptionPlain": "Build the platform. Remote within the US. $200k.",
        **extra,
    }


def _lever_job(**extra: object) -> dict[str, object]:
    return {
        "id": "abc",
        "text": "Software Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/abc",
        "categories": {"location": "Remote - US"},
        "createdAt": 1758326400000,
        "descriptionPlain": "Build things. Fully remote. Pays $150,000.",
        **extra,
    }


def _greenhouse_job(**extra: object) -> dict[str, object]:
    return {
        "id": 101,
        "title": "Software Engineer",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
        "location": {"name": "Remote"},
        "updated_at": "2026-09-20T00:00:00Z",
        "content": "<p>Remote role paying $180,000 a year.</p>",
        **extra,
    }


def test_work_mode_from_label_accepts_both_providers_spellings_only() -> None:
    assert work_mode_from_label("Remote") is WorkMode.REMOTE
    assert work_mode_from_label("remote") is WorkMode.REMOTE
    assert work_mode_from_label("Hybrid") is WorkMode.HYBRID
    assert work_mode_from_label("OnSite") is WorkMode.ONSITE
    assert work_mode_from_label("onsite") is WorkMode.ONSITE
    assert work_mode_from_label("on-site") is WorkMode.ONSITE
    assert work_mode_from_label("unspecified") is None
    assert work_mode_from_label("Remote (US only)") is None  # free text is not a stated mode
    assert work_mode_from_label(None) is None
    assert work_mode_from_label(True) is None


def test_ashby_work_mode_and_pay_present() -> None:
    job = _ashby_job(
        workplaceType="Hybrid",
        isRemote=False,
        compensation={
            "compensationTierSummary": "$150K – $190K • Offers Equity",
            "summaryComponents": [
                {"compensationType": "EquityPercentage", "interval": "NONE", "currencyCode": None, "minValue": 0.01, "maxValue": 0.05},
                {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 150000, "maxValue": 190000},
            ],
        },
    )
    assert ashby_work_mode(job) is WorkMode.HYBRID
    assert ashby_pay(job) == PostingPay(150000, 190000, "USD", PayPeriod.YEAR)

    with _client(lambda request: httpx.Response(200, json={"jobs": [job]})) as client:
        rows = list_ashby_board(client, "orbit", _config(("platform engineer",)))
    assert rows[0].work_mode is WorkMode.HYBRID
    assert rows[0].pay == PostingPay(150000, 190000, "USD", PayPeriod.YEAR)
    assert rows[0].to_json()["work_mode"] == "hybrid"
    assert rows[0].to_json()["pay"] == {"min": 150000, "max": 190000, "currency": "USD", "period": "year"}


def test_ashby_is_remote_true_alone_means_remote_false_alone_means_nothing() -> None:
    assert ashby_work_mode(_ashby_job(isRemote=True)) is WorkMode.REMOTE
    assert ashby_work_mode(_ashby_job(isRemote=False)) is None
    assert ashby_work_mode(_ashby_job(workplaceType="OnSite", isRemote=True)) is WorkMode.ONSITE  # the label wins


def test_ashby_pay_falls_back_to_the_first_tier_and_reads_hourly_and_monthly_intervals() -> None:
    tiers = {"compensationTiers": [{"title": "US", "components": [{"compensationType": "Salary", "interval": "1 HOUR", "currencyCode": "usd", "minValue": 60, "maxValue": None}]}]}
    assert ashby_pay(_ashby_job(compensation=tiers)) == PostingPay(60, None, "USD", PayPeriod.HOUR)
    monthly = {"summaryComponents": [{"compensationType": "Salary", "interval": "1 MONTH", "currencyCode": "EUR", "minValue": None, "maxValue": 9000}]}
    assert ashby_pay(_ashby_job(compensation=monthly)) == PostingPay(None, 9000, "EUR", PayPeriod.MONTH)
    no_interval = {"summaryComponents": [{"compensationType": "Salary", "currencyCode": "USD", "minValue": 1, "maxValue": 2}]}
    assert ashby_pay(_ashby_job(compensation=no_interval)) == PostingPay(1, 2, "USD", None)


def test_ashby_work_mode_and_pay_absent_stay_absent_never_inferred_from_text() -> None:
    # The description says "Remote" and "$200k"; the structured fields do not.
    job = _ashby_job()
    assert ashby_work_mode(job) is None
    assert ashby_pay(job) is None
    with _client(lambda request: httpx.Response(200, json={"jobs": [job]})) as client:
        rows = list_ashby_board(client, "orbit", _config(("platform engineer",)))
    assert rows[0].work_mode is None and rows[0].pay is None
    assert "work_mode" not in rows[0].to_json() and "pay" not in rows[0].to_json()
    # Stated-but-unusable shapes are skipped, never guessed at.
    assert ashby_pay(_ashby_job(compensation={"summaryComponents": [{"compensationType": "Bonus", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 1, "maxValue": 2}]})) is None
    assert ashby_pay(_ashby_job(compensation={"summaryComponents": [{"compensationType": "Salary", "interval": "NONE", "currencyCode": "USD", "minValue": 1, "maxValue": 2}]})) is None
    assert ashby_pay(_ashby_job(compensation={"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": None, "maxValue": None}]})) is None
    assert ashby_pay(_ashby_job(compensation={"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "", "minValue": 1, "maxValue": 2}]})) is None
    assert ashby_pay(_ashby_job(compensation={"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 5, "maxValue": 2}]})) is None
    assert ashby_pay(_ashby_job(compensation={"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": True, "maxValue": "2"}]})) is None
    assert ashby_pay(_ashby_job(compensation="$1-$2")) is None
    assert ashby_pay(_ashby_job(compensation={"compensationTiers": []})) is None


def test_lever_work_mode_and_pay_present() -> None:
    job = _lever_job(workplaceType="remote", salaryRange={"min": 140000, "max": 180000, "currency": "USD", "interval": "per-year-salary"})
    assert lever_work_mode(job) is WorkMode.REMOTE
    assert lever_pay(job) == PostingPay(140000, 180000, "USD", PayPeriod.YEAR)
    with _client(lambda request: httpx.Response(200, json=[job])) as client:
        rows = list_lever_board(client, "acme", _config())
    assert rows[0].work_mode is WorkMode.REMOTE
    assert rows[0].pay == PostingPay(140000, 180000, "USD", PayPeriod.YEAR)
    assert rows[0].to_json()["pay"] == {"min": 140000, "max": 180000, "currency": "USD", "period": "year"}


def test_lever_intervals_map_or_skip() -> None:
    assert lever_pay(_lever_job(salaryRange={"min": 40, "max": 55, "currency": "USD", "interval": "per-hour-wage"})) == PostingPay(40, 55, "USD", PayPeriod.HOUR)
    assert lever_pay(_lever_job(salaryRange={"min": 8000, "max": 9000, "currency": "GBP", "interval": "per-month-salary"})) == PostingPay(8000, 9000, "GBP", PayPeriod.MONTH)
    # An interval the contract cannot express (stated, but not year/hour/month) is skipped, not mislabeled.
    assert lever_pay(_lever_job(salaryRange={"min": 2000, "max": 2500, "currency": "USD", "interval": "per-week-salary"})) is None
    assert lever_pay(_lever_job(salaryRange={"min": 2000, "max": 2500, "currency": "USD", "interval": "one-time"})) is None
    # No interval at all: the range is stated, the period is not.
    assert lever_pay(_lever_job(salaryRange={"min": 2000, "max": 2500, "currency": "USD"})) == PostingPay(2000, 2500, "USD", None)
    assert lever_work_mode(_lever_job(workplaceType="on-site")) is WorkMode.ONSITE
    assert lever_work_mode(_lever_job(workplaceType="hybrid")) is WorkMode.HYBRID


def test_lever_work_mode_and_pay_absent_stay_absent_never_inferred_from_text() -> None:
    job = _lever_job(workplaceType="unspecified")
    assert lever_work_mode(job) is None
    assert lever_pay(job) is None
    assert lever_pay(_lever_job(salaryRange=None)) is None
    assert lever_pay(_lever_job(salaryRange={"currency": "USD", "interval": "per-year-salary"})) is None
    with _client(lambda request: httpx.Response(200, json=[job])) as client:
        rows = list_lever_board(client, "acme", _config())
    assert rows[0].work_mode is None and rows[0].pay is None
    assert "work_mode" not in rows[0].to_json() and "pay" not in rows[0].to_json()


def test_greenhouse_pay_from_pay_input_ranges_cents_to_units_no_period() -> None:
    job = _greenhouse_job(pay_input_ranges=[{"min_cents": 15000000, "max_cents": 18000050, "currency_type": "USD", "title": "Salary Range"}])
    assert greenhouse_pay(job) == PostingPay(150000, 180000.5, "USD", None)
    with _client(lambda request: httpx.Response(200, json={"jobs": [job]})) as client:
        rows = list_greenhouse_board(client, "acme", _config())
    assert rows[0].pay == PostingPay(150000, 180000.5, "USD", None)
    assert rows[0].work_mode is None  # Greenhouse has no workplace-type field
    assert rows[0].to_json()["pay"] == {"min": 150000, "max": 180000.5, "currency": "USD", "period": None}
    assert "work_mode" not in rows[0].to_json()
    # A one-sided range is still a stated range; an empty/absent list is not.
    assert greenhouse_pay(_greenhouse_job(pay_input_ranges=[{"min_cents": None, "max_cents": 100, "currency_type": "USD"}])) == PostingPay(None, 1, "USD", None)
    assert greenhouse_pay(_greenhouse_job(pay_input_ranges=[])) is None
    assert greenhouse_pay(_greenhouse_job(pay_input_ranges=[{"title": "n/a"}])) is None
    assert greenhouse_pay(_greenhouse_job()) is None


def test_greenhouse_pay_absent_stays_absent_and_the_digest_ignores_pay() -> None:
    plain = _greenhouse_job()
    with_pay = _greenhouse_job(pay_input_ranges=[{"min_cents": 100, "max_cents": 200, "currency_type": "USD"}])
    with _client(lambda request: httpx.Response(200, json={"jobs": [plain]})) as client:
        (row,) = list_greenhouse_board(client, "acme", _config())
    with _client(lambda request: httpx.Response(200, json={"jobs": [with_pay]})) as client:
        (paid,) = list_greenhouse_board(client, "acme", _config())
    assert row.pay is None and "pay" not in row.to_json()
    assert paid.pay is not None
    # The change-detection identity (title + text) is untouched by the new field.
    assert row.content_sha256 == paid.content_sha256


def test_two_phase_greenhouse_reads_pay_from_the_detail_payload_already_fetched() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/jobs/101"):
            return httpx.Response(
                200,
                json={**_greenhouse_job(), "content": "<p>Detail text.</p>", "pay_input_ranges": [{"min_cents": 12000000, "max_cents": 16000000, "currency_type": "USD"}]},
            )
        return httpx.Response(200, json={"jobs": [{k: v for k, v in _greenhouse_job().items() if k != "content"}]})

    with _client(handler) as client:
        result = fetch_greenhouse_board(client, "acme", _config())
    assert seen == ["/v1/boards/acme/jobs", "/v1/boards/acme/jobs/101"]  # no extra request for pay
    (row,) = result.rows
    assert row.text == "Detail text."
    assert row.pay == PostingPay(120000, 160000, "USD", None)
