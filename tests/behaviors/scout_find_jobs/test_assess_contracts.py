"""P4: the quick-assess DTOs round-trip through JSON and fail closed."""

from __future__ import annotations

import json

import pytest

from gigai.scout.find_jobs.assess_contracts import (
    FETCH_KINDS,
    AssessJobInput,
    AssessPreferences,
    AssessResumeInput,
    ResolvedJob,
    ResolvedResume,
    text_identity,
)
from gigai.scout.find_jobs.contracts import FindJobsContractError, PinnedResume

_DIGEST = "sha256:" + "a" * 64
_PINNED = PinnedResume("record_00000000-0000-4000-8000-000000000001", "revision_00000000-0000-4000-8000-000000000001", _DIGEST)


def _round_trip(value):
    encoded = json.loads(json.dumps(value.to_json()))
    return type(value).from_json(encoded)


# --- AssessJobInput ----------------------------------------------------------------


@pytest.mark.parametrize("value", [AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/1"), AssessJobInput(job_text="Pasted posting")])
def test_job_input_round_trips(value: AssessJobInput) -> None:
    assert _round_trip(value) == value
    assert value.digest().startswith("sha256:")


def test_job_input_from_json_omits_absent_keys_and_rejects_unknown() -> None:
    assert AssessJobInput.from_json({"job_text": "Pasted"}) == AssessJobInput(job_text="Pasted")
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessJobInput.from_json({"job_text": "Pasted", "job_html": "<p>"})
    assert excinfo.value.code == "unknown_key"
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessJobInput.from_json({"job_url": None, "job_text": None})
    assert excinfo.value.code == "job_input_invalid"
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessJobInput.from_json({"job_url": 7})
    assert excinfo.value.code == "wrong_type"


# --- AssessResumeInput -------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [AssessResumeInput(), AssessResumeInput(profile_id="profile_00000000-0000-4000-8000-000000000001"), AssessResumeInput(resume_text="Resume")],
)
def test_resume_input_round_trips(value: AssessResumeInput) -> None:
    assert _round_trip(value) == value
    assert value.is_ephemeral is (value.resume_text is not None)


def test_resume_input_rejects_both_fields() -> None:
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessResumeInput.from_json({"profile_id": "profile_x", "resume_text": "Resume"})
    assert excinfo.value.code == "resume_input_invalid"


# --- AssessPreferences -------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        AssessPreferences(),
        AssessPreferences(visa_sponsorship_required=True),
        AssessPreferences(titles=(), countries=()),
        AssessPreferences(visa_sponsorship_required=False, titles=("staff engineer", "principal engineer"), countries=("US", "CA")),
    ],
)
def test_preferences_round_trip(value: AssessPreferences) -> None:
    restored = _round_trip(value)
    assert restored == value
    assert AssessPreferences.from_json({}) == AssessPreferences()


def test_preferences_validate_countries_and_types() -> None:
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessPreferences.from_json({"countries": ["usa"]})
    assert excinfo.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessPreferences.from_json({"visa_sponsorship_required": "yes"})
    assert excinfo.value.code == "wrong_type"
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessPreferences.from_json({"titles": "staff engineer"})
    assert excinfo.value.code == "wrong_type"


# --- ResolvedJob -------------------------------------------------------------------


def _fetched(kind: str) -> ResolvedJob:
    url = "https://boards.greenhouse.io/acme/jobs/101"
    return ResolvedJob(
        job_identity=url, source_url=url + "?gh_src=x", normalized_url=url, fetch_kind=kind,
        title="Software Engineer", company="acme", location="Denver, CO", text="Build services.", text_sha256=_DIGEST,
    )


def test_resolved_job_round_trips_every_fetch_kind() -> None:
    assert FETCH_KINDS == ("pasted", "ats_single", "ats_board", "generic")
    for kind in FETCH_KINDS[1:]:
        assert _round_trip(_fetched(kind)) == _fetched(kind)
    pasted = ResolvedJob(
        job_identity=text_identity(_DIGEST), source_url=None, normalized_url=None, fetch_kind="pasted",
        title="", company="", location="", text="Pasted posting", text_sha256=_DIGEST,
    )
    assert _round_trip(pasted) == pasted
    assert pasted.job_identity == "text:" + _DIGEST


def test_resolved_job_fails_closed() -> None:
    with pytest.raises(FindJobsContractError) as excinfo:
        _fetched("scraped")
    assert excinfo.value.code == "bad_enum"
    with pytest.raises(FindJobsContractError) as excinfo:  # a fetched job's identity is its normalized URL
        ResolvedJob(
            job_identity="https://other.example/1", source_url="https://a.example/1", normalized_url="https://a.example/1",
            fetch_kind="generic", title="", company="", location="", text="t", text_sha256=_DIGEST,
        )
    assert excinfo.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError) as excinfo:  # pasted jobs carry no URL
        ResolvedJob(
            job_identity=text_identity(_DIGEST), source_url="https://a.example/1", normalized_url=None,
            fetch_kind="pasted", title="", company="", location="", text="t", text_sha256=_DIGEST,
        )
    assert excinfo.value.code == "invalid_value"
    payload = _fetched("generic").to_json()
    payload["text_sha256"] = "sha256:short"
    with pytest.raises(FindJobsContractError) as excinfo:
        ResolvedJob.from_json(payload)
    assert excinfo.value.code == "invalid_value"
    payload = _fetched("generic").to_json()
    payload["schema_version"] = "scout-resolved-job:0"
    with pytest.raises(FindJobsContractError) as excinfo:
        ResolvedJob.from_json(payload)
    assert excinfo.value.code == "bad_enum"
    with pytest.raises(FindJobsContractError) as excinfo:
        text_identity("nope")
    assert excinfo.value.code == "invalid_value"


# --- ResolvedResume ----------------------------------------------------------------


def test_resolved_resume_round_trips_without_the_text() -> None:
    profile = ResolvedResume(profile_id="profile_00000000-0000-4000-8000-000000000001", pinned=_PINNED, content_sha256=_DIGEST, text="SECRET RESUME")
    ephemeral = ResolvedResume(profile_id=None, pinned=None, content_sha256=_DIGEST, text="SECRET RESUME")
    for value in (profile, ephemeral):
        payload = value.to_json()
        assert "text" not in payload and "SECRET" not in json.dumps(payload) and "SECRET" not in repr(value)
        restored = _round_trip(value)
        assert restored == value  # text is excluded from equality...
        assert restored.text == ""  # ...and never comes back from JSON
    assert profile.is_ephemeral is False and ephemeral.is_ephemeral is True


def test_resolved_resume_fails_closed() -> None:
    with pytest.raises(FindJobsContractError) as excinfo:  # profile_id without its pinned ref
        ResolvedResume(profile_id="profile_x", pinned=None, content_sha256=_DIGEST)
    assert excinfo.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError) as excinfo:  # digest disagrees with the pinned revision
        ResolvedResume(profile_id="profile_x", pinned=_PINNED, content_sha256="sha256:" + "b" * 64)
    assert excinfo.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError) as excinfo:
        ResolvedResume.from_json({"schema_version": "scout-resolved-resume:1", "profile_id": None, "pinned": None})
    assert excinfo.value.code == "missing_key"
