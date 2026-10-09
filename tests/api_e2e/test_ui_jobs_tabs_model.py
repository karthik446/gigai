"""0.1.11.8 N3: the Jobs page's two tabs and the help behind each "?", in the UI's models, run under node.

``ui/src/jobsTabsModel.js`` and ``ui/src/routing.js`` are plain JavaScript, so this runs them under the system
``node`` and asserts on the JSON the script prints. LOUD skip without ``node``. ``routing.js`` is run from a copy
with its ``react`` import (used by ``useHashRoute`` only) left out; everything called is the file's own text. What
lives in JSX is pinned by reading the source. Hermetic: no server, no home, files under ``src/gigai/scout/ui`` only.

Pinned:

- the router: ``#/jobs`` (with or without its query) is the Jobs page on "Your jobs"; ``#/jobs/search`` is the same
  page on "Search"; a job page's address (``#/jobs/<encoded address>``, also one whose words start with "search")
  is still a job page, so the tab's address does not collide with a job's;
- the tabs: their labels, where each link goes (Search: ``#/jobs/search``; Your jobs: the list as it was left), the
  key that moves from one to the other, and the job page's way back (the tab the job was opened from);
- the help: each short line is short, and the whole text behind its "?" is the rule's own sentences, the ones the
  server says (``job_copies``);
- the wiring: one ``FreeSearchPanel`` on the Search tab only, role=tablist / tab / tabpanel with aria-selected, a
  "?" that is a button with aria-expanded, and no storage; the built bundle carries the tab and the short lines.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs import job_copies
from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"
REACT_IMPORT = 'import { useEffect, useState } from "react";'
JOB_MODEL_IMPORT = 'from "./jobModel.js";'
JOB = "https://jobs.example.test/acme/search?team=search"

SCRIPT = """
import * as t from TABS_URL;
import * as r from ROUTING_URL;

const out = {};
const job = JOB;
const route = (hash) => { const found = r.parseHash(hash); return [found.view, found.params, found.known]; };
out.routes = {
  bare: route("#/jobs"), slash: route("#/jobs/"), query: route("#/jobs?us=0&page=2"),
  search: route("#/jobs/search"), searchSlash: route("#/jobs/search/"), searchQuery: route("#/jobs/search?x=1"),
  job: route(r.jobHash(job)), searchWord: route("#/jobs/searching"), searchChild: route("#/jobs/search/more"),
  empty: route(""), unknown: route("#/nowhere"),
};
out.jobHash = r.jobHash(job);
out.hashes = [r.JOBS_HASH, t.SEARCH_TAB_HASH];
out.tabs = t.JOBS_TABS;
out.tabOf = [t.tabOf("search"), t.tabOf(undefined), t.tabOf("anything")];
out.tabOfHash = ["#/jobs", "#/jobs?us=0", "#/jobs/search", "#/jobs/search/", "#/jobs/search?x=1", r.jobHash(job), "#/jobs/searching", "", null].map(t.tabOfHash);
out.tabHash = [
  t.tabHash("search", "#/jobs?page=3"), t.tabHash("yours", "#/jobs?page=3&us=0"), t.tabHash("yours", "#/jobs"), t.tabHash("yours"),
  t.tabHash("yours", "#/jobs/search"), t.tabHash("yours", "#/settings"),
];
out.keys = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End", "Enter", "a"].map((key) => [t.tabForKey("yours", key), t.tabForKey("search", key)]);
out.back = [t.backToJobs(), (t.rememberTab("search"), t.backToJobs()), (t.rememberTab("yours"), t.backToJobs()), (t.rememberTab(undefined), t.backToJobs())];
out.short = { usOnly: t.US_ONLY_SHORT, copies: t.COPIES_SHORT, search: t.SEARCH_SHORT };
out.help = t.HELP;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the Jobs tabs model was NOT run")
    source = (UI_SRC / "routing.js").read_text(encoding="utf-8")
    assert source.count(REACT_IMPORT) == 1 and source.count(JOB_MODEL_IMPORT) == 1, "routing.js's imports changed: update this test"
    source = source.replace(REACT_IMPORT, "const useEffect = () => {}, useState = () => [];")
    source = source.replace(JOB_MODEL_IMPORT, f"from {json.dumps((UI_SRC / 'jobModel.js').resolve().as_uri())};")
    routing = tmp_path_factory.mktemp("jobs-tabs") / "routing.mjs"
    routing.write_text(source, encoding="utf-8")
    script = SCRIPT.replace("TABS_URL", json.dumps((UI_SRC / "jobsTabsModel.js").resolve().as_uri()))
    script = script.replace("ROUTING_URL", json.dumps(routing.as_uri())).replace("JOB;", json.dumps(JOB) + ";")
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_router_tells_the_search_tab_from_a_job_page(out: dict) -> None:
    routes = out["routes"]
    # Your jobs: the plain address, and the one with the list's page and filters.
    assert routes["bare"] == routes["slash"] == routes["query"] == routes["empty"] == ["jobs", {}, True]
    # Search: the same page (the top bar's "Jobs" stays current), its tab named.
    assert routes["search"] == routes["searchSlash"] == routes["searchQuery"] == ["jobs", {"tab": "search"}, True]
    # A job page is its address, percent-encoded: never the bare word, whatever words the address holds.
    assert out["jobHash"] == "#/jobs/https%3A%2F%2Fjobs.example.test%2Facme%2Fsearch%3Fteam%3Dsearch"
    assert routes["job"] == ["job", {"jobId": JOB}, True]
    assert routes["searchWord"] == ["job", {"jobId": "searching"}, True] and routes["searchChild"] == ["job", {"jobId": "search/more"}, True]
    assert routes["unknown"] == ["jobs", {}, False]
    assert out["hashes"] == ["#/jobs", "#/jobs/search"]


def test_the_two_tabs_their_links_and_their_keys(out: dict) -> None:
    assert [(tab["tab"], tab["label"]) for tab in out["tabs"]] == [("yours", "Your jobs"), ("search", "Search")]
    ids = [value for tab in out["tabs"] for value in (tab["id"], tab["panelId"])]
    assert len(set(ids)) == 4 and all(ids), "each tab and each panel has an id of its own (aria-controls, aria-labelledby)"
    assert out["tabOf"] == ["search", "yours", "yours"]
    assert out["tabOfHash"] == ["yours", "yours", "search", "search", "search", "yours", "yours", "yours", "yours"]
    # Search has one address; Your jobs goes back to the list as it was left, and never to another page's address.
    assert out["tabHash"] == ["#/jobs/search", "#/jobs?page=3&us=0", "#/jobs", "#/jobs", "#/jobs", "#/jobs"]
    # The arrows, Home and End move between the two (the ends wrap); no other key does.
    assert out["keys"] == [["search", "yours"], ["search", "yours"], ["search", "yours"], ["search", "yours"], ["yours", "yours"], ["search", "search"], [None, None], [None, None]]
    # A job page's way back: the tab the job was opened from (in memory: a reload forgets it).
    yours, search = {"hash": "#/jobs", "tab": "yours"}, {"hash": "#/jobs/search", "tab": "search"}
    assert out["back"] == [yours, search, yours, yours]


def test_each_help_is_a_short_line_and_the_whole_rule(out: dict) -> None:
    short, help_ = out["short"], out["help"]
    assert short["usOnly"] == "Hides postings clearly outside the US; unclear places stay listed."
    assert short["copies"] == "One row per job: the same job posted in several places is one row."
    assert short["search"] == "Searches every stored posting, newest first. A comma separates titles."
    assert all(len(line) <= 75 for line in short.values()), "a short line is one line"
    # The whole text is the rule in the server's words.
    assert help_["listUsOnly"] == {"label": "About US only", "paragraphs": [job_copies.US_ONLY_RULE]}
    assert help_["searchUsOnly"] == {"label": "About US only", "paragraphs": [job_copies.US_ONLY_RULE, job_copies.US_ONLY_WITH_SHOW_ALL]}
    assert help_["listCopies"]["paragraphs"][0] == job_copies.COPIES_RULE and len(help_["listCopies"]["paragraphs"]) == 2
    assert help_["searchCopies"]["paragraphs"] == help_["listCopies"]["paragraphs"] + ["The counts are rows."]
    assert "its US posting when it has one, else the earliest posted" in help_["listCopies"]["paragraphs"][1]
    rules = " ".join(help_["searchRules"]["paragraphs"])
    for words in (
        "in a profile's list or in none", "every word of a typed title must be in the posting's title", "whole words of the company's name or the posting's location",
        "work mode, countries and posted window apply until you turn on Show all", "Newest posted first. Not ranked. Save as a profile to rank.", "a search stores nothing",
    ):
        assert words in rules, words
    assert all(entry["label"] and entry["paragraphs"] and len(set(entry["paragraphs"])) == len(entry["paragraphs"]) for entry in help_.values())


