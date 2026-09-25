"""Spend cap enforcement + running tally for the Exa Agent spike (S23).

Hard caps, both enforced BEFORE a call is allowed to start:

- PER_RUN_CAP_DOLLARS = $2.00 (a run's own worst-case cost must not exceed
  this; for fixed efforts the cost is exact, for auto/max the run's own
  ``budget.maxCostDollars`` is set to this cap so Exa itself cannot charge
  more).
- TOTAL_CAP_DOLLARS = $30.00 (cumulative worst-case spend across every call
  this spike has made, tracked in ``spend.jsonl``).

Every accepted call appends one JSON line to ``spend.jsonl``:
``{"ts": iso8601, "run_label": str, "effort": str, "cap_dollars": float,
"cost_dollars": float, "cumulative_dollars": float, "reported": bool}``.
``reported=False`` means ``cost_dollars`` is the worst-case estimate charged
*before* the call (fixed-effort price, or the run's own cap for auto/max);
after a call completes, ``record_actual`` appends a second line with
``reported=True`` and the real ``costDollars`` Exa returned, so the tally
file is auditable both ways. The S23 doc's metrics table is reconciled
against a recount of this file, not against script output.

No line in this file, and no fixture this spike saves, may ever contain the
API key or an auth header value -- see ``redact.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PER_RUN_CAP_DOLLARS = 2.00
TOTAL_CAP_DOLLARS = 30.00

SPEND_FILE = Path(__file__).parent / "spend.jsonl"

# Fixed-effort prices, from https://exa.ai/pricing and
# https://exa.ai/docs/reference/agent-api-guide (read 2026-09-23; matches
# the brief's FACTS section exactly).
FIXED_EFFORT_COST_DOLLARS: dict[str, float] = {
    "minimal": 0.012,
    "low": 0.025,
    "medium": 0.10,
    "high": 0.50,
    "xhigh": 1.00,
}
# auto/max are metered ($0.10/ACU + $0.005/search); worst case is whatever
# budget.maxCostDollars caps the run at, which this spike always sets to
# PER_RUN_CAP_DOLLARS or below for auto/max requests.
METERED_EFFORTS = {"auto", "max"}


class SpendCapExceeded(RuntimeError):
    """Raised when a call would exceed the per-run or total cap."""


@dataclass(frozen=True)
class SpendDecision:
    run_label: str
    effort: str
    cap_dollars: float
    worst_case_cost_dollars: float
    cumulative_before_dollars: float
    cumulative_after_dollars: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_lines() -> list[dict[str, object]]:
    if not SPEND_FILE.exists():
        return []
    lines: list[dict[str, object]] = []
    for raw in SPEND_FILE.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            lines.append(json.loads(raw))
    return lines


def current_total_dollars() -> float:
    """Recount cumulative worst-case spend from the tally file itself.

    Only ``reported=False`` (pre-call, worst-case) lines are summed --
    ``reported=True`` actual-cost lines are informational and would
    double-count against the worst-case reservation already made for that
    call.
    """

    total = 0.0
    for line in _read_lines():
        if line.get("reported", False):
            continue
        cost = line["cost_dollars"]
        assert isinstance(cost, (int, float))
        total += float(cost)
    return total


def worst_case_cost_dollars(effort: str, cap_dollars: float | None) -> float:
    """The worst-case dollar cost of one call at ``effort``.

    Fixed efforts have an exact, documented price. Metered efforts
    (auto/max) have no fixed price -- their worst case is whatever
    ``budget.maxCostDollars`` the request itself sets, so a cap is
    required for those and this raises if one isn't given.
    """

    if effort in FIXED_EFFORT_COST_DOLLARS:
        return FIXED_EFFORT_COST_DOLLARS[effort]
    if effort in METERED_EFFORTS:
        if cap_dollars is None:
            raise SpendCapExceeded(
                f"effort={effort!r} is metered; a budget.maxCostDollars cap is required"
            )
        return cap_dollars
    raise SpendCapExceeded(f"unknown effort {effort!r}")


def check_and_reserve(run_label: str, effort: str, cap_dollars: float | None = None) -> SpendDecision:
    """Refuse (raise) or reserve worst-case spend for one call, appending a tally line.

    Call this BEFORE issuing the HTTP request. Raises ``SpendCapExceeded``
    without writing anything if the call would break either cap.
    """

    worst_case = worst_case_cost_dollars(effort, cap_dollars)
    if worst_case > PER_RUN_CAP_DOLLARS + 1e-9:
        raise SpendCapExceeded(
            f"{run_label}: worst-case cost ${worst_case:.3f} exceeds the ${PER_RUN_CAP_DOLLARS:.2f} per-run cap"
        )
    before = current_total_dollars()
    after = before + worst_case
    if after > TOTAL_CAP_DOLLARS + 1e-9:
        raise SpendCapExceeded(
            f"{run_label}: would bring cumulative spend to ${after:.3f}, over the ${TOTAL_CAP_DOLLARS:.2f} total cap "
            f"(currently ${before:.3f})"
        )
    line = {
        "ts": _now_iso(),
        "run_label": run_label,
        "effort": effort,
        "cap_dollars": cap_dollars if cap_dollars is not None else FIXED_EFFORT_COST_DOLLARS.get(effort),
        "cost_dollars": worst_case,
        "cumulative_dollars": after,
        "reported": False,
    }
    with SPEND_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
    return SpendDecision(
        run_label=run_label,
        effort=effort,
        cap_dollars=line["cap_dollars"],  # type: ignore[arg-type]
        worst_case_cost_dollars=worst_case,
        cumulative_before_dollars=before,
        cumulative_after_dollars=after,
    )


def record_actual(run_label: str, effort: str, actual_cost_dollars: float) -> None:
    """Append the real ``costDollars`` Exa reported for a completed run.

    Informational only -- does not affect ``current_total_dollars()``,
    which sums worst-case reservations so the guard stays conservative even
    if this is never called (e.g. the run fails before completion).
    """

    line = {
        "ts": _now_iso(),
        "run_label": run_label,
        "effort": effort,
        "cap_dollars": None,
        "cost_dollars": actual_cost_dollars,
        "cumulative_dollars": None,
        "reported": True,
    }
    with SPEND_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


__all__ = [
    "FIXED_EFFORT_COST_DOLLARS",
    "METERED_EFFORTS",
    "PER_RUN_CAP_DOLLARS",
    "SpendCapExceeded",
    "SpendDecision",
    "TOTAL_CAP_DOLLARS",
    "check_and_reserve",
    "current_total_dollars",
    "record_actual",
    "worst_case_cost_dollars",
]
