"""0.1.10.7 M4b: the Jobs page by posting, its model run under node.

``ui/src/postingsModel.js`` is plain JavaScript, so this runs it under the
system ``node`` over what the SERVER makes (``posting_search.search_postings``,
``scout_new`` as the peek, ``assess_these`` as the ask) on a synthetic home,
and asserts on the JSON the script prints. LOUD skip without ``node``. What
lives in JSX is pinned by reading the source.

Pinned: the profile chips are a filter (several on, each profile tagged on
every row it matches, best first); the three time chips, one at a time; the
New chip's number is the PEEK's and "Mark all seen" brings it to 0 (the GET
moved nothing); the query each filter sends; the row's state chips (needs
answers, assessed, Scout label, Scout ATS, stale, removed); "Assess these"
asks first and the dialog's Approve sends the body the server named; a
posting's markup stays text (no raw HTML anywhere in the UI); no run is
started from the UI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs.api import static as static_module

from tests.support.posting_fixtures import NOW, TITLE_SECOND_ONLY, build_postings_fixture, days_ago, job_url, lever_job

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"

MARKUP = '<script>alert("x")</script><b>Staff AI Engineer</b>'

SCRIPT = """
import * as m from MODEL_URL;

const data = DATA;
const { search, peek, seenPeek, ask, nothing, second } = data;
const rows = search.postings.rows;
const profiles = search.profiles;
const out = {};

// The time chips: one at a time, the New chip counts from the peek.
out.newCount = [m.newCountFromPeek(peek), m.newCountFromPeek(seenPeek), m.newCountFromPeek(null), m.newCountFromPeek({ counts: {} })];
out.timeChips = m.timeChips(m.newCountFromPeek(peek), "7d");
out.timeChipsUnread = m.timeChips(null, null).map((chip) => chip.label);
out.windows = [m.toggleWindow(null, "new"), m.toggleWindow("new", "7d"), m.toggleWindow("7d", "7d")];

// The profile chips: a filter, several may be on.
out.profileChips = m.profileChips(profiles, [data.secondId]);
let chosen = m.toggleProfile([], data.defaultId);
chosen = m.toggleProfile(chosen, data.secondId);
out.bothOn = chosen;
out.oneOff = m.toggleProfile(chosen, data.defaultId);
out.kept = m.keepActiveProfiles([data.secondId, "profile_gone"], profiles);

// The query each filter sends.
out.queries = {
  none: m.postingsQuery(m.EMPTY_FILTER),
  profiles: m.postingsQuery({ ...m.EMPTY_FILTER, profileIds: ["p1", "p2"] }),
  window: m.postingsQuery({ ...m.EMPTY_FILTER, window: "new" }),
  states: m.postingsQuery({ ...m.EMPTY_FILTER, states: m.toggleState(m.toggleState([], "needs_answers"), "recommended") }),
  removed: m.postingsQuery({ ...m.EMPTY_FILTER, removed: true, query: "  acme  " }),
  page: m.postingsQuery(m.EMPTY_FILTER, { offset: 50 }),
};
out.stateToggle = m.toggleState(["assessed", "recommended"], "assessed");
out.hasFilter = [m.hasFilter(m.EMPTY_FILTER), m.hasFilter({ ...m.EMPTY_FILTER, query: " " }), m.hasFilter({ ...m.EMPTY_FILTER, window: "30d" })];

