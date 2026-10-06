"""0.1.11.2 RANK-B: the Jobs page's rank labels, the model run under node.

``ui/src/postingsModel.js`` is plain JavaScript: this runs it under the system
``node`` over what the SERVER makes (``search_postings``, ``assess_these``) on
a synthetic home with five not-assessed postings around the weak-fit rank
(80, 50, 49, 20 and one not ranked yet). LOUD skip without ``node``. What
lives in JSX is pinned by reading the source.

Pinned: the collapse line ("2 weak fits, ranked low" + "show") and its filter
(the address carries it); a ranked-low row's chip; a posting not ranked yet
says "not ranked yet" and is never ranked low; "N not assessed" and the body
"Assess all" asks with; the dialog says "top 50 by rank", never "newest"; the
"ranking is still running" line.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import posting_search
from gigai.scout.find_jobs.api import static as static_module

from tests.support.fit_fixtures import ranked_new
from tests.support.posting_fixtures import NOW, build_postings_fixture

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
import * as m from MODEL_URL;

const data = DATA;
const out = {};
const rows = data.listed.postings.rows;
const lowRows = data.low.postings.rows;
out.order = rows.map((row) => m.scoreText(row));
out.lowOrder = lowRows.map((row) => m.scoreText(row));
out.line = [m.rankedLowLine(data.listed.counts, []), m.rankedLowLine(data.low.counts, ["ranked_low"]), m.rankedLowLine({ ranked_low: 1 }, []), m.rankedLowLine({ ranked_low: 0 }, []), m.rankedLowLine(null, [])];
out.toggle = [m.toggleRankedLow([]), m.toggleRankedLow(["needs_answers"]), m.toggleRankedLow(["ranked_low"])];
out.query = m.postingsQuery({ ...m.EMPTY_FILTER, states: m.toggleRankedLow([]) });
out.hash = m.jobsHash({ ...m.EMPTY_FILTER, states: ["ranked_low"] });
out.parsed = m.parseJobsHash("#/jobs?state=ranked_low").filter.states;
out.hasFilter = m.hasFilter({ ...m.EMPTY_FILTER, states: ["ranked_low"] });
out.chips = {
  low: lowRows.map((row) => m.rowChips(row).map((chip) => chip.label)),
  listed: rows.map((row) => m.rowChips(row).map((chip) => chip.label)),
  lowChip: m.rowChips(lowRows[0]).find((chip) => chip.kind === "rank"),
};
out.notAssessed = [m.notAssessedLine(data.listed.counts), m.notAssessedLine({ by_state: { matched: 2 } }), m.notAssessedLine(null), m.notAssessedLine({ by_state: { not_assessed: 1 } })];
out.allBody = [
  m.assessAllBody({ filter: m.EMPTY_FILTER, rows }),
  m.assessAllBody({ filter: { ...m.EMPTY_FILTER, profileIds: ["p1"], query: " python ", window: "7d", states: ["needs_answers"] }, rows }),
  m.assessAllBody({ filter: { ...m.EMPTY_FILTER, states: ["ranked_low"] }, rows: lowRows }),
  m.assessAllBody({ filter: { ...m.EMPTY_FILTER, profileIds: ["p1", "p2"] }, rows: [{ job_identity: "a", state: "not_assessed" }, { job_identity: "b", state: "matched" }] }),
];
const running = { enabled: true, in_progress: true, by_profile: [{ profile_id: "a", ranked: 100, total: 150 }, { profile_id: "b", ranked: 20, total: 23 }] };
out.ranking = [m.rankingLine(running), m.rankingLine({ ...running, in_progress: false }), m.rankingLine(null), m.rankingLine(data.listed.ranking)];

// The dialog of "Assess all": the server's ask; a capped one says the top 50 by rank.
out.dialog = m.approvalDialog(data.ask, data.ask.profiles);
out.dialogTitle = m.approvalTitle(out.dialog);
const capped = m.approvalDialog({ status: "ask", ranking: running, question: { to_assess: 60, batch: 50, more_after: 10, estimate: { calls: 50 }, yes: { api: { body: { states: ["not_assessed"] } } } },
  low_rank: { skipped: 112, batch: 50, more_after: 62, min_rank: 50, estimate: { calls: 50 }, yes: { api: { body: {} } } } }, []);
out.capped = { title: m.approvalTitle(capped), batch: m.approvalBatchLine(capped), low: m.lowRankLine(capped.lowRank), ranking: capped.ranking, body: m.approvalBody(capped, false) };
console.log(JSON.stringify(out));
"""


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the rank-order model was NOT run")
    assert node is not None
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    ranked = ranked_new(fx, monkeypatch)
    calls = fx.base.model.calls
    one = [fx.default_profile_id]

    data = {
        "ranked": ranked,
        "listed": posting_search.search_postings(fx.home_root, fx.target, now=NOW, profile_ids=one),
        "low": posting_search.search_postings(fx.home_root, fx.target, now=NOW, profile_ids=one, states=["ranked_low"]),
    }
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri()))
    # The ask "Assess all" sends is the model's own body: it is made first, then the server answers it.
    body = {"states": ["not_assessed"], "profile_id": fx.default_profile_id}
    data["ask"] = posting_search.assess_these(fx.home_root, fx.target, now=NOW, **body)  # type: ignore[arg-type]
    assert fx.base.model.calls == calls, "reading and asking called a model"
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script.replace("DATA", json.dumps(data))], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    result = json.loads(completed.stdout)
    result["data"] = data
    return result


