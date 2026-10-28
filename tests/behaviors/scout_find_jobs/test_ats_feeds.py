"""0.1.11.8 more sources: the provider registry, the six new feed parsers, the board URL parser and the robots guard.

Every payload here is SYNTHETIC, shaped like the feeds probed on 2026-10-08 (research/expand-reach/probes), with
invented companies and postings.
"""

from __future__ import annotations

from datetime import date
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
    assert providers.provider_names()[-1] == "workday"  # 0.1.11.12: the tenth, last in the enum


def test_published_tables_match_the_registry() -> None:
    assert providers.published_fields() == PUBLISHED_FIELDS
    assert providers.published_kinds() == PUBLISHED_KINDS
    assert "pinpoint" not in PUBLISHED_FIELDS  # its feed carries no posted date
    assert PUBLISHED_FIELDS["workday"] == "postedOn" and PUBLISHED_KINDS["workday"] == "posted"


def test_registry_urls_and_helpers() -> None:
    assert providers.list_url("workable", "acme") == "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"
    assert providers.board_url("recruitee", "acme") == "https://acme.recruitee.com"
    assert providers.list_url("nope", "acme") is None
    assert providers.source_from_host("acme.breezy.hr") == "breezy"
    assert providers.source_from_host("www.example.com") is None
    assert providers.catalog_aliases()["ashbyhq"] is ATSProvider.ASHBY
    assert providers.catalog_aliases()["gem"] is ATSProvider.GEM
    assert providers.interval_for("recruitee", 0.125) == 0.125  # no evidence of a limit on a tenant host: the default pace
    assert providers.interval_for("workable", 0.125) == 2.0  # apply.workable.com answers 429 at a faster pace (1 s was refused in the end)
    assert providers.interval_for("lever", 0.125) == 0.125
    assert providers.interval_for("workday", 0.125) == 1.0  # one tenant host at a time, one request a second (plan D4)
    assert providers.catalog_aliases()["myworkdayjobs"] is ATSProvider.WORKDAY
    assert providers.source_from_host("nvidia.wd5.myworkdayjobs.com") == "workday"
    assert "myworkdayjobs.com" in providers.board_hosts()
    assert providers.spec("workday").concurrency == 1 and providers.spec("workday").public_paths is None
    assert all(item.url_rules is None and item.concurrency is None and item.public_paths is None for item in providers.registry().values() if item.name != "workday")
    assert providers.token_from_list_url("workable", "https://apply.workable.com/api/v1/widget/accounts/acme?details=true") == "acme"
    assert providers.token_from_list_url("workable", "https://apply.workable.com/api/v1/widget/accounts/a/b?details=true") is None


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
        # 0.1.11.12 Workday: tenant and pod from the host, site from the path after an optional locale.
        ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", ("workday", "nvidia.wd5.NVIDIAExternalCareerSite")),
        ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/", ("workday", "nvidia.wd5.NVIDIAExternalCareerSite")),
        ("https://salesforce.wd12.myworkdayjobs.com/fr-CA/External_Career_Site/job/Toronto/Software-Engineer_JR-290013", ("workday", "salesforce.wd12.External_Career_Site")),
        ("https://nvidia.wd5.myworkdayjobs.com/en-US", None),
        ("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs", None),
        ("https://nvidia.myworkdayjobs.com/NVIDIAExternalCareerSite", None),
        ("https://a.b.wd5.myworkdayjobs.com/site", None),
        ("https://myworkdayjobs.com/site", None),
        ("https://wd5.myworkday.com/nvidia/d/home.htmld", None),
        ("https://nvidia.wd5.myworkday.com/en-US/NVIDIAExternalCareerSite", None),
    ],
)
def test_parse_board_url_new_hosts(url: str, expected: tuple[str, str] | None) -> None:
    assert parse_board_url(url) == expected


