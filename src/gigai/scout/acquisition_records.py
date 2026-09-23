"""Journal-authoritative persistence for bounded public Scout imports.

This module intentionally accepts rows that have already been acquired.  It
does not fetch, crawl, schedule, assess, or turn progress into a discovery
packet.  The input snapshot and every cumulative progress revision are
immutable journal artifacts; status is reconstructed from those artifacts in a
new process.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import time
import uuid
from typing import Callable

from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ..journal import (
    JournalArtifact,
    JournalConflictError,
    JournalTransition,
    read_committed_artifact,
    run_with_journal_writer,
)
from ..validators import validate_serialized_contract
from ..workpad import ResolvedWorkpad


_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ROW_KEYS = frozenset({
    "opportunity_id", "snapshot_id", "source_kind", "title", "employer",
    "url", "acquisition_state", "error", "duplicate_of", "excluded_reason",
    "source_snapshot",
})
_SOURCE_KEYS = frozenset({
    "source_kind", "locator", "url", "status", "captured_at",
    "content_sha256", "media_type", "size_bytes",
})
_MAX_ROWS = 512
_MAX_TEXT = 4096


class ScoutAcquisitionError(ValueError):
    """A stable refusal from the public acquisition persistence boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PublicAcquisitionStatus:
    batch_id: str
    project_id: str
    gig_id: str
    input_sha256: str
    input_ref: dict[str, object]
    processed: int
    next_index: int
    total: int
    deadline_seconds: str
    stop_reason: str
    considered: tuple[dict[str, object], ...]
    duplicates: tuple[dict[str, object], ...]
    failures: tuple[dict[str, object], ...]
    exclusions: tuple[dict[str, object], ...]
    history: tuple[dict[str, object], ...]
    journal_head: str

    @property
    def complete(self) -> bool:
        return self.stop_reason == "completed"

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": "scout-public-import-progress:1",
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "gig_id": self.gig_id,
            "input_sha256": self.input_sha256,
            "input_ref": dict(self.input_ref),
            "processed": self.processed,
            "next_index": self.next_index,
            "total": self.total,
            "deadline_seconds": self.deadline_seconds,
            "stop_reason": self.stop_reason,
            "complete": self.complete,
            "considered": [dict(item) for item in self.considered],
            "duplicates": [dict(item) for item in self.duplicates],
            "failures": [dict(item) for item in self.failures],
            "exclusions": [dict(item) for item in self.exclusions],
            "history": [dict(item) for item in self.history],
            "revisions": [dict(item) for item in self.history],
            "journal_head": self.journal_head,
        }


def _fail(code: str, message: str) -> None:
    raise ScoutAcquisitionError(code, message)


def _unsafe_path(message: str) -> None:
    _fail("acquisition_path_unsafe", message)


def _guard_relative_path(
    root: Path,
    relative: str,
    *,
    final_kind: str,
    allow_missing_final: bool = True,
) -> Path:
    """Walk every component without resolving an untrusted descendant.

    Existing ancestors must be real directories and every existing component,
    including the final component, must not be a symlink. Missing components
    are allowed only so the journal writer can create a new immutable path.
    """
    if root.is_symlink() or not root.is_dir():
        _unsafe_path("authenticated acquisition root is redirected or unavailable")
    relative_path = Path(relative)
    if relative_path.is_absolute() or "\\" in relative or ".." in relative_path.parts or not relative:
        _unsafe_path("acquisition path is absolute, escaped, or malformed")
    current = root
    parts = relative_path.parts
    for ordinal, component in enumerate(parts):
        current = current / component
        is_final = ordinal == len(parts) - 1
        if current.is_symlink():
            _unsafe_path("acquisition path component is a symlink")
        if not current.exists():
            if is_final and allow_missing_final:
                return current
            # Once an ancestor is absent, all descendants are necessarily
            # absent too; this is a valid create path but never a read path.
            return current
        if not is_final and not current.is_dir():
            _unsafe_path("acquisition path ancestor is not a directory")
        if is_final:
            if final_kind == "directory" and not current.is_dir():
                _unsafe_path("acquisition path component is not a directory")
            if final_kind == "file" and not current.is_file():
                _unsafe_path("acquisition path component is not a regular file")
    return current


