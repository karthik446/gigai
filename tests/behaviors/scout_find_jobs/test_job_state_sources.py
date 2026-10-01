"""uat-bug-018: ``JobStateSources``, the one place the job state's stores are read.

Real stores on a real gig (``build_gig_with_resume`` + a scripted model at
the C1 seam, as ``test_tailored_resume.py`` does): the quick-assess store
and the tailored-resume store are read per job by the file name the store
derives from the identity, per resume identity; a file that no longer
parses, or a symlink, is "nothing stored", as it is for the stores' own
readers. The application events are one committed read of the events
family that takes no journal writer lock.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import threading

import pytest

from gigai.application_events import record_application
import gigai.journal as journal
from gigai.scout.experience_answers import record_answer
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.job_state import AssessmentFact, JobStateSources, quick_assessment_fact, read_application_events
from gigai.scout.profile_records import selected_profile
from gigai.scout.quick_assess import quick_assess_path, run_quick_assessment
from gigai.scout.tailored_resume import tailored_resume_path
from gigai.workpad import resolve_workpad
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

from .test_m1_end_to_end import _fixture
from .test_tailored_resume import _PENDING_ASSESSMENT, _POSTING, _RESUME_TEXT, _VALID, _config_with_ollama, _install, _pasted, _run

_MATCHED_ASSESSMENT = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)
_NOT_ASSESSED = {"state": "not_assessed", "since": None, "next_events": ["applied"]}


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME_TEXT.encode("utf-8"))


@pytest.fixture(autouse=True)
def _no_ambient_jev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("GIGAI_JEV_COST_CAP_USD", raising=False)


def _assess(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch, output: str, **request: object):
    _install(monkeypatch, [output])
    return run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=_POSTING), **request),  # type: ignore[arg-type]
        home_root=fx.home_root,
        target=fx.target,
        config=_config_with_ollama(fx.home_root),
    )


def _tailor(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, [json.dumps(_VALID)])
    return _run(fx, _pasted())


@pytest.fixture
def gcp_answer(fx: ProfileFixtureGig) -> None:
    """The answer ``_VALID``'s one answer-cited line traces to."""

    record_answer(
        home_root=fx.home_root, requested_target=fx.target, gig_id=fx.resolved.gig_id,
        question_id="cloud:gcp", prompt="Have you run workloads on GCP?", answer="Yes, two years on GCP.",
    )


def _sources(fx: ProfileFixtureGig) -> JobStateSources:
    # No ``resolved``: these cases have no application event to read.
    return JobStateSources(home_root=fx.home_root, target=fx.target)


