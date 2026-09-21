"""Synthetic committed-journal coverage for completed Scout research reuse."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
import json
from pathlib import Path
import subprocess

import pytest

from gigai import external_recording
from gigai.canonical import EntityPrefix, canonical_json_bytes, digest_imported_bytes, generate_entity_id
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline
from gigai.scout_research_inputs import (
    ResearchInputError,
    hydrate_research_input_snapshot,
    resolve_research_input_from_journal,
    resolve_research_input,
    revalidate_research_input,
)
from gigai.scout_template import scout_candidate_inventory
from gigai.setup import build_config, run_setup
from gigai.journal import run_with_journal_writer
from gigai.journal import JournalArtifact, record_transition


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialized = initialize_defaults(home_root=home, requested_target=target, username="owner", inventory=scout_candidate_inventory())
    instance = initialized.instances[0]
    approve_offline(home_root=home, requested_target=target, gig_id=instance.gig_id, proposal_id=str(instance.proposal_id))
    return home, target, instance.gig_id


def _env(key: str, value: dict[str, object]) -> dict[str, object]:
    return {"origin": "direct_cli", "actor": {"kind": "operator", "id": "local-user"}, "input": value, "operation_key": key}


def _snapshot(home: Path, target: Path, gig_id: str, raw: dict[str, object]):
    resolved = external_recording._resolved(home_root=home, requested_target=target, gig_id=gig_id)
    return resolved, hydrate_research_input_snapshot(resolved, raw)


def _research() -> dict[str, object]:
    capture, review = b"Synthetic role capture.", b"Synthetic independent review."
    return {
        "role_title": "Forward Deployed Engineer",
        "role_summary": "Customer-facing engineering connects deployment work and product feedback.",
        "responsibilities": [{"responsibility_id": "responsibility_delivery", "description": "Deliver technical work with customer teams.", "claim_ids": ["claim_delivery"]}],
        "variations": [{"variation_id": "variation_product", "description": "Some employers emphasize product feedback.", "claim_ids": ["claim_delivery"]}],
        "reusable_sections": [{"section_id": "section_delivery", "heading": "Delivery context", "content": "Reuse this distinction later.", "claim_ids": ["claim_delivery"]}],
        "compensation": {"status": "unknown", "geography": None, "currency": None, "as_of_date": None, "pay_period": None, "base_range": None, "total_range": None, "source_limitations": ["No salary evidence was supplied."], "claim_ids": []},
        "sources": [{"source_id": "source_role", "locator": "https://example.test/role", "title": "Synthetic role profile", "publisher": "Example Labs", "kind": "employer", "published_date": None, "retrieved_date": "2026-09-09", "status": "independently_verified", "claim_ids": ["claim_delivery"], "capture_ref": {"artifact_id": "role_capture", "content_sha256": digest_imported_bytes(capture), "size_bytes": len(capture)}, "verification": {"method": "independent_review", "evidence_ref": {"artifact_id": "role_review", "content_sha256": digest_imported_bytes(review), "size_bytes": len(review)}, "actor": {"kind": "reviewer", "id": "synthetic-reviewer"}}}],
        "claims": [{"claim_id": "claim_delivery", "statement": "The supplied role profile describes customer-team delivery.", "source_ids": ["source_role"], "status": "independently_verified"}],
        "uncertainties": [{"uncertainty_id": "uncertainty_scope", "topic": "Employer specificity", "detail": "Scope varies by employer.", "claim_ids": []}],
        "questions": [{"question_id": "question_priority", "prompt": "Which delivery emphasis matters most?", "reason": "It narrows later tailoring.", "claim_ids": []}],
        "checks": [{"check_id": "check_source", "kind": "source_integrity", "result": "pass", "detail": "Supplied bytes match declared refs.", "claim_ids": ["claim_delivery"]}],
        "output_roles": ["tailoring_context", "interview_context"],
    }


def _complete(tmp_path: Path, *, plan_key: str = "plan", start_key: str = "start", split_check: bool = False):
    home, target, gig_id = _fixture(tmp_path)
    plan = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env(plan_key, {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"}], "output_kinds": ["research"], "predecessor": None}))
    started = external_recording.start_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env(start_key, {"run_plan_id": plan.payload["run_plan_id"]}))
    renderer = import_module("gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000076.research")
    capture, review = b"Synthetic role capture.", b"Synthetic independent review."
    packet = renderer.build_research_packet(project_id=plan.payload["project_id"], gig_id=gig_id, gig_version=plan.payload["gig_version"], graph_id=plan.payload["goal_graph_id"], graph_version=1, run_id=started.payload["run_id"], selected_inputs=plan.payload["inputs"], research=_research(), artifact_bytes={"role_capture": capture, "role_review": review})
    digest = digest_imported_bytes(packet.markdown)
    output_item = {"kind": "research", "markdown": packet.markdown.decode(), "sidecar": {"document_sha256": digest, "output_kind": "research", "run_id": started.payload["run_id"], "selected_inputs": plan.payload["inputs"]}, "domain_sidecar": {"schema_id": "urn:gigai:scout:research-packet:3", "value": packet.sidecar}, "supporting_artifacts": [{"artifact_id": name, "media_type": "text/plain", "content_base64": base64.b64encode(data).decode(), "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)} for name, data in {"role_capture": capture, "role_review": review}.items()]}
    check_item = {"kind": "research-role-completion", "markdown": "# Completion\n\npass\n", "sidecar": {"evidence_kind": "research-role-completion", "run_id": started.payload["run_id"], "output_sha256": digest, "result": "pass"}}
    checkpoint = external_recording.checkpoint_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("checkpoint", {"run_id": started.payload["run_id"], "parent_checkpoint": None, "questions": [], "artifact_refs": [output_item] if split_check else [output_item, check_item], "reason": "synthetic reuse fixture"}))
    output, check = checkpoint.payload["artifacts"] if not split_check else (checkpoint.payload["artifacts"][0], None)
    if split_check:
        second = external_recording.checkpoint_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("checkpoint-check", {"run_id": started.payload["run_id"], "parent_checkpoint": checkpoint.payload["checkpoint_id"], "questions": [], "artifact_refs": [check_item], "reason": "synthetic completion check"}))
        check = second.payload["artifacts"][0]
        parent = second.payload["checkpoint_id"]
    else:
        parent = checkpoint.payload["checkpoint_id"]
    receipt = external_recording.submit_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("submit", {"run_id": started.payload["run_id"], "parent_checkpoint": parent, "output_refs": [output], "check_refs": [check["sidecar"]], "disclosure": {"execution": "unobserved", "actor_report": "declared"}}))
    raw = {"family": "scout_research", "run_id": started.payload["run_id"], "receipt_id": receipt.payload["receipt_id"], "output_kind": "research"}
    resolved, snapshot = _snapshot(home, target, gig_id, raw)
    return home, target, gig_id, resolved, snapshot, raw, plan.payload, started.payload, receipt.payload


def test_resolve_research_input_emits_explicit_provenance_envelope(tmp_path: Path) -> None:
    _home, _target, _gig, resolved, snapshot, raw, plan, started, receipt = _complete(tmp_path)
    result = resolve_research_input(resolved, snapshot, raw)
    assert set(result) == {"family", "run_id", "receipt_id", "output_kind", "project_id", "gig_id", "gig_version", "run_plan_id", "run_plan_ref", "run_ref", "receipt_ref", "checkpoint_id", "checkpoint_ref", "selected_graph", "output", "domain_binding"}
    assert result["run_id"] == started["run_id"] == receipt["run_id"]
    assert result["run_plan_id"] == plan["run_plan_id"]
    assert result["output"]["kind"] == "research"
    assert result["domain_binding"]["schema_id"] == "urn:gigai:scout:research-packet:3"
    assert result["domain_binding"]["validator_id"] == "scout-role-research:3"
    revalidate_research_input(resolved, snapshot, result)
    changed = deepcopy(result)
    changed["selected_graph"]["graph_version"] = 999
    with pytest.raises(ResearchInputError) as refused:
        revalidate_research_input(resolved, snapshot, changed)
    assert refused.value.code == "research_input_mismatch"


def test_public_journal_helper_hydrates_and_resolves_without_caller_refs(tmp_path: Path) -> None:
    _home, _target, _gig, resolved, _snapshot_value, raw, _plan, _started, _receipt = _complete(tmp_path)
    result = resolve_research_input_from_journal(resolved, raw)
    assert result["run_id"] == raw["run_id"]


def test_hydration_retains_complete_run_history_and_accepts_held_writer(tmp_path: Path) -> None:
    home, target, gig_id, resolved, _snapshot_value, raw, _plan, started, _receipt = _complete(tmp_path, split_check=True)
    hydrated = hydrate_research_input_snapshot(resolved, raw)
    checkpoint_paths = {
        path for path in hydrated.artifacts
        if path.startswith(f"runs/{started['run_id']}/checkpoints/")
    }
    assert len(checkpoint_paths) == 2

    def inside(writer):
        return resolve_research_input_from_journal(resolved, raw, writer=writer)

    result = run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id,
        operation=inside,
    )
    assert result["run_id"] == raw["run_id"]


def test_unreferenced_forked_checkpoint_is_hydrated_and_refused(tmp_path: Path) -> None:
    home, target, gig_id, resolved, snapshot, raw, _plan, started, receipt = _complete(tmp_path)
    checkpoint = next(value for path, value in snapshot.artifacts.items() if path.endswith("checkpoints/" + receipt["outputs"][0]["markdown"]["path"].split("/")[3] + ".json"))
    forged = json.loads(checkpoint)
    forged["checkpoint_id"] = generate_entity_id(EntityPrefix.CHECKPOINT, uuid_factory=__import__("uuid").uuid4, is_persisted=lambda _value: False)
    forged["sequence"] = 3
    forged["parent_checkpoint"] = None
    forged_data = canonical_json_bytes(forged)
    record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id,
        handoff_id=generate_entity_id(EntityPrefix.HANDOFF, uuid_factory=__import__("uuid").uuid4, is_persisted=lambda _value: False),
        transition="external_recording_checkpointed", body="synthetic forked checkpoint",
        artifacts=(JournalArtifact(f"runs/{started['run_id']}/checkpoints/{forged['checkpoint_id']}.json", forged_data),),
        front_matter={"artifact_refs": [{"path": f"runs/{started['run_id']}/checkpoints/{forged['checkpoint_id']}.json", "content_sha256": digest_imported_bytes(forged_data), "media_type": "application/json", "size_bytes": len(forged_data)}]})
    with pytest.raises(ResearchInputError):
        resolve_research_input_from_journal(resolved, raw)


def test_extra_terminal_receipt_is_visible_to_hydration_and_refused(tmp_path: Path) -> None:
    _home, _target, gig_id, resolved, snapshot, raw, _plan, started, receipt = _complete(tmp_path)
    forged = dict(receipt)
    forged["receipt_id"] = generate_entity_id(EntityPrefix.RECEIPT, uuid_factory=__import__("uuid").uuid4, is_persisted=lambda _value: False)
    forged_data = canonical_json_bytes(forged)
    path = f"runs/{started['run_id']}/receipts/{forged['receipt_id']}.json"
    record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id,
        handoff_id=generate_entity_id(EntityPrefix.HANDOFF, uuid_factory=__import__("uuid").uuid4, is_persisted=lambda _value: False),
        transition="external_recording_succeeded", body="synthetic competing receipt",
        artifacts=(JournalArtifact(path, forged_data),),
        front_matter={"artifact_refs": [{"path": path, "content_sha256": digest_imported_bytes(forged_data), "media_type": "application/json", "size_bytes": len(forged_data)}]})
    with pytest.raises(ResearchInputError):
        resolve_research_input_from_journal(resolved, raw)


def test_resolve_research_input_accepts_output_and_check_from_distinct_checkpoints(tmp_path: Path) -> None:
    _home, _target, _gig, resolved, snapshot, raw, _plan, _started, receipt = _complete(tmp_path, split_check=True)
    result = resolve_research_input(resolved, snapshot, raw)
    output_path = result["output"]["markdown"]["path"]
    check_path = receipt["checks"][0]["path"]
    assert output_path.split("/")[3] == result["checkpoint_id"]
    assert check_path.split("/")[3] != result["checkpoint_id"]
    assert result["output"]["domain_sidecar"]["path"].split("/")[3] == result["checkpoint_id"]


def test_exact_completed_run_remains_selectable_when_newer_run_exists(tmp_path: Path) -> None:
    home, target, gig_id, resolved, snapshot, raw, plan, _started, _receipt = _complete(tmp_path)
    newer_plan = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("newer-plan", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "A newer context"}], "output_kinds": ["research"], "predecessor": None}))
    newer = external_recording.start_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("newer-start", {"run_plan_id": newer_plan.payload["run_plan_id"]}))
    assert newer.created
    resolved, snapshot = _snapshot(home, target, gig_id, raw)
    selected = resolve_research_input(resolved, snapshot, raw)
    assert selected["run_id"] == raw["run_id"]


@pytest.mark.parametrize(
    "raw_factory,code",
    [
        (lambda raw: {**raw, "receipt_id": "receipt_00000000-0000-4000-8000-000000000099"}, "research_input_not_found"),
        (lambda raw: {**raw, "run_id": "run_00000000-0000-4000-8000-000000000099"}, "research_input_not_found"),
        (lambda raw: {**raw, "output_kind": "discovery"}, "research_input_invalid"),
    ],
    ids=["missing-receipt", "foreign-run", "wrong-output-kind"],
)
def test_closed_selector_rejects_missing_foreign_or_wrong_records(tmp_path: Path, raw_factory, code: str) -> None:
    _home, _target, _gig, resolved, snapshot, raw, *_rest = _complete(tmp_path)
    with pytest.raises(ResearchInputError) as refused:
        resolve_research_input(resolved, snapshot, raw_factory(raw))
    assert refused.value.code == code


def test_forged_committed_receipt_bytes_are_refused(tmp_path: Path) -> None:
    _home, _target, _gig, resolved, snapshot, raw, *_rest = _complete(tmp_path)
    receipt_path = f"runs/{raw['run_id']}/receipts/{raw['receipt_id']}.json"
    forged = dict(snapshot.artifacts)
    forged[receipt_path] = forged[receipt_path].replace(b'"outcome":"succeeded"', b'"outcome":"cancelled"')
    forged_snapshot = replace(snapshot, artifacts=forged)
    with pytest.raises(ResearchInputError) as refused:
        resolve_research_input(resolved, forged_snapshot, raw)
    assert refused.value.code == "research_input_refused"


def test_cancelled_run_is_not_reusable_research(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    plan = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("plan", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": None}], "output_kinds": ["research"], "predecessor": None}))
    started = external_recording.start_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("start", {"run_plan_id": plan.payload["run_plan_id"]}))
    cancelled = external_recording.cancel_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_env("cancel", {"run_id": started.payload["run_id"], "reason": "synthetic cancellation"}))
    raw = {"family": "scout_research", "run_id": started.payload["run_id"], "receipt_id": cancelled.payload["receipt_id"], "output_kind": "research"}
    resolved, snapshot = _snapshot(home, target, gig_id, raw)
    with pytest.raises(ResearchInputError) as refused:
        resolve_research_input(resolved, snapshot, raw)
    assert refused.value.code == "research_input_not_terminal"


def test_unknown_historical_validator_is_unsupported_not_corruption(tmp_path: Path) -> None:
    _home, _target, _gig, resolved, snapshot, raw, plan, *_rest = _complete(tmp_path)
    contract_ref = plan["output_contract"]
    contract_path = contract_ref["path"]
    contract = json.loads(snapshot.artifacts[contract_path])
    contract["domains"]["research"]["validator_id"] = "scout-role-research:999"
    contract_bytes = canonical_json_bytes(contract)
    forged = dict(snapshot.artifacts)
    forged[contract_path] = contract_bytes
    forged_plan = deepcopy(plan)
    forged_plan["output_contract"] = {**contract_ref, "content_sha256": digest_imported_bytes(contract_bytes), "size_bytes": len(contract_bytes)}
    with pytest.raises(ResearchInputError) as refused:
        from gigai.scout_research_inputs import _fixed_domain_binding
        _fixed_domain_binding(replace(snapshot, artifacts=forged), forged_plan, resolved)
    assert refused.value.code == "research_input_validator_unsupported"