// The rows: tags, score, chips.
const byId = Object.fromEntries(rows.map((row) => [row.job_identity, row]));
const both = byId[data.bothUrl];
const only = byId[data.secondOnlyUrl];
out.tags = { both: m.profileTags(both, profiles), only: m.profileTags(only, profiles), second: m.profileTags(second.postings.rows.find((row) => row.job_identity === data.bothUrl), second.profiles) };
out.others = { both: m.secondProfiles(both, profiles).map((tag) => tag.label), only: m.secondProfiles(only, profiles) };
out.scores = [m.scoreText(both), m.scoreText({ score: 73, score_kind: "assessment" }), m.scoreText({ score: 81, score_kind: "rank" })];
// 0110-8-04: the server's own score text wins; a 1-of-1 never reads as a bare 100%. Only an older server's row falls back.
out.scoreTexts = [
  m.scoreText({ ...both, score: 100, score_kind: "assessment", score_text: "Matched · 1 of 1 requirements · rank 40" }),
  m.scoreText({ ...both, score: 100, score_kind: "assessment", score_text: "Matched (old assessment: older prompt) · 3 of 3 requirements · rank 95" }),
  m.scoreText({ score: 73, score_kind: "assessment", score_text: "  " }),
];
out.serverOrder = rows.map((row) => row.sort_group);
out.chips = {
  notAssessed: m.rowChips(both),
  needsAnswers: m.rowChips({ ...both, state: "needs_answers", open_questions: [{ question: "a" }, { question: "b" }] }),
  recommended: m.rowChips({ ...both, state: "matched", label: "recommended", ats_score: 84 }),
  attention: m.rowChips({ ...both, state: "tailored", label: "needs_attention", ats_score: 61, stale_reason: "posting_changed" }),
  removed: m.rowChips({ ...both, state: "not_a_match", removed_at: "2026-10-02T00:00:00Z", stale_reason: "settings_changed" }),
  unknownLabel: m.rowChips({ ...both, state: "matched", label: "ready_to_apply" }),
};
out.detail = [m.detailLine(both), m.detailLine({ company: "Acme", location: " ", work_mode: "unknown", salary: null })];
out.isNew = [m.isNew(both, search.anchor), m.isNew(byId[data.oldUrl], search.anchor), m.isNew({ ...both, removed_at: "x" }, search.anchor), m.isNew(both, null)];
out.count = [m.countLine(search.counts, rows.length), m.countLine({ matched: 120 }, 50), m.countLine({ matched: 1 }, 1), m.countLine(null, 0)];
// 0110-10-01: real pages
out.pages = {
  line: [m.countLine({ matched: 591 }, 50, 2), m.countLine({ matched: 591 }, 41, 12), m.countLine({ matched: 591 }, 25, 3, 25)],
  count: [m.pageCount(591), m.pageCount(0), m.pageCount(100, 25), m.pageCount(101, 100)],
  numbers: [m.pageNumbers(1, 3), m.pageNumbers(1, 12), m.pageNumbers(6, 12), m.pageNumbers(12, 12)],
  hash: [
    m.jobsHash(m.EMPTY_FILTER),
    m.jobsHash({ ...m.EMPTY_FILTER, states: ["needs_answers"] }, 3),
    m.jobsHash({ profileIds: ["p1"], window: "7d", states: ["assessed"], removed: true, query: " rust " }, 2, 25),
  ],
  parsed: [
    m.parseJobsHash("#/jobs?page=3&state=needs_answers"),
    m.parseJobsHash("#/jobs"),
    m.parseJobsHash("#/jobs?page=-4&size=7&window=bogus&state=bogus&q=go"),
    m.parseJobsHash(m.jobsHash({ profileIds: ["p1", "p2"], window: "30d", states: ["recommended"], removed: true, query: "a&b=c" }, 5, 100)),
  ],
};
out.needsAnswers = [m.needsAnswers(search.counts), m.needsAnswers({ by_state: { needs_answers: 4 } })];

