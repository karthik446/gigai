"""0.1.11.5 ASSESS-01: the Assess all dialog and the batch it starts, in a real browser on the REAL server code.

A second Scout server runs in this process on the session's synthetic home (``serve`` with the real backend), so
each model call of a batch can be held open: it is first a real child process that waits for the test
(``tests/support/slow_model_child.py``, started by the real ``adapters.process.run_json_process`` like a
`claude -p` call), then the fixture model. Two calls at a time; a batch of FOUR postings. No network, no real model.

Pinned, by the ticket's part:
(5) the button beside "N not assessed" says how many ("Assess all N" / "Assess top 50 of N"). (Until 0.1.11.9 a row
    shown for another profile also said "Or assess as <profile>": a job has one assessment now, so no row does.)
(3) the dialog never says "This can take a minute";
(4) with nothing ranked low the dialog has no low-rank box;
(2) Approve answers at once (202) and THE DIALOG CLOSES while the batch runs; the page shows "0 of 4 assessed", the
    profile and Cancel, moves to "2 of 4 assessed" as calls finish, and the two rows leave "not assessed" in place;
    a job page of a posting waiting in the batch says "Assessing… this posting is in the running batch";
(1) Cancel on the page: no further model call starts (two started, two never do), the calls in flight are NOT
    killed, the page says it waits for them, and when they end: "Cancelled: 2 of 4 assessed. What finished is kept;
    2 were not started.", exactly two postings are assessed on the server and no model process is left.
Then the two that were never started are assessed by a second batch that runs to its end ("Assessed 2 of 2.").

This flow CHANGES the shared home (four postings become assessed; the small home has six that are not, and "Assess
these" takes two): it runs after every other flow but the profile's delete, so no flow reads a home it changed.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
import time
from urllib.parse import quote, urlsplit

import pytest

from tests.support.slow_model_child import alive, gate_model_calls
from tests.ui.conftest import _ui_session, synthetic_environment
from tests.ui.evidence import shot
from tests.ui.support import tid

pytestmark = pytest.mark.ui
UI_ORDER = 88  # changes the shared home (the last four not-assessed postings become assessed): after every flow but the profile's delete

ASSESS = "/api/postings/assess"
STATUS = "/api/postings/assess/status"
CANCEL = "/api/postings/assess/cancel"


@dataclass(frozen=True)
class _Served:
    url: str
    pid: int
    log_path: str


@pytest.fixture
def batch_ui(request, ui_browser, scout_server, ui_artifacts, tmp_path, monkeypatch):
    """`ui` on a server of THIS process (the session's home), whose model calls the test holds open."""

    from tools.media import demo_home

    with synthetic_environment(scout_server.home):
        os.environ.update(demo_home.SEAM_ENV)
        from gigai.scout.find_jobs import assess_all
        from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

        monkeypatch.setattr(assess_all, "assess_concurrency", lambda cpus=None: 2)
        gate = gate_model_calls(monkeypatch, tmp_path / "gate")
        server = serve(backend=ScoutFindJobsBackend(home_root=scout_server.gigai_home, target=scout_server.target), bind=("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        served = _Served(f"http://127.0.0.1:{server.server_address[1]}", os.getpid(), scout_server.log_path)
        try:
            with _ui_session(request, ui_browser, served, ui_artifacts) as session:
                yield session, gate
        finally:
            gate.release()
            from gigai.scout import assess_batch_job

            assess_batch_job.wait_for_batch(scout_server.gigai_home, scout_server.target, timeout=60)
            server.shutdown()
            server.server_close()
            thread.join(10)


def _rows(ui) -> dict[str, dict]:
    return {row["job_identity"]: row for row in ui.server_json("/api/postings?limit=200")["postings"]["rows"]}


def _row(ui, job: str):
    return ui.page.locator(f'{tid("job-row")}:has(a[href="#/jobs/{quote(job, safe="")}"])')


def _until(read, wanted, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while True:
        found = read()
        if wanted(found):
            return found
        assert time.monotonic() < deadline, f"still {found!r}"
        time.sleep(0.1)


def test_the_dialog_closes_at_the_start_the_page_shows_progress_and_cancel_keeps_what_finished(batch_ui) -> None:
    from gigai.adapters.process import live_children

    ui, gate = batch_ui
    before = _rows(ui)
    waiting = [job for job, row in before.items() if row["state"] == "not_assessed"]
    assert len(waiting) >= 4, f"the home has {len(waiting)} postings that are not assessed; this flow needs four"
    picks = waiting[:4]

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert ui.page.locator(tid("assess-batch")).count() == 0  # no batch runs: no progress row, and no poll
    ui.step("listed")

    # (5) the button says how many. 0.1.11.9: no row offers "Or assess as <role>" (a job has one assessment).
    count = len(waiting)
    assert (ui.page.locator('[data-role="not-assessed-count"]').text_content() or "") == f"{count} not assessed"
    assert (ui.page.locator(tid("assess-all")).text_content() or "").strip() == (f"Assess top 50 of {count}" if count > 50 else f"Assess all {count}")
    assert ui.page.locator('[data-action="assess-as"]').count() == 0
    assert "Or assess as" not in (ui.page.locator(tid("jobs-list")).text_content() or "")

    for pick in picks:
        _row(ui, pick).locator("input[type='checkbox']").check()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == ASSESS) as asked:
        ui.page.click(tid("assess-these"))
    dialog = ui.page.locator(tid("approval-dialog"))
    dialog.wait_for()
    assert asked.value.json()["question"]["batch"] == 4
    # (3) and (4): the estimate, never "a minute"; nothing is ranked low here, so there is no box.
    text = dialog.text_content() or ""
    assert "can take a minute" not in text.lower() and "~4 model calls" in text
    assert dialog.locator(tid("approval-low-rank")).count() == 0 and dialog.locator(tid("approval-low-rank-note")).count() == 0
    assert dialog.locator('[data-action="approval-cancel"]').is_enabled()
    ui.step("asked")

    # (2) Approve STARTS the batch: 202 at once, the dialog closes, the page shows the progress.
    started_at = time.monotonic()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == ASSESS) as approved:
        dialog.locator('[data-action="approval-approve"]').click()
    assert approved.value.status == 202 and approved.value.json()["status"] == "started"
    assert approved.value.request.post_data_json["background"] is True and approved.value.request.post_data_json["approve"] is True
    dialog.wait_for(state="detached")
    assert time.monotonic() - started_at < 20, "the dialog stayed open for the batch"
    progress = ui.page.locator(tid("assess-batch"))
    progress.wait_for()
    first = gate.wait_started(2)
    assert _until(lambda: progress.locator('[data-role="assess-batch-count"]').text_content(), lambda found: found == "0 of 4 assessed")
    label = next(profile["label"] for profile in ui.server_json("/api/postings?limit=1")["profiles"] if profile["profile_id"] == before[picks[0]]["profile_id"])
    assert _until(lambda: progress.locator('[data-role="assess-batch-profile"]').text_content(), lambda found: bool(found)).endswith(label)
    assert progress.get_attribute("data-status") == "running" and progress.locator(tid("assess-batch-cancel")).is_enabled()
    assert ui.page.locator(tid("assess-these")).is_disabled() and ui.page.locator(tid("assess-all")).is_disabled()
    assert len(gate.started()) == 2, "two at a time"
    shot(ui, "assess-batch-progress")

    # A job page of a posting that waits in the batch says so.
    status = ui.server_json(STATUS)
    assert status["batch"]["total"] == 4 and status["batch"]["in_flight"] == 2 and sorted(status["batch"]["pending"]) == sorted(picks)
    page = ui.page.context.new_page()
    try:
        page.goto(ui.server.url + "/#/jobs/" + quote(picks[3], safe=""))
        line = page.locator(tid("job-assess-batch"))
        line.wait_for()
        assert (line.text_content() or "") == "Assessing… this posting is in the running batch (0 of 4 assessed)."
        assert page.get_by_role("button", name="Assess", exact=True).count() == 0, "a posting waiting in the batch must not offer a second Assess"
    finally:
        page.close()

    # (1) Cancel: no further call; the two in flight are not killed and the page says it waits for them.
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == CANCEL) as cancelled:
        progress.locator(tid("assess-batch-cancel")).click()
    assert cancelled.value.status == 200 and cancelled.value.json()["cancel_requested"] == 1
    # 0.1.11.5 B3b: the row says how many calls it is finishing (the status's `in_flight`), never a bare "Cancelling…".
    cancelling_line = progress.locator('[data-role="assess-batch-cancelling"]')
    cancelling_line.wait_for()
    assert (cancelling_line.text_content() or "") == "Cancelling: finishing the 2 in flight"
    assert (progress.locator('[data-role="assess-batch-waiting"]').text_content() or "") == "No further model call starts. What finished is kept."
    assert "Cancelling…" not in (progress.text_content() or "") and (progress.locator(tid("assess-batch-cancel")).text_content() or "").strip() == "Cancel"
    assert progress.get_attribute("data-status") == "cancelling" and progress.locator(tid("assess-batch-cancel")).is_disabled()
    time.sleep(0.5)
    assert all(alive(pid) for pid in first), "cancel killed a call in flight"
    assert len(gate.started()) == 2 and all(row["state"] == "not_assessed" for job, row in _rows(ui).items() if job in picks)
    shot(ui, "assess-batch-cancelling")

    gate.release()
    notice = ui.page.locator('[data-role="assess-notice"]')
    notice.wait_for(timeout=60_000)
    assert notice.text_content() == "Cancelled: 2 of 4 assessed. What finished is kept; 2 were not started."
    progress.wait_for(state="detached")
    after = _rows(ui)
    done = [job for job in picks if after[job]["state"] != "not_assessed"]
    assert len(done) == 2, f"what finished is kept: {[after[job]['state'] for job in picks]}"
    assert len(gate.started()) == 2, "a cancelled batch started another model call"
    _until(lambda: [pid for pid in first if alive(pid)], lambda left: left == [])
    assert live_children() == (), "a model call was left running"
    # The list was read again: the two rows show the server's state without a reload.
    for job in done:
        ui.page.locator(f'{tid("job-row")}:not([data-state="not_assessed"]):has(a[href="#/jobs/{quote(job, safe="")}"])').wait_for()
    assert (ui.page.locator(tid("assess-all")).text_content() or "").strip() == f"Assess all {count - 2}"
    assert (ui.page.locator(tid("assess-these")).text_content() or "").strip() == "Assess these", "the selection was not cleared"
    shot(ui, "assess-batch-cancelled")

    # The rest: a second batch that runs to its end (no call is held any more).
    rest = [job for job in picks if job not in done]
    for job in rest:
        _row(ui, job).locator("input[type='checkbox']").check()
    ui.page.click(tid("assess-these"))
    dialog.wait_for()
    assert dialog.locator("#assess-approval-title").text_content() == "Assess 2 postings?"
    dialog.locator('[data-action="approval-approve"]').click()
    dialog.wait_for(state="detached")
    ui.page.locator('[data-role="assess-notice"]:has-text("Assessed 2 of 2.")').wait_for(timeout=60_000)
    for job in rest:
        ui.page.locator(f'{tid("job-row")}:not([data-state="not_assessed"]):has(a[href="#/jobs/{quote(job, safe="")}"])').wait_for()
    assert all(_rows(ui)[job]["state"] != "not_assessed" for job in picks)
    assert ui.server_json(STATUS)["last"]["status"] == "done" and live_children() == ()
    ui.settle()
    assert ui.requests_after("listed", STATUS) < 200  # the poll runs only while a batch does
    ui.assert_clean()
