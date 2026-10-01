"""The ``tags``, ``text_index`` and ``refresh`` blocks of ``GET /api/sources/update`` (0110-024 P4, 0110-025 R4, 0110-026 F2).

Additive beside the ``background`` block (``refresh_tick.background_status``),
whose key set is fixed: these three say what the sources strip and the
settings page show, each in the words the UI uses.

* ``tags``: ``titles`` is every stored title (each has a LEVEL from the
  rules). The other four split them by FUNCTION and add up to ``titles``:
  the rules gave one (``tagged_by_rules``), a model did
  (``tagged_by_model``), a model looked and could not place it
  (``model_other``), or no rule and no model has answered
  (``awaiting_model``: exactly what ``TagStore.count_awaiting_model``
  counts). 0110-028: ``awaiting_model`` is not "waiting": under the settings
  in effect a model will tag ``awaiting_model_queued`` of them (the demand
  set: titles at the levels the active profiles ask for; all of them when
  the backfill is on) and will not tag ``awaiting_model_not_queued``, for
  the one reason ``not_queued_reason`` names. ``tagging`` says what the
  model queue is doing now. ``queue`` is the queue's own status
  (``RefreshTicker.tag_queue_status``: failures, the last error, when it
  retries), ``null`` when this server runs no refresh thread.
* ``text_index``: postings a keyword search can check, and the ones it cannot.
* ``refresh``: on or off, whether an update runs now and who started it,
  when the sources were last updated (and how many minutes ago), when the
  next background check is due, and the schedule the checks follow.

Like the ``background`` block, everything here is read: the two SQLite
caches through a read-only connection (never created, rebuilt or replaced by
a status poll), the settings and the last update's snapshot from their files.
No request, no model call.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .model_tag import LANE_BACKFILL, LANE_DEMAND, backfill_adapter, tagging_setting
from .refresh_tick import RefreshTicker, _cached_counts, _parse, auto_refresh_setting
from .tag_store import SOURCE_MODEL, SOURCE_RULES

_TAGS_BLANK: dict[str, object] = {
    "available": False,
    "titles": 0,
    "tagged_by_rules": 0,
    "tagged_by_model": 0,
    "model_other": 0,
    "awaiting_model": 0,
}

#: Why ``awaiting_model_not_queued`` titles will not be tagged by a model under the settings in effect.
NOT_QUEUED_NO_THREAD = "no_refresh_thread"  # this server runs no background thread (the CLI, a test server)
NOT_QUEUED_REFRESH_OFF = "refresh_off"  # sources.auto_refresh is off: no background work at all
NOT_QUEUED_MODEL_OFF = "model_off"  # tagging.model_enabled is off
NOT_QUEUED_BACKFILL_OFF = "backfill_off"  # only the demand set is tagged: tagging.backfill_enabled is off

TAGGING_INACTIVE = "inactive"
TAGGING_PAUSED = "paused"
TAGGING_OFF = "off"
TAGGING_FAILING = "failing"
TAGGING_WAITING_FOR_UPDATE = "waiting_for_update"  # a manual update is running: the queue starts when it is done
TAGGING_RUNNING = "running"
TAGGING_IDLE = "idle"


_BY_LEVEL = "_awaiting_by_level"


def _tag_counts(home_root: Path) -> dict[str, object]:
    from . import posting_tags

    store = posting_tags.default_store(home_root)
    version = store.tagger_version

    def read(conn: sqlite3.Connection) -> dict[str, object]:
        titles, by_rules, by_model, other, awaiting = conn.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(function IS NOT NULL AND function_source = ?), 0), "
            "COALESCE(SUM(function IS NOT NULL AND function_source = ?), 0), "
            "COALESCE(SUM(function IS NULL AND function_source = ?), 0), "
            # The predicate of TagStore.count_awaiting_model: no rule and no model has answered yet.
            "COALESCE(SUM(function IS NULL AND function_source IS NULL), 0) "
            "FROM title_tags WHERE tagger_version = ?",
            (SOURCE_RULES, SOURCE_MODEL, SOURCE_MODEL, version),
        ).fetchone()
        by_level = conn.execute(
            "SELECT level, COUNT(*) FROM title_tags WHERE tagger_version = ? AND function IS NULL AND function_source IS NULL GROUP BY level",
            (version,),
        ).fetchall()
        return {
            "available": True,
            "titles": int(titles),
            "tagged_by_rules": int(by_rules),
            "tagged_by_model": int(by_model),
            "model_other": int(other),
            "awaiting_model": int(awaiting),
            # Not part of the block: what the queued / not queued split is counted from.
            _BY_LEVEL: {str(level): int(count) for level, count in by_level},
        }

    # Its own cache slot: refresh_tick keeps the background block's counts under the bare path.
    return _cached_counts(store.path, read, _TAGS_BLANK, slot="sources_status.tags")


def _configured_model_target(target: Path | None) -> str | None:
    """``default_model_target`` of the project's ``find-jobs.json`` (what the demand lane asks), or ``None``."""

    if target is None:
        return None
    path = Path(target) / "find-jobs.json"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8")).get("default_model_target")
    except (OSError, ValueError, AttributeError):
        return None
    return value if type(value) is str and value else None


