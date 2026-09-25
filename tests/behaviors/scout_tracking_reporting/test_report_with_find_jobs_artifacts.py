"""regression-003: ``report_readers.py``'s ``_receipt_rows``/``_proposal_rows``
must not mistake find-jobs' own committed artifacts for Discover-shaped ones.

Root cause (fixed in P9c-runs-applications, this file adds the dedicated
tests the coordinator's decision required): ``runs/{run_id}/receipts/*.json``
is shared between Discover's own external-recording receipts (what
``_receipt_rows`` was written to read) and find-jobs' own per-node receipts
(``NodeReceipt``, ``runs/{run_id}/receipts/{acquire,assess,present}.json``);
``records/scout-proposals/.../revisions/*.json`` is shared between Discover's
own proposal revisions (what ``_proposal_rows`` was written to read) and
find-jobs' own committed ASSESSMENT revisions
(``proposal_records.save_assessment_revision``, ``schema_version:
"scout-assessment-revision:1"``, no ``opportunity`` key at all). Before the
fix, either kind of find-jobs artifact existing anywhere in the gig made
``rebuild_projection``/``report.render_html`` -- and therefore ``gigai scout
report`` -- crash, even when the crashing row had nothing to do with the
report the operator was trying to read.

Fixture reuse (per the task): ``_completed_find_jobs`` (``tests.behaviors.
scout_discovery.test_scout07_posting_inputs``) is the same real, genuine
Discover flow (``external_recording.plan_v2``/``start_v2``/
``checkpoint_v2``/``submit_v2``) ``test_scout_r4_journey.py`` builds its own
Discover evidence from -- it commits a real ``runs/{id}/receipts/*.json``
external-recording receipt, a real ``runs/{id}/external-run.json``, and a
real Discover opportunity, all through the public API, never hand-faked.
``save_assessment_revision`` is the same public find-jobs entrypoint
``test_interview_prep_fixtures.py``/``test_assess_model_policy.py`` already
call directly to commit a real assessment revision.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.journal import JournalArtifact, record_transition
from gigai.scout.find_jobs.contracts import (
    AssessmentResult,
    MatrixStatus,
    ModelTarget,
    NodeReceipt,
    NodeStatus,
    Producer,
    PinnedResume,
    RequirementMatrixRow,
    SelectedPosting,
)
from gigai.scout.projection import ScoutProjectionError, rebuild_projection
from gigai.scout.proposal_records import save_assessment_revision
from gigai.scout.report import render_html
from gigai.scout.report_readers import default_reader_set

from tests.behaviors.scout_discovery.test_scout07_posting_inputs import _completed_find_jobs

_TEMPLATE = b"<!doctype html><html><head><link rel='stylesheet' href='style.css'></head><body><main><!-- SCOUT:CONTENT --></main></body></html>"
_CSS = b"body { color: #111; }"


def _find_jobs_node_receipt(*, goal_id: str) -> bytes:
    """A real, schema-valid ``NodeReceipt`` (``acquire``'s own committed
    receipt shape) -- the exact family of file that shares ``runs/{run_id}/
    receipts/*.json`` with Discover's own external-recording receipts."""

    receipt = NodeReceipt(
        goal_id=goal_id,
        goal_version=1,
        executor="cap_00000000-0000-4000-8000-000000000001",
        node_slug="acquire",
        operation_key="regression-003-node-receipt",
        status=NodeStatus.COMPLETE,
        outcome="COMPLETE",
        errors=(),
        evidence=(),
        producer=Producer("scout.find_jobs.acquire", "1", "scout-acquire", ModelTarget.OLLAMA_LOCAL, "ollama_local"),
        usage=None,
        started_at="2026-09-25T00:00:00Z",
        finished_at="2026-09-25T00:00:05Z",
        failure=None,
    )
    return canonical_json_bytes(receipt.to_json())


def _commit_find_jobs_node_receipt(resolved, *, run_id: str, goal_id: str) -> str:
    path = f"runs/{run_id}/receipts/acquire.json"
    data = _find_jobs_node_receipt(goal_id=goal_id)
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id="handoff_00000000-0000-4000-8000-000000000501",
        transition="private_record_revised",
        body="synthetic find-jobs node receipt fixture (regression-003)",
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
    return path


def _write_find_jobs_assessment_revision(resolved) -> None:
    """A real committed find-jobs ASSESSMENT revision (``schema_version:
    "scout-assessment-revision:1"``) -- the exact family of file that shares
    ``records/scout-proposals/.../revisions/*.json`` with Discover's own
    proposal revisions."""

    url = "https://boards.greenhouse.io/acme/jobs/202"
    selected = SelectedPosting(url, url, digest_imported_bytes(url.encode("utf-8")), True)
    result = AssessmentResult(
        posting=selected,
        matrix=(RequirementMatrixRow("Python", ("Built Python services",), MatrixStatus.MET),),
        suggestions=(),
        questions=(),
        proposal_revision_ref=None,
    )
    producer = Producer("scout.find_jobs.assess", "1", "scout-assess", ModelTarget.OLLAMA_LOCAL, "ollama_local")
    pinned = PinnedResume(
        record_id="record_00000000-0000-4000-8000-000000000099",
        revision_id="revision_00000000-0000-4000-8000-000000000099",
        content_sha256=digest_imported_bytes(b"synthetic resume"),
    )
    save_assessment_revision(
        home_root=None, target=resolved, posting=selected, result=result, producer=producer, pinned_resume=pinned,
    )


_FIND_JOBS_RUN_ID = "run_00000000-0000-4000-8000-0000000000f9"


def test_rebuild_projection_and_report_succeed_with_both_discover_and_find_jobs_evidence(
    tmp_path: Path,
) -> None:
    """The end outcome: a gig with BOTH real Discover evidence AND real
    find-jobs artifacts (a NodeReceipt under runs/*/receipts/ and a
    save_assessment_revision under records/scout-proposals/) -- rebuild_
    projection succeeds and the report renders, showing the real Discover
    opportunity content, never crashing over the find-jobs artifacts sharing
    the same storage PATH SHAPE (never the same run_id -- a find-jobs run
    and a Discover run are always distinct Runs in a real gig; what's
    shared between them is the `receipts/*.json`/`scout-proposals/.../
    revisions/*.json` path CONVENTION, which is exactly what regression-003
    was about)."""

    resolved, _selector, _snapshot, metadata = _completed_find_jobs(tmp_path)
    posting = metadata["posting"]

    _commit_find_jobs_node_receipt(resolved, run_id=_FIND_JOBS_RUN_ID, goal_id="goal_00000000-0000-4000-8000-000000000001")
    _write_find_jobs_assessment_revision(resolved)

    projection = rebuild_projection(resolved=resolved, readers=default_reader_set(resolved))
    assert projection.opportunities, "the real Discover opportunity must still be read"
    opportunity = next(
        item for item in projection.opportunities
        if item.get("opportunity_id") == posting["opportunity_id"]
    )
    assert opportunity["title"] == posting["title"]

    rendered, _css = render_html(projection=projection, template=_TEMPLATE, css=_CSS, workpad=resolved.path)
    html = rendered.decode("utf-8")
    assert posting["title"] in html


_MALFORMED_RECEIPT_RUN_ID = "run_00000000-0000-4000-8000-0000000000fa"


def test_a_malformed_discover_receipt_still_raises_scout_report_run_invalid(tmp_path: Path) -> None:
    """A Discover receipt that is genuinely malformed (does NOT declare the
    find-jobs NodeReceipt schema, so the skip added for regression-003 never
    fires) must still fail the same way it always did -- the fix narrows the
    tolerance to exactly find-jobs' own schema, it does not weaken validation
    for an actual bad Discover receipt.

    Committed at a SEPARATE run_id/path (immutable receipts cannot be
    replaced in place once published -- ``_validate_committed_artifact``
    itself refuses a second publisher at the same path, a different,
    unrelated protection this test must not trip instead of the one it's
    aiming at) -- this is a second, standalone malformed receipt, not a
    tampered copy of the real one.
    """

    resolved, _selector, _snapshot, _metadata = _completed_find_jobs(tmp_path)

    # Still declares external-recording's "1.0" schema_version (so the
    # regression-003 skip does NOT apply), but is otherwise missing every
    # required field -- genuinely malformed, not a find-jobs artifact.
    path = f"runs/{_MALFORMED_RECEIPT_RUN_ID}/receipts/receipt_00000000-0000-4000-8000-0000000000fb.json"
    malformed = {"schema_version": "1.0", "not_a_real_receipt": True}
    data = canonical_json_bytes(malformed)
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id="handoff_00000000-0000-4000-8000-000000000502",
        transition="private_record_revised",
        body="malformed Discover receipt fixture (regression-003 malformed case)",
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

    with pytest.raises(ScoutProjectionError) as error:
        rebuild_projection(resolved=resolved, readers=default_reader_set(resolved))
    assert error.value.code == "scout_report_run_invalid"


def test_a_malformed_proposal_revision_still_fails_the_same_way_as_before(tmp_path: Path) -> None:
    """A proposal revision that is genuinely malformed (does NOT declare the
    find-jobs assessment schema, so the regression-003 skip never fires)
    must still fail exactly as it always did."""

    resolved, _selector, _snapshot, _metadata = _completed_find_jobs(tmp_path)

    record_id = "record_00000000-0000-4000-8000-000000000077"
    revision_id = "revision_00000000-0000-4000-8000-000000000077"
    path = f"records/scout-proposals/{record_id}/revisions/{revision_id}.json"
    malformed = {
        "schema_version": "scout-proposal-revision:1",
        "record_id": record_id,
        "revision_id": revision_id,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "state": "active",
        # No "opportunity" key -- this is the same shape of malformed
        # Discover proposal revision that must still fail closed.
    }
    data = canonical_json_bytes(malformed)
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id="handoff_00000000-0000-4000-8000-000000000503",
        transition="private_record_revised",
        body="malformed Discover proposal revision fixture (regression-003 malformed case)",
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

    with pytest.raises(ScoutProjectionError) as error:
        rebuild_projection(resolved=resolved, readers=default_reader_set(resolved))
    assert error.value.code == "scout_report_proposal_invalid"
