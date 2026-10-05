"""0.1.11 N6 (SPEC 5.4; the routes are N1b's, merged): a NOTE on a master line and on an entry, on the Master page. Small home, real server, nothing answered by the test.

A note is the user's guidance for choosing lines ("agentic roles: lead with this"). It goes to the assessment and to
the agent's brief, never into a resume, and it is never evidence. The page shows a note under its line and edits it in
place:

- a line without a note offers "Add a note"; saving is ONE `PUT /api/master/lines` that carries the revision the page
  read and the note ALONE (`{revision, id, use: "edit", note}`: never the line's text), and the master moves one
  revision; the note is then shown under its line, and the server holds it on that line, whose text is unchanged;
- "Edit note" changes it the same way; "Remove note" sends `note: ""` and the line has none again;
- an entry (a role) takes a note the same way (`PUT /api/master/entries`, `{revision, id, use: "edit", note}`: never
  its heading).

A note the server refuses (a contact detail, more than one line, over 300 characters) is covered over HTTP by the
route's own tests (packet N1b): a browser flow fails on any HTTP 400 or more by the harness's rule, and the page
shows such a refusal in the same error line as every other master write.

This flow CHANGES the shared home (a master is stored if none was; four revisions are added), so it runs late
(`UI_ORDER`); it leaves no note behind.

MEASURED (14-core laptop, 2026-10-05, Python 3.11): see the report of packet N6 (a note write is a master write: 3 to
4 s on the small home, as a line edit).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from tests.ui.support import FIRST_LOAD_WALL_SECONDS
from tests.ui.test_master_page import PAGE, ensure_master, wait_for_revision

pytestmark = pytest.mark.ui
UI_ORDER = 35  # changes the shared home (a master, four revisions): after the Master page flow, before the job-page ones that read the master

NOTE = "Agentic roles: lead with this line."
NOTE_EDITED = "Agentic and platform roles: lead with this line."
ENTRY_NOTE = "Shorter version: keep the first two bullets."
WRITE_WALL_SECONDS = 15.0  # a master write: 3.0 to 3.5 s measured for a line edit (test_master_page.py)


def master(ui) -> dict:
    return ui.server_json("/api/master")["master"]


def put(ui, path: str, click) -> dict:
    """Click, and return the body of the ONE write it sent to `path`."""

    with ui.page.expect_request(lambda request: request.method == "PUT" and urlsplit(request.url).path == path) as sent:
        click()
    return sent.value.post_data_json


def test_a_note_is_shown_under_its_line_and_edited_in_place(ui) -> None:
    ensure_master(ui)
    before = master(ui)
    item = next(item for item in before["items"] if item["kind"] == "bullet" and not item.get("note"))
    entry = next(entry for entry in before["entries"] if item["id"] in entry["bullets"])
    assert not entry.get("note")
    revision = before["revision"]

    ui.goto("/#/master")
    wait_for_revision(ui, revision)
    line = ui.page.locator(f'{PAGE} li[data-line-id="{item["id"]}"]')
    note = line.locator('[data-role="note"]')
    ui.step("shown")
    assert note.get_attribute("data-has-note") == "false" and note.locator('[data-role="note-text"]').count() == 0
    assert (note.locator('[data-action="edit-note"]').text_content() or "").strip() == "Add a note"
    ui.settle()
    assert ui.writes_after("start") == []
    ui.wall_budget("the Master page (small home)", FIRST_LOAD_WALL_SECONDS, "start", "shown")

    try:
        # --- add: one write, the note alone, on top of the revision the page read ---
        note.locator('[data-action="edit-note"]').click()
        form = line.locator('[data-role="note-form"]')
        form.locator("input").fill(NOTE)
        ui.step("before-add")
        body = put(ui, "/api/master/lines", lambda: form.locator('[data-action="save-note"]').click())
        wait_for_revision(ui, revision + 1)
        ui.step("added")
        assert body == {"revision": revision, "id": item["id"], "use": "edit", "note": NOTE}
        shown = line.locator('[data-role="note"][data-has-note="true"] [data-role="note-text"]')
        assert (shown.text_content() or "").strip() == f"Note: {NOTE}"
        assert (line.locator('[data-role="line-text"]').text_content() or "") == item["text"], "a note never changes the line"
        held = next(found for found in master(ui)["items"] if found["id"] == item["id"])
        assert (held["note"], held["text"]) == (NOTE, item["text"])
        assert ui.writes_after("before-add") == ["PUT /api/master/lines"]
        ui.wall_budget("save a note on a master line (small home)", WRITE_WALL_SECONDS, "before-add", "added")

        # --- edit: the form opens on the note as it is ---
        line.locator('[data-action="edit-note"]').click()
        assert (line.locator('[data-action="edit-note"]').count(), form.locator("input").input_value()) == (0, NOTE)
        form.locator("input").fill(NOTE_EDITED)
        body = put(ui, "/api/master/lines", lambda: form.locator('[data-action="save-note"]').click())
        wait_for_revision(ui, revision + 2)
        assert body == {"revision": revision + 1, "id": item["id"], "use": "edit", "note": NOTE_EDITED}
        assert (shown.text_content() or "").strip() == f"Note: {NOTE_EDITED}"

        # --- an entry takes a note the same way ---
        role = ui.page.locator(f'{PAGE} [data-entry-id="{entry["id"]}"]')
        role_note = role.locator(':scope > [data-role="note"]')
        role_note.locator('[data-action="edit-note"]').click()
        role_form = role.locator(':scope > [data-role="note-form"]')
        role_form.locator("input").fill(ENTRY_NOTE)
        body = put(ui, "/api/master/entries", lambda: role_form.locator('[data-action="save-note"]').click())
        wait_for_revision(ui, revision + 3)
        assert body == {"revision": revision + 2, "id": entry["id"], "use": "edit", "note": ENTRY_NOTE}
        assert (role.locator(':scope > [data-role="note"] [data-role="note-text"]').text_content() or "").strip() == f"Note: {ENTRY_NOTE}"
        held_entry = next(found for found in master(ui)["entries"] if found["id"] == entry["id"])
        assert (held_entry["note"], held_entry["heading"]) == (ENTRY_NOTE, entry["heading"])

        # --- remove: `note: ""`, and the line has none again ---
        line.locator('[data-action="edit-note"]').click()
        body = put(ui, "/api/master/lines", lambda: form.locator('[data-action="remove-note"]').click())
        wait_for_revision(ui, revision + 4)
        assert body == {"revision": revision + 3, "id": item["id"], "use": "edit", "note": ""}
        assert line.locator('[data-role="note"]').get_attribute("data-has-note") == "false"
        assert next(found for found in master(ui)["items"] if found["id"] == item["id"])["note"] is None
    finally:
        # Leave no note behind (the server asked directly, as the CLI would).
        now = master(ui)
        for found in now["items"]:
            if found["id"] == item["id"] and found.get("note"):
                now = ui.server_json("/api/master/lines", {"revision": now["revision"], "id": item["id"], "use": "edit", "note": ""}, method="PUT")["master"]
        for found in now["entries"]:
            if found["id"] == entry["id"] and found.get("note"):
                ui.server_json("/api/master/entries", {"revision": now["revision"], "id": entry["id"], "use": "edit", "note": ""}, method="PUT")
    ui.settle()
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests

