"""0.1.11.2 RANK-B: the Jobs page by rank: the collapse line, "N not assessed" + "Assess all", the top 50 by rank.

The demo home has no rank score (that needs the rank lane's model), so, as in `test_jobs_weak_fit.py`, the browser
talks to the REAL Scout server for everything except `GET /api/postings` and `POST /api/postings/assess`, which this
test answers itself (the first real response as the template: its profiles and row shape) the way the real routes
do: 60 not-assessed postings in rank order (58 ranked, every score 50 or more; two not ranked yet, last),
three more ranked below 50 that are left out unless `state=ranked_low` asks, `counts.ranked_low`, and `ranking` while
the background rank still runs. The real routes' own rules (the order, the collapse, the 50 highest of 60 and what an
approval assesses) are proven on the real server in `tests/behaviors/scout_pipeline/test_rank_order_and_top_batch.py`;
here the page, the client list store and the hash router are the real ones.

Pinned: the rows are drawn in the server's rank order and a ranked-low posting is not among them; a posting not
ranked yet is at the bottom and says "not ranked yet"; "3 weak fits, ranked low: show" lists them (their chip, the
address carries `state=ranked_low`) and "back to the list" closes it; "60 not assessed" with "Assess all", which asks
first (never `approve`, never `jobs`) and opens the existing dialog: "Assess the top 50 by rank of 60 postings?", the
estimate, the model, "10 more after these 50", and that ranking still runs; Approve sends the server's own yes, and
the line then reads "10 not assessed"; "Assess these" (select and assess) is still there; zero console errors.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.ui.support import tid

pytestmark = pytest.mark.ui

#: 60 not-assessed postings in the server's order: 58 ranked (99 down to 50; the score repeats near the end), then two not ranked yet.
RANKS: list[int | None] = [max(50, 99 - n) for n in range(58)] + [None, None]
#: Not assessed and ranked below 50: collapsed out of the list.
LOW = [49, 31, 12]
RANKING = {"enabled": True, "in_progress": True, "by_profile": [{"profile_id": "p", "ranked": 61, "total": 63}]}
RANKING_WORDS = "Ranking is still running: 61 of 63 ranked. The order, and the top 50 by rank, are of what is ranked so far."


def _row(template: dict, name: str, rank: int | None, *, assessed: bool = False, low: bool = False) -> dict:
    row = dict(template)
    url = f"https://jobs.lever.co/synthetic/{name}"
    said = f"rank {rank}" if rank is not None else "not ranked yet"
    row.update({
        "job_identity": url, "normalized_url": url, "title": f"Posting {name}", "state": "matched" if assessed else "not_assessed",
        "fit": 100 if assessed else None, "rank_score": rank, "label": None, "ats_score": None, "tailored": False, "stale_reason": None,
        "stale_label": None, "sort_group": "current" if assessed else "not_assessed", "open_questions": [], "ranked_low": low,
        "score_text": f"Matched · fit 100% · {said}" if assessed else f"{said} · not assessed",
    })
    return row


class Answers:
    """GET /api/postings and POST /api/postings/assess, as the real routes answer; `assessed` is how many an approval assessed."""

    def __init__(self) -> None:
        self.template: dict | None = None
        self.listed: list[str] = []
        self.bodies: list[dict] = []
        self.assessed = 0

    def __call__(self, route) -> None:
        url = urlsplit(route.request.url)
        if url.path == "/api/postings/assess":
            self._assess(route)
        elif url.path == "/api/postings":
            self._list(route, url.query)
        else:
            route.continue_()

    def _list(self, route, query: str) -> None:
        if self.template is None:
            self.template = route.fetch().json()  # the real server's own answer, once
        self.listed.append(query)
        asked = parse_qs(query)
        first = self.template["postings"]["rows"][0]
        if asked.get("state") == ["ranked_low"]:
            rows = [_row(first, f"low-{index:02d}", rank, low=True) for index, rank in enumerate(LOW, start=1)]
        else:
            # The top `assessed` by rank have a verdict now and lead the list; the rest keep the rank order.
            rows = [_row(first, f"job-{index:02d}", rank, assessed=index <= self.assessed) for index, rank in enumerate(RANKS, start=1)]
        by_state: dict[str, int] = {}
        for row in rows:
            by_state[row["state"]] = by_state.get(row["state"], 0) + 1
        limit, offset = int(asked.get("limit", ["50"])[0]), int(asked.get("offset", ["0"])[0])
        page = rows[offset:offset + limit]
        body = json.loads(json.dumps(self.template))
        body["counts"].update({"matched": len(rows), "shown": len(page), "by_state": by_state, "weak_fit": 0, "ranked_low": len(LOW)})
        body["postings"]["rows"] = page
        body["ranking"] = RANKING
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def _assess(self, route) -> None:
        body = json.loads(route.request.post_data or "{}")
        self.bodies.append(body)
        left = len(RANKS) - self.assessed
        batch = min(50, left)
        estimate = {"calls": batch, "tokens": 975000, "seconds": 560.0, "cost": None, "basis_calls": 12}
        yes = {"approve": True, "states": ["not_assessed"]}
        answer: dict = {
            "schema_version": "scout-postings-assess:1", "checked_at": "2026-10-03T15:00:00.000000Z", "not_found": [], "low_rank": None,
            "counts": {"selected": left, "to_assess": left, "already_current": 0, "not_found": 0, "low_rank_skipped": 0, "batch": batch, "more_after": left - batch},
            "postings": {"_labels": {}, "rule": "", "rows": []}, "profiles": [], "approval": None, "assessed": None, "question": None,
            "ranking": RANKING, "model_input_summary": None,
        }
        if body.get("approve"):
            self.assessed += batch
            answer.update({
                "status": "assessed", "assessed": {"requested": batch, "assessed": batch, "failed": [], "stopped": None, "fetched_on_demand": 0},
                "approval": {"id": "apv_" + "0" * 32, "decided_by": "operator", "jobs": batch},
            })
        else:
            answer.update({
                "status": "ask",
                "question": {
                    "kind": "assess_these", "selected": left, "to_assess": left, "already_current": 0, "low_rank_skipped": 0, "batch": batch,
                    "more_after": left - batch, "by_profile": [], "model_target": "codex_cli", "estimate": estimate,
                    "text": f"Assess the top {batch} by rank of {left} postings? ~{batch} calls ({left - batch} more after these {batch})",
                    "yes": {"api": {"method": "POST", "path": "/api/postings/assess", "body": yes}},
                },
            })
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))


def _wait_for_first(ui, title: str) -> None:
    ui.page.wait_for_function(
        """(title) => {
          const first = document.querySelector('[data-testid="job-row"] [data-action="open-job"]');
          const line = document.querySelector('[data-role="postings-count"]');
          return first && first.textContent === title && line && line.dataset.refreshing !== 'true';
        }""",
        arg=title,
    )


def _rows(ui) -> list[dict]:
    return ui.page.locator(tid("job-row")).evaluate_all(
        """(rows) => rows.map((row) => ({
          title: row.querySelector('[data-action="open-job"]').textContent,
          state: row.dataset.state,
          rank: row.dataset.rank === undefined ? null : Number(row.dataset.rank),
          score: row.querySelector('[data-role="score"]').textContent,
          chips: Array.from(row.querySelectorAll('[data-role="state-chips"] .state-pill')).map((chip) => chip.textContent),
        }))"""
    )


def _text(ui, selector: str) -> str:
    return " ".join((ui.page.locator(selector).first.text_content() or "").split())


def test_jobs_come_by_rank_weak_fits_are_collapsed_and_assess_all_takes_the_top_50_by_rank(ui) -> None:
    answers = Answers()
    ui.page.route("**/api/postings*", answers)
    ui.page.route("**/api/postings/assess", answers)
    low_line, waiting = tid("ranked-low-line"), tid("not-assessed-line")
    notice = ui.page.locator('[data-role="assess-notice"]')

    ui.goto("/#/jobs")
    _wait_for_first(ui, "Posting job-01")
    ui.step("default")

    # The rows are the server's, in its order: best rank first. No ranked-low posting is among them.
    rows = _rows(ui)
    assert [row["rank"] for row in rows] == RANKS[:50] and rows[0]["score"] == "rank 99 · not assessed"
    assert [row["rank"] for row in rows] == sorted((row["rank"] for row in rows), reverse=True)
    assert min(row["rank"] for row in rows) >= 50 and ui.page.locator(tid("ranked-low-chip")).count() == 0
    assert all("state=" not in query for query in answers.listed), answers.listed  # the default list asks for no state

    # The collapse line, what waits for an assessment, and how far the background rank is.
    assert _text(ui, low_line) == "3 weak fits, ranked low: show"
    assert _text(ui, f"{waiting} [data-role='not-assessed-count']") == "60 not assessed"
    assert _text(ui, tid("assess-all")) == "Assess all" and ui.page.locator(tid("assess-these")).count() == 1  # select-and-assess stays
    assert _text(ui, tid("ranking-line")) == RANKING_WORDS

    # A posting not ranked yet stays in the list, at the bottom, and says so.
    ui.goto("/#/jobs?page=2")
    _wait_for_first(ui, "Posting job-51")
    last = _rows(ui)
    assert [row["rank"] for row in last] == RANKS[50:] and [row["score"] for row in last[-2:]] == ["not ranked yet · not assessed"] * 2
    assert all(row["chips"] == ["Not assessed"] for row in last), last  # never "Ranked low": nothing says its rank is low

    # The count line opens the collapsed ones: their own chip, the address carries the filter. Never a hard filter.
    ui.goto("/#/jobs")
    _wait_for_first(ui, "Posting job-01")
    ui.page.click('[data-action="toggle-ranked-low"]')
    _wait_for_first(ui, "Posting low-01")
    assert answers.listed[-1] == "state=ranked_low&limit=50" and "state=ranked_low" in ui.page.url
    low = _rows(ui)
    assert [row["rank"] for row in low] == LOW and all(row["chips"] == ["Not assessed", "Ranked low"] for row in low), low
    assert _text(ui, low_line) == "Listing the 3 weak fits, ranked low: back to the list"
    ui.page.click('[data-action="toggle-ranked-low"]')
    _wait_for_first(ui, "Posting job-01")
    assert "state=" not in ui.page.url and _text(ui, low_line) == "3 weak fits, ranked low: show"

    # "Assess all" asks first: every not-assessed posting of the list, nothing approved, no posting named.
    ui.page.click(tid("assess-all"))
    ui.page.wait_for_selector(tid("approval-dialog"))
    assert answers.bodies == [{"states": ["not_assessed"]}]
    assert _text(ui, "#assess-approval-title") == "Assess the top 50 by rank of 60 postings?"
    assert _text(ui, '[data-role="approval-batch"]') == (
        '50 at a time: the top 50 by rank now, never more in one go. 10 more after these 50: "Assess these" again takes the next 10.'
    )
    assert _text(ui, '[data-role="approval-estimate"]').startswith("Estimate: ~50 model calls, ~975k tokens")
    assert "Model:" in _text(ui, tid("approval-dialog")) and "newest" not in _text(ui, tid("approval-dialog"))
    assert _text(ui, '[data-role="approval-ranking"]') == f"Ranking: {RANKING_WORDS}"  # a click during ranking is not silent

    # Approve: the server's own yes. The 50 are assessed, and the line says what is left.
    ui.page.click('[data-action="approval-approve"]')
    notice.wait_for()
    assert answers.bodies[-1] == {"approve": True, "states": ["not_assessed"]}
    assert _text(ui, '[data-role="assess-notice"]') == 'Assessed 50 of 50. 10 more not assessed yet: 50 at a time, "Assess these" again takes the next.'
    ui.page.wait_for_function(
        """() => ((document.querySelector('[data-role="not-assessed-count"]') || {}).textContent || '') === '10 not assessed'"""
    )
    assert {row["state"] for row in _rows(ui)} == {"matched"}  # the 50 assessed lead the list: best fit first

    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
