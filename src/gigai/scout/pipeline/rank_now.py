"""0.1.11.2 RANKUI: ranking the user asks for from the Jobs page ("Rank now", "Re-rank latest 100").

The background rank lane (``rank_lane.py``) ranks by itself. This is the
same lane, started by a click, so a user whose postings are not ranked can
see that and fix it from the page:

- ``unranked`` ("Rank now"): the postings of the active profiles that went
  up in the lane's window (the last 7 days) and have no rank score. It is
  ``rank_lane.rank_tick``, turn after turn (:data:`TURN_CALLS` calls a turn,
  so the rows get their scores, and the page its "ranked X of Y", after each
  turn) until nothing is left, the day's calls are used up, or the lane
  yields.
- ``latest`` ("Re-rank latest 100"): the newest :data:`RERANK_POSTINGS`
  postings of the window are ranked AGAIN, whether they have a score or not
  (``model_rank.rank_postings(use_cache=False)``), in at most
  :data:`RERANK_MAX_CALLS` calls of 50. With several profiles a call ranks
  one profile's postings, so the two calls hold the newest postings that fit
  in them. All or nothing against the day's allowance: when the calls it
  needs are more than the day has left, none is made.

NOTHING HERE CALLS A MODEL WITHOUT A YES. :func:`plan` is the ask: how many
postings and how many calls (the cost), the day's counter, whether it may
run and why not. :func:`start` is the yes: it starts ONE job per project in
this process (a second yes while it runs joins it) and returns at once; the
job is read with :func:`job_status`.

THE SAME RULES AS THE LANE. Every call is taken from the one daily counter
(``triggers.spend_rank_calls``: 100 a day, shared by every profile) before
it is made; the ``rank`` lease is held while ranking, so a click and the
background lane never rank the same batch twice (the click then ends
``busy_elsewhere``: the lane is already doing it); live work (a sources
update, an assess batch, a run) is yielded to; and the off switch is
respected: with ``rank.enabled`` off, or settings that cannot be read,
nothing is ranked and the answer says so (:data:`HOW_TO_ENABLE`).

Ids, counts and codes only in what this returns: no posting or resume text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import logging
import threading
import uuid

from . import rank_lane
from .settings import SOURCE_UNREADABLE, PipelineSetting, pipeline_setting
from .store import LEASE_RANK, PipelineStore
from .triggers import rank_calls_today, refund_rank_calls, spend_rank_calls

SCHEMA_VERSION = "scout-rank-now:1"

MODE_UNRANKED = "unranked"
MODE_LATEST = "latest"
MODES = (MODE_UNRANKED, MODE_LATEST)

#: "Re-rank latest 100": how many postings, and the most calls it may make (50 postings a call).
RERANK_POSTINGS = 100
RERANK_MAX_CALLS = 2
#: "Rank now": calls per lane turn. The rows get their scores when a turn ends, so this is how often the page moves.
TURN_CALLS = 2

REFUSAL_DISABLED = "rank_disabled"
REFUSAL_DAILY_CAP = "rank_daily_cap"
REFUSAL_NOTHING = "nothing_to_rank"

JOB_RUNNING = "running"
JOB_DONE = "done"

#: What the page and the API say when ranking is off: the one way to turn it on.
HOW_TO_ENABLE = (
    'Ranking is off. To turn it on, set "rank": {"enabled": true} in the Scout settings of this project '
    "(PUT /api/settings/background, or the settings file); `gigai scout pipeline status` says what switched it off."
)

_logger = logging.getLogger("gigai.scout.server")


class RankNowError(Exception):
    """A rank request that cannot be answered; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _enabled(setting: PipelineSetting) -> bool:
    return setting.rank_enabled and setting.rank_source != SOURCE_UNREADABLE and setting.rank_max_calls_per_day > 0


