"""0.1.11.4 J3: the jobs folder in the browser (real browser, real server, the opener stubbed).

The session's HOME is a temporary directory and the GigAI home is its ``.gigai`` (tools.media.demo_home), so the jobs
folder is the default ``~/Documents/GigAI/jobs`` of that temporary HOME: the real Documents folder is never involved. The test
server runs with ``GIGAI_SCOUT_OPEN_FOLDER_TEST=1`` (tools/media/demo_home.SEAM_ENV): "Open folder" writes the folder
it was asked to show to ``<GigAI home>/open-folder-test.log`` instead of opening a window.

First test, the job page: the hero job's line names its folder (``In your jobs folder: ~/.../jobs/<company>/<role>/resume.md``),
"Open folder" is ONE write (``POST /api/jobs-folder/open``, no path in the request) and the opener got exactly the job's
own folder; the page then says what was opened.

Second test, Settings: the Jobs folder panel shows the default with no "Use the default" button, Save is disabled until
another folder is typed, saving shows the chosen folder (``data-source="setting"``) in one request, "Open folder" opens it,
"Use the default" brings the default back, and the note says the resumes folder below it is the older one. A refused
path (422) is covered over HTTP (tests/api_e2e/test_edited_tailored_resume_journey.py): any 4xx fails a browser test.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest

from tests.ui.support import tid

pytestmark = pytest.mark.ui


def _opened(scout_server) -> list[str]:
    log = scout_server.gigai_home / "open-folder-test.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_the_job_page_opens_the_jobs_own_folder(ui, scout_server) -> None:
    demo = scout_server.demo
    key = f"profile_id={quote(demo.hero_profile_id, safe='')}&job_identity={quote(demo.hero_job, safe='')}"
    job = ui.server_json(f"/api/jobs-folder?{key}")["job"]
    assert job, "the hero job has its own folder"

    ui.goto("/#/jobs/" + quote(demo.hero_job, safe=""))
    ui.wait_for_job_page()
    line = ui.page.locator(f"#job-resume {tid('jobs-folder-file')}")
    line.wait_for()
    shown = f"{job['shown']}/resume.md"
    assert line.locator("code").text_content() == shown and (line.text_content() or "").startswith(f"In your jobs folder: {shown}")
    ui.step("shown")
    before = _opened(scout_server)

    with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/jobs-folder/open")) as sent:
        line.locator(tid("jobs-folder-open")).click()
    assert sent.value.post_data_json == {"profile_id": demo.hero_profile_id, "job_identity": demo.hero_job}, "an id pair, never a path"
    note = ui.page.locator(f"{tid('jobs-folder-note')}")
    note.wait_for()
    assert (note.text_content() or "").strip() == f"Opened {job['shown']}."
    assert ui.writes_after("shown") == ["POST /api/jobs-folder/open"]
    assert _opened(scout_server) == [*before, str(Path(job["path"]).resolve())]
    ui.assert_clean()


def test_settings_shows_the_jobs_folder_and_changes_it(ui, scout_server, tmp_path: Path) -> None:
    ui.goto("/#/settings")
    folder = ui.page.locator(tid("jobs-folder"))
    folder.wait_for()
    served = ui.server_json("/api/jobs-folder")
    default = f"{served['shown']} (the default)"
    assert served["source"] == "default" and served["shown"].startswith("~/") and served["shown"] == "~/Documents/GigAI/jobs"
    assert served["path"].startswith(str(scout_server.home)), "the folder is inside the temporary HOME"
    assert folder.locator("code").inner_text().strip() == default
    assert folder.get_attribute("data-source") == "default"
    assert ui.page.locator(tid("jobs-folder-default")).count() == 0
    save = ui.page.locator(tid("jobs-folder-save"))
    assert save.is_disabled()
    legacy = ui.page.locator("#settings-resumes-folder").inner_text()
    assert "older folder" in legacy and "new picks go to the Jobs folder" in legacy
    ui.step("shown")

    chosen = tmp_path / "my jobs"
    ui.page.fill(tid("jobs-folder-input"), str(chosen))
    save.click()
    ui.page.locator(f'{tid("jobs-folder")}[data-source="setting"]').wait_for()
    assert folder.locator("code").inner_text().strip() == str(chosen) and chosen.is_dir()
    assert ui.requests_after("shown", "/api/jobs-folder") == 1
    ui.step("chosen")

    before = _opened(scout_server)
    ui.page.locator(f'{tid("jobs-folder")} {tid("jobs-folder-open")}').click()
    ui.page.locator(tid("jobs-folder-note")).wait_for()
    assert _opened(scout_server) == [*before, str(chosen.resolve())]
    assert ui.writes_after("chosen")[-1] == "POST /api/jobs-folder/open"

    ui.page.locator(tid("jobs-folder-default")).click()
    ui.page.locator(f'{tid("jobs-folder")}[data-source="default"]').wait_for()
    assert folder.locator("code").inner_text().strip() == default
    ui.assert_clean()
