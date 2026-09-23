"""Journal-authoritative Scout ATS watchlist records."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import uuid

from ...canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ...journal import JournalArtifact, JournalTransition, run_with_journal_writer
from .contracts import WatchlistClient, WatchlistEntry
from ...workpad import ResolvedWorkpad, resolve_workpad


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resolved(home_root: Path, target: Path, gig_id: str | None) -> ResolvedWorkpad:
    if isinstance(target, ResolvedWorkpad):
        return target
    return resolve_workpad(
        home_root=home_root,
        requested_target=target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )


def _receipt_path(operation: str, key: str) -> str:
    digest = digest_imported_bytes(key.encode("utf-8")).removeprefix("sha256:")
    return f"records/operations/{operation}-{digest}.json"


def _ref(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": "application/json",
        "size_bytes": len(data),
    }


def _snapshot_entries(writer: object) -> tuple[WatchlistEntry, ...]:
    snapshot = writer.snapshot(("records/scout-watchlist/",))  # type: ignore[attr-defined]
    entries: list[WatchlistEntry] = []
    for path, data in sorted(snapshot.artifacts.items()):
        if not path.endswith(".json"):
            continue
        try:
            entries.append(WatchlistEntry.from_json(parse_json_bytes(data)))
        except Exception:
            continue
    return tuple(entries)


def list_active(
    home_root: Path,
    target: Path,
    gig_id: str | None = None,
) -> tuple[WatchlistEntry, ...]:
    """Return active entries from the authenticated journal snapshot."""

    resolved = _resolved(home_root, target, gig_id)

    def read(writer: object) -> tuple[WatchlistEntry, ...]:
        return tuple(item for item in _snapshot_entries(writer) if item.state == "active")

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=read,
    )


class JournalWatchlistClient:
    """A ``WatchlistClient`` bound to one authenticated workpad."""

    def __init__(self, home_root: Path, target: Path, gig_id: str | None = None) -> None:
        self._resolved = _resolved(home_root, target, gig_id)

    def add_to_watchlist(self, entry: WatchlistEntry) -> WatchlistEntry:
        key = f"scout_watchlist:{entry.provider}:{entry.board_token}"
        record_path = f"records/scout-watchlist/{entry.watchlist_id}.json"
        record_bytes = canonical_json_bytes(entry.to_json())
        receipt_path = _receipt_path("scout_watchlist_add", key)
        payload = {"provider": entry.provider.value, "board_token": entry.board_token}
        payload_sha = digest_imported_bytes(canonical_json_bytes(payload))

        def publish(writer: object) -> WatchlistEntry:
            snapshot = writer.snapshot(("records/",))  # type: ignore[attr-defined]
            for data in snapshot.artifacts.values():
                try:
                    receipt = parse_json_bytes(data)
                except Exception:
                    continue
                if not isinstance(receipt, dict) or receipt.get("operation_key") != key:
                    continue
                refs = receipt.get("artifact_refs")
                if isinstance(refs, list):
                    paths = [item.get("path") for item in refs if isinstance(item, dict)]
                    if record_path in paths and record_path in snapshot.artifacts:
                        return WatchlistEntry.from_json(parse_json_bytes(snapshot.artifacts[record_path]))

            receipt = {
                "schema_version": "1.0",
                "operation_id": f"operation_{uuid.uuid4()}",
                "project_id": self._resolved.project_id,
                "gig_id": self._resolved.gig_id,
                "operation": "scout_watchlist_add",
                "operation_key": key,
                "payload_sha256": payload_sha,
                "outcome": "committed",
                "artifact_refs": [_ref(record_path, record_bytes)],
                "created_at": _now(),
            }
            receipt_bytes = canonical_json_bytes(receipt)
            artifacts = (
                JournalArtifact(record_path, record_bytes),
                JournalArtifact(receipt_path, receipt_bytes),
            )
            metadata = {
                "project_id": self._resolved.project_id,
                "gig_id": self._resolved.gig_id,
                "operation": "scout_watchlist_add",
                "operation_key": key,
                "artifact_refs": [_ref(path, data) for path, data in ((record_path, record_bytes), (receipt_path, receipt_bytes))],
            }
            writer.record(  # type: ignore[attr-defined]
                JournalTransition(
                    f"handoff_{uuid.uuid4()}",
                    "scout_public_acquisition_progress",
                    "Committed Scout ATS watchlist entry.",
                    artifacts,
                    metadata,
                )
            )
            return entry

        return run_with_journal_writer(
            workpad=self._resolved.path,
            project_id=self._resolved.project_id,
            gig_id=self._resolved.gig_id,
            operation=publish,
        )


def add_to_watchlist(
    entry: WatchlistEntry,
    home_root: Path,
    target: Path,
    gig_id: str | None = None,
) -> WatchlistEntry:
    """Persist one entry, idempotently keyed by provider and board token."""

    return JournalWatchlistClient(home_root, target, gig_id).add_to_watchlist(entry)


__all__ = ["JournalWatchlistClient", "WatchlistClient", "add_to_watchlist", "list_active"]
