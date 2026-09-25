"""test-gap-001: the three checks every journey runs when it finishes.

``assert_clean_and_healthy`` bundles all three so a journey test calls one
function instead of repeating the same tail every time:

1. ``assert_managed_workpad_clean`` (``tests/support/workpad_assertions.py``)
   -- the exact ``git status --porcelain --untracked-files=all`` check
   ``read_index``/``gigai doctor`` runs; a real find-jobs run through HTTP
   must never leave the workpad dirty (regression-001/-r2).
2. doctor's ``journal.index`` check, via ``gigai.diagnostics.run_doctor`` --
   the same call ``gigai doctor`` makes; must report PASS.
3. A generous per-route latency budget (default budgets below, scaled per
   ``tests/support/latency.py`` for CI), checked by the journey itself via
   ``LatencyBudget``/``timed_request``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from gigai.diagnostics import run_doctor

from tests.support.latency import latency_bound
from tests.support.workpad_assertions import assert_managed_workpad_clean


# Generous wall-time budget for most routes -- against a REAL spawned child
# process (not an in-process fake server thread), each request pays real
# HTTP + process-scheduling overhead on top of the handler's own work; under
# heavy concurrent load on a dev machine (several parallel workers, each
# running their own real subprocess-based suite in this same worktree) this
# suite observed anywhere from 0.7s to 7.4s for a plain GET
# /api/runs/{id}/results, so 1s (the ticket's literal "/api/config < 1s"
# example) is far too tight as a *general* per-route default, and even 5s
# was seen to flake under contention. 15s stays "generous" in the ticket's
# sense -- an order of magnitude over the slowest observed latency -- while
# still catching a genuine regression (a route that starts blocking on
# something it shouldn't, e.g. a live network call slipping past a seam,
# which would take far longer than 15s against a closed port). /api/config
# keeps its own explicit tighter budget (CONFIG_ROUTE_LATENCY_BUDGET_SECONDS)
# because the ticket names that exact number; uat-bug-008 (78fcbf2) landed
# this, so it is a hard assertion again -- both budgets are scaled by
# ``tests.support.latency.latency_bound`` (ci-fix-pr37-r2), so a noisy
# shared CI runner (observed 1.3s here for a 1.0s laptop bound in unrelated
# runs) gets more room via ``GIGAI_TEST_LATENCY_SCALE`` without loosening
# the bound that actually catches a regression on a dev machine.
DEFAULT_LATENCY_BUDGET_SECONDS = 15.0
CONFIG_ROUTE_LATENCY_BUDGET_SECONDS = 1.0


@dataclass
class LatencyBudget:
    """Records one route's elapsed time against its budget; asserts on read."""

    route: str
    elapsed_seconds: float
    budget_seconds: float = DEFAULT_LATENCY_BUDGET_SECONDS

    def assert_within_budget(self) -> None:
        bound = latency_bound(self.budget_seconds)
        assert self.elapsed_seconds < bound, (
            f"{self.route} took {self.elapsed_seconds:.3f}s, over its "
            f"{bound:.1f}s budget ({self.budget_seconds:.1f}s x "
            f"{bound / self.budget_seconds:.1f} CI scale)"
        )


def timed_request(route: str, call, *, budget_seconds: float = DEFAULT_LATENCY_BUDGET_SECONDS):
    """Call ``call()`` (a zero-arg thunk issuing one HTTP request), returning
    ``(response, LatencyBudget)``. The caller asserts the response as usual
    and separately asserts the latency budget -- kept as two assertions so a
    latency failure's message never hides a status-code failure's."""

    started = time.monotonic()
    response = call()
    elapsed = time.monotonic() - started
    return response, LatencyBudget(route, elapsed, budget_seconds)


def assert_doctor_journal_index_passes(home: Path) -> None:
    report = run_doctor(home)
    checks = {check.id: check for check in report.checks}
    assert "journal.index" in checks, f"doctor produced no journal.index check: {sorted(checks)}"
    assert checks["journal.index"].status == "PASS", checks["journal.index"]


def assert_clean_and_healthy(workpad: Path, home: Path) -> None:
    """The two structural after-journey checks (workpad clean + doctor).

    Latency is asserted per-request by the journey itself (via
    ``timed_request``/``LatencyBudget``), not bundled here, since it must be
    measured around each individual call, not after the fact.
    """

    assert_managed_workpad_clean(workpad)
    assert_doctor_journal_index_passes(home)


__all__ = [
    "CONFIG_ROUTE_LATENCY_BUDGET_SECONDS",
    "DEFAULT_LATENCY_BUDGET_SECONDS",
    "LatencyBudget",
    "assert_clean_and_healthy",
    "assert_doctor_journal_index_passes",
    "timed_request",
]
