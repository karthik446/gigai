"""A1: Scout projection joins an application event's external_ref to a
find-jobs posting by normalized_url (5), tolerating an unmatched one."""

from __future__ import annotations

import json

from tests.behaviors.scout_proposals_tools.test_scout05_first_proposal import _bound_defaults
from tests.behaviors.scout_tracking_reporting.test_scout09_application_events import _application_ext

from gigai.canonical import digest_imported_bytes
from gigai.journal import JournalArtifact, record_transition, run_with_journal_writer
from gigai.scout.find_jobs.contracts import (
    AcquireOutput,
    ATSProvider,
    PostingRow,
    PostingRowResult,
    ProgressStatus,
    RowOutcome,
    SelectedPosting,
    SourceKind,
    URLSetDiff,
)
from gigai.scout.projection import projection_from_snapshot
from gigai.scout.report import render_html
from gigai.workpad import resolve_workpad

NORMALIZED_URL = "https://boards.greenhouse.io/acme/jobs/12345"


def _posting_row(*, url: str = NORMALIZED_URL, title: str = "Staff Backend Engineer") -> PostingRow:
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="acme",
        company="Acme Corp", title=title, location="Remote", published_at=None,
        content_sha256=digest_imported_bytes(url.encode("utf-8")), source_kind=SourceKind.ATS,
        query_key="staff backend engineer",
    )


def _write_acquire_output(resolved, run_id: str, posting: PostingRow) -> None:
    output = AcquireOutput(
        batch_id="batch_test", batch_ref=f"runs/{run_id}/outputs/acquire.json",
        progress_ref=f"runs/{run_id}/progress/acquire.jsonl", progress_status=ProgressStatus.COMPLETE,
        rows=(PostingRowResult(posting, RowOutcome.NEW),), failures=(),
        url_set_diff=URLSetDiff(added=(), removed=(), unchanged=(), edited=()),
        watchlist_refs=(), selected_postings=(SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True),),
    )
    path = f"runs/{run_id}/outputs/acquire.json"
    data = json.dumps(output.to_json()).encode("utf-8")
    # find-jobs commits its own Run outputs through the same journal
    # transition mechanism as any other committed artifact (one handoff per
    # publishing commit); this mirrors _publish_application_artifacts in
    # test_scout09_application_events.py for the same reason.
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id="handoff_00000000-0000-4000-8000-000000000401",
        transition="private_record_revised",
        body="synthetic find-jobs acquire output fixture",
        artifacts=(JournalArtifact(path, data),),
        front_matter={
            "artifact_refs": [
                {
                    "path": path,
                    "content_sha256": digest_imported_bytes(data),
                    "media_type": "application/json",
                    "size_bytes": len(data),
                }
            ]
        },
    )


def _snapshot(resolved):
    return run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(("records/", "runs/")),
    )


def test_external_ref_event_joins_matching_find_jobs_posting(tmp_path) -> None:
    home, target, _project, gig, _workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    posting = _posting_row()
    _write_acquire_output(resolved, "run_test_001", posting)
    result = _application_ext(resolved, "a1-join", external=NORMALIZED_URL, kind="applied")
    assert result["status"] == "recorded"

    projection = projection_from_snapshot(snapshot=_snapshot(resolved), project_id=resolved.project_id, gig_id=resolved.gig_id)
    assert len(projection.applications) == 1
    event = projection.applications[0]
    assert event["external_ref"] == NORMALIZED_URL
    assert "opportunity_ref" not in event
    assert event["linked_posting"] is not None
    assert event["linked_posting"]["normalized_url"] == NORMALIZED_URL
    assert event["linked_posting"]["title"] == "Staff Backend Engineer"


def test_external_ref_event_with_no_matching_posting_is_unlinked_not_dropped(tmp_path) -> None:
    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    # No acquire output committed at all: the external_ref names no posting
    # this Gig has ever seen.
    result = _application_ext(resolved, "a1-unlinked", external="https://boards.greenhouse.io/nowhere/jobs/1")
    assert result["status"] == "recorded"

    projection = projection_from_snapshot(snapshot=_snapshot(resolved), project_id=resolved.project_id, gig_id=resolved.gig_id)
    assert len(projection.applications) == 1
    event = projection.applications[0]
    assert event["external_ref"] == "https://boards.greenhouse.io/nowhere/jobs/1"
    assert event["linked_posting"] is None
    # Not dropped: the event still appears in the report's application
    # history, rendered as posting-unresolved rather than erroring.
    html_bytes, _css = render_html(
        projection=projection,
        template=b"<!doctype html><html><body><main><!-- SCOUT:CONTENT --></main></body></html>",
        css=b"body{}",
        workpad=workpad,
    )
    html = html_bytes.decode("utf-8")
    assert "posting unresolved" in html


def test_opportunity_ref_event_is_untouched_by_the_external_ref_join(tmp_path) -> None:
    """(1) reader: projection._link_external_refs -- an opportunity_ref event
    never gains a linked_posting key at all."""
    from tests.behaviors.scout_tracking_reporting.test_scout09_application_events import _application

    home, target, _project, gig, _workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    result = _application(resolved, "a1-legacy-untouched")
    assert result["status"] == "recorded"

    projection = projection_from_snapshot(snapshot=_snapshot(resolved), project_id=resolved.project_id, gig_id=resolved.gig_id)
    assert len(projection.applications) == 1
    event = projection.applications[0]
    assert event["opportunity_ref"] == "opportunity_00000000000000000000000000000001"
    assert "linked_posting" not in event