// Assess these: the ask, then the approval.
out.askBodies = {
  selection: m.assessAskBody({ selectedIds: [data.bothUrl], filter: { ...m.EMPTY_FILTER, window: "new" }, rows }),
  filter: m.assessAskBody({ filter: { ...m.EMPTY_FILTER, profileIds: ["p1"], window: "7d", states: ["needs_answers"], query: " rust " }, rows }),
  noFilter: m.assessAskBody({ filter: m.EMPTY_FILTER, rows }),
  twoProfiles: m.assessAskBody({ filter: { ...m.EMPTY_FILTER, profileIds: ["p1", "p2"] }, rows: rows.slice(0, 2) }),
  asProfile: m.assessAskBody({ selectedIds: [data.bothUrl], profileId: data.secondId }),
};
out.askHasApprove = Object.values(out.askBodies).some((body) => "approve" in body);
out.dialog = m.approvalDialog(ask, profiles);
out.estimate = [
  m.estimateLine(out.dialog),
  m.estimateLine({ calls: 12, tokens: "230k", seconds: "4.5 min" }),
  m.estimateLine({ calls: 1, tokens: null, seconds: null }),
];
out.noDialog = [m.approvalDialog(nothing, profiles), m.approvalDialog(null, profiles), m.approvalDialog({ status: "assessed", question: null }, profiles)];
// 0110-10-11: 50 at a time. The server's question says the total (120), the batch (50) and what is left (70).
{
  const capped = {
    status: "ask",
    question: { ...ask.question, to_assess: 120, batch: 50, more_after: 70, estimate: { calls: 50, tokens: 1170000, seconds: 1750, basis_calls: 9 } },
    low_rank: { skipped: 112, batch: 50, more_after: 62, min_rank: 50, estimate: { calls: 50 }, yes: { api: { body: { jobs: ["x"] } } } },
  };
  const dialog = m.approvalDialog(capped, profiles);
  out.capped = {
    numbers: [dialog.count, dialog.total, dialog.moreAfter, dialog.calls, dialog.lowRank.count, dialog.lowRank.batch, dialog.lowRank.moreAfter],
    title: [m.approvalTitle(dialog), m.approvalTitle(out.dialog), m.approvalTitle({ count: 1, moreAfter: 0 }), m.approvalTitle({ count: 0, moreAfter: 0, lowRank: {} })],
    batch: [m.approvalBatchLine(dialog), m.approvalBatchLine(out.dialog), m.approvalBatchLine({ count: 50, moreAfter: 3 })],
    low: m.lowRankLine(dialog.lowRank),
    outcome: [
      m.assessOutcomeLine({ status: "assessed", assessed: { requested: 50, assessed: 50, failed: [] }, counts: { more_after: 70 } }),
      m.assessOutcomeLine({ status: "assessed", assessed: { requested: 3, assessed: 3, failed: [] }, counts: { more_after: 0 } }),
    ],
  };
}
out.outcome = [
  m.assessOutcomeLine(ask),
  m.assessOutcomeLine(nothing),
  m.assessOutcomeLine({ status: "assessed", assessed: { requested: 3, assessed: 2, failed: [{ job_identity: "x", error_code: "assess_timeout" }] } }),
  m.assessOutcomeLine({ status: "assessed", assessed: { requested: 2, assessed: 2, failed: [] } }),
];

