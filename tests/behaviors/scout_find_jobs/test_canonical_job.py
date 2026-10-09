"""0.1.11.8 N1 + N2: the canonical-job rule (``find_jobs/canonical_job.py``) and the three-way place rule (``job_copies``).

Pure functions, no home: every posting and place is made up.

- ``pick_canonical``: a US posting before any other; among those (or all) the EARLIEST posted, an undated one last;
  a tie by the posting id; whatever order the copies were given in;
- ``place_of``: ``us`` / ``other`` / ``unclear`` for the locations the lists meet, and US only hides ``other`` alone;
- ``copy_key``: the description decides, and a posting without one is never merged;
- ``locations_text``: the one line a row of copies shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

import pytest

from gigai.scout.find_jobs import job_copies
from gigai.scout.find_jobs.canonical_job import canonical_order, pick_canonical
from gigai.scout.find_jobs.job_copies import PLACE_OTHER, PLACE_UNCLEAR, PLACE_US, copy_key, fold, in_us, locations_text, place_of, us_only_default


@dataclass(frozen=True)
class Copy:
    posting_id: str
    posted: str | None
    us: bool = False


def test_a_us_posting_is_the_canonical_job_else_the_earliest_posted_then_the_posting_id() -> None:
    estonia = Copy("300", "2026-09-01T00:00:00.000000Z")
    poland = Copy("200", "2026-09-05T00:00:00.000000Z")
    texas = Copy("900", "2026-09-20T00:00:00.000000Z", us=True)
    remote_us = Copy("800", "2026-09-10T00:00:00.000000Z", us=True)
    undated = Copy("100", None)
    undated_too = Copy("050", "")

    # No US posting: the earliest posted.
    assert pick_canonical([poland, estonia]) is estonia
    # A US posting before every other, however late it was posted; among two US ones the earliest.
    assert pick_canonical([estonia, poland, texas]) is texas
    assert pick_canonical([estonia, texas, remote_us, poland]) is remote_us
    # A posting with no date comes after every dated one; two of them tie by the posting id.
    assert pick_canonical([undated, poland]) is poland and pick_canonical([undated, undated_too]) is undated_too
    # The same instant: the posting id decides (as text).
    twin_a, twin_b = Copy("b-2", estonia.posted), Copy("a-10", estonia.posted)
    assert pick_canonical([twin_a, twin_b]) is twin_b
    # One posting is its own canonical job; none is refused.
    assert pick_canonical([undated]) is undated
    with pytest.raises(ValueError):
        pick_canonical([])

    # It depends on the copies alone, never on the order they were given in.
    everyone = [estonia, poland, texas, remote_us, undated, undated_too]
    for order in permutations(everyone):
        assert pick_canonical(list(order)) is remote_us
    assert canonical_order(everyone) == [remote_us, texas, estonia, poland, undated_too, undated]
    assert canonical_order(list(reversed(everyone))) == canonical_order(everyone)


def test_the_rule_reads_whatever_shape_the_caller_has() -> None:
    rows = [
        {"id": "7", "at": "2026-09-03T00:00:00Z", "place": "other"},
        {"id": "5", "at": "2026-09-09T00:00:00Z", "place": "us"},
        {"id": "9", "at": "2026-09-01T00:00:00Z", "place": "unclear"},  # "Remote" alone is not a US posting
    ]
    picked = pick_canonical(rows, us=lambda row: row["place"] == "us", posted=lambda row: row["at"], posting_id=lambda row: row["id"])
    assert picked["id"] == "5"
    no_us = [row for row in rows if row["place"] != "us"]
    assert pick_canonical(no_us, us=lambda row: row["place"] == "us", posted=lambda row: row["at"], posting_id=lambda row: row["id"])["id"] == "9"


PLACES = {
    # clearly in the US: any place of the location in the US is enough
    ("Austin, TX", None): PLACE_US, ("Remote - US", None): PLACE_US, ("Remote - United States", None): PLACE_US,
    ("New York, NY; London, UK", None): PLACE_US, ("North America", None): PLACE_US, ("Remote", ("US",)): PLACE_US,
    ("Berlin or New York", ("DE", "US")): PLACE_US,
    # every place it names is clearly outside the US
    ("Remote Estonia", None): PLACE_OTHER, ("Remote, Poland", None): PLACE_OTHER, ("Berlin", None): PLACE_OTHER,
    ("Munich, Germany", None): PLACE_OTHER, ("London, UK", ("GB",)): PLACE_OTHER, ("Remote", ("DE",)): PLACE_OTHER,
    ("Europe", None): PLACE_OTHER, ("EMEA", None): PLACE_OTHER, ("APAC", None): PLACE_OTHER, ("LATAM", None): PLACE_OTHER,
    ("Remote - Canada", None): PLACE_OTHER,
    # Scout cannot tell: never hidden, labelled
    ("Remote", None): PLACE_UNCLEAR, ("Anywhere", None): PLACE_UNCLEAR, ("100% Remote", None): PLACE_UNCLEAR, ("", None): PLACE_UNCLEAR,
    (None, None): PLACE_UNCLEAR, ("Springfield Campus", None): PLACE_UNCLEAR, ("Remote - EST", None): PLACE_UNCLEAR,
    ("Worldwide", None): PLACE_UNCLEAR, ("Remote - Americas", None): PLACE_UNCLEAR, ("AMER", None): PLACE_UNCLEAR,
    # a structured field that names no country says nothing: the text is read
    ("Remote", ()): PLACE_UNCLEAR, ("Remote - Germany", ()): PLACE_OTHER, ("Denver, CO", ()): PLACE_US,
}


@pytest.mark.parametrize(("location", "structured"), sorted(PLACES, key=repr))
def test_where_a_posting_is_for_us_only(location: str | None, structured: tuple[str, ...] | None) -> None:
    expected = PLACES[(location, structured)]
    assert place_of(location, structured) == expected
    # US only hides a posting only when every place it names is clearly outside the US.
    assert in_us(location, structured) is (expected != PLACE_OTHER)


def test_the_default_of_us_only_and_the_rule_sentence() -> None:
    assert us_only_default(("US",)) and us_only_default(("de", "us")) and not us_only_default(("DE",)) and not us_only_default(()) and not us_only_default(None)
    assert "clearly outside the US" in job_copies.US_ONLY_RULE and '"unclear location"' in job_copies.US_ONLY_RULE
    assert job_copies.UNCLEAR_LABEL == "unclear location"


def test_two_copies_share_the_company_the_title_and_the_description() -> None:
    key = copy_key("ashby:point-example", "Point Example", "Senior MLOps Engineer", "sha256:aaa", False)
    assert key is not None
    # Case, spaces and punctuation of the company and the title aside.
    assert copy_key("ashby:point-example", "POINT  example", "senior  mlops-engineer.", "sha256:aaa", False) == key
    # Another description, another title, another board, a removed copy: another job.
    assert copy_key("ashby:point-example", "Point Example", "Senior MLOps Engineer", "sha256:bbb", False) != key
    assert copy_key("ashby:point-example", "Point Example", "Senior MLOps Engineer II", "sha256:aaa", False) != key
    assert copy_key("lever:point-example", "Point Example", "Senior MLOps Engineer", "sha256:aaa", False) != key
    assert copy_key("ashby:point-example", "Point Example", "Senior MLOps Engineer", "sha256:aaa", True) != key
    # No stored description: never merged.
    assert copy_key("ashby:point-example", "Point Example", "Senior MLOps Engineer", None, False) is None
    assert copy_key("ashby:point-example", "Point Example", "Senior MLOps Engineer", "", False) is None
    # "+" and "#" are part of a title: two languages are two titles.
    assert fold("C++ Engineer") != fold("C# Engineer") != fold("C Engineer") and fold("Sr. MLOps  Engineer") == "sr mlops engineer"


def test_the_line_of_a_rows_locations() -> None:
    countries = ["Estonia", "Lithuania", "Latvia", "Bulgaria", "Romania", "Ukraine", "Poland"]
    assert locations_text([f"Remote {country}" for country in countries]) == "Remote: Estonia, Lithuania, Latvia +4"
    assert locations_text(["Remote - Estonia", "Remote (Poland)", "Remote, Latvia"]) == "Remote: Estonia, Poland, Latvia"
    assert locations_text(["Berlin, Germany", "Munich, Germany"]) == "Berlin, Germany; Munich, Germany"
    assert locations_text(["Remote - United States", "Austin, TX", "Denver, CO", "Boston, MA"]) == "Remote - United States; Austin, TX; Denver, CO +1"
    # One place, however many copies, is said once; no place is no line.
    assert locations_text(["Remote", "remote", " Remote "]) == "Remote" and locations_text([]) == "" and locations_text(["", None]) == ""  # type: ignore[list-item]
    # "Remote" alone beside a named remote place is not a prefix to strip.
    assert locations_text(["Remote", "Remote Poland"]) == "Remote; Remote Poland"