def test_workday_token_splits_and_urls() -> None:
    from gigai.scout.find_jobs.company_catalog import parse_company_record
    from gigai.scout.find_jobs.company_index import board_list_url, slug_from_list_url
    from gigai.scout.find_jobs.contracts import split_workday_token
    from gigai.scout.find_jobs.posting_live import board_job_url
    from gigai.scout.find_jobs.watchlist import watchlist_entry_from_url

    token = "nvidia.wd5.NVIDIAExternalCareerSite"
    assert split_workday_token(token) == ("nvidia", "wd5", "NVIDIAExternalCareerSite")
    assert split_workday_token("nvidia.wd5") is None and split_workday_token("nvidia.wd5/Site") is None and split_workday_token("Nvidia.wd5.Site") is None
    assert split_workday_token("acme.wd5.a.b") is None and split_workday_token(None) is None
    list_url = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
    assert providers.list_url("workday", token) == list_url
    assert providers.board_url("workday", token) == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    assert providers.list_url("workday", "nvidia") is None and providers.board_url("workday", "nvidia/site") is None
    assert providers.token_from_list_url("workday", list_url) is None  # a POST list never carries validators
    assert slug_from_list_url("workday", list_url) is None
    assert board_list_url("workday", token) == list_url
    with pytest.raises(Exception) as excinfo:
        board_list_url("workday", "nvidia")
    assert excinfo.value.code == "bad_slug"  # type: ignore[attr-defined]
    rules = providers.spec("workday").url_rules
    assert rules is not None
    detail = rules.detail_url(token, {"externalPath": "/job/US-CA-Santa-Clara/Senior-Software-Engineer_JR1990000"})
    assert detail == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Senior-Software-Engineer_JR1990000"
    assert rules.detail_url(token, {"externalPath": "job/x"}) is None and rules.detail_url(token, {"externalPath": "/job/../x"}) is None
    assert rules.detail_url(token, {}) is None
    assert providers.spec("workday").rows is ats_feeds.workday_rows  # packet 2: the real parser, no stand-in
    # The other sites go through the same rules: liveness has no job URL from an id, the catalog builds the board page.
    assert board_job_url("workday", token, "JR1990000") is None
    record = parse_company_record({"name": "NVIDIA", "ats": "workday", "board_slug": token})
    assert record is not None and record.board_url == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    assert parse_company_record({"name": "NVIDIA", "ats": "workday", "board_slug": "nvidia"}) is None
    entry = watchlist_entry_from_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Senior-Software-Engineer_JR1990000")
    assert entry.watchlist_id == f"scout_watchlist:workday:{token}" and entry.provider is ATSProvider.WORKDAY


# --- dates ----------------------------------------------------------------------------------------------------


def test_published_at_from_text_accepts_each_spelling() -> None:
    assert ats_feeds.published_at_from_text("2026-05-29") == "2026-05-29T00:00:00Z"
    assert ats_feeds.published_at_from_text("2026-10-05 15:45:18 UTC") == "2026-10-05T15:45:18Z"
    assert ats_feeds.published_at_from_text("2025-09-09T07:20:34.454Z") == "2025-09-09T07:20:34.454Z"
    assert ats_feeds.published_at_from_text("2026-10-08T10:00:00+02:00") == "2026-10-08T10:00:00+02:00"
    assert ats_feeds.published_at_from_text("2026-10-08T10:00:00") is None  # naive: not a date
    assert ats_feeds.published_at_from_text("2026-13-40") is None
    assert ats_feeds.published_at_from_text(None) is None


def test_published_at_from_posted_on() -> None:
    today = date(2026, 10, 28)
    assert ats_feeds.published_at_from_posted_on("Posted Today", today) == "2026-10-28T00:00:00Z"
    assert ats_feeds.published_at_from_posted_on("Posted Yesterday", today) == "2026-10-27T00:00:00Z"
    assert ats_feeds.published_at_from_posted_on("Posted 4 Days Ago", today) == "2026-10-24T00:00:00Z"
    assert ats_feeds.published_at_from_posted_on("Posted 1 Day Ago", today) == "2026-10-27T00:00:00Z"
    assert ats_feeds.published_at_from_posted_on("posted 30 days ago", today) == "2026-09-28T00:00:00Z"
    assert ats_feeds.published_at_from_posted_on("Today", today) == "2026-10-28T00:00:00Z"  # the leading word is optional
    assert ats_feeds.published_at_from_posted_on("Posted 30+ Days Ago", today) is None  # Workday's cap: undated until the detail
    assert ats_feeds.published_at_from_posted_on("", today) is None
    assert ats_feeds.published_at_from_posted_on(None, today) is None  # missing on every row of some tenants
    assert ats_feeds.published_at_from_posted_on("2026-10-20", today) is None  # not the relative spelling
    assert ats_feeds.published_at_from_posted_on("Posted Soon", today) is None


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


