from __future__ import annotations

from gigai.application_events import _digest, _scope
from gigai.canonical import canonical_json_bytes
from gigai.validators import validate_serialized_contract
from click.testing import CliRunner
from gigai.cli import cli
from tests.behaviors.scout_proposals_tools.test_scout05_first_proposal import _bound_defaults
from gigai.canonical import digest_imported_bytes
from gigai.journal import JournalArtifact, record_transition
from gigai.private_records import create_record, import_reference
from gigai.workpad import resolve_workpad
from gigai.application_events import ApplicationEventError, read_application, record_application
import pytest
import json


def _committed_event(*, project: str, gig: str, operation: str, evidence: dict | None = None) -> dict:
    event = {
        "schema_version": "1.0",
        "event_id": "event_00000000-0000-4000-8000-000000000201",
        "project_id": project,
        "gig_id": gig,
        "opportunity_ref": "opportunity_00000000000000000000000000000001",
        "event_kind": "saved",
        "occurred_at": "2026-09-10T12:00:00-06:00",
        "timezone": "America/Denver",
        "recorded_at": "2026-09-10T18:00:00Z",
        "document_refs": [],
        "notes": None,
        "supersedes": None,
        "request_evidence": evidence
        or {
            "kind": "direct_event_command",
            "scope_digest": "",
            "actor": {"kind": "operator", "id": "local-user"},
            "recorded_at": "2026-09-10T18:00:00Z",
            "command": "gigai application record",
        },
        "requested_event_sha256": "",
        "operation_key": operation,
        "payload_sha256": "",
        "actor": {"kind": "operator", "id": "local-user"},
    }
    event["requested_event_sha256"] = _digest(_scope(event))
    if evidence is None:
        event["request_evidence"]["scope_digest"] = event["requested_event_sha256"]
    elif event["request_evidence"]["scope_digest"] == "":
        event["request_evidence"]["scope_digest"] = event["requested_event_sha256"]
    semantic = dict(event)
    for key in ("event_id", "operation_key", "payload_sha256", "recorded_at"):
        semantic.pop(key, None)
    event["payload_sha256"] = _digest(semantic)
    return event


def _publish_application_artifacts(
    workpad, project, gig, event, *, include_event=True, receipt_operation_key=None
):
    event_path = f"records/applications/events/{event['event_id']}.json"
    receipt_path = (
        "records/operations/application-record-"
        f"{digest_imported_bytes((receipt_operation_key or event['operation_key']).encode()).removeprefix('sha256:')}.json"
    )
    receipt = {
        "schema_version": "1.0",
        "operation_key": event["operation_key"],
        "requested_event_sha256": event["requested_event_sha256"],
        "payload_sha256": event["payload_sha256"],
        "event": event,
    }
    event_bytes = canonical_json_bytes(event)
    receipt_bytes = canonical_json_bytes(receipt)
    artifacts = ([JournalArtifact(event_path, event_bytes)] if include_event else []) + [
        JournalArtifact(receipt_path, receipt_bytes)
    ]
    record_transition(
        workpad=workpad,
        project_id=project,
        gig_id=gig,
        handoff_id="handoff_00000000-0000-4000-8000-000000000202",
        transition="private_record_revised",
        body="synthetic first-publication application fixture",
        artifacts=tuple(artifacts),
        front_matter={
            "artifact_refs": [
                {
                    "path": artifact.path,
                    "content_sha256": digest_imported_bytes(artifact.content),
                    "media_type": "application/json",
                    "size_bytes": len(artifact.content),
                }
                for artifact in artifacts
            ]
        },
    )
    return event_path, receipt_path


def test_requested_digest_excludes_generated_identity_and_recording_time() -> None:
    value = {
        "project_id": "project_00000000-0000-4000-8000-000000000001",
        "gig_id": "gig_00000000-0000-4000-8000-000000000002",
        "opportunity_ref": "opportunity_00000000000000000000000000000001",
        "event_kind": "applied",
        "occurred_at": "2026-09-10T12:00:00-06:00",
        "timezone": "America/Denver",
        "document_refs": [],
        "notes": None,
        "supersedes": None,
        "event_id": "event_00000000-0000-4000-8000-000000000003",
        "recorded_at": "2026-09-10T18:00:00Z",
    }
    assert _digest(_scope(value)) == _digest({k: value[k] for k in _scope(value)})


