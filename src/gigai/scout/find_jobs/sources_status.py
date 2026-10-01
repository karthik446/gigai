"""The ``tags``, ``text_index`` and ``refresh`` blocks of ``GET /api/sources/update`` (0110-024 P4, 0110-025 R4, 0110-026 F2).

Additive beside the ``background`` block (``refresh_tick.background_status``),
whose key set is fixed: these three say what the sources strip and the
settings page show, each in the words the UI uses.

* ``tags``: how many stored titles the rules gave a function, how many a
  model did, how many a model looked at and could not place (``model_other``)
  and how many still wait for a model (``awaiting_model``: exactly what
  ``TagStore.count_awaiting_model`` counts, so it reaches 0 when the queue is
  done, which ``function IS NULL`` never does). ``queue`` is the model tag
  queue's own status (``RefreshTicker.tag_queue_status``: failures, the last
  error, when it retries), ``null`` when this server runs no refresh thread.
* ``text_index``: postings a keyword search can check, and the ones it cannot.
* ``refresh``: on or off, whether an update runs now and who started it,
  when the sources were last updated (and how many minutes ago), and when
  the next automatic check is due.

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
from .refresh_tick import RefreshTicker, _cached_counts, _parse
from .tag_store import SOURCE_MODEL, SOURCE_RULES

_TAGS_BLANK: dict[str, object] = {
    "available": False,
    "titles": 0,
    "tagged_by_rules": 0,
    "tagged_by_model": 0,
    "model_other": 0,
    "awaiting_model": 0,
}


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
        return {
            "available": True,
            "titles": int(titles),
            "tagged_by_rules": int(by_rules),
            "tagged_by_model": int(by_model),
            "model_other": int(other),
            "awaiting_model": int(awaiting),
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


def tags_block(
    home_root: Path,
    target: Path | None,
    *,
    ticker: RefreshTicker | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """The ``tags`` block: the counts, the tagging setting, the two lanes' models and the queue's status."""

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
    return {
        **_tag_counts(Path(home_root)),
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


def refresh_block(background: Mapping[str, object], *, now: datetime | None = None) -> dict[str, object]:
    """The ``refresh`` block, from the ``background`` block of the same status read.

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
    }


__all__ = ["refresh_block", "tags_block", "text_index_block"]
