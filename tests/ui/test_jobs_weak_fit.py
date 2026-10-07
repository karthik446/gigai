"""0110-10-02: the Jobs page keeps weak fits out of sight until their chip is on, and draws the rows by fit.

The demo home has no weak fit (that needs an assessment and a low rank score), so the browser talks to the REAL
Scout server for everything except `GET /api/postings`, which this test answers itself (the first real response as
the template: its profiles and row shape) the way the real route does: without `state=weak_fit` the three weak
fits are left out and only counted (`counts.weak_fit`), with it they are the list. The real route's own rule is
proven on the real server in `tests/behaviors/scout_pipeline/test_weak_fit_sort_threshold.py`; here the page, the
client list store and the hash router are the real ones. The second test answers `POST /api/postings/assess` the
same way (no model on the demo home is called): the ask with its low-ranked postings, then the approval.

Pinned: the "Weak fit" chip is there, OFF by default, with the count; no weak-fit row is listed and the default
request asks for no state; "Need your answers" in the header does not count them; the rows are drawn in the
server's order (inside a verdict group the fit number falls, then the rank) and each says its one fit number; the
chip lists the weak fits (their own chip, no question count), the address carries `state=weak_fit`, and a bookmark
of it opens the same list; "Assess these" shows the low-ranked postings as a second question (a box, off by
default) and Approve sends `include_low_rank` only when it is ticked; zero console errors (the `ui` fixture
asserts it).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.ui.support import tid

pytestmark = pytest.mark.ui

# (title, state, fit, rank, open questions): the listed rows, in the order the server sends them (0110-10-02:
# the verdict group, then the fit number, then the rank; a not-assessed row has no fit number).
LISTED = [
    ("Matched, every must-have", "matched", 100, 61, 0),
    ("Matched, most must-haves, high rank", "matched", 80, 95, 0),
    ("Matched, most must-haves, lower rank", "matched", 80, 60, 0),
    ("Waits on answers, good fit", "needs_answers", 75, 70, 2),
    ("Waits on answers, half met", "needs_answers", 50, 88, 1),
    ("Not assessed yet", "not_assessed", None, 99, 0),
]
WEAK = [
    ("Motion planning, one of ten", "weak_fit", 14, 39, 3),
    ("Robotics controls, two of ten", "weak_fit", 20, 45, 2),
    ("Embedded firmware, one of eight", "weak_fit", 12, 20, 1),
]
_WORDS = {"matched": "Matched", "needs_answers": "Needs your answers", "weak_fit": "Weak fit"}


def _row(template: dict, index: int, spec: tuple[str, str, int | None, int, int]) -> dict:
    title, state, fit, rank, questions = spec
    row = dict(template)
    asked = [{"question_id": f"topic:{index}-{n}", "question": f"Have you worked with topic {n}?"} for n in range(questions)]
    row.update({
        "job_identity": f"https://jobs.lever.co/synthetic/fit-{index:03d}", "normalized_url": f"https://jobs.lever.co/synthetic/fit-{index:03d}",
        "title": title, "state": state, "fit": fit, "rank_score": rank, "label": None, "ats_score": None, "tailored": False,
        "stale_reason": None, "stale_label": None, "sort_group": "not_assessed" if state == "not_assessed" else "current",
        # A weak fit asks no question: the server sends none for it.
        "open_questions": [] if state == "weak_fit" else asked,
        "assessment": None if fit is None else {"verdict": None, "met": round(fit * 20 / 100), "requirements": 20, "percent": fit, "assessed_at": "2026-10-06T10:00:00Z"},
        "score_text": f"rank {rank} · not assessed" if fit is None else f"{_WORDS[state]} · fit {fit}% · rank {rank}",
    })
    return row


class PostingsAnswers:
    """Answers GET /api/postings: the listed rows, or the weak fits when their state is asked for."""

    def __init__(self) -> None:
        self.template: dict | None = None
        self.asked: list[str] = []

    def __call__(self, route) -> None:
        url = urlsplit(route.request.url)
        if url.path != "/api/postings":
            route.continue_()
            return
        if self.template is None:
            self.template = route.fetch().json()  # the real server's own answer, once
        self.asked.append(url.query)
        states = parse_qs(url.query).get("state", [])
        specs = (WEAK if states == ["weak_fit"] else LISTED + WEAK if "weak_fit" in states else LISTED)
        first = self.template["postings"]["rows"][0]
        rows = [_row(first, index, spec) for index, spec in enumerate(specs, start=1)]
        by_state: dict[str, int] = {}
        for row in rows:
            by_state[row["state"]] = by_state.get(row["state"], 0) + 1
        body = json.loads(json.dumps(self.template))
        body["counts"].update({"matched": len(rows), "shown": len(rows), "by_state": by_state, "weak_fit": len(WEAK)})
        body["postings"]["rows"] = rows
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def _wait_for_first(ui, title: str) -> None:
    ui.page.wait_for_function(
        """(title) => {
          const first = document.querySelector('[data-testid="job-row"] [data-action="open-job"]');
          return first && first.textContent === title;
        }""",
        arg=title,
    )


def _rows(ui) -> list[dict[str, str | None]]:
    return ui.page.locator(tid("job-row")).evaluate_all(
        """(rows) => rows.map((row) => ({
          title: row.querySelector('[data-action="open-job"]').textContent,
          state: row.dataset.state,
          fit: row.dataset.fit === undefined ? null : row.dataset.fit,
          rank: row.dataset.rank === undefined ? null : row.dataset.rank,
          score: row.querySelector('[data-role="score"]').textContent,
          numbers: Array.from(row.querySelectorAll('[data-role="profile-tags"] [data-testid="fit-chip"], [data-role="profile-tags"] [data-testid="rank-chip"]')).map((chip) => chip.textContent),
          chips: Array.from(row.querySelectorAll('[data-role="state-chips"] .state-pill')).map((chip) => chip.textContent),
        }))"""
    )


def test_weak_fits_are_hidden_until_their_chip_is_on_and_rows_come_by_fit(ui) -> None:
    answers = PostingsAnswers()
    ui.page.route("**/api/postings*", answers)
    chip = ui.page.locator('[data-role="state-filter"] [data-state="weak_fit"]')

    ui.goto("/#/jobs")
    _wait_for_first(ui, LISTED[0][0])
    ui.step("default")

    # The chip is there, off, and says how many it would list. Nothing asked the server for a state.
    assert chip.count() == 1 and chip.get_attribute("aria-pressed") == "false"
    assert (chip.text_content() or "").split() == ["Weak", "fit", str(len(WEAK))]
    assert all("state=" not in query for query in answers.asked), answers.asked
    assert "state=" not in ui.page.url

    # No weak fit in the list; the header's "Need your answers" counts the two that still wait, not the three weak fits.
    rows = _rows(ui)
    assert [row["title"] for row in rows] == [spec[0] for spec in LISTED]
    assert "weak_fit" not in {row["state"] for row in rows} and ui.page.locator(tid("weak-fit-chip")).count() == 0
    assert ui.page.locator('.stat-tile:has-text("Need your answers") .stat-value').first.text_content() == "2"
    assert ui.page.locator('.stat-tile:has-text("Postings") .stat-value').first.text_content() == str(len(LISTED))

    # The order is the server's, drawn as sent: inside a verdict group the fit number falls, then the rank.
    assert [(row["state"], row["fit"], row["rank"]) for row in rows] == [
        ("matched", "100", "61"), ("matched", "80", "95"), ("matched", "80", "60"),
        ("needs_answers", "75", "70"), ("needs_answers", "50", "88"), ("not_assessed", None, "99"),
    ]
    for state in ("matched", "needs_answers"):
        keys = [(int(row["fit"] or -1), int(row["rank"] or -1)) for row in rows if row["state"] == state]
        assert keys == sorted(keys, reverse=True), (state, keys)
    # One fit number per assessed row, as a chip under the title (0.1.11.5 UI-01), the rank beside it; the middle column
    # keeps the state. The 100% fit leads although its rank (61) is the lowest but one.
    assert [row["numbers"] for row in rows[:2]] == [["Fit 100% · 20/20", "Rank 61"], ["Fit 80% · 16/20", "Rank 95"]]
    assert [row["score"] for row in rows[:2]] == ["Matched", "Matched"]
    assert rows[-1]["numbers"] == ["Rank 99"] and rows[-1]["score"] == "not assessed"  # a not assessed row: the rank chip alone
    assert rows[3]["chips"][0] == "Needs your answers (2)"  # type: ignore[index]

    # The chip lists the weak fits: their own chip, no question count, and the address carries the state.
    chip.click()
    _wait_for_first(ui, WEAK[0][0])
    ui.step("weak-on")
    assert ui.requests_after("default", "/api/postings") == 1 and answers.asked[-1] == "state=weak_fit&limit=50"
    assert chip.get_attribute("aria-pressed") == "true" and "state=weak_fit" in ui.page.url
    weak = _rows(ui)
    assert [row["title"] for row in weak] == [spec[0] for spec in WEAK] and {row["state"] for row in weak} == {"weak_fit"}
    assert all(row["chips"][0] == "Weak fit" for row in weak), weak  # type: ignore[index]
    assert not any("Needs your answers" in label for row in weak for label in row["chips"])  # type: ignore[union-attr]
    assert ui.page.locator(tid("weak-fit-chip")).count() == len(WEAK)
    assert weak[0]["score"] == "Weak fit" and weak[0]["numbers"] == ["Fit 14% · 3/20", "Rank 39"]
    # The header counts stay the unfiltered totals: the weak fits never join "Need your answers".
    assert ui.page.locator('.stat-tile:has-text("Need your answers") .stat-value').first.text_content() == "2"

    # A bookmark of the chip opens the same list.
    ui.reload()
    _wait_for_first(ui, WEAK[0][0])
    assert chip.get_attribute("aria-pressed") == "true" and ui.job_rows() == len(WEAK)

    # Off again: the weak fits are gone from the list, and the state leaves the address.
    chip.click()
    _wait_for_first(ui, LISTED[0][0])
    assert chip.get_attribute("aria-pressed") == "false" and "state=" not in ui.page.url
    assert "weak_fit" not in {row["state"] for row in _rows(ui)}
    ui.no_more_than_one_in_flight("/api/postings")

    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


class AssessAnswers:
    """Answers POST /api/postings/assess: the ask (3 to assess, 2 low-ranked skipped), then what an approval did."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, route) -> None:
        body = json.loads(route.request.post_data or "{}")
        self.bodies.append(body)
        estimate = {"calls": 3, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
        answer: dict = {
            "schema_version": "scout-postings-assess:1", "checked_at": "2026-10-03T15:00:00.000000Z", "not_found": [],
            "counts": {"selected": 5, "to_assess": 3, "already_current": 0, "not_found": 0, "low_rank_skipped": 2},
            "postings": {"_labels": {}, "rule": "", "rows": []}, "profiles": [], "approval": None, "assessed": None, "question": None,
            "low_rank": {
                "kind": "assess_low_rank", "skipped": 2, "min_rank": 50, "estimate": {**estimate, "calls": 2},
                "text": "2 low-ranked ones are skipped (rank below 50); assess those too? ~2 calls",
                "yes": {"api": {"method": "POST", "path": "/api/postings/assess", "body": {"approve": True, "include_low_rank": True}}},
            },
        }
        if body.get("approve"):
            done = 5 if body.get("include_low_rank") else 3
            answer.update({
                "status": "assessed", "assessed": {"requested": done, "assessed": done, "failed": [], "stopped": None, "fetched_on_demand": 0},
                "approval": {"id": "apv_" + "0" * 32, "decided_by": "operator", "jobs": done},
            })
            if body.get("include_low_rank"):
                answer["low_rank"] = None
                answer["counts"]["low_rank_skipped"] = 0
        else:
            answer.update({
                "status": "ask",
                "question": {
                    "kind": "assess_these", "selected": 5, "to_assess": 3, "already_current": 0, "low_rank_skipped": 2, "by_profile": [],
                    "model_target": "codex_cli", "estimate": estimate, "text": "Assess 3 postings? ~3 calls",
                    "yes": {"api": {"method": "POST", "path": "/api/postings/assess", "body": {"approve": True}}},
                },
            })
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))


