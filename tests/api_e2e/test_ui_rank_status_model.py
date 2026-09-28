"""uat-bug-021: the run page's Jev sentence, run under node.

``ui/src/runText.js`` is pure JavaScript (no React), so this test runs
``rankStatusLine`` / ``rankSkipWords`` / ``rankUsageLine`` under the system ``node`` the way
``test_ui_uat_batch2_model.py`` does (no JS test runner) and asserts on the
JSON the script prints. LOUD skip when ``node`` is not on PATH. What lives
in JSX is checked statically, by reading the source.

What is pinned:

* the page's sentence is the server's, word for word: for every status the
  server can record, ``rankStatusLine(status.to_json())`` equals
  ``jev_rank.RankStatus.line`` (the line in the server log);
* so is the day's usage, from a ``rank_status`` and from the ``usage``
  block of ``POST /rank`` / ``GET /api/jev/usage`` alike (``rankUsageLine``
  itself; the live bar and the Settings panel use it -- jev-disclosure-fixes
  finding 1);
* no line for a run that has no ``rank_status`` (one sealed before it
  existed, or one whose ranking pass has not ended);
* ``GET /progress`` serves the field and ``NodeStatusList`` shows the
  status line, but jev-disclosure-fixes (finding 1) dropped ITS OWN usage
  line: the run's own snapshot went stale the moment another pass (a click,
  another run) spent after it, and the live bar already shows the current
  figure, so ``NodeStatusList`` no longer calls ``rankUsageLine`` at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs import jev_budget
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.jev_rank import RankStatus
from gigai.scout.find_jobs.progress import ProgressSnapshot

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
RUN_TEXT_JS = UI_SRC / "runText.js"

NODE_SCRIPT = """
import * as runText from {run_text_url};
const input = JSON.parse(process.argv[process.argv.length - 1]);
process.stdout.write(JSON.stringify({{
  lines: input.statuses.map((status) => runText.rankStatusLine(status)),
  words: input.statuses.map((status) => runText.rankSkipWords(status)),
  usage: input.statuses.map((status) => runText.rankUsageLine(status)),
  usageBlocks: input.usageBlocks.map((usage) => runText.rankUsageLine(usage)),
  nothing: input.nothing.map((status) => runText.rankStatusLine(status)),
  nothingWords: input.nothing.map((status) => runText.rankSkipWords(status)),
  nothingUsage: input.nothing.map((status) => runText.rankUsageLine(status)),
}}));
"""

STATUSES = [
    RankStatus("scored", 412, 1458, "cost_cap_reached", 0.25, 0.23, None, 0.31, 0.5),
    RankStatus("scored", 1458, 1458, None, 0.25, 0.699840, None, 0.699840, 1.0),
    RankStatus("scored", 1, 1, None, 0.25, 0.0005, None, 0.0005, 0.5),
    RankStatus("scored", 500, 500, None, 0.25, 0.0, None, 0.25344, 0.5),
    RankStatus("scored", 16, 20, "cost_cap_reached", 0.005, 0.00768, None, 0.00768, 0.5),
    RankStatus("scored", 3, 4, "jev_error:jev_http_502", 0.25, 0.00144, None, 0.00144, 0.5),
    RankStatus("scored", 24, 24, None, 0.25, 0.01152, "8 -> 4 after 429", 0.01152, 0.5),
    RankStatus("scored", 412, 1458, "daily_budget_reached", 0.25, 0.19, None, 0.5, 0.5),
    RankStatus("running", 40, 500, None, 0.25, 0.0, None, 0.1, 0.5),
    RankStatus("skipped", 0, 10, "daily_budget_reached", 0.25, 0.0, None, 0.5001, 0.5),
    RankStatus("skipped", 0, 40, "jev_error:jev_http_502", 0.25, 0.0, "8 -> 4 after 502", 0.0, 0.5),
    RankStatus("skipped", 0, 500, "not_requested", 0.25, 0.0, None, 0.0, 0.5),
    RankStatus("scored", 12, 500, "not_requested", 0.25, 0.0, None, 0.1, 0.5),
    RankStatus.skipped("cost_cap_reached", total=5, cost_cap_usd=0.0),
    RankStatus.skipped("no_candidates"),
    RankStatus.skipped("no_home", total=3),
    RankStatus.skipped("no_key", total=1458),
    RankStatus.skipped("no_profile", total=3),
    RankStatus.skipped("no_resume", total=1458),
    RankStatus.skipped("no_run_input", total=1458),
    RankStatus.skipped("no_run_output"),
    RankStatus.skipped("jev_error:jev_http_402", total=40),
    RankStatus.skipped("error:WorkpadConflictError", total=1458),
]
USAGE = [(0.31, 0.5), (0.0, 0.5), (0.0005, 0.5), (0.00768, 0.004), (1.0, 1.25), (12.345, 20.0)]
NOTHING = [None, {}, {"status": "waiting"}, "scored", 3]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the run page's Jev sentence was not run")
    script = NODE_SCRIPT.format(run_text_url=json.dumps(RUN_TEXT_JS.as_uri()))
    fixture = {
        "statuses": [status.to_json() for status in STATUSES],
        "usageBlocks": [
            {"spent_today_usd": f"{spent:.6f}", "daily_budget_usd": jev_budget.format_usd(budget)} for spent, budget in USAGE
        ],
        "nothing": NOTHING,
    }
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(fixture)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_page_says_what_the_server_logged_word_for_word(out: dict) -> None:
    assert out["lines"] == [status.line for status in STATUSES]
    assert out["lines"][:11] == [
        "Jev: scored 412 of 1,458 (cost $0.23, cost cap $0.25)",
        "Jev: scored 1,458 of 1,458 (cost $0.70)",
        "Jev: scored 1 of 1 (cost $0.0005)",
        "Jev: scored 500 of 500 (cost $0.00)",
        "Jev: scored 16 of 20 (cost $0.0077, cost cap $0.005)",
        "Jev: scored 3 of 4 (cost $0.0014, Jev error: jev_http_502)",
        "Jev: scored 24 of 24 (cost $0.01, throttled: 8 -> 4 after 429)",
        "Jev: scored 412 of 1,458 (cost $0.19, daily budget $0.50 reached)",
        "Jev: scoring, 40 of 500 so far",
        "Jev: skipped (daily budget $0.50 reached)",
        "Jev: skipped (Jev error: jev_http_502, throttled: 8 -> 4 after 502)",
    ]
    assert out["lines"][11:13] == ["Jev: skipped (not asked yet)", "Jev: scored 12 of 500 (cost $0.00, not asked yet)"]
    assert out["lines"][18] == "Jev: skipped (no resume)"


def test_the_reason_alone_is_there_for_a_card_with_no_score(out: dict) -> None:
    assert out["words"] == [status.reason_words for status in STATUSES]
    assert out["words"] == [
        "cost cap $0.25",
        "",
        "",
        "",
        "cost cap $0.005",
        "Jev error: jev_http_502",
        "",
        "daily budget $0.50 reached",
        "",
        "daily budget $0.50 reached",
        "Jev error: jev_http_502",
        "not asked yet",
        "not asked yet",
        "cost cap $0.00 reached before any score",
        "no new postings to score",
        "no GigAI home",
        "no Jev key",
        "no profile selected",
        "no resume",
        "the run's input could not be read",
        "the run's postings could not be read",
        "Jev error: jev_http_402",
        "error: WorkpadConflictError",
    ]


def test_the_days_usage_is_the_servers_word_for_word(out: dict) -> None:
    assert out["usage"] == [status.usage_line for status in STATUSES]
    assert out["usage"][0] == "Jev: $0.31 of $0.50 today"
    # A status recorded with no home to read the day's spend from has no usage line.
    assert out["usage"][-1] is None
    assert out["usageBlocks"] == [jev_budget.usage_line(spent, budget) for spent, budget in USAGE]
    assert out["usageBlocks"] == [
        "Jev: $0.31 of $0.50 today",
        "Jev: $0.00 of $0.50 today",
        "Jev: $0.0005 of $0.50 today",
        "Jev: $0.0077 of $0.004 today",
        "Jev: $1.00 of $1.25 today",
        "Jev: $12.35 of $20.00 today",
    ]


def test_a_run_with_no_rank_status_has_no_line(out: dict) -> None:
    assert out["nothing"] == [None] * len(NOTHING)
    assert out["nothingWords"] == [""] * len(NOTHING)
    assert out["nothingUsage"] == [None] * len(NOTHING)


def test_the_progress_route_serves_it_and_the_status_panel_shows_it() -> None:
    assert "rank_status" in ProgressSnapshot.__dataclass_fields__
    server = (Path(static_module.__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    assert '"rank_status": None if snapshot.rank_status is None else dict(snapshot.rank_status),' in server
    status_list = (UI_SRC / "components" / "NodeStatusList.jsx").read_text(encoding="utf-8")
    assert "rankStatusLine(rankStatus)" in status_list and 'data-role="rank-status"' in status_list
    # jev-disclosure-fixes (finding 1): the status panel's OWN usage line is
    # gone; only the live bar (FindJobsView.jsx) and the Settings panel call
    # rankUsageLine now.
    assert "rankUsageLine" not in status_list and 'data-role="jev-usage"' not in status_list
