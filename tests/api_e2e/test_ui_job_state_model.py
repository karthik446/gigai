"""uat-bug-018 (phase B): the job state in the UI.

``ui/src/jobStateModel.js`` is pure JavaScript, so its rules run under the
system ``node`` (the way ``test_ui_job_model_sort.py`` does; a LOUD skip
when ``node`` is not on PATH) over one fixture:

* every state the backend can serve (``job_state.JOB_STATES``, read from
  the Python module) and every event kind it accepts next has WORDS; a
  state id is never what the operator reads;
* a job's state follows the server's precedence over what the page holds:
  application events > tailored resume > the latest verdict;
* the filter chips count the jobs in each state, in pipeline order, and
  only the states some job is in have a chip;
* the "need your answers" count is per list;
* Applications lists one row per JOB that is applied or beyond.

The views are checked statically (no JS test runner, no browser): the
state chips are on the grid Jobs and Assessments share, the job page
records the next events by ``job_identity``, Applications reads the jobs'
states, and the Questions tab's count moved to the Jobs and Assessments
links.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.application_events import EVENT_KINDS
from gigai.scout.find_jobs import job_state
from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
JOB_STATE_MODEL_JS = UI_SRC / "jobStateModel.js"
JOB_MODEL_JS = UI_SRC / "jobModel.js"

ACME = "https://boards.greenhouse.io/acme/jobs/101"
GLOBEX = "https://boards.greenhouse.io/globex/jobs/7"
INITECH = "https://boards.greenhouse.io/initech/jobs/55"
KONG = "https://jobs.ashbyhq.com/kong/abc"
HOOLI = "https://jobs.lever.co/hooli/xyz"
UMBRELLA = "https://boards.greenhouse.io/umbrella/jobs/9"
PASTED = "text:sha256:" + "a" * 64
SAVED_ONLY = "https://boards.greenhouse.io/nowhere/jobs/999999"
DISCOVER = "opportunity_" + "0" * 32
NOW = "2026-09-28T12:00:00Z"

PIPELINE = ["interview_scheduled", "offer_received", "rejected", "withdrawn"]

NODE_SCRIPT = """
import * as model from {module_url};
import { buildJobs, assessmentJobs, runJobs, filterJobs, EMPTY_FILTERS, hasActiveFilter } from {job_model_url};
const input = JSON.parse(process.argv[1]);
const now = new Date(input.now).getTime();

const built = buildJobs(input.build);
const jobs = model.withJobStates(built, input.applications, input.tailoredIds);
const run = runJobs(jobs);
const assessed = model.withJobStates(assessmentJobs(input.build.quickItems, jobs, [], []), input.applications, input.tailoredIds);
const brief = (job) => ({ id: job.id, state: job.state.state, since: job.state.since, nextEvents: job.state.nextEvents });
const applicationJobs = model.applicationJobs(input.applications, { known: model.namesFromAssessments(input.build.quickItems), now });

