"""0.1.11.2 RANKUI: the Jobs page's ranking panel labels, the model run under node.

``ui/src/rankNowModel.js`` is plain JavaScript: this runs it under the system
``node`` over what the SERVER makes (``rank_now.status``: the read and the
asks, no model call) on a synthetic home with unranked postings. LOUD skip
without ``node``. What lives in JSX is pinned by reading the source.

Pinned: the one status line ("Ranked 0 of 3 (last 7 days) · 3 not ranked
yet"), "ranked X of Y" while a job runs, the buttons (a greyed one says why in
its own label: "nothing to rank", "ranking is off", and the line says how to
turn it on, in the server's own words), the re-rank dialog's cost before it
runs and its refusal past the daily cap, and what a finished job says.
RANKVIS: the row is ALWAYS drawn; with no ranking block the line says
"Ranking status unavailable" and the buttons stay (never nothing).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.pipeline import rank_now, triggers

from tests.support.posting_fixtures import TITLE_SECOND_ONLY, build_postings_fixture, lever_job

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
import * as m from MODEL_URL;

const data = DATA;
const out = {};
const ranking = data.read.ranking;
out.howTo = m.HOW_TO_ENABLE;
out.isAnswer = [m.isRankAnswer(data.read), m.isRankAnswer({ schema_version: "scout-postings:1" }), m.isRankAnswer(null)];
out.totals = m.rankTotals(ranking);
out.line = m.rankStatusLine(ranking, null);
out.buttons = m.rankButtons(ranking, {});
out.busy = m.rankButtons(ranking, { busy: true });
const job = { mode: "unranked", state: "running", calls: 0, ranked: 0 };
const half = { enabled: true, in_progress: true, window_days: 7, by_profile: [{ profile_id: "a", ranked: 50, total: 60 }, { profile_id: "b", ranked: 0, total: 0 }] };
out.running = { line: m.rankStatusLine(half, job), buttons: m.rankButtons(half, { job }), latest: m.rankStatusLine(half, { ...job, mode: "latest" }), latestButtons: m.rankButtons(half, { job: { ...job, mode: "latest" } }) };
const all = { enabled: true, in_progress: false, window_days: 7, by_profile: [{ profile_id: "a", ranked: 57, total: 57 }] };
out.all = { line: m.rankStatusLine(all, null), buttons: m.rankButtons(all, {}) };
out.one = m.rankStatusLine({ enabled: true, window_days: 7, by_profile: [{ profile_id: "a", ranked: 4, total: 5 }] }, null);
out.none = { line: m.rankStatusLine({ enabled: true, window_days: 7, by_profile: [] }, null), buttons: m.rankButtons({ enabled: true, window_days: 7, by_profile: [] }, {}) };
out.missing = [m.rankStatusLine(null, null), m.rankButtons(undefined, {}), m.rankTotals({}), m.rankStatusLine(undefined, null, null, { loading: true }), m.rankButtons(null, { busy: true })];
out.off = { line: m.rankStatusLine(data.off.ranking, null, data.off.how_to_enable), buttons: m.rankButtons(data.off.ranking, { howToEnable: data.off.how_to_enable }), fallback: m.rankStatusLine(data.off.ranking, null) };
out.dialog = m.rerankDialog(data.ask);
out.capped = m.rerankDialog(data.capped);
out.notDialog = [m.rerankDialog(data.read), m.rerankDialog(data.askUnranked), m.rerankDialog(null)];
out.refusal = [m.rankRefusalLine(data.capped), m.rankRefusalLine(data.ask), m.rankRefusalLine({ schema_version: m.RANK_SCHEMA, plan: { refusal: "nothing_to_rank" } })];
const done = (extra) => m.rankOutcomeLine({ mode: "unranked", state: "done", calls: 2, ranked: 57, outcome: "ran", reason: null, ...extra });
out.outcome = [
  done({}), done({ mode: "latest", ranked: 100 }), done({ outcome: "waiting", reason: "daily_cap_reached", calls: 1, ranked: 50 }),
  done({ outcome: "yielded", reason: "sources_update", calls: 0, ranked: 0 }), done({ outcome: "busy_elsewhere", calls: 0, ranked: 0 }),
  done({ outcome: "idle", calls: 0, ranked: 0 }), done({ outcome: "unavailable", reason: "model_target_unavailable", calls: 0, ranked: 0 }),
  done({ calls: 1, ranked: 1 }), m.rankOutcomeLine(job), m.rankOutcomeLine(null),
];
console.log(JSON.stringify(out));
"""


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the rank-now model was NOT run")
    assert node is not None
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    now = datetime.now(UTC)
    fx.seed(
        "rn", [lever_job("rn", n, title=TITLE_SECOND_ONLY, text=f"Posting {n}: Python services, variant {n}.", created=now - timedelta(hours=n)) for n in (1, 2, 3)],
        seen_at=now - timedelta(minutes=30),
    )
    calls = fx.base.model.calls
    data: dict[str, object] = {
        "read": rank_now.status(fx.home_root, fx.target),
        "ask": rank_now.status(fx.home_root, fx.target, mode="latest"),
        "askUnranked": rank_now.status(fx.home_root, fx.target, mode="unranked"),
    }
    triggers.spend_rank_calls(fx.home_root, fx.target, 100)
    data["capped"] = rank_now.status(fx.home_root, fx.target, mode="latest")
    path = settings_path(fx.home_root, fx.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "rank": {"enabled": False}}), encoding="utf-8")
    data["off"] = rank_now.status(fx.home_root, fx.target, mode="latest")
    assert fx.base.model.calls == calls, "reading and asking called a model"
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "rankNowModel.js").resolve().as_uri()))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script.replace("DATA", json.dumps(data))], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    result = json.loads(completed.stdout)
    result["data"] = data
    return result


