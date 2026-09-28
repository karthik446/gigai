"""uat-ui-batch2 (operator UAT 2026-09-27/28): the UI's words and rules, run under node.

``ui/src/jobModel.js``, ``sourcesModel.js``, ``assessModel.js`` and
``runText.js`` are pure JavaScript (no React), so this test runs them under
the system ``node`` the way ``test_ui_uat_batch1_model.py`` does (no JS test
runner) and asserts on the JSON the script prints. LOUD skip when ``node``
is not on PATH. What lives in JSX is checked statically, by reading the
source.

What is pinned, by item:

* uat-bug-016 (N21)  Assessments lists every on-demand assessment, one card
  per job, newest first; Jobs and a run page list run postings only;
  "+ Assess a job" is on Assessments, not on Jobs; a card on Assessments
  opens ``#/assessments/<id>`` and that page goes back to Assessments.
* (found in the hand-check) an assessment made against a PASTED resume is
  filed under no profile; it is a card on Assessments all the same, and it
  never replaces a run posting's own verdict.
* uat-bug-013 (N16)  the Assess page names the ACTIVE profile and its resume
  and sends that profile's id; another profile or a pasted resume is the
  secondary choice.
* uat-bug-014 / uat-bug-015  a quick assessment's ``posting_text`` is the job
  page's text; ``rank_score`` is its Jev tile; every ``rank_skip_reason``
  the backend can emit (``assess_contracts.RANK_SKIP_REASONS``, read from
  the Python module itself) has words.
* N11-C  "Update sources": the progress bar is ``boards.done`` of
  ``boards.total`` and indeterminate while the total is 0; the result line
  is the server's summary; ``partial`` says "Run again to continue" and what
  is left; ``failed`` shows the server's message; Jobs shows
  ``index.message`` only when ``index.needs_update``.
* uat-bug-011  "N more matched, not imported this run", only above 0.
* N11-C part 2  a run that read the company index says what it read, what
  it fetched for the companies Exa found, how many wait for the next Update
  sources, and when the stored postings need an update; words only, and no
  rotation line. A run sealed before the swap keeps its rotation line.
* run dialog  the "Company boards" line is the stored (indexed) count and
  when it was last updated, from ``GET /api/sources/update``; the 4.5 MB
  ``GET /api/watchlist`` read is gone from the dialog.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs import sources_update
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.assess_contracts import RANK_SKIP_REASONS

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
JOB_MODEL_JS = UI_SRC / "jobModel.js"
SOURCES_MODEL_JS = UI_SRC / "sourcesModel.js"
ASSESS_MODEL_JS = UI_SRC / "assessModel.js"
RUN_TEXT_JS = UI_SRC / "runText.js"

RAW_ID = re.compile(r"[a-z]+_[a-z_]+")

NOW = "2026-09-28T12:00:00Z"

NODE_SCRIPT = """
import * as jobModel from {job_model_url};
import * as sources from {sources_url};
import * as assess from {assess_url};
import * as runText from {run_text_url};
const input = JSON.parse(process.argv[1]);

const card = (job) => ({
  id: job.id,
  status: job.status,
  text: job.posting.text,
  excerpt: jobModel.jdExcerpt(job.posting.text),
  rank: job.rank,
  scored: jobModel.isScored(job.rank),
  rankSkipReason: job.rankSkipReason,
  skipText: jobModel.jevSkipText(job.rankSkipReason),
  verdict: job.verdict,
  assessedAt: jobModel.assessedAt(job),
});
const jobs = jobModel.buildJobs(input.build);
const now = new Date(input.now).getTime();

