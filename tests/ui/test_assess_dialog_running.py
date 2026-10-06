"""0.1.11.2 UAT-010: the Assess dialog while the approved batch runs.

Real server, real ask; the approve request is held, then answered with a synthetic "nothing assessed", so the shared home is left as it was. While it
is held the dialog says "Assessing N postings…" with N the count the ask returned, and no longer says nothing has
been assessed yet; Cancel and Approve are off.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from tests.ui.evidence import shot
from tests.ui.support import tid

pytestmark = pytest.mark.ui
ASSESS = "/api/postings/assess"


def test_the_dialog_says_assessing_n_postings_while_the_batch_runs(ui) -> None:
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
    ui.page.route(f"**{ASSESS}", lambda route: held.append(route) if route.request.post_data_json.get("approve") else route.continue_())
    dialog.locator('[data-action="approval-approve"]').click()
    line = dialog.locator('[data-role="approval-assessing"]')
    line.wait_for()
    assert (line.text_content() or "").startswith(f"Assessing {count} posting"), line.text_content()
    assert dialog.locator('[data-role="approval-nothing-yet"]').count() == 0
    assert "Nothing has been assessed yet" not in (dialog.text_content() or "")
    assert dialog.locator('[data-action="approval-cancel"]').is_disabled()
    assert (dialog.locator('[data-action="approval-approve"]').text_content() or "").strip() == "Assessing…"
    shot(ui, "assess-dialog-running")
    held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"status": "assessed", "assessed": {"assessed": 0, "failed": []}}))
    ui.page.unroute_all()
    dialog.wait_for(state="detached")
