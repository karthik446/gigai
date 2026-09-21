from __future__ import annotations

from pathlib import Path
import json

import pytest
from click.testing import CliRunner

from gigai.lifecycle import create_offline
from gigai.cli import cli
from gigai.native_records import (
    archive_native_record,
    create_native_record,
    create_task_override,
    list_native_records,
    native_context,
    read_native_record,
    update_native_record,
)
from gigai.native_records_cli import native_record_group
from gigai.private_records import PrivateRecordError, import_reference, import_run_input, migrate_workpad_layout
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _options(tmp_path: Path):
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="native-lane", open_editor=False)
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
    return created, {"home_root": home, "requested_target": target, "gig_id": created.gig_id}


def _provenance(source_refs: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"kind": "user_reported", "source_refs": source_refs or []}


def _fact(value: object, *, state: str = "known", context: str | None = None, source_refs: list[dict[str, object]] | None = None, conflict_refs: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"state": state, "value": value if state == "known" else None, "context": context, "provenance": _provenance(source_refs), "conflict_refs": conflict_refs or []}


def _profile(scope: dict[str, object] | None = None, employer_ref: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0", "kind": "profile_preferences",
        "scope": scope or {"mode": "saved_default", "task_context_id": None, "base": None},
        "payload": {
            "hard_constraints": {"geography": _fact("Denver"), "work_mode": _fact("remote"), "seniority": _fact("senior"), "employment_type": _fact("full_time"), "compensation": _fact("120000 USD annual")},
            "soft_priorities": {"industry": _fact("climate")},
            "sponsorship_need": _fact("needs_sponsorship"),
            "employer_sponsorship": _fact("offers_sponsorship", context="Acme posting", source_refs=[employer_ref]) if employer_ref else _fact(None, state="unknown"),
            "eligibility": _fact("eligible", context="US work authorization"),
        },
    }


def _experience(scope: dict[str, object] | None = None, *, answered: bool = False) -> dict[str, object]:
    return {
        "schema_version": "1.0", "kind": "experience_qa",
        "scope": scope or {"mode": "saved_default", "task_context_id": None, "base": None},
        "payload": {"questions": [{"question_id": "leadership-01", "prompt": "Describe leadership experience.", "state": "answered" if answered else "missing", "answer": "Led a small team." if answered else None, "provenance": _provenance() if answered else None}]},
    }