process.stdout.write(JSON.stringify({
  skipText: Object.fromEntries(input.skipReasons.map((id) => [id, jobModel.jevSkipText(id)])),
  skipNone: [jobModel.jevSkipText(null), jobModel.jevSkipText(undefined), jobModel.jevSkipText("")],
  quickOnly: input.quickItems.map((item) => card(jobModel.quickOnlyJob(item))),
  quickOnlyWithRunRank: card(jobModel.quickOnlyJob(input.quickItems[1], input.runRank)),
  built: jobs.map(card),
  runJobs: jobModel.runJobs(jobs).map((job) => job.id),
  assessments: jobModel.assessmentJobs(input.build.quickItems, jobs).map(card),
  assessmentsAlone: jobModel.assessmentJobs(input.build.quickItems, []).map(card),
  assessmentsEmpty: [jobModel.assessmentJobs([], jobs), jobModel.assessmentJobs(null, null)],
  withPasted: jobModel.assessmentJobs(input.build.quickItems, jobs, input.pastedItems).map((job) => ({ ...card(job), pastedResume: Boolean(job.pastedResume) })),
  usedPasted: input.pastedItems.concat(input.quickItems).map((item) => jobModel.usedPastedResume(item)),
  pastedKey: jobModel.PASTED_RESUME_KEY,
  sortedNewest: jobModel.sortByAssessedAt(jobModel.assessmentJobs(input.build.quickItems, jobs).slice().reverse()).map((job) => job.id),
  progress: input.updates.map((update) => sources.sourcesProgress(update)),
  results: input.updates.map((update) => sources.sourcesResult(update)),
  running: input.statuses.map((status) => sources.isRunning(status)),
  notices: input.statuses.map((status) => sources.indexNotice(status)),
  noticeAtsOff: sources.indexNotice(input.statuses[0], { atsEnabled: false }),
  stored: input.statuses.map((status) => sources.storedLine(status && status.index)),
  stuck: input.stuck.map((update) => sources.looksStuck(update, now)),
  startErrors: input.startErrors.map((error) => sources.startErrorText(error)),
  against: input.against.map((item) => assess.assessingAgainst(item)),
  resumes: input.against.map((item) => assess.assessResume(item)),
  others: assess.otherProfiles(input.profiles, "default").map((profile) => profile.profile_id),
  can: input.can.map((item) => assess.canAssess(item)),
  privacy: assess.ASSESS_MODES.map((mode) => assess.assessPrivacyNote(mode)),
  notImported: input.notImported.map((value) => runText.notImportedLine(value)),
  boardLines: input.boardLines.map((item) => runText.indexedBoardsLine(item)),
  searches: input.searches.map((boards) => runText.searchLines(boards, (iso) => input.timeLabels[iso] || "")),
  searchNoLabels: runText.searchLines(input.searches[0]),
}));
"""

ACME = "https://boards.greenhouse.io/acme/jobs/101"
KONG = "https://jobs.ashbyhq.com/kong/1"
GLOBEX = "https://jobs.lever.co/globex/7"
PASTED = "text:3f9a"

SHA = "sha256:" + "a" * 64
RANK_89 = {
    "normalized_url": KONG,
    "content_sha256": SHA,
    "fit": "strong",
    "score": 89,
    "reasons": ["title_match"],
    "mismatch_flags": ["stack"],
    "hidden_by_default": False,
    "cost_usd": "0.000500",
    "cached": False,
}
RANK_LOW_HIDDEN = {**RANK_89, "normalized_url": GLOBEX, "fit": "no", "score": 12, "hidden_by_default": True}
RUN_RANK_ACME = {**RANK_89, "normalized_url": ACME, "fit": "maybe", "score": 55}
UNSCORED_ACME = {**RANK_89, "normalized_url": ACME, "fit": None, "score": None, "reasons": [], "mismatch_flags": []}

POSTING_TEXT = "Kong builds API infrastructure.\n\nYou will build reliable Python services for the gateway team."


PROFILE_RESUME = {"profile_id": "default", "pinned": {"record_id": "rec_1", "revision_id": "rev_1"}, "content_sha256": SHA}
PASTED_RESUME = {"profile_id": None, "pinned": None, "content_sha256": SHA}


def _quick(identity: str, *, at: str, verdict: str = "matched_above_threshold", fetch_kind: str = "ats_single", **extra: object) -> dict:
    pasted = fetch_kind == "pasted"
    return {
        "resume": PROFILE_RESUME,
        "job": {
            "job_identity": identity,
            "normalized_url": None if pasted else identity,
            "source_url": None if pasted else identity,
            "title": "" if pasted else "Software Engineer",
            "company": "" if pasted else "kong",
            "location": "Remote",
            "fetch_kind": fetch_kind,
        },
        "result": {"verdict": verdict, "matrix": [], "structured_questions": []},
        "created_at": at,
        "updated_at": at,
        **extra,
    }


def _row(url: str, *, text: str | None, assessment: dict | None = None) -> dict:
    return {
        "posting": {"title": "Platform Engineer", "company": "acme", "location": "Denver, CO", "url": url, "normalized_url": url, "text": text, "published_at": "2026-09-20T00:00:00Z"},
        "status": "assessed" if assessment else "not_assessed",
        "assessment": assessment,
    }


def _update(status: str, **fields: object) -> dict:
    base = {
        "update_id": "sources_update_1",
        "status": status,
        "started_at": "2026-09-28T11:40:00Z",
        "updated_at": "2026-09-28T11:59:58Z",
        "boards": {"total": 10370, "done": 2140, "fetched": 310, "cached": 1822, "failed": 8, "skipped": 0},
        "postings": {"new": 412, "changed": 95, "removed": 230, "live": 61022},
        "summary": "120 companies with new postings: 412 new, 95 changed, 230 removed",
        "error": None,
        "remaining": 8230,
    }
    base.update(fields)
    return base


def _index(status: str, **fields: object) -> dict:
    messages = {
        "empty": "No company postings are stored on this machine yet. Run Update sources, then search again.",
        "stale": "The stored company postings are out of date. Run Update sources, then search again.",
        "ready": None,
    }
    base = {
        "status": status,
        "needs_update": status != "ready",
        "message": messages[status],
        "companies_indexed": 0 if status == "empty" else 10362,
        "last_checked_at": None if status == "empty" else "2026-09-27T22:12:41.007Z",
        "stale_after_hours": 24.0,
    }
    base.update(fields)
    return base


def _search_index(status: str, **fields: object) -> dict:
    """``boards.index`` of a run's progress (``company_index.IndexState.to_json``)."""

    base = {
        "status": status,
        "needs_update": status != "ready",
        "message": _index(status)["message"],
        "companies": 10370,
        "indexed": 0 if status == "empty" else 10362,
        "not_indexed": 10370 if status == "empty" else 8,
        "oldest_checked_at": None if status == "empty" else "2026-09-25T10:00:00Z",
        "newest_checked_at": None if status == "empty" else "2026-09-28T10:00:00Z",
        "stale_after_hours": 24.0,
    }
    base.update(fields)
    return base


