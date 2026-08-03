"""Prototype dependency-aware placeholders for zero-effect workflow planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CaseRequired(RuntimeError):
    """The workflow inspected an unresolved result and needs fixture values."""


@dataclass(frozen=True)
class PlannedCall:
    call_id: str
    kind: str
    name: str
    dependencies: tuple[str, ...]


class PlanValue:
    """Opaque result of a planned call.

    It may flow into later calls, but Python cannot inspect it without a case.
    """

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id

    def _unresolved(self, operation: str) -> CaseRequired:
        return CaseRequired(
            f"{operation} requires the result of {self.call_id}; "
            "run rehearsal with a case"
        )

    def __bool__(self) -> bool:
        raise self._unresolved("boolean branch")

    def __iter__(self):
        raise self._unresolved("iteration")

    def __len__(self) -> int:
        raise self._unresolved("length")

    def __getattr__(self, name: str) -> Any:
        raise self._unresolved(f"field access {name!r}")

    def __eq__(self, other: object) -> bool:
        raise self._unresolved("comparison")

    def __repr__(self) -> str:
        return f"<planned:{self.call_id}>"


def _dependencies(value: Any) -> set[str]:
    if isinstance(value, PlanValue):
        return {value.call_id}
    if isinstance(value, dict):
        out: set[str] = set()
        for key, item in value.items():
            out.update(_dependencies(key))
            out.update(_dependencies(item))
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        out = set()
        for item in value:
            out.update(_dependencies(item))
        return out
    return set()


class PlanRun:
    def __init__(self) -> None:
        self.calls: list[PlannedCall] = []

    def _record(self, kind: str, name: str, value: Any) -> PlanValue:
        call_id = f"{len(self.calls) + 1:02d}-{name}"
        self.calls.append(
            PlannedCall(
                call_id=call_id,
                kind=kind,
                name=name,
                dependencies=tuple(sorted(_dependencies(value))),
            )
        )
        return PlanValue(call_id)

    async def tool(self, name: str, value: Any) -> PlanValue:
        return self._record("tool", name, value)

    async def model(self, role: str, value: Any) -> PlanValue:
        return self._record("model", role, value)


async def review_workflow(run: PlanRun, *, challenge: bool) -> PlanValue:
    """Input-driven branching can be fully planned without fixture values."""

    checks = await run.tool("project-checks", {"kind": "code"})
    findings = await run.model("reviewer", {"checks": checks})
    if challenge:
        challenged = await run.model("challenger", findings)
        return await run.tool(
            "recommendations",
            {"findings": findings, "challenges": challenged},
        )
    return await run.tool("recommendations", {"findings": findings})


async def result_driven_workflow(run: PlanRun) -> PlanValue:
    """A branch that inspects a planned result honestly requires a case."""

    checks = await run.tool("project-checks", {"kind": "code"})
    if checks.failed:
        return await run.model("failure-reviewer", checks)
    return await run.model("reviewer", checks)
