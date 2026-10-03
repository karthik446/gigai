"""0110-8-10: a city that shares a state's name is a city. Pure function, public strings only.

"New York, NY" derived ``unknown`` (the piece "new york" is a state name and
was skipped), and ``unknown`` passes every work-mode filter, so on-site New
York postings passed a Remote-only search and were assessed.
"""

from __future__ import annotations

import pytest

from gigai.scout.find_jobs.contracts import PostingRow
from gigai.scout.find_jobs.work_mode import derive_work_mode, in_area, parse_area, work_mode_fit

from tests.behaviors.scout_find_jobs.test_work_mode_filter import _config, _row as _posting  # the suite's own builders


@pytest.mark.parametrize(
    "location",
    [
        "New York, NY",
        "Washington, DC",
        "New York, New York",
        "New York, New York, USA",  # the Datadog case
        "Washington, District of Columbia, United States",
        "New York, NY, United States",
        "Delaware, OH",
    ],
)
def test_a_state_name_followed_by_a_state_is_a_city(location: str) -> None:
    found = derive_work_mode(location)
    assert (found.mode, found.source) == ("in_person", "derived"), location


@pytest.mark.parametrize("location", ["New York", "New York, United States", "Washington", "California", "NY", "New York, California"])
def test_a_bare_state_name_stays_unknown(location: str) -> None:
    assert derive_work_mode(location).mode == "unknown", location


@pytest.mark.parametrize("location", ["Remote - New York", "New York, NY (Remote)", "Remote; New York, NY"])
def test_a_stated_mode_still_wins(location: str) -> None:
    assert derive_work_mode(location).mode == "remote", location


def _row(location: str) -> PostingRow:
    return _posting("acme", location)


@pytest.mark.parametrize("location", ["New York, NY", "Washington, DC", "New York, New York, USA"])
def test_a_remote_only_search_drops_them(location: str) -> None:
    fit = work_mode_fit(_row(location), _config("remote"))
    assert (fit.mode, fit.passes) == ("in_person", False), location


def test_new_york_alone_is_kept_and_labelled_unknown() -> None:
    fit = work_mode_fit(_row("New York"), _config("remote"))
    assert (fit.mode, fit.passes) == ("unknown", True)


def test_the_city_is_not_read_as_the_state_of_the_same_name() -> None:
    # "Washington, DC" is not in the state of Washington; "New York, NY" is in New York state.
    assert in_area("Washington, DC", parse_area("Washington")) is False  # type: ignore[arg-type]
    assert in_area("New York, NY", parse_area("Albany, NY")) is True  # type: ignore[arg-type]
