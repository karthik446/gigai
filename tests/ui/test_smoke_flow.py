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

    # Then the server's CPU seconds, then a loose wall ceiling.
    assert ui.server_cpu_seconds_between("start", "loaded") <= 1.5
    assert ui.wall_seconds_between("start", "loaded") <= 5

    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
