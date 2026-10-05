"""0110-10-13: the job page says what one assessment sends, beside its Assess / Re-assess actions.

Real server, nothing stubbed, no model call, nothing changed. An agent's runtime refused to run the operator's
re-assessment because nothing said what the command would send and where; the same is true of a person about to click
Re-assess. The page now says it in one line (the stored posting, the resume with contact lines removed by pattern, the
search preferences, saved answers and matching stories, and the model target), and the line opens to this job's own
facts from the no-call preview (`POST /api/postings/assess` without `approve`: `model_input_summary`).

Pinned:
- the line is on the job page, names the configured model target, and loading the page asks the server for nothing
  more because of it (no write at all: the preview is asked for only when the line is opened);
- opened, it is ONE `POST /api/postings/assess` and the page shows the profile with where its resume comes from, the
  saved answers and stories, and whether the posting is fetched first, as the server's summary says them;
- no assessment was made: the recorded assess calls are what they were, and the job's assessment date is unchanged.

MEASURED (14-core laptop, 2026-10-04): the line opened to its facts in 0.1 to 0.3 s.
"""

from __future__ import annotations

import pytest

from tests.ui.support import INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

OPEN_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
SENDS = '.job-page [data-role="assess-sends"]'
SOURCE_WORDS = {
    "master_evidence": "the lines of your master resume picked for this posting",
    "profile_view": "this profile's own resume",
}


def _assess_calls(ui) -> int:
    return sum(item["calls"] for item in ui.server_json("/api/metrics?kind=assess")["aggregates"])


def test_the_job_page_says_what_one_assessment_sends_and_opens_to_the_no_call_preview(ui) -> None:
    rows = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    job = next(row for row in rows if row["state"] == "matched" and not row["tailored"])
    target = ui.server_json("/api/config")["config"]["default_model_target"]
    calls = _assess_calls(ui)

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("listed")
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']", has_text=job["title"]).first.click()
    ui.wait_for_job_page()
    line = ui.page.locator(f"{SENDS} summary")
    # The model target is the page's own read of the settings: wait for it, not for the line alone.
    ui.page.locator(f"{SENDS} summary", has_text="to your model target (").wait_for()
    ui.settle()
    ui.step("shown")
    text = line.text_content() or ""
    assert text.startswith("What one assessment sends to your model target ("), text
    assert text.endswith(
        ": the stored posting, your resume with its contact lines removed by pattern (which can miss an unusual name or contact "
        "format), your search preferences, your saved answers and the stories that match."
    ), text
    assert ("your own login" in text) is (target in ("codex_cli", "claude_cli")), text
    assert ui.page.locator(SENDS).count() == 1
    # The line alone asks the server for nothing: no preview is fetched until it is opened, and nothing is written.
    assert ui.requests_between("listed", "shown", "/api/postings/assess") == 0
    assert ui.writes_after("start") == []

    line.click()
    facts = ui.page.locator(f"{SENDS} div")
    facts.first.wait_for()
    ui.step("opened")
    ui.settle()
    assert ui.writes_after("shown") == ["POST /api/postings/assess"]  # one request, and it is the no-call preview
    summary = ui.server_json("/api/postings/assess", {"jobs": [job["job_identity"]], "again": True, "profile_id": job["profile_id"]})["model_input_summary"]
    (profile,) = summary["profiles"]
    assert facts.all_text_contents() == [
        f"Profile {profile['label']}: {SOURCE_WORDS[profile['resume_source']]}.",
        f"Saved answers: {summary['answers_saved'] if summary['answers_used'] else 'none'}. "
        f"Saved stories that can match: {summary['stories_saved'] if summary['stories_used'] else 'none'}.",
        "The posting's text is not stored: it is fetched from its public board first." if summary["public_fetch_needed"]
        else "The posting's text is stored: nothing is fetched.",
    ]
    ui.wall_budget("the line opens to this job's facts (small home)", OPEN_WALL_SECONDS, "shown", "opened")

    # Nothing was assessed: no model call was recorded and the job's assessment is the one it had.
    assert _assess_calls(ui) == calls
    after = next(row for row in ui.server_json("/api/postings?limit=50")["postings"]["rows"] if row["job_identity"] == job["job_identity"])
    assert after["assessment"]["assessed_at"] == job["assessment"]["assessed_at"]
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
