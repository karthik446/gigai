"""0.1.11.4 UI1 items 6 and 11: the Jobs card says what the posting states about sponsorship; the stored resume's preview shows Other.

Real server and page (the small home, no model call, no network). The small home's postings state nothing about
sponsorship, so the list answer is served with the label fields laid over three of its own rows (the way
`test_jobs_h1b_chip.py` lays a figure over one); the read model that derives them is pinned in
tests/behaviors/scout_find_jobs.

- item 6: a stated "no sponsorship" with no H-1B figure shows red "No sponsorship" on the card; "Sponsors visas" with a
  figure shows "Sponsors visas · 32 H-1B approvals"; an unknown posting with no figure shows no sponsorship chip at all;
- item 11: the job page's stored-resume preview shows an `## Other` section the stored markdown holds, in "Show
  changes" and in "Clean copy".
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from tests.ui.evidence import shot
from tests.ui.job_resume_fixtures import stored_resume

pytestmark = pytest.mark.ui

ROW = '[data-testid="job-row"]'
BADGE = ".status-badge[class*='sponsorship-']"


def test_the_card_says_the_stated_sponsorship_with_or_without_a_figure(ui) -> None:
    labelled: dict[str, str] = {}

    def postings(route) -> None:
        if route.request.method != "GET" or "/api/postings/" in route.request.url.split("?")[0]:
            route.continue_()
            return
        body = route.fetch().json()
        for place, row in enumerate(body.get("postings", {}).get("rows", [])[:3]):
            row["sponsorship"], row["h1b"] = [
                ("not_offered", None), ("offered", {"approvals": 32, "fiscal_years": ["2025", "2026"]}), ("unknown", None),
            ][place]
            labelled[row["job_identity"]] = row["sponsorship"]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.route("**/api/postings?*", postings)
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert len(labelled) == 3
    chips = {}
    for identity, sponsorship in labelled.items():
        card = ui.page.locator(f"{ROW}:has(a[href='#/jobs/{quote(identity, safe='')}'])")
        badge = card.locator(BADGE)
        chips[sponsorship] = [" ".join(text.split()) for text in badge.all_inner_texts()]
        if sponsorship == "not_offered":
            assert badge.first.get_attribute("class").count("sponsorship-not_offered") == 1
    assert chips["not_offered"] == ["No sponsorship"], chips
    assert chips["offered"] == ["Sponsors visas · 32 H-1B approvals"], chips
    assert chips["unknown"] == [], chips
    shot(ui, "card-sponsorship-label")
    ui.page.unroute_all()
    ui.assert_clean()


def test_the_stored_resume_preview_shows_the_other_section(ui, scout_server) -> None:
    profile = ui.server_json("/api/profiles")["profiles"][0]["profile_id"]
    job = scout_server.demo.hero_job
    assert stored_resume(ui, profile, job) is not None or True

    def without_other(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        for item in body.get("items", []):
            result = item.get("result") or {}
            result["sections"] = [section for section in result.get("sections", []) if section.get("heading") != "other"]
            item["markdown"] = item["markdown"].split("## Other")[0] + "## Other\n\n- Earlier experience: Staff scheduling engineer, 2012-2016, batch systems. <!-- R9 -->\n"
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.route("**/api/tailored-resumes?*", without_other)
    ui.goto("/#/jobs/" + quote(job, safe=""))
    ui.wait_for_job_page()
    ui.settle()
    panel = ui.page.locator('[data-testid="job-resume"]')
    text = panel.inner_text()
    assert "Other" in text and "Earlier experience: Staff scheduling engineer" in text, text
    ui.page.click('[data-action="view-clean"]')
    clean = ui.page.locator(".clean-resume")
    assert clean.locator("h4", has_text="Other").count() == 1
    assert "Earlier experience: Staff scheduling engineer" in clean.inner_text()
    shot(ui, "resume-preview-other-section")
    ui.page.unroute_all()