def _queued(
    counts: Mapping[str, object],
    *,
    setting: object,
    refresh_enabled: bool,
    ticker: RefreshTicker | None,
) -> tuple[int, int, str | None]:
    """``(queued, not queued, why not)`` of the titles no rule and no model has given a function.

    Queued: the titles a model WILL tag under the settings in effect. With
    the backfill off that is the demand set only (the levels the active
    profiles ask for, as the queue itself reads them); nothing is queued
    when model tagging or the background work is off, or this server has no
    background thread to do it.
    """

    awaiting = int(counts.get("awaiting_model") or 0)  # type: ignore[call-overload]
    queue = ticker.tag_queue if ticker is not None else None
    if queue is None:
        return 0, awaiting, NOT_QUEUED_NO_THREAD if awaiting else None
    if not refresh_enabled:
        return 0, awaiting, NOT_QUEUED_REFRESH_OFF if awaiting else None
    if not getattr(setting, "model_enabled", False):
        return 0, awaiting, NOT_QUEUED_MODEL_OFF if awaiting else None
    if getattr(setting, "backfill_enabled", False):
        return awaiting, 0, None
    by_level = counts.get(_BY_LEVEL)
    demand_levels = getattr(queue, "demand_levels", None)
    levels = demand_levels() if callable(demand_levels) else ()
    demand = sum(int(by_level.get(level, 0)) for level in levels) if isinstance(by_level, dict) else 0
    demand = min(demand, awaiting)
    rest = awaiting - demand
    return demand, rest, NOT_QUEUED_BACKFILL_OFF if rest else None


def _tagging(
    queued: int,
    queue: Mapping[str, object] | None,
    *,
    refresh_enabled: bool,
    model_enabled: bool,
    backfill: bool,
    manual_update_live: bool,
) -> dict[str, object]:
    """What the model tag queue is doing now: one ``state`` and, when it is failing, why and when it tries again.

    From the settings and the queue, not from the counts: tagging that is
    switched off reads ``off`` also when nothing is waiting.
    """

    state = TAGGING_IDLE
    detail: object = None
    retry_after: object = None
    model: object = None
    if queue is None:
        state = TAGGING_INACTIVE
    elif not refresh_enabled:
        state = TAGGING_PAUSED
    elif not model_enabled:
        state = TAGGING_OFF
    else:
        for lane in (LANE_DEMAND, LANE_BACKFILL) if backfill else (LANE_DEMAND,):
            status = queue.get(lane)
            if isinstance(status, dict) and (status.get("consecutive_failures") or 0) > 0 and queued > 0:
                state, detail, retry_after, model = TAGGING_FAILING, status.get("last_error"), status.get("retry_after"), status.get("model")
                break
        else:
            if queued > 0:
                state = TAGGING_WAITING_FOR_UPDATE if manual_update_live else TAGGING_RUNNING
    return {"state": state, "detail": detail, "retry_after": retry_after, "model": model}


