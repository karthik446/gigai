"""0.1.11.3 P10: the rule of the Jobs page's rank watch, the model run under node.

``ui/src/rankNowModel.js`` (``shouldWatchRank``, ``watchStep``, ``rankSignature``, ``rankStatusLine``) is plain JavaScript:
this runs it under the system ``node``. LOUD skip without ``node``.

Pinned: the page reads the ranking only while the block says the rank is in progress and ranking is on, the tab is
visible and the page has no job of its own (that one has its own faster read); it stops after RANK_WATCH_QUIET reads that
moved nothing (a lane stuck on the day's cap is not watched for ever); a read that moved the count, or flipped the rank
to done, says "read the list again", a read that did not, does not; the line says "Ranking… X of Y" only while watched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
import * as m from MODEL_URL;
const block = (ranked, total, extra = {}) => ({ enabled: true, in_progress: ranked < total, window_days: 7, by_profile: [{ profile_id: "a", ranked, total }], ...extra });
const out = {};
out.constants = [m.RANK_WATCH_MS, m.RANK_WATCH_QUIET];
const going = block(2, 5);
out.watch = {
  going: m.shouldWatchRank(going),
  done: m.shouldWatchRank(block(5, 5)),
  off: m.shouldWatchRank(block(2, 5, { enabled: false })),
  hidden: m.shouldWatchRank(going, { hidden: true }),
  noBlock: [m.shouldWatchRank(null), m.shouldWatchRank(undefined), m.shouldWatchRank({})],
  ownJob: m.shouldWatchRank(going, { job: { mode: "unranked", state: "running" } }),
  ownJobDone: m.shouldWatchRank(going, { job: { mode: "unranked", state: "done" } }),
  quietBelow: m.shouldWatchRank(going, { quiet: m.RANK_WATCH_QUIET - 1 }),
  quietAt: m.shouldWatchRank(going, { quiet: m.RANK_WATCH_QUIET }),
};
// A run of reads: the count moves twice, stands still, then the rank ends.
let seen = m.rankSignature(block(0, 5)), quiet = 0;
out.steps = [block(0, 5), block(2, 5), block(2, 5), block(5, 5), block(5, 5)].map((read) => {
  const step = m.watchStep(seen, read, quiet);
  seen = step.signature;
  quiet = step.quiet;
  return { changed: step.changed, quiet: step.quiet };
});
out.badRead = m.watchStep("x", null, 3);
out.signature = [m.rankSignature(null), m.rankSignature({}), m.rankSignature(block(2, 5)), m.rankSignature(block(2, 5, { in_progress: false }))];
out.lines = {
  watched: m.rankStatusLine(block(2, 5), null, null, { watching: true }),
  notWatched: m.rankStatusLine(block(2, 5), null),
  doneWatched: m.rankStatusLine(block(5, 5), null, null, { watching: true }),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the rank watch model was NOT run")
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "rankNowModel.js").resolve().as_uri()))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_page_watches_only_a_rank_in_progress_on_a_visible_tab_with_no_job_of_its_own(out: dict) -> None:
    assert out["constants"] == [4000, 30]  # a few seconds apart, and a bounded number of quiet reads
    watch = out["watch"]
    assert watch["going"] is True and watch["ownJobDone"] is True and watch["quietBelow"] is True
    assert [watch[name] for name in ("done", "off", "hidden", "ownJob", "quietAt")] == [False] * 5
    assert watch["noBlock"] == [False, False, False]


def test_a_read_that_moved_the_count_or_ended_the_rank_reads_the_list_again_and_one_that_did_not_does_not(out: dict) -> None:
    assert out["steps"] == [
        {"changed": False, "quiet": 1},  # the same as the list's own block
        {"changed": True, "quiet": 0},  # 2 of 5: the list is read again
        {"changed": False, "quiet": 1},
        {"changed": True, "quiet": 0},  # 5 of 5, the rank ended: the list is read again
        {"changed": False, "quiet": 1},
    ]
    assert out["badRead"] == {"signature": "x", "changed": False, "quiet": 4}  # an answer with no block moves nothing
    none, empty, going, idle = out["signature"]
    assert none is None and empty is None and going != idle


def test_the_line_says_ranking_x_of_y_only_while_it_is_watched(out: dict) -> None:
    lines = out["lines"]
    assert lines["watched"] == "Ranking… 2 of 5 (last 7 days)"
    assert lines["notWatched"] == "Ranked 2 of 5 (last 7 days) · 3 not ranked yet"
    assert lines["doneWatched"] == "Ranked 5 of 5 (last 7 days)"
