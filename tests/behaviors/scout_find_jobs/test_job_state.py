"""uat-bug-018: the job-state derivation (``job_state.py``), pure functions only.

Every state, the precedence between the three sources (application events
> tailored resume > latest verdict > not assessed), which assessment is the
latest, what ``since`` says, and the pipeline's transitions. No file, no
journal, no server: the stores are read by ``JobStateSources``, which the
API tests cover.
"""

from __future__ import annotations

import pytest

from gigai.application_events import EVENT_KINDS
from gigai.scout.find_jobs.assess_contracts import text_identity
from gigai.scout.find_jobs.contracts import FindJobsContractError, Verdict
from gigai.scout.find_jobs.job_state import (
    APPLICATION_STATES,
    JOB_STATES,
    AssessmentFact,
    JobState,
    JobStateError,
    application_state,
    check_transition,
    current_application_event,
    derive_job_state,
    event_identity,
    group_events,
    is_text_identity,
    latest_assessment,
    next_events,
    normalize_job_identity,
)

URL = "https://boards.greenhouse.io/acme/jobs/101"
PASTED = text_identity("sha256:" + "a" * 64)


def _event(kind: str, occurred_at: str, *, event_id: str | None = None, supersedes: str | None = None, recorded_at: str | None = None, ref: str = URL) -> dict[str, object]:
    return {
        "event_id": event_id or f"event_{kind}_{occurred_at}",
        "external_ref": ref,
        "event_kind": kind,
        "occurred_at": occurred_at,
        "recorded_at": recorded_at or occurred_at,
        "supersedes": supersedes,
    }


APPLIED = _event("applied", "2026-09-01T10:00:00Z")
INTERVIEW = _event("interview_scheduled", "2026-09-05T10:00:00Z")
OFFER = _event("offer_received", "2026-09-10T10:00:00Z")
REJECTED = _event("rejected", "2026-09-12T10:00:00Z")
WITHDRAWN = _event("withdrawn", "2026-09-12T11:00:00Z")
SAVED = _event("saved", "2026-08-30T10:00:00Z")

MATCHED = AssessmentFact(at="2026-08-20T09:00:00Z", verdict=Verdict.MATCHED_ABOVE_THRESHOLD.value)
NEEDS_ANSWERS = AssessmentFact(at="2026-08-20T09:00:00Z", verdict=Verdict.PENDING_USER_ANSWERS.value)
NOT_A_MATCH = AssessmentFact(at="2026-08-20T09:00:00Z", verdict=Verdict.NOT_A_MATCH.value)
NO_VERDICT = AssessmentFact(at="2026-08-20T09:00:00Z", verdict=None)


# --- every state ---------------------------------------------------------------------


def test_the_states_are_the_ticket_s_and_the_application_ones_are_core_event_kinds() -> None:
    assert JOB_STATES == (
        "not_assessed",
        "assessed",
        "needs_answers",
        "weak_fit",  # 0110-10-02: needs answers, few requirements met, low rank (JobStateSources only)
        "thin_posting",  # 0.1.11.2: matched by verdict on no requirement row at all; never a match
        "matched",
        "not_a_match",
        "tailored",
        "applied",
        "interview_scheduled",
        "offer_received",
        "rejected",
        "withdrawn",
    )
    # Each application state is named after the core event kind that causes
    # it; ``saved`` is the one event kind that is not a state.
    assert set(APPLICATION_STATES) == EVENT_KINDS - {"saved"}


def test_nothing_stored_is_not_assessed() -> None:
    assert derive_job_state() == JobState("not_assessed", None, ("applied",))


@pytest.mark.parametrize(
    ("fact", "state"),
    [
        (NEEDS_ANSWERS, "needs_answers"),
        (MATCHED, "matched"),
        (NOT_A_MATCH, "not_a_match"),
        (NO_VERDICT, "assessed"),
    ],
)
def test_an_assessed_job_reads_its_verdict(fact: AssessmentFact, state: str) -> None:
    assert derive_job_state(assessments=[fact]) == JobState(state, "2026-08-20T09:00:00Z", ("applied",))


