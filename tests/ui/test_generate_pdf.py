"""Flow 6 (REPORT.md 5.3): Generate PDF. Six contact fields and the optional work authorization line, a download, and
no name or contact detail that was typed is stored.

The hero job of the small home has a stored tailored resume, so `#/pdf/<profile>/<job>` is the page an agent's or
the CLI's headerless PDF is finished on. Real server, real render (Typst), nothing stubbed.

Pinned: the form has the six fields, all empty, and the button is off until a name is typed; Generate is ONE request
(`POST /api/tailored-resumes/pdf`) whose `header` carries the six values; the browser gets a download named by the
server (`<company>-<role>-<date>.pdf`, never the user's name) that is a PDF with the typed name in it; the page says
what it saved; then NOTHING of the six values is kept: not in localStorage, sessionStorage, a cookie or the address,
not in the form after a reload, and not in any file of the server's home (every file is read, the server log
included); the resumes folder holds no new file. Zero console errors.

0.1.11.3 item 6: the seventh field, "Work authorization (optional)", is empty here (this page has no profile answer
to start from), travels in the same `header`, prints in the PDF header's one contact line (item 15), and is kept nowhere,
like the six: not in the browser, not on the server, and it is empty again after a reload.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): the render 0.08 to 0.10 s wall, 0.07
server CPU seconds (the three-line resume of the fixture model; a real resume is more).
"""

from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.ui.support import FIRST_LOAD_WALL_SECONDS

pytestmark = pytest.mark.ui

#: Typed in the browser by the test: invented, on the reserved example.test domain and the 555-01xx range, and
#: different from the demo persona's, so a hit on disk can only come from this form.
HEADER = {
    "name": "Zephyrine Quillfeather",
    "email": "zephyrine.quillfeather@example.test",
    "phone": "(303) 555-0199",
    "location": "Ridgway, Colorado",
    "linkedin": "linkedin.example.test/in/zquillfeather",
    "link": "zquillfeather.example.test",
}
#: 0.1.11.3 item 6: the optional line; invented, with a marker no fixture holds.
WORK_AUTHORIZATION = "TN status, no sponsorship needed (ZQ-4410)"
SENT = {**HEADER, "work_authorization": WORK_AUTHORIZATION}
#: The server's own sentence for a resume that does not fit its pages (`resume_pdf.over_limit_note`, pinned in
#: tests/behaviors/scout_find_jobs/test_pdf_fits_page_limit.py), laid over a real response where a test needs it.
OVER_LIMIT_NOTE = (
    "This resume takes 3 pages: it does not fit on 2 pages even with the tightest spacing. To get 2 pages, shorten it automatically "
    "(the Shorten automatically button on the job's page, or `gigai scout resume pick --job-url URL --shorten`; no model call), "
    "then generate the PDF again. Or keep it at 3 pages."
)
PDF_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS  # a render, not a click: 0.08 to 0.10 s measured on a three-line resume
PDF_CPU_SECONDS = 5.0  # 0.07 measured; the renderer's first run on a machine reads its fonts


def files_holding(root: Path, needles: list[bytes]) -> list[str]:
    """Every file under `root` whose bytes hold one of `needles` (case as typed)."""

    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        hits = [needle.decode() for needle in needles if needle in data]
        if hits:
            found.append(f"{path.relative_to(root)}: {hits}")
    return found


