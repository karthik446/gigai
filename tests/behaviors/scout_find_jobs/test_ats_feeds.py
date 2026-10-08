"""0.1.11.8 more sources: the provider registry, the six new feed parsers, the board URL parser and the robots guard.

Every payload here is SYNTHETIC, shaped like the feeds probed on 2026-10-08 (research/expand-reach/probes), with
invented companies and postings.
"""

from __future__ import annotations

import json

import httpx
import pytest

from gigai.scout.find_jobs import ats_feeds, providers
from gigai.scout.find_jobs.ats_board_clients import (
    PUBLISHED_FIELDS,
    ATSBoardClientError,
    ATSBoardClients,
    BoardCache,
    BoardFetchStats,
    fetch_feed_board,
)
from gigai.scout.find_jobs.company_index import parse_board_body
from gigai.scout.find_jobs.contracts import ATSProvider, FindJobsConfig, SourceToggles, WorkMode, parse_board_url
from gigai.scout.find_jobs.robots_guard import ROBOTS_DISALLOWED, RobotsGuard
from gigai.scout.scout_new import PUBLISHED_KINDS


def _config(roles: tuple[str, ...] = ("software engineer",)) -> FindJobsConfig:
    return FindJobsConfig(
        roles=roles,
        merged_queries=("software engineer",),
        location=None,
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    )


# --- the registry ------------------------------------------------------------------------------------------


def test_registry_covers_every_enum_member_in_order() -> None:
    assert providers.provider_names() == tuple(member.value for member in ATSProvider)
    assert providers.provider_names()[:3] == ("greenhouse", "lever", "ashby")


def test_published_tables_match_the_registry() -> None:
    assert providers.published_fields() == PUBLISHED_FIELDS
    assert providers.published_kinds() == PUBLISHED_KINDS
    assert "pinpoint" not in PUBLISHED_FIELDS  # its feed carries no posted date


def test_registry_urls_and_helpers() -> None:
    assert providers.list_url("workable", "acme") == "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"
    assert providers.board_url("recruitee", "acme") == "https://acme.recruitee.com"
    assert providers.list_url("nope", "acme") is None
    assert providers.source_from_host("acme.breezy.hr") == "breezy"
    assert providers.source_from_host("www.example.com") is None
    assert providers.catalog_aliases()["ashbyhq"] is ATSProvider.ASHBY
    assert providers.catalog_aliases()["gem"] is ATSProvider.GEM
    assert providers.interval_for("recruitee", 0.125) == 1.0
    assert providers.interval_for("lever", 0.125) == 0.125


def test_jobs_from_payload_reads_each_envelope() -> None:
    assert providers.jobs_from_payload("gem", [{"id": 1}, "junk"]) == [{"id": 1}]
    assert providers.jobs_from_payload("rippling", {"items": [{"id": "a"}]}) == [{"id": "a"}]
    assert providers.jobs_from_payload("rippling", {"jobs": []}) is None
    assert providers.jobs_from_payload("workable", {"jobs": "no"}) is None


# --- parse_board_url: the new hosts ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://apply.workable.com/acme/", ("workable", "acme")),
        ("https://apply.workable.com/acme/j/ABC123/", ("workable", "acme")),
        ("https://acme.workable.com/", ("workable", "acme")),
        ("https://ats.rippling.com/acme-jobs/jobs/3f1b", ("rippling", "acme-jobs")),
        ("https://jobs.gem.com/acme/4003629005", ("gem", "acme")),
        ("https://acme.recruitee.com/o/senior-engineer", ("recruitee", "acme")),
        ("https://acme.pinpointhq.com/postings/12", ("pinpoint", "acme")),
        ("https://acme.breezy.hr/p/abc-senior-engineer", ("breezy", "acme")),
        ("https://www.recruitee.com/", None),
        ("https://app.breezy.hr/", None),
        ("https://a.b.recruitee.com/", None),
        ("https://careers.acme.com/", None),
        ("https://jobs.lever.co/acme", ("lever", "acme")),
    ],
)
def test_parse_board_url_new_hosts(url: str, expected: tuple[str, str] | None) -> None:
    assert parse_board_url(url) == expected


# --- dates ----------------------------------------------------------------------------------------------------


