"""0110-019: "Find postings from the last N days" -- widen a finished run's posted window.

The publication window (``max_age_days``) is applied when a run picks its
postings (``filters.published_too_old``), so a wider window used to need a
whole new run although every board is already stored on this machine. The
Jobs page now filters the postings it shows by their own date (the "Posted"
chips, ``ui/src/postedWindowModel.js``: no request at all), and when the
chosen window is wider than what the shown run searched, one click searches
the stored boards again with that window.

That search (:func:`find_older`) is ``index_search.read_indexed_boards``
over the watchlist with the RUN'S OWN sealed config, only the window
changed: it reads the company index and the board cache and makes no
request. Of what it returns, the rows the run already holds are left out,
and the rest pass the filters acquire applies after its own read
(location / visa, role, work mode, the public-row shape).

A sealed run is never rewritten. The postings found are a record of their
own, one file per run beside the quick-assess store (a plain file under the
GigAI home, like an "assess all" record; nothing here is journaled, so the
workpad stays clean):

``<home>/scout/<project_id>/posted_window/<run_id>.json``

* ``searches``: one entry per click (``days``, ``searched_at``, ``added``,
  ``matched``, ``not_added``, ``without_text``).
* ``rows``: the added postings, in the sealed ``PostingRow`` shape.

The run's reads (``api/run_reads``, ``api/rank``, ``api/assess_all``) list
those rows after the run's own, outcome ``new``. Nothing the run assessed is
touched: ranking and assessing the added rows goes through the existing
re-rank record and "assess all" job (``api/posted_window``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import threading

from .contracts import DEFAULT_MAX_AGE_DAYS, MAX_AGE_DAYS_MAXIMUM, FindJobsConfig, PostingRow, PostingRowResult, RowOutcome

RECORD_SCHEMA = "scout-posted-window:1"
#: The Jobs page's "Posted" chips, in days ("Any" is the chip with no window).
WINDOW_CHOICES = (7, 10, 30, 60)

_logger = logging.getLogger("gigai.scout.server")
_LOCK = threading.Lock()
# path -> ((mtime_ns, size), record): a results page reads the record on
# every request, and it changes only when a search adds to it.
_cache: "dict[str, tuple[tuple[int, int], PostedWindow]]" = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PostedWindow:
    """One run's record: the searches made and the postings they added."""

    run_id: str
    searches: tuple[dict[str, object], ...] = ()
    rows: tuple[PostingRow, ...] = ()

    @property
    def searched_days(self) -> int | None:
        """The widest window a search of this record covered (``None``: no search yet)."""

        days = [value for value in (item.get("days") for item in self.searches) if type(value) is int]
        return max(days) if days else None

    def row_results(self) -> tuple[PostingRowResult, ...]:
        return tuple(PostingRowResult(row, RowOutcome.NEW) for row in self.rows)

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": RECORD_SCHEMA,
            "run_id": self.run_id,
            "searches": [dict(item) for item in self.searches],
            "rows": [row.to_json() for row in self.rows],
        }


def records_dir(home_root: Path, target: Path) -> Path:
    from .discovery.storage import project_id

    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "posted_window"


def record_path(home_root: Path, target: Path, run_id: str) -> Path:
    return records_dir(home_root, target) / f"{run_id}.json"


def read(home_root: Path, target: Path, run_id: str) -> PostedWindow | None:
    """The run's record, or ``None`` when no search was made for it (or it cannot be read)."""

    try:
        path = record_path(home_root, target, run_id)
        if path.is_symlink() or not path.is_file():
            return None
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        kept = _cache.get(str(path))
        if kept is not None and kept[0] == stamp:
            return kept[1]
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != RECORD_SCHEMA or value.get("run_id") != run_id:
            return None
        searches = tuple(dict(item) for item in value.get("searches") or () if isinstance(item, dict))
        rows = tuple(PostingRow.from_json(item) for item in value.get("rows") or ())
    except Exception as exc:  # noqa: BLE001 - an unreadable record is no record: the run's own rows still read
        _logger.warning("posted window (%s): the record could not be read: %s", run_id, type(exc).__name__)
        return None
    window = PostedWindow(run_id, searches, rows)
    _cache[str(path)] = (stamp, window)
    return window


def added_rows(home_root: Path | None, target: Path | None, run_id: str) -> tuple[PostingRowResult, ...]:
    """The postings added to ``run_id`` after it ended, as rows of the run (outcome ``new``)."""

    if home_root is None or target is None:
        return ()
    window = read(Path(home_root), Path(target), run_id)
    return window.row_results() if window is not None else ()


def with_added(rows: Sequence[PostingRowResult], added: Sequence[PostingRowResult]) -> tuple[PostingRowResult, ...]:
    """``rows`` then ``added``, never a posting the run itself holds twice."""

    held = {row.posting.normalized_url for row in rows}
    return tuple(rows) + tuple(row for row in added if row.posting.normalized_url not in held)