PROFILES = [
    {"profile_id": "default", "label": "default", "resume_ref": {"record_id": "rec_1", "revision_id": "rev_1"}},
    {"profile_id": "staff", "label": "Staff roles", "resume_ref": {"record_id": "rec_2", "revision_id": "rev_9"}},
]
CONFIG = {
    "resume_preview": {"record_id": "rec_1", "revision_id": "rev_1", "content_sha256": SHA},
    "resume_label": "kar-staff-resume.md",
    "resume_created_at": "2026-09-23T10:00:00Z",
}
CONFIG_NO_RESUME = {"resume_preview": None, "resume_label": None, "resume_created_at": None}


def _against(mode: str, **fields: object) -> dict:
    return {"mode": mode, "profiles": PROFILES, "activeProfileId": "default", "otherProfileId": "", "config": CONFIG, "resumeText": "", **fields}


def _payload() -> dict:
    quick_items = [
        # 0: a URL job with no Jev key
        _quick(ACME, at="2026-09-27T10:00:00Z", verdict="pending_user_answers", posting_text="Build reliable Python services.", rank_skip_reason="no_key"),
        # 1: a URL job with its text and a Jev score
        _quick(KONG, at="2026-09-28T09:00:00Z", posting_text=POSTING_TEXT, rank_score=RANK_89),
        # 2: a pasted job against a pasted resume: no text stored, no Jev
        _quick(PASTED, at="2026-09-26T08:00:00Z", fetch_kind="pasted", rank_skip_reason="ephemeral_resume"),
        # 3: a file written before the new fields existed
        _quick(GLOBEX, at="2026-09-25T08:00:00Z"),
    ]
    return {
        "now": NOW,
        "skipReasons": list(RANK_SKIP_REASONS) + ["some_new_reason"],
        "quickItems": quick_items,
        "pastedItems": [
            # ACME again, against a pasted resume, newer than the profile's own
            _quick(ACME, at="2026-09-28T11:00:00Z", verdict="not_a_match", resume=PASTED_RESUME, rank_skip_reason="ephemeral_resume"),
            _quick("text:77aa", at="2026-09-21T08:00:00Z", fetch_kind="pasted", resume=PASTED_RESUME, rank_skip_reason="ephemeral_resume"),
        ],
        "runRank": {**RANK_89, "score": 40, "fit": "maybe"},
        "build": {
            # The loaded run carries ACME (no text kept, never scored by Jev)
            # and GLOBEX (its own text, hidden by Jev).
            "rows": [_row(ACME, text=None), _row(GLOBEX, text="Globex runs logistics software.")],
            "rankScores": [UNSCORED_ACME, RANK_LOW_HIDDEN],
            "quickItems": quick_items + [_quick(KONG, at="2026-09-20T09:00:00Z", verdict="not_a_match")],
            "runCreatedAt": "2026-09-24T00:00:00Z",
        },
        "updates": [
            None,
            _update("running"),
            _update("running", boards={"total": 0, "done": 0, "fetched": 0, "cached": 0, "failed": 0, "skipped": 0}),
            _update("running", boards={"total": 1, "done": 1, "failed": 0}),
            _update("succeeded", boards={"total": 10370, "done": 10370, "failed": 11}, remaining=0),
            # as the server reports a budget stop: `done` counts the skipped boards too
            _update("partial", boards={"total": 10370, "done": 10370, "fetched": 310, "cached": 1822, "failed": 8, "skipped": 8230}, remaining=8230),
            _update("partial", boards={"total": 3, "done": 2, "failed": 0}, remaining=1),
            _update("failed", error={"code": "watchlist_unreadable", "message": "the watchlist could not be read"}, summary=""),
            _update("interrupted"),
            _update("succeeded", summary="", boards={"total": 0, "done": 0}),
        ],
        "statuses": [
            {"running": False, "update": None, "index": _index("empty")},
            {"running": True, "update": _update("running"), "index": _index("stale")},
            {"running": False, "update": _update("succeeded"), "index": _index("ready")},
            {"running": False, "update": _update("partial"), "index": _index("stale", message="")},
            None,
            {"running": False, "update": None},
        ],
        "stuck": [
            _update("running", updated_at="2026-09-28T11:59:58Z"),
            _update("running", updated_at="2026-09-28T11:58:00Z"),
            _update("partial", updated_at="2026-09-28T09:00:00Z"),
            _update("running", updated_at="not a date"),
            None,
        ],
        "startErrors": [
            {"code": "sources_update_running", "message": "The configuration changed since it was loaded."},
            {"code": "target_unavailable", "message": "x"},
            {"code": "forbidden_origin", "message": "This action was refused.", "detail": "origin not allowed"},
            {"message": "Could not reach the local API."},
            None,
        ],
        "profiles": PROFILES,
        "against": [
            _against("active"),
            _against("active", config=None, configLoading=True),
            _against("active", config=None),
            _against("active", config=CONFIG_NO_RESUME),
            _against("profile", otherProfileId="staff"),
            _against("profile", otherProfileId=""),
            _against("text", resumeText="My resume"),
            _against("active", profiles=[], activeProfileId=None, config=None),
        ],
        "can": [
            {"jobMode": "url", "jobUrl": " https://x.test/1 ", "jobText": "", "mode": "active"},
            {"jobMode": "url", "jobUrl": "  ", "jobText": "ignored", "mode": "active"},
            {"jobMode": "text", "jobUrl": "", "jobText": "A posting", "mode": "text", "resumeText": " "},
            {"jobMode": "text", "jobUrl": "", "jobText": "A posting", "mode": "text", "resumeText": "My resume"},
            {"jobMode": "url", "jobUrl": "https://x.test/1", "mode": "profile", "otherProfileId": ""},
            {"jobMode": "url", "jobUrl": "https://x.test/1", "mode": "profile", "otherProfileId": "staff"},
        ],
        "notImported": [6926, 1, 0, None, -3, "12", True],
        "timeLabels": {"2026-09-25T10:00:00Z": "3 days ago", "2026-09-28T10:00:00Z": "2 hours ago", "2026-09-28T10:30:00Z": "2 hours ago"},
        "searches": [
            # ready, nothing new from Exa
            {"source": "index", "status": "done", "cached": 10362, "requests": 0, "index": _search_index("ready")},
            # Exa found companies that were not stored yet
            {
                "source": "index",
                "status": "done",
                "cached": 1,
                "index": _search_index("stale", indexed=1, oldest_checked_at="2026-09-28T10:00:00Z", newest_checked_at="2026-09-28T10:30:00Z"),
                "exa_new": {"cap": 20, "found": 27, "fetched": 18, "failed": 2, "waiting": 7, "requests": 40},
            },
            {"source": "index", "status": "done", "index": _search_index("ready", indexed=3), "exa_new": {"cap": 20, "found": 2, "fetched": 1, "failed": 0, "waiting": 1}},
            # nothing stored: the run fails at acquire, its progress says why
            {"source": "index", "status": "done", "cached": 0, "index": _search_index("empty")},
            # the read is still going
            {"source": "index", "status": "running", "total": 10370},
            # a run sealed before the swap, and no boards block at all
            {"total": 10370, "status": "done", "fetched": 2800},
            None,
        ],
        "boardLines": [
            {"atsEnabled": True, "index": _index("ready"), "lastUpdated": "2 hours ago"},
            {"atsEnabled": True, "index": _index("ready", companies_indexed=1), "lastUpdated": ""},
            {"atsEnabled": True, "index": _index("stale"), "lastUpdated": "2 days ago"},
            {"atsEnabled": True, "index": _index("empty")},
            {"atsEnabled": True, "index": None},
            {"atsEnabled": True, "index": None, "failed": True},
            {"atsEnabled": False, "index": _index("ready")},
        ],
    }


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; uat-ui-batch2 UI model checks not run")
    script = NODE_SCRIPT
    for name, path in (
        ("{job_model_url}", JOB_MODEL_JS),
        ("{sources_url}", SOURCES_MODEL_JS),
        ("{assess_url}", ASSESS_MODEL_JS),
        ("{run_text_url}", RUN_TEXT_JS),
    ):
        script = script.replace(name, json.dumps(path.resolve().as_uri()))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(_payload())],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken script, not a model bug
        raise AssertionError(f"node printed no JSON: {completed.stdout!r}\n{completed.stderr}") from exc


