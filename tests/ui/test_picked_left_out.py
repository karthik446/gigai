"""0.1.10.9 master P5: Picked / Left out on the job page, for a resume made from the master resume. Small home, real server.

The hero job's resume is tailored again once a master is stored (`POST /api/tailored-resumes`, the fixture model; the
tailoring then reads the job's candidate lines of the whole master and stores `selection`). The page shows "Picked (n)"
and "Left out (m)" beside the resume, by role, each line with its reason:

- the counts and every line are the stored selection's; a left-out line's text is the master's (ONE `GET /api/master`,
  when a list is first opened, never before);
- the header over the resume says "from your master resume (revision n), picked for profile ...", the same basis as the
  line under it ("GigAI picked the candidate lines from your whole master"), never "from resume <profile>" (0110-10-10 item 3);
- Add puts a left-out line on this job's resume in ONE request (`use: add`, the `updated_at` the page read): the
  resume's preview shows it under its role, and Picked lists it as "you added it to this resume";
- Remove takes it off again in one request (`use: remove`): the preview loses it and it is listed under Left out as
  "you removed it from this resume";
- an Add that would push the resume over 2 pages is ASKED about. The small home's resume is half a page, so that one
  answer is the test's: the first `PUT` of the second Add is answered as the route answers such an Add
  (`applied: false`, `would_cut`; the real route and its fit are proven in
  `tests/behaviors/scout_resume_gate/test_tailor_selection_edit.py` and `tests/api_e2e/test_master_api_journey.py`).
  The page asks, names the line that would go, and "Keep both" sends the same Add with `fit: keep` to the REAL server;
- "Save this wording to your master": a line edited on the resume (`PUT /api/tailored-resumes/lines`, as an agent does)
  offers it; one click writes that wording to the master line it replaced, on top of the revision read at that moment.

This flow CHANGES the shared home (a master, a tailored resume, a master line), so it runs late (`UI_ORDER`).

MEASURED (14-core laptop, 2026-10-04, Python 3.11, three runs): the job page with Picked / Left out 2.1 to 4.0 s wall
from a new page while the pipeline takes up the resume just tailored (1.0 to 1.4 server CPU seconds); Add and Remove
0.2 s each; saving the wording 2.9 to 4.4 s (one master write, and the resume of each profile that shows the line
printed again).
"""

from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

import pytest

from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid
from tests.ui.test_master_page import ensure_master

pytestmark = pytest.mark.ui
UI_ORDER = 40  # changes the shared home (a master, the hero job's tailored resume, a master line): after the Master page flows

PANEL = "#job-resume"
VIEW = f"{PANEL} {tid('picked-left-out')}"
JOB_PAGE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
JOB_PAGE_CPU_SECONDS = 5.0  # 1.0 to 1.4 measured idle (the pipeline's steps for the resume just tailored share the process)
CHANGE_WALL_SECONDS = INTERACTIVE_WALL_SECONDS  # Add, Remove: 0.2 s measured (a file write; no journal)
SAVE_WALL_SECONDS = 20.0  # one master write and the profiles' views printed again: 2.9 to 4.4 s measured
EDITED = "Led a zero-downtime Postgres migration of the 4 TB primary."

def _changes(ui, step: str) -> list[str]:
    """What the page wrote after a step, without the preview's render.

    0.1.11.5: the job page opens on Preview ALWAYS (a resume with a changed line opened on "Show changes"), and the
    preview is rendered again after every change of the resume. A render stores nothing."""

    return [write for write in ui.writes_after(step) if write != "POST /api/tailored-resumes/preview"]



def tailored_from_the_master(ui, demo) -> dict:
    """The hero job's stored resume, tailored from the master (again, through the real route, when it was not)."""

    ensure_master(ui)
    key = f"profile_id={quote(demo.hero_profile_id, safe='')}&job_identity={quote(demo.hero_job, safe='')}"
    items = ui.server_json(f"/api/tailored-resumes?{key}")["items"]
    if not items or not items[0].get("selection"):
        made = ui.server_json("/api/tailored-resumes", {"job": {"job_url": demo.hero_job}, "resume": {"profile_id": demo.hero_profile_id}})
        assert made.get("selection"), "with a master stored, the hero job's tailoring carries Picked / Left out"
        items = ui.server_json(f"/api/tailored-resumes?{key}")["items"]
    return items[0]