def run_days(config: FindJobsConfig, *, started_at: str | None) -> int:
    """How many days back the run itself searched.

    ``max_age_days`` (else the default); for a fixed ``published_after`` the
    whole days from that date to the run's start.
    """

    from .filters import _parse_published

    if config.published_after is not None:
        fixed = _parse_published(config.published_after)
        began = _parse_published(started_at) if started_at else None
        if fixed is not None and began is not None:
            return max(0, math.floor((began - fixed).total_seconds() / 86400))
    return config.max_age_days if config.max_age_days is not None else DEFAULT_MAX_AGE_DAYS


def valid_days(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_AGE_DAYS_MAXIMUM


@dataclass(frozen=True)
class FindOutcome:
    """What one search did: the record after it, the rows it added, its ``searches`` entry."""

    window: PostedWindow | None
    added: tuple[PostingRow, ...]
    search: dict[str, object]
    needs_update: bool = False


def find_older(
    *,
    home_root: Path,
    target: Path,
    run_id: str,
    config: FindJobsConfig,
    run_rows: Sequence[PostingRow],
    days: int,
    boards: Sequence[object] | None = None,
    now: datetime | None = None,
) -> FindOutcome:
    """Search the stored boards for postings of the last ``days`` days the run does not hold. No request.

    ``config`` is the run's sealed config and ``run_rows`` its own postings.
    With nothing stored (``Update sources`` never ran) nothing is recorded
    and ``needs_update`` is true.
    """

    from ..acquisition_records import public_row_refusal
    from .company_index import INDEX_EMPTY, CompanyIndex
    from .filters import exclusion_reason
    from .index_search import read_indexed_boards
    from .market_acquisition import IMPORT_ROW_CAP, _dedupe_identity, _merge_exa_and_ats_rows, _public_row, _role_match
    from .sources_update import board_cache_for_home
    from .work_mode import work_mode_fit

    home_root, target = Path(home_root), Path(target)
    wide = replace(config, max_age_days=days, published_after=None)
    if boards is None:
        from .watchlist import list_active

        boards = list_active(home_root, target)
    with _LOCK:
        found, _failures, summary = read_indexed_boards(
            boards,  # type: ignore[arg-type]
            index=CompanyIndex.for_home(home_root),
            cache=board_cache_for_home(home_root),
            config=wide,
            now=now,
            remember_search=False,  # the next run's "new since the last search" count is the runs' own
        )
        state = summary.get("index")
        index = dict(state) if isinstance(state, dict) else {}
        previous = read(home_root, target, run_id)
        search: dict[str, object] = {
            "days": days,
            "searched_at": _now(),
            "matched": 0,
            "added": 0,
            "not_added": 0,
            "without_text": 0,
            "index": index,
        }
        if boards and index.get("status") == INDEX_EMPTY:
            return FindOutcome(previous, (), search, needs_update=True)

        held = list(run_rows) + list(previous.rows if previous is not None else ())
        held_urls = {row.normalized_url for row in held}
        held_ids = {_dedupe_identity(row) for row in held}
        fresh: list[PostingRow] = []
        for row in _merge_exa_and_ats_rows(found):
            # The filters acquire applies after its own read of the index.
            if exclusion_reason(row, wide, now=now) is not None or not _role_match(row, wide.roles):
                continue
            if not work_mode_fit(row, wide).passes or public_row_refusal(_public_row(row)) is not None:
                continue
            search["matched"] = int(search["matched"]) + 1  # type: ignore[call-overload]
            if row.normalized_url in held_urls or _dedupe_identity(row) in held_ids:
                continue
            held_urls.add(row.normalized_url)
            held_ids.add(_dedupe_identity(row))
            fresh.append(row)
        # Newest first; a record never holds more than a run imports.
        fresh.sort(key=lambda row: row.published_at or "", reverse=True)
        room = max(0, IMPORT_ROW_CAP - len(previous.rows if previous is not None else ()))
        added = tuple(fresh[:room])
        search.update(added=len(added), not_added=len(fresh) - len(added), without_text=sum(1 for row in added if not row.text))
        window = PostedWindow(
            run_id,
            (previous.searches if previous is not None else ()) + (search,),
            (previous.rows if previous is not None else ()) + added,
        )
        from .discovery.storage import atomic_write

        atomic_write(record_path(home_root, target, run_id), json.dumps(window.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    _logger.info(
        "posted window (%s): last %d days: %d matched, %d added, %d over the limit, 0 board requests",
        run_id, days, search["matched"], search["added"], search["not_added"],
    )
    return FindOutcome(window, added, search)


__all__ = [
    "RECORD_SCHEMA",
    "WINDOW_CHOICES",
    "FindOutcome",
    "PostedWindow",
    "added_rows",
    "find_older",
    "read",
    "record_path",
    "records_dir",
    "run_days",
    "valid_days",
    "with_added",
]
