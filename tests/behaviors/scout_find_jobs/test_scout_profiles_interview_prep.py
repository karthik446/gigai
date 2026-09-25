"""S25 F1-b2: interview prep is keyed by profile; posting resolution is scoped to it.

Design source: ``orchestrator/docs/v0.1.9/spikes/S25-scout-interested-
profiles.md`` (Q5, "Legacy-run policy", the Packet plan's F1-b row).

Each test asserts the END outcome (what gets written/read, or what the
caller sees), never an intermediate.
"""

from __future__ import annotations

import json
from pathlib import Path

from gigai.canonical import digest_imported_bytes
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.interview_prep.posting import PostingUnavailableError, resolve_posting
from gigai.scout.interview_prep.storage import interview_prep_dir, prep_path, resolve_prep_read_path
from gigai.scout.profile_records import create_profile, selected_profile

from tests.support.scout_profile_fixtures import build_gig_with_resume

from .test_interview_prep_fixtures import _posting_row


def _write_acquire(workpad: Path, run_id: str, posting) -> None:
    from gigai.scout.find_jobs.contracts import (
        AcquireOutput, PostingRowResult, ProgressStatus, RowOutcome, SelectedPosting, URLSetDiff,
    )

    output = AcquireOutput(
        batch_id="batch_test", batch_ref=f"runs/{run_id}/outputs/acquire.json",
        progress_ref=f"runs/{run_id}/progress/acquire.jsonl", progress_status=ProgressStatus.COMPLETE,
        rows=(PostingRowResult(posting, RowOutcome.NEW),), failures=(),
        url_set_diff=URLSetDiff(added=(), removed=(), unchanged=(), edited=()),
        watchlist_refs=(), selected_postings=(SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True),),
    )
    outputs_dir = workpad / "runs" / run_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "acquire.json").write_text(json.dumps(output.to_json()), encoding="utf-8")


def _seal_run_input(workpad: Path, run_id: str, *, profile_ref: dict | None) -> None:
    """A minimal but VALID sealed ``find-jobs-run-input.json`` -- only what
    ``posting._sealed_run_profile_id`` reads (``profile_ref``, if any), but
    ``FindJobsRunInput.from_json`` validates ``config_digest ==
    config.digest()`` strictly, so it must be a genuine digest, not a fake
    one (a wrong digest fails to parse and silently degrades to "legacy",
    which would make this helper unable to produce the very fixture these
    tests need).
    """

    from gigai.scout.find_jobs.contracts import FindJobsConfig, ModelTarget, SourceToggles

    config = FindJobsConfig(
        roles=("staff platform engineer",), merged_queries=("staff platform engineer",),
        location=None, remote=True, published_after=None,
        sources=SourceToggles(exa=False, ats=False, hiringcafe=False),
        default_assess_cap=10, default_model_target=ModelTarget.OLLAMA_LOCAL,
    )
    sealed_dir = workpad / "runs" / run_id / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": 10,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "ollama_local",
        "pinned_resume": {
            "record_id": "record_00000000-0000-4000-8000-000000000001",
            "revision_id": "revision_00000000-0000-4000-8000-000000000001",
            "content_sha256": "sha256:" + "b" * 64,
        },
    }
    if profile_ref is not None:
        payload["profile_ref"] = profile_ref
    (sealed_dir / "find-jobs-run-input.json").write_text(json.dumps(payload), encoding="utf-8")


def _profile_ref(profile_id: str, *, revision: int = 1) -> dict:
    return {"profile_id": profile_id, "revision": revision, "content_digest": "sha256:" + "c" * 64}


# ---------------------------------------------------------------------------
# Interview prep storage: two profiles, same posting, no collision.
# ---------------------------------------------------------------------------


def test_two_profiles_same_posting_do_not_collide(tmp_path: Path) -> None:
    """Two profiles preparing the SAME posting write to two distinct files."""

    fixture = build_gig_with_resume(tmp_path, name="two-profiles-collision-proof")
    home, target = fixture.home_root, fixture.target
    profile_a_path = prep_path(home, target, "profile_aaaaaaaa-0000-4000-8000-000000000000", "https://boards.greenhouse.io/acme/jobs/1")
    profile_b_path = prep_path(home, target, "profile_bbbbbbbb-0000-4000-8000-000000000000", "https://boards.greenhouse.io/acme/jobs/1")

    assert profile_a_path != profile_b_path
    assert profile_a_path.parent != profile_b_path.parent  # distinct <profile_id>/ directories

    profile_a_path.parent.mkdir(parents=True)
    profile_b_path.parent.mkdir(parents=True)
    profile_a_path.write_text('{"prep": "for profile A"}', encoding="utf-8")
    profile_b_path.write_text('{"prep": "for profile B"}', encoding="utf-8")

    # Each profile's own read resolves to its own file, never the other's.
    assert resolve_prep_read_path(
        home, target, "profile_aaaaaaaa-0000-4000-8000-000000000000", "https://boards.greenhouse.io/acme/jobs/1",
        is_default_profile=False,
    ).read_text(encoding="utf-8") == '{"prep": "for profile A"}'
    assert resolve_prep_read_path(
        home, target, "profile_bbbbbbbb-0000-4000-8000-000000000000", "https://boards.greenhouse.io/acme/jobs/1",
        is_default_profile=False,
    ).read_text(encoding="utf-8") == '{"prep": "for profile B"}'