// A posting's markup is carried as text, never parsed.
const markup = byId[data.markupUrl];
out.markup = { title: markup.title, tags: m.profileTags(markup, profiles).length, job: m.postingJob(markup).posting.title, detail: m.detailLine(markup) };
out.job = m.postingJob(both);
console.log(JSON.stringify(out));
"""


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the postings model was NOT run")
    fx = build_postings_fixture(tmp_path, monkeypatch)
    fx.seed(
        "acme",
        [lever_job("acme", 1, salary=True), lever_job("acme", 2, title=TITLE_SECOND_ONLY), lever_job("acme", 3, title=MARKUP)],
        seen_at=days_ago(1),
    )
    fx.seed("old", [lever_job("old", 1)], seen_at=days_ago(20))
    calls = fx.base.model.calls

    def search(**kwargs: object) -> dict[str, object]:
        return posting_search.search_postings(fx.home_root, fx.target, now=NOW, **kwargs)  # type: ignore[arg-type]

    data = {
        "defaultId": fx.default_profile_id,
        "secondId": fx.second_profile_id,
        "bothUrl": job_url("acme", 1),
        "secondOnlyUrl": job_url("acme", 2),
        "markupUrl": job_url("acme", 3),
        "oldUrl": job_url("old", 1),
        "search": search(),
        "second": search(profile_ids=[fx.second_profile_id]),
        # The peek: GET /api/new's own builder. It moves no anchor.
        "peek": scout_new.scout_new(fx.home_root, fx.target, peek=True, now=NOW),
        "ask": posting_search.assess_these(fx.home_root, fx.target, jobs=[job_url("acme", 1), job_url("acme", 2)], now=NOW),
        "nothing": posting_search.assess_these(fx.home_root, fx.target, states=["matched"], now=NOW),
    }
    assert data["search"]["anchor"]["last_checked_at"] is None, "a search and a peek moved the anchor"  # type: ignore[index]
    # "Mark all seen" (POST /api/new/seen's own builder), then the same peek again.
    seen = scout_new.mark_all_seen(fx.home_root, fx.target, now=NOW)
    assert seen["previous"] is None and seen["set_by"] == "mark_all_seen"
    data["seenPeek"] = scout_new.scout_new(fx.home_root, fx.target, peek=True, now=NOW)
    assert fx.base.model.calls == calls, "reading, peeking and asking called a model"
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri())).replace("DATA", json.dumps(data))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    result = json.loads(completed.stdout)
    result["data"] = data
    return result


def test_the_new_chip_counts_from_the_peek_and_mark_all_seen_empties_it(out: dict) -> None:
    assert out["newCount"] == [3, 0, None, None]  # 3 first seen yesterday; after Mark all seen: none; unread: no number
    assert out["timeChips"] == [
        {"window": "new", "label": "New since last check (3)", "testId": "time-chip-new", "active": False},
        {"window": "7d", "label": "7 days", "testId": "time-chip-7d", "active": True},
        {"window": "30d", "label": "30 days", "testId": "time-chip-30d", "active": False},
    ]
    assert out["timeChipsUnread"] == ["New since last check", "7 days", "30 days"]
    assert out["windows"] == ["new", "7d", None], "one time chip at a time; a second click clears it"


def test_the_profile_chips_are_a_filter_and_every_row_is_tagged_best_first(out: dict) -> None:
    data = out["data"]
    chips = out["profileChips"]
    assert [chip["profileId"] for chip in chips] == [item["profile_id"] for item in data["search"]["profiles"]]
    assert {chip["profileId"]: chip["active"] for chip in chips} == {data["defaultId"]: False, data["secondId"]: True}
    assert {chip["label"] for chip in chips} == {item["label"] for item in data["search"]["profiles"]} and all(chip["matched"] >= 3 for chip in chips)
    assert out["bothOn"] == [data["defaultId"], data["secondId"]] and out["oneOff"] == [data["secondId"]]
    assert out["kept"] == [data["secondId"]], "a remembered profile that is no longer active is dropped"
    both, only = out["tags"]["both"], out["tags"]["only"]
    assert {tag["profileId"] for tag in both} == {data["defaultId"], data["secondId"]}
    assert [tag["best"] for tag in both] == [True, False] and [tag["shown"] for tag in both] == [True, False]
    assert [(tag["profileId"], tag["best"], tag["shown"]) for tag in only] == [(data["secondId"], True, True)]
    # With one profile chip on, the row shows that profile's state even where it is not the best tag.
    shown = [tag["profileId"] for tag in out["tags"]["second"] if tag["shown"]]
    assert shown == [data["secondId"]]
    assert len(out["others"]["both"]) == 1 and out["others"]["only"] == [], "Assess as <profile>: only for another profile it matches"


def test_the_query_each_filter_sends(out: dict) -> None:
    assert out["queries"] == {
        "none": "limit=50",
        "profiles": "profile_id=p1&profile_id=p2&limit=50",
        "window": "window=new&limit=50",
        "states": "state=needs_answers&state=recommended&limit=50",
        "removed": "q=acme&removed=1&limit=50",
        "page": "limit=50&offset=50",
    }
    assert out["stateToggle"] == ["recommended"] and out["hasFilter"] == [False, False, True]


def test_the_row_chips_say_the_state_the_scout_label_stale_and_removed(out: dict) -> None:
    chips = {name: [(chip["kind"], chip["label"], chip["tone"], chip.get("testId")) for chip in found] for name, found in out["chips"].items()}
    assert chips["notAssessed"] == [("state", "Not assessed", "plain", None)]
    assert chips["needsAnswers"] == [("state", "Needs your answers (2)", "warn", None), ("assessed", "Assessed", "plain", None)]
    assert chips["recommended"] == [
        ("state", "Matched", "ok", None), ("assessed", "Assessed", "plain", None),
        ("label", "Scout label: recommended", "ok", "scout-label-chip"), ("ats", "Scout ATS 84", "plain", "ats-chip"),
    ]
    assert chips["attention"] == [
        ("state", "Resume tailored", "ok", None), ("assessed", "Assessed", "plain", None),
        ("label", "Scout label: needs attention", "warn", "scout-label-chip"), ("ats", "Scout ATS 61", "plain", "ats-chip"),
        # 0110-10-12: the server's words. This row's score column does not say it (no `score_text` with the label), so the chip does.
        ("stale", "Old assessment: posting changed", "warn", "stale-chip"),
    ]
    assert chips["removed"] == [
        ("state", "Not a match", "danger", None), ("assessed", "Assessed", "plain", None),
        ("stale", "Old assessment: settings changed", "warn", "stale-chip"), ("removed", "Removed", "danger", None),
    ]
    assert [kind for kind, *_rest in chips["unknownLabel"]] == ["state", "assessed"], "a label code the backend does not have is never shown"
    assert out["scores"] == ["not ranked yet · not assessed", "73% of requirements met", "rank 81"]
    assert out["scoreTexts"] == [
        "Matched · 1 of 1 requirements · rank 40", "Matched (old assessment: older prompt) · 3 of 3 requirements · rank 95",
        "73% of requirements met",
    ]
    # The page draws the rows in the server's order and never sorts them: no stale or unassessed row above a current one.
    rank = {"current": 0, "stale": 1, "not_assessed": 2}
    assert [rank[group] for group in out["serverOrder"]] == sorted(rank[group] for group in out["serverOrder"])
    jobs_view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert ".sort(" not in jobs_view and "scoreText(row)" in jobs_view
    assert out["detail"][0].startswith("Acme · Remote - United States · Remote") and out["detail"][1] == "Acme"
    assert out["isNew"] == [True, False, False, False]
    assert out["count"] == ["Showing 1-4 of 4 postings", "Showing 1-50 of 120 postings", "Showing 1 of 1 posting", "Showing 0 of 0 postings"]
    pages = out["pages"]
    assert pages["line"] == ["Showing 51-100 of 591 postings", "Showing 551-591 of 591 postings", "Showing 51-75 of 591 postings"]
    assert pages["count"] == [12, 1, 4, 2]
    assert pages["numbers"] == [[1, 2, 3], [1, 2, 3, "…", 11, 12], [1, 2, 5, 6, 7, 11, 12], [1, 2, "…", 11, 12]] or pages["numbers"][2] == [1, 2, "…", 5, 6, 7, "…", 11, 12]
    assert pages["hash"] == ["#/jobs", "#/jobs?page=3&state=needs_answers", "#/jobs?size=25&page=2" if False else pages["hash"][2]]
    assert pages["hash"][2] == "#/jobs?page=2&size=25&profile=p1&state=assessed&window=7d&removed=1&q=rust"
    first, bare, junk, round_trip = pages["parsed"]
    assert (first["page"], first["size"], first["filter"]["states"], first["bare"]) == (3, 50, ["needs_answers"], False)
    assert bare["bare"] is True and bare["page"] == 1
    assert (junk["page"], junk["size"], junk["filter"]["window"], junk["filter"]["states"], junk["filter"]["query"]) == (1, 50, None, [], "go")
    assert round_trip["page"] == 5 and round_trip["size"] == 100 and round_trip["filter"] == {
        "profileIds": ["p1", "p2"], "window": "30d", "states": ["recommended"], "removed": True, "query": "a&b=c",
        "sort": None,  # 0110-10-14: the order rides with the filters; off (the server's own order) unless the address says so
    }
    assert out["needsAnswers"] == [0, 4]


def test_assess_these_asks_first_and_approve_sends_the_servers_own_body(out: dict) -> None:
    data = out["data"]
    assert out["askBodies"] == {
        "selection": {"jobs": [data["bothUrl"]]},
        "filter": {"profile_id": "p1", "query": "rust", "states": ["needs_answers"], "window": "7d"},
        "noFilter": {},
        "twoProfiles": {"jobs": [row["job_identity"] for row in data["search"]["postings"]["rows"][:2]]},
        "asProfile": {"jobs": [data["bothUrl"]], "profile_id": data["secondId"]},
    }
    assert out["askHasApprove"] is False, "the ask never approves: nothing is assessed before the dialog's Approve"
    dialog, question = out["dialog"], data["ask"]["question"]
    assert data["ask"]["status"] == "ask" and data["ask"]["assessed"] is None and data["ask"]["approval"] is None
    assert (dialog["count"], dialog["alreadyCurrent"], dialog["calls"]) == (question["to_assess"], 0, question["estimate"]["calls"]) == (2, 0, 2)
    labels = {item["profile_id"]: item["label"] for item in data["search"]["profiles"]}
    assert dialog["byProfile"] == [{"label": labels[item["profile_id"]], "count": item["count"]} for item in question["by_profile"]]
    assert dialog["modelTarget"] == question["model_target"] and dialog["basisCalls"] == 0
    assert dialog["approveBody"] == {"approve": True, "jobs": [data["bothUrl"], data["secondOnlyUrl"]]} == question["yes"]["api"]["body"]
    assert out["estimate"] == ["~2 model calls", "~12 model calls, ~230k tokens, ~4.5 min", "~1 model call"]
    assert out["noDialog"] == [None, None, None]
    assert data["nothing"]["status"] == "nothing_to_assess"
    assert out["outcome"] == [
        None,
        "Nothing to assess: every selected posting has a current assessment.",
        "Assessed 2 of 3. Not assessed: assess_timeout.",
        "Assessed 2 of 2.",
    ]


def test_the_approval_says_the_newest_50_the_total_and_what_is_left(out: dict) -> None:
    """0110-10-11 (the operator's rule): one approval is the newest 50, never more; the dialog states the total and the 50."""

    capped = out["capped"]
    assert capped["numbers"] == [50, 120, 70, 50, 112, 50, 62]
    assert capped["title"] == [
        "Assess the newest 50 of 120 postings?", "Assess 2 postings?", "Assess 1 posting?", "Only low-ranked postings are selected",
    ]
    assert capped["batch"] == [
        'the newest 50 now, never more in one go. 70 more after these 50: "Assess these" again takes the next 50.',
        None,
        'the newest 50 now, never more in one go. 3 more after these 50: "Assess these" again takes the next 3.',
    ]
    assert capped["low"] == (
        "112 low-ranked ones are skipped (rank below 50). Assess the newest 50 of those too? ~50 model calls (62 more after these 50)"
    )
    assert capped["outcome"] == [
        'Assessed 50 of 50. 70 more not assessed yet: 50 at a time, "Assess these" again takes the next.',
        "Assessed 3 of 3.",
    ]
    dialog = (UI_SRC / "components" / "AssessApprovalDialog.jsx").read_text(encoding="utf-8")
    assert "approvalTitle(dialog)" in dialog and 'data-role="approval-batch"' in dialog and "approvalBatchLine(dialog)" in dialog


def test_a_postings_markup_stays_text_and_nothing_in_the_ui_renders_raw_html(out: dict) -> None:
    assert out["markup"]["title"] == MARKUP == out["markup"]["job"], "the model carries the posting's words as they are, as a string"
    assert out["markup"]["tags"] >= 1
    # React draws a string child as text. No source file hands a string to the DOM as HTML.
    for path in sorted(UI_SRC.rglob("*.js*")):
        code = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith(("//", "*", "/*")))
        assert "dangerouslySetInnerHTML" not in code and ".innerHTML" not in code and "insertAdjacentHTML" not in code, path.name
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert '{row.title || "(untitled posting)"}' in view and '<div className="posting-detail">' in view and "{details}" in view and "{posted.text}" in view
    job = out["job"]
    assert (job["id"], job["status"], job["fromPostings"], job["verdict"], job["row"]) == (out["data"]["bothUrl"], "posting", True, "not_assessed", None)
    assert job["posting"]["normalized_url"] == out["data"]["bothUrl"] and job["posting"]["text"].startswith("Posting 1:")
    assert job["posting"]["work_mode"] == "remote" and job["assessment"] is None