def test_generate_pdf_six_fields_a_download_and_nothing_stored(ui, scout_server) -> None:
    demo = scout_server.demo
    folder = Path(ui.server_json("/api/resumes-folder")["path"])
    files_before = sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    ui.goto("/#/pdf/" + quote(demo.hero_profile_id, safe="") + "/" + quote(demo.hero_job, safe=""))
    form = ui.page.locator('[data-role="generate-pdf-form"]')
    form.wait_for()
    ui.step("form")

    # Six contact fields and the work authorization line, all empty; nothing to generate until there is a name.
    inputs = form.locator("input")
    assert inputs.evaluate_all("(fields) => fields.map((field) => field.id)") == [f"generate-pdf-{key}" for key in SENT]
    assert inputs.evaluate_all("(fields) => fields.map((field) => field.value)") == [""] * 7
    button = ui.page.locator('[data-role="generate-pdf"]')
    assert button.is_disabled()
    ui.page.fill("#generate-pdf-email", HEADER["email"])
    assert button.is_disabled(), "an email without a name must not be enough"
    for key, value in SENT.items():
        ui.page.fill(f"#generate-pdf-{key}", value)
    assert button.is_enabled()
    # 0.1.11.3 item 13: the open form asked once for the user's header file (a read; a POST so only Scout's page is answered).
    assert ui.writes_after("start") == ["POST /api/pdf-header"], "typing must not send anything"

    # Generate: one request with the six values, and a download named by the server.
    ui.step("typed")
    with ui.page.expect_download() as waiting:
        with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/tailored-resumes/pdf")) as sent:
            button.click()
    download = waiting.value
    ui.page.locator('[data-role="pdf-saved"]').wait_for()
    ui.step("saved")
    assert ui.writes_after("typed") == ["POST /api/tailored-resumes/pdf"]
    body = sent.value.post_data_json
    assert body["header"] == SENT and body["job_identity"] == demo.hero_job and body["profile_id"] == demo.hero_profile_id
    name = download.suggested_filename
    assert name.endswith(".pdf") and name.startswith("tallgrass-health-"), name
    assert "zephyrine" not in name.lower() and "quillfeather" not in name.lower(), "the file is named for the job, never for the user"
    assert (ui.page.locator('[data-role="pdf-saved"]').text_content() or "").strip() == f"Saved as {name}."
    pdf = Path(download.path()).read_bytes()
    assert pdf.startswith(b"%PDF-") and len(pdf) > 2000
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert HEADER["name"].upper() in text.upper() and HEADER["email"] in text, "the PDF's header does not carry what was typed"  # the name prints in capitals
    assert text.count(WORK_AUTHORIZATION) == 1 and any(WORK_AUTHORIZATION in line for line in text.split("\n")[1:4]), "the work authorization is in the header's contact line"
    ui.cpu_budget("Generate PDF (small home)", PDF_CPU_SECONDS, "typed", "saved")
    ui.wall_budget("Generate PDF (small home)", PDF_WALL_SECONDS, "typed", "saved")

    # Nothing stored in the browser: the six, and the work authorization wording.
    kept = ui.page.evaluate("() => JSON.stringify([Object.entries(window.localStorage), Object.entries(window.sessionStorage), document.cookie, window.location.href])")
    cookies = str(ui.page.context.cookies())
    for value in SENT.values():
        assert value not in kept and value not in cookies, f"the browser kept {value!r}"

    # Nothing stored on the server: not one file of its home holds a typed value, and the resumes folder is as it was.
    needles = [value.encode("utf-8") for value in SENT.values()]
    assert files_holding(scout_server.home, needles) == [], "the server's home holds what was typed into the PDF form"
    assert (sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []) == files_before

    # And the form forgets: after a reload every field is empty again, the work authorization line too.
    ui.reload()
    form.wait_for()
    assert form.locator("input").evaluate_all("(fields) => fields.map((field) => field.value)") == [""] * 7

    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def test_generate_pdf_stays_on_the_page_limit_and_shows_the_servers_note_when_it_cannot(ui, scout_server) -> None:
    """0.1.11.3 packet 7, in the browser: a header of four lines still downloads a PDF on the resume's page limit, with
    no note; and when the server says the resume cannot fit (`X-GigAI-Fit-Note`), the form shows that sentence.

    The small home's resume is a few lines, so the over-limit answer is the real response with the header laid over
    it (the size that overflowed, through the same route over HTTP: tests/behaviors/scout_find_jobs/test_pdf_fits_page_limit.py)."""

    from pypdf import PdfReader

    demo = scout_server.demo
    long_header = {**HEADER, "linkedin": "linkedin.example.test/in/zephyrine-quillfeather-clinical-applications", "link": "zquillfeather.example.test/portfolio/selected-work"}
    ui.goto("/#/pdf/" + quote(demo.hero_profile_id, safe="") + "/" + quote(demo.hero_job, safe=""))
    ui.page.locator('[data-role="generate-pdf-form"]').wait_for()
    for key, value in long_header.items():
        ui.page.fill(f"#generate-pdf-{key}", value)
    with ui.page.expect_download() as waiting:
        ui.page.locator('[data-role="generate-pdf"]').click()
    ui.page.locator('[data-role="pdf-saved"]').wait_for()
    pages = [page.extract_text() for page in PdfReader(io.BytesIO(Path(waiting.value.path()).read_bytes())).pages]
    assert len(pages) <= 2, f"Generate PDF made {len(pages)} pages"
    contact = [line for line in pages[0].splitlines()[:5] if "example.test" in line]
    assert len(contact) == 2, f"the contact line did not wrap to two: {pages[0].splitlines()[:5]}"
    assert ui.page.locator('[data-role="pdf-fit-note"]').count() == 0

    note = OVER_LIMIT_NOTE

    def over_limit(route) -> None:
        response = route.fetch()
        route.fulfill(response=response, headers={**response.headers, "X-GigAI-Fit-Note": note})

    ui.page.route("**/api/tailored-resumes/pdf", over_limit)
    with ui.page.expect_download():
        ui.page.locator('[data-role="generate-pdf"]').click()
    shown = ui.page.locator('[data-role="pdf-fit-note"]')
    shown.wait_for()
    # 0.1.11.3 item 15: on this page (a stored job's resume) the note carries the "Shorten automatically" button.
    assert (shown.text_content() or "").strip() == note + " Shorten automatically"
    ui.page.unroute("**/api/tailored-resumes/pdf")
    ui.assert_clean()


