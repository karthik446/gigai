"""Smoke flow: the Jobs page loads and shows rows, and nothing in the browser complains.

Proves the harness end to end (home, server, browser, books, artifacts). Budgets follow the order
structure, server CPU, wall-clock. Measured on a laptop with this home (2026-10-03, 0.1.10.8):
15 API requests (one `/api/postings`), 0.40 server CPU seconds, 0.51 s wall. The ceilings are
about 3x the CPU and request counts and 10x the wall time: loose on purpose.
"""

from __future__ import annotations

import pytest

from tests.ui.support import tid

pytestmark = pytest.mark.ui

FIRST_LOAD_CPU_SECONDS = 3.0  # always a failure when over. 0.26 to 0.40 measured idle, up to 0.76 with every core busy (2026-10-04)
FIRST_LOAD_WALL_SECONDS = 5.0  # about 10x the 0.51 s measured; reported, a failure only with GIGAI_UI_BUDGETS=enforce
#: KNOWN (REPORT.md 4.3 / 7.1, still true on 0.1.10.9): the page asks /api/assessments twice per profile on a Jobs load
#: (two profiles' worth here: 4). 2 is the target; the ceiling keeps it from getting worse.
ASSESSMENT_REQUESTS = 4


def test_jobs_page_loads_and_shows_rows(ui) -> None:
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.step("loaded")

    # What the page shows.
    assert ui.job_rows() > 0, "the Jobs list shows no rows"
    assert ui.page.locator(tid("jobs-list")).count() == 1

    # Structure first: it cannot flake on a slow runner.
    ui.no_more_than_one_in_flight("/api/postings")
    ui.no_more_than_one_in_flight("/api/new")
    assert ui.requests_after("start", "/api/postings") <= 2
    assert ui.requests_after("start") <= 40
    ui.settle()
    assert ui.requests_after("start", "/api/assessments") <= ASSESSMENT_REQUESTS

    # Then the server's CPU seconds, then a loose wall ceiling.
    ui.cpu_budget("Jobs first load (small home)", FIRST_LOAD_CPU_SECONDS, "start", "loaded")
    ui.wall_budget("Jobs first load (small home)", FIRST_LOAD_WALL_SECONDS, "start", "loaded")

    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
