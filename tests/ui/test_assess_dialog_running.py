"""0.1.11.2 UAT-010, 0.1.11.5 ASSESS-01: the Assess dialog between Approve and the start of the batch.

Real server, real ask; the approving request is held, then answered with a synthetic "batch started" and the cancel
with a synthetic "cancelling", so the shared home is left as it was. While the request is held the dialog says
"Starting N postings…" with N the count the ask returned (never "this can take a minute", and no longer "Nothing has
been assessed yet"), and CANCEL IS ON. Cancel closes the dialog at once; when the held request then answers that its
batch started, the page cancels that batch (one POST to the cancel route). The real batch, its progress and its
cancel are pinned in `test_assess_all_dialog.py`.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from tests.ui.evidence import shot
from tests.ui.support import tid

pytestmark = pytest.mark.ui
ASSESS = "/api/postings/assess"
CANCEL = "/api/postings/assess/cancel"
STATUS = "/api/postings/assess/status"
IDLE = {"schema_version": "scout-assess-batch:1", "running": False, "batch": None, "last": None}


def _batch(status: str, total: int) -> dict:
    return {
        "schema_version": "scout-assess-batch:1", "running": True, "last": None,
        "batch": {"id": "batch_synthetic", "status": status, "total": total, "assessed": 0, "failed": 0, "in_flight": 2, "profile_id": None, "estimate_seconds": None, "pending": [], "here": True},
    }


def test_the_dialog_says_starting_n_postings_and_cancel_works_while_the_approval_is_on_its_way(ui) -> None:
    listed = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    picks = [row["job_identity"] for row in listed if row["state"] == "not_assessed"][:2]
    assert len(picks) == 2
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    for pick in picks:
        ui.page.locator(f"{tid('job-row')}:has(a[href*='{pick.split('/')[-1]}'])").first.locator("input[type='checkbox']").check()
    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == ASSESS) as asked:
        ui.page.click(tid("assess-these"))
    count = asked.value.json()["question"]["to_assess"]
    dialog = ui.page.locator(tid("approval-dialog"))
    dialog.wait_for()
    assert dialog.locator('[data-role="approval-nothing-yet"]').count() == 1

    held: list = []
    cancels: list = []
    ui.page.route(f"**{ASSESS}", lambda route: held.append(route) if route.request.post_data_json.get("approve") else route.continue_())
    ui.page.route(f"**{CANCEL}", lambda route: (cancels.append(route.request.post_data_json), route.fulfill(status=200, content_type="application/json", body=json.dumps({**IDLE, "cancel_requested": 1})))[1])
    ui.page.route(f"**{STATUS}", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(IDLE)))
    dialog.locator('[data-action="approval-approve"]').click()
    line = dialog.locator('[data-role="approval-assessing"]')
    line.wait_for()
    said = line.text_content() or ""
    assert said.startswith(f"Starting {count} posting"), said
    assert "can take a minute" not in said.lower()
    assert dialog.locator('[data-role="approval-nothing-yet"]').count() == 0
    assert "Nothing has been assessed yet" not in (dialog.text_content() or "")
    assert held and held[0].request.post_data_json["background"] is True
    assert dialog.locator('[data-action="approval-cancel"]').is_enabled(), "Cancel must work while the approval is on its way"
    assert (dialog.locator('[data-action="approval-approve"]').text_content() or "").strip() == "Starting…"
    shot(ui, "assess-dialog-starting")

    dialog.locator('[data-action="approval-cancel"]').click()
    dialog.wait_for(state="detached")
    assert cancels == [], "nothing to cancel before the server said a batch started"
    held[0].fulfill(status=202, content_type="application/json", body=json.dumps({**_batch("running", count), "status": "started"}))
    ui.page.wait_for_function("() => true")
    for _ in range(100):
        if cancels:
            break
        ui.page.wait_for_timeout(50)
    assert cancels == [{}], "the batch the cancelled approval started was not cancelled"
    ui.page.locator(tid("assess-batch")).wait_for(state="detached")
    ui.page.unroute_all()
