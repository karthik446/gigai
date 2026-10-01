"""0110-025 (R3): the background thread that keeps the sources fresh while the Scout server runs.

One daemon thread per server (:class:`RefreshTicker`). Every
:data:`POLL_SECONDS` it reads the last update's snapshot and decides
(:func:`decide`, pure):

* ``disabled``: the setting ``sources.auto_refresh`` is off. Nothing else is
  read and no request is made.
* ``needs_first_update``: the company index is empty or no update has ever
  left a stamp. A tick never fills an index (it takes a sixth of the
  unindexed boards an hour): the first fill is the operator's Update sources.
* ``running``: an update is live, a manual one or a tick. The thread waits:
  one update at a time.
* ``waiting``: the last update started less than an hour ago.
* ``due``: it started an hour ago or more, so a tick runs now
  (``sources_update.run_refresh_tick``). A server started after a night off
  finds the last update stale and ticks at once.

A tick claims the same live-update snapshot a manual update does, so a
manual start and a tick can never both run; a manual Full refresh stops the
tick and takes over (``sources_update.start_background_update``). Stopping
the server sets the stop event: the tick ends between boards with a clean
``partial`` snapshot and the thread exits.

The setting lives in ``<home>/scout/<project_id>/settings.json`` (beside
``discovery/prefs.json``; ``{"schema_version": "scout-settings:1",
"sources": {"auto_refresh": false}}``). A missing file or key is ON; a file
that cannot be read is OFF (a background job that makes requests does not
guess). :data:`AUTO_REFRESH_ENV` overrides the file either way.

The model tag queue (0110-024 P3, ``model_tag.TagQueue``) rides the same
thread: after every look, :meth:`RefreshTicker.step` lets the queue drain a
bounded number of batches, unless an update is live (the queue yields to it)
or the thread is stopping. The queue has its own setting
(``tagging.model_enabled`` in the same settings file), so it also drains
with the refresh off. :meth:`RefreshTicker.kick_tags` wakes the thread for a
drain now (a profile changed, an update finished) instead of at the next
poll. A drain never changes what :meth:`RefreshTicker.step` returns.

:func:`background_status` is the one status block the refresh and the tag
queue share (``GET /api/sources/update`` -> ``background``). The queue's own
counters (calls, failures, the last error, the backoff) are
:meth:`RefreshTicker.tag_queue_status`; adding them to the block is the
status packet's (P4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

from .company_index import CompanyIndex, index_stamp
from .model_tag import TagQueue
from .refresh_plan import TICK_INTERVAL_SECONDS
from .sources_update import (
    TRIGGER_MANUAL,
    SourcesUpdateRunningError,
    default_http_client,
    load_effective_config,
    run_refresh_tick,
    settled_snapshot,
    snapshot_is_live,
)

#: ``0``/``false``/``off``/``no`` turns the background refresh off, ``1``/``true``/``on``/``yes`` on, whatever the setting says.
AUTO_REFRESH_ENV = "GIGAI_SCOUT_AUTO_REFRESH"
SETTINGS_SCHEMA = "scout-settings:1"
SETTINGS_FILENAME = "settings.json"
#: How often the thread looks at the snapshot (and how long a stop can take while it waits).
POLL_SECONDS = 30.0

STATE_DISABLED = "disabled"
STATE_INACTIVE = "inactive"  # status only: no tick thread in this process
STATE_NEEDS_FIRST_UPDATE = "needs_first_update"
STATE_RUNNING = "running"
STATE_WAITING = "waiting"
STATE_DUE = "due"

MESSAGE_DISABLED = "Automatic refresh is off."
MESSAGE_NEEDS_FIRST_UPDATE = "Run Update sources once. Automatic refresh starts after the first update."

SOURCE_DEFAULT = "default"
SOURCE_SETTING = "setting"
SOURCE_ENVIRONMENT = "environment"
SOURCE_UNREADABLE = "settings_unreadable"

_OFF = frozenset({"0", "false", "off", "no"})
_ON = frozenset({"1", "true", "on", "yes"})

_logger = logging.getLogger("gigai.scout.refresh")


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutoRefresh:
    """Whether the background refresh may run, and what said so."""

    enabled: bool
    source: str

    def to_json(self) -> dict[str, object]:
        return {"enabled": self.enabled, "source": self.source}


def settings_path(home_root: Path, target: Path) -> Path:
    """``<home>/scout/<project_id>/settings.json`` (raises when ``target`` is not a bound project)."""

    from .discovery.storage import project_id

    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / SETTINGS_FILENAME


def auto_refresh_setting(
    home_root: Path,
    target: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AutoRefresh:
    """``sources.auto_refresh`` for this project: the environment, then the settings file, then ON."""

    env = os.environ if environ is None else environ
    raw = (env.get(AUTO_REFRESH_ENV) or "").strip().lower()
    if raw in _OFF:
        return AutoRefresh(False, SOURCE_ENVIRONMENT)
    if raw in _ON:
        return AutoRefresh(True, SOURCE_ENVIRONMENT)
    if target is None:
        return AutoRefresh(True, SOURCE_DEFAULT)
    try:
        path = settings_path(home_root, target)
    except Exception:  # noqa: BLE001 - no bound project yet: there is no settings file to read
        return AutoRefresh(True, SOURCE_DEFAULT)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AutoRefresh(True, SOURCE_DEFAULT)
    except (OSError, ValueError):
        return AutoRefresh(False, SOURCE_UNREADABLE)
    if not isinstance(payload, dict) or payload.get("schema_version") != SETTINGS_SCHEMA:
        return AutoRefresh(False, SOURCE_UNREADABLE)
    sources = payload.get("sources", {})
    if not isinstance(sources, dict):
        return AutoRefresh(False, SOURCE_UNREADABLE)
    if "auto_refresh" not in sources:
        return AutoRefresh(True, SOURCE_DEFAULT)
    value = sources["auto_refresh"]
    if type(value) is not bool:
        return AutoRefresh(False, SOURCE_UNREADABLE)
    return AutoRefresh(value, SOURCE_SETTING)


# ---------------------------------------------------------------------------
# The decision (pure)
# ---------------------------------------------------------------------------


def _parse(value: object) -> datetime | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass(frozen=True)
class TickDecision:
    state: str
    next_tick_at: datetime | None = None
    message: str | None = None


def decide(
    snapshot: Mapping[str, object] | None,
    *,
    indexed: bool,
    enabled: bool,
    now: datetime,
    interval_seconds: float = TICK_INTERVAL_SECONDS,
) -> TickDecision:
    """What the tick thread does at ``now``, from the last update's snapshot alone.

    ``indexed``: the company index holds at least one company. The next
    tick is one interval after the last update *started* (manual or tick),
    so ticks stay an hour apart however long each one takes.
    """

    if not enabled:
        return TickDecision(STATE_DISABLED, message=MESSAGE_DISABLED)
    if snapshot_is_live(snapshot, now=now):
        return TickDecision(STATE_RUNNING)
    last = None
    if snapshot is not None:
        last = _parse(snapshot.get("started_at")) or _parse(snapshot.get("finished_at")) or _parse(snapshot.get("updated_at"))
    if not indexed or last is None:
        return TickDecision(STATE_NEEDS_FIRST_UPDATE, message=MESSAGE_NEEDS_FIRST_UPDATE)
    due = last + timedelta(seconds=interval_seconds)
    if now >= due:
        return TickDecision(STATE_DUE, next_tick_at=now)
    return TickDecision(STATE_WAITING, next_tick_at=due)


def _has_companies(index: CompanyIndex) -> bool:
    return next(iter(index.keys()), None) is not None


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------


class RefreshTicker:
    """The hourly refresh thread of one Scout server.

    ``clock``, ``wait``, ``run_tick``, ``client_factory`` and ``tag_queue``
    are seams for tests (a fake clock, a fake fetcher, a queue over a fake
    model); production passes none of them. :meth:`step` is one look at the
    snapshot and, when a tick is due, the tick itself, then one bounded drain
    of the model tag queue, on the calling thread. ``model_tags=False`` runs
    the refresh with no tag queue at all.
    """

    def __init__(
        self,
        *,
        home_root: Path,
        target: Path,
        client_factory: Callable[[], Any] = default_http_client,
        clock: Callable[[], datetime] | None = None,
        wait: Callable[[float], object] | None = None,
        run_tick: Callable[..., Any] = run_refresh_tick,
        config_loader: Callable[[Path, Path], Any] = load_effective_config,
        interval_seconds: float = TICK_INTERVAL_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        environ: Mapping[str, str] | None = None,
        logger: logging.Logger | None = None,
        tag_queue: TagQueue | None = None,
        model_tags: bool = True,
    ) -> None:
        self.home_root = Path(home_root)
        self.target = Path(target)
        self.interval_seconds = float(interval_seconds)
        self._client_factory = client_factory
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._wake = threading.Event()  # set by a kick and by stop: ends the wait between looks
        self._wait = wait if wait is not None else self._sleep
        self._run_tick = run_tick
        self._config_loader = config_loader
        self._poll_seconds = float(poll_seconds)
        self._environ = environ
        self._logger = logger if logger is not None else _logger
        self._thread: threading.Thread | None = None
        self._tick_stop: threading.Event | None = None
        self._retry_after: datetime | None = None
        self._last_state: str | None = None
        if tag_queue is None and model_tags:
            tag_queue = TagQueue(home_root=self.home_root, target=self.target, clock=self._clock, environ=environ, logger=self._logger)
        self._tag_queue = tag_queue

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="scout-sources-refresh", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 10.0) -> bool:
        """End the thread; a running tick stops between boards. ``True`` once the thread is gone."""

        self._stop.set()
        self._wake.set()
        tick = self._tick_stop
        if tick is not None:
            tick.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _sleep(self, seconds: float) -> None:
        """Wait for the next look: ``seconds``, or less when the thread is stopped or kicked."""

        if self._wake.wait(seconds):
            self._wake.clear()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - nothing else observes this thread: log it and keep the loop
                self._logger.warning("sources refresh: the tick loop hit %s; it goes on", type(exc).__name__)
            if self._stop.is_set():
                break
            self._wait(self._poll_seconds)

    # -- one look ----------------------------------------------------------

    def setting(self) -> AutoRefresh:
        return auto_refresh_setting(self.home_root, self.target, environ=self._environ)

    def step(self) -> str:
        """Decide, and run a tick when one is due. Returns what happened (a ``STATE_*``, ``ticked``, ``yielded`` or ``failed``).

        Then the model tag queue drains its batches for this look; what it did
        is in :meth:`tag_queue_status`, never in the return value.
        """

        outcome = self._refresh_step()
        self._drain_tags(outcome)
        return outcome

    # -- the model tag queue -----------------------------------------------

    @property
    def tag_queue(self) -> TagQueue | None:
        return self._tag_queue

    def kick_tags(self, *, reset_backoff: bool = False) -> None:
        """Drain the tag queue now instead of at the next poll (the roles are read again)."""

        if self._tag_queue is not None:
            self._tag_queue.kick(reset_backoff=reset_backoff)
        self._wake.set()

    def tag_queue_status(self) -> dict[str, object] | None:
        """The queue's counters, last error and backoff; ``None`` when this thread has no queue."""

        return None if self._tag_queue is None else self._tag_queue.status()

    def _drain_tags(self, outcome: str) -> None:
        queue = self._tag_queue
        if queue is None or self._stop.is_set() or outcome in (STATE_RUNNING, "yielded") or not self.setting().enabled:
            return  # stopping, an update is live (one thing at a time), or sources.auto_refresh is off (one switch for all background work)
        try:
            result = queue.drain(stop=self._stop)
        except Exception as exc:  # noqa: BLE001 - the tag queue never takes the refresh thread down
            self._logger.warning("model tags: the drain hit %s; it goes on", type(exc).__name__)
            return
        if result.batches:
            self._logger.info("model tags: %s batches=%s tagged=%s rejected=%s calls=%s", result.state, result.batches, result.tagged, result.rejected, result.calls)

    def _refresh_step(self) -> str:
        if not self.setting().enabled:
            return self._note(STATE_DISABLED)
        now = self._clock()
        index = CompanyIndex.for_home(self.home_root)
        decision = decide(
            index.read_update_summary(),
            indexed=_has_companies(index),
            enabled=True,
            now=now,
            interval_seconds=self.interval_seconds,
        )
        if decision.state != STATE_DUE:
            return self._note(decision.state)
        if self._retry_after is not None and now < self._retry_after:
            return self._note(STATE_WAITING)
        return self._note(self._tick(now))

    def _note(self, state: str) -> str:
        if state != self._last_state and state in (STATE_DISABLED, STATE_NEEDS_FIRST_UPDATE):
            self._logger.info("sources refresh: %s", state)
        self._last_state = state
        return state

    def _tick(self, now: datetime) -> str:
        stop = threading.Event()
        self._tick_stop = stop
        try:
            if self._stop.is_set():
                return STATE_WAITING
            client = self._client_factory()
            try:
                result = self._run_tick(
                    self.home_root,
                    self.target,
                    client=client,
                    now=now,
                    stop_event=stop,
                    config=self._config_loader(self.home_root, self.target),
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except SourcesUpdateRunningError:
            return "yielded"  # a manual update claimed the snapshot first
        except Exception as exc:  # noqa: BLE001 - a tick that cannot run is retried an interval later, never fatal
            self._retry_after = now + timedelta(seconds=self.interval_seconds)
            self._logger.warning("sources refresh: the tick failed (%s); next try in %.0f s", type(exc).__name__, self.interval_seconds)
            return "failed"
        finally:
            self._tick_stop = None
        self._retry_after = None
        snapshot = result.to_json() if hasattr(result, "to_json") else {}
        self._logger.info(
            "sources refresh tick %s: update_id=%s boards=%s %s",
            snapshot.get("status"),
            snapshot.get("update_id"),
            snapshot.get("boards"),
            snapshot.get("summary"),
        )
        return "ticked"


# ---------------------------------------------------------------------------
# The status block
# ---------------------------------------------------------------------------


def _signature(path: Path) -> tuple[object, ...]:
    """What changes when a SQLite cache (WAL mode) is written: the file and its write-ahead log."""

    parts: list[object] = []
    for name in (str(path), f"{path}-wal"):
        try:
            stat = os.stat(name)
        except OSError:
            parts.append(None)
        else:
            parts.append((stat.st_mtime_ns, stat.st_size, stat.st_ino))
    return tuple(parts)


#: The last counts read from each cache file, kept while the file is unchanged:
#: the status is polled every second during an update and the counts scan the store.
_COUNTS: dict[tuple[Path, str], tuple[tuple[object, ...], dict[str, object]]] = {}
_COUNTS_LOCK = threading.Lock()
#: A status read waits this long for a writer, then reports the store unavailable for this poll.
_COUNT_TIMEOUT_SECONDS = 1.0


def _cached_counts(
    path: Path,
    read: Callable[[sqlite3.Connection], dict[str, object]],
    blank: dict[str, object],
    *,
    slot: str = "",
) -> dict[str, object]:
    """``read`` over a read-only connection to the cache at ``path``; ``blank`` when it cannot be read.

    ``slot`` names the reader when more than one reads the same file (the
    ``background`` block here, ``sources_status`` for the API's own blocks):
    each keeps its own last counts.

    Read-only on purpose. The stores' own openers create a missing file and
    rebuild one they cannot open; a status poll must do neither, least of
    all while the update is writing that file for the first time.
    """

    if not path.is_file():
        return dict(blank)  # a status read never creates the cache
    with _COUNTS_LOCK:
        before = _signature(path)
        held = _COUNTS.get((path, slot))
        if held is not None and held[0] == before:
            return dict(held[1])
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=_COUNT_TIMEOUT_SECONDS)
            try:
                value = read(conn)
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return dict(blank)  # being created, locked or not this layout: unavailable now, read again next time
        # Signed as it was before the read: a write during the read only costs one more read.
        _COUNTS[(path, slot)] = (before, dict(value))
        return value