def test_the_stores_are_read_per_job_and_per_resume(fx: ProfileFixtureGig, gcp_answer: None, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None
    other_job = "https://boards.greenhouse.io/acme/jobs/101"

    assert _sources(fx).state_for(other_job, profile_id=profile.profile_id).to_json() == _NOT_ASSESSED

    first = _assess(fx, monkeypatch, _PENDING_ASSESSMENT)
    identity = first.job.job_identity
    assert identity.startswith("text:sha256:")
    needs_answers = {"state": "needs_answers", "since": first.created_at, "next_events": ["applied"]}
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == needs_answers
    # Another job, and the same job for another resume, know nothing of it.
    assert _sources(fx).state_for(other_job, profile_id=profile.profile_id).to_json() == _NOT_ASSESSED
    assert _sources(fx).state_for(identity, profile_id=None).to_json() == _NOT_ASSESSED

    # A re-assessment with a new verdict: the state and its ``since`` move.
    second = _assess(fx, monkeypatch, _MATCHED_ASSESSMENT)
    assert [entry.trigger for entry in second.history] == ["assess", "reassess"]
    matched = {"state": "matched", "since": second.updated_at, "next_events": ["applied"]}
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == matched
    # The same verdict again: ``since`` stays where the verdict was first given.
    third = _assess(fx, monkeypatch, _MATCHED_ASSESSMENT)
    assert third.updated_at >= second.updated_at and len(third.history) == 3
    assert quick_assessment_fact(third) == AssessmentFact(at=third.updated_at, verdict="matched_above_threshold", since=second.updated_at)
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == matched

    # A pasted resume's assessment is its own resume identity ("ephemeral").
    pasted = _assess(fx, monkeypatch, _PENDING_ASSESSMENT, resume=AssessResumeInput(resume_text=_RESUME_TEXT))
    assert pasted.resume.profile_id is None
    assert _sources(fx).state_for(identity, profile_id=None).state == "needs_answers"
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == matched

    # The item a route already holds is used as it is, not read again.
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id, quick=first).state == "needs_answers"

    # A run's assessment that is newer than the store's decides the verdict.
    later_run = AssessmentFact(at="2999-01-01T00:00:00Z", verdict="not_a_match")
    earlier_run = AssessmentFact(at="2000-01-01T00:00:00Z", verdict="not_a_match")
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id, run_assessment=later_run).state == "not_a_match"
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id, run_assessment=earlier_run).state == "matched"

    # A tailored resume wins over the verdict, for its own resume identity only.
    tailored = _tailor(fx, monkeypatch)
    assert tailored.job.job_identity == identity
    tailored_state = {"state": "tailored", "since": tailored.created_at, "next_events": ["applied"]}
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == tailored_state
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id, run_assessment=later_run).to_json() == tailored_state
    assert _sources(fx).state_for(identity, profile_id=None).state == "needs_answers"

    # A re-run keeps ``created_at``, so ``since`` does not move.
    again = _tailor(fx, monkeypatch)
    assert again.created_at == tailored.created_at
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == tailored_state

    # Application events win over both stores.
    applied = {"event_id": "event_1", "external_ref": identity, "event_kind": "applied", "occurred_at": "2026-09-01T10:00:00Z"}
    with_events = JobStateSources(home_root=fx.home_root, target=fx.target, events={identity: [applied]})
    assert with_events.state_for(identity, profile_id=profile.profile_id).to_json() == {
        "state": "applied",
        "since": "2026-09-01T10:00:00Z",
        "next_events": ["interview_scheduled", "offer_received", "rejected", "withdrawn"],
    }
    assert with_events.state_for(other_job, profile_id=profile.profile_id).to_json() == _NOT_ASSESSED


def test_a_stored_file_that_does_not_parse_is_nothing_stored(fx: ProfileFixtureGig, gcp_answer: None, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None
    assessed = _assess(fx, monkeypatch, _MATCHED_ASSESSMENT)
    identity = assessed.job.job_identity
    _tailor(fx, monkeypatch)
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).state == "tailored"

    tailored_file = tailored_resume_path(fx.home_root, fx.target, profile.profile_id, identity)
    quick_file = quick_assess_path(fx.home_root, fx.target, profile.profile_id, identity)
    assert tailored_file.is_file() and quick_file.is_file()

    tailored_file.write_text("{not json", encoding="utf-8")
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).state == "matched"
    tailored_file.write_text(json.dumps({"schema_version": "scout-tailor-response:1"}), encoding="utf-8")
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).state == "matched"

    real = quick_file.with_name("moved.bin")
    quick_file.rename(real)
    quick_file.symlink_to(real)  # the stores never follow a symlink
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == _NOT_ASSESSED
    quick_file.unlink()
    quick_file.write_text("[]", encoding="utf-8")
    assert _sources(fx).state_for(identity, profile_id=profile.profile_id).to_json() == _NOT_ASSESSED


def test_a_folder_with_no_project_has_nothing_stored(tmp_path: Path) -> None:
    sources = JobStateSources(home_root=tmp_path / "home", target=tmp_path / "unbound")
    assert sources.state_for("https://boards.greenhouse.io/acme/jobs/101", profile_id=None).to_json() == _NOT_ASSESSED
    assert sources.events_for("https://boards.greenhouse.io/acme/jobs/101") == []


# --- the application events read ---------------------------------------------------------