def test_shorten_automatically_asks_the_server_once_and_shows_its_sentence(ui, scout_server) -> None:
    """0.1.11.3 item 15, in the browser: when Generate PDF says the resume does not fit, the form offers "Shorten
    automatically" (never "edit it by hand"). The click is ONE `POST /api/job-resumes/pick` `{action: "shorten"}`,
    answered by the real server; the form shows what it left out in the server's own sentence, and a refusal in the
    page's own words by its code (never the server's text, which names commands).

    The small home's resume is a few lines. So the note is laid over the real PDF response (as in the test above),
    and the REAL answer to the click is the server's plain refusal for a resume that is short already; then the
    answer a long resume gets ("Left out 2 lines: ...") is laid over the real route to see the form show it and drop
    the note. The step itself, on a resume that really overflows, over HTTP on the real server and through the CLI:
    tests/behaviors/scout_find_jobs/test_pick_header_room.py."""

    demo = scout_server.demo
    ui.goto("/#/pdf/" + quote(demo.hero_profile_id, safe="") + "/" + quote(demo.hero_job, safe=""))
    ui.page.locator('[data-role="generate-pdf-form"]').wait_for()
    ui.page.fill("#generate-pdf-name", HEADER["name"])
    assert ui.page.locator('[data-action="shorten-resume"]').count() == 0, "the button is offered only with the note"

    def over_limit(route) -> None:
        response = route.fetch()
        route.fulfill(response=response, headers={**response.headers, "X-GigAI-Fit-Note": OVER_LIMIT_NOTE})

    ui.page.route("**/api/tailored-resumes/pdf", over_limit)
    with ui.page.expect_download():
        ui.page.locator('[data-role="generate-pdf"]').click()
    button = ui.page.locator('[data-role="pdf-fit-note"] [data-action="shorten-resume"]')
    button.wait_for()
    assert (button.text_content() or "").strip() == "Shorten automatically"
    assert "by hand" not in (ui.page.locator('[data-role="pdf-fit-note"]').text_content() or "")

    # The real server's answer for THIS resume: it is short already, said in plain words; nothing is changed.
    sent: list[dict] = []
    ui.page.on("request", lambda request: sent.append(request.post_data_json) if request.url.endswith("/api/job-resumes/pick") else None)
    with ui.page.expect_response("**/api/job-resumes/pick") as answered:
        button.click()
    assert answered.value.status == 409 and answered.value.json()["error"]["code"] == "resume_short_already"
    assert sent == [{"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "shorten"}]
    refused = ui.page.locator('[data-role="pdf-shorten-error"]')
    refused.wait_for()
    said = (refused.text_content() or "").strip()
    assert said == "The resume was not shortened. This resume already fits its pages with most of a page to spare, so nothing was left out."
    assert not any(word in said for word in ("resume_short_already", "pick.", "settle", "Traceback"))

    # A resume that was shortened: the server's sentence is shown as it is, and the note is gone until the next PDF.
    message = 'Left out 2 lines: "Ran the weekly on-call review."; "Wrote the runbook for the billing export.". Generate the PDF again.'

    def shortened(route) -> None:
        route.fulfill(status=200, json={"action": "shorten", "shortened": {"left_out": ["a", "b"], "must_have_cut": False, "waiting": False, "message": message}})

    ui.page.route("**/api/job-resumes/pick", shortened)
    button.click()
    done = ui.page.locator('[data-role="pdf-shortened"]')
    done.wait_for()
    assert (done.text_content() or "").strip() == message
    assert ui.page.locator('[data-role="pdf-fit-note"]').count() == 0 and ui.page.locator('[data-role="pdf-shorten-error"]').count() == 0
    ui.page.unroute("**/api/job-resumes/pick")
    ui.page.unroute("**/api/tailored-resumes/pdf")
    # The one refusal above (asked for) is the only thing the browser saw go wrong: it is taken off the list.
    assert ui.problems() and all("409" in problem for problem in ui.problems()), ui.problems()
    ui.network.console_errors.clear()
    ui.network.http_errors.clear()
    ui.assert_clean()
