"""Flow 3 (REPORT.md 5.3) on the small home: the Jobs chips. Profile, New / 7 days / 30 days, state.

Real server, nothing stubbed. What each chip must list is the server's own answer to the page, checked against the
same query asked of the server directly (as an agent would), so the test holds whatever the flows before it did to
the home (`jobs_page.click_chip`).

Pinned: a chip is ONE list request with that chip's query, and the rows are what the server lists for it; the "When"
chips are one at a time; the address carries the filter (and a bookmark of it opens the same list); the header's
"Postings" total stays the unfiltered total; the profile chip is remembered in this browser and every row it lists
carries that profile's tag; a state chip lists only rows in that state; "Clear filters" brings the whole list back;
never two list requests in flight; zero console errors.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): every chip 0.04 to 0.10 s wall, one
request, 0.02 to 0.03 server CPU seconds.
"""

from __future__ import annotations

import pytest

from tests.ui import jobs_page
from tests.ui.jobs_page import LIST, pressed
from tests.ui.support import INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

CHIP_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
CHIP_CPU_SECONDS = 1.0  # 0.02 to 0.03 measured


def click_chip(ui, selector: str, step: str, query: str) -> tuple[dict, dict]:
    return jobs_page.click_chip(ui, selector, step, query, home="small home", cpu_seconds=CHIP_CPU_SECONDS, wall_seconds=CHIP_WALL_SECONDS)


def wait_for_the_whole_list(ui, total: int) -> None:
    """No filter is on any more: every row is listed and the address is the bare `#/jobs`."""

    ui.page.wait_for_function(
        """(count) => document.querySelectorAll('[data-testid="job-row"]').length === count && location.hash === '#/jobs'""",
        arg=total,
    )


def postings_tile(ui) -> str | None:
    return ui.page.locator('.stat-tile:has-text("Postings") .stat-value').first.text_content()


def test_the_chips_filter_the_list_one_request_each(ui) -> None:
    everything = ui.server_json(f"{LIST}?limit=50")
    total = everything["counts"]["matched"]
    profiles = everything["profiles"]
    assert total > 3 and len(profiles) >= 2, "the small home has postings and two profiles"

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("loaded")
    assert ui.job_rows() == total and postings_tile(ui) == str(total)

    # When: "New since last check (N)", then 7 days, then 30 days: one at a time.
    new_chip, week_chip, month_chip = tid("time-chip-new"), tid("time-chip-7d"), tid("time-chip-30d")
    truth, page = click_chip(ui, new_chip, "new", "window=new")
    assert truth["counts"]["matched"] > 0, "the small home has postings that are new since the last check"
    assert (ui.page.locator(new_chip).text_content() or "").strip() == f"New since last check ({truth['counts']['matched']})"
    assert pressed(ui, new_chip) and "window=new" in ui.page.url
    assert all(row["isNew"] for row in page["rows"]), "a row listed as new does not carry the New tag"
    assert postings_tile(ui) == str(total), "the header total changed with a filter on"

    truth, page = click_chip(ui, week_chip, "7d", "window=7d")
    assert pressed(ui, week_chip) and not pressed(ui, new_chip) and "window=7d" in ui.page.url
    truth, page = click_chip(ui, month_chip, "30d", "window=30d")
    assert pressed(ui, month_chip) and not pressed(ui, week_chip) and "window=30d" in ui.page.url

    # A bookmark of the chip opens the same list.
    ui.reload()
    ui.wait_for_jobs_list()
    assert pressed(ui, month_chip) and ui.job_rows() == truth["counts"]["shown"]

    # Off again: the whole list, and the address is bare.
    ui.page.click(month_chip)
    wait_for_the_whole_list(ui, total)
    assert not pressed(ui, month_chip)
    ui.settle()

    # Profile: a filter, remembered in this browser; every row carries that profile's tag.
    second = profiles[1]
    chip = ui.page.locator('[data-role="profile-filter-chip"]').nth(1)
    assert (chip.text_content() or "").split() == [*second["label"].split(), str(second["matched"])]
    truth, page = click_chip(ui, '[data-role="profile-filter-chip"] >> nth=1', "profile", f"profile_id={second['profile_id']}")
    assert truth["counts"]["matched"] == second["matched"] and len(page["rows"]) == min(50, second["matched"])
    assert chip.get_attribute("aria-pressed") == "true" and f"profile={second['profile_id']}" in ui.page.url
    assert all(second["label"] in row["profiles"] for row in page["rows"]), "a row listed for the profile does not carry its tag"
    assert second["profile_id"] in (ui.page.evaluate("() => window.localStorage.getItem('scout.jobs.profileFilter')") or "")

    # State, with the profile still on: both filters are in the one request.
    needs = '[data-role="state-filter"] [data-state="needs_answers"]'
    truth, page = click_chip(ui, needs, "profile+needs_answers", f"profile_id={second['profile_id']}&state=needs_answers")
    assert {row["state"] for row in page["rows"]} <= {"needs_answers"}

    # Clear filters: everything is back, the address is bare, the profile is forgotten.
    ui.page.click('[data-action="clear-filters"]')
    wait_for_the_whole_list(ui, total)
    assert ui.page.evaluate("() => window.localStorage.getItem('scout.jobs.profileFilter')") == "[]"
    assert not pressed(ui, needs) and chip.get_attribute("aria-pressed") == "false"
    ui.settle()

    # State chips on their own: needs your answers, assessed, Scout label recommended.
    truth, page = click_chip(ui, needs, "needs_answers", "state=needs_answers")
    assert {row["state"] for row in page["rows"]} <= {"needs_answers"} and "state=needs_answers" in ui.page.url
    assert jobs_page.count_line(ui).startswith(f"Showing 1-{len(page['rows'])} of {truth['counts']['matched']} posting") or not page["rows"]
    ui.page.click(needs)
    wait_for_the_whole_list(ui, total)
    ui.settle()

    recommended = '[data-role="state-filter"] [data-state="recommended"]'
    truth, page = click_chip(ui, recommended, "recommended", "state=recommended")
    assert truth["counts"]["matched"] > 0, "the small home has a job with the Scout label"
    assert all(row["label"] for row in page["rows"]), "a row listed as recommended shows no Scout label chip"

    assessed = '[data-role="state-filter"] [data-state="assessed"]'
    truth, page = click_chip(ui, assessed, "recommended+assessed", "state=recommended&state=assessed")
    assert pressed(ui, assessed) and pressed(ui, recommended)
    assert "not_assessed" not in {row["state"] for row in page["rows"]}

    ui.no_more_than_one_in_flight(LIST)
    ui.no_more_than_one_in_flight("/api/new")
    assert ui.writes_after("start") == [], "a chip is a read: nothing may be written"
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
