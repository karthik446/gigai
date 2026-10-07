"""0.1.11 N6 (SPEC section 6, 8.1 N6): a Matched job. The resume, Picked / Left out, Add, Restore, the suggestions, and Apply gives a PDF and nothing follows.

The small home's hero job, with a master stored and its resume made from it again (`fresh_resume`, the real route
of this tree). The fixture model's resume shows no line of the master, so the flow first puts two bullets on
it through the real Add. What this tree's server does not serve yet is laid over its real answers by
`job_resume_fixtures.JobResumeFixture`: the v9 fields of the assessment, the suggestion record, `producer:
scout.pick`, and the code a pick gives those two lines. Everything the flow WRITES to the stored resume is the real
server's: the Add, the per-line Restore and the PDF.

- the header's ONE chip says Matched; ONE action, "Re-assess · 1 model call"; no "Tailor" anywhere;
- the suggested resume: ONE provenance line, "Picked by the assessment from your master (revision n) · k lines ·
  2 pages", and the resume as it will print;
- Requirements: each row with the posting wording behind its class and its id, "any one of: ..." for an
  alternatives row, and for a met row "in the resume", "not in the resume" (with the line to put back) or "from
  your answer only";
- `ready: false`: a banner over the resume names the requirement whose evidence the resume no longer shows and the
  line; one click shows that line under Left out, and Add (ONE real `PUT /api/tailored-resumes/selection`) puts it
  back: the banner goes and the row reads "in the resume";
- Picked: a line's reason names the requirements it supports, and what Scout added;
- Changed: a line whose wording was changed (as the user's agent does it, `PUT /api/tailored-resumes/lines`) is
  shown beside the master line it replaced, with Restore (ONE real `PUT`, `use: original`);
- Suggestions (0.1.11.3 item 9): the card is ONE line, "Suggestions (N open)", closed by default and BELOW the
  Suggested resume card; a click, Enter or Space opens and closes it. Open: the kind, what it is about, the posting phrase, why, who wrote it, the status; Dismiss and Done are
  ONE `POST /api/jobs/suggestions` each; "Work on this with your agent" shows the two brief commands;
- Apply: ONE button, "Generate PDF", inside the "Suggested resume" card (0.1.11.3 item 5), opens the Generate PDF form; the PDF is ONE `POST
  /api/tailored-resumes/pdf` and a download; after it NOTHING: no other request, no new page, no application
  recorded, the same buttons.

This flow CHANGES the shared home (a master, the hero job's resume, one line added to it), so it runs late
(`UI_ORDER`); the line it adds is removed again at the end.

MEASURED (14-core laptop, 2026-10-05, Python 3.11): see the report of packet N6.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

from tests.ui.job_resume_fixtures import (
    ALTERNATIVES,
    ANSWER_ONLY_REQUIREMENT,
    CLASS_BASIS,
    LOST_REQUIREMENT,
    NOW,
    POSTING_PHRASE,
    ROW_ANSWER_ONLY,
    ROW_LOST,
    JobResumeFixture,
    evidence_folder,
    fresh_resume,
    resume_key,
    row_id,
    shot,
    stored_resume,
)
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid
from tests.ui.test_master_page import ensure_master

pytestmark = pytest.mark.ui
UI_ORDER = 45  # changes the shared home (a master, the hero job's resume): after the Master page and Picked / Left out flows

PAGE = ".job-page"
PANEL = f"{PAGE} {tid('job-resume')}"
VIEW = f"{PANEL} {tid('picked-left-out')}"
SUGGESTIONS = f"{PAGE} {tid('job-suggestions')}"
APPLY = f"{PAGE} {tid('job-apply')}"
TABLE = f'{PAGE} [data-role="requirements-section"] table.matrix-table'
JOB_PAGE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
CHANGE_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
PDF_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
EDITED = "Ran the zero-downtime Postgres migration of the 4 TB primary, with the team."
HEADER = {"name": "Zephyrine Quillfeather", "email": "zephyrine.quillfeather@example.test"}
WHY_REWORD = "This line could lead with what the posting asks for, in words the line already supports."
WHY_MASTER = "An answer or a story supports this requirement, and no line of your master resume states it. Add a line to your master so a resume can show it."

def _changes(ui, step: str) -> list[str]:
    """What the page wrote after a step, without the preview's render.

    0.1.11.5: the job page's resume is the preview only (no "Show changes" view), and the
    preview is rendered again after every change of the resume. A render stores nothing."""

    return [write for write in ui.writes_after(step) if write != "POST /api/tailored-resumes/preview"]



def lines_of(stored: dict) -> list[dict]:
    return [line for section in stored["result"]["sections"] for line in [*section.get("lines", []), *(bullet for entry in section.get("entries", []) for bullet in entry["bullets"])]]


def coverage_of(ui, row: str) -> tuple[str | None, str]:
    cell = ui.page.locator(f'{TABLE} tr:has(td[data-row-id="{row}"]) [data-role="row-coverage"]')
    return cell.get_attribute("data-coverage"), (cell.text_content() or "").strip()


def test_a_matched_job_shows_its_picked_resume_and_apply_gives_the_pdf_and_nothing_follows(ui, scout_server) -> None:
    demo = scout_server.demo
    ensure_master(ui)
    stored = fresh_resume(ui, demo.hero_profile_id, demo.hero_job)  # whatever an earlier flow left on it, no line of it is the user's now
    master = {item["id"]: item for item in ui.server_json("/api/master")["master"]["items"]}
    key = {"profile_id": demo.hero_profile_id, "job_identity": demo.hero_job, "updated_at": stored["updated_at"]}
    bullets = [line["id"] for line in (*stored["selection"]["picked"], *stored["selection"]["left_out"]) if master.get(line["id"], {}).get("kind") == "bullet"]
    assert len(bullets) >= 3, "the small home's master has at least three bullets"
    picked, victim = bullets[:2], bullets[2]
    # The resume shows `picked` and not `victim` (the real route; "keep" answers an Add that would need room).
    shown = {line["id"] for line in stored["selection"]["picked"]}
    for item in picked:
        if item not in shown:
            ui.server_json("/api/tailored-resumes/selection", {**key, "use": "add", "item_id": item, "fit": "keep"}, method="PUT")
    if victim in shown:
        ui.server_json("/api/tailored-resumes/selection", {**key, "use": "remove", "item_id": victim}, method="PUT")
    stored = stored_resume(ui, demo.hero_profile_id, demo.hero_job)
    assert {line["id"] for line in stored["selection"]["picked"]} >= set(picked) and stored["updated_at"] == key["updated_at"]
    assessment = next(item for item in ui.server_json(f"/api/assessments?profile_id={quote(demo.hero_profile_id, safe='')}")["items"] if item["job"]["job_identity"] == demo.hero_job)
    assert assessment["result"]["verdict"] == "matched_above_threshold", "the small home's hero job is a match"
    met = next(place for place, row in enumerate(assessment["result"]["matrix"], start=1) if row["status"] == "met")
    applications_before = len(ui.server_json("/api/applications")["applications"])

    fixture = JobResumeFixture(ui, profile_id=demo.hero_profile_id, job_identity=demo.hero_job)
    fixture.real_sources = {met: picked[:2]}
    fixture.extra_rows = [
        {"id": ROW_LOST, "requirement": LOST_REQUIREMENT, "class": "askable", "class_basis": CLASS_BASIS, "status": "met", "resume_evidence": ["Postgres in production"], "sources": [victim]},
        {"id": ROW_ANSWER_ONLY, "requirement": ANSWER_ONLY_REQUIREMENT, "class": "askable", "class_basis": CLASS_BASIS, "alternatives": ALTERNATIVES, "status": "met",
         "resume_evidence": ["MongoDB at two employers"], "sources": ["A tooling:mongodb"]},
    ]
    fixture.selection = {"added_by_code": [{"id": picked[1], "code": "added_for_coverage", "requirement": row_id(met)}]}
    fixture.relabel = {item: {"code": "picked", "reason": "picked by the assessment"} for item in picked}
    fixture.master = {"revision_id": "rev-fixture", "revision": stored["sources"]["master"]["revision"], "content_sha256": "sha256:" + "0" * 64}
    fixture.suggestions = [
        {"id": "sg-1", "kind": "reword", "line": picked[0], "requirement": row_id(met), "posting_phrase": POSTING_PHRASE, "why": WHY_REWORD, "source": "assessment", "created_at": NOW, "status": "open", "resolved": None},
        {"id": "sg-2", "kind": "master_line", "line": None, "requirement": ROW_ANSWER_ONLY, "posting_phrase": None, "why": WHY_MASTER, "source": "assessment", "created_at": NOW, "status": "open", "resolved": None},
        {"id": "sg-3", "kind": "keyword", "line": picked[1], "requirement": None, "posting_phrase": "event-driven", "why": "The posting names it and your master supports it.", "source": "agent", "created_at": NOW,
         "status": "done", "resolved": {"by": "agent", "at": NOW, "how": "job_resume_edit", "ref": None}},
    ]
    fixture.install()

    try:
        ui.goto("/#/jobs/" + quote(demo.hero_job, safe=""))
        ui.wait_for_job_page()
        panel = ui.page.locator(f'{PANEL}[data-state="stored"]')
        panel.wait_for()
        attention = panel.locator('[data-role="needs-attention"]')
        attention.wait_for()
        ui.step("shown")

        # --- the header and the ONE action ---
        chip = ui.page.locator(f'{PAGE} [data-role="job-chip"]')
        assert chip.get_attribute("data-fit") == "matched" and (chip.text_content() or "").strip().startswith("Matched")
        reassess = ui.page.locator(f'{PAGE} [data-action="reassess"]')
        assert reassess.count() == 1 and (reassess.text_content() or "").strip() == "Re-assess · 1 model call"
        assert ui.page.locator(f'{PAGE} [data-action="tailor"]').count() == 0
        words = ui.page.locator(PAGE).inner_text()
        assert "Tailor resume" not in words and "Tailor again" not in words and "Tailoring" not in words

        # --- the suggested resume: one provenance line, the resume as it will print ---
        assert (panel.get_attribute("data-gate"), panel.get_attribute("data-origin"), panel.get_attribute("data-ready")) == ("suggest", "pick", "false")
        assert (panel.locator("h3").first.text_content() or "").strip() == "Suggested resume"
        revision = stored["sources"]["master"]["revision"]
        assert (panel.locator('[data-role="provenance"]').text_content() or "").strip() == f"Picked by the assessment from your master (revision {revision}) · {len(lines_of(stored))} lines · 2 pages"
        assert panel.locator(".md-preview, .clean-wrap").count() == 1

        # --- needs attention: the requirement whose evidence the resume no longer shows, and the line ---
        item = attention.locator(f'li[data-requirement="{ROW_LOST}"]')
        assert item.get_attribute("data-code") == "lost_mandatory_evidence"
        assert f"the resume no longer shows the evidence for {LOST_REQUIREMENT}" in (item.text_content() or "")
        assert item.locator('[data-action="show-line"]').get_attribute("data-line") == victim

        # --- Requirements: the class wording, alternatives, and where a met row's evidence is ---
        assert coverage_of(ui, row_id(met)) == ("kept", "in the resume")
        lost = coverage_of(ui, ROW_LOST)
        assert lost[0] == "lost" and lost[1].startswith("not in the resume") and victim in lost[1]
        assert coverage_of(ui, ROW_ANSWER_ONLY) == ("answer_only", "from your answer only")
        answer_row = ui.page.locator(f'{TABLE} td[data-row-id="{ROW_ANSWER_ONLY}"]')
        assert (answer_row.locator('[data-role="row-alternatives"]').text_content() or "").strip() == f"any one of: {', '.join(ALTERNATIVES)}"
        # 0.1.11.3 (item 4): the chips say Required / Nice to have; no internal id, no "Why this class", no "ask" in a chip.
        table_text = ui.page.locator(TABLE).inner_text()
        assert "Why this class" not in table_text and "req-" not in table_text, table_text
        assert answer_row.locator('[data-role="row-class-basis"], [data-action="show-class-basis"]').count() == 0
        chips = [" ".join((chip or "").split()) for chip in ui.page.locator(f"{TABLE} .status-badge").all_text_contents()]
        assert chips and all(chip.split(":")[0] in {"Required", "Nice to have", "Bonus", "One of a list", "Met", "Unclear", "Not met"} for chip in chips), chips
        assert "Nice to have: Met" in chips, chips
        assert not any("ask" in chip.lower() or "must-have" in chip.lower() for chip in chips), chips
        ui.settle()
        # (0.1.11.5: the page opens on Preview always; the one POST is the preview's render, which stores nothing.)
        assert ui.writes_after("start") == ["POST /api/tailored-resumes/preview"], "opening the job writes nothing and picks nothing"
        assert fixture.reads == 1 and fixture.picks == [], "the record is read once, and nothing is refreshed by itself"
        assert ui.requests_after("start", "/api/master") == 1  # the card reads the master once at load (picked from it)
        ui.wall_budget("open a Matched job with its picked resume (small home)", JOB_PAGE_WALL_SECONDS, "start", "shown")
        shot(ui, evidence_folder(), "matched-1-needs-attention")

        # --- "not in the resume": one click shows the line under Left out; Add puts it back (the real route) ---
        ui.page.locator(f'{TABLE} tr:has(td[data-row-id="{ROW_LOST}"]) [data-action="show-line"]').click()
        focused = ui.page.locator(f'{VIEW} [data-role="left-out"] li[data-item-id="{victim}"][data-focused="true"]')
        focused.wait_for()
        assert (focused.locator('[data-role="line-text"]').text_content() or "") == master[victim]["text"]
        ui.step("before-add")
        with ui.page.expect_request(lambda request: request.method == "PUT" and urlsplit(request.url).path == "/api/tailored-resumes/selection") as sent:
            focused.locator('[data-action="add"]').click()
        attention.wait_for(state="detached")
        ui.step("added")
        assert sent.value.post_data_json == {**key, "use": "add", "item_id": victim}
        ui.page.locator(f'{PANEL}[data-ready="true"]').wait_for()
        assert coverage_of(ui, ROW_LOST) == ("kept", "in the resume")
        # An Add makes the resume the user's (SPEC 2.4): it says so, and a later pick would wait as `proposed`.
        assert ui.page.locator(PANEL).get_attribute("data-origin") == "edited"
        assert (ui.page.locator(f'{PANEL} [data-role="provenance"]').text_content() or "").strip() == "Your edited resume"
        assert master[victim]["text"] in (stored_resume(ui, demo.hero_profile_id, demo.hero_job) or {})["markdown"], "the Add is the real store's"
        assert _changes(ui, "before-add") == ["PUT /api/tailored-resumes/selection"]
        ui.wall_budget("Add the line a requirement lost (small home)", CHANGE_WALL_SECONDS, "before-add", "added")

        # --- Picked: the requirements a line supports, and what Scout added ---
        ui.page.click(f'{VIEW} [data-action="show-picked"]')
        ui.page.locator(f'{VIEW} [data-role="picked"] li[data-item-id="{victim}"]').wait_for()
        reasons = dict(ui.page.locator(f'{VIEW} [data-role="picked"] li[data-item-id]').evaluate_all("(items) => items.map((item) => [item.dataset.itemId, item.querySelector('[data-role=\"reason\"]').textContent])"))
        assert reasons[picked[0]] == f"supports {row_id(met)}"
        requirement = assessment["result"]["matrix"][met - 1]["requirement"]
        assert reasons[picked[1]] == f"added by Scout: the only evidence for {requirement} · supports {row_id(met)}"
        assert reasons[victim] == f"you added it to this resume · supports {ROW_LOST}"
        shot(ui, evidence_folder(), "matched-2-picked-with-reasons")

        # --- Changed: a line changed in chat beside its master line, and Restore ---
        current = stored_resume(ui, demo.hero_profile_id, demo.hero_job)
        line = next(line for line in lines_of(current) if any(ref.get("item_id") == victim for ref in line["refs"]))
        ui.server_json("/api/tailored-resumes/lines", {**key, "line_id": line["id"], "use": "custom", "text": EDITED}, method="PUT")  # as the user's agent changes a line
        ui.reload()
        ui.wait_for_job_page()
        ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
        ui.page.click(f'{VIEW} [data-action="show-changed"]')
        changed = ui.page.locator(f'{VIEW} [data-role="changed"] li[data-line-id="{line["id"]}"]')
        changed.wait_for()
        assert ui.page.locator(f'{VIEW} [data-role="changed"] li[data-by="user"]').count() == 1
        assert (changed.locator('[data-role="line-text"]').text_content() or "") == EDITED
        assert master[victim]["text"] in (changed.locator('[data-role="line-before"]').text_content() or "")
        assert "Changed in chat by you" in (changed.locator('[data-role="line-sources"]').text_content() or "")
        shot(ui, evidence_folder(), "matched-3-changed-in-chat")
        ui.step("before-restore")
        with ui.page.expect_request(lambda request: request.method == "PUT" and urlsplit(request.url).path == "/api/tailored-resumes/lines") as sent:
            changed.locator('[data-action="restore-line"]').click()
        changed.wait_for(state="detached")
        ui.step("restored")
        assert sent.value.post_data_json == {**key, "line_id": line["id"], "use": "original"}
        restored = stored_resume(ui, demo.hero_profile_id, demo.hero_job) or {}
        assert EDITED not in restored["markdown"] and master[victim]["text"] in restored["markdown"]
        assert _changes(ui, "before-restore") == ["PUT /api/tailored-resumes/lines"]
        ui.wall_budget("Restore a line changed in chat (small home)", CHANGE_WALL_SECONDS, "before-restore", "restored")

        # --- Suggestions (0.1.11.3 item 9): ONE line, closed by default, below the resume card; a click or the keyboard opens it ---
        card = ui.page.locator(SUGGESTIONS)
        toggle = card.locator('[data-action="toggle-suggestions"]')
        rows = ui.page.locator(f'{SUGGESTIONS} [data-role="suggestions"] li[data-suggestion-id]')
        assert card.get_attribute("data-expanded") == "false" and toggle.get_attribute("aria-expanded") == "false"
        assert (toggle.text_content() or "").strip().lstrip("▸").strip() == "Suggestions (2 open, 1 closed)"
        assert rows.count() == 0 and card.locator("li, p, [data-action='suggestion-agent']").count() == 0, "closed: the card is its one line"
        assert card.inner_text().strip().lstrip("▸").strip() == "Suggestions (2 open, 1 closed)"
        assert ui.page.locator(".job-page > section.panel[id]").evaluate_all("(cards) => cards.map((card) => card.id).filter((id) => id === 'job-resume' || id === 'job-suggestions')") == [
            "job-resume", "job-suggestions",
        ], "the Suggested resume card (with Generate PDF) comes first, then Suggestions"
        assert ui.page.locator(f"{PANEL} [data-action='apply']").bounding_box()["y"] < card.bounding_box()["y"]
        toggle.focus()
        ui.page.keyboard.press("Enter")  # the keyboard opens it
        rows.first.wait_for()
        assert card.get_attribute("data-expanded") == "true" and toggle.get_attribute("aria-expanded") == "true"
        ui.page.keyboard.press("Space")  # and closes it
        rows.first.wait_for(state="detached")
        assert card.get_attribute("data-expanded") == "false"
        toggle.click()  # a click opens it
        rows.first.wait_for()
        assert _changes(ui, "restored") == [], "opening the list writes nothing"
        assert rows.evaluate_all("(items) => items.map((item) => [item.dataset.suggestionId, item.dataset.kind, item.dataset.status])") == [
            ["sg-1", "reword", "open"], ["sg-2", "master_line", "open"], ["sg-3", "keyword", "done"],
        ]
        first = ui.page.locator(f'{SUGGESTIONS} li[data-suggestion-id="sg-1"]')
        assert (first.locator('[data-role="suggestion-kind"]').text_content() or "") == "Reword a line"
        about = first.locator('[data-role="suggestion-about"]').text_content() or ""
        assert master[picked[0]]["text"] in about and requirement in about
        assert POSTING_PHRASE in (first.locator('[data-role="suggestion-phrase"]').text_content() or "")
        assert (first.locator('[data-role="suggestion-why"]').text_content() or "") == WHY_REWORD
        assert (first.locator('[data-role="suggestion-who"]').text_content() or "") == "From the assessment"
        third = ui.page.locator(f'{SUGGESTIONS} li[data-suggestion-id="sg-3"]')
        assert (third.locator('[data-role="suggestion-who"]').text_content() or "") == "From your agent"
        assert (third.locator('[data-role="suggestion-status"]').text_content() or "").startswith("Done · this job's resume was changed · by your agent")
        assert third.locator("button").count() == 0, "a closed suggestion offers nothing"
        assert ANSWER_ONLY_REQUIREMENT in (ui.page.locator(f'{SUGGESTIONS} li[data-suggestion-id="sg-2"] [data-role="suggestion-about"]').text_content() or "")
        first.locator('[data-action="suggestion-agent"]').click()
        commands = first.locator('[data-role="brief-command"]')
        commands.first.wait_for()
        brief = f"gigai scout resume brief --job-url '{demo.hero_job}' --profile '{demo.hero_profile_id}'"
        assert commands.all_text_contents() == [brief, f"{brief} --posting"]
        routes = first.locator('[data-role="brief-route"] code').all_text_contents()
        assert [route.split("?")[0] for route in routes] == ["GET /api/jobs/brief"] * 2 and "part=yours" in routes[0] and "part=posting" in routes[1]
        shot(ui, evidence_folder(), "matched-4-suggestions-and-brief-commands")
        ui.settle()
        ui.step("before-suggestions")
        with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/jobs/suggestions"):
            ui.page.locator(f'{SUGGESTIONS} li[data-suggestion-id="sg-2"] [data-action="suggestion-dismiss"]').click()
        ui.page.locator(f'{SUGGESTIONS} li[data-suggestion-id="sg-2"][data-status="dismissed"]').wait_for()
        with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/jobs/suggestions"):
            first.locator('[data-action="suggestion-done"]').click()
        ui.page.locator(f'{SUGGESTIONS} li[data-suggestion-id="sg-1"][data-status="done"]').wait_for()
        who = {"job_url": demo.hero_job, "profile_id": demo.hero_profile_id, "actor": "operator"}
        assert fixture.actions == [{**who, "action": "dismiss", "suggestion_id": "sg-2"}, {**who, "action": "resolve", "suggestion_id": "sg-1", "how": "job_resume_edit"}]
        ui.settle()
        assert _changes(ui, "before-suggestions") == ["POST /api/jobs/suggestions", "POST /api/jobs/suggestions"], "a suggestion is closed by one request, and nothing is rewritten"

        # --- Apply: one button, the PDF, and nothing follows ---
        apply = ui.page.locator(APPLY)
        button = apply.locator('[data-action="apply"]')
        assert ui.page.locator(f'{PAGE} [data-action="apply"]').count() == 1 and (button.text_content() or "").strip() == "Generate PDF"
        assert ui.page.locator(f'{PAGE} [data-role="open-generate-pdf"]').count() == 0
        state_before = ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state")
        button.click()
        form = apply.locator('[data-role="generate-pdf-form"]')
        form.wait_for()
        for field, value in HEADER.items():
            ui.page.fill(f"#generate-pdf-{field}", value)
        buttons_before = ui.page.locator(f"{PAGE} button").count()
        url_before = ui.page.url
        ui.settle()
        ui.step("before-pdf")
        with ui.page.expect_download() as waiting:
            form.locator('[data-role="generate-pdf"]').click()
        download = waiting.value
        apply.locator('[data-role="pdf-saved"]').wait_for()
        ui.step("pdf")
        pdf = Path(download.path()).read_bytes()
        assert pdf.startswith(b"%PDF-") and len(pdf) > 2000 and download.suggested_filename.endswith(".pdf")
        ui.settle()
        assert _changes(ui, "before-pdf") == ["POST /api/tailored-resumes/pdf"], "Apply is the PDF and nothing after it"
        assert ui.requests_after("before-pdf") == 1, "after the PDF the page asks the server for nothing"
        assert ui.page.url == url_before and len(ui.page.context.pages) == 1, "no posting is opened"
        assert ui.page.locator(f"{PAGE} button").count() == buttons_before, "no new button or prompt after the PDF"
        assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") == state_before
        assert len(ui.server_json("/api/applications")["applications"]) == applications_before, "Apply records no application"
        ui.wall_budget("Apply: the PDF of a picked resume (small home)", PDF_WALL_SECONDS, "before-pdf", "pdf")
        shot(ui, evidence_folder(), "matched-5-apply-the-pdf")
        ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
    finally:
        # What the flow added to the shared home goes again: the lines it put on the hero job's resume.
        now = stored_resume(ui, demo.hero_profile_id, demo.hero_job)
        for item in (*picked, victim):
            if now is not None and any(line["id"] == item for line in now["selection"]["picked"]):
                ui.server_json("/api/tailored-resumes/selection", {"profile_id": demo.hero_profile_id, "job_identity": demo.hero_job, "updated_at": now["updated_at"], "use": "remove", "item_id": item}, method="PUT")


def test_resume_key_is_the_stored_resume_query() -> None:
    assert resume_key("p 1", "https://x.test/a?b=1") == "profile_id=p%201&job_identity=https%3A%2F%2Fx.test%2Fa%3Fb%3D1"