def test_the_wiring_and_the_built_bundle() -> None:
    jobs = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    host = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    tabs = (UI_SRC / "components" / "JobsTabs.jsx").read_text(encoding="utf-8")
    tip = (UI_SRC / "components" / "HelpTip.jsx").read_text(encoding="utf-8")
    panel = (UI_SRC / "components" / "FreeSearchPanel.jsx").read_text(encoding="utf-8")
    job_page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    routing = (UI_SRC / "routing.js").read_text(encoding="utf-8")
    # The tab comes from the address; the search is drawn on the Search tab alone, the list's box on the other.
    assert 'pattern: /^#\\/jobs(?:\\/(search))?\\/?(?:\\?.*)?$/, param: "tab" }' in routing
    assert routing.index('{ view: "jobs",') < routing.index('{ view: "job",'), "the tab's entry is read before the job page's"
    assert "tab={tabOf(route.params.tab)}" in host
    assert jobs.count("<FreeSearchPanel") == 1 and jobs.count('id="jobs-filter-search"') == 1 and jobs.count("<JobsTabs tab={tab}") == 2
    search_branch = jobs[jobs.index("if (onSearch) {\n    return (") : jobs.index("  return (\n    <div>\n      <JobsTabs")]
    assert "<FreeSearchPanel" in search_branch and "jobs-filter-search" not in search_branch and 'data-testid="jobs-list"' not in search_branch
    assert jobs.count('role="tabpanel"') == 2 and "aria-labelledby={searchTab.id}" in jobs and "aria-labelledby={yoursTab.id}" in jobs
    # On Search the list's moves do not write the address.
    assert "if (tabRef.current === TAB_SEARCH) {\n        adopt(next);" in jobs
    for needle in ('role="tablist"', 'role="tab"', "aria-selected={selected}", "aria-controls={selected ? entry.panelId : undefined}", "tabIndex={selected ? 0 : -1}", "href={tabHash(entry.tab, listHash)}", "tabForKey(tab, event.key)"):
        assert needle in tabs, needle
    # The "?": a button that says whether it is open, closed by Escape; nothing kept in the browser.
    for needle in ('type="button"', "aria-expanded={open}", "aria-controls={open ? id : undefined}", "aria-label={help.label}", 'event.key === "Escape"', "{open && ("):
        assert needle in tip, needle
    for source in (tabs, tip, (UI_SRC / "jobsTabsModel.js").read_text(encoding="utf-8")):
        assert "localStorage" not in source and "sessionStorage" not in source and "dangerouslySetInnerHTML" not in source
    for needle in ("help={HELP.searchRules}", "help={HELP.searchUsOnly}", "help={HELP.searchCopies}", "{SEARCH_SHORT}", "{US_ONLY_SHORT}", "{COPIES_SHORT}"):
        assert needle in panel, needle
    assert "href={back.hash}" in job_page and '"← Search" : "← Jobs"' in job_page
    # No new dependency: React and the build tool, as before.
    package = json.loads((UI / "package.json").read_text(encoding="utf-8"))
    assert sorted(package["dependencies"]) == ["react", "react-dom"] and sorted(package["devDependencies"]) == ["@vitejs/plugin-react", "vite"]
    dist = UI / "dist" / "assets"
    if not dist.is_dir():
        pytest.skip("ui/dist is not built on this checkout (vite build never ran); nothing is served")
    bundle = "".join(path.read_text(encoding="utf-8") for path in dist.glob("*.js"))
    for words in ("Your jobs", "#/jobs/search", "jobs-tab-", "tablist", "help-tip", "Hides postings clearly outside the US; unclear places stay listed.", "One row per job: the same job posted in several places is one row.", "About this search"):
        assert words in bundle, f"the served ui/dist bundle lacks {words!r} (rebuild ui/dist)"
    styles = "".join(path.read_text(encoding="utf-8") for path in dist.glob("*.css"))
    assert ".jobs-tab" in styles and ".help-tip-body" in styles, "the served ui/dist stylesheet lacks the tabs (rebuild ui/dist)"
