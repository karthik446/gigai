"""0.1.10.7 M4a (DESIGN 10.6, 10.7): the background rank lane.

What a find-jobs run's rank step did, without a run: the postings of each
ACTIVE profile's demand set (the read model's rows, ``scout/postings.py``)
that have no score in the home's rank score cache get one, newest first, in
batches of 50 (``model_rank.DEFAULT_BATCH_SIZE``). The cache is the one a run
used, keyed by posting content, resume digest, prefs and model: a posting
ranked once is never ranked again until one of those changes, and two
profiles never share or overwrite a score.

WHO RUNS IT. The Scout server's pipeline thread, after each drain
(``PipelineRunner``), a few calls at a time; and ``gigai scout pipeline
rank`` on the calling thread for a user with no server. Both hold the
``rank`` lease of the project's ``pipeline.sqlite`` while they rank, so a
server and a CLI running together never rank the same batch twice.

NEVER an archived or deleted profile: only ``postings.active_profiles``.
The ``ephemeral`` pseudo-profile has no rows in the read model, so it is
never ranked either.

YIELDS like the pipeline's other lanes: no rank call starts while a manual
sources update, an assess batch or a find-jobs run is live
(``runner.live_work``); a call already running finishes.

THE DAILY COUNTERS are shared by every profile of the install
(``cap_counter`` ``rank_calls``, one count per local day):
``rank.max_calls_per_day`` (100) and ``rank.warn_calls_per_day`` (60). Each
model call takes one from the day's allowance BEFORE it is made
(``triggers.spend_rank_calls``, the one counter of the install). A call past the warning level is made and flagged
(``warning``); the call that would go over the maximum is not made: the lane
waits until the next local day (``daily_cap_reached``). A reserved call that
was never made (everything was cached, or no model) is given back.

OFF when the pipeline is off (``pipeline.enabled``, the environment, a
settings file that cannot be read) or ``rank.max_calls_per_day`` is 0.

METRICS: every call is recorded by ``call_metrics`` (kind ``rank``, with the
profile), by ``model_rank.rank_postings`` itself.

Ids, counts and codes only in what this returns: no posting or resume text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import os
from pathlib import Path
import threading
import uuid

from .settings import SOURCE_UNREADABLE, pipeline_setting
from .store import LEASE_RANK, PipelineStore, pipeline_path
from .triggers import rank_calls_today, refund_rank_calls, spend_rank_calls

SCHEMA_VERSION = "scout-rank-lane:1"

STATE_DISABLED = "disabled"
STATE_IDLE = "idle"  # nothing to rank
STATE_RAN = "ran"
STATE_YIELDED = "yielded"
STATE_WAITING = "waiting"  # the day's rank calls are used up
STATE_BUSY_ELSEWHERE = "busy_elsewhere"  # another process holds the rank lease
STATE_UNAVAILABLE = "unavailable"  # no gig, no config, or no usable model

WAIT_DAILY_CAP = "daily_cap_reached"
REASON_CAP_ZERO = "rank_cap_zero"
REASON_STOPPED = "stopped"
REASON_MODEL = "model_target_unavailable"
REASON_INVALID = "model_output_invalid"
REASON_CACHE_MISMATCH = "rank_cache_mismatch"


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _next_day(moment: datetime) -> str:
    midnight = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat(timespec="seconds")


def rank_status(
    home_root: Path, target: Path | None, *, environ: Mapping[str, str] | None = None, now: Callable[[], datetime] | None = None
) -> dict[str, object]:
    """The rank lane's setting and the day's counter. Reads only; never creates the pipeline file."""

    setting = pipeline_setting(Path(home_root), target, environ=environ)
    day = (now or _local_now)().date().isoformat()
    enabled = setting.enabled and setting.rank_max_calls_per_day > 0
    store: PipelineStore | None = None
    try:
        if target is not None:
            path = pipeline_path(Path(home_root), Path(target))
            if path.is_file():
                store = PipelineStore(path)
        return {"enabled": enabled, "calls_today": rank_calls_today(store, setting, day)}
    finally:
        if store is not None:
            store.close()


def _index_has_postings(home_root: Path) -> bool:
    """Whether the stored company index holds any company file. No file is opened."""

    try:
        with os.scandir(Path(home_root) / "cache" / "scout" / "companies") as entries:
            return any(entry.name.endswith(".json") and ":" in entry.name for entry in entries)
    except OSError:
        return False


def _candidate(view: object, *, home_root: Path, target: Path, resolved: object) -> tuple[str, object]:
    """``(resume text, prefs)`` of one profile, read once per turn: what ``rank_postings`` makes its candidate digest from."""

    from ..find_jobs.rank_run import rank_prefs
    from ..find_jobs.resume_input import resume_for_profile

    resume = resume_for_profile(view.record, resolved=resolved, home_root=home_root, target=target)  # type: ignore[attr-defined,arg-type]
    return resume.text, rank_prefs(view.config)  # type: ignore[attr-defined]


def _rank_batch(
    view: object, rows: list[object], candidate: tuple[str, object], *, home_root: Path, target: Path, model_target: str,
    config: object | None,
) -> object:
    """One model call for one batch of one profile's postings (``model_rank.rank_postings``, which writes the score cache)."""

    from ..find_jobs.model_rank import rank_postings

    return rank_postings(
        rows, resume_text=candidate[0], prefs=candidate[1], model_target=model_target, home_root=home_root,  # type: ignore[arg-type]
        config=config, batch_size=max(1, len(rows)), concurrency=1, max_calls=1, target=target,  # type: ignore[arg-type]
        profile_id=view.profile_id,  # type: ignore[attr-defined]
    )


