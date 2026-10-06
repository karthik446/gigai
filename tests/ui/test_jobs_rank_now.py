"""0.1.11.2 RANKUI: the Jobs page shows how far the rank is and ranks from the page. The REAL Scout server.

The demo home has postings of the last 7 days for two profiles; its server ranks with the fixture model
(`bindings._test_model_rank_reply`), inside the day's 100 rank calls. Nothing is answered by the test except in the
last step (the off switch: the real list with `ranking.enabled` false, as a server with ranking off serves it).

Pinned:
- the row's one line is the server's count ("Ranked X of Y (last 7 days) · N not ranked yet", or "Ranked Y of Y (last 7
  days)"), never a silent "not ranked yet" (RANKVIS: tests/ui/test_jobs_rank_row.py pins the row case by case);
- "Rank now" ranks the unranked ones: the line ends at "Ranked Y of Y", the list has no "not ranked yet" row left, and
  the server's own read says the same;
- "Re-rank latest 100" shows the COST FIRST (postings, calls, today's count) and makes no model call until Approve
  (the server's rank calls of the day do not move while the dialog is open, and Cancel leaves them); Approve makes
  exactly the calls the dialog named, and the page says "Re-ranked N postings in 2 calls.";
- RANKORDER: served `ranking.stale_resume` (the list answered as a server whose profile's resume changed serves it),
  the page says "Your master changed since these postings were ranked" with "Re-rank", which opens the same cost
  dialog and calls no model; the real server, where nothing changed, shows no such line;
- with ranking off both buttons say so, are disabled, and the line says how to turn it on;
- a thin posting's row (the real rows) shows the thin wording and no percentage;
- zero console errors.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.ui.evidence import shot
from tests.ui.support import tid

pytestmark = pytest.mark.ui

ROUTE = "/api/postings/rank"
PANEL, LINE, NOTICE = tid("rank-panel"), tid("rank-status-line"), tid("rank-notice")
RANK_NOW, RERANK, DIALOG = tid("rank-now"), tid("rerank-latest"), tid("rerank-dialog")
STALE_LINE, RERANK_STALE = tid("stale-resume-line"), tid("rerank-stale")
HOW_TO = 'set "rank": {"enabled": true}'


def _text(ui, selector: str) -> str:
    return " ".join((ui.page.locator(selector).first.text_content() or "").split())


def _totals(read: dict) -> tuple[int, int]:
    rows = read["ranking"]["by_profile"]
    return sum(item["ranked"] for item in rows), sum(item["total"] for item in rows)


def _wait_idle(ui, seconds: float = 60) -> dict:
    """The server's own read once no rank job runs."""

    deadline = time.monotonic() + seconds
    while True:
        read = ui.server_json(ROUTE, {})
        if not (read["job"] and read["job"]["state"] == "running"):
            return read
        assert time.monotonic() < deadline, f"the rank job still runs after {seconds} s: {read['job']}"
        time.sleep(0.3)


def _open(ui) -> None:
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.page.locator(PANEL).wait_for()
    ui.settle()