def tags_block(
    home_root: Path,
    target: Path | None,
    *,
    ticker: RefreshTicker | None = None,
    environ: Mapping[str, str] | None = None,
    background: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The ``tags`` block: the counts, what a model will and will not tag, the setting, the lanes' models, the queue.

    ``background`` is the ``background`` block of the same status read (who
    runs an update now); without it the refresh setting is read here.
    """

    setting = tagging_setting(Path(home_root), target, environ=environ)
    queue = ticker.tag_queue_status() if ticker is not None else None
    configured = _configured_model_target(target)
    kind, override = backfill_adapter(setting.backfill_model, configured)
    models: dict[str, object] = {
        LANE_DEMAND: configured,
        LANE_BACKFILL: None if kind is None else (f"{kind}:{override}" if override else kind),
    }
    if isinstance(queue, dict):
        # Once a lane has called a model, its status names the exact model that answered.
        for lane in (LANE_DEMAND, LANE_BACKFILL):
            status = queue.get(lane)
            called = status.get("model") if isinstance(status, dict) else None
            if isinstance(called, str) and called:
                models[lane] = called
    counts = _tag_counts(Path(home_root))
    auto = background.get("auto_refresh") if background is not None else None
    refresh_enabled = bool(auto.get("enabled")) if isinstance(auto, dict) else auto_refresh_setting(Path(home_root), target, environ=environ).enabled
    queued, not_queued, reason = _queued(counts, setting=setting, refresh_enabled=refresh_enabled, ticker=ticker)
    manual_live = background is not None and bool(background.get("in_progress")) and background.get("trigger") != "auto"
    return {
        **{key: value for key, value in counts.items() if key != _BY_LEVEL},
        "awaiting_model_queued": queued,
        "awaiting_model_not_queued": not_queued,
        "not_queued_reason": reason,
        "tagging": _tagging(
            queued,
            queue if isinstance(queue, dict) else None,
            refresh_enabled=refresh_enabled,
            model_enabled=setting.model_enabled,
            backfill=setting.backfill_enabled,
            manual_update_live=manual_live,
        ),
        "setting": setting.to_json(),
        "models": models,
        "queue": queue,
    }


def text_index_block(home_root: Path) -> dict[str, object]:
    """The ``text_index`` block: postings with stored text, and those a keyword search cannot check."""

    from . import text_index

    def read(conn: sqlite3.Connection) -> dict[str, object]:
        with_text, without_text = conn.execute(
            "SELECT COALESCE(SUM(has_text), 0), COALESCE(SUM(1 - has_text), 0) FROM postings"
        ).fetchone()
        return {"available": True, "postings_with_text": int(with_text), "unchecked": int(without_text)}

    return _cached_counts(
        text_index.text_index_path(Path(home_root)),
        read,
        {"available": False, "postings_with_text": 0, "unchecked": 0},
        slot="sources_status.text_index",
    )


def _minutes(later: datetime, earlier: datetime) -> int:
    return max(0, int((later - earlier).total_seconds() // 60))


def refresh_block(
    background: Mapping[str, object],
    *,
    now: datetime | None = None,
    schedule: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The ``refresh`` block, from the ``background`` block of the same status read.

    ``schedule`` (0110-029, ``refresh_tick.schedule_status``): when the
    background checks run; ``None`` when the caller did not read it.

    Derived, not read again, so the two can never disagree: ``enabled`` and
    ``state`` are the background block's; ``last_updated_at`` is when the
    last finished update ended (``null`` while one runs or before the first);
    ``next_tick_at`` is ``null`` unless a check is scheduled. The two
    ``*_minutes*`` fields are whole minutes at the time of this read.
    """

    moment = datetime.now(timezone.utc) if now is None else now
    auto = background.get("auto_refresh")
    last = background.get("last_update")
    last_updated_at = None
    if isinstance(last, dict):
        last_updated_at = last.get("finished_at") or last.get("started_at")
    updated = _parse(last_updated_at)
    next_tick_at = background.get("next_tick_at")
    due = _parse(next_tick_at)
    return {
        "enabled": bool(auto.get("enabled")) if isinstance(auto, dict) else False,
        "state": background.get("state"),
        "in_progress": bool(background.get("in_progress")),
        "trigger": background.get("trigger"),
        "last_updated_at": last_updated_at if updated is not None else None,
        "last_updated_minutes_ago": None if updated is None else _minutes(moment, updated),
        "next_tick_at": next_tick_at if due is not None else None,
        "next_tick_in_minutes": None if due is None else _minutes(due, moment),
        "schedule": dict(schedule) if schedule is not None else None,
    }


__all__ = ["refresh_block", "tags_block", "text_index_block"]
