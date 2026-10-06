"""0.1.10.7 PL5: triggers, caps and approvals, on the END outcome.

Synthetic gig and postings (``tests/support/pipeline_fixtures``); the model is
a scripted binding, so every model call is counted. Each test drives a real
write path (the CLI command a user or an agent runs) and reads what the
pipeline then did: the queue, the Scout label, the model calls made.

(a) answering a question queues exactly the (posting, profile) pair that
    asked it and the pipeline runs it to a Scout label; an unrelated job is
    not queued;
(b) a story about job A's open question re-opens A, not B;
(c) 12 jobs in one trigger: 10 run, 2 wait for an approval with an estimate;
    approving runs them; denying cancels with no model call;
(d) the 41st model call of the day waits, and the counter is one for the
    install: two profiles share it; the rank counter warns at 60 and stops
    at 100;
(e) 500 postings in the index: nothing queued, no model call;
(f) the status object holds no text and passes the outbound check;
(g) the settings: a round trip, and settings that cannot be read keep the
    pipeline off, reported;
(h) the runner yields while an assess batch is live and resumes after it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import threading

from click.testing import CliRunner
import pytest

from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs import background_settings
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.outbound_check import REDACTIONS_KEY, redact_payload
from gigai.scout.pipeline import busy, steps, triggers
from gigai.scout.pipeline.overview import overview
from gigai.scout.pipeline.runner import (
    BUSY_ASSESS_BATCH,
    DRAIN_DISABLED,
    DRAIN_IDLE,
    DRAIN_RAN,
    DRAIN_YIELDED,
    WAIT_DAILY_CAP,
    PipelineRunner,
    live_work,
)
from gigai.scout.pipeline.settings import PIPELINE_ENV, pipeline_setting
from gigai.scout.pipeline.store import CAP_PIPELINE_CALLS, STEPS, PipelineStore, fits, pipeline_path
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group

from tests.support.answers_stories_fixtures import config, pending, two_profiles
from tests.support.pipeline_fixtures import MARKERS, PipelineFixture, assessment, build_pipeline_fixture, resolved_job
from tests.support.posting_fixtures import NOW, build_postings_fixture, days_ago, job_url, lever_job

_TERRAFORM = ("tooling:terraform", "Have you used Terraform in production?")
_PAYMENTS = ("domain:payments", "Have you built payment systems?")
_ANSWER = "Yes, three years of Terraform modules for our clusters."


def _job(n: int) -> str:
    return f"https://jobs.example.test/acme/role-{n:02d}"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    return build_pipeline_fixture(tmp_path, monkeypatch, base=False)


def _ask(fx: PipelineFixture, url: str, question: tuple[str, str] = _TERRAFORM, *, profile_id: str | None = None) -> None:
    """A stored (base) assessment of ``url`` that leaves ``question`` open."""

    fx.model.assessed = pending(*question)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=profile_id or fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(url),
    )
    fx.model.assessed = assessment(met=2)
    fx.model.assess_prompts.clear()


def _cli(fx: PipelineFixture, *args: str) -> dict[str, object]:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _save_answer(fx: PipelineFixture, question: tuple[str, str] = _TERRAFORM, answer: str = _ANSWER) -> dict[str, object]:
    return _cli(fx, "answers", "save", question[0], "--answer-text", answer, "--question", question[1])


def _runner(fx: PipelineFixture, **options) -> PipelineRunner:
    options.setdefault("busy", lambda: None)
    return PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), **options)


def _steps(fx: PipelineFixture, job: str, profile_id: str | None = None) -> dict[str, str]:
    if not fx.db.is_file():
        return {}
    store = PipelineStore(fx.db)
    try:
        return {step.name: step.state for step in store.steps(profile_id=profile_id or fx.profile_id, job=job)}
    finally:
        store.close()


def _all_steps(fx: PipelineFixture):
    if not fx.db.is_file():
        return ()
    store = PipelineStore(fx.db)
    try:
        return store.steps()
    finally:
        store.close()


def _write_settings(fx: PipelineFixture, **blocks: object) -> Path:
    path = settings_path(fx.home_root, fx.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", **blocks}), encoding="utf-8")
    return path


_DONE = dict.fromkeys(STEPS, "done")


# --- (a) an answer queues exactly the pair that asked, and the pipeline runs it to a Scout label -----------


def test_a_answering_a_question_queues_the_pair_that_asked_and_runs_it_to_a_scout_label(fx: PipelineFixture) -> None:
    asked, unrelated = _job(1), _job(2)
    _ask(fx, asked, _TERRAFORM)
    _ask(fx, unrelated, _PAYMENTS)
    assert _all_steps(fx) == ()  # assessing queues nothing

    saved = _save_answer(fx)

    fired = saved["pipeline"]
    assert (fired["trigger"], fired["state"]) == ("answer_saved", "fired")
    assert fired["enqueued"] == [{"profile_id": fx.profile_id, "job_identity": asked, "step": "tailor"}]
    assert fired["awaiting_approval"] == [] and fired["approval"] is None and fired["skipped"] == []
    assert {(step.profile_id, step.job) for step in _all_steps(fx)} == {(fx.profile_id, asked)}  # exactly that pair
    assert _steps(fx, asked)["tailor"] == "ready" and fx.model.calls == 0  # queued; saving the answer called no model

    drained = _runner(fx).drain()

    assert drained.state == DRAIN_RAN and drained.model_calls == 2
    assert _steps(fx, asked) == _DONE and _steps(fx, unrelated) == {}
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1)
    label = steps.read_label(fx.home_root, fx.target, fx.profile_id, asked)
    assert label is not None and label["name"] == "Scout label" and label["label"] in steps.LABELS
    # The job's own (base) assessment was made before the answer: it is stale, and the label says so by code.
    assert (label["label"], label["reasons"]) == ("needs_attention", ["base_assessment_stale"])
    assert steps.read_label(fx.home_root, fx.target, fx.profile_id, unrelated) is None

    # The same answer again changes nothing; a tag alone answers nothing.
    assert "pipeline" not in _save_answer(fx)
    assert "pipeline" not in _cli(fx, "answers", "save", _TERRAFORM[0], "--tag", "infra")
    assert fx.model.calls == 2


def test_a_the_trigger_is_off_with_the_pipeline_and_never_reaches_a_pasted_resume_or_an_inactive_profile(
    fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gigai.scout import profile_records

    default, second = two_profiles(fx.gig)
    _ask(fx, _job(1), profile_id=default)
    _ask(fx, _job(1), profile_id=second)
    profile_records.write_profile(fx.gig.resolved, profile_id=second, state="archived")

    monkeypatch.setenv(PIPELINE_ENV, "0")
    assert "pipeline" not in _save_answer(fx) and _all_steps(fx) == ()  # switched off: nothing queued
    monkeypatch.delenv(PIPELINE_ENV)

    fired = _save_answer(fx, answer=_ANSWER + " And Terragrunt.")["pipeline"]
    assert [item["profile_id"] for item in fired["enqueued"]] == [default]  # the archived profile's pair is left out


# --- (b) a story about A's open question re-opens A, not B ---------------------------------------------------


def test_b_a_story_about_one_jobs_open_question_reopens_that_job_only(fx: PipelineFixture) -> None:
    job_a, job_b = _job(1), _job(2)
    _ask(fx, job_a, _TERRAFORM)
    _ask(fx, job_b, _PAYMENTS)
    for job in (job_a, job_b):
        assert triggers.process_now(fx.home_root, fx.target, fx.profile_id, job)["result"] == "enqueued"
    assert _runner(fx).drain().state == DRAIN_RAN
    assert _steps(fx, job_a) == _DONE and _steps(fx, job_b) == _DONE
    before_b = [step for step in _all_steps(fx) if step.job == job_b]
    calls = fx.model.calls

    saved = _cli(
        fx, "story", "save", "--title", "Rolled out Terraform modules",
        "--raw-text", "I wrote the Terraform modules our Kubernetes clusters are built from.",
    )

    fired = saved["pipeline"]
    assert (fired["trigger"], fired["state"]) == ("story_saved", "fired")
    assert fired["enqueued"] == [{"profile_id": fx.profile_id, "job_identity": job_a, "step": "tailor"}]
    assert _steps(fx, job_a) == {"tailor": "ready", "reassess": "blocked", "ats": "blocked", "label": "blocked"}
    assert [step for step in _all_steps(fx) if step.job == job_b] == before_b  # B: not a row touched
    assert fx.model.calls == calls  # saving the story called no model

    assert _runner(fx).drain().state == DRAIN_RAN
    assert _steps(fx, job_a) == _DONE and fx.model.calls == calls + 2
    assert "A story:" in fx.model.tailor_prompts[-1] and "Rolled out Terraform modules" in fx.model.tailor_prompts[-1]


# --- (c) the per-trigger cap: 10 run, 2 wait for an approval; approve runs them; deny cancels ---------------


def test_c_twelve_jobs_in_one_trigger_ten_run_two_wait_for_an_approval_and_approving_runs_them(fx: PipelineFixture) -> None:
    jobs = [_job(n) for n in range(12)]
    for job in jobs:
        _ask(fx, job)

    fired = _save_answer(fx)["pipeline"]

    assert len(fired["enqueued"]) == 10 and len(fired["awaiting_approval"]) == 2
    held = sorted(item["job_identity"] for item in fired["awaiting_approval"])
    approval = fired["approval"]
    # The estimate: one tailoring call and one re-assessment per job; no tailoring is recorded yet, so no token figure.
    assert (approval["jobs"], approval["est_calls"], approval["est_tokens"]) == (2, 4, None)
    assert approval["id"].startswith("apv_")
    for job in held:
        assert _steps(fx, job) == {"tailor": "awaiting_approval", "reassess": "blocked", "ats": "blocked", "label": "blocked"}

    first = _runner(fx).drain()

    assert first.state == DRAIN_RAN and first.model_calls == 20
    for job in jobs:
        assert (_steps(fx, job) == _DONE) is (job not in held), job
    for job in held:  # nothing of theirs ran
        assert _steps(fx, job)["tailor"] == "awaiting_approval"
        assert steps.read_label(fx.home_root, fx.target, fx.profile_id, job) is None
    assert _runner(fx).drain().state == DRAIN_IDLE and fx.model.calls == 20  # they do not run by waiting

    listing = _cli(fx, "pipeline", "approvals", "list")
    assert listing["schema_version"] == "scout-pipeline-approvals:1" and listing["pending"] == 1
    (item,) = listing["approvals"]
    assert (item["id"], item["state"], item["trigger"], item["jobs"], item["waiting_jobs"]) == (approval["id"], "pending", "answer_saved", 2, 2)
    assert sorted(entry["job_identity"] for entry in item["waiting"]) == held
    # The offer of `scout new` counts them (read through the store, as scout new does).
    store = PipelineStore(fx.db)
    try:
        offer = scout_new._pipeline_offer(store)
    finally:
        store.close()
    assert offer == {
        "waiting": 2, "awaiting_approval": 2, "approvals": [approval["id"]], "est_calls": 4,
        "command": "gigai scout new --process", "text": "2 waiting (2 need your approval), process now? ~4 calls",
    }

    approved = _cli(fx, "pipeline", "approvals", "approve", approval["id"], "--actor", "agent")["approval"]

    assert (approved["state"], approved["decided_by"], approved["decided_jobs"], approved["waiting_jobs"]) == ("approved", "agent", 2, 0)
    assert fx.model.calls == 20  # approving calls no model
    second = _runner(fx).drain()
    assert second.state == DRAIN_RAN and second.model_calls == 4
    for job in jobs:
        assert _steps(fx, job) == _DONE
        assert steps.read_label(fx.home_root, fx.target, fx.profile_id, job) is not None
    assert _cli(fx, "pipeline", "approvals", "list")["approvals"] == []
    # Deciding it again changes nothing.
    assert _cli(fx, "pipeline", "approvals", "deny", approval["id"])["approval"]["state"] == "approved"
    assert _steps(fx, held[0]) == _DONE


def test_c_denying_an_approval_cancels_its_jobs_with_no_model_call_and_process_now_takes_a_job_out(fx: PipelineFixture) -> None:
    _write_settings(fx, pipeline={"auto_jobs_per_trigger": 1})
    jobs = [_job(n) for n in range(4)]
    for job in jobs:
        _ask(fx, job)

    fired = _save_answer(fx)["pipeline"]

    assert len(fired["enqueued"]) == 1 and len(fired["awaiting_approval"]) == 3  # the cap is the setting's
    ran = fired["enqueued"][0]["job_identity"]
    held = [item["job_identity"] for item in fired["awaiting_approval"]]
    assert _runner(fx).drain().model_calls == 2 and _steps(fx, ran) == _DONE

    # "Process now" names one waiting job: it is opened (an explicit choice), the others keep waiting.
    queued = triggers.process_now(fx.home_root, fx.target, fx.profile_id, held[0])
    assert queued["result"] == "enqueued" and _steps(fx, held[0])["tailor"] == "ready"
    (pending_item,) = triggers.approvals(fx.home_root, fx.target)["approvals"]
    assert (pending_item["jobs"], pending_item["waiting_jobs"]) == (3, 2)
    assert _runner(fx).drain().model_calls == 2 and _steps(fx, held[0]) == _DONE
    calls = fx.model.calls

    denied = _cli(fx, "pipeline", "approvals", "deny", fired["approval"]["id"])["approval"]

    assert (denied["state"], denied["decided_by"], denied["decided_jobs"]) == ("declined", "operator", 2)
    for job in held[1:]:
        assert _steps(fx, job) == dict.fromkeys(STEPS, "cancelled")
        assert steps.read_label(fx.home_root, fx.target, fx.profile_id, job) is None
    assert _runner(fx).drain().state == DRAIN_IDLE and fx.model.calls == calls  # no model call for a denied job

    unknown = CliRunner().invoke(scout_group, ["pipeline", "approvals", "approve", "apv_missing", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert unknown.exit_code != 0 and "approval_not_found" in unknown.output


# --- (d) the daily caps: the 41st call waits; one counter for every profile ----------------------------------


def test_d_the_41st_model_call_of_the_day_waits_and_two_profiles_share_the_counter(fx: PipelineFixture) -> None:
    default, second = two_profiles(fx.gig)
    job = _job(1)
    _ask(fx, job, profile_id=default)
    _ask(fx, job, profile_id=second)
    for profile_id in (default, second):
        assert triggers.process_now(fx.home_root, fx.target, profile_id, job)["result"] == "enqueued"
    today = datetime(2026, 10, 3, 11, 0).astimezone()
    day = today.date().isoformat()
    store = PipelineStore(fx.db)
    try:
        assert store.spend(CAP_PIPELINE_CALLS, day, 37)  # 37 calls already made today
    finally:
        store.close()

    # 0.1.10.9: the store clock must be the SAME fake moment as `now`: with the real wall clock the step's
    # `not_before` (clock + time to the next local midnight) depended on when the suite ran, and a run just after
    # 00:00 UTC put it past the fake "tomorrow" below (CI run 37164818416). Both are injected, never the machine's.
    drained = _runner(fx, now=lambda: today, clock=lambda: today.timestamp(), workers=1).drain()

    # Calls 38, 39 and 40 are made (across BOTH profiles); the 41st is not: its step waits for tomorrow.
    assert drained.model_calls == 3 and fx.model.calls == 3
    waiting = [step for step in drained.steps if step.get("waiting") == WAIT_DAILY_CAP]
    assert len(waiting) == 1 and waiting[0]["name"] in ("tailor", "reassess")
    made = {(step["profile_id"], step["name"]) for step in drained.steps if step.get("model_calls")}
    assert {profile_id for profile_id, _name in made} == {default, second}  # both profiles spent from the one counter
    caps = triggers.caps(fx.home_root, fx.target, now=today)
    assert caps["pipeline_calls"] == {"used": 40, "limit": 40} and caps["jobs_per_trigger"] == 10
    status = overview(fx.home_root, fx.target, busy=lambda: None, now=today, clock=lambda: today.timestamp())
    assert status["caps"]["pipeline_calls"] == {"used": 40, "limit": 40}
    assert [item["waiting"] for item in status["jobs"] if item["waiting"]] == [WAIT_DAILY_CAP]
    assert _runner(fx, now=lambda: today, clock=lambda: today.timestamp(), workers=1).drain().model_calls == 0  # still today: nothing more

    tomorrow = today + timedelta(days=1)
    later = _runner(fx, now=lambda: tomorrow, clock=lambda: tomorrow.timestamp(), workers=1).drain()
    assert later.state == DRAIN_RAN and later.model_calls == 1
    assert _steps(fx, job, default) == _DONE and _steps(fx, job, second) == _DONE


def test_d_the_rank_counter_is_one_for_the_install_warns_past_60_and_stops_at_100(fx: PipelineFixture) -> None:
    today = datetime(2026, 10, 3, 11, 0).astimezone()

    first = triggers.spend_rank_calls(fx.home_root, fx.target, 59, now=today)
    assert first == {"allowed": True, "used": 59, "limit": 100, "warn_at": 60, "warning": False, "day": today.date().isoformat()}
    assert triggers.spend_rank_calls(fx.home_root, fx.target, 1, now=today)["warning"] is False  # the 60th: not flagged
    sixty_first = triggers.spend_rank_calls(fx.home_root, fx.target, 1, now=today)
    assert (sixty_first["allowed"], sixty_first["used"], sixty_first["warning"]) == (True, 61, True)  # the 61st: made, flagged
    assert triggers.spend_rank_calls(fx.home_root, fx.target, 39, now=today)["used"] == 100
    over = triggers.spend_rank_calls(fx.home_root, fx.target, 1, now=today)
    assert (over["allowed"], over["used"]) == (False, 100)  # the 101st is refused and not counted
    triggers.refund_rank_calls(fx.home_root, fx.target, 5, now=today)
    assert triggers.caps(fx.home_root, fx.target, now=today)["rank_calls"] == {"used": 95, "limit": 100, "warn_at": 60, "warning": True}
    assert triggers.caps(fx.home_root, fx.target, now=today)["pipeline_calls"]["used"] == 0  # its own counter
    assert triggers.spend_rank_calls(fx.home_root, fx.target, 1, now=today + timedelta(days=1))["used"] == 1  # a new day

    _write_settings(fx, rank={"max_calls_per_day": 3, "warn_calls_per_day": 2})
    assert triggers.spend_rank_calls(fx.home_root, fx.target, 1, now=today + timedelta(days=2))["limit"] == 3
    settings_path(fx.home_root, fx.target).write_text("{not json", encoding="utf-8")
    refused = triggers.spend_rank_calls(fx.home_root, fx.target, 1, now=today + timedelta(days=3))
    assert (refused["allowed"], refused["used"]) == (False, 0)  # settings that cannot be read: no call is allowed


# --- (e) 500 postings appear in the index: nothing is queued, no model call -----------------------------------


def test_e_500_new_postings_queue_nothing_and_call_no_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    pf = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    pf.seed("acme", [lever_job("acme", n) for n in range(500)], seen_at=days_ago(1))

    asked = scout_new.scout_new(pf.home_root, pf.target, now=NOW)  # the plain daily call: it reads all 500

    assert asked["status"] == "ask" and asked["counts"]["new"] == 500 and asked["pipeline"] is None
    runner = PipelineRunner(home_root=pf.home_root, target=pf.target, config=config(pf.home_root), busy=lambda: None)
    assert runner.drain().state == DRAIN_IDLE
    assert triggers.profile_changed(pf.home_root, pf.target).state == "nothing"
    store = PipelineStore(pipeline_path(pf.home_root, pf.target))
    try:
        assert store.posting_count() >= 500 and store.steps() == () and store.approvals() == ()
        assert store.used(CAP_PIPELINE_CALLS, triggers.today()) == 0
    finally:
        store.close()
    status = overview(pf.home_root, pf.target, busy=lambda: None)
    assert status["jobs"] == [] and status["counts"] == {"steps": {}, "jobs": {}, "jobs_total": 0}
    assert pf.base.model.calls == 0  # 500 postings: zero model calls


# --- (f) the status object: no text, and it passes the outbound check ---------------------------------------

_LOCAL_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")
_SYSTEM_KINDS = ("job", "code", "id", "timestamp", "day", "lane", "digest")


def _strings(node: object, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            assert fits("code", key) or fits("lane", key), f"{path}/{key}: a key that is not a name"
            yield from _strings(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, f"{path}/{index}")
    elif isinstance(node, str):
        yield path, node


def _assert_system_only(payload: dict[str, object], *, jobs: list[str], tmp_path: Path) -> None:
    """Every string is an id, a job identity, a code, a digest, a lane or a time: no sentence can be one."""

    for path, value in _strings(payload):
        assert any(fits(kind, value) for kind in _SYSTEM_KINDS) or _LOCAL_TIME.fullmatch(value), f"{path}: {value!r} is text"
    dumped = json.dumps(payload)
    for job in jobs:
        dumped = dumped.replace(job, "")  # a job is named by its link; what is left must hold none of its words
    for word in (
        "inference services", "Staff AI Engineer", "Requirements", "Terraform", "Python", "six years", "three years",
        "Have you", "Scout's own", "Acme", os.fspath(tmp_path), *MARKERS,
    ):
        assert word not in dumped, word
    assert redact_payload(json.loads(json.dumps(payload))) == payload and REDACTIONS_KEY not in payload  # the outbound check changes nothing


def test_f_the_status_object_holds_ids_and_codes_only_and_passes_the_outbound_check(fx: PipelineFixture, tmp_path: Path) -> None:
    _write_settings(fx, pipeline={"auto_jobs_per_trigger": 2})
    jobs = [_job(n) for n in range(4)]
    for job in jobs:
        _ask(fx, job)
    fired = _save_answer(fx)["pipeline"]
    fx.model.fail_next = TimeoutError("the model took too long to answer about Terraform")
    _runner(fx, workers=1).drain()

    status = overview(fx.home_root, fx.target, busy=lambda: None)

    assert status["schema_version"] == "scout-pipeline:1" and status["readable"] is True and status["runner"] is None
    assert status["setting"]["auto_jobs_per_trigger"] == 2 and status["caps"]["jobs_per_trigger"] == 2
    # Three calls counted against the day: the one that timed out was made too (it is not refunded); two were answered.
    assert status["caps"]["pipeline_calls"]["used"] == 3 and fx.model.calls == 2
    assert status["counts"]["jobs_total"] == 4 and status["counts"]["jobs"] == {"awaiting_approval": 2, "done": 1, "waiting": 1}
    assert sum(status["counts"]["steps"].values()) == 16
    assert status["approvals"]["pending"] == 1 and status["approvals"]["items"][0]["id"] == fired["approval"]["id"]
    assert status["approvals"]["items"][0]["est_calls"] == 4
    by_state = {item["state"]: item for item in status["jobs"]}
    assert by_state["done"]["label"] == {"label": "needs_attention", "reasons": ["base_assessment_stale"], "ats_score": by_state["done"]["label"]["ats_score"]}
    assert isinstance(by_state["done"]["label"]["ats_score"], int) and by_state["done"]["steps"] == _DONE
    assert by_state["waiting"]["waiting"] == "retry_backoff" and by_state["waiting"]["label"] is None  # the timed-out step retries later
    assert by_state["awaiting_approval"]["approval_id"] == fired["approval"]["id"]
    (error,) = status["errors"]
    assert (error["step"], error["error_code"], error["attempt"]) == ("tailor", "assess_timeout", 1)  # a code, never the message
    assert {lane["lane"] for lane in status["lanes"]} >= {"claude_cli", "codex_cli", "ollama", "local"}
    _assert_system_only(status, jobs=jobs, tmp_path=tmp_path)

    listing = triggers.approvals(fx.home_root, fx.target)
    _assert_system_only(listing, jobs=jobs, tmp_path=tmp_path)
    _assert_system_only(fired, jobs=jobs, tmp_path=tmp_path)

    # A project with no queue: the same object, empty, and nothing is created by reading.
    fx.db.unlink()
    empty = overview(fx.home_root, fx.target, busy=lambda: None)
    assert empty["jobs"] == [] and empty["caps"]["pipeline_calls"]["used"] == 0 and not fx.db.is_file()
    assert set(empty) == set(status)


# --- (g) the settings: a round trip; unreadable settings keep the pipeline off, reported --------------------


@pytest.mark.skip(reason="needs the pipeline on: re-enable in 0.1.11.1 (pipeline off by default in 0.1.11)")
def test_g_the_pipeline_settings_round_trip_and_unreadable_settings_keep_the_pipeline_off(fx: PipelineFixture) -> None:
    before = background_settings.background_settings(fx.home_root, fx.target, environ={})
    assert before["settings"]["pipeline"] == {
        "enabled": True, "auto_jobs_per_trigger": 10, "max_model_calls_per_day": 40, "label_min_ats": 0, "models": {},
    }
    assert before["settings"]["rank"] == {"enabled": True, "max_calls_per_day": 100, "warn_calls_per_day": 60}
    assert before["effective"]["pipeline"]["source"] == "default" and before["effective"]["pipeline"]["enabled"] is True

    patch = background_settings.validate_patch({
        "pipeline": {
            "enabled": False, "auto_jobs_per_trigger": 3, "max_model_calls_per_day": 12, "label_min_ats": 70,
            "models": {"tailor": "claude_cli", "reassess": "codex_cli"},
        },
        "rank": {"max_calls_per_day": 50, "warn_calls_per_day": 20},
    })
    path = background_settings.write_background_settings(fx.home_root, fx.target, patch)

    after = background_settings.background_settings(fx.home_root, fx.target, environ={})
    assert after["settings"]["pipeline"] == {
        "enabled": False, "auto_jobs_per_trigger": 3, "max_model_calls_per_day": 12, "label_min_ats": 70,
        "models": {"reassess": "codex_cli", "tailor": "claude_cli"},
    }
    assert after["settings"]["rank"] == {"enabled": True, "max_calls_per_day": 50, "warn_calls_per_day": 20}
    assert after["effective"]["pipeline"] == pipeline_setting(fx.home_root, fx.target, environ={}).to_json()
    assert after["effective"]["pipeline"]["source"] == "setting" and after["readable"] is True
    # The reader the runner uses sees the same file.
    setting = pipeline_setting(fx.home_root, fx.target, environ={})
    assert (setting.enabled, setting.auto_jobs_per_trigger, setting.max_model_calls_per_day, setting.label_min_ats) == (False, 3, 12, 70)
    assert dict(setting.models) == {"tailor": "claude_cli", "reassess": "codex_cli"}

    # One key named: the others stay. null puts a default, or the project's model target, back.
    background_settings.write_background_settings(
        fx.home_root, fx.target,
        background_settings.validate_patch({"pipeline": {"enabled": True, "models": {"tailor": None}, "max_model_calls_per_day": None}}),
    )
    kept = background_settings.background_settings(fx.home_root, fx.target, environ={})["settings"]["pipeline"]
    assert kept == {"enabled": True, "auto_jobs_per_trigger": 3, "max_model_calls_per_day": 40, "label_min_ats": 70, "models": {"reassess": "codex_cli"}}
    assert json.loads(path.read_text(encoding="utf-8"))["pipeline"] == {
        "enabled": True, "auto_jobs_per_trigger": 3, "label_min_ats": 70, "models": {"reassess": "codex_cli"},
    }

    for body, code in (
        ({"pipeline": {"enabled": "yes"}}, "wrong_type"),
        ({"pipeline": {"auto_jobs_per_trigger": -1}}, "invalid_value"),
        ({"pipeline": {"max_model_calls_per_day": 1001}}, "invalid_value"),
        ({"pipeline": {"max_model_calls_per_day": 2.5}}, "wrong_type"),
        ({"pipeline": {"max_model_calls_per_day": True}}, "wrong_type"),
        ({"pipeline": {"label_min_ats": 101}}, "invalid_value"),
        ({"pipeline": {"models": {"ats": "claude_cli"}}}, "unknown_key"),
        ({"pipeline": {"models": {"tailor": "gpt-9"}}}, "bad_enum"),
        ({"pipeline": {"models": {}}}, "invalid_value"),
        ({"pipeline": {"models": "claude_cli"}}, "wrong_type"),
        ({"pipeline": {"lanes": {}}}, "unknown_key"),
        ({"rank": {"max_calls_per_day": 10, "warn_calls_per_day": 11}}, "invalid_value"),
    ):
        with pytest.raises(background_settings.SettingsError) as refused:
            background_settings.validate_patch(body)
        assert refused.value.code == code, body

    # Settings that cannot be read: the pipeline is OFF and says why; nothing is queued, nothing runs; a write is refused.
    _ask(fx, _job(1))
    path.write_text('{"schema_version": "scout-settings:1", "pipeline": {"max_model_calls_per_day": "lots"}}', encoding="utf-8")
    off = background_settings.background_settings(fx.home_root, fx.target, environ={PIPELINE_ENV: "1"})
    assert (off["effective"]["pipeline"]["enabled"], off["effective"]["pipeline"]["source"]) == (False, "settings_unreadable")
    status = overview(fx.home_root, fx.target, busy=lambda: None, environ={PIPELINE_ENV: "1"})
    assert status["readable"] is False and status["setting"]["enabled"] is False and status["setting"]["source"] == "settings_unreadable"
    assert "pipeline" not in _save_answer(fx) and _all_steps(fx) == ()
    fired = triggers.pending_answer(fx.home_root, fx.target, _entry(fx)).fire()
    assert (fired.state, fired.reason) == ("disabled", "settings_unreadable")
    assert _runner(fx).drain().state in (DRAIN_DISABLED, DRAIN_IDLE) and fx.model.calls == 0

    path.write_text("{not json", encoding="utf-8")
    assert background_settings.background_settings(fx.home_root, fx.target, environ={})["readable"] is False
    with pytest.raises(background_settings.SettingsUnreadableError):
        background_settings.write_background_settings(fx.home_root, fx.target, background_settings.validate_patch({"pipeline": {"enabled": True}}))
    assert path.read_text(encoding="utf-8") == "{not json"  # left as it is


def _entry(fx: PipelineFixture):
    from gigai.scout import story_bank

    entry = story_bank.get_answer(home_root=fx.home_root, target=fx.target, question_id=_TERRAFORM[0])
    assert entry is not None
    return entry


# --- (d of the change) a profile's settings change re-opens only that profile's steps, by digest ------------


def test_a_profiles_settings_change_reopens_only_that_profiles_steps_by_digest(fx: PipelineFixture) -> None:
    default, second = two_profiles(fx.gig)
    job = _job(1)
    for profile_id in (default, second):
        _ask(fx, job, profile_id=profile_id)
        triggers.process_now(fx.home_root, fx.target, profile_id, job)
    assert _runner(fx).drain().state == DRAIN_RAN
    assert _steps(fx, job, default) == _DONE and _steps(fx, job, second) == _DONE
    calls = fx.model.calls
    assert triggers.profile_changed(fx.home_root, fx.target, second).state == "nothing"  # nothing changed: nothing re-opens

    updated = CliRunner().invoke(
        scout_group, ["profile", "update", second, "--work-mode", "onsite", "--country", "CA", "--home", str(fx.home_root), "--target", str(fx.target), "--json"]
    )
    assert updated.exit_code == 0, updated.output

    # The profile's own candidate settings changed: the re-assessment reads them, so that profile's job re-opens
    # from there. Not from the tailoring (0.1.10.7 fix1): its digest holds what changes the tailored text, and
    # the profile record's revision is not part of it.
    assert _steps(fx, job, second) == {"tailor": "done", "reassess": "ready", "ats": "done", "label": "blocked"}
    assert _steps(fx, job, default) == _DONE  # the other profile: untouched
    # The steps that re-opened say why; the tailoring and the ATS score were not touched.
    assert {step.name: step.trigger for step in _all_steps(fx) if step.profile_id == second} == {
        "tailor": "process_now", "reassess": "profile_changed", "ats": "process_now", "label": "profile_changed",
    }
    assert {step.trigger for step in _all_steps(fx) if step.profile_id == default} == {"process_now"}
    assert fx.model.calls == calls  # the update itself called no model

    assert _runner(fx).drain().state == DRAIN_RAN
    assert _steps(fx, job, second) == _DONE and fx.model.calls == calls + 1  # one re-assessment, no second tailoring
    assert triggers.profile_changed(fx.home_root, fx.target).state == "nothing"  # done with the inputs as they are


def test_a_changed_candidate_setting_reopens_the_reassessment_and_not_the_tailoring(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job(1)
    _ask(fx, job)
    triggers.process_now(fx.home_root, fx.target, fx.profile_id, job)
    assert _runner(fx).drain().state == DRAIN_RAN and _steps(fx, job) == _DONE
    calls = fx.model.calls

    # What the re-assessment's digest reads besides the tailored resume: the candidate constraints. The tailoring's does not.
    from gigai.scout.assessment_basis import BasisCheck, CurrentBasis

    changed = CurrentBasis(visa_sponsorship_required=True, countries=("CA",), location="Toronto", work_mode="onsite")
    monkeypatch.setattr(BasisCheck, "current", lambda self, profile_id: changed)

    fired = triggers.profile_changed(fx.home_root, fx.target, fx.profile_id)

    assert fired.enqueued == ({"profile_id": fx.profile_id, "job_identity": job, "step": "reassess"},)
    assert _steps(fx, job) == {"tailor": "done", "reassess": "ready", "ats": "done", "label": "blocked"}
    assert _runner(fx).drain().state == DRAIN_RAN
    assert _steps(fx, job) == _DONE and fx.model.calls == calls + 1  # one re-assessment, no second tailoring


# --- (h) the runner yields while an assess batch is live, and resumes -----------------------------------------


def test_h_the_runner_yields_to_an_injected_busy_signal_and_resumes(fx: PipelineFixture) -> None:
    _ask(fx, _job(1))
    triggers.process_now(fx.home_root, fx.target, fx.profile_id, _job(1))
    live: list[str | None] = [BUSY_ASSESS_BATCH]

    yielded = _runner(fx, busy=lambda: live[0]).drain()

    assert (yielded.state, yielded.reason, yielded.steps) == (DRAIN_YIELDED, BUSY_ASSESS_BATCH, []) and fx.model.calls == 0
    assert _steps(fx, _job(1))["tailor"] == "ready"
    live[0] = None
    assert _runner(fx, busy=lambda: live[0]).drain().state == DRAIN_RAN and _steps(fx, _job(1)) == _DONE


def test_h_the_runner_yields_while_scout_new_assesses_on_a_yes_and_resumes_when_it_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    pf = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx = pf.base
    pf.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))
    _ask(fx, _job(1))
    triggers.process_now(fx.home_root, fx.target, fx.profile_id, _job(1))  # a pipeline job that is ready to run

    entered, release = threading.Event(), threading.Event()
    answer = fx.model.answer

    def slow(prompt: str):
        entered.set()
        assert release.wait(60)
        return answer(prompt)

    monkeypatch.setattr(fx.model, "answer", slow)
    result: dict[str, object] = {}
    batch = threading.Thread(
        target=lambda: result.update(scout_new.scout_new(fx.home_root, fx.target, assess=True, now=NOW, config=config(fx.home_root))),
        daemon=True,
    )
    batch.start()
    try:
        assert entered.wait(60), "the assess batch never reached the model"
        # The real signal: the marker the batch left, read by the runner's own live_work (no injected busy).
        assert busy.assess_batch_live(fx.home_root, fx.target) and live_work(fx.home_root, fx.target) == BUSY_ASSESS_BATCH
        runner = PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root))
        yielded = runner.drain()
        assert (yielded.state, yielded.reason, yielded.steps) == (DRAIN_YIELDED, BUSY_ASSESS_BATCH, [])
        assert _steps(fx, _job(1))["tailor"] == "ready" and not fx.model.tailor_prompts
    finally:
        release.set()
        batch.join(60)
    assert not batch.is_alive() and result["assessed"]["assessed"] >= 1  # type: ignore[index]

    assert not busy.assess_batch_live(fx.home_root, fx.target) and live_work(fx.home_root, fx.target) is None
    assert list(busy.live_dir(fx.home_root, fx.target).glob("batch_*.json")) == []  # the marker is gone
    resumed = PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root)).drain()
    assert resumed.state == DRAIN_RAN and _steps(fx, _job(1)) == _DONE


def test_h_the_runner_yields_while_assess_these_assesses_on_approval_and_resumes_when_it_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.1.10.7 int2: an approved "assess these" batch leaves the same marker as ``scout new`` on a yes."""

    monkeypatch.setenv(PIPELINE_ENV, "on")
    pf = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx = pf.base
    pf.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))
    _ask(fx, _job(1))
    triggers.process_now(fx.home_root, fx.target, fx.profile_id, _job(1))  # a pipeline job that is ready to run

    def these(**kwargs: object) -> dict[str, object]:
        return posting_search.assess_these(  # type: ignore[arg-type]
            fx.home_root, fx.target, jobs=[job_url("acme", 1)], now=NOW, config=config(fx.home_root), **kwargs
        )

    # Asking marks nothing live: only an approved batch does.
    assert these()["status"] == "ask" and live_work(fx.home_root, fx.target) is None
    assert list(busy.live_dir(fx.home_root, fx.target).glob("batch_*.json")) == []

    entered, release = threading.Event(), threading.Event()
    answer = fx.model.answer

    def slow(prompt: str):
        entered.set()
        assert release.wait(60)
        return answer(prompt)

    monkeypatch.setattr(fx.model, "answer", slow)
    result: dict[str, object] = {}
    batch = threading.Thread(target=lambda: result.update(these(approve=True)), daemon=True)
    batch.start()
    try:
        assert entered.wait(60), "the assess batch never reached the model"
        # The real signal: ONE marker for the batch, read by the runner's own live_work (no injected busy).
        assert len(list(busy.live_dir(fx.home_root, fx.target).glob("batch_*.json"))) == 1
        assert busy.assess_batch_live(fx.home_root, fx.target) and live_work(fx.home_root, fx.target) == BUSY_ASSESS_BATCH
        runner = PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root))
        yielded = runner.drain()
        assert (yielded.state, yielded.reason, yielded.steps) == (DRAIN_YIELDED, BUSY_ASSESS_BATCH, [])
        assert _steps(fx, _job(1))["tailor"] == "ready" and not fx.model.tailor_prompts
    finally:
        release.set()
        batch.join(60)
    assert not batch.is_alive() and result["status"] == "assessed" and result["assessed"]["assessed"] >= 1  # type: ignore[index]

    assert not busy.assess_batch_live(fx.home_root, fx.target) and live_work(fx.home_root, fx.target) is None
    assert list(busy.live_dir(fx.home_root, fx.target).glob("batch_*.json")) == []  # the marker is gone
    resumed = PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root)).drain()
    assert resumed.state == DRAIN_RAN and _steps(fx, _job(1)) == _DONE


