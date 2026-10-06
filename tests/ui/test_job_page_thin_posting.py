"""0.1.11.2 THIN: the job page of a match read from fewer than 4 requirement rows says "thin posting", never "Matched".

Real server and page (the small home, no model call, no network). The small home's hero job is a match; its stored
assessment is served with its matrix cut to its first 2 rows and the 0.1.11 note a thin answer was stored with
("Only 2 requirements were read ..."), which is what an assessment already on disk looks like.

- the header's ONE chip reads "Thin posting: too few requirements to judge" (warn, `data-fit="thin_posting"`), not
  "Matched";
- near the requirements there is ONE line, the thin one; the old "Only 2 requirements were read" note is not beside it;
- with 4 rows the same job reads "Matched" and has no such line;
- the real server's Jobs rows each say `thin_posting` (true or false), and a matched row of fewer than 4 requirements
  says the thin label in its score column, never "Matched" or "fit 100%".
"""

from __future__ import annotations

import copy
import json
from urllib.parse import quote

import pytest

from tests.ui.evidence import shot

pytestmark = pytest.mark.ui

PAGE = ".job-page"
CHIP = f'{PAGE} [data-role="job-chip"]'
NOTE = f'{PAGE} [data-role="requirements-note"]'
LABEL = "Thin posting: too few requirements to judge"
OLD_NOTE = "Only 2 requirements were read from this posting. Open the posting to check."


def _lay(ui, job: str, rows: int) -> None:
    """The job's stored assessment, served as a match of ``rows`` met rows (the rest of the item is the server's own)."""

    def assessments(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        for item in body.get("items", []):
            if item.get("job", {}).get("job_identity") != job:
                continue
            first = item["result"]["matrix"][0]
            matrix = []
            for place in range(rows):
                row = copy.deepcopy(first)
                row.update({"requirement": f"Synthetic requirement {place + 1}", "status": "met"})
                matrix.append(row)
            item["result"].update({"verdict": "matched_above_threshold", "matrix": matrix, "questions": [], "structured_questions": []})
            if rows < 4:
                item["requirements_note"] = OLD_NOTE
            else:
                item.pop("requirements_note", None)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.goto("about:blank")
    ui.page.unroute_all()
    ui.page.route("**/api/assessments*", assessments)


def _open(ui, job: str) -> None:
    ui.goto("/#/jobs/" + quote(job, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(CHIP).wait_for()
    ui.settle()


def test_a_match_on_two_requirements_reads_thin_posting_on_the_job_page_and_four_reads_matched(ui, scout_server) -> None:
    job = scout_server.demo.hero_job

    _lay(ui, job, 2)
    _open(ui, job)
    chip = ui.page.locator(CHIP)
    assert (chip.get_attribute("data-fit"), (chip.text_content() or "").strip()) == ("thin_posting", LABEL)
    assert "fit-thin_posting" in (chip.get_attribute("class") or "")
    note = ui.page.locator(NOTE)
    assert note.count() == 1 and (note.text_content() or "").strip() == f"{LABEL} (2 read). Open the posting to check."
    words = ui.page.locator(PAGE).inner_text()
    assert "Only 2 requirements were read" not in words, "the old note is not shown beside the thin line"
    assert "Matched" not in ui.page.locator(f"{PAGE} .job-header").inner_text()
    shot(ui, "job-page-thin-posting")

    _lay(ui, job, 4)
    _open(ui, job)
    chip = ui.page.locator(CHIP)
    assert chip.get_attribute("data-fit") == "matched" and (chip.text_content() or "").strip().startswith("Matched")
    assert ui.page.locator(NOTE).count() == 0
    ui.page.unroute_all()

    # The real server's rows: every row says the field; a match of fewer than 4 requirements never says "Matched".
    rows = ui.server_json("/api/postings?limit=200")["postings"]["rows"]
    assert rows and all(type(row.get("thin_posting")) is bool for row in rows)
    for row in rows:
        requirements = (row.get("assessment") or {}).get("requirements") or 0
        thin = row["state"] == "thin_posting" or (row["state"] == "matched" and requirements < 4)
        assert row["thin_posting"] is thin, row["score_text"]
        if thin:
            assert row["score_text"].startswith("thin posting: too few requirements to judge"), row["score_text"]
            assert "Matched" not in row["score_text"] and "fit 100%" not in row["score_text"]
    ui.assert_clean()