WORKDAY_TOKEN = "acme.wd5.AcmeCareers"
WORKDAY_LIST = {
    "total": 5,
    "jobPostings": [
        {
            "title": "Senior Software Engineer, Platform",
            "externalPath": "/job/US-CA-Santa-Clara/Senior-Software-Engineer--Platform_R-290013",
            "locationsText": "Santa Clara, CA",
            "postedOn": "Posted 4 Days Ago",
            "bulletFields": ["R-290013"],
            "timeType": "Full time",
            "remoteType": "Hybrid",
        },
        {
            "title": "Software Engineer, Backend",
            "externalPath": "/job/US-TX-Austin/Software-Engineer--Backend_JR1990000",
            "locationsText": "3 Locations",
            "postedOn": "Posted 30+ Days Ago",
            "bulletFields": ["JR1990000"],
        },
        {
            # No ``_<reqid>`` suffix and no ``postedOn`` (the spike saw a tenant with none): the id comes from ``bulletFields``.
            "title": "Software Engineer II",
            "externalPath": "/job/Remote/Software-Engineer-II",
            "locationsText": "Remote",
            "bulletFields": ["R-55", "Full time"],
            "remoteType": "Remote",
        },
        {
            # Neither a suffix nor a plain first bullet: the index keys it by URL.
            "title": "Software Engineer, Tools",
            "externalPath": "/job/US-WA-Seattle/Software-Engineer-Tools",
            "locationsText": "Seattle, WA",
            "postedOn": "Posted Yesterday",
            "bulletFields": ["Full time"],
        },
        {"title": "Staff Accountant", "externalPath": "/job/US-CO-Denver/Staff-Accountant_R-1", "locationsText": "Denver, CO", "postedOn": "Posted Today"},
    ],
    "facets": [],
    "userAuthenticated": False,
}
WORKDAY_DETAIL = {
    "jobPostingInfo": {
        "title": "Senior Software Engineer, Platform",
        "jobDescription": "<p>Build the platform.</p><ul><li>Own the services.</li></ul>",
        "jobReqId": "R-290013",
        "jobPostingId": "Senior-Software-Engineer--Platform_R-290013",
        "location": "Santa Clara, CA",
        "postedOn": "Posted 4 Days Ago",
        "startDate": "2026-10-20",
        "timeType": "Full time",
        "remoteType": "Hybrid",
        "country": {"descriptor": "United States of America", "id": "bc33aa3152ec42d4995f4791a106ed09"},
    },
    "hiringOrganization": {"name": "Acme"},
    "similarJobs": [],
}
WORKDAY_DETAIL_BACKEND = {
    "jobPostingInfo": {
        "title": "Software Engineer, Backend",
        "jobDescription": "<p>Run the backend.</p>",
        "jobReqId": "JR1990000",
        "location": "Austin, TX",
        "additionalLocations": ["Denver, CO", "Remote - US"],
        "startDate": "2026-09-01",
        "country": {"descriptor": "United States of America"},
    }
}
WORKDAY_DETAILS = {
    WORKDAY_LIST["jobPostings"][0]["externalPath"]: WORKDAY_DETAIL,
    WORKDAY_LIST["jobPostings"][1]["externalPath"]: WORKDAY_DETAIL_BACKEND,
}