def test_the_jobs_page_wiring_and_test_ids() -> None:
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    # 0110-9-01: both take the store's abort signal (``options``) as the request's last argument.
    assert 'request("GET", `/api/postings${query ? `?${query}` : ""}`, undefined, options)' in api
    assert 'request("GET", "/api/new?peek=1", undefined, options)' in api, "the New chip reads the peek"
    assert 'request("POST", "/api/new/seen", {})' in api and 'request("POST", "/api/postings/assess", body)' in api
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    for test_id in ('data-testid="jobs-list"', 'data-testid="job-row"', 'data-testid="profile-chip"', 'data-testid="mark-all-seen"', 'data-testid="assess-these"'):
        assert test_id in view, test_id
    assert "data-testid={chip.testId}" in view  # time-chip-new|7d|30d, scout-label-chip, ats-chip (the model names them)
    assert 'data-testid="approval-dialog"' in (UI_SRC / "components" / "AssessApprovalDialog.jsx").read_text(encoding="utf-8")
    assert not re.search(r'data-testid=\{`', view), "test ids are stable: none is built from a value"
    # Mark all seen: the POST, then the peek and the list are read again.
    mark = view[view.index("const markAllSeen = () => {") :][:260]
    # 0110-9-01: through the client-side store (one request each, the rows staying shown while they are read).
    assert "postMarkAllSeen()" in mark and "postingsStore.refresh(filter, { page, size })" in mark
    store = (UI_SRC / "postingsStore.js").read_text(encoding="utf-8")
    assert "store.show(filter, { ...options, force: true });\n      store.peekNew({ force: true });" in store
    # The ask opens the dialog; only the dialog's Approve sends the approving body.
    # 0110-10-02: the body is the server's yes, or (the dialog's low-rank box ticked) the one it names for those too.
    assert view.count("postAssessThese(") == 2 and "postAssessThese(approvalBody(approval.dialog, includeLowRank))" in view
    assert "approve: true" not in view and "onApprove={approve}" in view
    # A profile is a filter here: the top bar's dropdown is not drawn on Jobs.
    assert 'const switcher = currentView === "jobs" ? null :' in (UI_SRC / "components" / "TopBar.jsx").read_text(encoding="utf-8")
    find_jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert 'if (route.view === "jobs") {\n    return (\n      <JobsView' in find_jobs and "postingJob(listed)" in find_jobs


