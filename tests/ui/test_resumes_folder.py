"""0110-10-05 A: Settings shows the resumes folder, and it can be changed and put back (real browser, real server).

The session's HOME is a temporary directory and the GigAI home is a folder inside it that is not ``~/.gigai``, so
its default resumes folder is ``<home>/resumes`` (only the default home uses ``~/Documents/GigAI/resumes``),
printed as the user types it (``~/.../resumes``): the real Documents folder is never involved. The chosen folder
is a temporary one too, and the test ends on the default again, so the shared home is left as it was found. The job page's own line (the job's file in the folder) needs a
tailored resume, which the demo home has none of; it is covered under node in
``tests/api_e2e/test_ui_resumes_folder_model.py`` and over HTTP in ``test_edited_tailored_resume_journey.py``.

Pinned: the panel is there with the default folder and no "Use the default" button; Save is disabled until
another folder is typed; saving shows the chosen folder (``data-source="setting"``) and the folder exists, in
one request; "Use the default" brings the default back, in one request; zero console errors and no failed
request (the ``ui`` fixture asserts it). A refused path (422) is covered over HTTP, not here: any 4xx fails a
browser test by design.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.ui.support import tid

pytestmark = pytest.mark.ui


def test_settings_shows_the_resumes_folder_and_changes_it(ui, tmp_path: Path) -> None:
    ui.goto("/#/settings")
    folder = ui.page.locator(tid("resumes-folder"))
    folder.wait_for()
    served = ui.page.evaluate("() => fetch('/api/resumes-folder').then((response) => response.json())")
    default = f"{served['shown']} (the default)"
    assert served["source"] == "default" and served["shown"].startswith("~/") and served["shown"].endswith("/resumes")
    assert "Documents" not in served["path"], "a temporary home never uses the Documents folder"
    assert folder.inner_text().strip() == default
    assert folder.get_attribute("data-source") == "default"
    assert ui.page.locator(tid("resumes-folder-default")).count() == 0
    save = ui.page.locator(tid("resumes-folder-save"))
    assert save.is_disabled()
    ui.step("shown")

    chosen = tmp_path / "my resumes"
    ui.page.fill(tid("resumes-folder-input"), str(chosen))
    save.click()
    ui.page.locator(f'{tid("resumes-folder")}[data-source="setting"]').wait_for()
    assert folder.inner_text().strip() == str(chosen) and chosen.is_dir()
    assert ui.requests_after("shown", "/api/resumes-folder") == 1
    ui.step("chosen")

    ui.page.locator(tid("resumes-folder-default")).click()
    ui.page.locator(f'{tid("resumes-folder")}[data-source="default"]').wait_for()
    assert folder.inner_text().strip() == default
    assert ui.requests_after("chosen", "/api/resumes-folder") == 1
