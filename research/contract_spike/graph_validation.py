from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class GoalGraphError(ValueError):
    pass


def validate_goal_graph(graph: dict[str, Any]) -> None:
    goals = graph["goals"]
    goal_ids = [goal["goal_id"] for goal in goals]
    if len(goal_ids) != len(set(goal_ids)):
        raise GoalGraphError("duplicate goal_id")

    known = set(goal_ids)
    entries = set(graph["entry_goal_ids"])
    terminals = set(graph["terminal_goal_ids"])
    if not entries <= known or not terminals <= known:
        raise GoalGraphError("entry or terminal goal does not exist")

    outcomes = {goal["goal_id"]: set(goal["outcomes"]) for goal in goals}
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {goal_id: 0 for goal_id in goal_ids}
    edge_ids: set[str] = set()

    for edge in graph["edges"]:
        edge_id = edge["edge_id"]
        if edge_id in edge_ids:
            raise GoalGraphError("duplicate edge_id")
        edge_ids.add(edge_id)
        source = edge["from_goal_id"]
        target = edge["to_goal_id"]
        if source not in known or target not in known:
            raise GoalGraphError("edge endpoint does not exist")
        if not set(edge["on_outcomes"]) <= outcomes[source]:
            raise GoalGraphError("edge names an outcome its source cannot produce")
        adjacency[source].append(target)
        indegree[target] += 1

    queue = deque(goal_id for goal_id, degree in indegree.items() if degree == 0)
    visited: list[str] = []
    while queue:
        goal_id = queue.popleft()
        visited.append(goal_id)
        for target in adjacency[goal_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(visited) != len(goal_ids):
        raise GoalGraphError("goal graph contains a dependency or recovery cycle")

    reachable = set(entries)
    queue = deque(entries)
    while queue:
        for target in adjacency[queue.popleft()]:
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    required = {goal["goal_id"] for goal in goals if goal["required"]}
    if not required <= reachable or not terminals <= reachable:
        raise GoalGraphError("required or terminal goal is unreachable")

    incoming_dependency_count: dict[str, int] = defaultdict(int)
    for edge in graph["edges"]:
        if edge["kind"] == "dependency":
            incoming_dependency_count[edge["to_goal_id"]] += 1
    for goal_id, count in incoming_dependency_count.items():
        if count > 1 and goal_id in entries:
            raise GoalGraphError("join goal cannot also be an entry goal")