def _calls(postings: int, size: int) -> int:
    return -(-postings // size) if postings > 0 else 0


def _latest_batches(store: PipelineStore, views: object, since: str, *, size: int) -> list[tuple[object, list[object]]]:
    """The newest :data:`RERANK_POSTINGS` in-window rows of the active profiles as at most :data:`RERANK_MAX_CALLS` batches.

    ``[(profile view, its rows)]``, each of ``size`` rows or fewer, the batch with the newest posting first.
    """

    from ..scout_new import batch_date

    found: list[tuple[str, str, int, object]] = []
    for place, view in enumerate(views):  # type: ignore[arg-type,var-annotated]
        for row in store.postings(profile_id=view.profile_id):
            when = batch_date(row)
            if when > since:
                found.append((when, row.job, place, row))
    found.sort(key=lambda item: (item[1], item[2]))
    found.sort(key=lambda item: item[0], reverse=True)  # the newest first; equal dates keep the URL order
    per_profile: dict[int, list[tuple[int, object]]] = {}
    for index, (_when, _job, place, row) in enumerate(found[:RERANK_POSTINGS]):
        per_profile.setdefault(place, []).append((index, row))
    ordered = list(views)  # type: ignore[call-overload]
    batches: list[tuple[int, object, list[object]]] = []
    for place, items in per_profile.items():
        for start in range(0, len(items), size):
            chunk = items[start:start + size]
            batches.append((chunk[0][0], ordered[place], [row for _index, row in chunk]))
    batches.sort(key=lambda item: item[0])  # by the newest posting each holds
    return [(view, rows) for _index, view, rows in batches[:RERANK_MAX_CALLS]]


def _plan(
    mode: str, store: PipelineStore, views: object, setting: PipelineSetting, moment: datetime, *, size: int
) -> dict[str, object]:
    day = moment.date().isoformat()
    today = rank_calls_today(store, setting, day)
    left = max(0, int(today["limit"]) - int(today["used"]))  # type: ignore[call-overload]
    since = rank_lane._window_start(moment)
    by_profile: list[dict[str, object]] = []
    if mode == MODE_LATEST:
        counted: dict[str, list[int]] = {}
        for view, rows in _latest_batches(store, views, since, size=size):
            entry = counted.setdefault(view.profile_id, [0, 0])  # type: ignore[attr-defined]
            entry[0] += len(rows)
            entry[1] += 1
        by_profile = [{"profile_id": profile_id, "postings": found[0], "calls": found[1]} for profile_id, found in counted.items()]
    else:
        for view in views:  # type: ignore[attr-defined]
            unranked = len(rank_lane._unranked(store, view.profile_id, since))
            if unranked:
                by_profile.append({"profile_id": view.profile_id, "postings": unranked, "calls": _calls(unranked, size)})
    postings_count = sum(int(item["postings"]) for item in by_profile)  # type: ignore[call-overload]
    calls = sum(int(item["calls"]) for item in by_profile)  # type: ignore[call-overload]
    refusal: str | None = None
    if not _enabled(setting):
        refusal = REFUSAL_DISABLED
    elif postings_count == 0:
        refusal = REFUSAL_NOTHING
    elif (calls > left) if mode == MODE_LATEST else (left == 0):
        refusal = REFUSAL_DAILY_CAP  # re-rank is all or nothing; "Rank now" ranks what the day has left
    return {
        "mode": mode, "postings": postings_count, "calls": calls,
        "max_calls": RERANK_MAX_CALLS if mode == MODE_LATEST else None, "batch_size": size,
        "calls_left_today": left, "allowed": refusal is None, "refusal": refusal, "by_profile": by_profile,
        "window_days": _window_days(),
    }


def _window_days() -> int:
    from ..scout_new import FIRST_USE_DAYS

    return FIRST_USE_DAYS


def _batch_size() -> int:
    from ..find_jobs.model_rank import DEFAULT_BATCH_SIZE

    return DEFAULT_BATCH_SIZE


def status(
    home_root: Path, target: Path, *, mode: str | None = None, now: Callable[[], datetime] | None = None,
    model_wait: float | None = None,
) -> dict[str, object]:
    """What the page reads, and the ask: the switch, the day's counter, how far the rank is, the job, and ``plan`` for ``mode``.

    No model call, and nothing is started. ``mode`` ``None``: no ``plan`` (the page reading its state).
    """

    from .. import postings
    from ..scout_new import _ranking

    if mode is not None and mode not in MODES:
        raise RankNowError("invalid_value", f"mode must be one of: {', '.join(MODES)}")
    home_root, target = Path(home_root), Path(target)
    moment = (now or _local_now)()
    setting = pipeline_setting(home_root, target)
    store = postings.open_store(home_root, target)
    try:
        try:
            refreshed = postings.refresh(home_root, target, store=store, now=moment, wait=model_wait)
        except postings.PostingModelPreparing:
            refreshed = postings.refresh(home_root, target, store=store, now=moment)  # a POST waits for the first build
        enabled = _enabled(setting)
        return {
            "schema_version": SCHEMA_VERSION,
            "enabled": enabled,
            "source": setting.rank_source,
            "how_to_enable": None if enabled else HOW_TO_ENABLE,
            "calls_today": rank_calls_today(store, setting, moment.date().isoformat()),
            "ranking": _ranking(store, refreshed.profiles, home_root, target, now=moment),
            "plan": None if mode is None else _plan(mode, store, refreshed.profiles, setting, moment, size=_batch_size()),
            "job": job_status(home_root, target),
        }
    finally:
        store.close()


# --- "Re-rank latest 100": one turn ---------------------------------------------------------


def rerank_latest(
    home_root: Path,
    target: Path,
    *,
    config: object | None = None,
    busy: Callable[[], str | None] | None = None,
    now: Callable[[], datetime] | None = None,
    batch_size: int | None = None,
) -> dict[str, object]:
    """Rank the newest :data:`RERANK_POSTINGS` in-window postings again, in at most :data:`RERANK_MAX_CALLS` calls.

    The answer has the lane's keys (``rank_lane.rank_tick``): ``state``, ``reason``, ``calls``, ``ranked``,
    ``calls_today``. All or nothing against the day's allowance: ``waiting`` / ``daily_cap_reached`` with 0 calls
    when the calls it needs are more than the day has left.
    """

    from .. import postings
    from ..find_jobs.model_rank import rank_postings
    from ..quick_assess import _default_model_target
    from .runner import live_work

    home_root, target = Path(home_root), Path(target)
    clock = now or _local_now
    started = clock()
    day = started.date().isoformat()
    setting = pipeline_setting(home_root, target)
    size = batch_size if batch_size is not None else _batch_size()
    answer: dict[str, object] = {
        "schema_version": rank_lane.SCHEMA_VERSION, "state": rank_lane.STATE_IDLE, "reason": None, "calls": 0, "ranked": 0,
        "warning": False, "retry_at": None, "profiles": [], "calls_today": rank_calls_today(None, setting, day),
    }

    def done(state: str, reason: str | None = None) -> dict[str, object]:
        answer.update(state=state, reason=reason)
        return answer

    if not _enabled(setting):
        return done(rank_lane.STATE_DISABLED, rank_lane.REASON_CAP_ZERO if setting.rank_enabled and setting.rank_max_calls_per_day <= 0 else setting.rank_source)
    try:
        reason = busy() if busy is not None else live_work(home_root, target)
    except Exception:  # noqa: BLE001 - what is live cannot be read: wait, do not guess
        reason = "live_work_unreadable"
    if reason is not None:
        return done(rank_lane.STATE_YIELDED, reason)
    try:
        store = postings.open_store(home_root, target)
    except Exception:  # noqa: BLE001 - no bound project: there is nothing to rank
        return done(rank_lane.STATE_UNAVAILABLE, "target_unavailable")
    held = False
    holder = f"n{uuid.uuid4().hex[:12]}"
    try:
        held = store.take_lease(LEASE_RANK, worker=holder)
        answer["calls_today"] = rank_calls_today(store, setting, day)
        if not held:
            return done(rank_lane.STATE_BUSY_ELSEWHERE)
        try:
            refreshed = postings.refresh(home_root, target, store=store, now=clock())
        except postings.PostingModelError as exc:
            return done(rank_lane.STATE_UNAVAILABLE, exc.code)
        batches = _latest_batches(store, refreshed.profiles, rank_lane._window_start(started), size=size)
        today = answer["calls_today"]
        assert isinstance(today, Mapping)
        if len(batches) > int(today["limit"]) - int(today["used"]):  # type: ignore[call-overload]
            answer["retry_at"] = rank_lane._next_day(clock())
            return done(rank_lane.STATE_WAITING, rank_lane.WAIT_DAILY_CAP)  # all or nothing: no call is made
        model_target = _default_model_target(target).value
        state, why = rank_lane.STATE_IDLE, None
        calls = ranked = 0
        profiles: dict[str, dict[str, object]] = {}
        for view, records in batches:
            call = spend_rank_calls(home_root, target, 1, setting=setting, store=store, now=started)
            if not call["allowed"]:
                state, why = rank_lane.STATE_WAITING, rank_lane.WAIT_DAILY_CAP
                answer["retry_at"] = rank_lane._next_day(clock())
                break
            jobs = {record.job for record in records}  # type: ignore[attr-defined]
            try:
                rows = [row for row in postings.posting_rows(home_root, records) if row.normalized_url in jobs]  # type: ignore[attr-defined,arg-type]
                resume_text, prefs = rank_lane._candidate(view, home_root=home_root, target=target, resolved=refreshed.resolved)
                result = rank_postings(
                    rows, resume_text=resume_text, prefs=prefs, model_target=model_target, home_root=home_root,  # type: ignore[arg-type]
                    config=config, batch_size=max(1, len(rows)), concurrency=1, max_calls=1, target=target,  # type: ignore[arg-type]
                    profile_id=view.profile_id, use_cache=False,  # type: ignore[attr-defined]
                )
            except BaseException:  # noqa: BLE001 - re-raised: the call it reserved was never counted as made
                refund_rank_calls(home_root, target, 1, store=store, now=started)
                raise
            made = sum(item.attempts for item in result.batches if item.source == "model")
            if made == 0:
                refund_rank_calls(home_root, target, 1, store=store, now=started)
            else:
                answer["warning"] = bool(answer["warning"]) or bool(call["warning"])
            scored = sum(1 for item in result.postings if item.scored and not item.cached)
            calls += made
            ranked += scored
            entry = profiles.setdefault(view.profile_id, {"profile_id": view.profile_id, "postings": 0, "ranked": 0, "calls": 0})  # type: ignore[attr-defined]
            entry.update(
                postings=int(entry["postings"]) + len(rows), ranked=int(entry["ranked"]) + scored, calls=int(entry["calls"]) + made,  # type: ignore[call-overload]
            )
            store.renew_lease(LEASE_RANK, worker=holder)
            if result.status != "complete":
                unavailable = str(result.fail_open_reason or "").startswith(("model_target_unavailable", "model_unavailable"))
                state, why = rank_lane.STATE_UNAVAILABLE, rank_lane.REASON_MODEL if unavailable else rank_lane.REASON_INVALID
                break
        if calls:
            postings.refresh(home_root, target, store=store, now=clock())  # the rows get the scores just stored
        if state == rank_lane.STATE_IDLE and calls:
            state = rank_lane.STATE_RAN
        answer.update(calls=calls, ranked=ranked, profiles=list(profiles.values()), calls_today=rank_calls_today(store, setting, day))
        return done(state, why)
    finally:
        if held:
            store.release_lease(LEASE_RANK, worker=holder)
        store.close()


# --- the job: one per project in this process ------------------------------------------------


@dataclass
class _Job:
    job_id: str
    mode: str
    postings: int
    planned_calls: int
    started_at: str
    state: str = JOB_RUNNING
    finished_at: str | None = None
    calls: int = 0
    ranked: int = 0
    outcome: str | None = None  # the lane's last state: ran, idle, waiting, yielded, busy_elsewhere, unavailable, disabled
    reason: str | None = None
    retry_at: str | None = None
    warning: bool = False
    thread: threading.Thread | None = field(default=None, repr=False)

    def to_json(self) -> dict[str, object]:
        return {
            "job_id": self.job_id, "mode": self.mode, "state": self.state, "postings": self.postings,
            "planned_calls": self.planned_calls, "calls": self.calls, "ranked": self.ranked, "outcome": self.outcome,
            "reason": self.reason, "retry_at": self.retry_at, "warning": self.warning, "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_LOCK = threading.Lock()
_JOBS: dict[tuple[str, str], _Job] = {}


def _key(home_root: Path, target: Path) -> tuple[str, str]:
    return str(Path(home_root).resolve()), str(Path(target).resolve())


def job_status(home_root: Path, target: Path) -> dict[str, object] | None:
    """The project's rank job in this process (running, or the last one that finished); ``None`` when none was started."""

    with _LOCK:
        job = _JOBS.get(_key(home_root, target))
        return None if job is None else job.to_json()


def _run(job: _Job, home_root: Path, target: Path, config: object | None, now: Callable[[], datetime] | None) -> None:
    clock = now or _local_now
    try:
        if job.mode == MODE_LATEST:
            result = rerank_latest(home_root, target, config=config, now=now)
            with _LOCK:
                job.calls, job.ranked = int(result["calls"]), int(result["ranked"])  # type: ignore[call-overload]
        else:
            # Turn after turn: each turn ends with the rows refreshed, so the page's "ranked X of Y" moves.
            turns = 0
            while True:
                result = rank_lane.rank_tick(home_root, target, config=config, now=now, max_calls=TURN_CALLS)
                turns += 1
                with _LOCK:
                    job.calls += int(result["calls"])  # type: ignore[call-overload]
                    job.ranked += int(result["ranked"])  # type: ignore[call-overload]
                    job.warning = job.warning or bool(result["warning"])
                more = result["state"] == rank_lane.STATE_RAN and int(result["calls"]) >= TURN_CALLS  # type: ignore[call-overload]
                if not more or turns > job.planned_calls + 2:
                    break
        outcome = str(result["state"])
        if outcome == rank_lane.STATE_IDLE and job.calls:
            outcome = rank_lane.STATE_RAN  # the last turn found nothing left: the job ranked
        with _LOCK:
            job.outcome, job.reason = outcome, None if result["reason"] is None else str(result["reason"])
            job.retry_at = None if result["retry_at"] is None else str(result["retry_at"])
            job.warning = job.warning or bool(result["warning"])
    except Exception as exc:  # noqa: BLE001 - a thread's last boundary: the type only, a message may quote a record
        _logger.warning("rank now (%s): %s", job.mode, type(exc).__name__)
        with _LOCK:
            job.outcome, job.reason = rank_lane.STATE_UNAVAILABLE, type(exc).__name__
    finally:
        with _LOCK:
            job.state, job.finished_at = JOB_DONE, clock().isoformat(timespec="seconds")
        if job.calls:
            _logger.info("rank now (%s): ranked %d posting(s) in %d call(s), %s", job.mode, job.ranked, job.calls, job.outcome)


def start(
    home_root: Path, target: Path, mode: str, *, config: object | None = None, now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """The yes: start the job for ``mode`` (or join the one running) and answer at once, as :func:`status` plus ``started``.

    Raises :class:`RankNowError` (``rank_disabled``, ``rank_daily_cap``) when it may not run: nothing is started and no
    model call is made. With nothing to rank it starts nothing and says so (``started`` false, ``plan.refusal``).
    """

    home_root, target = Path(home_root), Path(target)
    answer = status(home_root, target, mode=mode, now=now)
    running = answer["job"]
    if isinstance(running, Mapping) and running["state"] == JOB_RUNNING:
        return {**answer, "started": False}  # single flight: the click joins the job that runs
    plan = answer["plan"]
    assert isinstance(plan, Mapping)
    if plan["refusal"] == REFUSAL_DISABLED:
        raise RankNowError(REFUSAL_DISABLED, HOW_TO_ENABLE)
    if plan["refusal"] == REFUSAL_DAILY_CAP:
        today = answer["calls_today"]
        assert isinstance(today, Mapping)
        raise RankNowError(
            REFUSAL_DAILY_CAP,
            f"Today's rank calls do not cover this: it needs {plan['calls']}, {plan['calls_left_today']} of {today['limit']} are left. "
            "Nothing was ranked; the count starts again tomorrow.",
        )
    if plan["refusal"] is not None:
        return {**answer, "started": False}
    clock = now or _local_now
    with _LOCK:
        current = _JOBS.get(_key(home_root, target))
        if current is not None and current.state == JOB_RUNNING:
            return {**answer, "job": current.to_json(), "started": False}
        job = _Job(
            job_id=f"rank_{uuid.uuid4().hex[:12]}", mode=mode, postings=int(plan["postings"]), planned_calls=int(plan["calls"]),
            started_at=clock().isoformat(timespec="seconds"),
        )
        job.thread = threading.Thread(target=_run, args=(job, home_root, target, config, now), name="scout-rank-now", daemon=True)
        _JOBS[_key(home_root, target)] = job
        started = job.to_json()
    job.thread.start()
    return {**answer, "job": started, "started": True}


def wait_for_jobs(*, timeout: float | None = None) -> bool:
    """Wait until no rank job of this process runs; false when ``timeout`` ran out first (tests, and a server that stops)."""

    with _LOCK:
        threads = [job.thread for job in _JOBS.values() if job.thread is not None]
    for thread in threads:
        thread.join(timeout)
    return not any(thread.is_alive() for thread in threads)


__all__ = [
    "HOW_TO_ENABLE",
    "JOB_DONE",
    "JOB_RUNNING",
    "MODES",
    "MODE_LATEST",
    "MODE_UNRANKED",
    "REFUSAL_DAILY_CAP",
    "REFUSAL_DISABLED",
    "REFUSAL_NOTHING",
    "RERANK_MAX_CALLS",
    "RERANK_POSTINGS",
    "SCHEMA_VERSION",
    "RankNowError",
    "job_status",
    "rerank_latest",
    "start",
    "status",
    "wait_for_jobs",
]