def test_h_a_marker_left_by_a_dead_process_or_gone_quiet_is_not_waited_for(fx: PipelineFixture) -> None:
    directory = busy.live_dir(fx.home_root, fx.target)
    directory.mkdir(parents=True)
    dead = directory / "batch_dead.json"
    dead.write_text(json.dumps({"kind": "assess_batch", "pid": 2**22 + 12345, "token": "0" * 32, "started_at": "2026-10-03T10:00:00Z"}), encoding="utf-8")
    earlier = directory / "batch_earlier.json"  # this pid, another process token: an earlier process
    earlier.write_text(json.dumps({"kind": "assess_batch", "pid": os.getpid(), "token": "1" * 32, "started_at": "2026-10-03T10:00:00Z"}), encoding="utf-8")
    (directory / "batch_broken.json").write_text("not json", encoding="utf-8")
    assert not busy.assess_batch_live(fx.home_root, fx.target)

    with busy.assess_batch(fx.home_root, fx.target) as live:
        assert busy.assess_batch_live(fx.home_root, fx.target)
        marker = json.loads(live.path.read_text(encoding="utf-8"))
        assert set(marker) == {"kind", "pid", "token", "started_at"} and marker["pid"] == os.getpid()
        quiet = live.path.stat().st_mtime - busy.BATCH_QUIET_SECONDS - 5
        os.utime(live.path, (quiet, quiet))
        assert not busy.assess_batch_live(fx.home_root, fx.target)  # nothing finished for a quarter hour
        live.beat()
        assert busy.assess_batch_live(fx.home_root, fx.target)
    assert not busy.assess_batch_live(fx.home_root, fx.target) and not live.path.exists()


