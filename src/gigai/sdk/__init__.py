"""What gig code imports from gigai.

The eval record types and the ``eval run`` contracts (``Score``, ``ScoredTrace``, ``Scorer``, ``Postprocess`` and the
lifted scorers) load on first use (module ``__getattr__``), so importing ``gigai.sdk``, which the CLI does, imports
neither ``gigai.evals.records`` nor ``gigai.evals.contracts``. Traces are written and replayed through the context:
``ctx.trace`` and ``ctx.replay`` name this gig's own eval store; nothing here takes a home or a gig name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .context import GigContext

if TYPE_CHECKING:
    from ..evals.contracts import (
        CORRECT,
        INCORRECT,
        NOANSWER,
        PARTIAL,
        Postprocess,
        Score,
        ScoredTrace,
        Scorer,
        includes,
        lifted,
        match,
        pattern,
    )
    from ..evals.records import (
        Dataset,
        ItemRef,
        ModelCall,
        Prereg,
        PromptRef,
        ReplayRow,
        RunRecord,
        ScoreRecord,
        Settings,
        TraceRecord,
        model_call,
        new_id,
    )
    from ..steps import StepTargetError

_RECORDS = frozenset(
    {
        "Dataset",
        "ItemRef",
        "ModelCall",
        "Prereg",
        "PromptRef",
        "ReplayRow",
        "RunRecord",
        "ScoreRecord",
        "Settings",
        "TraceRecord",
        "model_call",
        "new_id",
    }
)
_CONTRACTS = frozenset(
    {
        "CORRECT",
        "INCORRECT",
        "NOANSWER",
        "PARTIAL",
        "Postprocess",
        "Score",
        "ScoredTrace",
        "Scorer",
        "includes",
        "lifted",
        "match",
        "pattern",
    }
)


def __getattr__(name: str) -> Any:
    if name in _RECORDS:
        from ..evals import records

        return getattr(records, name)
    if name in _CONTRACTS:
        from ..evals import contracts

        return getattr(contracts, name)
    if name == "StepTargetError":
        from ..steps import StepTargetError

        return StepTargetError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CORRECT",
    "INCORRECT",
    "NOANSWER",
    "PARTIAL",
    "Dataset",
    "GigContext",
    "ItemRef",
    "ModelCall",
    "Postprocess",
    "Prereg",
    "PromptRef",
    "ReplayRow",
    "RunRecord",
    "Score",
    "ScoreRecord",
    "ScoredTrace",
    "Scorer",
    "Settings",
    "StepTargetError",
    "TraceRecord",
    "includes",
    "lifted",
    "match",
    "model_call",
    "new_id",
    "pattern",
]
