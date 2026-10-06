"""0.1.11.2 SIZE: a job page opened from the Jobs list shows the WHOLE stored posting, with its line breaks.

Real server and page (fixture home, no model call, no network). The Jobs list row carries a 400-character, one-paragraph
preview of the text (`description`); the page asks `GET /api/jobs?url=` for the one job it opens and draws the stored text
whole, so a REQUIRED section past the first 400 characters is on the page. While the whole text is not there (none served)
the preview stays (the served job has no text), and it says it is the start of the posting. Synthetic text only.
"""

from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

import pytest

from tests.ui.evidence import shot

pytestmark = pytest.mark.ui

PAGE = ".job-page"
JD = f"{PAGE} .jd-excerpt"
MORE = f"{PAGE} .jd-more"
IDENTITY = "https://jobs.example.test/acme/synthetic-staff-1"
LONG = (
    "Acme builds scheduling software for clinics and values careful, humble engineering. " * 8
    + "\n\nRESPONSIBILITIES\n- Own the gameplay systems\n- Review designs\n\nREQUIRED SKILLS\n- Five years of Python in production\n- Postgres schema changes on live tables\n\nNICE TO HAVE\n- Rust\n"
)
PREVIEW = " ".join(LONG.split())[:399].rstrip() + "…"


def _row() -> dict:
    return {
        "job_identity": IDENTITY, "normalized_url": IDENTITY, "job_url": IDENTITY, "title": "Synthetic Staff Engineer",
        "company": "Acme", "company_slug": "acme", "company_name": "Acme", "location": "Remote", "work_mode": "remote", "salary": None,
        "description": PREVIEW, "first_seen": "2026-10-01T00:00:00Z", "profile_id": None, "state": "not_assessed",
    }


def _lay(ui, *, whole: bool) -> None:
    def postings(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        rows = body.get("postings", {}).get("rows")
        if isinstance(rows, list) and not any(row.get("job_identity") == IDENTITY for row in rows):
            rows.append(_row())
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def job(route) -> None:
        if urlsplit(route.request.url).path != "/api/jobs":
            route.continue_()
        elif whole:
            posting = {"job_identity": IDENTITY, "normalized_url": IDENTITY, "source_url": IDENTITY, "title": "Synthetic Staff Engineer", "text": LONG}
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"job_identity": IDENTITY, "posting": posting}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"job_identity": IDENTITY, "posting": {"job_identity": IDENTITY}}))  # no text served

    ui.page.goto("about:blank")
    ui.page.unroute_all()
    ui.page.route("**/api/postings*", postings)
    ui.page.route("**/api/jobs?*", job)


def _open(ui) -> None:
    ui.goto("/#/jobs/" + quote(IDENTITY, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(JD).wait_for()
    ui.settle()


def test_the_job_page_shows_the_required_section_of_the_whole_stored_text(ui) -> None:
    _lay(ui, whole=True)
    _open(ui)
    shown = ui.page.locator(JD).inner_text()
    assert "REQUIRED SKILLS\n- Five years of Python in production\n- Postgres schema changes on live tables" in shown, shown
    assert "NICE TO HAVE\n- Rust" in shown
    assert "…" not in shown and ui.page.locator(MORE).count() == 0
    shot(ui, "job-page-whole-description")
    ui.assert_clean()


def test_a_preview_the_server_cut_says_it_is_the_start_of_the_posting(ui) -> None:
    _lay(ui, whole=False)
    _open(ui)
    assert ui.page.locator(JD).inner_text().endswith("…")
    more = ui.page.locator(MORE)
    assert more.count() == 1 and "This is the start of the posting." in more.inner_text()
    assert "Open posting for the rest" in more.inner_text()
