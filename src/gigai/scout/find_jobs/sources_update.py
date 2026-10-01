"""``gigai scout sources update`` (N11-C): refresh every watchlist board, write the company index.

Operator direction 2026-09-27: refreshing the sources and searching are two
steps. This is the refresh: every company on the watchlist (the catalog
boards seeding admits plus the ones the operator or discovery added) gets
one conditional GET through the existing board client, at the existing
polite pacing, and its file in the company index (``company_index.py``) is
brought in step with the body the board cache now holds. The search side
(``index_search.read_indexed_boards``) reads that index and makes no board
request.

The engine is the acquire rotation, unchanged (``market_acquisition.
_fetch_boards``): least recently attempted boards first, per-provider pools
and pacer, a time budget, the last-fetched index flushed as it goes. So an
update is resumable: the boards a budget (or a killed process) left behind
lead the next one. This module only supplies the board list, listens to the
per-board outcomes, and writes the index and one status snapshot
(``<home>/cache/scout/companies/last-update.json``) that the CLI, the API
and a later search all read.

Nothing here writes a journal record except the watchlist seeding acquire
already does (one transition when the catalog or the prefs changed).

0110-025 (hourly background refresh) adds three things, all additive:

* the snapshot's ``failures`` block: a histogram of per-board failure codes
  (``http_429``, ``http_404``, ``timeout`` ...), so a provider pushing back
  is visible;
* :func:`run_refresh_tick`: ONE background tick. It asks the boards
  ``refresh_plan.plan_tick`` picks (busy boards, one slice of the quiet
  ones), spread evenly over the tick instead of at the polite maximum, and
  stops between boards when its stop event is set. It starts no thread: the
  server's tick loop calls it;
* ``stop`` on :func:`update_sources`: a stopped update ends ``partial`` with
  ``cancelled: true`` and the boards it did not ask counted in ``remaining``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any
import uuid

from .ats_board_clients import ATSBoardClients, BoardCache
from .company_index import (
    DEFAULT_STALE_AFTER_HOURS,
    INDEX_EMPTY,
    INDEX_READY,
    INDEX_STALE,
    CompanyChange,
    CompanyIndex,
    UpdateTotals,
    index_stamp,
    refresh_company,
)
from .contracts import FindJobsConfig, SourceToggles, WatchlistEntry
from .market_acquisition import CANCELLED_CODE, AcquireLimits, _catalog_us_counts, _fetch_boards, _seed_watchlist
from .refresh_plan import TICK_INTERVAL_SECONDS, BoardFacts, RefreshPlan, plan_tick

SOURCES_UPDATE_STATUS_SCHEMA = "scout-sources-update-status:1"
#: How often the running snapshot is rewritten (it is also the liveness
#: heartbeat another process reads before it starts a second update). The
#: first board that settles is written at once, so a short update still
#: shows progress.
SNAPSHOT_INTERVAL_SECONDS = 1.0
#: A ``running`` snapshot not rewritten for this long belongs to a process
#: that died: it reads as ``interrupted`` and no longer blocks a new update.
HEARTBEAT_TIMEOUT_SECONDS = 300.0

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"  # every board was attempted
STATUS_PARTIAL = "partial"  # the time budget left boards for the next update
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"  # a running snapshot whose process is gone

#: Who started an update (the snapshot's ``trigger``): the operator (CLI,
#: the Update sources button) or the background refresh tick.
TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"
#: How many failed boards the snapshot names (``failures.boards``); the
#: histogram (``failures.codes``) always counts every one.
FAILED_BOARDS_LISTED = 50
#: A tick spreads its requests over this long and gives up on what it has
#: not started by the budget; both leave room before the next hourly tick.
TICK_SPREAD_SECONDS = 3000.0
TICK_BUDGET_SECONDS = 3300.0
#: Test/operator override of :data:`TICK_SPREAD_SECONDS` (``0``: no spread,
#: the polite maximum, as a manual update).
TICK_SPREAD_ENV = "GIGAI_SCOUT_REFRESH_SPREAD_SECONDS"


class SourcesUpdateError(RuntimeError):
    """A sources update that could not start; ``code`` is stable, the message is for a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SourcesUpdateRunningError(SourcesUpdateError):
    def __init__(self) -> None:
        super().__init__("sources_update_running", "a sources update is already running")


