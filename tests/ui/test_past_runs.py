"""Flow 9 (REPORT.md 5.3): Past runs. History, read-only.

Since 0.1.10.7 the UI starts no run: Jobs lists the stored postings directly, and `#/runs` is what is left of the
runs, as history. The small home has never had a run, so the first test is the real page on the real server in its
empty state. The second test shows the table: `GET /api/runs?profile_id=...` is answered by the test with two runs
(the page, the router and the top bar are the real ones; the rest of the server is real), and no run page is opened,
since those runs do not exist on the server. The same test answers the app's FIRST read of the runs (made before it
knows the profile, so for every profile) AFTER the profile's own: found by this flow under load, the late answer
used to replace the profile's runs (`hooks.useRuns` kept whichever answer came last).

Pinned: the top bar's "Past runs" link opens the page; it says it is history and read-only and names the profile;
with no run it says so; with runs it lists them newest first with their counts and status, each linking to its run
page; an answer to an earlier read that arrives late does not replace them; and looking at the page starts nothing
(no request that is not a GET).

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): 0.07 s from the click to the page
(the runs were read when the app started), no request at all.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.ui.support import INTERACTIVE_WALL_SECONDS

pytestmark = pytest.mark.ui

RUNS_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
RUNS = [
    {"run_id": "run_00000000-0000-4000-8000-000000000002", "created_at": "2026-09-30T09:00:00.000000Z", "status": "succeeded", "counts": {"found": 41, "new": 7, "assessed": 5, "matched": 3}},
    {"run_id": "run_00000000-0000-4000-8000-000000000001", "created_at": "2026-09-23T09:00:00.000000Z", "status": "failed", "counts": {"found": 12, "new": 12, "assessed": 0, "matched": 0}},
]


def open_from_the_top_bar(ui) -> None:
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("jobs")
    ui.page.click('a[href="#/runs"] >> nth=0')
    ui.page.locator('[data-role="history-tag"]').wait_for()
    ui.step("runs")


def test_past_runs_is_history_and_starts_nothing(ui) -> None:
    profiles = ui.server_json("/api/profiles")
    selected = next(item for item in profiles["profiles"] if item["profile_id"] == profiles["selected_profile_id"])
    assert ui.server_json(f"/api/runs?profile_id={selected['profile_id']}")["runs"] == [], "the small home has never had a run"

    open_from_the_top_bar(ui)
    assert ui.page.url.endswith("#/runs") and ui.page.title() == "Scout · Past runs"
    heading = ui.page.locator("main h2").first.text_content() or ""
    assert "Past runs" in heading and "History" in heading and selected["label"] in heading
    note = ui.page.locator('[data-role="runs-history-note"]').text_content() or ""
    assert "Read-only" in note and "no new run is started here" in note
    assert ui.page.locator("main table.runs-table").count() == 0
    assert "No past runs for this profile." in (ui.page.locator("main").text_content() or "")
    # Nothing on the page starts a run: no button at all, and its one link goes back to Jobs.
    section = ui.page.locator("main section.panel").first
    assert section.locator("button").count() == 0
    assert section.locator("a").evaluate_all("(links) => links.map((link) => link.getAttribute('href'))") == ["#/jobs"]
    ui.settle()
    assert ui.writes_after("start") == [], "looking at Past runs wrote something"
    ui.wall_budget("open Past runs (small home)", RUNS_WALL_SECONDS, "jobs", "runs")
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def test_past_runs_lists_the_selected_profiles_runs_whatever_order_the_answers_come_in(ui) -> None:
    # The app reads the runs twice when it starts: once before it knows the profile (every profile's runs) and once
    # for the selected profile. The first read is held here and answered LAST: the page must keep the profile's runs.
    held: list = []

    def answer(route) -> None:
        url = urlsplit(route.request.url)
        query = parse_qs(url.query)
        if url.path != "/api/runs" or "status" in query:
            route.continue_()  # only the two history lists are ours; the rest is the real server's
        elif "profile_id" not in query:
            held.append(route)
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"schema_version": "scout-runs-list-response:1", "runs": RUNS}))

    ui.page.route("**/api/runs*", answer)
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.page.click('a[href="#/runs"] >> nth=0')
    ui.page.locator('[data-role="history-tag"]').wait_for()
    rows = ui.page.locator("main table.runs-table tbody tr")
    rows.first.wait_for()

    def listed() -> list[list[str]]:
        return rows.evaluate_all("(rows) => rows.map((row) => Array.from(row.querySelectorAll('td')).slice(1).map((cell) => cell.textContent))")

    assert listed() == [["41", "7", "5", "3", "succeeded"], ["12", "12", "0", "0", "failed"]]
    links = rows.locator("td a").evaluate_all("(links) => links.map((link) => [link.getAttribute('href'), link.getAttribute('title')])")
    assert links == [[f"#/runs/{run['run_id']}", run["run_id"]] for run in RUNS]
    assert ui.page.locator("main section.panel").first.locator("button").count() == 0

    # Now the first read comes back (the real server: no run at all). It is stale: the page keeps what it shows.
    assert len(held) == 1, "the app's first, unfiltered read of the runs was expected"
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == "/api/runs" and not urlsplit(response.url).query):
        held.pop().continue_()
    ui.settle()
    assert listed() == [["41", "7", "5", "3", "succeeded"], ["12", "12", "0", "0", "failed"]], "a late answer to an earlier read replaced the selected profile's runs"
    assert ui.writes_after("start") == []
    ui.assert_clean()
