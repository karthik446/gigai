from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.journal import JournalArtifact, JournalSnapshot, record_transition
from gigai.journal import run_with_journal_writer
from gigai.scout.find_jobs.contracts import (
    ModelTarget,
    NodeContext,
    NodeReceipt,
    PresentInput,
    PresentOutput,
    PresentPayload,
)
from gigai.scout.projection import ScoutProjection, ScoutReaderSet, build_present_payload, present_node, projection_from_snapshot, query_projection
from gigai.scout.report import ScoutReportError, publish_report, read_current_report, render_html
from gigai.scout.report_readers import _run_rows
from gigai.workpad import ResolvedWorkpad, resolve_workpad, select_active_workpad
from tests.behaviors.scout_tracking_reporting.test_scout09_application_events import _application
from tests.behaviors.scout_proposals_tools.test_scout05_first_proposal import _bound_defaults

FIXTURES = Path(__file__).resolve().parents[2] / "behaviors" / "scout_find_jobs" / "fixtures"


def _fixture(name: str) -> dict:
    import json

    return json.loads((FIXTURES / name).read_bytes())


def _projection(**changes):
    value = {
        "schema_version": "scout-projection:1",
        "project_id": "project_00000000-0000-4000-8000-000000000001",
        "gig_id": "gig_00000000-0000-4000-8000-000000000002",
        "journal_head": "a" * 40,
        "opportunities": (), "proposals": (), "questions": (), "documents": (),
        "applications": (), "runs": (), "cursor": {"schema_version": "scout-projection:1", "journal_head": "a" * 40},
    }
    value.update(changes)
    return ScoutProjection(**value)


def _resolved(tmp_path: Path) -> ResolvedWorkpad:
    root = tmp_path / "gig"
    (root / ".git").mkdir(parents=True)
    (root / "ui").mkdir()
    (root / "state.sqlite").touch()
    (root / "ui/template.html").write_text("<!doctype html><html><head><link rel='stylesheet' href='style.css'></head><body><main><!-- SCOUT:CONTENT --></main></body></html>", encoding="utf-8")
    (root / "ui/style.css").write_text("body { color: #111; }", encoding="utf-8")
    return ResolvedWorkpad(
        project_id="project_00000000-0000-4000-8000-000000000001",
        gig_id="gig_00000000-0000-4000-8000-000000000002",
        path=root,
        target_root=tmp_path,
        target_kind="directory",
    )


def test_projection_fixture_reader_and_sql_view_are_rebuildable():
    snapshot = JournalSnapshot("b" * 40, {})
    readers = ScoutReaderSet(
        opportunities=lambda _snapshot, _project, _gig: [{
            "opportunity_id": "opportunity_00000000000000000000000000000001",
            "snapshot_id": "snapshot_00000000000000000000000000000001",
            "title": "Forward Deployed Engineer",
        }],
        proposals=lambda *_args: [{"opportunity_id": "opportunity_00000000000000000000000000000001", "status": "complete", "revision_id": "revision_00000000-0000-4000-8000-000000000003"}],
    )
    projection = projection_from_snapshot(snapshot=snapshot, project_id="project_00000000-0000-4000-8000-000000000001", gig_id="gig_00000000-0000-4000-8000-000000000002", readers=readers)
    assert projection.opportunities[0]["title"] == "Forward Deployed Engineer"
    connection = query_projection(projection)
    assert connection.execute("select count(*) from opportunities").fetchone()[0] == 1
    assert connection.execute("select value from scout_cursor where key='journal_head'").fetchone()[0] == "b" * 40
    connection.close()


def test_report_escapes_markup_and_rejects_unsafe_source():
    projection = _projection(opportunities=({
        "opportunity_id": "opportunity_00000000000000000000000000000001",
        "snapshot_id": "snapshot_00000000000000000000000000000001",
        "title": "<img src=x onerror=alert(1)>", "employer": "Acme & Co",
    },))
    template = b"<html><body><main><!-- SCOUT:CONTENT --></main></body></html>"
    rendered, _css = render_html(projection=projection, template=template, css=b"body{}")
    assert b"&lt;img" in rendered
    assert b"<img" not in rendered
    with pytest.raises(ScoutReportError) as error:
        render_html(projection=projection, template=b"<html><body><script>alert(1)</script><main></main></body></html>", css=b"body{}")
    assert error.value.code == "report_source_unsafe"


