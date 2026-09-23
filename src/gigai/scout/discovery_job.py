"""Read-only R1 view of persisted discovery acquisition results.

The completed discovery packet is already the authoritative public capture.
This adapter deliberately does not create a second mutable job database: it
normalizes the selected packet posting and its public acquisition state for
proposal callers, while retaining exact resolver provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType
import time

from .posting_inputs import ScoutPostingInputError, resolve_discovery_posting_input_from_journal
from ..workpad import ResolvedWorkpad


@dataclass(frozen=True)
class DiscoveryJob:
    """One immutable public acquisition row derived from a committed packet."""

    opportunity_id: str
    snapshot_id: str
    acquisition_state: str
    source_kind: str
    title: str | None
    employer: str | None
    public_source: Mapping[str, object]
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.acquisition_state not in {"considered", "uncertain", "excluded"}:
            raise ValueError("discovery acquisition state is invalid")
        if self.source_kind not in {"agent_discovered", "user_provided"}:
            raise ValueError("discovery source kind is invalid")
        if not isinstance(self.opportunity_id, str) or not isinstance(self.snapshot_id, str):
            raise ValueError("discovery identity is invalid")
        object.__setattr__(self, "public_source", MappingProxyType(dict(self.public_source)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


class ScoutDiscoveryJobError(ValueError):
    """A bounded refusal to expose unauthenticated public acquisition data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DiscoveryImportProgress:
    """Bounded, public-only progress from one caller-supplied acquisition batch.

    This is intentionally a transient import result.  It does not authenticate
    postings or write a second job store; callers must publish each accepted
    posting through the existing discovery journal protocol.
    """

    considered: tuple[Mapping[str, object], ...]
    duplicates: tuple[Mapping[str, object], ...]
    failures: tuple[Mapping[str, object], ...]
    exclusions: tuple[Mapping[str, object], ...]
    processed: int
    next_index: int
    stopped_reason: str

    def __post_init__(self) -> None:
        if self.processed < 0 or self.next_index < self.processed:
            raise ValueError("discovery import progress counters are invalid")
        if self.stopped_reason not in {"completed", "deadline"}:
            raise ValueError("discovery import stop reason is invalid")
        for field in ("considered", "duplicates", "failures", "exclusions"):
            object.__setattr__(self, field, tuple(MappingProxyType(dict(item)) for item in getattr(self, field)))

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": "scout-public-import-progress:1",
            "processed": self.processed,
            "next_index": self.next_index,
            "stopped_reason": self.stopped_reason,
            "considered": [dict(item) for item in self.considered],
            "duplicates": [dict(item) for item in self.duplicates],
            "failures": [dict(item) for item in self.failures],
            "exclusions": [dict(item) for item in self.exclusions],
        }


_PUBLIC_IMPORT_KEYS = frozenset({
    "opportunity_id", "snapshot_id", "source_kind", "title", "employer", "url",
    "acquisition_state", "error", "duplicate_of", "excluded_reason",
})
_PUBLIC_IMPORT_LIMIT = 512


def run_bounded_public_import(
    rows: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    *, deadline_seconds: float = 30.0,
    clock: object = time.monotonic,
) -> DiscoveryImportProgress:
    """Classify already-acquired public rows until a wall-time deadline.

    No fetching, crawling, journal allocation, or private matching occurs
    here.  The strict row shape keeps acquisition failures/duplicates visible
    without treating caller data as an authenticated discovery packet.
    """
    if not isinstance(rows, (tuple, list)) or len(rows) > _PUBLIC_IMPORT_LIMIT:
        raise ScoutDiscoveryJobError("discovery_import_invalid", "public import batch exceeds its bound")
    if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) or not 0 < deadline_seconds <= 300:
        raise ScoutDiscoveryJobError("discovery_import_invalid", "public import deadline is outside its bound")
    now = clock
    if not callable(now):
        raise ScoutDiscoveryJobError("discovery_import_invalid", "public import clock is invalid")
    started = float(now())
    considered: list[Mapping[str, object]] = []
    duplicates: list[Mapping[str, object]] = []
    failures: list[Mapping[str, object]] = []
    exclusions: list[Mapping[str, object]] = []
    identities: set[tuple[str, str]] = set()
    index = 0
    while index < len(rows):
        if float(now()) - started >= deadline_seconds:
            break
        row = rows[index]
        index += 1
        if not isinstance(row, Mapping) or not set(row).issubset(_PUBLIC_IMPORT_KEYS):
            failures.append({"index": index - 1, "reason": "row_shape_invalid"})
            continue
        item = dict(row)
        state = item.get("acquisition_state", "considered")
        if not isinstance(state, str):
            failures.append({"index": index - 1, "reason": "acquisition_state_invalid"})
            continue
        opportunity = item.get("opportunity_id")
        snapshot = item.get("snapshot_id")
        if not isinstance(opportunity, str) or not isinstance(snapshot, str) or not opportunity or not snapshot:
            failures.append({"index": index - 1, "reason": "identity_missing"})
            continue
        identity = (opportunity, snapshot)
        if identity in identities or item.get("duplicate_of") is not None:
            duplicates.append({**item, "reason": "duplicate"})
            continue
        identities.add(identity)
        if item.get("error") is not None or state in {"failed", "error"}:
            failures.append({**item, "reason": "acquisition_failed"})
        elif item.get("excluded_reason") is not None or state == "excluded":
            exclusions.append({**item, "reason": "acquisition_excluded"})
        else:
            considered.append(item)
    stopped_reason = "completed" if index == len(rows) else "deadline"
    return DiscoveryImportProgress(tuple(considered), tuple(duplicates), tuple(failures), tuple(exclusions), index, index, stopped_reason)


def resolve_selected_discovery_job(*, resolved: ResolvedWorkpad, selector: Mapping[str, object]) -> DiscoveryJob:
    """Hydrate one posting through the accepted completed-Run resolver."""
    try:
        value = resolve_discovery_posting_input_from_journal(resolved, selector)
    except ScoutPostingInputError as exc:
        raise ScoutDiscoveryJobError(exc.code, "discovery job is unavailable or unauthenticated") from exc
    posting = value.get("posting")
    if not isinstance(posting, Mapping):
        raise ScoutDiscoveryJobError("discovery_job_invalid", "discovery posting is malformed")
    source = posting.get("source")
    if not isinstance(source, Mapping):
        raise ScoutDiscoveryJobError("discovery_job_invalid", "discovery source provenance is unavailable")
    status = source.get("status")
    acquisition_state = "considered" if status in {"captured", "retrieved", "published"} else "uncertain"
    title = posting.get("title") if isinstance(posting.get("title"), str) else None
    employer = posting.get("employer") if isinstance(posting.get("employer"), str) else None
    return DiscoveryJob(
        opportunity_id=str(value["opportunity_id"]),
        snapshot_id=str(value["snapshot_id"]),
        acquisition_state=acquisition_state,
        source_kind="agent_discovered",
        title=title,
        employer=employer,
        public_source=dict(source),
        provenance={
            "run_ref": value.get("run_ref"),
            "receipt_ref": value.get("receipt_ref"),
            "checkpoint_ref": value.get("checkpoint_ref"),
            "posting_ref": value.get("posting_ref"),
        },
    )


__all__ = [
    "DiscoveryImportProgress", "DiscoveryJob", "ScoutDiscoveryJobError",
    "resolve_selected_discovery_job", "run_bounded_public_import",
]
