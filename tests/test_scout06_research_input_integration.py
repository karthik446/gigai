from __future__ import annotations

import base64
from copy import deepcopy
from importlib import import_module

import pytest

from gigai import external_recording
from gigai.canonical import digest_imported_bytes
from gigai.external_recording import ExternalRecordingError
from tests.test_scout06_research_inputs import _complete, _env, _research


def test_v2_plan_selects_completed_research_run_and_replays_exact_input(tmp_path) -> None:
    home, target, gig_id, resolved, _snapshot, selector, _old_plan, _started, _receipt = _complete(tmp_path)
    request = {
        "graph_selector": "research-role",
        "gig_version": None,
        "selection_record": None,
        "input_refs": [
            {"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"},
            deepcopy(selector),
        ],
        "output_kinds": ["research"],
        "predecessor": None,
    }
    first = external_recording.plan_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("reuse-research", request),
    )
    assert first.created is True
    selected = [item for item in first.payload["inputs"] if item.get("family") == "scout_research"]
    assert len(selected) == 1
    assert selected[0]["run_id"] == selector["run_id"]
    assert selected[0]["receipt_id"] == selector["receipt_id"]
    assert "selected_inputs" not in selected[0]
    newer = external_recording.plan_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("newer-role-research", {**request, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Newer context"}]}),
    )
    external_recording.start_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("newer-role-research-start", {"run_plan_id": newer.payload["run_plan_id"]}),
    )
    replay = external_recording.plan_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("reuse-research", request),
    )
    assert replay.created is False
    assert replay.payload == first.payload
    started = external_recording.start_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("reuse-research-start", {"run_plan_id": first.payload["run_plan_id"]}),
    )
    assert started.created is True
    assert started.payload["schema_version"] == "2.0"


