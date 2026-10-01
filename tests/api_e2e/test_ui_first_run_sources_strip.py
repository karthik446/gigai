"""uat-bug-048: the Jobs page tells a new user to Update sources first.

``ui/src/sourcesStripModel.js`` is pure JavaScript, so it runs under the
system ``node`` (LOUD skip without it); what lives in JSX is pinned by
reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
NOW = "2026-09-29T12:00:00Z"

SCRIPT = """
import * as m from URL;
const now = new Date(NOW).getTime();
const status = (index, update) => ({ running: Boolean(update && update.status === "running"), update: update || null, index });
const out = {
  empty: m.sourcesStrip(status({ status: "empty", companies_indexed: 0, last_checked_at: null }), { now }),
  emptyKnown: m.sourcesStrip(status({ status: "empty", companies_indexed: 0 }, { status: "failed", boards: { total: 10370 } }), { now }),
  fresh: m.sourcesStrip(status({ status: "ready", companies_indexed: 10360, last_checked_at: "2026-09-29T09:00:00Z" }), { now }),
  stale: m.sourcesStrip(status({ status: "stale", companies_indexed: 10360, last_checked_at: "2026-09-27T09:00:00Z" }), { now }),
  updating: m.sourcesStrip(status({ status: "ready", companies_indexed: 10360, last_checked_at: "2026-09-29T09:00:00Z" }, { status: "running", boards: { total: 200, checked: 50 } }), { now }),
  emptyUpdating: m.sourcesStrip(status({ status: "empty", companies_indexed: 0 }, { status: "running", boards: { total: 0 } }), { now }),
  unknown: m.sourcesStrip(null, { now }),
  noRunEmpty: m.noRunText(m.sourcesStrip(status({ status: "empty", companies_indexed: 0 }), { now })),
  noRunFresh: m.noRunText(m.sourcesStrip(status({ status: "ready", companies_indexed: 5, last_checked_at: "2026-09-29T11:00:00Z" }), { now })),
  stepsEmpty: m.firstRunSteps(status({ status: "empty", companies_indexed: 0 }), { hasRun: false }),
  stepsUpdated: m.firstRunSteps(status({ status: "ready", companies_indexed: 12 }), { hasRun: false, stored: "12 companies stored" }),
  stepsBoth: m.firstRunSteps(status({ status: "ready", companies_indexed: 12 }), { hasRun: true }),
  freshNoRun: m.sourcesStrip(status({ status: "ready", companies_indexed: 12, last_checked_at: "2026-09-29T11:00:00Z" }), { now, hasRun: false }),
  noRunFreshNoRun: m.noRunText(m.sourcesStrip(status({ status: "ready", companies_indexed: 12, last_checked_at: "2026-09-29T11:00:00Z" }), { now, hasRun: false })),
  landEmptyFromSettings: m.finishLanding(status({ status: "empty", companies_indexed: 0 }), { fromSettings: true }),
  landFullFromSettings: m.finishLanding(status({ status: "ready", companies_indexed: 9 }), { fromSettings: true }),
  landFullFirstRun: m.finishLanding(status({ status: "ready", companies_indexed: 9 }), { fromSettings: false }),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the sources strip model was NOT run")
    script = SCRIPT.replace("URL", json.dumps((UI_SRC / "sourcesStripModel.js").resolve().as_uri())).replace("NOW", json.dumps(NOW))
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_empty_store_shows_numbered_steps_and_blocks_run(out: dict) -> None:
    empty = out["empty"]
    assert empty["kind"] == "empty"
    first, second = empty["steps"]
    assert (first["title"], first["state"], first["action"]) == ("Update sources", "current", "update-sources")
    assert first["description"].startswith("Downloads postings from")
    assert "can take 15 minutes or more the first time" in first["description"]
    assert (second["title"], second["state"], second["action"]) == ("Run find jobs", "todo", None)
    assert empty["runBlocked"] == "Update sources first, then run."


def test_board_count_comes_from_the_server_never_a_constant(out: dict) -> None:
    assert "~10,370 company boards" in out["emptyKnown"]["steps"][0]["description"]
    assert "~" not in out["empty"]["steps"][0]["description"], "no count when the server did not say one"
    source = (UI_SRC / "sourcesStripModel.js").read_text(encoding="utf-8")
    assert "10370" not in source.replace("10,370 boards", "") and "10,360" not in source.split("export function")[1]


def test_fresh_store_shows_count_and_age_and_runs(out: dict) -> None:
    fresh = out["fresh"]
    assert fresh["line"] == "Company postings: 10,360 companies stored · updated 3 hours ago · up to date"
    assert fresh["amber"] is False and fresh["steps"] is None and fresh["runBlocked"] == ""


def test_stale_store_is_amber_out_of_date_and_still_runs(out: dict) -> None:
    stale = out["stale"]
    assert stale["amber"] is True and stale["line"].endswith("· out of date")
    assert "2 days ago" in stale["line"] and stale["runBlocked"] == ""


def test_running_update_shows_inline_progress(out: dict) -> None:
    assert out["updating"]["running"] is True
    assert out["updating"]["progress"]["percent"] == 25
    assert out["emptyUpdating"]["progress"]["determinate"] is False
    assert "Updating sources" in out["emptyUpdating"]["runBlocked"]


def test_unreadable_status_shows_no_strip(out: dict) -> None:
    assert out["unknown"]["kind"] == "unknown" and out["unknown"]["runBlocked"] == ""


def test_empty_state_is_short_while_the_steps_show(out: dict) -> None:
    assert out["noRunEmpty"] == "No runs yet." and out["noRunFreshNoRun"] == "No runs yet."
    assert "Update sources" not in out["noRunFresh"] and "Run one above" in out["noRunFresh"]


def test_stepper_states(out: dict) -> None:
    states = lambda key: [step["state"] for step in out[key]]  # noqa: E731
    assert states("stepsEmpty") == ["current", "todo"]
    assert states("stepsUpdated") == ["done", "current"]
    assert out["stepsUpdated"][0]["description"] == "12 companies stored on this machine."
    assert states("stepsBoth") == ["done", "done"]
    assert out["freshNoRun"]["steps"] is not None and out["fresh"]["steps"] is None


def test_disabled_reason_is_shown_once() -> None:
    strip = (UI_SRC / "components" / "SourcesStrip.jsx").read_text(encoding="utf-8")
    jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert strip.count("strip.runBlocked") == 2  # step 2's line only (condition + text)
    assert "strip.runBlocked && !strip.steps" in jobs, "the header line shows only when no stepper does"


def test_wizard_finish_lands_on_jobs_with_step_1_only_when_empty(out: dict) -> None:
    assert out["landEmptyFromSettings"] == {"toJobs": True, "highlightUpdateStep": True}
    assert out["landFullFromSettings"] == {"toJobs": False, "highlightUpdateStep": False}
    assert out["landFullFirstRun"] == {"toJobs": True, "highlightUpdateStep": False}


def test_jsx_wiring() -> None:
    jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert "<SourcesStrip" in jobs and "strip.runBlocked" in jobs and "noRunText(strip)" in jobs
    strip = (UI_SRC / "components" / "SourcesStrip.jsx").read_text(encoding="utf-8")
    assert 'data-highlight={step.state === "current"' in strip and "startSourcesUpdate" in strip
    app = (UI_SRC / "App.jsx").read_text(encoding="utf-8")
    assert "finishLanding(" in app
