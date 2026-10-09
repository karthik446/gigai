"""0.1.11.8 N1 + N2: "US only" and the row of copies, in the UI's models and the search store, run under node.

``ui/src/postingsModel.js``, ``ui/src/freeSearchModel.js`` and ``ui/src/freeSearchStore.js`` are plain JavaScript,
so this runs them under the system ``node`` over what the REAL server answers on a synthetic home
(``tests/support/copies_fixtures.py``: one job posted once per country, one in two US cities, and what never merges:
another description, another title, another company, a posting that says "Remote" alone), and asserts on the JSON
the script prints. LOUD skip without ``node``. What lives in JSX is pinned
by reading the source.

Two node runs: the first prints the queries the models build; the server answers exactly those; the second drives
the models and the store with those answers.

Pinned: the box sends nothing until it is touched (the server applies the setup's default) and ``us_only=1|0`` after;
it is part of the address of the Jobs list (``us=0``), is not a filter "Clear filters" clears, and goes with a filter's
"Assess these"; the box is ticked from what the answer applied; Show all does not change it and the scope line and
"Show all N" say "US only"; a posting nobody places is listed with the "unclear location" label while US only is on;
a row of copies is its canonical job, shows where its copies are and how many postings it stands for, in both lists;
the search's row of copies is offered ONE Assess and ONE Mark applied, and neither once a copy has it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from gigai.scout.find_jobs import job_copies
from gigai.scout.find_jobs.api import static as static_module

from tests.support.copies_fixtures import COUNTRIES, JOBS, ONE_JOB, OTHER, SLUG, anywhere_profile, seed_copies
from tests.support.posting_fixtures import build_postings_fixture, job_url

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"

QUERIES = """
import * as p from POSTINGS_URL;
import * as s from SEARCH_URL;
const anywhere = ANYWHERE;
const filter = { ...p.EMPTY_FILTER, profileIds: [anywhere] };
const typed = { ...s.EMPTY_FORM, title: "staff engineer" };
const out = {
  list: p.postingsQuery(filter, { limit: 50 }),
  listOff: p.postingsQuery({ ...filter, usOnly: false }, { limit: 50 }),
  listOn: p.postingsQuery({ ...filter, usOnly: true }, { limit: 50 }),
  search: s.searchQuery(typed),
  searchCount: s.countQuery(typed),
  searchAll: s.searchQuery({ ...typed, showAll: true }),
  searchAllCount: s.countQuery({ ...typed, showAll: true }),
  searchAllOff: s.searchQuery({ ...typed, showAll: true, usOnly: false }),
  searchAllOffCount: s.countQuery({ ...typed, showAll: true, usOnly: false }),
  searchOn: s.searchQuery({ ...typed, usOnly: true }),
  searchOnCount: s.countQuery({ ...typed, usOnly: true }),
};
console.log(JSON.stringify(out));
"""

DRIVE = """
import * as p from POSTINGS_URL;
import * as s from SEARCH_URL;
import { createFreeSearchStore } from STORE_URL;

const data = DATA;
const out = {};
const anywhere = data.anywhere;

// --- the Jobs list ---------------------------------------------------------------------------
const filter = { ...p.EMPTY_FILTER, profileIds: [anywhere] };
out.constants = { rule: p.US_ONLY_RULE, label: p.US_ONLY_LABEL, copies: p.COPIES_RULE, canonical: p.CANONICAL_RULE, unclear: p.UNCLEAR_LABEL, withShowAll: s.US_ONLY_WITH_SHOW_ALL };
out.hash = [p.jobsHash(filter), p.jobsHash({ ...filter, usOnly: false }), p.jobsHash({ ...filter, usOnly: true })];
out.parsed = out.hash.map((hash) => p.parseJobsHash(hash).filter.usOnly);
out.hasFilter = [p.hasFilter(p.EMPTY_FILTER), p.hasFilter({ ...p.EMPTY_FILTER, usOnly: false })];
const listed = data.answers[data.queries.list];
const listedOff = data.answers[data.queries.listOff];
out.checked = [p.usOnlyChecked(null, null), p.usOnlyChecked(null, listed.us_only), p.usOnlyChecked(false, listed.us_only), p.usOnlyChecked(true, { on: false, default: false }),
  p.usOnlyChecked(null, { default: true }), p.usOnlyChecked(null, listedOff.us_only)];