def test_assess_these_asks_about_the_low_ranked_separately(ui) -> None:
    ui.page.route("**/api/postings*", PostingsAnswers())
    asked = AssessAnswers()
    ui.page.route("**/api/postings/assess", asked)
    box = ui.page.locator(f"{tid('approval-low-rank')} input[type='checkbox']")
    notice = ui.page.locator('[data-role="assess-notice"]')

    ui.goto("/#/jobs")
    _wait_for_first(ui, LISTED[0][0])

    # The ask: the count above the threshold, and the low-ranked ones as a second question, off by default.
    ui.page.click(tid("assess-these"))
    ui.page.wait_for_selector(tid("approval-dialog"))
    assert ui.page.locator("#assess-approval-title").text_content() == "Assess 3 postings?"
    line = (ui.page.locator(tid("approval-low-rank")).text_content() or "").strip()
    assert line == "2 low-ranked ones are skipped (rank below 50). Assess those too? ~2 model calls"
    assert not box.is_checked()
    assert asked.bodies == [{}]  # the ask itself approves nothing

    # Approve with the box off: the server's own yes, nothing about the low-ranked.
    ui.page.click('[data-action="approval-approve"]')
    notice.wait_for()
    assert asked.bodies[-1] == {"approve": True}
    assert notice.text_content() == "Assessed 3 of 3. 2 low-ranked ones were skipped (rank below 50)."

    # Again, the box ticked: the body the server named for the low-ranked too.
    ui.page.click(tid("assess-these"))
    ui.page.wait_for_selector(tid("approval-dialog"))
    assert not box.is_checked()  # off again for every new question
    box.check()
    ui.page.click('[data-action="approval-approve"]')
    ui.page.wait_for_function("""() => (document.querySelector('[data-role="assess-notice"]') || {}).textContent === 'Assessed 5 of 5.'""")
    assert asked.bodies[-1] == {"approve": True, "include_low_rank": True}

    ui.assert_clean()
