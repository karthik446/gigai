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
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time
import uuid

from .store import _pid_alive, pipeline_path, process_token

KIND_ASSESS_BATCH = "assess_batch"
LIVE_DIR = "live"
#: A marker not touched for this long is not waited for: its batch hangs or its process is gone.
BATCH_QUIET_SECONDS = 15 * 60.0


def live_dir(home_root: Path, target: Path) -> Path:
    return pipeline_path(Path(home_root), Path(target)).parent / LIVE_DIR


class LiveBatch:
    """One live batch's marker. ``beat`` after each posting keeps it fresh."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def beat(self) -> None:
        if self.path is None:
            return
        try:
            os.utime(self.path)
        except OSError:
            pass  # the marker is gone or cannot be touched: the batch goes on, the runner stops waiting later


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
        path.write_text(
            json.dumps({"kind": KIND_ASSESS_BATCH, "pid": os.getpid(), "token": process_token(), "started_at": started}),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - the marker is a courtesy to the runner; a batch never fails because it could not be written
        path = None
    try:
        yield LiveBatch(path)
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
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


def assess_batch_live(home_root: Path, target: Path) -> bool:
    """Whether an assess batch of this project is running now, in this process or another. Reads only."""

    try:
        directory = live_dir(home_root, target)
    except Exception:  # noqa: BLE001 - no bound project: there is no batch to wait for
        return False
    if not directory.is_dir():
        return False
    now = time.time()
    return any(_is_live(path, now) for path in directory.glob("batch_*.json"))


__all__ = ["BATCH_QUIET_SECONDS", "KIND_ASSESS_BATCH", "LiveBatch", "assess_batch", "assess_batch_live", "live_dir"]
