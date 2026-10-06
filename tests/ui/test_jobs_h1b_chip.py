"""0.1.11.3 item 8: the company's H-1B approvals are on the Jobs card and the job page, as a label, never when there is no figure.

Real server and page (the small home, no model call, no network). The small home's boards are made up, so the bundled
catalog holds no figure for them: the server's own rows say ``h1b: null`` and the page shows NOTHING about H-1B. To see
the chip, the list answer is served with a figure on ONE row (the server's own rows otherwise); the row-level read model
with a catalog figure is pinned in tests/behaviors/scout_find_jobs/test_postings_h1b_label.py.

- the card of the row with a figure says "Sponsorship not stated · 32 H-1B approvals"; no other card says H-1B at all;
- that job's page says the same; another job's page (no figure) says nothing about H-1B (no "unknown", no zero);
- with no figure anywhere (the server's rows) the list and a job page never say H-1B.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

pytestmark = pytest.mark.ui

ROW = '[data-testid="job-row"]'
CHIP_TEXT = "Sponsorship not stated · 32 H-1B approvals"


def _serve_one_figure(ui) -> str:
    """Serve the Jobs list with a catalog figure on its first row; return that row's job identity."""

    figured: list[str] = []

    def postings(route) -> None:
        if route.request.method != "GET" or "/api/postings/" in route.request.url.split("?")[0]:
            route.continue_()
            return
        body = route.fetch().json()
        for place, row in enumerate(body.get("postings", {}).get("rows", [])):
            row["h1b"] = {"approvals": 32, "fiscal_years": ["2025", "2026"]} if place == 0 else None
            row.setdefault("sponsorship", None)
            if place == 0:
                figured[:] = [row["job_identity"]]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.route("**/api/postings?*", postings)
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert figured, "the list was served"
    return figured[0]


def test_the_server_rows_without_a_figure_show_no_h1b_anywhere(ui, scout_server) -> None:
    rows = ui.server_json("/api/postings?limit=200")["postings"]["rows"]
    assert rows and all(row["h1b"] is None and "sponsorship" in row for row in rows)
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert "H-1B" not in ui.page.locator("body").inner_text()
    ui.goto("/#/jobs/" + quote(scout_server.demo.hero_job, safe=""))
    ui.wait_for_job_page()
    assert "H-1B" not in ui.page.locator(".job-page").inner_text()
    ui.assert_clean()


def test_the_chip_is_on_the_card_and_the_job_page_and_only_where_a_figure_exists(ui, scout_server) -> None:
    figured = _serve_one_figure(ui)
    cards = ui.page.locator(ROW)
    assert cards.count() >= 2
    with_chip = ui.page.locator(f"{ROW}:has(a[href='#/jobs/{quote(figured, safe='')}'])")
    assert with_chip.count() == 1
    assert CHIP_TEXT in with_chip.inner_text()
    assert ui.page.locator(f"{ROW}:has-text('H-1B')").count() == 1, "no other card says H-1B"
    assert "unknown" not in with_chip.inner_text().lower()

    ui.goto("/#/jobs/" + quote(figured, safe=""))
    ui.wait_for_job_page()
    ui.settle()
    assert CHIP_TEXT in ui.page.locator(".job-page .job-header").inner_text()

    other = next(row["job_identity"] for row in ui.server_json("/api/postings?limit=200")["postings"]["rows"] if row["job_identity"] != figured)
    ui.page.unroute_all()
    ui.goto("/#/jobs/" + quote(other, safe=""))
    ui.wait_for_job_page()
    ui.settle()
    assert "H-1B" not in ui.page.locator(".job-page").inner_text()
    ui.assert_clean()
