"""uat-bug-042: "Assess all new" -- a full assessment of every new posting of a run.

A run fully assesses only its top ``selection_cap`` postings (10 by
default). With a local CLI model there is no per-token bill, so the Jobs page
offers one click that assesses every other new posting of the run shown.
Each posting goes through the existing single-posting path, the one the job
page's Assess button uses (``quick_assess.run_quick_assessment``, origin
``job_page``), under the run's own sealed model target and the selected
profile's resume. So a result lands exactly where a click on one card's
Assess puts it: the quick-assess store, merged onto the card by the UI.

What is queued (:func:`build_queue`): the run's rows in the grid's order
(rank order among the not-assessed rows, likely fits first) whose acquire
outcome is ``new`` or ``edited``, that have a URL, that the run did not
assess (nor carry an assessment forward for), that are not already in the
quick-assess store for the profile, that the run did not leave out on
purpose (:data:`EXCLUDED_REASONS`: its location filter, a duplicate
of a posting it kept, an unchanged posting), and that are not a near-copy
(``selection.duplicate_key``: same company, title and country) of a posting
assessed or queued before it -- the run's own selection drops those too.

0110-039: a posting whose stored assessment for the profile was made with an
older prompt, other candidate settings or a story bank that has since
changed (``assessment_basis``) is queued again, whatever the run's outcome
or reason for it: the click re-assesses it. Only a CURRENT stored assessment
is skipped. The plan counts the two apart (``new_count``, ``stale_count``).
Nothing is re-assessed without the click.

Bounded: :func:`assess_concurrency` calls at a time -- K=4
(``model_rank.SMALL_MACHINE_CONCURRENCY``): each codex/claude child is
~260-330 MB of RSS, and the ranker's small-machine K is the bound that is
known safe on any machine; it is never more than the ranker's own K
(``rank_run.run_concurrency``). The in-run "all" cap uses the same K
(``proposal_execution``).

Durable record, one directory per job, beside the quick-assess store (plain
files under the GigAI home, like the results themselves; nothing here is
journaled, so the workpad stays clean):
``<home>/scout/<project_id>/assess_all/aa_<uuid>/``

* ``record.json``: ``kind: "assess_all"``, the run, profile, model target,
  K, the queued URLs in order, ``status`` (``running`` -> ``complete`` /
  ``cancelled`` / ``failed``), times, ``reason``.
* ``progress.jsonl``: one ``started`` / ``finished`` / ``skipped`` line per
  posting (``finished`` carries ``ok``, the verdict or the error code, and
  the call's ``seconds``).
* ``owner.json`` (pid + process token) and, to cancel from any process,
  ``cancel``.

Single flight per (run, profile): a start while a job for them runs -- in
this process or another live one -- joins it. A cancel stops new calls; the
calls in flight finish and every finished result is kept (it is already in
the store). A record left ``running`` by a process that is gone reads as
``interrupted``. Resume is a new start: the queue is built again, so every
posting assessed since (by this job, a cancelled one, or a click on a
card) is skipped and never assessed twice.

The estimate (:func:`plan`): per-call seconds MEASURED on this machine for
this run -- the median of the finished calls of earlier "assess all" jobs
of the run, else of the run's own assessments (``progress/assess.jsonl``
start/finish times). With neither there is no minute figure: the plan gives
the count and K only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait as wait_futures
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import statistics
import threading
import time
from typing import Any
import uuid

from .model_rank import SMALL_MACHINE_CONCURRENCY

RECORD_PREFIX = "aa_"
RECORD_SCHEMA = "scout-assess-all:1"
FINISHED = frozenset({"complete", "cancelled", "failed"})
#: K: calls at a time (see the module docstring for why 4).
ASSESS_CONCURRENCY = SMALL_MACHINE_CONCURRENCY
#: Rows the run left out on purpose; "Assess all new" leaves them out too.
#: 0.1.11.3: not ``sponsorship_excluded`` (an older run's reason). Sponsorship is a label and leaves nothing out.
EXCLUDED_REASONS = frozenset({"location_mismatch", "role_mismatch", "duplicate", "unchanged"})
#: The error codes after which no further call can succeed: the job stops.
FATAL_CODES = frozenset({"model_target_unavailable", "profile_not_found", "profile_unavailable", "resume_unavailable", "target_unavailable"})
_NEW_OUTCOMES = frozenset({"new", "edited"})
_RECORD_FILENAME = "record.json"
_LINES_FILENAME = "progress.jsonl"
_OWNER_FILENAME = "owner.json"
_CANCEL_FILENAME = "cancel"
_PROCESS_TOKEN = uuid.uuid4().hex

_logger = logging.getLogger("gigai.scout.server")


def assess_concurrency(cpus: int | None = None) -> int:
    """K for full assessments: :data:`ASSESS_CONCURRENCY`, never more than the ranker's own K."""

    from .rank_run import run_concurrency

    return max(1, min(ASSESS_CONCURRENCY, run_concurrency(cpus)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- the queue ---------------------------------------------------------------------


@dataclass(frozen=True)
class QueueItem:
    """One posting to assess: its identity, URL, and the run's title/company for it."""

    normalized_url: str
    url: str
    title: str = ""
    company: str = ""

    def to_json(self) -> dict[str, str]:
        return {"normalized_url": self.normalized_url, "url": self.url, "title": self.title, "company": self.company}

    @classmethod
    def from_json(cls, value: object) -> "QueueItem | None":
        if not isinstance(value, Mapping):
            return None
        url = value.get("url")
        identity = value.get("normalized_url")
        if not isinstance(url, str) or not isinstance(identity, str):
            return None
        return cls(identity, url, str(value.get("title") or ""), str(value.get("company") or ""))


def build_queue(
    rows: Iterable[Any],
    *,
    run_assessed: Iterable[str],
    not_assessed_reasons: Mapping[str, str],
    already_assessed: Iterable[str],
    stale: Iterable[str] = (),
) -> list[QueueItem]:
    """The postings "Assess all new" would assess, in ``rows`` order (the grid's).

    ``rows`` are ``PostingRowResult``-like (``.posting``, ``.outcome``);
    ``run_assessed`` the URLs the run assessed or carried forward;
    ``not_assessed_reasons`` the run's reason per not-assessed URL;
    ``already_assessed`` the URLs the quick-assess store holds for the profile;
    ``stale`` (0110-039) those of them whose stored assessment is the one
    shown and was made with older settings: queued again wherever they are
    in the run (an unchanged posting too), never skipped as a near-copy.
    """

    from .selection import duplicate_key

    rows = list(rows)
    stale = set(stale)
    skip = (set(run_assessed) | set(already_assessed)) - stale
    # Near-copies of a posting already assessed are not queued; of two
    # near-copies not assessed, the first in ``rows`` order is.
    covered = {_duplicate_key(row.posting, duplicate_key) for row in rows if row.posting.normalized_url in skip}
    queue: list[QueueItem] = []
    seen: set[str] = set()
    for row in rows:
        posting = row.posting
        identity = posting.normalized_url
        outcome = getattr(row.outcome, "value", row.outcome)
        again = identity in stale
        if identity in seen or identity in skip or (not again and outcome not in _NEW_OUTCOMES):
            continue
        if not again and not_assessed_reasons.get(identity) in EXCLUDED_REASONS:
            continue
        url = getattr(posting, "url", None) or ""
        if not url:
            continue
        key = _duplicate_key(posting, duplicate_key)
        if key in covered and not again:
            continue
        covered.add(key)
        seen.add(identity)
        queue.append(QueueItem(identity, url, getattr(posting, "title", "") or "", getattr(posting, "company", "") or ""))
    return queue


def _duplicate_key(posting: Any, key: Callable[[Any], object]) -> object:
    from types import SimpleNamespace

    return key(
        SimpleNamespace(
            company=getattr(posting, "company", "") or "",
            title=getattr(posting, "title", "") or "",
            location=getattr(posting, "location", "") or "",
        )
    )


# --- the estimate ------------------------------------------------------------------


def run_call_seconds(run_root: Path) -> list[float]:
    """Seconds per assessment the run itself measured (``progress/assess.jsonl``), in file order."""

    from .progress import _read_jsonl, progress_dir

    started: dict[str, datetime] = {}
    seconds: list[float] = []
    for event in _read_jsonl(progress_dir(run_root) / "assess.jsonl"):
        url = event.get("normalized_url")
        at = _parse_at(event.get("at"))
        if not isinstance(url, str) or at is None:
            continue
        if event.get("event") == "started":
            started[url] = at
        elif event.get("event") == "finished" and event.get("ok") and url in started:
            elapsed = (at - started.pop(url)).total_seconds()
            if elapsed > 0:
                seconds.append(elapsed)
    return seconds


def estimate_minutes(count: int, concurrency: int, seconds_per_call: float) -> int:
    """Whole minutes for ``count`` calls ``concurrency`` at a time, rounded up (at least 1 when there is work)."""

    if count <= 0:
        return 0
    waves = math.ceil(count / max(1, concurrency))
    return max(1, math.ceil(waves * seconds_per_call / 60))


def plan(
    *,
    count: int,
    model_target: str,
    concurrency: int,
    job_seconds: Sequence[float] = (),
    run_seconds: Sequence[float] = (),
    stale_count: int = 0,
) -> dict[str, object]:
    """What a start would do: ``count`` calls, K at a time, and a minute figure only from measured times.

    ``stale_count`` (0110-039) of the ``count`` are re-assessments of a stored
    assessment made with older settings; ``new_count`` is the rest.
    """

    if job_seconds:
        samples, source = list(job_seconds), "assess_all"
    elif run_seconds:
        samples, source = list(run_seconds), "run"
    else:
        samples, source = [], None
    per_call = round(statistics.median(samples), 1) if samples else None
    return {
        "count": count,
        "new_count": max(0, count - stale_count),
        "stale_count": stale_count,
        "model_target": model_target,
        "concurrency": concurrency,
        "per_call_seconds": per_call,
        "per_call_source": source,
        "per_call_samples": len(samples),
        "estimate_minutes": estimate_minutes(count, concurrency, per_call) if per_call is not None else None,
    }


# --- records -------------------------------------------------------------------------


def records_dir(home_root: Path, target: Path) -> Path:
    from .discovery.storage import project_id

    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "assess_all"


def _read_json(path: Path) -> object | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    from .discovery.storage import atomic_write

    atomic_write(path, json.dumps(dict(value), indent=2, sort_keys=True).encode("utf-8"))


def _read_lines(path: Path) -> list[dict[str, object]]:
    from .progress import _read_jsonl

    return _read_jsonl(path)


@dataclass(frozen=True)
class AssessAllRecord:
    """One ``assess_all/aa_*`` record as read from disk."""

    record_id: str
    root: Path
    details: dict[str, object]

    @property
    def status(self) -> str:
        return str(self.details.get("status") or "")

    @property
    def run_id(self) -> str | None:
        value = self.details.get("run_id")
        return value if isinstance(value, str) else None

    @property
    def profile_id(self) -> str | None:
        value = self.details.get("profile_id")
        return value if isinstance(value, str) else None

    @property
    def queue(self) -> list[QueueItem]:
        items = self.details.get("queue")
        found = [QueueItem.from_json(item) for item in items] if isinstance(items, list) else []
        return [item for item in found if item is not None]

    def owner_alive(self) -> bool:
        """True while a live process runs this job."""

        owner = _read_json(self.root / _OWNER_FILENAME)
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

    def lines(self) -> list[dict[str, object]]:
        return _read_lines(self.root / _LINES_FILENAME)

    def cancel_requested(self) -> bool:
        return (self.root / _CANCEL_FILENAME).is_file()


def list_records(home_root: Path, target: Path, *, run_id: str | None = None) -> list[AssessAllRecord]:
    """Every "assess all" record (of ``run_id`` when given), newest first."""

    try:
        root = records_dir(home_root, target)
    except Exception:  # noqa: BLE001 - an unbound folder simply has no records
        return []
    if not root.is_dir():
        return []
    records = []
    for directory in root.glob(f"{RECORD_PREFIX}*"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        details = _read_json(directory / _RECORD_FILENAME)
        if not isinstance(details, dict) or details.get("kind") != "assess_all":
            continue
        record = AssessAllRecord(directory.name, directory, details)
        if run_id is None or record.run_id == run_id:
            records.append(record)
    records.sort(key=lambda record: (str(record.details.get("started_at") or ""), record.record_id), reverse=True)
    return records


def finished_call_seconds(records: Iterable[AssessAllRecord]) -> list[float]:
    """Seconds per successful call measured by earlier jobs."""

    seconds: list[float] = []
    for record in records:
        for line in record.lines():
            value = line.get("seconds")
            if line.get("event") == "finished" and line.get("ok") and isinstance(value, (int, float)) and value > 0:
                seconds.append(float(value))
    return seconds


def summary(record: AssessAllRecord | None) -> dict[str, object] | None:
    """A record for a response: identity, live status, and "x of N assessed"."""

    if record is None:
        return None
    total = len(record.queue)
    started: set[str] = set()
    finished: dict[str, dict[str, object]] = {}
    skipped: set[str] = set()
    for line in record.lines():
        url = line.get("normalized_url")
        if not isinstance(url, str):
            continue
        event = line.get("event")
        if event == "started":
            started.add(url)
        elif event == "finished":
            finished[url] = line
        elif event == "skipped":
            skipped.add(url)
    assessed = sum(1 for line in finished.values() if line.get("ok"))
    failed = len(finished) - assessed
    status = record.live_status()
    if status == "interrupted":
        # The record was read while the job ran, and a request builds this
        # last. A job that ENDED in between is not "interrupted": it reads
        # "running" this once (as it was when the request's counts were
        # read), and the next read says how it ended, with counts to match.
        now = _read_json(record.root / _RECORD_FILENAME)
        if isinstance(now, dict) and now.get("status") in FINISHED:
            status = "running"
    in_flight = len(started - set(finished)) if status == "running" else 0
    done = assessed + failed + len(skipped)
    text = f"{assessed} of {total} assessed"
    if failed:
        text += f", {failed} failed"
    return {
        "record_id": record.record_id,
        "kind": "assess_all",
        "run_id": record.run_id,
        "profile_id": record.profile_id,
        "model_target": record.details.get("model_target"),
        "concurrency": record.details.get("concurrency"),
        "status": status,
        "reason": record.details.get("reason"),
        "started_at": record.details.get("started_at"),
        "finished_at": record.details.get("finished_at"),
        "total": total,
        "assessed": assessed,
        "failed": failed,
        "skipped": len(skipped),
        "in_flight": in_flight,
        "remaining": max(0, total - done),
        "text": text,
    }


# --- the job -------------------------------------------------------------------------


#: ``assess_one(item) -> verdict value or None``; raises ``AssessOneError`` for a
#: posting that could not be assessed (the code is recorded).
AssessOne = Callable[[QueueItem], "str | None"]


class AssessOneError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class JobInput:
    """What one job needs: where to write, what to assess, how."""

    home_root: Path
    target: Path
    run_id: str
    profile_id: str
    model_target: str
    queue: tuple[QueueItem, ...]
    assess_one: AssessOne
    concurrency: int = field(default_factory=assess_concurrency)
    #: True when the posting is already in the store (checked again just
    #: before each call: a click on a card may have assessed it meanwhile).
    is_assessed: Callable[[QueueItem], bool] = lambda _item: False


@dataclass
class _Job:
    record_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


_LOCK = threading.RLock()
_REGISTRY: dict[str, _Job] = {}


def _details(record_id: str, source: JobInput, *, status: str, started_at: str, **extra: object) -> dict[str, object]:
    return {
        "schema_version": RECORD_SCHEMA,
        "kind": "assess_all",
        "record_id": record_id,
        "run_id": source.run_id,
        "profile_id": source.profile_id,
        "model_target": source.model_target,
        "concurrency": source.concurrency,
        "queue": [item.to_json() for item in source.queue],
        "status": status,
        "started_at": started_at,
        **extra,
    }


def _claim(root: Path) -> None:
    _write_json(root / _OWNER_FILENAME, {"pid": os.getpid(), "token": _PROCESS_TOKEN, "claimed_at": _now()})


def _work(source: JobInput, record: AssessAllRecord, job: _Job) -> None:
    lines_path = record.root / _LINES_FILENAME
    write_lock = threading.Lock()

    def line(event: str, item: QueueItem, **extra: object) -> None:
        from .progress import _append_line

        with write_lock:
            _append_line(lines_path, {"event": event, "normalized_url": item.normalized_url, "at": _now(), **extra})

    def cancelled() -> bool:
        return job.cancel.is_set() or (record.root / _CANCEL_FILENAME).is_file()

    def call(item: QueueItem) -> tuple[bool, str | None, float]:
        began = time.monotonic()
        try:
            verdict = source.assess_one(item)
        except AssessOneError as exc:
            return False, exc.code, time.monotonic() - began
        except Exception as exc:  # noqa: BLE001 - one posting's failure never stops the rest
            _logger.warning("assess all (%s): a posting failed: %s", source.run_id, type(exc).__name__)
            return False, f"error:{type(exc).__name__}", time.monotonic() - began
        return True, verdict, time.monotonic() - began

    status, reason = "complete", None
    pending = iter(source.queue)
    try:
        with ThreadPoolExecutor(max_workers=source.concurrency, thread_name_prefix="scout-assess-all") as pool:
            in_flight: dict[Future, QueueItem] = {}
            stop = False

            def submit_next() -> None:
                while not stop and not cancelled():
                    item = next(pending, None)
                    if item is None:
                        return
                    if source.is_assessed(item):
                        line("skipped", item, reason="already_assessed")
                        continue
                    line("started", item)
                    in_flight[pool.submit(call, item)] = item
                    return

            while len(in_flight) < source.concurrency:
                before = len(in_flight)
                submit_next()
                if len(in_flight) == before:
                    break
            while in_flight:
                done, _running = wait_futures(tuple(in_flight), return_when=FIRST_COMPLETED)
                for future in done:
                    item = in_flight.pop(future)
                    ok, value, seconds = future.result()
                    if ok:
                        line("finished", item, ok=True, verdict=value, seconds=round(seconds, 3))
                    else:
                        line("finished", item, ok=False, code=value, seconds=round(seconds, 3))
                        if value in FATAL_CODES:
                            stop, status, reason = True, "failed", value
                    submit_next()
        if status == "complete" and cancelled() and next(pending, None) is not None:
            status = "cancelled"
    except Exception as exc:  # noqa: BLE001 - the record must end, whatever went wrong
        _logger.exception("assess all (%s): the job failed", source.run_id)
        status, reason = "failed", f"error:{type(exc).__name__}"
    finally:
        details = dict(record.details)
        details.update(status=status, reason=reason, finished_at=_now())
        try:
            _write_json(record.root / _RECORD_FILENAME, details)
        except OSError:
            _logger.exception("assess all (%s): the record could not be finished", source.run_id)
        _logger.info("assess all (%s): %s", source.run_id, (summary(AssessAllRecord(record.record_id, record.root, details)) or {}).get("text"))
        job.done.set()
        with _LOCK:
            if _REGISTRY.get(record.record_id) is job:
                _REGISTRY.pop(record.record_id, None)


@dataclass(frozen=True)
class StartOutcome:
    record: AssessAllRecord | None
    action: str  # "started" | "joined" | "none"


def running_record(home_root: Path, target: Path, run_id: str, profile_id: str) -> AssessAllRecord | None:
    """The job for (run, profile) a live process runs now, if any."""

    for record in list_records(home_root, target, run_id=run_id):
        if record.profile_id == profile_id and record.status == "running" and record.owner_alive():
            return record
    return None


def start_or_join(source: JobInput) -> StartOutcome:
    """Single flight per (run, profile): join the live job, else start one for ``source.queue``."""

    with _LOCK:
        live = running_record(source.home_root, source.target, source.run_id, source.profile_id)
        if live is not None:
            return StartOutcome(live, "joined")
        if not source.queue:
            return StartOutcome(None, "none")
        record_id = f"{RECORD_PREFIX}{uuid.uuid4()}"
        root = records_dir(source.home_root, source.target) / record_id
        details = _details(record_id, source, status="running", started_at=_now())
        _write_json(root / _RECORD_FILENAME, details)
        _claim(root)
        record = AssessAllRecord(record_id, root, details)
        job = _Job(record_id)
        _REGISTRY[record_id] = job
        thread = threading.Thread(target=_work, args=(source, record, job), name=f"scout-assess-all-{record_id}", daemon=True)
        job.thread = thread
        thread.start()
        return StartOutcome(record, "started")


def cancel(home_root: Path, target: Path, record_id: str) -> bool:
    """Ask the job ``record_id`` to stop (any process): no new call starts; calls in flight finish."""

    record = next((item for item in list_records(home_root, target) if item.record_id == record_id), None)
    if record is None or record.status != "running":
        return False
    with _LOCK:
        job = _REGISTRY.get(record_id)
    if job is not None:
        job.cancel.set()
    (record.root / _CANCEL_FILENAME).write_text(_now(), encoding="utf-8")
    return True


def wait_for_jobs(*, timeout: float | None = None) -> bool:
    """Wait until no job runs in this process; false when ``timeout`` ran out first."""

    with _LOCK:
        running = [item for item in _REGISTRY.values() if not item.done.is_set()]
    return all(item.done.wait(timeout) for item in running)


# --- the real single-posting path ------------------------------------------------------


def quick_assess_one(*, home_root: Path, target: Path, profile_id: str, model_target: str) -> AssessOne:
    """``assess_one`` through ``run_quick_assessment`` (the job page's Assess), for one profile and target."""

    from ..quick_assess import QuickAssessError, run_quick_assessment
    from .assess_contracts import ORIGIN_JOB_PAGE, AssessJobInput, AssessRequest, AssessResumeInput
    from .contracts import ModelTarget

    target_enum = ModelTarget(model_target)

    def assess_one(item: QueueItem) -> str | None:
        request = AssessRequest(
            job=AssessJobInput(job_url=item.url, title=item.title or None, company=item.company or None),
            resume=AssessResumeInput(profile_id=profile_id),
            model_target=target_enum,
            origin=ORIGIN_JOB_PAGE,
        )
        try:
            response = run_quick_assessment(request, home_root=Path(home_root), target=Path(target))
        except QuickAssessError as exc:
            raise AssessOneError(exc.code) from None
        verdict = response.result.verdict
        return verdict.value if verdict is not None else None

    return assess_one


def stored_for_profile(home_root: Path, target: Path, profile_id: str) -> Callable[[QueueItem], bool]:
    """True when the quick-assess store already holds ``item`` for ``profile_id``."""

    from ..quick_assess import quick_assess_path

    def is_assessed(item: QueueItem) -> bool:
        try:
            return quick_assess_path(Path(home_root), Path(target), profile_id, item.normalized_url).is_file()
        except Exception:  # noqa: BLE001 - unknown means "not known to be assessed"
            return False

    return is_assessed


def current_for_profile(home_root: Path, target: Path, profile_id: str) -> Callable[[QueueItem], bool]:
    """True when the store holds a CURRENT assessment of ``item`` for ``profile_id`` (0110-039).

    What the job checks just before each call: a posting assessed meanwhile
    (a click on its card) is skipped, a stored assessment made with older
    settings is not. The settings are read again at each check, so a change
    made while the job runs is seen. A stored file that cannot be read
    counts as assessed, as it did before.
    """

    from ..assessment_basis import BasisCheck
    from ..quick_assess import _read_stored, quick_assess_path

    def is_assessed(item: QueueItem) -> bool:
        try:
            path = quick_assess_path(Path(home_root), Path(target), profile_id, item.normalized_url)
            if not path.is_file():
                return False
            stored = _read_stored(path)
            if stored is None:
                return True
            return BasisCheck(home_root=Path(home_root), target=Path(target)).reason(stored) is None
        except Exception:  # noqa: BLE001 - unknown means "not known to be assessed"
            return False

    return is_assessed


__all__ = [
    "ASSESS_CONCURRENCY",
    "EXCLUDED_REASONS",
    "FINISHED",
    "AssessAllRecord",
    "AssessOneError",
    "JobInput",
    "QueueItem",
    "StartOutcome",
    "assess_concurrency",
    "build_queue",
    "cancel",
    "current_for_profile",
    "estimate_minutes",
    "finished_call_seconds",
    "list_records",
    "plan",
    "quick_assess_one",
    "records_dir",
    "run_call_seconds",
    "running_record",
    "start_or_join",
    "stored_for_profile",
    "summary",
    "wait_for_jobs",
]