process.stdout.write(JSON.stringify({
  labels: Object.fromEntries(input.states.map((state) => [state, model.stateLabel(state)])),
  actions: Object.fromEntries(input.eventKinds.map((kind) => [kind, model.eventActionLabel(kind)])),
  unknownLabel: model.stateLabel("some_new_state"),
  unknownAction: model.eventActionLabel("some_new_event"),
  order: model.STATE_ORDER,
  applicationStates: model.APPLICATION_STATES,
  isApplication: input.states.filter((state) => model.isApplicationState(state)),
  run: run.map(brief),
  assessed: assessed.map(brief),
  withoutApplications: model.withJobStates(built, [], []).map(brief),
  options: model.stateOptions(run),
  optionsSelected: model.stateOptions(run, "withdrawn"),
  optionsEmpty: model.stateOptions([]),
  counts: model.stateCounts(run),
  waiting: { run: model.needAnswersCount(run), assessed: model.needAnswersCount(assessed), none: model.needAnswersCount([]) },
  waitingLabels: [model.needAnswersLabel(1), model.needAnswersLabel(4)],
  filtered: Object.fromEntries(input.states.concat(["all"]).map((state) => [state, filterJobs(run, { ...EMPTY_FILTERS, showHidden: true, state }).map((job) => job.id)])),
  emptyFilters: EMPTY_FILTERS,
  activeFilter: [hasActiveFilter(EMPTY_FILTERS), hasActiveFilter({ ...EMPTY_FILTERS, state: "applied" })],
  applicationJobs: applicationJobs.map((job) => ({ ...job, events: job.events.map((event) => event.event_kind) })),
  inProgress: model.inProgressCount(input.applications),
  inProgressNone: model.inProgressCount([]),
  identities: input.applications.map((row) => model.applicationIdentity(row)),
}));
"""


def _posting(url: str, title: str, company: str) -> dict:
    return {"normalized_url": url, "url": url, "title": title, "company": company, "location": "Remote", "provider": "greenhouse", "source_kind": "ats", "published_at": "2026-09-20T00:00:00+00:00"}


def _state(state: str, since: str | None, following: list[str]) -> dict:
    return {"state": state, "since": since, "next_events": following}


def _row(url: str, title: str, company: str, verdict: str | None, job_state_value: dict | None) -> dict:
    """One ``boardRows.rowsFromResults`` row (``jobState`` is ``rows[].job_state``)."""

    assessment = None if verdict is None else {"verdict": verdict, "matrix": [], "structured_questions": []}
    return {
        "posting": _posting(url, title, company),
        "status": "assessed" if assessment else "not_assessed",
        "assessment": assessment,
        "jobState": job_state_value,
    }


def _quick(identity: str, verdict: str, *, at: str, title: str = "", company: str = "", served: dict | None = None, pasted: bool = False, origin: str = "quick_assess") -> dict:
    item = {
        "job": {
            "job_identity": identity,
            "normalized_url": None if pasted else identity,
            "source_url": None if pasted else identity,
            "fetch_kind": "pasted" if pasted else "generic",
            "title": title,
            "company": company,
            "location": "",
        },
        "resume": {"profile_id": "profile_1", "pinned": None},
        "result": {"verdict": verdict, "matrix": [], "structured_questions": []},
        "origin": origin,
        "created_at": at,
        "updated_at": at,
        "history": [],
    }
    if served is not None:
        item["job_state"] = served
    return item


def _event(identity: str, kind: str, at: str, served: dict | None, *, linked: dict | None = None, field: str = "external_ref") -> dict:
    return {"event_id": f"event_{kind}_{at}", field: identity, "event_kind": kind, "occurred_at": at, "current": True, "linked_posting": linked, "job_state": served}


def _fixture() -> dict:
    applied_globex = _state("interview_scheduled", "2026-09-10T10:00:00Z", ["offer_received", "rejected", "withdrawn"])
    applied_pasted = _state("applied", "2026-09-05T10:00:00Z", PIPELINE)
    applied_hooli = _state("rejected", "2026-09-26T10:00:00Z", [])
    applied_discover = _state("offer_received", "2026-09-27T10:00:00Z", ["rejected", "withdrawn"])
    rows = [
        # The run says "needs your answers"; nothing newer anywhere.
        _row(ACME, "Staff Engineer", "acme", "pending_user_answers", _state("needs_answers", "2026-09-24T00:00:00Z", ["applied"])),
        # The served state is older than the applications list: the list wins.
        _row(GLOBEX, "Platform Engineer", "globex", "matched_above_threshold", _state("matched", "2026-09-24T00:00:00Z", ["applied"])),
        # Served as tailored.
        _row(INITECH, "Backend Engineer", "initech", "not_a_match", _state("tailored", "2026-09-25T09:00:00Z", ["applied"])),
        # The run never assessed it; the quick store did, after the run, and
        # that response was merged in place (it carries no job_state).
        _row(KONG, "SRE", "kong", None, _state("not_assessed", None, ["applied"])),
        # A live row (no served state yet) the operator tailored on this page.
        _row(UMBRELLA, "Data Engineer", "umbrella", "matched_above_threshold", None),
        _row(HOOLI, "ML Engineer", "hooli", "matched_above_threshold", _state("matched", "2026-09-24T00:00:00Z", ["applied"])),
    ]
    quick_items = [
        # Assessed from the run posting's job page: it stays under Jobs.
        _quick(KONG, "matched_above_threshold", at="2026-09-25T00:00:00Z", title="SRE", company="kong", origin="job_page"),
        _quick(PASTED, "pending_user_answers", at="2026-09-26T00:00:00Z", title="Pasted Role", company="Pasted Co", pasted=True, served=_state("needs_answers", "2026-09-26T00:00:00Z", ["applied"])),
        _quick("https://careers.example.test/jobs/9", "pending_user_answers", at="2026-09-27T00:00:00Z", title="On demand", company="Example", served=_state("needs_answers", "2026-09-27T00:00:00Z", ["applied"])),
    ]
    applications = [
        _event(GLOBEX, "applied", "2026-09-01T10:00:00Z", applied_globex, linked=_posting(GLOBEX, "Platform Engineer", "globex")),
        _event(GLOBEX, "interview_scheduled", "2026-09-10T10:00:00Z", applied_globex, linked=_posting(GLOBEX, "Platform Engineer", "globex")),
        _event(PASTED, "applied", "2026-09-05T10:00:00Z", applied_pasted),
        _event(HOOLI, "applied", "2026-09-20T10:00:00Z", applied_hooli, linked=_posting(HOOLI, "ML Engineer", "hooli")),
        _event(HOOLI, "rejected", "2026-09-26T10:00:00Z", applied_hooli, linked=_posting(HOOLI, "ML Engineer", "hooli")),
        _event(SAVED_ONLY, "saved", "2026-09-02T10:00:00Z", None),
        _event(DISCOVER, "offer_received", "2026-09-27T10:00:00Z", applied_discover, field="opportunity_ref"),
    ]
    return {
        "now": NOW,
        "states": list(job_state.JOB_STATES),
        "eventKinds": sorted(EVENT_KINDS - {"saved"}),
        "build": {"rows": rows, "rankScores": [], "quickItems": quick_items, "runCreatedAt": "2026-09-24T00:00:00Z"},
        "applications": applications,
        "tailoredIds": [UMBRELLA],
    }


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the job state model check was not run")
    assert node is not None
    script = NODE_SCRIPT.replace("{module_url}", json.dumps(JOB_STATE_MODEL_JS.resolve().as_uri())).replace(
        "{job_model_url}", json.dumps(JOB_MODEL_JS.resolve().as_uri())
    )
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(_fixture())],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def _by_id(jobs: list[dict], identity: str) -> dict:
    return next(job for job in jobs if job["id"] == identity)


# --- words, never ids --------------------------------------------------------------------


def test_every_state_the_backend_serves_has_words(out: dict) -> None:
    assert out["labels"] == {
        "not_assessed": "Not assessed",
        "assessed": "Assessed",
        "needs_answers": "Needs your answers",
        "weak_fit": "Weak fit",
        "matched": "Matched",
        "not_a_match": "Not a match",
        "tailored": "Resume tailored",
        "applied": "Applied",
        "interview_scheduled": "Interview scheduled",
        "offer_received": "Offer received",
        "rejected": "Rejected",
        "withdrawn": "Withdrawn",
    }
    assert set(out["labels"]) == set(job_state.JOB_STATES)
    for state, label in out["labels"].items():
        assert "_" not in label and label != state and label[0].isupper()
    # A state this page has never heard of is still words.
    assert out["unknownLabel"] == "Some new state"


def test_every_event_a_job_accepts_next_has_a_button(out: dict) -> None:
    accepted = {kind for state in job_state.JOB_STATES for kind in job_state.next_events(state)}
    assert accepted == set(out["actions"]) == EVENT_KINDS - {"saved"}
    assert out["actions"] == {
        "applied": "Mark applied",
        "interview_scheduled": "Interview scheduled",
        "offer_received": "Offer received",
        "rejected": "Rejected",
        "withdrawn": "Withdrawn",
    }
    assert out["unknownAction"] == "Some new event"


def test_the_ui_knows_the_same_states_as_the_backend(out: dict) -> None:
    assert sorted(out["order"]) == sorted(job_state.JOB_STATES)
    assert out["applicationStates"] == list(job_state.APPLICATION_STATES)
    assert out["isApplication"] == list(job_state.APPLICATION_STATES)


# --- one job's state ---------------------------------------------------------------------


def test_a_jobs_state_follows_the_precedence(out: dict) -> None:
    run = out["run"]
    assert [job["id"] for job in run] == [ACME, GLOBEX, INITECH, KONG, UMBRELLA, HOOLI]
    # The verdict, with the server's `since`.
    assert _by_id(run, ACME) == {"id": ACME, "state": "needs_answers", "since": "2026-09-24T00:00:00Z", "nextEvents": ["applied"]}
    # Application events win over the row's served (older) state.
    assert _by_id(run, GLOBEX) == {"id": GLOBEX, "state": "interview_scheduled", "since": "2026-09-10T10:00:00Z", "nextEvents": ["offer_received", "rejected", "withdrawn"]}
    assert _by_id(run, HOOLI) == {"id": HOOLI, "state": "rejected", "since": "2026-09-26T10:00:00Z", "nextEvents": []}
    # A tailored resume wins over the verdict (not a match).
    assert _by_id(run, INITECH) == {"id": INITECH, "state": "tailored", "since": "2026-09-25T09:00:00Z", "nextEvents": ["applied"]}
    # A re-assessment merged in place: the latest verdict, not the row's
    # served "not assessed"; nobody said since when.
    assert _by_id(run, KONG) == {"id": KONG, "state": "matched", "since": None, "nextEvents": ["applied"]}
    # Tailored on this page since the lists were read.
    assert _by_id(run, UMBRELLA) == {"id": UMBRELLA, "state": "tailored", "since": None, "nextEvents": ["applied"]}


def test_without_applications_the_served_states_stand(out: dict) -> None:
    jobs = out["withoutApplications"]
    assert _by_id(jobs, GLOBEX)["state"] == "matched"
    assert _by_id(jobs, HOOLI)["state"] == "matched"
    assert _by_id(jobs, INITECH)["state"] == "tailored"
    assert _by_id(jobs, UMBRELLA) == {"id": UMBRELLA, "state": "matched", "since": None, "nextEvents": ["applied"]}
    assert _by_id(jobs, PASTED) == {"id": PASTED, "state": "needs_answers", "since": "2026-09-26T00:00:00Z", "nextEvents": ["applied"]}


def test_a_pasted_job_reaches_the_pipeline(out: dict) -> None:
    assessed = out["assessed"]
    assert [job["id"] for job in assessed] == ["https://careers.example.test/jobs/9", PASTED]
    assert _by_id(assessed, PASTED) == {"id": PASTED, "state": "applied", "since": "2026-09-05T10:00:00Z", "nextEvents": PIPELINE}


# --- chips and counts --------------------------------------------------------------------


def test_the_chips_count_the_jobs_in_each_state(out: dict) -> None:
    assert out["counts"] == {"needs_answers": 1, "interview_scheduled": 1, "tailored": 2, "matched": 1, "rejected": 1}
    # Pipeline order; only the states a job is in; "All" first.
    assert out["options"] == [
        {"value": "all", "label": "All", "count": 6},
        {"value": "needs_answers", "label": "Needs your answers", "count": 1},
        {"value": "matched", "label": "Matched", "count": 1},
        {"value": "tailored", "label": "Resume tailored", "count": 2},
        {"value": "interview_scheduled", "label": "Interview scheduled", "count": 1},
        {"value": "rejected", "label": "Rejected", "count": 1},
    ]
    assert sum(option["count"] for option in out["options"][1:]) == out["options"][0]["count"]
    # The chosen chip stays when its last job leaves the state.
    assert {"value": "withdrawn", "label": "Withdrawn", "count": 0} in out["optionsSelected"]
    assert out["optionsEmpty"] == [{"value": "all", "label": "All", "count": 0}]


def test_the_state_filter_is_the_grids(out: dict) -> None:
    assert out["emptyFilters"]["state"] == "all"
    assert out["activeFilter"] == [False, True]
    filtered = out["filtered"]
    assert filtered["all"] == [ACME, GLOBEX, INITECH, KONG, UMBRELLA, HOOLI]
    assert filtered["needs_answers"] == [ACME]
    assert filtered["tailored"] == [INITECH, UMBRELLA]
    assert filtered["interview_scheduled"] == [GLOBEX]
    assert filtered["rejected"] == [HOOLI]
    assert filtered["applied"] == [] and filtered["not_assessed"] == []
    for option in out["options"][1:]:
        assert len(filtered[option["value"]]) == option["count"]


def test_need_your_answers_is_counted_per_list(out: dict) -> None:
    assert out["waiting"] == {"run": 1, "assessed": 1, "none": 0}
    assert out["waitingLabels"] == ["1 job needs your answers", "4 jobs need your answers"]


# --- Applications ------------------------------------------------------------------------


def test_applications_lists_the_jobs_that_are_applied_or_beyond(out: dict) -> None:
    jobs = out["applicationJobs"]
    # One row per job, newest state first; a job that was only saved is not one.
    assert [job["identity"] for job in jobs] == [DISCOVER, HOOLI, GLOBEX, PASTED]
    assert SAVED_ONLY not in [job["identity"] for job in jobs]
    assert all(job["state"] in job_state.APPLICATION_STATES for job in jobs)

    globex = next(job for job in jobs if job["identity"] == GLOBEX)
    assert globex["state"] == "interview_scheduled" and globex["since"] == "2026-09-10T10:00:00Z"
    assert globex["title"] == "Platform Engineer" and globex["company"] == "globex" and globex["linked"] is True
    assert globex["events"] == ["applied", "interview_scheduled"]
    assert globex["nextEvents"] == ["offer_received", "rejected", "withdrawn"]
    # 18 days without news.
    assert globex["daysInState"] == 18 and globex["needsAction"] is True

    # A pasted posting is named by the quick store, never by its identity.
    pasted = next(job for job in jobs if job["identity"] == PASTED)
    assert pasted["title"] == "Pasted Role" and pasted["company"] == "Pasted Co"
    assert pasted["pasted"] is True and pasted["url"] is None and pasted["linked"] is False
    assert pasted["needsAction"] is True  # applied 23 days ago

    # A rejected job needs nothing, however long ago.
    hooli = next(job for job in jobs if job["identity"] == HOOLI)
    assert hooli["state"] == "rejected" and hooli["needsAction"] is False

    # A Discover event has no posting and no name: words, not its id.
    discover = next(job for job in jobs if job["identity"] == DISCOVER)
    assert discover["title"] == "Posting" and discover["url"] is None and discover["needsAction"] is False

    assert out["inProgress"] == 3  # interview + applied + offer; rejected is not in progress
    assert out["inProgressNone"] == 0
    assert out["identities"] == [GLOBEX, GLOBEX, PASTED, HOOLI, HOOLI, SAVED_ONLY, DISCOVER]


# --- the views (static) ------------------------------------------------------------------


def _source(*parts: str) -> str:
    return UI_SRC.joinpath(*parts).read_text(encoding="utf-8")


def test_jobs_and_assessments_share_the_state_chips() -> None:
    grid = _source("components", "JobsGrid.jsx")
    assert 'data-role="state-filter"' in grid and '<div className="chip-group-label">State</div>' in grid
    assert "{option.label} <span className=\"chip-count\">{option.count}</span>" in grid
    assert 'stateOptions(filterJobs(jobs, { ...effectiveFilters, state: "all" }), effectiveFilters.state)' in grid
    assert 'setFilter("state", value)' in grid
    # The verdict chips they replace are gone.
    assert "ASSESSED_OPTIONS" not in grid and 'label="Assessed"' not in grid
    # Assessments is the same grid.
    assert "<JobsGrid" in _source("views", "AssessmentsView.jsx")
    # Every job the grids get carries its state.
    view = _source("views", "FindJobsView.jsx")
    assert "withJobStates(buildJobs({ rows, rankScores, quickItems, runCreatedAt }), applications, tailoredIds)" in view
    assert "withJobStates(assessmentJobs(quickItems, jobs, pastedItems, runPostingIds), applications, tailoredIds)" in view
    assert "jobState: row.job_state || null" in _source("boardRows.js")


def test_the_job_page_shows_the_state_and_records_the_next_events() -> None:
    page = _source("views", "JobPage.jsx")
    assert "<JobStateActions jobId={job.id} state={state} pasted={pasted} onRecorded={handleApplicationRecorded} />" in page
    assert "<StateChip state={state} always showSince />" in page
    assert "state.nextEvents.map((eventKind) => (" in page
    assert "postApplication({ job_identity: jobId, event_kind: eventKind })" in page
    assert "eventActionLabel(eventKind)" in page
    # The buttons come from the state; none is written by hand, and the one
    # "Mark applied" of before is gone with its URL-only rule.
    assert "MarkApplied" not in page and "normalized_url: normalizedUrl" not in page
    assert "posting.url && <JobStateActions" not in page
    # A pasted posting says what editing its text means.
    assert "If you paste an edited version later, Scout treats it as a new job" in page
    # A tailored resume changes the state at once.
    assert "onTailored(tailoredJobId)" in page
    assert "{stateLabel(id)}" in _source("components", "StateChip.jsx")


def test_no_view_shows_a_state_id() -> None:
    """The ids are data (a filter value, a CSS class, a data- attribute);
    what is drawn comes from ``stateLabel`` / ``eventActionLabel``."""

    drawn = re.compile(r">\s*\{[^{}]*\b(?:state\.state|job\.state\.state|job\.state|option\.value|eventKind)\s*\}\s*<")
    for parts in (("components", "StateChip.jsx"), ("components", "JobsGrid.jsx"), ("components", "JobCard.jsx"), ("views", "JobPage.jsx"), ("views", "ApplicationsView.jsx"), ("components", "TopBar.jsx")):
        source = _source(*parts)
        assert drawn.search(source) is None, f"{parts[-1]} draws a raw state or event id"
    applications = _source("views", "ApplicationsView.jsx")
    assert "<StateChip state={{ state: job.state, since: job.since }} always />" in applications
    assert "stateLabel(filter)" in applications and "eventKindLabel(item.event_kind)" in applications
    assert '"Pasted posting"' in applications


def test_applications_is_the_applied_and_beyond_view() -> None:
    applications = _source("views", "ApplicationsView.jsx")
    assert "applicationJobs(applications, { known: names })" in applications
    assert 'data-role="state-filter"' in applications and 'data-role="applications-jobs"' in applications
    assert "job.pasted ? assessmentHash(job.identity) : jobHash(job.identity)" in applications
    # The old five-stage fold and its "needs action" over events are gone.
    model = _source("applicationsModel.js")
    for name in ("PIPELINE_STAGES", "pipelineCounts", "currentApplications"):
        assert name not in model and name not in applications
    assert "needsAction" not in model  # it is a job's now: jobStateModel.applicationJobs
    strip = _source("components", "JobsSummaryStrip.jsx")
    assert "inProgressCount(applications || [])" in strip and "pipelineCounts" not in strip


def test_the_questions_count_moved_to_jobs_and_assessments() -> None:
    bar = _source("components", "TopBar.jsx")
    assert 'needAnswers && (view === "jobs" || view === "assessments") ? needAnswers[view] : 0' in bar
    assert "aria-label={needAnswersLabel(badge)}" in bar and "questionsCount" not in bar
    app = _source("App.jsx")
    assert "needAnswers={needAnswers}" in app and "onNeedAnswers={setNeedAnswers}" in app
    view = _source("views", "FindJobsView.jsx")
    # 0.1.10.7 M4b: the Jobs count is the by-posting list's (GET /api/postings counts.by_state.needs_answers,
    # read with no filter on), handed up by JobsView; the Assessments count is unchanged.
    assert "onNeedAnswers({ jobs: postingsWaiting, assessments: assessmentsWaiting })" in view
    assert "onCounts={setPostingsWaiting}" in view and "needAnswersCount(assessed)" in view
    jobs = _source("views", "JobsView.jsx")
    assert "onCounts(needsAnswers(response.counts));" in jobs and "if (!hasFilter(filter)) {" in jobs
    assert '<Tile label="Need your answers"' in jobs and "Open questions" not in jobs and "QUESTIONS_HASH" not in jobs


def test_settings_reads_one_page_of_the_watchlist() -> None:
    """journal-read-scope: the full watchlist is ~10,000 entries, megabytes."""

    form = _source("components", "AddCompanyForm.jsx")
    assert 'request("GET", `/api/watchlist?limit=${WATCHLIST_SHOWN}`)' in form
    assert 'request("GET", "/api/watchlist")' not in form
    assert "export const WATCHLIST_SHOWN = 25;" in form
    assert "watchingLines(total, entries.length).heading" in form
    from gigai.scout.find_jobs.api.watchlist import WATCHLIST_PAGE_LIMIT_MAX

    assert 1 <= 25 <= WATCHLIST_PAGE_LIMIT_MAX
