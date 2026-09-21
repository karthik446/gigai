from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from gigai.journal import JournalSnapshot
from gigai.journal import run_with_journal_writer
from gigai.scout_projection import ScoutProjection, ScoutReaderSet, projection_from_snapshot, query_projection
from gigai.scout_report import ScoutReportError, publish_report, read_current_report, render_html
from gigai.workpad import ResolvedWorkpad
from tests.test_scout09_application_events import _application
from tests.test_scout05_first_proposal import _bound_defaults


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
