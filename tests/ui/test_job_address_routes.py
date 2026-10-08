"""0.1.11.2 after-release #2 and #10: a job page opened with an address the store keeps differently.

Real server and page on the fixture model and transport (no model call, no network); synthetic addresses only.
Pinned:
- #10: a job assessed at `.../jobs/101?gh_jid=101` opens at `#/jobs/<same address with a slash before the query>`
  (`.../jobs/101/?gh_jid=101`): the route reads the address the way the store does, and the page is the job's;
- #2: when no job page exists for an address but an on-demand assessment does, the not-found state says "This job
  was assessed on demand" and links to `#/assessments/<its address>`; with none, it keeps the plain not-found words.
"""

from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

import pytest

from tests.ui.evidence import shot

pytestmark = pytest.mark.ui

URL = "https://boards.greenhouse.io/acme/jobs/101?gh_jid=101"
SLASHED = "https://boards.greenhouse.io/acme/jobs/101/?gh_jid=101"
UNKNOWN = "https://boards.greenhouse.io/acme/jobs/909?gh_jid=909"
PAGE = ".job-page"
VERDICT = f'{PAGE} [data-role="verdict-wording"]'
HINT = '[data-role="on-demand-hint"]'


def assess_by_box(ui, url: str) -> None:
    ui.goto("/#/assess")
    ui.page.locator("#quick-assess-url").wait_for()
    ui.settle()
    ui.page.locator("#quick-assess-url").fill(url)
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/api/assess", timeout=60_000) as answered:
        ui.page.locator('button[type="submit"]').click()
    assert answered.value.status in (200, 201), answered.value.text()
    ui.wait_for_job_page()
    ui.page.locator(VERDICT).wait_for()


def test_an_address_with_a_slash_before_the_query_opens_the_job_the_store_keeps(ui) -> None:
    assess_by_box(ui, URL)
    identity = ui.server_json(f"/api/jobs?url={quote(URL, safe='')}")["job_identity"]
    assert "/101?" in identity and "/101/?" not in identity

    ui.goto("/#/jobs/" + quote(SLASHED, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(VERDICT).wait_for()
    ui.settle()
    assert ui.page.locator(f"{PAGE} h2", has_text="We have no stored posting").count() == 0
    assert (ui.page.locator(VERDICT).text_content() or "").strip()
    shot(ui, "address-slash-before-query-opens")
    ui.assert_clean()


def test_the_not_found_page_points_at_an_on_demand_assessment_and_says_plainly_when_there_is_none(ui) -> None:
    assess_by_box(ui, URL)

    # No job page can be built for the item (its identity is blank), but the store holds the assessment for the address.
    def blank_identity(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        for item in body.get("items", []):
            if item.get("job", {}).get("normalized_url") == URL:
                item["job"]["job_identity"] = ""
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.goto("about:blank")
    ui.page.route("**/api/assessments*", blank_identity)
    ui.goto("/#/jobs/" + quote(SLASHED, safe=""))
    ui.page.locator(HINT).wait_for()
    ui.settle()
    text = ui.page.locator(HINT).text_content() or ""
    assert text.startswith("This job was assessed on demand:") and "Assessments" in text, text
    href = ui.page.locator(f"{HINT} a").get_attribute("href") or ""
    assert href.startswith("#/assessments/"), href
    shot(ui, "not-found-on-demand-hint")
    ui.page.unroute_all()

    ui.goto("/#/jobs/" + quote(UNKNOWN, safe=""))
    ui.page.locator(f"{PAGE}, .panel h2", has_text="We have no stored posting at this address").first.wait_for()
    ui.settle()
    assert ui.page.locator(HINT).count() == 0
    assert "Nothing is stored for" in (ui.page.locator(".panel .muted").first.text_content() or "")
    assert ui.page.locator("[data-role='back-to-jobs']").get_attribute("href") == "#/jobs"
    shot(ui, "not-found-plain")
    # 0.1.11.7 FS2: an address no list holds is asked ONCE of the by-address job read (the company index may hold it).
    # Nothing holds this one: that read answers 404, and it is the only problem of the page.
    assert ui.network.http_errors == ["HTTP 404 GET /api/jobs"], ui.network.http_errors
    ui.network.http_errors.clear()
    ui.network.console_errors[:] = [line for line in ui.network.console_errors if "404" not in line]
