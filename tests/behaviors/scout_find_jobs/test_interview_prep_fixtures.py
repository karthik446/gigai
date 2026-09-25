"""Shared synthetic fixtures for interview prep tests (no live calls).

Builds a bound Scout project with a resume reference and one find-jobs run's
acquire output on disk -- the exact shape ``interview_prep.posting`` and
``interview_prep.resume`` read (``AcquireOutput``/``PostingRow`` at
``runs/<run_id>/outputs/acquire.json``, a resume ``reference`` record).
Optionally seals an assessment revision so prep notes have a matrix to
reuse.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from gigai.canonical import digest_imported_bytes
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline
from gigai.private_records import create_record, import_reference
from gigai.scout.find_jobs.contracts import (
    AcquireOutput,
    ATSProvider,
    AssessmentResult,
    MatrixStatus,
    ModelTarget,
    Producer,
    PinnedResume,
    PostingRow,
    PostingRowResult,
    ProgressStatus,
    RequirementMatrixRow,
    RowOutcome,
    SelectedPosting,
    SourceKind,
    URLSetDiff,
)
from gigai.scout.proposal_records import save_assessment_revision
from gigai.scout.template import scout_candidate_inventory
from gigai.setup import build_config, run_setup
from gigai.workpad import select_active_workpad

POSTING_URL = "https://boards.greenhouse.io/acme/jobs/12345"
NORMALIZED_URL = "https://boards.greenhouse.io/acme/jobs/12345"
COMPANY = "Acme Corp"
TITLE = "Staff Backend Engineer"
POSTING_TEXT = (
    "You will own the payments platform and lead design reviews.\n"
    "Requirements: 8+ years of experience in distributed systems.\n"
    "You will collaborate with product on roadmap.\n"
    "Must have proficiency in Kubernetes and Go.\n"
)
RESUME_TEXT = b"Jane Doe\nStaff engineer with 9 years building distributed payments systems in Go.\n"


def bound_project(tmp_path: Path) -> tuple[Path, Path, str]:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialized = initialize_defaults(home_root=home, requested_target=target, username="owner", inventory=scout_candidate_inventory())
    instance = initialized.instances[0]
    approve_offline(home_root=home, requested_target=target, gig_id=instance.gig_id, proposal_id=str(instance.proposal_id))
    select_active_workpad(home_root=home, requested_target=target, gig_id=instance.gig_id, allow_semantic_state=True)
    return home, target, instance.gig_id


def add_resume(home: Path, target: Path, gig_id: str, tmp_path: Path, *, text: bytes = RESUME_TEXT) -> str:
    """Import a resume reference AND its ``g45_reference`` record wrapper.

    S25 F1-b2: a profile's ``resume_ref`` pins ``{record_id, revision_id}``
    (the record wrapper), not the bare imported reference id -- the same
    pair ``gigai scout resume add`` creates in one call
    (``scout_cli.resume_add_command``). Without the record, no profile can
    ever be migrated onto this resume at all (``profile_records.
    ensure_default_profile`` requires a committed record revision, not just
    an imported reference) -- this fixture predates F1-a/F1-b and is
    updated here to match, so every test built on it exercises the SAME
    profile-migration path the real CLI does.
    """

    resume_file = tmp_path / "resume.txt"
    resume_file.write_bytes(text)
    imported = import_reference(
        home_root=home, requested_target=target, gig_id=gig_id, kind="resume", source=resume_file,
        operation_key=f"test-resume:{digest_imported_bytes(text)}",
    )
    create_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key=f"test-resume-record:{imported.item_id}",
    )
    return imported.item_id


def _posting_row(*, text: str = POSTING_TEXT, url: str = POSTING_URL, company: str = COMPANY, title: str = TITLE) -> PostingRow:
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="acme",
        company=company, title=title, location="Remote", published_at=None,
        content_sha256=digest_imported_bytes(text.encode("utf-8")), source_kind=SourceKind.ATS,
        query_key="staff backend engineer", text=text,
    )


def write_acquire_output(home: Path, target: Path, run_id: str, *, posting: PostingRow | None = None, outcome: RowOutcome = RowOutcome.NEW) -> PostingRow:
    posting = posting or _posting_row()
    output = AcquireOutput(
        batch_id="batch_test", batch_ref=f"runs/{run_id}/outputs/acquire.json",
        progress_ref=f"runs/{run_id}/progress/acquire.jsonl", progress_status=ProgressStatus.COMPLETE,
        rows=(PostingRowResult(posting, outcome),), failures=(),
        url_set_diff=URLSetDiff(added=(), removed=(), unchanged=(), edited=()),
        watchlist_refs=(), selected_postings=(SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True),),
    )
    outputs_dir = home_workpad_path(home, target) / "runs" / run_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "acquire.json").write_text(json.dumps(output.to_json()), encoding="utf-8")
    return posting


def home_workpad_path(home: Path, target: Path) -> Path:
    from gigai.workpad import resolve_workpad
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    return resolved.path


def write_assessment(
    home: Path, target: Path, posting: PostingRow, *,
    matrix: tuple[RequirementMatrixRow, ...] = (
        RequirementMatrixRow("8+ years distributed systems", ("9 years building distributed payments systems",), MatrixStatus.MET),
        RequirementMatrixRow("Kubernetes production experience", (), MatrixStatus.GAP),
    ),
) -> None:
    selected = SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True)
    result = AssessmentResult(posting=selected, matrix=matrix, suggestions=(), questions=(), proposal_revision_ref=None)
    producer = Producer("scout.find_jobs.assess", "1", "scout-assess", ModelTarget.OLLAMA_LOCAL, "ollama_local")
    pinned = PinnedResume(record_id="record_00000000-0000-4000-8000-000000000000", revision_id="revision_00000000-0000-4000-8000-000000000000", content_sha256=digest_imported_bytes(RESUME_TEXT))
    save_assessment_revision(home_root=home, target=target, posting=selected, result=result, producer=producer, pinned_resume=pinned)


__all__ = [
    "COMPANY", "NORMALIZED_URL", "POSTING_TEXT", "POSTING_URL", "RESUME_TEXT", "TITLE",
    "add_resume", "bound_project", "home_workpad_path", "write_acquire_output", "write_assessment",
]
