"""SCOPE-ADD-3 C1: a re-rank is a durable, small run record of the ``rank`` kind.

It replaces ``api/rank.py``'s in-memory daemon-thread ``_PASSES`` (a server
restart lost the pass, and nothing on disk said a ranking happened, with
which prompt, model or cost). A re-rank never mutates the sealed run it
ranks; it is a record of its own, next to the runs:

``runs/rank_<uuid>/`` (the ``rank_`` prefix keeps it out of every
``runs/run_*`` enumeration: ``run.py``, ``runs_list``):

* ``run-details.json`` (journal-committed; replaced at each transition, the
  one mutable path the journal allows under ``runs/``): ``kind: "rank"``,
  the parent run, the single-flight ``key`` and its digest, the input (the
  parent's sealed ``acquire.json`` digest, how many postings, the model
  target), ``status`` (``running`` -> ``complete``/``partial``/
  ``cancelled``/``skipped``/``failed``), times, totals.
* ``outputs/rank.json`` (journal-committed once, at the end): the pass's
  ``RankResult.to_json()`` plus the record's identity.
* ``progress/`` (run-local, never journaled): ``rank.jsonl`` batch lines
  (the same ``ProgressWriter`` lines a run writes), ``rank.json`` status,
  ``owner.json`` (the pid + process token running the pass) and, to cancel
  it from any process, ``rank-cancel``.

Single flight, keyed by ``(parent run, profile, resume revision, prefs
digest, prompt version)``: a start while a pass for the same key runs --
in this process or another live one -- joins it. Durable across a restart:
a record left ``running`` by a process that is gone is ``interrupted``, and
the next start RESUMES it in place; the batches it completed are served
from ``model_rank``'s score cache and never paid for again. A finished
record is never reopened (its ``outputs/rank.json`` has one publisher): a
``complete`` one answers every later start; after a ``partial``,
``cancelled``, ``skipped`` or ``failed`` pass a start makes a new record
(cache-first, so only the rows without a score cost a call).

The reads use the newest finished record with a score for the run
(:func:`newest_finished`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any
import uuid

from ...canonical import canonical_json_digest
from .model_rank import PROMPT_VERSION, RankResult, prefs_digest
from .progress import ProgressWriter, progress_dir, read_rank
from .rank_digest import DIGEST_VERSION, CandidatePrefs

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from .contracts import PostingRow

RECORD_PREFIX = "rank_"
RECORD_SCHEMA = "scout-rank-run:1"
FINISHED = frozenset({"complete", "partial", "cancelled", "skipped", "failed"})
_OWNER_FILENAME = "owner.json"
# One token per process: a record this process owns but no longer runs
# (its thread died) is told apart from one another live process runs.
_PROCESS_TOKEN = uuid.uuid4().hex

_logger = logging.getLogger("gigai.scout.server")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RankKey:
    """The single-flight identity of a re-rank (REPORT Q6)."""

    parent_run_id: str
    profile_id: str
    resume_revision_id: str
    prefs_digest: str
    prompt_version: str = PROMPT_VERSION

    def to_json(self) -> dict[str, object]:
        return {
            "parent_run_id": self.parent_run_id,
            "profile_id": self.profile_id,
            "resume_revision_id": self.resume_revision_id,
            "prefs_digest": self.prefs_digest,
            "prompt_version": self.prompt_version,
        }

    def digest(self) -> str:
        return canonical_json_digest(self.to_json())


@dataclass(frozen=True)
class RankRecord:
    """One ``runs/rank_*`` record as read from disk."""

    record_id: str
    root: Path
    details: dict[str, object]

    @property
    def status(self) -> str:
        return str(self.details.get("status") or "")

    @property
    def key_digest(self) -> str | None:
        value = self.details.get("key_digest")
        return value if isinstance(value, str) else None

    @property
    def parent_run_id(self) -> str | None:
        value = self.details.get("parent_run_id")
        return value if isinstance(value, str) else None

    @property
    def started_at(self) -> str:
        return str(self.details.get("started_at") or "")

    def owner_alive(self) -> bool:
        """True while a live process runs this record's pass."""

        owner = _read_json(progress_dir(self.root) / _OWNER_FILENAME)
        if not isinstance(owner, dict):
            return False
        pid = owner.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        if pid == os.getpid():
            if owner.get("token") != _PROCESS_TOKEN:
                return False  # a pid reused by this process after a restart
            with _LOCK:
                current = _REGISTRY.get(self.record_id)
            return current is not None and not current.done.is_set()
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def live_status(self) -> str:
        """``status``, but ``interrupted`` for a ``running`` record no live process runs."""

        if self.status == "running" and not self.owner_alive():
            return "interrupted"
        return self.status

    def result(self) -> RankResult | None:
        """The sealed ``outputs/rank.json``, or ``None`` (not finished, or unreadable)."""

        value = _read_json(self.root / "outputs" / "rank.json")
        if not isinstance(value, dict):
            return None
        try:
            return RankResult.from_json(value)
        except (KeyError, TypeError, ValueError):
            return None

    def cancel_path(self) -> Path:
        from .rank_run import CANCEL_FILENAME

        return progress_dir(self.root) / CANCEL_FILENAME


