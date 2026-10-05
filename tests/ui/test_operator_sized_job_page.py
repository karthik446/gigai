"""0.1.11 N6, the RELEASE GATE of the job page (SPEC section 6): on the operator-sized home, open a job, Apply, go back, the list is still there.

290,000 postings over 10,350 companies, 2 profiles (`tests/support/operator_home.py`, synthetic), the REAL server
process with its background threads running, nothing answered by the test. 0.1.10.8 passed every test and its Jobs
page never loaded on a home of this size: a page is not done until it was loaded there, in a real browser.

The flow: Jobs, the assessed postings (the address the "Assessed" chip gives); one is opened FROM THE LIST; its job
page shows the resume panel with the stored resume, the four pipeline rows and ONE "Apply: get the PDF" button; Apply opens the Generate PDF form, the PDF is ONE `POST /api/tailored-resumes/pdf` and a download, and
nothing follows it (no other request, no new page); Back shows the same rows at once, with no "Loading" and at most
the one refresh in place of the list.

The job needs a stored resume to apply with, and this home has none. Every assessment of its fixture model waits on
one question, and its made-up postings are too short to be assessed again (`posting_requirements_unreadable`), so
no job of this home can be made a match. The flow therefore opens a HELD job whose resume the user asked for: the
server stores it first, through its own route (`job_resume_fixtures.ensure_stored_resume`: the route of this tree; one
place to change with packet N4, where it becomes the draft of `POST /api/job-resumes/pick`). The page then shows why
the job is held, the stored resume and the ONE Apply button. That CHANGES the session's operator-sized home (one
stored resume, and the pipeline's own steps for it), so this flow runs after the ones that only read it (`UI_ORDER`).

With `GIGAI_UI_EVIDENCE=<folder>` the flow also writes the screenshots of its four steps (PNG, with the page's own
text beside each for the media privacy gate, `python -m tools.media.privacy_scan <folder>`) and `numbers.json`: the
size of the home, each step's wall and server-CPU seconds, the requests it made and the server's peak memory.

MEASURED (14-core laptop, 2026-10-05, Python 3.11): see the report of packet N6 and `numbers.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.ui import jobs_page
from tests.ui.job_resume_fixtures import ensure_stored_resume, evidence_folder, shot
from tests.ui.jobs_page import LIST, WAITING, identities
from tests.ui.operator_home_ui import SERVER_RSS_MB
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid
from tools.media.operator_ui_check import COLD_ROWS_SECONDS, WATCHED

pytestmark = [pytest.mark.ui, pytest.mark.operator_sized]
UI_ORDER = 5  # changes the session's operator-sized home (one stored resume): after the flows that only read it

HOME = "operator-sized"
PAGE = ".job-page"
PANEL = f"{PAGE} {tid('job-resume')}"
APPLY = f"{PAGE} {tid('job-apply')}"
OPEN_JOB_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS  # the title, the state line, the timeline AND the stored resume
OPEN_JOB_CPU_SECONDS = 5.0
APPLY_WALL_SECONDS = INTERACTIVE_WALL_SECONDS  # the form opens: a click
PDF_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS  # a render
BACK_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
BACK_CPU_SECONDS = 3.0  # as test_operator_sized_jobs.py
HEADER = {"name": "Zephyrine Quillfeather", "email": "zephyrine.quillfeather@example.test"}
#: The job's own pipeline is no longer running or queued (done, failed, or waiting for an approval): nothing of it is still to come.
SETTLED_TIMELINE = f".job-page {tid('step-timeline')}" + "".join(f':not([data-state="{state}"])' for state in ("running", "waiting"))
PIPELINE_PATIENCE_MS = 120_000


def test_open_a_job_apply_and_go_back_on_the_operator_sized_home(operator_ui, operator_server) -> None:
    ui = operator_ui
    folder = evidence_folder()
    built = operator_server.built
    # --- Jobs, once, so that a cold server prepares its list (the page waits for it; the server answers no rows before) ---
    ui.watch_count_line()
    ui.goto("/#/jobs?state=assessed")
    ui.wait_for_jobs_list(timeout_ms=int(COLD_ROWS_SECONDS * 1000))
    ui.settle()
    ui.step("prepared")

    # --- an assessed job with a stored resume to apply with (the server asked directly, as an agent would). Every
    # assessment of this home waits on one question, so the job is HELD and its resume is the one a user asked for.
    assessed = ui.server_json(f"{LIST}?state=assessed&limit=50")["postings"]["rows"]
    job = next(row for row in assessed if row["state"] in ("matched", "tailored", "needs_answers"))
    ensure_stored_resume(ui, job["profile_id"], job["job_identity"])

    # --- Jobs: the assessed postings, as they are now ---
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == LIST and response.status == 200, timeout=int(COLD_ROWS_SECONDS * 1000)) as answered:
        ui.reload()
    ui.wait_for_jobs_list(timeout_ms=int(COLD_ROWS_SECONDS * 1000))
    ui.step("listed")
    listed = jobs_page.shown(ui)["rows"]
    assert identities(listed) == identities(answered.value.json()["postings"]["rows"]) and job["job_identity"] in identities(listed)
    ui.settle()
    ui.step("list-settled")
    shot(ui, folder, "n6-1-jobs-list")

    # --- open the job from the list ---
    row = ui.page.locator(f"{tid('job-row')} [data-action='open-job']", has_text=job["title"]).first
    row.click()
    ui.wait_for_job_page()
    panel = ui.page.locator(f'{PANEL}[data-state="stored"]')
    panel.wait_for()
    ui.step("opened")
    ui.page.locator(SETTLED_TIMELINE).wait_for(timeout=PIPELINE_PATIENCE_MS)  # the job's own pipeline has nothing still to come
    assert ui.page.locator(f"{PAGE} .job-title").text_content() == job["title"]
    assert ui.page.locator(f"{PAGE} {tid('step-timeline')} [data-step]").evaluate_all("(steps) => steps.map((step) => step.dataset.step)") == ["assess", "pick", "ats", "label"]
    assert panel.locator('[data-role="provenance"]').count() == 1 and panel.locator(".md-preview, .clean-wrap").count() == 1
    if job["state"] == "needs_answers":  # a held job: the page says why, beside the resume the user asked for
        assert (panel.get_attribute("data-gate"), panel.locator('[data-role="hold-sentence"]').count()) == ("hold_question", 1)
    assert ui.page.locator(f'{PAGE} [data-action="tailor"]').count() == 0
    ui.settle()
    ui.step("job-settled")
    assert ui.writes_after("list-settled") == [], "opening a job writes nothing"
    ui.cpu_budget(f"open a job with its resume ({HOME})", OPEN_JOB_CPU_SECONDS, "list-settled", "job-settled")
    ui.wall_budget(f"open a job with its resume ({HOME})", OPEN_JOB_WALL_SECONDS, "list-settled", "opened")
    shot(ui, folder, "n6-2-job-page")

    # --- Apply: one button, the form, the PDF, nothing after it ---
    button = ui.page.locator(f'{APPLY} [data-action="apply"]')
    assert ui.page.locator(f'{PAGE} [data-action="apply"]').count() == 1 and (button.text_content() or "").strip() == "Apply: get the PDF"
    button.click()
    form = ui.page.locator(f'{APPLY} [data-role="generate-pdf-form"]')
    form.wait_for()
    ui.step("apply-open")
    ui.wall_budget(f"Apply opens the form ({HOME})", APPLY_WALL_SECONDS, "job-settled", "apply-open")
    for field, value in HEADER.items():
        ui.page.fill(f"#generate-pdf-{field}", value)
    ui.settle()
    ui.step("before-pdf")
    with ui.page.expect_download() as waiting:
        form.locator('[data-role="generate-pdf"]').click()
    download = waiting.value
    ui.page.locator(f'{APPLY} [data-role="pdf-saved"]').wait_for()
    ui.step("pdf")
    pdf = Path(download.path()).read_bytes()
    assert pdf.startswith(b"%PDF-") and len(pdf) > 2000
    ui.settle()
    ui.step("pdf-settled")
    assert ui.writes_after("before-pdf") == ["POST /api/tailored-resumes/pdf"] and ui.requests_after("before-pdf") == 1, "Apply is the PDF and nothing after it"
    assert len(ui.page.context.pages) == 1 and "#/jobs/" in ui.page.url
    ui.wall_budget(f"Apply: the PDF ({HOME})", PDF_WALL_SECONDS, "before-pdf", "pdf")
    shot(ui, folder, "n6-3-apply")

    # --- Back: the list is still there ---
    seen = len(ui.count_lines())
    ui.page.go_back()
    ui.page.wait_for_function(jobs_page.SETTLED_ROWS_JS, arg=len(listed))
    ui.step("back")
    assert "state=assessed" in ui.page.url, f"Back from the job did not land on the list it came from: {ui.page.url}"
    assert identities(jobs_page.shown(ui)["rows"]) == identities(listed), "back from the job: the list does not show the rows it had"
    said = [line for line in ui.count_lines()[seen:] if line]
    assert not [line for line in said if line.startswith(WAITING)], f"back from the job: the list was loading again; the count line said {said}"
    ui.settle()
    ui.step("back-settled")
    assert ui.requests_between("pdf-settled", "back-settled", LIST) <= 1  # at most the one refresh in place, never a re-list
    ui.cpu_budget(f"Back to the list from a job ({HOME})", BACK_CPU_SECONDS, "pdf-settled", "back-settled")
    ui.wall_budget(f"Back to the list from a job ({HOME})", BACK_WALL_SECONDS, "pdf-settled", "back")
    shot(ui, folder, "n6-4-back-to-the-list")

    for resource in WATCHED.values():
        ui.no_more_than_one_in_flight(resource)
    peak = ui.memory_budget(f"the server's peak memory ({HOME})", SERVER_RSS_MB)
    if folder is not None:
        seconds = lambda first, last: round(float(ui.wall_seconds_between(first, last)), 2)  # noqa: E731
        cpu = lambda first, last: round(float(ui.server_cpu_seconds_between(first, last)), 2)  # noqa: E731
        numbers = {
            "home": {"postings": built.postings, "companies": built.companies, "profiles": len(built.profiles), "prebuilt": operator_server.prebuilt},
            "steps": {
                "jobs list (state=assessed) shown": {"wall_s": seconds("start", "listed"), "rows": len(listed)},
                "open a job from the list: title, state, timeline and the stored resume": {"wall_s": seconds("list-settled", "opened"), "server_cpu_s": cpu("list-settled", "job-settled"), "api_requests": int(ui.requests_between("list-settled", "job-settled"))},
                "Apply opens the Generate PDF form": {"wall_s": seconds("job-settled", "apply-open")},
                "Apply: the PDF (one request, a download)": {"wall_s": seconds("before-pdf", "pdf"), "server_cpu_s": cpu("before-pdf", "pdf-settled"), "pdf_bytes": len(pdf), "api_requests": int(ui.requests_between("before-pdf", "pdf-settled"))},
                "Back: the same rows, no Loading": {"wall_s": seconds("pdf-settled", "back"), "server_cpu_s": cpu("pdf-settled", "back-settled"), "list_requests": int(ui.requests_between("pdf-settled", "back-settled", LIST))},
            },
            "server_peak_rss_mb": round(float(peak), 1),
            "console_errors_page_errors_http_400_plus": ui.problems(),
        }
        (folder / "numbers.json").write_text(json.dumps(numbers, indent=2) + "\n", encoding="utf-8")
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
