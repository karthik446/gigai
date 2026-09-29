"""uat-bug-028: the card says why a posting passed the work mode + area; the wizard saves all four modes.

``ui/src/jobModel.js``, ``boardRows.js`` and ``wizard/wizardState.js`` are
plain JavaScript, so this runs them under the system ``node`` (no JS test
runner) and asserts on the JSON it prints. LOUD skip when ``node`` is not
on PATH. The API half (``rows[].work_mode_fit``, the save) is pinned in
``tests/behaviors/scout_find_jobs/test_work_mode_filter.py``.

Pinned:

* ``whyPassedLine``: "Remote (from location text)", "Hybrid · Denver",
  "Hybrid or on-site (not stated) · Denver", "Work mode not stated",
  "· area not stated"; nothing under Any or without a fit;
* ``workModeChip``: the board's field first; else a mode read from the
  location text, marked derived (the Reddit "Remote - United States"
  Greenhouse posting shows Remote); none for a plain city or unknown;
* ``rowsFromResults`` -> ``buildJobs`` carries ``work_mode_fit`` to
  ``job.workModeFit``;
* the wizard: every mode survives ``setupBody`` as itself, the city goes
  only with Hybrid/Onsite, a stored placeholder city starts empty, and the
  saved mode comes from prefs, else ``work_mode``, else Any (an old
  ``remote`` flag alone never picks Remote-only);
* JobCard renders both (read as source).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const model = await import(JOB_MODEL_URL);
const rows = await import(BOARD_ROWS_URL);
const wizard = await import(WIZARD_STATE_URL);
const fit = (mode, source, preference, area, in_area) => ({ mode, source, preference, area, in_area, passes: true });
const reddit = { normalized_url: "https://boards.greenhouse.io/reddit/jobs/7243312", title: "Senior Software Engineer, Home Experience", location: "Remote - United States" };
const g2i = { normalized_url: "https://jobs.ashbyhq.com/g2i/1", title: "Engineer", location: "United States", work_mode: "remote" };
const roku = { normalized_url: "https://boards.greenhouse.io/roku/jobs/1", title: "Engineer", location: "San Jose, California" };
const payload = {
  rows: [
    { posting: reddit, work_mode_fit: fit("remote", "derived", "remote", null, null) },
    { posting: g2i, work_mode_fit: fit("remote", "board", "remote", null, null) },
    { posting: roku },
  ],
  assessments: [],
  not_assessed: [],
};
const jobs = model.buildJobs({ rows: rows.rowsFromResults(payload), rankScores: [], quickItems: [], runCreatedAt: null });
const fields = (workMode, city) => ({ ...wizard.initialFields({ prefs: null, config: null, selectedProfile: null, resumes: [] }), titles: ["x"], workMode, city });
process.stdout.write(JSON.stringify({
  lines: {
    derivedRemote: model.whyPassedLine(fit("remote", "derived", "remote", null, null)),
    boardRemote: model.whyPassedLine(fit("remote", "board", "remote", null, null)),
    hybridDenver: model.whyPassedLine(fit("hybrid", "board", "hybrid", "Denver", true)),
    hybridDenverDerived: model.whyPassedLine(fit("hybrid", "derived", "hybrid", "Denver", true)),
    plainCity: model.whyPassedLine(fit("in_person", "derived", "onsite", "Denver", true)),
    noPlace: model.whyPassedLine(fit("hybrid", "derived", "hybrid", "Denver", null)),
    unknown: model.whyPassedLine(fit("unknown", "none", "remote", null, null)),
    any: model.whyPassedLine(fit("remote", "derived", "any", null, null)),
    none: model.whyPassedLine(null),
  },
  chips: {
    reddit: model.workModeChip(jobs[0]),
    g2i: model.workModeChip(jobs[1]),
    roku: model.workModeChip(jobs[2]),
    plainCity: model.workModeChip({ posting: roku, workModeFit: fit("in_person", "derived", "remote", null, null) }),
  },
  carried: jobs.map((job) => job.workModeFit),
  bodies: ["remote", "hybrid", "onsite", "any"].map((mode) => wizard.setupBody(fields(mode, " Denver, CO "), null)),
  placeholderCity: wizard.initialFields({ prefs: { city: "REPLACE_WITH_YOUR_LOCATION (e.g. Denver, CO, or null for any)", work_mode: "hybrid" }, config: null, selectedProfile: null, resumes: [] }).city,
  realCity: wizard.initialFields({ prefs: { city: "Denver, CO", work_mode: "onsite" }, config: null, selectedProfile: null, resumes: [] }).city,
  modes: {
    prefs: wizard.initialWorkMode({ work_mode: "onsite" }, { config: { work_mode: "hybrid", remote: true } }),
    config: wizard.initialWorkMode(null, { config: { work_mode: "hybrid", remote: true } }),
    oldRemote: wizard.initialWorkMode(null, { config: { remote: true } }),
    oldAny: wizard.initialWorkMode(null, { config: { remote: false } }),
    nothing: wizard.initialWorkMode(null, null),
  },
  review: wizard.reviewRows(fields("hybrid", "Denver, CO"), []).find((row) => row[0] === "Work mode")[1],
  hints: wizard.WORK_MODES.map((mode) => [mode.value, mode.hint]),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the UI model test cannot run")
    script = (
        NODE_SCRIPT.replace("JOB_MODEL_URL", json.dumps((UI_SRC / "jobModel.js").as_uri()))
        .replace("BOARD_ROWS_URL", json.dumps((UI_SRC / "boardRows.js").as_uri()))
        .replace("WIZARD_STATE_URL", json.dumps((UI_SRC / "wizard" / "wizardState.js").as_uri()))
    )
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_card_says_why_a_posting_passed(out: dict) -> None:
    assert out["lines"] == {
        "derivedRemote": "Remote (from location text)",
        "boardRemote": "Remote",
        "hybridDenver": "Hybrid · Denver",
        "hybridDenverDerived": "Hybrid · Denver (from location text)",
        "plainCity": "Hybrid or on-site (not stated) · Denver",
        "noPlace": "Hybrid · area not stated (from location text)",
        "unknown": "Work mode not stated",
        "any": None,
        "none": None,
    }


def test_the_reddit_remote_us_posting_shows_a_derived_remote_chip(out: dict) -> None:
    assert out["chips"]["reddit"]["label"] == "Remote" and out["chips"]["reddit"]["derived"] is True
    assert out["chips"]["g2i"] == {"label": "Remote", "derived": False, "title": "Stated by the job board"}
    assert out["chips"]["roku"] is None
    assert out["chips"]["plainCity"] is None


def test_the_fit_is_carried_from_the_results_row_to_the_job(out: dict) -> None:
    assert [item and item["source"] for item in out["carried"]] == ["derived", "board", None]


def test_the_wizard_saves_every_mode_as_itself(out: dict) -> None:
    assert [(body["work_mode"], body["city"]) for body in out["bodies"]] == [
        ("remote", None),
        ("hybrid", "Denver, CO"),
        ("onsite", "Denver, CO"),
        ("any", None),
    ]
    assert out["review"] == "Hybrid · Denver, CO"
    assert all(hint for _mode, hint in out["hints"])


def test_the_wizard_starts_from_the_saved_mode_and_never_the_placeholder(out: dict) -> None:
    assert out["placeholderCity"] == ""
    assert out["realCity"] == "Denver, CO"
    assert out["modes"] == {"prefs": "onsite", "config": "hybrid", "oldRemote": "any", "oldAny": "any", "nothing": "any"}


def test_the_job_card_renders_the_chip_and_the_line() -> None:
    source = (UI_SRC / "components" / "JobCard.jsx").read_text(encoding="utf-8")
    assert "workModeChip(job)" in source
    assert "whyPassedLine(job.workModeFit)" in source
    assert "{whyPassed}" in source
