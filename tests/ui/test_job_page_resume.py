"""0.1.11 N6 (SPEC section 6, 8.1 N6): the job page without tailoring. Three of its four flows; the Matched one is `test_job_page_matched.py`.

No model writes a resume in 0.1.11: the resume for a job is picked when the job is assessed, and only for a job the
gate lets through. These flows read the page as that leaves it. None of them changes the shared home (each write
they make is answered by the test).

**A held job** (`test_a_held_job_...`). (a) REAL server, a posting that waits for the user's answer: the header's ONE
chip says "Needs your answers", there is no resume, ONE sentence says why, a link goes to the questions, and "Make a
draft anyway" is offered. There is ONE action on the page, "Re-assess · 1 model call", and no "Tailor" anywhere.
(b) "Has a gap" (OD1): the assessment's `resume_gate` is `hold_unmet` (a field the server of this tree does not store
yet: the fixture lays it over the real assessment, `job_resume_fixtures.py`). The chip says "Has a gap", the State
line says it, the sentence names the requirement, and "Make a draft anyway" is ONE `POST /api/job-resumes/pick`
`{action: "draft"}`; the draft is shown marked as a draft and the job stays held.

**A stale job** (`test_a_stale_job_...`). The suggestion route's `stale` list says a printed line changed in the
master: the panel says so and offers "Re-pick · no model call" (never Re-assess: the assessment is current); nothing
refreshes by itself; Apply first says the resume is stale and offers "Re-pick first" or "Use it as it is"; the
re-pick is ONE `POST /api/job-resumes/pick` `{action: "refresh"}` and the label goes. (An old ASSESSMENT is the real
server's flow: `test_reassess_stale.py`.)

**A legacy 0.1.10 job** (`test_a_job_tailored_by_0_1_10_...`). REAL server, nothing answered by the test: the small
home's hero job was tailored by the pipeline of this tree, which is still the 0.1.10 pipeline. Its resume stays and
says "Made by the tailoring of 0.1.10"; a line the tailor reworded says "reworded by the old tailor"; the pipeline
panel has FOUR rows (Assessed, Resume picked, Scout ATS, Scout label), no "before -> after tailoring" line, the label
"made on 0.1.10's tailored resume" and "Check again"; Apply is the one button. Then, with a `proposed` selection in
the record (the fixture): "A new suggested resume is available: Compare · Use it · Dismiss", and Use it is ONE
`POST /api/job-resumes/pick` `{action: "use_proposed"}`: the user's resume is never replaced by itself.

MEASURED (14-core laptop, 2026-10-05, Python 3.11): see the report of packet N6; the ceilings below are the harness's
standing ones for a job page and a click.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

import pytest

from tests.ui.job_resume_fixtures import GAP_REQUIREMENT, ROW_GAP, JobResumeFixture, evidence_folder, shot, stored_resume
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

PAGE = ".job-page"
PANEL = f"{PAGE} {tid('job-resume')}"
APPLY = f"{PAGE} {tid('job-apply')}"
TIMELINE = f"{PAGE} {tid('step-timeline')}"
CHIP = f'{PAGE} [data-role="job-chip"]'
REASSESS = f'{PAGE} [data-action="reassess"]'
JOB_PAGE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
CLICK_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
#: The job page's own reads of the stored resume and its record when it opens: one each.
OPEN_READS = 1


def open_job(ui, job_identity: str) -> None:
    ui.goto("/#/jobs/" + quote(job_identity, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(PANEL).wait_for()


def no_tailoring(ui) -> None:
    """The page offers no tailoring: no such action, and neither of its two buttons' words."""

    assert ui.page.locator(f'{PAGE} [data-action="tailor"]').count() == 0
    words = ui.page.locator(PAGE).inner_text()
    assert "Tailor resume" not in words and "Tailor again" not in words and "Tailoring" not in words, words


def pick_requests(ui, since: str) -> list[str]:
    return [line for line in ui.writes_after(since) if line.endswith("/api/job-resumes/pick")]


