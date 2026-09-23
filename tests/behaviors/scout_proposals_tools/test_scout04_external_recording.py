from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from gigai import external_recording
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.lifecycle import approve_offline, create_offline, propose_graph_set_offline
from gigai.private_records import (
    create_record,
    import_reference,
    import_run_input,
    migrate_workpad_layout,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_serialized_contract
from tests.behaviors.scout_proposals_tools.test_scout02_graph_set_flow import _write_definition


_PROJECT = "project_00000000-0000-4000-8000-000000000001"
_GIG = "gig_00000000-0000-4000-8000-000000000001"
_PLAN = "run_plan_00000000-0000-4000-8000-000000000001"
_RUN = "run_00000000-0000-4000-8000-000000000001"
_REF = {
    "path": "runs/example.json",
    "content_sha256": "sha256:" + "0" * 64,
    "media_type": "application/json",
    "size_bytes": 1,
}


def external_schema_fixtures() -> dict[str, dict[str, object]]:
    """Pure fixture inventory for central schema-validation integration."""
    invocation = {
        "schema_version": "1.0",
        "invocation_id": "inv_00000000-0000-4000-8000-000000000001",
        "operation": "plan",
        "project_id": _PROJECT,
        "gig_id": _GIG,
        "origin": "direct_cli",
        "actor": {"kind": "operator", "id": "local-user"},
        "input": {},
        "operation_key": "fixture",
        "payload_sha256": "sha256:" + "1" * 64,
        "created_at": "2026-09-08T00:00:00Z",
    }
    invocation["input"] = {
        "graph_selector": "research-role",
        "gig_version": None,
        "selection_record": None,
        "input_refs": [
            {
                "family": "g45_run_input",
                "id": "input_00000000-0000-4000-8000-000000000001",
            }
        ],
        "output_kinds": ["report"],
        "predecessor": None,
    }
    plan = {
        "schema_version": "1.0",
        "run_plan_id": _PLAN,
        "mode": "external_agent_recording",
        "project_id": _PROJECT,
        "gig_id": _GIG,
        "gig_version": 1,
        "journal_commit": "a" * 40,
        "graph_set": _REF,
        "selected_graph_id": "research-role",
        "goal_graph_id": "graph_00000000-0000-4000-8000-000000000001",
        "selected_graph": _REF,
        "selection_record": _REF,
        "invocation": invocation,
        "inputs": [{"family": "g45_run_input"}],
        "output_contract": _REF,
        "check_contract": _REF,
        "effects": ["write_workpad"],
        "limits": {},
        "predecessor": None,
        "state": "sealed",
        "created_at": "2026-09-08T00:00:00Z",
        "sealed_at": "2026-09-08T00:00:00Z",
    }
    plan["inputs"] = [
        {
            "family": "g45_run_input",
            "run_input_id": "input_00000000-0000-4000-8000-000000000001",
            "record_ref": _REF,
            "snapshot_ref": _REF,
        }
    ]
    plan["limits"] = {
        "max_envelope_bytes": 262144,
        "max_artifact_bytes": 1048576,
        "max_artifacts_per_operation": 32,
        "max_total_bytes_per_operation": 4194304,
        "max_checkpoint_questions": 32,
        "max_checkpoints_per_run": 256,
    }
    run = {
        "schema_version": "1.0",
        "run_id": _RUN,
        "run_plan": _REF,
        "invocation": invocation,
        "mode": "external_agent_recording",
        "project_id": _PROJECT,
        "gig_id": _GIG,
        "gig_version": 1,
        "status": "active",
        "started_at": "2026-09-08T00:00:00Z",
    }
    checkpoint = {
        "schema_version": "1.0",
        "checkpoint_id": "checkpoint_00000000-0000-4000-8000-000000000001",
        "run_id": _RUN,
        "run_plan": _REF,
        "invocation": invocation,
        "sequence": 1,
        "parent_checkpoint": None,
        "questions": [],
        "artifacts": [],
        "reason": "fixture",
        "created_at": "2026-09-08T00:00:00Z",
    }
    receipt = {
        "schema_version": "1.0",
        "receipt_id": "receipt_00000000-0000-4000-8000-000000000001",
        "run_id": _RUN,
        "run_plan": _REF,
        "invocation": invocation,
        "operation_key": "fixture",
        "payload_sha256": "sha256:" + "2" * 64,
        "outcome": "cancelled",
        "outputs": [],
        "checks": [],
        "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        "created_at": "2026-09-08T00:00:00Z",
    }
    return {
        "external-recording-invocation.schema.json": invocation,
        "external-recording-plan.schema.json": plan,
        "external-recording-run.schema.json": run,
        "external-recording-checkpoint.schema.json": checkpoint,
        "external-recording-receipt.schema.json": receipt,
    }


def _ref(
    path: str, data: bytes, media_type: str = "application/json"
) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


def test_external_schema_fixture_inventory_is_valid() -> None:
    for schema, fixture in external_schema_fixtures().items():
        assert validate_serialized_contract(
            schema, canonical_json_bytes(fixture)
        ).valid, schema


@pytest.mark.parametrize(
    ("schema", "mutate"),
    [
        (
            "external-recording-invocation.schema.json",
            lambda value: value["input"].pop("graph_selector"),
        ),
        (
            "external-recording-invocation.schema.json",
            lambda value: value.update(
                origin="agent_invocation",
                actor={"kind": "operator", "id": "local-user"},
            ),
        ),
        (
            "external-recording-plan.schema.json",
            lambda value: value.update(invocation={}, inputs=[{}], limits={}),
        ),
        (
            "external-recording-run.schema.json",
            lambda value: value["invocation"]["input"].update(extra=True),
        ),
        (
            "external-recording-checkpoint.schema.json",
            lambda value: value.update(parent_checkpoint="checkpoint_not-a-uuid"),
        ),
        (
            "external-recording-receipt.schema.json",
            lambda value: value["disclosure"].update(provider="claimed"),
        ),
    ],
)
def test_external_schemas_reject_nested_adversarial_payloads(schema, mutate) -> None:
    payload = deepcopy(external_schema_fixtures()[schema])
    mutate(payload)
    assert not validate_serialized_contract(schema, canonical_json_bytes(payload)).valid


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, Path, str]:
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="external-recording",
        open_editor=False,
    )
    approve_offline(
        home_root=home, requested_target=target, proposal_id=created.proposal_id
    )
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    proposed = propose_graph_set_offline(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        definition_path=_write_definition(
            tmp_path / "definition", workpad, created.gig_id
        ),
    )
    approve_offline(
        home_root=home, requested_target=target, proposal_id=proposed.proposal_id
    )
    migrate_workpad_layout(
        workpad=workpad, project_id=created.project_id, gig_id=created.gig_id
    )
    imported = import_run_input(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        data=b"# Posting\n\nExplicit job description.\n",
    )
    return home, target, created.gig_id, workpad, imported.item_id


