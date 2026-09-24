"""Spend cap enforcement + running tally for the discovery bake-off (S24).

Same pattern as ``research/exa_agent_spike/spend_guard.py`` (S23), reused
rather than rewritten, with this spike's own caps and its own tally file:

- PER_CALL_CAP_DOLLARS = $2.00 (a call's own worst-case cost must not exceed
  this).
- TOTAL_CAP_DOLLARS = $10.00 (cumulative worst-case spend across every call
  this bake-off makes, tracked in ``spend.jsonl`` -- separate from S23's
  ``research/exa_agent_spike/spend.jsonl``, which is untouched by this file).

Every accepted call appends one JSON line to ``spend.jsonl``:
``{"ts": iso8601, "candidate": str, "run_label": str, "cap_dollars": float,
"cost_dollars": float, "cumulative_dollars": float, "reported": bool}``.
``reported=False`` means ``cost_dollars`` is the worst-case estimate charged
*before* the call; after a call completes, ``record_actual`` appends a
second line with ``reported=True`` and the real cost, so the tally file is
auditable both ways -- the bake-off doc's comparison table is reconciled
against a recount of this file, not against script output.

No line in this file, and no fixture this bake-off saves, may ever contain
an API key or auth header value -- see ``redact.py`` (also reused from S23,
unmodified, imported via sys.path).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PER_CALL_CAP_DOLLARS = 2.00
TOTAL_CAP_DOLLARS = 10.00

SPEND_FILE = Path(__file__).parent / "spend.jsonl"


class SpendCapExceeded(RuntimeError):
    """Raised when a call would exceed the per-call or total cap."""


@dataclass(frozen=True)
class SpendDecision:
    candidate: str
    run_label: str
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
    double-count against the worst-case reservation already made.
    """

    total = 0.0
    for line in _read_lines():
        if line.get("reported", False):
            continue
        cost = line["cost_dollars"]
        assert isinstance(cost, (int, float))
        total += float(cost)
    return total


def check_and_reserve(candidate: str, run_label: str, worst_case_cost_dollars: float) -> SpendDecision:
    """Refuse (raise) or reserve worst-case spend for one call, appending a tally line.

    Call this BEFORE issuing the HTTP request, with the caller's own
    worst-case cost estimate for that specific call (fixed price if the
    provider publishes one, else the tightest cap the request itself sets).
    Raises ``SpendCapExceeded`` without writing anything if the call would
    break either cap.
    """

    if worst_case_cost_dollars > PER_CALL_CAP_DOLLARS + 1e-9:
        raise SpendCapExceeded(
            f"{candidate}/{run_label}: worst-case cost ${worst_case_cost_dollars:.4f} "
            f"exceeds the ${PER_CALL_CAP_DOLLARS:.2f} per-call cap"
        )
    before = current_total_dollars()
    after = before + worst_case_cost_dollars
    if after > TOTAL_CAP_DOLLARS + 1e-9:
        raise SpendCapExceeded(
            f"{candidate}/{run_label}: would bring cumulative spend to ${after:.4f}, "
            f"over the ${TOTAL_CAP_DOLLARS:.2f} total cap (currently ${before:.4f})"
        )
    line = {
        "ts": _now_iso(),
        "candidate": candidate,
        "run_label": run_label,
        "cap_dollars": worst_case_cost_dollars,
        "cost_dollars": worst_case_cost_dollars,
        "cumulative_dollars": after,
        "reported": False,
    }
    with SPEND_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
    return SpendDecision(
        candidate=candidate,
        run_label=run_label,
        cap_dollars=worst_case_cost_dollars,
        worst_case_cost_dollars=worst_case_cost_dollars,
        cumulative_before_dollars=before,
        cumulative_after_dollars=after,
    )


def record_actual(candidate: str, run_label: str, actual_cost_dollars: float) -> None:
    """Append the real cost a provider reported (or $0 for a free call) for a completed run.

    Informational only -- does not affect ``current_total_dollars()``, which
    sums worst-case reservations so the guard stays conservative even if
    this is never called (e.g. the call fails after being reserved).
    """

    line = {
        "ts": _now_iso(),
        "candidate": candidate,
        "run_label": run_label,
        "cap_dollars": None,
        "cost_dollars": actual_cost_dollars,
        "cumulative_dollars": None,
        "reported": True,
    }
    with SPEND_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


__all__ = [
    "PER_CALL_CAP_DOLLARS",
    "SpendCapExceeded",
    "SpendDecision",
    "TOTAL_CAP_DOLLARS",
    "check_and_reserve",
    "current_total_dollars",
    "record_actual",
]