def _tag_counts(home_root: Path) -> dict[str, object]:
    """Titles in the tag store (every one has a rules level) and how many still lack a function."""

    from . import posting_tags

    store = posting_tags.default_store(home_root)
    version = store.tagger_version

    def read(conn: sqlite3.Connection) -> dict[str, object]:
        titles, lacking = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(function IS NULL), 0) FROM title_tags WHERE tagger_version = ?", (version,)
        ).fetchone()
        return {"available": True, "titles": int(titles), "with_function": int(titles) - int(lacking), "lacking_function": int(lacking)}

    return _cached_counts(store.path, read, {"available": False, "titles": 0, "with_function": 0, "lacking_function": 0})


def _text_counts(home_root: Path) -> dict[str, object]:
    """Postings in the text index; ``unchecked`` have no stored text, so a text query cannot match them."""

    from . import text_index

    def read(conn: sqlite3.Connection) -> dict[str, object]:
        with_text, without_text = conn.execute(
            "SELECT COALESCE(SUM(has_text), 0), COALESCE(SUM(1 - has_text), 0) FROM postings"
        ).fetchone()
        return {"available": True, "postings": int(with_text) + int(without_text), "with_text": int(with_text), "unchecked": int(without_text)}

    return _cached_counts(text_index.text_index_path(home_root), read, {"available": False, "postings": 0, "with_text": 0, "unchecked": 0})