def test_application_event_schema_is_strict() -> None:
    assert not validate_serialized_contract(
        "application-event.schema.json", canonical_json_bytes({"extra": True})
    ).valid


def test_confirmed_cli_publishes_and_replays_from_disposable_journal(tmp_path) -> None:
    home, target, _project, gig, _workpad, _other = _bound_defaults(tmp_path)
    request = tmp_path / "application.json"
    request.write_text(
        json.dumps(
            {
                "operation_key": "scout09-cli-positive",
                "opportunity_ref": "opportunity_00000000000000000000000000000001",
                "event_kind": "applied",
                "occurred_at": "2026-09-10T12:00:00-06:00",
                "timezone": "America/Denver",
                "document_refs": [],
                "notes": "sent",
            }
        )
    )
    runner = CliRunner()
    first = runner.invoke(
        cli,
        [
            "application",
            "record",
            "--gig",
            gig,
            "--home",
            str(home),
            "--target",
            str(target),
            "--input",
            str(request),
            "--confirm",
            "--json",
        ],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        cli,
        [
            "application",
            "record",
            "--gig",
            gig,
            "--home",
            str(home),
            "--target",
            str(target),
            "--input",
            str(request),
            "--confirm",
            "--json",
        ],
    )
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["status"] == "already_recorded"
    history = runner.invoke(
        cli,
        [
            "application",
            "status",
            "--gig",
            gig,
            "--home",
            str(home),
            "--target",
            str(target),
            "--opportunity",
            "opportunity_00000000000000000000000000000001",
            "--json",
        ],
    )
    assert history.exit_code == 0, history.output
    assert json.loads(history.output)["current_status"] == "applied"


