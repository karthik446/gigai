"""Weekly ``discover companies`` engine — the interface contract for S2-A/S2-B.

Public surface (frozen by the S2-A/S2-B interface contract,
``.orchestrator/workers/s2a-discovery-engine.md``):

- ``run_discovery(*, home_root, target, prefs, runs=3, on_progress=None) -> DiscoveryResult``
- ``latest_discovery(*, home_root, target) -> DiscoveryResult | None``
- ``load_prefs`` / ``save_prefs`` (re-exported from ``prefs.py``)
- ``DiscoveryPrefs``, ``DiscoveryResult``

Runs the two S24-recommended sources (OpenAI ``web_search`` + the H-1B
baseline) together, merges/verifies/watchlists the results (``merge.py``),
and never raises for a provider error -- every failure is recorded in
``DiscoveryResult.sources[].error`` instead.

Scout-only package: core never imports this (``[[gigai_gig_architecture_rule]]``
-- gigs import core, core never imports a gig; this module is deeper still,
Scout-internal only, not imported by anything outside ``find_jobs``).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import httpx

from ..watchlist import list_active
from . import h1b_source, openai_source
from .merge import add_usable_boards_to_watchlist, merge_and_verify, normalize_company
from .prefs import DiscoveryPrefs, DiscoveryPrefsError, load_prefs, save_prefs
from .storage import atomic_write, runs_dir
from .types import Candidate, SourceRunOutcome

_H1B_PROBE_DEFAULT_TOP_N = 200
_H1B_PROBE_TOP_N_ENV_VAR = "GIGAI_DISCOVERY_H1B_PROBE_TOP_N"


def _h1b_probe_top_n() -> int:
    """Bounded-N override for the smoke test / operator tuning; default 200 (S24)."""

    raw = os.environ.get(_H1B_PROBE_TOP_N_ENV_VAR)
    if raw is None:
        return _H1B_PROBE_DEFAULT_TOP_N
    try:
        value = int(raw)
    except ValueError:
        return _H1B_PROBE_DEFAULT_TOP_N
    return value if value > 0 else _H1B_PROBE_DEFAULT_TOP_N


class DiscoveryBudgetExceeded(RuntimeError):
    """Raised before any spend when the worst case exceeds ``prefs.budget_usd_per_session``."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DiscoveryResult:
    discovery_id: str
    status: str  # "running" | "succeeded" | "failed" | "partial"
    started_at: str
    finished_at: str | None
    cost_usd: float
    sources: tuple[SourceRunOutcome, ...]
    new_boards: tuple[dict[str, object], ...]
    skipped: dict[str, int]

    def to_json(self) -> dict[str, object]:
        return {
            "discovery_id": self.discovery_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cost_usd": round(self.cost_usd, 6),
            "sources": [s.to_json() for s in self.sources],
            "new_boards": list(self.new_boards),
            "skipped": dict(self.skipped),
        }

    @classmethod
    def from_json(cls, obj: object) -> "DiscoveryResult":
        if type(obj) is not dict:
            raise ValueError("discovery result must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        sources = tuple(
            SourceRunOutcome(
                name=str(s["name"]),
                runs=int(s["runs"]),  # type: ignore[arg-type]
                cost_usd=float(s["cost_usd"]),  # type: ignore[arg-type]
                candidates=(),
                error=s.get("error"),  # type: ignore[arg-type]
                skip_reason=s.get("skip_reason"),  # type: ignore[arg-type]
            )
            for s in value.get("sources", [])  # type: ignore[union-attr]
        )
        return cls(
            discovery_id=str(value["discovery_id"]),
            status=str(value["status"]),
            started_at=str(value["started_at"]),
            finished_at=value.get("finished_at"),  # type: ignore[arg-type]
            cost_usd=float(value["cost_usd"]),  # type: ignore[arg-type]
            sources=sources,
            new_boards=tuple(value.get("new_boards", [])),  # type: ignore[arg-type]
            skipped=dict(value.get("skipped", {})),  # type: ignore[arg-type]
        )


def _result_path(home_root: Path, target: Path, discovery_id: str) -> Path:
    return runs_dir(home_root, target) / f"{discovery_id}.json"


def _save_result(home_root: Path, target: Path, result: DiscoveryResult) -> None:
    atomic_write(
        _result_path(home_root, target, result.discovery_id),
        json.dumps(result.to_json(), indent=2, sort_keys=True).encode("utf-8"),
    )


def latest_discovery(*, home_root: Path, target: Path) -> DiscoveryResult | None:
    """Return the most recently started discovery run for this project, if any."""

    directory = runs_dir(home_root, target)
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob("*.json"))
    if not candidates:
        return None
    latest_path = max(candidates, key=lambda p: p.stat().st_mtime)
    return DiscoveryResult.from_json(json.loads(latest_path.read_text(encoding="utf-8")))


def _worst_case_session_cost(runs: int) -> float:
    model = openai_source.resolve_model()
    per_call = openai_source.worst_case_cost_usd(model)
    return per_call * runs  # H-1B source is always free ($0)


