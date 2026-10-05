"""0110-10-05 on the job page: the Tailored resume panel's resumes-folder line, and "Cut for length" with Restore.

The hero job of the small home has a tailored resume the background pipeline made (real server, fixture model).

First test, nothing stubbed: the panel says where this job's markdown is in the resumes folder, as the user types
the path, and that file exists and holds the tailored markdown; a resume that fits says nothing about length.

Second test: the fixture model's tailored resume is three lines, and a cut needs a resume that prints on three
pages (the real rule, store and route are proven on such a resume in
`tests/api_e2e/test_tailored_resume_length_journey.py` and `test_tailor_part_c_outcomes.py`). So the browser talks
to the REAL server for everything except the stored resume: `GET /api/tailored-resumes` is the real answer with a
length record added (two roles cut, three bullets of an older role trimmed; the record is checked against the
product's own parser, `LengthFit.from_json`), and `PUT /api/tailored-resumes/length` is answered as the route
answers (the same resume, `status` flipped, `updated_at` unchanged). The panel, its line and its button are real.

Pinned: the callout says what was left out ("Cut for length (3 pages to 2): <roles>; N older bullets (...)") and
offers Restore; Restore is ONE request (`use: restore`, the `updated_at` the page read) and the line becomes "Put
back (was cut for length): ..." with "Cut for length again"; that is one request too (`use: cut`) and the first line
is back. Zero console errors.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): the job page with its panel 0.61 to
0.65 s wall from a new page, 0.42 to 0.45 server CPU seconds; Restore 0.13 s.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

import pytest

from gigai.scout.tailor_length import LengthFit
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

PANEL = "#job-resume"
JOB_PAGE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
JOB_PAGE_CPU_SECONDS = 3.0  # 0.42 to 0.45 measured idle (the app's first load included), up to 0.63 with every core busy
LENGTH_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
CUT_ROLES = ["Junior Developer — Bellweather Retail (2009–2011)", "Junior Developer — Dunmore Telecom (2007–2009)"]
TRIMMED_ROLE = "Software Engineer — Mossbank Analytics (2011–2014)"


def _line(number: int, text: str) -> dict:
    return {"kind": "copy", "text": text, "refs": [{"kind": "resume", "line": number, "text": text}], "id": f"L{number}", "origin": "model"}


def length_record(status: str) -> dict:
    """A length record as `tailor_length.LengthFit.to_json` writes it: two roles cut, three bullets of one role trimmed."""

    cut = []
    for position, role in enumerate(CUT_ROLES, start=6):
        number = 100 + position * 10
        cut.append({"position": position, "role": role, "entry": {"heading": [_line(number, f"### {role}")], "bullets": [_line(number + 1, "- Built and maintained internal tools.")]}})
    trimmed = [{"heading": "L90", "role": TRIMMED_ROLE, "bullets": [_line(91 + n, f"- Older bullet {n + 1} of this role.") for n in range(3)], "records": []}]
    record = {"max_pages": 2, "pages": 2, "full_pages": 3, "status": status, "cut": cut, "trimmed": trimmed}
    assert LengthFit.from_json(json.loads(json.dumps(record))).to_json()["status"] == status  # a record the product itself reads
    return record


def open_hero(ui, demo) -> None:
    ui.goto("/#/jobs/" + quote(demo.hero_job, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()


def test_the_panel_says_where_the_resume_is_in_the_resumes_folder(ui, scout_server) -> None:
    demo = scout_server.demo
    key = f"profile_id={quote(demo.hero_profile_id, safe='')}&job_identity={quote(demo.hero_job, safe='')}"
    folder = ui.server_json(f"/api/resumes-folder?{key}")
    stored = ui.server_json(f"/api/tailored-resumes?{key}")["items"][0]
    assert folder["files"]["markdown"], "the small home's hero job has its markdown in the resumes folder"

    open_hero(ui, demo)
    line = ui.page.locator(f"{PANEL} {tid('resumes-folder-file')}")
    line.wait_for()
    ui.step("shown")
    shown = f"{folder['shown'].rstrip('/')}/{folder['files']['markdown']}"
    assert (line.text_content() or "").strip() == f"In your resumes folder: {shown}"
    assert shown.startswith("~/") and line.locator("code").text_content() == shown
    # The file the line names is there, and it is this job's tailored markdown.
    on_disk = Path(folder["path"]) / folder["files"]["markdown"]
    # (the folder's copy is the clean one: the store's markdown without its `<!-- R1 -->` source marks)
    assert on_disk.is_file() and on_disk.read_text(encoding="utf-8") == re.sub(r" <!-- [^>]*-->", "", stored["markdown"])
    assert on_disk.resolve().is_relative_to(scout_server.home), "the resumes folder is outside the temporary home"

    # A resume that fits says nothing about length; the panel is a stored resume with its change summary.
    assert ui.page.locator(f"{PANEL} {tid('length-note')}").count() == 0
    assert (ui.page.locator(f"{PANEL} {tid('change-summary')}").text_content() or "").strip()
    ui.settle()
    assert ui.requests_after("start", "/api/resumes-folder") == 1 and ui.requests_after("start", "/api/tailored-resumes") == 1
    assert ui.writes_after("start") == []
    ui.cpu_budget("job page with its tailored resume, cold page (small home)", JOB_PAGE_CPU_SECONDS, "start", "shown")
    ui.wall_budget("job page with its tailored resume, cold page (small home)", JOB_PAGE_WALL_SECONDS, "start", "shown")
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


class StoredWithACut:
    """Answers the two tailored-resume calls: the real stored resume with a length record, and the length action."""

    def __init__(self) -> None:
        self.stored: dict | None = None
        self.puts: list[dict] = []

    def __call__(self, route) -> None:
        request = route.request
        path = urlsplit(request.url).path
        if path == "/api/tailored-resumes" and request.method == "GET":
            if self.stored is None:
                self.stored = route.fetch().json()  # the real server's own answer, once
                self.stored["items"][0]["result"]["length"] = length_record("cut")
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.stored))
            return
        if path == "/api/tailored-resumes/length" and request.method == "PUT" and self.stored is not None:
            body = json.loads(request.post_data or "{}")
            self.puts.append(body)
            item = copy.deepcopy(self.stored["items"][0])
            item["result"]["length"] = length_record("restored" if body.get("use") == "restore" else "cut")
            self.stored["items"][0] = item
            route.fulfill(status=200, content_type="application/json", body=json.dumps(item))
            return
        route.continue_()


def test_cut_for_length_is_said_and_restore_puts_it_back(ui, scout_server) -> None:
    demo = scout_server.demo
    server = StoredWithACut()
    ui.page.route("**/api/tailored-resumes*", server)
    ui.page.route("**/api/tailored-resumes/length", server)

    open_hero(ui, demo)
    note = ui.page.locator(f"{PANEL} {tid('length-note')}")
    note.wait_for()
    stamp = server.stored["items"][0]["updated_at"]  # type: ignore[index]

    # What was left out, in one line, and the one way back.
    assert note.get_attribute("data-status") == "cut"
    assert (note.locator("span").first.text_content() or "") == f"Cut for length (3 pages to 2): {CUT_ROLES[0]}; {CUT_ROLES[1]}; 3 older bullets (3 of {TRIMMED_ROLE})."
    restore = note.locator('[data-action="length-restore"]')
    assert restore.text_content() == "Restore" and note.locator("button").count() == 1

    # Restore: one request with the stamp the page read, and the line says it is back.
    ui.step("before-restore")
    restore.click()
    ui.page.locator(f"{PANEL} {tid('length-note')}[data-status='restored']").wait_for()
    ui.step("restored")
    key = {"profile_id": demo.hero_profile_id, "job_identity": demo.hero_job, "updated_at": stamp}
    assert server.puts == [{**key, "use": "restore"}]
    assert ui.writes_after("before-restore") == ["PUT /api/tailored-resumes/length"]
    said = note.locator("span").first.text_content() or ""
    assert said == f"Put back (was cut for length): {CUT_ROLES[0]}; {CUT_ROLES[1]}; 3 older bullets (3 of {TRIMMED_ROLE}). The resume is now 3 pages, over the 2-page limit."
    again = note.locator('[data-action="length-cut"]')
    assert again.text_content() == "Cut for length again"
    ui.wall_budget("Restore what was cut for length", LENGTH_WALL_SECONDS, "before-restore", "restored")

    # Cut for length again: one more request, and the first line is back.
    again.click()
    ui.page.locator(f"{PANEL} {tid('length-note')}[data-status='cut']").wait_for()
    assert server.puts[1:] == [{**key, "use": "cut"}]
    assert (note.locator("span").first.text_content() or "").startswith("Cut for length (3 pages to 2): ")
    assert ui.page.locator(f"{PANEL} [data-role='choice-error']").count() == 0
    ui.assert_clean()
