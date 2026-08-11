"""Verify G20 through a freshly installed GigAI distribution."""

from __future__ import annotations

from pathlib import Path
import tempfile
import uuid

from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.improvement import validate_improvement_manifest
from gigai.learning import publish_learning_record
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
from gigai.validators import SCHEMA_NAMES
from gigai.workpad import resolve_workpad


DIGEST = "sha256:" + "a" * 64


def _artifact(path: str, digest: str = DIGEST) -> dict[str, object]:
    return {"path": path, "content_sha256": digest, "media_type": "application/json", "size_bytes": 10}


def main() -> int:
    if len(SCHEMA_NAMES) != 25:
        raise SystemExit(f"installed G20 schema inventory is {len(SCHEMA_NAMES)}, expected 25")
    with tempfile.TemporaryDirectory(prefix="gigai-g20-installed-") as raw_root:
        root = Path(raw_root)
        home = root / "home"
        target = root / "target"
        target.mkdir()
        run_setup(build_config(home_root=home, workpad_root=root / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
        initialize_target(home_root=home, requested_target=target)
        values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 200))
        created = create_offline(home_root=home, requested_target=target, name="installed-g20", open_editor=False, uuid_factory=lambda: next(values))
        approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)

        source_path = home / "evidence" / "finding.json"
        source_path.parent.mkdir(parents=True)
        source_bytes = b"installed G20 source\n"
        source_path.write_bytes(source_bytes)
        pointer_path = resolved.path / "manifests/active-gig-version.json"
        pointer_bytes = pointer_path.read_bytes()
        record = {
            "schema_version": "1.0",
            "record_version": 1,
            "learning_id": "learning_12345678-1234-4234-9234-123456789abc",
            "project_id": created.project_id,
            "gig_id": created.gig_id,
            "subject": {"kind": "run", "run_id": "run_12345678-1234-4234-9234-123456789abc"},
            "active_version": 1,
            "active_pointer_sha256": digest_imported_bytes(pointer_bytes),
            "source": {
                "kind": "finding",
                "source_id": "finding_12345678-1234-4234-9234-123456789abc",
                "artifact": {**_artifact("evidence/finding.json"), "content_sha256": digest_imported_bytes(source_bytes), "size_bytes": len(source_bytes)},
            },
            "provenance": "observed_outcome",
            "observed_at": "2026-08-10T12:00:00Z",
            "explanation": None,
            "created_at": "2026-08-10T12:00:00Z",
            "updated_at": "2026-08-10T12:00:00Z",
        }
        publish_learning_record(home_root=home, record=record, source_root=home, active_pointer_path=pointer_path)
        manifest = {
            "schema_version": "1.0",
            "manifest_version": 1,
            "manifest_id": "improve_manifest_12345678-1234-4234-9234-123456789abc",
            "project_id": created.project_id,
            "gig_id": created.gig_id,
            "base_gig_version": 1,
            "parent_proposal_id": created.proposal_id,
            "learning_record_ids": [record["learning_id"]],
            "change_request": "Tighten the installed rubric.",
            "changes": [{"target": "rubric", "path": "rubric.minimum_evidence", "operation": "replace", "before": _artifact("before.json"), "after": _artifact("after.json")}],
            "evidence_gate": {"result": "pass", "report": _artifact("evidence-gate.json"), "supporting_record_ids": [record["learning_id"]], "checked_at": "2026-08-10T12:00:00Z"},
            "quality_gate": {
                "result": "pass", "report": _artifact("quality-gate.json"), "evaluator_version": "g20-installed-v1", "corpus_id": "corpus_g20_installed_v1", "baseline_sha256": DIGEST, "candidate_sha256": DIGEST,
                "development": {"case_count": 4, "bar_pass": True, "metrics": {"recall": 1}}, "calibration": {"case_count": 2, "bar_pass": True, "metrics": {"recall": 1}}, "final_holdout": {"case_count": 2, "bar_pass": True, "metrics": {"recall": 1}}, "final_holdout_pass": True, "no_regression": True, "checked_at": "2026-08-10T12:00:00Z"
            },
            "created_at": "2026-08-10T12:00:00Z",
            "updated_at": "2026-08-10T12:00:00Z",
        }
        validate_improvement_manifest(manifest, {record["learning_id"]: canonical_json_bytes(record)})
        stage_improvement_manifest(home_root=home, requested_target=target, manifest=manifest, uuid_factory=lambda: next(values))
        reference = root / "reference.txt"
        reference.write_bytes(b"installed improve reference\n")
        started = start_interview(home_root=home, requested_target=target, name="improve", request="Improve this Gig.", reference_paths=(reference,), improve=True, uuid_factory=lambda: next(values))
        session = started.session
        session = answer_question(session, "scope", "Improve this Gig.")
        session = answer_question(session, "references", [session.references[0].reference_id])
        session = answer_question(session, "effect", "write_workpad")
        session = answer_question(session, "privacy", "local_only")
        session = answer_question(session, "capability", "none")
        approved = approve_interview_session(home_root=home, requested_target=target, start=started, session=session, uuid_factory=lambda: next(values))
        if approved.state != "approved":
            raise SystemExit("installed G20 improve interview did not approve")
        proposal = parse_json_bytes((resolved.path / "manifests/gig-proposal.json").read_bytes())
        pointer = parse_json_bytes(pointer_path.read_bytes())
        if proposal.get("kind") != "improve" or pointer.get("active_version") != 2:
            raise SystemExit("installed G20 did not publish one improved Gig version")
    print("verified installed GigAI G20 improve lifecycle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
