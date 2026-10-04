"""Flow 2 (REPORT.md 5.3) on the small home: open a job, come Back, and the list is still there.

0110-9-01's addendum was "every navigation back re-requests the whole list". On the operator-sized home that is
`test_operator_sized_jobs.py`; this is the same flow on every PR, where it costs a second: the rows that were read
are shown at once on Back (the count line never says "Loading" again), they are the same rows, and Back costs at
most one list request (the refresh in place), never a re-list. The job page is waited for in full (title, state line,
pipeline timeline: `wait_for_job_page`), since "a heading exists" passed in the spike with every request still open.

Both ways back are checked: the browser's Back and the page's own "← Jobs" link.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): job page 0.09 to 0.10 s wall, 8
requests, none for the list; Back 0.02 s wall, 0 list requests, 3 requests in all, 0.04 server CPU seconds; the
"Jobs" link 0.06 to 0.07 s.
"""

from __future__ import annotations

import pytest

from tests.ui.support import INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

WAITING = ("Loading", "Still loading", "Preparing")
JOB_PAGE_REQUESTS = 16  # 8 measured (config, answers, the pipeline job, the tailored resume, the assessments)
BACK_REQUESTS = 8  # 3 measured (the sources status and the assessments); none of them the list
BACK_LIST_REQUESTS = 1  # at most the refresh in place; 0 measured (the list is under 30 s old)
OPEN_JOB_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
BACK_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
BACK_CPU_SECONDS = 1.0  # 0.04 measured; the background pipeline thread shares the process


def test_open_a_job_and_back_the_list_is_still_there(ui) -> None:
    ui.watch_count_line()
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("loaded")
    titles = ui.job_titles()
    assert len(titles) > 1, "the small home lists more than one posting"

    # Open the second row (the first is the hero job, which has the most on its page).
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']").nth(1).click()
    ui.wait_for_job_page()
    ui.step("opened")
    assert "#/jobs/" in ui.page.url
    assert ui.page.locator(".job-page .job-title").text_content() == titles[1]
    ui.settle()
    ui.step("job-settled")
    # The job page asks for its own things; the list is not read again for it.
    assert ui.requests_between("loaded", "job-settled", "/api/postings") == 0
    assert ui.requests_between("loaded", "job-settled") <= JOB_PAGE_REQUESTS
    ui.wall_budget("open a job (small home)", OPEN_JOB_WALL_SECONDS, "loaded", "opened")

    # Back: the same rows, at once.
    seen = len(ui.count_lines())
    ui.page.go_back()
    ui.wait_for_jobs_list()
    ui.step("back")
    assert ui.job_titles() == titles, "back from the job: the rows are not the ones that were read"
    said = [line for line in ui.count_lines()[seen:] if line]
    assert not [line for line in said if line.startswith(WAITING)], f"back from the job: the list was loading again; the count line said {said}"
    ui.settle()
    ui.step("back-settled")
    assert ui.requests_between("job-settled", "back-settled", "/api/postings") <= BACK_LIST_REQUESTS
    assert ui.requests_between("job-settled", "back-settled") <= BACK_REQUESTS
    ui.cpu_budget("Back to Jobs (small home)", BACK_CPU_SECONDS, "job-settled", "back-settled")
    ui.wall_budget("Back to Jobs (small home)", BACK_WALL_SECONDS, "job-settled", "back")

    # The page's own "← Jobs" link is the same way back.
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']").nth(1).click()
    ui.wait_for_job_page()
    ui.settle()
    ui.step("opened-again")
    seen = len(ui.count_lines())
    ui.page.click(".job-page a.back-link")
    ui.wait_for_jobs_list()
    ui.step("link-back")
    assert ui.job_titles() == titles
    said = [line for line in ui.count_lines()[seen:] if line]
    assert not [line for line in said if line.startswith(WAITING)], f"the Jobs link: the list was loading again; the count line said {said}"
    ui.settle()
    assert ui.requests_after("opened-again", "/api/postings") <= BACK_LIST_REQUESTS
    ui.wall_budget("the Jobs link back (small home)", BACK_WALL_SECONDS, "opened-again", "link-back")

    # The whole way: never two list reads, peeks or status reads at once.
    for resource in ("/api/postings", "/api/new", "/api/postings/status"):
        ui.no_more_than_one_in_flight(resource)
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