def test_generated_identity_replay_and_exact_content_are_journaled(tmp_path: Path):
    created, options = _options(tmp_path)
    result = create_native_record(**options, content=_profile(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="native-profile")
    replay = create_native_record(**options, content=_profile(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="native-profile")
    assert result.created and not replay.created
    assert (replay.record_id, replay.revision_id) == (result.record_id, result.revision_id)
    metadata = read_native_record(**options, record_id=result.record_id)
    assert "content" not in metadata and metadata["scope"]["mode"] == "saved_default"
    content = read_native_record(**options, record_id=result.record_id, content=True)["content"]
    assert content == (created.workpad / "records" / result.record_id / "blobs" / f"{result.revision_id}.json").read_bytes()
    assert b"needs_sponsorship" not in json.dumps(list_native_records(**options)).encode()


def test_history_parent_cas_archive_and_payload_free_context(tmp_path: Path):
    _created, options = _options(tmp_path)
    first = create_native_record(**options, content=_experience(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="qa-create")
    second = update_native_record(**options, record_id=first.record_id, parent_revision=first.revision_id, content=_experience(answered=True), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="qa-answer")
    with pytest.raises(PrivateRecordError, match="current parent"):
        update_native_record(**options, record_id=first.record_id, parent_revision=first.revision_id, content=_experience(answered=True), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="qa-stale")
    first_bytes = read_native_record(**options, record_id=first.record_id, revision_id=first.revision_id, content=True)["content"]
    second_bytes = read_native_record(**options, record_id=first.record_id, revision_id=second.revision_id, content=True)["content"]
    assert b'"answer":null' in first_bytes
    context = native_context(**options)
    assert context["outstanding_questions"] == []
    assert "Describe leadership" not in json.dumps(context)
    archived = archive_native_record(**options, record_id=first.record_id, parent_revision=second.revision_id, actor={"kind": "operator", "id": "user"}, operation_key="qa-archive")
    assert archived.state == "archived"
    assert read_native_record(**options, record_id=first.record_id, revision_id=first.revision_id, content=True)["content"] == first_bytes
    assert read_native_record(**options, record_id=first.record_id, revision_id=archived.revision_id, content=True)["content"] == second_bytes
    assert list_native_records(**options) == []
    assert list_native_records(**options, include_archived=True)[0]["state"] == "archived"


def test_task_override_is_separate_record_and_generated_context_replays(tmp_path: Path):
    _created, options = _options(tmp_path)
    base = create_native_record(**options, content=_profile(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="base")
    override = create_task_override(**options, content=_profile(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="override", base={"record_id": base.record_id, "revision_id": base.revision_id})
    replay = create_task_override(**options, content=_profile(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="override", base={"record_id": base.record_id, "revision_id": base.revision_id})
    assert override.record_id != base.record_id
    assert override.task_context_id and replay.task_context_id == override.task_context_id
    assert replay.record_id == override.record_id


def test_strict_uncertain_and_source_conversation_semantics(tmp_path: Path):
    _created, options = _options(tmp_path)
    invalid = _profile()
    invalid["payload"]["eligibility"]["state"] = "unknown"  # type: ignore[index]
    invalid["payload"]["eligibility"]["value"] = "eligible"  # type: ignore[index]
    with pytest.raises(PrivateRecordError, match="uncertain"):
        create_native_record(**options, content=invalid, actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="invalid")
    conversation = {"schema_version": "1.0", "kind": "selected_conversation", "scope": {"mode": "saved_default", "task_context_id": None, "base": None}, "payload": {"mode": "summary", "text": "User supplied a short summary.", "source": {"source_kind": "supplied_text", "label": "operator paste", "selection_note": None, "message_ids": [], "session_reference": None}}}
    created = create_native_record(**options, content=conversation, actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="conversation")
    assert b"operator paste" in read_native_record(**options, record_id=created.record_id, content=True)["content"]


def test_supplied_source_requires_a_real_committed_reference(tmp_path: Path):
    _created, options = _options(tmp_path)
    source_file = tmp_path / "resume.md"
    source_file.write_text("Private resume bytes\n", encoding="utf-8")
    imported = import_reference(**options, kind="resume", source=source_file, operation_key="resume")
    snapshot_ref = imported.record["snapshot"]
    assert isinstance(snapshot_ref, dict)
    source_ref = dict(snapshot_ref)
    original_digest = source_ref["content_sha256"]
    supplied = {"schema_version": "1.0", "kind": "supplied_source", "scope": {"mode": "saved_default", "task_context_id": None, "base": None}, "payload": {"sources": [{"source_kind": "resume", "label": "operator supplied resume", "status": "captured", "artifact_refs": [source_ref], "verification_ref": None, "provenance": {"kind": "imported", "source_refs": [source_ref]}}]}}
    created = create_native_record(**options, content=supplied, actor={"kind": "operator", "id": "user"}, origin="imported", operation_key="source")
    assert read_native_record(**options, record_id=created.record_id)["kind"] == "supplied_source"
    supplied["payload"]["sources"][0]["artifact_refs"][0]["content_sha256"] = "sha256:" + "0" * 64  # type: ignore[index]
    with pytest.raises(PrivateRecordError, match="exact evidence"):
        create_native_record(**options, content=supplied, actor={"kind": "operator", "id": "user"}, origin="imported", operation_key="bad-source")
    supplied["payload"]["sources"][0]["artifact_refs"][0]["content_sha256"] = original_digest  # type: ignore[index]
    supplied["payload"]["sources"][0]["artifact_refs"][0]["media_type"] = "application/json"  # type: ignore[index]
    with pytest.raises(PrivateRecordError, match="media"):
        create_native_record(**options, content=supplied, actor={"kind": "operator", "id": "user"}, origin="imported", operation_key="bad-media")


def test_known_employer_needs_committed_evidence_but_eligibility_can_be_user_reported(tmp_path: Path):
    _created, options = _options(tmp_path)
    with pytest.raises(PrivateRecordError, match="employer sponsorship"):
        create_native_record(**options, content={**_profile(), "payload": {**_profile()["payload"], "employer_sponsorship": _fact("offers_sponsorship", context="Acme posting")}}, actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="no-employer-evidence")
    imported = import_run_input(**options, data=b"Acme posting: visa sponsorship is available.\n", label="Acme posting", operation_key="employer-evidence")
    source_ref = imported.record["snapshot"]
    assert isinstance(source_ref, dict)
    result = create_native_record(**options, content=_profile(employer_ref=source_ref), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="employer-evidence-profile")
    assert result.created


def test_conflicting_facts_require_committed_conflict_evidence(tmp_path: Path):
    _created, options = _options(tmp_path)
    profile = _profile()
    profile["payload"]["sponsorship_need"] = _fact(None, state="conflicting")  # type: ignore[index]
    with pytest.raises(PrivateRecordError, match="conflict evidence"):
        create_native_record(**options, content=profile, actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="empty-conflict")
    source_file = tmp_path / "conflict.md"
    source_file.write_text("Conflicting supplied statement.\n", encoding="utf-8")
    imported = import_reference(**options, kind="resume", source=source_file, operation_key="conflict-evidence")
    source_ref = imported.record["snapshot"]
    assert isinstance(source_ref, dict)
    profile["payload"]["sponsorship_need"] = _fact(None, state="conflicting", conflict_refs=[source_ref])  # type: ignore[index]
    assert create_native_record(**options, content=profile, actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="bound-conflict").created


def test_fresh_process_style_cli_group_story(tmp_path: Path):
    _created, options = _options(tmp_path)
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(_profile()), encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(native_record_group, ["create", "--content-file", str(source), "--operation-key", "cli-create", "--home", str(options["home_root"]), "--target", str(options["requested_target"]), "--gig", options["gig_id"], "--json"])
    assert created.exit_code == 0, created.output
    record_id = json.loads(created.output)["record_id"]
    listed = runner.invoke(native_record_group, ["list", "--home", str(options["home_root"]), "--target", str(options["requested_target"]), "--gig", options["gig_id"], "--json"])
    assert listed.exit_code == 0 and b"needs_sponsorship" not in listed.output.encode()
    explicit = runner.invoke(native_record_group, ["read", "--id", record_id, "--content", "--home", str(options["home_root"]), "--target", str(options["requested_target"]), "--gig", options["gig_id"]])
    assert explicit.exit_code == 0 and b"needs_sponsorship" in explicit.stdout_bytes


def test_mounted_native_override_cli_normalizes_generated_and_explicit_scope(tmp_path: Path):
    _created, options = _options(tmp_path)
    base = create_native_record(**options, content=_profile(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="mounted-base")
    source = tmp_path / "override.json"
    source.write_text(json.dumps(_profile()), encoding="utf-8")
    prefix = ["record", "native", "override", "--content-file", str(source), "--base-record", base.record_id, "--base-revision", base.revision_id, "--home", str(options["home_root"]), "--target", str(options["requested_target"]), "--gig", options["gig_id"], "--json"]
    runner = CliRunner()
    generated = runner.invoke(cli, [*prefix, "--operation-key", "mounted-generated"])
    assert generated.exit_code == 0, generated.output
    generated_value = json.loads(generated.output)
    replay = runner.invoke(cli, [*prefix, "--operation-key", "mounted-generated"])
    assert replay.exit_code == 0 and json.loads(replay.output)["record_id"] == generated_value["record_id"]
    context = "task_context_12345678-1234-4234-8234-123456789abc"
    explicit = runner.invoke(cli, [*prefix, "--task-context", context, "--operation-key", "mounted-explicit"])
    assert explicit.exit_code == 0, explicit.output
    assert json.loads(explicit.output)["task_context_id"] == context
    explicit_replay = runner.invoke(cli, [*prefix, "--task-context", context, "--operation-key", "mounted-explicit"])
    assert explicit_replay.exit_code == 0 and json.loads(explicit_replay.output)["record_id"] == json.loads(explicit.output)["record_id"]
    invalid = runner.invoke(cli, [*prefix, "--task-context", "task_context_not-a-uuid", "--operation-key", "bad-context"])
    assert invalid.exit_code == 1 and "native_record_invalid" in invalid.output and "Traceback" not in invalid.output
    assert read_native_record(**options, record_id=base.record_id)["scope"] == {"mode": "saved_default", "task_context_id": None, "base": None}
    with pytest.raises(PrivateRecordError, match="override base is required"):
        create_task_override(**options, content=_profile(), actor={"kind": "operator", "id": "user"}, origin="user_reported", operation_key="missing-base", scope=None)