def test_published_at_from_text_accepts_each_spelling() -> None:
    assert ats_feeds.published_at_from_text("2026-05-29") == "2026-05-29T00:00:00Z"
    assert ats_feeds.published_at_from_text("2026-10-05 15:45:18 UTC") == "2026-10-05T15:45:18Z"
    assert ats_feeds.published_at_from_text("2025-09-09T07:20:34.454Z") == "2025-09-09T07:20:34.454Z"
    assert ats_feeds.published_at_from_text("2026-10-08T10:00:00+02:00") == "2026-10-08T10:00:00+02:00"
    assert ats_feeds.published_at_from_text("2026-10-08T10:00:00") is None  # naive: not a date
    assert ats_feeds.published_at_from_text("2026-13-40") is None
    assert ats_feeds.published_at_from_text(None) is None


# --- the six parsers ------------------------------------------------------------------------------------------

WORKABLE = {
    "name": "Acme Robotics",
    "jobs": [
        {
            "title": "Senior Software Engineer, Platform",
            "shortcode": "81B46579FE",
            "url": "https://apply.workable.com/j/81B46579FE",
            "published_on": "2026-05-29",
            "created_at": "2026-05-20",
            "telecommuting": True,
            "department": "Engineering",
            "country": "United States",
            "city": "Denver",
            "state": "Colorado",
            "locations": [{"country": "United States", "countryCode": "US", "city": "Denver", "region": "Colorado"}],
            "description": "<p>Build the platform.</p><ul><li>Python</li></ul>",
        },
        {"title": "Account Executive", "shortcode": "X1", "url": "https://apply.workable.com/j/X1", "published_on": "2026-05-29"},
        {"title": "Software Engineer", "shortcode": "NOURL"},
    ],
}


def test_workable_rows() -> None:
    stats = BoardFetchStats()
    rows = ats_feeds.workable_rows(WORKABLE["jobs"], "acme", _config(), stats)
    assert [row.title for row in rows] == ["Senior Software Engineer, Platform"]
    row = rows[0]
    assert row.provider is ATSProvider.WORKABLE
    assert row.url == "https://apply.workable.com/j/81B46579FE"
    assert row.published_at == "2026-05-29T00:00:00Z"
    assert row.countries == ("US",)
    assert row.work_mode is WorkMode.REMOTE
    assert row.location == "Remote (Denver, Colorado, United States)"
    assert row.text == "Build the platform.\nPython"
    assert row.query_key == "ats:workable:acme"
    assert stats.listed == 3 and stats.prefiltered_out == 1


RIPPLING_LIST = {
    "items": [
        {
            "id": "c224bc39-251f-47ba-b6f8-45b187c40471",
            "name": "Staff Software Engineer",
            "url": "https://ats.rippling.com/acme-jobs/jobs/c224bc39-251f-47ba-b6f8-45b187c40471",
            "department": {"name": "Engineering"},
            "locations": [{"name": "Remote (United States)", "country": "United States", "countryCode": "US", "state": "", "city": "", "workplaceType": "REMOTE"}],
        },
        {
            "id": "f62af676-fa3f-4f75-915e-b104ff5c845c",
            "name": "Software Engineer, Backend",
            "url": "https://ats.rippling.com/acme-jobs/jobs/f62af676-fa3f-4f75-915e-b104ff5c845c",
            "locations": [
                {"name": "New York", "countryCode": "US", "workplaceType": "ON_SITE"},
                {"name": "London", "countryCode": "GB", "workplaceType": "HYBRID"},
            ],
        },
    ],
    "totalItems": 2,
}
RIPPLING_DETAIL = {
    "uuid": "c224bc39-251f-47ba-b6f8-45b187c40471",
    "name": "Staff Software Engineer",
    "description": {"company": "<p>About Acme.</p>", "role": "<p>Own the platform.</p>"},
    "createdOn": "2026-10-01T12:00:00-07:00",
    "employmentType": {"label": "Full-time"},
}


