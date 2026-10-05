"""0110-10-11 (UAT findings 9 and 10): what "new" counts, and what a sources update's board numbers count.

Finding 9: "0 new since your last check" after updates that stored 474 new
postings, with unassessed matches whose ``first_seen`` is before the anchor.
CHECKED, and it is the design, not a bug:

- ``first_seen`` is Scout's own first sighting: the time the update that first
  read the posting wrote its company's file. Never the board's date, and a
  later change to the posting keeps it.
- ``scout new`` counts the postings an active profile MATCHES that were first
  stored after the anchor. A sources update's "N new" counts every posting on
  every board, whatever its title.

So an update can store hundreds of new postings and leave nothing new for the
profiles. What was wrong is that nothing said so: the message now does.

Finding 10: the update said "Boards 3859" while status said 10,349 stored
companies. The update asks only the companies that are due; the line now
counts out of all of them and says where the rest are.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gigai.scout import scout_new

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job


def _new(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("peek", True)
    return scout_new.scout_new(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def test_an_update_that_stores_postings_no_profile_matches_leaves_nothing_new_and_the_message_says_what_new_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    scout_new.mark_all_seen(fx.home_root, fx.target, now=days_ago(2))  # the last check
    # The update after it: six postings new to Scout, none with a title a profile searches for.
    titles = ("Office Manager", "Account Executive", "Marketing Manager", "Recruiter", "Sales Engineer", "Paralegal")
    fx.seed("globex", [lever_job("globex", n, title=title) for n, title in enumerate(titles, start=1)], seen_at=days_ago(1))
    assert sum(1 for posting in fx.index.read("lever", "globex").postings.values() if posting.first_seen > "2026-10-02") == 6  # type: ignore[union-attr]

    nothing = _new(fx)

    assert (nothing["status"], nothing["counts"]["new"]) == ("nothing_new", 0)  # type: ignore[index]
    message = str(nothing["message"])
    assert message.startswith("Nothing new since your last check (")
    assert "): no posting that matches your profiles was first stored by Scout after it." in message
    assert "(A sources update counts every new posting on every board, whatever its title.)" in message
    assert message in scout_new.render(nothing)


def test_first_seen_is_scouts_own_sighting_never_the_boards_date_and_a_later_change_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    scout_new.mark_all_seen(fx.home_root, fx.target, now=days_ago(2))
    # The board says it posted this ten days ago: long before the last check. Scout first read it yesterday.
    fx.seed("acme", [lever_job("acme", 1, created=days_ago(10))], seen_at=days_ago(1))

    found = _new(fx)

    assert (found["status"], found["counts"]["new"]) == ("ask", 1)  # type: ignore[index]
    row = found["postings"]["rows"][0]  # type: ignore[index]
    assert row["first_seen_at"] == "2026-10-02T15:00:00.000000Z" and str(row["published_at"]).startswith("2026-09-23")
    assert str(found["message"]).startswith("1 new posting since ")

    # The user looks (the anchor moves); a later update then reads the same posting with a changed description.
    seen = scout_new.scout_new(fx.home_root, fx.target, now=NOW, assess=False)
    assert seen["anchor"]["advances"] is True  # type: ignore[index]
    fx.seed(
        "acme", [lever_job("acme", 1, created=days_ago(10), text="Changed: own the Python inference services. Requirements: Kubernetes.")],
        seen_at=NOW + timedelta(hours=2), watch=False,
    )

    later = _new(fx, now=NOW + timedelta(hours=3))

    # Changed, not new: its first sighting is still yesterday's, before the anchor. This is the unassessed match
    # "with a first_seen before the anchor, fetched in this update" of the finding.
    assert (later["status"], later["counts"]["new"]) == ("nothing_new", 0)  # type: ignore[index]
    kept = next(row for row in later["postings"]["rows"] if row["job_identity"] == job_url("acme", 1))  # type: ignore[index]
    assert kept["first_seen_at"] == "2026-10-02T15:00:00.000000Z" and kept["state"] == "not_assessed"


def test_the_update_lines_count_out_of_every_company_and_say_where_the_rest_are() -> None:
    from gigai.scout.scout_cli import _sources_checked_line, _sources_progress_line

    # The operator's numbers: 3,859 companies were due, 6,490 had been checked within the day; 10,349 are stored.
    boards: dict[str, object] = {
        "total": 3859, "done": 3859, "checked": 3859, "fetched": 96, "cached": 3725, "failed": 38, "skipped": 0,
        "never_checked": 0, "up_to_date": 6490,
    }

    assert _sources_checked_line(boards, 340.2) == (
        "Checked 3,859 of 10,349 companies this run (3725 unchanged, 38 did not answer) in 340s. "
        "The other 6,490 were checked within the last day and were not asked again."
    )
    running = _sources_progress_line({"boards": {**boards, "checked": 1200, "failed": 3}, "postings": {"new": 9, "changed": 4, "removed": 2}})
    assert running == (
        "Boards due this run: 1,200 of 3,859 checked, 3 failed (6,490 more were checked recently and are not asked again): "
        "9 new, 4 changed, 2 removed"
    )
    # Every company was due: nothing to explain.
    assert _sources_checked_line({**boards, "total": 12, "checked": 12, "cached": 12, "failed": 0, "up_to_date": 0}, 3.0) == (
        "Checked 12 of 12 companies this run (12 unchanged, 0 did not answer) in 3s."
    )
    assert _sources_progress_line({"boards": {"checked": 2, "total": 12}, "postings": {}}) == (
        "Boards due this run: 2 of 12 checked: 0 new, 0 changed, 0 removed"
    )