def _guard_acquisition_tree(root: Path, batch_id: str) -> None:
    """Guard all known acquisition ancestors before any status I/O."""
    _guard_relative_path(root, "records", final_kind="directory")
    _guard_relative_path(root, "records/scout-acquisition", final_kind="directory")
    _guard_relative_path(root, f"records/scout-acquisition/{batch_id}", final_kind="directory")
    _guard_relative_path(root, _input_path(batch_id), final_kind="file")
    _guard_relative_path(root, f"records/scout-acquisition/{batch_id}/progress", final_kind="directory")


def _guard_external_file(path: Path) -> Path:
    """Validate an arbitrary rows path component-by-component.

    ``/tmp`` is a known macOS alias for ``/private/tmp``; accept that system
    alias only when it points exactly at the canonical target. No user-created
    descendant is resolved or trusted.
    """
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = Path(os.path.normpath(os.fspath(candidate)))
    if candidate.parts[:2] == (candidate.anchor, "tmp"):
        tmp = Path(candidate.anchor) / "tmp"
        try:
            canonical_tmp = tmp.resolve(strict=True) if tmp.is_symlink() else None
        except (OSError, RuntimeError):
            canonical_tmp = None
        if canonical_tmp == Path(candidate.anchor) / "private" / "tmp":
            candidate = Path(candidate.anchor) / "private" / "tmp" / Path(*candidate.parts[2:])
    current = Path(candidate.anchor)
    parts = candidate.parts[1:]
    if not parts:
        _fail("acquisition_source_unsafe", "rows source must be one regular local file")
    for ordinal, component in enumerate(parts):
        current = current / component
        if current.is_symlink():
            _fail("acquisition_source_unsafe", "rows source path component is a symlink")
        is_final = ordinal == len(parts) - 1
        if not current.exists():
            _fail("acquisition_source_unsafe", "rows source path is unavailable")
        if not is_final and not current.is_dir():
            _fail("acquisition_source_unsafe", "rows source parent is not a directory")
        if is_final and not current.is_file():
            _fail("acquisition_source_unsafe", "rows source must be one regular local file")
    return current


def read_public_rows_file(path: Path) -> list[dict[str, object]]:
    """Read one public JSON rows file after shared ancestor/final checks."""
    checked = _guard_external_file(path)
    try:
        from ..canonical import parse_json_bytes
        parsed = parse_json_bytes(checked.read_bytes())
    except Exception as exc:
        _fail("acquisition_source_invalid", "rows file must be valid UTF-8 JSON")
        raise AssertionError("unreachable") from exc
    if not isinstance(parsed, list) or any(not isinstance(row, dict) for row in parsed):
        _fail("acquisition_source_invalid", "rows file must contain a JSON array of objects")
    return parsed


def _validate_batch_id(batch_id: str) -> str:
    if type(batch_id) is not str or not _BATCH_ID.fullmatch(batch_id):
        _fail("acquisition_batch_invalid", "batch identity is invalid or unsafe")
    return batch_id


