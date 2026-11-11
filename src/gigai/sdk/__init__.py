"""What gig code imports from gigai.

The eval record types load on first use (module ``__getattr__``), so importing ``gigai.sdk``, which the CLI does,
does not import ``gigai.evals.records``. Traces are written and replayed through the context: ``ctx.trace`` and
``ctx.replay`` name this gig's own eval store; nothing here takes a home or a gig name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .context import GigContext

if TYPE_CHECKING:
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


def __getattr__(name: str) -> Any:
    if name in _RECORDS:
        from ..evals import records

        return getattr(records, name)
    if name == "StepTargetError":
        from ..steps import StepTargetError

        return StepTargetError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Dataset",
    "GigContext",
    "ItemRef",
    "ModelCall",
    "Prereg",
    "PromptRef",
    "ReplayRow",
    "RunRecord",
    "ScoreRecord",
    "Settings",
    "StepTargetError",
    "TraceRecord",
    "model_call",
    "new_id",
]