def background_status(
    home_root: Path,
    target: Path | None,
    *,
    ticker: RefreshTicker | None = None,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """The ``background`` block of the sources status: the refresh tick, the tags and the text index.

    ``ticker`` is this server's tick thread (``None``: no thread here, as
    for the CLI or a server started without one; the state is then
    ``inactive`` unless the setting is off).
    """

    moment = datetime.now(timezone.utc) if now is None else now
    home = Path(home_root)
    setting = auto_refresh_setting(home, target, environ=environ)
    index = CompanyIndex.for_home(home)
    raw = index.read_update_summary()
    running = snapshot_is_live(raw, now=moment)
    snapshot = settled_snapshot(raw, now=moment)
    active = ticker is not None and ticker.alive and not ticker.stopping
    interval = ticker.interval_seconds if ticker is not None else TICK_INTERVAL_SECONDS
    decision = decide(raw, indexed=_has_companies(index), enabled=setting.enabled, now=moment, interval_seconds=interval)
    state, message, next_tick = decision.state, decision.message, decision.next_tick_at
    if setting.enabled and not active and state != STATE_RUNNING:
        state, next_tick = STATE_INACTIVE, None
    last = None
    if snapshot is not None and not running:
        last = {
            "update_id": snapshot.get("update_id"),
            "status": snapshot.get("status"),
            "trigger": snapshot.get("trigger") or TRIGGER_MANUAL,
            "started_at": snapshot.get("started_at"),
            "finished_at": snapshot.get("finished_at"),
        }
    return {
        "auto_refresh": {**setting.to_json(), "active": active},
        "state": state,
        "message": message,
        "in_progress": running,
        "trigger": (snapshot.get("trigger") or TRIGGER_MANUAL) if snapshot is not None else None,
        "last_update": last,
        "next_tick_at": index_stamp(next_tick) if next_tick is not None else None,
        "interval_seconds": interval,
        "tags": _tag_counts(home),
        "text": _text_counts(home),
    }


__all__ = [
    "AUTO_REFRESH_ENV",
    "MESSAGE_DISABLED",
    "MESSAGE_NEEDS_FIRST_UPDATE",
    "POLL_SECONDS",
    "SETTINGS_FILENAME",
    "SETTINGS_SCHEMA",
    "STATE_DISABLED",
    "STATE_DUE",
    "STATE_INACTIVE",
    "STATE_NEEDS_FIRST_UPDATE",
    "STATE_RUNNING",
    "STATE_WAITING",
    "AutoRefresh",
    "RefreshTicker",
    "TickDecision",
    "auto_refresh_setting",
    "background_status",
    "decide",
    "settings_path",
]
