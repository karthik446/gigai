"""0.1.10.9 master P8: the Master page and the master's file in the resumes folder (real browser, real server).

The master is also a file, `master.md` in the resumes folder, written by GigAI after every change. The "user" here is
this test writing into that folder (a temporary one: the session's home is not `~/.gigai`, so its resumes folder is
`<home>/resumes`).

Pinned, on the small home, nothing answered by the test:

- the page has a line about the file and ONE button, "Import the file";
- an edit of the file is not read by the page or the server by itself: after Refresh the line says "master.md has
  changes not imported yet" and the master is as it was;
- the button is ONE write (`POST /api/master/sync`, an empty body); the page then shows the new revision with the
  typed line under its role, says what was imported, and the file on disk carries the new line's id;
- a write made on the page while the file holds changes that are not imported leaves the file alone and says the
  revision went beside it (`master-2.md`).

A refused import (409 when the master moved on, 422 for contact data) is covered over HTTP
(`tests/api_e2e/test_master_file_journey.py`) and in the model test: any 4xx fails a browser test by design.

This flow CHANGES the shared home (two master revisions), so it runs late (`UI_ORDER`), and it leaves the folder
with `master.md` saying what is stored.

MEASURED (14-core laptop, 2026-10-04, Python 3.11): see the report of worker M8.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.ui.test_master_page import PAGE, WRITE_CPU_SECONDS, WRITE_WALL_SECONDS, ensure_master, wait_for_revision

pytestmark = pytest.mark.ui
UI_ORDER = 35  # changes the shared home's master: after the Master page flows, before Picked / Left out

FILE_LINE = f'{PAGE} [data-role="master-file"]'
TYPED = "Wrote the on-call handbook that 3 teams adopted."
PAGE_LINE = "Kept the release checklist under 20 steps for 2 years."


def test_the_master_page_says_the_file_has_changes_and_imports_it(ui) -> None:
    body = ensure_master(ui)
    start = body["master"]["revision"]
    folder = Path(ui.server_json("/api/resumes-folder")["path"])
    file = folder / "master.md"
    if body["file"]["state"] != "current" or body["file"]["behind"]:
        ui.server_json("/api/master/sync", {})  # a master made before this build's file: written now, nothing imported
    assert ui.server_json("/api/master")["file"]["state"] == "current" and file.is_file()
    role = next(entry for entry in body["master"]["entries"] if entry["section"] == "experience")
    first = next(item["text"] for item in body["master"]["items"] if item["id"] == role["bullets"][0])

    ui.goto("/#/master")
    wait_for_revision(ui, start)
    line = ui.page.locator(FILE_LINE)
    line.wait_for()
    ui.step("shown")
    assert line.get_attribute("data-file-state") == "current"
    assert (line.text_content() or "").startswith("Also a file you can edit: ") and "master.md" in (line.text_content() or "")
    button = line.locator('[data-action="import-file"]')
    assert button.text_content() == "Import the file" and ui.page.locator(f'{PAGE} [data-action="import-file"]').count() == 1

    # The user types a line into the file, under the first role, with no id.
    original = file.read_text(encoding="utf-8")
    assert original.count(f"- {first}") == 1
    edited = original.replace(f"- {first}", f"- {TYPED}\n- {first}")
    file.write_text(edited, encoding="utf-8")
    ui.settle()
    assert ui.writes_after("shown") == [], "the page wrote something by itself"
    assert line.get_attribute("data-file-state") == "current"  # the page reads the file's state when it is asked to
    ui.page.click(f'{PAGE} [data-action="refresh"]')
    ui.page.locator(f'{FILE_LINE}[data-file-state="changed"]').wait_for()
    ui.step("changed")
    assert "master.md has changes not imported yet" in (line.text_content() or "") and button.text_content() == "Import the file"
    # Said, not read: the master is as it was, on the page and on the server.
    assert ui.server_json("/api/master")["master"]["revision"] == start and file.read_text(encoding="utf-8") == edited
    assert TYPED not in (ui.page.locator(PAGE).text_content() or "")
    assert ui.writes_after("shown") == []

    # --- Import the file: ONE write, no body ---
    with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/master/sync")) as sent:
        button.click()
    wait_for_revision(ui, start + 1)
    ui.step("imported")
    assert json.loads(sent.value.post_data or "{}") == {}
    said = ui.page.locator(f'{PAGE} [data-role="master-notice"]').text_content() or ""
    assert said.startswith(f"Imported master.md as revision {start + 1}: 1 added, 0 changed, 0 retired; ")
    now = ui.server_json("/api/master")
    typed = next(item for item in now["master"]["items"] if item["text"] == TYPED)
    assert (now["master"]["revision"], now["master"]["written_by"], typed["entry_id"]) == (start + 1, "operator", role["id"])
    shown = ui.page.locator(f'{PAGE} [data-entry-id="{role["id"]}"] li[data-line-id="{typed["id"]}"] [data-role="line-text"]')
    shown.wait_for()
    assert shown.text_content() == TYPED
    # The file is GigAI's again: every line with its id, and the page says the file is in order.
    assert f"- {TYPED} <!-- id:{typed['id']} -->" in file.read_text(encoding="utf-8")
    ui.page.locator(f'{FILE_LINE}[data-file-state="current"]').wait_for()
    ui.settle()
    assert ui.writes_after("changed") == ["POST /api/master/sync"]
    ui.wall_budget("Import master.md from the Master page (small home)", WRITE_WALL_SECONDS, "changed", "imported")
    ui.cpu_budget("Import master.md from the Master page (small home)", WRITE_CPU_SECONDS, "changed", "imported")

    # --- a write on the page while the file holds changes that are not imported: the file is left alone ---
    mine = file.read_text(encoding="utf-8") + "\n<!-- a note of my own -->\n"
    file.write_text(mine, encoding="utf-8")
    entry = ui.page.locator(f'{PAGE} [data-entry-id="{role["id"]}"]')
    entry.locator('[data-action="add-line"]').click()
    entry.locator("form.master-form textarea").fill(PAGE_LINE)
    entry.locator('form.master-form [data-action="save-line"]').click()
    wait_for_revision(ui, start + 2)
    ui.step("added")
    said = ui.page.locator(f'{PAGE} [data-role="master-notice"]').text_content() or ""
    assert "master.md has changes not imported yet, so it was left as it is: this revision is in master-2.md beside it." in said
    assert file.read_text(encoding="utf-8") == mine, "the user's file was replaced"
    assert PAGE_LINE in (folder / "master-2.md").read_text(encoding="utf-8")
    ui.page.locator(f'{FILE_LINE}[data-file-state="changed"]').wait_for()
    assert f"The master changed here since that file was written (revision {start + 1}, now {start + 2}; the master as it is now is in master-2.md)" in (line.text_content() or "")
    ui.settle()
    assert ui.writes_after("imported") == ["POST /api/master/lines"]
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests

    # Leave the folder as GigAI writes it (not through the page): the user takes the newer file as theirs, and imports it.
    file.write_text((folder / "master-2.md").read_text(encoding="utf-8"), encoding="utf-8")
    done = ui.server_json("/api/master/sync", {"revision": start + 2})
    assert done["status"] == "unchanged" and ui.server_json("/api/master")["file"]["state"] == "current"
    assert sorted(path.name for path in folder.glob("master*.md")) == ["master.md"]