def _exclusion_set(home_root: Path, target: Path, prefs: DiscoveryPrefs) -> set[str]:
    names: set[str] = set()
    for entry in list_active(home_root, target):
        names.add(normalize_company(entry.company))
    for name in prefs.exclude_companies:
        names.add(normalize_company(name))
    return names


def run_discovery(
    *,
    home_root: Path,
    target: Path,
    prefs: DiscoveryPrefs,
    runs: int = 3,
    on_progress: Callable[[dict], None] | None = None,
) -> DiscoveryResult:
    """Run one discovery session: OpenAI ``web_search`` x ``runs`` + the H-1B baseline once.

    Synchronous; may take ~5-30 minutes. Never raises for a provider error
    (missing key, rate limit, transport failure) -- those are recorded in
    the returned result's ``sources[].error``/``skip_reason``. Raises
    :class:`DiscoveryBudgetExceeded` (before any spend) if the worst-case
    cost for ``runs`` OpenAI calls exceeds ``prefs.budget_usd_per_session``.
    """

    discovery_id = f"discovery_{uuid.uuid4()}"
    started_at = _now()

    worst_case = _worst_case_session_cost(runs)
    if worst_case > prefs.budget_usd_per_session:
        raise DiscoveryBudgetExceeded(
            f"worst-case session cost ${worst_case:.4f} exceeds budget_usd_per_session "
            f"${prefs.budget_usd_per_session:.4f} for {runs} run(s); lower --runs or raise the budget"
        )

    def _progress(event: dict) -> None:
        if on_progress is not None:
            on_progress(event)

    _progress({"stage": "discovery_start", "discovery_id": discovery_id, "runs": runs})

    exclusions = _exclusion_set(home_root, target, prefs)

    all_candidates: list[Candidate] = []
    source_outcomes: list[SourceRunOutcome] = []

    with httpx.Client(timeout=openai_source.REQUEST_TIMEOUT_SECONDS) as openai_client:
        openai_exclusions = exclusions | {c.lower() for c in prefs.exclude_companies}
        openai_outcome = openai_source.run(
            client=openai_client,
            prefs=prefs,
            exclusions=tuple(sorted(openai_exclusions)),
            runs=runs,
            on_progress=_progress,
        )
    source_outcomes.append(openai_outcome)
    all_candidates.extend(openai_outcome.candidates)

    # Operator rule (2026-09-24): the H-1B (DOL LCA) source only makes sense
    # when the operator actually requires visa sponsorship -- its entire
    # candidate set is sponsorship evidence. When sponsorship isn't
    # required, skip it entirely: no download, no cache refresh, no board
    # probing (a real I/O cost, not just a formality).
    if prefs.visa_sponsorship_required:
        with httpx.Client(timeout=60.0) as h1b_client:
            h1b_outcome = h1b_source.run(
                home_root=home_root,
                client=h1b_client,
                prefs=prefs,
                top_n=_h1b_probe_top_n(),
                on_progress=_progress,
            )
    else:
        h1b_outcome = SourceRunOutcome(
            name="h1b", runs=0, cost_usd=0.0, candidates=(), skip_reason="sponsorship_not_required"
        )
    source_outcomes.append(h1b_outcome)
    all_candidates.extend(h1b_outcome.candidates)

    skipped: dict[str, int] = {}
    new_boards: list[dict[str, object]] = []
    if all_candidates:
        with httpx.Client(timeout=30.0) as merge_client:
            boards, merge_skipped = merge_and_verify(
                client=merge_client,
                all_candidates=all_candidates,
                prefs=prefs,
                exclusions=exclusions,
                on_progress=_progress,
            )
        skipped.update(merge_skipped)
        added = add_usable_boards_to_watchlist(
            home_root=home_root,
            target=target,
            discovery_id=discovery_id,
            boards=boards,
        )
        new_boards = [b.to_json() for b in added]

    total_cost = sum(s.cost_usd for s in source_outcomes)
    any_error = any(s.error for s in source_outcomes)
    all_skipped_or_errored = all(s.error or s.skip_reason for s in source_outcomes)
    if all_skipped_or_errored:
        status = "failed"
    elif any_error:
        status = "partial"
    else:
        status = "succeeded"

    result = DiscoveryResult(
        discovery_id=discovery_id,
        status=status,
        started_at=started_at,
        finished_at=_now(),
        cost_usd=total_cost,
        sources=tuple(source_outcomes),
        new_boards=tuple(new_boards),
        skipped=skipped,
    )
    _save_result(home_root, target, result)
    _progress({"stage": "discovery_done", "discovery_id": discovery_id, "status": status, "new_boards": len(new_boards)})
    return result


__all__ = [
    "DiscoveryBudgetExceeded",
    "DiscoveryPrefs",
    "DiscoveryPrefsError",
    "DiscoveryResult",
    "latest_discovery",
    "load_prefs",
    "run_discovery",
    "save_prefs",
]