def _envelope(operation_key: str, typed_input: dict[str, object]) -> dict[str, object]:
    return {
        "origin": "direct_cli",
        "actor": {"kind": "operator", "id": "local-user"},
        "input": typed_input,
        "operation_key": operation_key,
    }


def test_external_plan_start_checkpoint_and_missing_submit_are_journaled(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _selection, input_id = _fixture(tmp_path)
    plan = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "plan-1",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{"family": "g45_run_input", "id": input_id}],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        ),
    )
    assert plan.created and plan.payload["mode"] == "external_agent_recording"
    assert validate_serialized_contract(
        "external-recording-plan.schema.json",
        __import__(
            "gigai.canonical", fromlist=["canonical_json_bytes"]
        ).canonical_json_bytes(plan.payload),
    ).valid
    replay = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "plan-1",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{"family": "g45_run_input", "id": input_id}],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        ),
    )
    assert (
        not replay.created
        and replay.payload["run_plan_id"] == plan.payload["run_plan_id"]
    )
    started = external_recording.start(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("start-1", {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    checkpoint = external_recording.checkpoint(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "checkpoint-1",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [
                    {
                        "id": "experience",
                        "state": "answered",
                        "prompt": "Do you have this experience?",
                    }
                ],
                "artifact_refs": [],
                "reason": "partial output",
            },
        ),
    )
    assert checkpoint.payload["sequence"] == 1
    with pytest.raises(
        external_recording.ExternalRecordingError, match="required outputs"
    ) as missing:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "submit-1",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [],
                    "check_refs": [],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert missing.value.code == "external_output_missing"
    inspected = external_recording.inspect(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        run_id=started.payload["run_id"],
    )
    assert len(inspected["checkpoints"]) == 1


def test_external_rejects_agent_origin_claiming_direct_cli_actor(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _selection, _input_id = _fixture(tmp_path)
    with pytest.raises(external_recording.ExternalRecordingError) as invalid:
        external_recording.start(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope={
                "origin": "agent_invocation",
                "actor": {"kind": "operator", "id": "local-user"},
                "input": {
                    "run_plan_id": "run_plan_00000000-0000-4000-8000-000000000001"
                },
                "operation_key": "bad-origin",
            },
        )
    assert invalid.value.code == "external_invocation_invalid"


def test_replay_conflict_checkpoint_cas_terminal_and_agent_origin(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _workpad, input_id = _fixture(tmp_path)
    request = {
        "graph_selector": "career",
        "gig_version": None,
        "selection_record": None,
        "input_refs": [{"family": "g45_run_input", "id": input_id}],
        "output_kinds": ["report"],
        "predecessor": None,
    }
    plan = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("same-plan", request),
    )
    changed = dict(request, output_kinds=["different"])
    with pytest.raises(external_recording.ExternalRecordingError) as conflict:
        external_recording.plan(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope("same-plan", changed),
        )
    assert conflict.value.code == "external_operation_conflict"
    started = external_recording.start(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("start-cas", {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    with pytest.raises(external_recording.ExternalRecordingError) as bad_parent:
        external_recording.checkpoint(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "bad-parent",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": "checkpoint_00000000-0000-4000-8000-000000000001",
                    "questions": [],
                    "artifact_refs": [],
                    "reason": "wrong parent",
                },
            ),
        )
    assert bad_parent.value.code == "external_checkpoint_conflict"
    cancelled = external_recording.cancel(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "cancel-1",
            {
                "run_id": started.payload["run_id"],
                "reason": "operator stopped recording",
            },
        ),
    )
    assert cancelled.payload["outcome"] == "cancelled"
    cancel_replay = external_recording.cancel(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "cancel-1",
            {
                "run_id": started.payload["run_id"],
                "reason": "operator stopped recording",
            },
        ),
    )
    assert (
        not cancel_replay.created
        and cancel_replay.payload["receipt_id"] == cancelled.payload["receipt_id"]
    )
    with pytest.raises(external_recording.ExternalRecordingError) as terminal:
        external_recording.checkpoint(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "after-cancel",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": None,
                    "questions": [],
                    "artifact_refs": [],
                    "reason": "not allowed",
                },
            ),
        )
    assert terminal.value.code == "external_run_terminal"
    agent_started = external_recording.start(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope={
            "origin": "agent_invocation",
            "actor": {"kind": "agent", "id": "codex", "session_id": "opaque-session"},
            "input": {"run_plan_id": plan.payload["run_plan_id"]},
            "operation_key": "agent-start",
        },
    )
    assert agent_started.payload["invocation"]["origin"] == "agent_invocation"


