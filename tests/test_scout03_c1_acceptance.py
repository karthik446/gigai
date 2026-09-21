"""Coordinator regressions for the completed C1 storage handoff."""

from pathlib import Path
import sqlite3
import subprocess
import uuid

import pytest

from gigai.lifecycle import create_offline
from gigai.index import JournalIndexError
from gigai.journal import JournalArtifact, reconcile_journal, record_transition
import gigai.private_records as records
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _new_gig(tmp_path: Path):
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(
        home_root=home, workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",), open_with_target=False,
    ))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(
        home_root=home, requested_target=target, name="c1-acceptance",
        open_editor=False,
    )
    options = dict(home_root=home, requested_target=target, gig_id=created.gig_id)
    return created, options


@pytest.fixture
def private_gig(tmp_path: Path):
    created, options = _new_gig(tmp_path)
    records.migrate_workpad_layout(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id,
    )
    source = tmp_path / "resume.md"
    source.write_bytes(b"Original private resume\n")
    imported = records.import_reference(
        **options, kind="resume", source=source, operation_key="source-1",
    )
    create_options = dict(
        **options, kind="imported_reference", content_family="g45_reference",
        content_id=imported.item_id, actor={"kind": "operator", "id": "user"},
        origin="imported", operation_key="record-1",
    )
    return created, options, create_options, imported


def _head(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def test_generated_record_identity_replays_original_receipt(private_gig):
    created, _options, arguments, _imported = private_gig
    first = records.create_record(**arguments)
    head = _head(created.workpad)
    replay = records.create_record(**arguments)
    assert replay.created is False
    assert (replay.record_id, replay.revision_id, replay.receipt) == (
        first.record_id, first.revision_id, first.receipt,
    )
    assert _head(created.workpad) == head


def test_content_read_rejects_modified_snapshot(private_gig):
    created, options, arguments, imported = private_gig
    first = records.create_record(**arguments)
    snapshot = created.workpad / "references" / imported.item_id / "source.txt"
    snapshot.write_bytes(b"UNCOMMITTED replacement\n")
    with pytest.raises(records.PrivateRecordError):
        records.read_record(
            **options, record_id=first.record_id,
            revision_id=first.revision_id, content=True,
        )


def test_missing_committed_revision_cannot_silently_revert_current(private_gig):
    created, options, arguments, _imported = private_gig
    first = records.create_record(**arguments)
    second = records.create_record(**{
        **arguments, "record_id": first.record_id,
        "parent_revision": first.revision_id, "operation_key": "record-2",
    })
    revision_path = created.workpad / "records" / first.record_id / "revisions" / f"{second.revision_id}.json"
    revision_path.unlink()
    with pytest.raises(records.PrivateRecordError):
        records.read_record(**options, record_id=first.record_id)


def test_hidden_current_revision_cannot_bypass_parent_cas(private_gig):
    created, options, arguments, _imported = private_gig
    first = records.create_record(**arguments)
    second = records.create_record(**{
        **arguments, "record_id": first.record_id,
        "parent_revision": first.revision_id, "operation_key": "record-cas-2",
    })
    hidden = created.workpad / "records" / first.record_id / "revisions" / f"{second.revision_id}.json"
    hidden.unlink()
    head = _head(created.workpad)
    with pytest.raises(records.PrivateRecordError, match="working evidence"):
        records.create_record(**{
            **arguments, "record_id": first.record_id,
            "parent_revision": first.revision_id, "operation_key": "record-cas-stale",
        })
    assert _head(created.workpad) == head


def test_extra_uncommitted_private_evidence_refuses_publication(private_gig):
    created, options, _arguments, _imported = private_gig
    extra = created.workpad / "records" / "operations" / "uncommitted.json"
    extra.write_bytes(b"{}")
    with pytest.raises(records.PrivateRecordError, match="extra"):
        records.import_run_input(**options, data=b"posting", operation_key="extra-evidence")


def test_post_commit_runtime_failure_returns_pending_result(private_gig, monkeypatch):
    _created, options, _arguments, _imported = private_gig

    def unavailable(**_kwargs):
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(records, "rebuild_scout_projection", unavailable)
    result = records.import_run_input(**options, data=b"Job posting", operation_key="pending")
    assert result.created and result.projection_pending
    assert result.receipt is not None
    assert "projection_pending" not in result.receipt
    assert "rebuild_action" not in result.receipt


def test_context_staging_never_follows_preexisting_symlink(private_gig, tmp_path):
    created, options, _arguments, _imported = private_gig
    outside = tmp_path / "unrelated.txt"
    outside.write_bytes(b"keep this unchanged")
    temporary = created.workpad / "indexes" / ".context.tmp"
    temporary.symlink_to(outside)
    try:
        records.rebuild_scout_projection(resolved=records._resolved(**options))
    except (records.PrivateRecordError, JournalIndexError, OSError):
        pass
    assert outside.read_bytes() == b"keep this unchanged"


def test_projection_rejects_trigger_without_destroying_trace(private_gig):
    created, options, arguments, _imported = private_gig
    records.create_record(**arguments)
    database = created.workpad / "state.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS interview_events ("
            "session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL, "
            "state TEXT NOT NULL, payload_sha256 TEXT NOT NULL, occurred_at TEXT NOT NULL, "
            "PRIMARY KEY(session_id, sequence))"
        )
        connection.execute(
            "INSERT INTO interview_events VALUES (?, ?, ?, ?, ?, ?)",
            ("probe", 1, "opened", "draft", "sha256:" + "a" * 64, "2026-09-08T00:00:00Z"),
        )
        connection.execute(
            "CREATE TRIGGER unexpected_trace_delete AFTER DELETE ON scout_records "
            "BEGIN DELETE FROM interview_events; END"
        )
    refused = False
    try:
        records.rebuild_scout_projection(resolved=records._resolved(**options))
    except JournalIndexError:
        refused = True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM interview_events").fetchone()[0] == 1
    assert refused, "a closed managed schema must reject unexpected executable SQL"


