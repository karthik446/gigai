import asyncio

import pytest

from ..planning import (
    CaseRequired,
    PlanRun,
    result_driven_workflow,
    review_workflow,
)


def test_review_plan_resolves_input_driven_branch_without_case() -> None:
    run = PlanRun()

    asyncio.run(review_workflow(run, challenge=True))

    assert [(call.kind, call.name) for call in run.calls] == [
        ("tool", "project-checks"),
        ("model", "reviewer"),
        ("model", "challenger"),
        ("tool", "recommendations"),
    ]
    assert run.calls[1].dependencies == ("01-project-checks",)
    assert run.calls[2].dependencies == ("02-reviewer",)
    assert run.calls[3].dependencies == ("02-reviewer", "03-challenger")


def test_result_driven_branch_requires_rehearsal_case() -> None:
    run = PlanRun()

    with pytest.raises(CaseRequired, match="field access 'failed'.*01-project-checks"):
        asyncio.run(result_driven_workflow(run))
