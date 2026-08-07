from __future__ import annotations

from pathlib import Path
import copy
import subprocess
import uuid

from gigai.lifecycle import approve_offline, create_offline
import pytest
from gigai.canonical import (
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
    parse_json_front_matter,
)
from gigai.journal import JournalArtifact, record_transition

from gigai.run import (
    _blocked_by_terminal_outcome,
    _critical_path,
    _ready_goals,
    _terminal_status,
    _execute_deterministic,
    _prepare_records,
    _target_observation,
    _validate_scheduler_policy,
    launch_run,
    read_run_details,
    _PreScheduleFailure,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad


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
        name="run-proof",
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


def test_sequential_scheduler_completes_every_goal_in_dependency_order(
    tmp_path: Path,
) -> None:
    home, target, gig_id = _fixture(tmp_path)
    result = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=True,
        uuid_factory=lambda: uuid.UUID(
            "00000000-0000-4000-8000-000000000030"
        ),
    )
    details = read_run_details(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        run_id=result.run_id,
    )
    assert result.status == "succeeded"
    assert details["status"] == "succeeded"
    assert all(goal["status"] == "complete" for goal in details["goals"])
    assert details["goal_sets"]["active"] == []
    assert len(list((result.workpad / "handoffs").glob("*-goal-started.txt"))) == 2
    assert len(list((result.workpad / "handoffs").glob("*-goal-completed.txt"))) == 2
    assert details["realized_max_parallel_goals"] == 1


def _goal(goal_id: str) -> dict[str, object]:
    return {"goal_id": goal_id, "activation": "automatic", "executor": {"kind": "local_capability", "capability": "gigai.offline"}, "effects": ["write_workpad"]}


def test_join_waits_for_all_exact_predecessors_and_critical_path_is_stable() -> None:
    a = "goal_00000000-0000-4000-8000-000000000001"
    b = "goal_00000000-0000-4000-8000-000000000002"
    c = "goal_00000000-0000-4000-8000-000000000003"
    graph = {"goals": [_goal(a), _goal(b), _goal(c)], "entry_goal_ids": [a, b], "terminal_goal_ids": [c], "edges": [
        {"kind": "dependency", "from_goal_id": a, "to_goal_id": c, "on_outcomes": ["COMPLETE"], "automatic": True},
        {"kind": "dependency", "from_goal_id": b, "to_goal_id": c, "on_outcomes": ["COMPLETE"], "automatic": True},
    ]}
    details = {a: {"status": "complete", "outcome": "COMPLETE"}, b: {"status": "pending", "outcome": None}, c: {"status": "pending", "outcome": None}}
    assert _ready_goals(graph, details) == [b]
    assert _critical_path(graph) == [a, c]
    details[b] = {"status": "complete", "outcome": "COMPLETE"}
    assert _ready_goals(graph, details) == [c]


def test_unsupported_parallel_policy_fails_before_scheduling() -> None:
    graph = {"aggregate_budget": {"max_parallel_goals": 2}, "failure_policy": "fail_gig", "edges": [], "goals": []}
    with pytest.raises(_PreScheduleFailure):
        _validate_scheduler_policy(graph)


def test_terminal_unlisted_outcome_blocks_dependent() -> None:
    a = "goal_00000000-0000-4000-8000-000000000001"
    b = "goal_00000000-0000-4000-8000-000000000002"
    graph = {"edges": [{"kind": "dependency", "from_goal_id": a, "to_goal_id": b, "on_outcomes": ["COMPLETE"]}]}
    details = {a: {"status": "complete", "outcome": "REJECTED"}, b: {"status": "pending", "outcome": None}}
    assert _blocked_by_terminal_outcome(graph, details) == [b]


def test_non_entry_orphan_never_becomes_ready() -> None:
    a = "goal_00000000-0000-4000-8000-000000000001"
    orphan = "goal_00000000-0000-4000-8000-000000000002"
    graph = {"goals": [_goal(a), _goal(orphan)], "entry_goal_ids": [a], "edges": []}
    details = {a: {"status": "ready", "outcome": None}, orphan: {"status": "pending", "outcome": None}}
    assert _ready_goals(graph, details) == [a]