def test_workday_rows_from_list_and_detail() -> None:
    asked: list[str] = []

    def lookup(path: str):
        asked.append(path)
        return WORKDAY_DETAILS.get(path)

    jobs = WORKDAY_LIST["jobPostings"]
    today = date(2026, 10, 28)
    stats = BoardFetchStats()
    rows = ats_feeds.workday_rows(jobs, WORKDAY_TOKEN, _config(), stats, lookup, today=today)
    assert [row.title for row in rows] == ["Senior Software Engineer, Platform", "Software Engineer, Backend", "Software Engineer II", "Software Engineer, Tools"]
    assert stats.listed == 5 and stats.prefiltered_out == 1
    assert asked == [job["externalPath"] for job in jobs[:4]]  # the lookup is keyed by externalPath, the detail's address
    platform, backend, remote, tools = rows
    assert platform.url == "https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/US-CA-Santa-Clara/Senior-Software-Engineer--Platform_R-290013"
    assert platform.provider is ATSProvider.WORKDAY and platform.board_token == WORKDAY_TOKEN and platform.query_key == f"ats:workday:{WORKDAY_TOKEN}"
    assert platform.published_at == "2026-10-20T00:00:00Z"  # the detail's startDate wins over "Posted 4 Days Ago" (2026-10-24)
    assert platform.text is not None and "Build the platform." in platform.text and "Own the services." in platform.text and "<p>" not in platform.text
    assert platform.content_sha256 is not None
    assert platform.countries == ("US",) and platform.work_mode is WorkMode.HYBRID and platform.location == "Santa Clara, CA"
    assert backend.published_at == "2026-09-01T00:00:00Z"  # "30+" alone is undated; the detail dates it
    assert backend.location == "Austin, TX; Denver, CO; Remote - US"  # "3 Locations" spelled out from the detail
    assert backend.work_mode is None and backend.countries == ("US",)
    assert remote.published_at is None and remote.text is None and remote.countries is None  # no postedOn, no detail
    assert remote.work_mode is WorkMode.REMOTE and remote.location == "Remote"
    assert tools.published_at == "2026-10-27T00:00:00Z" and tools.url.endswith("/AcmeCareers/job/US-WA-Seattle/Software-Engineer-Tools")
    assert [ats_feeds.workday_posting_id(job) for job in jobs] == ["R-290013", "JR1990000", "R-55", None, "R-1"]
    # Without details: the relative date at day precision, "30+" undated, no countries, "N Locations" kept as listed.
    platform_only, backend_only, _remote, _tools = ats_feeds.workday_rows(jobs, WORKDAY_TOKEN, _config(), None, None, today=today)
    assert platform_only.published_at == "2026-10-24T00:00:00Z" and platform_only.countries is None and platform_only.text is None
    assert backend_only.published_at is None and backend_only.location == "3 Locations"
    assert ats_feeds.workday_rows(jobs, "acme", _config()) == ()  # not a Workday token: no public URL can be built
    # The detail readers are the same table the pasted-URL path uses; the Rippling answers are unchanged.
    assert ats_feeds.detail_text("workday", WORKDAY_DETAIL) == platform.text
    assert ats_feeds.detail_title("workday", WORKDAY_DETAIL) == "Senior Software Engineer, Platform"
    assert ats_feeds.detail_text("workday", {"jobPostingInfo": {}}) is None and ats_feeds.detail_title("workday", {}) is None
    assert ats_feeds.detail_text("rippling", RIPPLING_DETAIL) == "About Acme.\nOwn the platform."
    assert ats_feeds.detail_title("rippling", RIPPLING_DETAIL) == "Staff Software Engineer"
    assert ats_feeds.detail_text("breezy", {"description": "x"}) is None and ats_feeds.detail_title("breezy", {"name": "x"}) is None


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
    # Workday (0.1.11.12): the fetcher's merged list body ({"total","jobPostings"}) under the three-part token; the posting
    # id is the requisition id, the URL-keyed fallback only when a row has none; details come through the lookup.
    body = json.dumps(WORKDAY_LIST).encode("utf-8")
    workday = parse_board_body("workday", WORKDAY_TOKEN, body)
    assert set(workday) == {"R-290013", "JR1990000", "R-55", "R-1", "url:https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/US-WA-Seattle/Software-Engineer-Tools"}
    assert workday["JR1990000"].published_at is None and workday["JR1990000"].content_sha256 is None and workday["JR1990000"].countries is None
    assert workday["R-290013"].published_at is not None and workday["R-290013"].updated_at is None  # relative date, no change marker
    detailed = parse_board_body("workday", WORKDAY_TOKEN, body, detail_lookup=lambda path, _marker: WORKDAY_DETAILS.get(path))
    assert detailed["JR1990000"].published_at == "2026-09-01T00:00:00Z" and detailed["JR1990000"].countries == ("US",)
    assert detailed["JR1990000"].content_sha256 is not None and detailed["JR1990000"].location == "Austin, TX; Denver, CO; Remote - US"
    assert detailed["R-55"].content_sha256 is None


def test_workday_details_are_read_from_the_board_cache(tmp_path) -> None:
    """``has_detail`` and ``cached_detail_lookup`` find a Workday detail at ``url_rules.detail_url`` (what packet 3 stores)."""
    from gigai.scout.find_jobs.company_index import cached_detail_lookup, has_detail

    assert has_detail("workday") and has_detail("rippling") and has_detail("greenhouse") and not has_detail("breezy")
    cache = BoardCache(tmp_path / "ats-boards")
    path = WORKDAY_LIST["jobPostings"][0]["externalPath"]
    detail_url = providers.spec("workday").url_rules.detail_url(WORKDAY_TOKEN, {"externalPath": path})
    cache.store("workday", detail_url, body=json.dumps(WORKDAY_DETAIL).encode("utf-8"), etag=None, last_modified=None, marker="Posted 4 Days Ago")
    lookup = cached_detail_lookup(cache, WORKDAY_TOKEN, ats="workday")
    assert lookup(path, None) == WORKDAY_DETAIL
    assert lookup("/job/US/Other_R-2", None) is None


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
