"""0.1.10.9 master P5: the Master resume page (#/master), on the small home, against the real server. Nothing is answered by the test.

First flow, the MIGRATION with its conflict question. The small home's two profiles share one resume, so the test
first gives the second profile a resume of its own through the real routes (`POST /api/resumes`, `PUT /api/profiles`):
the same resume with one line worded with older numbers. The page then shows the merge and asks the ONE question
(both wordings, and the profile that holds each); the button stays off until it is answered; one click makes the
master, and each profile keeps the resume it showed.

Second flow, the page itself: the master is listed BY ROLE as the server gives it, every line with the strength the
server derived, and the revision the page read. A line is added under a role, edited, retired (it asks first) and
restored from History; each is ONE write that carries the revision the page read, and the revision on the page
moves by one each time. The new line is offered to the profiles ("1 new master line: refresh?"), never added by
itself; Refresh makes the profile's resume its selection and the offer goes away.

Both flows CHANGE the shared home (a master is stored, a profile's resume moves), so they run late (`UI_ORDER`).

MEASURED (14-core laptop, 2026-10-04, Python 3.11, four runs): the page with the migration preview 2.9 to 3.5 s wall
from a new page (the merge of two resumes: 1.6 to 1.9 server CPU seconds); making the master 2.4 to 2.8 s (0.7 CPU
seconds); the Master page 0.3 to 2.7 s wall, 0.2 to 1.0 CPU seconds; a line write 3.0 to 3.5 s wall (0.9 to 1.1 CPU
seconds: two journal writes and the profiles' statuses); a refresh 3.2 to 4.2 s. On the operator-sized home a line
write is 5 to 6 s (the P5 worker report).
"""

from __future__ import annotations

import json

import pytest

from tests.ui.support import FIRST_LOAD_WALL_SECONDS
from tools.media import persona

pytestmark = pytest.mark.ui
UI_ORDER = 30  # changes the shared home (stores a master, moves a profile's resume): after the readers and the job page flow

PAGE = '[data-role="master-page"]'
MIGRATION = '[data-role="master-migration"]'
PAGE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
PAGE_CPU_SECONDS = 4.0  # 0.2 to 1.9 measured idle (the higher number: the merge of two resumes)
MAKE_WALL_SECONDS = 15.0  # 2.4 to 2.8 s measured: the master's record and two profile writes
MAKE_CPU_SECONDS = 8.0
WRITE_WALL_SECONDS = 15.0  # two journal writes and the profiles' statuses: 3.0 to 3.5 s measured
WRITE_CPU_SECONDS = 6.0
REFRESH_WALL_SECONDS = 20.0  # the index read, the selection and the view's record: 3.2 to 4.2 s measured
OLDER_LINE = "Built Python services that price and route 25,000 shipments a day."
NEWER_LINE = "Built Python services that price and route 40,000 shipments a day."
NEW_LINE = "Cut the deploy time of 40 services from 50 to 12 minutes."
EDITED_LINE = "Cut the deploy time of 40 services from 50 to 11 minutes."
NEAR_LINE = "Cut the deploy time of 40 services from 50 minutes to 11 minutes."  # EDITED_LINE in other words


def ensure_master(ui) -> dict:
    """The master of the shared home, made through the API when no flow before this one made it (a subset run)."""

    body = ui.server_json("/api/master")
    if body["master"] is None:
        plan = ui.server_json("/api/master/migration")
        made = ui.server_json("/api/master/migration", {"answers": {question["question_id"]: "a" for question in plan["questions"]}})
        assert made["written"], made
        body = ui.server_json("/api/master")
    return body


def revision_on_page(ui) -> int:
    return int(ui.page.locator(PAGE).get_attribute("data-master-revision") or 0)


def wait_for_revision(ui, revision: int) -> None:
    ui.page.locator(f'{PAGE}[data-master-revision="{revision}"]').wait_for()


def page_lines(ui) -> list[dict]:
    return ui.page.locator(f"{PAGE} [data-master-section] li[data-line-id]").evaluate_all(
        """(items) => items.map((item) => ({
          id: item.dataset.lineId,
          strength: item.dataset.strength,
          text: item.querySelector('[data-role="line-text"]').textContent,
          entry: (item.closest('[data-entry-id]') || { dataset: {} }).dataset.entryId || null,
        }))"""
    )


