"""0.1.11.5 (ASSESS-01): an approved "Assess these" batch as a job of the server, with a status read and a cancel.

Before this the Jobs page waited on ONE request for the whole batch (about 29
minutes for 50 postings), so its dialog stayed open and nothing could stop it.
Now ``POST /api/postings/assess`` with ``approve`` and ``background`` starts
the batch on a thread of the server and answers as soon as the batch is live
(:func:`start`); the page closes its dialog and reads how far it is.

* :func:`status` is what ``GET /api/postings/assess/status`` answers
  (``scout-assess-batch:1``): ``batch`` is the live batch of the project, read
  from its marker alone (``pipeline.busy.batch_status``: one small folder,
  never a store or a posting read, so the cost is flat), whichever process
  runs it; ``last`` is how the last batch this server ran ended. A batch of
  this server is ``running`` until its ``last`` is known: its marker goes
  first, and in between the batch is answered from its own counts.
* :func:`cancel` is ``POST /api/postings/assess/cancel``: the batch starts no
  further model call. THE CALLS IN FLIGHT FINISH and their results are stored
  like any other (their tokens are spent already); the batch then ends
  ``cancelled`` with how many were assessed. A batch started in a terminal
  (``gigai scout jobs assess --yes``, ``gigai scout new --yes``) is cancelled
  the same way, and ``gigai scout jobs assess --cancel`` is the command.

A server that is STOPPED ends the model processes it started
(``adapters.process.terminate_children``, from ``present_api``'s SIGTERM
handler): nothing waits for them, and what they would have answered is lost.

Ids, codes and counts only (``pending`` holds job identities: posting URLs).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import logging
from pathlib import Path
import threading

from .pipeline import busy
from .pipeline.busy import LiveBatch

SCHEMA_VERSION = "scout-assess-batch:1"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
#: How long ``POST /api/postings/assess`` waits for an approved batch to be live before it answers anyway.
START_WAIT_SECONDS = 30.0

_logger = logging.getLogger("gigai.scout.server")


class BatchJob:
    """One approved batch running on a thread of this process."""

    def __init__(self) -> None:
        self.ready = threading.Event()  # the batch is live (``live`` is set), or the call ended without one
        self.done = threading.Event()
        self.live: LiveBatch | None = None
        self.response: dict[str, object] | None = None
        self.error: BaseException | None = None
        self.last: dict[str, object] | None = None
        self.previous_last: dict[str, object] | None = None  # how the batch before this one ended
        self.thread: threading.Thread | None = None


_JOBS: dict[tuple[Path, Path], BatchJob] = {}
_LOCK = threading.Lock()


def _key(home_root: Path, target: Path) -> tuple[Path, Path]:
    return Path(home_root).resolve(strict=False), Path(target).resolve(strict=False)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ended(job: BatchJob, batch_id: str | None) -> dict[str, object] | None:
    """How the batch ended, for ``last``; ``None`` when the call assessed nothing (an ask, nothing to assess)."""

    if job.error is not None:
        if job.live is None:
            return None  # refused before a batch was live: the request itself answers the error
        return {
            "id": batch_id, "status": STATUS_FAILED, "requested": job.live.total, "assessed": job.live.assessed, "failed": job.live.failed,
            "not_started": max(0, job.live.total - job.live.assessed - job.live.failed), "error_code": getattr(job.error, "code", None) or "assess_batch_failed",
            "failed_codes": [], "more_after": 0, "finished_at": _now(),
        }
    assessed = (job.response or {}).get("assessed")
    if not isinstance(assessed, Mapping):
        return None
    failed = [item for item in assessed.get("failed") or () if isinstance(item, Mapping)]
    counts = (job.response or {}).get("counts")
    cancelled = assessed.get("stopped") == STATUS_CANCELLED
    return {
        "id": batch_id, "status": STATUS_CANCELLED if cancelled else STATUS_DONE,
        "requested": assessed.get("requested", 0), "assessed": assessed.get("assessed", 0), "failed": len(failed),
        "not_started": assessed.get("not_started", 0) if cancelled else 0,
        "error_code": None if cancelled else assessed.get("stopped"),
        "failed_codes": sorted({str(item["error_code"]) for item in failed if item.get("error_code")}),
        "more_after": counts.get("more_after", 0) if isinstance(counts, Mapping) else 0,
        "finished_at": _now(),
    }


def start(home_root: Path, target: Path, call: Callable[[Callable[[LiveBatch], None]], dict[str, object]], *, wait: float = START_WAIT_SECONDS) -> BatchJob:
    """Run ``call(on_live)`` (an approved ``assess_these``) on a thread; back when its batch is live, or it ended.

    ``job.live`` set: a batch was started; :func:`status` says how far it is (or, in ``last``, how it ended: a
    fast batch can be over already). Not set and ``job.done`` set: nothing was started (``job.response`` is the
    call's answer, or ``job.error`` what it raised: nothing to assess, a refusal).
    """

    job = BatchJob()
    key = _key(home_root, target)
    with _LOCK:
        previous = _JOBS.get(key)
    job.previous_last = previous.last if previous is not None else None

    def on_live(live: LiveBatch) -> None:
        job.live = live
        with _LOCK:
            _JOBS[key] = job  # before the request is back: a status read never finds the job of the batch before
        job.ready.set()

    def run() -> None:
        try:
            job.response = call(on_live)
        except BaseException as exc:  # noqa: BLE001 - kept for the request that waits (it answers the error); a batch that was live says it failed
            job.error = exc
            if job.live is not None:
                _logger.warning("assess batch failed: %s", type(exc).__name__)
        finally:
            job.last = _ended(job, job.live.batch_id if job.live is not None else None)
            job.done.set()
            job.ready.set()

    job.thread = threading.Thread(target=run, name="scout-assess-these-batch", daemon=True)
    job.thread.start()
    job.ready.wait(wait)
    with _LOCK:
        current = _JOBS.get(key)
        # A call that assessed nothing (a refusal: another batch runs) leaves the job of the batch that DOES run in place.
        if job.live is not None or current is None or current.done.is_set():
            _JOBS[key] = job
    return job


def started_body(home_root: Path, target: Path) -> dict[str, object]:
    """What the request answers (202) once its batch is live: the status object with ``status: "started"``."""

    return {**status(home_root, target), "status": "started"}


def status(home_root: Path, target: Path) -> dict[str, object]:
    """``scout-assess-batch:1``: the live batch (or ``None``) and how the last batch of this server ended (or ``None``)."""

    with _LOCK:
        job = _JOBS.get(_key(home_root, target))
    last, ending = None, None
    if job is not None:
        # ``done`` is read first and ``last`` is set before it: a job that is done has its end.
        if job.done.is_set():
            last = job.last if job.last is not None else job.previous_last
        else:
            last, ending = job.previous_last, job.live
    batch = busy.batch_status(Path(home_root), Path(target))
    if batch is None and ending is not None:
        # The batch's marker is removed INSIDE its call and ``last`` is set when the call has returned: read in
        # between, the batch is ending, not over. It is still the running batch (from its own counts), so the page
        # never sees a batch end with no ``last`` or with the ``last`` of the batch before it.
        batch = ending.view()
    return {"schema_version": SCHEMA_VERSION, "running": batch is not None, "batch": batch, "last": last}


def cancel(home_root: Path, target: Path) -> dict[str, object]:
    """Ask the live batch to stop (see the module docstring) and answer :func:`status` with ``cancel_requested``."""

    with _LOCK:
        job = _JOBS.get(_key(home_root, target))
    if job is not None and job.live is not None and not job.done.is_set():
        job.live.cancel()  # this process: also when the marker could not be written
    asked = busy.request_cancel(Path(home_root), Path(target))
    return {**status(home_root, target), "cancel_requested": asked}


def wait_for_batch(home_root: Path, target: Path, *, timeout: float | None = None) -> bool:
    """Wait until the batch this process started for the project ended; false when ``timeout`` ran out first."""

    with _LOCK:
        job = _JOBS.get(_key(home_root, target))
    return True if job is None else job.done.wait(timeout)


__all__ = ["BatchJob", "SCHEMA_VERSION", "START_WAIT_SECONDS", "cancel", "start", "started_body", "status", "wait_for_batch"]
