"""0.3.4 (gigai-9j8): the assess form says WHY a posting could not be read, and offers its paste path.

Real page and server; the `POST /api/assess` answer is routed in the browser (`page.route`) with the error body the
server writes for these codes (`find_jobs/api/assess.py write_assess_error`: `{error: {code, message, reason}}`), so no
network and no model. Pinned:
- `job_text_unavailable` (422): the server's reason ("the page at this URL carries no posting text ...") and "Switch to
  Paste text and paste the posting's text instead." Before, the form said the generic 422 text "The server rejected this
  request as invalid. Reload and try again." and hid the reason.
- `job_fetch_failed` (502): the same shape with the server's failures.
- the callout's "Paste the posting text" switches the form to Paste text and focuses `#quick-assess-text`.
- another 422 (`invalid_value`) still says the generic 422 text: nothing else changed in the mapping.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from tests.ui.evidence import shot

pytestmark = pytest.mark.ui

URL = "https://careers.example.test/jobs/77"
ERROR = '[data-role="assess-error"]'
PASTE = '[data-action="assess-paste-text"]'
GENERIC_422 = "The server rejected this request as invalid. Reload and try again."
PASTE_HINT = "Switch to Paste text and paste the posting's text instead."
#: The server's own messages (`job_input.py`), verbatim; `tests/api_e2e/test_ui_assess_job_text_model.py` pins them against the real resolver.
NO_TEXT = "the page at this URL carries no posting text (a script-only page, or a posting that is no longer listed); pass --job-text with the posting text instead"
FETCH_FAILED = "fetching the job failed: HTTP 503 from https://careers.example.test/jobs/77; pass --job-text with the posting text instead"


def _answer(status: int, code: str, message: str, **extra):
    def route(route) -> None:
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps({"error": {"code": code, "message": message, **extra}}))

    return route


def _assess(ui, status: int, code: str, message: str, **extra) -> str:
    ui.page.unroute_all()
    ui.page.route("**/api/assess", _answer(status, code, message, **extra))
    ui.goto("/#/assess")
    ui.page.locator("#quick-assess-url").wait_for()
    ui.settle()
    ui.page.locator("#quick-assess-url").fill(URL)
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/api/assess", timeout=30_000) as answered:
        ui.page.locator('button[type="submit"]').click()
    assert answered.value.status == status
    ui.page.locator(ERROR).wait_for()
    return (ui.page.locator(ERROR).text_content() or "").strip()


def test_a_script_only_page_says_the_servers_reason_and_offers_the_paste_path(ui) -> None:
    text = _assess(ui, 422, "job_text_unavailable", NO_TEXT, reason="no_text")
    assert text.startswith("The page at this URL carries no posting text (a script-only page, or a posting that is no longer listed). " + PASTE_HINT), text
    assert GENERIC_422 not in text and "--job-text" not in text, text
    assert ui.page.locator("#quick-assess-text").count() == 0  # still on Job URL
    shot(ui, "assess-job-text-unavailable")

    ui.page.locator(PASTE).click()
    field = ui.page.locator("#quick-assess-text")
    field.wait_for()
    assert field.evaluate("(el) => document.activeElement === el"), "the paste field has the focus"
    assert ui.page.locator(PASTE).count() == 0  # the offer is gone once the form is on Paste text
    assert ui.page.locator(".toggle-button.active").text_content() == "Paste text"
    shot(ui, "assess-job-text-unavailable-paste")

    assert ui.network.http_errors == ["HTTP 422 POST /api/assess"], ui.network.http_errors
    ui.network.http_errors.clear()
    ui.network.console_errors[:] = [line for line in ui.network.console_errors if "422" not in line]
    ui.assert_clean()


def test_a_fetch_failure_says_the_servers_failures_and_offers_the_paste_path(ui) -> None:
    text = _assess(ui, 502, "job_fetch_failed", FETCH_FAILED, reason="network_error")
    assert text.startswith("Fetching the job failed: HTTP 503 from https://careers.example.test/jobs/77. " + PASTE_HINT), text
    assert ui.page.locator(PASTE).count() == 1
    shot(ui, "assess-job-fetch-failed")
    assert ui.network.http_errors == ["HTTP 502 POST /api/assess"], ui.network.http_errors
    ui.network.http_errors.clear()
    ui.network.console_errors[:] = [line for line in ui.network.console_errors if "502" not in line]
    ui.assert_clean()


def test_any_other_422_keeps_the_generic_text(ui) -> None:
    text = _assess(ui, 422, "invalid_value", "job_url: not a URL")
    assert text == GENERIC_422, text
    assert ui.page.locator(PASTE).count() == 0
    assert ui.network.http_errors == ["HTTP 422 POST /api/assess"], ui.network.http_errors
    ui.network.http_errors.clear()
    ui.network.console_errors[:] = [line for line in ui.network.console_errors if "422" not in line]
    ui.page.unroute_all()
    ui.assert_clean()