def test_the_migration_asks_its_question_and_makes_the_master(ui) -> None:
    if ui.server_json("/api/master")["master"] is not None:
        pytest.skip("the shared home already has a master (another flow made it): the migration flow needs a home without one")
    profiles = {item["label"]: item for item in ui.server_json("/api/profiles")["profiles"]}
    second = profiles[persona.SECOND_PROFILE_LABEL]
    first = next(item for item in profiles.values() if item["profile_id"] != second["profile_id"])
    # The second profile gets its own resume: the same one, one line with older numbers (the real routes, as the Settings page uses them).
    assert NEWER_LINE in persona.RESUME_MARKDOWN
    stored = ui.server_json("/api/resumes", {"text": persona.RESUME_MARKDOWN.replace(NEWER_LINE, OLDER_LINE)})["resume_ref"]
    ui.server_json(
        f"/api/profiles/{second['profile_id']}",
        {"label": second["label"], "titles": second["titles"], "resume_record_id": stored["record_id"], "resume_revision_id": stored["revision_id"]},
        method="PUT",
    )
    before = {item["profile_id"]: item["resume_ref"] for item in ui.server_json("/api/profiles")["profiles"]}
    plan = ui.server_json("/api/master/migration")
    assert plan["status"] == "needs_answers" and len(plan["questions"]) == 1, plan
    question = plan["questions"][0]

    ui.goto("/#/master")
    panel = ui.page.locator(f'{MIGRATION}[data-migration-state="questions"]')
    panel.wait_for()
    ui.step("asked")
    # The merge in one line, and the one question: both wordings, each with the profile that holds it.
    assert (panel.locator('[data-role="migration-summary"]').text_content() or "").startswith("2 resumes, ")
    row = panel.locator(f'li[data-question-id="{question["question_id"]}"]')
    shown = " ".join(row.locator("p.story-answer").all_text_contents())
    assert NEWER_LINE in shown and OLDER_LINE in shown and first["label"] in shown and second["label"] in shown
    assert [button.get_attribute("data-choice") for button in row.locator("[data-choice]").all()] == ["a", "b", "both"]
    make = panel.locator('[data-action="make-master"]')
    assert make.is_disabled(), "the master cannot be made before the question is answered"
    ui.settle()
    assert ui.writes_after("start") == [] and ui.requests_after("start", "/api/master/migration") == 1
    ui.cpu_budget("Master page: the migration preview (small home)", PAGE_CPU_SECONDS, "start", "asked")
    ui.wall_budget("Master page: the migration preview (small home)", PAGE_WALL_SECONDS, "start", "asked")

    newer = next(option["key"] for option in question["options"] if NEWER_LINE in option["text"])
    row.locator(f'[data-choice="{newer}"]').click()
    assert not make.is_disabled()
    with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/master/migration")) as sent:
        make.click()
    wait_for_revision(ui, 1)
    ui.step("made")
    assert json.loads(sent.value.post_data or "{}") == {"answers": {question["question_id"]: newer}}
    ui.settle()
    assert ui.writes_after("asked") == ["POST /api/master/migration"]
    ui.wall_budget("Make the master resume (small home)", MAKE_WALL_SECONDS, "asked", "made")
    ui.cpu_budget("Make the master resume (small home)", MAKE_CPU_SECONDS, "asked", "made")

    # What the server stored: the answered wording once, the other one nowhere; written by the user (a browser page).
    master = ui.server_json("/api/master")["master"]
    texts = [item["text"] for item in master["items"]]
    assert (master["revision"], master["written_by"]) == (1, "operator") and texts.count(NEWER_LINE) == 1 and OLDER_LINE not in texts
    # Each profile keeps the resume it showed: nothing assessed goes stale.
    assert {item["profile_id"]: item["resume_ref"] for item in ui.server_json("/api/profiles")["profiles"]} == before
    # The page is now the master, with both profiles' selections under it (the panel reads them after the page is up).
    rows = ui.page.locator(f'{PAGE} [data-role="master-selections"] li[data-profile-id]')
    rows.nth(1).wait_for()
    assert rows.count() == 2 and ui.page.locator(f'{PAGE} [data-role="master-selections"] [data-role="selection-offer"]').count() == 0
    ui.assert_clean()


