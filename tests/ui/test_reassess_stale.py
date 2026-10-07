"""0110-10-12: an old assessment is renewed from its job page, and the page then shows the NEW assessment's date.

Real server, nothing stubbed, on the fixture model. After an upgrade every assessment is "old" (made with an older
prompt). The operator's job read "Matched (old assessment: older prompt)" and "Stale: older settings" on its Jobs row,
its state was "Resume tailored", it had no open question, and the job page's Re-assess was off: "There are no open
questions, so there is nothing new to re-assess with." The page that marked the assessment old could not renew it.

An older PROMPT cannot be made on a synthetic home, so the flow makes the same shape with the other reason the server
has for "the whole profile's assessments are old": a setting the assessment reads changes (`PUT /api/setup`, the user
now needs sponsorship), which is what Settings does. The job is the small home's tailored one: its state is "Resume
tailored", so the server's `job_state` carries no stale marker and only the stored item says `basis_stale`.

This flow CHANGES the shared home (a setting, one assessment), so it runs late (`UI_ORDER`); the setting is put back
and the job assessed again under it in a `finally`. The changed setting also re-opens the pipeline steps of the jobs
the pipeline has processed (one runs by itself, the rest wait for an approval): the flow waits for that to settle and
leaves it as it is.

Pinned:
- the Jobs row says the assessment is old ONCE, in the server's words (`stale_label`, in the score column): no second
  chip in other words;
- the job page says the one reason; there is ONE Re-assess, it is ON with no question open and nothing typed, and it
  says why and what it costs; the page has written nothing so far;
- Re-assess is ONE `POST /api/assess` for this job, and no answer is written;
- then, without a reload: the note is gone, the assessment's date is the NEW one (`updated_at`, not the first
  assessment's `created_at`), Re-assess is off again (a current assessment, no open question); the Scout label and the
  stored resume, both made before the new assessment, say so instead of reading as its own;
- the server agrees, the Jobs row no longer says "old assessment", and a reload shows the same.

MEASURED (14-core laptop, 2026-10-04, three runs): the re-assessment 0.3 to 0.6 s wall on the fixture model, 0.2 to 0.4
server CPU seconds.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote, urlsplit

import pytest

from tests.ui.support import INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui
UI_ORDER = 80  # changes the shared home (a setting, an assessment): after every flow but the profile's delete

OPEN_JOB_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
REASSESS_WALL_SECONDS = 15.0  # one model call on the fixture model: 0.3 to 0.6 s measured
REASSESS_CPU_SECONDS = 10.0  # 0.2 to 0.4 measured; the background threads share the process
#: How long the flow waits for the background pipeline the changed setting queued (a patience, not a budget).
PIPELINE_PATIENCE_MS = 90_000
OLD = "old assessment"
REASON = "settings changed"

PAGE = ".job-page"
NOTE = f'{PAGE} [data-role="assessment-stale"]'
REASSESS = f'{PAGE} [data-action="reassess"]'
HELP = f'{PAGE} [data-help="reassess"]'
ASSESSED_AT = f'{PAGE} [data-role="assessed-at"]'
LABEL = f"{PAGE} {tid('step-timeline')} {tid('scout-label-chip')}"
RESUME_STALE = f'{PAGE} #job-resume [data-role="resume-stale"]'
SETTLED_TIMELINE = f"{PAGE} {tid('step-timeline')}" + "".join(f':not([data-state="{state}"])' for state in ("not_started", "running", "waiting"))

def _changes(ui, step: str) -> list[str]:
    """What the page wrote after a step, without the preview's render.

    0.1.11.5: the job page opens on Preview ALWAYS (a resume with a changed line opened on "Show changes"), and the
    preview is rendered again when the stored resume changes. A render stores nothing."""

    return [write for write in ui.writes_after(step) if write != "POST /api/tailored-resumes/preview"]



def stored_assessment(ui, job: dict) -> dict:
    """The job as the server holds it: its newest assessment, its state, its tailored resume."""

    body = ui.server_json(f"/api/jobs?url={quote(job['job_identity'], safe='')}")
    return {"assessment": body["assessments"][0], "state": body["job_state"], "tailored": body["tailored_resumes"]}


def row_of(ui, job: dict) -> dict:
    rows = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    return next(row for row in rows if row["job_identity"] == job["job_identity"])


def assess_again(ui, job: dict) -> None:
    """The job's assessment made again through the API (the home's own "assess these"), as the demo home does."""

    asked = ui.server_json("/api/postings/assess", {"jobs": [job["job_identity"]], "profile_id": job["profile_id"], "again": True})
    if asked.get("status") == "ask":
        ui.server_json("/api/postings/assess", asked["question"]["yes"]["api"]["body"])


@contextmanager
def old_assessments(ui, *, renewed: bool = False) -> Iterator[tuple[dict, dict, dict]]:
    """The small home's tailored job with its assessment made OLD (a setting changed): (the job, its row now, the job as stored).

    The setting is put back at the end, which makes the assessments current again. A flow that re-assessed the job
    meanwhile (`renewed`) made its assessment under the changed setting: that one is made again under the old one.
    """

    rows = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    job = next(row for row in rows if row["tailored"] and row["state"] == "matched" and not row["open_questions"])
    assert job["stale_reason"] is None, "the small home's tailored job starts with a current assessment"
    prefs = {key: value for key, value in ui.server_json("/api/setup")["prefs"].items() if key != "schema_version"}
    try:
        # A setting the assessment reads changes: every assessment of the profile is old now.
        ui.server_json("/api/setup", {**prefs, "visa_sponsorship_required": not prefs["visa_sponsorship_required"]}, method="PUT")
        row, stored = row_of(ui, job), stored_assessment(ui, job)
        assert (row["stale_reason"], row["stale_label"]) == ("settings_changed", f"{OLD}: {REASON}")
        assert (stored["assessment"]["basis_stale"], stored["assessment"]["basis_stale_reason"]) == (True, "settings_changed")
        # The operator's shape: the state is the tailored resume's, not the verdict's, and no question is open.
        assert stored["state"]["state"] == "tailored" and row["open_questions"] == []
        yield job, row, stored
    finally:
        ui.server_json("/api/setup", prefs, method="PUT")
        if renewed:
            assess_again(ui, job)


def open_from_the_list(ui, job: dict):
    """Jobs, settled; returns the job's row on the page."""

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("listed")
    return ui.page.locator(tid("job-row"), has=ui.page.locator("[data-action='open-job']", has_text=job["title"])).first


