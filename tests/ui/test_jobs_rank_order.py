"""0.1.11.2 RANK-B + RANKORDER: the Jobs page by rank: ranked-low postings LISTED under a plain divider, "N not assessed" + "Assess all".

The demo home has no rank score (that needs the rank lane's model), so, as in `test_jobs_weak_fit.py`, the browser
talks to the REAL Scout server for everything except `GET /api/postings` and `POST /api/postings/assess`, which this
test answers itself (the first real response as the template: its profiles and row shape) the way the real routes
do: 63 not-assessed postings in rank order: 58 ranked 50 or more, then three ranked below 50 (`ranked_low: true`,
`counts.ranked_low` 3), then two not ranked yet, and `ranking` while the background rank still runs. The real
routes' own rules (the order, the 50 highest of 60 and what an approval assesses) are proven on the real server in
`tests/behaviors/scout_pipeline/test_rank_order_and_top_batch.py`; here the page, the client list store and the hash
router are the real ones.

Pinned: the rows are drawn in the server's rank order; NOTHING is collapsed (no "weak fits, ranked low: show" line,
the default list asks for no state): the three ranked-low postings are rows of the list, on the page their rank
puts them on, under a plain "Ranked low (3)" divider, with their chip; each can be ticked ("Assess these" counts
it) and opened (its job deep link); the postings not ranked yet come after them under "Not ranked yet"; in the
"Newest posted" order there is no divider; "63 not assessed" with "Assess all", which asks first (never `approve`,
never `jobs`) and opens the existing dialog: "Assess the top 50 by rank of 60 postings?", the estimate, the model,
"10 more after these 50", and that ranking still runs; Approve sends the server's own yes; zero console errors.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.ui.support import tid

pytestmark = pytest.mark.ui

#: Not assessed and ranked below 50: listed after the other ranked ones, never left out.
LOW = [49, 31, 12]
#: 63 not-assessed postings in the server's order: 58 ranked (99 down to 50; the score repeats near the end), the three
#: ranked low, then two not ranked yet.
RANKS: list[int | None] = [max(50, 99 - n) for n in range(58)] + LOW + [None, None]
#: What "Assess all" takes without its low-rank yes (the assess threshold): the 58 and the two not ranked yet.
ASSESSABLE = 60
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
        # The top `assessed` by rank have a verdict now and lead the list; the rest keep the rank order.
        rows = [
            _row(first, f"job-{index:02d}", rank, assessed=index <= self.assessed, low=rank is not None and rank < 50 and index > self.assessed)
            for index, rank in enumerate(RANKS, start=1)
        ]
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
        left = ASSESSABLE - self.assessed
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


def _list(ui) -> list[str]:
    """The list as drawn, top to bottom: a row's rank ("-" when it has none) or a divider's words."""

    return ui.page.locator(f"{tid('jobs-list')} > li").evaluate_all(
        """(items) => items.map((item) => item.classList.contains('posting-divider')
          ? item.textContent.trim()
          : (item.dataset.rank === undefined ? '-' : item.dataset.rank))"""
    )


def test_jobs_come_by_rank_ranked_low_postings_are_listed_under_a_divider_and_assess_all_takes_the_top_50_by_rank(ui) -> None:
    answers = Answers()
    ui.page.route("**/api/postings*", answers)
    ui.page.route("**/api/postings/assess", answers)
    waiting = tid("not-assessed-line")
    low_divider, unranked_divider = tid("ranked-low-divider"), tid("not-ranked-divider")
    notice = ui.page.locator('[data-role="assess-notice"]')

    ui.goto("/#/jobs")
    _wait_for_first(ui, "Posting job-01")
    ui.step("default")

    # The rows are the server's, in its order: best rank first. The first page is the 50 best ranked.
    rows = _rows(ui)
    assert [row["rank"] for row in rows] == RANKS[:50] and rows[0]["score"] == "rank 99 · not assessed"
    assert [row["rank"] for row in rows] == sorted((row["rank"] for row in rows), reverse=True)
    assert all("state=" not in query for query in answers.listed), answers.listed  # the default list asks for no state

    # Nothing is collapsed: no "N weak fits, ranked low: show" line and no toggle. All 63 wait for an assessment.
    assert ui.page.locator(tid("ranked-low-line")).count() == 0 and ui.page.locator('[data-action="toggle-ranked-low"]').count() == 0
    assert "weak fits, ranked low" not in (ui.page.locator("body").text_content() or "")
    assert _text(ui, f"{waiting} [data-role='not-assessed-count']") == "63 not assessed"
    assert _text(ui, tid("assess-all")) == "Assess all" and ui.page.locator(tid("assess-these")).count() == 1  # select-and-assess stays
    assert _text(ui, tid("ranking-line")) == RANKING_WORDS

    # The ranked-low postings ARE in the list, where their rank puts them: after the other ranked ones, under a plain
    # "Ranked low (3)" divider; the ones not ranked yet come after them and say so.
    ui.goto("/#/jobs?page=2")
    _wait_for_first(ui, "Posting job-51")
    last = _rows(ui)
    assert [row["rank"] for row in last] == RANKS[50:]
    assert _list(ui) == ["50"] * 8 + ["Ranked low (3)", "49", "31", "12", "Not ranked yet", "-", "-"]
    assert ui.page.locator(low_divider).count() == 1 and ui.page.locator(unranked_divider).count() == 1
    assert [row["chips"] for row in last] == [["Not assessed"]] * 8 + [["Not assessed", "Ranked low"]] * 3 + [["Not assessed"]] * 2, last
    assert [row["score"] for row in last[8:]] == [
        "rank 49 · not assessed", "rank 31 · not assessed", "rank 12 · not assessed", "not ranked yet · not assessed", "not ranked yet · not assessed",
    ]
    ui.step("ranked-low-listed")

    # A ranked-low row is a row like any other: it can be ticked (the select-and-assess count moves) ...
    low_row = ui.page.locator(tid("job-row")).nth(9)  # rank 31
    assert low_row.get_attribute("data-rank") == "31"
    box = low_row.locator('input[type="checkbox"]')
    assert box.is_enabled() and not box.is_checked()
    before = _text(ui, tid("assess-these"))
    box.check()
    assert box.is_checked() and _text(ui, tid("assess-these")) != before and "1" in _text(ui, tid("assess-these"))
    box.uncheck()
    # ... and opened: its link is the job's deep link, and the job page answers for it.
    link = low_row.locator('[data-action="open-job"]')
    assert (link.get_attribute("href") or "").startswith("#/jobs/") and "job-60" in (link.get_attribute("href") or "")

    # In the other order the ranked-low rows are spread through the list: no divider, the same rows.
    ui.goto("/#/jobs?sort=newest_posted&page=2")
    _wait_for_first(ui, "Posting job-51")
    assert ui.page.locator(low_divider).count() == 0 and ui.page.locator(unranked_divider).count() == 0
    assert [row["rank"] for row in _rows(ui)] == RANKS[50:] and ui.page.locator(tid("ranked-low-chip")).count() == 3

    ui.goto("/#/jobs")
    _wait_for_first(ui, "Posting job-01")
    assert ui.page.locator(low_divider).count() == 0  # the first page holds none of them: no divider without a row under it

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
        """() => ((document.querySelector('[data-role="not-assessed-count"]') || {}).textContent || '') === '13 not assessed'"""
    )
    assert {row["state"] for row in _rows(ui)} == {"matched"}  # the 50 assessed lead the list: best fit first

    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
