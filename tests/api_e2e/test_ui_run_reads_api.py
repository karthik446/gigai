"""run-reads-fast (uat-bug-022): ``ui/src/api.js`` reads a run a page at a time.

``api.js`` is plain JavaScript (no React), so this runs it under the system
``node`` with a recorded ``fetch`` the way the other UI model tests do (no
JS test runner) and asserts on the JSON the script prints. LOUD skip when
``node`` is not on PATH.

What is pinned:

* ``getRunResults(runId)`` asks for pages of 100 (``?limit=&offset=``) until
  it has the run's ``total`` rows, and answers one response in the no-query
  read's shape: the pages' rows, assessments, not-assessed rows and
  carried-forward assessments, in page order; the carried-forward list is
  in payload too, where the cards are built from;
* ``onPage`` is called after every page with everything read so far (the
  first call is the first page: the top of the grid), and returning
  ``false`` from it stops the read;
* a page with no rows ends the read, whatever ``total`` said;
* ``getRunProgress`` reads the summary; ``getRunPosting`` reads one posting
  by its ``normalized_url``, percent-encoded;
* ``storedRankScores`` is the rows' stored scores in ``POST /rank``'s shape;
* ``getRuns`` can ask for the newest run of a status (``status``/``limit``);
* the client never asks the no-query ``/results`` or ``/progress``;
* the cards built from a merged response (``boardRows.rowsFromResults``)
  show a carried-forward assessment, which the views used to drop;
* the views (read as source): the Jobs view draws each page as it comes and
  stops when another run is shown, opens on the newest succeeded run with
  one small read instead of the runs list, and the job page reads its own
  posting's text.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
API_JS = UI_SRC / "api.js"
BOARD_ROWS_JS = UI_SRC / "boardRows.js"
RUN_ID = "run_123e4567-e89b-42d3-a456-426614174002"
POSTING_URL = "https://boards.greenhouse.io/acme/jobs/101?gh_src=a b&x=1"

NODE_SCRIPT = """
import * as api from API_JS_URL;
import { rowsFromResults } from BOARD_ROWS_JS_URL;
const input = JSON.parse(process.argv[1]);

const asked = [];
let pages = input.pages;
globalThis.fetch = async (path, options) => {
  asked.push(`${options.method} ${path}`);
  const url = new URL(path, "http://127.0.0.1");
  let body = {};
  if (url.pathname.endsWith("/results")) {
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset"));
    body = pages({ limit, offset });
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
};

const row = (index) => ({
  posting: { normalized_url: `https://a.test/${index}`, title: `Job ${index}` },
  outcome: "new",
  rank_score: index % 2 === 0 ? { normalized_url: `https://a.test/${index}`, score: 90 - index, fit: "strong" } : null,
});
const paged = (total, claimed = total) => ({ limit, offset }) => {
  const indexes = [];
  for (let index = offset; index < Math.min(offset + limit, total); index += 1) indexes.push(index);
  return {
    schema_version: "scout-find-jobs-run-results-response:1",
    run_id: input.runId,
    total: claimed, limit, offset,
    created_at: "2026-09-28T00:00:00Z",
    counts: { found: claimed, new: claimed, assessed: 2, matched: 1 },
    payload: {
      run_id: input.runId, status: "succeeded", config: { roles: ["engineer"] },
      rows: indexes.map(row),
      assessments: indexes.filter((index) => index % 100 === 0).map((index) => ({ posting: { normalized_url: `https://a.test/${index}` }, verdict: "matched_above_threshold" })),
      not_assessed: indexes.filter((index) => index % 100 === 1).map((index) => ({ posting: { normalized_url: `https://a.test/${index}` }, reason: "model_denied" })),
    },
    carried_forward_assessments: indexes.filter((index) => index % 100 === 2).map((index) => ({ normalized_url: `https://a.test/${index}`, from_run_date: "2026-09-01T00:00:00Z", result: { verdict: "matched_above_threshold" } })),
  };
};
const summary = (response) => ({
  total: response.total,
  counts: response.counts,
  createdAt: response.created_at,
  status: response.payload.status,
  config: response.payload.config,
  rows: response.payload.rows.map((item) => item.posting.normalized_url),
  assessments: response.payload.assessments.map((item) => item.posting.normalized_url),
  notAssessed: response.payload.not_assessed.map((item) => item.posting.normalized_url),
  carried: response.carried_forward_assessments.map((item) => item.normalized_url),
  carriedInPayload: response.payload.carried_forward_assessments.map((item) => item.normalized_url),
});
const read = async (total, options, claimed) => {
  asked.length = 0;
  pages = paged(total, claimed);
  const seen = [];
  const onPage = options && options.stopAfter
    ? (response) => { seen.push(response.payload.rows.length); return seen.length < options.stopAfter; }
    : (response) => { seen.push(response.payload.rows.length); };
  const response = await api.getRunResults(input.runId, { onPage, ...(options && options.pageSize ? { pageSize: options.pageSize } : {}) });
  return { asked: asked.slice(), seen, response: summary(response) };
};

const out = {};
out.pageSize = api.RESULTS_PAGE_SIZE;
out.full = await read(250);
out.exact = await read(200);
out.one = await read(37);
out.none = await read(0);
out.smallPages = await read(7, { pageSize: 3 });
out.stopped = await read(450, { stopAfter: 2 });
out.shorterThanSaid = await read(150, null, 400);

asked.length = 0;
pages = paged(250);
const plain = await api.getRunResults(input.runId);
out.noCallback = { asked: asked.slice(), rows: plain.payload.rows.length };
out.scores = api.storedRankScores(plain).length;
// What FindJobsView does with a response: the cards come from its payload.
out.cards = rowsFromResults(plain.payload).slice(0, 4).map((card) => ({
  url: card.posting.normalized_url,
  status: card.status,
  assessed: Boolean(card.assessment),
  fromRunDate: card.fromRunDate,
  reason: card.notAssessedReason,
}));
out.scoreShape = api.storedRankScores(plain)[1];

asked.length = 0;
await api.getRunResultsPage(input.runId, { limit: 25, offset: 50 });
await api.getRunResultsPage(input.runId);
await api.getRunProgress(input.runId);
await api.getRunPosting(input.runId, input.postingUrl);
await api.getRunStatus(input.runId);
out.urls = asked.slice();
out.postingUrlReadBack = new URL(out.urls[3].split(" ")[1], "http://127.0.0.1").searchParams.get("url");
pages = () => ({ total: 0, limit: 100, offset: 0, payload: { rows: [], assessments: [], not_assessed: [] } });
const bare = await api.getRunResultsPage(input.runId);
out.bare = [bare.carried_forward_assessments, bare.payload.carried_forward_assessments];
asked.length = 0;
await api.getRuns();
await api.getRuns({ profileId: "profile_1" });
await api.getRuns({ profileId: "profile_1", status: "succeeded", limit: 1 });
out.runsUrls = asked.slice();

process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; run-reads-fast api.js checks not run")
    script = NODE_SCRIPT.replace("API_JS_URL", json.dumps(API_JS.resolve().as_uri())).replace(
        "BOARD_ROWS_JS_URL", json.dumps(BOARD_ROWS_JS.resolve().as_uri())
    )
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps({"runId": RUN_ID, "postingUrl": POSTING_URL})],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _page(offset: int, limit: int = 100) -> str:
    return f"GET /api/runs/{RUN_ID}/results?limit={limit}&offset={offset}"