def test_report_shows_proposal_content_and_source_link():
    projection = _projection(proposals=({
        "opportunity_id": "opportunity_00000000000000000000000000000001",
        "status": "complete",
        "revision_id": "revision_00000000-0000-4000-8000-000000000003",
        "path": "records/scout-proposals/record_00000000-0000-4000-8000-000000000004/revisions/revision_00000000-0000-4000-8000-000000000003.json",
        "source_path": "runs/run_00000000-0000-4000-8000-000000000005/supporting/posting.bin",
        "assessment": {
            "proposed_resume_focus": "Distributed systems",
            "fit_reasons": ["Python delivery"],
            "focused_experience_questions": ["Describe the launch."],
        },
    },))
    rendered, _css = render_html(
        projection=projection,
        template=b"<html><body><main><!-- SCOUT:CONTENT --></main></body></html>",
        css=b"body{}",
    )
    assert b"Proposal content" in rendered
    assert b"Distributed systems" in rendered
    assert b"Describe the launch." in rendered
    assert b"source unavailable" not in rendered


def test_report_publish_preserves_ui_and_old_selector_on_failed_regeneration(tmp_path):
    resolved = _resolved(tmp_path)
    projection = _projection()
    result = publish_report(resolved=resolved, projection=projection)
    selector_before = (resolved.path / "reports/scout/current.json").read_bytes()
    template_before = (resolved.path / "ui/template.html").read_bytes()
    assert Path(result["path"]).is_file()
    assert template_before == (resolved.path / "ui/template.html").read_bytes()
    (resolved.path / "ui/template.html").write_text("<html><body><script>no</script><main></main></body></html>", encoding="utf-8")
    with pytest.raises(ScoutReportError, match="executable"):
        publish_report(resolved=resolved, projection=projection)
    assert (resolved.path / "reports/scout/current.json").read_bytes() == selector_before


