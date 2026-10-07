"""0.1.11.5 UI-01: the fit and rank chips of a Jobs row, and the assessed job page's box, run under node.

``ui/src/postingsModel.js`` is plain JavaScript; rows here are synthetic and shaped as the server serves them
(``scout_new.posting_row``: ``fit``, ``rank_score``, ``assessment.met`` / ``.requirements``, ``score_text``).
LOUD skip without ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
THIN = "Thin posting, not enough requirements to score"

SCRIPT = """
import * as m from POSTINGS_URL;

const assessed = {
  state: "matched", thin_posting: false, fit: 92, rank_score: 92, assessment: { met: 19, requirements: 22 },
  score_text: "Matched · fit 92% · 19 of 22 requirements · rank 92 · resume tailored", // the server's sentence: the gaps are added by the page
  minor_gap_text: "3 minor gaps: Helm, Istio, Argo CD", minor_gaps: ["Helm", "Istio", "Argo CD"],
};
const notAssessed = { state: "not_assessed", thin_posting: false, fit: null, rank_score: 82, assessment: null, score_text: "rank 82 · not assessed" };
const rankedLow = { ...notAssessed, rank_score: 31, ranked_low: true, score_text: "rank 31 · not assessed" };
const notRanked = { ...notAssessed, rank_score: null, score_text: "not ranked yet · not assessed" };
const thin = {
  state: "matched", thin_posting: true, fit: null, rank_score: 90, assessment: { met: 2, requirements: 2 },
  score_text: "Thin posting, not enough requirements to score · 2 of 2 requirements · rank 90", minor_gap_text: "1 minor gap: Helm",
};
const applied = { ...assessed, application: { status: "applied", since: "2026-10-06T10:00:00Z" } };
const old = { ...assessed, stale_reason: "older_prompt", score_text: "Matched (old assessment: older prompt) · fit 92% · 19 of 22 requirements · rank 92", minor_gap_text: null };
const rows = { assessed, notAssessed, rankedLow, notRanked, thin, applied, old };
const out = { chips: {}, state: {}, box: {}, rowChips: {} };
for (const [name, row] of Object.entries(rows)) {
  out.chips[name] = m.scoreChips(row).map((chip) => [chip.kind, chip.label, chip.testId]);
  out.state[name] = m.stateText(row);
  out.box[name] = m.scoreBox(row);
  out.rowChips[name] = m.rowChips(row).map((chip) => chip.label);
}
// 0.1.11.5 (B1 review): the Fit chip's colour by the row's state. Green ("ok") only for a match.
const fitAt50 = { thin_posting: false, fit: 50, rank_score: 71, assessment: { met: 5, requirements: 10 } };
out.tones = {};
for (const state of ["matched", "tailored", "needs_answers", "has_gap", "weak_fit", "not_a_match"]) {
  out.tones[state] = Object.fromEntries(m.scoreChips({ ...fitAt50, state }).map((chip) => [chip.kind, chip.tone]));
}
out.tones.thin_posting = Object.fromEntries(m.scoreChips(thin).map((chip) => [chip.kind, chip.tone]));
out.tones.not_assessed = Object.fromEntries(m.scoreChips(notAssessed).map((chip) => [chip.kind, chip.tone]));
out.greenStates = m.FIT_GREEN_STATES;
out.noScoreText = m.stateText({ state: "matched" });
out.noListedRow = [m.scoreBox(null), m.scoreChips(null)];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the score chips model was NOT run")
    assert node is not None
    script = SCRIPT.replace("POSTINGS_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri()))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_an_assessed_row_has_a_fit_chip_and_a_rank_chip(out: dict) -> None:
    assert out["chips"]["assessed"] == [["fit", "Fit 92% · 19/22", "fit-chip"], ["rank", "Rank 92", "rank-chip"]]
    assert out["chips"]["old"] == out["chips"]["assessed"]


def test_the_fit_chip_is_green_only_on_a_matched_row_and_neutral_on_every_other_state(out: dict) -> None:
    """0.1.11.5 (B1 review): a green chip read as "good fit" on a job that waits on answers at Fit 50%."""

    tones = out["tones"]
    assert tones["matched"] == {"fit": "ok", "rank": "plain"}
    assert tones["tailored"] == {"fit": "ok", "rank": "plain"}  # a matched job with its resume
    for state in ("needs_answers", "has_gap", "weak_fit", "not_a_match"):
        assert tones[state] == {"fit": "plain", "rank": "plain"}, state
    assert tones["thin_posting"] == {"rank": "plain"} and tones["not_assessed"] == {"rank": "plain"}  # no fit chip at all
    assert out["greenStates"] == ["matched", "tailored"]
    # The page draws the tone as a class, and only `tone-ok` is green.
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert "className={`score-chip ${chip.kind} tone-${chip.tone}`}" in view and "data-tone={chip.tone}" in view
    styles = (UI_SRC / "styles.css").read_text(encoding="utf-8")
    assert ".score-chip.fit.tone-ok { border-color: var(--ok); background: var(--ok-bg); color: var(--ok); }" in styles
    assert ".score-chip.fit {" not in styles, "a fit chip with no tone must not be green"


def test_a_not_assessed_row_has_the_rank_chip_alone_and_a_ranked_low_row_keeps_its_own_chip(out: dict) -> None:
    assert out["chips"]["notAssessed"] == [["rank", "Rank 82", "rank-chip"]]
    assert out["chips"]["rankedLow"] == [["rank", "Rank 31", "rank-chip"]]
    assert "Ranked low" in out["rowChips"]["rankedLow"]  # the state chip (and the list's divider) stay
    assert out["chips"]["notRanked"] == [["rank", "Not ranked yet", "rank-chip"]]  # the old sentence said so too


def test_a_thin_posting_shows_no_fit_chip(out: dict) -> None:
    assert out["chips"]["thin"] == [["rank", "Rank 90", "rank-chip"]]
    assert out["state"]["thin"] == THIN, "no number and no minor gap in the middle column of a thin posting"
    assert out["rowChips"]["thin"][0] == "Thin posting"


def test_the_middle_column_keeps_the_state_and_only_the_count_of_minor_gaps(out: dict) -> None:
    assert out["state"]["assessed"] == "Matched · resume tailored · 3 minor gaps"
    assert out["state"]["notAssessed"] == "not assessed" and out["state"]["notRanked"] == "not assessed"
    assert out["state"]["old"] == "Matched (old assessment: older prompt)"
    for name in ("assessed", "notAssessed", "rankedLow", "thin", "old"):
        text = out["state"][name]
        assert "%" not in text and "rank" not in text.lower() and "requirements" not in text.replace("not enough requirements to score", "")
        assert "Helm" not in text, "the gap names stay on the job page"
    assert out["noScoreText"] is None


def test_the_applied_badge_is_still_shown(out: dict) -> None:
    assert out["rowChips"]["applied"][0].startswith("Applied")
    assert out["chips"]["applied"] == out["chips"]["assessed"]


def test_the_assessed_job_page_box_has_the_fit_and_the_rank_under_it(out: dict) -> None:
    box = out["box"]["assessed"]
    assert box == {"fit": {"percent": "92%", "requirements": "19 of 22 requirements", "short": "92% · 19/22"}, "rank": "Rank 92"}
    assert out["box"]["thin"] == {"fit": None, "rank": "Rank 90"}
    # A page not assessed keeps its own rank tile ("82 likely fit"), so no box here; so does a missing Jobs row.
    assert out["box"]["notAssessed"] is None and out["box"]["rankedLow"] is None
    assert out["noListedRow"] == [None, []]  # no row, no chips
