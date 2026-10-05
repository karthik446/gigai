"""0110-10-14: a posting's date is on its Jobs row and on its job page, said as what it is.

Real server, nothing stubbed, nothing written. The operator's Jobs rows and job page showed title, company, location,
work mode and profiles, and no date. Two dates are stored and they are different facts: the BOARD's date
(`published_at`, what the 7 / 30 days chips judge) and when Scout first stored the posting (`first_seen_at`, what "New
since last check" judges). The page shows the board's date when there is one, with the word for what the board means
by it ("posted", or "updated" for a board that gives only its last change), else "first seen": never one as the other.

On the small home every board is a Lever board, so every row is "posted": its postings went up 12, 9, 6, 4 and 0 days
ago and Scout first saw all of them today, so a page that showed `first_seen` as the posting day would say "today" on
every row. The other two words are pinned in `tests/api_e2e/test_ui_stale_reassess_model.py` (the rule) and
`tests/behaviors/scout_pipeline/test_posting_dates.py` (the server's rows for a Greenhouse board and a board with no date).

Pinned: every row has ONE date, beside company and location; it is the server's `published_at` for that row (not its
`first_seen_at`), worded "posted <how long ago>", with the exact day on hover; the job page's header says the same date
with the exact day beside it, and asks nothing for it when it was opened from the list (the row has it); opened by its
link (a reload), an assessed job's page asks for the job ONCE (`GET /api/jobs?url=`) and says the same date. No write.

MEASURED (14-core laptop, 2026-10-04, three runs): the job page from the list 0.07 to 0.10 s; the reload until the date
is shown 0.25 to 0.4 s.

The correction (0.1.10.11): the date is the day the posting WENT UP for every board kind (a Greenhouse posting's used
to be its last change), and the list can be ordered by it. The second flow pins the "Newest posted" chip: off by
default (the server's own order: the best fit first); on, ONE list request with `sort=newest_posted`, the rows in the
order the server answered, newest posting day first; the address keeps it over a reload; off again, the plain list.
It selects nothing: the count is the whole list's and no "Clear filters" is offered for it. No write. (The job page's
"updated N days ago" beside the posting day needs a board that gives a last change; the small home's Lever boards give
none, so that rule is pinned in `tests/api_e2e/test_ui_posted_date_model.py` and the server's rows in
`tests/behaviors/scout_pipeline/test_posting_dates.py`.)
"""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import unquote

import pytest

from tests.ui import jobs_page
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

OPEN_JOB_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
RELOAD_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
ORDER_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
ORDER_CPU_SECONDS = 1.0  # a chip's read of the list: 0.02 to 0.03 measured for the others
WORDS = {"posted": "posted", "updated": "updated", "first_seen": "first seen"}

ROW_DATES_JS = """() => Array.from(document.querySelectorAll('[data-testid="job-row"]')).map((row) => {
  const dates = Array.from(row.querySelectorAll('[data-role="posted"]'));
  const date = dates[0];
  return {
    href: row.querySelector('[data-action="open-job"]').getAttribute('href'),
    count: dates.length,
    inDetail: !!date && !!date.closest('.posting-detail'),
    kind: date ? date.dataset.kind : null,
    at: date ? date.dataset.at : null,
    text: date ? date.textContent : null,
    title: date ? date.getAttribute('title') : null,
  };
})"""


def expected_date(row: dict) -> tuple[str, str]:
    """(kind, instant) the page must show for a server row: the board's date with its kind, else the first sighting."""

    if row.get("published_at"):
        return ("updated" if row.get("published_kind") == "updated" else "posted"), row["published_at"]
    return "first_seen", row["first_seen_at"]


