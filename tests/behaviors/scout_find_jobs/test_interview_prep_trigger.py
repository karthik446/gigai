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
    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path)
    write_acquire_output(home, target, "run_1")
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
    home, target, gig_id = bound_project(tmp_path)
    write_acquire_output(home, target, "run_1")
    (target / "find-jobs.json").write_text(
        '{"schema_version":"find-jobs-config:1","roles":["staff backend"],"merged_queries":[],'
        '"location":null,"remote":true,"published_after":null,'
        '"sources":{"exa":false,"ats":true,"hiringcafe":false},'
        '"default_assess_cap":10,"default_model_target":"ollama_local"}',
        encoding="utf-8",
    )
    with pytest.raises(InterviewPrepError) as excinfo:
        build_prep(home_root=home, target=target, posting_url=POSTING_URL)
    assert excinfo.value.code == "resume_missing"


def test_current_resume_reads_the_newest_imported_reference(tmp_path: Path) -> None:
    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path, text=b"Old resume text.\n")
    identity, data = current_resume(home_root=home, requested_target=target, gig_id=gig_id)
    assert data == b"Old resume text.\n"
    assert identity.content_sha256
