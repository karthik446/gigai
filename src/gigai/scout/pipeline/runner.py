"""0.1.10.7 PL4: the runner that claims pipeline steps and runs them.

One executor, two hosts (DESIGN 3.2):

* :class:`PipelineRunner` in the Scout server: a third background thread
  beside the sources refresh and the model tag queue (``refresh_tick``).
  Every :data:`POLL_SECONDS`, or at once on :meth:`PipelineRunner.kick`, it
  runs one :meth:`PipelineRunner.drain`.
* ``gigai scout pipeline run --once`` (and ``pipeline process <job>``): the
  same ``drain`` on the calling thread, for a user with no server running.

Both claim through ``PipelineStore.claim`` (one ``BEGIN IMMEDIATE``
transaction in the project's ``pipeline.sqlite``), so a server and a
``--once`` running together never run a step twice, and the lane caps
(claude_cli 2, codex_cli 2, ollama 1, local 4, model calls 4 in all) hold
across both.

**One drain.** A few worker threads claim until nothing is left to claim
now. For each claimed step: its input digest is computed from its inputs as
they are (``steps.input_digest``); a downstream step whose digest is the one
it was done with finishes without running; a model step first takes one call
from the day's allowance; the step runs with its lease renewed every
:data:`~gigai.scout.pipeline.store.RENEW_SECONDS`; its outcome and the
numbers of the model calls it made are stored (``finish`` / ``fail``:
retries, backoff and the lane backoff on ``model_target_unavailable`` are the
store's).

**Yield** (DESIGN 7). No step is claimed while a manual sources update, an
"assess all" batch, the batch ``scout new`` assesses on a yes (``busy.py``)
or a find-jobs run is live (:func:`live_work`): they
already use the machine's model slots, and the operator asked for them. A
step already running finishes. A background sources check is not waited for.

**The daily cap.** ``pipeline.max_model_calls_per_day`` (40) counts every
model call of every pipeline step, for the whole install. The call that would
go over it is not made and does not fail: its step waits until the next
local day with ``daily_cap_reached`` (``PipelineStore.defer``).

**A dead runner.** ``claim`` gives a step back at once when the process that
held it is gone; a drain also does it first thing (``reclaim``).

**The rank lane and old runs** (0.1.10.7 M4a). After a drain the server's
thread also gives the background rank lane a turn (``rank_lane.rank_tick``:
a few calls, first :data:`RANK_START_DELAY_SECONDS` after the start, then
every :data:`RANK_POLL_SECONDS`, or at once on :meth:`PipelineRunner.kick_rank`;
it backs off when no model answers) and, once per start, imports what old
find-jobs runs assessed into the read model (``run_history.migrate_runs``).
An "assess these" batch is live work too (the same ``busy.py`` marker as the
``scout new`` batch): nothing is claimed while it runs.

**Off.** ``pipeline.enabled`` false, :data:`~gigai.scout.pipeline.settings.PIPELINE_ENV`
off, or a settings file that cannot be read: nothing is claimed
(``pipeline process`` still runs the one job it was asked for unless the
settings cannot be read).

Nothing here is on a request's path: the server's routes never wait for a
drain, and ``/api/health`` never opens the pipeline file.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from pathlib import Path
import threading
import time

from ..call_metrics import capture_calls, total_metrics
from . import steps as steps_module
from .busy import assess_batch_live
from .settings import SOURCE_UNREADABLE, PipelineSetting, pipeline_setting
from .store import (
    CAP_PIPELINE_CALLS,
    LOCAL_LANE,
    LOST_LEASE,
    RENEW_SECONDS,
    STATE_BLOCKED,
    STATE_READY,
    STATE_RUNNING,
    Claim,
    PipelineStore,
    pipeline_path,
)

STATUS_SCHEMA = "scout-pipeline-status:1"
DRAIN_SCHEMA = "scout-pipeline-drain:1"

#: How often the server's thread looks for work (and how long a stop can take while it waits).
POLL_SECONDS = 30.0
#: How often the server's thread gives the rank lane a turn when nothing kicked it, and how many calls a turn may make.
RANK_POLL_SECONDS = 600.0
#: The rank lane's first turn after the server starts: the start itself spends no model call.
RANK_START_DELAY_SECONDS = 120.0
RANK_CALLS_PER_TURN = 4
#: The rank lane's backoff when no model answers or its answer cannot be read: doubling, like a model lane's.
RANK_BACKOFF_SECONDS = 300.0
RANK_BACKOFF_MAX_SECONDS = 6 * 3600.0
#: Claiming threads of one drain: the model total (4) is the store's; the rest run local steps.
DEFAULT_WORKERS = 6
#: How long an idle worker waits for a running one to open more steps.
_IDLE_WAIT_SECONDS = 0.05
#: How long one answer of :func:`live_work` is kept by a drain.
_BUSY_TTL_SECONDS = 5.0
#: A find-jobs run that wrote nothing for this long is not waited for (its process is gone).
RUN_QUIET_SECONDS = 15 * 60.0

DRAIN_DISABLED = "disabled"
DRAIN_IDLE = "idle"  # nothing to claim
DRAIN_RAN = "ran"
DRAIN_YIELDED = "yielded"
DRAIN_STOPPED = "stopped"

BUSY_SOURCES_UPDATE = "sources_update"
BUSY_ASSESS_BATCH = "assess_batch"
BUSY_FIND_JOBS_RUN = "find_jobs_run"

WAIT_DAILY_CAP = "daily_cap_reached"
WAIT_RETRY = "retry_backoff"
WAIT_LANE = "lane_backoff"

OUTCOME_DONE = "done"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_WAITING = "waiting"

_LIVE_RUN_STATUSES = frozenset({"preparing", "running", "verifying"})
_logger = logging.getLogger("gigai.scout.pipeline")


# --- what the runner yields to --------------------------------------------------------------


def _find_jobs_run_is_live(home_root: Path, target: Path) -> bool:
    """A find-jobs run of this project that is not finished and wrote something in the last quarter hour."""

    import json

    from ..quick_assess import QuickAssessError, _resolve_workpad

    try:
        runs = Path(_resolve_workpad(home_root, target).path) / "runs"
    except QuickAssessError:
        return False  # no gig: no run
    if not runs.is_dir():
        return False
    newest = time.time() - RUN_QUIET_SECONDS
    for run_dir in runs.glob("run_*"):
        details = run_dir / "run-details.json"
        try:
            touched = max(details.stat().st_mtime, (run_dir / "progress").stat().st_mtime if (run_dir / "progress").is_dir() else 0.0)
            if touched < newest:
                continue
            payload = json.loads(details.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("status") in _LIVE_RUN_STATUSES:
            return True
    return False


def live_work(home_root: Path, target: Path) -> str | None:
    """What the pipeline yields to right now, or ``None``: a manual sources update, an assess batch, a find-jobs run."""

    from ..find_jobs import assess_all
    from ..find_jobs.company_index import CompanyIndex
    from ..find_jobs.sources_update import TRIGGER_AUTO, snapshot_is_live

    snapshot = CompanyIndex.for_home(Path(home_root)).read_update_summary()
    if snapshot_is_live(snapshot) and (snapshot or {}).get("trigger") != TRIGGER_AUTO:
        return BUSY_SOURCES_UPDATE
    if any(record.live_status() == "running" for record in assess_all.list_records(Path(home_root), Path(target))):
        return BUSY_ASSESS_BATCH
    if assess_batch_live(Path(home_root), Path(target)):
        return BUSY_ASSESS_BATCH  # 0.1.10.7 PL5: the batch `scout new` assesses on a yes, an "assess these" batch (busy.py)
    if _find_jobs_run_is_live(Path(home_root), Path(target)):
        return BUSY_FIND_JOBS_RUN
    return None


# --- one drain ------------------------------------------------------------------------------


@dataclass
class DrainResult:
    """What one drain did. ``steps``: one entry per claimed step (ids, codes and numbers only)."""

    state: str = DRAIN_IDLE
    #: Why nothing (more) was claimed: the settings' ``source`` when disabled, what is live when yielded.
    reason: str | None = None
    steps: list[dict[str, object]] = field(default_factory=list)
    model_calls: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": DRAIN_SCHEMA,
            "state": self.state,
            "reason": self.reason,
            "steps": list(self.steps),
            "model_calls": self.model_calls,
        }


class _Shared:
    """What the workers of one drain share."""

    def __init__(self, max_steps: int | None) -> None:
        self.changed = threading.Condition()
        self.running = 0  # workers running a step, or just done with one and about to look again
        self.claimed = 0
        self.max_steps = max_steps
        self.result = DrainResult()
        self.inflight: dict[tuple[str, str, str], Claim] = {}
        self.busy_reason: str | None = None
        self.busy_read_at: float | None = None


class PipelineRunner:
    """The pipeline's executor for one project. ``start`` runs it as the server's background thread.

    ``busy`` (what is live: a reason or ``None``), ``step_api`` (an object
    with ``input_digest`` / ``unchanged`` / ``run_step``, the ``steps``
    module by default), ``clock``, ``now`` (the local date and time, for the
    daily cap), ``renew_seconds`` and ``poll_seconds`` are seams for tests.
    """

    def __init__(
        self,
        *,
        home_root: Path,
        target: Path,
        config: object | None = None,
        environ: Mapping[str, str] | None = None,
        logger: logging.Logger | None = None,
        busy: Callable[[], str | None] | None = None,
        step_api: object | None = None,
        clock: Callable[[], float] = time.time,
        now: Callable[[], datetime] | None = None,
        workers: int = DEFAULT_WORKERS,
        poll_seconds: float = POLL_SECONDS,
        renew_seconds: float = RENEW_SECONDS,
        store_options: Mapping[str, object] | None = None,
    ) -> None:
        self.home_root = Path(home_root)
        self.target = Path(target)
        self._config = config
        self._environ = environ
        self._logger = logger if logger is not None else _logger
        self._busy = busy if busy is not None else (lambda: live_work(self.home_root, self.target))
        self._steps = step_api if step_api is not None else steps_module
        self._clock = clock
        self._now = now if now is not None else (lambda: datetime.now().astimezone())
        self._workers = max(1, int(workers))
        self._poll_seconds = float(poll_seconds)
        self._renew_seconds = float(renew_seconds)
        self._store_options = dict(store_options or {})
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: DrainResult | None = None
        self._last_state: str | None = None
        self._resolved_path: Path | None = None
        self._rank_next = 0.0  # monotonic: when the rank lane gets its next turn
        self._rank_kicked = threading.Event()
        self._rank_backoff = 0.0
        self._last_rank: dict[str, object] | None = None
        self._migrated = False

    # -- the server's thread ---------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._rank_next = time.monotonic() + RANK_START_DELAY_SECONDS
        self._thread = threading.Thread(target=self._loop, name="scout-pipeline", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 10.0) -> bool:
        """End the thread: no new claim; a step that is running finishes. ``True`` once the thread is gone."""

        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def kick(self) -> None:
        """Look for work now instead of at the next poll (a job was enqueued)."""

        self._wake.set()

    def kick_rank(self) -> None:
        """Give the rank lane its turn now (the stored postings or a profile changed), not at its next poll."""

        self._rank_kicked.set()
        self._wake.set()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.drain(stop=self._stop)
                self._note(result)
            except Exception as exc:  # noqa: BLE001 - nothing else observes this thread: log it and keep the loop
                self._logger.warning("pipeline: the runner loop hit %s; it goes on", type(exc).__name__)
            if self._stop.is_set():
                break
            self._background()
            if self._stop.is_set():
                break
            if self._wake.wait(self._poll_seconds):
                self._wake.clear()

    def _background(self) -> None:
        """Once per start: import what old runs assessed. Then the rank lane's turn, when it is due or was kicked."""

        from . import rank_lane

        try:
            if not self._migrated:
                self._migrated = True
                from ..run_history import migrate_runs

                counts = migrate_runs(self.home_root, self.target)
                if counts["runs_imported"]:
                    self._logger.info(
                        "pipeline: imported %d assessment(s) of %d old run(s) into the read model",
                        counts["assessments_imported"], counts["runs_imported"],
                    )
            kicked = self._rank_kicked.is_set()
            self._rank_kicked.clear()
            if time.monotonic() < self._rank_next and (self._rank_backoff or not kicked):
                return  # not due yet; a kick does not cut a backoff short
            result = rank_lane.rank_tick(
                self.home_root, self.target, config=self._config, environ=self._environ, busy=self._busy, stop=self._stop,
                max_calls=RANK_CALLS_PER_TURN,
            )
            self._last_rank = result
            if result["state"] == rank_lane.STATE_UNAVAILABLE:
                self._rank_backoff = min(RANK_BACKOFF_MAX_SECONDS, self._rank_backoff * 2 or RANK_BACKOFF_SECONDS)
                self._rank_next = time.monotonic() + self._rank_backoff
            else:
                self._rank_backoff = 0.0
                more = result["state"] == rank_lane.STATE_RAN and int(result["calls"]) >= RANK_CALLS_PER_TURN  # type: ignore[call-overload]
                self._rank_next = time.monotonic() + (0.0 if more else RANK_POLL_SECONDS)
            if result["calls"]:
                self._logger.info("pipeline: ranked %d posting(s) in %d call(s)", result["ranked"], result["calls"])
        except Exception as exc:  # noqa: BLE001 - nothing else observes this thread: log it and keep the loop
            self._logger.warning("pipeline: the rank lane hit %s; it goes on", type(exc).__name__)
            self._rank_next = time.monotonic() + RANK_POLL_SECONDS

    def _note(self, result: DrainResult) -> None:
        state = f"{result.state}:{result.reason or ''}"
        if result.steps:
            self._logger.info("pipeline: ran %d step(s), %d model call(s)", len(result.steps), result.model_calls)
        elif state != self._last_state:
            self._logger.info("pipeline: %s%s", result.state, f" ({result.reason})" if result.reason else "")
        self._last_state = state

    # -- settings, the store, the day --------------------------------------------------------

    def setting(self) -> PipelineSetting:
        from ..find_jobs.refresh_tick import SETTINGS_FILENAME

        try:
            known = self._path().parent.parent / SETTINGS_FILENAME
        except Exception:  # noqa: BLE001 - no bound project yet: the reader answers the defaults
            known = None
        return pipeline_setting(self.home_root, self.target, environ=self._environ, path=known)

    def _path(self) -> Path:
        """The project's pipeline file; the project is resolved once per runner, not at every look."""

        if self._resolved_path is None:
            self._resolved_path = pipeline_path(self.home_root, self.target)
        return self._resolved_path

    def _open(self) -> PipelineStore:
        return PipelineStore(self._path(), clock=self._clock, **self._store_options)  # type: ignore[arg-type]

    def _day(self) -> str:
        return self._now().date().isoformat()

    def _next_day(self) -> float:
        """The store clock's time at the next local midnight."""

        moment = self._now()
        midnight = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return self._clock() + (midnight - moment).total_seconds()

    # -- one drain ---------------------------------------------------------------------------

    def drain(
        self,
        *,
        only: tuple[str, str] | None = None,
        max_steps: int | None = None,
        stop: threading.Event | None = None,
        force_enabled: bool = False,
    ) -> DrainResult:
        """Claim and run steps until nothing can be claimed now; returns what was done.

        ``only`` limits the drain to one ``(profile_id, job)``; ``max_steps``
        to that many claims. ``force_enabled`` runs with the pipeline switched
        off (``pipeline process <job>``: the user asked for this one), never
        with settings that cannot be read.
        """

        setting = self.setting()
        if not setting.enabled and not (force_enabled and setting.source != SOURCE_UNREADABLE):
            self._last = DrainResult(DRAIN_DISABLED, setting.source)
            return self._last
        try:
            exists = self._path().is_file()
        except Exception:  # noqa: BLE001 - no bound project yet: there is no queue to look at
            exists = False
        if not exists:
            self._last = DrainResult(DRAIN_IDLE)
            return self._last  # nothing was ever enqueued: a look never creates the file
        stop = stop if stop is not None else threading.Event()
        store = self._open()
        shared = _Shared(max_steps)
        renew_stop = threading.Event()
        renewer: threading.Thread | None = None
        try:
            counts = store.counts()
            if not any(counts.get(state) for state in (STATE_READY, STATE_BLOCKED, STATE_RUNNING)):
                self._last = shared.result
                return self._last  # an idle look reads; it writes nothing
            if counts.get(STATE_RUNNING):
                store.reclaim()  # a holder that died gives its step back now, not when its lease runs out
            ctx = steps_module.StepContext(self.home_root, self.target, self._config, setting)
            renewer = threading.Thread(target=self._renew, args=(store, shared, renew_stop), name="scout-pipeline-renew", daemon=True)
            renewer.start()
            pool = [
                threading.Thread(target=self._work, args=(ctx, store, shared, stop, only, f"w{index}"), name=f"scout-pipeline-w{index}", daemon=True)
                for index in range(self._workers)
            ]
            for thread in pool:
                thread.start()
            for thread in pool:
                thread.join()
        finally:
            renew_stop.set()
            if renewer is not None:
                renewer.join()
            store.close()
        result = shared.result
        if stop.is_set():
            result.state = DRAIN_STOPPED
        elif shared.busy_reason is not None:
            result.state, result.reason = DRAIN_YIELDED, shared.busy_reason
        elif result.steps:
            result.state = DRAIN_RAN
        self._last = result
        return result

    def _live(self, shared: _Shared) -> str | None:
        """What is live, read at most every few seconds per drain."""

        with shared.changed:
            read_at = shared.busy_read_at
            if read_at is not None and time.monotonic() - read_at < _BUSY_TTL_SECONDS and shared.busy_reason is None:
                return None
        try:
            reason = self._busy()
        except Exception as exc:  # noqa: BLE001 - what is live cannot be read: the runner waits, it does not guess
            self._logger.warning("pipeline: what is live could not be read (%s); nothing is claimed", type(exc).__name__)
            reason = "live_work_unreadable"
        with shared.changed:
            shared.busy_reason, shared.busy_read_at = reason, time.monotonic()
        return reason

    def _work(
        self, ctx: object, store: PipelineStore, shared: _Shared, stop: threading.Event, only: tuple[str, str] | None, worker: str
    ) -> None:
        # ``holding``: this worker ran a step and has not yet looked for the steps it opened. While any worker
        # holds, an idle one waits instead of leaving, so what a finished step opens is run side by side.
        holding = False
        try:
            while not stop.is_set():
                if self._live(shared) is not None:
                    return
                with shared.changed:
                    if shared.max_steps is not None and shared.claimed >= shared.max_steps:
                        return
                    shared.claimed += 1
                claim = None
                try:
                    claim = store.claim(worker=worker, only=only)
                except Exception as exc:  # noqa: BLE001 - a claim that fails (a locked file) is one missed look, logged
                    self._logger.warning("pipeline: worker %s could not claim (%s)", worker, type(exc).__name__)
                if claim is None:
                    with shared.changed:
                        shared.claimed -= 1
                        if holding:
                            holding = False
                            shared.running -= 1
                            shared.changed.notify_all()
                        if shared.running == 0:
                            return  # nobody is running a step that could open another
                        shared.changed.wait(_IDLE_WAIT_SECONDS)
                    continue
                with shared.changed:
                    if not holding:
                        holding = True
                        shared.running += 1
                try:
                    self._execute(ctx, store, claim, shared)
                except Exception as exc:  # noqa: BLE001 - one step's bookkeeping failed: logged; its lease is reclaimed like a dead holder's
                    self._logger.warning("pipeline: worker %s hit %s", worker, type(exc).__name__)
        finally:
            with shared.changed:
                if holding:
                    shared.running -= 1
                shared.changed.notify_all()

    def _renew(self, store: PipelineStore, shared: _Shared, stop: threading.Event) -> None:
        """Extend the lease of every step this drain is running, until the drain ends."""

        while not stop.wait(self._renew_seconds):
            with shared.changed:
                claims = list(shared.inflight.values())
            for claim in claims:
                try:
                    store.renew(claim)
                except Exception as exc:  # noqa: BLE001 - a renew that fails is tried again; the lease is ten minutes
                    self._logger.debug("pipeline: a lease was not renewed (%s)", type(exc).__name__)

    def _execute(self, ctx: object, store: PipelineStore, claim: Claim, shared: _Shared) -> None:
        setting: PipelineSetting = ctx.setting  # type: ignore[attr-defined]
        key = (claim.profile_id, claim.job, claim.name)
        entry: dict[str, object] = {"profile_id": claim.profile_id, "job": claim.job, "name": claim.name, "lane": claim.lane}

        def note(outcome: str, **more: object) -> None:
            entry.update(outcome=outcome, **more)
            with shared.changed:
                shared.result.steps.append(entry)

        try:
            digest = self._steps.input_digest(ctx, store, claim)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - any failure to read a step's inputs fails the step with a bounded code
            code = steps_module.error_code(exc)
            note(store.fail(claim, code), error_code=code)
            return
        if self._steps.unchanged(ctx, store, claim, digest):  # type: ignore[attr-defined]
            state = store.finish(claim, input_digest=digest)
            note(OUTCOME_UNCHANGED if state == OUTCOME_DONE else state)
            return
        reserved: str | None = None
        if claim.lane != LOCAL_LANE:
            day = self._day()
            if not store.spend(CAP_PIPELINE_CALLS, day, 1, limit=setting.max_model_calls_per_day):
                state = store.defer(claim, not_before=self._next_day(), code=WAIT_DAILY_CAP)
                note(OUTCOME_WAITING if state != LOST_LEASE else state, waiting=WAIT_DAILY_CAP)
                return
            reserved = day
        with shared.changed:
            shared.inflight[key] = claim
        failure: BaseException | None = None
        result = None
        try:
            with capture_calls() as calls:
                try:
                    result = self._steps.run_step(ctx, store, claim)  # type: ignore[attr-defined]
                except Exception as exc:  # noqa: BLE001 - a step may raise anything; it is stored as a bounded code, never its message
                    failure = exc
        finally:
            with shared.changed:
                shared.inflight.pop(key, None)
        made = len(calls)
        if reserved is not None:
            if made == 0:
                store.refund(CAP_PIPELINE_CALLS, reserved, 1)  # it failed before its call
            elif made > 1:
                store.spend(CAP_PIPELINE_CALLS, reserved, made - 1)  # a retry inside the step: counted, never refused mid-step
        metrics = total_metrics(calls)
        with shared.changed:
            shared.result.model_calls += made
        if failure is not None:
            code = steps_module.error_code(failure)
            note(store.fail(claim, code, metrics=metrics), error_code=code, model_calls=made)
            return
        state = store.finish(
            claim, input_digest=digest, output_ref=result.output_ref, output_digest=result.output_digest, metrics=metrics  # type: ignore[union-attr]
        )
        # ``code``: how a done step ended when not as usual (``tailor_kept_user_edits``: the stored resume is the user's).
        code = getattr(result, "code", None)
        note(state, model_calls=made, **({"code": code} if code else {}))

    # -- status ------------------------------------------------------------------------------

    def status(self) -> dict[str, object]:
        """This runner's own state for a status block: whether its thread runs and what its last drain did."""

        return {
            "active": self.alive and not self.stopping,
            "last": None if self._last is None else {"state": self._last.state, "reason": self._last.reason, "steps": len(self._last.steps)},
            "rank": None if self._last_rank is None else {
                key: self._last_rank[key] for key in ("state", "reason", "calls", "ranked", "warning")
            },
        }


