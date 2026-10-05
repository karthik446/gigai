"""Wave-1b acquisition node for the Scout find-jobs graph.

The node is deliberately an orchestration boundary: provider clients are
injected protocols, while persistence is delegated to the existing public
acquisition journal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlsplit


from ...canonical import canonical_json_bytes, digest_imported_bytes
from ..acquisition_records import (
    MAX_PUBLIC_IMPORT_ROWS,
    import_public_rows,
    preflight_public_import,
    public_row_refusal,
)
from .ats_board_clients import BoardCache, BoardFetchIndex, BoardFetchStats
from .contracts import (
    ATSBoardClient,
    AcquireInput,
    AcquireOutput,
    AssessmentResult,
    CarriedForwardAssessment,
    DropCount,
    FailureRow,
    FindJobsConfig,
    FindJobsContractError,
    NodeContext,
    NotAssessedReason,
    PostingRow,
    PostingRowResult,
    ProgressStatus,
    RowOutcome,
    SelectedPosting,
    SelectionRule,
    SourceKind,
    URLSetDiff,
    WatchlistEntry,
    WatchlistFirstSeen,
    ExaSearchClient,
    WatchlistClient,
    diff_url_sets,
    normalize_url,
)
from .filters import exclusion_reason, location_mismatch_detail
from .work_mode import work_mode_fit
from .tag_store import TagStore
from .title_query import open_tag_store, title_matches
from .progress import ProgressWriter
from .selection import normalize_title, rank_rows, select_for_assessment, selection_limits
from ...workpad import ResolvedWorkpad, resolve_workpad

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from .model_rank import RankResult

# U26: cap on the total size of raw provider responses stored per run, so a
# very large/unbounded response set can't fill the workpad disk unbounded.
RAW_PAYLOAD_CAP_BYTES = 20 * 1024 * 1024

# uat-bug-011: the most postings one run imports (and so seals, shows and
# selects from). Below the journal's own bound, which is checked here, when
# the module loads, rather than at the end of a run.
IMPORT_ROW_CAP = 500
if IMPORT_ROW_CAP > MAX_PUBLIC_IMPORT_ROWS:  # pragma: no cover - a constant mismatch
    raise RuntimeError("IMPORT_ROW_CAP exceeds the public import journal bound")

# Q2 (acquire at scale): the knobs for the ATS board fetch pass. Env vars so
# an operator can tune a run without a config-contract change (the same
# precedent as the ``GIGAI_SCOUT_*`` knobs); ``AcquireLimits`` is also an
# explicit ``acquire_node`` keyword for tests and callers.
ATS_CONCURRENCY_ENV = "GIGAI_SCOUT_ATS_CONCURRENCY"
ATS_MIN_INTERVAL_ENV = "GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS"
ACQUIRE_BUDGET_ENV = "GIGAI_SCOUT_ACQUIRE_BUDGET_SECONDS"
DEFAULT_ATS_CONCURRENCY_PER_PROVIDER = 4
DEFAULT_ATS_MIN_INTERVAL_SECONDS = 0.125  # 8 requests/s per provider, across all its workers
DEFAULT_ACQUIRE_BUDGET_SECONDS = 1200.0  # 20 minutes for the whole ATS pass
BUDGET_EXCEEDED_CODE = "time_budget_exceeded"
# 0110-025 (R2): a board the pass never asked because its stop event was set
# (a background tick cancelled by a manual Full refresh, or the server
# stopping). Reported like a budget skip: status ``skipped``, this code.
CANCELLED_CODE = "cancelled"
# How long a paced wait sleeps before it looks at the stop event again.
_STOP_POLL_SECONDS = 0.5
# acquire-rotation: how often the last-fetched index is flushed mid-pass, so
# a run killed before its end still advances the rotation for the boards it
# reached (the final flush at the end of the pass is unconditional).
ROTATION_FLUSH_INTERVAL_SECONDS = 30.0
# N11-C: where the ATS pass of a find-jobs run gets its watchlist rows.
# ``index`` (the DEFAULT): the company index `gigai scout sources update`
# wrote -- no board request at all. ``fetch``: the board fetch itself
# (`_fetch_boards`, the engine of sources update), a pass of up to 20
# minutes; only a caller that asks for it by name gets it, so a caller that
# forgets the keyword reads the index and never brings the per-search fetch
# back.
BOARDS_FROM_FETCH = "fetch"
BOARDS_FROM_INDEX = "index"
SOURCES_UPDATE_REQUIRED_CODE = "sources_update_required"
# Option E (orchestrator decision 2026-09-27): the ONE case where a search
# asks a board: a company Exa discovered in this same run that is not in the
# company index yet. At most this many such boards per run; each is written
# to the index, so the next search needs nothing. The rest wait for the next
# `gigai scout sources update` (counted, `boards.exa_new.waiting`).
EXA_NEW_BOARD_FETCH_CAP = 20

# Query parameter names ATS boards use to carry a job id when the posting is
# served from a custom career-site domain rather than the board's own
# subdomain (U20 dedupe): e.g. "https://www.pinterestcareers.com/jobs?gh_jid=…"
# is the same Greenhouse job as "https://job-boards.greenhouse.io/pinterest/
# jobs/…" for the numeric id in the path.
_JOB_ID_QUERY_KEYS = ("gh_jid", "lever_id", "job_id")
_NUMERIC_PATH_SEGMENT = re.compile(r"^\d{4,}$")


class AcquireAllSourcesFailedError(FindJobsContractError):
    """Every enabled acquisition source failed; no batch was written."""


@dataclass(frozen=True)
class AcquireLimits:
    """Q2: bounded concurrency, polite pacing and a run-time budget for the ATS pass.

    ``concurrency_per_provider`` workers per ATS provider (Greenhouse, Lever
    and Ashby each get their own pool, so one slow provider never starves
    the others); ``min_request_interval_seconds`` between request *starts*
    per provider, shared by that provider's workers (a token-bucket-style
    pacer -- 0.125 s is 8 requests/s against one provider's public API);
    ``time_budget_seconds`` for the whole board pass: boards not started by
    then are skipped (recorded per board in progress and as one
    ``time_budget_exceeded`` failure row), in-flight boards finish, and the
    run seals cleanly with what it has. ``None``/``<= 0`` disables the
    budget.

    ``spread_seconds`` (0110-025, the background refresh tick): spread each
    provider's requests evenly over this long instead of sending them at the
    polite maximum. The interval is computed per provider from its board
    count (:meth:`interval_for`) and never drops below
    ``min_request_interval_seconds``, so a spread can only slow a pass down.
    ``None`` (every manual update and every find-jobs run) is the fixed
    interval.
    """

    concurrency_per_provider: int = DEFAULT_ATS_CONCURRENCY_PER_PROVIDER
    min_request_interval_seconds: float = DEFAULT_ATS_MIN_INTERVAL_SECONDS
    time_budget_seconds: float | None = DEFAULT_ACQUIRE_BUDGET_SECONDS
    spread_seconds: float | None = None

    def interval_for(self, board_count: int) -> float:
        """Seconds between request starts for a provider with ``board_count`` boards in this pass."""

        floor = max(0.0, float(self.min_request_interval_seconds))
        if self.spread_seconds is None or self.spread_seconds <= 0 or board_count <= 0:
            return floor
        return max(floor, float(self.spread_seconds) / board_count)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "AcquireLimits":
        env = os.environ if environ is None else environ

        def _float(name: str, default: float | None) -> float | None:
            raw = env.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        concurrency_raw = _float(ATS_CONCURRENCY_ENV, float(DEFAULT_ATS_CONCURRENCY_PER_PROVIDER))
        concurrency = max(1, int(concurrency_raw)) if concurrency_raw is not None else DEFAULT_ATS_CONCURRENCY_PER_PROVIDER
        interval = _float(ATS_MIN_INTERVAL_ENV, DEFAULT_ATS_MIN_INTERVAL_SECONDS)
        budget = _float(ACQUIRE_BUDGET_ENV, DEFAULT_ACQUIRE_BUDGET_SECONDS)
        return cls(
            concurrency_per_provider=concurrency,
            min_request_interval_seconds=max(0.0, interval if interval is not None else 0.0),
            time_budget_seconds=budget if budget is not None and budget > 0 else None,
        )

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "concurrency_per_provider": self.concurrency_per_provider,
            "min_request_interval_seconds": self.min_request_interval_seconds,
            "time_budget_seconds": self.time_budget_seconds,
        }
        if self.spread_seconds is not None:
            value["spread_seconds"] = self.spread_seconds
        return value


class _PassCancelled(Exception):
    """The pass's stop event was set while a request waited for its slot."""


class _RateLimiter:
    """Thread-safe pacer: request starts at least ``min_interval`` apart.

    The slot is reserved under the lock and the sleep happens outside it, so
    N workers sharing one limiter start their requests in a strict cadence
    rather than all sleeping and then bursting together.

    With a ``stop`` event the sleep is taken in short steps and a set event
    raises :class:`_PassCancelled` instead of starting the request, so a
    spread pass (seconds between starts) stops at once.
    """

    def __init__(self, min_interval: float, *, stop: threading.Event | None = None) -> None:
        self._min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_start = 0.0
        self._stop = stop

    def wait(self) -> None:
        stop = self._stop
        if stop is not None and stop.is_set():
            raise _PassCancelled()
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_start)
            self._next_start = start + self._min_interval
        delay = start - now
        if delay <= 0:
            return
        if stop is None:
            time.sleep(delay)
            return
        while True:
            remaining = start - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, _STOP_POLL_SECONDS))
            if stop.is_set():
                raise _PassCancelled()


def _response_failure_code(response: Any) -> str | None:
    """``http_<status>`` for an answer that is neither a body nor a ``304``."""

    status = getattr(response, "status_code", None)
    if type(status) is not int or status in (200, 304):
        return None
    return f"http_{status}"


def _exception_failure_code(exc: BaseException) -> str:
    """``timeout``, ``connect_error`` or ``network_error`` for a request that got no answer."""

    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & {"TimeoutException", "TimeoutError"}:
        return "timeout"
    if "ConnectError" in names or "ConnectionError" in names:
        return "connect_error"
    return "network_error"


