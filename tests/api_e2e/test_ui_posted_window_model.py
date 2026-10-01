"""0110-019: the Jobs page's "Posted" chip row and "Find postings from the last N days", run under node.

``postedWindowModel.js`` and ``jobModel.js`` are plain JavaScript, so this
runs them under the system ``node`` and asserts on the JSON the script
prints. LOUD skip when ``node`` is not on PATH.

Pinned: the chips (7d / 10d / 30d / 60d / Any, the Python choices); the
filter over a run's cards by their own posting date -- 10d -> 30d shows more
of the cards the run holds, 30d -> 10d hides the older ones, Any shows all,
a card with no date is never hidden -- with no request; a chosen window
makes the filter "active" (the grid then reads every row) and "Clear
filters" goes back to Any; the button shows only when the chosen window is
wider than what the run searched, and its words; the line after a click. The
grid and the view use them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs import posted_window
from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"

SCRIPT = """
import * as m from MODEL_URL;
import { EMPTY_FILTERS, filterJobs, hasActiveFilter } from JOB_MODEL_URL;
import { filtersKey } from PAGE_MODEL_URL;

const out = {};
const NOW = Date.parse("2026-10-01T12:00:00Z");
const daysAgo = (days) => new Date(NOW - days * 86400000).toISOString();
const job = (id, published_at) => ({ id, posting: { title: id, company: "acme", location: "Denver, CO", published_at }, rank: null, verdict: "not_assessed", sponsorship: "unknown" });
// What a run that searched 30 days holds, plus a card with no date and one with a date that does not parse.
const jobs = [job("d2", daysAgo(2)), job("d5", daysAgo(5)), job("d9", daysAgo(9)), job("d20", daysAgo(20)), job("d25", daysAgo(25)), job("undated", null), job("odd", "soon")];
const shown = (posted) => filterJobs(jobs, { ...EMPTY_FILTERS, posted }, NOW).map((item) => item.id);

out.options = m.postedOptions();
out.optionsFromServer = m.postedOptions([7, 10, 30, 60]);
out.days = m.POSTED_DAYS;
out.emptyPosted = EMPTY_FILTERS.posted;
out.shown = { 7: shown(7), 10: shown(10), 30: shown(30), 60: shown(60), any: shown("any") };
out.active = [hasActiveFilter(EMPTY_FILTERS), hasActiveFilter({ ...EMPTY_FILTERS, posted: 10 }), hasActiveFilter({ ...EMPTY_FILTERS, posted: "any" })];
out.keyChanges = filtersKey(EMPTY_FILTERS) !== filtersKey({ ...EMPTY_FILTERS, posted: 30 });
out.edge = [
  m.postedWithin(daysAgo(10), 10, NOW),
  m.postedWithin(new Date(NOW - 10 * 86400000 - 1000).toISOString(), 10, NOW),
  m.postedWithin(null, 10, NOW),
  m.postedWithin(daysAgo(400), "any", NOW),
];

const ten = { run_id: "r1", run_days: 10, searched_days: 10, choices: [7, 10, 30, 60], added_total: 0, search: null, skip_reason: null };
out.button = {
  narrower: m.canFindOlder(ten, 7),
  same: m.canFindOlder(ten, 10),
  wider: m.canFindOlder(ten, 30),
  widest: m.canFindOlder(ten, 60),
  any: m.canFindOlder(ten, "any"),
  unread: m.canFindOlder(null, 30),
  afterSearch: m.canFindOlder({ ...ten, searched_days: 30 }, 30),
  afterSearchWider: m.canFindOlder({ ...ten, searched_days: 30 }, 60),
};
out.label = [m.findOlderLabel(30), m.findOlderLabel(60)];
out.hint = [m.findOlderHint(ten), m.findOlderHint(null)];
const search = (added, extra = {}) => ({ days: 30, added, matched: 4, not_added: 0, ...extra });
out.lines = [
  m.searchLine(null),
  m.searchLine(ten),
  m.searchLine({ ...ten, search: search(2), assess: { added: 2, limit: 1 } }),
  m.searchLine({ ...ten, search: search(1), assess: { added: 1, limit: 10 } }),
  m.searchLine({ ...ten, search: search(3), assess: { added: 3, limit: null } }),
  m.searchLine({ ...ten, search: search(2), assess: null }),
  m.searchLine({ ...ten, search: search(0) }),
  m.searchLine({ ...ten, search: search(500, { not_added: 12 }), assess: { added: 500, limit: 500 } }),
  m.searchLine({ ...ten, skip_reason: "sources_update_required", search: search(0) }),
  m.searchLine({ ...ten, skip_reason: "run_not_finished" }),
  m.searchLine({ ...ten, skip_reason: "no_run_input" }),
];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the posted-window model was not run")
    script = SCRIPT
    for name, module in (("PAGE_MODEL_URL", "pageModel.js"), ("JOB_MODEL_URL", "jobModel.js"), ("MODEL_URL", "postedWindowModel.js")):
        script = script.replace(name, json.dumps((UI_SRC / module).resolve().as_uri()))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_chip_row_is_7_10_30_60_days_and_any_and_starts_on_any(out: dict) -> None:
    assert out["options"] == [[7, "7d"], [10, "10d"], [30, "30d"], [60, "60d"], ["any", "Any"]]
    assert out["optionsFromServer"] == out["options"]
    assert tuple(out["days"]) == posted_window.WINDOW_CHOICES, "the chips are the server's choices"
    assert out["emptyPosted"] == "any", "nothing is hidden until a chip is chosen"


