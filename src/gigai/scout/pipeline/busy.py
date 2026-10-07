"""0.1.10.7 PL5: an assess batch that is live, so the pipeline's runner yields to it.

``gigai scout new`` on a yes (and ``POST /api/new`` with ``assess: true``)
assesses its new postings a few at a time through the job page's path. That
batch already uses the machine's model slots and the user asked for it, so
the runner claims nothing while it runs (DESIGN 7), the same as for an
"assess all" batch or a find-jobs run. An "assess all" batch is seen through
its own record (``assess_all``); this batch has none, so it leaves a marker:

    <home>/scout/<project_id>/pipeline/live/batch_<hex>.json
    {"kind": "assess_batch", "pid": 123, "token": "<process token>", "started_at": "..."}

Ids and numbers only. The marker is removed when the batch ends. One left by
a process that is gone (its pid is not alive, or it is this pid under another
process token) is not waited for, and neither is one that was not touched for
:data:`BATCH_QUIET_SECONDS` (the batch touches it after every posting).

0.1.11.5 (ASSESS-01): the marker also says HOW FAR the batch is, and a batch
can be CANCELLED through it, from this process or another:

    {..., "total": 50, "assessed": 12, "failed": 1, "in_flight": 4, "profile_id": "<the profile of the call started
     last>", "estimate_seconds": 1730.0, "pending": ["<job identity>", ...]}

``pending`` is the postings of the batch that have no result yet (posting
URLs: public). :func:`request_cancel` leaves ``batch_<hex>.cancel`` beside the
marker; the batch reads it before each posting (:meth:`LiveBatch.cancelled`)
and starts no further model call. The calls in flight finish and their
results are kept. :func:`batch_status` is the read the Jobs page polls: the
markers of one small folder, never a store read.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import threading
import time
import uuid

from .store import _pid_alive, pipeline_path, process_token

KIND_ASSESS_BATCH = "assess_batch"
STATUS_RUNNING = "running"
STATUS_CANCELLING = "cancelling"
LIVE_DIR = "live"
#: A marker not touched for this long is not waited for: its batch hangs or its process is gone.
BATCH_QUIET_SECONDS = 15 * 60.0


def live_dir(home_root: Path, target: Path) -> Path:
    return pipeline_path(Path(home_root), Path(target)).parent / LIVE_DIR


class LiveBatch:
    """One live batch's marker. ``beat`` after each posting keeps it fresh.

    0.1.11.5: ``begin`` says what the batch holds, ``started`` / ``finished`` move its counts (from the batch's own
    threads), and ``cancelled`` is whether someone asked for it to stop. With no marker (it could not be written)
    the counts are kept in memory only and a cancel from another process is not seen.
    """

    def __init__(self, path: Path | None, marker: dict[str, object] | None = None) -> None:
        self.path = path
        self._marker: dict[str, object] = dict(marker or {})
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._pending: list[str] = []
        self.total = 0
        self.assessed = 0
        self.failed = 0
        self.in_flight = 0

    @property
    def batch_id(self) -> str | None:
        return None if self.path is None else self.path.stem

    def beat(self) -> None:
        if self.path is None:
            return
        try:
            os.utime(self.path)
        except OSError:
            pass  # the marker is gone or cannot be touched: the batch goes on, the runner stops waiting later

    def _write(self) -> None:
        if self.path is None:
            return
        body = {
            **self._marker, "total": self.total, "assessed": self.assessed, "failed": self.failed, "in_flight": self.in_flight,
            "pending": list(self._pending),
        }
        try:
            scratch = self.path.with_suffix(".tmp")
            scratch.write_text(json.dumps(body), encoding="utf-8")
            os.replace(scratch, self.path)
        except OSError:
            pass  # as for ``beat``: the counts are a courtesy to the page, never a reason to fail the batch

    def begin(self, jobs: Sequence[str], *, estimate_seconds: float | None = None) -> None:
        """What the batch is about to assess. Called once, before the first posting."""

        with self._lock:
            self.total, self._pending = len(jobs), list(dict.fromkeys(jobs))
            if isinstance(estimate_seconds, (int, float)) and estimate_seconds > 0:
                self._marker["estimate_seconds"] = float(estimate_seconds)
            self._write()

    def started(self, profile_id: str | None) -> None:
        with self._lock:
            self.in_flight += 1
            if profile_id:
                self._marker["profile_id"] = profile_id
            self._write()

    def finished(self, job: str, *, assessed: bool, counted: bool = True, was_started: bool = True) -> None:
        """One posting has its result. ``counted`` false: it was never started (a cancelled batch), neither assessed nor failed."""

        with self._lock:
            if was_started:
                self.in_flight = max(0, self.in_flight - 1)
            if counted:
                if assessed:
                    self.assessed += 1
                else:
                    self.failed += 1
            if job in self._pending:
                self._pending.remove(job)
            self._write()

    def view(self) -> dict[str, object]:
        """The shape of :func:`batch_status`, from this object's own counts: for the process that runs the batch,
        when its marker is gone already (the batch is ending) or was never written."""

        with self._lock:
            marker = {
                **self._marker, "total": self.total, "assessed": self.assessed, "failed": self.failed, "in_flight": self.in_flight,
                "pending": list(self._pending),
            }
        return _batch_view(self.batch_id, marker, cancelling=self._cancel.is_set(), here=True)

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        """Whether a cancel was asked for: here (:meth:`cancel`) or by anyone through the marker's ``.cancel`` file."""

        if self._cancel.is_set():
            return True
        if self.path is not None and _cancel_path(self.path).exists():
            self._cancel.set()
            return True
        return False


