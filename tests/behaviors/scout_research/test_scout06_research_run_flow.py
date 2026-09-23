"""Real role-only v2 external research integration checks."""

from __future__ import annotations

import base64
import json
from importlib import import_module
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from gigai import external_recording
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.cli import cli
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline
from gigai.private_records import import_run_input
from gigai.scout.template import scout_candidate_inventory
from gigai.setup import build_config, run_setup


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


def _started_v2_run(tmp_path: Path, *, plan_key: str = "role-plan", start_key: str = "role-start") -> tuple[Path, Path, str, dict[str, object], dict[str, object]]:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(plan_key, {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"}], "output_kinds": ["research"], "predecessor": None}),
    )
    started = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(start_key, {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    return home, target, gig_id, plan.payload, started.payload


def _valid_v2_checkpoint(
    *, plan: dict[str, object], started: dict[str, object], operation_key: str = "checkpoint"
) -> dict[str, object]:
    renderer = import_module("gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000071.research")
    capture = b"Synthetic supplied role capture."
    review = b"Synthetic independent source review."
    research = {
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
    packet = renderer.build_research_packet(
        project_id=plan["project_id"], gig_id=plan["gig_id"], gig_version=plan["gig_version"],
        graph_id=plan["goal_graph_id"], graph_version=1, run_id=started["run_id"],
        selected_inputs=plan["inputs"], research=research,
        artifact_bytes={"role_capture": capture, "role_review": review},
    )
    digest = digest_imported_bytes(packet.markdown)
    return _envelope(operation_key, {"run_id": started["run_id"], "parent_checkpoint": None, "questions": [], "artifact_refs": [{"kind": "research", "markdown": packet.markdown.decode(), "sidecar": {"document_sha256": digest, "output_kind": "research", "run_id": started["run_id"], "selected_inputs": plan["inputs"]}, "domain_sidecar": {"schema_id": "urn:gigai:scout:research-packet:2", "value": packet.sidecar}, "supporting_artifacts": [{"artifact_id": name, "media_type": "text/plain", "content_base64": base64.b64encode(data).decode(), "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)} for name, data in {"role_capture": capture, "role_review": review}.items()]}, {"kind": "research-role-completion", "markdown": "# Completion\n\npass\n", "sidecar": {"evidence_kind": "research-role-completion", "run_id": started["run_id"], "output_sha256": digest, "result": "pass"}}], "reason": "synthetic supplied research"})


def test_role_only_v2_plan_and_start_use_real_candidate(tmp_path: Path) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("role-plan", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"}], "output_kinds": ["research"], "predecessor": None}),
    )
    assert plan.created and plan.payload["schema_version"] == "2.0"
    assert plan.payload["inputs"] == [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"}]
    started = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("role-start", {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    assert started.created and started.payload["schema_version"] == "2.0"


def test_v2_plan_replay_is_exact_and_v1_cannot_downgrade(tmp_path: Path) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    request = {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": None}], "output_kinds": ["research"], "predecessor": None}
    first = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("same", request))
    replay = external_recording.plan_v2(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("same", request))
    assert not replay.created and replay.payload == first.payload
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        external_recording.plan(home_root=home, requested_target=target, gig_id=gig_id, envelope=_envelope("v1", request))
    assert refused.value.code == "external_invocation_invalid"


def test_external_cli_advertises_frozen_protocol_versions() -> None:
    result = CliRunner().invoke(cli, ["external", "plan", "--help"])
    assert result.exit_code == 0
    assert "--protocol-version [1|2]" in result.output


def test_role_only_plan_preserves_optional_committed_g45_input(tmp_path: Path) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    imported = import_run_input(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        data=b"Synthetic optional supplied posting.\n",
    )
    plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("optional-g45", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": None}, {"family": "g45_run_input", "id": imported.item_id}], "output_kinds": ["research"], "predecessor": None}),
    )
    assert [item["family"] for item in plan.payload["inputs"]] == ["role_request", "g45_run_input"]


def test_role_only_v2_checkpoint_mixed_research_and_check_then_submit(tmp_path: Path) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    runner = CliRunner()
    plan_input = tmp_path / "plan.json"
    plan_request = _envelope("cli-plan", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"}], "output_kinds": ["research"], "predecessor": None})
    plan_input.write_bytes(canonical_json_bytes(plan_request))
    planned = runner.invoke(cli, ["external", "plan", "--gig", gig_id, "--home", str(home), "--target", str(target), "--invocation", str(plan_input), "--protocol-version", "2", "--json"])
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    started_input = tmp_path / "start.json"
    started_input.write_bytes(canonical_json_bytes(_envelope("cli-start", {"run_plan_id": plan["run_plan_id"]})))
    started_result = runner.invoke(cli, ["external", "start", "--gig", gig_id, "--home", str(home), "--target", str(target), "--invocation", str(started_input), "--protocol-version", "2", "--json"])
    assert started_result.exit_code == 0, started_result.output
    started = json.loads(started_result.output)

    renderer = import_module("gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000071.research")
    capture = b"Synthetic supplied role capture."
    review = b"Synthetic independent source review."
    research = {
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
    packet = renderer.build_research_packet(
        project_id=plan["project_id"], gig_id=gig_id, gig_version=plan["gig_version"],
        graph_id=plan["goal_graph_id"], graph_version=1, run_id=started["run_id"],
        selected_inputs=plan["inputs"], research=research,
        artifact_bytes={"role_capture": capture, "role_review": review},
    )
    digest = digest_imported_bytes(packet.markdown)
    checkpoint_request = _envelope("cli-checkpoint", {"run_id": started["run_id"], "parent_checkpoint": None, "questions": [], "artifact_refs": [{"kind": "research", "markdown": packet.markdown.decode(), "sidecar": {"document_sha256": digest, "output_kind": "research", "run_id": started["run_id"], "selected_inputs": plan["inputs"]}, "domain_sidecar": {"schema_id": "urn:gigai:scout:research-packet:2", "value": packet.sidecar}, "supporting_artifacts": [{"artifact_id": name, "media_type": "text/plain", "content_base64": base64.b64encode(data).decode(), "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)} for name, data in {"role_capture": capture, "role_review": review}.items()]}, {"kind": "research-role-completion", "markdown": "# Completion\n\npass\n", "sidecar": {"evidence_kind": "research-role-completion", "run_id": started["run_id"], "output_sha256": digest, "result": "pass"}}], "reason": "synthetic supplied research"})
    checkpoint_input = tmp_path / "checkpoint.json"
    checkpoint_input.write_bytes(canonical_json_bytes(checkpoint_request))
    checkpoint_result = runner.invoke(cli, ["external", "checkpoint", "--gig", gig_id, "--home", str(home), "--target", str(target), "--invocation", str(checkpoint_input), "--protocol-version", "2", "--json"])
    assert checkpoint_result.exit_code == 0, checkpoint_result.output
    checkpoint = json.loads(checkpoint_result.output)
    assert checkpoint["schema_version"] == "2.0"
    output, check = checkpoint["artifacts"]
    submit_request = _envelope("cli-submit", {"run_id": started["run_id"], "parent_checkpoint": checkpoint["checkpoint_id"], "output_refs": [{"kind": output["kind"], "markdown": output["markdown"], "sidecar": output["sidecar"], "domain_sidecar": output["domain_sidecar"], "supporting_artifacts": output["supporting_artifacts"]}], "check_refs": [check["sidecar"]], "disclosure": {"execution": "unobserved", "actor_report": "declared"}})
    submit_input = tmp_path / "submit.json"
    submit_input.write_bytes(canonical_json_bytes(submit_request))
    submitted_result = runner.invoke(cli, ["external", "submit", "--gig", gig_id, "--home", str(home), "--target", str(target), "--invocation", str(submit_input), "--protocol-version", "2", "--json"])
    assert submitted_result.exit_code == 0, submitted_result.output
    submitted = json.loads(submitted_result.output)
    assert submitted["outcome"] == "succeeded"
    replay_result = runner.invoke(cli, ["external", "submit", "--gig", gig_id, "--home", str(home), "--target", str(target), "--invocation", str(submit_input), "--protocol-version", "2", "--json"])
    assert replay_result.exit_code == 0, replay_result.output
    assert json.loads(replay_result.output) == submitted
