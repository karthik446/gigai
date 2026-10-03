"""N33: the Jobs page's paging state, run under node.

``pageModel.js`` (the paging state) and ``api.js``'s ``createResultsPager``
(the page reader) are plain JavaScript, so this runs them under the system
``node`` with a recorded page source and asserts on the JSON the script
prints. LOUD skip when ``node`` is not on PATH.

Pinned: a page of 50 rows first; "Show more" appends the next 50 and asks
only for the pages it lacks (no earlier page again, two waiting calls never
read one page twice); the count line ("Showing 50 of 500 postings", "Showing
100 of 500 postings"); a filter change goes back to page 1 while an equal
filter keeps the paging; a filter reads every row; the grid's sort of the
loaded rows is a prefix of the sort of the whole run (the order does not
reshuffle when a later page loads); ``JobsGrid`` / ``FindJobsView`` (as
source) use these and the built bundle carries the button.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"

SCRIPT = """
import * as model from PAGE_MODEL_URL;
import * as api from API_URL;
import { sortJobs } from JOB_MODEL_URL;

const asked = [];
const source = (total) => async (runId, { limit, offset }) => {
  asked.push([offset, limit]);
  const rows = [];
  for (let index = offset; index < Math.min(offset + limit, total); index += 1) {
    rows.push({ posting: { normalized_url: `u${index}` } });
  }
  return {
    total, limit, offset, created_at: "t", counts: {},
    payload: { rows, assessments: [], not_assessed: [], carried_forward_assessments: [] },
    carried_forward_assessments: [],
  };
};
const urls = (response) => response.payload.rows.map((row) => row.posting.normalized_url);
const out = {};

// -- the pager: append, no refetch
{
  asked.length = 0;
  const seen = [];
  const pager = api.createResultsPager("run", { fetchPage: source(500), onPage: (r) => { seen.push(r.payload.rows.length); } });
  out.pageRows = api.JOBS_PAGE_ROWS;
  const first = await pager.ensure(50);
  out.first = { asked: asked.slice(), rows: urls(first).length, total: pager.total(), loaded: pager.loaded() };
  asked.length = 0;
  await pager.ensure(50);
  out.repeat = asked.slice();
  const second = await pager.ensure(100);
  out.second = { asked: asked.slice(), rows: urls(second), seen: seen.slice() };
  asked.length = 0;
  // two callers waiting at once read a page once
  await Promise.all([pager.ensure(150), pager.ensure(150)]);
  out.concurrent = asked.slice();
  asked.length = 0;
  const all = await pager.ensure(100000);
  out.all = { asked: asked.slice(), rows: urls(all), loaded: pager.loaded() };
  asked.length = 0;
  await pager.ensure(600);
  out.afterAll = asked.slice();
}
// -- limit beyond total; a run smaller than a page; an empty run
{
  asked.length = 0;
  const pager = api.createResultsPager("run", { fetchPage: source(30) });
  const response = await pager.ensure(50);
  out.small = { asked: asked.slice(), rows: urls(response).length, total: pager.total() };
  asked.length = 0;
  const none = api.createResultsPager("run", { fetchPage: source(0) });
  await none.ensure(50);
  out.none = { asked: asked.slice(), loaded: none.loaded() };
  asked.length = 0;
  // a run that has fewer rows than it said never loops
  const liar = api.createResultsPager("run", { fetchPage: async (r, o) => ({ ...(await source(50)(r, o)), total: 500 }) });
  await liar.ensure(500);
  out.liar = { asked: asked.slice(), loaded: liar.loaded() };
  // stop() ends the read
  asked.length = 0;
  const stopping = api.createResultsPager("run", { fetchPage: source(500), onPage: () => false });
  await stopping.ensure(500);
  out.stopped = asked.slice();
}
// -- the paging state
{
  let shown = model.initialShown();
  out.paging = { initial: shown };
  const line = (drawn, matching) => model.showingLine({ drawn, matching });
  out.lines = [line(50, 500), line(100, 500), line(500, 500), model.showingLine({ drawn: 1, matching: 3, noun: "assessments" })];
  shown = model.showMoreShown(shown);
  out.paging.afterOne = shown;
  shown = model.showMoreShown(shown);
  out.paging.afterTwo = shown;
  out.wanted = {
    top: model.rowsWanted({ shown: 50, total: 500, filtersActive: false }),
    second: model.rowsWanted({ shown: 100, total: 500, filtersActive: false }),
    past: model.rowsWanted({ shown: 550, total: 500, filtersActive: false }),
    small: model.rowsWanted({ shown: 50, total: 30, filtersActive: false }),
    filtered: model.rowsWanted({ shown: 50, total: 500, filtersActive: true }),
  };
  const filters = { search: "", company: "", fit: "all", sponsorship: "all", assessed: "all", state: "all" };
  out.keys = {
    same: model.filtersKey(filters) === model.filtersKey({ ...filters }),
    reordered: model.filtersKey(filters) === model.filtersKey({ state: "all", assessed: "all", sponsorship: "all", fit: "all", company: "", search: "" }),
    search: model.filtersKey(filters) === model.filtersKey({ ...filters, search: "go" }),
    fit: model.filtersKey(filters) === model.filtersKey({ ...filters, fit: "strong" }),
  };
  out.more = [model.hasMore({ drawn: 50, matching: 500 }), model.hasMore({ drawn: 500, matching: 500 }), model.hasMore({ drawn: 0, matching: 0 })];
  out.labels = [model.showMoreLabel({ drawn: 50, matching: 500 }), model.showMoreLabel({ drawn: 480, matching: 500 }), model.showMoreLabel({ drawn: 500, matching: 500 })];
  out.pageOf = [model.pageOf([1, 2, 3], 2), model.pageOf([1, 2, 3], 10), model.pageOf([1, 2, 3], -1)];
}
// -- the grid's sort of the loaded rows is a prefix of the sort of the run
{
  let seed = 7;
  const next = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed; };
  const verdicts = ["matched_above_threshold", "pending_user_answers", "assessed", "not_assessed", "not_a_match"];
  const jobs = [];
  for (let index = 0; index < 500; index += 1) {
    const roll = next() % 10;
    const rank = roll < 2 ? null : roll === 9 ? { score: 50, fit: "maybe", demoted: true } : { score: (next() % 7) * 10, fit: "maybe", demoted: false };
    jobs.push({ id: `j${index}`, verdict: index < 20 ? verdicts[index % 3] : "not_assessed", rank, posting: { published_at: `2026-09-${20 + (next() % 3)}` } });
  }
  const whole = sortJobs(jobs).map((job) => job.id);
  // the server sends the whole order page by page; the grid sorts what it has
  const pages = [];
  let loaded = [];
  for (let offset = 0; offset < 500; offset += 50) {
    loaded = loaded.concat(sortJobs(jobs).slice(offset, offset + 50));
    pages.push(sortJobs(loaded).map((job) => job.id));
  }
  out.stable = pages.every((ids, index) => ids.join() === whole.slice(0, (index + 1) * 50).join());
  out.wholeLength = whole.length;
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the Jobs page's paging state was not run")
    script = (
        SCRIPT.replace("PAGE_MODEL_URL", json.dumps((UI_SRC / "pageModel.js").resolve().as_uri()))
        .replace("API_URL", json.dumps((UI_SRC / "api.js").resolve().as_uri()))
        .replace("JOB_MODEL_URL", json.dumps((UI_SRC / "jobModel.js").resolve().as_uri()))
    )
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_first_read_is_one_page_of_50(out: dict) -> None:
    assert out["pageRows"] == 50
    assert out["first"] == {"asked": [[0, 50]], "rows": 50, "total": 500, "loaded": 50}
    assert out["repeat"] == [], "rows already in are not read again"


