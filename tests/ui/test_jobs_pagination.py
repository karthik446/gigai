"""0110-10-01: the Jobs page has real pages (Prev / Next, page numbers, "Showing 51-100 of N"), and the page
and the chips live in the address.

The demo home holds 13 postings, too few for a second page, so the browser talks to the REAL Scout server for
everything except `GET /api/postings`, which this test answers itself with 130 postings (the first real response
as the template: its profiles, counts and row shape), honouring `limit`, `offset` and the state chip as the real
route does. The Jobs page, the client list store (one request in flight, a page read a moment ago kept) and the
hash router are the real ones. Row 55 is a REAL posting, so its job page opens against the real server.

Pinned: page 2 shows rows 51-100 and the address says `page=2`; opening a job and going Back lands on page 2;
a chip resets to page 1; each page change is exactly ONE request; a bookmark (`#/jobs?page=3`) opens page 3; the page
size is a choice; zero console errors (the `ui` fixture asserts it).

Measured on a laptop (2026-10-03): one `/api/postings` request per page change, every step under 0.2 s wall.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.ui.support import tid

pytestmark = pytest.mark.ui

TOTAL = 130
NEEDS_ANSWERS = 40  # what the "Needs your answers" chip matches in this test's list
REAL_ROW = 54  # zero-based: row 55 is the real posting whose job page opens


class PostingsAnswers:
    """Answers GET /api/postings with TOTAL postings; counts what it was asked."""

    def __init__(self) -> None:
        self.template: dict | None = None
        self.asked: list[str] = []

    def __call__(self, route) -> None:
        url = urlsplit(route.request.url)
        if url.path != "/api/postings":
            route.continue_()
            return
        if self.template is None:
            self.template = route.fetch().json()  # the real server's own answer, once
        query = parse_qs(url.query)
        self.asked.append(url.query)
        limit = int(query.get("limit", ["50"])[0])
        offset = int(query.get("offset", ["0"])[0])
        filtered = "needs_answers" in query.get("state", [])
        matched = NEEDS_ANSWERS if filtered else TOTAL
        real = self.template["postings"]["rows"]
        rows = []
        for index in range(offset, min(offset + limit, matched)):
            base = dict(real[index % len(real)])
            if index != REAL_ROW or filtered:
                base["job_identity"] = f"https://jobs.lever.co/synthetic/{index + 1:04d}"
                base["title"] = f"Role {index + 1:03d}"
            else:
                base["title"] = f"Role {index + 1:03d}"
            rows.append(base)
        body = json.loads(json.dumps(self.template))
        body["counts"].update({"matched": matched, "shown": len(rows), "by_state": {"needs_answers": NEEDS_ANSWERS}})
        body["postings"]["rows"] = rows
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def titles(ui) -> list[str]:
    return ui.page.locator(f"{tid('job-row')} [data-action='open-job']").all_text_contents()


def wait_for_page(ui, page: int, first_title: str) -> None:
    ui.page.wait_for_function(
        """([page, title]) => {
          const pager = document.querySelector('[data-testid="pager"]');
          const first = document.querySelector('[data-testid="job-row"] [data-action="open-job"]');
          return pager && pager.dataset.page === String(page) && first && first.textContent === title
            && document.querySelectorAll('[data-testid="job-row"]').length > 0;
        }""",
        arg=[page, first_title],
    )


def count_line(ui) -> str:
    return (ui.page.locator('[data-role="postings-count"]').first.text_content() or "").split(" · ")[0].strip()


def test_jobs_pages_are_real_pages(ui) -> None:
    answers = PostingsAnswers()
    ui.page.route("**/api/postings*", answers)

    ui.goto("/#/jobs")
    wait_for_page(ui, 1, "Role 001")
    ui.step("page1")
    assert ui.job_rows() == 50
    assert count_line(ui) == "Showing 1-50 of 130 postings"
    assert ui.page.locator(tid("pager-prev")).is_disabled()
    assert ui.page.locator(f"{tid('pager-page')}").all_text_contents() == ["1", "2", "3"]
    # The header counts are totals.
    assert ui.page.locator('.stat-tile:has-text("Postings") .stat-value').first.text_content() == str(TOTAL)

    # Next: page 2 shows rows 51-100, the address says so, and it cost exactly one request.
    ui.page.click(tid("pager-next"))
    wait_for_page(ui, 2, "Role 051")
    ui.step("page2")
    assert ui.requests_after("page1", "/api/postings") == 1
    assert "page=2" in ui.page.url
    assert count_line(ui) == "Showing 51-100 of 130 postings"
    assert titles(ui) == [f"Role {n:03d}" for n in range(51, 101)]
    ui.no_more_than_one_in_flight("/api/postings")
    assert answers.asked[-1] == "limit=50&offset=50"

    # A page number is a page change too.
    ui.page.click(f"{tid('pager-page')}[data-page='3']")
    wait_for_page(ui, 3, "Role 101")
    ui.step("page3")
    assert ui.requests_after("page2", "/api/postings") == 1
    assert count_line(ui) == "Showing 101-130 of 130 postings"
    assert ui.job_rows() == 30
    assert ui.page.locator(tid("pager-next")).is_disabled()
    ui.page.click(tid("pager-prev"))
    wait_for_page(ui, 2, "Role 051")  # page 2 was read a moment ago: no request at all
    ui.step("back-to-2")
    assert ui.requests_after("page3", "/api/postings") == 0

    # Open a job from page 2 and come Back: page 2, the same rows.
    ui.page.locator(tid("job-row")).nth(REAL_ROW - 50).locator("[data-action='open-job']").click()
    ui.page.wait_for_selector(".job-title")
    assert "#/jobs/" in ui.page.url and "page=" not in ui.page.url
    ui.step("job-open")
    ui.page.go_back()
    wait_for_page(ui, 2, "Role 051")
    ui.step("job-back")
    assert "page=2" in ui.page.url
    assert titles(ui) == [f"Role {n:03d}" for n in range(51, 101)]
    assert ui.requests_after("job-open", "/api/postings") <= 1  # at most the one refresh in place, never a re-list

    # A chip resets to page 1 (and the page leaves the address); the header total stays the total.
    ui.page.click('[data-role="state-filter"] [data-state="needs_answers"]')
    wait_for_page(ui, 1, "Role 001")
    ui.step("chip")
    assert ui.requests_after("job-back", "/api/postings") <= 2  # the one refresh above may still be in flight; the chip adds one
    assert "page=" not in ui.page.url and "state=needs_answers" in ui.page.url
    assert count_line(ui).startswith(f"Showing 1-{NEEDS_ANSWERS} of {NEEDS_ANSWERS} posting")
    assert ui.page.locator('.stat-tile:has-text("Postings") .stat-value').first.text_content() == str(TOTAL)
    assert ui.page.locator(tid("pager")).count() == 1 and ui.page.locator(tid("pager-prev")).is_disabled()
    ui.no_more_than_one_in_flight("/api/postings")

    # A bookmark: the address alone opens the page.
    ui.goto(f"/#/jobs?page=3")
    ui.reload()
    wait_for_page(ui, 3, "Role 101")
    assert count_line(ui) == "Showing 101-130 of 130 postings"

    # The page size is a choice; it goes back to page 1.
    ui.page.select_option(tid("pager-size"), "25")
    wait_for_page(ui, 1, "Role 001")
    assert ui.job_rows() == 25
    assert count_line(ui) == "Showing 1-25 of 130 postings"
    assert "size=25" in ui.page.url
    assert answers.asked[-1] == "limit=25"

    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
