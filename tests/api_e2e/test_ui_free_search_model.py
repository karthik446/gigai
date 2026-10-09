"""0.1.11.7 FS2: "Search all jobs", its model and its store run under node.

``ui/src/freeSearchModel.js`` and ``ui/src/freeSearchStore.js`` are plain JavaScript, so this runs them under the
system ``node`` over what the REAL server answers (``GET /api/search`` on a synthetic home), and asserts on the JSON
the script prints. LOUD skip without ``node``. What lives in JSX is pinned by reading the source.

Two node runs: the first prints the queries the model builds; the server answers exactly those; the second drives
the store with a fetcher that hands the server's answers back.

Pinned: the request each box builds (titles split on commas, no profile ever, ``all=1``, the next page's ``offset``,
the count as its own light request); page first and count after, in that order, and a count that fails leaves the
rows; "Load more" adds the next rows and never shows one twice; an answer of an earlier search is dropped; the lines
(shown, total, no match, "Show all N"); a row's labels and its three actions' bodies (the DEFAULT profile's id for
Assess, the job alone for Mark applied); the job page's row of a posting no profile holds; the profile draft.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import re
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

from tests.support.posting_fixtures import SECOND_LABEL, TITLE_BOTH, TITLE_SECOND_ONLY, build_postings_fixture, job_url, lever_job

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"
WATCHED, UNWATCHED = "acme-health", "quiet-harbor"
RECENT, OLD = 58, 3  # quiet-harbor's postings inside and outside the default posted window

QUERIES = """
import * as m from MODEL_URL;
const form = { title: " Staff AI  Engineer ,, Staff Engineer ", company: " quiet ", location: "", showAll: false };
const typed = { title: "staff ai engineer", company: "", location: "", showAll: false };
const out = {
  titles: m.typedTitles(form.title),
  clean: m.cleanForm(form),
  can: [m.canSearch(m.EMPTY_FORM), m.canSearch({ ...m.EMPTY_FORM, title: " , " }), m.canSearch({ ...m.EMPTY_FORM, company: "acme" }), m.canSearch(typed)],
  same: [m.sameSearch(typed, { ...typed, title: " staff ai engineer " }), m.sameSearch(typed, { ...typed, showAll: true })],
  page: m.searchQuery(typed),
  count: m.countQuery(typed),
  all: m.searchQuery({ ...typed, showAll: true }),
  allCount: m.countQuery({ ...typed, showAll: true }),
  allNext: m.searchQuery({ ...typed, showAll: true }, { offset: 50 }),
  words: m.searchQuery(form),
  wordsCount: m.countQuery(form),
  nothing: m.searchQuery({ ...typed, title: "harbor pilot" }),
  nothingCount: m.countQuery({ ...typed, title: "harbor pilot" }),
};
console.log(JSON.stringify(out));
"""

DRIVE = """
import * as m from MODEL_URL;
import { createFreeSearchStore, dropProfileDraft, keepProfileDraft, pendingProfileDraft } from STORE_URL;

const data = DATA;
const sent = [];
let failCount = false;
let hold = null; // {query, release}: an answer kept back until released
const fetchSearch = (query) => {
  sent.push(query);
  if (failCount && query.includes("count=1")) {
    return Promise.reject(new Error("the count failed"));
  }
  const answer = data.answers[query];
  if (!answer) {
    return Promise.reject(new Error("no answer for " + query));
  }
  if (hold && hold.query === query) {
    return new Promise((resolve) => { hold.release = () => resolve(answer); });
  }
  return Promise.resolve(answer);
};
const store = createFreeSearchStore({ fetchSearch });
const seen = [];
store.subscribe(() => {
  const state = store.getState();
  seen.push({ loading: state.loading, counting: state.counting, rows: state.results ? state.results.rows.length : null, total: state.results ? state.results.total : null });
});
const out = {};
const lines = () => {
  const { results } = store.getState();
  return { shown: m.shownLine(results), total: m.totalLine(results), empty: m.noMatchLine(results), hidden: m.hiddenLabel(results), read: results.read, more: results.more, rows: results.rows.length, scope: m.scopeText(results) };
};

// Nothing typed: nothing is asked.
await store.search();
out.nothingTyped = sent.length;