def test_legacy_mutable_artifact_recovery_remains_supported(private_gig):
    created, _options, _arguments, _imported = private_gig
    arguments = dict(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id,
        transition="creation_started", body="Mutable legacy artifact update",
    )
    record_transition(
        **arguments, handoff_id=f"handoff_{uuid.uuid4()}",
        artifacts=(JournalArtifact("manifests/c1-mutable.json", b'{"value":1}'),),
    )

    def interrupt(phase):
        if phase == "after_transaction_prepare":
            raise RuntimeError("interrupted legacy write")

    with pytest.raises(RuntimeError, match="interrupted legacy write"):
        record_transition(
            **arguments, handoff_id=f"handoff_{uuid.uuid4()}", observer=interrupt,
            artifacts=(JournalArtifact("manifests/c1-mutable.json", b'{"value":2}'),),
        )
    result = reconcile_journal(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id,
    )
    assert result.reconciled
    assert (created.workpad / "manifests/c1-mutable.json").read_bytes() == b'{"value":2}'


def test_interrupted_layout_migration_can_reconcile(tmp_path, monkeypatch):
    created, _options = _new_gig(tmp_path)

    def interrupted_transition(**kwargs):
        def interrupt(phase):
            if phase == "after_artifact_replace":
                raise RuntimeError("interrupted migration")
        return record_transition(**kwargs, observer=interrupt)

    monkeypatch.setattr(records, "record_transition", interrupted_transition)
    with pytest.raises(RuntimeError, match="interrupted migration"):
        records.migrate_workpad_layout(
            workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id,
        )
    result = reconcile_journal(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id,
    )
    assert result.reconciled
    assert records.workpad_layout_version(
        created.workpad, project_id=created.project_id, gig_id=created.gig_id,
    ) == 2


def test_v1_tools_and_reports_do_not_block_layout_migration(tmp_path):
    created, _options = _new_gig(tmp_path)
    (created.workpad / "tools").mkdir()
    (created.workpad / "reports").mkdir()
    records.migrate_workpad_layout(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id,
    )
    assert records.workpad_layout_version(
        created.workpad, project_id=created.project_id, gig_id=created.gig_id,
    ) == 2