def rank_tick(
    home_root: Path,
    target: Path,
    *,
    config: object | None = None,
    environ: Mapping[str, str] | None = None,
    busy: Callable[[], str | None] | None = None,
    now: Callable[[], datetime] | None = None,
    stop: threading.Event | None = None,
    max_calls: int | None = None,
    batch_size: int | None = None,
    force_enabled: bool = False,
) -> dict[str, object]:
    """Rank what the active profiles' demand sets have unranked, within the day's allowance; returns what was done.

    ``max_calls`` bounds this tick (the server ranks a few batches per look,
    so the other lanes get their turn); ``busy`` is what the lane yields to
    (``runner.live_work`` by default); ``force_enabled`` runs with the
    pipeline switched off (the user asked: ``gigai scout pipeline rank``),
    never with settings that cannot be read.
    """

    from .. import postings
    from ..find_jobs.model_rank import DEFAULT_BATCH_SIZE
    from ..quick_assess import _default_model_target
    from .runner import live_work

    home_root, target = Path(home_root), Path(target)
    clock = now or _local_now
    setting = pipeline_setting(home_root, target, environ=environ)
    started = clock()  # the day this turn counts for, also when it runs past midnight
    day = started.date().isoformat()
    answer: dict[str, object] = {
        "schema_version": SCHEMA_VERSION, "state": STATE_IDLE, "reason": None, "calls": 0, "ranked": 0, "warning": False,
        "retry_at": None, "profiles": [], "calls_today": rank_calls_today(None, setting, day),
    }

    def done(state: str, reason: str | None = None) -> dict[str, object]:
        answer.update(state=state, reason=reason)
        return answer

    if not setting.enabled and not (force_enabled and setting.source != SOURCE_UNREADABLE):
        return done(STATE_DISABLED, setting.source)
    if setting.rank_max_calls_per_day <= 0:
        return done(STATE_DISABLED, REASON_CAP_ZERO)
    is_busy = busy if busy is not None else (lambda: live_work(home_root, target))
    size = batch_size if batch_size is not None else DEFAULT_BATCH_SIZE
    if size < 1:
        raise ValueError("batch_size must be positive")

    def live() -> str | None:
        try:
            return is_busy()
        except Exception:  # noqa: BLE001 - what is live cannot be read: the lane waits, it does not guess
            return "live_work_unreadable"

    reason = live()
    if reason is not None:
        return done(STATE_YIELDED, reason)
    try:
        if not pipeline_path(home_root, target).is_file() and not _index_has_postings(home_root):
            return done(STATE_IDLE)  # nothing stored to rank, and no pipeline file yet: a look never creates it
        store = postings.open_store(home_root, target)
    except Exception:  # noqa: BLE001 - no bound project: there is nothing to rank
        return done(STATE_UNAVAILABLE, "target_unavailable")
    held = False
    holder = f"r{uuid.uuid4().hex[:12]}"  # one holder per turn, also among the threads of one process
    try:
        held = store.take_lease(LEASE_RANK, worker=holder)
        if not held:
            answer["calls_today"] = rank_calls_today(store, setting, day)
            return done(STATE_BUSY_ELSEWHERE)
        try:
            refreshed = postings.refresh(home_root, target, store=store, now=clock())
        except postings.PostingModelError as exc:
            return done(STATE_UNAVAILABLE, exc.code)
        model_target = _default_model_target(target).value
        state, why = STATE_IDLE, None
        profiles: list[dict[str, object]] = []
        calls = ranked = 0
        halted = False
        # Active profiles only: an archived or deleted profile is not in ``refreshed.profiles`` and has no rows.
        for view in refreshed.profiles:
            if halted:
                break
            unranked = {row.job: row for row in store.postings(profile_id=view.profile_id) if row.rank_score is None}
            entry: dict[str, object] = {"profile_id": view.profile_id, "unranked": len(unranked), "ranked": 0, "calls": 0}
            profiles.append(entry)
            if not unranked:
                continue
            # 0110-9-01: the boards the unranked rows are on, never the whole index again each turn.
            rows = [row for row in postings.posting_rows(home_root, unranked.values()) if row.normalized_url in unranked]  # type: ignore[attr-defined]
            rows.sort(key=lambda row: (unranked[row.normalized_url].first_seen, row.normalized_url), reverse=True)  # type: ignore[attr-defined]
            candidate: tuple[str, object] | None = None
            for start in range(0, len(rows), size):
                if stop is not None and stop.is_set():
                    state, why, halted = STATE_YIELDED, REASON_STOPPED, True
                    break
                if max_calls is not None and calls >= max_calls:
                    halted = True
                    break
                reason = live()
                if reason is not None:
                    state, why, halted = STATE_YIELDED, reason, True
                    break
                call = spend_rank_calls(home_root, target, 1, setting=setting, store=store, now=started)
                if not call["allowed"]:
                    state, why, halted = STATE_WAITING, WAIT_DAILY_CAP, True
                    answer["retry_at"] = _next_day(clock())
                    break
                batch = rows[start:start + size]
                try:
                    if candidate is None:
                        candidate = _candidate(view, home_root=home_root, target=target, resolved=refreshed.resolved)
                    result = _rank_batch(
                        view, batch, candidate, home_root=home_root, target=target, model_target=model_target, config=config
                    )
                except BaseException:  # noqa: BLE001 - re-raised: the call it reserved was never counted as made
                    refund_rank_calls(home_root, target, 1, store=store, now=started)
                    raise
                made = sum(item.attempts for item in result.batches if item.source == "model")  # type: ignore[attr-defined]
                if made == 0:
                    refund_rank_calls(home_root, target, 1, store=store, now=started)  # nothing was asked: every posting was cached, or no model answered
                else:
                    answer["warning"] = bool(answer["warning"]) or bool(call["warning"])
                scored = sum(1 for item in result.postings if item.scored and not item.cached)  # type: ignore[attr-defined]
                calls += made
                ranked += scored
                entry["calls"] = int(entry["calls"]) + made  # type: ignore[call-overload]
                entry["ranked"] = int(entry["ranked"]) + scored  # type: ignore[call-overload]
                store.renew_lease(LEASE_RANK, worker=holder)
                if result.status != "complete":  # type: ignore[attr-defined]
                    # No usable model, or an answer that could not be read: the lane stops here and backs off (the caller's).
                    unavailable = str(result.fail_open_reason or "").startswith(("model_target_unavailable", "model_unavailable"))  # type: ignore[attr-defined]
                    state, why, halted = STATE_UNAVAILABLE, REASON_MODEL if unavailable else REASON_INVALID, True
                    break
        if calls:
            postings.refresh(home_root, target, store=store, now=clock())  # the rows get the scores just cached
            for entry in profiles:
                left = sum(1 for row in store.postings(profile_id=str(entry["profile_id"])) if row.rank_score is None)
                if entry["ranked"] and left >= int(entry["unranked"]):  # type: ignore[call-overload]
                    # Scores were cached and the rows do not see them: never rank the same postings again and again.
                    state, why = STATE_UNAVAILABLE, REASON_CACHE_MISMATCH
        if state == STATE_IDLE and calls:
            state = STATE_RAN
        answer.update(calls=calls, ranked=ranked, profiles=profiles, calls_today=rank_calls_today(store, setting, day))
        return done(state, why)
    finally:
        if held:
            store.release_lease(LEASE_RANK, worker=holder)
        store.close()


__all__ = [
    "REASON_CACHE_MISMATCH",
    "REASON_CAP_ZERO",
    "REASON_INVALID",
    "REASON_MODEL",
    "SCHEMA_VERSION",
    "STATE_BUSY_ELSEWHERE",
    "STATE_DISABLED",
    "STATE_IDLE",
    "STATE_RAN",
    "STATE_UNAVAILABLE",
    "STATE_WAITING",
    "STATE_YIELDED",
    "WAIT_DAILY_CAP",
    "rank_status",
    "rank_tick",
]
