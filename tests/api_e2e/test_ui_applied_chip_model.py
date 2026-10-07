"""0.1.11.5 packet AP: the Jobs page's "Applied" chip says how many jobs you applied to, the model run under node.

``ui/src/postingsModel.js`` is plain JavaScript: this runs it under the system ``node``. LOUD skip without ``node``.
What the server puts in ``counts.applied`` and what it leaves out of the list is pinned in
tests/behaviors/scout_pipeline/test_applied_left_out.py; the real page in tests/ui/test_jobs_applied_badge.py.

Pinned: the chip carries the server's number ("Applied 7") like the weak-fit chip, and no number when there is none
(or before the first read); it stays a generic state filter (the address carries it); "Assess all" under the chip
names the page's rows, since a filter never selects a job with an application.
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

const chip = (states, counts) => m.stateChips(states, counts).find((item) => item.value === "applied");
const out = {};
out.seven = chip([], { applied: 7, weak_fit: 2 });
out.on = chip(["applied"], { applied: 7, weak_fit: 2 });
out.none = [chip([], { applied: 0, weak_fit: 2 }).count, chip([], {}).count, chip([], null).count];
out.counts = [m.appliedCount({ applied: 7 }), m.appliedCount({ applied: 0 }), m.appliedCount({ applied: "7" }), m.appliedCount(null)];
out.weak = m.stateChips([], { applied: 7, weak_fit: 2 }).find((item) => item.value === "weak_fit").count;
out.others = m.stateChips([], { applied: 7, weak_fit: 2 }).filter((item) => !["applied", "weak_fit"].includes(item.value)).map((item) => item.count);
out.query = m.postingsQuery({ ...m.EMPTY_FILTER, states: m.toggleState([], "applied") });
out.hash = m.jobsHash({ ...m.EMPTY_FILTER, states: ["applied"] }, 1);
const rows = [{ job_identity: "https://jobs.example/a", state: "not_assessed" }, { job_identity: "https://jobs.example/b", state: "matched" }];
out.assessAll = [
  m.assessAllBody({ filter: m.EMPTY_FILTER, rows }),
  m.assessAllBody({ filter: { ...m.EMPTY_FILTER, states: ["applied"] }, rows }),
];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the applied-chip model was NOT run")
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri()))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_applied_chip_carries_the_servers_count_and_none_when_there_is_none(out: dict) -> None:
    assert (out["seven"]["label"], out["seven"]["active"], out["seven"]["count"]) == ("Applied", False, 7)
    assert (out["on"]["active"], out["on"]["count"]) == (True, 7), "the number stays while the chip is on"
    assert out["none"] == [None, None, None], "no application (or nothing read yet): the chip, no number"
    assert out["counts"] == [7, None, None, None]
    assert out["weak"] == 2 and out["others"] == [None, None, None], "the weak-fit chip keeps its own number; no other chip has one"
    assert "stay out of the list unless this is on" in out["seven"]["title"]


def test_the_chip_is_a_state_filter_and_assess_all_under_it_names_the_rows(out: dict) -> None:
    assert out["query"] == "state=applied&limit=50" and out["hash"] == "#/jobs?state=applied"
    assert out["assessAll"][0] == {"states": ["not_assessed"]}, "the default list: the filter (the server leaves the applied ones out)"
    assert out["assessAll"][1] == {"jobs": ["https://jobs.example/a"]}, "under the chip: the page's not-assessed rows, named"


def test_the_jobs_page_draws_the_chip_count_from_the_model() -> None:
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert "stateChips(filter.states, counts)" in view and 'data-role="chip-count"' in view