def ago(instant: str, today: date) -> str:
    """The page's own rule, written again here: whole calendar days in the reader's time zone."""

    days = (today - datetime.fromisoformat(instant.replace("Z", "+00:00")).astimezone().date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago" if days < 60 else f"{days // 30} months ago"


def test_a_jobs_row_and_its_job_page_show_the_postings_date(ui) -> None:
    rows = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    by_job = {row["job_identity"]: row for row in rows}
    # The small home's boards give a posting day, and it is not the day Scout first saw the posting.
    assert all(row["published_kind"] == "posted" for row in rows)
    assert any(row["published_at"][:10] != row["first_seen_at"][:10] for row in rows), "the small home has a posting older than its first sighting"
    before = date.today()

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("listed")
    shown = {unquote(item["href"].removeprefix("#/jobs/")): item for item in ui.page.evaluate(ROW_DATES_JS)}
    days = {before, date.today()}  # a run across midnight may count from either day
    assert sorted(shown) == sorted(by_job)
    for identity, item in shown.items():
        row = by_job[identity]
        kind, instant = expected_date(row)
        assert item["count"] == 1 and item["inDetail"], f"{row['title']}: one date, beside company and location"
        assert (item["kind"], item["at"]) == (kind, instant), f"{row['title']}: the page shows {item['kind']} {item['at']}, the server has {kind} {instant}"
        assert item["at"] != row["first_seen_at"] or kind == "first_seen", "a first sighting shown as the board's date"
        assert item["text"] in {f"{WORDS[kind]} {ago(instant, day)}" for day in days}, item["text"]
        assert item["title"].startswith("Posted ") and "First seen by Scout" in item["title"], item["title"]
    # More than one age is on the page: the dates are the postings' own, not the day of the read.
    assert len({item["text"] for item in shown.values()}) > 1

    # Open an assessed job from the list: the header says the row's date with the exact day, and asks nothing for it.
    job = next(row for row in rows if row["state"] == "matched" and not row["tailored"])
    kind, instant = expected_date(job)
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']", has_text=job["title"]).first.click()
    ui.wait_for_job_page()
    header = ui.page.locator('.job-page .job-sub [data-role="posted"]')
    header.wait_for()
    ui.step("opened")
    assert ui.page.locator(".job-page .job-title").text_content() == job["title"]
    assert header.count() == 1 and (header.get_attribute("data-kind"), header.get_attribute("data-at")) == (kind, instant)
    text = header.text_content() or ""
    assert text.startswith(f"{shown[job['job_identity']]['text']} (") and text.endswith(")"), f"the header says {text!r}, the row said {shown[job['job_identity']]['text']!r}"
    assert (header.get_attribute("title") or "").startswith("Posted ")
    # The assessment's own date is beside it, and is a different thing.
    assessed = ui.page.locator('.job-page .job-sub [data-role="assessed-at"]')
    assert assessed.count() == 1 and assessed.get_attribute("data-at") == job["assessment"]["assessed_at"]
    ui.settle()
    ui.step("job-settled")
    assert ui.requests_between("listed", "job-settled", "/api/jobs") == 0, "the row has the date: the page asked for the job anyway"
    ui.wall_budget("open a job and see its date (small home)", OPEN_JOB_WALL_SECONDS, "listed", "opened")

    # Opened by its link (a reload): the page has no row, so it asks for the job once, and says the same date.
    ui.reload()
    ui.step("reloaded")
    ui.wait_for_job_page()
    header.wait_for()
    ui.step("date-shown")
    assert (header.get_attribute("data-kind"), header.get_attribute("data-at")) == (kind, instant)
    assert (header.text_content() or "").startswith(f"{WORDS[kind]} ")
    ui.settle()
    assert ui.requests_after("reloaded", "/api/jobs") == 1, "a job page opened by its link reads the job once for its date"
    ui.no_more_than_one_in_flight("/api/jobs")
    ui.wall_budget("a job page by its link shows the date (small home)", RELOAD_WALL_SECONDS, "reloaded", "date-shown")

    assert ui.writes_after("start") == []
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def test_newest_posted_orders_the_list_by_the_day_each_posting_went_up(ui) -> None:
    plain = ui.server_json("/api/postings?limit=50")
    total = plain["counts"]["matched"]
    assert plain["filters"]["sort"] == "fit" and 3 < total <= 50, "the small home's postings fit one page"

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("listed")
    chip = tid("order-chip-newest-posted")
    assert (ui.page.locator(chip).text_content() or "").strip() == "Newest posted"
    assert not jobs_page.pressed(ui, chip), "the order is the best fit first unless the chip is on"
    assert ui.job_rows() == total

    truth, page = jobs_page.click_chip(
        ui, chip, "newest-posted", "sort=newest_posted", home="small home", cpu_seconds=ORDER_CPU_SECONDS, wall_seconds=ORDER_WALL_SECONDS,
    )
    assert jobs_page.pressed(ui, chip) and ui.page.url.endswith("#/jobs?sort=newest_posted")
    rows = truth["postings"]["rows"]
    # The server ordered them (the page never sorts): the day each posting went up, the newest first.
    days = [row["published_at"] or row["first_seen_at"] for row in rows]
    assert truth["filters"]["sort"] == "newest_posted" and days == sorted(days, reverse=True) and len(set(days)) > 1
    assert jobs_page.identities(page["rows"]) == jobs_page.identities(rows)
    shown = ui.page.evaluate(ROW_DATES_JS)
    assert [item["at"] for item in shown] == [expected_date(row)[1] for row in rows], "the dates on the page are not in the order the server gave"
    # An order selects nothing: every posting is still listed, and there is no filter to clear.
    assert truth["counts"]["matched"] == total and ui.job_rows() == total
    assert ui.page.locator('[data-action="clear-filters"]').count() == 0

    # The address keeps the order: a reload shows the same list.
    ui.reload()
    ui.wait_for_jobs_list()
    assert jobs_page.pressed(ui, chip)
    assert [item["at"] for item in ui.page.evaluate(ROW_DATES_JS)] == [item["at"] for item in shown]

    # Off again: the plain list, the bare address.
    ui.page.click(chip)
    ui.page.wait_for_function("() => location.hash === '#/jobs'")
    ui.page.wait_for_function(jobs_page.SETTLED_ROWS_JS, arg=total)
    assert not jobs_page.pressed(ui, chip)
    ui.settle()

    assert ui.writes_after("start") == []
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
