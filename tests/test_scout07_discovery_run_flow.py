"""Real journaled find-jobs v2 flow using the packaged discovery bridge."""

from __future__ import annotations

import base64
from importlib import import_module
import json
from pathlib import Path
import subprocess

import pytest

from gigai import external_recording
from gigai.canonical import digest_imported_bytes
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline
from gigai.native_records import create_native_record
from gigai.private_records import _private_snapshot
from gigai.workpad import resolve_workpad
from gigai.scout_template import scout_candidate_inventory
from gigai.setup import build_config, run_setup


def _fact(value: str | None, *, state: str = "known") -> dict[str, object]:
    return {"state": state, "value": value if state == "known" else None,
            "context": None, "provenance": {"kind": "user_reported", "source_refs": []}, "conflict_refs": []}


def _profile() -> dict[str, object]:
    return {"schema_version": "1.0", "kind": "profile_preferences",
            "scope": {"mode": "saved_default", "task_context_id": None, "base": None},
            "payload": {"hard_constraints": {"geography": _fact("Denver"), "work_mode": _fact("remote"), "seniority": _fact("senior"), "employment_type": _fact("full_time"), "compensation": _fact("120000 USD annual")},
                        "soft_priorities": {}, "sponsorship_need": _fact("needs_sponsorship"),
                        "employer_sponsorship": _fact(None, state="unknown"), "eligibility": {**_fact("eligible"), "context": "US work authorization"}}}


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialized = initialize_defaults(home_root=home, requested_target=target, username="owner", inventory=scout_candidate_inventory())
    instance = initialized.instances[0]
    approve_offline(home_root=home, requested_target=target, gig_id=instance.gig_id, proposal_id=str(instance.proposal_id))
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    return home, target, instance.gig_id, workpad


def _envelope(key: str, value: dict[str, object]) -> dict[str, object]:
    return {"origin": "direct_cli", "actor": {"kind": "operator", "id": "local-user"}, "input": value, "operation_key": key}


def _packet(plan: dict[str, object], run: dict[str, object], preference: bytes, *, outcome: str = "no_match"):
    renderer = import_module("gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000074.discovery")
    capture, review = b"Synthetic job capture.", b"Synthetic independent review."
    posting = {"employer": "Example Labs", "title": "Forward Deployed Engineer", "source_posting_id": "fde-074", "observed_date": "2026-09-10", "availability": "reported_open",
               "source": {"source_id": "source_job", "locator": "https://jobs.example.test/fde-074", "title": "Synthetic posting", "publisher": "Example Labs", "published_date": "2026-09-09", "retrieved_date": "2026-09-10", "status": "independently_verified", "capture_ref": {"artifact_id": "job_capture", "content_sha256": digest_imported_bytes(capture), "size_bytes": len(capture)}, "verification": {"method": "independent_review", "evidence_ref": {"artifact_id": "job_review", "content_sha256": digest_imported_bytes(review), "size_bytes": len(review)}, "actor": {"kind": "reviewer", "id": "synthetic-reviewer"}}},
               "facts": {"geography": {"state": "known", "value": "Denver", "source_ids": ["source_job"]}, "work_mode": {"state": "known", "value": "remote", "source_ids": ["source_job"]}, "seniority": {"state": "known", "value": "senior", "source_ids": ["source_job"]}, "employment_type": {"state": "known", "value": "full_time", "source_ids": ["source_job"]}, "compensation": {"state": "known", "value": "120000 USD annual", "source_ids": ["source_job"]}, "employer_sponsorship": {"state": "unknown", "value": None, "source_ids": ["source_job"]}, "eligibility": {"state": "known", "value": "eligible", "source_ids": ["source_job"]}}}
    discovery = {"outcome": outcome, "postings": [posting], "shortlist": [], "exclusions": [], "questions": []}
    packet = renderer.build_discovery_packet(project_id=plan["project_id"], gig_id=plan["gig_id"], gig_version=plan["gig_version"], graph_id=plan["goal_graph_id"], graph_version=1, run_id=run["run_id"], selected_inputs=plan["inputs"], preference_bytes={plan["inputs"][0]["content"]["blob_ref"]["path"]: preference}, discovery=discovery, supporting={"job_capture": capture, "job_review": review})
    digest = digest_imported_bytes(packet.markdown)
    artifact = {"kind": "discovery", "markdown": packet.markdown.decode(), "sidecar": {"document_sha256": digest, "output_kind": "discovery", "run_id": run["run_id"], "selected_inputs": plan["inputs"]}, "domain_sidecar": {"schema_id": "urn:gigai:scout:discovery-packet:2", "value": packet.sidecar}, "supporting_artifacts": [{"artifact_id": name, "media_type": "text/plain", "content_base64": base64.b64encode(data).decode(), "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)} for name, data in {"job_capture": capture, "job_review": review}.items()]}
    check = {"kind": "find-jobs-completion", "markdown": "# Completion\n\npass\n", "sidecar": {"evidence_kind": "find-jobs-completion", "run_id": run["run_id"], "output_sha256": digest, "result": "pass"}}
    return artifact, check


def test_find_jobs_v2_real_plan_checkpoint_submit_and_exact_replay(tmp_path: Path) -> None:
    home, target, gig_id, workpad = _fixture(tmp_path)
    native = create_native_record(home_root=home, requested_target=target, gig_id=gig_id, content=_profile(), actor={"kind": "operator", "id": "local-user"}, origin="user_reported", operation_key="profile")
    plan = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("find-plan", {"graph_selector": "find-jobs", "gig_version": None, "selection_record": None, "input_refs": [{"family": "scout_record", "record_id": native.record_id, "revision_id": native.revision_id, "scope": {"mode": "saved_default", "task_context_id": None}}], "output_kinds": ["discovery"], "predecessor": None}))
    started = external_recording.start_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("find-start", {"run_plan_id": plan.payload["run_plan_id"]}))
    selected = plan.payload["inputs"][0]
    blob_ref = selected["content"]["blob_ref"]
    snapshot = _private_snapshot(resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id))
    preference = snapshot.artifacts[blob_ref["path"]]
    artifact, check = _packet(plan.payload, started.payload, preference)
    checkpoint = external_recording.checkpoint_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("find-check", {"run_id": started.payload["run_id"], "parent_checkpoint": None, "questions": [], "artifact_refs": [artifact, check], "reason": "synthetic supplied discovery"}))
    output, recorded_check = checkpoint.payload["artifacts"]
    submit_envelope = _envelope("find-submit", {"run_id": started.payload["run_id"], "parent_checkpoint": checkpoint.payload["checkpoint_id"], "output_refs": [output], "check_refs": [recorded_check["sidecar"]], "disclosure": {"execution": "unobserved", "actor_report": "declared"}})
    submitted = external_recording.submit_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=submit_envelope)
    replay = external_recording.submit_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=submit_envelope)
    assert submitted.created and submitted.payload["outcome"] == "succeeded"
    assert not replay.created and replay.payload == submitted.payload