def test_the_master_page_lists_by_role_and_adds_edits_retires_and_restores_a_line(ui) -> None:
    body = ensure_master(ui)
    master = body["master"]
    start = master["revision"]
    role = next(entry for entry in master["entries"] if entry["section"] == "experience")
    ui.page.add_init_script(
        """(() => { window.__gigaiWrites = []; const real = window.fetch; window.fetch = (url, options) => {
             if (options && options.method && options.method !== 'GET') window.__gigaiWrites.push([options.method + ' ' + url, JSON.parse(options.body || '{}')]);
             return real(url, options); }; })()"""
    )

    ui.goto("/#/master")
    wait_for_revision(ui, start)
    ui.step("shown")
    # By role, as the server gives it: every line once, under its entry, with the strength the server derived.
    shown = page_lines(ui)
    assert sorted(line["id"] for line in shown) == sorted(item["id"] for item in master["items"])
    by_id = {item["id"]: item for item in master["items"]}
    assert all((line["text"], line["strength"], line["entry"]) == (by_id[line["id"]]["text"], by_id[line["id"]]["strength"], by_id[line["id"]]["entry_id"]) for line in shown)
    assert [line["id"] for line in shown if line["entry"] == role["id"]] == role["bullets"]
    assert {line["strength"] for line in shown} >= {"quantified", "stated"}
    assert ui.page.locator(f'{PAGE} [data-entry-id="{role["id"]}"] [data-role="entry-heading"]').text_content() == role["heading"]
    revision_line = ui.page.locator(f'{PAGE} [data-role="master-revision"]').text_content() or ""
    assert revision_line.startswith(f"Revision {start} · written by ")
    ui.settle()
    assert ui.writes_after("start") == [] and ui.requests_after("start", "/api/master") == 1
    ui.cpu_budget("Master page (small home)", PAGE_CPU_SECONDS, "start", "shown")
    ui.wall_budget("Master page (small home)", PAGE_WALL_SECONDS, "start", "shown")

    # --- add a line under the role ---
    entry = ui.page.locator(f'{PAGE} [data-entry-id="{role["id"]}"]')
    entry.locator('[data-action="add-line"]').click()
    entry.locator("form.master-form textarea").fill(NEW_LINE)
    entry.locator('form.master-form [data-action="save-line"]').click()
    wait_for_revision(ui, start + 1)
    ui.step("added")
    line = entry.locator("li[data-line-id]").last
    line_id = line.get_attribute("data-line-id")
    assert line.locator('[data-role="line-text"]').text_content() == NEW_LINE and line.get_attribute("data-strength") == "quantified"
    assert line_id not in by_id
    # The new line is OFFERED to the profiles, never added by itself; the page says so and each profile gets a Refresh.
    assert "1 new master line: refresh?" in (ui.page.locator(f'{PAGE} [data-role="master-notice"]').text_content() or "")
    offers = ui.page.locator(f'{PAGE} [data-role="master-selections"] li[data-selection-state="offer"]')
    offers.first.wait_for()
    offered = ui.server_json("/api/master/selection")["profiles"]
    assert offers.count() == sum(1 for status in offered if status["offer"]) >= 1
    assert all(text == "1 new master line: refresh?" for text in offers.locator('[data-role="selection-offer"]').all_text_contents())
    ui.wall_budget("Add a line to the master (small home)", WRITE_WALL_SECONDS, "shown", "added")
    ui.cpu_budget("Add a line to the master (small home)", WRITE_CPU_SECONDS, "shown", "added")

    # --- edit it: the id stays ---
    line = ui.page.locator(f'{PAGE} li[data-line-id="{line_id}"]')
    line.locator('[data-action="edit-line"]').click()
    line.locator("textarea").fill(EDITED_LINE)
    line.locator('[data-action="save-line"]').click()
    wait_for_revision(ui, start + 2)
    assert line.locator('[data-role="line-text"]').text_content() == EDITED_LINE

    # --- retire it: it asks first ---
    asked: list[str] = []

    def accept(dialog) -> None:
        asked.append(dialog.message)
        dialog.accept()

    ui.page.once("dialog", accept)
    line.locator('[data-action="retire-line"]').click()
    wait_for_revision(ui, start + 3)
    line.wait_for(state="detached")
    assert len(asked) == 1 and "History can put it back" in asked[0]

    # --- History: the revisions, what is retired, and the way back ---
    ui.page.click(f'{PAGE} [data-action="toggle-history"]')
    history = ui.page.locator(f'{PAGE} [data-role="master-history"]')
    retired = history.locator(f'li[data-retired-id="{line_id}"]')
    retired.wait_for()
    assert [int(item.get_attribute("data-revision") or 0) for item in history.locator('[data-role="revisions"] li').all()] == list(range(start + 3, 0, -1))
    assert EDITED_LINE in (retired.text_content() or "") and f"retired in revision {start + 3}" in (retired.text_content() or "")
    retired.locator('[data-action="restore"]').click()
    wait_for_revision(ui, start + 4)
    ui.page.locator(f'{PAGE} li[data-line-id="{line_id}"]').wait_for()  # back under its role, with its own id
    retired.wait_for(state="detached")
    ui.step("restored")

    # Each change was ONE write that carried the revision the page had read.
    writes = ui.page.evaluate("() => window.__gigaiWrites")
    assert [(name, body["revision"], body.get("use")) for name, body in writes] == [
        ("POST /api/master/lines", start, None), ("PUT /api/master/lines", start + 1, "edit"),
        ("PUT /api/master/lines", start + 2, "retire"), ("PUT /api/master/lines", start + 3, "restore"),
    ]
    assert writes[0][1] == {"revision": start, "text": NEW_LINE, "entry_id": role["id"]} and writes[1][1]["id"] == writes[2][1]["id"] == writes[3][1]["id"] == line_id
    ui.settle()
    assert ui.writes_after("shown") == ["POST /api/master/lines", "PUT /api/master/lines", "PUT /api/master/lines", "PUT /api/master/lines"]
    now = ui.server_json("/api/master")["master"]
    assert now["revision"] == start + 4 and now["written_by"] == "operator"
    assert next(item for item in now["items"] if item["id"] == line_id)["text"] == EDITED_LINE

    # --- Refresh: the profile's resume becomes its selection, and the offer is taken up ---
    status = next(item for item in ui.server_json("/api/master/selection")["profiles"] if item["offer"])
    row = ui.page.locator(f'{PAGE} [data-role="master-selections"] li[data-profile-id="{status["profile_id"]}"]')
    before = {item["profile_id"]: item["resume_ref"] for item in ui.server_json("/api/profiles")["profiles"]}
    row.locator('[data-action="selection-refresh"]').click()
    note = row.locator('[data-role="selection-note"]')
    note.wait_for()
    ui.step("refreshed")
    assert (note.text_content() or "").startswith(("Refreshed: ", "First selection: "))
    row.locator('[data-role="selection-offer"]').wait_for(state="detached")
    after = {item["profile_id"]: item["resume_ref"] for item in ui.server_json("/api/profiles")["profiles"]}
    assert after[status["profile_id"]] != before[status["profile_id"]], "the profile's resume is now its selection"
    assert {key: value for key, value in after.items() if key != status["profile_id"]} == {key: value for key, value in before.items() if key != status["profile_id"]}
    ui.settle()
    assert ui.writes_after("restored") == ["POST /api/master/selection"]
    ui.wall_budget("Refresh a profile's selection (small home)", REFRESH_WALL_SECONDS, "restored", "refreshed")

    # --- a line the master already has in other words is ASKED about: nothing is written until "Add it anyway" ---
    entry.locator('[data-action="add-line"]').click()
    entry.locator("form.master-form textarea").fill(NEAR_LINE)
    entry.locator('form.master-form [data-action="save-line"]').click()
    entry.locator('form.master-form [data-action="save-line"]', has_text="Add it anyway").wait_for()
    said = ui.page.locator(f'{PAGE} [data-role="master-notice"]').text_content() or ""
    assert said.startswith("Not added: the master already has a line that says nearly this") and EDITED_LINE in said
    assert ui.server_json("/api/master")["master"]["revision"] == start + 4, "asked means nothing was written"
    assert entry.locator("form.master-form textarea").input_value() == NEAR_LINE  # the form keeps what was typed
    entry.locator('form.master-form [data-action="save-line"]').click()
    wait_for_revision(ui, start + 5)
    assert entry.locator("li[data-line-id]").last.locator('[data-role="line-text"]').text_content() == NEAR_LINE
    asked_then_forced = [body for name, body in ui.page.evaluate("() => window.__gigaiWrites") if name == "POST /api/master/lines"][-2:]
    assert asked_then_forced == [
        {"revision": start + 4, "text": NEAR_LINE, "entry_id": role["id"]}, {"revision": start + 4, "text": NEAR_LINE, "entry_id": role["id"], "force": True},
    ]
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
