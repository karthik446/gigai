"""Local, append-only learning-record publication for G20."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
from typing import Callable, Mapping

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
    validate_entity_id,
)
from .validators import validate_serialized_contract


LEARNING_DIRECTORY = "learning"
RECORD_DIRECTORY = "records"
JOURNAL_FILENAME = "journal.jsonl"


class LearningError(RuntimeError):
    """A learning record cannot be published or reconciled safely."""

    code = "learning_error"


class LearningRefusedError(LearningError):
    code = "learning_refused"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class LearningResult:
    record: dict[str, object]
    path: Path
    journal_digest: str


@dataclass(frozen=True)
class ReconciliationResult:
    discarded: tuple[str, ...]
    retained: int


LearningObserver = Callable[[str], None]


def learning_root(home_root: Path) -> Path:
    """Return the derived local learning root without accepting an override."""

    home = Path(home_root).expanduser().resolve(strict=False)
    root = home / LEARNING_DIRECTORY
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise LearningRefusedError("learning root is not a real directory", code="invalid_root")
    root.mkdir(parents=True, exist_ok=True)
    records = root / RECORD_DIRECTORY
    if records.is_symlink() or (records.exists() and not records.is_dir()):
        raise LearningRefusedError("learning records directory is unavailable", code="invalid_root")
    records.mkdir(mode=0o700, exist_ok=True)
    return root


def validate_learning_record(record: Mapping[str, object] | bytes) -> dict[str, object]:
    payload = record if isinstance(record, bytes) else canonical_json_bytes(record)
    report = validate_serialized_contract("learning-record.schema.json", payload)
    if not report.valid:
        codes = ", ".join(item.code for item in report.findings)
        raise LearningRefusedError(f"learning record failed validation: {codes}", code="invalid_record")
    parsed = parse_json_bytes(payload)
    if not isinstance(parsed, dict):
        raise LearningRefusedError("learning record must be an object", code="invalid_record")
    validate_entity_id(parsed["learning_id"], expected_prefix=EntityPrefix.LEARNING)
    return parsed


def publish_learning_record(
    *,
    home_root: Path,
    record: Mapping[str, object] | bytes,
    source_root: Path,
    active_pointer_path: Path,
    observer: LearningObserver | None = None,
    failpoint: str | None = None,
) -> LearningResult:
    """Atomically publish one validated record and its append-only journal entry."""

    observer = observer or (lambda _step: None)
    root = learning_root(home_root)
    reconciliation = reconcile_learning_root(home_root=root.parent)
    if reconciliation.discarded:
        observer("reconciled")
    parsed = validate_learning_record(record)
    _verify_source_identity(parsed, source_root)
    _verify_active_pointer(parsed, active_pointer_path)
    learning_id = parsed["learning_id"]
    assert isinstance(learning_id, str)
    records_dir = root / RECORD_DIRECTORY
    destination = records_dir / f"{learning_id}.json"
    if destination.exists() or destination.is_symlink():
        raise LearningRefusedError("learning identity already exists", code="duplicate_learning_id")

    source_key = _source_key(parsed)
    for existing in _read_published_records(root):
        if _source_key(existing) == source_key:
            raise LearningRefusedError("source artifact was already observed", code="duplicate_observation")

    payload = canonical_json_bytes(parsed)
    observer("before_temporary_write")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{learning_id}.", suffix=".tmp", dir=records_dir
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        observer("after_temporary_write")
        if failpoint == "after_temporary_write":
            raise LearningError("injected interruption after temporary write")
        os.replace(temporary, destination)
        observer("after_atomic_rename")
        if failpoint == "after_atomic_rename":
            raise LearningError("injected interruption after atomic rename")
        journal_entry = {
            "learning_id": learning_id,
            "record_sha256": digest_imported_bytes(payload),
            "artifact": f"{RECORD_DIRECTORY}/{learning_id}.json",
        }
        observer("before_journal_publication")
        if failpoint == "before_journal_publication":
            raise LearningError("injected interruption before journal publication")
        _append_journal(root, journal_entry)
        observer("after_journal_publication")
        journal_digest = digest_imported_bytes(canonical_json_bytes(journal_entry))
        return LearningResult(parsed, destination, journal_digest)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_learning_root(home_root: Path) -> ReconciliationResult:
    """Discard orphaned/partial records and repair only the local journal."""

    root = learning_root(home_root)
    records_dir = root / RECORD_DIRECTORY
    discarded: list[str] = []
    for temporary in records_dir.glob(".*.tmp"):
        if temporary.is_symlink() or temporary.is_file():
            temporary.unlink(missing_ok=True)
            discarded.append(temporary.name)

    valid: dict[str, tuple[dict[str, object], str]] = {}
    for path in sorted(records_dir.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            discarded.append(path.name)
            continue
        try:
            record = validate_learning_record(path.read_bytes())
        except (OSError, LearningError):
            path.unlink(missing_ok=True)
            discarded.append(path.name)
            continue
        valid[record["learning_id"]] = (record, digest_imported_bytes(path.read_bytes()))

    existing_entries = _read_journal(root)
    retained_entries: list[dict[str, object]] = []
    seen_sources: set[tuple[object, ...]] = set()
    seen_ids: set[str] = set()
    for entry in existing_entries:
        learning_id = entry.get("learning_id")
        if not isinstance(learning_id, str) or learning_id not in valid or learning_id in seen_ids:
            discarded.append(str(learning_id))
            continue
        record, record_digest = valid[learning_id]
        if entry.get("record_sha256") != record_digest:
            discarded.append(learning_id)
            continue
        source_key = _source_key(record)
        if source_key in seen_sources:
            discarded.append(learning_id)
            (records_dir / f"{learning_id}.json").unlink(missing_ok=True)
            continue
        seen_ids.add(learning_id)
        seen_sources.add(source_key)
        retained_entries.append(entry)

    for learning_id in set(valid) - seen_ids:
        (records_dir / f"{learning_id}.json").unlink(missing_ok=True)
        discarded.append(learning_id)
    if retained_entries != existing_entries:
        _write_journal(root, retained_entries)
    return ReconciliationResult(tuple(discarded), len(retained_entries))


def load_learning_records(
    *, home_root: Path, learning_ids: list[str]
) -> dict[str, bytes]:
    """Load exactly the cited local records, refusing missing or redirected files."""

    root = learning_root(home_root)
    records: dict[str, bytes] = {}
    for learning_id in learning_ids:
        validate_entity_id(learning_id, expected_prefix=EntityPrefix.LEARNING)
        path = root / RECORD_DIRECTORY / f"{learning_id}.json"
        if path.is_symlink() or not path.is_file():
            raise LearningRefusedError("cited learning record is missing", code="missing_record")
        payload = path.read_bytes()
        validate_learning_record(payload)
        records[learning_id] = payload
    return records


def _source_key(record: Mapping[str, object]) -> tuple[object, ...]:
    subject = record["subject"]
    assert isinstance(subject, Mapping)
    source = record["source"]
    assert isinstance(source, Mapping)
    artifact = source["artifact"]
    assert isinstance(artifact, Mapping)
    return (
        record["gig_id"],
        record["active_version"],
        subject.get("kind"),
        subject.get("run_id", subject.get("goal_id")),
        source["kind"],
        source["source_id"],
        artifact["path"],
        artifact["content_sha256"],
    )


def _verify_source_identity(record: Mapping[str, object], source_root: Path) -> None:
    root = Path(source_root).expanduser().resolve(strict=False)
    source = record["source"]
    assert isinstance(source, Mapping)
    artifact = source["artifact"]
    assert isinstance(artifact, Mapping)
    relative = artifact["path"]
    assert isinstance(relative, str)
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise LearningRefusedError("source artifact is unavailable", code="source_missing")
    try:
        path.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise LearningRefusedError("source artifact escapes its root", code="source_escape") from exc
    payload = path.read_bytes()
    if digest_imported_bytes(payload) != artifact["content_sha256"] or len(payload) != artifact["size_bytes"]:
        raise LearningRefusedError("source artifact bytes changed", code="source_digest_mismatch")


def _verify_active_pointer(record: Mapping[str, object], pointer_path: Path) -> None:
    path = Path(pointer_path).expanduser()
    if path.is_symlink() or not path.is_file():
        raise LearningRefusedError("active-version pointer is unavailable", code="pointer_missing")
    payload = path.read_bytes()
    try:
        pointer = parse_json_bytes(payload)
    except ValueError as exc:
        raise LearningRefusedError("active-version pointer is malformed", code="pointer_invalid") from exc
    if not isinstance(pointer, Mapping):
        raise LearningRefusedError("active-version pointer is not an object", code="pointer_invalid")
    if pointer.get("gig_id") != record["gig_id"] or pointer.get("active_version") != record["active_version"]:
        raise LearningRefusedError("active-version pointer does not match observation", code="pointer_mismatch")
    if digest_imported_bytes(payload) != record["active_pointer_sha256"]:
        raise LearningRefusedError("active-version pointer bytes changed", code="pointer_digest_mismatch")


def _read_published_records(root: Path) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for path in sorted((root / RECORD_DIRECTORY).glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            result.append(validate_learning_record(path.read_bytes()))
        except (OSError, LearningError):
            continue
    return tuple(result)


def _read_journal(root: Path) -> list[dict[str, object]]:
    path = root / JOURNAL_FILENAME
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise LearningRefusedError("learning journal is unavailable", code="invalid_journal")
    entries: list[dict[str, object]] = []
    for line in path.read_bytes().splitlines():
        try:
            parsed = parse_json_bytes(line)
        except ValueError as exc:
            raise LearningRefusedError("learning journal contains malformed JSON", code="invalid_journal") from exc
        if not isinstance(parsed, dict):
            raise LearningRefusedError("learning journal entry is not an object", code="invalid_journal")
        entries.append(parsed)
    return entries


def _append_journal(root: Path, entry: Mapping[str, object]) -> None:
    entries = _read_journal(root)
    entries.append(dict(entry))
    _write_journal(root, entries)


def _write_journal(root: Path, entries: list[Mapping[str, object]]) -> None:
    payload = b"".join(canonical_json_bytes(entry) + b"\n" for entry in entries)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".journal.", suffix=".tmp", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / JOURNAL_FILENAME)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "JOURNAL_FILENAME",
    "LEARNING_DIRECTORY",
    "LearningError",
    "LearningRefusedError",
    "LearningResult",
    "ReconciliationResult",
    "learning_root",
    "load_learning_records",
    "publish_learning_record",
    "reconcile_learning_root",
    "validate_learning_record",
]