def test_a_user_whose_postings_are_not_ranked_sees_the_count_and_can_rank_now(out: dict) -> None:
    assert out["isAnswer"] == [True, False, False]
    assert out["totals"] == {"enabled": True, "ranked": 0, "total": 3, "unranked": 3, "windowDays": 7}
    assert out["line"] == "Ranked 0 of 3 (last 7 days) · 3 not ranked yet"
    assert out["one"] == "Ranked 4 of 5 (last 7 days) · 1 not ranked yet"
    now, rerank = out["buttons"]["rankNow"], out["buttons"]["rerank"]
    assert (now["label"], now["disabled"]) == ("Rank now", False) and "3 postings not ranked yet" in now["title"]
    assert (rerank["label"], rerank["disabled"]) == ("Re-rank latest 100", False) and "Asks first" in rerank["title"]
    assert [out["busy"][name]["disabled"] for name in ("rankNow", "rerank")] == [True, True]  # a request is on its way
    # RANKVIS: no ranking block (an older server, a list that could not be read): the row says so, never nothing.
    line, buttons, totals, reading, busy = out["missing"]
    assert (line, totals, reading) == ("Ranking status unavailable.", None, "Reading the ranking status…")
    assert [(buttons[name]["label"], buttons[name]["disabled"]) for name in ("rankNow", "rerank")] == [("Rank now", False), ("Re-rank latest 100", False)]
    assert all(buttons[name]["title"].startswith("Ranking status unavailable. ") for name in ("rankNow", "rerank"))
    assert [busy[name]["disabled"] for name in ("rankNow", "rerank")] == [True, True]


def test_ranked_x_of_y_while_a_job_runs_and_what_it_says_when_all_are_ranked(out: dict) -> None:
    running = out["running"]
    assert running["line"] == "Ranking now: ranked 50 of 60 (last 7 days)"
    assert (running["buttons"]["rankNow"]["label"], running["buttons"]["rankNow"]["disabled"], running["buttons"]["rerank"]["disabled"]) == ("Ranking…", True, True)
    assert running["latest"] == "Re-ranking the latest 100: ranked 50 of 60 (last 7 days)"
    assert (running["latestButtons"]["rerank"]["label"], running["latestButtons"]["rankNow"]["label"]) == ("Re-ranking…", "Rank now")
    assert out["all"]["line"] == "Ranked 57 of 57 (last 7 days)"
    # Nothing unranked: "Rank now" is greyed and says why in its label and its title; a re-rank is still offered.
    assert out["all"]["buttons"]["rankNow"] == {"label": "Rank now: nothing to rank", "disabled": True, "title": "Nothing to rank: every posting of the last 7 days is ranked."}
    assert (out["all"]["buttons"]["rerank"]["label"], out["all"]["buttons"]["rerank"]["disabled"]) == ("Re-rank latest 100", False)
    assert out["none"]["line"] == "Ranked 0 of 0 (last 7 days)"
    none = out["none"]["buttons"]
    assert [(none[name]["label"], none[name]["disabled"], none[name]["title"]) for name in ("rankNow", "rerank")] == [
        ("Rank now: nothing to rank", True, "Nothing to rank: no posting of the last 7 days."),
        ("Re-rank latest 100: nothing to rank", True, "Nothing to rank: no posting of the last 7 days."),
    ]


