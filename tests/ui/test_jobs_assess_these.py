"""Flow 4 (REPORT.md 5.3): "Assess these". The dialog asks first; Approve assesses, on the fixture model.

Real server, nothing stubbed: the ask is the real route's count and estimate, and the approval runs the real
assessment on the fixture model the small home is set up with (no network, no real model). The stubbed variant
(`test_jobs_weak_fit.py`) covers the low-ranked second question, which this home has none of.

This flow CHANGES the shared home (two postings become assessed), so it runs after the flows that only read it
(`UI_ORDER`).

Pinned: with two not-assessed rows ticked the button says "(2 selected)"; the click is ONE request that approves
nothing, and the dialog says the server's count, an estimate and "Nothing has been assessed yet"; Cancel closes it
and nothing was assessed (the server's states are what they were); asked again, Approve sends the body the server
named for the yes, the page says "Assessed 2 of 2.", the two rows leave "not assessed" in place (one list read, no
reload) and the server agrees; the selection is cleared. Zero console errors.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): the ask 0.07 to 0.08 s; the approval
(two fixture-model assessments) 0.73 to 0.77 s wall, 0.47 to 0.49 server CPU seconds.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

import pytest

from tests.ui.support import INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui
UI_ORDER = 10  # changes the shared home: after the flows that only read it

ASSESS = "/api/postings/assess"
ASK_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
APPROVE_WALL_SECONDS = 15.0  # two model calls on the fixture model: 0.73 to 0.77 s measured
APPROVE_CPU_SECONDS = 3.0  # 0.47 to 0.49 measured


def row_of(ui, job_identity: str):
    return ui.page.locator(f'{tid("job-row")}:has(a[href="#/jobs/{quote(job_identity, safe="")}"])')


def states(ui, picks: list[str]) -> dict[str, str]:
    rows = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    return {row["job_identity"]: row["state"] for row in rows if row["job_identity"] in picks}


def ask(ui, name: str) -> dict:
    """Click "Assess these" and return the server's answer to the ask (the dialog is open after this)."""

    ui.step(f"before-{name}")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == ASSESS) as answered:
        ui.page.click(tid("assess-these"))
    ui.page.locator(tid("approval-dialog")).wait_for()
    ui.step(name)
    return answered.value


def test_assess_these_asks_first_and_approve_assesses(ui) -> None:
    listed = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    picks = [row["job_identity"] for row in listed if row["state"] == "not_assessed"][:2]
    assert len(picks) == 2, "the small home has two postings that are not assessed yet"

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    for pick in picks:
        assert row_of(ui, pick).get_attribute("data-state") == "not_assessed"
        row_of(ui, pick).locator("input[type='checkbox']").check()
    button = ui.page.locator(tid("assess-these"))
    assert (button.text_content() or "").strip() == "Assess these (2 selected)"
    assert ui.writes_after("start") == []

    # The ask: one request that approves nothing; the dialog says the server's count and estimate.
    response = ask(ui, "asked")
    assert response.request.post_data_json == {"jobs": picks}
    answer = response.json()
    assert answer["status"] == "ask" and answer["question"]["to_assess"] == 2 and answer["assessed"] is None
    dialog = ui.page.locator(tid("approval-dialog"))
    assert dialog.locator("#assess-approval-title").text_content() == "Assess 2 postings?"
    assert "2" in (dialog.locator('[data-role="approval-estimate"]').text_content() or "")
    assert (dialog.locator('[data-role="approval-nothing-yet"]').text_content() or "").startswith("Nothing has been assessed yet.")
    assert dialog.locator(tid("approval-low-rank")).count() == 0, "nothing here is ranked below the threshold"
    assert ui.writes_after("before-asked") == [f"POST {ASSESS}"]
    assert states(ui, picks) == {pick: "not_assessed" for pick in picks}, "the ask assessed something"
    ui.wall_budget("the Assess these question", ASK_WALL_SECONDS, "before-asked", "asked")

    # Cancel: closed, and nothing was assessed.
    dialog.locator('[data-action="approval-cancel"]').click()
    dialog.wait_for(state="detached")
    assert ui.writes_after("asked") == [] and states(ui, picks) == {pick: "not_assessed" for pick in picks}

    # Asked again and approved: the body the server named for the yes, and the two rows are assessed in place.
    yes = ask(ui, "asked-again").json()["question"]["yes"]["api"]["body"]
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == ASSESS, timeout=60_000) as approved:
        dialog.locator('[data-action="approval-approve"]').click()
    notice = ui.page.locator('[data-role="assess-notice"]')
    notice.wait_for()
    ui.step("assessed")
    assert approved.value.request.post_data_json == yes and yes["approve"] is True and yes["jobs"] == picks
    done = approved.value.json()
    assert done["status"] == "assessed" and done["assessed"]["assessed"] == 2 and done["assessed"]["failed"] == []
    assert notice.text_content() == "Assessed 2 of 2."
    assert dialog.count() == 0
    for pick in picks:
        ui.page.locator(f'{tid("job-row")}:not([data-state="not_assessed"]):has(a[href="#/jobs/{quote(pick, safe="")}"])').wait_for()
    after = states(ui, picks)
    assert "not_assessed" not in after.values(), f"the server still lists them as not assessed: {after}"
    assert {pick: row_of(ui, pick).get_attribute("data-state") for pick in picks} == after, "the rows do not show the server's states"
    assert (button.text_content() or "").strip() == "Assess these", "the selection was not cleared"
    ui.settle()
    ui.step("settled")
    assert ui.writes_after("asked-again") == [f"POST {ASSESS}"]
    assert ui.requests_between("asked-again", "settled", "/api/postings") == 1, "the rows are refreshed in place by one list read"
    ui.no_more_than_one_in_flight("/api/postings")
    ui.cpu_budget("approve and assess two postings (fixture model)", APPROVE_CPU_SECONDS, "asked-again", "assessed")
    ui.wall_budget("approve and assess two postings (fixture model)", APPROVE_WALL_SECONDS, "asked-again", "assessed")
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
