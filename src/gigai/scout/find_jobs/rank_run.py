"""SCOPE-ADD-3 C1: the model ranking pass as a step of a run.

``model_rank.rank_postings`` (B1) is the ranker; this module is what a run
and a re-rank record (``rank_records``) wrap around it:

* :func:`rank_prefs`: the candidate constraints from the run's sealed
  ``FindJobsConfig`` (titles, countries, visa, area, remote).
* :func:`run_call_cap` / :func:`run_concurrency`: the pass is bounded by
  ``rank_postings``' own call cap, never by the run's ``remaining_budget``
  (recorded in ``run-details.json`` but not enforced for scout nodes,
  uat-bug-029 follow-ups). The cap covers every posting at batch 50 with
  half again for retries and splits, up to :data:`RUN_MAX_CALLS`; a pass
  that reaches it fails open (``fail_open_reason`` ``call_budget: ...``).
* :class:`RankStreamer`: ``on_batch`` -> one ``progress/rank.jsonl`` line per
  landed batch, so ``GET /progress`` shows "Ranked N of M" and the first
  batch's scores as soon as it lands. It also polls a cancel file, which is
  how a re-rank started in one process is cancelled from another.
* :func:`to_rank_scores`: the result in the sealed ``AcquireOutput.rank_scores``
  shape (``rank_contracts.RankScore``; no schema change): ``mismatch_flags``
  carries the blockers (``selection`` demotes on it), ``hidden_by_default``
  is always false (blockers demote, never hide), ``cost_usd`` ``"0"``.
* :func:`seal_rank_json`: ``RankResult.to_json()`` committed to the workpad
  journal as ``<run dir>/outputs/rank.json``. The journal refuses an
  uncommitted file outside ``runs/*/{progress,raw,logs}``, so the file is
  published by a journal transition (the pattern ``acquisition_records``
  uses from inside the acquire node), never written loose. Best effort: a
  refused commit is logged and the run goes on; the scores are still sealed
  in ``AcquireOutput.rank_scores`` and streamed in ``progress/rank.jsonl``.

The local CLI (the run's ``model_target``) is the only ranker.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import logging
import math
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any, Mapping
import uuid

from .model_rank import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONCURRENCY,
    SMALL_MACHINE_CONCURRENCY,
    BatchResult,
    CandidatePrefs,
    RankResult,
    rank_postings,
)

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from .contracts import FindJobsConfig, PostingRow
    from .rank_contracts import RankScore
    from .progress import ProgressWriter

# The most calls one pass may make: ~6,000 postings at batch 50 with retries.
# The operator accepts "up to ~5 minutes" (~80 s per 500 postings at K=8);
# a full-catalog run over this many postings fails open for the rest
# (``call_budget``) instead of ranking for a quarter of an hour.
RUN_MAX_CALLS = 180
# A machine with this many CPUs or fewer runs K=SMALL_MACHINE_CONCURRENCY
# (each codex/claude child is ~260-330 MB of RSS, REPORT.md).
SMALL_MACHINE_CPUS = 4
CANCEL_FILENAME = "rank-cancel"
# The journal's transitions are a closed set (``journal.TRANSITIONS``). The
# Scout domain's evidence transition is reused, as ``watchlist.py`` does for
# its records; ``domain: "scout-find-jobs-rank"`` in the front matter says
# what the commit is. No reader keys on this name for anything else.
RANK_JOURNAL_TRANSITION = "scout_public_acquisition_progress"
RANK_JOURNAL_DOMAIN = "scout-find-jobs-rank"
RANK_OUTPUT_NAME = "rank.json"

_logger = logging.getLogger("gigai.scout.server")


def rank_prefs(config: "FindJobsConfig") -> CandidatePrefs:
    """The candidate constraints the resume digest states, from the run's sealed config."""

    from .contracts import WorkModePreference

    remote = config.work_mode is WorkModePreference.REMOTE if config.work_mode is not None else bool(config.remote)
    return CandidatePrefs(
        titles=tuple(str(role) for role in config.roles if str(role).strip()),
        countries=tuple(config.countries),
        visa_sponsorship_required=bool(config.visa_sponsorship_required),
        location=config.location or "",
        remote_preferred=remote,
    )


