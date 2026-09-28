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
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
from .market_acquisition import AcquireLimits, _catalog_us_counts, _fetch_boards, _seed_watchlist

SOURCES_UPDATE_STATUS_SCHEMA = "scout-sources-update-status:1"
#: How often the running snapshot is rewritten (it is also the liveness
#: heartbeat another process reads before it starts a second update).
SNAPSHOT_INTERVAL_SECONDS = 5.0
#: A ``running`` snapshot not rewritten for this long belongs to a process
#: that died: it reads as ``interrupted`` and no longer blocks a new update.
HEARTBEAT_TIMEOUT_SECONDS = 300.0

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"  # every board was attempted
STATUS_PARTIAL = "partial"  # the time budget left boards for the next update
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"  # a running snapshot whose process is gone


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
        self.done = 0
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
        del cache, postings, matched, elapsed_ms, code
        self.done += 1
        self.counts[status] = self.counts.get(status, 0) + 1
        self.requests += requests
        if status in ("fetched", "cached"):
            self.totals.add(self._refresh(provider, board_token))
        self.publish()

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
            "boards": {"total": self.total, "done": self.done, **self.counts},
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
            "remaining": self.counts.get("skipped", 0) if self.state["status"] != STATUS_RUNNING else max(0, self.total - self.done),
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
) -> SourcesUpdateResult:
    """Refresh ``boards`` through the rotation and bring the company index in step.

    ``config`` only decides which Greenhouse postings get their description
    fetched now (the titles its roles match, as in acquire); every listed
    posting of every board is indexed whatever the roles are. Raises
    :class:`SourcesUpdateRunningError` when another update is live (its
    snapshot was rewritten in the last :data:`HEARTBEAT_TIMEOUT_SECONDS`)
    unless ``force``.
    """

    if not force and snapshot_is_live(index.read_update_summary()):
        raise SourcesUpdateRunningError()
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
        )
        listener.boards_finished(summary)
        attempted = listener.done - listener.counts.get("skipped", 0)
        if boards and attempted > 0 and listener.counts.get("failed", 0) == attempted:
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
        "boards": {"total": 0, "done": 0, "fetched": 0, "cached": 0, "failed": 0, "skipped": 0},
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
    "SourcesUpdateError",
    "SourcesUpdateResult",
    "SourcesUpdateRunningError",
    "board_cache_for_home",
    "default_http_client",
    "index_summary",
    "listing_config",
    "load_effective_config",
    "new_update_id",
    "read_status",
    "run_sources_update",
    "settled_snapshot",
    "snapshot_is_live",
    "start_background_update",
    "update_sources",
]