def open_hero(ui, demo) -> None:
    ui.goto("/#/jobs/" + quote(demo.hero_job, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
    ui.page.locator(VIEW).wait_for()


def listed(ui, role: str) -> list[dict]:
    return ui.page.locator(f'{VIEW} [data-role="{role}"] li[data-item-id]').evaluate_all(
        """(items) => items.map((item) => ({
          id: item.dataset.itemId, code: item.dataset.code,
          text: item.querySelector('[data-role="line-text"]').textContent,
          reason: item.querySelector('[data-role="reason"]').textContent,
          group: item.closest('[data-group]').querySelector('strong').textContent,
        }))"""
    )


def counts(ui) -> list[str]:
    return [(ui.page.locator(f'{VIEW} [data-action="show-{name}"]').text_content() or "").strip() for name in ("picked", "left-out")]


def preview_text(ui) -> str:
    """The resume as the panel shows it, in whichever of its two views is on (as it will print, or with its sources)."""

    return " ".join(ui.page.locator(f"{PANEL} .md-preview, {PANEL} .clean-wrap").all_inner_texts())


def test_picked_and_left_out_are_shown_with_reasons_and_a_line_is_removed_added_and_saved_to_the_master(ui, scout_server) -> None:
    demo = scout_server.demo
    stored = tailored_from_the_master(ui, demo)
    selection = stored["selection"]
    master = {item["id"]: item for item in ui.server_json("/api/master")["master"]["items"]}
    reasons = {line["id"]: line["reason"] for line in (*selection["picked"], *selection["left_out"])}
    victim = next(line["id"] for line in selection["left_out"] if master.get(line["id"], {}).get("kind") == "bullet")
    role = next(entry for entry in ui.server_json("/api/master")["master"]["entries"] if victim in entry["bullets"])
    key = {"profile_id": demo.hero_profile_id, "job_identity": demo.hero_job, "updated_at": stored["updated_at"]}
    assert master[victim]["text"] not in stored["markdown"]

    open_hero(ui, demo)
    ui.step("shown")
    assert counts(ui) == [f"Picked ({len(selection['picked'])})", f"Left out ({len(selection['left_out'])})"]
    assert ui.page.locator(VIEW).get_attribute("data-picked-by") == selection["picked_by"]
    # 0110-10-10 item 3: the header and the line under it name the same basis, the one the stored resume records.
    # 0.1.11: the header's ONE provenance line says who made the resume. This tree's server still tailors, so the
    # resume of this flow is the old tailor's and says so (a resume picked by the assessment: test_job_page_matched.py).
    header = ui.page.locator(f"{PANEL} .tailor-meta")
    under = ui.page.locator(f'{VIEW} [data-role="picked-by"]')
    assert "from your whole master" in (under.text_content() or "")
    assert (header.locator('[data-role="provenance"]').text_content() or "").startswith("Made by the tailoring of 0.1.10"), header.text_content()
    assert header.get_attribute("data-basis") == under.get_attribute("data-basis") == "master"
    ui.settle()
    # Closed, the lists ask the server nothing: the master is read when one is opened. 0.1.11.5: the page opens on
    # Preview ALWAYS (this resume has reworded lines: it opened on "Show changes"), so the one POST is the preview's
    # render; it stores nothing.
    assert ui.requests_after("start", "/api/master") == 0 and ui.writes_after("start") == ["POST /api/tailored-resumes/preview"]
    ui.cpu_budget("job page with Picked / Left out, cold page (small home)", JOB_PAGE_CPU_SECONDS, "start", "shown")
    ui.wall_budget("job page with Picked / Left out, cold page (small home)", JOB_PAGE_WALL_SECONDS, "start", "shown")

    # --- Left out: every line of the stored selection, with its reason and the master's text, under its role ---
    ui.page.click(f'{VIEW} [data-action="show-left-out"]')
    ui.page.locator(f'{VIEW} [data-role="left-out"] li[data-item-id="{victim}"]').wait_for()
    left = listed(ui, "left-out")
    assert [line["id"] for line in left] == [line["id"] for line in selection["left_out"]]
    assert all((line["reason"], line["text"]) == (reasons[line["id"]], master[line["id"]]["text"]) for line in left)
    assert next(line for line in left if line["id"] == victim)["group"] == role["heading"]
    ui.settle()
    assert ui.requests_after("shown", "/api/master") == 1

    # --- Add: one request, the line is on the resume, under its role ---
    ui.step("before-add")
    with ui.page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/api/tailored-resumes/selection")) as sent:
        ui.page.click(f'{VIEW} [data-role="left-out"] li[data-item-id="{victim}"] [data-action="add"]')
    ui.page.locator(f'{VIEW} [data-role="left-out"] li[data-item-id="{victim}"]').wait_for(state="detached")
    ui.step("added")
    assert json.loads(sent.value.post_data or "{}") == {**key, "use": "add", "item_id": victim}
    assert (ui.page.locator(f'{VIEW} [data-role="selection-note"]').text_content() or "") == "Added to this resume."
    assert counts(ui) == [f"Picked ({len(selection['picked']) + 1})", f"Left out ({len(selection['left_out']) - 1})"]
    assert master[victim]["text"] in preview_text(ui) and role["heading"] in preview_text(ui)
    ui.wall_budget("Add a line to the tailored resume (small home)", CHANGE_WALL_SECONDS, "before-add", "added")
    on_server = ui.server_json(f"/api/tailored-resumes?profile_id={quote(demo.hero_profile_id, safe='')}&job_identity={quote(demo.hero_job, safe='')}")["items"][0]
    assert {line["id"]: line["code"] for line in on_server["selection"]["picked"]}[victim] == "added_by_you" and on_server["updated_at"] == stored["updated_at"]
    assert master[victim]["text"] in on_server["markdown"]

    # --- Picked: the line, with why; Remove takes it off again in one request ---
    ui.page.click(f'{VIEW} [data-action="show-picked"]')
    ui.page.locator(f'{VIEW} [data-role="picked"] li[data-item-id="{victim}"]').wait_for()
    shown = next(line for line in listed(ui, "picked") if line["id"] == victim)
    assert (shown["code"], shown["reason"], shown["text"], shown["group"]) == ("added_by_you", "you added it to this resume", master[victim]["text"], role["heading"])
    ui.step("before-remove")
    with ui.page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/api/tailored-resumes/selection")) as sent:
        ui.page.click(f'{VIEW} [data-role="picked"] li[data-item-id="{victim}"] [data-action="remove"]')
    ui.page.locator(f'{VIEW} [data-role="picked"] li[data-item-id="{victim}"]').wait_for(state="detached")
    ui.step("removed")
    assert json.loads(sent.value.post_data or "{}") == {**key, "use": "remove", "item_id": victim}
    assert (ui.page.locator(f'{VIEW} [data-role="selection-note"]').text_content() or "").startswith("Removed from this resume.")
    assert counts(ui) == [f"Picked ({len(selection['picked'])})", f"Left out ({len(selection['left_out'])})"]
    assert master[victim]["text"] not in preview_text(ui)
    ui.page.click(f'{VIEW} [data-action="show-left-out"]')
    ui.page.locator(f'{VIEW} [data-role="left-out"] li[data-item-id="{victim}"]').wait_for()
    gone = next(line for line in listed(ui, "left-out") if line["id"] == victim)
    assert (gone["code"], gone["reason"], gone["text"]) == ("removed_by_you", "you removed it from this resume", master[victim]["text"])
    ui.settle()
    assert _changes(ui, "shown") == ["PUT /api/tailored-resumes/selection", "PUT /api/tailored-resumes/selection"]
    assert ui.requests_after("shown", "/api/master") == 1, "the master is read once, however many lists are opened"
    ui.wall_budget("Remove a line from the tailored resume (small home)", CHANGE_WALL_SECONDS, "before-remove", "removed")
    on_server = ui.server_json(f"/api/tailored-resumes?profile_id={quote(demo.hero_profile_id, safe='')}&job_identity={quote(demo.hero_job, safe='')}")["items"][0]

    # --- an Add that needs room is asked about (that one answer is the test's; "Keep both" goes to the real server) ---
    asked: list[dict] = []

    def needs_room(route) -> None:
        body = json.loads(route.request.post_data or "{}")
        if urlsplit(route.request.url).path == "/api/tailored-resumes/selection" and body.get("use") == "add" and "fit" not in body and not asked:
            asked.append(body)
            change = {
                "use": "add", "item_id": body["item_id"], "applied": False, "changed": False, "needs_choice": True, "pages": 3, "max_pages": 2,
                "would_cut": [{"id": "b-older", "kind": "bullet", "text": "Ran the on-call rotation for a team of six.", "role": "Mossbank Analytics"}], "cut": [],
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps({**on_server, "selection_change": change}))
            return
        route.continue_()

    ui.page.route("**/api/tailored-resumes/selection", needs_room)
    ui.step("before-question")
    ui.page.click(f'{VIEW} [data-role="left-out"] li[data-item-id="{victim}"] [data-action="add"]')
    question = ui.page.locator(f'{VIEW} [data-role="room-question"]')
    question.wait_for()
    assert (question.locator("span").first.text_content() or "") == (
        'With this line the resume is 3 pages. To keep 2 pages, this line would be cut: "Ran the on-call rotation for a team of six." (Mossbank Analytics).'
    )
    assert [button.text_content() for button in question.locator("button").all()] == ["Add it and cut that line", "Keep both (3 pages)", "Cancel"]
    assert master[victim]["text"] not in preview_text(ui), "an Add that asks changes nothing"
    with ui.page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/api/tailored-resumes/selection")) as sent:
        question.locator('[data-action="add-keep"]').click()
    question.wait_for(state="detached")
    assert json.loads(sent.value.post_data or "{}") == {**key, "use": "add", "item_id": victim, "fit": "keep"}
    ui.page.locator(f'{VIEW} [data-role="left-out"] li[data-item-id="{victim}"]').wait_for(state="detached")
    assert master[victim]["text"] in preview_text(ui)
    ui.page.unroute("**/api/tailored-resumes/selection", needs_room)

    # --- "Save this wording to your master": a line edited on the resume, saved to the master line it replaced ---
    current = ui.server_json(f"/api/tailored-resumes?profile_id={quote(demo.hero_profile_id, safe='')}&job_identity={quote(demo.hero_job, safe='')}")["items"][0]
    line = next(
        line for section in current["result"]["sections"] for entry in section.get("entries", []) for line in entry["bullets"]
        if any(ref.get("item_id") == victim for ref in line["refs"])
    )
    ui.server_json("/api/tailored-resumes/lines", {**key, "line_id": line["id"], "use": "custom", "text": EDITED}, method="PUT")  # as the user's agent edits a line
    revision = ui.server_json("/api/master")["master"]["revision"]
    ui.reload()
    ui.wait_for_job_page()
    ui.page.locator(VIEW).wait_for()
    # 0.1.11.5: the page opens on Preview; the per-line controls are under "Show changes", one click away.
    assert ui.page.locator(f'{PANEL} [data-action="view-clean"]').get_attribute("aria-pressed") == "true"
    ui.page.locator(f'{PANEL} [data-action="view-changes"]').click()
    save = ui.page.locator(f'{PANEL} .line-controls[data-line-id="{line["id"]}"] [data-action="save-wording"]')
    save.wait_for()
    assert ui.page.locator(f'{PANEL} [data-action="save-wording"]').count() == 1, "only the edited line offers it"
    ui.step("before-save")
    with ui.page.expect_request(lambda request: request.method == "PUT" and request.url.endswith("/api/master/lines")) as sent:
        save.click()
    saved = ui.page.locator(f'{PANEL} [data-role="wording-saved"]')
    saved.wait_for()
    ui.step("saved")
    assert json.loads(sent.value.post_data or "{}") == {"revision": revision, "id": victim, "use": "edit", "text": EDITED}
    assert (saved.text_content() or "") == f"Saved to your master (revision {revision + 1}). Other jobs and profiles use it from now on."
    after = ui.server_json("/api/master")["master"]
    assert after["revision"] == revision + 1 and after["written_by"] == "operator"
    assert next(item for item in after["items"] if item["id"] == victim)["text"] == EDITED
    ui.settle()
    assert _changes(ui, "before-save") == ["PUT /api/master/lines"]
    ui.wall_budget("Save a wording to the master (small home)", SAVE_WALL_SECONDS, "before-save", "saved")
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
