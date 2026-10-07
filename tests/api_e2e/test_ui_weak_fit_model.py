"""0.1.10.9 ticket 0110-10-02: the Jobs page's weak-fit chip and the low-rank question, the model run under node.

``ui/src/postingsModel.js`` is plain JavaScript: this runs it under the system
``node`` over what the SERVER makes (``search_postings``, ``assess_these``) on
a synthetic home with one weak fit and five postings around the assess
threshold. LOUD skip without ``node``. What lives in JSX is pinned by reading
the source.

Pinned: the "Weak fit" chip is a row of the generic state filters (so the
address carries it), off by default, with the server's count; a weak-fit row
has its own chip and no question count; "Need your answers" does not count
it; the approval dialog carries the low-ranked postings as a second question
whose box decides which body Approve sends.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import posting_search
from gigai.scout.find_jobs.api import static as static_module

from tests.support.fit_fixtures import ranked_new, weak_fixture
from tests.support.posting_fixtures import NOW, build_postings_fixture

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
import * as m from MODEL_URL;

const data = DATA;
const out = {};
out.filters = m.STATE_FILTERS.map((option) => option.value);
out.emptyStates = m.EMPTY_FILTER.states;
out.defaultQuery = m.postingsQuery(m.EMPTY_FILTER);
out.chips = {
  off: m.stateChips([], data.everything.counts),
  on: m.stateChips(["weak_fit"], data.weak.counts),
  unread: m.stateChips([], null).find((chip) => chip.value === "weak_fit"),
};
out.weakQuery = m.postingsQuery({ ...m.EMPTY_FILTER, states: m.toggleState([], "weak_fit") });
out.hash = m.jobsHash({ ...m.EMPTY_FILTER, states: ["weak_fit"] }, 2);
out.parsed = m.parseJobsHash("#/jobs?page=2&state=weak_fit").filter.states;
out.needsAnswers = [m.needsAnswers(data.everything.counts), m.needsAnswers(data.weak.counts)];
out.weakCount = [m.weakFitCount(data.everything.counts), m.weakFitCount({}), m.weakFitCount(null)];
const weakRow = data.weak.postings.rows[0];
out.weakRow = { chips: m.rowChips(weakRow), score: m.scoreText(weakRow), fit: weakRow.fit };
out.listedStates = data.everything.postings.rows.map((row) => row.state);

// The approval dialog: the low-ranked postings are a second question.
out.dialog = m.approvalDialog(data.ask, data.ask.profiles);
out.line = m.lowRankLine(out.dialog.lowRank, out.dialog.count);
out.bodies = [m.approvalBody(out.dialog, false), m.approvalBody(out.dialog, true)];
out.onlyLow = m.approvalDialog(data.onlyLow, data.onlyLow.profiles);
out.onlyLowBodies = [m.approvalBody(out.onlyLow, false), m.approvalBody(out.onlyLow, true)];
out.onlyLowLine = m.lowRankLine(out.onlyLow.lowRank, out.onlyLow.count);
out.noLow = [m.lowRankLine(null), m.approvalBody(null, true)];
out.outcome = [
  m.assessOutcomeLine({ status: "assessed", assessed: { requested: 3, assessed: 3, failed: [] }, low_rank: { skipped: 2, min_rank: 50 } }),
  m.assessOutcomeLine({ status: "nothing_to_assess", low_rank: { skipped: 1, min_rank: 50 } }),
  m.assessOutcomeLine({ status: "nothing_to_assess", low_rank: null }),
];
console.log(JSON.stringify(out));
"""


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the weak-fit model was NOT run")
    assert node is not None
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    weak = weak_fixture(fx, monkeypatch)
    ranked = ranked_new(fx, monkeypatch)
    calls = fx.base.model.calls

    def search(**kwargs: object) -> dict[str, object]:
        return posting_search.search_postings(fx.home_root, fx.target, now=NOW, **kwargs)  # type: ignore[arg-type]

    data = {
        "weakUrl": weak["weak"],
        "ranked": ranked,
        "everything": search(),
        "weak": search(states=["weak_fit"]),
        "ask": posting_search.assess_these(fx.home_root, fx.target, jobs=sorted(ranked.values()), now=NOW),
        "onlyLow": posting_search.assess_these(fx.home_root, fx.target, jobs=[ranked["r20"]], now=NOW),
    }
    assert fx.base.model.calls == calls, "reading and asking called a model"
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri())).replace("DATA", json.dumps(data))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    result = json.loads(completed.stdout)
    result["data"] = data
    return result