out.leftOut = [p.usOnlyLeftOutLine(listed.counts), p.usOnlyLeftOutLine(listedOff.counts), p.usOnlyLeftOutLine({ us_only_left_out: 1 }), p.usOnlyLeftOutLine(null)];
const rowOf = (answer, job) => answer.postings.rows.find((row) => row.job_identity === job);
const one = rowOf(listedOff, data.jobs.one);
const two = rowOf(listed, data.jobs.two);
const single = rowOf(listed, data.jobs.single);
const nowhere = rowOf(listed, data.jobs.unclear);
out.unclear = [p.unclearLabel(nowhere, true), p.unclearLabel(nowhere, false), p.unclearLabel(two, true), p.unclearLabel(null, true)];
out.listRows = { on: listed.postings.rows.length, off: listedOff.postings.rows.length, matched: [listed.counts.matched, listedOff.counts.matched] };
out.place = [p.rowPlace(one), p.rowPlace(two), p.rowPlace(single), p.rowPlace({ location: "Denver, CO" }), p.rowPlace(null)];
out.tags = [p.copiesTag(one), p.copiesTag(two), p.copiesTag(single), p.copiesTag({ copies: 1 }), p.copiesTag(null)];
out.detail = [p.detailLine(one), p.detailLine(two), p.detailLine(single)];
out.ask = [p.assessAskBody({ filter }), p.assessAskBody({ filter: { ...filter, usOnly: false } }), p.assessAskBody({ filter: { ...filter, usOnly: true }, selectedIds: [data.jobs.one] }),
  p.assessAllBody({ filter: { ...filter, usOnly: false } }), p.assessAllBody({ filter })];

// --- the search ------------------------------------------------------------------------------
const sent = [];
const fetchSearch = (query) => {
  sent.push(query);
  const answer = data.answers[query];
  return answer ? Promise.resolve(answer) : Promise.reject(new Error("no answer for " + query));
};
const store = createFreeSearchStore({ fetchSearch });
const state = () => store.getState();
const box = () => s.searchUsOnlyChecked(state().form, state().results, state().usOnlyDefault);
const lines = () => {
  const { results } = state();
  return { shown: s.shownLine(results), total: s.totalLine(results), hidden: s.hiddenLabel(results), scope: s.scopeText(results), rows: results.rows.length, box: box() };
};
out.boxBefore = [box(), (store.knowUsOnlyDefault(listed.us_only.default), box())];
out.same = [s.sameSearch({ ...s.EMPTY_FORM, title: "a" }, { ...s.EMPTY_FORM, title: "a", usOnly: false }), s.sameSearch({ ...s.EMPTY_FORM, title: "a", usOnly: true }, { title: "a", usOnly: true })];
out.clean = s.cleanForm({ title: "a", usOnly: "yes" });