def test_requirements_returns_agent_readable_contracts_and_missing_question_needs_successor(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _workpad, input_id = _fixture(tmp_path)
    details = external_recording.requirements(
        home_root=home, requested_target=target, gig_id=gig_id, graph_selector="career"
    )
    assert details["input_contract"]["content"]["kind"] == "run_input_contract"
    plan = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "plan-question",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{"family": "g45_run_input", "id": input_id}],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        ),
    )
    started = external_recording.start(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "start-question", {"run_plan_id": plan.payload["run_plan_id"]}
        ),
    )
    checkpoint = external_recording.checkpoint(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "question",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [
                    {
                        "id": "harness",
                        "state": "missing",
                        "prompt": "Unsupported harness-engineering experience?",
                    }
                ],
                "artifact_refs": [],
                "reason": "candidate evidence is missing",
            },
        ),
    )
    assert (
        external_recording.inspect(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            run_id=started.payload["run_id"],
        )["run"]["status"]
        == "waiting_input"
    )
    with pytest.raises(external_recording.ExternalRecordingError) as successor:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "submit-question",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [
                        {
                            "kind": "report",
                            "markdown": {
                                "path": "runs/missing.md",
                                "content_sha256": "sha256:" + "0" * 64,
                                "media_type": "text/markdown",
                                "size_bytes": 1,
                            },
                            "sidecar": {
                                "path": "runs/missing.json",
                                "content_sha256": "sha256:" + "0" * 64,
                                "media_type": "application/json",
                                "size_bytes": 1,
                            },
                        }
                    ],
                    "check_refs": [],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert successor.value.code == "external_successor_required"
    successor_action = successor.value.result()["error"]["next_action"]
    assert successor_action["predecessor"] == {
        "kind": "checkpoint",
        "run_id": started.payload["run_id"],
        "checkpoint_id": checkpoint.payload["checkpoint_id"],
    }
    assert successor_action["missing_inputs"] == [
        {
            "id": "harness",
            "prompt": "Unsupported harness-engineering experience?",
            "state": "missing",
        }
    ]


