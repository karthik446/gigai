"""uat-bug-025: a run posting past the run's assess cap says so, and can be assessed.

``ui/src/jobModel.js`` is plain JavaScript, so this runs it under the system
``node`` (no JS test runner) and asserts on the JSON it prints. LOUD skip
when ``node`` is not on PATH. The API half (``assess_cap`` on every results
page) is pinned in ``tests/behaviors/scout_find_jobs/test_run_reads.py``.

Pinned:

* while the run is going an ``acquired`` row reads "Waiting to be assessed";
* once it has ended the same row reads "Not assessed: this run assessed its
  top N", N being the run's own cap (never a hard-coded 10), and without a
  cap read "Not assessed: past this run's full-assessment limit";
* a row the run did not leave ``acquired`` keeps its own words, and an
  on-demand job (no run row) is left alone;
* the job page offers "Assess" for such a row once the run has ended, with
  origin ``job_page`` (``assessOriginFor``: a run row's page), and not while
  the run is going (read as source).
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

NODE_SCRIPT = """
import * as model from JOB_MODEL_URL;
const row = (status) => ({ posting: { normalized_url: "https://a.test/" + status, title: status }, status });
const acquired = { id: "a", row: row("acquired"), status: "acquired", notAssessedReason: null };
const failed = { id: "f", row: row("failed"), status: "failed", notAssessedReason: null };
const onDemand = { id: "o", row: null, status: "on_demand" };
const jobs = [acquired, failed, onDemand];
const ended = (cap) => model.withRunEnd(jobs, { ended: true, assessCap: cap });
process.stdout.write(JSON.stringify({
  going: model.notAssessedLine(model.withRunEnd(jobs, { ended: false, assessCap: 10 })[0]),
  ended10: model.notAssessedLine(ended(10)[0]),
  ended25: model.notAssessedLine(ended(25)[0]),
  endedNoCap: model.notAssessedLine(ended(null)[0]),
  failed: model.notAssessedLine(ended(10)[1]),
  onDemandUntouched: ended(10)[2] === onDemand,
  origin: model.assessOriginFor(ended(10)[0]),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the UI model test cannot run")
    script = NODE_SCRIPT.replace("JOB_MODEL_URL", json.dumps(JOB_MODEL_JS.as_uri()))
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_waiting_only_while_the_run_is_going(out: dict) -> None:
    assert out["going"] == "Waiting to be assessed"


def test_after_the_run_the_line_names_the_runs_own_cap(out: dict) -> None:
    assert out["ended10"] == "Not assessed: this run assessed its top 10"
    assert out["ended25"] == "Not assessed: this run assessed its top 25"
    assert out["endedNoCap"] == "Not assessed: past this run's full-assessment limit"


def test_other_rows_keep_their_words(out: dict) -> None:
    assert out["failed"] == "Assessment failed"
    assert out["onDemandUntouched"] is True


def test_a_run_rows_assess_is_the_job_pages(out: dict) -> None:
    assert out["origin"] == "job_page"


def test_the_job_page_offers_assess_only_once_the_run_has_ended() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text()
    assert '(job.status !== "acquired" || job.runEnded) && <AssessNow' in page
    assert '"Assessing…" : "Assess"}' in page
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text()
    assert "withRunEnd(" in view and "assess_cap: response.assess_cap" in view


def test_the_cap_reads_as_full_assessments_not_as_a_limit_on_what_is_looked_at() -> None:
    """uat-bug-040: wording only; the field, config key and range are unchanged."""
    dialog = (UI_SRC / "components" / "RunConfirmDialog.jsx").read_text()
    assert "Full assessments (1-50)" in dialog and "Assessment cap" not in dialog
    assert "Every matching posting is ranked. This many of the top-ranked ones are then assessed in full; you can assess the" in " ".join(dialog.split())
    assert "rest one at a time with Assess." in dialog
    assert "config.default_assess_cap" in dialog and "min={1}" in dialog and "max={50}" in dialog
    display = (UI_SRC / "display.js").read_text()
    assert "Ranked below the full-assessment limit for this run. Use Assess to assess it now." in display