// Page first, count after.
store.setForm({ title: " staff ai  engineer " });
await store.search();
out.firstSent = sent.slice();
out.firstSeen = seen.slice();
out.first = lines();
out.defaultsText = store.getState().defaultsText;
out.switchLabel = [m.showAllLabel(null), m.showAllLabel(store.getState().defaultsText)];
const results = store.getState().results;
const names = m.profileNames(results, data.profiles);
out.order = results.rows.map((row) => row.posted);
out.labels = Object.fromEntries(results.rows.map((row) => [row.job_identity, m.rowLabels(row, names).map((label) => label.label)]));
out.labelKinds = Object.fromEntries(results.rows.map((row) => [row.job_identity, m.rowLabels(row, names).map((label) => label.kind)]));

// The row's actions.
const profile = m.defaultProfileOf(data.profiles);
const byJob = Object.fromEntries(results.rows.map((row) => [row.job_identity, row]));
const held = byJob[data.jobs.held];
const unheld = byJob[data.jobs.unheld];
const applied = byJob[data.jobs.applied];
out.defaultProfile = profile.profile_id;
out.assessLabel = m.assessLabel(profile);
out.assess = { held: m.assessRequest(held, profile), unheld: m.assessRequest(unheld, profile) };
out.canAssess = [m.canAssess(held, profile), m.canAssess(held, null), m.canAssess({ ...held, removed: true }, profile), m.canAssess(m.assessedRow(held, { job_state: { state: "needs_answers" } }, profile), profile),
  m.canAssess({ ...held, assessment: { state: "matched", profile_id: data.secondId } }, profile)];
out.assessedLabels = m.rowLabels(m.assessedRow(unheld, { job_state: { state: "needs_answers" } }, profile), names).map((label) => label.label);
out.apply = { can: [m.canApply(unheld), m.canApply(applied), m.canApply({ ...unheld, job_identity: null })], body: m.applyRequest(unheld),
  after: m.rowLabels(m.appliedRow(unheld, "2026-10-06T10:00:00Z"), names).map((label) => label.label) };
out.jobPageRow = { held: m.unheldPostingRow(held), unheld: m.unheldPostingRow(unheld) };
out.jobId = [m.rowJobId(held), m.rowJobId({ job_identity: null, job_url: "https://example.test/j/1" }), m.rowJobId({})];
// The same row from the by-address job read, only when it says the company index answered.
const servedPosting = { job_identity: "https://jobs.example.test/a/1", normalized_url: "https://jobs.example.test/a/1", source_url: "https://jobs.example.test/a/1?src=x", fetch_kind: "company_index",
  title: "Staff AI Engineer", company: "Example Co", location: "Remote", published_at: "2026-10-01T00:00:00Z", published_kind: "posted", first_seen_at: "2026-10-02T00:00:00Z", removed_at: null, text: null };
out.servedRow = [m.servedPostingRow({ job_identity: servedPosting.job_identity, posting: servedPosting }), m.servedPostingRow({ job_identity: "x", posting: { ...servedPosting, fetch_kind: "ats_board" } }),
  m.servedPostingRow({ job_identity: "x", posting: null }), m.servedPostingRow(null)];
store.changeRow(m.appliedRow(unheld, "2026-10-06T10:00:00Z"));
out.changed = store.getState().results.rows.filter((row) => row.application && row.job_identity === data.jobs.unheld).length;

// Show all: a new search, its count, then Load more twice.
sent.length = 0;
await store.setShowAll(true);
out.allSent = sent.slice();
out.all = lines();
out.allDefaultsText = store.getState().defaultsText;
sent.length = 0;
await store.loadMore();
out.moreSent = sent.slice();
out.more = lines();
const jobs = store.getState().results.rows.map((row) => row.job_url);
out.unique = new Set(jobs).size === jobs.length;
out.allOrder = store.getState().results.rows.map((row) => row.posted);
await store.loadMore();
out.noMore = sent.length;

// A count that fails leaves the rows.
failCount = true;
await store.setShowAll(false);
out.countFailed = { ...lines(), counting: store.getState().counting, error: store.getState().error };
failCount = false;

// An earlier search's answer is dropped: the page of "staff ai engineer" is held, "harbor pilot" answers first.
hold = { query: data.queries.page };
const slow = store.search();
store.setForm({ title: "harbor pilot" });
const held2 = hold;
hold = null;
await store.search();
held2.release();
await slow;
out.latest = { ...lines(), title: store.getState().results.form.title };

