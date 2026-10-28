"""0.1.11.12 F5b (gigai-18i): a posting's own text can say a "Remote" label is hybrid.

A board label or a location that reads remote (or says nothing) is read as HYBRID, with the source
``posting_text``, when one sentence of the posting body ties a day count or weekdays to office, on-site, in-person or
hybrid work ("three days a week in our office", "2 days per week on-site", "in the office Tuesdays and Thursdays").
A false hybrid drops a remote role from a remote-only search unseen, so a bare "hybrid", "hybrid cloud", a home office,
7 days, customer sites, travel, an optional visit, a negation, or a fully remote body leave the label as it was; so does
a body that says nothing.

Offline, synthetic: every posting below is invented and only has the SHAPE of a real case.
"""

from __future__ import annotations

import pytest

from gigai.scout.find_jobs.contracts import ATSProvider, FindJobsConfig, PostingRow, SourceKind, SourceToggles, WorkMode, WorkModePreference, normalize_url
from gigai.scout.find_jobs.work_mode import SOURCE_POSTING_TEXT, derive_work_mode, work_mode_fit

HYBRID_BODY = "About the role\nThis role is hybrid, three days a week in our office. You will build the data platform."


@pytest.mark.parametrize(
    ("location", "board", "text"),
    [
        ("Remote", None, HYBRID_BODY),  # the location reads remote
        ("United States", WorkMode.REMOTE, HYBRID_BODY),  # the board's own field says remote
        ("", None, "Team norms: we meet 2 days per week on-site in Austin."),  # the location says nothing
        ("Remote - US", None, "You will be in the office 3 days each week with your team."),
        ("Remote", None, "Expect to be in the office two days a week."),
        ("Remote", None, "We work in person three days a week at our Denver hub."),
        ("Remote", None, "You will be in the office Tuesdays and Thursdays."),  # weekday attendance
        ("Remote", None, "Twice a week in the office, rest from home."),
        ("Remote", None, "Hybrid role - in office Mon/Wed/Fri."),
        ("Remote", None, "This is not fully remote: three days a week in the office."),  # "not" before the veto
    ],
)
def test_office_days_in_the_body_read_a_remote_or_unknown_label_as_hybrid(location: str, board: WorkMode | None, text: str) -> None:
    mode = derive_work_mode(location, board, text)
    assert (mode.mode, mode.source) == ("hybrid", SOURCE_POSTING_TEXT)


@pytest.mark.parametrize(
    "text",
    [
        "We build hybrid cloud infrastructure for banks, from our office tooling to the edge.",
        "You will own hybrid search (BM25 plus vectors) in our office productivity suite.",
        "This role is fully remote, with occasional team offsites.",
        "100% remote. There is no office requirement, though our Toronto office is open 5 days a week.",
        # 0.1.11.12 F5b fix round: a false hybrid drops a remote role unseen, so each of these stays remote.
        "You'll work in a hybrid model from our Toronto office.",  # a bare "hybrid": no day count
        "We offer flexible hybrid or remote work, with offices in NYC and SF.",
        "Our product helps hybrid offices manage desk booking.",
        "You will build software for hybrid work in the office.",
        "We provide a home office stipend and two meeting-free days per week.",
        "Our offices are open 7 days a week if you want to drop in.",
        "Office hours: Monday to Friday, 9-5 ET, 5 days a week.",
        "You will visit customer sites on-site 2 days per week.",
        "Travel to our office 3 days a quarter for planning.",
        "Our support team works in-office 5 days a week in Manila; this role does not.",
        "This role is remote-friendly with 3 days a week in the London office for those nearby.",
        "Our in-office teams in Dublin work hybrid. You will pair with them 2 days a week over video calls from home.",
        "Join a fast-growing team building developer tools.",
        "",
    ],
)
def test_a_body_without_office_days_leaves_the_remote_label(text: str) -> None:
    mode = derive_work_mode("Remote", None, text)
    assert (mode.mode, mode.source) == ("remote", "derived")


def test_no_text_is_the_label_alone_and_an_onsite_label_is_never_turned_hybrid() -> None:
    assert derive_work_mode("Remote", None) == derive_work_mode("Remote", None, None)
    assert derive_work_mode("Remote", None).mode == "remote"
    onsite = derive_work_mode("Austin, TX (On-site)", None, HYBRID_BODY)
    assert (onsite.mode, onsite.source) == ("onsite", "derived")


def _row(location: str, text: str | None) -> PostingRow:
    url = "https://boards.greenhouse.io/docebo/jobs/1"
    return PostingRow(
        url=url,
        normalized_url=normalize_url(url),
        provider=ATSProvider.GREENHOUSE,
        board_token="docebo",
        company="docebo",
        title="Senior Software Engineer",
        location=location,
        published_at="2026-09-20T00:00:00Z",
        content_sha256=None,
        source_kind=SourceKind.ATS,
        query_key="ats:greenhouse:docebo",
        text=text,
    )


def _remote_only() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location=None,
        remote=False,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
        countries=("US",),
        work_mode=WorkModePreference("remote"),
    )


def test_the_fit_reads_the_rows_text_so_a_remote_only_search_drops_the_hybrid_body() -> None:
    fit = work_mode_fit(_row("Remote", HYBRID_BODY), _remote_only())
    assert (fit.mode, fit.source, fit.passes) == ("hybrid", SOURCE_POSTING_TEXT, False)
    assert fit.to_json()["source"] == "posting_text"
    plain = work_mode_fit(_row("Remote", "This role is fully remote."), _remote_only())
    assert (plain.mode, plain.source, plain.passes) == ("remote", "derived", True)
