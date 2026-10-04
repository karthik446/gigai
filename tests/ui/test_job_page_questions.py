"""Flow 5 (REPORT.md 5.3): the job page. Its questions, answer one, and the pipeline timeline runs.

Real server, nothing stubbed, on the fixture model. The small home has postings that wait for the user's answer
(assessed before the GCP answer existed). The flow opens one from the "Needs your answers" chip, answers its
question in the box and re-assesses; the answer is saved, the assessment comes back with every requirement met, and
the background pipeline takes the job by itself (the home lets one job of a trigger run without an approval): tailor,
assess the tailored resume, Scout ATS, Scout label.

This flow CHANGES the shared home (an answer, an assessment, a tailored resume), so it runs late (`UI_ORDER`).

Pinned: the page is the job (title, state "Needs your answers", the questions section with the server's question and
the requirement it is about); Re-assess is ONE `POST /api/answers` carrying the typed answer and this job; after it
the questions section is gone and the state leaves "Needs your answers", without a reload; the timeline reaches
"done" with the Scout label and Scout ATS chips by the page's own polling; the server holds the typed answer as the
user's ("Written by you"), and its row for the job agrees; after a reload the page shows the tailored resume the
pipeline stored, its resumes-folder line and the state "Resume tailored". Zero console errors.

KNOWN BUG (found by this flow, not fixed here): WITHOUT that reload the page never shows the tailored resume the
pipeline stored. Every check above is made first; the test then reports itself as an expected failure (xfail) with
that reason, and passes by itself once the page follows the pipeline.

MEASURED (14-core laptop, 2026-10-04, four runs: Python 3.11 three times, 3.13 once): the job page 0.09 to 0.10 s;
the re-assessment 3.4 to 4.3 s wall, 1.6 to 2.3 server CPU seconds (the answer's journal write, one fixture-model
call, and the pipeline's first steps beside them); the pipeline done 3.8 to 6.8 s after that (four steps, polled
every 3 s).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui
UI_ORDER = 20  # changes the shared home: after the flows that only read it

ANSWER = "Yes. I ran production workloads on Google Cloud for two years: GKE, Cloud SQL and Pub/Sub."
JOB_PAGE_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
REASSESS_WALL_SECONDS = 15.0  # a journal write and a model call on the fixture model: 3.4 to 4.3 s measured
REASSESS_CPU_SECONDS = 10.0  # 1.6 to 2.3 measured: the journal write, the assessment and the pipeline's first steps share the process
PIPELINE_WALL_SECONDS = 45.0  # four background steps and a 3 s poll: 3.8 to 6.8 s measured
#: How long the flow waits for the background pipeline (a patience: the budget is PIPELINE_WALL_SECONDS).
PIPELINE_PATIENCE_MS = 90_000
#: KNOWN, found by this flow: the page asks GET /api/answers/match up to four times for ONE open question while it
#: settles (once per render that changes what is on record). 1 is the target; the ceiling keeps it from getting worse.
MATCH_REQUESTS = 4
#: KNOWN BUG, found by this flow (0.1.10.9 U3 report): everything above it passed when this is the outcome.
KNOWN_STALE_PANEL = (
    "the job page does not show the tailored resume the background pipeline just stored: the timeline says the tailor "
    "step is done, but the Tailored resume panel, 'Tailor again' and the state 'Resume tailored' appear only after a "
    "reload (the page reads GET /api/tailored-resumes once, when the job opens)"
)


def test_answer_a_question_on_the_job_page_and_the_pipeline_runs(ui) -> None:
    waiting = ui.server_json("/api/postings?state=needs_answers&limit=50")["postings"]["rows"]
    assert waiting, "the small home has a posting that waits for the user's answer"
    job = waiting[0]
    question = job["open_questions"][0]
    profile_id = job["profile_id"]

    ui.goto("/#/jobs?state=needs_answers")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("listed")
    assert job["title"] in ui.job_titles()
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']", has_text=job["title"]).first.click()
    ui.wait_for_job_page()
    section = ui.page.locator('.job-page [data-role="questions-section"]')
    section.wait_for()
    ui.step("opened")
    ui.wall_budget("open a job that has questions (small home)", JOB_PAGE_WALL_SECONDS, "listed", "opened")

    # The job, its state and its one question, as the server has them.
    assert ui.page.locator(".job-page .job-title").text_content() == job["title"]
    assert ui.page.locator('.job-page [data-role="job-state"]').get_attribute("data-state") == "needs_answers"
    assert section.locator("h3").text_content() == f"Questions for you ({len(job['open_questions'])})"
    box = section.locator(f'.row-question[data-question-id="{question["question_id"]}"]')
    assert box.locator("label").text_content() == question["question"]
    assert ui.page.locator('.job-page [data-role="requirements-section"] table.matrix-table tbody tr').count() >= 2
    ui.settle()
    assert ui.writes_after("start") == []
    assert ui.requests_after("listed", "/api/answers/match") <= MATCH_REQUESTS * len(job["open_questions"])

    # Answer it and re-assess: one request, with the typed answer and this job.
    box.locator("input").fill(ANSWER)
    reassess = section.locator('[data-action="reassess"]')
    assert reassess.is_enabled()
    ui.step("typed")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/answers", timeout=60_000) as saved:
        reassess.click()
    section.wait_for(state="detached", timeout=60_000)
    ui.step("reassessed")
    sent = saved.value.request.post_data_json
    assert (sent["question_id"], sent["answer"], sent["reassess"]) == (question["question_id"], ANSWER, {"job_identity": job["job_identity"]})
    assert saved.value.status in (200, 201) and saved.value.json()["reassessed"], "the answer was saved but the job was not re-assessed"
    assert ui.writes_after("typed") == ["POST /api/answers"]
    state = ui.page.locator('.job-page [data-role="job-state"]')
    assert state.get_attribute("data-state") != "needs_answers"
    assert "#/jobs/" in ui.page.url, "the page left the job"
    ui.cpu_budget("save an answer and re-assess (fixture model)", REASSESS_CPU_SECONDS, "typed", "reassessed")
    ui.wall_budget("save an answer and re-assess (fixture model)", REASSESS_WALL_SECONDS, "typed", "reassessed")

    # The pipeline takes it from here, by itself, and the page follows without a reload.
    timeline = ui.page.locator(f".job-page {tid('step-timeline')}")
    ui.page.locator(f".job-page {tid('step-timeline')}[data-state='done']").wait_for(timeout=PIPELINE_PATIENCE_MS)
    ui.step("pipeline-done")
    assert timeline.locator("[data-step]").evaluate_all("(steps) => steps.map((step) => [step.dataset.step, step.dataset.state])") == [
        ["tailor", "done"], ["reassess", "done"], ["ats", "done"], ["label", "done"],
    ]
    assert timeline.locator(tid("scout-label-chip")).count() == 1 and timeline.locator(tid("ats-chip")).count() == 1
    assert ui.writes_after("reassessed") == [], "the pipeline ran by itself: the page asked for nothing"
    ui.wall_budget("the background pipeline finishes the job (fixture model)", PIPELINE_WALL_SECONDS, "reassessed", "pipeline-done")
    ui.settle()
    panel = ui.page.locator('#tailored-resume[data-state="stored"]')
    shown_without_reload = panel.count() == 1 and state.get_attribute("data-state") == "tailored"

    # The server agrees: the answer is the user's, and the job no longer waits.
    recorded = next(item for item in ui.server_json("/api/answers")["answers"] if item["question_id"] == question["question_id"])
    assert recorded["answer"] == ANSWER and recorded["written_by"] == "operator"
    row = next(item for item in ui.server_json("/api/postings?limit=50")["postings"]["rows"] if item["job_identity"] == job["job_identity"])
    assert row["state"] != "needs_answers" and row["open_questions"] == [] and row["tailored"] is True
    job_state = ui.server_json(f"/api/pipeline/job?job_identity={job['job_identity']}&profile_id={profile_id}")
    assert job_state["state"] == "done"
    ui.no_more_than_one_in_flight("/api/pipeline/job")

    # The tailored resume the pipeline stored is on the job page (with its line in the resumes folder) after a reload.
    ui.reload()
    ui.wait_for_job_page()
    panel.wait_for()
    ui.page.locator(f"#tailored-resume {tid('resumes-folder-file')}").wait_for()
    ui.page.locator('.job-page [data-role="job-state"][data-state="tailored"]').wait_for()
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
    if not shown_without_reload:
        pytest.xfail(KNOWN_STALE_PANEL)