def _read_json(path: Path) -> object | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_records(workpad: Path, *, parent_run_id: str | None = None) -> list[RankRecord]:
    """Every ``runs/rank_*`` record (of ``parent_run_id`` when given), newest first."""

    runs = workpad / "runs"
    if not runs.is_dir():
        return []
    records = []
    for root in runs.glob(f"{RECORD_PREFIX}*"):
        if root.is_symlink() or not root.is_dir():
            continue
        details = _read_json(root / "run-details.json")
        if not isinstance(details, dict) or details.get("kind") != "rank":
            continue
        record = RankRecord(root.name, root, details)
        if parent_run_id is None or record.parent_run_id == parent_run_id:
            records.append(record)
    records.sort(key=lambda record: (record.started_at, record.record_id), reverse=True)
    return records


def newest_finished(
    workpad: Path,
    parent_run_id: str,
    *,
    profile_id: str | None = None,
    resume_revision_id: str | None = None,
) -> tuple[RankRecord, RankResult] | None:
    """The newest finished re-rank of ``parent_run_id`` that scored anything (for that profile/resume when given)."""

    for record in list_records(workpad, parent_run_id=parent_run_id):
        if record.status not in FINISHED:
            continue
        key = record.details.get("key")
        if isinstance(key, dict):
            if profile_id is not None and key.get("profile_id") != profile_id:
                continue
            if resume_revision_id is not None and key.get("resume_revision_id") != resume_revision_id:
                continue
        result = record.result()
        if result is not None and any(item.scored for item in result.postings):
            return record, result
    return None


# --- the passes this process runs --------------------------------------------------------


@dataclass
class _Pass:
    record_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


_REGISTRY: dict[str, _Pass] = {}
# Re-entrant: ``start_or_join`` holds it while ``owner_alive`` reads the registry.
_LOCK = threading.RLock()


@dataclass(frozen=True)
class RankInput:
    """What one re-rank needs: the parent run's rows and identity, the resume, the prefs, the model."""

    workpad_resolved: Any
    parent_run_id: str
    rows: tuple["PostingRow", ...]
    acquire_output_digest: str
    profile_id: str
    resume_revision_id: str
    resume_text: str
    prefs: CandidatePrefs
    model_target: str
    home_root: Path

    def key(self) -> RankKey:
        return RankKey(self.parent_run_id, self.profile_id, self.resume_revision_id, prefs_digest(self.prefs))


def _details(record_id: str, source: RankInput, *, status: str, started_at: str, **extra: object) -> dict[str, object]:
    key = source.key()
    value: dict[str, object] = {
        "schema_version": RECORD_SCHEMA,
        "kind": "rank",
        "run_id": record_id,
        "parent_run_id": source.parent_run_id,
        "key": key.to_json(),
        "key_digest": key.digest(),
        "input": {
            "acquire_output_digest": source.acquire_output_digest,
            "postings": len(source.rows),
            "model_target": source.model_target,
            "digest_version": DIGEST_VERSION,
            "prompt_version": PROMPT_VERSION,
        },
        "status": status,
        "started_at": started_at,
        "finished_at": None,
        "fail_open_reason": None,
        "totals": None,
        "rank_ref": None,
    }
    value.update(extra)
    return value