def run_call_cap(total: int, *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Calls for ``total`` postings: one per batch plus half again for retries, at least 4, at most ``RUN_MAX_CALLS``."""

    batches = math.ceil(max(total, 0) / batch_size)
    return min(RUN_MAX_CALLS, batches + max(4, math.ceil(batches / 2)))


def run_concurrency(cpus: int | None = None) -> int:
    """K=8, or ``SMALL_MACHINE_CONCURRENCY`` (4) on a machine with ``SMALL_MACHINE_CPUS`` or fewer."""

    count = os.cpu_count() if cpus is None else cpus
    return SMALL_MACHINE_CONCURRENCY if count is not None and count <= SMALL_MACHINE_CPUS else DEFAULT_CONCURRENCY


def fit_for(score: int | None) -> str | None:
    """The ``RankScore.fit`` category for a model score (display only)."""

    if score is None:
        return None
    return "strong" if score >= 70 else "maybe" if score >= 40 else "no"


def to_rank_scores(result: RankResult, rows: Sequence["PostingRow"]) -> tuple["RankScore", ...]:
    """One ``RankScore`` per row of ``rows`` (in ``rows`` order), unscored where the pass had none."""

    from .rank_contracts import RankScore

    by_url = result.by_url()
    scores = []
    for row in rows:
        ranked = by_url.get(row.normalized_url)
        score = ranked.score if ranked is not None else None
        scores.append(RankScore(
            normalized_url=row.normalized_url,
            content_sha256=row.content_sha256 or (ranked.content_sha256 if ranked is not None else "") or "unknown",
            fit=fit_for(score),
            score=score,
            reasons=(),
            mismatch_flags=tuple(ranked.blockers) if ranked is not None and score is not None else (),
            hidden_by_default=False,
            cost_usd="0",
            cached=bool(ranked.cached) if ranked is not None else False,
        ))
    return tuple(scores)


def read_sealed_rank(root: Path, run_id: str) -> RankResult | None:
    """The run's sealed ``runs/<run_id>/outputs/rank.json`` as a ``RankResult``, or ``None``.

    ``None`` for a run with no such file (sealed before the model ranker, a
    fail-open pass that scored nothing, a refused commit) or an unreadable
    one. Never raises: an ordering enrichment must not fail its caller.
    """

    try:
        value = json.loads((Path(root) / "runs" / run_id / "outputs" / RANK_OUTPUT_NAME).read_text(encoding="utf-8"))
        return RankResult.from_json(value) if isinstance(value, Mapping) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def sealed_rank_scores(root: Path, run_id: str, rows: Sequence["PostingRow"]) -> tuple["RankScore", ...]:
    """The scores acquire's selection used, read back from the sealed ``rank.json``.

    The twin recompute in assess (``proposal_execution``) ranks its eligible
    set by these, so it picks exactly what acquire picked. Same conversion
    acquire applies to the in-memory result (:func:`to_rank_scores`); ``()``
    (date order) when there is no usable sealed pass, as acquire's own
    fail-open does.
    """

    result = read_sealed_rank(root, run_id)
    if result is None or result.status == "skipped":
        return ()
    return to_rank_scores(result, rows)


def posting_line(item: Any) -> dict[str, object]:
    """One posting's rank, as a ``rank.jsonl`` batch line and the reads carry it."""

    return {
        "normalized_url": item.normalized_url,
        "score": item.score,
        "reasons": list(item.reasons),
        "blockers": list(item.blockers),
        "demoted": bool(item.blockers) and item.score is not None,
        "unscored_reason": item.unscored_reason,
    }


def status_json(result: RankResult | None, *, total: int, reason: str | None = None) -> dict[str, object]:
    """``progress/rank.json``: what the pass did, in the ``rank_status`` shape ``GET /progress`` already serves.

    ``status`` is ``scored`` (the pass ran; ``scored`` of ``total`` got a
    score) or ``skipped`` (no model call was possible). The cost/budget keys
    (``cost_*``, ``throttled``, ``*_budget_usd``, ``usage_line``) are kept,
    empty, so a reader of the old shape keeps working until the UI moves on.
    """

    if result is None or result.status == "skipped":
        why = reason or (result.fail_open_reason if result is not None else None) or "skipped"
        scored = 0
        status = "skipped"
        text = f"skipped: {why}"
    else:
        why = result.fail_open_reason
        scored = sum(item.scored for item in result.postings)
        status = "scored"
        text = f"scored {scored} of {total}"
    line = f"Ranking: {text}" + ("" if status == "skipped" or why is None else f" ({why})")
    value: dict[str, object] = {
        "status": status,
        "ranker": "model",
        "scored": scored,
        "total": total,
        "reason": why,
        "text": text,
        "line": line,
        "cost_cap_usd": None,
        "cost_usd": "0",
        "throttled": None,
        "spent_today_usd": None,
        "daily_budget_usd": None,
        "usage_line": None,
    }
    if result is not None:
        value.update({
            "pass_status": result.status,
            "model_target": result.model_target,
            "resolved_model": result.resolved_model,
            "effort": result.effort,
            "effort_applied": result.effort_applied,
            "demoted": sum(item.demoted for item in result.postings),
            "calls": result.totals()["calls"],
            "max_calls": result.max_calls,
            "seconds": result.seconds,
        })
    return value


class RankStreamer:
    """``on_batch`` for one pass: a ``rank.jsonl`` line per landed batch; honours a cancel file.

    ``cancel_path`` (a file under the pass's own ``progress/``): when it
    exists, ``cancel`` is set, so no further model call starts. A pass
    started by another process (a server restarted mid-pass is a new
    process) is cancelled by creating that file.

    ``on_landed`` (uat-bug-031) gets each landed batch's ``normalized_url``s
    right after its ``rank.jsonl`` line: a run over the import cap writes
    those postings' progress lines then. It never stops the pass: an
    exception from it is logged and the pass goes on.
    """

    def __init__(
        self,
        progress: "ProgressWriter | None",
        *,
        total: int,
        cancel: threading.Event,
        cancel_path: Path | None = None,
        on_update: Callable[[int], None] | None = None,
        on_landed: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        self._progress = progress
        self._on_landed = on_landed
        self._total = total
        self._cancel = cancel
        self._cancel_path = cancel_path
        self._on_update = on_update
        self._ranked: set[str] = set()

    @property
    def ranked(self) -> int:
        return len(self._ranked)

    def started(self, *, model_target: str, batch_size: int, concurrency: int, max_calls: int, **extra: object) -> None:
        if self._progress is not None:
            self._progress.rank_started({
                "total": self._total,
                "model_target": model_target,
                "batch_size": batch_size,
                "concurrency": concurrency,
                "max_calls": max_calls,
                **extra,
            })

    def __call__(self, batch: BatchResult) -> None:
        for item in batch.postings:
            self._ranked.add(item.normalized_url)
        if self._progress is not None:
            self._progress.rank_batch({
                "batch_id": batch.batch_id,
                "source": batch.source,
                "valid": batch.valid,
                "attempts": batch.attempts,
                "seconds": batch.seconds,
                "split": batch.split,
                "split_from": batch.split_from,
                "ranked": len(self._ranked),
                "total": self._total,
                "postings": [posting_line(item) for item in batch.postings],
            })
        if self._on_landed is not None:
            try:
                self._on_landed(tuple(item.normalized_url for item in batch.postings))
            except Exception:  # noqa: BLE001 - a progress write never stops the ranking pass
                _logger.warning("rank: writing batch %s's posting lines failed", batch.batch_id, exc_info=True)
        if self._on_update is not None:
            self._on_update(len(self._ranked))
        if self._cancel_path is not None and self._cancel_path.exists():
            self._cancel.set()

    def finished(self, result: RankResult) -> None:
        if self._progress is not None:
            self._progress.rank_finished({
                "status": result.status,
                "fail_open_reason": result.fail_open_reason,
                "scored": sum(item.scored for item in result.postings),
                "demoted": sum(item.demoted for item in result.postings),
                "total": self._total,
                "calls": result.totals()["calls"],
                "seconds": result.seconds,
            })


def run_pass(
    rows: Sequence["PostingRow"],
    *,
    resume_text: str,
    prefs: CandidatePrefs,
    model_target: object,
    home_root: Path,
    streamer: RankStreamer,
    cancel: threading.Event,
    config: object | None = None,
    max_calls: int | None = None,
    concurrency: int | None = None,
    **extra_started: object,
) -> RankResult:
    """One pass over ``rows`` at batch 50, K=``run_concurrency()``, capped at ``run_call_cap``; streamed."""

    kind = str(getattr(model_target, "value", model_target))
    calls = run_call_cap(len(rows)) if max_calls is None else max_calls
    workers = run_concurrency() if concurrency is None else concurrency
    streamer.started(model_target=kind, batch_size=DEFAULT_BATCH_SIZE, concurrency=workers, max_calls=calls, **extra_started)
    result = rank_postings(
        rows,
        resume_text=resume_text,
        prefs=prefs,
        model_target=kind,
        home_root=home_root,
        on_batch=streamer,
        cancel=cancel,
        config=config,  # type: ignore[arg-type]
        batch_size=DEFAULT_BATCH_SIZE,
        concurrency=workers,
        max_calls=calls,
    )
    streamer.finished(result)
    return result


def json_bytes(value: Mapping[str, object]) -> bytes:
    """Sorted, compact UTF-8 JSON. Not ``canonical_json_bytes``: a pass's ``seconds`` are floats,
    which identity-bearing canonical JSON forbids; these files identify nothing."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def rank_json_bytes(result: RankResult, **extra: object) -> bytes:
    """The sealed ``rank.json`` bytes: ``RankResult.to_json()`` plus the caller's identity keys."""

    return json_bytes({**result.to_json(), **extra})


def seal_rank_json(resolved: Any, relative_dir: str, data: bytes, *, front_matter: Mapping[str, object]) -> bool:
    """Commit ``<relative_dir>/outputs/rank.json`` to the workpad journal; false (logged) when refused.

    Never raises: sealing the ranking record must never fail the run (or
    the re-rank) it belongs to.
    """

    from ...journal import JournalArtifact, record_transition

    path = f"{relative_dir}/outputs/{RANK_OUTPUT_NAME}"
    try:
        record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=f"handoff_{uuid.uuid4()}",
            transition=RANK_JOURNAL_TRANSITION,
            body=f"Sealed the model ranking pass at {path}.",
            artifacts=(JournalArtifact(path, data),),
            front_matter={
                "project_id": resolved.project_id,
                "gig_id": resolved.gig_id,
                "domain": RANK_JOURNAL_DOMAIN,
                "operation": "scout_rank_sealed",
                **dict(front_matter),
            },
            allow_artifact_replacement=False,
        )
    except Exception as exc:  # noqa: BLE001 - sealing never fails the run; the type is logged
        _logger.warning("rank: %s could not be sealed: %s", path, type(exc).__name__)
        return False
    return True


__all__ = [
    "CANCEL_FILENAME",
    "RANK_JOURNAL_DOMAIN",
    "RANK_JOURNAL_TRANSITION",
    "RANK_OUTPUT_NAME",
    "RUN_MAX_CALLS",
    "SMALL_MACHINE_CPUS",
    "RankStreamer",
    "fit_for",
    "json_bytes",
    "posting_line",
    "read_sealed_rank",
    "sealed_rank_scores",
    "rank_json_bytes",
    "rank_prefs",
    "run_call_cap",
    "run_concurrency",
    "run_pass",
    "seal_rank_json",
    "status_json",
    "to_rank_scores",
]
