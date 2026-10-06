"""0.1.11.3 items 5 and 6: Generate PDF is INSIDE the "Suggested resume" card, and its form carries the profile's
sponsorship answer as an editable "Work authorization" line that reaches the PDF.

Real server, nothing stubbed, on the small home's tailored job (a stored resume, no open question).

Before: the only way to a PDF was "Apply: get the PDF" in an "Apply" card at the very bottom of the job page, below
the suggestions, and the PDF said nothing about work authorization even when the profile said the user needs
sponsorship.

Pinned:
- item 5: the page has ONE way to the PDF, a button that reads "Generate PDF", and it is inside the resume card
  (`#job-resume`), above the picked resume's preview, the Suggestions card and the pipeline; no "Apply" card is left;
- item 6, a profile that does NOT need sponsorship: the form's "Work authorization (optional)" field is empty; a PDF
  made that way sends an empty `work_authorization` and prints no such line;
- item 6, a profile that DOES need sponsorship (`PUT /api/setup`, what Settings does): the field is prefilled
  "Requires visa sponsorship"; the user edits it; Generate is ONE `POST /api/tailored-resumes/pdf` whose
  `header.work_authorization` is the edited wording, and the downloaded PDF prints it once, as a line of its header;
  the edit is NOT remembered (operator, 2026-10-06: GigAI forgets what is typed into this form right after): the
  browser stores nothing, and a second Generate PDF (the form closed and opened again, and after a reload) starts
  from the profile's answer again, never from the last edit;
- the wording is in no file of the server's home, and the stored job resume (markdown and JSON) does not hold it.

This flow CHANGES the shared home (a setting, put back at the end), so it runs late (`UI_ORDER`).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

from tests.ui.support import tid
from tests.ui.test_generate_pdf import files_holding
from tests.ui.test_reassess_stale import old_assessments

pytestmark = pytest.mark.ui
UI_ORDER = 81  # changes the shared home (a setting, put back): right after the old-assessment flow, before the profile's delete

PAGE = ".job-page"
PANEL = f"{PAGE} #job-resume"
APPLY = f"{PANEL} {tid('job-apply')}"
BUTTON = f'{APPLY} [data-action="apply"]'
FORM = f'{APPLY} [data-role="generate-pdf-form"]'
TIMELINE = f"{PAGE} {tid('step-timeline')}"
FIELD = "#generate-pdf-work_authorization"
PREFILL = "Requires visa sponsorship"
EDITED = "H-1B, requires sponsorship (ZQ-5582)"
NAME = "Zephyrine Quillfeather"
#: Set GIGAI_UI_P2B_SHOT to a file path to keep a picture of the resume card with the form open (hand check only).
SHOT = os.environ.get("GIGAI_UI_P2B_SHOT")


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(path.read_bytes())).pages)


def ready(ui) -> None:
    ui.wait_for_job_page()
    ui.page.locator(PANEL).wait_for()
    # The pipeline card is drawn (the placement check reads it). Its state is not waited for: an earlier flow of the run
    # may have left this job's steps waiting for an approval, and nothing here depends on them.
    ui.page.locator(TIMELINE).wait_for()
    ui.page.locator(BUTTON).wait_for()


def open_job(ui, job: dict) -> None:
    ui.goto("/#/jobs/" + quote(job["job_identity"], safe=""))
    ready(ui)


def open_form(ui):
    """Click the card's Generate PDF; a stale resume asks first ("Use it as it is"). Returns the form."""

    ui.page.locator(BUTTON).click()
    form = ui.page.locator(FORM)
    as_is = ui.page.locator(f'{APPLY} [data-action="apply-as-is"]')
    form.or_(as_is).first.wait_for()
    if as_is.count():
        as_is.click()
    form.wait_for()
    return form


def generate(ui, form, step: str) -> tuple[dict, Path]:
    """Generate: ONE request; returns (its body, the downloaded PDF)."""

    ui.settle()
    ui.step(step)
    with ui.page.expect_download() as waiting:
        with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/tailored-resumes/pdf") as sent:
            form.locator('[data-role="generate-pdf"]').click()
    download = waiting.value
    ui.page.locator(f'{APPLY} [data-role="pdf-saved"]').wait_for()
    ui.settle()
    assert ui.writes_after(step) == ["POST /api/tailored-resumes/pdf"]
    return sent.value.post_data_json, Path(download.path())


def generate_named(ui, form, step: str) -> tuple[dict, Path]:
    ui.page.fill("#generate-pdf-name", NAME)
    return generate(ui, form, step)