def test_every_verdict_the_contract_has_maps_to_a_state() -> None:
    states = {derive_job_state(assessments=[AssessmentFact(at=None, verdict=item.value)]).state for item in Verdict}
    assert states == {"needs_answers", "matched", "not_a_match"}


def test_a_tailored_resume_reads_tailored() -> None:
    assert derive_job_state(tailored_at="2026-08-25T09:00:00Z") == JobState("tailored", "2026-08-25T09:00:00Z", ("applied",))
    # A stored resume whose date is unknown is still a tailored resume.
    assert derive_job_state(has_tailored_resume=True) == JobState("tailored", None, ("applied",))


@pytest.mark.parametrize(
    ("events", "state", "since", "following"),
    [
        ([APPLIED], "applied", "2026-09-01T10:00:00Z", ("interview_scheduled", "offer_received", "rejected", "withdrawn")),
        ([APPLIED, INTERVIEW], "interview_scheduled", "2026-09-05T10:00:00Z", ("offer_received", "rejected", "withdrawn")),
        ([APPLIED, INTERVIEW, OFFER], "offer_received", "2026-09-10T10:00:00Z", ("rejected", "withdrawn")),
        ([APPLIED, INTERVIEW, REJECTED], "rejected", "2026-09-12T10:00:00Z", ()),
        ([APPLIED, WITHDRAWN], "withdrawn", "2026-09-12T11:00:00Z", ()),
    ],
)
def test_application_events_read_the_latest_event(events, state, since, following) -> None:
    assert derive_job_state(events=events) == JobState(state, since, following)
    # The order the events are listed in never matters.
    assert derive_job_state(events=list(reversed(events))) == JobState(state, since, following)


# --- precedence ----------------------------------------------------------------------


def test_application_events_win_over_a_tailored_resume_and_a_verdict() -> None:
    state = derive_job_state(events=[APPLIED], tailored_at="2026-09-20T09:00:00Z", assessments=[NOT_A_MATCH])
    assert state.state == "applied"
    assert state.since == "2026-09-01T10:00:00Z"


def test_a_tailored_resume_wins_over_a_verdict() -> None:
    newer = AssessmentFact(at="2026-09-30T09:00:00Z", verdict=Verdict.NOT_A_MATCH.value)
    state = derive_job_state(tailored_at="2026-08-25T09:00:00Z", assessments=[newer])
    assert (state.state, state.since) == ("tailored", "2026-08-25T09:00:00Z")


def test_a_verdict_wins_over_not_assessed() -> None:
    assert derive_job_state(assessments=[None, MATCHED]).state == "matched"


def test_a_saved_event_is_never_a_state() -> None:
    assert application_state([SAVED]) is None
    assert current_application_event([SAVED]) is None
    assert derive_job_state(events=[SAVED], assessments=[MATCHED]).state == "matched"
    assert derive_job_state(events=[SAVED]).state == "not_assessed"
    # ... and a later ``saved`` does not take a job out of its state.
    late_saved = _event("saved", "2026-09-30T10:00:00Z")
    assert derive_job_state(events=[APPLIED, late_saved]).state == "applied"


def test_a_superseded_event_is_not_active() -> None:
    wrong = _event("rejected", "2026-09-06T10:00:00Z", event_id="event_wrong")
    correction = _event("interview_scheduled", "2026-09-06T10:00:00Z", event_id="event_right", supersedes="event_wrong")
    state = derive_job_state(events=[APPLIED, wrong, correction])
    assert (state.state, state.since) == ("interview_scheduled", "2026-09-06T10:00:00Z")


def test_events_on_the_same_instant_are_ordered_by_when_they_were_recorded() -> None:
    first = _event("applied", "2026-09-01T10:00:00Z", recorded_at="2026-09-01T10:00:01Z")
    second = _event("interview_scheduled", "2026-09-01T10:00:00Z", recorded_at="2026-09-01T10:00:02Z")
    assert derive_job_state(events=[second, first]).state == "interview_scheduled"


