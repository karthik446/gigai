"""0.1.11.3 item 12: "Mark applied" shows: an "Applied · <date>" badge on the job page and on the Jobs card, an "Applied" filter, and
the next status (Interview scheduled, ...) changes the badge, with no page reload.

Real server and page (the small home, no model call, no network). The row-level read model (every status in turn, one
read of the events per request) is pinned in tests/behaviors/scout_find_jobs/test_postings_applied_label.py.

This flow CHANGES the shared home (an application event is append-only), so it runs late (`UI_ORDER`).

- a job with no application shows no badge, on its card or its page;
- "Mark applied" on the job page: the header badge reads "Applied · <month day>" at once (the page is not reloaded), and
  the job's assessment state chip is not touched;
- 0.1.11.5 (packet AP): the job is then LEFT OUT of the Jobs list ("Showing 1-N of M" counts one fewer, no card, no
  badge) and the "Applied" chip says how many there are ("Applied 1"); the chip lists that job and no other, with its
  badge and its assessment state untouched;
- "Interview scheduled" on the job page: the badge reads "Interview · <month day>", with no reload; the card (listed
  by the "Applied" chip) follows, and the job stays out of the list without the chip.
"""

from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import quote

import pytest

from tests.ui.jobs_page import LIST, count_line, identities, shown

pytestmark = pytest.mark.ui

UI_ORDER = 85  # changes the shared home (one application event): after the old-assessment flow, before the profile's delete

ROW = '[data-testid="job-row"]'
BADGE = '[data-testid="application-badge"]'
PAGE_BADGE = '.job-page [data-role="application-badge"]'
MARK = "window.__applied_badge_marker"
APPLIED_CHIP = '[data-role="state-filter"] [data-state="applied"]'
CHIP_COUNT = f'{APPLIED_CHIP} [data-role="chip-count"]'


def _day(since: str) -> str:
    return datetime.fromisoformat(since.replace("Z", "+00:00")).strftime("%b ") + str(datetime.fromisoformat(since.replace("Z", "+00:00")).day)


def _card(ui, job: str):
    return ui.page.locator(f"{ROW}:has(a[href='#/jobs/{quote(job, safe='')}'])")


def _application(ui, job: str) -> dict | None:
    rows = ui.server_json(f"{LIST}?limit=200&state=applied")["postings"]["rows"]  # 0.1.11.5: only this filter lists it
    return next(row["application"] for row in rows if row["job_identity"] == job)