def test_a_held_job_shows_no_resume_one_sentence_and_a_draft_on_request(ui, scout_server) -> None:
    demo = scout_server.demo

    # --- (a) the real server: a posting that waits for the user's answer ---
    waiting = ui.server_json("/api/postings?state=needs_answers&limit=50")["postings"]["rows"]
    assert waiting, "the small home has a posting that waits for the user's answer"
    job = waiting[0]
    assert stored_resume(ui, job["profile_id"], job["job_identity"]) is None, "a job that waits for answers has no stored resume in the small home"
    open_job(ui, job["job_identity"])
    ui.step("held-open")
    panel = ui.page.locator(PANEL)
    assert panel.get_attribute("data-state") == "held" and panel.get_attribute("data-gate") == "hold_question"
    assert ui.page.locator(CHIP).get_attribute("data-fit") == "needs_answers"
    assert (ui.page.locator(CHIP).text_content() or "").strip() == f"Needs your answers ({len(job['open_questions'])})"
    sentence = (panel.locator('[data-role="hold-sentence"]').text_content() or "").strip()
    assert sentence.startswith("No resume is suggested for this job: ") and sentence.endswith("for your answer."), sentence
    assert "must-have requirement" in sentence
    assert panel.locator(".md-preview, .clean-wrap").count() == 0, "a held job shows no resume"
    assert panel.locator('[data-action="go-to-questions"]').count() == 1 and ui.page.locator(f"{PAGE} #job-questions").count() == 1
    draft = panel.locator('[data-action="make-draft"]')
    assert draft.count() == 1 and (draft.text_content() or "").strip() == "Make a draft anyway" and draft.is_enabled()
    assert ui.page.locator(f"{APPLY}").count() == 0, "nothing to apply with: no resume"
    assert ui.page.locator(REASSESS).count() == 1 and (ui.page.locator(REASSESS).text_content() or "").strip() == "Re-assess · 1 model call"
    no_tailoring(ui)
    ui.settle()
    assert ui.writes_after("start") == [], "opening a held job writes nothing"
    assert ui.requests_after("start", "/api/jobs/suggestions") == 0, "a job with no v9 assessment asks the suggestion route nothing (it has no record)"
    shot(ui, evidence_folder(), "held-1-needs-answers-real-server")
    ui.wall_budget("open a held job (small home)", JOB_PAGE_WALL_SECONDS, "start", "held-open")

    # --- (b) Has a gap: matched by verdict, held by the gate; a draft on request ---
    fixture = JobResumeFixture(ui, profile_id=demo.hero_profile_id, job_identity=demo.hero_job)
    fixture.decision = "hold_unmet"
    fixture.gate_reasons = [{"code": "askable_unmet", "requirement": ROW_GAP}]
    fixture.extra_rows = [{"id": ROW_GAP, "requirement": GAP_REQUIREMENT, "class": "askable", "class_basis": "Requirements: Kubernetes in production", "status": "unmet", "resume_evidence": []}]
    fixture.resume, fixture.selection = "none", None
    fixture.install()
    open_job(ui, demo.hero_job)
    ui.step("gap-open")
    panel = ui.page.locator(f'{PANEL}[data-state="held"]')
    panel.wait_for()
    assert panel.get_attribute("data-gate") == "hold_unmet"
    chip = ui.page.locator(CHIP)
    assert chip.get_attribute("data-fit") == "has_gap" and (chip.text_content() or "").strip() == "Has a gap"
    assert chip.get_attribute("title") == f"Has a gap: {GAP_REQUIREMENT}"
    assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") == "has_gap"
    assert (panel.locator('[data-role="hold-sentence"]').text_content() or "").strip() == f"No resume is suggested for this job: Has a gap: {GAP_REQUIREMENT}."
    assert panel.locator(".md-preview, .clean-wrap").count() == 0 and ui.page.locator(APPLY).count() == 0
    ui.settle()
    assert fixture.picks == [] and ui.writes_after("gap-open") == [], "nothing is drafted until it is asked for"
    shot(ui, evidence_folder(), "held-2-has-a-gap")

    ui.step("before-draft")
    panel.locator('[data-action="make-draft"]').click()
    drafted = ui.page.locator(f'{PANEL}[data-state="stored"][data-draft="true"]')
    drafted.wait_for()
    ui.step("drafted")
    assert fixture.picks == [{"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "draft"}]
    assert pick_requests(ui, "before-draft") == ["POST /api/job-resumes/pick"]
    assert (drafted.locator('[data-role="provenance"]').text_content() or "").startswith("A draft, picked by Scout's own rules because you asked for one")
    assert drafted.locator('[data-role="draft-note"]').count() == 1
    assert (drafted.locator('[data-role="hold-sentence"]').text_content() or "").strip() == f"This job is held: Has a gap: {GAP_REQUIREMENT}."
    assert drafted.get_attribute("data-gate") == "hold_unmet" and drafted.locator('[data-action="make-draft"]').count() == 0
    assert drafted.locator(".md-preview, .clean-wrap").count() == 1, "the draft is shown"
    no_tailoring(ui)
    ui.wall_budget("make a draft for a held job", CLICK_WALL_SECONDS, "before-draft", "drafted")
    shot(ui, evidence_folder(), "held-3-draft-on-request")
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def test_a_stale_job_says_so_and_re_pick_is_one_request_with_no_model_call(ui, scout_server) -> None:
    demo = scout_server.demo
    fixture = JobResumeFixture(ui, profile_id=demo.hero_profile_id, job_identity=demo.hero_job)
    fixture.stale = ["picked_line_changed"]
    fixture.install()
    open_job(ui, demo.hero_job)
    panel = ui.page.locator(f'{PANEL}[data-state="stored"]')
    panel.wait_for()
    stale = panel.locator('[data-role="resume-stale"]')
    stale.wait_for()
    ui.step("shown")

    # The label, and the one refresh that is allowed, with its cost in the button.
    assert stale.locator("li").evaluate_all("(items) => items.map((item) => item.dataset.stale)") == ["picked_line_changed"]
    assert "a line this resume prints was changed or retired in your master" in (stale.text_content() or "")
    buttons = stale.locator("button")
    assert [(button.get_attribute("data-action"), (button.text_content() or "").strip()) for button in buttons.all()] == [("repick", "Re-pick · no model call")]
    assert ui.page.locator(f'{PAGE} [data-role="assessment-stale"]').count() == 0, "the assessment itself is current"
    assert panel.locator(".md-preview, .clean-wrap").count() == 1, "a stale resume stays visible"
    ui.settle()
    # Nothing refreshes by itself: the page read the resume and the record once each, and wrote nothing.
    # (0.1.11.5: the page opens on Preview always; the one POST is the preview's render, which stores nothing.)
    assert fixture.picks == [] and ui.writes_after("start") == ["POST /api/tailored-resumes/preview"]
    assert fixture.reads == OPEN_READS and ui.requests_after("start", "/api/tailored-resumes") == OPEN_READS
    shot(ui, evidence_folder(), "stale-1-label-and-re-pick")

    # Apply first says the resume is stale, and offers the re-pick or the resume as it is.
    apply = ui.page.locator(APPLY)
    assert apply.get_attribute("data-stale") == "true"
    apply.locator('[data-action="apply"]').click()
    ask = apply.locator('[data-role="apply-stale"]')
    ask.wait_for()
    assert "This resume is stale" in (ask.text_content() or "")
    assert [(button.get_attribute("data-action"), (button.text_content() or "").strip()) for button in ask.locator("button").all()] == [
        ("apply-repick", "Re-pick first · no model call"), ("apply-as-is", "Use it as it is"),
    ]
    assert apply.locator('[data-role="generate-pdf-form"]').count() == 0, "the form does not open before the choice"
    shot(ui, evidence_folder(), "stale-2-apply-asks-first")
    ask.locator('[data-action="apply-as-is"]').click()
    apply.locator('[data-role="generate-pdf-form"]').wait_for()
    apply.locator('[data-action="apply"]').click()  # Close
    apply.locator('[data-role="generate-pdf-form"]').wait_for(state="detached")
    assert fixture.picks == [], "using it as it is re-picks nothing"

    # Re-pick: one request, no model call, and the label goes.
    ui.step("before-repick")
    with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/job-resumes/pick") as sent:
        stale.locator('[data-action="repick"]').click()
    stale.wait_for(state="detached")
    ui.step("repicked")
    assert sent.value.post_data_json == {"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "refresh"}
    assert fixture.picks == [{"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "refresh"}]
    ui.settle()
    assert ui.writes_after("before-repick") == ["POST /api/job-resumes/pick"], "a re-pick is one request and calls no model route"
    assert apply.get_attribute("data-stale") is None
    assert panel.locator(".md-preview, .clean-wrap").count() == 1
    ui.wall_budget("re-pick a stale resume", CLICK_WALL_SECONDS, "before-repick", "repicked")
    ui.assert_clean()


def test_a_job_tailored_by_0_1_10_keeps_its_resume_and_says_who_made_it(ui, scout_server) -> None:
    demo = scout_server.demo
    stored = stored_resume(ui, demo.hero_profile_id, demo.hero_job)
    assert stored is not None and stored["producer"]["callable"] != "scout.pick", "the small home's hero job was tailored by the pipeline of this tree"
    assert "edited" not in stored
    pipeline = ui.server_json(f"/api/pipeline/job?job_identity={quote(demo.hero_job, safe='')}&profile_id={quote(demo.hero_profile_id, safe='')}")
    assert pipeline["label"] is not None, "the hero job has a Scout label"
    reworded = [line for section in stored["result"]["sections"] for line in [*section.get("lines", []), *(bullet for entry in section.get("entries", []) for bullet in entry["bullets"])] if line["kind"] == "rewritten" and line.get("origin", "model") == "model"]

    open_job(ui, demo.hero_job)
    panel = ui.page.locator(f'{PANEL}[data-state="stored"]')
    panel.wait_for()
    ui.page.locator(f"{TIMELINE} {tid('scout-label-chip')}").wait_for()
    ui.step("shown")

    # The resume stays, and says who made it.
    assert panel.get_attribute("data-origin") == "old_tailor"
    assert (panel.locator('[data-role="provenance"]').text_content() or "").strip() == "Made by the tailoring of 0.1.10"
    assert (panel.locator("h3").first.text_content() or "").strip() == "Your resume for this job"
    assert panel.locator(".md-preview, .clean-wrap").count() == 1
    if reworded:
        panel.locator('[data-action="view-changes"]').click()
        marks = panel.locator('.md-preview [data-role="old-tailor"]')
        assert marks.count() == len(reworded) and set(marks.all_text_contents()) == {"reworded by the old tailor"}

    # The pipeline panel: four rows, no "after tailoring" line, the 0.1.10 label and "Check again".
    timeline = ui.page.locator(TIMELINE)
    assert timeline.get_attribute("data-legacy") == "true"
    assert timeline.locator("[data-step]").evaluate_all("(steps) => steps.map((step) => step.dataset.step)") == ["assess", "pick", "ats", "label"]
    assert timeline.locator(".step-title").all_text_contents() == ["Assessed", "Resume picked", "Scout ATS", "Scout label"]
    assert timeline.locator('[data-role="tailored-variant"]').count() == 0 and "after tailoring" not in timeline.inner_text()
    label = timeline.locator(tid("scout-label-chip"))
    assert label.get_attribute("data-legacy") == "true" and "made on 0.1.10's tailored resume" in (label.text_content() or "")
    again = timeline.locator('[data-action="process-now"]')
    assert (again.text_content() or "").strip() == "Check again" and again.is_enabled()

    # Apply is the one button; nothing offers tailoring.
    apply = ui.page.locator(f"{APPLY} [data-action='apply']")
    assert apply.count() == 1 and (apply.text_content() or "").strip() == "Generate PDF"
    assert ui.page.locator(f'{PAGE} [data-role="open-generate-pdf"]').count() == 0, "Apply is the one way to the PDF"
    assert ui.page.locator(REASSESS).count() == 1
    no_tailoring(ui)
    ui.settle()
    # (0.1.11.5: a resume with reworded lines opens on Preview too: its render is the one POST; it stores nothing.)
    assert [write for write in ui.writes_after("start") if write != "POST /api/tailored-resumes/preview"] == [], "opening a 0.1.10 job writes nothing and tailors nothing"
    ui.wall_budget("open a job tailored by 0.1.10 (small home)", JOB_PAGE_WALL_SECONDS, "start", "shown")
    shot(ui, evidence_folder(), "legacy-1-job-tailored-by-0.1.10")

    # --- 0.1.11.4 E1: Re-pick on a resume that is the user's says what it does, and the new pick waits beside it ---
    waiting = {"picked_by": "code", "fallback": "no_pick", "draft": False, "pages": 2, "max_pages": 2, "line_marks": []}
    mine = JobResumeFixture(ui, profile_id=demo.hero_profile_id, job_identity=demo.hero_job)
    mine.picked, mine.selection = False, None
    mine.stale = ["picked_line_changed"]
    mine.refresh_proposes = waiting
    mine.install()
    open_job(ui, demo.hero_job)
    stale = ui.page.locator(f'{PANEL} [data-role="resume-stale"]')
    stale.wait_for()
    ui.step("mine-stale")
    assert (stale.locator('[data-role="edited-keeps"]').text_content() or "").strip() == "You edited this resume: a new pick will wait beside it, yours stays until you use it."
    assert ui.page.locator(f'{PANEL} [data-role="proposed"]').count() == 0
    # 0.1.11.5: the card opens on Preview always, and says "Making the preview…" until its pages are rendered. The
    # resume's text is read once the render is there (the preview's own state and its page count), never before: a
    # text read mid-render held that sentence and could not equal the one read after the re-pick.
    rendered = ui.page.locator(f'{PANEL} [data-testid="resume-preview"][data-state="ready"] [data-role="preview-pages"]')
    rendered.wait_for()
    kept = (ui.page.locator(f"{PANEL} .md-preview, {PANEL} .clean-wrap").first.text_content() or "").strip()
    assert kept and "Making the preview" not in kept, kept[:200]
    with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/job-resumes/pick") as repick:
        stale.locator('[data-action="repick"]').click()
    waits = ui.page.locator(f'{PANEL} [data-role="proposed"]')
    waits.wait_for()
    assert repick.value.post_data_json == {"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "refresh"}
    assert (waits.locator('[data-action="use-proposed"]').text_content() or "").strip() == "Use it"
    assert "Yours stays as it is until you take the new one." in (waits.text_content() or "")
    assert ui.page.locator(PANEL).get_attribute("data-origin") == "old_tailor", "the resume shown is still the user's"
    rendered.wait_for()
    assert (ui.page.locator(f"{PANEL} .md-preview, {PANEL} .clean-wrap").first.text_content() or "").strip() == kept
    ui.settle()
    assert mine.picks == [{"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "refresh"}]
    assert ui.writes_after("mine-stale") == ["POST /api/job-resumes/pick"]
    shot(ui, evidence_folder(), "legacy-1b-re-pick-on-an-edited-resume-waits-beside-it")

    # --- a new selection waits as `proposed`: the user's resume is never replaced by itself ---
    printed = [ref["item_id"] for section in stored["result"]["sections"] for line in [*section.get("lines", []), *(bullet for entry in section.get("entries", []) for bullet in entry["bullets"])] for ref in line["refs"] if ref.get("item_id")]
    fixture = JobResumeFixture(ui, profile_id=demo.hero_profile_id, job_identity=demo.hero_job)
    fixture.picked, fixture.selection = False, None
    fixture.stale = ["assessment_newer"]
    fixture.proposed = {"picked_by": "model", "fallback": None, "draft": False, "pages": 2, "max_pages": 2, "line_marks": [{"id": item, "mark": "0" * 16} for item in printed[:1]]}
    fixture.install()
    open_job(ui, demo.hero_job)
    offer = ui.page.locator(f'{PANEL} [data-role="proposed"]')
    offer.wait_for()
    ui.step("proposed")
    assert ui.page.locator(PANEL).get_attribute("data-origin") == "old_tailor"
    assert [(button.get_attribute("data-action"), (button.text_content() or "").strip()) for button in offer.locator("button").all()] == [
        ("compare-proposed", "Compare"), ("use-proposed", "Use it"), ("dismiss-proposed", "Dismiss"),
    ]
    assert "this resume was made before the latest assessment" in (ui.page.locator(f'{PANEL} [data-role="resume-stale"]').text_content() or "")
    assert ui.page.locator(f'{PANEL} [data-role="resume-stale"] button').count() == 0, "the way on is the proposed resume, not a refresh"
    ui.settle()
    assert fixture.picks == []
    offer.locator('[data-action="compare-proposed"]').click()
    offer.locator('[data-role="proposed-compare"]').wait_for()
    shot(ui, evidence_folder(), "legacy-2-a-new-suggested-resume-is-available")
    with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/job-resumes/pick") as sent:
        offer.locator('[data-action="use-proposed"]').click()
    offer.wait_for(state="detached")
    assert sent.value.post_data_json == {"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "use_proposed"}
    assert fixture.picks == [{"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "action": "use_proposed"}]
    ui.settle()
    assert ui.writes_after("proposed") == ["POST /api/job-resumes/pick"]
    ui.assert_clean()