def _validate_text(value: object, field: str, *, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    if type(value) is not str or not value or len(value) > _MAX_TEXT:
        _fail("public_row_invalid", f"{field} must be a bounded text value")


def _validate_source_snapshot(value: object) -> dict[str, object]:
    if value is None:
        return {"status": "supplied"}
    if not isinstance(value, Mapping) or not set(value).issubset(_SOURCE_KEYS):
        _fail("public_source_invalid", "source snapshot contains unsupported fields")
    result = dict(value)
    for key in ("source_kind", "locator", "url", "status", "captured_at", "content_sha256", "media_type"):
        if key in result:
            _validate_text(result[key], f"source_snapshot.{key}")
    if "size_bytes" in result and (type(result["size_bytes"]) is not int or not 0 <= result["size_bytes"] <= 10_000_000):
        _fail("public_source_invalid", "source snapshot size is invalid")
    if "content_sha256" in result and not _DIGEST.fullmatch(str(result["content_sha256"])):
        _fail("public_source_invalid", "source snapshot digest is invalid")
    try:
        canonical_json_bytes(result)
    except (TypeError, ValueError, UnicodeError) as exc:
        _fail("public_source_invalid", "source snapshot is not canonical public data")
        raise AssertionError("unreachable") from exc
    return result


def _validate_rows(rows: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    if not isinstance(rows, (list, tuple)) or len(rows) > _MAX_ROWS:
        _fail("public_rows_invalid", "public import batch exceeds its bound")
    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not set(row).issubset(_ROW_KEYS):
            _fail("public_row_fields_rejected", "public import rows contain unknown or private fields")
        value = dict(row)
        for key in ("opportunity_id", "snapshot_id"):
            _validate_text(value.get(key), key, nullable=False)
        if value.get("source_kind") is not None and value["source_kind"] not in {"agent_discovered", "user_provided"}:
            _fail("public_row_invalid", "source_kind is invalid")
        for key in ("title", "employer", "url", "error", "duplicate_of", "excluded_reason"):
            if key in value:
                _validate_text(value[key], key)
        if "acquisition_state" in value and value["acquisition_state"] not in {"considered", "uncertain", "excluded", "failed", "error"}:
            _fail("public_row_invalid", "acquisition_state is invalid")
        value["source_snapshot"] = _validate_source_snapshot(value.get("source_snapshot"))
        try:
            canonical_json_bytes(value)
        except (TypeError, ValueError, UnicodeError) as exc:
            _fail("public_row_invalid", "public row is not canonical data")
            raise AssertionError("unreachable") from exc
        normalized.append(value)
    return tuple(normalized)


def _input_payload(batch_id: str, resolved: ResolvedWorkpad, rows: tuple[dict[str, object], ...]) -> bytes:
    return canonical_json_bytes({
        "schema_version": "scout-public-import-input:1",
        "batch_id": batch_id,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "rows": list(rows),
    })


def _input_path(batch_id: str) -> str:
    return f"records/scout-acquisition/{batch_id}/input.json"


def _progress_path(batch_id: str, revision_id: str) -> str:
    return f"records/scout-acquisition/{batch_id}/progress/{revision_id}.json"


def _ref(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}


def _deadline(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 300:
        _fail("acquisition_deadline_invalid", "deadline must be greater than zero and at most 300 seconds")
    return float(value)


def _deadline_text(value: float) -> str:
    return format(value, ".12g")


def _new_revision() -> str:
    return f"revision_{uuid.uuid4()}"


def _classify(row: dict[str, object], index: int, identities: set[tuple[str, str]]) -> tuple[str, dict[str, object]]:
    item = {"index": index, **row}
    identity = (str(row["opportunity_id"]), str(row["snapshot_id"]))
    if identity in identities or row.get("duplicate_of") is not None:
        item["outcome"] = "duplicate"
        item["reason"] = "duplicate"
        return "duplicates", item
    identities.add(identity)
    if row.get("error") is not None or row.get("acquisition_state") in {"failed", "error"}:
        item["outcome"] = "failure"
        item["reason"] = "acquisition_failed"
        return "failures", item
    if row.get("excluded_reason") is not None or row.get("acquisition_state") == "excluded":
        item["outcome"] = "exclusion"
        item["reason"] = "acquisition_excluded"
        return "exclusions", item
    item["outcome"] = "considered"
    item["reason"] = "public_row_accepted"
    return "considered", item


def _progress_payload(
    *, batch_id: str, resolved: ResolvedWorkpad, input_sha256: str, input_ref: dict[str, object],
    revision_id: str, parent_revision: str | None, parent_journal_head: str | None,
    processed: int, total: int, deadline_seconds: str, stop_reason: str,
    outcomes: dict[str, list[dict[str, object]]],
) -> bytes:
    return canonical_json_bytes({
        "schema_version": "scout-public-import-progress:1",
        "batch_id": batch_id,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "input_sha256": input_sha256,
        "input_ref": input_ref,
        "progress_revision": revision_id,
        "parent_progress_revision": parent_revision,
        "parent_journal_head": parent_journal_head,
        "processed": processed,
        "next_index": processed,
        "total": total,
        "deadline_seconds": deadline_seconds,
        "stop_reason": stop_reason,
        "considered": outcomes["considered"],
        "duplicates": outcomes["duplicates"],
        "failures": outcomes["failures"],
        "exclusions": outcomes["exclusions"],
    })


def _decode_progress(data: bytes, *, path: str, resolved: ResolvedWorkpad, input_sha256: str) -> dict[str, object]:
    report = validate_serialized_contract("scout-public-import-progress.schema.json", data)
    if not report.valid:
        _fail("acquisition_progress_invalid", "committed acquisition progress does not match its schema")
    try:
        value = parse_json_bytes(data)
    except ValueError as exc:
        _fail("acquisition_progress_invalid", "committed acquisition progress is not valid JSON")
        raise AssertionError("unreachable") from exc
    if not isinstance(value, dict) or value.get("project_id") != resolved.project_id or value.get("gig_id") != resolved.gig_id or value.get("input_sha256") != input_sha256:
        _fail("acquisition_scope_conflict", "acquisition progress scope or input digest differs")
    input_ref = value.get("input_ref")
    if not isinstance(input_ref, dict) or input_ref.get("path") != _input_path(str(value.get("batch_id"))) or input_ref.get("content_sha256") != input_sha256:
        _fail("acquisition_input_conflict", "acquisition progress input reference differs")
    revision = value.get("progress_revision")
    if not isinstance(revision, str) or path != _progress_path(str(value["batch_id"]), revision):
        _fail("acquisition_progress_invalid", "progress revision path does not match its identity")
    return value


def _load_status(root: Path, resolved: ResolvedWorkpad, batch_id: str) -> PublicAcquisitionStatus:
    input_path = _input_path(batch_id)
    _guard_acquisition_tree(root, batch_id)
    try:
        input_data, _ = read_committed_artifact(workpad=root, project_id=resolved.project_id, gig_id=resolved.gig_id, path=input_path)
    except Exception as exc:
        raise ScoutAcquisitionError("acquisition_batch_not_found", "acquisition input batch is not committed") from exc
    input_file = root / input_path
    if input_file.is_symlink() or not input_file.is_file() or input_file.read_bytes() != input_data:
        _fail("acquisition_input_tampered", "working acquisition input differs from committed bytes")
    if not validate_serialized_contract("scout-public-import-input.schema.json", input_data).valid:
        _fail("acquisition_input_invalid", "committed acquisition input does not match its schema")
    try:
        input_value = parse_json_bytes(input_data)
    except ValueError as exc:
        _fail("acquisition_input_invalid", "committed acquisition input is malformed")
        raise AssertionError("unreachable") from exc
    if not isinstance(input_value, dict) or input_value.get("batch_id") != batch_id or input_value.get("project_id") != resolved.project_id or input_value.get("gig_id") != resolved.gig_id:
        _fail("acquisition_scope_conflict", "acquisition input belongs to another scope")
    rows = input_value.get("rows")
    if not isinstance(rows, list):
        _fail("acquisition_input_invalid", "committed acquisition input rows are malformed")
    input_sha256 = digest_imported_bytes(input_data)
    directory = root / "records" / "scout-acquisition" / batch_id / "progress"
    if directory.is_symlink() or not directory.is_dir():
        _fail("acquisition_progress_missing", "acquisition progress is not committed")
    records: list[tuple[dict[str, object], str, str]] = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        relative = path.relative_to(root).as_posix()
        _guard_relative_path(root, relative, final_kind="file", allow_missing_final=False)
        try:
            data, commit = read_committed_artifact(workpad=root, project_id=resolved.project_id, gig_id=resolved.gig_id, path=relative)
        except Exception as exc:
            raise ScoutAcquisitionError("acquisition_progress_unavailable", "acquisition progress publication is unauthenticated") from exc
        if path.read_bytes() != data:
            _fail("acquisition_progress_tampered", "working acquisition progress differs from committed bytes")
        records.append((_decode_progress(data, path=relative, resolved=resolved, input_sha256=input_sha256), commit, relative))
    if not records:
        _fail("acquisition_progress_missing", "acquisition progress is not committed")
    # The parent commit chain is the CAS and ordering proof. There must be one
    # first revision and one tip; an injected/forked progress file is refused.
    commits = {commit for _value, commit, _path in records}
    roots = [value for value, _commit, _path in records if value.get("parent_journal_head") is None]
    if len(roots) != 1:
        _fail("acquisition_progress_conflict", "acquisition progress history has multiple roots")
    for value, _commit, _path in records:
        parent = value.get("parent_journal_head")
        if parent is not None and (not isinstance(parent, str) or parent not in commits):
            _fail("acquisition_progress_conflict", "acquisition progress parent CAS is missing")
        parent_revision = value.get("parent_progress_revision")
        if parent is not None:
            parent_value = next((candidate for candidate, candidate_commit, _candidate_path in records if candidate_commit == parent), None)
            if parent_value is None or parent_revision != parent_value.get("progress_revision"):
                _fail("acquisition_progress_conflict", "acquisition progress parent revision differs")
    child_commits = {value.get("parent_journal_head") for value, _commit, _path in records if value.get("parent_journal_head") is not None}
    tips = [(value, commit, path) for value, commit, path in records if commit not in child_commits]
    if len(tips) != 1:
        _fail("acquisition_progress_conflict", "acquisition progress history is forked")
    latest, latest_commit, _latest_path = tips[0]
    expected_indexes = list(range(int(latest["processed"])))
    seen_indexes: list[int] = []
    outcomes: dict[str, tuple[dict[str, object], ...]] = {}
    for key in ("considered", "duplicates", "failures", "exclusions"):
        entries = latest.get(key)
        if not isinstance(entries, list):
            _fail("acquisition_progress_invalid", "acquisition progress outcomes are malformed")
        outcomes[key] = tuple(dict(item) for item in entries if isinstance(item, dict))
        seen_indexes.extend(int(item["index"]) for item in outcomes[key] if isinstance(item.get("index"), int))
    if sorted(seen_indexes) != expected_indexes or int(latest["next_index"]) != int(latest["processed"]):
        _fail("acquisition_progress_conflict", "acquisition progress indexes are not an exact prefix")
    history = tuple(value for value, _commit, _path in sorted(records, key=lambda item: int(item[0]["processed"])))
    return PublicAcquisitionStatus(
        batch_id=batch_id, project_id=resolved.project_id, gig_id=resolved.gig_id,
        input_sha256=input_sha256, input_ref=_ref(input_path, input_data),
        processed=int(latest["processed"]), next_index=int(latest["next_index"]), total=len(rows), deadline_seconds=str(latest["deadline_seconds"]),
        stop_reason=str(latest["stop_reason"]), considered=outcomes["considered"],
        duplicates=outcomes["duplicates"], failures=outcomes["failures"], exclusions=outcomes["exclusions"],
        history=history, journal_head=latest_commit,
    )


def read_public_acquisition_status(*, resolved: ResolvedWorkpad, batch_id: str) -> PublicAcquisitionStatus:
    """Read one authenticated acquisition batch in a fresh-process-safe way."""
    _validate_batch_id(batch_id)
    if not isinstance(resolved, ResolvedWorkpad):
        _fail("acquisition_scope_invalid", "resolved workpad is invalid")
    return _load_status(resolved.path, resolved, batch_id)


def import_public_rows(
    *, resolved: ResolvedWorkpad, batch_id: str, rows: Sequence[Mapping[str, object]],
    deadline_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic,
) -> PublicAcquisitionStatus:
    """Persist or resume a bounded import of already-acquired public rows."""
    _validate_batch_id(batch_id)
    _deadline(deadline_seconds)
    if not callable(clock):
        _fail("acquisition_clock_invalid", "acquisition clock is invalid")
    normalized = _validate_rows(rows)
    input_data = _input_payload(batch_id, resolved, normalized)
    input_sha256 = digest_imported_bytes(input_data)
    input_path = _input_path(batch_id)

    def operation(writer) -> PublicAcquisitionStatus:
        _guard_acquisition_tree(writer.root, batch_id)
        existing_input = writer.root / input_path
        if existing_input.exists() or existing_input.is_symlink():
            existing = _load_status(writer.root, resolved, batch_id)
            if existing.input_sha256 != input_sha256:
                _fail("acquisition_input_conflict", "batch identity is already bound to different input bytes")
            if existing.complete:
                return existing
            status = existing
            outcomes = {key: [dict(item) for item in getattr(status, key)] for key in ("considered", "duplicates", "failures", "exclusions")}
            start = status.next_index
            parent_revision = str(status.history[-1]["progress_revision"])
            parent_head = status.journal_head
        else:
            status = None
            outcomes = {"considered": [], "duplicates": [], "failures": [], "exclusions": []}
            start = 0
            parent_revision = None
            parent_head = None
        started = float(clock())
        index = start
        identities = {(str(item["opportunity_id"]), str(item["snapshot_id"])) for key in outcomes for item in outcomes[key]}
        while index < len(normalized) and float(clock()) - started < float(deadline_seconds):
            kind, item = _classify(normalized[index], index, identities)
            outcomes[kind].append(item)
            index += 1
        stop_reason = "completed" if index == len(normalized) else "deadline"
        revision_id = _new_revision()
        progress_path = _progress_path(batch_id, revision_id)
        progress_data = _progress_payload(
            batch_id=batch_id, resolved=resolved, input_sha256=input_sha256,
            input_ref=_ref(input_path, input_data), revision_id=revision_id,
            parent_revision=parent_revision, parent_journal_head=parent_head,
            processed=index, total=len(normalized), deadline_seconds=_deadline_text(float(deadline_seconds)), stop_reason=stop_reason, outcomes=outcomes,
        )
        artifacts = [JournalArtifact(progress_path, progress_data)]
        if status is None:
            artifacts.insert(0, JournalArtifact(input_path, input_data))
        writer.record(JournalTransition(
            f"handoff_{uuid.uuid4()}", "scout_public_acquisition_progress",
            "Committed bounded public Scout acquisition progress.", tuple(artifacts),
            {
                "project_id": resolved.project_id, "gig_id": resolved.gig_id,
                "domain": "scout-public-acquisition", "batch_id": batch_id,
                "schema_version": "scout-public-import-progress:1",
                "progress_revision": revision_id, "input_sha256": input_sha256,
                "parent_progress_revision": parent_revision,
                "parent_journal_head": parent_head,
                "artifact_refs": [_ref(item.path, item.content) for item in artifacts],
            },
        ), allow_artifact_replacement=False)
        return _load_status(writer.root, resolved, batch_id)

    try:
        return run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=operation)
    except ScoutAcquisitionError:
        raise
    except (JournalConflictError, OSError, ValueError) as exc:
        raise ScoutAcquisitionError("acquisition_persistence_refused", str(exc)) from exc


def resume_public_acquisition(
    *, resolved: ResolvedWorkpad, batch_id: str, deadline_seconds: float = 30.0,
    rows: Sequence[Mapping[str, object]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> PublicAcquisitionStatus:
    status = read_public_acquisition_status(resolved=resolved, batch_id=batch_id)
    if status.complete:
        return status
    if rows is not None:
        return import_public_rows(resolved=resolved, batch_id=batch_id, rows=rows, deadline_seconds=deadline_seconds, clock=clock)
    _guard_acquisition_tree(resolved.path, batch_id)
    input_data, _ = read_committed_artifact(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=_input_path(batch_id))
    value = parse_json_bytes(input_data)
    assert isinstance(value, dict) and isinstance(value.get("rows"), list)
    return import_public_rows(resolved=resolved, batch_id=batch_id, rows=tuple(value["rows"]), deadline_seconds=deadline_seconds, clock=clock)


# Short aliases make the service convenient for callers while retaining the
# explicit public-acquisition vocabulary in the primary API.
start_public_acquisition = import_public_rows
status_public_acquisition = read_public_acquisition_status
resume_public_import = resume_public_acquisition
read_public_import_status = read_public_acquisition_status
run_public_import = import_public_rows
persist_public_acquisition_progress = import_public_rows


__all__ = [
    "PublicAcquisitionStatus", "ScoutAcquisitionError", "import_public_rows",
    "read_public_acquisition_status", "resume_public_acquisition",
    "start_public_acquisition", "status_public_acquisition",
    "resume_public_import", "read_public_import_status", "run_public_import",
    "persist_public_acquisition_progress",
]