def test_find_jobs_domain_tamper_refuses_before_publication(tmp_path: Path) -> None:
    home, target, gig_id, _ = _fixture(tmp_path)
    native = create_native_record(home_root=home, requested_target=target, gig_id=gig_id, content=_profile(), actor={"kind": "operator", "id": "local-user"}, origin="user_reported", operation_key="profile")
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("bad-find", {"graph_selector": "find-jobs", "gig_version": None, "selection_record": None, "input_refs": [{"family": "scout_record", "record_id": native.record_id, "revision_id": "revision_foreign", "scope": {"mode": "saved_default", "task_context_id": None}}], "output_kinds": ["discovery"], "predecessor": None}))
    assert refused.value.code in {"external_authority_mismatch", "external_input_invalid", "external_invocation_invalid"}


def test_find_jobs_partial_packet_and_tamper_are_journal_safe(tmp_path: Path) -> None:
    home, target, gig_id, _ = _fixture(tmp_path)
    native = create_native_record(home_root=home, requested_target=target, gig_id=gig_id, content=_profile(), actor={"kind": "operator", "id": "local-user"}, origin="user_reported", operation_key="profile")
    plan = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("partial-plan", {"graph_selector": "find-jobs", "gig_version": None, "selection_record": None, "input_refs": [{"family": "scout_record", "record_id": native.record_id, "revision_id": native.revision_id, "scope": {"mode": "saved_default", "task_context_id": None}}], "output_kinds": ["discovery"], "predecessor": None}))
    started = external_recording.start_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("partial-start", {"run_plan_id": plan.payload["run_plan_id"]}))
    selected = plan.payload["inputs"][0]
    snapshot = _private_snapshot(resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id))
    artifact, check = _packet(plan.payload, started.payload, snapshot.artifacts[selected["content"]["blob_ref"]["path"]], outcome="partial")
    checkpoint = external_recording.checkpoint_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("partial-check", {"run_id": started.payload["run_id"], "parent_checkpoint": None, "questions": [], "artifact_refs": [artifact, check], "reason": "partial synthetic discovery"}))
    assert checkpoint.created and checkpoint.payload["artifacts"][0]["kind"] == "discovery"
    before = _private_snapshot(resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id)).artifacts
    tampered = json.loads(json.dumps(artifact))
    tampered["domain_sidecar"]["value"]["origin"]["gig_id"] = "gig_00000000-0000-4000-8000-000000000099"
    with pytest.raises(external_recording.ExternalRecordingError):
        external_recording.checkpoint_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("tampered-check", {"run_id": started.payload["run_id"], "parent_checkpoint": checkpoint.payload["checkpoint_id"], "questions": [], "artifact_refs": [tampered, check], "reason": "tampered synthetic discovery"}))
    after = _private_snapshot(resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id)).artifacts
    assert before == after