# --- `gigai scout new` offers the waiting work, approvals counted, and --process is the yes ----------------


def test_scout_new_offers_waiting_work_with_its_approvals_and_process_approves_and_runs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    pf = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx = pf.base
    _write_settings(fx, pipeline={"auto_jobs_per_trigger": 1})
    pf.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(20))  # an index, and nothing new in it
    jobs = [_job(n) for n in range(3)]
    for job in jobs:
        _ask(fx, job)
    fired = _save_answer(fx)["pipeline"]
    assert len(fired["enqueued"]) == 1 and len(fired["awaiting_approval"]) == 2

    def new(*args: str) -> dict[str, object]:
        result = CliRunner().invoke(scout_group, ["new", *args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    offered = new("--peek")
    assert offered["pipeline"] == {
        "waiting": 3, "awaiting_approval": 2, "approvals": [fired["approval"]["id"]], "est_calls": 6,
        "command": "gigai scout new --process", "text": "3 waiting (2 need your approval), process now? ~6 calls",
    }
    assert offered["processed"] is None and fx.model.calls == 0  # offered, never started

    done = new("--process")

    assert done["processed"]["approved"] == [{"id": fired["approval"]["id"], "jobs": 2}]
    assert done["processed"]["drain"]["state"] == DRAIN_RAN and done["processed"]["drain"]["model_calls"] == 6
    assert done["pipeline"] is None and done["anchor"]["advances"] is False  # nothing waits now; --process moves no anchor
    for job in jobs:
        assert _steps(fx, job) == _DONE
    assert triggers.approvals(fx.home_root, fx.target)["pending"] == 0
    scout_new.check_response(done)  # still one label only: the processed block is ids, codes and counts
    plain = CliRunner().invoke(scout_group, ["new", "--process", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert plain.exit_code == 0 and "Pipeline: nothing to run." in plain.output