def test_completion_requires_contract_bound_output_and_check(tmp_path: Path) -> None:
    home, target, gig_id, _workpad, input_id = _fixture(tmp_path)
    with pytest.raises(external_recording.ExternalRecordingError) as made_up_kind:
        external_recording.plan(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "made-up-output",
                {
                    "graph_selector": "career",
                    "gig_version": None,
                    "selection_record": None,
                    "input_refs": [{"family": "g45_run_input", "id": input_id}],
                    "output_kinds": ["made_up"],
                    "predecessor": None,
                },
            ),
        )
    assert made_up_kind.value.code == "external_authority_mismatch"
    plan = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "completion-plan",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{"family": "g45_run_input", "id": input_id}],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        ),
    )
    started = external_recording.start(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "completion-start", {"run_plan_id": plan.payload["run_plan_id"]}
        ),
    )
    document = "# Role summary\n"
    sidecar = {
        "run_id": started.payload["run_id"],
        "selected_inputs": plan.payload["inputs"],
        "output_kind": "report",
        "document_sha256": digest_imported_bytes(document.encode()),
    }
    check_document = "# Proposal validation\n"
    check_sidecar = {
        "evidence_kind": "proposal-validation",
        "run_id": started.payload["run_id"],
        "output_sha256": sidecar["document_sha256"],
        "result": "fail",
    }
    checkpoint = external_recording.checkpoint(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "completion-checkpoint",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [],
                "artifact_refs": [
                    {"kind": "report", "markdown": document, "sidecar": sidecar},
                    {
                        "kind": "proposal-validation",
                        "markdown": check_document,
                        "sidecar": check_sidecar,
                    },
                ],
                "reason": "completed output",
            },
        ),
    )
    output = checkpoint.payload["artifacts"][0]
    check = checkpoint.payload["artifacts"][1]
    for ordinal, mutation in enumerate(
        (
            lambda sidecar: sidecar.pop("result"),
            lambda sidecar: sidecar.update(result="unknown"),
        ),
        start=1,
    ):
        malformed = deepcopy(checkpoint.payload["invocation"])
        mutation(malformed["input"]["artifact_refs"][1]["sidecar"])
        assert not validate_serialized_contract(
            "external-recording-invocation.schema.json",
            canonical_json_bytes(malformed),
        ).valid
        with pytest.raises(external_recording.ExternalRecordingError) as invalid_result:
            external_recording.checkpoint(
                home_root=home,
                requested_target=target,
                gig_id=gig_id,
                envelope=_envelope(
                    f"completion-invalid-result-{ordinal}",
                    {
                        "run_id": started.payload["run_id"],
                        "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                        "questions": [],
                        "artifact_refs": [
                            {
                                "kind": "proposal-validation",
                                "markdown": "# Invalid declared result\n",
                                "sidecar": malformed["input"]["artifact_refs"][1][
                                    "sidecar"
                                ],
                            }
                        ],
                        "reason": "must reject malformed check result",
                    },
                ),
            )
        assert invalid_result.value.code == "external_invocation_invalid"
    with pytest.raises(external_recording.ExternalRecordingError) as no_check:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "completion-no-check",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [output],
                    "check_refs": [],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert no_check.value.code == "external_output_missing"
    with pytest.raises(external_recording.ExternalRecordingError) as output_as_check:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "completion-output-as-check",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [output],
                    "check_refs": [output["sidecar"]],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert output_as_check.value.code == "external_output_invalid"
    with pytest.raises(external_recording.ExternalRecordingError) as swapped_output:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "completion-swapped-output",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [
                        {
                            "kind": "report",
                            "markdown": output["markdown"],
                            "sidecar": check["sidecar"],
                        }
                    ],
                    "check_refs": [check["sidecar"]],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert swapped_output.value.code == "external_output_invalid"
    with pytest.raises(external_recording.ExternalRecordingError) as changed_kind:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "completion-changed-kind",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [
                        {
                            "kind": "proposal-validation",
                            "markdown": output["markdown"],
                            "sidecar": output["sidecar"],
                        }
                    ],
                    "check_refs": [check["sidecar"]],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert changed_kind.value.code == "external_output_invalid"
    with pytest.raises(external_recording.ExternalRecordingError) as duplicate_output:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "completion-duplicate-output",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [output, output],
                    "check_refs": [check["sidecar"]],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert duplicate_output.value.code == "external_output_invalid"
    with pytest.raises(external_recording.ExternalRecordingError) as failed_check:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "completion-failed-check",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                    "output_refs": [output],
                    "check_refs": [check["sidecar"]],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert failed_check.value.code == "external_output_invalid"
    corrected_sidecar = dict(check_sidecar, result="pass")
    corrected = external_recording.checkpoint(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "completion-corrected-check",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                "questions": [],
                "artifact_refs": [
                    {
                        "kind": "proposal-validation",
                        "markdown": "# Corrected proposal validation\n",
                        "sidecar": corrected_sidecar,
                    }
                ],
                "reason": "corrected declared check result",
            },
        ),
    )
    corrected_check = corrected.payload["artifacts"][0]
    with pytest.raises(external_recording.ExternalRecordingError) as mixed_checks:
        external_recording.submit(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "completion-mixed-checks",
                {
                    "run_id": started.payload["run_id"],
                    "parent_checkpoint": corrected.payload["checkpoint_id"],
                    "output_refs": [output],
                    "check_refs": [check["sidecar"], corrected_check["sidecar"]],
                    "disclosure": {
                        "execution": "unobserved",
                        "actor_report": "declared",
                    },
                },
            ),
        )
    assert mixed_checks.value.code == "external_output_invalid"
    complete = external_recording.submit(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "completion-submit",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": corrected.payload["checkpoint_id"],
                "output_refs": [output],
                "check_refs": [corrected_check["sidecar"]],
                "disclosure": {"execution": "unobserved", "actor_report": "declared"},
            },
        ),
    )
    assert complete.payload["outcome"] == "succeeded"
    replay = external_recording.submit(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "completion-submit",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": corrected.payload["checkpoint_id"],
                "output_refs": [output],
                "check_refs": [corrected_check["sidecar"]],
                "disclosure": {"execution": "unobserved", "actor_report": "declared"},
            },
        ),
    )
    assert (
        not replay.created
        and replay.payload["receipt_id"] == complete.payload["receipt_id"]
    )
    for schema, emitted in {
        "external-recording-plan.schema.json": plan.payload,
        "external-recording-run.schema.json": started.payload,
        "external-recording-checkpoint.schema.json": checkpoint.payload,
        "external-recording-receipt.schema.json": complete.payload,
    }.items():
        assert validate_serialized_contract(
            schema, canonical_json_bytes(emitted)
        ).valid, schema
        assert validate_serialized_contract(
            "external-recording-invocation.schema.json",
            canonical_json_bytes(emitted["invocation"]),
        ).valid, schema