def test_event_order_compares_instants_not_strings() -> None:
    # 09:00 at -05:00 is 14:00 UTC, after 12:00Z, though it sorts first as text.
    applied = _event("applied", "2026-09-01T12:00:00Z")
    interview = _event("interview_scheduled", "2026-09-01T09:00:00-05:00")
    assert derive_job_state(events=[applied, interview]).state == "interview_scheduled"


# --- which assessment is the latest --------------------------------------------------


def test_the_newer_assessment_decides_the_verdict() -> None:
    run = AssessmentFact(at="2026-08-20T09:00:00Z", verdict=Verdict.PENDING_USER_ANSWERS.value)
    quick = AssessmentFact(at="2026-08-21T09:00:00Z", verdict=Verdict.MATCHED_ABOVE_THRESHOLD.value)
    assert derive_job_state(assessments=[run, quick]).state == "matched"
    assert derive_job_state(assessments=[quick, run]).state == "matched"
    later_run = AssessmentFact(at="2026-08-22T09:00:00Z", verdict=Verdict.NOT_A_MATCH.value)
    assert derive_job_state(assessments=[later_run, quick]).state == "not_a_match"


def test_on_a_tie_the_later_listed_assessment_wins() -> None:
    run = AssessmentFact(at="2026-08-20T09:00:00Z", verdict=Verdict.PENDING_USER_ANSWERS.value)
    quick = AssessmentFact(at="2026-08-20T09:00:00Z", verdict=Verdict.MATCHED_ABOVE_THRESHOLD.value)
    assert latest_assessment([run, quick]) is quick


def test_an_assessment_with_no_date_loses_to_one_that_has_a_date() -> None:
    undated = AssessmentFact(at=None, verdict=Verdict.NOT_A_MATCH.value)
    assert latest_assessment([MATCHED, undated]) is MATCHED
    assert latest_assessment([undated]) is undated
    assert latest_assessment([None]) is None


def test_since_is_when_the_verdict_was_first_given() -> None:
    fact = AssessmentFact(at="2026-08-22T09:00:00Z", verdict=Verdict.MATCHED_ABOVE_THRESHOLD.value, since="2026-08-20T09:00:00Z")
    assert derive_job_state(assessments=[fact]).since == "2026-08-20T09:00:00Z"


# --- transitions ---------------------------------------------------------------------


def test_next_events_by_state() -> None:
    for state in ("not_assessed", "assessed", "needs_answers", "matched", "not_a_match", "tailored"):
        assert next_events(state) == ("applied",)
    assert next_events("applied") == ("interview_scheduled", "offer_received", "rejected", "withdrawn")
    assert next_events("interview_scheduled") == ("offer_received", "rejected", "withdrawn")
    assert next_events("offer_received") == ("rejected", "withdrawn")
    assert next_events("rejected") == ()
    assert next_events("withdrawn") == ()
    assert next_events("no_such_state") == ()
    # Whatever a state accepts next is a real core event kind.
    for state in JOB_STATES:
        assert set(next_events(state)) <= EVENT_KINDS


@pytest.mark.parametrize(
    ("events", "event_kind"),
    [
        ([], "applied"),
        ([], "saved"),
        ([SAVED], "applied"),
        ([SAVED], "saved"),
        ([APPLIED], "interview_scheduled"),
        ([APPLIED], "offer_received"),
        ([APPLIED], "rejected"),
        ([APPLIED], "withdrawn"),
        ([APPLIED, INTERVIEW], "offer_received"),
        ([APPLIED, INTERVIEW], "rejected"),
        ([APPLIED, INTERVIEW], "withdrawn"),
        ([APPLIED, INTERVIEW, OFFER], "withdrawn"),
        ([APPLIED, INTERVIEW, OFFER], "rejected"),
    ],
)
def test_an_accepted_transition_passes(events, event_kind) -> None:
    check_transition(events=events, event_kind=event_kind, occurred_at="2026-09-20T10:00:00Z")