def test_with_ranking_off_the_buttons_say_so_and_the_line_says_how_to_turn_it_on(out: dict) -> None:
    assert out["howTo"] == rank_now.HOW_TO_ENABLE  # the page's fallback is the server's own sentence
    off = out["off"]
    assert off["line"] == f"Ranked 0 of 3 (last 7 days) · {rank_now.HOW_TO_ENABLE}" == off["fallback"]
    assert off["buttons"]["rankNow"] == {"label": "Rank now: ranking is off", "disabled": True, "title": rank_now.HOW_TO_ENABLE}
    assert off["buttons"]["rerank"] == {"label": "Re-rank latest 100: ranking is off", "disabled": True, "title": rank_now.HOW_TO_ENABLE}


def test_the_re_rank_dialog_shows_the_cost_before_it_runs_and_refuses_past_the_daily_cap(out: dict) -> None:
    dialog = out["dialog"]
    assert dialog["title"] == "Re-rank the latest 3 postings?"
    assert (dialog["postings"], dialog["calls"], dialog["allowed"], dialog["refusal"]) == (3, 1, True, None)
    assert dialog["costLine"] == "1 model call (up to 50 postings a call, at most 2 calls)"
    assert dialog["todayLine"] == "0 of 100 rank calls used today; 100 left"
    assert dialog["approveBody"] == {"mode": "latest", "approve": True}
    capped = out["capped"]
    assert (capped["allowed"], capped["approveBody"]) == (False, None)  # Approve is off: nothing can be sent
    assert capped["refusal"] == "Today's rank calls do not cover this: it needs 1, 0 of 100 are left. The count starts again tomorrow."
    assert capped["todayLine"] == "100 of 100 rank calls used today; 0 left"
    assert out["notDialog"] == [None, None, None]
    assert out["refusal"] == [capped["refusal"], None, "No posting of the window to rank."]


def test_what_a_finished_job_says(out: dict) -> None:
    assert out["outcome"] == [
        "Ranked 57 postings in 2 calls.",
        "Re-ranked 100 postings in 2 calls.",
        "Ranked 50 postings in 1 call. Today's rank calls are used up; ranking goes on tomorrow.",
        "Ranking is waiting: sources update is running. Click again when that is done.",
        "Ranking is already running in the background; the list updates as it goes.",
        "Nothing to rank.",
        "Ranking stopped (model target unavailable).",
        "Ranked 1 posting in 1 call.",
        None,
        None,
    ]


def test_the_page_draws_the_panel_the_buttons_and_the_cost_dialog() -> None:
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert "<RankPanel ranking={response && response.ranking}" in view and 'data-testid="ranking-line"' in view
    assert "&& <RankPanel" not in view  # RANKVIS: the row is always drawn, under no condition of the page
    panel = (UI_SRC / "components" / "RankPanel.jsx").read_text(encoding="utf-8")
    for needle in (
        'data-testid="rank-panel"', 'data-testid="rank-status-line"', 'data-testid="rank-now"', 'data-testid="rerank-latest"',
        'data-testid="rank-notice"', 'postPostingsRank({ mode: "unranked", approve: true })', 'postPostingsRank({ mode: "latest" })',
        "postPostingsRank(dialog.approveBody)", "refresh.current()",
    ):
        assert needle in panel, needle
    # No model call without a click: the only approvals are in the two click handlers; nothing is posted on load.
    assert panel.count("approve: true") == 1 and "useEffect(() => {\n    if (!running)" in panel
    # RANKVIS: no early return hides the row (the one `return null` is `take`, for an answer of another route).
    assert panel.count("return null") == 1 and "return null; // not this route's answer" in panel and "!line || !buttons" not in panel
    dialog = (UI_SRC / "components" / "RerankApprovalDialog.jsx").read_text(encoding="utf-8")
    for needle in ('data-testid="rerank-dialog"', 'data-role="rerank-cost"', 'data-role="rerank-refusal"', 'data-action="rerank-approve"', "!dialog.approveBody"):
        assert needle in dialog, needle
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert 'request("POST", "/api/postings/rank", body || {})' in api
