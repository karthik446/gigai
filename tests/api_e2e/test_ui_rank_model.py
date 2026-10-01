"""SCOPE-ADD-3 D: the pages' ranking, counts and honest copy, run under node.

Replaces ``test_ui_jev_pass_model.py`` and ``test_ui_rank_status_model.py``
(both pinned the retired Jev ranker's UI, deleted with it). ``ui/src/
rankModel.js``, ``jobModel.js`` and ``boardRows.js`` are pure JavaScript (no
React), so this test runs them under the system ``node`` the way the other
``test_ui_*_model.py`` files do (no JS test runner) and asserts on the JSON
the script prints. LOUD skip when ``node`` is not on PATH. What lives in JSX
is checked statically, by reading the source; the shipped bundle
(``ui/dist``) is checked for the same words.

What is pinned (operator decisions after the ranking spike):

* streaming: the live grid is ``GET /progress``'s postings the way
  FindJobsView builds it (rowsFromProgress -> mergeRows -> buildJobs ->
  sortJobs). The first batch shows ranked the moment it lands, the list
  re-orders by score as batches arrive, postings not ranked yet come after
  the ranked ones, and a posting with a blocker comes last, still listed,
  with its blocker. While the operator is on the list the drawn order holds
  and new cards join at the end (``streamOrder``).
* counts: "Ranked 350 of 1,458 · Assessing 3 of 10" from ``progress.rank``
  and ``progress.assess_counts`` as the server folds them (``read_rank``,
  run here on a real ``rank.jsonl``); either or both null render nothing and
  never throw.
* the re-rank pass: one POST /rank ``{start: true}`` per click, ``{}`` reads
  only while the record says ``running``, ``{cancel: true}`` for Cancel, the
  results are re-read once when a pass it saw running ends, and answers for
  a run no longer shown are dropped.
* honest copy: "likely fits first · likely no-matches last" is on the grid
  and in the bundle; "best matches first" is nowhere; a card's tooltip has
  the model's reasons and blockers and says a rank is not a match.
* Jev is gone from the UI: no source file mentions it, the Settings panel,
  the wizard's key hint, the run dialog's consent line, the cards' tile and
  the "Score with Jev" bar are gone, and so are their files.
* uat-bug-029: POST /api/assess 422 ``posting_requirements_unreadable`` is
  "Couldn't read this posting's requirements" (the server's own words), shown
  as a note on the job page and the Assess page, never an error.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import quick_assess
from gigai.scout.find_jobs import progress as progress_module
from gigai.scout.find_jobs.api import static as static_module

UI_ROOT = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI_ROOT / "src"
UI_DIST = UI_ROOT / "dist"

NODE_SCRIPT = """
import * as rank from {rank_url};
import * as jobModel from {job_model_url};
import * as boardRows from {board_rows_url};
import * as api from {api_url};
const input = JSON.parse(process.argv[process.argv.length - 1]);

// The live grid, built the way FindJobsView builds it on every poll.
let rows = [];
let drawn = [];
const snapshots = input.snapshots.map((snapshot) => {{
  rows = boardRows.mergeRows(rows, boardRows.rowsFromProgress(snapshot));
  const jobs = jobModel.sortJobs(jobModel.buildJobs({{ rows, rankScores: [], quickItems: [], runCreatedAt: null }}));
  const held = rank.streamOrder(jobs, drawn, true);
  const out = {{
    order: jobs.map((job) => job.posting.title),
    held: held.map((job) => job.posting.title),
    free: rank.streamOrder(jobs, drawn, false).map((job) => job.posting.title),
    tiles: Object.fromEntries(jobs.map((job) => [job.posting.title, rank.rankTileText(job.rank)])),
    blockers: Object.fromEntries(jobs.map((job) => [job.posting.title, rank.rankBlockersLine(job.rank)])),
    filters: Object.fromEntries(jobs.map((job) => [job.posting.title, rank.rankFilterValue(job.rank)])),
    tooltips: Object.fromEntries(jobs.map((job) => [job.posting.title, rank.rankTooltip(job.rank)])),
  }};
  drawn = jobs.map((job) => job.id);
  return out;
}});