def _commit_details(resolved: Any, record_id: str, details: dict[str, object], *, operation: str) -> None:
    from ...journal import JournalArtifact, record_transition
    from .rank_run import RANK_JOURNAL_DOMAIN, RANK_JOURNAL_TRANSITION, json_bytes

    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=f"handoff_{uuid.uuid4()}",
        transition=RANK_JOURNAL_TRANSITION,
        body=f"Rank record {record_id}: {details.get('status')}.",
        artifacts=(JournalArtifact(f"runs/{record_id}/run-details.json", json_bytes(details)),),
        front_matter={
            "project_id": resolved.project_id,
            "gig_id": resolved.gig_id,
            "run_id": record_id,
            "domain": RANK_JOURNAL_DOMAIN,
            "operation": operation,
            "parent_run_id": details.get("parent_run_id"),
            "status": details.get("status"),
        },
    )


def _claim(record: RankRecord) -> None:
    """Write ``owner.json`` for this process; clear a stale cancel request."""

    directory = progress_dir(record.root)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"{_OWNER_FILENAME}.tmp{os.getpid()}"
    tmp.write_text(json.dumps({"pid": os.getpid(), "token": _PROCESS_TOKEN, "claimed_at": _now()}), encoding="utf-8")
    os.replace(tmp, directory / _OWNER_FILENAME)
    try:
        record.cancel_path().unlink()
    except FileNotFoundError:
        pass


def _work(source: RankInput, record: RankRecord, current: _Pass) -> None:
    """The pass itself, on its own thread: stream, seal ``outputs/rank.json``, commit the final details."""

    from . import rank_run

    resolved = source.workpad_resolved
    progress = ProgressWriter(record.root)
    details = dict(record.details)
    try:
        streamer = rank_run.RankStreamer(
            progress, total=len(source.rows), cancel=current.cancel, cancel_path=record.cancel_path()
        )
        # ``source.rows`` come in the parent run's own rank order (the caller
        # orders them), so a pass cut short by its cap scores those first.
        result = rank_run.run_pass(
            source.rows,
            resume_text=source.resume_text,
            prefs=source.prefs,
            model_target=source.model_target,
            home_root=source.home_root,
            streamer=streamer,
            cancel=current.cancel,
            record_id=record.record_id,
            parent_run_id=source.parent_run_id,
        )
        progress.rank_status(rank_run.status_json(result, total=len(source.rows)))
        sealed = rank_run.seal_rank_json(
            resolved,
            f"runs/{record.record_id}",
            rank_run.rank_json_bytes(
                result, run_id=record.record_id, kind="rank", parent_run_id=source.parent_run_id,
                key=source.key().to_json(),
            ),
            front_matter={"run_id": record.record_id, "schema_version": "scout-rank:1"},
        )
        details.update({
            "status": result.status,
            "finished_at": _now(),
            "fail_open_reason": result.fail_open_reason,
            "totals": result.totals(),
            "rank_ref": f"runs/{record.record_id}/outputs/rank.json" if sealed else None,
        })
    except Exception as exc:  # noqa: BLE001 - a re-rank never takes the server down; the type is recorded
        _logger.warning("rank (%s): the pass failed: %s", record.record_id, type(exc).__name__)
        details.update({"status": "failed", "finished_at": _now(), "fail_open_reason": f"error:{type(exc).__name__}"})
    try:
        _commit_details(resolved, record.record_id, details, operation="scout_rank_finished")
    except Exception as exc:  # noqa: BLE001 - the record stays "running", read as interrupted; the type is logged
        _logger.warning("rank (%s): the final details could not be committed: %s", record.record_id, type(exc).__name__)