def test_show_more_appends_the_next_page_and_reads_no_earlier_page(out: dict) -> None:
    second = out["second"]
    assert second["asked"] == [[50, 50]]
    assert second["rows"] == [f"u{index}" for index in range(100)], "the second page follows the first"
    assert second["seen"] == [50, 100]
    assert out["concurrent"] == [[100, 50]], "two callers waiting for the same rows read one page"
    assert out["all"]["asked"] == [[150, 50], [200, 50], [250, 50], [300, 50], [350, 50], [400, 50], [450, 50]]
    assert out["all"]["rows"] == [f"u{index}" for index in range(500)] and out["all"]["loaded"] == 500
    assert out["afterAll"] == [], "past the total nothing is asked"


def test_a_small_empty_or_overstated_run_never_loops(out: dict) -> None:
    assert out["small"] == {"asked": [[0, 50]], "rows": 30, "total": 30}
    assert out["none"] == {"asked": [[0, 50]], "loaded": 0}
    assert out["liar"] == {"asked": [[0, 50], [50, 50]], "loaded": 50}
    assert out["stopped"] == [[0, 50]], "onPage returning false ends the read"


def test_the_count_line_reads_showing_n_of_total(out: dict) -> None:
    assert out["lines"] == ["Showing 50 of 500 postings", "Showing 100 of 500 postings", "Showing 500 of 500 postings", "Showing 1 of 3 assessments"]
    assert out["paging"] == {"initial": 50, "afterOne": 100, "afterTwo": 150}
    assert out["more"] == [True, False, False]
    assert out["labels"] == ["Show 50 more", "Show 20 more", "Show 0 more"]
    assert out["pageOf"] == [[1, 2], [1, 2, 3], []]


def test_a_filter_change_goes_back_to_page_one_and_a_filter_reads_every_row(out: dict) -> None:
    assert out["keys"] == {"same": True, "reordered": True, "search": False, "fit": False}
    assert out["wanted"] == {"top": 50, "second": 100, "past": 500, "small": 30, "filtered": 500}


def test_the_sort_of_the_loaded_rows_never_reshuffles_when_a_page_loads(out: dict) -> None:
    assert out["wholeLength"] == 500 and out["stable"] is True


def test_the_grid_and_the_view_use_the_paging_state() -> None:
    grid = (UI_SRC / "components" / "JobsGrid.jsx").read_text(encoding="utf-8")
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert "showingLine" in grid and 'data-role="show-more"' in grid
    assert re.search(r"useEffect\(\(\) => \{\s*setShown\(initialShown\(\)\);\s*\}, \[filterState\]\)", grid), "a filter change resets the page"
    assert "onWantRows" in grid and "showMoreShown" in grid
    assert "createResultsPager" in view and "getRunResults" not in view, "the view reads pages on demand, not the whole run"
    # 0.1.10.7 M4b: one grid of a run is left, on the past run's page.
    assert view.count("onWantRows={runActive ? null : wantResultRows}") == 1, "a live run's streaming grid is not paged from the server"
    assert "RANK_ORDER_NOTE" in grid, "the honest order note stays"


def test_the_built_bundle_carries_the_paging() -> None:
    bundle = "".join(path.read_text(encoding="utf-8") for path in (UI / "dist" / "assets").glob("index-*.js"))
    assert 'data-role":"show-more"' in bundle or "show-more" in bundle
    assert "Showing " in bundle and " more" in bundle
