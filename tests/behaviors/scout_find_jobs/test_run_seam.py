from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import uuid

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.graph_node_registry import clear, register
from gigai.lifecycle import approve_offline, create_offline
from gigai.private_records import create_record, import_reference, migrate_workpad_layout
from gigai.run import (
    RunError,
    _PreScheduleFailure,
    _apply_registered_receipt_to_detail,
    _effective_goal_effects,
    _execute_goal,
    _target_observation,
    _terminal_status,
    _validate_operator_consent,
    _validate_scheduler_policy,
    launch_find_jobs_run,
    resolve_newest_resume,
)
from gigai.scout.find_jobs.contracts import (
    AcquireOutput,
    FindJobsConfig,
    ModelTarget,
    NodeReceipt,
    NodeStatus,
    RunRequest,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad


FIXTURES = Path(__file__).with_name("fixtures")


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID(
            "12345678-1234-4234-9234-123456789abc"
        ),
    )
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
        for index in range(1, 40)
    )
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="run-seam",
        open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    return home, target, created.gig_id


@pytest.fixture(autouse=True)
def _isolated_registry() -> None:
    clear()
    yield
    clear()


def _graph(goal: dict[str, object]) -> dict[str, object]:
    return {
        "graph_id": "graph_find_jobs_test",
        "graph_version": 1,
        "aggregate_budget": {"max_parallel_goals": 1},
        "failure_policy": "fail_gig",
        "edges": [],
        "goals": [goal],
    }


def _goal(*, slug: str, capability: str, effects: list[str]) -> dict[str, object]:
    return {
        "goal_id": "goal_00000000-0000-4000-8000-000000000001",
        "goal_version": 1,
        "slug": slug,
        "activation": "automatic",
        "executor": {"kind": "local_capability", "capability": capability},
        "effects": effects,
    }


def test_registered_callable_writes_dto_output_receipt_and_evidence(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    goal = _goal(slug="acquire", capability="scout.test.acquire", effects=["write_workpad"])
    graph = _graph(goal)
    calls: list[object] = []
    output = AcquireOutput.from_json(
        json.loads((FIXTURES / "fixture-acquire-batch-v1.json").read_text())
    )

    def stub(context: object, node_input: object) -> AcquireOutput:
        calls.append((context, node_input))
        return output

    register(
        "graph_find_jobs_test",
        1,
        "acquire",
        "scout.test.acquire",
        {"write_workpad"},
        stub,
    )
    run_id = "run_00000000-0000-4000-8000-000000000001"
    (resolved.path / "runs" / run_id).mkdir(parents=True)
    target_before = _target_observation(resolved)
    output_ref = _execute_goal(
        resolved,
        run_id,
        goal["goal_id"],
        target_before,
        goal=goal,
        graph=graph,
        manifest_digest="sha256:" + "0" * 64,
        started_at="2026-09-23T00:00:00+00:00",
    )
    assert calls
    assert output_ref["path"] == f"runs/{run_id}/outputs/acquire.json"
    output_path = resolved.path / "runs" / run_id / "outputs" / "acquire.json"
    receipt_path = resolved.path / "runs" / run_id / "receipts" / "acquire.json"
    assert AcquireOutput.from_json(parse_json_bytes(output_path.read_bytes())) == output
    receipt = NodeReceipt.from_json(parse_json_bytes(receipt_path.read_bytes()))
    assert receipt.status is NodeStatus.COMPLETE
    assert receipt.evidence[0].path == f"runs/{run_id}/outputs/acquire.json"

    detail: dict[str, object] = {}
    _apply_registered_receipt_to_detail(
        resolved, run_id, goal, detail, expected_status=NodeStatus.COMPLETE.value
    )
    evidence = detail["evidence"]
    assert isinstance(evidence, list)
    assert {item["path"] for item in evidence} == {
        f"runs/{run_id}/outputs/acquire.json",
        f"runs/{run_id}/receipts/acquire.json",
    }
    assert not (resolved.path / "runs" / run_id / "evidence" / f"{goal['goal_id']}.txt").exists()


def test_unregistered_capability_and_undeclared_effect_are_refused() -> None:
    unregistered = _graph(
        _goal(slug="acquire", capability="scout.missing", effects=["write_workpad"])
    )
    with pytest.raises(_PreScheduleFailure, match="executor is unsupported"):
        _validate_scheduler_policy(unregistered)

    goal = _goal(
        slug="acquire",
        capability="scout.test.acquire",
        effects=["write_workpad", "network_read"],
    )
    register(
        "graph_find_jobs_test",
        1,
        "acquire",
        "scout.test.acquire",
        {"write_workpad"},
        lambda _context, _input: None,
    )
    with pytest.raises(_PreScheduleFailure, match="undeclared effect"):
        _validate_scheduler_policy(_graph(goal))


def test_local_assess_target_narrows_effects() -> None:
    goal = _goal(
        slug="assess",
        capability="scout.test.assess",
        effects=["network_read", "credential_use", "write_workpad"],
    )
    register(
        "graph_find_jobs_test",
        1,
        "assess",
        "scout.test.assess",
        {"network_read", "credential_use", "write_workpad"},
        lambda _context, _input: None,
    )
    _validate_scheduler_policy(_graph(goal), model_target=ModelTarget.OLLAMA_LOCAL.value)
    assert _effective_goal_effects(goal, model_target=ModelTarget.OLLAMA_LOCAL.value) == {
        "write_workpad"
    }


def test_ui_consent_requires_verified_loopback() -> None:
    consent = json.loads((FIXTURES / "fixture-ui-consent-v1.json").read_text())
    with pytest.raises(RunError, match="verified loopback"):
        _validate_operator_consent(consent)
    _validate_operator_consent(consent, ui_loopback_verified=True)


def test_launch_validates_config_digest_before_resume_resolution(tmp_path: Path) -> None:
    config_payload = json.loads(
        (FIXTURES / "fixture-find-jobs-config-v1.json").read_text()
    )
    config = FindJobsConfig.from_json(config_payload)
    request = RunRequest.from_json(
        json.loads((FIXTURES / "fixture-api-run-request-v1.json").read_text())
    )
    request = replace(request, config_digest=config.digest())
    with pytest.raises(RunError, match="config digest"):
        launch_find_jobs_run(
            home_root=tmp_path / "home",
            target=tmp_path / "target",
            run_request=replace(request, config_digest="sha256:" + "f" * 64),
            config_bytes=canonical_json_bytes(config.to_json()),
            ui_loopback_verified=True,
        )


def test_no_resume_is_a_clear_error() -> None:
    with pytest.raises(RunError, match="find_jobs_resume_required"):
        resolve_newest_resume(Path("/definitely/not/a/gigai-home"), None)


def test_resolve_newest_resume_pins_committed_record_revision(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    migrate_workpad_layout(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
    )
    source = tmp_path / "resume.md"
    source.write_bytes(b"Committed resume\n")
    imported = import_reference(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        kind="resume",
        source=source,
        operation_key="run-seam-resume-import",
    )
    revision = create_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key="run-seam-resume-record",
    )
    pinned = resolve_newest_resume(home, target)
    assert pinned.record_id == revision.record_id
    assert pinned.revision_id == revision.revision_id
    assert pinned.content_sha256 == digest_imported_bytes(source.read_bytes())


def test_aggregate_complete_plus_running_is_running() -> None:
    assert _terminal_status(
        {"acquire": {"status": "complete"}, "assess": {"status": "running"}}
    ) == "running"