// The finished run's grid: /results rows with their `rank`, an assessed row
// in its verdict group first, and a live re-rank score merged over a row.
const resultsJobs = jobModel.sortJobs(jobModel.buildJobs({{
  rows: boardRows.rowsFromResults(input.results),
  rankScores: rank.mergeRankScores(api.storedRankScores({{ payload: input.results }}), input.freshScores),
  quickItems: [],
  runCreatedAt: null,
}}));

// A re-rank pass against a scripted POST /rank.
async function simulate(answers, {{ first = "start", stopAfter = null, current = true, cancelAt = null }} = {{}}) {{
  const calls = [];
  const seen = [];
  const timers = [];
  let ends = 0;
  const pending = [];
  const pass = rank.createRankPass({{
    runId: "run_1",
    postRank: (runId, fields) => {{
      calls.push(fields);
      const answer = answers[Math.min(calls.length - 1, answers.length - 1)];
      return new Promise((resolve) => pending.push(() => resolve(answer)));
    }},
    onResponse: (response) => seen.push(response.rank_record ? response.rank_record.status : null),
    onEnd: () => {{ ends += 1; }},
    isCurrent: () => current,
    schedule: (callback, ms) => {{ timers.push({{ callback, ms }}); return timers.length; }},
    cancel: (id) => {{ timers[id - 1].cleared = true; }},
    intervalMs: 2000,
  }});
  let step = first === "read" ? pass.read() : pass.start();
  for (let turn = 0; turn < 10; turn += 1) {{
    if (stopAfter !== null && calls.length > stopAfter) {{
      pass.stop();
    }}
    const release = pending.shift();
    if (!release) {{
      break;
    }}
    release();
    await step;
    if (cancelAt !== null && calls.length === cancelAt) {{
      step = pass.cancel();
      continue;
    }}
    const timer = timers[timers.length - 1];
    if (!timer || timer.used || timer.cleared) {{
      break;
    }}
    timer.used = true;
    step = timer.callback();
  }}
  return {{ calls, seen, timers: timers.map((timer) => timer.ms), ends }};
}}

// uat-bug-029 through the real api.js request(): a fake fetch answers 422.
globalThis.fetch = async () => ({{
  ok: false,
  status: 422,
  text: async () => JSON.stringify({{ error: {{ code: "posting_requirements_unreadable", message: input.unreadableMessage }} }}),
}});
let unreadable = null;
try {{
  await api.postAssess({{ job: {{ job_url: "https://example.test/1" }} }});
}} catch (error) {{
  unreadable = {{ status: error.status, code: error.code, message: error.message, isUnreadable: rank.isRequirementsUnreadable(error) }};
}}

