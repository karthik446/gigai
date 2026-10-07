"""0.1.11.2 RANK-B: the Jobs page's rank labels, the model run under node.

``ui/src/postingsModel.js`` is plain JavaScript: this runs it under the system
``node`` over what the SERVER makes (``search_postings``, ``assess_these``) on
a synthetic home with five not-assessed postings around the weak-fit rank
(80, 50, 49, 20 and one not ranked yet). LOUD skip without ``node``. What
lives in JSX is pinned by reading the source.

Pinned (RANKORDER, the operator's correction): the ranked-low postings are in
the list, in rank order, never collapsed; a plain "Ranked low (2)" divider
stands above them and "Not ranked yet" below (``listItems``), only in the
rank order; a ranked-low row's chip; a posting not ranked yet says "not
ranked yet" and is never ranked low; the "Your master changed since these
postings were ranked" line (``rankNowModel.staleResumeLine``); "N not assessed" and the body
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
import * as r from RANK_URL;

const data = DATA;
const out = {};
const rows = data.listed.postings.rows;
const lowRows = data.low.postings.rows;
out.order = rows.map((row) => m.scoreText(row));
out.lowOrder = lowRows.map((row) => m.scoreText(row));
const shape = (items) => items.map((item) => (item.kind === "divider" ? `${item.testId}: ${item.text}` : m.scoreText(item.row)));
out.items = shape(m.listItems(rows, data.listed.counts, null));
out.itemsFit = shape(m.listItems(rows, data.listed.counts, "fit"));
out.itemsNewest = shape(m.listItems(rows, data.listed.counts, m.NEWEST_POSTED));
out.itemsPageTwo = shape(m.listItems(rows.slice(3), data.listed.counts, null));
out.itemsNone = [m.listItems([], data.listed.counts, null), m.listItems(null, null, null), shape(m.listItems(rows.slice(0, 2), null, null))];
out.itemRows = m.listItems(rows, data.listed.counts, null).filter((item) => item.kind === "row").map((item) => item.row.job_identity);
out.rowIds = rows.map((row) => row.job_identity);
out.exports = ["rankedLowLine", "toggleRankedLow", "RANKED_LOW"].filter((name) => name in m);
out.parsed = m.parseJobsHash("#/jobs?state=ranked_low").filter.states;
const ranked = { enabled: true, in_progress: false, window_days: 7, by_profile: [{ profile_id: "a", ranked: 5, total: 5, stale_resume: true }] };
out.stale = [
  r.staleResumeLine({ ...ranked, stale_resume: true }),
  r.staleResumeLine({ ...ranked, stale_resume: false }),
  r.staleResumeLine(ranked),
  r.staleResumeLine({ ...ranked, stale_resume: true, enabled: false }),
  r.staleResumeLine({ ...ranked, stale_resume: true }, { state: "running", mode: "latest" }),
  r.staleResumeLine(null),
  r.staleResumeLine(data.listed.ranking),
];
out.chips = {
  low: lowRows.map((row) => m.rowChips(row).map((chip) => chip.label)),
  listed: rows.map((row) => m.rowChips(row).map((chip) => chip.label)),
  lowChip: m.rowChips(lowRows[0]).find((chip) => chip.kind === "rank"),
  lowScore: lowRows.map((row) => m.scoreText(row)),
};
out.notAssessed = [m.notAssessedLine(data.listed.counts), m.notAssessedLine({ by_state: { matched: 2 } }), m.notAssessedLine(null), m.notAssessedLine({ by_state: { not_assessed: 1 } })];
out.allBody = [
  m.assessAllBody({ filter: m.EMPTY_FILTER, rows }),
  m.assessAllBody({ filter: { ...m.EMPTY_FILTER, profileIds: ["p1"], query: " python ", window: "7d", states: ["needs_answers"] }, rows }),
  m.assessAllBody({ filter: { ...m.EMPTY_FILTER, profileIds: ["p1", "p2"] }, rows: [{ job_identity: "a", state: "not_assessed" }, { job_identity: "b", state: "matched" }] }),
];
const running = { enabled: true, in_progress: true, by_profile: [{ profile_id: "a", ranked: 100, total: 150 }, { profile_id: "b", ranked: 20, total: 23 }] };
out.ranking = [m.rankingLine(running), m.rankingLine({ ...running, in_progress: false }), m.rankingLine(null), m.rankingLine(data.listed.ranking)];

// The dialog of "Assess all": the server's ask; a capped one says the top 50 by rank.
out.dialog = m.approvalDialog(data.ask, data.ask.profiles);
out.dialogTitle = m.approvalTitle(out.dialog);
const capped = m.approvalDialog({ status: "ask", ranking: running, question: { to_assess: 60, batch: 50, more_after: 10, estimate: { calls: 50 }, yes: { api: { body: { states: ["not_assessed"] } } } },
  low_rank: { skipped: 112, batch: 50, more_after: 62, min_rank: 50, estimate: { calls: 50 }, yes: { api: { body: {} } } } }, []);
out.capped = { title: m.approvalTitle(capped), batch: m.approvalBatchLine(capped), low: m.lowRankLine(capped.lowRank, capped.count), ranking: capped.ranking, body: m.approvalBody(capped, false) };
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
    script = script.replace("RANK_URL", json.dumps((UI_SRC / "rankNowModel.js").resolve().as_uri()))
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


def test_every_posting_is_listed_in_rank_order_with_the_ranked_low_ones_under_a_plain_divider(out: dict) -> None:
    # The page draws EVERY row, in the server's order: by rank, so 49 and 20 come after 80 and 50, the unranked one last.
    assert out["order"] == [
        "rank 80 · not assessed", "rank 50 · not assessed", "rank 49 · not assessed", "rank 20 · not assessed", "not ranked yet · not assessed",
    ]
    assert out["data"]["listed"]["counts"]["ranked_low"] == 2 and out["data"]["listed"]["counts"]["matched"] == 5
    # A plain divider above the two, and one where the unranked start: nothing is collapsed, no row is dropped or moved.
    assert out["items"] == out["itemsFit"] == [
        "rank 80 · not assessed", "rank 50 · not assessed", "ranked-low-divider: Ranked low (2)", "rank 49 · not assessed",
        "rank 20 · not assessed", "not-ranked-divider: Not ranked yet", "not ranked yet · not assessed",
    ]
    assert out["itemRows"] == out["rowIds"]
    # A page that starts inside them says so at its top (the count is the list's, not the page's).
    assert out["itemsPageTwo"] == ["ranked-low-divider: Ranked low (2)", "rank 20 · not assessed", "not-ranked-divider: Not ranked yet", "not ranked yet · not assessed"]
    # In another order they are spread through the list: no divider, the rows as they are.
    assert out["itemsNewest"] == out["order"]
    assert out["itemsNone"] == [[], [], ["rank 80 · not assessed", "rank 50 · not assessed"]]
    assert out["chips"]["listed"] == [["Not assessed"], ["Not assessed"], ["Not assessed", "Ranked low"], ["Not assessed", "Ranked low"], ["Not assessed"]]
    # The optional server filter (`state=ranked_low`) lists only them; the chip says where they are listed, never "left out".
    assert out["chips"]["lowScore"] == ["rank 49 · not assessed", "rank 20 · not assessed"]
    assert out["chips"]["lowChip"]["testId"] == "ranked-low-chip" and "listed after the other ranked postings" in out["chips"]["lowChip"]["title"]
    assert "left out" not in out["chips"]["lowChip"]["title"]


def test_there_is_no_collapse_left_in_the_model(out: dict) -> None:
    assert out["exports"] == []  # no "N weak fits, ranked low: show" line, no toggle
    assert out["parsed"] == []  # the page has no ranked-low view of its own: the rows are in the list


def test_a_changed_master_is_said_with_the_re_rank_offer(out: dict) -> None:
    stale, fresh, absent, off, running, none, served = out["stale"]
    assert stale == "Your master changed since these postings were ranked"
    assert fresh is None and absent is None and none is None
    assert off is None  # ranking is off: a re-rank would be refused, so it is not offered
    assert running is None  # the re-rank runs
    assert served is None and out["data"]["listed"]["ranking"]["stale_resume"] is False  # the server's own block: nothing changed


def test_n_not_assessed_and_what_assess_all_asks(out: dict) -> None:
    assert out["notAssessed"] == ["5 not assessed", None, None, "1 not assessed"]
    plain, filtered, several = out["allBody"]
    assert plain == {"states": ["not_assessed"]}  # every not-assessed posting, whatever is ticked: never `jobs`, never `approve`
    assert filtered == {"states": ["not_assessed"], "profile_id": "p1", "query": "python", "window": "7d"}
    assert several == {"jobs": ["a"]}  # the route takes one profile: the page's not-assessed rows are named
    # The server's answer to that ask: three are the batch, the two ranked low are the second question (the assess threshold).
    dialog = out["dialog"]
    assert (dialog["count"], dialog["total"], dialog["moreAfter"], dialog["lowRank"]["count"]) == (3, 3, 0, 2)
    assert out["dialogTitle"] == "Assess 3 postings?" and dialog["approveBody"]["approve"] is True
    assert dialog["approveBody"]["states"] == ["not_assessed"] and "jobs" not in dialog["approveBody"]


def test_the_dialog_says_top_50_by_rank_and_that_ranking_still_runs(out: dict) -> None:
    capped = out["capped"]
    assert capped["title"] == "Assess the top 50 by rank of 60 postings?"
    assert capped["batch"] == 'the top 50 by rank now, never more in one go. 10 more after these 50: "Assess these" again takes the next 10.'
    assert capped["low"] == "Include the 112 low-ranked ones in the pool (still 50 per run)."  # 0.1.11.5 ASSESS-01
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
        "listItems(rows, counts, filter.sort)", 'className="posting-divider"', "data-testid={item.testId}",
        'data-testid="not-assessed-line"', 'data-testid="assess-all"',
        "ask(assessAllBody({ filter, rows }))", 'data-testid="ranking-line"', 'data-testid="assess-these"',
    ):
        assert needle in view, needle
    assert "ranked-low-line" not in view and "toggle-ranked-low" not in view  # the collapse is gone
    panel = (UI_SRC / "components" / "RankPanel.jsx").read_text(encoding="utf-8")
    for needle in ('data-testid="stale-resume-line"', 'data-testid="rerank-stale"', "staleResumeLine(shown, job)", "onClick={askRerank}"):
        assert needle in panel, needle
    dialog = (UI_SRC / "components" / "AssessApprovalDialog.jsx").read_text(encoding="utf-8")
    assert 'data-role="approval-ranking"' in dialog and "dialog.ranking" in dialog