def test_the_weak_fit_chip_is_a_generic_state_filter_off_by_default_with_the_servers_count(out: dict) -> None:
    assert out["filters"] == ["needs_answers", "assessed", "recommended", "applied", "weak_fit"]
    assert out["emptyStates"] == [] and "state" not in out["defaultQuery"], "the default list asks for no state: weak fits stay out"
    off = {chip["value"]: chip for chip in out["chips"]["off"]}
    assert (off["weak_fit"]["label"], off["weak_fit"]["active"], off["weak_fit"]["count"]) == ("Weak fit", False, 1)
    assert [chip["count"] for chip in out["chips"]["off"] if chip["value"] != "weak_fit"] == [None, None, None, None]
    on = {chip["value"]: chip for chip in out["chips"]["on"]}
    assert (on["weak_fit"]["active"], on["weak_fit"]["count"]) == (True, 1)
    assert out["chips"]["unread"]["count"] is None  # before the first read: the chip, no number
    # The chip sends the state, and the address carries it (pagination's hash, a bookmark, Back).
    assert out["weakQuery"] == "state=weak_fit&limit=50"
    assert out["hash"] == "#/jobs?page=2&state=weak_fit" and out["parsed"] == ["weak_fit"]


def test_a_weak_fit_is_not_listed_or_counted_in_need_your_answers_and_its_row_asks_nothing(out: dict) -> None:
    data = out["data"]
    assert "weak_fit" not in out["listedStates"] and data["weakUrl"] not in [row["job_identity"] for row in data["everything"]["postings"]["rows"]]
    # The header tile reads by_state.needs_answers: the three that still wait, never the weak fit.
    assert out["needsAnswers"] == [3, 0] and out["weakCount"] == [1, None, None]
    chips = out["weakRow"]["chips"]
    assert [(chip["kind"], chip["label"], chip["tone"]) for chip in chips] == [("state", "Weak fit", "plain"), ("assessed", "Assessed", "plain")]
    assert chips[0]["testId"] == "weak-fit-chip" and not any("Needs your answers" in chip["label"] for chip in chips)
    assert out["weakRow"]["score"] == "Weak fit · fit 14% · 1 of 10 requirements · rank 39" and out["weakRow"]["fit"] == 14


def test_the_approval_dialog_asks_about_the_low_ranked_separately(out: dict) -> None:
    data = out["data"]
    ranked = data["ranked"]
    dialog = out["dialog"]
    assert (dialog["count"], dialog["calls"]) == (3, 3)
    assert (dialog["lowRank"]["count"], dialog["lowRank"]["minRank"], dialog["lowRank"]["calls"]) == (2, 50, 2)
    # 0.1.11.5 ASSESS-01: the box says what it does to this run (the server's `low_rank.included`), never "assess those too".
    assert out["line"] == "Include the 2 low-ranked ones (rank below 50): 5 postings in this run instead of 3."
    plain, both = out["bodies"]
    assert plain == {"approve": True, "jobs": sorted(ranked.values())} == data["ask"]["question"]["yes"]["api"]["body"]
    assert both == {"approve": True, "jobs": sorted(ranked.values()), "include_low_rank": True}
    # Only low-ranked postings selected: nothing to approve until the box is ticked.
    assert (out["onlyLow"]["count"], out["onlyLow"]["lowRank"]["count"]) == (0, 1)
    assert out["onlyLowBodies"] == [None, {"approve": True, "jobs": [ranked["r20"]], "include_low_rank": True}]
    assert out["onlyLowLine"] == "Include the low-ranked one (rank below 50): 1 posting in this run."
    assert out["noLow"] == [None, None]
    assert out["outcome"] == [
        "Assessed 3 of 3. 2 low-ranked ones were skipped (rank below 50).",
        "Nothing was assessed. 1 low-ranked one was skipped (rank below 50).",
        "Nothing to assess: every selected posting has a current assessment.",
    ]


def test_the_jobs_page_draws_the_chip_count_the_row_fit_and_the_dialogs_box() -> None:
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert "stateChips(filter.states, counts).map((chip) => (" in view and "data-state={chip.value}" in view
    assert 'data-role="chip-count"' in view and 'data-fit={typeof row.fit === "number" ? row.fit : undefined}' in view
    # The header tile is by_state.needs_answers (a weak fit is another state), and Approve sends the body the box chose.
    assert "needsAnswers: needsAnswers(counts)" in view and "postAssessThese(approvalBody(approval.dialog, includeLowRank), { background: true })" in view
    dialog = (UI_SRC / "components" / "AssessApprovalDialog.jsx").read_text(encoding="utf-8")
    assert 'data-testid="approval-low-rank"' in dialog and "checked={includeLowRank}" in dialog
    assert "disabled={submitting || !approvalBody(dialog, includeLowRank)}" in dialog
