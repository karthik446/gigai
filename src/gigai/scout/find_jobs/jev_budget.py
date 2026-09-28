"""What Jev may cost in a day, and what it has cost today.

Operator (UAT 2026-09-28): Jev had spent $1.00, too much. The per-run cost
cap bounds one ranking pass; nothing bounded a day of runs, of ``POST
/rank`` calls and of pages that ranked on their own. This module is the
bound: every Jev call that was paid for is written to a ledger under the
home, and a pass stops asking once today's total reaches the daily budget.

Ledger: ``<home>/cache/scout/jev/spend/<YYYY-MM-DD>.jsonl``, one line per
paid call, ``{"at", "cost_usd", "where", "run_id"}``; never a posting, a
resume or a key. It is the home's, not a project's: the budget holds
across every project that shares the key. A day is the machine's local
day. Lines are appended (one short write each), so a run's child process
and the server can both write; a line that cannot be read is skipped.

Budget: ``daily_budget_usd(home_root)`` is the ONE place it is read:
``GIGAI_JEV_DAILY_BUDGET_USD`` when set to a number that is not negative,
else ``DEFAULT_DAILY_BUDGET_USD``. A setting stored with the preferences
has its place here and nowhere else.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path

DEFAULT_DAILY_BUDGET_USD = 0.50
DAILY_BUDGET_ENV = "GIGAI_JEV_DAILY_BUDGET_USD"


def format_usd(value: float) -> str:
    """A configured amount as it was set: ``0.25`` -> ``"0.25"``, ``0.125`` -> ``"0.125"``."""

    text = f"{value:.4f}".rstrip("0")
    whole, _, cents = text.partition(".")
    return f"{whole}.{cents.ljust(2, '0')}"


def format_cost(value: float) -> str:
    """A spent amount in cents; below one cent, to four places: ``"0.23"``, ``"0.0005"``."""

    if value <= 0:
        return "0.00"
    if value < 0.01:
        return format_usd(value)
    return f"{value:.2f}"


def daily_budget_usd(home_root: Path | None = None) -> float:
    """The most Jev may cost in one day, in USD. ``home_root`` is where a stored setting would be read."""

    raw = os.environ.get(DAILY_BUDGET_ENV)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = -1.0
        if value >= 0:
            return value
    return DEFAULT_DAILY_BUDGET_USD


def _today() -> date:
    return datetime.now().astimezone().date()


def spend_dir(home_root: Path) -> Path:
    return Path(home_root) / "cache" / "scout" / "jev" / "spend"


def _ledger_path(home_root: Path, day: date) -> Path:
    return spend_dir(home_root) / f"{day.isoformat()}.jsonl"


def record_spend(home_root: Path, cost_usd: float, *, where: str, run_id: str | None = None, day: date | None = None) -> None:
    """Append one paid call to today's ledger. A call that cost nothing is not written."""

    if not cost_usd > 0:
        return
    path = _ledger_path(home_root, day or _today())
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "cost_usd": f"{cost_usd:.6f}",
            "where": where,
            "run_id": run_id,
        },
        separators=(",", ":"),
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def spent_today_usd(home_root: Path, *, day: date | None = None) -> float:
    """The sum of today's ledger; 0 when there is none."""

    path = _ledger_path(home_root, day or _today())
    if path.is_symlink() or not path.is_file():
        return 0.0
    total = 0.0
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return 0.0
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
            total += float(entry["cost_usd"])
        except (ValueError, TypeError, KeyError):
            continue
    return total


def usage_line(spent_usd: float, budget_usd: float) -> str:
    """``Jev: $0.31 of $0.50 today``."""

    return f"Jev: ${format_cost(spent_usd)} of ${format_usd(budget_usd)} today"


def usage(home_root: Path) -> dict[str, object]:
    """Today's Jev spend and the daily budget, as an API answers it."""

    spent = spent_today_usd(home_root)
    budget = daily_budget_usd(home_root)
    return {
        "day": _today().isoformat(),
        "spent_today_usd": f"{spent:.6f}",
        "daily_budget_usd": format_usd(budget),
        "remaining_usd": f"{max(0.0, budget - spent):.6f}",
        "budget_reached": spent >= budget,
        "line": usage_line(spent, budget),
    }


__all__ = [
    "DAILY_BUDGET_ENV",
    "DEFAULT_DAILY_BUDGET_USD",
    "daily_budget_usd",
    "format_cost",
    "format_usd",
    "record_spend",
    "spend_dir",
    "spent_today_usd",
    "usage",
    "usage_line",
]