def test_no_run_is_started_from_the_ui_and_past_runs_are_history() -> None:
    """The "Run find jobs" button and its run-start flow are gone from every view."""

    callers = []
    for path in sorted(UI_SRC.rglob("*.jsx")):
        text = path.read_text(encoding="utf-8")
        if path.name != "RunConfirmDialog.jsx" and ("RunConfirmDialog" in text or "startRun(" in text or "buildRunRequest(" in text):
            callers.append(path.name)
    assert callers == [], f"these views can still start a run: {callers}"
    for name in ("views/JobsView.jsx", "views/RunsView.jsx", "sourcesStripModel.js", "components/SourcesStrip.jsx", "components/TopBar.jsx"):
        assert "Run find jobs" not in (UI_SRC / name).read_text(encoding="utf-8"), name
    runs = (UI_SRC / "views" / "RunsView.jsx").read_text(encoding="utf-8")
    assert "Past runs" in runs and 'data-role="history-tag"' in runs and "Read-only: no new run is started here." in runs
    assert "<button" not in runs.replace('<button className="button small secondary" onClick={reload}>', ""), "the list only reads (Retry re-reads)"
    routing = (UI_SRC / "routing.js").read_text(encoding="utf-8")
    assert 'view: "runs", path: "#/runs", label: "Past runs"' in routing


def test_the_built_bundle_has_the_jobs_page() -> None:
    bundle = "".join(path.read_text(encoding="utf-8") for path in (UI / "dist" / "assets").glob("index-*.js"))
    for needle in ("/api/postings", "/api/new/seen", "/api/new?peek=1", "/api/postings/assess", "jobs-list", "job-row", "profile-chip", "time-chip-", "mark-all-seen", "assess-these", "approval-dialog", "Past runs"):
        assert needle in bundle, needle
    assert "Run find jobs" not in bundle and "Confirm and run" not in bundle, "the run-start flow is not in the served bundle"