def _cancel_path(marker: Path) -> Path:
    return marker.with_suffix(".cancel")


@contextmanager
def assess_batch(home_root: Path, target: Path) -> Iterator[LiveBatch]:
    """Mark an assess batch live for as long as the block runs. Never fails the batch.

    A marker that cannot be written (no bound project, a read-only home) is
    skipped: the batch runs, and the runner does not yield to it.
    """

    path: Path | None = None
    try:
        directory = live_dir(home_root, target)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"batch_{uuid.uuid4().hex}.json"
        started = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        marker = {"kind": KIND_ASSESS_BATCH, "pid": os.getpid(), "token": process_token(), "started_at": started}
        path.write_text(json.dumps(marker), encoding="utf-8")
    except Exception:  # noqa: BLE001 - the marker is a courtesy to the runner; a batch never fails because it could not be written
        path = None
    try:
        yield LiveBatch(path, marker if path is not None else None)
    finally:
        if path is not None:
            for left in (path, _cancel_path(path), path.with_suffix(".tmp")):
                try:
                    left.unlink(missing_ok=True)
                except OSError:
                    pass  # left behind: read as not live once its process is gone or it goes quiet


def _is_live(path: Path, now: float) -> bool:
    try:
        if now - path.stat().st_mtime > BATCH_QUIET_SECONDS:
            return False
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(marker, dict):
        return False
    pid, token = marker.get("pid"), marker.get("token")
    if type(pid) is not int or type(token) is not str:
        return False
    if pid == os.getpid():
        return token == process_token()  # this pid, an earlier process: gone
    return _pid_alive(pid)


def _live_markers(home_root: Path, target: Path) -> list[tuple[Path, dict[str, object]]]:
    try:
        directory = live_dir(home_root, target)
    except Exception:  # noqa: BLE001 - no bound project: there is no batch to wait for
        return []
    if not directory.is_dir():
        return []
    now = time.time()
    found: list[tuple[Path, dict[str, object]]] = []
    for path in sorted(directory.glob("batch_*.json")):
        if not _is_live(path, now):
            continue
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(marker, dict):
            found.append((path, marker))
    return found


def assess_batch_live(home_root: Path, target: Path) -> bool:
    """Whether an assess batch of this project is running now, in this process or another. Reads only."""

    return bool(_live_markers(home_root, target))


def _whole(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def batch_status(home_root: Path, target: Path) -> dict[str, object] | None:
    """0.1.11.5: how far the live assess batch of this project is, from its marker alone; ``None`` when none runs.

    ``{"id", "status": "running" | "cancelling", "total", "assessed", "failed", "in_flight", "profile_id",
    "estimate_seconds", "started_at", "pending": [job identities], "here": bool}``. ``here``: the batch runs in this
    process. The oldest live batch when there are several (one at a time is the rule; ``scout new`` has no lease).
    """

    markers = _live_markers(home_root, target)
    if not markers:
        return None
    path, marker = min(markers, key=lambda item: str(item[1].get("started_at") or ""))
    return _batch_view(path.stem, marker, cancelling=_cancel_path(path).exists(), here=marker.get("pid") == os.getpid())


def _batch_view(batch_id: str | None, marker: Mapping[str, object], *, cancelling: bool, here: bool) -> dict[str, object]:
    estimate = marker.get("estimate_seconds")
    pending = marker.get("pending")
    return {
        "id": batch_id,
        "status": STATUS_CANCELLING if cancelling else STATUS_RUNNING,
        "total": _whole(marker.get("total")), "assessed": _whole(marker.get("assessed")), "failed": _whole(marker.get("failed")),
        "in_flight": _whole(marker.get("in_flight")),
        "profile_id": marker.get("profile_id") if isinstance(marker.get("profile_id"), str) else None,
        "estimate_seconds": float(estimate) if isinstance(estimate, (int, float)) and not isinstance(estimate, bool) else None,
        "started_at": marker.get("started_at") if isinstance(marker.get("started_at"), str) else None,
        "pending": [job for job in pending if isinstance(job, str)] if isinstance(pending, list) else [],
        "here": here,
    }


def request_cancel(home_root: Path, target: Path) -> int:
    """0.1.11.5: ask every live assess batch of this project to stop; how many were asked.

    The batch starts no further model call; the calls in flight finish and every finished result is kept. A batch
    of another process (``gigai scout jobs assess --yes`` in a terminal) is asked the same way.
    """

    asked = 0
    for path, _marker in _live_markers(home_root, target):
        try:
            _cancel_path(path).write_text("", encoding="utf-8")
        except OSError:
            continue
        asked += 1
    return asked


__all__ = [
    "BATCH_QUIET_SECONDS", "KIND_ASSESS_BATCH", "LiveBatch", "STATUS_CANCELLING", "STATUS_RUNNING", "assess_batch",
    "assess_batch_live", "batch_status", "live_dir", "request_cancel",
]
