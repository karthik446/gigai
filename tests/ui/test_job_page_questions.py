"""Flow 5 (REPORT.md 5.3): the job page. Its questions, answer one, and the pipeline timeline runs.

Real server, nothing stubbed, on the fixture model. The small home has postings that wait for the user's answer
(assessed before the GCP answer existed). The flow opens one from the "Needs your answers" chip, answers its
question in the box and re-assesses; the answer is saved, the assessment comes back with every requirement met, and
the background pipeline takes the job by itself (the home lets one job of a trigger run without an approval). The page
shows it in the FOUR rows of 0.1.11: Assessed, Resume picked, Scout ATS, Scout label. (The server of this tree still
runs the 0.1.10 steps until packet N4 lands: its `tailor` step is the one that stores the job's resume and is shown in
the "Resume picked" row, and "Assessed" has no step of its own yet. With N4 all four rows are the server's own.)

This flow CHANGES the shared home (an answer, an assessment, a stored resume), so it runs late (`UI_ORDER`).

Pinned: the page is the job (title, state "Needs your answers", the questions section with the server's question and
the requirement it is about); Re-assess is ONE `POST /api/answers` carrying the typed answer and this job; after it
the questions section is gone and the state leaves "Needs your answers", without a reload; the timeline reaches
"done" with the Scout label and Scout ATS chips by the page's own polling; the page then shows the resume the
pipeline stored, its jobs-folder line, the ONE "Generate PDF" button and the state "Resume ready", WITHOUT a
reload (the 0.1.10.9 U3 flow found it shown only after one): the stored resume is read once for the new assessment (the
pick comes with it) and ONCE more when the timeline says the pick step finished, and the timeline is not read again for
it; no action on the page tailors anything; the server holds the typed answer as the user's ("Written
by you"), and its row for the job agrees; a reload shows the same. Zero console errors.

A second flow (first in the file: it writes nothing) holds `GET /api/answers` until the box is filled: the answers
on record then arrive with one for this job's question, so the open questions change under a typed answer. The box
keeps what was typed, and the near-match lookup (`GET /api/answers/match`) was made once. The response is the real
server's, only late.

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
#: ONE GET /api/answers/match for one open question (it was up to four while the page settled: two holders of the
#: same drafts each asked, and again on every render that made new objects of the same questions and answers).
MATCH_REQUESTS = 1
DRAFT = "Typed before the answers on record arrived."


def test_a_typed_answer_stays_when_the_open_questions_change(ui) -> None:
    """The answers on record arrive AFTER the user typed, and they hold one for the question: the box keeps what was typed.

    The page looks a near match up once per open question, and again only when the open questions change. Here they
    change under a typed answer: the small home has an answer to this job's question on record (the job was assessed
    before it existed), and the test holds `GET /api/answers` until the box is filled. Nothing is written.
    """

    job = ui.server_json("/api/postings?state=needs_answers&limit=50")["postings"]["rows"][0]
    question = job["open_questions"][0]
    on_record = {item["question_id"]: item["answer"] for item in ui.server_json("/api/answers")["answers"]}
    assert question["question_id"] in on_record and on_record[question["question_id"]] != DRAFT
    held: list = []

    def answer(route) -> None:
        if route.request.method == "GET" and urlsplit(route.request.url).path == "/api/answers":
            held.append(route)
        else:
            route.continue_()  # the near-match lookup and everything else is the real server's

    ui.goto("/#/jobs?state=needs_answers")
    ui.wait_for_jobs_list()
    ui.settle()
    ui.step("listed")
    ui.page.route("**/api/answers*", answer)
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == "/api/answers/match"):
        ui.page.locator(f"{tid('job-row')} [data-action='open-job']", has_text=job["title"]).first.click()
    ui.wait_for_job_page()
    section = ui.page.locator('.job-page [data-role="questions-section"]')
    box = section.locator(f'.row-question[data-question-id="{question["question_id"]}"] input')
    box.fill(DRAFT)
    assert len(held) == 1, "the job page reads the answers on record once"

    # The answers on record arrive (the real server's), with one for this question: nothing is left to look up.
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == "/api/answers"):
        held.pop().continue_()
    ui.settle()
    assert box.input_value() == DRAFT, "the typed answer was lost when the open questions changed"
    assert section.locator('[data-action="reassess"]').is_enabled()
    assert ui.requests_after("listed", "/api/answers/match") == MATCH_REQUESTS, "one lookup for the question while it was open, none after"
    assert ui.writes_after("start") == []
    ui.assert_clean()


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
    assert ui.requests_after("typed", "/api/answers/match") == 0, "the answered question was looked up again"
    state = ui.page.locator('.job-page [data-role="job-state"]')
    assert state.get_attribute("data-state") != "needs_answers"
    assert "#/jobs/" in ui.page.url, "the page left the job"
    ui.cpu_budget("save an answer and re-assess (fixture model)", REASSESS_CPU_SECONDS, "typed", "reassessed")
    ui.wall_budget("save an answer and re-assess (fixture model)", REASSESS_WALL_SECONDS, "typed", "reassessed")

    # The pipeline takes it from here, by itself, and the page follows without a reload.
    timeline = ui.page.locator(f".job-page {tid('step-timeline')}")
    ui.page.locator(f".job-page {tid('step-timeline')}[data-state='done']").wait_for(timeout=PIPELINE_PATIENCE_MS)
    ui.step("pipeline-done")
    rows = timeline.locator("[data-step]").evaluate_all("(steps) => steps.map((step) => [step.dataset.step, step.dataset.state])")
    assert [name for name, _state in rows] == ["assess", "pick", "ats", "label"], "the pipeline panel shows four rows"
    assert rows[1:] == [["pick", "done"], ["ats", "done"], ["label", "done"]]
    # "Assessed" is `done` once the server runs the four steps of 0.1.11 (N4); this tree's pipeline has no such step yet.
    assert rows[0][1] in ("done", "not_started")
    assert timeline.locator('[data-role="tailored-variant"]').count() == 0, 'the "before -> after tailoring" line is gone'
    assert timeline.locator(tid("scout-label-chip")).count() == 1 and timeline.locator(tid("ats-chip")).count() == 1
    assert ui.writes_after("reassessed") == [], "the pipeline ran by itself: the page asked for nothing"
    ui.wall_budget("the background pipeline finishes the job (fixture model)", PIPELINE_WALL_SECONDS, "reassessed", "pipeline-done")

    # The page shows the resume the pipeline stored: the panel, its folder line, the one Apply button, "Resume ready".
    # No reload: the stored resume is read for the new assessment and when the pick step finished, and the timeline
    # stays as it is. Nothing on the page tailors.
    panel = ui.page.locator('#job-resume[data-state="stored"]')
    tailored_state = ui.page.locator('.job-page [data-role="job-state"][data-state="tailored"]')
    panel.wait_for()
    ui.page.locator(f"#job-resume {tid('jobs-folder-file')}").wait_for()
    tailored_state.wait_for()
    assert "Resume ready" in (tailored_state.text_content() or "")
    assert ui.page.locator('.job-page [data-action="tailor"]').count() == 0
    assert (ui.page.locator('.job-page [data-action="apply"]').text_content() or "").strip() == "Generate PDF"
    ui.settle()
    assert ui.requests_after("typed", "/api/tailored-resumes") == 2
    assert ui.requests_after("pipeline-done", "/api/pipeline/job") == 0, "the timeline was read again for the resume the pipeline stored"
    assert timeline.get_attribute("data-state") == "done"
    assert ui.writes_after("reassessed") == []

    # The server agrees: the answer is the user's, and the job no longer waits.
    recorded = next(item for item in ui.server_json("/api/answers")["answers"] if item["question_id"] == question["question_id"])
    assert recorded["answer"] == ANSWER and recorded["written_by"] == "operator"
    row = next(item for item in ui.server_json("/api/postings?limit=50")["postings"]["rows"] if item["job_identity"] == job["job_identity"])
    assert row["state"] != "needs_answers" and row["open_questions"] == [] and row["tailored"] is True
    job_state = ui.server_json(f"/api/pipeline/job?job_identity={job['job_identity']}&profile_id={profile_id}")
    assert job_state["state"] == "done"
    ui.no_more_than_one_in_flight("/api/pipeline/job")

    # A reload shows the same.
    ui.reload()
    ui.wait_for_job_page()
    panel.wait_for()
    ui.page.locator(f"#job-resume {tid('jobs-folder-file')}").wait_for()
    tailored_state.wait_for()
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
