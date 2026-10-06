"""0.1.11.3 packet 1: "Pick it now" picks. Small home, REAL server: nothing here is answered by the test.

The operator's dead end (2026-10-06): a fully met job said "No resume is stored for this job yet", and its button
"Pick it now · no model call" answered a red developer message ("scout.pick.settle_stored is not part of it"),
because the step behind `POST /api/job-resumes/pick` `{action: "refresh"}` had never been built. This flow clicks
that button on a real job page:

- a Matched job with a stored assessment and no stored resume, on a home with a master resume: the panel says
  "No resume is stored for this job yet" and offers the ONE button, with its cost in it;
- the click is ONE `POST /api/job-resumes/pick` (`refresh`), no model route, and the resume it picked is shown
  without a reload: made by the pick, with its ONE provenance line and the Apply button;
- no error is shown, and no word of the page names a module, a function or an error code.

The words of every refusal the button can get are pinned without a browser in `test_job_resume_model.py`
(`pickErrorText`), and the step itself on the real store in
`tests/behaviors/scout_find_jobs/test_assess_then_picked.py`.

This flow CHANGES the shared home (a master, one job's picked resume), so it runs late (`UI_ORDER`).
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

import pytest

from tests.ui.job_resume_fixtures import stored_resume
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, tid
from tests.ui.test_master_page import ensure_master

pytestmark = pytest.mark.ui
UI_ORDER = 46  # changes the shared home (a master, one job's picked resume): after the Matched job page flow

PAGE = ".job-page"
PANEL = f"{PAGE} {tid('job-resume')}"
APPLY = f"{PAGE} {tid('job-apply')}"
#: The pick measures pages (a PDF layout or two): a first load's patience, not a click's.
PICK_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
_INTERNAL = re.compile(r"scout\.pick|settle_stored|pick_not_available|job_resume_port|\b[a-z]+(?:_[a-z]+)+\b")


def matched_job_without_a_resume(ui) -> dict:
    """A Matched posting of the small home with a CURRENT assessment and no stored job resume.

    The pipeline of this tree stores a resume only for its hero job. A job whose assessment is old is not this
    flow's: the page offers "Re-assess" for it, never a pick (`test_job_resume_model.py`), and an earlier flow that
    changed the master may have made one old. Such a job is assessed again first, through the real route.
    """

    def candidates() -> list[dict]:
        rows = ui.server_json("/api/postings?state=matched&limit=50")["postings"]["rows"]
        return [row for row in rows if row.get("profile_id") and stored_resume(ui, row["profile_id"], row["job_identity"]) is None]

    found = candidates()
    assert found, "the small home has no Matched posting without a stored resume"
    current = [row for row in found if row.get("stale_reason") is None]
    if current:
        return current[0]
    old = found[0]
    ui.server_json("/api/assess", {"job": {"job_url": old["job_identity"]}, "resume": {"profile_id": old["profile_id"]}}, timeout=180)
    again = [row for row in candidates() if row["job_identity"] == old["job_identity"] and row.get("stale_reason") is None]
    assert again, f"{old['job_identity']} is still not a Matched job with a current assessment and no resume after a re-assessment"
    return again[0]


def test_pick_it_now_picks_the_resume_of_a_matched_job_in_one_request(ui, scout_server) -> None:
    ensure_master(ui)
    job = matched_job_without_a_resume(ui)
    profile_id, identity = job["profile_id"], job["job_identity"]

    ui.goto("/#/jobs/" + quote(identity, safe=""))
    ui.wait_for_job_page()
    panel = ui.page.locator(f'{PANEL}[data-state="none"]')
    panel.wait_for()
    ui.step("shown")
    offer = panel.locator('[data-role="no-resume"]')
    assert "No resume is stored for this job yet." in (offer.text_content() or "")
    button = offer.locator('[data-action="repick"]')
    assert (button.text_content() or "").strip() == "Pick it now · no model call"
    ui.settle()
    assert ui.writes_after("shown") == [], "nothing is picked by opening the job"

    ui.step("before-pick")
    with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/job-resumes/pick") as sent:
        button.click()
    picked = ui.page.locator(f'{PANEL}[data-state="stored"]')
    picked.wait_for()
    ui.step("picked")
    assert sent.value.post_data_json == {"job_url": identity, "profile_id": profile_id, "action": "refresh"}
    ui.settle()
    assert [line for line in ui.writes_after("before-pick") if line.endswith("/api/job-resumes/pick")] == ["POST /api/job-resumes/pick"]
    assert not any(line.endswith(("/api/assess", "/api/tailored-resumes")) for line in ui.writes_after("before-pick")), "a pick calls no model route"

    # The END outcome: the job has its picked resume, shown without a reload, and Apply is there for it.
    assert ui.page.locator(f'{PANEL} [data-role="pick-error"]').count() == 0
    assert picked.get_attribute("data-origin") == "pick" and picked.get_attribute("data-gate") == "suggest"
    assert picked.locator(".md-preview, .clean-wrap").count() == 1
    stored = stored_resume(ui, profile_id, identity)
    assert stored is not None and stored["producer"]["callable"] == "scout.pick" and stored.get("selection"), stored and stored.get("producer")
    assert ui.page.locator(f'{APPLY} [data-action="apply"]').count() == 1
    words = ui.page.locator(PANEL).inner_text()
    assert not _INTERNAL.search(words), _INTERNAL.search(words)
    ui.wall_budget("pick a job's resume", PICK_WALL_SECONDS, "before-pick", "picked")
    ui.assert_clean()