def board_cache_for_home(home_root: Path) -> BoardCache:
    """``<home>/cache/scout/ats-boards``: the cache the acquire path already fills."""

    return BoardCache(Path(home_root) / "cache" / "scout" / "ats-boards")


def new_update_id() -> str:
    return f"sources_update_{uuid.uuid4().hex}"


def listing_config() -> FindJobsConfig:
    """A config that matches no title: board lists only, no Greenhouse detail requests."""

    return FindJobsConfig(
        roles=(),
        merged_queries=(),
        location=None,
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    )


def default_http_client() -> Any:
    """The HTTP client a find-jobs run's acquire node uses (same timeouts, no redirects, no proxies)."""

    from .bindings import _http_client

    return _http_client()


def load_effective_config(home_root: Path, target: Path) -> FindJobsConfig | None:
    """``find-jobs.json`` with the selected profile's titles, as a run would read it; ``None`` if unusable.

    Only ``roles`` matters here (which Greenhouse descriptions to fetch
    now), so an unreadable config or profile never stops an update: it
    falls back to the shared file, then to listing only.
    """

    from ...canonical import parse_json_bytes
    from ...workpad import resolve_workpad
    from .. import profile_records
    from .effective_config import overlay_selected_profile

    path = Path(target) / "find-jobs.json"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
    except (OSError, ValueError):
        return None
    try:
        resolved = resolve_workpad(home_root=Path(home_root), requested_target=Path(target), gig_id=None, allow_semantic_state=True)
        profile = profile_records.selected_profile(resolved, home_root=Path(home_root), target=Path(target))
    except Exception as exc:  # noqa: BLE001 - the shared roles are a fine fallback
        print(f"scout sources update: selected profile unavailable, using find-jobs.json roles ({type(exc).__name__})", file=sys.stderr)
        return config
    return overlay_selected_profile(config, profile)


def _parse(value: object) -> datetime | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def snapshot_is_live(snapshot: Mapping[str, object] | None, *, now: datetime | None = None) -> bool:
    """Whether ``snapshot`` is an update some process is still running."""

    if snapshot is None or snapshot.get("status") != STATUS_RUNNING:
        return False
    beat = _parse(snapshot.get("updated_at"))
    if beat is None:
        return False
    moment = datetime.now(timezone.utc) if now is None else now
    return moment - beat <= timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)


def settled_snapshot(snapshot: Mapping[str, object] | None, *, now: datetime | None = None) -> dict[str, object] | None:
    """``snapshot`` as a reader should see it: a dead ``running`` one reads as ``interrupted``."""

    if snapshot is None:
        return None
    value = dict(snapshot)
    if value.get("status") == STATUS_RUNNING and not snapshot_is_live(value, now=now):
        value["status"] = STATUS_INTERRUPTED
    return value