def test_agent_reuses_authenticated_selection_for_distinct_plan_key(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _workpad, input_id = _fixture(tmp_path)
    direct = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "selection-direct",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{"family": "g45_run_input", "id": input_id}],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        ),
    )
    direct_second = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "selection-direct-second",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [
                    {"family": "g45_run_input", "id": input_id},
                    {
                        "family": "g45_run_input",
                        "id": import_run_input(
                            home_root=home,
                            requested_target=target,
                            gig_id=gig_id,
                            data=b"Second selected input.",
                        ).item_id,
                    },
                ],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        ),
    )
    assert direct_second.created
    assert (
        direct_second.payload["selection_record"] == direct.payload["selection_record"]
    )
    assert direct_second.payload["run_plan_id"] != direct.payload["run_plan_id"]
    agent = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope={
            "origin": "agent_invocation",
            "actor": {
                "kind": "agent",
                "id": "codex",
                "session_id": "sealed-selection-session",
            },
            "operation_key": "selection-agent",
            "input": {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": direct.payload["selection_record"],
                "input_refs": [{"family": "g45_run_input", "id": input_id}],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        },
    )
    assert agent.payload["selection_record"] == direct.payload["selection_record"]


def test_all_admitted_g45_input_families_revalidate_at_start(tmp_path: Path) -> None:
    home, target, gig_id, _workpad, run_input_id = _fixture(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text("# Resume\n\nBounded local fixture.\n", encoding="utf-8")
    reference = import_reference(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        kind="resume",
        source=source,
    )
    wrapper = create_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=reference.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key="external-wrapper",
    )
    inputs = [
        {"family": "g45_run_input", "id": run_input_id},
        {"family": "g45_reference", "id": reference.item_id},
        {
            "family": "scout_record",
            "record_id": wrapper.record_id,
            "revision_id": wrapper.revision_id,
        },
    ]
    for index, input_ref in enumerate(inputs):
        plan = external_recording.plan(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                f"input-family-{index}",
                {
                    "graph_selector": "career",
                    "gig_version": None,
                    "selection_record": None,
                    "input_refs": [input_ref],
                    "output_kinds": ["report"],
                    "predecessor": None,
                },
            ),
        )
        started = external_recording.start(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                f"input-family-start-{index}",
                {"run_plan_id": plan.payload["run_plan_id"]},
            ),
        )
        assert started.created

    with pytest.raises(external_recording.ExternalRecordingError) as malformed:
        external_recording.plan(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                "foreign-input",
                {
                    "graph_selector": "career",
                    "gig_version": None,
                    "selection_record": None,
                    "input_refs": [
                        {
                            "family": "g45_reference",
                            "id": "ref_00000000-0000-4000-8000-000000000099",
                        }
                    ],
                    "output_kinds": ["report"],
                    "predecessor": None,
                },
            ),
        )
    assert malformed.value.code == "external_record_not_found"