def _urls(*indexes: int) -> list[str]:
    return [f"https://a.test/{index}" for index in indexes]


def test_a_run_is_read_in_pages_of_100_until_every_row_is_in(out: dict) -> None:
    assert out["pageSize"] == 100
    full = out["full"]
    assert full["asked"] == [_page(0), _page(100), _page(200)]
    assert full["response"]["rows"] == _urls(*range(250))
    assert full["response"]["total"] == 250
    assert full["response"]["counts"] == {"found": 250, "new": 250, "assessed": 2, "matched": 1}
    assert full["response"]["createdAt"] == "2026-09-28T00:00:00Z"
    assert (full["response"]["status"], full["response"]["config"]) == ("succeeded", {"roles": ["engineer"]})


def test_the_pages_joins_are_merged_in_page_order(out: dict) -> None:
    response = out["full"]["response"]
    assert response["assessments"] == _urls(0, 100, 200)
    assert response["notAssessed"] == _urls(1, 101, 201)
    assert response["carried"] == _urls(2, 102, 202)
    # The cards are built from the payload alone: the list is there too.
    assert response["carriedInPayload"] == response["carried"]


def test_on_page_gets_everything_read_so_far_first_page_first(out: dict) -> None:
    assert out["full"]["seen"] == [100, 200, 250]
    assert out["smallPages"]["seen"] == [3, 6, 7]
    assert out["smallPages"]["asked"] == [_page(0, 3), _page(3, 3), _page(6, 3)]


def test_no_page_is_asked_for_once_the_run_is_in(out: dict) -> None:
    assert out["exact"]["asked"] == [_page(0), _page(100)]
    assert (out["exact"]["seen"], len(out["exact"]["response"]["rows"])) == ([100, 200], 200)
    assert out["one"]["asked"] == [_page(0)] and out["one"]["seen"] == [37]
    assert out["none"]["asked"] == [_page(0)] and out["none"]["response"]["rows"] == []


def test_returning_false_from_on_page_stops_the_read(out: dict) -> None:
    stopped = out["stopped"]
    assert stopped["asked"] == [_page(0), _page(100)]
    assert stopped["seen"] == [100, 200]
    assert len(stopped["response"]["rows"]) == 200 and stopped["response"]["total"] == 450


