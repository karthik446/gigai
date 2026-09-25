"""Compute per-run metrics from a fixture + its check_boards output, shared across candidates.

Same metric definitions as S23 (``research/exa_agent_spike/`` did not have a
committed ``metrics.py``, despite its README describing one -- see the S24
doc's Non-claims. This module fills that gap for both spikes' definitions,
recomputed from rows, not assumed):

- ``cost``: from ``spend.jsonl``'s ``reported: true`` actual-cost line(s)
  for that run label.
- ``latency``: from the fixture's own ``latency_seconds``.
- ``companies``: count of companies returned/derived.
- ``new_pct``: share of companies not in the exclusion list (case-
  insensitive substring match against the full 23-name exclusion list).
- ``usable_pct``: share with ``is_usable_board=True`` in the paired
  ``check_boards.py`` output (>=1 matching US posting on a real poll).
- ``grounded_pct``: share with a non-empty ``sponsorship_evidence`` AND
  ``source`` field.
- ``dollars_per_new_usable_board``: cost / (count of companies that are
  BOTH new AND usable) -- the primary metric, matching S23's definition
  exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EXCLUSION_FILE = Path(__file__).parent / "inputs" / "exclusion_full.json"


def load_exclusion() -> set[str]:
    names = json.loads(EXCLUSION_FILE.read_text(encoding="utf-8"))
    return {n.strip().lower() for n in names}


def is_new(company_name: str, exclusion: set[str]) -> bool:
    name = (company_name or "").strip().lower()
    if not name:
        return False
    return not any(excluded in name or name in excluded for excluded in exclusion)


@dataclass(frozen=True)
class RunMetrics:
    run_label: str
    cost_dollars: float
    latency_seconds: float
    companies_count: int
    new_count: int
    new_pct: float
    usable_count: int
    usable_pct: float
    grounded_count: int
    grounded_pct: float
    new_and_usable_count: int
    dollars_per_new_usable_board: float | None


def compute_run_metrics(
    run_label: str,
    cost_dollars: float,
    latency_seconds: float,
    companies: list[dict],
    boardcheck: list[dict] | None,
    exclusion: set[str],
) -> RunMetrics:
    """``companies`` rows must each have company/sponsorship_evidence/source keys.

    ``boardcheck`` is the parallel list from ``check_boards.check_many`` (as
    dicts), matched to ``companies`` by list position -- callers must pass
    them in the same order they were generated.
    """

    n = len(companies)
    new_flags = [is_new(c.get("company", ""), exclusion) for c in companies]
    new_count = sum(new_flags)
    grounded_count = sum(
        1 for c in companies if (c.get("sponsorship_evidence") or "").strip() and (c.get("source") or "").strip()
    )
    if boardcheck is not None:
        usable_flags = [bc.get("is_usable_board", False) for bc in boardcheck]
    else:
        usable_flags = [False] * n
    usable_count = sum(usable_flags)
    new_and_usable = sum(1 for is_n, is_u in zip(new_flags, usable_flags) if is_n and is_u)

    return RunMetrics(
        run_label=run_label,
        cost_dollars=cost_dollars,
        latency_seconds=latency_seconds,
        companies_count=n,
        new_count=new_count,
        new_pct=(new_count / n * 100) if n else 0.0,
        usable_count=usable_count,
        usable_pct=(usable_count / n * 100) if n else 0.0,
        grounded_count=grounded_count,
        grounded_pct=(grounded_count / n * 100) if n else 0.0,
        new_and_usable_count=new_and_usable,
        dollars_per_new_usable_board=(cost_dollars / new_and_usable) if new_and_usable else None,
    )