def _by_id(cards: list[dict], job_id: str) -> dict:
    return next(card for card in cards if card["id"] == job_id)


# --- uat-bug-015: the Jev tile of a quick assessment ---------------------------------


def test_every_skip_reason_the_backend_can_emit_has_words(out: dict) -> None:
    assert out["skipText"] == {
        "no_key": "No Jev key",
        "ephemeral_resume": "Pasted resume: not sent to Jev",
        "no_title_or_company": "No title or company to score",
        "cost_cap": "Jev budget reached",
        "error": "Jev unavailable",
        "some_new_reason": "Some new reason",  # an id this table does not know is humanized
    }
    assert set(RANK_SKIP_REASONS) <= set(out["skipText"])
    for raw, words in out["skipText"].items():
        assert not RAW_ID.search(words), f"{raw!r} still reads as an id: {words!r}"
    assert out["skipNone"] == ["", "", ""]


def test_a_quick_assessment_carries_its_jev_score_or_why_not(out: dict) -> None:
    no_key, scored, pasted, old = out["quickOnly"]
    assert no_key["rank"] is None and no_key["rankSkipReason"] == "no_key" and no_key["skipText"] == "No Jev key"
    assert scored["rank"] == RANK_89 and scored["scored"] is True
    assert scored["rankSkipReason"] is None and scored["skipText"] == ""
    assert pasted["rank"] is None and pasted["skipText"] == "Pasted resume: not sent to Jev"
    # A file written before the fields existed: no score, and no reason invented.
    assert old["rank"] is None and old["rankSkipReason"] is None and old["skipText"] == ""