def test_waiting_input_can_continue_unchanged_or_require_a_pinned_successor(
    tmp_path: Path,
) -> None:
    home, target, gig_id, _workpad, original_input = _fixture(tmp_path)
    plan = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "waiting-plan",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{"family": "g45_run_input", "id": original_input}],
                "output_kinds": ["report"],
                "predecessor": None,
            },
        ),
    )
    started = external_recording.start(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "waiting-start", {"run_plan_id": plan.payload["run_plan_id"]}
        ),
    )
    missing = external_recording.checkpoint(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "waiting-missing",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [
                    {"id": "proof", "state": "missing", "prompt": "Need proof?"}
                ],
                "artifact_refs": [],
                "reason": "missing answer",
            },
        ),
    )
    continued = external_recording.checkpoint(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "waiting-answer",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": missing.payload["checkpoint_id"],
                "questions": [
                    {"id": "proof", "state": "answered", "prompt": "Need proof?"}
                ],
                "artifact_refs": [],
                "reason": "user-reported answer recorded without changing inputs",
            },
        ),
    )
    assert continued.payload["sequence"] == 2
    assert (
        external_recording.inspect(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            run_id=started.payload["run_id"],
        )["run"]["status"]
        == "active"
    )
    answer_input = import_run_input(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        data=b"User-reported answer: proof supplied.",
    )
    successor = external_recording.plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "waiting-successor",
            {
                "graph_selector": "career",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [
                    {"family": "g45_run_input", "id": original_input},
                    {"family": "g45_run_input", "id": answer_input.item_id},
                ],
                "output_kinds": ["report"],
                "predecessor": {
                    "kind": "checkpoint",
                    "run_id": started.payload["run_id"],
                    "checkpoint_id": continued.payload["checkpoint_id"],
                },
            },
        ),
    )
    successor_run = external_recording.start(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "waiting-successor-start", {"run_plan_id": successor.payload["run_plan_id"]}
        ),
    )
    assert successor_run.created
    old = external_recording.inspect(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        run_id=started.payload["run_id"],
    )
    assert [item["checkpoint_id"] for item in old["checkpoints"]] == [
        missing.payload["checkpoint_id"],
        continued.payload["checkpoint_id"],
    ]