class _ThrottledClient:
    """Pass-through httpx-like client whose ``get``/``post`` wait on a limiter.

    It also remembers, per worker thread, why the last request did not
    answer with a body (0110-025 R5): the board clients redact every
    failure to ``http_error``/``network_error``, and the status is what
    tells a rate limit (``http_429``) from a dead board (``http_404``).
    """

    def __init__(self, client: Any, limiter: _RateLimiter) -> None:
        self._client = client
        self._limiter = limiter
        self._seen = threading.local()

    def _send(self, method: str, url: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self._limiter.wait()
        self._seen.failure = None
        try:
            response = getattr(self._client, method)(url, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - noted for the board's failure code, then re-raised
            self._seen.failure = _exception_failure_code(exc)
            raise
        self._seen.failure = _response_failure_code(response)
        return response

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        return self._send("get", url, args, kwargs)

    def post(self, url: str, *args: Any, **kwargs: Any) -> Any:
        return self._send("post", url, args, kwargs)

    def last_failure(self) -> str | None:
        """The calling thread's last request failure (``None`` after an answered request)."""

        return getattr(self._seen, "failure", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


#: The board clients' redacted codes that a request-level detail refines.
_REDACTED_REQUEST_CODES = frozenset({"http_error", "network_error"})


def _board_failure_code(exc: BaseException, client: Any) -> str:
    """One board's failure code: the HTTP status or transport failure when known.

    ``http_429``, ``http_404``, ``timeout``, ``connect_error``; otherwise the
    board client's own stable code (``bad_json``, ``unsupported_provider``),
    and for anything else the exception's class name, as before.
    """

    code = getattr(exc, "code", None)
    code = code if type(code) is str and code else None
    if isinstance(client, _ThrottledClient) and code in _REDACTED_REQUEST_CODES:
        seen = client.last_failure()
        if seen is not None:
            return seen
    if code is not None:
        return code
    if isinstance(exc, TimeoutError):
        return "timeout"
    return type(exc).__name__.lower()


@dataclass(frozen=True)
class _BoardOutcome:
    board: WatchlistEntry
    status: str  # "fetched" | "cached" | "failed" | "skipped"
    rows: tuple[PostingRow, ...]
    stats: BoardFetchStats | None
    elapsed_ms: int
    code: str | None


def _board_index_key(board: WatchlistEntry) -> str:
    return BoardFetchIndex.key(board.provider.value, board.board_token)


def _rotation_stamp() -> str:
    """Fixed-width UTC stamp (milliseconds) so index stamps compare as strings."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class _RotationPlan:
    """The ordered page for this run plus the cursor it starts from (acquire-rotation)."""

    ordered: tuple[WatchlistEntry, ...]
    index: BoardFetchIndex  # with the cycle already advanced when this run opens a new one
    stamp: str  # what every board attempted this run is stamped with
    covered_before: int  # boards already covered in this cycle before this run
    covered_keys: frozenset[str]

    @property
    def total(self) -> int:
        return len(self.ordered)

    def rotation_json(self, *, page_sizes: Mapping[str, int] | None, newly_covered: int | None) -> dict[str, object]:
        """The ``rotation`` block of ``boards.json``.

        Before the pass ends ``page_sizes`` is the previous run's (an
        estimate, ``estimated: true``) and ``newly_covered`` is unknown;
        at the end both are this run's measurements. ``runs_per_rotation``
        (K) is the worst provider's ``ceil(boards / page)``: the pools run
        side by side, so the rotation is only as fast as its slowest
        provider (Greenhouse, in practice).
        """

        totals: dict[str, int] = {}
        for board in self.ordered:
            totals[board.provider.value] = totals.get(board.provider.value, 0) + 1
        providers: dict[str, dict[str, object]] = {}
        worst: int | None = None
        for provider, count in sorted(totals.items()):
            page = page_sizes.get(provider) if page_sizes else None
            runs = math.ceil(count / page) if page else None
            providers[provider] = {"total": count, "page_size": page, "runs_per_rotation": runs}
            if runs is not None:
                worst = runs if worst is None else max(worst, runs)
        page_total = sum(page_sizes.values()) if page_sizes else None
        first = self.covered_before + 1
        last = self.covered_before + newly_covered if newly_covered else None
        return {
            "cycle": self.index.cycle,
            "cycle_started_at": self.index.cycle_started_at,
            "total": self.total,
            "first": first,
            "last": last,
            "page_size": page_total,
            "runs_per_rotation": worst,
            "estimated": newly_covered is None,
            "providers": providers,
        }


def _plan_rotation(
    boards: Sequence[WatchlistEntry],
    *,
    index: BoardFetchIndex,
    catalog_counts: Mapping[tuple[str, str], int] | None,
    stamp: str,
) -> _RotationPlan:
    """Order this run's boards: the next page of the watchlist by last fetch (acquire-rotation).

    Operator direction 2026-09-25: every board stays on the watchlist and the
    rotation bounds the cost. The order is

    1. user/discovery-added boards (not ``catalog:`` seeded) before catalog
       boards -- they are few, so they are re-checked every run and a budget
       can never starve them (Q2's rule, kept);
    2. never-fetched boards first, then by last-attempted stamp ascending
       (least recently attempted first: what the previous run's budget left
       behind leads, so consecutive runs page through the whole watchlist);
    3. ties (every board attempted in one run carries that run's stamp) by
       the catalog record's ``us_posting_count`` descending, then (provider,
       token) -- so the order is deterministic run to run, independent of
       thread timing.

    The cursor: boards stamped at/after ``cycle_started_at`` are covered in
    the current cycle; once every planned board is, this run opens the next
    cycle. The returned plan's ``index`` already reflects that.
    """

    covered_keys: set[str] = set()
    keys = [_board_index_key(board) for board in boards]
    if index.cycle_started_at is not None:
        started = index.cycle_started_at
        covered_keys = {key for key in keys if (at := index.boards.get(key)) is not None and at >= started}
    if boards and (index.cycle_started_at is None or len(covered_keys) == len(keys)):
        index = replace(index, cycle=index.cycle + (1 if index.cycle_started_at is not None else 0), cycle_started_at=stamp)
        covered_keys = set()

    def order_key(board: WatchlistEntry) -> tuple[int, int, str, int, str, str]:
        from_catalog = board.first_seen.query_key.startswith("catalog:")
        last = index.boards.get(_board_index_key(board))
        us = catalog_counts.get((board.provider.value, board.board_token)) if catalog_counts else None
        return (
            1 if from_catalog else 0,
            0 if last is None else 1,
            last or "",
            -(us if us is not None else -1),
            board.provider.value,
            board.board_token,
        )

    return _RotationPlan(
        ordered=tuple(sorted(boards, key=order_key)),
        index=index,
        stamp=stamp,
        covered_before=len(covered_keys),
        covered_keys=frozenset(covered_keys),
    )


def _fetch_one_board(
    board: WatchlistEntry,
    *,
    ats: ATSBoardClient,
    client: Any,
    config: FindJobsConfig,
    cache: BoardCache | None,
    deadline: float | None,
    stop: threading.Event | None = None,
) -> _BoardOutcome:
    if stop is not None and stop.is_set():
        return _BoardOutcome(board, "skipped", (), None, 0, CANCELLED_CODE)
    if deadline is not None and time.monotonic() >= deadline:
        return _BoardOutcome(board, "skipped", (), None, 0, BUDGET_EXCEEDED_CODE)
    started = time.monotonic()
    fetch = getattr(ats, "fetch_board", None)
    try:
        if callable(fetch):
            result = fetch(client, board.provider.value, board.board_token, config, cache=cache)
            rows = tuple(result.rows)
            stats: BoardFetchStats | None = result.stats
        else:
            rows = tuple(ats.list_board(client, board.provider.value, board.board_token, config))
            stats = None
    except _PassCancelled:
        # Stopped while waiting for its request slot: not asked, so not
        # stamped; it leads the next pass like a budget skip.
        return _BoardOutcome(board, "skipped", (), None, 0, CANCELLED_CODE)
    except Exception as exc:  # noqa: BLE001 - one board's failure is one failure row
        elapsed = int((time.monotonic() - started) * 1000)
        return _BoardOutcome(board, "failed", (), None, elapsed, _board_failure_code(exc, client))
    elapsed = int((time.monotonic() - started) * 1000)
    cached = stats is not None and stats.cache in {"hit", "revalidated"} and stats.detail_fetched == 0
    return _BoardOutcome(board, "cached" if cached else "fetched", rows, stats, elapsed, None)


def _fetch_boards(
    boards: Sequence[WatchlistEntry],
    *,
    ats: ATSBoardClient,
    client: Any,
    config: FindJobsConfig,
    limits: AcquireLimits,
    cache: BoardCache | None,
    progress: ProgressWriter | None,
    started_at: float,
    catalog_counts: Mapping[tuple[str, str], int] | None = None,
    stop: threading.Event | None = None,
) -> tuple[list[PostingRow], list[FailureRow], dict[str, object]]:
    """Fetch the next page of watchlist boards with per-provider pools, pacing and a budget.

    0110-025: ``limits.spread_seconds`` spreads each provider's request
    starts evenly (the interval comes from that provider's board count,
    ``AcquireLimits.interval_for``), and ``stop`` is checked before every
    board and while a request waits for its slot. Once it is set no new
    request starts: the boards not asked are ``skipped`` with code
    ``cancelled`` (not stamped, so they lead the next pass), a request
    already on the wire finishes, and the pass returns as it does after a
    budget stop.

    acquire-rotation: the boards are ordered by ``_plan_rotation`` (least
    recently attempted first, from the ``BoardCache``'s last-fetched index),
    every board attempted this run -- fetched, cached (a ``304`` counts) or
    failed, never a budget-skipped one -- is stamped with this run's stamp,
    and the index is flushed every :data:`ROTATION_FLUSH_INTERVAL_SECONDS`
    and at the end. The page is whatever the budget fits; the boards it
    skips keep their old stamp and lead the next run. Without a cache (no
    home) nothing persists and every run orders the same way.

    Rows come back in the planned board order (never completion order), so
    the sealed batch is deterministic regardless of thread timing; a
    per-board progress line is written from this (main) thread as each
    future settles. Returns ``(rows, failures, summary)``.
    """

    fetch_index = cache.load_fetch_index() if cache is not None else BoardFetchIndex()
    plan = _plan_rotation(boards, index=fetch_index, catalog_counts=catalog_counts, stamp=_rotation_stamp())
    ordered = plan.ordered
    fetch_index = plan.index
    budget = limits.time_budget_seconds if limits.time_budget_seconds and limits.time_budget_seconds > 0 else None
    deadline = started_at + budget if budget is not None else None
    if progress is not None:
        progress.boards_planned(
            total=len(ordered),
            budget_seconds=budget,
            rotation=plan.rotation_json(page_sizes=fetch_index.page_sizes, newly_covered=None),
        )
    workers = max(1, int(limits.concurrency_per_provider))
    stamped: dict[str, str] = {}
    last_flush = time.monotonic()

    def flush_index() -> None:
        nonlocal fetch_index, last_flush
        if cache is None:
            return
        fetch_index = replace(fetch_index, boards={**fetch_index.boards, **stamped})
        try:
            cache.store_fetch_index(fetch_index)
        except OSError as exc:
            print(f"scout acquire: could not write the board rotation index ({type(exc).__name__})", file=sys.stderr)
        last_flush = time.monotonic()

    provider_totals: dict[str, int] = {}
    for board in ordered:
        provider_totals[board.provider.value] = provider_totals.get(board.provider.value, 0) + 1
    limiters: dict[str, _RateLimiter] = {}
    throttled: dict[str, Any] = {}
    executors: dict[str, ThreadPoolExecutor] = {}
    futures: dict[Future[_BoardOutcome], int] = {}
    outcomes: dict[int, _BoardOutcome] = {}
    try:
        for index, board in enumerate(ordered):
            provider = board.provider.value
            if provider not in executors:
                limiters[provider] = _RateLimiter(limits.interval_for(provider_totals[provider]), stop=stop)
                throttled[provider] = _ThrottledClient(client, limiters[provider]) if client is not None else None
                executors[provider] = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"scout-ats-{provider}")
            future = executors[provider].submit(
                _fetch_one_board,
                board,
                ats=ats,
                client=throttled[provider],
                config=config,
                cache=cache,
                deadline=deadline,
                stop=stop,
            )
            futures[future] = index
        for future in as_completed(futures):
            outcome = future.result()
            outcomes[futures[future]] = outcome
            if outcome.status != "skipped":
                stamped[_board_index_key(outcome.board)] = plan.stamp
                if cache is not None and time.monotonic() - last_flush >= ROTATION_FLUSH_INTERVAL_SECONDS:
                    flush_index()
            if progress is not None:
                stats = outcome.stats
                progress.board_finished(
                    provider=outcome.board.provider.value,
                    board_token=outcome.board.board_token,
                    status=outcome.status,
                    requests=stats.requests if stats is not None else 0,
                    cache=stats.cache if stats is not None else None,
                    postings=stats.listed if stats is not None else len(outcome.rows),
                    matched=len(outcome.rows),
                    elapsed_ms=outcome.elapsed_ms,
                    code=outcome.code,
                )
    finally:
        for executor in executors.values():
            executor.shutdown(wait=True)

    page_sizes: dict[str, int] = {}
    newly_covered = 0
    for key in stamped:
        provider = key.split(":", 1)[0]
        page_sizes[provider] = page_sizes.get(provider, 0) + 1
        if key not in plan.covered_keys:
            newly_covered += 1
    fetch_index = replace(fetch_index, page_sizes={**fetch_index.page_sizes, **page_sizes})
    flush_index()
    rotation = plan.rotation_json(page_sizes=page_sizes, newly_covered=newly_covered)

    rows: list[PostingRow] = []
    failures: list[FailureRow] = []
    counts = {"fetched": 0, "cached": 0, "failed": 0, "skipped": 0}
    cancelled = 0
    requests = 0
    cache_hits = 0
    listed = 0
    prefiltered_out = 0
    detail_fetched = 0
    detail_cached = 0
    for index in range(len(ordered)):
        outcome = outcomes[index]
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
        rows.extend(outcome.rows)
        if outcome.stats is not None:
            requests += outcome.stats.requests
            cache_hits += 1 if outcome.stats.cache in {"hit", "revalidated"} else 0
            listed += outcome.stats.listed
            prefiltered_out += outcome.stats.prefiltered_out
            detail_fetched += outcome.stats.detail_fetched
            detail_cached += outcome.stats.detail_cached
        if outcome.status == "failed":
            failures.append(FailureRow(SourceKind.ATS, outcome.board.board_token, None, outcome.code or "error", "ATS board fetch failed"))
        elif outcome.status == "skipped" and outcome.code == CANCELLED_CODE:
            cancelled += 1
    over_budget = counts["skipped"] - cancelled
    if over_budget:
        failures.append(
            FailureRow(
                SourceKind.ATS,
                "ats",
                None,
                BUDGET_EXCEEDED_CODE,
                f"{over_budget} of {len(ordered)} watchlist boards were not fetched: the "
                f"{budget or 0:.0f}s acquire time budget ran out",
            )
        )
    if cancelled:
        failures.append(
            FailureRow(
                SourceKind.ATS,
                "ats",
                None,
                CANCELLED_CODE,
                f"{cancelled} of {len(ordered)} watchlist boards were not fetched: the board pass was stopped",
            )
        )
    summary: dict[str, object] = {
        "total": len(ordered),
        **counts,
        "requests": requests,
        "cache_hits": cache_hits,
        "listed": listed,
        "prefiltered_out": prefiltered_out,
        "detail_fetched": detail_fetched,
        "detail_cached": detail_cached,
        "matched": len(rows),
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "budget_seconds": budget,
        "limits": limits.to_json(),
        "rotation": rotation,
    }
    if stop is not None:
        summary["cancelled"] = cancelled
    return rows, failures, summary


def _catalog_us_counts() -> dict[tuple[str, str], int]:
    """``(provider, token) -> us_posting_count`` from the shipped catalog, or ``{}`` if it cannot load.

    Only a tie-breaker for the rotation order (``_plan_rotation``); a
    catalog that fails its digest pin must not fail acquire here -- seeding
    already reported that.
    """

    try:
        from .company_catalog import load_company_catalog

        catalog = load_company_catalog()
    except Exception as exc:  # noqa: BLE001 - ordering hint only
        print(f"scout acquire: company catalog unavailable for the rotation order ({type(exc).__name__})", file=sys.stderr)
        return {}
    return {
        (record.provider.value, record.board_token): record.us_posting_count
        for record in catalog.records
        if record.us_posting_count is not None
    }


def _read_index(
    boards: Sequence[WatchlistEntry],
    *,
    home_root: Path | None,
    config: FindJobsConfig,
    progress: ProgressWriter | None,
    started_at: float,
) -> tuple[list[PostingRow], list[FailureRow], dict[str, object]]:
    """N11-C: the watchlist's rows from the company index under ``home_root`` (no request).

    Without a home there is no index to read: that is the empty index, and
    the run says "Run Update sources" like any other search with nothing
    stored.
    """

    from .company_index import CompanyIndex
    from .index_search import read_indexed_boards

    root = Path(home_root) if home_root is not None else None
    index = CompanyIndex.for_home(root) if root is not None else CompanyIndex(Path(os.devnull) / "scout-companies")
    cache = _board_cache(root) or BoardCache(Path(os.devnull) / "scout-ats-boards")
    return read_indexed_boards(
        boards,
        index=index,
        cache=cache,
        config=config,
        progress=progress,
        started_at=started_at,
        remember_search=root is not None,
        tags=open_tag_store(root),
    )


def _index_line(summary: Mapping[str, object]) -> str:
    from .index_search import index_line

    return index_line(dict(summary))


def _fetch_exa_new_boards(
    discovered: Sequence[PostingRow],
    *,
    batch_id: str,
    home_root: Path | None,
    ats: ATSBoardClient,
    client: Any,
    config: FindJobsConfig,
    limits: AcquireLimits,
) -> tuple[list[PostingRow], list[FailureRow], dict[str, object]]:
    """Option E: fetch the boards of companies Exa just found that are not indexed yet.

    Only boards named by this run's own Exa rows, only the ones with no
    company index file, and at most :data:`EXA_NEW_BOARD_FETCH_CAP` of them
    (in the order Exa returned them). The fetch is the ordinary one
    (`_fetch_boards`: cache, pacing, rotation stamp); each board that
    answered is then written to the company index. Returns ``(rows,
    failures, exa_new)`` where ``exa_new`` is the block the progress and
    the UI read: ``{"cap", "found", "fetched", "failed", "waiting",
    "requests"}``.
    """

    from .company_index import CompanyIndex, refresh_company

    exa_new: dict[str, object] = {
        "cap": EXA_NEW_BOARD_FETCH_CAP,
        "found": 0,
        "fetched": 0,
        "failed": 0,
        "waiting": 0,
        "requests": 0,
    }
    cache = _board_cache(home_root)
    if home_root is None or cache is None:
        return [], [], exa_new
    index = CompanyIndex.for_home(Path(home_root))
    wanted: dict[tuple[str, str], WatchlistEntry] = {}
    for row in discovered:
        if row.board_token is None:
            continue
        key = (row.provider.value, row.board_token)
        if key in wanted or index.read(*key) is not None:
            continue
        wanted[key] = WatchlistEntry(
            watchlist_id=f"scout_watchlist:{row.provider.value}:{row.board_token}",
            provider=row.provider,
            board_token=row.board_token,
            company=row.company,
            state="active",
            first_seen=WatchlistFirstSeen(SourceKind.EXA, row.url, row.query_key, batch_id, _now()),
        )
    exa_new["found"] = len(wanted)
    if not wanted:
        return [], [], exa_new
    page = list(wanted.values())[:EXA_NEW_BOARD_FETCH_CAP]
    exa_new["waiting"] = len(wanted) - len(page)
    counting = _CountingClient(client) if client is not None else None
    fetched_rows, fetch_failures, summary = _fetch_boards(
        page,
        ats=ats,
        client=counting,
        config=config,
        limits=limits,
        cache=cache,
        progress=None,
        started_at=time.monotonic(),
        catalog_counts=None,
    )
    for board in page:
        try:
            refresh_company(index, cache, ats=board.provider.value, slug=board.board_token, company=board.company)
        except Exception as exc:  # noqa: BLE001 - the rows are already in hand; the next update indexes it
            print(
                f"scout acquire: could not index {board.provider.value}:{board.board_token} ({type(exc).__name__})",
                file=sys.stderr,
            )
    exa_new["fetched"] = int(summary.get("fetched", 0) or 0) + int(summary.get("cached", 0) or 0)  # type: ignore[call-overload]
    exa_new["failed"] = int(summary.get("failed", 0) or 0)  # type: ignore[call-overload]
    exa_new["waiting"] = len(wanted) - len(page) + int(summary.get("skipped", 0) or 0)  # type: ignore[call-overload]
    # Counted at the client, so a board that did not answer counts too.
    exa_new["requests"] = counting.requests if counting is not None else 0
    # The budget marker of this small pass is already counted as `waiting`.
    failures = [failure for failure in fetch_failures if failure.code != BUDGET_EXCEEDED_CODE]
    return fetched_rows, failures, exa_new


class _CountingClient:
    """Pass-through client that counts the requests it is asked to make."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        self.requests = 0

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            self.requests += 1
        return self._client.get(url, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _with_exa_new(summary: Mapping[str, object], exa_new: Mapping[str, object], *, matched: int) -> dict[str, object]:
    """The index read's summary plus what option E fetched (``requests`` is only ever that)."""

    merged = dict(summary)
    merged["exa_new"] = dict(exa_new)
    merged["fetched"] = exa_new["fetched"]
    merged["failed"] = exa_new["failed"]
    merged["requests"] = exa_new["requests"]
    merged["matched"] = int(merged.get("matched", 0) or 0) + matched  # type: ignore[call-overload]
    return merged


def _board_cache(home_root: Path | None) -> BoardCache | None:
    """``<home>/cache/scout/ats-boards`` (next to the H-1B cache), or ``None`` without a home."""

    if home_root is None:
        return None
    # No index validators here: a run's own board fetch needs the body, so a 304 answered to a
    # snapshot's ETag (no cached body) must never reach it. Only sources update uses them.
    return BoardCache(Path(home_root) / "cache" / "scout" / "ats-boards", validator_source=lambda _provider, _url: None)


def _seed_watchlist(
    resolved: ResolvedWorkpad,
    *,
    home_root: Path | None,
    target: Path | None,
    progress: ProgressWriter | None,
) -> FailureRow | None:
    """Q2: seed the watchlist from the bundled catalog before listing boards.

    Explicit, never silent: the outcome (seeded / skipped because no prefs
    are saved yet / failed) is written to ``progress/watchlist-seed.json``
    and printed on the run's stderr. Needs the requested ``target`` to find
    the project's prefs (``<home>/scout/<project_id>/discovery/prefs.json``);
    direct-call unit tests without one skip seeding exactly as before this
    packet. A seeding failure never fails the run: the boards already on the
    watchlist are still fetched, and the failure is one redacted row.
    """

    if home_root is None or target is None:
        return None
    try:
        from .discovery.prefs import load_prefs

        prefs = load_prefs(home_root=home_root, target=target)
    except Exception as exc:  # noqa: BLE001 - unreadable prefs: record and carry on with the current watchlist
        if progress is not None:
            progress.watchlist_seeded({"status": "failed", "reason": "prefs_unreadable", "error": type(exc).__name__})
        print(f"scout acquire: watchlist not seeded from the company catalog: prefs unreadable ({type(exc).__name__})", file=sys.stderr)
        return None
    if prefs is None:
        if progress is not None:
            progress.watchlist_seeded({"status": "skipped", "reason": "prefs_missing"})
        print("scout acquire: watchlist not seeded from the company catalog: no setup preferences saved yet", file=sys.stderr)
        return None
    try:
        from .watchlist import seed_watchlist_from_catalog

        result = seed_watchlist_from_catalog(home_root, resolved, gig_id=resolved.gig_id, prefs=prefs)
    except Exception as exc:  # noqa: BLE001 - recorded as a failure row; existing watchlist still runs
        if progress is not None:
            progress.watchlist_seeded({"status": "failed", "reason": "seed_failed", "error": type(exc).__name__})
        print(f"scout acquire: watchlist seeding from the company catalog failed ({type(exc).__name__})", file=sys.stderr)
        return FailureRow(SourceKind.ATS, "catalog", None, type(exc).__name__.lower(), "watchlist seeding from the company catalog failed")
    if progress is not None:
        progress.watchlist_seeded({"status": "seeded", **result.to_json()})
    print(
        f"scout acquire: watchlist seeded from company catalog {result.catalog_revision}: "
        f"+{result.added} boards ({result.already_present} already present, "
        f"{result.excluded_by_country} excluded by country, {result.excluded_by_company} excluded by company)",
        file=sys.stderr,
    )
    return None


def _rotation_line(rotation: object) -> str:
    """One human line: ``boards N-M of T this run; full rotation every ~K runs``."""

    if not isinstance(rotation, dict):
        return "scout acquire: rotation: n/a"
    first, last, total, runs = rotation.get("first"), rotation.get("last"), rotation.get("total"), rotation.get("runs_per_rotation")
    span = f"{first}-{last}" if last is not None else f"{first}-?"
    if runs == 1:
        cadence = "every run"
    elif isinstance(runs, int) and runs > 1:
        cadence = f"every ~{runs} runs"
    else:
        cadence = "cadence unknown"
    return f"scout acquire: boards {span} of {total} this run (cycle {rotation.get('cycle')}); full rotation {cadence}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_batch_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-")
    return (value or "acquire")[:120]


def job_id_from_url(url: str) -> str | None:
    """Best-effort ATS job id extracted from a posting URL (U20 dedupe).

    Checks known job-id query parameters first (``gh_jid`` etc., used by
    custom career-site domains that proxy a Greenhouse/Lever board), then the
    last numeric path segment (the board-subdomain URL shape, e.g.
    ``.../pinterest/jobs/7683977``). Returns ``None`` when neither is found
    (e.g. Ashby's UUID-slug job ids, which are already unique per posting and
    don't need this fallback -- the board_token + full path is enough).
    """

    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    query = dict(parse_qsl(parsed.query))
    for key in _JOB_ID_QUERY_KEYS:
        value = query.get(key)
        if value:
            return value
    parts = [part for part in parsed.path.split("/") if part]
    for part in reversed(parts):
        if _NUMERIC_PATH_SEGMENT.match(part):
            return part
    return None


# P4: ``job_input.resolve_job`` reuses the parser; the old private name stays
# an alias so existing callers/tests keep working.
_job_id_from_url = job_id_from_url


def _dedupe_identity(row: PostingRow) -> str:
    """The identity key used to dedupe an Exa row against its ATS row.

    Same normalized URL is the strongest signal (handled by the caller's
    ``seen`` set before this is even consulted); this is the fallback for
    when the same job is served from two different URLs (a board subdomain
    and a custom career-site domain proxying the same board). Falls back to
    the row's own normalized URL when no job id can be parsed out, which
    makes the row unique to itself (no false-positive dedupe).
    """

    job_id = _job_id_from_url(row.url) or _job_id_from_url(row.normalized_url)
    if row.board_token and job_id:
        return f"{row.provider.value}:{row.board_token}:{job_id}"
    return f"self:{row.normalized_url}"


def _merge_exa_and_ats_rows(rows: Sequence[PostingRow]) -> list[PostingRow]:
    """Dedupe Exa vs ATS rows for the same job; the ATS row wins (U20).

    Two dedupe passes:

    1. Exact normalized-URL collision (unchanged from before this packet):
       whichever row is seen first for that URL wins, but an ATS row is now
       sorted first so ties resolve to it deterministically.
    2. Identity collision (:func:`_dedupe_identity`: same board_token + job
       id parsed from the URL, reached via two different hostnames -- e.g. a
       Greenhouse-hosted board URL and the employer's custom career-site
       domain proxying the same posting). When both an Exa and an ATS row
       share an identity, the ATS row's fuller title/location/text/hash wins
       and the Exa row is dropped entirely (not kept as a second entry).
    """

    # Sort ATS-sourced rows first so both passes prefer them on a tie.
    ordered = sorted(rows, key=lambda row: 0 if row.source_kind is SourceKind.ATS else 1)

    by_url: dict[str, PostingRow] = {}
    order: list[str] = []
    for row in ordered:
        normalized = normalize_url(row.normalized_url or row.url)
        if normalized != row.normalized_url:
            # dataclasses.replace (not a positional PostingRow(...) rebuild)
            # so a future additive field (like B1's `countries`) carries
            # through automatically instead of silently reverting to its
            # default on every URL-normalization pass.
            row = replace(row, normalized_url=normalized)
        if normalized in by_url:
            continue  # ATS-first ordering already means the first seen wins.
        by_url[normalized] = row
        order.append(normalized)

    url_deduped = [by_url[key] for key in order]

    by_identity: dict[str, PostingRow] = {}
    identity_order: list[str] = []
    for row in url_deduped:
        identity = _dedupe_identity(row)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = row
            identity_order.append(identity)
            continue
        if existing.source_kind is not SourceKind.ATS and row.source_kind is SourceKind.ATS:
            by_identity[identity] = row
        # else: keep whichever ATS/first row is already stored; the Exa
        # duplicate is dropped.

    return [by_identity[key] for key in identity_order]


def _role_match(row: PostingRow, roles: Sequence[str], tags: TagStore | None = None) -> bool:
    # ONE shared title matcher (``title_query``), the same one index search
    # uses: the whole-word rule, plus the profile's tag query when a tag store
    # is given. Company and location no longer count: roles name job titles.
    return title_matches(row.title, roles, tags)


def _digest(row: PostingRow) -> str:
    return row.content_sha256 or digest_imported_bytes(canonical_json_bytes(row.to_json()))


def _progress_writer(
    context: NodeContext, home_root: Path | None, target: Path | None
) -> ProgressWriter | None:
    """Best-effort ``ProgressWriter`` for this run, or ``None`` if unresolvable.

    Progress is purely additive UX; a caller that can't resolve a workpad
    (most direct-call unit tests construct ``NodeContext`` without a real
    on-disk run) must still get the exact same sealed ``AcquireOutput`` as
    before this packet, so every failure mode here degrades to ``None``
    rather than raising.
    """

    try:
        resolved = _resolved(context, home_root, target)
        run_id = context.run_id
        if not run_id:
            return None
        return ProgressWriter(resolved.path / "runs" / run_id)
    except Exception:  # noqa: BLE001 - progress must never break the sealed run
        return None


def _resolved(context: NodeContext, home_root: Path | None, target: Path | None) -> ResolvedWorkpad:
    if home_root is not None or target is not None:
        return resolve_workpad(
            home_root=home_root or Path.home() / ".gigai",
            requested_target=target,
            gig_id=context.gig_id,
            allow_semantic_state=True,
        )
    workpad = Path(context.workpad_path)
    return ResolvedWorkpad(
        project_id=context.project_id,
        gig_id=context.gig_id,
        path=workpad,
        target_root=workpad,
        target_kind="directory",
    )


def _watchlist_entries(watchlist: WatchlistClient) -> tuple[WatchlistEntry, ...]:
    for name in ("active_entries", "list_active", "entries", "list"):
        method = getattr(watchlist, name, None)
        if callable(method):
            try:
                values = method()
            except TypeError:
                continue
            return tuple(v if isinstance(v, WatchlistEntry) else WatchlistEntry.from_json(v) for v in values)
    values = getattr(watchlist, "active", ())
    return tuple(v if isinstance(v, WatchlistEntry) else WatchlistEntry.from_json(v) for v in values)


def _watchlist_add(watchlist: WatchlistClient, row: PostingRow, *, query_key: str, batch_id: str) -> str | None:
    if row.board_token is None:
        return None
    entry = WatchlistEntry(
        watchlist_id=f"scout_watchlist:{row.provider.value}:{row.board_token}",
        provider=row.provider,
        board_token=row.board_token,
        company=row.company,
        state="active",
        first_seen=WatchlistFirstSeen(SourceKind.EXA, row.url, query_key, batch_id, _now()),
    )
    result = watchlist.add_to_watchlist(entry)
    return getattr(result, "watchlist_id", entry.watchlist_id)


def _prior_observations(root: Path, current_batch: str) -> dict[str, str | None]:
    base = root / "records" / "scout-acquisition"
    result: dict[str, str | None] = {}
    if not base.is_dir():
        return result
    for input_file in sorted(base.glob("*/input.json")):
        if input_file.parent.name == current_batch:
            continue
        try:
            payload = json.loads(input_file.read_text())
            for row in payload.get("rows", []):
                url = row.get("url") or row.get("normalized_url")
                if url:
                    result[normalize_url(str(url))] = row.get("source_snapshot", {}).get("content_sha256") or row.get("content_sha256")
        except (OSError, ValueError, TypeError):
            continue
    return result


def _default_profile_id(resolved: ResolvedWorkpad | None) -> str | None:
    """The gig's migrated-default profile id, or ``None`` if unresolvable.

    S25 F1-b / Legacy-run policy: a run sealed before F1-b (no
    ``profile_ref``) is attributed to the gig's ``origin ==
    "migrated_default"`` profile for cache-key purposes -- never "visible to
    all profiles," never "excluded" (see the S25 spike's Legacy-run policy
    section). ``resolved`` is ``None`` for the many direct-call unit tests
    that construct a fake/non-journaled workpad (``_resolved`` in this same
    module returns a plain ``ResolvedWorkpad`` for those, but
    ``profile_records.list_profiles`` requires a REAL journaled workpad) --
    degrades to ``None`` rather than raise, exactly like
    ``_run_profile_identity``'s own "no sealed input -> None" precedent:
    callers treat ``None`` as "cannot attribute," never a match.
    """

    if resolved is None:
        return None
    try:
        from .. import profile_records
    except ImportError:
        return None
    try:
        profiles = profile_records.list_profiles(resolved)
    except Exception:
        return None
    for profile in profiles:
        if profile.origin == "migrated_default":
            return profile.profile_id
    return None


@dataclass(frozen=True)
class _RunProfileIdentity:
    """A run's sealed ``(resume_revision_id, profile_id)`` pair for the A2 cache key."""

    resume_revision_id: str
    profile_id: str | None


def _run_profile_identity(root: Path, run_id: str, *, default_profile_id: str | None) -> _RunProfileIdentity | None:
    """The pinned resume revision + profile identity sealed for ``run_id``.

    uat-bug-009: reads ``runs/<run_id>/sealed/find-jobs-run-input.json`` --
    the same sealed file ``proposal_execution.py``'s own ``_read_sealed_config``
    reads for its config -- rather than growing ``AcquireInput``'s sealed
    contract with a resume field (a schema change). Written by
    ``run.launch_find_jobs_run`` before the graph is even scheduled, so it is
    on disk before this node's body runs on a real run; degrades to ``None``
    for the many direct-call unit tests that construct ``AcquireInput``
    without a real on-disk run (older run dirs too) -- callers treat ``None``
    the same as "no config": never a match, so an unchanged row's prior
    assessment is never trusted without a known, current resume revision to
    compare it against.

    S25 A2/F1-b: the corrected cache key adds ``profile_id`` ALONGSIDE
    ``resume_revision_id`` (never replacing it -- r1's design defect the
    coordinator's r2 review fixed, see the S25 spike's A2 section). A run
    with a sealed ``profile_ref`` uses its own ``profile_id`` directly; a
    legacy run (no ``profile_ref``) is attributed to ``default_profile_id``
    (the Legacy-run policy) but KEEPS its own sealed
    ``pinned_resume.revision_id`` -- never coerced to any fixed stand-in.
    """

    path = root / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        from .contracts import FindJobsRunInput

        payload = json.loads(path.read_text(encoding="utf-8"))
        sealed = FindJobsRunInput.from_json(payload)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    profile_id = sealed.profile_ref.profile_id if sealed.profile_ref is not None else default_profile_id
    return _RunProfileIdentity(sealed.pinned_resume.revision_id, profile_id)


def _read_sealed_run_input_for_rank(root: Path, run_id: str) -> object | None:
    """Same sealed-file read as ``_run_profile_identity``, returning the whole DTO."""

    path = root / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        from .contracts import FindJobsRunInput

        return FindJobsRunInput.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _read_resume_text_for_rank(
    resolved: ResolvedWorkpad, sealed: object, *, home_root: Path
) -> str | None:
    """The sealed run's pinned resume text, or ``None`` if it can't be read.

    P6: ranking is display-ordering only, never a sealed authority the way
    ``proposal_execution._read_pinned_resume`` is for assess -- so this
    degrades to ``None`` (no rank calls made) on any failure (resume record
    missing, digest mismatch, I/O error) rather than raising and failing the
    whole acquire step. Never verifies the digest match `_read_pinned_resume`
    enforces; a stale/rotated resume at rank time just means a slightly-stale
    score, corrected the moment the real assess call re-derives the matrix.

    uat-bug-021 root cause, two defects, both swallowed by the bare
    ``except`` that was here, so every run since P6 ranked nothing:

    1. the import read ``from ..private_records`` (``gigai.scout``, where
       no such module exists) and raised ``ModuleNotFoundError`` on every
       call; the module is ``gigai.private_records``;
    2. the lookup was given ``resolved.path``, the WORKPAD, as its target,
       which ``resolve_workpad`` refuses ("target is not bound to a GigAI
       project"). The target is ``resolved.target_root``.

    The exception's type is logged; its message may name a path, so it is not.
    """

    try:
        from ...private_records import read_record

        value = read_record(
            home_root=home_root,
            requested_target=resolved.target_root,
            record_id=sealed.pinned_resume.record_id,  # type: ignore[attr-defined]
            revision_id=sealed.pinned_resume.revision_id,  # type: ignore[attr-defined]
            content=True,
            gig_id=resolved.gig_id,
        )
    except Exception as exc:  # noqa: BLE001 - ranking never fails acquire; the type is logged
        _rank_logger().warning("rank: the run's resume could not be read: %s", type(exc).__name__)
        return None
    content = value.get("content")
    if not isinstance(content, bytes):
        _rank_logger().warning("rank: the run's resume record has no content")
        return None
    return content.decode("utf-8", errors="replace")


def _rank_logger() -> logging.Logger:
    return logging.getLogger("gigai.scout.server")


def _title_tier(row: PostingRow, roles: Sequence[str]) -> int:
    title = normalize_title(row.title or "")
    wanted = [normalize_title(str(role)) for role in roles if str(role).strip()]
    if title in wanted:
        return 0
    if any(role and role in title for role in wanted):
        return 1
    return 2


def _rank_order(rows: Sequence[PostingRow], roles: Sequence[str]) -> list[PostingRow]:
    """The order the ranking pass asks about ``rows``: the most promising first.

    SCOPE-ADD-3 C1: EVERY row is ranked (nothing is left out, duplicates
    included); the order only decides which rows a pass cut short by its
    call cap still scores. A title that IS one of the target roles, then a
    title that contains one, then the rest; inside each, the newest first;
    ties in ``rows`` order.
    """

    ordered = sorted(rows, key=lambda row: row.published_at or "", reverse=True)
    ordered.sort(key=lambda row: _title_tier(row, roles))
    return ordered


class _OverCapPostingLines:
    """uat-bug-031: over the import cap, each posting's progress line is written as its rank batch lands.

    A run at or under the cap writes every line up front and ``rank``
    attaches to it as batches land. Over the cap, which rows the run keeps
    is only known after ranking, and writing every matched row up front
    would show rows that are later dropped. So the lines follow the ranked
    batches (``read_progress`` orders them by rank: the grid fills and
    re-orders while the pass runs), and :meth:`finish` writes the imported
    rows no batch reached (a pass that failed open or was cut short) and
    then ``imported.json``, which narrows ``postings`` to the imported
    rows. A line is appended at most once per posting and once per batch
    (one write), never rewritten: 1,458 matched rows are 1,458 lines at most.
    """

    def __init__(self, progress: ProgressWriter, rows: Sequence[PostingRow], outcomes: Mapping[str, RowOutcome]) -> None:
        self._progress = progress
        self._rows = {row.normalized_url: row for row in rows}
        self._outcomes = outcomes
        self._written: set[str] = set()

    def landed(self, normalized_urls: Sequence[str]) -> None:
        lines = []
        for url in normalized_urls:
            row = self._rows.get(url)
            if row is None or url in self._written:
                continue
            self._written.add(url)
            lines.append((row.to_json(), self._outcomes[url].value))
        self._progress.postings_acquired(lines)

    def finish(self, imported: Sequence[PostingRow]) -> None:
        self.landed([row.normalized_url for row in imported])
        self._progress.import_capped(cap=IMPORT_ROW_CAP, normalized_urls=[row.normalized_url for row in imported])


@dataclass(frozen=True)
class _RankStep:
    """What the run's ranking step did: the pass (``None`` when skipped before it) and why."""

    result: "RankResult | None"
    reason: str | None = None


def _rank_rows_with_status(
    rows: Sequence[PostingRow],
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    config: FindJobsConfig,
    model_target: object,
    home_root: Path | None,
    progress: ProgressWriter | None,
    on_landed: Callable[[tuple[str, ...]], None] | None = None,
) -> _RankStep:
    """SCOPE-ADD-3 C1: rank ``rows`` with the run's own model target, streamed to ``progress/rank.jsonl``.

    Preconditions, checked in this order, each a skip reason:
    ``no_candidates``, ``no_home``, ``no_run_input``, ``no_resume``. Then
    one ``model_rank`` pass (``rank_run.run_pass``: batch 50, K=8 or 4,
    capped at ``rank_run.run_call_cap``), which never raises for a model
    problem: an unavailable target is ``status="skipped"``, a cap or an
    invalid batch leaves rows unscored with ``fail_open_reason``. Anything
    else (a bug) is ``error:<ExceptionType>``: ranking never fails acquire.
    """

    from . import rank_run

    if not rows:
        return _RankStep(None, "no_candidates")
    if home_root is None:
        return _RankStep(None, "no_home")
    sealed = _read_sealed_run_input_for_rank(resolved.path, run_id)
    if sealed is None:
        return _RankStep(None, "no_run_input")
    resume_text = _read_resume_text_for_rank(resolved, sealed, home_root=home_root)
    if not resume_text:
        return _RankStep(None, "no_resume")
    try:
        cancel = threading.Event()
        streamer = rank_run.RankStreamer(progress, total=len(rows), cancel=cancel, on_landed=on_landed)
        result = rank_run.run_pass(
            _rank_order(rows, config.roles),
            resume_text=resume_text,
            prefs=rank_run.rank_prefs(config),
            model_target=model_target,
            home_root=home_root,
            streamer=streamer,
            cancel=cancel,
            target=getattr(resolved, "target_root", None),
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001 - ranking never fails acquire; the type is recorded and logged
        return _RankStep(None, f"error:{type(exc).__name__}")
    return _RankStep(result)


def _rank_candidates(
    rows: list[PostingRow],
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    config: FindJobsConfig,
    model_target: object = None,
    home_root: Path | None,
    progress: ProgressWriter | None = None,
    on_landed: Callable[[tuple[str, ...]], None] | None = None,
    **_unused: object,
) -> tuple:
    """SCOPE-ADD-3 C1: the run's ranking step. One ``RankScore`` per row of ``rows``, or ``()``.

    Runs at the seam between the acquire rows and selection: the run's own ``model_target`` (the operator's local
    CLI, ``codex_cli`` by default) ranks EVERY row that passed the filters.
    Never raises. Fails open: a skipped or failed pass returns ``()`` (or
    unscored entries), which leaves the import cap and the selection in
    today's order (``selection.rank_rows`` with no scores), and assess runs
    regardless.

    What happened is never silent: ``progress/rank.json`` (``rank_status``
    on ``GET /progress``) says ``scored N of M`` or ``skipped: <reason>``,
    ``progress/rank.jsonl`` has one line per landed batch, the full pass is
    sealed as ``runs/<run_id>/outputs/rank.json`` (journal-committed), and
    one log line says it: INFO when every row was scored, WARNING otherwise.
    """

    from . import rank_run

    step = _rank_rows_with_status(
        rows,
        resolved=resolved,
        run_id=run_id,
        config=config,
        model_target=model_target if model_target is not None else config.default_model_target,
        home_root=home_root,
        progress=progress,
        on_landed=on_landed,
    )
    result = step.result
    status = rank_run.status_json(result, total=len(rows), reason=step.reason)
    if progress is not None:
        progress.rank_status(status)
    if result is None or result.status == "skipped":
        _rank_logger().warning("rank (%s, acquire): skipped: %s", run_id, status["reason"])
    elif result.status == "complete":
        _rank_logger().info("rank (%s, acquire): %s", run_id, status["text"])
    else:
        _rank_logger().warning("rank (%s, acquire): %s (%s)", run_id, status["text"], status["reason"])
    if result is None:
        return ()
    if result.postings:
        # Never raises (a refused commit is logged): sealing never fails acquire.
        rank_run.seal_rank_json(
            resolved,
            f"runs/{run_id}",
            rank_run.rank_json_bytes(result, run_id=run_id, kind="run"),
            front_matter={"run_id": run_id, "schema_version": "scout-rank:1"},
        )
    if result.status == "skipped":
        return ()
    return rank_run.to_rank_scores(result, rows)


@dataclass(frozen=True)
class _PriorAssessment:
    """One posting's most recent *successful* assessment from an earlier run."""

    result: "AssessmentResult"
    resume_revision_id: str
    profile_id: str | None
    run_date: str | None
    # 0110-034b: the story bank marks the producing run sealed with its
    # output (``AssessOutput.story_bank.entries``); ``None`` for a run from
    # before the bank reached runs, which reads as "saw an empty bank".
    bank_marks: Mapping[str, str] | None = None
    # 0110-035: the assess prompt version and the candidate-constraints
    # digest the producing run sealed; ``None`` for a run sealed before them.
    prompt_version: str | None = None
    constraints_digest: str | None = None


def _prior_assessments(
    root: Path, current_run_id: str, *, default_profile_id: str | None = None
) -> dict[str, _PriorAssessment]:
    """Every URL's latest successful assessment from an earlier run's sealed output.

    uat-bug-009 root cause: acquire's candidate loop excluded every
    ``UNCHANGED`` row outright (this function is what lets it stop doing
    that for a row that was never actually, successfully assessed --
    ``NEW_never_assessed``/never-run-2-2-2 the operator's evidence run.md:
    a failed run leaves postings acquired but with no ``outputs/assess.json``
    at all, so those postings are already excluded here -- see the
    ``AssessOutput.assessments`` scan below only ever iterating *sealed*
    outputs that exist).
    ``runs/*/outputs/assess.json`` (sealed per run, never cleaned up) is the
    only durable, resume-revision-tagged record of "which postings were
    actually, successfully assessed" -- ``records/scout-proposals/...`` (the
    R1 immutable revision layer) is keyed by opaque record/revision uuids
    with no content-digest index, so it cannot answer "was this digest ever
    assessed" without an unbounded scan of every proposal record ever
    written; ``AssessOutput`` is already the exact bounded, per-run answer.
    Later runs win ties (sorted by directory name, which is the run's UUID --
    not a true time order, but the exact "from run <date>" wall-clock label
    is resolved separately, from ``outputs/assess.json``'s own mtime, by the
    caller that needs to display it -- ordering here only decides which
    *result* to prefer when a posting was assessed successfully more than
    once, and the newest successful one is always the more useful carry-
    forward, so last-write-wins over ``sorted()`` order is adequate).

    S25 A2/F1-b: each entry also carries the PRODUCING run's profile
    identity (``_run_profile_identity``, legacy runs attributed to
    ``default_profile_id``) -- the caller compares it against the CURRENT
    run's own profile identity before trusting a carry-forward, so a
    profile-B run never carries forward profile-A's assessment of the same
    URL even if their resume revisions happened to coincide.
    """

    from .contracts import AssessOutput

    result: dict[str, _PriorAssessment] = {}
    runs_dir = root / "runs"
    if not runs_dir.is_dir():
        return result
    for output_file in sorted(runs_dir.glob("*/outputs/assess.json")):
        run_id = output_file.parent.parent.name
        if run_id == current_run_id:
            continue
        try:
            payload = json.loads(output_file.read_text(encoding="utf-8"))
            output = AssessOutput.from_json(payload)
        except (OSError, ValueError, TypeError):
            continue
        resume_revision_id = output.pinned_resume.revision_id
        identity = _run_profile_identity(root, run_id, default_profile_id=default_profile_id)
        profile_id = identity.profile_id if identity is not None else default_profile_id
        run_date = None
        try:
            run_date = datetime.fromtimestamp(output_file.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except OSError:
            pass
        bank_marks = None if output.story_bank is None else output.story_bank.entries
        for assessment in output.assessments:
            result[assessment.posting.normalized_url] = _PriorAssessment(
                assessment, resume_revision_id, profile_id, run_date, bank_marks, output.prompt_version, output.constraints_digest
            )
    return result


class _CapturedResponse:
    """Redacted record of one HTTP exchange, kept for U26 raw storage.

    Only the response body, status, and the request URL with its query keys
    stripped are kept -- never request headers (where API keys live) and
    never response headers (which can carry rate-limit/auth echo values).
    Request headers are never even read by :class:`_RecordingHTTPClient`.
    """

    __slots__ = ("source", "url", "status_code", "body")

    def __init__(self, source: str, url: str, status_code: int, body: bytes) -> None:
        self.source = source
        self.url = url
        self.status_code = status_code
        self.body = body


class _RecordingHTTPClient:
    """Wraps an httpx-like client to capture every response body (U26).

    Transparent pass-through: `exa.search()`/`ats.list_board()` call `.get`/
    `.post` exactly as before and get the real response back unchanged; this
    just also appends a redacted :class:`_CapturedResponse` to `captured` as
    a side effect, with `source` derived from the request URL's hostname via
    `source_for_url`, since the underlying protocols don't pass a source
    name through explicitly.
    """

    def __init__(self, client: Any, *, source_for_url: "Callable[[str], str]") -> None:
        self._client = client
        self._source_for_url = source_for_url
        self.captured: list[_CapturedResponse] = []

    def _record(self, url: str, response: Any) -> Any:
        try:
            body = response.content
        except Exception:  # noqa: BLE001 - never let capture break the real call
            body = b""
        source = self._source_for_url(url)
        self.captured.append(_CapturedResponse(source, url, getattr(response, "status_code", 0), body))
        return response

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        response = self._client.get(url, *args, **kwargs)
        return self._record(url, response)

    def post(self, url: str, *args: Any, **kwargs: Any) -> Any:
        response = self._client.post(url, *args, **kwargs)
        return self._record(url, response)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _strip_query(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _source_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if "exa.ai" in host:
        return "exa"
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    return "other"


def _write_raw_payloads(resolved: ResolvedWorkpad, run_id: str, captured: Sequence[_CapturedResponse]) -> None:
    """Persist redacted raw provider responses under runs/<run_id>/raw/ (U26).

    Gzip-compressed, one file per response, plus an index.json listing
    source/url/status/bytes/sha256 for every stored (and every skipped, once
    the cap is hit) response. Never writes request headers, response
    headers, or API keys -- only ``_CapturedResponse.body`` (the JSON
    response bytes) and the query-stripped URL.
    """

    if not captured:
        return
    raw_root = resolved.path / "runs" / run_id / "raw"
    per_source_counts: dict[str, int] = {}
    index_entries: list[dict[str, object]] = []
    total_bytes = 0
    for item in captured:
        gzipped = gzip.compress(item.body, compresslevel=6)
        entry: dict[str, object] = {
            "source": item.source,
            "url": _strip_query(item.url),
            "status": item.status_code,
            "bytes": len(item.body),
            "sha256": digest_imported_bytes(item.body),
        }
        if total_bytes + len(gzipped) > RAW_PAYLOAD_CAP_BYTES:
            entry["stored"] = False
            entry["skipped_reason"] = "raw_payload_cap_reached"
            index_entries.append(entry)
            continue
        n = per_source_counts.get(item.source, 0)
        per_source_counts[item.source] = n + 1
        source_dir = raw_root / item.source
        source_dir.mkdir(parents=True, exist_ok=True)
        path = source_dir / f"{n}.json.gz"
        path.write_bytes(gzipped)
        entry["stored"] = True
        entry["path"] = path.relative_to(resolved.path).as_posix()
        total_bytes += len(gzipped)
        index_entries.append(entry)
    raw_root.mkdir(parents=True, exist_ok=True)
    index = {
        "schema_version": "scout-acquire-raw-index:1",
        "cap_bytes": RAW_PAYLOAD_CAP_BYTES,
        "cap_note": "gzip-compressed bytes counted against the cap; entries after the cap is reached are listed but not stored.",
        "entries": index_entries,
    }
    (raw_root / "index.json").write_text(canonical_json_bytes(index).decode("utf-8"))


def _public_row(row: PostingRow) -> dict[str, object]:
    digest = _digest(row)
    opportunity_digest = digest_imported_bytes(row.normalized_url.encode("utf-8"))
    return {
        "opportunity_id": opportunity_digest.split(":", 1)[-1][:32],
        "snapshot_id": digest.split(":", 1)[-1][:32],
        "source_kind": "agent_discovered",
        "title": row.title,
        "employer": row.company,
        "url": row.url,
        "acquisition_state": "considered",
        "source_snapshot": {
            "source_kind": row.source_kind.value,
            "locator": row.normalized_url,
            "url": row.url,
            "status": "observed",
            "captured_at": _now(),
            "content_sha256": digest,
            "media_type": "application/json",
        },
    }


def acquire_node(
    context: NodeContext,
    input: AcquireInput,
    *,
    http_client: Any,
    exa: ExaSearchClient,
    ats: ATSBoardClient,
    watchlist: WatchlistClient,
    home_root: Path | None = None,
    target: Path | None = None,
    limits: AcquireLimits | None = None,
    boards_from: str = BOARDS_FROM_INDEX,
) -> AcquireOutput:
    """The acquire node of a find-jobs run.

    ``boards_from`` says where the watchlist's rows come from. The default,
    ``"index"``, reads the company index `gigai scout sources update` wrote
    and asks no board (option E's new Exa companies aside). ``"fetch"``
    fetches every watchlist board in this call and has to be asked for by
    name.
    """

    if boards_from not in (BOARDS_FROM_FETCH, BOARDS_FROM_INDEX):
        raise FindJobsContractError("invalid_value", "boards_from must be 'fetch' or 'index'")
    # B4: `progress.finish_step("acquire", ...)` must run on every exit path
    # (success or a raised AcquireAllSourcesFailedError/other exception), so
    # the real body is a nested function and this outer frame is the single
    # try/finally around it -- the sealed control flow inside is untouched.
    progress = _progress_writer(context, home_root, target)
    if progress is not None:
        progress.start_step("acquire")
    try:
        output = _acquire_node_body(
            context,
            input,
            http_client=http_client,
            exa=exa,
            ats=ats,
            watchlist=watchlist,
            home_root=home_root,
            target=target,
            progress=progress,
            limits=limits if limits is not None else AcquireLimits.from_environment(),
            boards_from=boards_from,
        )
    except BaseException:
        if progress is not None:
            progress.finish_step("acquire", ok=False)
        raise
    if progress is not None:
        progress.finish_step("acquire", ok=True)
    return output


def _acquire_node_body(
    context: NodeContext,
    input: AcquireInput,
    *,
    http_client: Any,
    exa: ExaSearchClient,
    ats: ATSBoardClient,
    watchlist: WatchlistClient,
    home_root: Path | None,
    target: Path | None,
    progress: ProgressWriter | None,
    limits: AcquireLimits,
    boards_from: str = BOARDS_FROM_INDEX,
) -> AcquireOutput:
    started_at = time.monotonic()
    batch_id = _safe_batch_id(context.operation_key)
    # uat-bug-011: what the journal import can refuse without seeing the rows
    # (the workpad, the batch identity, a batch already bound) is refused
    # here, before any source is fetched, not after a 19-minute board pass.
    resolved = _resolved(context, home_root, target)
    preflight_public_import(resolved=resolved, batch_id=batch_id)
    failures: list[FailureRow] = []
    rows: list[PostingRow] = list(input.rows)
    watchlist_refs: list[str] = []
    source_outcomes: dict[str, bool] = {}
    exa_discovered: tuple[PostingRow, ...] = ()
    recording_client = _RecordingHTTPClient(http_client, source_for_url=_source_from_url) if http_client is not None else None
    active_client = recording_client if recording_client is not None else http_client

    if not rows:
        if input.config.sources.exa:
            exa_ok = False
            try:
                discovered = tuple(exa.search(active_client, input.config, home_root=home_root))
                exa_discovered = discovered
                rows.extend(discovered)
                for row in discovered:
                    ref = _watchlist_add(watchlist, row, query_key=row.query_key, batch_id=batch_id)
                    if ref:
                        watchlist_refs.append(ref)
                exa_ok = True
            except Exception as exc:
                failures.append(FailureRow(SourceKind.EXA, "exa", None, type(exc).__name__.lower(), "Exa search failed"))
            source_outcomes["exa"] = exa_ok
        if input.config.sources.ats:
            ats_ok = False
            # Q2: seed the watchlist from the bundled company catalog (filtered
            # by the saved prefs) BEFORE listing it, so a first run after setup
            # already covers every catalog board the prefs admit.
            seed_failure = _seed_watchlist(
                _resolved(context, home_root, target), home_root=home_root, target=target, progress=progress
            )
            if seed_failure is not None:
                failures.append(seed_failure)
            try:
                boards = _watchlist_entries(watchlist)
                if not boards:
                    ats_ok = True
                elif boards_from == BOARDS_FROM_INDEX:
                    # N11-C: the search reads the company index that
                    # `gigai scout sources update` wrote and makes NO board
                    # request (there is no client in that call). It returns
                    # what the fetch below returns, so sealing, the reuse
                    # rule, the import cap and the selection are untouched.
                    board_rows, board_failures, board_summary = _read_index(
                        boards, home_root=home_root, config=input.config, progress=progress, started_at=started_at
                    )
                    rows.extend(board_rows)
                    failures.extend(board_failures)
                    # Option E: the companies Exa found in THIS run that the
                    # index does not hold yet are fetched now (at most
                    # EXA_NEW_BOARD_FETCH_CAP), indexed, and their postings
                    # join the run with their descriptions.
                    new_rows, new_failures, exa_new = _fetch_exa_new_boards(
                        exa_discovered,
                        batch_id=batch_id,
                        home_root=home_root,
                        ats=ats,
                        client=active_client,
                        config=input.config,
                        limits=limits,
                    )
                    rows.extend(new_rows)
                    failures.extend(new_failures)
                    board_summary = _with_exa_new(board_summary, exa_new, matched=len(new_rows))
                    ats_ok = bool(board_summary.get("cached") or board_summary.get("fetched"))
                    if progress is not None:
                        progress.boards_finished(board_summary)
                    print(_index_line(board_summary), file=sys.stderr)
                else:
                    # Q2: per-provider worker pools, a per-provider request
                    # pacer, a per-board response cache (conditional GETs /
                    # digest reuse), a title prefilter before any detail
                    # request, a run-time budget that skips (and records)
                    # the boards it can't reach, and one progress line per
                    # board -- see `_fetch_boards`. Sealing, the reuse rule
                    # and the diversity selection below are untouched.
                    board_rows, board_failures, board_summary = _fetch_boards(
                        boards,
                        ats=ats,
                        client=active_client,
                        config=input.config,
                        limits=limits,
                        cache=_board_cache(home_root),
                        progress=progress,
                        started_at=started_at,
                        catalog_counts=_catalog_us_counts() if home_root is not None else None,
                    )
                    rows.extend(board_rows)
                    failures.extend(board_failures)
                    ats_ok = bool(board_summary.get("fetched") or board_summary.get("cached"))
                    if progress is not None:
                        progress.boards_finished(board_summary)
                    print(
                        "scout acquire: ATS boards {total}: {fetched} fetched, {cached} cached, {failed} failed, "
                        "{skipped} skipped (budget); {requests} requests, {cache_hits} cache hits, "
                        "{listed} postings listed, {prefiltered_out} prefiltered out, {matched} matched, "
                        "{elapsed_seconds}s".format(**board_summary),
                        file=sys.stderr,
                    )
                    print(_rotation_line(board_summary.get("rotation")), file=sys.stderr)
            except Exception as exc:
                failures.append(FailureRow(SourceKind.ATS, "ats", None, type(exc).__name__.lower(), "ATS watchlist fetch failed"))
            source_outcomes["ats"] = ats_ok

        # B5 (0.1.8.1, live UAT: a run where every source failed still
        # reported "succeeded" with 0 postings). The original check here
        # was `source_outcomes and not any(source_outcomes.values())`, which
        # has a vacuous-success hole: ATS sets `ats_ok = True` whenever its
        # watchlist has *no boards to fetch* (`boards` empty, :431) -- a
        # legitimate "nothing to do" case on its own, but on a *first* run
        # (or any run where Exa is the only source that would have populated
        # the watchlist and Exa itself failed), that same "no boards yet"
        # state is indistinguishable from "ATS succeeded". `exa: False,
        # ats: True (vacuous)` then made `any(source_outcomes.values())`
        # true, and the node returned COMPLETE with an empty batch instead
        # of raising. The correct invariant (per the B5 ticket: "0 postings
        # and >=1 source failure") doesn't depend on ATS's vacuous-ok
        # bookkeeping at all -- it only needs to know whether *any* row was
        # actually produced and whether *any* source recorded a real
        # failure. `source_outcomes` is kept only for the error message's
        # per-source failure codes below, not as the raise condition itself.
        # Q2: the single `time_budget_exceeded` row is a bookkeeping marker
        # (boards skipped, recorded per board in progress), not a source
        # failure, UNLESS the budget left every board unfetched -- then the
        # run genuinely produced nothing from ATS and must not seal an
        # empty batch as success.
        # N11-C: a search with nothing stored to read says what to do about
        # it, in words the UI can show as they are.
        update_required = [failure for failure in failures if failure.code == SOURCES_UPDATE_REQUIRED_CODE]
        if not rows and update_required and len(update_required) == len(failures):
            raise AcquireAllSourcesFailedError(SOURCES_UPDATE_REQUIRED_CODE, update_required[0].message)
        if not rows and (
            any(failure.code != BUDGET_EXCEEDED_CODE for failure in failures)
            or (failures and not source_outcomes.get("ats", False))
        ):
            codes = ", ".join(f"{failure.source_kind.value}:{failure.code}" for failure in failures)
            detail = f" ({codes})" if codes else ""
            raise AcquireAllSourcesFailedError(
                "acquire_all_sources_failed",
                f"every enabled acquisition source failed{detail}",
            )

    rows = _merge_exa_and_ats_rows(rows)

    # B1 (0.1.8.1, operator: "why are we doing filtering on fucking ui?"):
    # apply the country/location (`exclusion_reason`) and role filters right
    # after fetch, on the full merged set -- not left for UI chips to apply
    # against every row the ATS APIs returned worldwide. A non-matching row
    # is dropped from `results`/`outputs/acquire.json` entirely (never
    # reaches the NEW/EDITED/selection accounting below); `raw/` (written
    # separately, `_write_raw_payloads`) still keeps every response
    # unfiltered for audit/replay. Per-reason counts are recorded additively
    # on the output (`dropped_counts`) so the drop is visible on the run
    # without re-deriving it from raw/ vs acquire.json.
    #
    # 0.1.8.1 r1 (coordinator review, B1's region-token rule): the
    # drop-count key uses `location_mismatch_detail` rather than
    # `exclusion_reason`'s own (coarse, stable) return value, so a
    # region-only location ("AMER", "EMEA", ...) is auditable as its own
    # REGION_ONLY bucket instead of being folded into LOCATION_MISMATCH --
    # `exclusion_reason` itself is still what actually decides whether the
    # row is dropped at all (identical result either way; this only changes
    # which key the drop gets counted under).
    role_tags = open_tag_store(home_root)
    kept_rows: list[PostingRow] = []
    drop_counts: dict[NotAssessedReason, int] = {}
    for row in rows:
        reason = exclusion_reason(row, input.config)
        if reason is NotAssessedReason.LOCATION_MISMATCH:
            reason = location_mismatch_detail(row, input.config) or reason
        if reason is None and not _role_match(row, input.config.roles, role_tags):
            reason = NotAssessedReason.ROLE_MISMATCH
        # uat-bug-028: the config's work mode + area, for every source's
        # rows (the index search already applied it to the rows it read).
        if reason is None and not work_mode_fit(row, input.config).passes:
            reason = NotAssessedReason.WORK_MODE_MISMATCH
        if reason is not None:
            drop_counts[reason] = drop_counts.get(reason, 0) + 1
            continue
        kept_rows.append(row)
    rows = kept_rows

    # uat-bug-011: the journal checks every row's shape (bounded text, a
    # well-formed digest) and refuses the whole batch over one bad row.
    # Check each row now and leave a refused one out as a failure row: one
    # posting is lost, not the run.
    public_rows: dict[str, dict[str, object]] = {}
    shaped_rows: list[PostingRow] = []
    for row in rows:
        public = _public_row(row)
        refusal = public_row_refusal(public)
        if refusal is not None:
            failures.append(
                FailureRow(
                    row.source_kind,
                    row.query_key or "acquire",
                    row.url if 0 < len(row.url) <= 4096 else None,
                    refusal.code,
                    "posting left out: the public import refuses its shape",
                )
            )
            continue
        public_rows[row.normalized_url] = public
        shaped_rows.append(row)
    rows = shaped_rows

    current = {row.normalized_url: _digest(row) for row in rows}
    previous = _prior_observations(resolved.path, batch_id)
    url_diff = diff_url_sets(previous, current)
    added = {item.url for item in url_diff.added}
    edited = {item.url for item in url_diff.edited}
    # uat-bug-009 root cause: an UNCHANGED row (same content digest as an
    # earlier acquire) used to be excluded from `candidates` outright --
    # even when that earlier run never actually produced a successful
    # assessment for it (a failed run, an over-cap drop, a since-changed
    # resume revision). A run 1 that fails at assess then made every posting
    # in run 2 "unchanged" and permanently unassessable. Fixed: an UNCHANGED
    # row only stays excluded when a *successful* assessment of the exact
    # same content digest exists for the *current* resume revision -- config
    # inputs that affect assess (resume content, via its revision id; the
    # digest already captures the posting content itself) -- otherwise it's
    # still eligible for selection under the cap, same as NEW/EDITED. A
    # changed/unresolvable resume revision makes an earlier assessment not
    # count (never a match), so it never wrongly skips a posting the
    # operator's new resume hasn't actually been assessed against.
    #
    # S25 A2/F1-b: the corrected cache key adds `profile_id` ALONGSIDE the
    # resume-revision dimension (never replacing it -- see
    # `_run_profile_identity`'s own docstring for the r1 design defect this
    # fixes). A legacy run (no sealed `profile_ref`) is attributed to the
    # gig's migrated-default profile for this comparison, resolved once per
    # acquire call.
    default_profile_id = _default_profile_id(resolved)
    current_identity = _run_profile_identity(resolved.path, context.run_id, default_profile_id=default_profile_id)
    current_resume_revision_id = None if current_identity is None else current_identity.resume_revision_id
    current_profile_id = None if current_identity is None else current_identity.profile_id
    prior_assessments = _prior_assessments(resolved.path, context.run_id, default_profile_id=default_profile_id)
    # 0110-034b / 0110-035: an earlier assessment is not carried forward
    # when what it was made with has changed: the assess prompt version, the
    # candidate's constraints (sponsorship need, eligible countries, own
    # location), or the profile's story bank in a way that could change it
    # (an open question the bank can now answer; a cited bank answer edited,
    # deleted or unshared). Same rule, same function as assess's own
    # unchanged skip (``proposal_execution._basis_stale``). No home/target
    # (direct-call tests) or no profile: no bank.
    from .. import story_bank
    from ..assessment_core import assess_prompt_version, constraints_digest
    from ..contact_cleanup import same_resume_revision
    from ..proposal_execution import _basis_stale

    current_constraints = constraints_digest(
        visa_sponsorship_required=input.config.visa_sponsorship_required,
        countries=tuple(input.config.countries or ()),
        location=input.config.location or "",
        work_mode=input.config.effective_work_mode,  # 0110-038: the same digest the assess node seals
    )

    bank = (
        story_bank.assess_bank(home_root=home_root, target=target, profile_id=current_profile_id)
        if home_root is not None and target is not None
        else story_bank.AssessBank(None)
    )
    outcomes: dict[str, RowOutcome] = {}
    candidates: list[PostingRow] = []
    carried_forward: dict[str, _PriorAssessment] = {}
    over_import_cap = len(rows) > IMPORT_ROW_CAP
    for row in rows:
        if row.normalized_url in added:
            outcome = RowOutcome.NEW
        elif row.normalized_url in edited:
            outcome = RowOutcome.EDITED
        else:
            outcome = RowOutcome.UNCHANGED
        outcomes[row.normalized_url] = outcome
        is_candidate = outcome in {RowOutcome.NEW, RowOutcome.EDITED}
        if outcome is RowOutcome.UNCHANGED:
            prior = prior_assessments.get(row.normalized_url)
            if (
                prior is not None
                and prior.result.posting.content_sha256 == row.content_sha256
                and current_resume_revision_id is not None
                # 0110-046: the contact cleanup's clean copy is the same resume (the model never saw the removed lines).
                and same_resume_revision(home_root, prior.resume_revision_id, current_resume_revision_id)
                # S25 A2: profile_id must ALSO match -- a profile-B run never
                # carries forward profile-A's assessment of the same URL,
                # even when their resume revisions happen to coincide.
                # `None == None` is a legitimate match: no profile has ever
                # been migrated for this workpad at all (no profile system
                # engaged), which is exactly today's pre-F1-b behaviour and
                # must keep working unchanged, not be newly blocked by an
                # unattributable-profile false negative.
                and prior.profile_id == current_profile_id
                and not _basis_stale(
                    prior, bank=bank, constraints=current_constraints, prompt_version=assess_prompt_version(input.config.effective_work_mode)
                )
            ):
                carried_forward[row.normalized_url] = prior
            else:
                is_candidate = True
        if progress is not None and not over_import_cap:
            # B4: one line per posting kept after the B1 filter, appended as
            # acquire produces it -- this is what lets a card render before
            # the whole run (or even the whole acquire step) finishes. Over
            # the import cap, which rows the run keeps is only known once
            # they are ranked; their lines follow the ranked batches
            # (`_OverCapPostingLines`, uat-bug-031).
            progress.posting_acquired(row.to_json(), outcome=outcome.value)
        if input.selection_rule is SelectionRule.NEW_OR_EDITED_ROLE_MATCH and is_candidate:
            candidates.append(row)

    # SCOPE-ADD-3 C1: the run's ranking step. The run's own model target (the
    # operator's local CLI) ranks EVERY row that passed the filters, before
    # the import cap and the selection, streaming batch lines to
    # progress/rank.jsonl and sealing outputs/rank.json. Fails open by design
    # (no resume, target unavailable, call cap) -- `_rank_candidates` never
    # raises; an empty or unscored `rank_scores` leaves every ranking below
    # in today's date order, and assess runs regardless.
    # `rank_scores` is sealed onto `AcquireOutput` below so assess's own twin
    # recompute (`proposal_execution.py`) reuses the SAME scores rather than
    # re-ranking -- the two selections can never disagree.
    posting_lines = _OverCapPostingLines(progress, rows, outcomes) if progress is not None and over_import_cap else None
    rank_scores = _rank_candidates(
        rows,
        resolved=resolved,
        run_id=context.run_id,
        config=input.config,
        model_target=context.model_target,
        home_root=home_root,
        progress=progress,
        on_landed=None if posting_lines is None else posting_lines.landed,
    )

    # uat-bug-011 (P0, UAT N14): a full-catalog run matched 7,426 postings,
    # more than 512 of them passed every filter above, and the journal
    # import refused the whole batch at the very end. The run's row set is
    # now bounded here: the best `IMPORT_ROW_CAP` rows (`rank_rows`: rank
    # score with blocked rows demoted, then newest -- the same ranking the
    # selection below uses; SCOPE-ADD-3 C1: by RANK, not date) are
    # the run. The sealed rows, the progress postings, the selection and the
    # journal import all see exactly those rows; the rest are a count
    # (`not_imported_count`), never a failure. A row left out is not
    # recorded as observed, so `_prior_observations` has no entry for it and
    # it is NEW again the next time it is seen.
    #
    # Rotation (orchestrator decision 2026-09-27): where the score does not
    # separate two rows (the same score, or no scores at all), the row
    # no earlier run imported goes first, then the newest. "Imported before"
    # is `previous`: the rows of this target's earlier sealed batches, which
    # is exactly what an import recorded (the company index knows what a
    # sources update saw, not what a run imported). So without scores, runs
    # over more rows than the cap take different slices until every row has
    # had its turn.
    not_imported_count = max(0, len(rows) - IMPORT_ROW_CAP)
    if over_import_cap:
        ranked = rank_rows(rows, rank_scores, imported_before=frozenset(previous))
        imported = {row.normalized_url for row in ranked[:IMPORT_ROW_CAP]}
        rows = [row for row in rows if row.normalized_url in imported]
        candidates = [row for row in candidates if row.normalized_url in imported]
        carried_forward = {url: prior for url, prior in carried_forward.items() if url in imported}
        rank_scores = tuple(score for score in rank_scores if score.normalized_url in imported)
        # `removed` stays as diffed against everything that matched: a
        # posting left out of the import is still live, not removed.
        url_diff = URLSetDiff(
            added=tuple(item for item in url_diff.added if item.url in imported),
            removed=url_diff.removed,
            unchanged=tuple(item for item in url_diff.unchanged if item.url in imported),
            edited=tuple(item for item in url_diff.edited if item.url in imported),
        )

    results = [PostingRowResult(row, outcomes[row.normalized_url]) for row in rows]
    if posting_lines is not None:
        # uat-bug-031: the imported rows no batch reached, then
        # `imported.json`: `/progress` serves exactly the imported rows.
        posting_lines.finish(rows)

    # B2 (0.1.8.1 live UAT): the naive first-N-in-batch-order walk let one
    # board's postings fill the entire selection (a run saw 5/5 picks from
    # ClickHouse alone, 3 sharing a title). `select_for_assessment` (a pure,
    # independently-tested module) replaces that walk: dedupe near-identical
    # postings, cap how many one company can contribute, then round-robin
    # fill the remaining cap across companies.
    #
    # uat-bug-010 (UAT N13): the scores are passed in. Sorting `candidates`
    # by score before this call did nothing, because every step inside
    # re-sorts by date; the assess cap went to the newest postings, not the
    # best-ranked rows. `candidates` stays in `rows` order, the order assess's
    # recompute reads the sealed rows back in.
    # uat-bug-042: a cap of "all" is every new posting (up to
    # ASSESS_ALL_CEILING, no per-company cap); a number is today's selection.
    cap_limit, per_company = selection_limits(input.selection_cap)
    selection = select_for_assessment(candidates, cap=cap_limit, per_company=per_company, rank_scores=rank_scores)
    # `select_for_assessment` returns the same `PostingRow` objects it was
    # given (see selection.py's `ordered_selected`); the narrower `Candidate`
    # protocol is only its own input/output typing, so cast back for the
    # richer fields (`.url`, `_digest`) this module needs.
    selected_rows = cast("tuple[PostingRow, ...]", selection.selected)
    selected = [SelectedPosting(row.normalized_url, row.url, _digest(row), True) for row in selected_rows]
    # `selection.dropped`'s "duplicate"/"company_cap"/"over_cap" reasons feed
    # the same additive per-reason accounting as the exclusion-based drops
    # above (coordinator decision): "duplicate" maps onto the existing
    # NotAssessedReason.DUPLICATE; "company_cap" and "over_cap" both mean "an
    # otherwise-eligible row didn't fit under the cap" and fold onto the
    # existing NotAssessedReason.OVER_CAP -- no new enum value. Assess
    # re-derives the same per-row reason from the same pure helper for its
    # own not-assessed labeling (see proposal_execution.py), so the two can
    # never disagree.
    for selection_reason in selection.dropped.values():
        reason = NotAssessedReason.DUPLICATE if selection_reason == "duplicate" else NotAssessedReason.OVER_CAP
        drop_counts[reason] = drop_counts.get(reason, 0) + 1

    if progress is not None:
        # B4: the run's assess cap plus how many candidates it applies to
        # (operator: show "assessing 5 of 42 matches, cap 5"), known as soon
        # as acquire finishes selecting -- well before assess starts.
        progress.cap_known(
            cap=cap_limit,
            candidate_count=len(candidates),
            not_imported_count=not_imported_count,
            selected_count=len(selected),
        )

    if per_company is None:
        over_ceiling = sum(1 for reason in selection.dropped.values() if reason == "over_cap")
        if over_ceiling:
            print(
                f"scout acquire: full assessments \"all\": assessing the top {len(selected)} new postings "
                f"(the most one run assesses); {over_ceiling} more stay not assessed -- "
                "use \"Assess all new\" on the Jobs page for them",
                file=sys.stderr,
            )

    if not_imported_count:
        print(
            f"scout acquire: {len(rows)} postings imported; {not_imported_count} more matched, "
            f"not imported this run (import cap {IMPORT_ROW_CAP})",
            file=sys.stderr,
        )

    status = import_public_rows(resolved=resolved, batch_id=batch_id, rows=[public_rows[row.normalized_url] for row in rows] or [{
        "opportunity_id": "empty", "snapshot_id": digest_imported_bytes(b"empty")[:32], "source_kind": "agent_discovered", "title": "empty", "employer": "empty", "url": "https://example.invalid/empty", "acquisition_state": "excluded", "excluded_reason": "no_rows",
    }])
    if recording_client is not None:
        # Q2: board fetches complete on worker threads in arbitrary order;
        # sort the captured responses so raw/index.json is deterministic.
        captured = sorted(recording_client.captured, key=lambda item: (item.source, item.url, item.status_code))
        _write_raw_payloads(resolved, context.run_id, captured)
    progress_files = sorted((resolved.path / "records" / "scout-acquisition" / batch_id / "progress").glob("*.json"))
    progress_ref = progress_files[-1].relative_to(resolved.path).as_posix() if progress_files else ""
    return AcquireOutput(
        batch_id=batch_id,
        batch_ref=status.input_ref["path"],
        progress_ref=progress_ref,
        progress_status=ProgressStatus.COMPLETE if status.complete else ProgressStatus.FAILED,
        rows=tuple(results),
        failures=tuple(failures),
        url_set_diff=url_diff,
        watchlist_refs=tuple(dict.fromkeys(watchlist_refs)),
        selected_postings=tuple(selected),
        dropped_counts=tuple(DropCount(reason, count) for reason, count in sorted(drop_counts.items(), key=lambda item: item[0].value)),
        carried_forward_assessments=tuple(
            CarriedForwardAssessment(url, prior.result, prior.run_date)
            for url, prior in sorted(carried_forward.items())
        ),
        rank_scores=rank_scores,
        not_imported_count=not_imported_count,
    )


__all__ = [
    "ACQUIRE_BUDGET_ENV",
    "ATS_CONCURRENCY_ENV",
    "ATS_MIN_INTERVAL_ENV",
    "BUDGET_EXCEEDED_CODE",
    "IMPORT_ROW_CAP",
    "AcquireLimits",
    "acquire_node",
]
