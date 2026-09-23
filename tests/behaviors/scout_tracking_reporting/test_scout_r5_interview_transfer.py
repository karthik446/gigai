from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
import uuid
import zipfile

import pytest
from click.testing import CliRunner

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.lifecycle import approve_offline, create_offline, propose_graph_set_offline
from gigai.native_records import create_native_record, read_native_record
from gigai.private_records import migrate_workpad_layout
from gigai.private_transfer import (
    PrivateTransferError,
    backup_private,
    export_definition,
    import_definition,
    restore_private,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.scout.interview_records import (
    ScoutInterviewError,
    prepare_interview,
    read_interview_preparation,
    revise_interview,
    save_interview_feedback,
)
from gigai.scout.posting_inputs import resolve_discovery_posting_input_from_journal
from gigai.cli import cli
from gigai.run import read_run_details
from tests.behaviors.scout_proposals_tools.test_scout02_graph_set_flow import _write_definition
from tests.behaviors.scout_research.test_scout06_research_inputs import _complete as complete_research
from tests.behaviors.scout_discovery.test_scout07_posting_inputs import _completed_find_jobs


def _env(tmp_path: Path):
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="r5", open_editor=False)
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
    native = create_native_record(
        home_root=home, requested_target=target, gig_id=created.gig_id,
        content={"schema_version": "1.0", "kind": "experience_qa", "scope": {"mode": "saved_default", "task_context_id": None, "base": None}, "payload": {"questions": [{"question_id": "delivery", "prompt": "Describe a delivery tradeoff.", "state": "missing", "answer": None, "provenance": None}]}},
        actor={"kind": "operator", "id": "local-user"}, origin="user_reported", operation_key="r5-experience",
    )
    raw = read_native_record(home_root=home, requested_target=target, gig_id=created.gig_id, record_id=native.record_id, revision_id=native.revision_id, content=True)["content"]
    assert isinstance(raw, bytes)
    sidecar = (created.workpad / "records" / native.record_id / "revisions" / f"{native.revision_id}.json").read_bytes()
    source_ref = {"record_id": native.record_id, "revision_id": native.revision_id, "family": "scout_record", "kind": "experience", "scope": {"mode": "saved_default", "task_context_id": None}, "artifact_ref": {"path": f"records/{native.record_id}/revisions/{native.revision_id}.json", "content_sha256": digest_imported_bytes(sidecar), "media_type": "application/json", "size_bytes": len(sidecar)}}
    return home, target, created, source_ref


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _selection(mode: str = "role_only", source_ref: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "mode": mode,
        "role": {"title": "Forward Deployed Engineer"},
        "posting": {"record_id": _id("record"), "revision_id": _id("revision"), "family": "posting", "snapshot_id": "snapshot_001"} if mode == "opportunity" else None,
        "stage_or_format": {"stage": "onsite", "format": "panel"},
        "research_revisions": [], "candidate_evidence": [source_ref] if source_ref is not None else [], "prior_feedback": [], "prior_preparation": [],
        "reuse_prior_research": False,
    }


def _content(source_ref: dict[str, object]) -> dict[str, object]:
    return {
        "role_expectations": ["Clarify deployment constraints."],
        "technical_topics": ["Bounded evaluation."],
        "evidence_backed_stories": [{"text": "A sourced story.", "source_refs": [source_ref]}],
        "hypothetical_examples": [{"label": "hypothetical", "prompt": "Design a rollout."}],
        "practice_questions": ["What would you measure?"],
        "interviewer_questions": ["How is success reviewed?"],
        "unresolved_gaps": ["Need a concrete example from the selected evidence."],
    }


def _empty_content() -> dict[str, object]:
    return {
        "role_expectations": ["Clarify deployment constraints."],
        "technical_topics": ["Bounded evaluation."],
        "evidence_backed_stories": [],
        "hypothetical_examples": [{"label": "hypothetical", "prompt": "Design a rollout."}],
        "practice_questions": ["What would you measure?"],
        "interviewer_questions": ["How is success reviewed?"],
        "unresolved_gaps": [],
    }