def test_the_jobs_page_shows_how_far_the_rank_is_ranks_now_and_re_ranks_with_the_cost_first(ui) -> None:
    before = _wait_idle(ui)
    assert before["enabled"] is True, before
    ranked, total = _totals(before)
    assert total > 0, "the demo home has no posting of the last 7 days: nothing to rank"

    _open(ui)
    unranked = total - ranked
    all_ranked = f"Ranked {total} of {total} (last 7 days)"
    if unranked:
        # Not ranked, and the page says so with the count; "Rank now" is on.
        assert _text(ui, LINE) == f"Ranked {ranked} of {total} (last 7 days) · {unranked} not ranked yet"
        assert ui.page.locator(RANK_NOW).is_enabled() and _text(ui, RANK_NOW) == "Rank now"
        shot(ui, "jobs-rank-panel-unranked")
        for _attempt in range(3):  # the server's own background rank may hold the lane for a moment: the click is repeated
            if ui.page.locator(RANK_NOW).is_disabled():
                break
            ui.page.locator(RANK_NOW).click()
            ui.page.wait_for_function(
                "([line, notice, done]) => (document.querySelector(line)?.textContent || '').trim() === done || !!document.querySelector(notice)",
                arg=[LINE, NOTICE, all_ranked], timeout=60000,
            )
            ui.settle()
            if _text(ui, LINE) == all_ranked:
                break
            _wait_idle(ui)
            _open(ui)
    # The end outcome: every in-window posting is ranked, on the page, in the list and in the server's read.
    assert _text(ui, LINE) == all_ranked
    assert ui.page.locator(RANK_NOW).is_disabled() and ui.page.locator(RERANK).is_enabled()
    assert _text(ui, RANK_NOW) == "Rank now: nothing to rank"
    after = _wait_idle(ui)
    assert _totals(after) == (total, total) and after["ranking"]["in_progress"] is False
    if unranked:
        assert after["calls_today"]["used"] > before["calls_today"]["used"]
    listed = ui.server_json("/api/postings?limit=200&window=7d")
    assert listed["postings"]["rows"] and all(row["rank_score"] is not None for row in listed["postings"]["rows"])
    shot(ui, "jobs-rank-panel-all-ranked")

    # A thin posting's row: the thin wording, and no percentage (the real server's rows).
    scores = ui.page.locator(f"{tid('job-row')} [data-role='score']").all_text_contents()
    thin = [text for text in scores if "thin posting" in text.lower()]
    assert all("not enough requirements to score" in text and "%" not in text for text in thin), thin
    served_thin = [row for row in ui.server_json("/api/postings?limit=200")["postings"]["rows"] if row["thin_posting"]]
    assert all(row["fit"] is None and "%" not in row["score_text"] for row in served_thin), [row["score_text"] for row in served_thin]

    # Re-rank latest 100: THE COST FIRST. The ask and the open dialog make no model call; Cancel leaves the count.
    used = after["calls_today"]["used"]
    plan = ui.server_json(ROUTE, {"mode": "latest"})["plan"]
    assert (plan["postings"], plan["allowed"]) == (total, True) and 1 <= plan["calls"] <= 2
    ui.page.locator(RERANK).click()
    ui.page.locator(DIALOG).wait_for()
    calls = plan["calls"]
    call_word = "call" if calls == 1 else "calls"
    assert _text(ui, f"{DIALOG} h2") == f"Re-rank the latest {total} postings?"
    assert _text(ui, f"{DIALOG} [data-role='rerank-cost']") == f"Cost: {calls} model {call_word} (up to 50 postings a call, at most 2 calls)"
    assert _text(ui, f"{DIALOG} [data-role='rerank-today']") == f"Today: {used} of 100 rank calls used today; {100 - used} left"
    assert "Ranking starts only when you approve" in _text(ui, f"{DIALOG} [data-role='rerank-nothing-yet']")
    assert _text(ui, f"{DIALOG} [data-action='rerank-approve']") == f"Approve and re-rank ({calls} {call_word})"
    shot(ui, "jobs-rerank-dialog-cost")
    assert ui.server_json(ROUTE, {})["calls_today"]["used"] == used  # nothing ran for the ask
    ui.page.locator(f"{DIALOG} [data-action='rerank-cancel']").click()
    ui.page.locator(DIALOG).wait_for(state="detached")
    read = ui.server_json(ROUTE, {})
    assert read["calls_today"]["used"] == used and not (read["job"] and read["job"]["state"] == "running")

    # Approve: the calls the dialog named, no more; already-ranked postings are ranked again; the page says what it did.
    for _attempt in range(3):
        ui.page.locator(RERANK).click()
        ui.page.locator(DIALOG).wait_for()
        ui.page.locator(f"{DIALOG} [data-action='rerank-approve']").click()
        ui.page.locator(DIALOG).wait_for(state="detached")
        ui.page.locator(NOTICE).wait_for(timeout=60000)
        if _text(ui, NOTICE).startswith("Re-ranked"):
            break
        _wait_idle(ui)  # the background rank held the lane: asked again
    assert _text(ui, NOTICE) == f"Re-ranked {total} postings in {calls} {call_word}."
    done = _wait_idle(ui)
    assert (done["job"]["mode"], done["job"]["outcome"], done["job"]["calls"], done["job"]["ranked"]) == ("latest", "ran", calls, total)
    assert done["calls_today"]["used"] == used + calls and _totals(done) == (total, total)
    ui.settle()
    assert _text(ui, LINE) == all_ranked
    shot(ui, "jobs-rerank-done")

    # 0.1.11.2 RANKORDER: A CHANGED MASTER OFFERS A RE-RANK. Nothing changed on this server: no line. Then the list as a
    # server whose profile's resume changed after the rank serves it (`ranking.stale_resume`; the real flag is proven in
    # tests/behaviors/scout_pipeline/test_rank_resume_stale.py): the line, and "Re-rank" opens the SAME cost dialog.
    assert ui.server_json("/api/postings?limit=1")["ranking"]["stale_resume"] is False
    assert ui.page.locator(STALE_LINE).count() == 0

    def master_changed(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        if isinstance(body.get("ranking"), dict):
            body["ranking"]["stale_resume"] = True
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.goto("about:blank")
    ui.page.route("**/api/postings?*", master_changed)
    ui.page.route("**/api/postings", master_changed)
    _open(ui)
    used = ui.server_json(ROUTE, {})["calls_today"]["used"]
    assert _text(ui, f"{STALE_LINE} [role='status']") == "Your master changed since these postings were ranked"
    assert _text(ui, RERANK_STALE) == "Re-rank" and ui.page.locator(RERANK_STALE).is_enabled()
    assert _text(ui, RERANK) == "Re-rank latest 100" and ui.page.locator(RERANK).is_enabled()  # always there
    shot(ui, "jobs-master-changed-rerank-offer")
    ui.page.locator(RERANK_STALE).click()
    ui.page.locator(DIALOG).wait_for()
    assert _text(ui, f"{DIALOG} h2") == f"Re-rank the latest {total} postings?"
    assert _text(ui, f"{DIALOG} [data-role='rerank-cost']") == f"Cost: {calls} model {call_word} (up to 50 postings a call, at most 2 calls)"
    assert _text(ui, f"{DIALOG} [data-role='rerank-today']") == f"Today: {used} of 100 rank calls used today; {100 - used} left"
    assert ui.server_json(ROUTE, {})["calls_today"]["used"] == used  # the click asked; no model was called
    ui.page.locator(f"{DIALOG} [data-action='rerank-cancel']").click()
    ui.page.locator(DIALOG).wait_for(state="detached")
    read = ui.server_json(ROUTE, {})
    assert read["calls_today"]["used"] == used and not (read["job"] and read["job"]["state"] == "running")
    ui.page.unroute_all()

    # The off switch, as a server with ranking off serves the list: the buttons say so and the line says how to turn it on.
    def ranking_off(route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        if isinstance(body.get("ranking"), dict):
            body["ranking"]["enabled"] = False
            body["ranking"]["stale_resume"] = True  # a changed master is not offered a re-rank that would be refused
            body["ranking"]["in_progress"] = False
            body["ranking"]["by_profile"][0]["total"] += 3  # three postings nothing ranked
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    ui.page.goto("about:blank")
    ui.page.route("**/api/postings?*", ranking_off)
    ui.page.route("**/api/postings", ranking_off)
    _open(ui)
    assert _text(ui, LINE).startswith(f"Ranked {total} of {total + 3} (last 7 days) · Ranking is off. To turn it on, ") and HOW_TO in _text(ui, LINE)
    assert (_text(ui, RANK_NOW), _text(ui, RERANK)) == ("Rank now: ranking is off", "Re-rank latest 100: ranking is off")
    assert ui.page.locator(RANK_NOW).is_disabled() and ui.page.locator(RERANK).is_disabled()
    assert ui.page.locator(STALE_LINE).count() == 0
    assert HOW_TO in (ui.page.locator(RANK_NOW).get_attribute("title") or "")
    shot(ui, "jobs-rank-panel-off")
    ui.page.unroute_all()
    ui.assert_clean()