def test_a_page_with_no_rows_ends_the_read(out: dict) -> None:
    short = out["shorterThanSaid"]
    assert short["asked"] == [_page(0), _page(100), _page(150)]
    assert short["seen"] == [100, 150]
    assert len(short["response"]["rows"]) == 150


def test_a_caller_with_no_callback_gets_the_whole_run(out: dict) -> None:
    assert out["noCallback"] == {"asked": [_page(0), _page(100), _page(200)], "rows": 250}


def test_a_page_with_no_carried_forward_list_has_an_empty_one(out: dict) -> None:
    assert out["bare"] == [[], []]


def test_stored_scores_are_the_rows_scores_in_ranks_shape(out: dict) -> None:
    assert out["scores"] == 125  # every other row has one
    assert out["scoreShape"] == {"normalized_url": "https://a.test/2", "score": 88, "fit": "strong"}


def test_the_reads_a_page_makes(out: dict) -> None:
    page, default_page, progress, posting, status = out["urls"]
    assert page == _page(50, 25)
    assert default_page == _page(0)
    assert progress == f"GET /api/runs/{RUN_ID}/progress?summary=1"
    assert posting.startswith(f"GET /api/runs/{RUN_ID}/posting?url=")
    assert " " not in posting.removeprefix("GET ") and "&x=1" not in posting
    assert out["postingUrlReadBack"] == POSTING_URL
    assert status == f"GET /api/runs/{RUN_ID}"


def test_the_newest_run_of_a_status_is_one_small_read(out: dict) -> None:
    assert out["runsUrls"] == [
        "GET /api/runs",
        "GET /api/runs?profile_id=profile_1",
        "GET /api/runs?profile_id=profile_1&status=succeeded&limit=1",
    ]


def test_the_client_never_asks_the_full_reads() -> None:
    source = API_JS.read_text(encoding="utf-8")
    assert "/results`" not in source, "api.js asks the no-query /results"
    assert "/progress`" not in source, "api.js asks the no-query /progress"
    assert "/results?${query}`" in source and "/progress?summary=1`" in source


def test_a_carried_forward_assessment_reaches_its_card(out: dict) -> None:
    assessed, refused, carried, plain = out["cards"]
    assert (assessed["status"], assessed["assessed"]) == ("assessed", True)
    assert (refused["status"], refused["reason"]) == ("not_assessed", "model_denied")
    # The server sends the list beside the payload and the cards are built
    # from the payload: before, this card read "acquired", with no assessment.
    assert carried == {
        "url": "https://a.test/2",
        "status": "carried_forward",
        "assessed": True,
        "fromRunDate": "2026-09-01T00:00:00Z",
        "reason": None,
    }
    assert (plain["status"], plain["assessed"]) == ("acquired", False)


def test_the_jobs_view_draws_each_page_and_opens_on_one_small_read() -> None:
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")

    load = view[view.index("const loadResults = useCallback(") :]
    load = load[: load.index("[loadQuickItems, followRankPass],")]
    on_page = load[load.index("onPage: (response) => {") : load.index(".then(() => {")]
    # A page for a run that is no longer shown stops the read.
    assert "if (shownRunId.current !== id) {\n            return false;" in on_page
    assert "setResults(response.payload);" in on_page
    # The grid stops saying "loading" with the FIRST page, not the last.
    first_page = on_page[on_page.index("if (!drawn) {") :]
    assert "setResultsLoading(false);" in first_page and "created_at: response.created_at" in first_page
    assert "setPagesLoading(false);" in load[load.index(".then(() => {") :]

    # The run to open on is asked for on its own; the runs list is not waited for.
    opens = view[view.index("// Q4a: nothing loaded yet") :]
    opens = opens[: opens.index("async function handleConfirm")]
    assert 'getRuns({ profileId, status: "succeeded", limit: 1 })' in opens
    assert "shownRunId.current === null" in opens
    assert "runsState" not in opens
    assert "runsState.loading && !runId" not in view
    assert "loading={fromAssessments ? quickLoading : resultsLoading || pagesLoading || quickLoading || (newestLoading && !runId)}" in view
    assert "const runCreatedAt = currentRun ? currentRun.created_at : runMeta ? runMeta.created_at : null;" in view
    assert "runId={runId}" in view[view.index("<JobPage") :][:400]


def test_the_job_page_reads_its_own_posting_text() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")

    assert "const needsText = Boolean(runId && job && job.row && !job.posting.text);" in page
    read = page[page.index("const needsText") :]
    read = read[: read.index("const handleApplicationRecorded")]
    assert "getRunPosting(runId, jobId)" in read
    assert "current && setPostingText(response.row.posting.text || null)" in read
    assert "}, [runId, jobId, needsText]);" in read
    # The text it read is the posting the page shows; a posting that has its
    # text (a quick assessment's) is left as it is.
    assert "job && postingText && !job.posting.text ? { ...job.posting, text: postingText }" in page
    assert "<JobDescription posting={posting}" in page