# --- status (read only) -----------------------------------------------------------------------


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def pipeline_status(
    home_root: Path,
    target: Path | None,
    *,
    profile_id: str | None = None,
    job: str | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.time,
    now: Callable[[], datetime] | None = None,
    busy: Callable[[], str | None] | None = None,
) -> dict[str, object]:
    """What the pipeline is doing: the settings, the steps and why a step waits. Reads only; never creates the file.

    Ids, codes, numbers and paths: no posting, resume or answer text. With
    ``job`` (and its ``profile_id``) it also names that job's stored outputs
    (``steps.job_outputs``).
    """

    home_root = Path(home_root)
    setting = pipeline_setting(home_root, target, environ=environ)
    moment = (now if now is not None else (lambda: datetime.now().astimezone()))()
    day = moment.date().isoformat()
    answer: dict[str, object] = {
        "schema_version": STATUS_SCHEMA,
        "setting": setting.to_json(),
        "counts": {},
        "calls_today": {"day": day, "used": 0, "limit": setting.max_model_calls_per_day},
        "lanes": [],
        "yielding_to": None,
        "steps": [],
    }
    if target is None:
        return answer
    target = Path(target)
    try:
        answer["yielding_to"] = (busy if busy is not None else (lambda: live_work(home_root, target)))()
    except Exception:  # noqa: BLE001 - status never fails on what it cannot read; the runner itself then waits
        answer["yielding_to"] = "live_work_unreadable"
    path = pipeline_path(home_root, target)
    if path.is_file():
        store = PipelineStore(path, clock=clock)
        try:
            current = clock()
            backoffs = {item.lane: item for item in store.lane_backoffs() if item.not_before > current}
            used = store.used(CAP_PIPELINE_CALLS, day)
            answer["counts"] = store.counts()
            answer["calls_today"] = {"day": day, "used": used, "limit": setting.max_model_calls_per_day}
            answer["lanes"] = [
                {"lane": item.lane, "error_code": item.error_code, "retry_at": _iso(item.not_before)} for item in backoffs.values()
            ]
            rows = []
            for step in store.steps(profile_id=profile_id, job=job):
                waiting = None
                if step.state == STATE_READY:
                    if step.not_before > current and step.error_code == WAIT_DAILY_CAP:
                        waiting = WAIT_DAILY_CAP
                    elif step.lane in backoffs:
                        waiting = WAIT_LANE
                    elif step.not_before > current:
                        waiting = WAIT_RETRY
                rows.append(
                    {
                        "profile_id": step.profile_id, "job": step.job, "name": step.name, "state": step.state, "lane": step.lane,
                        "model_target": step.model_target, "attempts": step.attempts, "error_code": step.error_code,
                        "waiting": waiting, "retry_at": _iso(step.not_before) if waiting is not None and step.not_before > current else None,
                        "updated_at": step.updated_at,
                    }
                )
            answer["steps"] = rows
            if job is not None and profile_id is not None:
                answer["runs"] = [
                    {
                        "name": run.name, "attempt": run.attempt, "outcome": run.outcome, "error_code": run.error_code, "lane": run.lane,
                        "model_target": run.adapter, "model": run.model, "input_tokens": run.input_tokens,
                        "output_tokens": run.output_tokens, "cached_tokens": run.cached_tokens, "cost_usd": run.cost_usd,
                        "seconds": round(run.seconds, 3), "started_at": run.started_at,
                    }
                    for run in store.runs(profile_id=profile_id, job=job)
                ]
        finally:
            store.close()
    if job is not None and profile_id is not None:
        answer["outputs"] = steps_module.job_outputs(home_root, target, profile_id, job)
    return answer


def run_once(
    home_root: Path, target: Path, *, max_steps: int | None = None, only: tuple[str, str] | None = None, force_enabled: bool = False,
    config: object | None = None, environ: Mapping[str, str] | None = None,
) -> DrainResult:
    """One drain on the calling thread: ``gigai scout pipeline run --once`` and ``pipeline process <job>``."""

    runner = PipelineRunner(home_root=home_root, target=target, config=config, environ=environ)
    return runner.drain(only=only, max_steps=max_steps, force_enabled=force_enabled)


__all__ = [
    "BUSY_ASSESS_BATCH",
    "BUSY_FIND_JOBS_RUN",
    "BUSY_SOURCES_UPDATE",
    "DEFAULT_WORKERS",
    "DRAIN_DISABLED",
    "DRAIN_IDLE",
    "DRAIN_RAN",
    "DRAIN_STOPPED",
    "DRAIN_YIELDED",
    "POLL_SECONDS",
    "WAIT_DAILY_CAP",
    "WAIT_LANE",
    "WAIT_RETRY",
    "DrainResult",
    "PipelineRunner",
    "live_work",
    "pipeline_status",
    "run_once",
]
