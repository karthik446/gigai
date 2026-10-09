"""0.1.11.8 N1 + N2: "US only" and the copies of one job as ONE row, on a profile's Jobs list. Synthetic only.

The END outcome is asserted: the rows and counts ``search_postings`` serves (what the Jobs page and
``gigai scout jobs list`` show) and what "Assess these" would assess, on a fresh home with made-up Lever boards.

The postings are ``tests/support/copies_fixtures.py``'s (fourteen postings, seven jobs): one job posted once per
country (seven copies, none in the US), one job in two US cities, and what never merges: the same description under
a title written another way, the same title with another description, the same title at another company, a posting
that says "Remote" alone.

Pinned:

- US only is by the posting's LOCATION, on by default in this US setup and off for a setup without the US; it hides
  only a posting clearly outside the US and says how many; "Remote" alone is LISTED and labelled; a posting named by
  its address is read whatever the switch says;
- the copies are one row BEFORE the page and the counts (a page of 1 is one job; ``matched`` counts jobs), with every
  location, only when the description is the same, never across two companies, two titles or the removed flag;
- the row is ONE canonical job (``canonical_job.pick_canonical``), and any copy with an application makes it applied
  (left out of the list, listed by ``state=applied``);
- "Assess these" by a filter is ONE posting per job, the canonical one;
- the command says both in a line each, and ``--no-us-only --no-collapse`` lists every posting.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
import uuid

from click.testing import CliRunner
import pytest

from gigai.application_events import record_application
from gigai.cli import cli
from gigai.scout import posting_search, postings, profile_records
from gigai.scout.find_jobs import job_copies
from gigai.workpad import committed_read_cache

from tests.support.copies_fixtures import ABROAD, COUNTRIES, JOBS, NOT_ABROAD, ONE_JOB, OTHER, POSTINGS, SLUG, anywhere_profile, board, copy_job, seed_copies
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url

@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    seed_copies(fixture, seen_at=days_ago(1))
    return fixture


@pytest.fixture
def anywhere(fx: PostingsFixture) -> str:
    return anywhere_profile(fx)


def _search(fx: PostingsFixture, profile_id: str, **more: object) -> dict:
    return posting_search.search_postings(fx.home_root, fx.target, profile_ids=[profile_id], now=NOW, limit=200, **more)  # type: ignore[arg-type]


def _rows(response: dict) -> dict[str, dict]:
    return {row["job_identity"]: row for row in response["postings"]["rows"]}


def test_us_only_is_by_the_postings_location_and_on_by_default_in_a_us_setup(fx: PostingsFixture, anywhere: str, monkeypatch: pytest.MonkeyPatch) -> None:
    kept = {job_url(SLUG, n) for n in NOT_ABROAD} | {job_url(OTHER, 1)}
    abroad = {job_url(SLUG, n) for n in ABROAD}
    everything = _search(fx, anywhere, us_only=False, collapse=False)
    assert set(_rows(everything)) == kept | abroad and len(kept | abroad) == POSTINGS and everything["counts"]["us_only_left_out"] == 0
    assert everything["us_only"] == {"on": False, "default": True, "rule": job_copies.US_ONLY_RULE} and everything["filters"]["us_only"] is False

    # The default of this setup (the shared settings' countries hold the US): on. By the location, never the board:
    # point-example keeps its four postings that are not clearly abroad and loses its nine others.
    default = _search(fx, anywhere, collapse=False)
    rows = _rows(default)
    assert set(rows) == kept and default["us_only"]["on"] is True and default["filters"]["us_only"] is True
    assert default["counts"]["us_only_left_out"] == len(ABROAD) == 9 and default["counts"]["matched"] == 5
    assert _rows(_search(fx, anywhere, us_only=True, collapse=False)).keys() == rows.keys()
    assert posting_search.us_only_setting(fx.home_root, fx.target) is True
    # "Remote" alone: nobody says where. It is NOT hidden; its row says so. A US place does not.
    assert rows[job_url(SLUG, 13)]["location"] == "Remote" and rows[job_url(SLUG, 13)]["location_unclear"] is True
    assert [url for url, row in rows.items() if row["location_unclear"]] == [job_url(SLUG, 13)]
    assert all(row["location_unclear"] is False for row in _rows(everything).values() if row["job_identity"] != job_url(SLUG, 13))

    # A posting named by its address is an exact read: the switch does not apply.
    named = posting_search.search_postings(fx.home_root, fx.target, jobs=[job_url(SLUG, 1)], now=NOW, us_only=True)
    assert [row["location"] for row in named["postings"]["rows"]] == ["Remote Estonia"] and named["filters"]["us_only"] is False

    # No profile setting was changed by any of it: the profile still has no country.
    record = next(item for item in profile_records.list_profiles(fx.base.gig.resolved) if item.profile_id == anywhere)
    assert record.search_settings is not None and tuple(record.search_settings.countries or ()) == ()

    # A setup whose countries do not hold the US (or that cannot be read): off unless asked for.
    real = postings._shared_config
    monkeypatch.setattr(postings, "_shared_config", lambda home, target: real(home, target).__class__(**{**_fields(real(home, target)), "countries": ("DE",)}))
    assert posting_search.us_only_setting(fx.home_root, fx.target) is False
    monkeypatch.setattr(postings, "_shared_config", lambda home, target: (_ for _ in ()).throw(posting_search.PostingModelError("config_unavailable", "x")))
    assert posting_search.us_only_setting(fx.home_root, fx.target) is False


def _fields(config: object) -> dict[str, object]:
    from dataclasses import fields

    return {item.name: getattr(config, item.name) for item in fields(config) if item.init}  # type: ignore[arg-type]


def test_the_copies_of_one_job_are_one_row_before_the_page_and_the_counts(fx: PostingsFixture, anywhere: str) -> None:
    # Before: every posting its own row (what 0.1.11.7 listed): the same title once per country.
    each = _search(fx, anywhere, us_only=False, collapse=False)
    assert each["counts"]["matched"] == POSTINGS and [row["title"] for row in each["postings"]["rows"]].count("Staff Engineer") == 9  # 7, another team's, the other board's

    found = _search(fx, anywhere, us_only=False)
    rows = _rows(found)
    assert found["filters"]["collapse"] is True and found["counts"]["matched"] == found["counts"]["shown"] == len(rows) == JOBS
    assert found["counts"]["postings"] == POSTINGS and found["counts"]["by_state"] == {"not_assessed": JOBS}
    # ONE row for the seven countries: its canonical job (no US posting, all posted at one instant: the posting id).
    one = rows[job_url(SLUG, 1)]
    assert one["copies"] == 7 and one["title"] == "Staff Engineer" and one["location"] == "Remote Estonia"
    assert one["locations"] == [f"Remote {country}" for country in COUNTRIES]
    assert one["locations_text"] == "Remote: Estonia, Lithuania, Latvia +4"
    assert [member["job_identity"] for member in one["members"]] == [job_url(SLUG, n) for n in ONE_JOB]
    assert [member["location"] for member in one["members"]][:2] == ["Remote Estonia", "Remote Lithuania"]
    # Two cities of ONE country are one row too, and both are listed.
    two = rows[job_url(SLUG, 8)]
    assert two["copies"] == 2 and two["locations_text"] == "Remote - United States; Austin, TX"
    # Conservative. Never merged: the same description under a title written another way (the digest is of the title
    # as written), the same title with another description (another team), another title, another company.
    for n in (10, 11, 12, 13):
        assert rows[job_url(SLUG, n)]["copies"] == 1, n
    assert rows[job_url(SLUG, 12)]["title"] == "Staff Engineer" and rows[job_url(SLUG, 12)]["locations"] == ["Remote Spain"]
    other = rows[job_url(OTHER, 1)]
    assert other["title"] == "Staff Engineer" and other["copies"] == 1 and other["members"] == [
        {"job_identity": job_url(OTHER, 1), "job_url": job_url(OTHER, 1), "location": "Remote - United States"},
    ]

    # A page is a page of ROWS, and the offset counts rows: seven pages of 1, each another job.
    pages = [
        posting_search.search_postings(fx.home_root, fx.target, profile_ids=[anywhere], now=NOW, us_only=False, limit=1, offset=offset)
        for offset in range(JOBS + 1)
    ]
    assert [len(page["postings"]["rows"]) for page in pages] == [1] * JOBS + [0] and all(page["counts"]["matched"] == JOBS for page in pages)
    assert {page["postings"]["rows"][0]["job_identity"] for page in pages[:JOBS]} == set(rows)

    # With US only (the default here) the copies abroad are not rows and not copies of one.
    default = _search(fx, anywhere)
    assert set(_rows(default)) == {job_url(SLUG, 8), job_url(SLUG, 10), job_url(SLUG, 13), job_url(OTHER, 1)}
    assert default["counts"]["matched"] == 4 and default["counts"]["postings"] == 5 and default["counts"]["us_only_left_out"] == 9

    # A copy its board no longer lists is never merged with the live ones.
    fx.seed(SLUG, board(leave_out=(2,)), seen_at=days_ago(0.5))
    live = _rows(_search(fx, anywhere, us_only=False))[job_url(SLUG, 1)]
    assert live["copies"] == 6 and job_url(SLUG, 2) not in [member["job_identity"] for member in live["members"]]
    gone = _search(fx, anywhere, us_only=False, removed=True)["postings"]["rows"]
    assert [(row["job_identity"], row["copies"]) for row in gone] == [(job_url(SLUG, 2), 1)]


def test_the_row_is_one_canonical_job_a_us_posting_first_then_the_earliest(fx: PostingsFixture, anywhere: str) -> None:
    # The same seven countries, now posted at different times (posting 1 the newest, 7 the earliest), and the same job
    # posted in the US as well (posting 14, the newest of all).
    newest = NOW - timedelta(hours=3)
    us_copy = copy_job(SLUG, 14, "Staff Engineer", "Denver, CO", "US", newest + timedelta(minutes=20))
    fx.seed(SLUG, board(newest=newest), seen_at=days_ago(0.9))
    rows = _rows(_search(fx, anywhere, us_only=False))
    # No US posting: the EARLIEST posted is the job (Poland), and it lists the others, earliest first.
    assert job_url(SLUG, 7) in rows and job_url(SLUG, 1) not in rows
    assert rows[job_url(SLUG, 7)]["copies"] == 7 and rows[job_url(SLUG, 7)]["locations_text"] == "Remote: Poland, Ukraine, Romania +4"
    # Two US cities: the earliest of the two.
    assert rows[job_url(SLUG, 9)]["copies"] == 2 and rows[job_url(SLUG, 9)]["location"] == "Austin, TX"

    fx.seed(SLUG, [*board(newest=newest), us_copy], seen_at=days_ago(0.8))
    rows = _rows(_search(fx, anywhere, us_only=False))
    # A US posting IS the job, however late it was posted; the row lists the other seven locations after it.
    job = rows[job_url(SLUG, 14)]
    assert job["copies"] == 8 and job["location"] == "Denver, CO" and job["locations"][0] == "Denver, CO" and job["location_unclear"] is False
    assert job["members"][0]["job_identity"] == job_url(SLUG, 14) and job_url(SLUG, 7) not in rows
    # So with US only on the job is listed (for its US posting), with only that copy: the others are left out.
    default = _rows(_search(fx, anywhere))
    assert default[job_url(SLUG, 14)]["copies"] == 1 and default[job_url(SLUG, 14)]["locations"] == ["Denver, CO"]


def test_any_copy_with_an_application_makes_the_row_applied(fx: PostingsFixture, anywhere: str) -> None:
    assert job_url(SLUG, 1) in _rows(_search(fx, anywhere, us_only=False))
    with committed_read_cache():
        recorded = record_application(
            resolved=fx.base.gig.resolved,
            data={
                "external_ref": job_url(SLUG, 3), "event_kind": "applied", "occurred_at": "2026-10-02T10:00:00Z", "timezone": "UTC",
                "operation_key": f"copies-{uuid.uuid4()}",
            },
            confirm=True,
        )
    assert recorded["status"] == "recorded"
    # The job is applied to (through its Latvia copy): the row is left out of the list like any applied posting ...
    after = _search(fx, anywhere, us_only=False)
    assert not any(row["job_identity"] in {job_url(SLUG, n) for n in ONE_JOB} for row in after["postings"]["rows"])
    assert after["counts"]["matched"] == JOBS - 1 and after["counts"]["applied"] == 1
    # ... and "Applied" lists it as ONE row: the canonical job, standing for all seven, with the application.
    applied = _search(fx, anywhere, us_only=False, states=["applied"])["postings"]["rows"]
    assert [(row["job_identity"], row["copies"], row["application"]["status"]) for row in applied] == [(job_url(SLUG, 1), 7, "applied")]
    assert applied[0]["application"]["since"] == "2026-10-02T10:00:00Z" and len(applied[0]["locations"]) == 7
    # Every posting its own row: only the copy with the application is applied (what 0.1.11.7 showed: six more to apply to).
    each = _search(fx, anywhere, us_only=False, collapse=False)
    assert each["counts"]["matched"] == POSTINGS - 1 and each["counts"]["applied"] == 1


def test_assess_these_by_a_filter_is_one_posting_per_job(fx: PostingsFixture, anywhere: str) -> None:
    def ask(**more: object) -> dict:
        return posting_search.assess_these(fx.home_root, fx.target, profile_id=anywhere, states=["not_assessed"], include_low_rank=True, now=NOW, **more)  # type: ignore[arg-type]

    wide = ask(us_only=False)
    assert wide["status"] == "ask" and wide["counts"]["selected"] == JOBS, "seven jobs, not fourteen postings"
    assert wide["question"]["to_assess"] == JOBS and wide["question"]["yes"]["api"]["body"]["us_only"] is False
    asked = {row["job_identity"] for row in wide["postings"]["rows"]}
    assert job_url(SLUG, 1) in asked and not asked & {job_url(SLUG, n) for n in ONE_JOB[1:]}, "the canonical posting of the job, and no other copy"
    # The default (US only on in this setup): the four jobs not clearly abroad; the yes does not pin a switch nobody set.
    default = ask()
    assert default["counts"]["selected"] == 4 and "us_only" not in default["question"]["yes"]["api"]["body"]
    # Named postings are assessed as named, whatever the switch: two copies named are two postings asked about.
    named = posting_search.assess_these(fx.home_root, fx.target, jobs=[job_url(SLUG, 1), job_url(SLUG, 2)], profile_id=anywhere, include_low_rank=True, now=NOW)
    assert named["counts"]["selected"] == 2


def test_the_command_says_what_us_only_left_out_and_what_the_rows_stand_for(fx: PostingsFixture, anywhere: str) -> None:
    def run(*args: str) -> str:
        result = CliRunner().invoke(cli, ["scout", "jobs", "list", "--profile", anywhere, *args, "--home", str(fx.home_root), "--target", str(fx.target)])
        assert result.exit_code == 0, result.output
        return result.output

    text = run()
    assert "9 outside the US are left out (US only): --no-us-only" in text
    assert "The 4 rows stand for 5 postings: the same job posted more than once is one row (--no-collapse lists each)." in text
    assert "Staff Engineer, Payments (Remote - United States; Austin, TX; 2 copies) [" in text
    assert "Staff Engineer, Platform (unclear location) [" in text, "a posting nobody places is listed and labelled, not hidden"
    wide = run("--no-us-only")
    assert "left out (US only)" not in wide and "The 7 rows stand for 14 postings" in wide and "unclear location" not in wide
    assert "Staff Engineer (Remote: Estonia, Lithuania, Latvia +4; 7 copies) [" in wide
    every = json.loads(run("--no-us-only", "--no-collapse", "--json"))
    assert every["counts"]["matched"] == POSTINGS and every["filters"]["us_only"] is False and every["filters"]["collapse"] is False
    assert all(row["copies"] == 1 for row in every["postings"]["rows"])
    asked = json.loads(run("--us-only", "--collapse", "--json"))
    assert asked["counts"]["matched"] == 4 and asked["filters"]["us_only"] is True and asked["filters"]["collapse"] is True