def _approve_prepare_interview_graph(tmp_path: Path, home: Path, target: Path, created) -> None:
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id)
    source_root = tmp_path / "graph-definition"
    definition = _write_definition(source_root, created.workpad, created.gig_id)
    payload = json.loads(definition.read_text(encoding="utf-8"))
    descriptor = next(item for item in payload["graphs"] if item["graph_id"] == "career")
    descriptor["graph_id"] = "prepare-interview"
    descriptor["aliases"] = []
    graph_path = source_root / descriptor["goal_graph"]["path"]
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    first_goal = graph["goals"][0]
    graph["goals"] = [first_goal]
    graph["edges"] = []
    graph["entry_goal_ids"] = [first_goal["goal_id"]]
    graph["terminal_goal_ids"] = [first_goal["goal_id"]]
    graph["goals"][0]["slug"] = "prepare-interview"
    graph["goals"][0]["title"] = "Prepare for an interview"
    graph["goals"][0]["verification"]["required_evidence"] = ["interview-preparation-completion"]
    graph["required_completion_evidence"] = ["interview-preparation-completion"]
    graph_path.write_bytes(canonical_json_bytes(graph))
    descriptor["goal_graph"] = {
        "path": graph_path.name,
        "content_sha256": digest_imported_bytes(graph_path.read_bytes()),
        "size_bytes": graph_path.stat().st_size,
    }
    definition.write_bytes(canonical_json_bytes(payload))
    proposed = propose_graph_set_offline(
        home_root=home, requested_target=target, gig_id=created.gig_id,
        definition_path=definition,
    )
    approve_offline(home_root=home, requested_target=target, proposal_id=proposed.proposal_id)