process.stdout.write(JSON.stringify({{
  snapshots,
  results: resultsJobs.map((job) => ({{ title: job.posting.title, score: job.rank ? job.rank.score : null, reasons: rank.rankReasonsLine(job.rank), blockers: rank.rankBlockersLine(job.rank) }})),
  counts: input.counts.map(([r, a]) => rank.rankCountsLine(r, a)),
  rankedBy: input.rankedBy.map(([s, r]) => rank.rankedByLine(s, r)),
  shortfall: input.shortfall.map(([r, s]) => rank.rankShortfallLine(r, s)),
  passLines: input.records.map((record) => rank.rankPassLine(record)),
  buttons: [rank.rankButtonLabel(false), rank.rankButtonLabel(true)],
  reasonWords: input.reasons.map((reason) => rank.rankReasonWords(reason)),
  notes: {{ order: rank.RANK_ORDER_NOTE, honest: rank.RANK_HONEST_NOTE, fit: rank.FIT_WORDS, unreadable: rank.REQUIREMENTS_UNREADABLE_TEXT }},
  pass: await simulate(input.passAnswers),
  passCancelled: await simulate(input.cancelAnswers, {{ cancelAt: 1 }}),
  passFirstReadIdle: await simulate([input.passAnswers[2]], {{ first: "read" }}),
  passFirstReadRunning: await simulate(input.passAnswers, {{ first: "read" }}),
  passStopped: await simulate(input.passAnswers, {{ stopAfter: 1 }}),
  passNotCurrent: await simulate(input.passAnswers, {{ current: false }}),
  unreadable,
  unreadableOthers: [rank.isRequirementsUnreadable(null), rank.isRequirementsUnreadable({{ code: "assess_timeout" }})],
}}));
"""


def _posting(title: str, published: str, rank: dict | None = None) -> dict[str, object]:
    posting: dict[str, object] = {
        "normalized_url": f"https://boards.greenhouse.io/acme/jobs/{title}",
        "title": title,
        "company": "Acme",
        "location": "Remote",
        "published_at": published,
        "provider": "greenhouse",
        "source_kind": "ats",
    }
    if rank is not None:
        posting["rank"] = rank
    return posting


def _line(score: int | None, *, reasons: tuple[str, ...] = (), blockers: tuple[str, ...] = (), unscored: str | None = None) -> dict:
    return {
        "score": score,
        "reasons": list(reasons),
        "blockers": list(blockers),
        "demoted": bool(blockers) and score is not None,
        "unscored_reason": unscored,
        "batch_id": "b000",
    }


# Six postings, newest first by date: a..f. The server's own folding
# (progress._with_rank) attaches `rank` per posting; the UI must not rely on
# the server's order, so each snapshot lists them in DATE order.
DATES = {"a": "2026-09-28", "b": "2026-09-27", "c": "2026-09-26", "d": "2026-09-25", "e": "2026-09-24", "f": "2026-09-23"}
BATCH_1 = {"d": _line(90, reasons=("Python services match",)), "a": _line(40), "f": _line(70)}
BATCH_2 = {
    "b": _line(99, reasons=("Stack overlap",), blockers=("Requires an active clearance",)),
    "c": _line(None, unscored="invalid: no answer"),
    "e": _line(55),
}


def _snapshot(ranks: dict) -> dict:
    return {"postings": [_posting(title, DATES[title], ranks.get(title)) for title in DATES], "assessments": []}


def _payload() -> dict:
    results_rows = [
        {"posting": _posting("r-low", "2026-09-28"), "outcome": "new", "rank_score": None, "rank": _line(30)},
        {"posting": _posting("r-high", "2026-09-20"), "outcome": "new", "rank_score": None, "rank": _line(88, reasons=("Senior Go role",))},
        {"posting": _posting("r-assessed", "2026-09-10"), "outcome": "new", "rank_score": None, "rank": _line(20)},
        {"posting": _posting("r-older-run", "2026-09-27"), "outcome": "new", "rank_score": {
            "normalized_url": "https://boards.greenhouse.io/acme/jobs/r-older-run", "fit": "maybe", "score": 60,
            "reasons": [], "mismatch_flags": [], "hidden_by_default": True}, "rank": None},
        {"posting": _posting("r-rerank", "2026-09-26"), "outcome": "new", "rank_score": None, "rank": _line(10, reasons=("Stale reason",))},
    ]
    results = {
        "rows": results_rows,
        "assessments": [{"posting": results_rows[2]["posting"], "verdict": "matched_above_threshold", "matrix": [], "structured_questions": []}],
        "not_assessed": [],
    }
    running = {"rank_record": {"record_id": "rank_1", "status": "running", "ranked": 50, "total": 480, "text": "Ranked 50 of 480"}, "scores": []}
    done = {"rank_record": {"record_id": "rank_1", "status": "complete", "ranked": 480, "total": 480, "text": "Ranked 480 of 480"}, "scores": []}
    cancelled = {"rank_record": {"record_id": "rank_1", "status": "cancelled", "ranked": 100, "total": 480, "text": "Ranked 100 of 480"}, "scores": []}
    return {
        "snapshots": [_snapshot({}), _snapshot(BATCH_1), _snapshot({**BATCH_1, **BATCH_2})],
        "results": results,
        "freshScores": [{"normalized_url": "https://boards.greenhouse.io/acme/jobs/r-rerank", "fit": "strong", "score": 95, "reasons": [], "mismatch_flags": []}],
        "counts": [
            [{"status": "running", "ranked": 350, "total": 1458, "text": "Ranked 350 of 1,458"}, {"selected": 10, "position": 3, "status": "running", "text": "Assessing 3 of 10"}],
            [{"status": "running", "ranked": 350, "total": 1458}, {"selected": 10, "position": 3}],
            [None, None],
            [{"status": "complete", "ranked": 13, "total": 13, "text": "Ranked 13 of 13"}, None],
            [None, {"selected": 10, "finished": 10, "position": 10, "status": "done", "text": "Assessed 10 of 10"}],
            [{}, {}],
            ["ranked", 3],
            [{"ranked": 7}, None],
        ],
        "rankedBy": [
            [{"resolved_model": "gpt-5-codex", "model_target": "codex_cli"}, {"model_target": "codex_cli"}],
            [None, {"model_target": "codex_cli"}],
            [None, None],
            [{"model_target": ""}, {}],
        ],
        "shortfall": [
            [{"status": "partial", "fail_open_reason": "call_budget: 12 of 480 postings unscored"}, None],
            [{"status": "complete", "fail_open_reason": None}, None],
            [{"status": "running"}, None],
            [None, {"status": "skipped", "reason": "no_resume"}],
            [None, {"status": "scored", "reason": None}],
            [None, None],
        ],
        "records": [
            running["rank_record"],
            done["rank_record"],
            cancelled["rank_record"],
            {"status": "interrupted", "ranked": 30, "total": 80},
            {"status": "partial", "text": "Ranked 480 of 480", "fail_open_reason": "call_budget: 3 of 480 postings unscored"},
            {"status": "skipped", "fail_open_reason": "model_target_unavailable: codex not found"},
            None,
        ],
        "reasons": ["call_budget: 12 of 480 postings unscored", "cancelled", "model_target_unavailable: codex not on PATH", "error:TimeoutError", "12 of 480 postings unscored", "", None],
        "passAnswers": [running, running, done],
        "cancelAnswers": [running, cancelled],
        "unreadableMessage": quick_assess.POSTING_UNREADABLE_MESSAGE,
    }


def _run(node: str, script: str, payload: object) -> dict:
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def node() -> str:
    found = shutil.which("node")
    if found is None:
        pytest.skip("node not found on PATH; the UI's rank model was not run")
    assert found is not None
    return found


@pytest.fixture(scope="module")
def out(node: str) -> dict:
    script = NODE_SCRIPT.format(
        rank_url=json.dumps((UI_SRC / "rankModel.js").as_uri()),
        job_model_url=json.dumps((UI_SRC / "jobModel.js").as_uri()),
        board_rows_url=json.dumps((UI_SRC / "boardRows.js").as_uri()),
        api_url=json.dumps((UI_SRC / "api.js").as_uri()),
    )
    return _run(node, script, _payload())


def _source(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


def _dist_js() -> str:
    bundles = sorted((UI_DIST / "assets").glob("index-*.js"))
    assert len(bundles) == 1, f"ui/dist must ship exactly one bundle: {bundles}"
    index = (UI_DIST / "index.html").read_text(encoding="utf-8")
    assert f"/assets/{bundles[0].name}" in index, "index.html does not load the shipped bundle"
    return bundles[0].read_text(encoding="utf-8")


# --- streaming ----------------------------------------------------------------------


def test_before_any_batch_lands_the_grid_is_newest_first_and_nothing_is_ranked(out: dict) -> None:
    first = out["snapshots"][0]
    assert first["order"] == ["a", "b", "c", "d", "e", "f"]
    assert set(first["filters"].values()) == {"unranked"}
    assert all(tile == {"score": "–", "label": "not ranked"} for tile in first["tiles"].values())


def test_the_first_batch_shows_ranked_at_once_best_first_before_the_unranked(out: dict) -> None:
    second = out["snapshots"][1]
    # d 90, f 70, a 40 landed; b, c, e wait for their batch, newest first.
    assert second["order"] == ["d", "f", "a", "b", "c", "e"]
    assert second["tiles"]["d"] == {"score": "90", "label": "likely fit"}
    assert second["tiles"]["a"] == {"score": "40", "label": "possible fit"}
    assert second["tooltips"]["d"].startswith("Rank 90 · likely fit\nWhy: Python services match")


def test_each_batch_reorders_by_score_unscored_after_ranked_and_a_blocker_last_but_listed(out: dict) -> None:
    third = out["snapshots"][2]
    # e 55 slots between f 70 and a 40; c (the model gave no score) follows
    # the scored ones; b scored 99 but names a blocker: last, still listed.
    assert third["order"] == ["d", "f", "e", "a", "c", "b"]
    assert third["filters"]["b"] == "blocked" and third["filters"]["c"] == "unranked"
    assert third["tiles"]["b"] == {"score": "99", "label": "blocker"}
    assert third["blockers"]["b"] == "Blocker: Requires an active clearance"
    assert "Blocker: Requires an active clearance" in third["tooltips"]["b"]
    assert "Why: Stack overlap" in third["tooltips"]["b"]
    assert third["tooltips"]["c"] == "Not ranked: invalid: no answer"


def test_while_the_operator_is_on_the_list_the_drawn_order_holds(out: dict) -> None:
    second, third = out["snapshots"][1], out["snapshots"][2]
    # Held: the order drawn before the batch landed stays, card for card.
    assert second["held"] == out["snapshots"][0]["order"]
    assert third["held"] == second["order"]
    # Released: the new order.
    assert third["free"] == third["order"]


def test_a_finished_runs_grid_orders_by_verdict_then_rank_and_a_rerank_score_wins(out: dict) -> None:
    titles = [row["title"] for row in out["results"]]
    # The assessed (matched) row leads its verdict group; then by score: the
    # live re-rank's 95 over the stored 10, the row's own 88, the older run's
    # stored RankScore 60 (its hidden_by_default ignored: nothing is hidden),
    # the stored 30.
    assert titles == ["r-assessed", "r-rerank", "r-high", "r-older-run", "r-low"]
    by_title = {row["title"]: row for row in out["results"]}
    assert by_title["r-rerank"]["score"] == 95 and by_title["r-rerank"]["reasons"] == ""  # a stale reason never rides on a new score
    assert by_title["r-high"]["reasons"] == "Senior Go role"


# --- counts -------------------------------------------------------------------------


def test_the_run_page_counts_ranked_and_assessing(out: dict) -> None:
    assert out["counts"] == [
        "Ranked 350 of 1,458 · Assessing 3 of 10",
        "Ranked 350 of 1,458 · Assessing 3 of 10",  # from the numbers when no text is served
        None,  # a run sealed before either existed: nothing
        "Ranked 13 of 13",
        "Assessed 10 of 10",
        None,
        None,  # junk never throws
        "Ranked 7",
    ]


def test_the_counts_are_the_servers_own_words(tmp_path: Path, node: str) -> None:
    lines = [
        {"event": "started", "total": 1458, "model_target": "codex_cli", "batch_size": 50, "concurrency": 8, "max_calls": 180, "at": "2026-09-29T10:00:00Z"},
        *(
            {"event": "batch", "batch_id": f"b{index:03d}", "total": 1458, "postings": [
                {"normalized_url": f"https://x.test/{index}/{item}", "score": 50, "reasons": [], "blockers": [], "demoted": False, "unscored_reason": None}
                for item in range(50)
            ]}
            for index in range(7)
        ),
    ]
    (tmp_path / "rank.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    folded = progress_module.read_rank(tmp_path)
    assert folded is not None and folded["text"] == "Ranked 350 of 1,458"
    served = {key: value for key, value in folded.items() if key != "scores"}  # GET /progress drops `scores`
    no_text = {key: value for key, value in served.items() if key != "text"}
    script = (
        f"import * as rank from {json.dumps((UI_SRC / 'rankModel.js').as_uri())};\n"
        "const [a, b] = JSON.parse(process.argv[process.argv.length - 1]);\n"
        "process.stdout.write(JSON.stringify([rank.rankCountsLine(a, null), rank.rankCountsLine(b, null), rank.rankedByLine(null, a)]));\n"
    )
    assert _run(node, script, [served, no_text]) == ["Ranked 350 of 1,458", "Ranked 350 of 1,458", "Ranked by your model: codex_cli"]


def test_ranked_by_is_said_only_when_the_server_names_the_model(out: dict) -> None:
    assert out["rankedBy"] == ["Ranked by your model: gpt-5-codex", "Ranked by your model: codex_cli", None, None]


def test_a_pass_that_left_postings_unranked_says_so(out: dict) -> None:
    assert out["shortfall"] == [
        "Some postings were not ranked (stopped at its call limit (12 of 480 postings unscored)). They are listed after the ranked ones.",
        None,
        None,
        "Some postings were not ranked (no resume). They are listed after the ranked ones.",
        None,
        None,
    ]
    assert out["reasonWords"] == [
        "stopped at its call limit (12 of 480 postings unscored)",
        "stopped before every posting was ranked",
        "your model was not available",
        "error: TimeoutError",
        "12 of 480 postings unscored",
        "",
        "",
    ]


def test_the_status_panel_and_both_pages_show_the_counts() -> None:
    panel = _source("components/NodeStatusList.jsx")
    assert "rankCountsLine(rank, assessCounts)" in panel and 'data-role="rank-counts"' in panel
    assert "rankedByLine(rankStatus, rank)" in panel and "rankShortfallLine(rank, rankStatus)" in panel
    view = _source("views/FindJobsView.jsx")
    assert view.count("<NodeStatusList") == 2
    assert view.count("rank={progress?.rank}") == 2 and view.count("assessCounts={progress?.assess_counts}") == 2
    # the run's last counts are read once more when it ends
    terminal = view[view.index("if (TERMINAL_STATUSES.has(status.status)) {") :][:400]
    assert "getRunProgress(id)" in terminal


# --- the Rank / Re-rank pass ----------------------------------------------------------


def test_the_click_starts_one_pass_reads_while_it_runs_and_rereads_the_results_once(out: dict) -> None:
    assert out["pass"]["calls"] == [{"start": True}, {}, {}]
    assert out["pass"]["seen"] == ["running", "running", "complete"]
    assert out["pass"]["timers"] == [2000, 2000], "a read is scheduled only while the pass runs"
    assert out["pass"]["ends"] == 1


def test_cancel_posts_cancel_and_the_pass_ends(out: dict) -> None:
    assert out["passCancelled"]["calls"] == [{"start": True}, {"cancel": True}]
    assert out["passCancelled"]["seen"] == ["running", "cancelled"]
    assert out["passCancelled"]["ends"] == 1


def test_opening_a_run_reads_once_and_starts_nothing(out: dict) -> None:
    idle = out["passFirstReadIdle"]
    assert idle["calls"] == [{}] and idle["timers"] == [] and idle["ends"] == 0
    # a pass already running (another tab, before a reload) is followed
    running = out["passFirstReadRunning"]
    assert running["calls"] == [{}, {}, {}] and running["ends"] == 1
    assert all("start" not in call for call in running["calls"])


def test_a_stopped_pass_or_another_run_shown_drops_the_answers(out: dict) -> None:
    assert out["passStopped"]["seen"] == ["running"] and out["passStopped"]["ends"] == 0
    assert out["passNotCurrent"]["seen"] == [] and out["passNotCurrent"]["timers"] == []


def test_the_bar_says_what_the_pass_did(out: dict) -> None:
    assert out["buttons"] == ["Rank", "Re-rank"]
    assert out["passLines"] == [
        "Re-ranking: Ranked 50 of 480",
        "Re-rank done: Ranked 480 of 480",
        "Re-rank cancelled: Ranked 100 of 480",
        "Re-rank interrupted: Ranked 30 of 80; Re-rank picks it up again",
        "Re-rank ended early: Ranked 480 of 480 (stopped at its call limit (3 of 480 postings unscored))",
        "Re-rank skipped: your model was not available",
        None,
    ]


def test_the_view_posts_rank_only_through_the_pass() -> None:
    view = _source("views/FindJobsView.jsx")
    assert view.count("postRank") == 2 and "createRankPass({" in view and "        postRank,\n" in view
    assert 'data-action="rank"' in view and 'data-action="cancel-rank"' in view
    assert 'followRankPass(runId, "start")' in view and 'followRankPass(runId, "cancel")' in view
    assert 'followRankPass(id, "read");' in view
    model = _source("rankModel.js")
    assert model.count("{ start: true }") == 1 and model.count("{ cancel: true }") == 1


# --- honest copy --------------------------------------------------------------------


def test_the_copy_says_likely_fits_first_and_never_best_matches_first(out: dict) -> None:
    assert out["notes"]["order"] == "likely fits first · likely no-matches last"
    assert out["notes"]["fit"] == {"strong": "likely fit", "maybe": "possible fit", "no": "likely no-match"}
    assert "does not say you match" in out["notes"]["honest"]
    grid = _source("components/JobsGrid.jsx")
    assert "{RANK_ORDER_NOTE}" in grid and 'data-role="rank-order-note"' in grid
    badge = _source("components/RankBadge.jsx")
    assert "RANK_HONEST_NOTE" in badge and "rankTooltip(rank)" in badge
    for path in UI_SRC.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8").lower()
            assert "best match" not in text, path
    # a rank is never a verdict: no rank word claims a match
    for words in out["notes"]["fit"].values():
        assert words != "match" and "matched" not in words


def test_a_card_and_the_job_page_show_the_models_reasons_and_blockers() -> None:
    card = _source("components/JobCard.jsx")
    assert "<RankBadge rank={job.rank}" in card and "rankBlockersLine" in card and "rankReasonsLine" in card
    assert 'data-role="rank-blockers"' in card
    page = _source("views/JobPage.jsx")
    assert "<RankBadge rank={job.rank} detail" in page


# --- Jev is gone from the UI ------------------------------------------------------------


def test_no_ui_source_mentions_jev() -> None:
    offenders = [str(path.relative_to(UI_SRC)) for path in UI_SRC.rglob("*") if path.is_file() and "jev" in path.read_text(encoding="utf-8").lower()]
    assert offenders == []
    for gone in ("jevModel.js", "components/JevBadge.jsx", "components/JevSettingsPanel.jsx"):
        assert not (UI_SRC / gone).exists(), gone


def test_settings_the_wizard_the_run_dialog_and_the_cards_have_no_jev_parts() -> None:
    settings = _source("views/SettingsView.jsx")
    assert "SettingsPanel" not in settings.replace("SourcesUpdatePanel", "")
    wizard = _source("wizard/wizardState.js")
    assert 'unset("exa")' in wizard and 'unset("jev")' not in wizard and "secrets add jev" not in wizard
    dialog = _source("components/RunConfirmDialog.jsx")
    assert "RunConfirmDialog({ config, onConfirm, onCancel, submitting, error, initialKeywords })" in dialog
    assert 'data-role="rank-run-note"' in dialog
    view = _source("views/FindJobsView.jsx")
    # no privacy notice about a hosted ranker, no day's-usage line, no "Score with" button
    assert "privacy-note" not in view and "usage" not in view.lower() and "Score with" not in view
    assert "/api/jev" not in _source("api.js")


# --- uat-bug-029 --------------------------------------------------------------------


def test_requirements_unreadable_is_the_servers_words_and_a_note(out: dict) -> None:
    assert out["notes"]["unreadable"] == quick_assess.POSTING_UNREADABLE_MESSAGE
    assert out["unreadable"] == {
        "status": 422,
        "code": "posting_requirements_unreadable",
        "message": "Couldn't read this posting's requirements",
        "isUnreadable": True,
    }
    assert out["unreadableOthers"] == [False, False]
    for relative, tag in (("views/JobPage.jsx", 'className="muted requirements-unreadable"'), ("views/AssessView.jsx", 'className="callout info"')):
        source = _source(relative)
        assert "isRequirementsUnreadable(err)" in source and "{REQUIREMENTS_UNREADABLE_TEXT}" in source, relative
        note = source[source.index('data-role="requirements-unreadable"') - 120 :][:200]
        assert tag in note and "danger" not in note, relative


# --- the shipped bundle -----------------------------------------------------------------


def test_the_bundle_ships_the_same_words() -> None:
    bundle = _dist_js()
    for words in (
        "likely fits first · likely no-matches last",
        "Couldn't read this posting's requirements",
        "Ranked by your model: ",
        "Re-rank",
        "posting_requirements_unreadable",
        "rank-counts",
    ):
        assert words in bundle, words
    assert "best matches first" not in bundle.lower()
    assert re.search(r"jev", bundle, re.IGNORECASE) is None