def test_the_quick_assessments_own_score_wins_over_the_runs(out: dict) -> None:
    # rank = item.rank_score || rankByUrl.get(identity) || null
    assert out["quickOnlyWithRunRank"]["rank"] == RANK_89


def test_a_run_row_jev_never_scored_shows_why_its_quick_assessment_has_none(out: dict) -> None:
    acme = _by_id(out["built"], ACME)
    assert acme["status"] != "on_demand"
    assert acme["scored"] is False and acme["rankSkipReason"] == "no_key"
    globex = _by_id(out["built"], GLOBEX)
    assert globex["rank"]["score"] == 12 and globex["rankSkipReason"] is None  # the run's own score stays


# --- uat-bug-014: the posting text -------------------------------------------------


def test_a_quick_assessed_url_job_shows_its_posting_text(out: dict) -> None:
    no_key, scored, pasted, old = out["quickOnly"]
    assert scored["text"] == POSTING_TEXT
    assert scored["excerpt"]["text"].startswith("Kong builds API infrastructure.")
    assert no_key["excerpt"] == {"text": "Build reliable Python services.", "truncated": False}
    # Pasted job text is never stored, and an older file has none: no excerpt.
    assert pasted["text"] is None and pasted["excerpt"] is None
    assert old["text"] is None and old["excerpt"] is None


def test_a_run_row_with_no_text_shows_the_text_its_quick_assessment_fetched(out: dict) -> None:
    assert _by_id(out["built"], ACME)["text"] == "Build reliable Python services."
    assert _by_id(out["built"], GLOBEX)["text"] == "Globex runs logistics software."  # the row's own text stays


# --- uat-bug-016: Assessments ------------------------------------------------------


def test_jobs_lists_run_postings_only(out: dict) -> None:
    assert out["runJobs"] == [ACME, GLOBEX]
    on_demand = [card["id"] for card in out["built"] if card["status"] == "on_demand"]
    assert sorted(on_demand) == sorted([KONG, PASTED])  # still jobs, so their job page is found by id


def test_assessments_is_every_on_demand_assessment_newest_first(out: dict) -> None:
    cards = out["assessments"]
    assert [card["id"] for card in cards] == [KONG, ACME, PASTED, GLOBEX]
    assert [card["assessedAt"] for card in cards] == sorted((card["assessedAt"] for card in cards), reverse=True)
    # One card per job: KONG's older not_a_match entry is not a second card.
    assert _by_id(cards, KONG)["verdict"] == "matched_above_threshold"
    # A job the loaded run also carries is that run's job (the card Jobs shows).
    assert _by_id(cards, ACME)["status"] != "on_demand" and _by_id(cards, GLOBEX)["rank"]["score"] == 12
    assert _by_id(cards, KONG)["status"] == "on_demand"
    assert out["sortedNewest"] == [KONG, ACME, PASTED, GLOBEX]


def test_assessments_does_not_need_a_loaded_run(out: dict) -> None:
    cards = out["assessmentsAlone"]
    assert [card["id"] for card in cards] == [KONG, ACME, PASTED, GLOBEX]
    assert all(card["status"] == "on_demand" for card in cards)
    assert out["assessmentsEmpty"] == [[], []]


def test_a_pasted_resume_assessment_is_a_card_on_assessments(out: dict) -> None:
    from gigai.scout import quick_assess

    assert out["pastedKey"] == quick_assess.EPHEMERAL_RESUME_KEY == quick_assess.resume_key(None)
    assert out["usedPasted"] == [True, True, False, False, False, False]
    cards = out["withPasted"]
    assert [card["id"] for card in cards] == [ACME, KONG, PASTED, GLOBEX, "text:77aa"]
    acme = _by_id(cards, ACME)
    # The newest assessment of ACME used a pasted resume: the card is the
    # store's own, never the run posting's card with that verdict on it.
    assert acme["pastedResume"] is True and acme["status"] == "on_demand" and acme["verdict"] == "not_a_match"
    assert acme["skipText"] == "Pasted resume: not sent to Jev"
    # ...and Jobs still shows the run posting with the PROFILE's verdict.
    assert _by_id(out["built"], ACME)["verdict"] == "pending_user_answers"
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert "getAssessments({ profileId: PASTED_RESUME_KEY })" in view
    merge = view[view.index("const jobs = useMemo(() => buildJobs(") :][:200]
    assert "pastedItems" not in merge, "a pasted-resume assessment must not reach the run rows' merge"


