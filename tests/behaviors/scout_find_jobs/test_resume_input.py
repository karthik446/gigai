"""P4: the quick-assess resume resolves from a profile or stays ephemeral.

Built on ``tests.support.scout_profile_fixtures.build_gig_with_resume`` (a
real journal-authoritative workpad with one committed resume and a real
``find-jobs.json``), never a hand-faked tree.  The ephemeral test asserts
the END outcome the plan names: ``private_records.list_imports`` is unchanged
after resolving pasted resume text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.private_records import list_imports
from gigai.scout.find_jobs.assess_contracts import AssessPreferences, AssessResumeInput, ResolvedResume
from gigai.scout.find_jobs.contracts import FindJobsContractError, PinnedResume
from gigai.scout.find_jobs.resume_input import (
    read_config_preferences,
    resolve_preferences,
    resolve_profile,
    resolve_resume,
)
from gigai.scout.profile_records import create_profile, list_profiles, selected_profile
from gigai.scout.scout_cli import STARTER_FIND_JOBS_CONFIG

from tests.support.scout_profile_fixtures import build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services. (fixture only.)\n"


def _resolve(fx, input: AssessResumeInput) -> ResolvedResume:
    return resolve_resume(input, resolved=fx.resolved, home_root=fx.home_root, target=fx.target)


# --- profile path ------------------------------------------------------------------


def test_no_input_resolves_the_selected_profiles_resume(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path, resume_text=_RESUME)
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    resolved = _resolve(fx, AssessResumeInput())

    assert resolved.is_ephemeral is False
    assert resolved.profile_id == selected.profile_id
    assert resolved.pinned == selected.resume_ref
    assert resolved.pinned.record_id == fx.resume_record_id
    assert resolved.content_sha256 == digest_imported_bytes(_RESUME)
    assert resolved.text == _RESUME.decode("utf-8")
    assert "text" not in resolved.to_json()  # resume content is never serialized


def test_explicit_profile_resolves_that_profiles_resume(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path, resume_text=_RESUME)
    resume_ref = PinnedResume(fx.resume_record_id, fx.resume_revision_id, digest_imported_bytes(_RESUME))
    created = create_profile(
        fx.resolved, label="Staff backend", titles=("staff backend engineer",), titles_to_avoid=(),
        queries=("staff backend engineer",), resume_ref=resume_ref,
    )
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None and selected.profile_id != created.profile_id  # creating never selects

    resolved = _resolve(fx, AssessResumeInput(profile_id=created.profile_id))

    assert resolved.profile_id == created.profile_id
    assert resolved.pinned == resume_ref
    assert resolved.text == _RESUME.decode("utf-8")
    profile = resolve_profile(AssessResumeInput(profile_id=created.profile_id), resolved=fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None and profile.titles == ("staff backend engineer",)


def test_unknown_profile_is_profile_not_found(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    with pytest.raises(FindJobsContractError) as excinfo:
        _resolve(fx, AssessResumeInput(profile_id="profile_00000000-0000-4000-8000-00000000dead"))
    assert excinfo.value.code == "profile_not_found"


def test_no_selected_profile_is_profile_unavailable(tmp_path: Path) -> None:
    """No ``find-jobs.json`` -> no default profile can be migrated -> typed error, not None."""

    fx = build_gig_with_resume(tmp_path, write_config=False)
    with pytest.raises(FindJobsContractError) as excinfo:
        _resolve(fx, AssessResumeInput())
    assert excinfo.value.code == "profile_unavailable"
    assert "--resume-text" in str(excinfo.value)


def test_pinned_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path, resume_text=_RESUME)
    stale = PinnedResume(fx.resume_record_id, fx.resume_revision_id, "sha256:" + "1" * 64)
    created = create_profile(
        fx.resolved, label="stale", titles=("a",), titles_to_avoid=(), queries=("a",), resume_ref=stale,
    )
    with pytest.raises(FindJobsContractError) as excinfo:
        _resolve(fx, AssessResumeInput(profile_id=created.profile_id))
    assert excinfo.value.code == "resume_digest_mismatch"


# --- ephemeral path ------------------------------------------------------------------


def test_ephemeral_resume_never_writes_a_record(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    imports_before = list_imports(home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id)
    profiles_before = list_profiles(fx.resolved)
    text = "Jane Doe\nStaff engineer, ten years of Python.\n"

    resolved = _resolve(fx, AssessResumeInput(resume_text=text))

    assert resolved.is_ephemeral is True
    assert resolved.profile_id is None and resolved.pinned is None
    assert resolved.text == text.strip()
    assert resolved.content_sha256 == digest_imported_bytes(text.strip().encode("utf-8"))
    imports_after = list_imports(home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id)
    assert imports_after == imports_before
    assert list_profiles(fx.resolved) == profiles_before

    # Serialization, repr and the round trip never carry the resume text.
    serialized = json.dumps(resolved.to_json())
    assert "Jane Doe" not in serialized and "Jane Doe" not in repr(resolved)
    assert ResolvedResume.from_json(json.loads(serialized)) == resolved


def test_whitespace_only_ephemeral_text_is_rejected(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    with pytest.raises(FindJobsContractError) as excinfo:
        _resolve(fx, AssessResumeInput(resume_text=" \n "))
    assert excinfo.value.code == "resume_input_invalid"


def test_profile_and_text_together_are_rejected() -> None:
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessResumeInput(profile_id="profile_x", resume_text="text")
    assert excinfo.value.code == "resume_input_invalid"


# --- preferences ---------------------------------------------------------------------


def test_preference_defaults_come_from_config_and_profile(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)  # fixture config: countries=("US",), visa False
    profile = resolve_profile(AssessResumeInput(), resolved=fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None and profile.titles

    prefs = resolve_preferences(None, target=fx.target, profile=profile)

    assert prefs.visa_sponsorship_required is False
    assert prefs.countries == ("US",)
    assert prefs.titles == tuple(profile.titles)


def test_preference_overrides_win_field_by_field(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    profile = resolve_profile(AssessResumeInput(), resolved=fx.resolved, home_root=fx.home_root, target=fx.target)

    prefs = resolve_preferences(
        AssessPreferences(visa_sponsorship_required=True, titles=("data engineer",)), target=fx.target, profile=profile,
    )

    assert prefs.visa_sponsorship_required is True
    assert prefs.titles == ("data engineer",)
    assert prefs.countries == ("US",)  # not overridden: still the config default


def test_ephemeral_resume_defaults_titles_to_empty(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path)
    prefs = resolve_preferences(None, target=fx.target, profile=None)
    assert prefs.titles == ()
    assert prefs.countries == ("US",)


def test_config_preferences_are_read_tolerantly(tmp_path: Path) -> None:
    from gigai.canonical import canonical_json_bytes

    missing = tmp_path / "missing"
    missing.mkdir()
    assert read_config_preferences(missing) == (False, ())

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "find-jobs.json").write_text("{not json", encoding="utf-8")
    assert read_config_preferences(malformed) == (False, ())

    starter = tmp_path / "starter"
    starter.mkdir()
    (starter / "find-jobs.json").write_bytes(canonical_json_bytes(STARTER_FIND_JOBS_CONFIG.to_json()))
    assert read_config_preferences(starter) == (False, ())

    opted_in = tmp_path / "opted-in"
    opted_in.mkdir()
    payload = STARTER_FIND_JOBS_CONFIG.to_json() | {"visa_sponsorship_required": True, "countries": ["US", "CA"]}
    (opted_in / "find-jobs.json").write_bytes(canonical_json_bytes(payload))
    assert read_config_preferences(opted_in) == (True, ("US", "CA"))