def test_legacy_flat_prep_file_migrates_under_default_profile(tmp_path: Path) -> None:
    """A pre-F1-b2 legacy flat prep file is read as the DEFAULT profile's own,

    without being moved or deleted (per the S25 spike's exclusion: legacy
    prep files are read, not migrated on disk).
    """

    fixture = build_gig_with_resume(tmp_path, name="legacy-flat-prep-migration-proof")
    home, target = fixture.home_root, fixture.target
    posting_id = "https://boards.greenhouse.io/acme/jobs/legacy"
    legacy_dir = interview_prep_dir(home, target)
    legacy_dir.mkdir(parents=True)
    legacy_digest = digest_imported_bytes(posting_id.encode("utf-8")).removeprefix("sha256:")
    legacy_file = legacy_dir / f"{legacy_digest}.json"
    legacy_file.write_text('{"prep": "legacy, pre-F1-b2"}', encoding="utf-8")

    default_profile_id = "profile_dddddddd-0000-4000-8000-000000000000"
    other_profile_id = "profile_eeeeeeee-0000-4000-8000-000000000000"

    # The default profile sees the legacy file (read fallback).
    default_read_path = resolve_prep_read_path(home, target, default_profile_id, posting_id, is_default_profile=True)
    assert default_read_path == legacy_file
    assert default_read_path.read_text(encoding="utf-8") == '{"prep": "legacy, pre-F1-b2"}'

    # A DIFFERENT (non-default) profile never sees it -- it gets its own
    # (not-yet-existing) per-profile path instead.
    other_read_path = resolve_prep_read_path(home, target, other_profile_id, posting_id, is_default_profile=False)
    assert other_read_path != legacy_file
    assert not other_read_path.is_file()

    # The legacy file itself is untouched (never moved/deleted).
    assert legacy_file.is_file()
    assert legacy_file.read_text(encoding="utf-8") == '{"prep": "legacy, pre-F1-b2"}'


# ---------------------------------------------------------------------------
# Posting resolution: legacy runs are visible ONLY to the default profile.
# ---------------------------------------------------------------------------


def test_legacy_run_visible_only_to_default_profile_scope(tmp_path: Path) -> None:
    """A run sealed before F1-a (no ``profile_ref``) is a candidate for the

    migrated DEFAULT profile's own posting resolution, but never for any
    OTHER (operator-created) profile's -- it was never produced under that
    profile (the S25 spike's Legacy-run policy).
    """

    fixture = build_gig_with_resume(tmp_path, name="legacy-scope-proof")
    default_profile = selected_profile(fixture.resolved, home_root=fixture.home_root, target=fixture.target)
    assert default_profile is not None
    assert default_profile.origin == "migrated_default"

    other_ref = PinnedResume(
        record_id=fixture.resume_record_id, revision_id=fixture.resume_revision_id, content_sha256="sha256:" + "0" * 64
    )
    other_profile = create_profile(
        fixture.resolved,
        label="other profile",
        titles=("staff platform engineer",),
        titles_to_avoid=(),
        queries=("staff platform engineer",),
        resume_ref=other_ref,
    )

    posting = _posting_row(url="https://boards.greenhouse.io/acme/jobs/legacy-run")
    legacy_run_id = "run_legacy_0000"
    _write_acquire(fixture.resolved.path, legacy_run_id, posting)
    _seal_run_input(fixture.resolved.path, legacy_run_id, profile_ref=None)  # legacy: no profile_ref at all

    # The DEFAULT profile's own posting resolution sees the legacy run.
    info = resolve_posting(
        resolved=fixture.resolved, normalized_url=posting.normalized_url, run_id=None,
        profile_id=default_profile.profile_id,
    )
    assert info.run_id == legacy_run_id

    # The OTHER profile's own posting resolution does NOT see it.
    try:
        resolve_posting(
            resolved=fixture.resolved, normalized_url=posting.normalized_url, run_id=None,
            profile_id=other_profile.profile_id,
        )
        raised = False
    except PostingUnavailableError:
        raised = True
    assert raised, "a legacy run must not be visible to a non-default profile's posting resolution"


def test_run_with_a_sealed_profile_ref_is_visible_only_to_that_profile(tmp_path: Path) -> None:
    """A run sealed AFTER F1-a (with its own ``profile_ref``) is scoped to

    exactly that profile -- never the default profile's scan (unless it IS
    the default), and never any other profile's.
    """

    fixture = build_gig_with_resume(tmp_path, name="sealed-profile-scope-proof")
    default_profile = selected_profile(fixture.resolved, home_root=fixture.home_root, target=fixture.target)
    assert default_profile is not None

    other_ref = PinnedResume(
        record_id=fixture.resume_record_id, revision_id=fixture.resume_revision_id, content_sha256="sha256:" + "0" * 64
    )
    other_profile = create_profile(
        fixture.resolved,
        label="other profile",
        titles=("staff platform engineer",),
        titles_to_avoid=(),
        queries=("staff platform engineer",),
        resume_ref=other_ref,
    )

    posting = _posting_row(url="https://boards.greenhouse.io/acme/jobs/other-profile-run")
    run_id = "run_other_profile_0000"
    _write_acquire(fixture.resolved.path, run_id, posting)
    _seal_run_input(fixture.resolved.path, run_id, profile_ref=_profile_ref(other_profile.profile_id))

    info = resolve_posting(
        resolved=fixture.resolved, normalized_url=posting.normalized_url, run_id=None,
        profile_id=other_profile.profile_id,
    )
    assert info.run_id == run_id

    try:
        resolve_posting(
            resolved=fixture.resolved, normalized_url=posting.normalized_url, run_id=None,
            profile_id=default_profile.profile_id,
        )
        raised = False
    except PostingUnavailableError:
        raised = True
    assert raised, "a run sealed for profile B must not be visible to the default profile's own scan"
