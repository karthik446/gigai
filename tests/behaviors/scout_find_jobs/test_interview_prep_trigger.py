"""Trigger + idempotency + posting/resume resolution (no live calls)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout.interview_prep import InterviewPrepError, build_prep
from gigai.scout.interview_prep.posting import PostingUnavailableError, resolve_posting
from gigai.scout.interview_prep.resume import current_resume
from gigai.workpad import resolve_workpad

from .test_interview_prep_fixtures import (
    NORMALIZED_URL,
    POSTING_URL,
    add_resume,
    bound_project,
    write_acquire_output,
    write_assessment,
)


def test_missing_find_jobs_config_refuses_before_any_web_call(tmp_path: Path) -> None:
    """No ``find-jobs.json`` at all -> refused with the RIGHT error: setup is

    missing, not the resume. F1-b2-r1 fix: resume resolution now goes
    through the gig's SELECTED profile (``current_resume``), and a profile
    only comes to exist via migration off a real ``find-jobs.json`` -- but a
    project with a resume and NO ``find-jobs.json`` at all must still say
    ``find_jobs_config_missing`` (the user HAS a resume; what's missing is
    Scout setup), never the misleading ``resume_missing``. This restores
    this test's original (pre-F1-b2) assertion, unchanged.
    """

    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path)
    write_acquire_output(home, target, "run_1")
    with pytest.raises(InterviewPrepError) as excinfo:
        build_prep(home_root=home, target=target, posting_url=POSTING_URL)
    assert excinfo.value.code == "find_jobs_config_missing"


def test_starter_placeholder_find_jobs_config_refuses_with_config_missing(tmp_path: Path) -> None:
    """``find-jobs.json`` exists but is still the UNCHANGED starter placeholder

    (``gigai scout install`` writes it, before the setup interview is ever
    run) -- no profile can migrate off it (``profile_records.
    ensure_default_profile``'s own guard), so this must be treated exactly
    like "no find-jobs.json at all": ``find_jobs_config_missing``, never
    ``resume_missing``.
    """

    from gigai.scout.scout_cli import write_starter_find_jobs_config

    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path)
    write_acquire_output(home, target, "run_1")
    write_starter_find_jobs_config(target)
    with pytest.raises(InterviewPrepError) as excinfo:
        build_prep(home_root=home, target=target, posting_url=POSTING_URL)
    assert excinfo.value.code == "find_jobs_config_missing"


def test_posting_not_found_refuses(tmp_path: Path) -> None:
    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    with pytest.raises(PostingUnavailableError) as excinfo:
        resolve_posting(resolved=resolved, normalized_url=NORMALIZED_URL, run_id=None)
    assert excinfo.value.code == "posting_no_runs"


def test_posting_resolves_from_newest_run_when_no_run_given(tmp_path: Path) -> None:
    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path)
    posting = write_acquire_output(home, target, "run_1")
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    info = resolve_posting(resolved=resolved, normalized_url=NORMALIZED_URL, run_id=None)
    assert info.posting.title == posting.title
    assert info.run_id == "run_1"
    assert info.matrix is None  # not assessed yet


def test_posting_resolution_reuses_assess_matrix_when_it_exists(tmp_path: Path) -> None:
    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path)
    posting = write_acquire_output(home, target, "run_1")
    write_assessment(home, target, posting)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    info = resolve_posting(resolved=resolved, normalized_url=NORMALIZED_URL, run_id=None)
    assert info.matrix is not None
    assert info.matrix_source == "assess"
    assert any(row.status.value == "met" for row in info.matrix.matrix)


def test_no_resume_refuses_before_any_web_call(tmp_path: Path) -> None:
    """A REAL (non-starter, schema-valid) ``find-jobs.json`` but NO resume

    at all -- Scout IS set up, so this is genuinely ``resume_missing``, not
    ``find_jobs_config_missing`` (F1-b2-r1's own distinction). ``merged_
    queries`` is fixed to match ``roles`` here (a pre-existing fixture typo:
    an empty list fails ``FindJobsConfig.from_json`` outright, which used to
    go unnoticed only because the OLD resume resolver never parsed
    ``find-jobs.json`` at all -- the new profile-aware one does, so an
    unparseable config now degrades the same as a missing one).
    """

    home, target, gig_id = bound_project(tmp_path)
    write_acquire_output(home, target, "run_1")
    (target / "find-jobs.json").write_text(
        '{"schema_version":"find-jobs-config:1","roles":["staff backend"],"merged_queries":["staff backend"],'
        '"location":null,"remote":true,"published_after":null,'
        '"sources":{"exa":false,"ats":true,"hiringcafe":false},'
        '"default_assess_cap":10,"default_model_target":"ollama_local"}',
        encoding="utf-8",
    )
    with pytest.raises(InterviewPrepError) as excinfo:
        build_prep(home_root=home, target=target, posting_url=POSTING_URL)
    assert excinfo.value.code == "resume_missing"


def test_current_resume_reads_the_selected_profiles_resume(tmp_path: Path) -> None:
    """S25 F1-b2: ``current_resume`` resolves the SELECTED PROFILE's resume,

    not "the newest imported reference" -- this replaces this file's
    pre-F1-b2 version of this test (that behaviour, and its name, are
    exactly what F1-b2 retires: a profile now exists to have a
    ``resume_ref``, and a profile only exists once a real
    ``find-jobs.json`` lets one migrate).
    """

    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path, text=b"Old resume text.\n")
    (target / "find-jobs.json").write_text(
        '{"schema_version":"find-jobs-config:1","roles":["staff backend"],"merged_queries":["staff backend"],'
        '"location":null,"remote":true,"published_after":null,'
        '"sources":{"exa":false,"ats":true,"hiringcafe":false},'
        '"default_assess_cap":10,"default_model_target":"ollama_local"}',
        encoding="utf-8",
    )
    identity, data = current_resume(home_root=home, requested_target=target, gig_id=gig_id)
    assert data == b"Old resume text.\n"
    assert identity.content_sha256