def test_mark_applied_shows_a_badge_on_the_page_and_the_card_and_filters(ui, scout_server) -> None:
    rows = ui.server_json(f"{LIST}?limit=200")["postings"]["rows"]
    job = next(row["job_identity"] for row in rows if row["job_identity"] != scout_server.demo.hero_job and row["application"] is None)
    assert all(row["application"] is None for row in rows), "nothing is applied to in a fresh home"

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert ui.page.locator(BADGE).count() == 0
    state_before = _card(ui, job).get_attribute("data-state")
    matched_before = ui.server_json(f"{LIST}?limit=50")["counts"]["matched"]
    assert count_line(ui).endswith(f"of {matched_before} postings")
    assert ui.page.locator(APPLIED_CHIP).inner_text().strip() == "Applied" and ui.page.locator(CHIP_COUNT).count() == 0, "none applied: no number"

    ui.goto("/#/jobs/" + quote(job, safe=""))
    ui.wait_for_job_page()
    ui.settle()
    assert ui.page.locator(PAGE_BADGE).count() == 0
    ui.page.evaluate(f"{MARK} = true")
    with ui.page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/applications")) as posted:
        ui.page.click(f'.job-page [data-event="applied"]')
    assert posted.value.ok
    ui.page.locator(PAGE_BADGE).wait_for()
    badge = ui.page.locator(PAGE_BADGE)
    assert re.fullmatch(r"Applied · [A-Z][a-z]{2} \d{1,2}", badge.inner_text().strip()), badge.inner_text()
    since = _application(ui, job)["since"]
    assert badge.inner_text().strip() == f"Applied · {_day(since)}"
    assert badge.get_attribute("data-status") == "applied"
    assert ui.page.evaluate(MARK) is True, "the page was not reloaded"

    # 0.1.11.5: the Jobs list leaves the job out (the list, its count), and the chip says there is one.
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.page.wait_for_function("() => (document.querySelector('[data-role=\"postings-count\"]') || {dataset: {}}).dataset.refreshing !== 'true'")
    counts = ui.server_json(f"{LIST}?limit=50")["counts"]
    assert (counts["matched"], counts["applied"]) == (matched_before - 1, 1)
    assert count_line(ui).endswith(f"of {matched_before - 1} postings"), count_line(ui)
    assert job not in identities(shown(ui)["rows"]) and _card(ui, job).count() == 0, "an applied job is not in the default list"
    assert ui.page.locator(BADGE).count() == 0
    assert ui.page.locator(CHIP_COUNT).inner_text().strip() == "1" and ui.page.locator(APPLIED_CHIP).inner_text().split() == ["Applied", "1"]
    # The chip lists it, and only it: the badge, and the assessment state untouched.
    with ui.page.expect_response(lambda r: r.url.split("?")[0].endswith(LIST) and "state=applied" in r.url):
        ui.page.click(APPLIED_CHIP)
    ui.page.wait_for_function("() => document.querySelectorAll('[data-testid=\"job-row\"]').length === 1")
    assert ui.page.locator(f"{ROW} [data-action='open-job']").get_attribute("href") == "#/jobs/" + quote(job, safe="")
    card = _card(ui, job)
    assert card.locator(BADGE).inner_text().strip() == f"Applied · {_day(since)}"
    assert card.get_attribute("data-state") == state_before, "a label only: the card's assessment state is unchanged"
    assert ui.page.locator(BADGE).count() == 1
    assert count_line(ui) == "Showing 1 of 1 posting" and ui.page.locator(CHIP_COUNT).inner_text().strip() == "1"

    # A later status: the badge on the job page follows, with no reload; the card follows too.
    ui.goto("/#/jobs/" + quote(job, safe=""))
    ui.wait_for_job_page()
    ui.settle()
    assert ui.page.locator(PAGE_BADGE).inner_text().startswith("Applied · ")
    ui.page.evaluate(f"{MARK} = true")
    with ui.page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/applications")):
        ui.page.click('.job-page [data-event="interview_scheduled"]')
    ui.page.wait_for_function("() => (document.querySelector('.job-page [data-role=\"application-badge\"]') || {}).dataset?.status === 'interview_scheduled'")
    later = _application(ui, job)
    assert later["status"] == "interview_scheduled"
    assert ui.page.locator(PAGE_BADGE).inner_text().strip() == f"Interview · {_day(later['since'])}"
    assert ui.page.evaluate(MARK) is True, "the page was not reloaded"
    assert ui.page.locator('.job-page [data-event="applied"]').count() == 0, "the applied button is gone once applied"

    ui.goto("/#/jobs?state=applied")
    ui.wait_for_jobs_list()
    ui.settle()
    # The kept rows show first and are read again in place: wait for that read, never a reload.
    ui.page.wait_for_function("() => (document.querySelector('[data-role=\"postings-count\"]') || {dataset: {}}).dataset.refreshing !== 'true'")
    assert _card(ui, job).locator(BADGE).inner_text().strip() == f"Interview · {_day(later['since'])}"
    # A later status is still an application: the list without the chip leaves it out.
    with ui.page.expect_response(lambda r: r.url.split("?")[0].endswith(LIST) and "state=applied" not in r.url):
        ui.page.click(APPLIED_CHIP)
    ui.page.wait_for_function("(job) => !document.querySelector(`[data-testid=\"job-row\"] a[href='#/jobs/${job}']`)", arg=quote(job, safe=""))
    ui.page.wait_for_function("() => (document.querySelector('[data-role=\"postings-count\"]') || {dataset: {}}).dataset.refreshing !== 'true'")
    assert job not in identities(shown(ui)["rows"]) and ui.page.locator(CHIP_COUNT).inner_text().strip() == "1"
    ui.assert_clean()
