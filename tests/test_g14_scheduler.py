from __future__ import annotations

from pathlib import Path
import uuid

from gigai.lifecycle import approve_offline, create_offline
import pytest

from gigai.run import (
    _blocked_by_terminal_outcome,
    _critical_path,
    _ready_goals,
    _validate_scheduler_policy,
    launch_run,
    read_run_details,
    _PreScheduleFailure,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


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
