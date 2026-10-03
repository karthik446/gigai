"""PL1: the pipeline queue's behaviours, one at a time (``gigai.scout.pipeline.store``).

Synthetic jobs and digests only. Time is a fake clock wherever a lease, a
backoff or a timestamp matters; the cross-process cases run a real second
process (``_queue_worker.py``) and wait on what it prints or on a release
file, never on a duration.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from gigai.scout.pipeline import store as pipeline_store
from gigai.scout.pipeline.store import (
    LANE_BACKOFF_MAX_SECONDS,
    LANE_BACKOFF_SECONDS,
    PipelineStore,
    PipelineStoreError,
    StepMetrics,
    process_token,
)

_WORKER = Path(__file__).with_name("_queue_worker.py")
_P = "profile_a"
_JOB = "https://jobs.example.test/acme/1"


def _digest(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class _Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def store(tmp_path: Path, clock: _Clock) -> PipelineStore:
    return PipelineStore(tmp_path / "pipeline.sqlite", clock=clock)


def _enqueue(store: PipelineStore, job: str = _JOB, digest: str | None = None, **kwargs) -> str:
    kwargs.setdefault("lane", "claude_cli")
    kwargs.setdefault("trigger", "answer_saved")
    return store.enqueue(_P, job, "tailor", input_digest=digest or _digest(job, "r1"), **kwargs)


def _states(store: PipelineStore, job: str = _JOB) -> dict[str, str]:
    return {step.name: step.state for step in store.steps(job=job)}


def _run(store: PipelineStore, name: str, job: str = _JOB) -> str:
    claim = store.claim()
    assert claim is not None and (claim.job, claim.name) == (job, name)
    return store.finish(claim, input_digest=claim.input_digest or _digest(job, name), output_digest=_digest(job, name, "out"))


def _worker(*args: str) -> Any:
    done = subprocess.run([sys.executable, str(_WORKER), *args], capture_output=True, text=True, timeout=120, check=True)
    return json.loads(done.stdout)


# --- the file ----------------------------------------------------------------------------


def test_the_file_is_wal_sqlite_with_the_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "pipeline" / "pipeline.sqlite"
    PipelineStore(path)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA user_version").fetchone()[0] == pipeline_store.SCHEMA_VERSION
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {
        "step", "step_run", "model_call", "lane", "approval", "anchor", "cap_counter", "posting", "posting_build",
        "run_import", "run_assessment", "job_lease",
    }
    PipelineStore(path)  # opening again is a no-op


def test_a_version_1_file_gains_the_model_call_table_and_keeps_its_rows(tmp_path: Path) -> None:
    """0.1.10.7 E: schema 2 adds ``model_call``; a file written before it is upgraded in place."""

    path = tmp_path / "pipeline.sqlite"
    store = PipelineStore(path)
    assert _enqueue(store) == "enqueued"
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE model_call")
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    upgraded = PipelineStore(path)

    assert upgraded.recovered_from is None and upgraded.step(_P, _JOB, "tailor") is not None
    assert upgraded.record_call(kind="assess", lane="codex_cli", seconds=1.5) == 1
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == pipeline_store.SCHEMA_VERSION == 4


def test_pipeline_path_is_per_project_under_the_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("gigai.scout.find_jobs.discovery.storage.project_id", lambda home, target: "proj_123")
    assert pipeline_store.pipeline_path(tmp_path, tmp_path / "work") == tmp_path / "scout" / "proj_123" / "pipeline" / "pipeline.sqlite"


def test_a_corrupt_file_is_set_aside_and_started_again(tmp_path: Path, clock: _Clock) -> None:
    path = tmp_path / "pipeline.sqlite"
    path.write_bytes(b"this is not a database at all" * 100)
    store = PipelineStore(path, clock=clock)
    assert store.recovered_from == tmp_path / f"pipeline.sqlite.corrupt-{int(clock.now)}"
    assert store.recovered_from.read_bytes().startswith(b"this is not a database")
    assert _enqueue(store) == "enqueued"


def test_a_file_from_a_newer_gigai_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=99")
    connection.close()
    with pytest.raises(PipelineStoreError) as excinfo:
        PipelineStore(path)
    assert excinfo.value.code == "pipeline_schema_newer"


# --- enqueue: the digest no-op and re-open ------------------------------------------------


def test_enqueue_creates_the_fixed_dag_with_the_root_ready(store: PipelineStore) -> None:
    assert _enqueue(store, model_target="claude_cli", downstream_lanes={"reassess": ("codex_cli", "codex_cli")}) == "enqueued"
    steps = {step.name: step for step in store.steps(job=_JOB)}
    assert {name: step.state for name, step in steps.items()} == {"tailor": "ready", "reassess": "blocked", "ats": "blocked", "label": "blocked"}
    assert {name: (step.lane, step.model_target) for name, step in steps.items()} == {
        "tailor": ("claude_cli", "claude_cli"),
        "reassess": ("codex_cli", "codex_cli"),
        "ats": ("local", None),
        "label": ("local", None),
    }
    assert steps["tailor"].input_digest == _digest(_JOB, "r1") and steps["reassess"].input_digest is None


def test_steps_unblock_in_dag_order_as_their_dependencies_finish(store: PipelineStore) -> None:
    _enqueue(store, downstream_lanes={"reassess": ("codex_cli", "codex_cli")})
    assert _run(store, "tailor") == "done"
    assert _states(store) == {"tailor": "done", "reassess": "ready", "ats": "ready", "label": "blocked"}
    first = store.claim()
    second = store.claim()
    assert {first.name, second.name} == {"reassess", "ats"}
    store.finish(first, input_digest=_digest("a"))
    assert _states(store)["label"] == "blocked"  # still waits for the other one
    store.finish(second, input_digest=_digest("b"))
    assert _run(store, "label") == "done"
    assert _states(store) == {"tailor": "done", "reassess": "done", "ats": "done", "label": "done"}
    assert store.claim() is None


def test_same_digest_after_done_is_noop_unchanged_and_while_queued_noop_already_queued(store: PipelineStore) -> None:
    assert _enqueue(store) == "enqueued"
    assert _enqueue(store) == "noop_already_queued"
    _run(store, "tailor")
    assert _enqueue(store) == "noop_unchanged"
    assert _states(store)["tailor"] == "done"


def test_a_changed_digest_reopens_the_step_and_reblocks_everything_downstream(store: PipelineStore) -> None:
    _enqueue(store)
    for name in ("tailor", "reassess", "ats", "label"):
        claim = store.claim()
        assert claim.name == name or {claim.name, name} <= {"reassess", "ats"}
        store.finish(claim, input_digest=claim.input_digest or _digest(claim.name))
    before = {step.name: step for step in store.steps(job=_JOB)}

    assert _enqueue(store, digest=_digest(_JOB, "r2")) == "enqueued"

    after = {step.name: step for step in store.steps(job=_JOB)}
    assert {name: step.state for name, step in after.items()} == {"tailor": "ready", "reassess": "blocked", "ats": "blocked", "label": "blocked"}
    assert after["tailor"].input_digest == _digest(_JOB, "r2") and after["tailor"].done_digest == _digest(_JOB, "r1")
    # A downstream step keeps the digest it was done with: the runner can finish it without a call.
    assert after["reassess"].input_digest is None and after["reassess"].done_digest == before["reassess"].done_digest
    assert all(after[name].generation == before[name].generation + 1 for name in after)


def test_enqueue_of_a_middle_step_reblocks_only_its_downstream(store: PipelineStore) -> None:
    _enqueue(store)
    _run(store, "tailor")
    for _ in range(2):
        claim = store.claim()
        store.finish(claim, input_digest=_digest(claim.name))
    _run(store, "label")
    assert store.enqueue(_P, _JOB, "ats", input_digest=_digest("ats-rules-v2"), trigger="process_now", lane="local") == "enqueued"
    assert _states(store) == {"tailor": "done", "reassess": "done", "ats": "ready", "label": "blocked"}


def test_force_reopens_a_done_step_with_the_same_digest(store: PipelineStore) -> None:
    _enqueue(store)
    _run(store, "tailor")
    assert _enqueue(store, trigger="process_now", force=True) == "enqueued"
    assert _states(store)["tailor"] == "ready"


def test_a_failed_step_is_not_reopened_by_the_same_digest_but_is_by_a_new_one(store: PipelineStore) -> None:
    _enqueue(store)
    store.fail(store.claim(), "profile_unavailable")
    assert _states(store)["tailor"] == "failed"
    assert _enqueue(store) == "noop_failed"
    assert _enqueue(store, digest=_digest(_JOB, "r2")) == "enqueued"
    assert _states(store)["tailor"] == "ready"


def test_a_digest_change_while_running_lets_the_call_finish_then_reopens(store: PipelineStore) -> None:
    _enqueue(store)
    claim = store.claim()
    assert _enqueue(store, digest=_digest(_JOB, "r2")) == "enqueued"
    assert _states(store)["tailor"] == "running"  # the call in flight is not interrupted
    assert store.claim() is None or store.claim().name != "tailor"

    assert store.finish(claim, output_ref="resumes/profile_a/abc.json", output_digest=_digest("out-r1")) == "ready"
    step = store.step(_P, _JOB, "tailor")
    assert step.done_digest == _digest(_JOB, "r1") and step.input_digest == _digest(_JOB, "r2")
    assert step.output_ref == "resumes/profile_a/abc.json"  # valid for its inputs, kept
    again = store.claim()
    assert again.name == "tailor" and again.input_digest == _digest(_JOB, "r2")


def test_an_upstream_reopen_while_a_downstream_step_runs_reopens_it_after_its_call(store: PipelineStore) -> None:
    _enqueue(store)
    _run(store, "tailor")
    reassess = store.claim(lanes=["claude_cli"])
    assert reassess.name == "reassess"
    _enqueue(store, digest=_digest(_JOB, "r2"))
    assert store.finish(reassess, input_digest=_digest("reassess-1")) == "blocked"


# --- claim, lanes, caps ------------------------------------------------------------------


def test_per_lane_caps_and_the_model_total_hold(tmp_path: Path, clock: _Clock) -> None:
    store = PipelineStore(tmp_path / "p.sqlite", clock=clock)
    for index in range(4):
        _enqueue(store, job=f"https://jobs.example.test/claude/{index}", lane="claude_cli")
        _enqueue(store, job=f"https://jobs.example.test/codex/{index}", lane="codex_cli")
        _enqueue(store, job=f"https://jobs.example.test/ollama/{index}", lane="ollama")
    claims = []
    while (claim := store.claim()) is not None:
        claims.append(claim)
    lanes = sorted(claim.lane for claim in claims)
    # claude 2 + codex 2 = the model total of 4: the ollama lane waits even though its own cap (1) has room.
    assert lanes == ["claude_cli", "claude_cli", "codex_cli", "codex_cli"]
    store.finish(claims[0], input_digest=_digest("x"))
    nxt = store.claim(lanes=["ollama"])
    assert nxt is not None and nxt.lane == "ollama"
    assert store.claim(lanes=["ollama"]) is None  # ollama cap 1


def test_local_lane_cap_is_four_and_does_not_count_toward_model_calls(tmp_path: Path, clock: _Clock) -> None:
    store = PipelineStore(tmp_path / "p.sqlite", clock=clock)
    for index in range(6):
        store.enqueue(_P, f"https://jobs.example.test/l/{index}", "ats", input_digest=_digest(str(index)), trigger="process_now", lane="local")
    store.enqueue(_P, "https://jobs.example.test/m/0", "label", input_digest=_digest("m"), trigger="process_now", lane="local")
    _enqueue(store, job="https://jobs.example.test/c/0")
    # Nothing done upstream: the ats / label steps were enqueued directly and their deps are missing.
    assert store.claim(lanes=["local"]) is None
    for index in range(6):
        store.enqueue(_P, f"https://jobs.example.test/l/{index}", "tailor", input_digest=_digest(str(index)), trigger="process_now", lane="local")
    local = [store.claim(lanes=["local"]) for _ in range(5)]
    assert sum(claim is not None for claim in local) == 4


def test_api_lanes_get_their_own_cap_of_two(store: PipelineStore) -> None:
    for index in range(3):
        _enqueue(store, job=f"https://jobs.example.test/api/{index}", lane="api:openrouter-main")
    assert [store.claim() is not None for _ in range(3)] == [True, True, False]


def test_a_cap_held_by_a_live_process_is_not_taken_and_its_other_lanes_stay_open(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    store = PipelineStore(db)
    for index in range(3):
        _enqueue(store, job=f"https://jobs.example.test/claude/{index}", lane="claude_cli")
    _enqueue(store, job="https://jobs.example.test/codex/0", lane="codex_cli")
    release = tmp_path / "release"
    holder = subprocess.Popen(
        [sys.executable, str(_WORKER), "hold", str(db), "claude_cli", "2", str(release)], stdout=subprocess.PIPE, text=True
    )
    try:
        held = json.loads(holder.stdout.readline())
        assert len([claim for claim in held if claim is not None]) == 2
        # This process, and a third one, see the claude lane full: its holder is alive.
        assert store.claim(lanes=["claude_cli"]) is None
        assert _worker("try", str(db), "claude_cli") is None
        assert _worker("try", str(db), "codex_cli")["lane"] == "codex_cli"
    finally:
        release.touch()
        holder.wait(timeout=120)
    assert holder.returncode == 0
    assert store.claim(lanes=["claude_cli"]) is not None  # released: room again


def test_claim_refuses_a_worker_name_that_is_not_an_id(store: PipelineStore) -> None:
    with pytest.raises(PipelineStoreError):
        store.claim(worker="thread one")


# --- leases and reclaim ------------------------------------------------------------------


def test_renew_extends_the_lease_and_an_expired_lease_is_reclaimed(store: PipelineStore, clock: _Clock) -> None:
    _enqueue(store)
    claim = store.claim()
    clock.now += 500
    renewed = store.renew(claim)
    assert renewed is not None and renewed.lease_until == clock.now + pipeline_store.LEASE_SECONDS
    clock.now += 500  # past the first lease, inside the renewed one
    assert store.reclaim() == 0
    clock.now += 200  # past the renewed lease
    assert store.reclaim() == 1
    assert _states(store)["tailor"] == "ready"
    assert store.renew(claim) is None
    assert store.finish(claim, input_digest=_digest("late")) == "lost_lease"
    assert [run.outcome for run in store.runs()] == ["interrupted", "lost_lease"]


def test_a_dead_holder_is_reclaimed_at_once_not_on_lease_expiry(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    store = PipelineStore(db)
    for index in range(2):
        _enqueue(store, job=f"https://jobs.example.test/claude/{index}", lane="claude_cli")
    crashed = _worker("crash", str(db), "claude_cli")  # 600 s lease, process gone
    assert store.step(_P, crashed["job"], "tailor").state == "running"

    # Both claude slots are this process's at once: the dead holder does not halve the lane.
    first, second = store.claim(lanes=["claude_cli"]), store.claim(lanes=["claude_cli"])
    assert first is not None and second is not None
    assert {first.job, second.job} == {f"https://jobs.example.test/claude/{index}" for index in range(2)}
    reclaimed = store.step(_P, crashed["job"], "tailor")
    assert reclaimed.attempts == 2 and reclaimed.lease_owner.startswith(process_token())
    interrupted = [run for run in store.runs() if run.outcome == "interrupted"]
    assert [(run.job, run.owner) for run in interrupted] == [(crashed["job"], crashed["owner"])]


def test_this_pid_with_another_token_is_an_earlier_process_and_is_reclaimed(store: PipelineStore) -> None:
    _enqueue(store)
    claim = store.claim()
    connection = sqlite3.connect(store.path)
    connection.execute("UPDATE step SET lease_owner=? WHERE name='tailor'", ("0" * 32 + ":w", ))
    connection.commit()
    connection.close()
    assert store.reclaim() == 1
    assert store.finish(claim, input_digest=_digest("x")) == "lost_lease"


def test_our_own_live_claim_is_never_reclaimed(store: PipelineStore) -> None:
    _enqueue(store)
    store.claim()
    assert store.reclaim() == 0
    assert os.getpid() == store.step(_P, _JOB, "tailor").lease_pid


# --- cancel ------------------------------------------------------------------------------


def test_cancel_stops_waiting_steps_and_lets_a_running_call_finish_with_its_output(store: PipelineStore) -> None:
    _enqueue(store)
    claim = store.claim()
    assert store.cancel(_P, _JOB) == 4
    assert _states(store) == {"tailor": "running", "reassess": "cancelled", "ats": "cancelled", "label": "cancelled"}
    assert store.finish(claim, output_ref="resumes/profile_a/x.json", output_digest=_digest("out")) == "cancelled"
    step = store.step(_P, _JOB, "tailor")
    assert step.output_ref == "resumes/profile_a/x.json" and step.lease_owner is None
    assert store.claim() is None
    assert _enqueue(store) == "enqueued"  # a new trigger re-opens a cancelled job
    assert _states(store)["tailor"] == "ready"


def test_a_cancelled_running_step_whose_holder_died_settles_cancelled(store: PipelineStore, clock: _Clock) -> None:
    _enqueue(store)
    store.claim()
    store.cancel(_P, _JOB)
    clock.now += pipeline_store.LEASE_SECONDS + 1
    assert store.reclaim() == 1
    assert _states(store)["tailor"] == "cancelled"


# --- retry and backoff -------------------------------------------------------------------


def test_transient_errors_back_off_exponentially_then_fail_after_four_attempts(store: PipelineStore, clock: _Clock) -> None:
    _enqueue(store)
    waits = []
    for attempt in range(1, 5):
        claim = store.claim()
        assert claim is not None and claim.attempt == attempt
        state = store.fail(claim, "assess_timeout")
        step = store.step(_P, _JOB, "tailor")
        if attempt < 4:
            assert state == "ready" and store.claim() is None  # not before its backoff
            waits.append(step.not_before - clock.now)
            clock.now = step.not_before
        else:
            assert state == "failed" and step.error_code == "assess_timeout"
    assert waits == [60.0, 120.0, 240.0]
    assert [run.outcome for run in store.runs()] == ["error"] * 4


def test_invalid_model_output_gets_one_more_attempt(store: PipelineStore) -> None:
    _enqueue(store)
    assert store.fail(store.claim(), "model_output_invalid") == "ready"
    assert store.fail(store.claim(), "model_output_invalid") == "failed"


def test_any_other_code_fails_at_once_and_retry_reopens(store: PipelineStore) -> None:
    _enqueue(store)
    assert store.fail(store.claim(), "resume_unavailable") == "failed"
    assert store.claim() is None
    assert store.retry(_P, _JOB) == 1
    step = store.step(_P, _JOB, "tailor")
    assert (step.state, step.attempts, step.error_code) == ("ready", 0, None)
    assert store.claim().name == "tailor"


def test_model_target_unavailable_backs_the_lane_off_doubling_to_six_hours(store: PipelineStore, clock: _Clock) -> None:
    _enqueue(store, job="https://jobs.example.test/a")
    _enqueue(store, job="https://jobs.example.test/b")
    _enqueue(store, job="https://jobs.example.test/c", lane="codex_cli")
    claim = store.claim(lanes=["claude_cli"])
    assert store.fail(claim, "model_target_unavailable") == "ready"
    assert store.step(_P, claim.job, "tailor").attempts == 0  # waiting for the lane is not an attempt
    assert store.claim(lanes=["claude_cli"]) is None  # the whole lane waits
    assert store.claim(lanes=["codex_cli"]) is not None  # other lanes do not
    backoffs = []
    for _ in range(9):
        clock.now = store.lane_backoffs()[0].not_before
        store.fail(store.claim(lanes=["claude_cli"]), "model_target_unavailable")
        backoffs.append(store.lane_backoffs()[0].backoff_seconds)
    assert backoffs[0] == LANE_BACKOFF_SECONDS * 2 and backoffs[-1] == LANE_BACKOFF_MAX_SECONDS
    clock.now = store.lane_backoffs()[0].not_before
    good = store.claim(lanes=["claude_cli"])
    store.finish(good, input_digest=_digest("ok"))
    assert store.lane_backoffs() == ()  # a success clears the lane


def test_finish_records_the_metrics_row(store: PipelineStore, clock: _Clock) -> None:
    _enqueue(store)
    claim = store.claim()
    clock.now += 42.5
    store.finish(
        claim,
        output_digest=_digest("out"),
        metrics=StepMetrics(adapter="claude_cli", model="claude-opus-5-5", input_tokens=1200, output_tokens=300, cached_tokens=800, cost_usd=0.0123, cost_status="provider_reported"),
    )
    (run,) = store.runs()
    assert (run.outcome, run.seconds, run.model, run.input_tokens, run.cached_tokens, run.cost_status) == (
        "ok", 42.5, "claude-opus-5-5", 1200, 800, "provider_reported",
    )
    assert run.input_digest == _digest(_JOB, "r1") and run.attempt == 1 and run.started_at.endswith("Z")


# --- approvals, anchor, caps -------------------------------------------------------------


def test_steps_wait_for_their_approval_then_open_or_are_cancelled(store: PipelineStore) -> None:
    approval = store.create_approval(trigger="story_saved", jobs=2, est_calls=6, est_tokens=400_000)
    a, b = "https://jobs.example.test/a", "https://jobs.example.test/b"
    _enqueue(store, job=a, trigger="story_saved", approval_id=approval)
    _enqueue(store, job=b, trigger="story_saved", approval_id=approval)
    assert _states(store, a)["tailor"] == "awaiting_approval"
    assert store.claim() is None
    assert [item.id for item in store.approvals(state="pending")] == [approval]

    assert store.decide_approval(approval, approved=True, decided_by="agent") == "approved"
    assert _states(store, a)["tailor"] == "ready" and _states(store, b)["tailor"] == "ready"
    assert store.decide_approval(approval, approved=False, decided_by="operator") == "approved"  # decided once
    (decided,) = store.approvals()
    assert (decided.state, decided.decided_by, decided.est_calls) == ("approved", "agent", 6)

    other = store.create_approval(trigger="answer_saved", jobs=1, est_calls=3)
    c = "https://jobs.example.test/c"
    _enqueue(store, job=c, approval_id=other)
    assert store.decide_approval(other, approved=False, decided_by="operator") == "declined"
    assert set(_states(store, c).values()) == {"cancelled"}
    with pytest.raises(PipelineStoreError):
        store.decide_approval("apv_missing", approved=True, decided_by="operator")


def test_the_anchor_is_one_row_per_install_and_never_moves_back(store: PipelineStore) -> None:
    assert store.anchor() is None
    assert store.advance_anchor("2026-10-02T09:00:00Z", set_by="scout_new").set_by == "scout_new"
    assert store.advance_anchor("2026-10-01T09:00:00Z", set_by="mark_all_seen").last_checked_at == "2026-10-02T09:00:00Z"
    store.advance_anchor("2026-10-03T08:00:00.250000Z", set_by="mark_all_seen")
    assert store.anchor() == pipeline_store.Anchor("2026-10-03T08:00:00.250000Z", "mark_all_seen")
    with pytest.raises(sqlite3.IntegrityError):
        sqlite3.connect(store.path).execute("INSERT INTO anchor VALUES ('profile_a', '2026-10-03T08:00:00Z', 'scout_new')")


def test_daily_cap_counters_stop_at_the_limit_per_day(store: PipelineStore) -> None:
    for _ in range(40):
        assert store.spend("pipeline_calls", "2026-10-02", limit=40)
    assert store.spend("pipeline_calls", "2026-10-02", limit=40) is False
    assert store.used("pipeline_calls", "2026-10-02") == 40
    assert store.spend("pipeline_calls", "2026-10-03", 3, limit=40) and store.used("pipeline_calls", "2026-10-03") == 3
    assert store.spend("rank_calls", "2026-10-02", 60) and store.used("rank_calls", "2026-10-02") == 60


# --- 0.1.10.7 PL4: what the runner adds to the queue ------------------------------------------


def test_defer_gives_a_claim_back_to_wait_without_an_attempt_or_a_run_row(store: PipelineStore, clock: _Clock) -> None:
    _enqueue(store)
    claim = store.claim(worker="w1")

    assert store.defer(claim, not_before=clock.now + 3600, code="daily_cap_reached") == "ready"

    step = store.step(_P, _JOB, "tailor")
    assert (step.state, step.attempts, step.error_code, step.not_before, step.lease_owner) == ("ready", 0, "daily_cap_reached", clock.now + 3600, None)
    assert store.runs() == () and store.claim(worker="w1") is None  # not an attempt, and it waits
    assert store.defer(claim, not_before=0, code="daily_cap_reached") == "lost_lease"  # no longer held
    clock.now += 3601
    again = store.claim(worker="w1")
    assert again is not None and again.attempt == 1
    with pytest.raises(PipelineStoreError):
        store.defer(again, not_before=0, code="the cap was reached")  # a code, never a sentence
    # A cancel that came in while it was held wins over the wait.
    store.cancel(_P, _JOB)
    assert store.defer(again, not_before=clock.now + 60, code="daily_cap_reached") == "cancelled"


def test_claim_only_takes_the_steps_of_the_named_job(store: PipelineStore) -> None:
    other = "https://jobs.example.test/acme/2"
    _enqueue(store)
    _enqueue(store, job=other)

    claim = store.claim(worker="w1", only=(_P, other))

    assert claim is not None and claim.job == other
    assert store.claim(worker="w1", only=(_P, other)) is None  # its downstream is blocked; the other job is not taken
    assert store.step(_P, _JOB, "tailor").state == "ready"
    with pytest.raises(PipelineStoreError):
        store.claim(worker="w1", only=(_P, "not a job"))


def test_refund_gives_back_counted_calls_and_never_goes_below_zero(store: PipelineStore) -> None:
    assert store.spend("pipeline_calls", "2026-10-02", 2, limit=2) is True
    assert store.spend("pipeline_calls", "2026-10-02", 1, limit=2) is False
    store.refund("pipeline_calls", "2026-10-02", 1)
    assert store.used("pipeline_calls", "2026-10-02") == 1
    assert store.spend("pipeline_calls", "2026-10-02", 1, limit=2) is True
    store.refund("pipeline_calls", "2026-10-02", 5)
    assert store.used("pipeline_calls", "2026-10-02") == 0
    store.refund("pipeline_calls", "2026-10-03")  # a day with no count: nothing to give back
    assert store.used("pipeline_calls", "2026-10-03") == 0
