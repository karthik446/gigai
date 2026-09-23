"""Standalone callable R1 entry for a saved local proposal assessment.

R4 owns CLI registration.  This module supplies a bounded library callable so
that registration cannot accidentally turn a proposal into Tailor or an
application action.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..config import GigAIConfig
from ..model_execution import InvocationBudget
from .proposal_execution import ScoutProposalExecutionError, execute_local_proposal
from .proposal_records import ProposalRevisionResult, record_proposal_revision
from ..workpad import ResolvedWorkpad


def run_saved_proposal(*, resolved: ResolvedWorkpad, config: GigAIConfig, run_id: str, goal_id: str, model_target: str, posting_selector: Mapping[str, object], private_selectors: tuple[Mapping[str, object], ...], local_allowed: bool, configured_digest: str, budget: InvocationBudget | None = None) -> ProposalRevisionResult:
    """Execute and persist one explicitly selected proposal assessment.

    Input selectors are resolved by the execution host; model output cannot
    choose records, opportunity identity, or proposal lineage.  Callers must
    provide the sealed proposal Run and local permission explicitly.
    """
    execution = execute_local_proposal(
        resolved=resolved,
        config=config,
        run_id=run_id,
        goal_id=goal_id,
        model_target=model_target,
        posting_selector=posting_selector,
        private_selectors=private_selectors,
        local_allowed=local_allowed,
        budget=budget,
    )
    return record_proposal_revision(
        resolved=resolved,
        execution=execution,
        posting_selector=posting_selector,
        private_selectors=private_selectors,
        model_target=model_target,
        configured_digest=configured_digest,
    )


__all__ = ["run_saved_proposal", "ScoutProposalExecutionError"]