def test_research_selector_is_v2_only_and_historical_refusal_is_typed(tmp_path) -> None:
    home, target, gig_id, resolved, _snapshot, selector, _old_plan, _started, _receipt = _complete(tmp_path)
    with pytest.raises(ExternalRecordingError) as refused:
        external_recording.plan(
            home_root=home, requested_target=target, gig_id=gig_id,
            envelope=_env("v1-research", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [selector], "output_kinds": ["research"], "predecessor": None}),
        )
    assert refused.value.code == "external_invocation_invalid"
    changed = dict(selector)
    changed["receipt_id"] = "receipt_00000000-0000-4000-8000-000000000099"
    head_before_missing = external_recording._resolved(
        home_root=home, requested_target=target, gig_id=gig_id
    ).path
    head_before_missing = __import__("subprocess").check_output(
        ["git", "-C", str(head_before_missing), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(ExternalRecordingError) as missing:
        external_recording.plan_v2(
            home_root=home, requested_target=target, gig_id=gig_id,
            envelope=_env("missing-research", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"}, changed], "output_kinds": ["research"], "predecessor": None}),
        )
    assert missing.value.code == "external_record_not_found"
    head_after_missing = __import__("subprocess").check_output(
        ["git", "-C", str(external_recording._resolved(home_root=home, requested_target=target, gig_id=gig_id).path), "rev-parse", "HEAD"], text=True
    ).strip()
    assert head_after_missing == head_before_missing
    cancelled_plan = external_recording.plan_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("cancelled-source-plan", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Another context"}], "output_kinds": ["research"], "predecessor": None}),
    )
    cancelled_run = external_recording.start_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("cancelled-source-start", {"run_plan_id": cancelled_plan.payload["run_plan_id"]}),
    )
    cancelled = external_recording.cancel_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("cancelled-source-cancel", {"run_id": cancelled_run.payload["run_id"], "reason": "synthetic"}),
    )
    cancelled_head = __import__("subprocess").check_output(
        ["git", "-C", str(external_recording._resolved(home_root=home, requested_target=target, gig_id=gig_id).path), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(ExternalRecordingError) as terminal:
        external_recording.plan_v2(
            home_root=home, requested_target=target, gig_id=gig_id,
            envelope=_env("cancelled-research", {"graph_selector": "research-role", "gig_version": None, "selection_record": None, "input_refs": [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Customer delivery"}, {"family": "scout_research", "run_id": cancelled_run.payload["run_id"], "receipt_id": cancelled.payload["receipt_id"], "output_kind": "research"}], "output_kinds": ["research"], "predecessor": None}),
        )
    assert terminal.value.code == "external_record_not_found"
    assert __import__("subprocess").check_output(
        ["git", "-C", str(external_recording._resolved(home_root=home, requested_target=target, gig_id=gig_id).path), "rev-parse", "HEAD"], text=True
    ).strip() == cancelled_head


def test_second_run_reuses_completed_research_through_checkpoint_and_submit(tmp_path) -> None:
    home, target, gig_id, _resolved, _snapshot, selector, _old_plan, _started, _receipt = _complete(tmp_path)
    request = {
        "graph_selector": "research-role",
        "gig_version": None,
        "selection_record": None,
        "input_refs": [
            {"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Second run context"},
            deepcopy(selector),
        ],
        "output_kinds": ["research"],
        "predecessor": None,
    }
    second_plan = external_recording.plan_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("second-research-plan", request),
    )
    selected = [item for item in second_plan.payload["inputs"] if item.get("family") == "scout_research"]
    assert len(selected) == 1
    assert selected[0]["run_id"] == selector["run_id"]
    assert selected[0]["receipt_id"] == selector["receipt_id"]
    assert selected[0]["output"]["kind"] == "research"
    assert selected[0]["output"]["markdown"]["path"].startswith("runs/")
    historical_output_refs = deepcopy(selected[0]["output"])

    # The approved candidate maps this domain to the inventoried .076 source
    # paths; the broker authenticates those committed bytes before validation.
    contract_path = str(second_plan.payload["output_contract"]["path"])
    resolved = external_recording._resolved(home_root=home, requested_target=target, gig_id=gig_id)
    contract = external_recording._json(
        resolved.path.joinpath(contract_path).read_bytes(),
        "external_authority_mismatch",
    )
    domain = contract["domains"]["research"]
    assert domain["schema_ref"]["path"].endswith("cap_00000000-0000-4000-8000-000000000076/research.schema.json")
    assert domain["validator_source_ref"]["path"].endswith("cap_00000000-0000-4000-8000-000000000076/research.py")
    schema_ref, schema_bytes = external_recording._committed_ref(
        resolved, domain["schema_ref"], "external_authority_mismatch"
    )
    source_ref, source_bytes = external_recording._committed_ref(
        resolved, domain["validator_source_ref"], "external_authority_mismatch"
    )
    assert schema_ref["content_sha256"] == digest_imported_bytes(schema_bytes)
    assert source_ref["content_sha256"] == digest_imported_bytes(source_bytes)

    second_started = external_recording.start_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("second-research-start", {"run_plan_id": second_plan.payload["run_plan_id"]}),
    )
    renderer = import_module("gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000076.research")
    capture, review = b"Synthetic role capture.", b"Synthetic independent review."
    head_before_output = __import__("subprocess").check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()
    packet = renderer.build_research_packet(
        project_id=second_plan.payload["project_id"], gig_id=gig_id,
        gig_version=second_plan.payload["gig_version"], graph_id=second_plan.payload["goal_graph_id"],
        graph_version=1, run_id=second_started.payload["run_id"],
        selected_inputs=second_plan.payload["inputs"], research=_research(),
        artifact_bytes={"role_capture": capture, "role_review": review},
    )
    assert __import__("subprocess").check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip() == head_before_output
    digest = digest_imported_bytes(packet.markdown)
    output = {
        "kind": "research",
        "markdown": packet.markdown.decode(),
        "sidecar": {
            "document_sha256": digest,
            "output_kind": "research",
            "run_id": second_started.payload["run_id"],
            "selected_inputs": second_plan.payload["inputs"],
        },
        "domain_sidecar": {
            "schema_id": "urn:gigai:scout:research-packet:3",
            "value": packet.sidecar,
        },
        "supporting_artifacts": [
            {
                "artifact_id": name,
                "media_type": "text/plain",
                "content_base64": base64.b64encode(data).decode(),
                "content_sha256": digest_imported_bytes(data),
                "size_bytes": len(data),
            }
            for name, data in {"role_capture": capture, "role_review": review}.items()
        ],
    }
    check = {
        "kind": "research-role-completion",
        "markdown": "# Second completion\n\npass\n",
        "sidecar": {
            "evidence_kind": "research-role-completion",
            "run_id": second_started.payload["run_id"],
            "output_sha256": digest,
            "result": "pass",
        },
    }
    checkpoint = external_recording.checkpoint_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("second-research-checkpoint", {
            "run_id": second_started.payload["run_id"], "parent_checkpoint": None,
            "questions": [], "artifact_refs": [output, check], "reason": "second run historical research reuse",
        }),
    )
    assert checkpoint.created is True
    assert len(checkpoint.payload["artifacts"]) == 2
    submitted = external_recording.submit_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("second-research-submit", {
            "run_id": second_started.payload["run_id"],
            "parent_checkpoint": checkpoint.payload["checkpoint_id"],
            "output_refs": [checkpoint.payload["artifacts"][0]],
            "check_refs": [checkpoint.payload["artifacts"][1]["sidecar"]],
            "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        }),
    )
    assert submitted.created is True
    assert submitted.payload["outcome"] == "succeeded"
    assert submitted.payload["outputs"][0]["kind"] == "research"
    assert submitted.payload["outputs"][0]["markdown"] == checkpoint.payload["artifacts"][0]["markdown"]
    replay = external_recording.submit_v2(
        home_root=home, requested_target=target, gig_id=gig_id,
        envelope=_env("second-research-submit", {
            "run_id": second_started.payload["run_id"],
            "parent_checkpoint": checkpoint.payload["checkpoint_id"],
            "output_refs": [checkpoint.payload["artifacts"][0]],
            "check_refs": [checkpoint.payload["artifacts"][1]["sidecar"]],
            "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        }),
    )
    assert replay.created is False
    assert replay.payload == submitted.payload
    assert selected[0]["run_id"] == selector["run_id"] != second_started.payload["run_id"]
    assert output["sidecar"]["selected_inputs"] == second_plan.payload["inputs"]
    assert selected[0]["output"] == historical_output_refs


@pytest.mark.parametrize("mutation", ["changed", "foreign"], ids=["changed-bytes", "foreign-run"])
def test_research_reuse_changed_or_foreign_history_refuses_before_publication(tmp_path, mutation) -> None:
    home, target, gig_id, resolved, _snapshot, selector, _old_plan, _started, receipt = _complete(tmp_path)
    if mutation == "changed":
        path = resolved.path / receipt["outputs"][0]["markdown"]["path"]
        path.write_bytes(path.read_bytes() + b"\nworking-copy drift\n")
    else:
        selector = {**selector, "run_id": "run_00000000-0000-4000-8000-000000000099"}
    head_before = __import__("subprocess").check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(Exception) as refused:
        external_recording.plan_v2(
            home_root=home, requested_target=target, gig_id=gig_id,
            envelope=_env("reuse-invalid-history", {
                "graph_selector": "research-role", "gig_version": None,
                "selection_record": None,
                "input_refs": [
                    {"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": "Reuse"},
                    selector,
                ],
                "output_kinds": ["research"], "predecessor": None,
            }),
        )
    if mutation == "foreign":
        assert getattr(refused.value, "code", None) == "external_record_not_found"
    else:
        assert str(refused.value) == "authoritative workpad has uncommitted divergence"
    assert __import__("subprocess").check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip() == head_before