// The box untouched: nothing is sent for it; the answer says it applied.
store.setForm({ title: "staff engineer" });
await store.search();
out.firstSent = sent.slice();
out.first = lines();
out.firstUsOnly = state().results.usOnly;
// Show all: US only is its own switch and stays.
sent.length = 0;
await store.setShowAll(true);
out.allSent = sent.slice();
out.all = lines();
// Unticked with Show all: every country.
sent.length = 0;
await store.setUsOnly(false);
out.offSent = sent.slice();
out.off = lines();
const rows = state().results.rows;
const searchOne = rows.find((row) => row.job_identity === data.jobs.one);
const other = rows.find((row) => row.job_identity === data.jobs.other);
out.searchRow = { copies: searchOne.copies, location: s.rowLocation(searchOne), tag: p.copiesTag(searchOne), members: searchOne.members.map((member) => member.job_identity), otherCopies: other.copies, otherLocation: s.rowLocation(other) };
out.titles = rows.map((row) => row.title + " @ " + row.company_key);
const names = s.profileNames(state().results, data.profiles);
const searchNowhere = rows.find((row) => row.job_identity === data.jobs.unclear);
out.searchUnclear = [s.rowLabels(searchNowhere, names, { usOnly: true }).map((label) => label.label), s.rowLabels(searchNowhere, names).map((label) => label.label), s.rowLabels(searchOne, names, { usOnly: true }).map((label) => label.kind)];
// The row's actions are the ROW's: one Assess, one Mark applied; a copy that has it takes the offer away.
const profile = s.defaultProfileOf(data.profiles);
out.actions = {
  assess: [s.canAssess(searchOne, profile), s.assessRequest(searchOne, profile).job.job_url],
  apply: [s.canApply(searchOne), s.applyRequest(searchOne)],
  afterApply: s.canApply(s.appliedRow(searchOne, "2026-10-06T10:00:00Z")),
  afterAssess: s.canAssess(s.assessedRow(searchOne, { job_state: { state: "matched" } }, profile), profile),
};
// Ticked again without Show all, then Clear: the box keeps what was set.
sent.length = 0;
await store.setShowAll(false);
await store.setUsOnly(true);
out.onSent = sent.slice();
out.on = lines();
store.clear();
out.cleared = { form: state().form, box: box(), usOnlyDefault: state().usOnlyDefault };
console.log(JSON.stringify(out));
"""


def _node(script: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the US-only and copies models were NOT run")
    completed = subprocess.run([node, "--input-type=module", "-"], input=script, capture_output=True, text=True, timeout=60, check=False)  # the script goes by stdin: Linux caps one argument at 128 KiB
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def _urls(script: str) -> str:
    for name, file in (("POSTINGS_URL", "postingsModel.js"), ("SEARCH_URL", "freeSearchModel.js"), ("STORE_URL", "freeSearchStore.js")):
        script = script.replace(name, json.dumps((UI_SRC / file).resolve().as_uri()))
    return script


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from gigai.scout.find_jobs import search_index
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    now = datetime.now(UTC)
    seed_copies(fx, seen_at=now - timedelta(minutes=30), newest=now - timedelta(hours=2))
    anywhere = anywhere_profile(fx)
    queries = _node(_urls(QUERIES).replace("ANYWHERE", json.dumps(anywhere)))
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path: str) -> dict:
        with urllib.request.urlopen(url + path, timeout=60) as response:
            return json.loads(response.read())

    try:
        assert get("/api/postings?limit=1")["schema_version"] == "scout-postings:1"  # the profiles' lists exist
        assert search_index.rebuild_from_index(fx.home_root).available
        answers = {query: get(("/api/postings?" if name.startswith("list") else "/api/search?") + query) for name, query in queries.items()}
        profiles = get("/api/profiles")["profiles"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
        search_index.close(fx.home_root)
    data = {
        "answers": answers, "queries": queries, "profiles": profiles, "anywhere": anywhere,
        # The canonical jobs: the earliest posted of the seven countries (Poland), the earlier of the two US cities.
        "jobs": {"one": job_url(SLUG, 7), "two": job_url(SLUG, 9), "single": job_url(SLUG, 10), "other": job_url(OTHER, 1), "unclear": job_url(SLUG, 13)},
    }
    result = _node(_urls(DRIVE).replace("DATA", json.dumps(data)))
    result["queries"], result["anywhere"] = queries, anywhere
    return result


def test_the_us_only_box_of_the_jobs_list(out: dict) -> None:
    queries, anywhere = out["queries"], out["anywhere"]
    # Untouched, the box sends nothing: the server applies the setup's default. Touched, it says 1 or 0.
    assert queries["list"] == f"profile_id={anywhere}&limit=50"
    assert queries["listOff"] == f"profile_id={anywhere}&us_only=0&limit=50" and queries["listOn"] == f"profile_id={anywhere}&us_only=1&limit=50"
    # It is part of the view's address, and no profile setting.
    assert out["hash"] == [f"#/jobs?profile={anywhere}", f"#/jobs?profile={anywhere}&us=0", f"#/jobs?profile={anywhere}&us=1"]
    assert out["parsed"] == [None, False, True]
    assert out["hasFilter"] == [False, False], '"Clear filters" is not offered for it, and does not clear it'
    # Ticked from what the answer applied (this US setup: on), unless this view set it.
    assert out["checked"] == [False, True, False, True, True, False]
    assert out["constants"]["rule"] == job_copies.US_ONLY_RULE and out["constants"]["label"] == "US only"
    assert out["leftOut"] == ["9 outside the US are left out.", None, "1 outside the US is left out.", None]
    # The rows: the four jobs not clearly abroad with the box on, the seven jobs (fourteen postings) with it off.
    assert out["listRows"] == {"on": 4, "off": JOBS, "matched": [4, JOBS]}
    # A posting nobody places is LISTED with the box on, and labelled; without the box nothing is said.
    label, off, placed, none = out["unclear"]
    assert label["label"] == "unclear location" == out["constants"]["unclear"] == job_copies.UNCLEAR_LABEL and label["kind"] == "place"
    assert off is None and placed is None and none is None
    # A filter's "Assess these" selects what the list shows: it carries the box when it was touched.
    assert out["ask"] == [
        {"profile_id": anywhere}, {"profile_id": anywhere, "us_only": False}, {"jobs": [job_url(SLUG, 7)], "profile_id": anywhere},
        {"states": ["not_assessed"], "profile_id": anywhere, "us_only": False}, {"states": ["not_assessed"], "profile_id": anywhere},
    ]


def test_a_row_of_copies_says_where_and_how_many(out: dict) -> None:
    # The row is the canonical job (the earliest posted of the seven: Poland) and lists the others after it.
    assert out["place"] == ["Remote: Poland, Ukraine, Romania +4", "Austin, TX; Remote - United States", "Remote - United States", "Denver, CO", ""]
    one, two, single, plain, nothing = out["tags"]
    assert one["count"] == 7 and one["label"] == "7 postings" and one["title"].startswith("The same job posted 7 times: Remote Poland; Remote Ukraine;")
    assert two["count"] == 2 and two["label"] == "2 postings" and single is None and plain is None and nothing is None
    assert "· Remote: Poland, Ukraine, Romania +4" in out["detail"][0] and "Austin, TX; Remote - United States" in out["detail"][1]
    # The help lines are the server's sentences.
    assert out["constants"]["copies"] == job_copies.COPIES_RULE and out["constants"]["canonical"] == job_copies.CANONICAL_RULE
    assert out["constants"]["withShowAll"] == job_copies.US_ONLY_WITH_SHOW_ALL
    # The search's row: the canonical job, every copy named; what never merges is a row of its own (another
    # description, a title written another way, another company).
    row = out["searchRow"]
    assert row["copies"] == 7 and row["location"] == "Remote: Poland, Ukraine, Romania +4" and row["tag"]["label"] == "7 postings"
    assert row["members"] == [job_url(SLUG, n) for n in reversed(ONE_JOB)] and len(COUNTRIES) == 7
    assert row["otherCopies"] == 1 and row["otherLocation"] == "Remote - United States"
    assert sorted(out["titles"]) == sorted([
        f"Staff Engineer @ lever:{SLUG}", f"Staff Engineer @ lever:{SLUG}", f"STAFF  Engineer. @ lever:{SLUG}", f"Staff Engineer, Payments @ lever:{SLUG}",
        f"Staff Engineer, Search @ lever:{SLUG}", f"Staff Engineer, Platform @ lever:{SLUG}", f"Staff Engineer @ lever:{OTHER}",
    ])
    # "unclear location" is the first label of a row nobody places, only while US only is on; never of a placed row.
    assert out["searchUnclear"][0][0] == "unclear location" and "unclear location" not in out["searchUnclear"][1] and "place" not in out["searchUnclear"][2]
    # ONE Assess (of the row's canonical posting) and ONE Mark applied; a copy that has either takes the offer away.
    actions = out["actions"]
    assert actions["assess"] == [True, job_url(SLUG, 7)] and actions["apply"] == [True, {"job_identity": job_url(SLUG, 7), "event_kind": "applied"}]
    assert actions["afterApply"] is False and actions["afterAssess"] is False


def test_us_only_is_its_own_switch_in_the_search(out: dict) -> None:
    queries = out["queries"]
    assert out["boxBefore"] == [False, True], "the box shows the setup's default as soon as the page knows it"
    assert out["same"] == [False, True] and out["clean"]["usOnly"] is None
    # Untouched: no us_only in the request; the answer says it applied.
    assert queries["search"] == "title=staff+engineer&limit=50" and out["firstSent"][0] == queries["search"]
    assert out["firstUsOnly"] == {"on": True, "default": True, "rule": job_copies.US_ONLY_RULE}
    assert out["first"]["rows"] == 4 and out["first"]["box"] is True and "US" in out["first"]["scope"]
    # Show all keeps it: the scope says so, and so would "Show all N".
    assert out["allSent"][0] == queries["searchAll"] == "title=staff+engineer&all=1&limit=50"
    assert out["all"]["rows"] == 4 and out["all"]["scope"] == "US only, any date" and out["all"]["box"] is True
    assert out["all"]["total"] == '4 postings match "staff engineer" (US only, any date).'
    # Unticked: us_only=0, every country; the seven copies abroad are ONE more row (and two more jobs abroad).
    assert out["offSent"][0] == queries["searchAllOff"] == "title=staff+engineer&all=1&us_only=0&limit=50"
    assert out["off"]["rows"] == JOBS and out["off"]["scope"] == "any place, any date" and out["off"]["box"] is False
    assert out["off"]["total"] == '7 postings match "staff engineer" (any place, any date).'
    # Ticked without Show all: us_only=1 beside the default filters, said once.
    assert out["onSent"][-2:] == [queries["searchOn"], queries["searchOnCount"]] and queries["searchOn"] == "title=staff+engineer&us_only=1&limit=50"
    assert out["on"]["rows"] == 4 and out["on"]["box"] is True and out["on"]["scope"].count("US") == 1
    # Clear empties the boxes; the switch stays as it was set.
    assert out["cleared"] == {"form": {"title": "", "company": "", "location": "", "showAll": False, "usOnly": True}, "box": True, "usOnlyDefault": True}


def test_the_wiring_and_the_built_bundle() -> None:
    panel = (UI_SRC / "components" / "FreeSearchPanel.jsx").read_text(encoding="utf-8")
    jobs = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    model = (UI_SRC / "freeSearchModel.js").read_text(encoding="utf-8")
    for needle in ('data-testid="free-search-us-only"', "store.setUsOnly(event.target.checked)", 'data-role="free-search-rules"', 'data-role="search-copies"', "help={HELP.searchUsOnly}", "help={HELP.searchCopies}", "rowLabels(row, names, { usOnly })"):
        assert needle in panel, needle
    for needle in ('data-testid="jobs-us-only"', "usOnly: event.target.checked", 'data-role="jobs-list-rules"', 'data-role="job-copies"', "usOnlyDefault={usOnlyServed ? usOnlyServed.default : undefined}", 'data-testid="unclear-location"', "help={HELP.listUsOnly}", "help={HELP.listCopies}"):
        assert needle in jobs, needle
    # The help says how the two switches relate, in the server's words. 0.1.11.8 N3: each rule is a short line and
    # the whole text behind a "?" (jobsTabsModel.HELP: tests/api_e2e/test_ui_jobs_tabs_model.py).
    assert job_copies.US_ONLY_WITH_SHOW_ALL in model
    tabs = (UI_SRC / "jobsTabsModel.js").read_text(encoding="utf-8")
    assert "searchUsOnly: { label: \"About US only\", paragraphs: [US_ONLY_RULE, US_ONLY_WITH_SHOW_ALL] }" in tabs
    assert "paragraphs: [COPIES_RULE, CANONICAL_RULE, SEARCH_COPIES_RULE]" in tabs and "paragraphs: [COPIES_RULE, CANONICAL_RULE] }" in tabs
    # The box is this view's: nothing of it goes to the browser's storage or to a profile.
    for source in (panel, (UI_SRC / "freeSearchStore.js").read_text(encoding="utf-8")):
        assert "localStorage" not in source and "sessionStorage" not in source
    assert "putProfile" not in jobs and "patchProfile" not in jobs
    dist = UI / "dist" / "assets"
    if not dist.is_dir():
        pytest.skip("ui/dist is not built on this checkout (vite build never ran); nothing is served")
    bundle = "".join(path.read_text(encoding="utf-8") for path in dist.glob("*.js"))
    for words in ("US only", "free-search-us-only", "jobs-us-only", "us_only", "outside the US", "still applies with Show all until you turn it off", "unclear location", "its US posting when it has one, else the earliest posted"):
        assert words in bundle, f"the served ui/dist bundle lacks {words!r} (rebuild ui/dist)"