def test_generate_pdf_is_in_the_suggested_resume_card_and_prints_the_work_authorization_line(ui, scout_server) -> None:
    rows = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    job = next(row for row in rows if row["tailored"] and row["state"] == "matched" and not row["open_questions"])
    assert ui.server_json("/api/setup")["prefs"]["visa_sponsorship_required"] is False, "the small home's profile needs no sponsorship"

    # --- item 5: ONE Generate PDF button, inside the resume card, above the preview and the suggestions ---
    open_job(ui, job)
    assert ui.page.locator(f'{PAGE} [data-action="apply"]').count() == 1, "one way to the PDF"
    assert (ui.page.locator(BUTTON).text_content() or "").strip() == "Generate PDF"
    assert ui.page.locator(f"{PAGE} {tid('job-apply')}").count() == 1 and ui.page.locator(APPLY).count() == 1, "the button is inside the resume card"
    assert ui.page.locator(f"{PAGE} section.panel > .resume-toolbar > h3", has_text="Apply").count() == 0, "no Apply card is left"
    assert "Apply: get the PDF" not in ui.page.locator(PAGE).inner_text()
    heading = (ui.page.locator(f"{PANEL} > .resume-toolbar h3").text_content() or "").strip()
    assert heading in ("Suggested resume", "Your resume for this job"), heading
    top = lambda selector: ui.page.locator(selector).first.bounding_box()["y"]  # noqa: E731
    assert top(f"{PANEL} > .resume-toolbar h3") < top(BUTTON) < top(f"{PANEL} .md-preview"), "the button is at the top of the card, above the resume"
    below = [selector for selector in (f"{PAGE} {tid('job-suggestions')}", TIMELINE) if ui.page.locator(selector).count()]
    assert TIMELINE in below and all(top(BUTTON) < top(selector) for selector in below), "the button is above the Suggestions card and the pipeline"
    assert top(BUTTON) - top(f"{PANEL} > .resume-toolbar h3") < 120, "right under the card's heading"

    # --- item 6, no sponsorship needed: the line is empty, and the PDF prints none ---
    form = open_form(ui)
    assert form.locator("label", has_text="Work authorization (optional)").count() == 1
    ui.page.locator(PANEL).screenshot(path=str(SHOT)) if SHOT else None
    assert ui.page.input_value(FIELD) == "", "the profile needs no sponsorship: no line"
    ui.page.fill("#generate-pdf-name", NAME)
    body, pdf = generate(ui, form, "before-plain-pdf")
    assert body["header"]["work_authorization"] == "" and body["header"]["name"] == NAME
    plain = pdf_text(pdf)
    assert NAME.upper() in plain.upper() and "sponsorship" not in plain.lower()
    ui.assert_clean()

    # --- item 6, the profile needs sponsorship: prefilled, editable, and the edited wording reaches the PDF ---
    with old_assessments(ui) as (stale_job, _row, _stored):
        assert stale_job["job_identity"] == job["job_identity"]
        assert ui.server_json("/api/setup")["prefs"]["visa_sponsorship_required"] is True
        open_job(ui, job)
        ui.reload()  # the profile's answer is read with the page's settings
        ready(ui)
        form = open_form(ui)
        assert ui.page.input_value(FIELD) == PREFILL, "the profile says sponsorship is needed: the form starts with it"
        ui.page.fill(FIELD, EDITED)
        ui.page.fill("#generate-pdf-name", NAME)
        body, pdf = generate(ui, form, "before-pdf-with-the-line")
        assert body["header"]["work_authorization"] == EDITED, "the edited wording is what the PDF request carries"
        assert set(body) == {"profile_id", "job_identity", "header"} and body["job_identity"] == job["job_identity"]
        text = pdf_text(pdf)
        assert text.count(EDITED) == 1 and EDITED in text.split("\n")[:4], text[:300]
        assert text.split("\n")[0] == NAME.upper(), "the header starts with the name; the line is one of its lines"

        # Not remembered: the browser stores none of it, and the next form starts from the profile's answer again.
        kept = ui.page.evaluate("() => JSON.stringify([Object.entries(window.localStorage), Object.entries(window.sessionStorage), document.cookie, window.location.href])")
        assert "ZQ-5582" not in kept and NAME not in kept and "ZQ-5582" not in str(ui.page.context.cookies())
        assert ui.page.input_value(FIELD) == EDITED, "the open form still shows what was typed for this PDF"
        ui.page.locator(BUTTON).click()  # Close
        form.wait_for(state="detached")
        form = open_form(ui)  # a second Generate PDF
        assert ui.page.input_value(FIELD) == PREFILL, "a second Generate PDF starts from the profile's answer, not the last edit"
        assert ui.page.input_value("#generate-pdf-name") == ""
        body, pdf = generate_named(ui, form, "before-second-pdf")
        assert body["header"]["work_authorization"] == PREFILL and pdf_text(pdf).count(PREFILL) == 1 and "ZQ-5582" not in pdf_text(pdf)
        ui.reload()
        ready(ui)
        open_form(ui)
        assert ui.page.input_value(FIELD) == PREFILL and ui.page.input_value("#generate-pdf-name") == "", "and after a reload"

        # Printed on the PDF only: not in the stored job resume, the resumes folder or any file of the server's home.
        stored = ui.server_json(f"/api/tailored-resumes?profile_id={quote(job['profile_id'], safe='')}&job_identity={quote(job['job_identity'], safe='')}")
        assert "ZQ-5582" not in json.dumps(stored) and stored["items"], "the job resume's markdown and JSON do not hold the line"
        assert files_holding(scout_server.home, [b"ZQ-5582", NAME.encode()]) == [], "the server's home holds what was typed into the PDF form"
        folder = Path(ui.server_json("/api/resumes-folder")["path"])
        assert files_holding(folder, [b"ZQ-5582"]) == [] if folder.is_dir() else True
        ui.assert_clean()