def test_rippling_rows_read_date_and_text_from_the_detail() -> None:
    def lookup(job_id: str):
        return RIPPLING_DETAIL if job_id == "c224bc39-251f-47ba-b6f8-45b187c40471" else None

    rows = ats_feeds.rippling_rows(RIPPLING_LIST["items"], "acme-jobs", _config(), None, lookup)
    assert [row.title for row in rows] == ["Staff Software Engineer", "Software Engineer, Backend"]
    first, second = rows
    assert first.published_at == "2026-10-01T12:00:00-07:00"
    assert first.text == "About Acme.\nOwn the platform."
    assert first.work_mode is WorkMode.REMOTE and first.countries == ("US",)
    assert second.published_at is None and second.text is None
    assert second.work_mode is None  # two locations with different modes: not stated
    assert second.countries == ("GB", "US")
    assert second.location == "New York; London"


GEM = [
    {
        "id": 4003629005,
        "title": "Senior Software Engineer",
        "absolute_url": "https://jobs.gem.com/acme/4003629005",
        "content": "<div><strong>About</strong> us.</div>",
        "content_plain": "About us.",
        "first_published_at": "2026-09-30T18:04:11Z",
        "updated_at": "2026-10-02T18:04:11Z",
        "location": {"name": "San Francisco, United States"},
        "location_type": "hybrid",
        "departments": [{"name": "Engineering"}],
    }
]


def test_gem_rows() -> None:
    rows = ats_feeds.gem_rows(GEM, "acme", _config())
    (row,) = rows
    assert row.provider is ATSProvider.GEM
    assert row.published_at == "2026-09-30T18:04:11Z"
    assert row.text == "About us."
    assert row.countries == ("US",)
    assert row.work_mode is WorkMode.HYBRID
    assert row.location == "San Francisco, United States"


RECRUITEE = {
    "offers": [
        {
            "id": 1800123,
            "title": "Software Engineer - Payments",
            "slug": "software-engineer-payments",
            "careers_url": "https://careers.acme.example/o/software-engineer-payments",
            "location": "Amsterdam, Netherlands",
            "city": "Amsterdam",
            "country": "Netherlands",
            "country_code": "NL",
            "remote": False,
            "hybrid": True,
            "on_site": False,
            "created_at": "2026-10-05 15:45:18 UTC",
            "published_at": "2026-10-06 09:00:00 UTC",
            "updated_at": "2026-10-08 10:14:42 UTC",
            "department": "Engineering",
            "description": "<p>Payments team.</p>",
            "requirements": "<ul><li>Go</li></ul>",
        }
    ]
}


def test_recruitee_rows() -> None:
    (row,) = ats_feeds.recruitee_rows(RECRUITEE["offers"], "acme", _config())
    assert row.url == "https://careers.acme.example/o/software-engineer-payments"
    assert row.published_at == "2026-10-06T09:00:00Z"
    assert row.countries == ("NL",)
    assert row.work_mode is WorkMode.HYBRID
    assert row.text == "Payments team.\nGo"


PINPOINT = {
    "data": [
        {
            "id": 7,
            "title": "Software Engineer",
            "url": "https://acme.pinpointhq.com/postings/7",
            "location": {"id": 1, "name": "Austin, Texas", "city": "Austin", "province": "Texas", "country": "United States"},
            "workplace_type": "fully_remote",
            "workplace_type_text": "Fully remote",
            "department": "Engineering",
            "description": "<p>Ship things.</p>",
            "key_responsibilities": "<p>Own services.</p>",
            "skills_knowledge_expertise": "<p>Python.</p>",
            "benefits": "<p>Health.</p>",
        }
    ]
}


def test_pinpoint_rows_have_no_posted_date() -> None:
    (row,) = ats_feeds.pinpoint_rows(PINPOINT["data"], "acme", _config())
    assert row.published_at is None
    assert row.location == "Austin, Texas"
    assert row.countries == ("US",)
    assert row.work_mode is WorkMode.REMOTE
    assert row.text == "Ship things.\nOwn services.\nPython.\nHealth."


BREEZY = [
    {
        "id": "a8871d47cd6201",
        "friendly_id": "a8871d47cd6201-senior-software-engineer",
        "name": "Senior Software Engineer",
        "url": "https://acme.breezy.hr/p/a8871d47cd6201-senior-software-engineer",
        "published_date": "2026-09-09T07:20:34.454Z",
        "type": {"id": "fullTime", "name": "Full-Time"},
        "location": {"name": "Remote, United States", "country": {"name": "United States", "id": "US"}, "city": "", "is_remote": True},
        "department": "Engineering",
    }
]