def test_failed_goal_takes_precedence_over_blocked_dependents() -> None:
    details = {
        "goal_a": {"status": "failed"},
        "goal_b": {"status": "blocked"},
    }
    assert _terminal_status(details) == "failed"


def test_operator_gate_and_recovery_policy_reject_before_scheduling() -> None:
    base = {"aggregate_budget": {"max_parallel_goals": 1}, "failure_policy": "fail_gig", "edges": [], "goals": []}
    with pytest.raises(_PreScheduleFailure):
        _validate_scheduler_policy({**base, "goals": [{**_goal("goal_00000000-0000-4000-8000-000000000001"), "activation": "operator_gate"}]})
    with pytest.raises(_PreScheduleFailure):
        _validate_scheduler_policy({**base, "edges": [{"kind": "recovery"}]})


def test_tampered_sealed_run_graph_fails_before_goal_execution(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)

    def tamper(step: str) -> None:
        if step == "after_run_started_commit":
            run_graph = next((tmp_path / "workpads").rglob("runs/run_*/goal-graph.json"))
            run_graph.write_text("{}")

    result = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=True,
        observer=tamper,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000030"),
    )
    assert result.status == "failed"
    assert not list(result.run_path.rglob("evidence/*.txt"))


def test_scheduler_executes_three_goal_join_in_canonical_order(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    graph = parse_json_bytes((resolved.path / "manifests/goal-graph.json").read_bytes())
    proposal = parse_json_bytes((resolved.path / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(graph, dict) and isinstance(proposal, dict)
    first, second = graph["goals"]
    third = copy.deepcopy(second)
    third_id = "goal_00000000-0000-4000-8000-000000000099"
    third.update({"goal_id": third_id, "display_ordinal": "G02", "slug": "finalize", "title": "Finalize"})
    graph["goals"] = [first, second, third]
    graph["entry_goal_ids"] = [first["goal_id"], second["goal_id"]]
    graph["terminal_goal_ids"] = [third_id]
    original_edge = graph["edges"][0]
    graph["edges"] = [
        {**original_edge, "edge_id": "edge_00000000-0000-4000-8000-000000000098", "from_goal_id": first["goal_id"], "to_goal_id": third_id},
        {**original_edge, "edge_id": "edge_00000000-0000-4000-8000-000000000097", "from_goal_id": second["goal_id"], "to_goal_id": third_id},
    ]
    authority_commit = subprocess.run(
        ["git", "-C", str(resolved.path), "rev-parse", "gig-v000001"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    target_before = _target_observation(resolved)
    run_id = "run_00000000-0000-4000-8000-000000000030"
    run_path = resolved.path / "runs" / run_id
    run_path.mkdir(parents=True)
    prepared = _prepare_records(
        resolved=resolved, run_id=run_id, gig_version=1,
        authority_commit=authority_commit, graph=graph, proposal=proposal,
        target_before=target_before, invocation_argv=("gigai", "run"),
    )
    manifest_digest = digest_imported_bytes(prepared[f"runs/{run_id}/run-manifest.json"])
    started = record_transition(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id,
        handoff_id="handoff_00000000-0000-4000-8000-000000000030",
        transition="run_started", body="custom three-goal scheduler fixture",
        artifacts=tuple(JournalArtifact(path, data) for path, data in prepared.items()),
        front_matter={
            "gig_version": 1, "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "source_manifest_sha256": manifest_digest, "outcome": "SEALED",
        },
    )
    terminal = _execute_deterministic(
        resolved=resolved, run_id=run_id, gig_version=1, graph=graph,
        target_before=target_before, run_started_handoff_id=started.handoff_id,
        manifest_digest=manifest_digest,
    )
    details = parse_json_bytes((run_path / "run-details.json").read_bytes())
    assert terminal.path.name.endswith("run-succeeded.txt")
    assert details["status"] == "succeeded"
    assert [goal["goal_id"] for goal in details["goals"]] == [first["goal_id"], second["goal_id"], third_id]
    assert all(goal["status"] == "complete" for goal in details["goals"])
    started_goals = []
    for path in sorted((resolved.path / "handoffs").glob("*-goal-started.txt")):
        metadata, _body = parse_json_front_matter(path.read_bytes())
        started_goals.append(metadata["goal_id"])
    assert started_goals == [first["goal_id"], second["goal_id"], third_id]