def test_the_list_is_in_rank_order_and_a_posting_not_ranked_yet_says_so_at_the_bottom(out: dict) -> None:
    # The page draws the rows in the server's order: by rank, the one not ranked yet last. Rank 49 and 20 are collapsed.
    assert out["order"] == ["rank 80 · not assessed", "rank 50 · not assessed", "not ranked yet · not assessed"]
    assert out["lowOrder"] == ["rank 49 · not assessed", "rank 20 · not assessed"]
    assert out["chips"]["listed"] == [["Not assessed"], ["Not assessed"], ["Not assessed"]]  # never "Ranked low": not for an unranked one
    assert out["chips"]["low"] == [["Not assessed", "Ranked low"], ["Not assessed", "Ranked low"]]
    assert out["chips"]["lowChip"]["testId"] == "ranked-low-chip" and "left out of the main list" in out["chips"]["lowChip"]["title"]


def test_the_collapse_line_counts_the_ranked_low_postings_and_opens_them(out: dict) -> None:
    closed, opened, single, none, unread = out["line"]
    assert closed == {"text": "2 weak fits, ranked low", "action": "show", "active": False}
    assert opened == {"text": "Listing the 2 weak fits, ranked low", "action": "back to the list", "active": True}
    assert single["text"] == "1 weak fit, ranked low" and none is None and unread is None
    # A click lists only them; a second click is the list as it was. The address carries it (a bookmark, Back).
    assert out["toggle"] == [["ranked_low"], ["ranked_low"], []]
    assert out["query"] == "state=ranked_low&limit=50"
    assert out["hash"] == "#/jobs?state=ranked_low" and out["parsed"] == ["ranked_low"] and out["hasFilter"] is True


def test_n_not_assessed_and_what_assess_all_asks(out: dict) -> None:
    assert out["notAssessed"] == ["3 not assessed", None, None, "1 not assessed"]
    plain, filtered, low, several = out["allBody"]
    assert plain == {"states": ["not_assessed"]}  # every not-assessed posting, whatever is ticked: never `jobs`, never `approve`
    assert filtered == {"states": ["not_assessed"], "profile_id": "p1", "query": "python", "window": "7d"}
    assert low == {"states": ["ranked_low"]}  # on the ranked-low list it asks about those
    assert several == {"jobs": ["a"]}  # the route takes one profile: the page's not-assessed rows are named
    # The server's answer to that ask: the three listed are the batch, the two ranked low are the second question.
    dialog = out["dialog"]
    assert (dialog["count"], dialog["total"], dialog["moreAfter"], dialog["lowRank"]["count"]) == (3, 3, 0, 2)
    assert out["dialogTitle"] == "Assess 3 postings?" and dialog["approveBody"]["approve"] is True
    assert dialog["approveBody"]["states"] == ["not_assessed"] and "jobs" not in dialog["approveBody"]


def test_the_dialog_says_top_50_by_rank_and_that_ranking_still_runs(out: dict) -> None:
    capped = out["capped"]
    assert capped["title"] == "Assess the top 50 by rank of 60 postings?"
    assert capped["batch"] == 'the top 50 by rank now, never more in one go. 10 more after these 50: "Assess these" again takes the next 10.'
    assert capped["low"] == "112 low-ranked ones are skipped (rank below 50). Assess the top 50 by rank of those too? ~50 model calls (62 more after these 50)"
    assert "newest" not in json.dumps(capped)
    running = "Ranking is still running: 120 of 173 ranked. The order, and the top 50 by rank, are of what is ranked so far."
    assert capped["ranking"] == running
    # The server's own block: the fixture's rank lane is on and has ranked 4 of the 10 rows (5 postings, 2 profiles).
    served = "Ranking is still running: 4 of 10 ranked. The order, and the top 50 by rank, are of what is ranked so far."
    assert out["ranking"] == [running, None, None, served]
    assert out["dialog"]["ranking"] == served  # the ASK carries it too: the dialog says it before the approval


def test_the_page_draws_the_lines_and_the_dialog_the_ranking() -> None:
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    for needle in (
        'data-testid="ranked-low-line"', 'data-action="toggle-ranked-low"', 'data-testid="not-assessed-line"', 'data-testid="assess-all"',
        "ask(assessAllBody({ filter, rows }))", 'data-testid="ranking-line"', 'data-testid="assess-these"',
    ):
        assert needle in view, needle
    dialog = (UI_SRC / "components" / "AssessApprovalDialog.jsx").read_text(encoding="utf-8")
    assert 'data-role="approval-ranking"' in dialog and "dialog.ranking" in dialog