def test_assess_a_job_lives_on_assessments_not_on_jobs() -> None:
    find_jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assessments = (UI_SRC / "views" / "AssessmentsView.jsx").read_text(encoding="utf-8")
    assert "+ Assess a job" in assessments and "href={ASSESS_HASH}" in assessments
    assert 'data-action="assess"' not in find_jobs and "ASSESS_HASH" not in find_jobs
    # Jobs' grid is the run's postings; the Assessments grid sorts newest first.
    jobs_page = find_jobs[find_jobs.index("  const grid = (") :]
    assert "jobs={runJobs}" in jobs_page and "jobs={jobs}" not in jobs_page
    assert 'from="assessments"' in assessments
    grid = (UI_SRC / "components" / "JobsGrid.jsx").read_text(encoding="utf-8")
    assert "assessments ? sortByAssessedAt(matching) : sortJobs(matching)" in grid
    # Nothing the operator assessed is hidden because Jev scored it low.
    assert "assessments ? { ...shownFilters, showHidden: true }" in grid


def test_an_assessment_card_opens_its_page_under_assessments() -> None:
    card = (UI_SRC / "components" / "JobCard.jsx").read_text(encoding="utf-8")
    assert 'href={from === "assessments" ? assessmentHash(job.id) : jobHash(job.id)}' in card
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert "← Assessments" in page and "← Jobs" in page
    app = (UI_SRC / "App.jsx").read_text(encoding="utf-8")
    assert "navigate(assessmentHash(response.job.job_identity))" in app  # the #/assess flow ends on the job page
    # Questions lists assessments from the same store: its links open there too.
    questions = (UI_SRC / "views" / "PendingAnswersView.jsx").read_text(encoding="utf-8")
    assert "href={assessmentHash(item.job.job_identity)}" in questions and "jobHash" not in questions
    routing = (UI_SRC / "routing.js").read_text(encoding="utf-8")
    nav = routing[routing.index("export function navViewFor") :]
    assert 'view === "assessment" || view === "assess"' in nav and 'return "assessments";' in nav


# --- uat-bug-013: the Assess page's profile ----------------------------------------------


def test_the_assess_page_names_the_active_profile_and_its_resume(out: dict) -> None:
    active, loading, failed, no_resume, other, unchosen, pasted, nothing = out["against"]
    assert active["line"] == "Assessing against: default · kar-staff-resume.md · added Sep 23"
    assert active["who"] == "default" and active["tooltip"] == "rec_1 (rev_1)"
    assert loading["line"] == "Assessing against: default · loading its resume…"
    assert failed["line"] == "Assessing against: default"  # the read failed: the profile, and nothing invented
    assert no_resume["line"] == "Assessing against: default · no resume added yet"
    assert other["line"] == "Assessing against: Staff roles · its own resume" and other["tooltip"] == "rec_2 (rev_9)"
    assert unchosen["line"] == "Assessing against: choose a profile"
    assert pasted["line"] == "Assessing against: a pasted resume · used for this assessment only, never stored"
    assert nothing["line"] == "Assessing against: the selected profile"
    for item in out["against"]:
        assert "undefined" not in item["line"] and "null" not in item["line"]


def test_what_the_page_says_is_what_it_sends(out: dict) -> None:
    active, _, _, _, other, unchosen, pasted, nothing = out["resumes"]
    assert active == {"profile_id": "default"}
    assert other == {"profile_id": "staff"}
    assert unchosen == {"profile_id": None}
    assert pasted == {"resume_text": "My resume"}
    assert nothing == {"profile_id": None}  # the server reads null as the selected profile
    assert out["others"] == ["staff"]  # the secondary choice never repeats the active profile


def test_assess_is_enabled_by_default_with_a_posting(out: dict) -> None:
    assert out["can"] == [True, False, False, True, False, True]
    profile_note, other_note, pasted_note = out["privacy"]
    assert profile_note == other_note == "Your resume is sent to Jev to rank postings."
    assert "not sent to Jev" in pasted_note


def test_another_profile_or_a_pasted_resume_is_the_secondary_choice() -> None:
    view = (UI_SRC / "views" / "AssessView.jsx").read_text(encoding="utf-8")
    assert 'useState("active")' in view, "the Assess page must start on the active profile"
    assert 'data-role="assessing-against"' in view and "Use another profile or paste a resume" in view
    # The old primary toggle is gone.
    assert "Use a profile's resume" not in view and "Paste resume text" not in view
    # The secondary block is drawn only once asked for.
    assert '{(mode !== "active" || choosing) && (\n            <div className="assess-other"' in view
    assert "activeProfileId: selectedProfileId" in view
    # The resume label is the app's own GET /api/config (re-read when the
    # active profile changes): the page adds no request of its own.
    assert "getConfig" not in view
    app = (UI_SRC / "App.jsx").read_text(encoding="utf-8")
    assert "config={configStale ? null : configResponse}" in app
    switch = app[app.index("function handleSelectProfile") :][:400]
    assert "refreshConfig()" in switch


# --- N11-C: Update sources ---------------------------------------------------------


def test_the_model_knows_every_update_status_the_backend_has() -> None:
    statuses = {
        sources_update.STATUS_RUNNING,
        sources_update.STATUS_SUCCEEDED,
        sources_update.STATUS_PARTIAL,
        sources_update.STATUS_FAILED,
        sources_update.STATUS_INTERRUPTED,
    }
    assert statuses == {"running", "succeeded", "partial", "failed", "interrupted"}
    model = SOURCES_MODEL_JS.read_text(encoding="utf-8")
    for status in statuses:
        assert f'"{status}"' in model, f"sourcesModel.js has no case for {status!r}"