@pytest.mark.parametrize(
    ("events", "event_kind"),
    [
        ([], "interview_scheduled"),  # no interview before applied
        ([], "offer_received"),
        ([], "rejected"),
        ([], "withdrawn"),
        ([SAVED], "interview_scheduled"),
        ([APPLIED], "applied"),  # not applied twice
        ([APPLIED], "saved"),
        ([APPLIED, INTERVIEW], "applied"),
        ([APPLIED, INTERVIEW], "interview_scheduled"),
        ([APPLIED, INTERVIEW, OFFER], "interview_scheduled"),
        ([APPLIED, INTERVIEW, OFFER], "offer_received"),
        ([APPLIED, REJECTED], "applied"),  # rejected ends the pipeline
        ([APPLIED, REJECTED], "interview_scheduled"),
        ([APPLIED, REJECTED], "withdrawn"),
        ([APPLIED, WITHDRAWN], "applied"),  # so does withdrawn
        ([APPLIED, WITHDRAWN], "rejected"),
    ],
)
def test_a_transition_the_state_does_not_accept_is_refused(events, event_kind) -> None:
    with pytest.raises(JobStateError) as refused:
        check_transition(events=events, event_kind=event_kind, occurred_at="2026-09-20T10:00:00Z")
    assert refused.value.code == "application_transition_refused"
    assert event_kind in str(refused.value) or event_kind == "saved"


def test_a_refusal_says_what_is_accepted_next() -> None:
    with pytest.raises(JobStateError) as refused:
        check_transition(events=[], event_kind="interview_scheduled")
    assert "has not been marked applied yet" in str(refused.value)
    assert "accepted next: applied" in str(refused.value)
    with pytest.raises(JobStateError) as ended:
        check_transition(events=[APPLIED, REJECTED], event_kind="applied")
    assert "ends its pipeline" in str(ended.value)
    assert "accepted next: none" in str(ended.value)


def test_an_event_dated_before_the_current_state_is_refused() -> None:
    with pytest.raises(JobStateError) as refused:
        check_transition(events=[APPLIED], event_kind="interview_scheduled", occurred_at="2026-08-31T10:00:00Z")
    assert refused.value.code == "application_event_out_of_order"
    # The same instant, or no date at all, is accepted.
    check_transition(events=[APPLIED], event_kind="interview_scheduled", occurred_at="2026-09-01T10:00:00Z")
    check_transition(events=[APPLIED], event_kind="interview_scheduled")


def test_a_refused_transition_wins_over_its_date() -> None:
    with pytest.raises(JobStateError) as refused:
        check_transition(events=[APPLIED], event_kind="applied", occurred_at="2026-08-31T10:00:00Z")
    assert refused.value.code == "application_transition_refused"


def test_a_corrected_event_no_longer_blocks_the_pipeline() -> None:
    wrong = _event("rejected", "2026-09-06T10:00:00Z", event_id="event_wrong")
    correction = _event("interview_scheduled", "2026-09-06T10:00:00Z", event_id="event_right", supersedes="event_wrong")
    check_transition(events=[APPLIED, wrong, correction], event_kind="offer_received", occurred_at="2026-09-07T10:00:00Z")


# --- identities ----------------------------------------------------------------------


def test_a_pasted_identity_is_the_exact_pattern() -> None:
    assert is_text_identity(PASTED)
    assert normalize_job_identity(PASTED) == PASTED
    for value in (
        "text:sha256:" + "a" * 63,
        "text:sha256:" + "a" * 65,
        "text:sha256:" + "A" * 64,
        "text:sha256:" + "g" * 64,
        "text:" + "a" * 64,
        "sha256:" + "a" * 64,
        " " + PASTED,
        PASTED + "\n",
        None,
        42,
    ):
        assert not is_text_identity(value)


def test_a_url_identity_is_normalized_like_a_posting_url() -> None:
    assert normalize_job_identity("https://Boards.Greenhouse.io/acme/jobs/101/?utm_source=x&gh_src=y") == URL
    assert normalize_job_identity(URL) == URL


@pytest.mark.parametrize("value", ["text:sha256:" + "a" * 63, "not a url", "ftp://example.com/job", "text:md5:abc"])
def test_anything_else_is_not_a_job_identity(value: str) -> None:
    with pytest.raises(FindJobsContractError):
        normalize_job_identity(value)


