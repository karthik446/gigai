"""0.1.10.7 PL5: ``GET /api/pipeline`` -- what the background pipeline is doing, as one object.

``scout-pipeline:1``: the settings in effect, the lanes, today's counters
against their caps, how many steps and jobs are in each state, the approvals
that wait, each job's steps with its Scout label, and the last errors.

SYSTEM DATA ONLY (DESIGN 9): ids, codes, counts, numbers and timestamps. A
job is named by its job identity (the posting's public link, or
``text:sha256:...``); a Scout label is its code (``recommended`` /
``needs_attention``) with its reason codes and the Scout ATS score. No
posting, resume, answer or story text, no title, no company, no file path: a
caller reads a job's outputs through the routes that own them.

Reads only: it never creates ``pipeline.sqlite`` and never calls a model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
import time

from . import steps as steps_module
from .runner import WAIT_DAILY_CAP, WAIT_LANE, WAIT_RETRY, live_work
from .settings import SOURCE_UNREADABLE, pipeline_setting
from .store import (
    API_LANE_CAP,
    APPROVAL_PENDING,
    LANE_CAPS,
    OUTCOME_ERROR,
    STATE_AWAITING_APPROVAL,
    STATE_BLOCKED,
    STATE_CANCELLED,
    STATE_DONE,
    STATE_FAILED,
    STATE_READY,
    STATE_RUNNING,
    STEPS,
    PipelineStore,
    Step,
    pipeline_path,
)
from .triggers import approvals_of, caps

OVERVIEW_SCHEMA = "scout-pipeline:1"
#: Jobs listed in one response, most recently changed first; ``counts.jobs`` counts all of them.
JOBS_LIMIT = 200
ERRORS_LIMIT = 20

JOB_RUNNING = "running"
JOB_AWAITING_APPROVAL = "awaiting_approval"
JOB_FAILED = "failed"
JOB_WAITING = "waiting"
JOB_DONE = "done"
JOB_CANCELLED = "cancelled"
JOB_STATES: tuple[str, ...] = (JOB_RUNNING, JOB_AWAITING_APPROVAL, JOB_FAILED, JOB_WAITING, JOB_DONE, JOB_CANCELLED)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def job_state(states: Mapping[str, str]) -> str:
    """One job's state from its steps' (name -> state): what it waits for first."""

    values = set(states.values())
    if STATE_RUNNING in values:
        return JOB_RUNNING
    if STATE_AWAITING_APPROVAL in values:
        return JOB_AWAITING_APPROVAL
    if STATE_FAILED in values:
        return JOB_FAILED
    if values & {STATE_READY, STATE_BLOCKED}:
        return JOB_WAITING
    if values == {STATE_DONE}:
        return JOB_DONE
    return JOB_CANCELLED if STATE_CANCELLED in values else JOB_WAITING


def _label(home_root: Path, target: Path, profile_id: str, job: str) -> dict[str, object] | None:
    """The stored Scout label of a job, as codes and numbers only."""

    record = steps_module.read_label(home_root, target, profile_id, job)
    if record is None:
        return None
    label, reasons, score = record.get("label"), record.get("reasons"), record.get("ats_score")
    if label not in steps_module.LABELS:
        return None
    return {
        "label": label,
        "reasons": [reason for reason in reasons if reason in steps_module.LABEL_REASONS] if isinstance(reasons, list) else [],
        "ats_score": score if type(score) is int else None,
    }


def _job_json(rows: Mapping[str, Step], waits: Mapping[str, str]) -> dict[str, object]:
    first = next(iter(rows.values()))
    states = {name: rows[name].state for name in STEPS if name in rows}
    state = job_state(states)
    failed = next((rows[name] for name in STEPS if name in rows and rows[name].state == STATE_FAILED), None)
    waiting = next((waits[name] for name in STEPS if name in waits), None)
    label_step = rows.get("label")
    return {
        "profile_id": first.profile_id,
        "job_identity": first.job,
        "state": state,
        "steps": states,
        "trigger": rows[next(iter(states))].trigger,
        "approval_id": next((step.approval_id for step in rows.values() if step.state == STATE_AWAITING_APPROVAL), None),
        "waiting": waiting,
        "error_code": None if failed is None else failed.error_code,
        # Filled for the jobs a response lists: the label as the pipeline last set it, only while its step is done
        # (then it is the label of the current inputs).
        "label": None,
        "_labelled": label_step is not None and label_step.state == STATE_DONE,
        "updated_at": max(step.updated_at for step in rows.values()),
    }


def overview(
    home_root: Path,
    target: Path | None,
    *,
    runner: object | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.time,
    now: datetime | None = None,
    busy: Callable[[], str | None] | None = None,
) -> dict[str, object]:
    """The ``scout-pipeline:1`` object. ``runner``: the server's ``PipelineRunner``, for whether its thread runs."""

    home_root = Path(home_root)
    setting = pipeline_setting(home_root, target, environ=environ)
    answer: dict[str, object] = {
        "schema_version": OVERVIEW_SCHEMA,
        "setting": setting.to_json(),
        # False: the settings file exists and cannot be read. The pipeline is then off, whatever the environment says.
        "readable": setting.source != SOURCE_UNREADABLE,
        "runner": None if runner is None else runner.status(),  # type: ignore[attr-defined]
        "yielding_to": None,
        "lanes": [],
        "caps": None,
        "counts": {"steps": {}, "jobs": {}, "jobs_total": 0},
        "approvals": {"pending": 0, "items": []},
        "jobs": [],
        "errors": [],
    }
    if target is None:
        return answer
    target = Path(target)
    try:
        answer["yielding_to"] = (busy if busy is not None else (lambda: live_work(home_root, target)))()
    except Exception:  # noqa: BLE001 - status never fails on what it cannot read; the runner itself then waits
        answer["yielding_to"] = "live_work_unreadable"
    path = pipeline_path(home_root, target)
    if not path.is_file():
        answer["caps"] = caps(home_root, target, setting=setting, now=now)
        return answer
    store = PipelineStore(path, clock=clock)
    try:
        current = clock()
        answer["caps"] = caps(home_root, target, setting=setting, store=store, now=now)
        backoffs = {item.lane: item for item in store.lane_backoffs() if item.not_before > current}
        all_steps = store.steps()
        running: dict[str, int] = {}
        jobs: dict[tuple[str, str], dict[str, Step]] = {}
        waits: dict[tuple[str, str], dict[str, str]] = {}
        for step in all_steps:
            key = (step.profile_id, step.job)
            jobs.setdefault(key, {})[step.name] = step
            if step.state == STATE_RUNNING:
                running[step.lane] = running.get(step.lane, 0) + 1
            if step.state == STATE_READY:
                if step.not_before > current and step.error_code == WAIT_DAILY_CAP:
                    waits.setdefault(key, {})[step.name] = WAIT_DAILY_CAP
                elif step.lane in backoffs:
                    waits.setdefault(key, {})[step.name] = WAIT_LANE
                elif step.not_before > current:
                    waits.setdefault(key, {})[step.name] = WAIT_RETRY
        lanes = sorted({*LANE_CAPS, *running, *backoffs, *(step.lane for step in all_steps)})
        answer["lanes"] = [
            {
                "lane": lane,
                "running": running.get(lane, 0),
                "cap": LANE_CAPS.get(lane, API_LANE_CAP),
                "error_code": backoffs[lane].error_code if lane in backoffs else None,
                "retry_at": _iso(backoffs[lane].not_before) if lane in backoffs else None,
            }
            for lane in lanes
        ]
        listed = [_job_json(rows, waits.get(key, {})) for key, rows in jobs.items()]
        listed.sort(key=lambda item: (str(item["updated_at"]), str(item["job_identity"]), str(item["profile_id"])), reverse=True)
        per_state: dict[str, int] = {}
        for item in listed:
            per_state[str(item["state"])] = per_state.get(str(item["state"]), 0) + 1
        answer["counts"] = {"steps": store.counts(), "jobs": per_state, "jobs_total": len(listed)}
        shown = listed[:JOBS_LIMIT]
        for index, item in enumerate(listed):
            if item.pop("_labelled") and index < JOBS_LIMIT:
                item["label"] = _label(home_root, target, str(item["profile_id"]), str(item["job_identity"]))
        answer["jobs"] = shown
        pending = approvals_of(store, state=APPROVAL_PENDING)
        answer["approvals"] = {"pending": len(pending), "items": pending}
        errors = [
            {
                "profile_id": run.profile_id, "job_identity": run.job, "step": run.name, "error_code": run.error_code,
                "attempt": run.attempt, "at": run.started_at,
            }
            for run in store.runs()
            if run.outcome == OUTCOME_ERROR
        ]
        answer["errors"] = errors[-ERRORS_LIMIT:][::-1]
    finally:
        store.close()
    return answer


__all__ = ["ERRORS_LIMIT", "JOBS_LIMIT", "JOB_STATES", "OVERVIEW_SCHEMA", "job_state", "overview"]