def test_the_progress_bar_is_done_of_total_and_indeterminate_at_zero(out: dict) -> None:
    none, running, listing, one, *ended = out["progress"]
    assert none is None
    assert running == {"determinate": True, "percent": 21, "line": "2,140 of 10,370 company boards checked, 8 did not answer."}
    assert listing == {"determinate": False, "percent": None, "line": "Listing the company boards to check…"}
    assert one == {"determinate": True, "percent": 100, "line": "1 of 1 company board checked."}
    assert ended == [None] * 6  # nothing to draw once the update ended


def test_the_result_line_is_the_servers_summary(out: dict) -> None:
    none, running, listing, one, succeeded, partial, partial_one, failed, interrupted, bare = out["results"]
    assert none is None and running is None and listing is None and one is None
    assert succeeded == {
        "tone": "ok",
        "line": "120 companies with new postings: 412 new, 95 changed, 230 removed",
        "detail": "Checked 10,370 of 10,370 company boards; 11 did not answer.",
        "next": "",
    }
    assert partial["tone"] == "warn" and partial["line"] == succeeded["line"]
    assert partial["detail"] == "Checked 2,140 of 10,370 company boards; 8 did not answer."  # skipped boards were not checked
    assert partial["next"] == "Run again to continue: 8,230 company boards are left."
    assert partial_one["next"] == "Run again to continue: 1 company board is left."
    assert failed == {"tone": "danger", "line": "The update failed.", "detail": "the watchlist could not be read", "next": ""}
    assert interrupted["tone"] == "warn" and interrupted["next"] == "Run again to continue."
    assert bare == {"tone": "ok", "line": "Sources are up to date.", "detail": "", "next": ""}


def test_jobs_shows_the_index_message_only_when_an_update_is_needed(out: dict) -> None:
    empty, stale_running, ready, stale_no_message, missing, no_index = out["notices"]
    assert empty == {
        "message": "No company postings are stored on this machine yet. Run Update sources, then search again.",
        "running": False,
        "status": "empty",
    }
    assert stale_running["message"] == "The stored company postings are out of date. Run Update sources, then search again."
    assert stale_running["running"] is True
    assert ready is None and missing is None and no_index is None
    assert stale_no_message["message"].endswith("Run Update sources, then search again.")
    assert out["noticeAtsOff"] is None  # company boards are off: nothing to update
    assert out["running"] == [False, True, False, False, False, False]
    assert out["stored"] == ["", "10,362 companies stored on this machine", "10,362 companies stored on this machine", "10,362 companies stored on this machine", "", ""]


def test_a_running_update_that_stopped_moving_offers_to_start_over(out: dict) -> None:
    assert out["stuck"] == [False, True, False, False, False]


def test_a_refused_start_says_why(out: dict) -> None:
    running, no_target, refused, unreachable, nothing = out["startErrors"]
    assert running == "An update is already running."
    assert no_target.startswith("No Scout project is set up")
    assert refused == "origin not allowed"
    assert unreachable == "Could not reach the local API."
    assert nothing == "The update could not be started."


def test_the_settings_action_and_the_jobs_message_are_wired() -> None:
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    # POST with a JSON body ({} or {"force": true}), so the CSRF guard's
    # content-type rule is met.
    assert 'request("POST", "/api/sources/update", force ? { force: true } : {})' in api
    assert 'request("GET", "/api/sources/update")' in api
    panel = (UI_SRC / "components" / "SourcesUpdatePanel.jsx").read_text(encoding="utf-8")
    assert "disabled={running || starting || !status}" in panel
    assert 'data-action="update-sources"' in panel and "Update sources" in panel
    assert "setTimeout(read, SOURCES_POLL_MS)" in panel and "isRunning(response)" in panel
    settings = (UI_SRC / "views" / "SettingsView.jsx").read_text(encoding="utf-8")
    assert "<SourcesUpdatePanel />" in settings
    find_jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    assert 'data-role="index-notice"' in find_jobs and "<a href={SETTINGS_HASH}>" in find_jobs
    assert 'useSourcesStatus({ enabled: route.view === "jobs" })' in find_jobs


# --- uat-bug-011 ------------------------------------------------------------------


def test_not_imported_is_said_only_above_zero(out: dict) -> None:
    many, one, zero, missing, negative, text, boolean = out["notImported"]
    assert many == "6,926 more matched, not imported this run."
    assert one == "1 more matched, not imported this run."
    assert zero is None and missing is None and negative is None and text is None and boolean is None


