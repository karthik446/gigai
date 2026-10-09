"""0.1.11.3 item 11: a posting that fails the profile's country or title filter is NOT in its list. Synthetic only.

The END outcome is asserted: the rows ``search_postings`` serves for the profile (what the Jobs page and
``gigai scout jobs list`` show), on a fresh home with fixture Lever boards whose ``country`` is null, so the
location text is the only country signal (the shape of a region-wide remote job).

- a remote job open only in Europe ("Europe", "Remote - Europe", "Remote (Germany)", "London / Remote") is not
  listed for a US-only profile; a US remote job is;
- the default country is the US (operator decision): a bare "Remote" / "Anywhere" is listed for a profile whose
  countries hold the US and is NOT listed for a profile without the US;
- a region lists for a profile whose country is in it (Europe for a German profile).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import posting_search, profile_records
from gigai.scout.find_jobs.filters import country_match
from gigai.scout.profile_records import ProfileSearchSettings

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, lever_job
from tests.support.scout_profile_fixtures import uuids

SLUG = "tern-works"


def _job(n: int, title: str, location: str, country: str | None) -> dict[str, object]:
    job = lever_job(SLUG, n, title=title, text=f"{title}, posting {n}. Requirements: Python in production; Kubernetes.")
    job["categories"] = {"location": location}
    job["country"] = country
    return job


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    return build_postings_fixture(tmp_path, monkeypatch, deleted=False)


def _listed(fx: PostingsFixture, profile_id: str) -> set[tuple[str, str]]:
    # The PROFILE's own countries are what these tests are about: the list's US-only switch (0.1.11.8, on by default in
    # this US setup) is off, or a profile set to Germany would list nothing here.
    rows = posting_search.search_postings(fx.home_root, fx.target, profile_ids=[profile_id], now=NOW, limit=200, us_only=False)["postings"]["rows"]  # type: ignore[index]
    return {(row["title"], row["location"]) for row in rows}


def _profile(fx: PostingsFixture, label: str, n: int, *, countries: tuple[str, ...]) -> str:
    resolved = fx.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fx.default_profile_id)
    settings = ProfileSearchSettings(countries=countries, location=None, max_age_days=None, work_mode="any")
    record = profile_records.create_profile(
        resolved, label=label, titles=("staff engineer",), titles_to_avoid=(), queries=("staff engineer",),
        resume_ref=default.resume_ref, search_settings=settings, uuid_factory=uuids(n),
    )
    return record.profile_id


EUROPE_ONLY = ("Europe", "Remote - Europe", "Remote (Germany)", "London / Remote", "Europe (Remote)", "EU")


def test_a_remote_job_open_only_in_europe_is_not_in_a_us_profiles_list(fx: PostingsFixture) -> None:
    jobs = [_job(n, "Staff Engineer", location, None) for n, location in enumerate(EUROPE_ONLY, start=1)]
    jobs.append(_job(20, "Staff Engineer", "Remote - United States", None))
    jobs.append(_job(21, "Staff Engineer", "Utah | Remote", None))
    fx.seed(SLUG, jobs, seen_at=days_ago(1))

    listed = _listed(fx, fx.second_profile_id)  # titles: staff engineer; countries: US (the fixture config)

    assert {location for _title, location in listed} == {"Remote - United States", "Utah | Remote"}


BARE_REMOTE = ("Remote", "Anywhere", "remote")


def test_a_bare_remote_job_is_listed_for_a_us_profile_and_not_for_a_profile_without_the_us(fx: PostingsFixture) -> None:
    german = _profile(fx, "Berlin only", 62, countries=("DE",))
    both = _profile(fx, "Either side", 63, countries=("DE", "US"))
    jobs = [_job(n, "Staff Engineer", location, None) for n, location in enumerate(BARE_REMOTE, start=1)]
    jobs.append(_job(20, "Staff Engineer", "Remote - United States", None))
    jobs.append(_job(21, "Staff Engineer", "Remote - Germany", None))
    fx.seed(SLUG, jobs, seen_at=days_ago(1))

    assert {location for _title, location in _listed(fx, fx.second_profile_id)} == {*BARE_REMOTE, "Remote - United States"}
    assert {location for _title, location in _listed(fx, german)} == {"Remote - Germany"}
    assert {location for _title, location in _listed(fx, both)} == {*BARE_REMOTE, "Remote - United States", "Remote - Germany"}


def test_a_region_lists_for_a_profile_whose_country_is_in_it(fx: PostingsFixture) -> None:
    german = _profile(fx, "Berlin track", 60, countries=("DE",))
    fx.seed(SLUG, [
        _job(1, "Staff Engineer", "Europe", None),
        _job(2, "Staff Engineer", "Remote (Germany)", None),
        _job(3, "Staff Engineer", "Remote - United States", None),
        _job(4, "Staff Engineer", "Remote - North America", None),
    ], seen_at=days_ago(1))

    assert {location for _title, location in _listed(fx, german)} == {"Europe", "Remote (Germany)"}


@pytest.mark.parametrize(
    ("location", "us", "de"),
    [
        ("Europe", False, True),
        ("Remote, Europe", False, True),
        ("Remote Europe", False, True),
        ("European Union", False, True),
        ("Remote - EU", False, True),
        ("UK & Europe", False, True),
        ("Remote (Germany)", False, True),
        ("London / Remote", False, False),
        ("London | Berlin", False, True),
        ("Asia", False, False),
        ("Remote - North America", True, False),
        ("Latin America", False, False),
        # Unchanged: the operator's AMER ruling, a stated country, and no country at all.
        ("AMER", False, False),
        ("EMEA", False, False),
        ("Remote (US)", True, False),
        ("San Francisco, CA (HQ)", True, False),
        # The default country is the US: remote words alone are a US job.
        ("Remote", True, False),
        ("remote", True, False),
        ("Anywhere", True, False),
        ("100% Remote", True, False),
        ("Work from home", True, False),
        # Still ambiguous (kept): no location at all, a place no table knows, a timezone beside Remote.
        ("", None, None),
        ("Remote - EST", None, None),
        ("Springfield Office", None, None),
    ],
)
def test_country_rule_on_region_and_bracketed_locations(location: str, us: bool | None, de: bool | None) -> None:
    assert country_match(location, ("US",)) is us
    assert country_match(location, ("DE",)) is de