def test_a_jobs_row_says_an_assessment_is_old_once(ui) -> None:
    with old_assessments(ui) as (job, row, _stored):
        listed = open_from_the_list(ui, job)
        score = listed.locator('[data-role="score"]').text_content() or ""
        whole = (listed.text_content() or "").lower()
        # ONE label, the server's own words, in the score column: no chip beside it saying it again in other words.
        assert score.count(row["stale_label"]) == 1, score
        assert listed.locator('[data-kind="stale"]').count() == 0, f"a second label beside the score column's: {whole!r}"
        assert whole.count(OLD) == 1 and "stale" not in whole and "older settings" not in whole, whole
        assert _changes(ui, "start") == []
        ui.assert_clean()


def test_re_assess_an_old_assessment_from_its_job_page(ui) -> None:
    with old_assessments(ui, renewed=True) as (job, _row, before):
        old_at = before["assessment"]["updated_at"]
        listed = open_from_the_list(ui, job)

        # Its job page: ONE Re-assess, and it is ON, with why and what it costs; the one reason is said in the header.
        listed.locator("[data-action='open-job']").click()
        ui.wait_for_job_page()
        ui.step("opened")
        ui.wall_budget("open a job with an old assessment (small home)", OPEN_JOB_WALL_SECONDS, "listed", "opened")
        # The changed setting re-opened this job's pipeline steps (the Scout label is made again, from the old
        # assessment): the page follows that by its own polling. Waited for here, so that nothing of the pipeline's is
        # still to come: the timeline is read and neither running nor queued (done, or waiting for an approval).
        ui.page.locator(SETTLED_TIMELINE).wait_for(timeout=PIPELINE_PATIENCE_MS)
        assert ui.page.locator(f"{PAGE} .job-title").text_content() == job["title"]
        assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") == "tailored"
        assert ui.page.locator(f'{PAGE} [data-role="questions-section"]').count() == 0, "the job has no open question"
        reassess = ui.page.locator(REASSESS)
        assert reassess.count() == 1, "one Re-assess on the page"
        assert reassess.is_enabled(), f"Re-assess is off for an old assessment: {ui.page.locator(HELP).text_content()!r}"
        said = ui.page.locator(HELP).text_content() or ""
        assert f"old ({REASON})" in said and "one model call" in said, said
        assert ui.page.locator(NOTE).count() == 1 and ui.page.locator(NOTE).get_attribute("data-reason") == REASON
        # 0.1.11: the stale label sits beside the header's chip, in the Jobs row's words.
        assert (ui.page.locator(NOTE).text_content() or "").strip() == f"old assessment: {REASON}"
        # The resume panel says the same, and offers the ONE refresh that is allowed for an old assessment, with its
        # cost in the button: never a re-pick (a new selection never sits beside scores made on other evidence).
        stale = ui.page.locator(RESUME_STALE)
        assert stale.locator("li").first.get_attribute("data-stale") == f"assessment_stale:{REASON.replace(' ', '_')}"
        assert f"old assessment: {REASON}" in (stale.text_content() or "")
        assert [(button.get_attribute("data-action"), (button.text_content() or "").strip()) for button in stale.locator("button").all()] == [("reassess-stale", "Re-assess · 1 model call")]
        assert ui.page.locator(ASSESSED_AT).get_attribute("data-at") == old_at
        ui.settle()
        assert _changes(ui, "start") == [], "the page only read so far"

        # Re-assess: ONE request, for this job; no answer is written.
        ui.step("ready")
        with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/assess", timeout=60_000) as answered:
            reassess.click()
        ui.page.locator(NOTE).wait_for(state="detached", timeout=60_000)
        ui.step("reassessed")
        made = answered.value.json()
        assert answered.value.status in (200, 201)
        assert answered.value.request.post_data_json == {"job": {"job_url": job["job_url"]}, "origin": "job_page"}
        assert _changes(ui, "ready") == ["POST /api/assess"]
        assert made["basis_stale"] is False and made["updated_at"] > old_at and made["created_at"] < made["updated_at"]
        ui.cpu_budget("re-assess an old assessment (fixture model)", REASSESS_CPU_SECONDS, "ready", "reassessed")
        ui.wall_budget("re-assess an old assessment (fixture model)", REASSESS_WALL_SECONDS, "ready", "reassessed")

        # Without a reload: the NEW assessment's date, no "old" note, and Re-assess is off again (nothing to renew).
        new_at = made["updated_at"]
        assert ui.page.locator(ASSESSED_AT).get_attribute("data-at") == new_at, "the header still shows the first assessment's date"
        assert ui.page.locator(ASSESSED_AT).get_attribute("data-at") != made["created_at"]
        assert ui.page.locator(NOTE).count() == 0
        assert reassess.is_disabled() and "no open questions" in (ui.page.locator(HELP).text_content() or "")
        assert "#/jobs/" in ui.page.url
        # What was made before the new assessment says so: the Scout label, and the tailored resume.
        after = stored_assessment(ui, job)
        pipeline = ui.server_json(f"/api/pipeline/job?job_identity={quote(job['job_identity'], safe='')}&profile_id={job['profile_id']}")
        assert pipeline["label"]["updated_at"] < new_at, "the small home's label was made before this re-assessment"
        label = ui.page.locator(LABEL)
        assert label.count() == 1
        if not any(step["name"] in ("assess", "pick") for step in pipeline["steps"]):
            # This tree's server still runs the 0.1.10 pipeline (until packet N4: none of its steps is one of the
            # four of 0.1.11): its label says THAT, which is the stronger statement of the same thing (it is not the
            # assessment shown's), and offers "Check again".
            assert label.get_attribute("data-legacy") == "true" and "made on 0.1.10's tailored resume" in (label.text_content() or "")
        else:
            assert label.get_attribute("data-older") == "true", "a label made before the new assessment reads as its own"
            assert "from before the latest assessment" in (label.text_content() or "")
        tailored_at = max(item["updated_at"] for item in after["tailored"])
        stale = ui.page.locator(RESUME_STALE)
        assert tailored_at < new_at and stale.locator('li[data-stale="assessment_newer"]').count() == 1
        assert "this resume was made before the latest assessment" in (stale.text_content() or "")

        # The server agrees, and the Jobs row no longer says "old".
        assert (after["assessment"]["updated_at"], after["assessment"]["basis_stale"]) == (new_at, False)
        renewed = row_of(ui, job)
        assert renewed["stale_reason"] is None and OLD not in renewed["score_text"] and renewed["assessment"]["assessed_at"] == new_at

        # A reload shows the same.
        ui.reload()
        ui.wait_for_job_page()
        ui.page.locator(ASSESSED_AT).wait_for()
        ui.page.locator(LABEL).wait_for()
        ui.settle()
        assert ui.page.locator(ASSESSED_AT).get_attribute("data-at") == new_at
        assert ui.page.locator(NOTE).count() == 0 and ui.page.locator(REASSESS).is_disabled()
        legacy = not any(step["name"] in ("assess", "pick") for step in pipeline["steps"])  # as above: this tree's pipeline is the 0.1.10 one
        assert ui.page.locator(LABEL).get_attribute("data-legacy" if legacy else "data-older") == "true"
        assert ui.page.locator(f'{RESUME_STALE} li[data-stale="assessment_newer"]').count() == 1
        assert _changes(ui, "reassessed") == []
        ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