// The profile draft of a search, and of one with Show all and words.
store.setForm({ title: "Staff AI Engineer, Staff Engineer", company: "quiet", showAll: false });
await store.search();
out.wordsLines = lines();
const draft = m.profileDraft(store.getState().results);
out.draft = draft;
out.draftLine = m.draftSettingsLine(draft);
out.draftAll = m.draftSettingsLine(m.profileDraft({ ...store.getState().results, filters: null, form: { ...store.getState().results.form, company: "" } }));
out.canSave = [m.canSaveAsProfile(store.getState().results), m.canSaveAsProfile({ form: m.cleanForm({ company: "acme" }) }), m.canSaveAsProfile(null)];
keepProfileDraft(draft);
out.kept = [pendingProfileDraft() === draft, (dropProfileDraft(), pendingProfileDraft())];

// Errors.
out.errors = [m.errorText({ code: "config_unavailable", message: "x" }), m.errorText({ code: "invalid_value", detail: "limit must be 1..200", message: "generic" }), m.errorText(new Error("boom"))];
store.clear();
out.cleared = { results: store.getState().results, form: store.getState().form, defaultsText: store.getState().defaultsText };
console.log(JSON.stringify(out));
"""


def _node(script: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the free search model was NOT run")
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def _urls(script: str) -> str:
    return script.replace("MODEL_URL", json.dumps((UI_SRC / "freeSearchModel.js").resolve().as_uri())).replace("STORE_URL", json.dumps((UI_SRC / "freeSearchStore.js").resolve().as_uri()))


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from gigai.scout.find_jobs import search_index
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    queries = _node(_urls(QUERIES))
    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    now = datetime.now(UTC)
    seen = now - timedelta(minutes=30)
    fx.seed(
        WATCHED,
        [lever_job(WATCHED, n, title=TITLE_BOTH, created=now - timedelta(hours=n)) for n in (1, 2, 3)] + [lever_job(WATCHED, 4, title=TITLE_SECOND_ONLY, created=now - timedelta(hours=4))],
        seen_at=seen,
    )
    fx.seed(
        UNWATCHED,
        [lever_job(UNWATCHED, n, title=TITLE_BOTH, created=now - timedelta(hours=10 + n)) for n in range(1, RECENT + 1)]
        + [lever_job(UNWATCHED, RECENT + n, title=TITLE_BOTH, created=now - timedelta(days=400 + n)) for n in range(1, OLD + 1)],
        seen_at=seen, watch=False,
    )
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path: str) -> dict:
        with urllib.request.urlopen(url + path, timeout=60) as response:
            return json.loads(response.read())

    try:
        assert len(get("/api/postings?limit=50")["postings"]["rows"]) == 4  # the two profiles' lists exist
        applied = job_url(WATCHED, 2)
        request = urllib.request.Request(url + "/api/applications", data=json.dumps({"job_identity": applied, "event_kind": "applied"}).encode(), headers={"Content-Type": "application/json", "Origin": url})
        with urllib.request.urlopen(request, timeout=60) as response:
            assert response.status == 201
        assert search_index.rebuild_from_index(fx.home_root).available
        asked = [value for key, value in queries.items() if key not in ("titles", "clean", "can", "same")]
        answers = {query: get(f"/api/search?{query}") for query in asked}
        profiles = get("/api/profiles")["profiles"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
        search_index.close(fx.home_root)
    data = {
        "answers": answers, "profiles": profiles, "queries": queries, "secondId": fx.second_profile_id,
        "jobs": {"held": job_url(WATCHED, 1), "applied": applied, "unheld": job_url(UNWATCHED, 1)},
    }
    result = _node(_urls(DRIVE).replace("DATA", json.dumps(data)))
    result["queries"], result["ids"] = queries, {"default": fx.default_profile_id, "second": fx.second_profile_id}
    result["default_label"] = next(item["label"] for item in profiles if item["is_default"])
    return result


def test_the_requests_the_boxes_build(out: dict) -> None:
    queries = out["queries"]
    assert queries["titles"] == ["Staff AI Engineer", "Staff Engineer"], "a comma separates titles; spaces are squeezed"
    assert queries["clean"] == {"title": "Staff AI Engineer, Staff Engineer", "company": "quiet", "location": "", "showAll": False, "usOnly": None}
    assert queries["can"] == [False, False, True, True], "a company word alone can be searched; commas alone cannot"
    assert queries["same"] == [True, False]
    assert queries["page"] == "title=staff+ai+engineer&limit=50"
    assert queries["count"] == "title=staff+ai+engineer&limit=1&count=1", "the count is its own light request"
    assert queries["all"] == "title=staff+ai+engineer&all=1&limit=50"
    assert queries["allNext"] == "title=staff+ai+engineer&all=1&limit=50&offset=50"
    assert queries["words"] == "title=Staff+AI+Engineer%2C+Staff+Engineer&company=quiet&limit=50"
    assert all("profile" not in query for query in queries.values() if isinstance(query, str)), "the search never names a profile"
    assert out["nothingTyped"] == 0, "nothing typed: nothing is asked"


def test_the_page_comes_first_and_the_count_after(out: dict) -> None:
    queries = out["queries"]
    total = 3 + RECENT
    assert out["firstSent"] == [queries["page"], queries["count"]]
    # What the page showed, in order: searching, the 50 rows with no total and the count on its way, then the total.
    states = [(item["loading"], item["counting"], item["rows"], item["total"]) for item in out["firstSeen"]]
    assert (True, False, None, None) in states
    rows_at = states.index((False, True, 50, None))
    assert states[-1] == (False, False, 50, total) and rows_at < len(states) - 1 and all(state[3] is None for state in states[:-1])
    first = out["first"]
    scope = out["defaultsText"]
    assert scope and scope == first["scope"] and "last" in scope
    assert first == {
        "shown": f'Showing 1-50, newest first: "staff ai engineer" ({scope}).',
        "total": f'{total} postings match "staff ai engineer" ({scope}).',
        "empty": None,
        # 0.1.11.8: this fixture is a US setup, so US only is on (nobody touched its box) and Show all keeps it.
        "hidden": f"Show all {total + OLD} (US only, any date)",
        "read": 50, "more": True, "rows": 50, "scope": scope,
    }
    assert out["order"] == sorted(out["order"], reverse=True), "newest posted first, as the server gave them"
    assert out["switchLabel"][0].startswith("Show all (any place, any date") and out["switchLabel"][1] == f"Show all (drops the default filters: {scope})"


def test_show_all_load_more_a_failed_count_and_the_newest_search_wins(out: dict) -> None:
    queries = out["queries"]
    total = 3 + RECENT + OLD
    assert out["allSent"] == [queries["all"], queries["allCount"]]
    assert out["all"]["total"] == f'{total} postings match "staff ai engineer" (US only, any date).'
    assert out["all"]["hidden"] is None and out["all"]["scope"] == "US only, any date" and out["all"]["rows"] == 50
    assert out["allDefaultsText"] == out["defaultsText"], "the switch still says what it drops"
    assert out["moreSent"] == [queries["allNext"]], "the next 50, from the rows read so far; no second count"
    assert out["more"]["rows"] == total and out["more"]["read"] == total and out["more"]["more"] is False and out["unique"]
    assert out["more"]["shown"].startswith(f"Showing 1-{total}, newest first:") and out["more"]["total"].startswith(f"{total} postings match")
    assert out["allOrder"] == sorted(out["allOrder"], reverse=True)
    assert out["noMore"] == 1, "no page is asked for when there is none"
    failed = out["countFailed"]
    assert failed["rows"] == 50 and failed["total"] is None and failed["counting"] is False and failed["error"] is None
    latest = out["latest"]
    assert latest["title"] == "harbor pilot" and latest["rows"] == 0 and latest["shown"] is None
    assert latest["empty"].startswith('No stored posting matches "harbor pilot" (') and latest["total"] == latest["empty"]


def test_a_rows_labels_and_what_its_actions_send(out: dict) -> None:
    ids, label = out["ids"], out["default_label"]
    held, applied, unheld = job_url(WATCHED, 1), job_url(WATCHED, 2), job_url(UNWATCHED, 1)
    both = [f"in: {label}", f"in: {SECOND_LABEL}"]
    assert out["labels"][held] == both and out["labelKinds"][held] == ["profile", "profile"]
    assert out["labels"][applied][:2] == both and re.fullmatch(r"Applied · \w{3} \d{1,2}", out["labels"][applied][2]) and out["labelKinds"][applied][2] == "application"
    assert out["labels"][unheld] == [], "a posting no profile holds has no label"
    # Assess: always as the DEFAULT profile, said on the button.
    assert out["defaultProfile"] == ids["default"] and out["assessLabel"] == f"Assess · 1 model call · as {label}"
    assert out["assess"]["held"] == {"job": {"job_url": held}, "resume": {"profile_id": ids["default"]}, "origin": "job_page"}
    assert out["assess"]["unheld"] == {"job": {"job_url": unheld}, "resume": {"profile_id": ids["default"]}, "origin": "quick_assess"}, "no list holds it: listed under Assessments"
    assert out["canAssess"] == [True, False, False, False, True], "not without a default profile, not a removed posting, not one the default profile assessed"
    assert out["assessedLabels"] == ["assessed: needs answers"]
    # Mark applied: the job alone.
    assert out["apply"]["can"] == [True, False, False] and out["apply"]["body"] == {"job_identity": unheld, "event_kind": "applied"}
    assert out["apply"]["after"] == ["Applied · Oct 6"] and out["changed"] == 1
    # The job page's row: only for a posting no profile holds (a held one reads its own row by its address).
    assert out["jobPageRow"]["held"] is None
    row = out["jobPageRow"]["unheld"]
    assert row["job_identity"] == unheld and row["job_url"] == unheld and row["title"] == TITLE_BOTH and row["from_search"] is True
    assert row["location"] == "Remote - United States" and row["published_at"] and row["profiles"] == [] and row["description"] is None
    assert out["jobId"] == [held, "https://example.test/j/1", None]
    # A page opened by its address: the row comes from the job read only when the company index answered it.
    served = out["servedRow"]
    assert served[1:] == [None, None, None], "a run's, an assessment's or a list's posting is never rebuilt as a search row"
    assert served[0] == {
        "job_identity": "https://jobs.example.test/a/1", "job_url": "https://jobs.example.test/a/1?src=x", "title": "Staff AI Engineer", "company": "Example Co",
        "location": "Remote", "published_at": "2026-10-01T00:00:00Z", "published_kind": "posted", "first_seen": "2026-10-02T00:00:00Z", "removed_at": None,
        "profiles": [], "application": None, "description": None, "from_search": True,
    }


def test_the_profile_draft_and_the_errors(out: dict) -> None:
    scope = out["defaultsText"]
    assert out["wordsLines"]["rows"] == 50 and "company quiet" in out["wordsLines"]["shown"]
    draft = out["draft"]
    assert draft["label"] == "Staff AI Engineer" and draft["titles"] == ["Staff AI Engineer", "Staff Engineer"]
    assert draft["settingsText"] == scope and draft["showAll"] is False and draft["wordsLeftOut"] == "quiet"
    assert draft["note"] == "The profile matches by the profile rule, which can list more than this search."
    assert out["draftLine"] == f"The profile starts with this search's settings: {scope}. The company and location words of the search (quiet) are not part of a profile."
    assert out["draftAll"].startswith("This search showed every posting (Show all). The profile starts with the default profile's")
    assert out["canSave"] == [True, False, False], "a search with no title has nothing to save as a profile"
    assert out["kept"] == [True, None], "the draft is handed over once"
    assert "Show all" in out["errors"][0] and out["errors"][1] == "limit must be 1..200" and out["errors"][2] == "boom"
    assert out["cleared"]["results"] is None and out["cleared"]["form"] == {"title": "", "company": "", "location": "", "showAll": False, "usOnly": None}
    assert out["cleared"]["defaultsText"] == scope


def test_the_jobs_page_wiring_and_the_built_bundle() -> None:
    panel = (UI_SRC / "components" / "FreeSearchPanel.jsx").read_text(encoding="utf-8")
    jobs = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    profiles = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert "<FreeSearchPanel profiles={allProfiles}" in jobs and "unheldPostingRow(row)" in jobs
    host = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert "getJob(jobRouteId).then((served) =>" in host and "servedPostingRow(served)" in host, "a miss of the list asks the by-address job read"
    assert 'request("GET", `/api/search?${query}`' in api
    # The search never switches the selected profile, and stores nothing in the browser.
    assert "onSelectProfile" not in panel
    store = (UI_SRC / "freeSearchStore.js").read_text(encoding="utf-8")
    for source in (panel, store):
        assert "localStorage" not in source and "sessionStorage" not in source
    assert "dangerouslySetInnerHTML" not in panel
    for needle in ('data-testid="free-search-title"', 'data-testid="free-search-show-all"', 'data-testid="free-search-more"', 'data-testid="save-as-profile"', 'data-action="search-assess-confirm"'):
        assert needle in panel, needle
    assert "pendingProfileDraft()" in profiles and 'data-testid="profile-from-search"' in profiles
    dist = UI / "dist" / "assets"
    if not dist.is_dir():
        pytest.skip("ui/dist is not built on this checkout (vite build never ran); nothing is served")
    bundle = "".join(path.read_text(encoding="utf-8") for path in dist.glob("*.js"))
    for words in ("Search all jobs", "Not ranked. Save as a profile to rank.", "Save this search as a profile", "/api/search?", "The profile matches by the profile rule, which can list more than this search."):
        assert words in bundle, f"the served ui/dist bundle lacks {words!r} (rebuild ui/dist)"
