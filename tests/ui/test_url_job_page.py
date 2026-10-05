"""0.1.11 URLCHECK: a job assessed by URL (not in the stored-postings index) through the page's paste/URL box.

Real server and real page; the fixture model and transport (no model call, no network); one synthetic posting URL
the fixture serves and the small home does not hold. Pinned:
- "Assess a job" with a URL: ONE `POST /api/assess` (origin `quick_assess`), the page opens as a job page
  (`#/assessments/<url>`) with the verdict wording, the requirements and the chip of an on-demand job;
- the same job opens at `#/jobs/<url>` (a link an agent gives) and `GET /api/jobs?url=` answers it;
- the notices the API serves (`model_notice`, `requirements_note`) show on it, as on any job page;
- Jobs LIST: the job is not there (`GET /api/postings` has no row for it); Assessments lists it;
- Re-assess is the page's own gated action: off for a current assessment with no open question, and says why.
"""

from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

import pytest

pytestmark = pytest.mark.ui

URL = "https://boards.greenhouse.io/acme/jobs/101"
PAGE = ".job-page"
VERDICT = f'{PAGE} [data-role="verdict-wording"]'
NOTE = f'{PAGE} [data-role="requirements-note"]'
NOTICE = f'{PAGE} [data-role="model-notice"]'
REASSESS = f'{PAGE} [data-action="reassess"]'
HELP = f'{PAGE} [data-help="reassess"]'
SENTENCE = "Requirements read from the posting; one row is a guess."
NOTICE_TEXT = "This model is not one GigAI's accuracy results are for."


def test_a_job_assessed_by_the_box_opens_with_its_notices_and_is_not_on_the_jobs_list(ui) -> None:
    counts_before = ui.server_json("/api/postings")["counts"]
    ui.goto("/#/assess")
    ui.page.locator("#quick-assess-url").wait_for()
    ui.settle()
    ui.step("form")
    ui.page.locator("#quick-assess-url").fill(URL)
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/api/assess", timeout=60_000) as answered:
        ui.page.locator('button[type="submit"]').click()
    assert answered.value.status in (200, 201), answered.value.text()
    assert answered.value.request.post_data_json["job"] == {"job_url": URL}
    assert answered.value.request.post_data_json["origin"] == "quick_assess"
    ui.wait_for_job_page()
    ui.page.locator(VERDICT).wait_for()
    ui.settle()
    ui.step("opened")
    assert ui.page.url.split("#", 1)[1] == "/assessments/" + quote(URL, safe=""), ui.page.url
    assert ui.writes_after("form") == ["POST /api/assess"]
    assert (ui.page.locator(VERDICT).text_content() or "").strip()
    assert ui.page.locator(f"{PAGE} table tbody tr").count() >= 1, "the requirements table has rows"

    # The same job at the Jobs address; the API answers it; the notices the API serves show.
    served = ui.server_json(f"/api/jobs?url={quote(URL, safe='')}")
    assert served["job_identity"] == URL and served["assessments"]

    def lay(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        for item in body.get("items", []):
            if item.get("job", {}).get("job_identity") == URL:
                item["requirements_note"] = SENTENCE
                item["model_notice"] = {"text": NOTICE_TEXT, "link": {"label": "GigAI's accuracy results", "path": "/scout/accuracy-0-1-11/"}}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.goto("about:blank")
    ui.page.route("**/api/assessments*", lay)
    ui.goto("/#/jobs/" + quote(URL, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(VERDICT).wait_for()
    ui.settle()
    ui.step("by-jobs-address")
    assert ui.page.locator(NOTE).count() == 1 and SENTENCE in (ui.page.locator(NOTE).text_content() or "")
    assert ui.page.locator(NOTICE).count() == 1 and NOTICE_TEXT in (ui.page.locator(NOTICE).text_content() or "")
    reassess = ui.page.locator(REASSESS)
    assert reassess.count() == 1 and reassess.is_disabled(), "a current assessment with no open question: Re-assess is off"
    assert "no open questions" in (ui.page.locator(HELP).text_content() or "")
    ui.page.unroute_all()

    # Jobs LIST: no row; Assessments: listed.
    after = ui.server_json("/api/postings")
    assert after["counts"] == counts_before, "the index holds the same postings: a URL job is not added to it"
    assert URL not in json.dumps(after["postings"]) and "/jobs/101" not in json.dumps(after["postings"])
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    assert not any(URL in (href or "") for href in ui.page.locator(f"{PAGE}, a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))"))
    ui.goto("/#/assessments")
    ui.page.locator("a[href*='101']").first.wait_for()