def test_a_chip_filters_the_cards_shown_by_their_posting_date(out: dict) -> None:
    shown = out["shown"]
    assert shown["10"] == ["d2", "d5", "d9", "undated", "odd"]
    # 10d -> 30d shows more of what the run holds; 30d -> 10d hides the older ones again.
    assert shown["30"] == ["d2", "d5", "d9", "d20", "d25", "undated", "odd"]
    assert set(shown["10"]) < set(shown["30"]) and set(shown["30"]) - set(shown["10"]) == {"d20", "d25"}
    assert shown["7"] == ["d2", "d5", "undated", "odd"]
    assert shown["60"] == shown["any"] == ["d2", "d5", "d9", "d20", "d25", "undated", "odd"]
    # Exactly N days old is inside; a second older is outside; no date is never hidden; Any hides nothing.
    assert out["edge"] == [True, False, True, True]


def test_a_chosen_window_is_an_active_filter_so_every_row_is_read(out: dict) -> None:
    assert out["active"] == [False, True, False]
    assert out["keyChanges"] is True, "choosing a chip goes back to page 1"


def test_the_button_shows_only_when_the_window_is_wider_than_what_was_searched(out: dict) -> None:
    assert out["button"] == {
        "narrower": False,
        "same": False,
        "wider": True,
        "widest": True,
        "any": False,
        "unread": False,
        "afterSearch": False,
        "afterSearchWider": True,
    }
    assert out["label"] == ["Find postings from the last 30 days", "Find postings from the last 60 days"]
    assert out["hint"] == [
        "This run searched the last 10 days. The search reads the boards stored on this machine: no download, no new run.",
        "",
    ]


def test_the_line_after_a_click_says_what_was_added_and_what_is_assessed(out: dict) -> None:
    assert out["lines"] == [
        "",
        "",
        "Added 2 postings from the last 30 days. Ranking those, then assessing the top 1.",
        "Added 1 posting from the last 30 days. Ranking and assessing only those.",
        "Added 3 postings from the last 30 days. Ranking and assessing only those.",
        "Added 2 postings from the last 30 days.",
        "No more postings from the last 30 days in the stored boards.",
        "Added 500 postings from the last 30 days. Ranking and assessing only those. 12 postings more matched and are over the run's limit.",
        "No company postings are stored on this machine yet. Run Update sources, then try again.",
        "This run is still going; older postings can be added once it ends.",
        "This run sealed no search to repeat.",
    ]


def test_the_grid_has_the_posted_chips_beside_rank_and_the_button() -> None:
    grid = (UI_SRC / "components" / "JobsGrid.jsx").read_text(encoding="utf-8")
    rank = grid.index('<ChipGroup label="Rank"')
    posted = grid.index('label="Posted"')
    state = grid.index("<StateChips options={states}")
    assert rank < posted < state, "Posted sits beside Rank, above State"
    assert grid[rank:posted].count('<div className="filter-row">') == 0, "Rank and Posted share one row"
    assert 'data-role="posted-filter"' in grid and "postedOptions(" in grid
    assert 'onChange={(value) => setFilter("posted", value)}' in grid
    assert 'data-action="find-older"' in grid and "findOlderLabel(filters.posted)" in grid
    assert "canFindOlder(postedWindow, filters.posted)" in grid and "onFindOlder(filters.posted)" in grid
    assert 'data-role="find-older-result"' in grid and "searchLine(postedWindow)" in grid


def test_the_view_reads_the_window_and_the_click_searches_then_reloads_the_run() -> None:
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert "postPostedWindow(id, {})" in view, "a load reads what the run searched"
    assert "postPostedWindow(id, { days })" in view and "loadResults(id);" in view
    assert view.count("onFindOlder={runActive ? null : findOlder}") == 2, "Jobs and a run page both offer it; a live run does not"
    assert "startRun" in view and view.count("startRun(") == 1, "the click starts no run: the only startRun is Run find jobs"
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert "/posted-window`" in api and "export function postPostedWindow(runId, fields)" in api


def test_the_built_bundle_carries_the_posted_chips() -> None:
    bundle = "".join(path.read_text(encoding="utf-8") for path in (UI / "dist" / "assets").glob("index-*.js"))
    assert "Find postings from the last " in bundle and "/posted-window" in bundle and "posted-filter" in bundle