def test_the_run_page_reads_the_field_the_progress_route_carries() -> None:
    from gigai.scout.find_jobs.progress import ProgressSnapshot

    assert "not_imported_count" in ProgressSnapshot.__dataclass_fields__
    find_jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    run_page = find_jobs[find_jobs.index('if (route.view === "run") {') :]
    run_page = run_page[: run_page.index("\n  return (\n    <div>\n      <JobsSummaryStrip")]
    assert "notImported={progress?.not_imported_count}" in run_page
    # A finished run opened later has its progress read once, for the run shown.
    start = find_jobs.index("getRunProgress(pastRunId)")
    assert "shownRunId.current === pastRunId" in find_jobs[start : start + 200]
    status_list = (UI_SRC / "components" / "NodeStatusList.jsx").read_text(encoding="utf-8")
    assert "notImportedLine(notImported)" in status_list and 'data-role="not-imported"' in status_list


# --- run dialog -------------------------------------------------------------------


def test_the_run_dialog_counts_the_stored_company_boards(out: dict) -> None:
    ready, one, stale, empty, loading, failed, off = out["boardLines"]
    assert ready == {"line": "10,362 company boards indexed (last updated 2 hours ago).", "needsUpdate": False}
    assert one == {"line": "1 company board indexed.", "needsUpdate": False}
    assert stale == {
        "line": "10,362 company boards indexed (last updated 2 days ago). They are out of date: update sources first.",
        "needsUpdate": True,
    }
    assert empty == {"line": "Update sources first: no company postings are stored on this machine yet.", "needsUpdate": True}
    assert loading == {"line": "Counting the company boards stored on this machine…", "needsUpdate": False}
    assert "could not be loaded" in failed["line"] and failed["needsUpdate"] is False
    assert off["line"].startswith("Not checked") and off["needsUpdate"] is False
    for item in out["boardLines"]:
        line = item["line"]
        assert "?" not in line and "unknown" not in line.lower() and "null" not in line and "undefined" not in line, line


def test_the_run_dialog_no_longer_reads_the_watchlist() -> None:
    dialog = (UI_SRC / "components" / "RunConfirmDialog.jsx").read_text(encoding="utf-8")
    assert "getWatchlist" not in dialog and "AddCompanyForm" not in dialog
    assert "getSourcesUpdate()" in dialog and "indexedBoardsLine(" in dialog
    assert 'data-role="company-boards"' in dialog and "href={SETTINGS_HASH}" in dialog


# --- N11-C part 2: a search reads the stored company postings ------------------------


def test_a_search_says_what_it_read_in_words(out: dict) -> None:
    ready, exa, exa_one, empty, running, before_swap, no_boards = out["searches"]
    assert ready == [{"role": "read", "text": "Read 10,362 indexed companies (updated between 3 days ago and 2 hours ago).", "settings": False}]
    assert exa == [
        {"role": "read", "text": "Read 1 indexed company (updated 2 hours ago).", "settings": False},
        {"role": "fetched", "text": "Fetched 18 new companies found by Exa; 2 did not answer.", "settings": False},
        {"role": "waiting", "text": "7 more wait for the next Update sources.", "settings": True},
        {"role": "update", "text": "The stored company postings are out of date. Run Update sources, then search again.", "settings": True},
    ]
    assert [line["text"] for line in exa_one] == [
        "Read 3 indexed companies (updated between 3 days ago and 2 hours ago).",
        "Fetched 1 new company found by Exa.",
        "1 more waits for the next Update sources.",
    ]
    assert empty == [
        {"role": "update", "text": "No company postings are stored on this machine yet. Run Update sources, then search again.", "settings": True}
    ]
    assert running == [{"role": "read", "text": "Reading the company postings stored on this machine…", "settings": False}]
    # A run sealed before the swap (or no boards block): not a search of the
    # index, so the rotation line stands.
    assert before_swap is None and no_boards is None
    assert out["searchNoLabels"] == [{"role": "read", "text": "Read 10,362 indexed companies.", "settings": False}]
    for lines in out["searches"]:
        for line in lines or []:
            text = line["text"]
            assert not RAW_ID.search(text), f"a raw key in user text: {text!r}"
            assert "?" not in text and "unknown" not in text.lower() and "null" not in text and "undefined" not in text, text


def test_the_index_state_keys_the_ui_reads_are_the_backends() -> None:
    from gigai.scout.find_jobs.company_index import INDEX_EMPTY, IndexState

    state = IndexState(INDEX_EMPTY, 3, 0, 3, None, None, 24.0).to_json()
    assert {"status", "needs_update", "message", "indexed", "oldest_checked_at", "newest_checked_at"} <= set(state)
    assert _search_index("empty")["message"] == state["message"]
    text = RUN_TEXT_JS.read_text(encoding="utf-8")
    for key in ("index.indexed", "index.oldest_checked_at", "index.newest_checked_at", "index.needs_update", "index.message", "exa.fetched", "exa.waiting", "exa.failed"):
        assert key in text, f"runText.searchLines does not read {key}"


def test_a_search_has_no_rotation_line() -> None:
    status_list = (UI_SRC / "components" / "NodeStatusList.jsx").read_text(encoding="utf-8")
    assert "const line = search ? null : rotationLine(rotation, boards);" in status_list
    assert "searchLines(boards, relativeTimeLabel)" in status_list
    assert "data-role={`search-${item.role}`}" in status_list and "<a href={SETTINGS_HASH}>" in status_list
