"""0.1.11.5 (b++): "Save this wording to my master" on an edited point, and the job page ALWAYS opens on Preview.

Real Chromium against the real server of `test_job_page_resume_points.py` (its own server and synthetic home: the
pick fixture, a job assessed against an invented 56-line master, its resume picked by the assessment; the one
answer that is the test's is `GET /api/setup`, as there).

Pinned, on what the page shows and on the master the server holds:

- THE JOB PAGE OPENS ON "PREVIEW", also for a resume with a changed point (before: it reopened on "Show changes",
  the marked-up text); "Show changes" is one click away and Preview one click back;
- NOTHING REACHES THE MASTER UNLESS THE PERSON ASKS: an edit writes this job's resume only; the master's files are
  byte for byte what they were;
- only an EDITED point whose words are not the master's carries the action: one button on the whole list after one
  edit, and none once the master has the point's words (the model test pins a point reworded back to the master's);
- the click WRITES NOTHING: it shows what the master says now and what it will say, with "Change my master" and
  "Cancel" (the focus is on Cancel; Escape cancels too). After Cancel the master's files are byte for byte what they
  were and no write was sent;
- the confirm changes EXACTLY THAT ONE LINE: one `PUT /api/master/lines`, the master is one revision on, that line
  has the new words, every other line and every role is as it was, and this job's stored resume is untouched;
- afterwards the page says so plainly, with the next pick of the other jobs; the point no longer carries the action
  (its words are the master's now), and it is still there after a reload;
- no id is shown.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from gigai.scout.tailored_resume import tailored_resume_path

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.ui.test_job_page_resume_points import _ID, _files, _held, _ready, _saved, _scratch_home, page  # noqa: F401 - the fixtures

pytestmark = pytest.mark.ui
UI_ORDER = 62  # a server and a home of its own: nothing of the shared home is read or written

PANEL = "#job-resume"
POINTS = f'{PANEL} [data-testid="resume-points"]'
POINT = f'{POINTS} [data-role="point"]'
STATUS = f'{POINTS} [data-role="points-status"]'
ACTION = '[data-action="save-to-master"]'
CONFIRM = '[data-role="master-confirm"]'
PREVIEW_BUTTON = f'{PANEL} [data-action="view-clean"]'
CHANGES_BUTTON = f'{PANEL} [data-action="view-changes"]'
MASTER_WRITE = "PUT /api/master/lines"
LINES = "PUT /api/tailored-resumes/lines"
PREVIEW_ROUTE = "/api/tailored-resumes/preview"
NEW = "Rebuilt the night-shift roster engine so a schedule change reaches every clinic in under a minute."


def _master(ui) -> dict:
    return ui.api.client.get("/api/master").json()["master"]


def _type(ui, box, words: str) -> None:
    box.focus()
    ui.page.keyboard.press("ControlOrMeta+A")
    ui.page.keyboard.type(words)
    ui.page.keyboard.press("Enter")
    _saved(ui)
    _ready(ui)


def _opens_on_preview(ui) -> None:
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
    ui.page.locator(f'{PANEL} [data-testid="resume-preview"], {PANEL} .md-preview').first.wait_for()
    assert ui.page.locator(PREVIEW_BUTTON).get_attribute("aria-pressed") == "true", "the job page did not open on Preview"
    assert ui.page.locator(CHANGES_BUTTON).get_attribute("aria-pressed") == "false" and ui.page.locator(f"{PANEL} .md-preview").count() == 0
    _ready(ui)


def test_an_edited_point_is_saved_to_the_master_only_after_the_confirm_and_the_page_opens_on_preview(page) -> None:  # noqa: ANN001, F811
    ui = page
    fixture = ui.fx
    stored_file = tailored_resume_path(fixture.home_root, fixture.target, fixture.default_profile_id, _JOB)
    before_master = _master(ui)
    files = _files(fixture.home_root)
    assert files, "the home holds a master"

    # --- as picked: Preview, and no point offers its wording to the master ---
    ui.step("open")
    ui.goto("/#/jobs/" + quote(_JOB, safe=""))
    _opens_on_preview(ui)
    assert ui.page.locator(f"{POINT} {ACTION}").count() == 0, "a point as the master has it offers to save its wording"
    assert ui.requests_after("open", "/api/master") == 0, "the master is read for a resume with an edited point (or for 'Add a left-out point'), not before"

    # --- an edit is saved for THIS JOB only: the master is not written ---
    role_points = ui.page.locator(f'{POINTS} [data-role="point-group"]').nth(1).locator('[data-role="point"]')
    one = role_points.nth(0)
    item_id = one.get_attribute("data-item-id")
    says = next(item["text"] for item in before_master["items"] if item["id"] == item_id)
    ui.step("edit")
    _type(ui, one.locator("textarea"), NEW)
    one.locator(ACTION).wait_for()
    ui.settle()
    assert ui.writes_after("edit") == [LINES, f"POST {PREVIEW_ROUTE}"] and ui.requests_after("edit", "/api/master") == 1, "an edit wrote more than this job's resume"
    assert _files(fixture.home_root) == files and _master(ui) == before_master, "an edit of a point wrote the master"
    assert (one.locator(ACTION).text_content() or "").strip() == "Save this wording to my master"
    assert ui.page.locator(f"{POINT} {ACTION}").count() == 1, "a point that was not edited offers to save its wording"

    # --- a reload opens on PREVIEW (the resume has a changed point), and the other view is one click away ---
    ui.step("reloaded")
    ui.reload()
    _opens_on_preview(ui)
    one = ui.page.locator(f'{POINT}[data-item-id="{item_id}"]')
    one.locator(ACTION).wait_for()
    assert one.get_attribute("data-edited") == "true" and one.locator("textarea").input_value() == NEW
    ui.page.locator(CHANGES_BUTTON).click()
    ui.page.locator(f'{PANEL} .md-preview[data-view="changes"]').wait_for()
    ui.page.locator(PREVIEW_BUTTON).click()
    _ready(ui)
    ui.settle()
    assert set(ui.writes_after("reloaded")) == {f"POST {PREVIEW_ROUTE}"}, "a reload or a change of view wrote something"
    held = _held(ui)
    resume_bytes = stored_file.read_bytes()

    # --- the click writes nothing: it shows what it will change. Cancel leaves the master byte for byte. ---
    ui.step("ask")
    one.locator(ACTION).click()
    confirm = one.locator(CONFIRM)
    confirm.wait_for()
    assert (confirm.locator('[data-role="master-old"]').text_content() or "").strip() == says, "the confirm does not show what the master says now"
    assert (confirm.locator('[data-role="master-new"]').text_content() or "").strip() == NEW, "the confirm does not show what the master will say"
    assert "This changes one line of your master." in (confirm.text_content() or "")
    assert ui.page.evaluate("() => document.activeElement.dataset.action") == "cancel-master", "the focus is not on Cancel"
    assert (confirm.locator('[data-action="confirm-master"]').text_content() or "").strip() == "Change my master"
    assert ui.page.locator(CONFIRM).count() == 1 and one.locator(ACTION).count() == 0
    confirm.locator('[data-action="cancel-master"]').click()
    confirm.wait_for(state="detached")
    one.locator(ACTION).click()
    confirm.wait_for()
    ui.page.keyboard.press("Escape")
    confirm.wait_for(state="detached")
    ui.settle()
    assert ui.writes_after("ask") == [], "asking, or Cancel, wrote something"
    assert _files(fixture.home_root) == files and _master(ui) == before_master, "Cancel changed the master"

    # --- the confirm changes exactly that one line ---
    ui.step("confirm")
    one.locator(ACTION).click()
    confirm.wait_for()
    confirm.locator('[data-action="confirm-master"]').click()
    ui.page.locator(f'{POINTS}[data-state="master-saved"]').wait_for()
    confirm.wait_for(state="detached")
    ui.settle()
    after = _master(ui)
    assert (ui.page.locator(STATUS).text_content() or "").strip() == (
        f"Saved to your master (revision {after['revision']}): that one line. This job's resume already has these words; other jobs take the new wording on their next pick."
    )
    assert ui.writes_after("confirm") == [MASTER_WRITE], "the confirm is one write of the master and nothing else"
    assert after["revision"] == before_master["revision"] + 1
    was = {item["id"]: item for item in before_master["items"]}
    now = {item["id"]: item for item in after["items"]}
    assert list(now) == list(was), "the master gained, lost or moved a line"
    assert [item_id_ for item_id_ in was if now[item_id_] != was[item_id_]] == [item_id], "another line of the master changed"
    assert now[item_id]["text"] == NEW and was[item_id]["text"] == says
    assert {key for key in was[item_id] if now[item_id][key] != was[item_id][key]} <= {"text", "strength", "mark", "written_by", "source"}, "more than the line's wording changed"
    assert after["entries"] == before_master["entries"], "a role of the master changed"
    assert _files(fixture.home_root) != files, "the master on this home's disk is what it was: the write went somewhere else"
    assert _held(ui) == held and stored_file.read_bytes() == resume_bytes, "saving a wording to the master changed this job's resume"
    # The point's words are the master's now: no action any more, here and after a reload.
    one.locator(ACTION).wait_for(state="detached")
    assert ui.page.locator(f"{POINT} {ACTION}").count() == 0 and one.get_attribute("data-edited") == "true"
    assert not _ID.search(ui.page.locator(POINTS).inner_text()), "the list shows an id"

    ui.step("again")
    ui.reload()
    _opens_on_preview(ui)
    ui.page.locator(f'{POINT}[data-item-id="{item_id}"]').wait_for()
    ui.settle()
    assert ui.requests_after("again", "/api/master") == 1, "the master is read once for a resume with an edited point"
    assert ui.page.locator(f"{POINT} {ACTION}").count() == 0, "a point whose wording equals the master's shows the action after a reload"
    assert _master(ui) == after and [write for write in ui.writes_after("again") if write != f"POST {PREVIEW_ROUTE}"] == []