@pytest.fixture
def lock_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every journal writer-lock acquisition in this process, in order."""

    taken: list[str] = []
    real = journal._writer_lock

    @contextmanager
    def spying(path: Path, timeout_seconds: float):
        taken.append(threading.current_thread().name)
        with real(path, timeout_seconds):
            yield

    monkeypatch.setattr(journal, "_writer_lock", spying)
    return taken


def _record(resolved, operation: str, external_ref: str, event_kind: str, occurred_at: str) -> dict[str, object]:
    result = record_application(
        resolved=resolved,
        confirm=True,
        data={
            "operation_key": operation,
            "external_ref": external_ref,
            "event_kind": event_kind,
            "occurred_at": occurred_at,
            "timezone": "UTC",
        },
    )
    assert result["status"] == "recorded"
    return result["event"]


def test_the_events_read_takes_no_writer_lock(tmp_path: Path, lock_spy: list[str]) -> None:
    home, target, _workpad = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    url = "https://boards.greenhouse.io/acme/jobs/101"
    pasted = "text:sha256:" + "b" * 64

    lock_spy.clear()
    assert read_application_events(resolved) == {}
    assert lock_spy == []

    applied = _record(resolved, "job-state-1", url, "applied", "2026-09-01T10:00:00Z")
    interview = _record(resolved, "job-state-2", url, "interview_scheduled", "2026-09-05T10:00:00Z")
    saved = _record(resolved, "job-state-3", pasted, "saved", "2026-09-02T10:00:00Z")
    assert len(lock_spy) == 3  # the three writes, and only they

    lock_spy.clear()
    events = read_application_events(resolved)
    sources = JobStateSources(home_root=home, target=target, resolved=resolved)
    state = sources.state_for(url, profile_id=None)
    saved_only = sources.state_for(pasted, profile_id=None)
    unknown = sources.state_for("https://boards.greenhouse.io/acme/jobs/999", profile_id=None)
    assert lock_spy == []

    assert set(events) == {url, pasted}
    assert sorted(event["event_id"] for event in events[url]) == sorted([applied["event_id"], interview["event_id"]])
    assert [event["event_id"] for event in events[pasted]] == [saved["event_id"]]
    assert state.to_json() == {
        "state": "interview_scheduled",
        "since": "2026-09-05T10:00:00Z",
        "next_events": ["offer_received", "rejected", "withdrawn"],
    }
    assert saved_only.to_json() == _NOT_ASSESSED
    assert unknown.to_json() == _NOT_ASSESSED


def test_one_request_reads_the_events_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, _workpad = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    _record(resolved, "job-state-once", "https://boards.greenhouse.io/acme/jobs/101", "applied", "2026-09-01T10:00:00Z")

    reads: list[tuple[str, ...]] = []
    real = journal.read_committed_snapshot

    def counting(**kwargs):
        reads.append(tuple(kwargs["prefixes"]))
        return real(**kwargs)

    # 0110-036: the events come from the ones kept at the journal head (one
    # ask a request); the snapshot of the events family is what answers when
    # they cannot.
    import gigai.application_event_index as event_index
    import gigai.scout.find_jobs.job_state as job_state_module

    kept_reads: list[object] = []
    real_kept = event_index.committed_application_events

    def counting_kept(resolved, **kwargs):
        kept_reads.append(resolved)
        return real_kept(resolved, **kwargs)

    monkeypatch.setattr(job_state_module, "read_committed_snapshot", counting)
    monkeypatch.setattr(job_state_module, "committed_application_events", counting_kept)
    sources = JobStateSources(home_root=home, target=target, resolved=resolved)
    states = [sources.state_for(f"https://boards.greenhouse.io/acme/jobs/{job}", profile_id=None).state for job in range(100, 140)]

    assert states.count("applied") == 1 and states.count("not_assessed") == 39
    # One read for forty jobs.
    assert len(kept_reads) == 1 and reads == []

    def unavailable(resolved, **kwargs):
        raise event_index.ApplicationEventIndexUnavailable("off")

    monkeypatch.setattr(job_state_module, "committed_application_events", unavailable)
    sources = JobStateSources(home_root=home, target=target, resolved=resolved)
    again = [sources.state_for(f"https://boards.greenhouse.io/acme/jobs/{job}", profile_id=None).state for job in range(100, 140)]
    assert again == states
    # One read for forty jobs, and only of the events family.
    assert reads == [("records/applications/events/",)]
