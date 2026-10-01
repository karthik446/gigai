"""The search side of the company index (N11-C): watchlist postings with NO board request.

``read_indexed_boards`` is what a find-jobs acquire pass calls in place of
the board fetch. It returns the same three things the fetch returned
(``rows, failures, summary``), so sealing, the reuse rule, ranking and the
selection after it are untouched; only where the rows come from changes:

1. every watchlist company's index file is read
   (``<home>/cache/scout/companies/<ats>:<slug>.json``);
2. its live postings are filtered on the indexed fields alone -- the title
   (``title_query.TitleMatcher``: the fetch path's whole-word rule plus the
   profile's tag query, the same matcher ``_role_match`` uses), the publication
   window and the country rule (``filters``) -- so most companies never
   have their board body opened;
3. the postings that remain are read back as the acquire-shaped
   ``PostingRow`` (description included) from the board cache
   ``gigai scout sources update`` filled;
4. uat-bug-028: those rows are filtered on the config's work mode + area
   (``work_mode.work_mode_fit``). This needs the cached row, since the
   board's own ``work_mode`` field lives there and not in the index. It is
   still before ranking and the import cap; ``work_mode_filtered_out`` in
   the summary counts what it dropped.

There is no HTTP client in this module. When nothing is indexed the pass
returns no rows and one ``sources_update_required`` failure; when the index
is stale it still returns what is stored and says so. Either way the
``index`` block of the summary (``status`` / ``needs_update`` / ``message``)
is what the UI shows: "Run Update sources".

Every matching live posting is returned, not only the ones first seen or
changed since the last search: which rows are NEW, EDITED or UNCHANGED, and
which unchanged ones still need an assessment, is decided by the existing
acquire rules from the sealed batches, exactly as it was when the rows came
from a fetch. ``touched_since_last_search`` in the summary is the index's
own count of what is new or changed since the previous search read it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

from .ats_board_clients import BoardCache
from .company_index import (
    DEFAULT_STALE_AFTER_HOURS,
    INDEX_EMPTY,
    CompanyIndex,
    CompanyIndexEntry,
    IndexedPosting,
    cached_posting_rows,
    index_stamp,
    index_state,
)
from .contracts import ATSProvider, FailureRow, FindJobsConfig, PostingRow, SourceKind, WatchlistEntry, normalize_url
from .filters import country_match, published_too_old
from .tag_store import TagStore
from .title_query import TitleMatcher
from .work_mode import work_mode_fit

SOURCES_UPDATE_REQUIRED_CODE = "sources_update_required"
_LAST_SEARCH_FILENAME = "last-search.json"
_LAST_SEARCH_SCHEMA = "scout-index-last-search:1"


def _last_search_path(index: CompanyIndex) -> Path:
    return index.root / _LAST_SEARCH_FILENAME


def read_last_search(index: CompanyIndex) -> str | None:
    """When the previous search read the index (``None`` before the first one)."""

    try:
        payload = json.loads(_last_search_path(index).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != _LAST_SEARCH_SCHEMA:
        return None
    value = payload.get("searched_at")
    return value if type(value) is str and value else None


def write_last_search(index: CompanyIndex, searched_at: str) -> None:
    path = _last_search_path(index)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}-{threading.get_ident()}")
    try:
        tmp.write_text(json.dumps({"schema_version": _LAST_SEARCH_SCHEMA, "searched_at": searched_at}), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _indexed_row(entry: CompanyIndexEntry, posting: IndexedPosting) -> PostingRow:
    """The indexed fields as a row, for the window and country rules only (no description)."""

    return PostingRow(
        url=posting.url,
        normalized_url=normalize_url(posting.url),
        provider=ATSProvider(entry.ats),
        board_token=entry.slug,
        company=entry.slug,
        title=posting.title,
        location=posting.location,
        published_at=posting.published_at,
        content_sha256=posting.content_sha256,
        source_kind=SourceKind.ATS,
        query_key=f"ats:{entry.ats}:{entry.slug}",
        countries=posting.countries,
    )


def _keep(entry: CompanyIndexEntry, posting: IndexedPosting, config: FindJobsConfig, now: datetime) -> bool:
    row = _indexed_row(entry, posting)
    if published_too_old(row, config, now=now):
        return False
    if config.countries and country_match(row.location, config.countries, structured_countries=row.countries) is False:
        return False
    return True


def read_indexed_boards(
    boards: Sequence[WatchlistEntry],
    *,
    index: CompanyIndex,
    cache: BoardCache,
    config: FindJobsConfig,
    progress: object | None = None,
    started_at: float | None = None,
    now: datetime | None = None,
    stale_after_hours: float = DEFAULT_STALE_AFTER_HOURS,
    remember_search: bool = True,
    tags: TagStore | None = None,
) -> tuple[list[PostingRow], list[FailureRow], dict[str, object]]:
    """Read the watchlist's postings from the company index. Makes no request.

    Returns ``(rows, failures, summary)`` in the shape of the board fetch it
    replaces: rows in watchlist order (user-added boards first, then by
    provider and token), one ``sources_update_required`` failure when the
    search has nothing current to read, and a summary whose ``requests`` is
    always ``0``. ``tags`` is the title-tag store for the tag query (``None``:
    the whole-word rule alone); the summary's ``title_match`` block counts what
    matched by rule, by tag only, and what the rule judged alone for want of a tag.
    """

    began = time.monotonic() if started_at is None else started_at
    moment = datetime.now(timezone.utc) if now is None else now
    ordered = sorted(
        boards,
        key=lambda board: (board.first_seen.query_key.startswith("catalog:"), board.provider.value, board.board_token),
    )
    since = read_last_search(index)
    title_matcher = TitleMatcher(config.roles, tags)
    planned = getattr(progress, "boards_planned", None)
    if callable(planned):
        planned(total=len(ordered), budget_seconds=None, rotation=None)
    finished = getattr(progress, "board_finished", None)

    rows: list[PostingRow] = []
    entries: list[CompanyIndexEntry | None] = []
    listed = matched = prefiltered_out = filtered_out = without_text = not_cached = touched = 0
    work_mode_filtered_out = 0
    companies_read = companies_matched = 0
    for board in ordered:
        ats, slug = board.provider.value, board.board_token
        board_started = time.monotonic()
        entry = index.read(ats, slug)
        entries.append(entry)
        board_rows: list[PostingRow] = []
        live_count = 0
        if entry is not None:
            companies_read += 1
            wanted: list[str] = []
            fresh: set[str] = set()
            for posting in entry.live():
                live_count += 1
                if not title_matcher.matches(posting.title):
                    prefiltered_out += 1
                    continue
                if not _keep(entry, posting, config, moment):
                    filtered_out += 1
                    continue
                wanted.append(posting.posting_id)
                if since is None or posting.touched_at > since:
                    fresh.add(posting.posting_id)
            listed += live_count
            if wanted:
                found = cached_posting_rows(cache, ats, slug, wanted)
                kept = {posting_id: row for posting_id, row in found.rows.items() if work_mode_fit(row, config).passes}
                board_rows = list(kept.values())
                work_mode_filtered_out += len(found.rows) - len(kept)
                without_text += sum(1 for posting_id in found.without_text if posting_id in kept)
                not_cached += len(found.missing)
                touched += len(fresh - (found.rows.keys() - kept.keys()))
                if board_rows:
                    companies_matched += 1
        rows.extend(board_rows)
        matched += len(board_rows)
        if callable(finished):
            finished(
                provider=ats,
                board_token=slug,
                status="cached" if entry is not None else "skipped",
                requests=0,
                cache="index" if entry is not None else None,
                postings=live_count,
                matched=len(board_rows),
                elapsed_ms=int((time.monotonic() - board_started) * 1000),
                code=None if entry is not None else SOURCES_UPDATE_REQUIRED_CODE,
            )

    state = index_state(entries, now=moment, stale_after_hours=stale_after_hours)
    failures: list[FailureRow] = []
    if boards and state.status == INDEX_EMPTY:
        failures.append(FailureRow(SourceKind.ATS, "ats", None, SOURCES_UPDATE_REQUIRED_CODE, state.message or "Run Update sources."))
    searched_at = index_stamp(moment)
    if remember_search and companies_read:
        try:
            write_last_search(index, searched_at)
        except OSError:
            pass
    summary: dict[str, object] = {
        "source": "index",
        "total": len(ordered),
        "fetched": 0,
        "cached": companies_read,
        "failed": 0,
        "skipped": len(ordered) - companies_read,
        "requests": 0,
        "cache_hits": companies_read,
        "listed": listed,
        "prefiltered_out": prefiltered_out,
        "title_match": title_matcher.counts.to_json(),
        "filtered_out": filtered_out,
        "work_mode_filtered_out": work_mode_filtered_out,
        "detail_fetched": 0,
        "detail_cached": matched - without_text,
        "matched": matched,
        "companies_matched": companies_matched,
        "without_text": without_text,
        "not_cached": not_cached,
        "touched_since_last_search": touched,
        "last_search_at": since,
        "searched_at": searched_at,
        "elapsed_seconds": round(time.monotonic() - began, 3),
        "budget_seconds": None,
        "index": state.to_json(),
    }
    return rows, failures, summary


def index_line(summary: dict[str, object]) -> str:
    """One human line for the run's log: what the search read, and that it asked no board."""

    state = summary.get("index")
    status = state.get("status") if isinstance(state, dict) else "unknown"
    line = (
        "scout acquire: read the company index ({status}): {cached} of {total} companies, "
        "{listed} postings stored, {matched} matched ({touched_since_last_search} new or changed since the last search), "
        "{requests} board requests, {elapsed_seconds}s"
    ).format(status=status, **summary)
    exa_new = summary.get("exa_new")
    if isinstance(exa_new, dict) and exa_new.get("found"):
        noun = "company" if exa_new.get("fetched") == 1 else "companies"
        # The cap counts companies (boards), not requests: one company can
        # take several requests, which the line above counts.
        line += f"; fetched {exa_new.get('fetched')} new {noun} found by Exa (cap {exa_new.get('cap')} companies per search)"
        if exa_new.get("failed"):
            line += f", {exa_new.get('failed')} did not answer"
        if exa_new.get("waiting"):
            line += f", {exa_new.get('waiting')} more wait for the next Update sources"
    return line


__all__ = [
    "SOURCES_UPDATE_REQUIRED_CODE",
    "index_line",
    "read_indexed_boards",
    "read_last_search",
    "write_last_search",
]
