"""Q4a-nav-r1: the Jobs grid sorts on-demand (quick-assess) jobs exactly like
run postings.

``ui/src/jobModel.js`` is pure JavaScript (no React, no JSX), so this test
runs it under the system ``node`` -- the same binary ``vite build`` needs,
no JS test runner or package.json script added (coordinator decision,
Q4a-nav-r1) -- and asserts on the JSON the script prints, never on its exit
code alone. It is a LOUD skip when ``node`` is not on PATH.

The rule under test (operator answer 1, Q4a): verdict group
matched > needs answers > assessed-no-verdict > not assessed > not a match,
then Jev score descending (unscored last), then ``published_at`` newest
first. A mixed list puts one run posting and one quick-assess item in
EVERY verdict group, with a quick item that Jev never scored, and the
expected order interleaves them: nothing about the source moves a card.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
JOB_MODEL_JS = UI_SRC / "jobModel.js"

# One run posting and one quick-assess item per verdict group. Run rows are
# boardRows.js's shape (posting + status + the run's own assessment); quick
# items are GET /api/assessments entries (job.job_identity / normalized_url
# + result). `rank` gives every job but `quick-none-unscored` a Jev score,
# so the within-group order is decided by score and the unscored quick item
# sorts last in its group; the two not-assessed jobs have no assessment at
# all, so published_at decides and the quick one (published_at null) is last.
RUN_AT = "2026-09-25T10:00:00+00:00"
QUICK_AT = "2026-09-25T11:00:00+00:00"  # newer than the run: the store wins ties

RUN_ROWS = [
    ("run-match", "matched_above_threshold", 70),
    ("run-pend", "pending_user_answers", 70),
    ("run-assessed", None, 70),  # a result with no verdict: the "assessed" group
    ("run-none", "not_assessed", 70),
    ("run-nomatch", "not_a_match", 70),
]
QUICK_ITEMS = [
    ("quick-match", "matched_above_threshold", 90),
    ("quick-pend", "pending_user_answers", 50),
    ("quick-assessed", None, 90),
    ("quick-none-unscored", "not_assessed", None),
    ("quick-nomatch", "not_a_match", 90),
]

EXPECTED_ORDER = [
    "quick-match",  # 90 > 70
    "run-match",
    "run-pend",  # 70 > 50
    "quick-pend",
    "quick-assessed",  # 90 > 70
    "run-assessed",
    "run-none",  # 70 beats the unscored quick item (score null sorts last)
    "quick-none-unscored",
    "quick-nomatch",  # 90 > 70
    "run-nomatch",
]


def _result(verdict: str | None) -> dict | None:
    if verdict == "not_assessed":
        return None
    payload: dict = {"matrix": [], "structured_questions": []}
    if verdict is not None:
        payload["verdict"] = verdict
    return payload


def _fixture() -> dict:
    rows = []
    rank_scores = []
    for job_id, verdict, score in RUN_ROWS:
        url = f"https://example.test/{job_id}"
        assessment = _result(verdict)
        rows.append(
            {
                "posting": {"normalized_url": url, "title": job_id, "company": "Acme", "location": "Denver, CO", "provider": "greenhouse", "source_kind": "ats", "published_at": "2026-09-20T00:00:00+00:00"},
                "status": "assessed" if assessment else "not_assessed",
                "assessment": assessment,
            }
        )
        rank_scores.append({"normalized_url": url, "score": score, "fit": "strong", "reasons": [], "mismatch_flags": [], "hidden_by_default": False})
    quick_items = []
    for job_id, verdict, score in QUICK_ITEMS:
        identity = f"https://example.test/{job_id}"
        quick_items.append(
            {
                "job": {"job_identity": identity, "normalized_url": identity, "title": job_id, "company": "Acme", "location": "", "source_url": identity, "fetch_kind": "generic"},
                "result": _result(verdict),
                "created_at": QUICK_AT,
                "updated_at": QUICK_AT,
                "history": [],
            }
        )
        if score is not None:
            rank_scores.append({"normalized_url": identity, "score": score, "fit": "strong", "reasons": [], "mismatch_flags": [], "hidden_by_default": False})
    return {"rows": rows, "rankScores": rank_scores, "quickItems": quick_items, "runCreatedAt": RUN_AT}


# The node side: build the grid's job list the way FindJobsView does
# (buildJobs merges rows + the quick store; on-demand items become
# status "on_demand" jobs) and sort it; print JSON only.
NODE_SCRIPT = """
import { buildJobs, sortJobs } from {module_url};
const fixture = JSON.parse(process.argv[1]);
const jobs = buildJobs(fixture);
const sorted = sortJobs(jobs);
process.stdout.write(JSON.stringify({
  order: sorted.map((job) => job.posting.title),
  statuses: Object.fromEntries(sorted.map((job) => [job.posting.title, job.status])),
  verdicts: Object.fromEntries(sorted.map((job) => [job.posting.title, job.verdict])),
  scores: Object.fromEntries(sorted.map((job) => [job.posting.title, job.rank ? job.rank.score : null])),
}));
"""


def _run_node(fixture: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; jobModel sort check not run")
    script = NODE_SCRIPT.replace("{module_url}", json.dumps(JOB_MODEL_JS.resolve().as_uri()))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(fixture)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken script, not a sort bug
        raise AssertionError(f"node printed no JSON: {completed.stdout!r}\n{completed.stderr}") from exc


def test_quick_assess_jobs_sort_like_run_postings() -> None:
    fixture = _fixture()
    out = _run_node(fixture)

    # The merge kept every job and typed the sources apart.
    assert sorted(out["order"]) == sorted(job_id for job_id, _, _ in RUN_ROWS + QUICK_ITEMS)
    assert {job_id: out["statuses"][job_id] for job_id, _, _ in QUICK_ITEMS} == {job_id: "on_demand" for job_id, _, _ in QUICK_ITEMS}
    assert all(out["statuses"][job_id] != "on_demand" for job_id, _, _ in RUN_ROWS)
    assert out["scores"]["quick-none-unscored"] is None

    # The order: verdict group, then Jev score, then published_at -- the
    # source never enters into it.
    assert out["order"] == EXPECTED_ORDER
    assert out["verdicts"]["quick-assessed"] == "assessed"
    assert out["verdicts"]["run-assessed"] == "assessed"
    assert out["verdicts"]["quick-none-unscored"] == "not_assessed"


def test_a_source_swap_does_not_change_the_order() -> None:
    """Move every Jev score from the quick items onto the run postings and
    back: the order changes ONLY with the scores, never with the source."""

    fixture = _fixture()
    for score in fixture["rankScores"]:
        score["score"] = 100 - score["score"]  # 90 <-> 10, 70 <-> 30, 50 <-> 50
    out = _run_node(fixture)
    assert out["order"] == [
        "run-match",  # 30 > 10
        "quick-match",
        "quick-pend",  # 50 > 30
        "run-pend",
        "run-assessed",  # 30 > 10
        "quick-assessed",
        "run-none",
        "quick-none-unscored",
        "run-nomatch",  # 30 > 10
        "quick-nomatch",
    ]