def test_events_group_by_their_one_identity() -> None:
    other = _event("applied", "2026-09-02T10:00:00Z", ref="https://jobs.lever.co/acme/abc")
    pasted = _event("applied", "2026-09-03T10:00:00Z", ref=PASTED)
    discover = {"event_id": "event_d", "opportunity_ref": "opportunity_" + "0" * 32, "event_kind": "applied", "occurred_at": "2026-09-04T10:00:00Z"}
    nameless = {"event_id": "event_n", "event_kind": "applied", "occurred_at": "2026-09-04T10:00:00Z"}
    grouped = group_events([APPLIED, INTERVIEW, other, pasted, discover, nameless, "not an event"])  # type: ignore[list-item]
    assert set(grouped) == {URL, "https://jobs.lever.co/acme/abc", PASTED, "opportunity_" + "0" * 32}
    assert grouped[URL] == [APPLIED, INTERVIEW]
    assert event_identity(discover) == "opportunity_" + "0" * 32
    assert event_identity(nameless) is None
    # One job's events never decide another's state.
    assert derive_job_state(events=grouped[PASTED]).state == "applied"
    assert derive_job_state(events=grouped[URL]).state == "interview_scheduled"


def test_the_served_shape_is_state_since_next_events() -> None:
    assert derive_job_state(events=[APPLIED]).to_json() == {
        "state": "applied",
        "since": "2026-09-01T10:00:00Z",
        "next_events": ["interview_scheduled", "offer_received", "rejected", "withdrawn"],
    }
    assert derive_job_state().to_json() == {"state": "not_assessed", "since": None, "next_events": ["applied"]}


# --- ledger 32: an assessment made on posting text that has since changed -------------------

_OLD = "sha256:" + "a" * 64
_NEW = "sha256:" + "b" * 64


def test_an_assessment_of_changed_posting_text_is_marked_stale() -> None:
    state = derive_job_state(
        assessments=[AssessmentFact(at="2026-09-01T10:00:00Z", verdict="matched_above_threshold", content_sha256=_OLD)],
        current_content_sha256=_NEW,
    )
    assert state.state == "matched"
    assert state.to_json()["assessment_stale"] == {"reason": "posting_changed"}


@pytest.mark.parametrize(
    ("assessed_on", "current"),
    [(_OLD, _OLD), (None, _NEW), (_OLD, None)],
    ids=["unchanged", "old record without a digest", "no current posting"],
)
def test_nothing_says_stale_without_two_digests_that_differ(assessed_on: str | None, current: str | None) -> None:
    state = derive_job_state(
        assessments=[AssessmentFact(at="2026-09-01T10:00:00Z", verdict="matched_above_threshold", content_sha256=assessed_on)],
        current_content_sha256=current,
    )
    assert "assessment_stale" not in state.to_json()


def test_only_the_assessment_that_gives_the_state_can_be_stale() -> None:
    stale = AssessmentFact(at="2026-09-01T10:00:00Z", verdict="matched_above_threshold", content_sha256=_OLD)
    fresh = AssessmentFact(at="2026-09-02T10:00:00Z", verdict="not_a_match", content_sha256=_NEW)
    assert "assessment_stale" not in derive_job_state(assessments=[stale, fresh], current_content_sha256=_NEW).to_json()
    tailored = derive_job_state(assessments=[stale], has_tailored_resume=True, current_content_sha256=_NEW)
    assert "assessment_stale" not in tailored.to_json()


def test_posting_incomplete_is_a_not_assessed_reason_and_in_the_openapi_document() -> None:
    from gigai.scout.find_jobs.api import openapi
    from gigai.scout.find_jobs.contracts import NotAssessedReason

    assert NotAssessedReason("posting_incomplete") is NotAssessedReason.POSTING_INCOMPLETE
    document = openapi.openapi_document(version="0")
    posting = document["paths"]["/api/runs/{run_id}/posting"]["get"]["description"]
    assert "posting_incomplete" in posting and "assessment_stale" in posting
    assert "assessment_stale" in document["paths"]["/api/jobs"]["get"]["description"]