def test_report_current_selector_marks_stale_after_journal_head_changes(tmp_path):
    resolved = _resolved(tmp_path)
    subprocess.run(["git", "-C", str(resolved.path), "init", "--quiet", "--initial-branch=main"], check=True)
    (resolved.path / "README").write_text("fixture")
    subprocess.run(["git", "-C", str(resolved.path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(resolved.path), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture"], check=True)
    projection = _projection(journal_head=subprocess.check_output(["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True).strip())
    publish_report(resolved=resolved, projection=projection)
    status = read_current_report(resolved=resolved)
    assert status["stale"] is False
    (resolved.path / "README").write_text("changed")
    subprocess.run(["git", "-C", str(resolved.path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(resolved.path), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "changed"], check=True)
    assert read_current_report(resolved=resolved)["stale"] is True


def test_application_link_strict_mode_refuses_without_opportunity_reader():
    # The explicit strict gate is separate from legacy event history: without
    # a committed R1 opportunity reader, no model- or fixture-owned ID is
    # allowed to appear verified.
    snapshot = JournalSnapshot("c" * 40, {})
    projection = projection_from_snapshot(snapshot=snapshot, project_id="project_00000000-0000-4000-8000-000000000001", gig_id="gig_00000000-0000-4000-8000-000000000002")
    assert projection.applications == ()


def test_projection_preserves_duplicate_correction_branches_and_current_status(tmp_path):
    home, target, _project, gig, _workpad, _other = _bound_defaults(tmp_path)
    resolved = __import__("gigai.workpad", fromlist=["resolve_workpad"]).resolve_workpad(
        home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True
    )
    first = _application(resolved, "r3-correction-first")
    _application(resolved, "r3-correction-child", kind="applied", supersedes=first["event"]["event_id"])
    snapshot = run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(("records/",)),
    )
    projection = projection_from_snapshot(snapshot=snapshot, project_id=resolved.project_id, gig_id=resolved.gig_id)
    assert len(projection.applications) == 2
    current = [item for item in projection.applications if item["current"]]
    assert len(current) == 1
    assert current[0]["current_status"] == "applied"


def _resolved_bound(tmp_path: Path, *, activate: bool = False):
    home, target, _project, gig, _workpad, _other = _bound_defaults(tmp_path)
    if activate:
        select_active_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    return resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True), home, target


def _commit_run_details(resolved, run_id: str, *, goal_statuses: list[str], status: str = "running", handoff_id: str) -> None:
    path = f"runs/{run_id}/run-details.json"
    zero_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": None, "currency": None, "cost_status": "not_applicable"}
    goals = [
        {
            "goal_id": f"goal_00000000-0000-4000-8000-{index:012d}",
            "goal_version": 1,
            "executor": "scout.find_jobs.acquire",
            "status": goal_status,
            "outcome": "COMPLETE" if goal_status == "complete" else None,
            "errors": [],
            "evidence": [],
            "usage": zero_usage,
            "started_at": "2026-09-22T00:00:00Z",
            "finished_at": None,
        }
        for index, goal_status in enumerate(goal_statuses, start=1)
    ]
    details = {
        "schema_version": "1.0",
        "run_id": run_id,
        "gig_id": resolved.gig_id,
        "gig_version": 1,
        "goal_graph_sha256": "sha256:" + "a" * 64,
        "status": status,
        "started_at": "2026-09-22T00:00:00Z",
        "finished_at": None,
        "goal_sets": {"pending": [], "ready": [], "active": [], "complete": [], "failed": [], "blocked": [], "gated": [], "cancelled": []},
        "goals": goals,
        "critical_path": [goal["goal_id"] for goal in goals],
        "realized_max_parallel_goals": 1,
        "execution_summary": "fixture",
        "tool_errors": [],
        "model_errors": [],
        "aggregate_usage": zero_usage,
        "remaining_budget": {"max_model_calls": 0, "max_tool_calls": 0, "max_tokens": 0, "max_cost": None, "currency": None, "max_wall_time_ms": 0, "max_parallel_goals": 1},
        "target_before": {"path": "target.json", "content_sha256": "sha256:" + "b" * 64, "media_type": "application/json", "size_bytes": 2},
        "target_after": None,
        "completion_audit": {"status": "missing", "path": None},
        "terminal_handoff": None,
        "workpad_commit": None,
        "next_actions": [],
    }
    data = canonical_json_bytes(details)
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=handoff_id,
        transition="run_started",
        body="synthetic run-details fixture for aggregate-status precedence",
        artifacts=(JournalArtifact(path, data),),
        front_matter={"artifact_refs": [{"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}]},
    )


@pytest.mark.parametrize(
    "goal_statuses,expected",
    [
        (["complete", "failed"], "failed"),
        (["complete", "running"], "active"),
        (["complete", "complete"], "succeeded"),
    ],
)
def test_run_rows_uses_contract_aggregate_status_precedence(tmp_path, goal_statuses, expected):
    # The pre-fix reader set status to "succeeded" the moment any goal state
    # was "complete", checked before "failed"/"running"; the corrected reader
    # follows the contracts' interrupted > failed > blocked > cancelled >
    # running > pending > succeeded precedence instead.
    resolved, _home, _target = _resolved_bound(tmp_path)
    run_id = "run_00000000-0000-4000-8000-000000000301"
    _commit_run_details(resolved, run_id, goal_statuses=goal_statuses, handoff_id="handoff_00000000-0000-4000-8000-000000000301")
    snapshot = run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(("runs/",)),
    )
    rows = {row["run_id"]: row for row in _run_rows(snapshot, resolved.project_id, resolved.gig_id)}
    assert rows[run_id]["status"] == expected


def _committed_json(resolved, path: str, payload: dict, *, handoff_id: str) -> None:
    data = canonical_json_bytes(payload)
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=handoff_id,
        transition="goal_completed",
        body="synthetic find-jobs node evidence fixture",
        artifacts=(JournalArtifact(path, data),),
        front_matter={"artifact_refs": [{"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}]},
    )


def _run_input_payload(fixture_payload: dict) -> dict:
    # The frozen fixture already carries a config_digest that matches its own
    # config's canonical digest; reuse it as-is rather than recomputing one.
    run_input = _fixture("fixture-run-input-v1.json")
    assert run_input["pinned_resume"] == fixture_payload["pinned_resume"]
    return run_input


def test_build_present_payload_round_trips_fixtures_via_present_payload(tmp_path):
    resolved, home, target = _resolved_bound(tmp_path, activate=True)
    run_id = "run_123e4567-e89b-42d3-a456-426614174002"
    payload_fixture = _fixture("fixture-present-payload-v1.json")
    receipts_fixture = _fixture("fixture-node-receipts-v1.json")
    acquire_output = {"schema_version": "scout-find-jobs-acquire-output:1", "batch_id": "batch-001", "batch_ref": "records/scout-acquisition/batch-001/input.json", "progress_ref": "records/scout-acquisition/batch-001/progress/revision-001.json", "progress_status": "complete", "rows": payload_fixture["rows"], "failures": [], "url_set_diff": {"added": [], "removed": [], "unchanged": [], "edited": []}, "watchlist_refs": [], "selected_postings": []}
    assess_output = {"schema_version": "scout-find-jobs-assess-output:1", "selected_postings": [], "pinned_resume": payload_fixture["pinned_resume"], "target": "targets/acme", "selection_cap": 10, "selection_rule": "new_or_edited_role_match", "candidate_rows": payload_fixture["rows"], "assessments": payload_fixture["assessments"], "not_assessed": payload_fixture["not_assessed"], "proposal_revision_refs": [], "model_target": "ollama_local", "producer": receipts_fixture["receipts"][1]["producer"], "usage": None, "failures": payload_fixture["failures"]}

    _committed_json(resolved, f"runs/{run_id}/sealed/find-jobs-run-input.json", _run_input_payload(payload_fixture), handoff_id="handoff_00000000-0000-4000-8000-000000000401")
    _committed_json(resolved, f"runs/{run_id}/outputs/acquire.json", acquire_output, handoff_id="handoff_00000000-0000-4000-8000-000000000402")
    _committed_json(resolved, f"runs/{run_id}/outputs/assess.json", assess_output, handoff_id="handoff_00000000-0000-4000-8000-000000000403")
    for index, slug in enumerate(("acquire", "assess", "present"), start=1):
        _committed_json(resolved, f"runs/{run_id}/receipts/{slug}.json", receipts_fixture["receipts"][index - 1], handoff_id=f"handoff_00000000-0000-4000-8000-00000000041{index}")

    payload = build_present_payload(home_root=home, target=target, run_id=run_id)
    assert isinstance(payload, PresentPayload)
    assert payload.run_id == run_id
    assert payload.status.value == "interrupted"
    assert len(payload.node_receipts) == 3
    assert payload.pinned_resume is not None and payload.pinned_resume.record_id == "resume-record-001"
    assert {row.outcome.value for row in payload.rows} == {"edited", "new"}
    assert len(payload.assessments) == 1
    assert len(payload.not_assessed) == 1

    # PresentPayload's own from_json/to_json round trip proves the built
    # value is a valid contract payload, not just an ad hoc dict.
    round_tripped = PresentPayload.from_json(payload.to_json())
    assert round_tripped.to_json() == payload.to_json()


def test_present_node_builds_output_from_batch_and_assessment_refs_with_no_mutation(tmp_path):
    resolved, home, target = _resolved_bound(tmp_path, activate=True)
    run_id = "run_123e4567-e89b-42d3-a456-426614174099"
    payload_fixture = _fixture("fixture-present-payload-v1.json")
    receipts_fixture = _fixture("fixture-node-receipts-v1.json")
    batch_ref = "records/scout-acquisition/batch-777/input.json"
    assessment_ref = "records/scout-assessment/revision-777.json"
    acquire_output = {"schema_version": "scout-find-jobs-acquire-output:1", "batch_id": "batch-777", "batch_ref": batch_ref, "progress_ref": "records/scout-acquisition/batch-777/progress/revision-001.json", "progress_status": "complete", "rows": payload_fixture["rows"], "failures": payload_fixture["failures"], "url_set_diff": {"added": [], "removed": [], "unchanged": [], "edited": []}, "watchlist_refs": [], "selected_postings": []}
    assess_output = {"schema_version": "scout-find-jobs-assess-output:1", "selected_postings": [], "pinned_resume": payload_fixture["pinned_resume"], "target": "targets/acme", "selection_cap": 10, "selection_rule": "new_or_edited_role_match", "candidate_rows": payload_fixture["rows"], "assessments": payload_fixture["assessments"], "not_assessed": payload_fixture["not_assessed"], "proposal_revision_refs": [], "model_target": "ollama_local", "producer": receipts_fixture["receipts"][1]["producer"], "usage": None, "failures": []}

    _committed_json(resolved, f"runs/{run_id}/sealed/find-jobs-run-input.json", _run_input_payload(payload_fixture), handoff_id="handoff_00000000-0000-4000-8000-000000000501")
    _committed_json(resolved, batch_ref, acquire_output, handoff_id="handoff_00000000-0000-4000-8000-000000000502")
    _committed_json(resolved, assessment_ref, assess_output, handoff_id="handoff_00000000-0000-4000-8000-000000000503")

    receipts = tuple(NodeReceipt.from_json(item) for item in receipts_fixture["receipts"])
    context = NodeContext(
        run_id=run_id,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        graph_id="find-jobs:functional",
        graph_version=1,
        goal_slug="present",
        manifest_digest="sha256:" + "1" * 64,
        operation_key="find-jobs:present:001",
        target_observation_digest="sha256:" + "2" * 64,
        workpad_path=str(resolved.path),
        redeemed_consent_ref="records/consent/1.json",
        model_target=ModelTarget.OLLAMA_LOCAL,
    )
    present_input = PresentInput(batch_ref=batch_ref, assessment_ref=assessment_ref, node_receipts=receipts)

    before = run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(("records/", "runs/")),
    )
    output = present_node(context, present_input, home_root=home, target=target)
    after = run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(("records/", "runs/")),
    )

    assert isinstance(output, PresentOutput)
    assert output.aggregate_status.value == "interrupted"
    assert output.payload.run_id == run_id
    assert len(output.payload.rows) == len(payload_fixture["rows"])
    assert len(output.payload.assessments) == 1
    # No application/tracking mutation: the committed artifact set is
    # unchanged by calling the present node.
    assert after.artifacts == before.artifacts
    assert after.head == before.head