def _spawn(source: RankInput, record: RankRecord) -> _Pass:
    current = _Pass(record.record_id)
    _REGISTRY[record.record_id] = current
    _claim(record)

    def run(this: _Pass = current) -> None:
        try:
            _work(source, record, this)
        finally:
            this.done.set()

    current.thread = threading.Thread(target=run, name=f"gigai-rank-{record.record_id}", daemon=True)
    current.thread.start()
    return current


@dataclass(frozen=True)
class StartOutcome:
    """What a start/join/read answered: the record (``None``: never ranked) and what happened."""

    record: RankRecord | None
    action: str  # "started" | "resumed" | "joined" | "done" | "none"


def start_or_join(source: RankInput, *, start: bool) -> StartOutcome:
    """Single flight for ``source.key()``; only ``start=True`` can start or resume a pass."""

    key_digest = source.key().digest()
    workpad = source.workpad_resolved.path
    with _LOCK:
        matching = [
            record for record in list_records(workpad, parent_run_id=source.parent_run_id)
            if record.key_digest == key_digest
        ]
        newest = matching[0] if matching else None
        if newest is not None and newest.status == "running":
            if newest.owner_alive():
                return StartOutcome(newest, "joined")
            if not start:
                return StartOutcome(newest, "none")
            _spawn(source, newest)  # interrupted: resume in place, cache-first
            return StartOutcome(newest, "resumed")
        if newest is not None and newest.status == "complete":
            return StartOutcome(newest, "done")
        if not start:
            return StartOutcome(newest, "none")
        record_id = f"{RECORD_PREFIX}{uuid.uuid4()}"
        details = _details(record_id, source, status="running", started_at=_now())
        root = workpad / "runs" / record_id
        _commit_details(source.workpad_resolved, record_id, details, operation="scout_rank_started")
        record = RankRecord(record_id, root, details)
        _spawn(source, record)
        return StartOutcome(record, "started")


def cancel(workpad: Path, record_id: str) -> bool:
    """Ask the pass running ``record_id`` to stop (any process): no new model call starts."""

    record = next((item for item in list_records(workpad) if item.record_id == record_id), None)
    if record is None or record.status != "running":
        return False
    with _LOCK:
        current = _REGISTRY.get(record_id)
    if current is not None:
        current.cancel.set()
    path = record.cancel_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_now(), encoding="utf-8")
    return True


def wait_for_passes(*, timeout: float | None = None) -> bool:
    """Wait until no pass runs in this process; false when ``timeout`` ran out first."""

    with _LOCK:
        running = [item for item in _REGISTRY.values() if not item.done.is_set()]
    return all(item.done.wait(timeout) for item in running)


def live_scores(record: RankRecord) -> dict[str, dict[str, object]]:
    """The scores a record's ``progress/rank.jsonl`` holds so far, by ``normalized_url``."""

    folded = read_rank(progress_dir(record.root))
    found = folded.get("scores") if folded is not None else None
    return dict(found) if isinstance(found, dict) else {}


def record_summary(record: RankRecord | None) -> dict[str, object] | None:
    """A record for a response: identity, status (``interrupted`` when its owner is gone), live counts."""

    if record is None:
        return None
    folded = read_rank(progress_dir(record.root)) or {}
    return {
        "record_id": record.record_id,
        "kind": "rank",
        "parent_run_id": record.parent_run_id,
        "status": record.live_status(),
        "started_at": record.details.get("started_at"),
        "finished_at": record.details.get("finished_at"),
        "fail_open_reason": record.details.get("fail_open_reason"),
        "ranked": folded.get("ranked", 0),
        "scored": folded.get("scored", 0),
        "total": folded.get("total") if folded.get("total") is not None else (record.details.get("input") or {}).get("postings"),  # type: ignore[union-attr]
        "text": folded.get("text"),
    }


__all__ = [
    "FINISHED",
    "RECORD_PREFIX",
    "RECORD_SCHEMA",
    "RankInput",
    "RankKey",
    "RankRecord",
    "StartOutcome",
    "cancel",
    "list_records",
    "live_scores",
    "newest_finished",
    "record_summary",
    "start_or_join",
    "wait_for_passes",
]