def test_breezy_rows_list_without_text() -> None:
    (row,) = ats_feeds.breezy_rows(BREEZY, "acme", _config())
    assert row.published_at == "2026-09-09T07:20:34.454Z"
    assert row.text is None
    assert row.countries == ("US",)
    assert row.work_mode is WorkMode.REMOTE
    assert row.content_sha256 is not None  # the title alone, as a Greenhouse list row before its detail


# --- the generic fetch path and the company index -------------------------------------------------------------


def test_fetch_feed_board_caches_the_list_and_rippling_details(tmp_path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/api/v2/board/acme-jobs/jobs":
            return httpx.Response(200, json=RIPPLING_LIST, headers={"etag": '"v1"'})
        if request.url.path.startswith("/api/v1/board/acme-jobs/jobs/"):
            job_id = request.url.path.rsplit("/", 1)[-1]
            if job_id == RIPPLING_DETAIL["uuid"]:
                return httpx.Response(200, json=RIPPLING_DETAIL)
            return httpx.Response(404, json={"error": "gone"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = BoardCache(tmp_path / "ats-boards")
    result = ATSBoardClients().fetch_board(client, "rippling", "acme-jobs", _config(), cache=cache)
    assert [row.title for row in result.rows] == ["Staff Software Engineer", "Software Engineer, Backend"]
    assert result.stats.requests == 3 and result.stats.detail_fetched == 1 and result.stats.detail_failed == 1
    # The second fetch: a conditional list request (304 -> hit) and the cached detail, no detail request.
    def handler_304(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/api/v2/board/acme-jobs/jobs" and request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler_304))
    again = fetch_feed_board(client, "rippling", "acme-jobs", _config(), cache=cache)
    assert again.stats.cache == "hit" and again.stats.detail_cached == 1 and again.stats.detail_fetched == 0
    assert again.rows[0].text == "About Acme.\nOwn the platform."


def test_parse_board_body_for_each_new_provider() -> None:
    bodies = {
        "workable": WORKABLE,
        "rippling": RIPPLING_LIST,
        "gem": GEM,
        "recruitee": RECRUITEE,
        "pinpoint": PINPOINT,
        "breezy": BREEZY,
    }
    for provider, payload in bodies.items():
        observed = parse_board_body(provider, "acme", json.dumps(payload).encode("utf-8"))
        assert observed, provider
        for posting in observed.values():
            assert posting.title and posting.url, provider
    workable = parse_board_body("workable", "acme", json.dumps(WORKABLE).encode("utf-8"))
    assert set(workable) == {"81B46579FE", "X1"}  # the shortcode is the posting id; a job without a URL is skipped
    assert workable["81B46579FE"].content_sha256 is not None
    breezy = parse_board_body("breezy", "acme", json.dumps(BREEZY).encode("utf-8"))
    assert breezy["a8871d47cd6201"].content_sha256 is None  # no description in the list
    gem = parse_board_body("gem", "acme", json.dumps(GEM).encode("utf-8"))
    assert gem["4003629005"].updated_at == "2026-10-02T18:04:11Z"


def test_unknown_provider_still_fails_closed() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
    with pytest.raises(ATSBoardClientError) as excinfo:
        ATSBoardClients().list_board(client, "smartrecruiters", "acme", _config())
    assert excinfo.value.code == "unsupported_provider"


# --- the robots guard -----------------------------------------------------------------------------------------


def _robots_client(rules: dict[str, httpx.Response]) -> tuple[httpx.Client, list[str]]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return rules.get(request.url.host, httpx.Response(404))
        return httpx.Response(200, json=[])

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_robots_guard_blocks_a_disallowed_feed_and_remembers_the_host(tmp_path) -> None:
    client, calls = _robots_client({"acme.recruitee.com": httpx.Response(200, text="User-agent: *\nDisallow: /api/\n")})
    guard = RobotsGuard(tmp_path / "robots")
    with pytest.raises(ATSBoardClientError) as excinfo:
        guard.check(client, "https://acme.recruitee.com/api/offers/", "recruitee", "acme")
    assert excinfo.value.code == ROBOTS_DISALLOWED
    assert guard.allows(client, "https://acme.recruitee.com/o/job") is True
    assert calls.count("https://acme.recruitee.com/robots.txt") == 1  # one request per host, then memory
    # A fresh guard with the same directory reads the stored answer, no request.
    second = RobotsGuard(tmp_path / "robots")
    assert second.allows(client, "https://acme.recruitee.com/api/offers/") is False
    assert calls.count("https://acme.recruitee.com/robots.txt") == 1


def test_robots_guard_treats_missing_rules_as_allowed() -> None:
    client, calls = _robots_client({"api.ashbyhq.com": httpx.Response(401, text="Unauthorized"), "x.breezy.hr": httpx.Response(200, text="<html>page</html>", headers={"content-type": "text/html"})})
    guard = RobotsGuard()
    assert guard.allows(client, "https://api.ashbyhq.com/posting-api/job-board/acme") is True
    assert guard.allows(client, "https://x.breezy.hr/json") is True
    assert guard.allows(client, "https://nothing.pinpointhq.com/postings.json") is True


def test_board_clients_ask_the_guard_before_the_list() -> None:
    client, calls = _robots_client({"apply.workable.com": httpx.Response(200, text="User-agent: GigAI\nDisallow: /\n\nUser-agent: *\nAllow: /\n")})
    clients = ATSBoardClients(robots=RobotsGuard())
    with pytest.raises(ATSBoardClientError) as excinfo:
        clients.fetch_board(client, "workable", "acme", _config())
    assert excinfo.value.code == ROBOTS_DISALLOWED
    assert calls == ["https://apply.workable.com/robots.txt"]  # the feed itself was never asked
    assert ATSBoardClients().robots is None


def test_shared_guard_honours_the_environment_switch(tmp_path, monkeypatch) -> None:
    from gigai.scout.find_jobs import robots_guard

    monkeypatch.setattr(robots_guard, "_SHARED", None)
    assert robots_guard.shared_guard(tmp_path / "ats-boards", {"GIGAI_SCOUT_ROBOTS": "0"}) is None
    guard = robots_guard.shared_guard(tmp_path / "ats-boards", {})
    assert guard is not None and guard.cache_dir == tmp_path / "robots"
    assert robots_guard.shared_guard(tmp_path / "ats-boards", {"GIGAI_SCOUT_ROBOTS": "1"}) is guard
    monkeypatch.setattr(robots_guard, "_SHARED", None)


def test_update_gate_warms_rippling_details_for_the_titles_it_names(tmp_path) -> None:
    """The sources update lists with a config that matches no title; the gate names which details to fetch."""
    from gigai.scout.find_jobs.sources_update import listing_config

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v2/board/acme-jobs/jobs":
            return httpx.Response(200, json=RIPPLING_LIST)
        if request.url.path.endswith(RIPPLING_DETAIL["uuid"]):
            return httpx.Response(200, json=RIPPLING_DETAIL)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = BoardCache(tmp_path / "ats-boards")
    result = ATSBoardClients().fetch_board(
        client, "rippling", "acme-jobs", listing_config(), cache=cache, descriptions=True, title_filter=lambda title: title.startswith("Staff")
    )
    assert result.rows == ()  # the listing config matches no title
    assert calls == ["/api/v2/board/acme-jobs/jobs", f"/api/v1/board/acme-jobs/jobs/{RIPPLING_DETAIL['uuid']}"]
    # The company index reads the warmed detail back from the cache: date and text for that posting only.
    body = cache.lookup("rippling", providers.list_url("rippling", "acme-jobs")).body
    from gigai.scout.find_jobs.company_index import cached_detail_lookup

    observed = parse_board_body("rippling", "acme-jobs", body, detail_lookup=cached_detail_lookup(cache, "acme-jobs", ats="rippling"))
    staff = observed[RIPPLING_DETAIL["uuid"]]
    assert staff.published_at == "2026-10-01T12:00:00-07:00" and staff.content_sha256 is not None
    other = observed["f62af676-fa3f-4f75-915e-b104ff5c845c"]
    assert other.published_at is None and other.content_sha256 is None