def test_interview_revision_feedback_reloads_and_rejects_unsupported_story(tmp_path: Path):
    home, target, created, source_ref = _env(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": created.gig_id}
    first = prepare_interview(**options, selection=_selection(source_ref=source_ref), content=_content(source_ref), operation_key="prep-1")
    replay = prepare_interview(**options, selection=_selection(source_ref=source_ref), content=_content(source_ref), operation_key="prep-1")
    assert not replay.created and replay.revision_id == first.revision_id
    feedback = save_interview_feedback(**options, record_id=first.record_id, parent_revision=first.revision_id, feedback=[{"text": "The answer was too broad."}], operation_key="feedback-1")
    assert feedback.revision_id != first.revision_id
    loaded = read_interview_preparation(**options, record_id=first.record_id)
    assert loaded["revision_id"] == feedback.revision_id and len(loaded["feedback"]) == 1
    historical = read_interview_preparation(**options, record_id=first.record_id, revision_id=first.revision_id)
    assert historical["revision_id"] == first.revision_id and historical["feedback"] == []
    before_stale = subprocess.check_output(["git", "-C", str(created.workpad), "rev-parse", "HEAD"], text=True).strip()
    with pytest.raises(ScoutInterviewError) as stale:
        save_interview_feedback(**options, record_id=first.record_id, parent_revision=first.revision_id, feedback=[{"text": "stale"}], operation_key="feedback-stale")
    assert stale.value.code == "interview_stale_parent"
    after_stale = subprocess.check_output(["git", "-C", str(created.workpad), "rev-parse", "HEAD"], text=True).strip()
    assert after_stale == before_stale
    revised = revise_interview(**options, record_id=first.record_id, parent_revision=feedback.revision_id, content=_content(source_ref), operation_key="revise-1")
    assert revised.revision_id != feedback.revision_id
    bad = _content(source_ref)
    bad["evidence_backed_stories"] = [{"text": "An unsupported skill claim."}]
    with pytest.raises(ScoutInterviewError, match="source_refs"):
        revise_interview(**options, record_id=first.record_id, parent_revision=revised.revision_id, content=bad, operation_key="bad-story")


def test_feedback_source_requires_real_feedback_delta_not_preparation_or_copy(tmp_path: Path):
    home, target, created, source_ref = _env(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": created.gig_id}
    first = prepare_interview(**options, selection=_selection(source_ref=source_ref), content=_content(source_ref), operation_key="feedback-source-prep")

    def revision_ref(result, kind: str) -> dict[str, object]:
        path = created.workpad / "records" / "scout-interviews" / result.record_id / "revisions" / f"{result.revision_id}.json"
        data = path.read_bytes()
        return {
            "family": "scout_interview", "kind": kind, "record_id": result.record_id,
            "revision_id": result.revision_id,
            "artifact_ref": {
                "path": path.relative_to(created.workpad).as_posix(),
                "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data),
            },
        }

    initial_as_feedback = revision_ref(first, "feedback")
    initial_selection = _selection(source_ref=source_ref)
    initial_selection["prior_feedback"] = [initial_as_feedback]
    with pytest.raises(ScoutInterviewError, match="feedback revision"):
        prepare_interview(**options, selection=initial_selection, content=_content(source_ref), operation_key="feedback-source-initial")

    feedback = save_interview_feedback(
        **options, record_id=first.record_id, parent_revision=first.revision_id,
        feedback=[{"text": "Answer with the tradeoff first."}], operation_key="feedback-source-real",
    )
    feedback_selection = _selection(source_ref=source_ref)
    feedback_selection["prior_feedback"] = [revision_ref(feedback, "feedback")]
    reused = prepare_interview(**options, selection=feedback_selection, content=_content(source_ref), operation_key="feedback-source-reuse")
    assert reused.created

    ordinary = revise_interview(
        **options, record_id=first.record_id, parent_revision=feedback.revision_id,
        content=_content(source_ref), operation_key="feedback-source-ordinary-edit",
    )
    ordinary_selection = _selection(source_ref=source_ref)
    ordinary_selection["prior_feedback"] = [revision_ref(ordinary, "feedback")]
    with pytest.raises(ScoutInterviewError, match="feedback revision"):
        prepare_interview(**options, selection=ordinary_selection, content=_content(source_ref), operation_key="feedback-source-copy")


def test_interview_accepts_actual_saved_discovery_posting_snapshot(tmp_path: Path):
    resolved, selector, _snapshot, _expected = _completed_find_jobs(tmp_path)
    posting = resolve_discovery_posting_input_from_journal(resolved, selector)
    posting_ref = posting["posting_ref"]
    assert isinstance(posting_ref, dict) and isinstance(posting_ref.get("ref"), dict)
    selection = _selection("opportunity")
    selection["posting"] = {
        "family": "scout_discovery", "kind": "posting", "run_id": selector["run_id"],
        "receipt_id": selector["receipt_id"], "output_kind": "discovery",
        "opportunity_id": selector["opportunity_id"], "snapshot_id": selector["snapshot_id"],
        "artifact_ref": posting_ref["ref"],
    }
    result = prepare_interview(
        home_root=tmp_path / "home", requested_target=tmp_path / "target", gig_id=resolved.gig_id,
        selection=selection, content=_empty_content(), operation_key="saved-discovery-posting",
    )
    assert result.created and result.record["selection"]["posting"]["family"] == "scout_discovery"


def test_interview_accepts_actual_saved_research_revision_snapshot(tmp_path: Path):
    home, target, gig_id, resolved, _snapshot, raw, _plan, _started, _receipt = complete_research(tmp_path)
    research_ref = {
        "family": "scout_research", "kind": "research", "run_id": raw["run_id"],
        "receipt_id": raw["receipt_id"], "output_kind": raw["output_kind"],
    }
    # Resolve the committed domain sidecar through the public research reader;
    # the interview service then re-authenticates those exact bytes in its own
    # single writer snapshot.
    from gigai.scout.research_inputs import resolve_research_input_from_journal
    resolved_research = resolve_research_input_from_journal(resolved, raw)
    research_ref["artifact_ref"] = resolved_research["output"]["domain_sidecar"]
    selection = _selection()
    selection["research_revisions"] = [research_ref]
    selection["reuse_prior_research"] = True
    result = prepare_interview(
        home_root=home, requested_target=target, gig_id=gig_id,
        selection=selection, content=_empty_content(), operation_key="saved-research-revision",
    )
    assert result.created and result.record["selection"]["research_revisions"][0]["family"] == "scout_research"


def test_public_prepare_interview_runs_approved_graph_and_persists_run_evidence(tmp_path: Path):
    home, target, created, source_ref = _env(tmp_path)
    _approve_prepare_interview_graph(tmp_path, home, target, created)
    selection_file = tmp_path / "selection.json"
    content_file = tmp_path / "content.json"
    selection_file.write_bytes(canonical_json_bytes(_selection(source_ref=source_ref)))
    content_file.write_bytes(canonical_json_bytes(_content(source_ref)))
    result = CliRunner().invoke(
        cli,
        [
            "scout-interview", "prepare", "--selection-file", str(selection_file),
            "--content-file", str(content_file), "--operation-key", "run-prep-1",
            "--home", str(home), "--target", str(target), "--gig", created.gig_id,
            "--run", "--confirm", "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "succeeded"
    run_details = read_run_details(home_root=home, requested_target=target, gig_id=created.gig_id, run_id=payload["run_id"])
    assert run_details["status"] == "succeeded"
    assert (created.workpad / "runs" / payload["run_id"] / "scout-interview" / "result.json").is_file()


def test_public_revision_race_refuses_competing_parent_without_second_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import gigai.scout.interview_records as records

    home, target, created, source_ref = _env(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": created.gig_id}
    first = prepare_interview(**options, selection=_selection(source_ref=source_ref), content=_content(source_ref), operation_key="race-prep")
    parent = save_interview_feedback(**options, record_id=first.record_id, parent_revision=first.revision_id, feedback=[{"text": "feedback"}], operation_key="race-feedback")
    entered = threading.Event()
    errors: list[BaseException] = []
    competing_thread: list[threading.Thread] = []
    original = records._authenticate_inputs

    def competing(*, resolved, writer, snapshot, selection, content):
        def compete() -> None:
            try:
                save_interview_feedback(**options, record_id=first.record_id, parent_revision=parent.revision_id, feedback=[{"text": "competing"}], operation_key="race-competing")
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=compete)
        competing_thread.append(thread)
        thread.start()
        entered.set()
        assert entered.wait(timeout=2)
        result = original(resolved=resolved, writer=writer, snapshot=snapshot, selection=selection, content=content)
        return result

    monkeypatch.setattr(records, "_authenticate_inputs", competing)
    revised = revise_interview(**options, record_id=first.record_id, parent_revision=parent.revision_id, content=_content(source_ref), operation_key="race-revise")
    competing_thread[0].join(timeout=12)
    assert revised.revision_id != parent.revision_id
    assert errors and isinstance(errors[0], ScoutInterviewError)
    assert errors[0].code == "interview_stale_parent"


def test_opportunity_requires_exact_snapshot_and_role_only_has_no_posting(tmp_path: Path):
    home, target, created, source_ref = _env(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": created.gig_id}
    with pytest.raises(ScoutInterviewError, match="snapshot"):
        prepare_interview(**options, selection={**_selection("opportunity"), "posting": {"record_id": _id("record"), "revision_id": _id("revision"), "family": "posting"}}, content=_content(source_ref), operation_key="missing-snapshot")
    with pytest.raises(ScoutInterviewError, match="role-only"):
        prepare_interview(**options, selection={**_selection(), "posting": {"record_id": _id("record"), "revision_id": _id("revision"), "family": "posting", "snapshot_id": "s"}}, content=_content(source_ref), operation_key="unexpected-posting")


def test_interview_refusal_preserves_journal_and_rejects_foreign_family_path(tmp_path: Path):
    home, target, created, source_ref = _env(tmp_path)
    options = {"home_root": home, "requested_target": target, "gig_id": created.gig_id}
    before = subprocess.check_output(["git", "-C", str(created.workpad), "rev-parse", "HEAD"], text=True).strip()
    spoofed = dict(source_ref)
    spoofed["family"] = "scout_interview"
    with pytest.raises(ScoutInterviewError):
        prepare_interview(
            **options,
            selection=_selection(source_ref=spoofed),
            content=_content(spoofed),
            operation_key="foreign-family",
        )
    after = subprocess.check_output(["git", "-C", str(created.workpad), "rev-parse", "HEAD"], text=True).strip()
    assert after == before

    missing = dict(source_ref)
    missing["artifact_ref"] = {**source_ref["artifact_ref"], "path": "records/record_missing/revisions/revision_missing.json"}
    with pytest.raises(ScoutInterviewError):
        prepare_interview(
            **options,
            selection=_selection(source_ref=missing),
            content=_content(missing),
            operation_key="missing-source",
        )


def test_definition_and_private_transfer_are_separate_and_refuse_replacement(tmp_path: Path):
    home, target, created, _source_ref = _env(tmp_path)
    archive = tmp_path / "definition.zip"
    exported = export_definition(workpad=created.workpad, destination=archive)
    second = tmp_path / "second-home"
    imported = import_definition(archive=archive, destination=second)
    assert exported.kind == "scout_definition_export" and imported.kind == exported.kind
    assert not (second / "records").exists()
    with pytest.raises(PrivateTransferError, match="overwrite"):
        import_definition(archive=archive, destination=second)
    private_archive = tmp_path / "private.zip"
    backup = backup_private(workpad=created.workpad, destination=private_archive, project_id=created.project_id, gig_id=created.gig_id)
    restored = tmp_path / "restored"
    restore = restore_private(archive=private_archive, destination=restored, expected_project_id=created.project_id, expected_gig_id=created.gig_id)
    assert backup.kind == "scout_private_transfer" and restore.kind == backup.kind
    assert not (restored / "state.sqlite").exists()
    with pytest.raises(PrivateTransferError, match="overwrite"):
        restore_private(archive=private_archive, destination=restored)


def test_transfer_rejects_path_traversal(tmp_path: Path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        manifest = {"schema_version": "1.0", "kind": "scout_definition_export", "format": "zip", "files": [{"path": "../escape.txt", "content_sha256": "sha256:" + "0" * 64, "size_bytes": 1}]}
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("../escape.txt", b"x")
    with pytest.raises(PrivateTransferError, match="path"):
        import_definition(archive=archive, destination=tmp_path / "dest")
