"""Probe (uat-bug-029 follow-up): is a Scout run's ``remaining_budget`` enforced?

Answer proved here: NO for scout nodes.  ``InvocationBudget.reserve`` is the only
enforcement point and only fires when a budget object reaches ``run_model_invocation``;
no Scout production path builds one, and ``remaining_budget`` in run-details is a static copy.
"""

from __future__ import annotations

import ast
from pathlib import Path

from gigai.model_execution import InvocationBudget
from gigai.run import ProposalRunRequest, TailorRunRequest

_SRC = Path(__file__).resolve().parents[3] / "src" / "gigai"


def test_run_requests_carry_no_budget_by_default() -> None:
    proposal = ProposalRunRequest(graph_selector="g", model_target="t", posting_selector={}, private_selectors=())
    tailor = TailorRunRequest(graph_selector="g", model_target="t", posting_selector={}, private_selectors=())
    assert proposal.budget is None and tailor.budget is None


def test_reserve_only_limits_when_a_budget_object_exists() -> None:
    budget = InvocationBudget(max_model_calls=3, max_tokens=12000)
    assert [budget.reserve(1000) for _ in range(4)] == [True, True, True, False]  # the mechanism works


def test_no_scout_production_code_builds_an_invocation_budget_or_reserves() -> None:
    offenders = []
    for path in sorted((_SRC / "scout").rglob("*.py")) + [_SRC / "run.py", _SRC / "cli.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in {"InvocationBudget", "reserve"}:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
