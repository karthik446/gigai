"""Wave-1b acquisition node for the Scout find-jobs graph.

The node is deliberately an orchestration boundary: provider clients are
injected protocols, while persistence is delegated to the existing public
acquisition journal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from ...canonical import canonical_json_bytes, digest_imported_bytes
from ..acquisition_records import import_public_rows
from .contracts import (
    ATSBoardClient,
    ATSProvider,
    AcquireInput,
    AcquireOutput,
    FailureRow,
    FindJobsContractError,
    NodeContext,
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
    parse_board_url,
)
from ...workpad import ResolvedWorkpad, resolve_workpad


class AcquireAllSourcesFailedError(FindJobsContractError):
    """Every enabled acquisition source failed; no batch was written."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_batch_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-")
    return (value or "acquire")[:120]


def _role_match(row: PostingRow, roles: Sequence[str]) -> bool:
    haystack = f"{row.title} {row.company} {row.location}".casefold()
    return any(str(role).casefold().strip() in haystack for role in roles)


def _digest(row: PostingRow) -> str:
    return row.content_sha256 or digest_imported_bytes(canonical_json_bytes(row.to_json()))


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
) -> AcquireOutput:
    batch_id = _safe_batch_id(context.operation_key)
    failures: list[FailureRow] = []
    rows: list[PostingRow] = list(input.rows)
    watchlist_refs: list[str] = []
    source_outcomes: dict[str, bool] = {}

    if not rows:
        if input.config.sources.exa:
            exa_ok = False
            try:
                discovered = tuple(exa.search(http_client, input.config))
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
            try:
                boards = _watchlist_entries(watchlist)
                if not boards:
                    ats_ok = True
                for board in boards:
                    try:
                        rows.extend(ats.list_board(http_client, board.provider.value, board.board_token, input.config))
                        ats_ok = True
                    except Exception as exc:
                        failures.append(FailureRow(SourceKind.ATS, board.board_token, None, type(exc).__name__.lower(), "ATS board fetch failed"))
            except Exception as exc:
                failures.append(FailureRow(SourceKind.ATS, "ats", None, type(exc).__name__.lower(), "ATS watchlist fetch failed"))
            source_outcomes["ats"] = ats_ok

        if source_outcomes and not any(source_outcomes.values()):
            codes = ", ".join(f"{failure.source_kind.value}:{failure.code}" for failure in failures)
            detail = f" ({codes})" if codes else ""
            raise AcquireAllSourcesFailedError(
                "acquire_all_sources_failed",
                f"every enabled acquisition source failed{detail}",
            )

    unique: list[PostingRow] = []
    seen: set[str] = set()
    for row in rows:
        normalized = normalize_url(row.normalized_url or row.url)
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized != row.normalized_url:
            row = PostingRow(row.url, normalized, row.provider, row.board_token, row.company, row.title, row.location, row.published_at, row.content_sha256, row.source_kind, row.query_key)
        unique.append(row)
    rows = unique
    current = {row.normalized_url: _digest(row) for row in rows}
    resolved = _resolved(context, home_root, target)
    previous = _prior_observations(resolved.path, batch_id)
    url_diff = diff_url_sets(previous, current)
    added = {item.url for item in url_diff.added}
    edited = {item.url for item in url_diff.edited}
    results: list[PostingRowResult] = []
    selected: list[SelectedPosting] = []
    for row in rows:
        if row.normalized_url in added:
            outcome = RowOutcome.NEW
        elif row.normalized_url in edited:
            outcome = RowOutcome.EDITED
        else:
            outcome = RowOutcome.UNCHANGED
        results.append(PostingRowResult(row, outcome))
        if input.selection_rule is SelectionRule.NEW_OR_EDITED_ROLE_MATCH and outcome in {RowOutcome.NEW, RowOutcome.EDITED} and _role_match(row, input.config.roles) and len(selected) < input.selection_cap:
            selected.append(SelectedPosting(row.normalized_url, row.url, _digest(row), True))

    status = import_public_rows(resolved=resolved, batch_id=batch_id, rows=[_public_row(row) for row in rows] or [{
        "opportunity_id": "empty", "snapshot_id": digest_imported_bytes(b"empty")[:32], "source_kind": "agent_discovered", "title": "empty", "employer": "empty", "url": "https://example.invalid/empty", "acquisition_state": "excluded", "excluded_reason": "no_rows",
    }])
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
    )


__all__ = ["acquire_node"]
