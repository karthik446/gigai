"""Synthetic journal-public-path checks for the R2 document service."""

from pathlib import Path
from dataclasses import replace

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.lifecycle import create_offline
from gigai.private_records import migrate_workpad_layout
from gigai.scout_document_records import (
    read_document_revision,
    read_final_selection,
    record_document_revision,
    record_final_selection,
    ScoutDocumentRecordError,
)
from gigai.scout_documents import SourceLineage, prepare_document_revision, select_final_documents
from gigai.scout_tailor_selection import TailorSelection, TailorSource
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad


OPP = "opportunity_" + "c" * 32
SNAP = "snapshot_" + "d" * 32
RECORD = "record_123e4567-e89b-42d3-a456-426614174010"
REV = "revision_123e4567-e89b-42d3-a456-426614174010"
REV_COVER = "revision_123e4567-e89b-42d3-a456-426614174011"


def _selection() -> TailorSelection:
    posting = b"Python role in Denver"
    candidate = b"Built Python tools"
    return TailorSelection(
        OPP,
        SNAP,
        ("resume", "cover_letter"),
        (
            TailorSource("posting_1", "posting", posting, digest_imported_bytes(posting), {"family": "g45_run_input", "run_input_id": "run_input_123e4567-e89b-42d3-a456-426614174010"}),
            TailorSource("candidate_1", "candidate_evidence", candidate, digest_imported_bytes(candidate), {"family": "g45_reference", "reference_id": "ref_123e4567-e89b-42d3-a456-426614174010"}),
        ),
    )


@pytest.fixture
def resolved(tmp_path: Path):
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="r2-documents", open_editor=False)
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
    return resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)


def test_document_and_final_selection_are_published_and_replay_safe(resolved) -> None:
    selection = _selection()
    resume_bytes = b"# Resume\n\n## Experience\nBuilt Python tools.\n"
    cover_bytes = b"# Cover Letter\n\nHello team.\n"
    resume = prepare_document_revision(selection, "resume", RECORD, REV, resume_bytes)
    cover = prepare_document_revision(selection, "cover_letter", RECORD, REV_COVER, cover_bytes, parent_revision_id=None)
    invocation = {"run_id": "run_123e4567-e89b-42d3-a456-426614174010", "goal_id": "goal_123e4567-e89b-42d3-a456-426614174010", "invocation_id": "inv_123e4567-e89b-42d3-a456-426614174010", "output_sha256": digest_imported_bytes(resume_bytes)}
    first = record_document_revision(resolved=resolved, revision=resume, invocation=invocation, operation_key="resume-1")
    replay = record_document_revision(resolved=resolved, revision=resume, invocation=invocation, operation_key="resume-1")
    assert first.created is True
    assert replay.created is False
    assert read_document_revision(resolved=resolved, record_id=RECORD, revision_id=REV, document_kind="resume").content == resume_bytes
    record_document_revision(resolved=resolved, revision=cover, invocation=invocation, operation_key="cover-1")
    final = select_final_documents(selection, (resume, cover), selected_by="local-user")
    selected = record_final_selection(resolved=resolved, selection=final, invocation=invocation, operation_key="final-1")
    selected_replay = record_final_selection(resolved=resolved, selection=final, invocation=invocation, operation_key="final-1")
    assert selected.created is True
    assert selected_replay.created is False
    assert read_final_selection(resolved=resolved, opportunity_id=OPP, snapshot_id=SNAP)["documents"]
    foreign = replace(resume, source_lineage=(SourceLineage("posting_1", resume.source_lineage[0].content_sha256, {"gig_id": "gig_123e4567-e89b-42d3-a456-426614174099"}),))
    with pytest.raises(ScoutDocumentRecordError):
        record_document_revision(resolved=resolved, revision=foreign, invocation=invocation, operation_key="foreign")