def test_no_confirm_writes_nothing_and_statuses_are_per_opportunity(tmp_path) -> None:
    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    request = tmp_path / "application.json"
    base = {
        "operation_key": "scout09-no-confirm",
        "event_kind": "saved",
        "occurred_at": "2026-09-10T12:00:00-06:00",
        "timezone": "America/Denver",
        "document_refs": [],
        "notes": None,
    }
    request.write_text(
        json.dumps(
            {**base, "opportunity_ref": "opportunity_00000000000000000000000000000001"}
        )
    )
    runner = CliRunner()
    refused = runner.invoke(
        cli,
        [
            "application",
            "record",
            "--gig",
            gig,
            "--home",
            str(home),
            "--target",
            str(target),
            "--input",
            str(request),
            "--json",
        ],
    )
    assert refused.exit_code != 0
    assert not list((workpad / "records" / "applications").rglob("*.json"))
    for index, opportunity in enumerate(
        (
            "opportunity_00000000000000000000000000000001",
            "opportunity_00000000000000000000000000000002",
        )
    ):
        request.write_text(
            json.dumps(
                {
                    **base,
                    "operation_key": f"scout09-status-{index}",
                    "opportunity_ref": opportunity,
                }
            )
        )
        result = runner.invoke(
            cli,
            [
                "application",
                "record",
                "--gig",
                gig,
                "--home",
                str(home),
                "--target",
                str(target),
                "--input",
                str(request),
                "--confirm",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
    status = runner.invoke(
        cli,
        [
            "application",
            "status",
            "--gig",
            gig,
            "--home",
            str(home),
            "--target",
            str(target),
            "--json",
        ],
    )
    assert json.loads(status.output)["statuses"] == {
        "opportunity_00000000000000000000000000000001": "saved",
        "opportunity_00000000000000000000000000000002": "saved",
    }


def _application(resolved, key, *, opportunity="opportunity_00000000000000000000000000000001", kind="saved", occurred="2026-09-10T12:00:00-06:00", notes=None, documents=None, supersedes=None, event_id=None):
    data = {"operation_key": key, "opportunity_ref": opportunity, "event_kind": kind, "occurred_at": occurred, "timezone": "America/Denver", "document_refs": documents or [], "notes": notes, "supersedes": supersedes}
    if event_id is not None:
        data["event_id"] = event_id
    return record_application(resolved=resolved, data=data, confirm=True)


def test_historical_document_bytes_are_pinned_after_working_copy_edit(tmp_path) -> None:
    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    source = target / "resume.md"
    source.write_text("historical")
    reference = import_reference(home_root=home, requested_target=target, gig_id=gig, kind="resume", source=source, operation_key="doc-ref")
    revision = create_record(home_root=home, requested_target=target, gig_id=gig, kind="imported_reference", content_family="g45_reference", content_id=reference.item_id, actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key="doc-record")
    snapshot = revision.revision["content"]["snapshot_ref"]
    source.write_text("edited working copy")
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    result = _application(resolved, "doc-pinned", documents=[{"record_id": revision.record_id, "revision_id": revision.revision_id, "content_sha256": snapshot["content_sha256"]}])
    assert result["status"] == "recorded"


def test_missing_committed_document_bytes_refuse_without_publication(tmp_path) -> None:
    home, target, project, gig, workpad, _other = _bound_defaults(tmp_path)
    record_id = "record_00000000-0000-4000-8000-000000000101"
    revision_id = "revision_00000000-0000-4000-8000-000000000102"
    missing_path = "references/ref_00000000-0000-4000-8000-000000000103/missing.txt"
    revision = {"schema_version":"1.0", "record_id":record_id, "revision_id":revision_id, "parent_revision":None, "project_id":project, "gig_id":gig, "kind":"imported_reference", "privacy_class":"private_sensitive", "origin":"imported", "actor":{"kind":"operator","id":"local-user"}, "content":{"family":"g45_reference", "reference_id":"ref_00000000-0000-4000-8000-000000000103", "record_ref":{"path":"references/ref_00000000-0000-4000-8000-000000000103/reference.json", "content_sha256":"sha256:"+"1"*64, "media_type":"application/json", "size_bytes":1}, "snapshot_ref":{"path":missing_path, "content_sha256":"sha256:"+"2"*64, "media_type":"text/plain", "size_bytes":2}}, "relationships":[], "created_at":"2026-09-10T18:00:00Z", "state":"active"}
    raw = canonical_json_bytes(revision)
    record_transition(workpad=workpad, project_id=project, gig_id=gig, handoff_id="handoff_00000000-0000-4000-8000-000000000104", transition="private_record_revised", body="synthetic malformed committed snapshot", artifacts=(JournalArtifact(f"records/{record_id}/revisions/{revision_id}.json", raw),), front_matter={"artifact_refs":[{"path":f"records/{record_id}/revisions/{revision_id}.json", "content_sha256":digest_imported_bytes(raw), "media_type":"application/json", "size_bytes":len(raw)}]})
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    before = len(__import__("subprocess").check_output(["git", "-C", str(workpad), "rev-list", "--all"], text=True).splitlines())
    with pytest.raises(ApplicationEventError, match="committed authority") as error:
        _application(resolved, "doc-missing", documents=[{"record_id":record_id, "revision_id":revision_id, "content_sha256":"sha256:"+"2"*64}])
    assert error.value.code == "application_document_ref_missing"
    after = len(__import__("subprocess").check_output(["git", "-C", str(workpad), "rev-list", "--all"], text=True).splitlines())
    assert after == before


def test_corrections_allow_branches_and_reject_self_or_foreign_targets(tmp_path) -> None:
    home, target, _project, gig, _workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    first = _application(resolved, "branch-a")
    child = _application(resolved, "branch-b", kind="applied", supersedes=first["event"]["event_id"])
    assert child["status"] == "recorded"
    branch = _application(resolved, "branch-c", kind="rejected", supersedes=first["event"]["event_id"])
    assert branch["status"] == "recorded"
    with pytest.raises(ApplicationEventError) as duplicate:
        _application(resolved, "self-cycle", supersedes=first["event"]["event_id"], event_id=first["event"]["event_id"])
    assert duplicate.value.code == "application_event_identity_conflict"
    with pytest.raises(ApplicationEventError) as foreign:
        _application(resolved, "foreign-target", supersedes=child["event"]["event_id"], opportunity="opportunity_00000000000000000000000000000002")
    assert foreign.value.code == "application_correction_invalid"


def test_direct_evidence_and_receipt_identity_are_validated(tmp_path) -> None:
    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    first = _application(resolved, "evidence-good")
    event_path = workpad / "records" / "applications" / "events" / f"{first['event']['event_id']}.json"
    receipt_path = next((workpad / "records" / "operations").glob("application-record-*.json"))
    event = json.loads(event_path.read_text())
    event["request_evidence"]["scope_digest"] = "sha256:" + "f" * 64
    event_path.write_bytes(canonical_json_bytes(event))
    with pytest.raises(ApplicationEventError) as forged:
        _application(resolved, "evidence-next")
    assert forged.value.code in {"application_journal_conflict", "application_event_invalid"}
    event_path.write_bytes(canonical_json_bytes(first["event"]))
    receipt = json.loads(receipt_path.read_text())
    receipt["operation_key"] = "forged"
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(ApplicationEventError) as receipt_error:
        _application(resolved, "evidence-good")
    assert receipt_error.value.code == "application_journal_conflict"


@pytest.mark.parametrize(
    "evidence",
    [
        {
            "kind": "direct_event_command",
            "scope_digest": "sha256:" + "f" * 64,
            "actor": {"kind": "operator", "id": "local-user"},
            "recorded_at": "2026-09-10T18:00:00Z",
            "command": "gigai application record",
        },
        {
            "kind": "direct_event_command",
            "scope_digest": "",
            "actor": {"kind": "operator", "id": "different-operator"},
            "recorded_at": "2026-09-10T18:00:00Z",
            "command": "gigai application record",
        },
    ],
    ids=["wrong-scope", "wrong-actor-consistency"],
)
def test_committed_hash_valid_event_with_inconsistent_direct_evidence_refuses_semantically(
    tmp_path, evidence
) -> None:
    home, target, project, gig, workpad, _other = _bound_defaults(tmp_path)
    event = _committed_event(
        project=project, gig=gig, operation="semantic-evidence", evidence=evidence
    )
    event_path, receipt_path = _publish_application_artifacts(
        workpad, project, gig, event
    )
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    head_before = __import__("subprocess").check_output(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(ApplicationEventError) as history_error:
        read_application(resolved=resolved)
    assert history_error.value.code == "application_request_mismatch"
    with pytest.raises(ApplicationEventError) as publish_error:
        _application(resolved, "semantic-evidence-next")
    assert publish_error.value.code == "application_event_invalid"
    assert isinstance(publish_error.value.__cause__, ApplicationEventError)
    assert publish_error.value.__cause__.code == "application_request_mismatch"
    head_after = __import__("subprocess").check_output(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"], text=True
    ).strip()
    assert head_after == head_before
    assert (workpad / event_path).is_file()
    assert (workpad / receipt_path).is_file()


@pytest.mark.parametrize("mode", ["operation-identity", "missing-event"])
def test_same_key_retry_refuses_inconsistent_committed_receipt_semantically(tmp_path, mode) -> None:
    home, target, project, gig, workpad, _other = _bound_defaults(tmp_path)
    requested_key = "receipt-semantic-key"
    event_operation = "receipt-other-identity" if mode == "operation-identity" else requested_key
    event = _committed_event(project=project, gig=gig, operation=event_operation)
    event_path, receipt_path = _publish_application_artifacts(
        workpad,
        project,
        gig,
        event,
        include_event=mode != "missing-event",
        receipt_operation_key=requested_key,
    )
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)
    head_before = __import__("subprocess").check_output(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(ApplicationEventError) as error:
        _application(resolved, requested_key)
    if mode == "operation-identity":
        assert error.value.code == "application_operation_conflict"
    else:
        assert error.value.code == "application_receipt_invalid"
    head_after = __import__("subprocess").check_output(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"], text=True
    ).strip()
    assert head_after == head_before
    assert (workpad / receipt_path).is_file()
    assert (workpad / event_path).exists() is (mode != "missing-event")
