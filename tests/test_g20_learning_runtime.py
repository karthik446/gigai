from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes
from gigai.improvement import ImprovementRefusedError, validate_improvement_manifest
from gigai.learning import LearningRefusedError, publish_learning_record, reconcile_learning_root
from gigai.lifecycle import (
    approve_interview_session,
    approve_offline,
    create_offline,
    stage_improvement_manifest,
    start_interview,
)
from gigai.proposal_interview import answer_question
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad
from gigai.canonical import parse_json_bytes


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_12345678-1234-4234-9234-123456789abc"
RUN_ID = "run_12345678-1234-4234-9234-123456789abc"
DIGEST = "sha256:" + "a" * 64


def _record(index: int = 1, provenance: str = "observed_outcome") -> dict[str, object]:
    suffix = f"{index:012x}"
    return {
        "schema_version": "1.0",
        "record_version": 1,
        "learning_id": f"learning_12345678-1234-4234-9234-{suffix}",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "subject": {"kind": "run", "run_id": RUN_ID},
        "active_version": 1,
        "active_pointer_sha256": DIGEST,
        "source": {
            "kind": "finding",
            "source_id": f"finding_12345678-1234-4234-9234-{suffix}",
            "artifact": {
                "path": f"evidence/finding-{index}.json",
                "content_sha256": DIGEST,
                "media_type": "application/json",
                "size_bytes": 10,
            },
        },
        "provenance": provenance,
        "observed_at": "2026-08-10T12:00:00Z",
        "explanation": None,
        "created_at": "2026-08-10T12:00:00Z",
        "updated_at": "2026-08-10T12:00:00Z",
    }


def _artifact(path: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": DIGEST,
        "media_type": "application/json",
        "size_bytes": 10,
    }


def _manifest(record_ids: list[str]) -> dict[str, object]:
    split = {"case_count": 8, "bar_pass": True, "metrics": {"recall": 1}}
    return {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": "improve_manifest_12345678-1234-4234-9234-123456789abc",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "base_gig_version": 1,
        "parent_proposal_id": "gp_12345678-1234-4234-9234-123456789abc",
        "learning_record_ids": record_ids,
        "change_request": "Improve the rubric.",
        "changes": [{
            "target": "rubric",
            "path": "rubric.minimum_evidence",
            "operation": "replace",
            "before": _artifact("before.json"),
            "after": _artifact("after.json"),
        }],
        "evidence_gate": {
            "result": "pass",
            "report": _artifact("evidence-gate.json"),
            "supporting_record_ids": record_ids,
            "checked_at": "2026-08-10T12:00:00Z",
        },
        "quality_gate": {
            "result": "pass",
            "report": _artifact("quality-gate.json"),
            "evaluator_version": "g20-v1",
            "corpus_id": "corpus_g20_v1",
            "baseline_sha256": DIGEST,
            "candidate_sha256": DIGEST,
            "development": split,
            "calibration": split,
            "final_holdout": split,
            "final_holdout_pass": True,
            "no_regression": True,
            "checked_at": "2026-08-10T12:00:00Z",
        },
        "created_at": "2026-08-10T12:00:00Z",
        "updated_at": "2026-08-10T12:00:00Z",
    }


def test_learning_publication_is_atomic_and_duplicate_safe(tmp_path: Path) -> None:
    home = tmp_path / "home"
    record = _record()
    published = publish_learning_record(home_root=home, record=record)
    assert published.path.is_file()
    assert (home / "learning" / "journal.jsonl").is_file()
    duplicate = deepcopy(record)
    duplicate["learning_id"] = "learning_12345678-1234-4234-9234-000000000002"
    with pytest.raises(LearningRefusedError, match="already observed"):
        publish_learning_record(home_root=home, record=duplicate)


def test_orphan_after_rename_is_discarded_on_reconcile(tmp_path: Path) -> None:
    home = tmp_path / "home"
    with pytest.raises(Exception, match="injected interruption"):
        publish_learning_record(home_root=home, record=_record(), failpoint="after_atomic_rename")
    result = reconcile_learning_root(home)
    assert result.retained == 0
    assert not list((home / "learning" / "records").glob("*.json"))


def test_learning_root_refuses_symlink_escape(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "learning").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LearningRefusedError, match="real directory"):
        publish_learning_record(home_root=home, record=_record())


def test_improvement_has_independent_evidence_and_quality_gates() -> None:
    record = _record()
    learning_id = record["learning_id"]
    manifest = _manifest([learning_id])
    parsed, gates = validate_improvement_manifest(manifest, {learning_id: record})
    assert parsed["manifest_id"] == manifest["manifest_id"]
    assert gates.evidence_sufficient and gates.quality_passed

    feedback = _record(provenance="operator_feedback")
    feedback_id = feedback["learning_id"]
    under_evidenced = _manifest([feedback_id])
    with pytest.raises(ImprovementRefusedError, match="cannot satisfy"):
        validate_improvement_manifest(under_evidenced, {feedback_id: feedback})

    regressing = _manifest([learning_id])
    regressing["quality_gate"]["no_regression"] = False
    with pytest.raises(Exception, match="quality"):
        validate_improvement_manifest(regressing, {learning_id: record})


def test_g22_improve_mode_advances_existing_version_once(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target)
    values = iter(__import__("uuid").UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 100))
    created = create_offline(home_root=home, requested_target=target, name="g20-base", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    record = _record()
    record["project_id"] = created.project_id
    record["gig_id"] = created.gig_id
    publish_learning_record(home_root=home, record=record)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)
    manifest = _manifest([record["learning_id"]])
    manifest["project_id"] = created.project_id
    manifest["gig_id"] = created.gig_id
    manifest["parent_proposal_id"] = created.proposal_id
    stage_improvement_manifest(home_root=home, requested_target=target, manifest=manifest, uuid_factory=lambda: next(values))
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b"improve evidence\n")
    started = start_interview(
        home_root=home,
        requested_target=target,
        name="improve",
        request="Tighten the rubric from observed review outcomes.",
        reference_paths=(evidence,),
        improve=True,
        uuid_factory=lambda: next(values),
    )
    session = started.session
    session = answer_question(session, "scope", "Tighten the rubric from observed review outcomes.")
    session = answer_question(session, "references", [session.references[0].reference_id])
    session = answer_question(session, "effect", "write_workpad")
    session = answer_question(session, "privacy", "local_only")
    session = answer_question(session, "capability", "none")
    approved = approve_interview_session(
        home_root=home,
        requested_target=target,
        start=started,
        session=session,
        uuid_factory=lambda: next(values),
    )
    assert approved.state == "approved"
    proposal = parse_json_bytes((resolved.path / "manifests/gig-proposal.json").read_bytes())
    pointer = parse_json_bytes((resolved.path / "manifests/active-gig-version.json").read_bytes())
    assert proposal["kind"] == "improve"
    assert proposal["base_gig_version"] == 1
    assert pointer["active_version"] == 2
    assert pointer["approved_proposal_id"] == proposal["proposal_id"]
    replayed = approve_interview_session(
        home_root=home,
        requested_target=target,
        start=started,
        session=approved,
        uuid_factory=lambda: next(values),
    )
    assert replayed.proposal_id == approved.proposal_id
    assert parse_json_bytes((resolved.path / "manifests/active-gig-version.json").read_bytes())["active_version"] == 2