def index_summary(
    index: CompanyIndex,
    snapshot: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
    stale_after_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> dict[str, object]:
    """The index's state without reading a company file (one directory listing).

    Cheap enough for a status poll: ``empty`` when no company file exists,
    ``stale`` when the last update is older than ``stale_after_hours`` (or
    left no snapshot), else ``ready``. A search computes the exact state
    over the companies it reads (``company_index.index_state``).
    """

    indexed = sum(1 for _key in index.keys())
    last = None
    if snapshot is not None:
        last = snapshot.get("finished_at") or snapshot.get("updated_at")
    checked = _parse(last)
    status = INDEX_READY
    if indexed == 0:
        status = INDEX_EMPTY
    else:
        moment = datetime.now(timezone.utc) if now is None else now
        if checked is None or moment - checked > timedelta(hours=stale_after_hours):
            status = INDEX_STALE
    message = None
    if status == INDEX_EMPTY:
        message = "No company postings are stored on this machine yet. Run Update sources, then search again."
    elif status == INDEX_STALE:
        message = "The stored company postings are out of date. Run Update sources, then search again."
    return {
        "status": status,
        "needs_update": status != INDEX_READY,
        "message": message,
        "companies_indexed": indexed,
        "last_checked_at": last if isinstance(last, str) and checked is not None else None,
        "stale_after_hours": stale_after_hours,
    }


@dataclass(frozen=True)
class SourcesUpdateResult:
    """The final snapshot of one update (the same dict the API and ``--json`` return)."""

    snapshot: dict[str, object]

    @property
    def status(self) -> str:
        return str(self.snapshot["status"])

    @property
    def summary(self) -> str:
        return str(self.snapshot["summary"])

    def to_json(self) -> dict[str, object]:
        return dict(self.snapshot)


class _Listener:
    """What ``_fetch_boards`` reports to, in place of a run's ``ProgressWriter``.

    ``board_finished`` is called from the fetch loop's own (main) thread as
    each board settles, so the index is written one company at a time and
    never from two threads at once.
    """

    def __init__(
        self,
        *,
        update_id: str,
        index: CompanyIndex,
        cache: BoardCache,
        companies: Mapping[tuple[str, str], str],
        limits: AcquireLimits,
        roles: Sequence[str],
        on_progress: Callable[[dict[str, object]], None] | None,
    ) -> None:
        self._index = index
        self._cache = cache
        self._companies = companies
        self._on_progress = on_progress
        self._last_write = 0.0
        self.totals = UpdateTotals()
        self.counts = {"fetched": 0, "cached": 0, "failed": 0, "skipped": 0}
        self.requests = 0
        self.total = len(companies)
        self.done = 0  # boards settled: checked, or skipped by the budget
        self.checked = 0  # boards that were asked (fetched, unchanged or did not answer)
        self.never_checked: int | None = None
        self.up_to_date = 0  # boards left alone: checked within the stale window
        self.full_refresh = False
        self.trigger = TRIGGER_MANUAL
        self.tick: dict[str, object] | None = None
        self.cancelled = False
        self.failure_codes: dict[str, int] = {}
        self.failed_boards: list[dict[str, str]] = []
        self.rotation: dict[str, object] | None = None
        self.watchlist_seed: dict[str, object] | None = None
        self.state: dict[str, object] = {
            "update_id": update_id,
            "status": STATUS_RUNNING,
            "started_at": index_stamp(),
            "finished_at": None,
            "pid": os.getpid(),
            "roles": list(roles),
            "limits": limits.to_json(),
            "error": None,
        }
        self._started = time.monotonic()

    # -- the ProgressWriter methods the acquire code calls -------------------

    def watchlist_seeded(self, payload: Mapping[str, object]) -> None:
        self.watchlist_seed = dict(payload)

    def boards_planned(self, *, total: int, budget_seconds: float | None, rotation: Mapping[str, object] | None = None) -> None:
        del budget_seconds
        self.total = total
        self.rotation = dict(rotation) if rotation is not None else None
        self.publish(force=True)

    def board_finished(
        self,
        *,
        provider: str,
        board_token: str,
        status: str,
        requests: int = 0,
        cache: str | None = None,
        postings: int = 0,
        matched: int = 0,
        elapsed_ms: int = 0,
        code: str | None = None,
    ) -> None:
        del cache, postings, matched, elapsed_ms
        self.done += 1
        if status == "failed":
            failure = code or "error"
            self.failure_codes[failure] = self.failure_codes.get(failure, 0) + 1
            if len(self.failed_boards) < FAILED_BOARDS_LISTED:
                self.failed_boards.append({"board": f"{provider}:{board_token}", "code": failure})
        elif status == "skipped" and code == CANCELLED_CODE:
            self.cancelled = True
        self.counts[status] = self.counts.get(status, 0) + 1
        self.requests += requests
        first = False
        if status != "skipped":
            first = self.checked == 0
            self.checked += 1
        if status in ("fetched", "cached"):
            self.totals.add(self._refresh(provider, board_token))
        self.publish(force=first)

    def boards_finished(self, summary: Mapping[str, object]) -> None:
        rotation = summary.get("rotation")
        if isinstance(rotation, dict):
            self.rotation = dict(rotation)

    # -- the index and the snapshot -------------------------------------------

    def _refresh(self, provider: str, board_token: str) -> CompanyChange:
        try:
            return refresh_company(
                self._index,
                self._cache,
                ats=provider,
                slug=board_token,
                company=self._companies.get((provider, board_token)),
            )
        except Exception as exc:  # noqa: BLE001 - one company's index write never fails the update
            print(f"scout sources update: could not index {provider}:{board_token} ({type(exc).__name__})", file=sys.stderr)
            return CompanyChange(provider, board_token, "unreadable", code=type(exc).__name__.lower())

    def snapshot(self) -> dict[str, object]:
        totals = self.totals
        return {
            **self.state,
            "updated_at": index_stamp(),
            "elapsed_seconds": round(time.monotonic() - self._started, 3),
            "boards": {
                "total": self.total,
                "done": self.done,
                "checked": self.checked,
                **self.counts,
                "never_checked": self.never_checked,
                "up_to_date": self.up_to_date,
            },
            "full_refresh": self.full_refresh,
            "trigger": self.trigger,
            "tick": self.tick,
            "cancelled": self.cancelled,
            # Why boards did not answer, by code (`http_429` is a provider
            # pushing back, `http_404` a board that is gone); `boards` names
            # the first few.
            "failures": {
                "total": sum(self.failure_codes.values()),
                "codes": dict(sorted(self.failure_codes.items())),
                "boards": list(self.failed_boards),
            },
            "companies": {
                "checked": totals.companies,
                "indexed": totals.indexed,
                "updated": totals.updated,
                "untouched": totals.untouched,
                "unreadable": totals.unreadable + totals.missing,
                "with_new": totals.companies_with_new,
                "with_changes": totals.companies_with_changes,
            },
            "postings": {"new": totals.new, "changed": totals.changed, "removed": totals.removed, "live": totals.live},
            "requests": self.requests,
            # What this update has not reached: while it runs, every board
            # not asked yet; at the end, the boards the budget left. They
            # lead the next update (`boards.never_checked` is the backlog
            # that only ever shrinks).
            "remaining": (
                self.counts.get("skipped", 0)
                if self.state["status"] != STATUS_RUNNING
                else max(0, self.total - self.checked)
            ),
            "rotation": self.rotation,
            "watchlist_seed": self.watchlist_seed,
            "summary": totals.summary_line(),
        }

    def publish(self, *, force: bool = False) -> dict[str, object] | None:
        now = time.monotonic()
        if not force and now - self._last_write < SNAPSHOT_INTERVAL_SECONDS:
            return None
        self._last_write = now
        value = self.snapshot()
        try:
            self._index.write_update_summary(value)
        except OSError as exc:
            print(f"scout sources update: could not write the status snapshot ({type(exc).__name__})", file=sys.stderr)
        if self._on_progress is not None:
            self._on_progress(value)
        return value


def update_sources(
    boards: Sequence[WatchlistEntry],
    *,
    cache: BoardCache,
    index: CompanyIndex,
    client: Any,
    config: FindJobsConfig | None = None,
    limits: AcquireLimits | None = None,
    ats: Any = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    update_id: str | None = None,
    catalog_counts: Mapping[tuple[str, str], int] | None = None,
    watchlist_seed: Mapping[str, object] | None = None,
    force: bool = False,
    full_refresh: bool = False,
    stale_after_hours: float = DEFAULT_STALE_AFTER_HOURS,
    now: datetime | None = None,
    stop: threading.Event | None = None,
) -> SourcesUpdateResult:
    """Refresh ``boards`` through the rotation and bring the company index in step.

    ``config`` only decides which Greenhouse postings get their description
    fetched now (the titles its roles match, as in acquire); every listed
    posting of every board is indexed whatever the roles are. Raises
    :class:`SourcesUpdateRunningError` when another update is live (its
    snapshot was rewritten in the last :data:`HEARTBEAT_TIMEOUT_SECONDS`)
    unless ``force``.

    Incremental: a board already checked within ``stale_after_hours`` (its
    rotation stamp) whose company file is in the index is left alone, so an
    update right after an update asks ~0 boards. ``full_refresh`` asks every
    board anyway. (``force`` is a different thing: it overrides a live
    snapshot.)

    ``stop``: once set, no further board is asked; the update ends
    ``partial`` with ``cancelled: true`` and every board it reached indexed.
    """

    if not force and snapshot_is_live(index.read_update_summary()):
        raise SourcesUpdateRunningError()
    all_boards = boards
    if not full_refresh:
        boards = _stale_boards(boards, cache, index, stale_after_hours=stale_after_hours, now=now)
    return _run_update(
        boards,
        all_boards=all_boards,
        cache=cache,
        index=index,
        client=client,
        config=config,
        limits=limits,
        ats=ats,
        on_progress=on_progress,
        update_id=update_id,
        catalog_counts=catalog_counts,
        watchlist_seed=watchlist_seed,
        full_refresh=full_refresh,
        stop=stop,
    )


def _run_update(
    boards: Sequence[WatchlistEntry],
    *,
    all_boards: Sequence[WatchlistEntry],
    cache: BoardCache,
    index: CompanyIndex,
    client: Any,
    config: FindJobsConfig | None,
    limits: AcquireLimits | None,
    ats: Any,
    on_progress: Callable[[dict[str, object]], None] | None,
    update_id: str | None,
    catalog_counts: Mapping[tuple[str, str], int] | None,
    watchlist_seed: Mapping[str, object] | None,
    full_refresh: bool,
    stop: threading.Event | None,
    trigger: str = TRIGGER_MANUAL,
    tick: Mapping[str, object] | None = None,
) -> SourcesUpdateResult:
    """Ask exactly ``boards`` (already chosen out of ``all_boards``) and write the snapshot."""

    up_to_date = len(all_boards) - len(boards)
    config = config if config is not None else listing_config()
    limits = limits if limits is not None else AcquireLimits.from_environment()
    listener = _Listener(
        update_id=update_id or new_update_id(),
        index=index,
        cache=cache,
        companies={(board.provider.value, board.board_token): board.company for board in boards},
        limits=limits,
        roles=config.roles,
        on_progress=on_progress,
    )
    if watchlist_seed is not None:
        listener.watchlist_seeded(watchlist_seed)
    listener.up_to_date = up_to_date
    listener.full_refresh = full_refresh
    listener.trigger = trigger
    listener.tick = dict(tick) if tick is not None else None
    listener.never_checked = _never_checked(cache, all_boards)
    listener.publish(force=True)
    try:
        _rows, _failures, summary = _fetch_boards(
            boards,
            ats=ats if ats is not None else ATSBoardClients(),
            client=client,
            config=config,
            limits=limits,
            cache=cache,
            progress=listener,  # type: ignore[arg-type]
            started_at=time.monotonic(),
            catalog_counts=catalog_counts,
            stop=stop,
        )
        listener.boards_finished(summary)
        listener.never_checked = _never_checked(cache, all_boards)
        attempted = listener.checked
        if boards and attempted > 0 and up_to_date == 0 and listener.counts.get("failed", 0) == attempted:
            listener.state["status"] = STATUS_FAILED
            listener.state["error"] = {"code": "every_board_failed", "message": "no board could be fetched"}
        elif listener.counts.get("skipped", 0):
            listener.state["status"] = STATUS_PARTIAL
        else:
            listener.state["status"] = STATUS_SUCCEEDED
    except Exception as exc:  # noqa: BLE001 - recorded in the snapshot, then re-raised
        listener.state["status"] = STATUS_FAILED
        listener.state["error"] = {"code": type(exc).__name__.lower(), "message": "the sources update stopped early"}
        listener.state["finished_at"] = index_stamp()
        listener.publish(force=True)
        raise
    listener.state["finished_at"] = index_stamp()
    final = listener.publish(force=True) or listener.snapshot()
    return SourcesUpdateResult(final)


def _stale_boards(
    boards: Sequence[WatchlistEntry],
    cache: BoardCache,
    index: CompanyIndex,
    *,
    stale_after_hours: float,
    now: datetime | None,
) -> list[WatchlistEntry]:
    """The boards an incremental update still has to ask: never checked, checked long ago, or not in the index."""

    moment = datetime.now(timezone.utc) if now is None else now
    cutoff = timedelta(hours=stale_after_hours)
    stamps = cache.load_fetch_index().boards
    indexed = set(index.keys())
    due: list[WatchlistEntry] = []
    for board in boards:
        checked = _parse(stamps.get(f"{board.provider.value}:{board.board_token}"))
        fresh = checked is not None and moment - checked <= cutoff and (board.provider.value, board.board_token) in indexed
        if not fresh:
            due.append(board)
    return due


def _never_checked(cache: BoardCache, boards: Sequence[WatchlistEntry]) -> int:
    """Watchlist boards no update or run has ever asked (no stamp in the rotation index)."""

    stamped = cache.load_fetch_index().boards
    return sum(1 for board in boards if f"{board.provider.value}:{board.board_token}" not in stamped)


def run_sources_update(
    *,
    home_root: Path,
    target: Path,
    client: Any,
    config: FindJobsConfig | None = None,
    limits: AcquireLimits | None = None,
    ats: Any = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    update_id: str | None = None,
    force: bool = False,
    full_refresh: bool = False,
    stop: threading.Event | None = None,
) -> SourcesUpdateResult:
    """The whole command for one Scout project: seed, list the watchlist, update.

    Seeds the watchlist from the bundled catalog exactly as an acquire pass
    does (nothing when no setup preferences are saved yet, and nothing new
    when the catalog and the prefs are unchanged), then refreshes every
    active board.
    """

    from ...workpad import resolve_workpad
    from .watchlist import list_active

    home = Path(home_root)
    index = CompanyIndex.for_home(home)
    if not force and snapshot_is_live(index.read_update_summary()):
        raise SourcesUpdateRunningError()
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    seed = _Seed()
    _seed_watchlist(resolved, home_root=home, target=target, progress=seed)  # type: ignore[arg-type]
    boards = list_active(home, target, resolved.gig_id)
    return update_sources(
        boards,
        cache=board_cache_for_home(home),
        index=index,
        client=client,
        config=config,
        limits=limits,
        ats=ats,
        on_progress=on_progress,
        update_id=update_id,
        catalog_counts=_catalog_us_counts(),
        watchlist_seed=seed.payload,
        force=force,
        full_refresh=full_refresh,
        stop=stop,
    )


def read_board_facts(index: CompanyIndex, boards: Sequence[WatchlistEntry]) -> dict[str, BoardFacts]:
    """What the company index says about each of ``boards`` (one file read per indexed board).

    Keyed like the rotation stamps (``"<provider>:<board token>"``); a
    board with no readable index file has no entry.
    """

    indexed = set(index.keys())
    facts: dict[str, BoardFacts] = {}
    for board in boards:
        ats, slug = board.provider.value, board.board_token
        if (ats, slug) not in indexed:
            continue
        entry = index.read(ats, slug)
        if entry is None:
            continue
        live = sum(1 for posting in entry.postings.values() if not posting.removed)
        facts[_board_key(board)] = BoardFacts(live=live, checked_at=entry.checked_at, changed_at=entry.changed_at)
    return facts


def _board_key(board: WatchlistEntry) -> str:
    return f"{board.provider.value}:{board.board_token}"


def plan_refresh_tick(
    boards: Sequence[WatchlistEntry],
    *,
    cache: BoardCache,
    index: CompanyIndex,
    now: datetime | None = None,
) -> tuple[RefreshPlan, list[WatchlistEntry]]:
    """The tick plan for ``boards`` (the whole watchlist) and the boards it asks, busy first."""

    moment = datetime.now(timezone.utc) if now is None else now
    by_key = {_board_key(board): board for board in boards}
    plan = plan_tick(
        list(by_key),
        facts=read_board_facts(index, boards),
        stamps=cache.load_fetch_index().boards,
        now=moment,
    )
    return plan, [by_key[key] for key in plan.keys]


def tick_limits(environ: Mapping[str, str] | None = None) -> AcquireLimits:
    """The limits of a background tick: the environment's, spread over the tick.

    Concurrency and the polite minimum interval are a manual update's
    (:meth:`AcquireLimits.from_environment`); the spread
    (:data:`TICK_SPREAD_SECONDS`, or :data:`TICK_SPREAD_ENV`) slows each
    provider down to one request every ``spread / boards`` seconds.
    """

    env = os.environ if environ is None else environ
    spread: float | None = TICK_SPREAD_SECONDS
    raw = env.get(TICK_SPREAD_ENV)
    if raw is not None and raw.strip():
        try:
            spread = float(raw)
        except ValueError:
            spread = TICK_SPREAD_SECONDS
    if spread is not None and spread <= 0:
        spread = None
    return replace(AcquireLimits.from_environment(env), spread_seconds=spread, time_budget_seconds=TICK_BUDGET_SECONDS)


def run_refresh_tick(
    home_root: Path,
    target: Path,
    *,
    client: Any,
    now: datetime | None = None,
    stop_event: threading.Event | None = None,
    config: FindJobsConfig | None = None,
    limits: AcquireLimits | None = None,
    ats: Any = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    update_id: str | None = None,
) -> SourcesUpdateResult:
    """Plan and run ONE background refresh tick, on the calling thread.

    Seeds and lists the watchlist as a manual update does, picks this
    tick's boards (``refresh_plan.plan_tick``: every busy board and one
    slice of the quiet ones), and asks them spread over the tick
    (:func:`tick_limits` unless ``limits`` is given). The snapshot is the
    same file a manual update writes, with ``trigger: "auto"`` and the plan
    in ``tick``; ``boards.up_to_date`` counts the watchlist boards this tick
    left alone.

    One update at a time: raises :class:`SourcesUpdateRunningError` when an
    update (manual or a tick) is live, and claims the snapshot under the
    same lock :func:`start_background_update` uses, so a manual start and a
    tick can never both begin. Setting ``stop_event`` ends the tick between
    boards: ``partial``, ``cancelled: true``. No thread is started here.
    """

    from ...workpad import resolve_workpad
    from .watchlist import list_active

    home = Path(home_root)
    index = CompanyIndex.for_home(home)
    update_id = update_id or new_update_id()
    with _START_LOCK:
        if snapshot_is_live(index.read_update_summary()):
            raise SourcesUpdateRunningError()
        index.write_update_summary({**_blank_snapshot(update_id, STATUS_RUNNING), "trigger": TRIGGER_AUTO})
    try:
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
        seed = _Seed()
        _seed_watchlist(resolved, home_root=home, target=target, progress=seed)  # type: ignore[arg-type]
        watchlist = list_active(home, target, resolved.gig_id)
        cache = board_cache_for_home(home)
        plan, boards = plan_refresh_tick(watchlist, cache=cache, index=index, now=now)
    except Exception as exc:  # noqa: BLE001 - release the claimed snapshot, then re-raise
        failed = _blank_snapshot(
            update_id,
            STATUS_FAILED,
            error={"code": getattr(exc, "code", type(exc).__name__.lower()), "message": "the sources refresh could not run"},
        )
        try:
            index.write_update_summary({**failed, "trigger": TRIGGER_AUTO})
        except OSError:
            pass
        raise
    return _run_update(
        boards,
        all_boards=watchlist,
        cache=cache,
        index=index,
        client=client,
        config=config,
        limits=limits if limits is not None else tick_limits(),
        ats=ats,
        on_progress=on_progress,
        update_id=update_id,
        catalog_counts=_catalog_us_counts(),
        watchlist_seed=seed.payload,
        full_refresh=False,
        stop=stop_event,
        trigger=TRIGGER_AUTO,
        tick=plan.to_json(),
    )


class _Seed:
    """Catches what ``_seed_watchlist`` reports (it writes to a run's progress otherwise)."""

    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def watchlist_seeded(self, payload: Mapping[str, object]) -> None:
        self.payload = dict(payload)


def _blank_snapshot(update_id: str, status: str, *, error: Mapping[str, object] | None = None) -> dict[str, object]:
    stamp = index_stamp()
    return {
        "update_id": update_id,
        "status": status,
        "started_at": stamp,
        "finished_at": None if status == STATUS_RUNNING else stamp,
        "updated_at": stamp,
        "pid": os.getpid(),
        "roles": [],
        "limits": None,
        "error": dict(error) if error is not None else None,
        "elapsed_seconds": 0.0,
        "boards": {"total": 0, "done": 0, "checked": 0, "fetched": 0, "cached": 0, "failed": 0, "skipped": 0, "never_checked": None, "up_to_date": 0},
        "full_refresh": False,
        "trigger": TRIGGER_MANUAL,
        "tick": None,
        "cancelled": False,
        "failures": {"total": 0, "codes": {}, "boards": []},
        "companies": {"checked": 0, "indexed": 0, "updated": 0, "untouched": 0, "unreadable": 0, "with_new": 0, "with_changes": 0},
        "postings": {"new": 0, "changed": 0, "removed": 0, "live": 0},
        "requests": 0,
        "remaining": 0,
        "rotation": None,
        "watchlist_seed": None,
        "summary": UpdateTotals().summary_line(),
    }


_START_LOCK = threading.Lock()


def start_background_update(
    *,
    home_root: Path,
    target: Path,
    client_factory: Callable[[], Any],
    config: FindJobsConfig | None = None,
    limits: AcquireLimits | None = None,
    force: bool = False,
    full_refresh: bool = False,
    on_finished: Callable[[dict[str, object]], None] | None = None,
) -> str:
    """Start :func:`run_sources_update` on a daemon thread; return its ``update_id``.

    The same shape as the API's discovery run: the caller gets an id at
    once and polls :func:`read_status`. The ``running`` snapshot is written
    before this returns, so a status read straight after the start already
    sees it. Raises :class:`SourcesUpdateRunningError` when an update is
    live, unless ``force``.
    """

    home = Path(home_root)
    index = CompanyIndex.for_home(home)
    update_id = new_update_id()
    with _START_LOCK:
        if not force and snapshot_is_live(index.read_update_summary()):
            raise SourcesUpdateRunningError()
        index.write_update_summary(_blank_snapshot(update_id, STATUS_RUNNING))

    def run() -> None:
        final: dict[str, object]
        try:
            client = client_factory()
            try:
                result = run_sources_update(
                    home_root=home,
                    target=target,
                    client=client,
                    config=config,
                    limits=limits,
                    update_id=update_id,
                    force=True,  # the live snapshot is this update's own
                    full_refresh=full_refresh,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
            final = result.to_json()
        except Exception as exc:  # noqa: BLE001 - nothing else observes this thread: record the failure
            print(f"scout sources update: failed ({type(exc).__name__})", file=sys.stderr)
            current = index.read_update_summary()
            if current is not None and current.get("update_id") == update_id and current.get("status") == STATUS_FAILED:
                final = dict(current)
            else:
                final = _blank_snapshot(
                    update_id,
                    STATUS_FAILED,
                    error={"code": getattr(exc, "code", type(exc).__name__.lower()), "message": "the sources update could not run"},
                )
                try:
                    index.write_update_summary(final)
                except OSError:
                    pass
        if on_finished is not None:
            on_finished(final)

    threading.Thread(target=run, name="scout-sources-update", daemon=True).start()
    return update_id


def read_status(home_root: Path, *, now: datetime | None = None) -> dict[str, object]:
    """``{"running", "update", "index"}``: what ``GET /api/sources/update`` and the CLI report."""

    index = CompanyIndex.for_home(Path(home_root))
    snapshot = index.read_update_summary()
    return {
        "schema_version": SOURCES_UPDATE_STATUS_SCHEMA,
        "running": snapshot_is_live(snapshot, now=now),
        "update": settled_snapshot(snapshot, now=now),
        "index": index_summary(index, snapshot, now=now),
    }


__all__ = [
    "HEARTBEAT_TIMEOUT_SECONDS",
    "SNAPSHOT_INTERVAL_SECONDS",
    "SOURCES_UPDATE_STATUS_SCHEMA",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_PARTIAL",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "TICK_BUDGET_SECONDS",
    "TICK_INTERVAL_SECONDS",
    "TICK_SPREAD_ENV",
    "TICK_SPREAD_SECONDS",
    "TRIGGER_AUTO",
    "TRIGGER_MANUAL",
    "SourcesUpdateError",
    "SourcesUpdateResult",
    "SourcesUpdateRunningError",
    "board_cache_for_home",
    "default_http_client",
    "index_summary",
    "listing_config",
    "load_effective_config",
    "new_update_id",
    "plan_refresh_tick",
    "read_board_facts",
    "read_status",
    "run_refresh_tick",
    "run_sources_update",
    "settled_snapshot",
    "snapshot_is_live",
    "start_background_update",
    "tick_limits",
    "update_sources",
]
