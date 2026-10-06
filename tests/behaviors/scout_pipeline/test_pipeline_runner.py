"""PL4: the runner. Who may claim, when it waits, and that a step never runs twice.

The race and the kill use real processes (``_queue_worker.py``); the rest run
the real steps over the synthetic gig of ``tests/support/pipeline_fixtures``
with a scripted model, so a model call that should not happen is counted.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.scout.pipeline import runner as runner_module
from gigai.scout.pipeline import steps
from gigai.scout.pipeline.runner import (
    BUSY_FIND_JOBS_RUN,
    DRAIN_DISABLED,
    DRAIN_IDLE,
    DRAIN_RAN,
    DRAIN_YIELDED,
    PipelineRunner,
    live_work,
    pipeline_status,
)
from gigai.scout.pipeline.settings import PIPELINE_ENV, PipelineSetting, pipeline_setting
from gigai.scout.pipeline.store import LANE_CAPS, MODEL_TOTAL_CAP, STEPS, PipelineStore
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.scout_cli import scout_group

from tests.support.answers_stories_fixtures import config
from tests.support.pipeline_fixtures import JOB, PipelineFixture, assess_base, build_pipeline_fixture

_WORKER = Path(__file__).with_name("_queue_worker.py")
_JOBS = [f"https://jobs.example.test/acme/{index:02d}" for index in range(20)]


def _digest(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _peak(intervals: list[tuple[float, float]]) -> int:
    events = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals], key=lambda item: (item[0], item[1]))
    best = current = 0
    for _, delta in events:
        current += delta
        best = max(best, current)
    return best


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    return build_pipeline_fixture(tmp_path, monkeypatch)


def _runner(fx: PipelineFixture, **options) -> PipelineRunner:
    options.setdefault("busy", lambda: None)
    return PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), **options)


def _enqueue(fx: PipelineFixture, job: str = JOB) -> None:
    assert steps.enqueue_job(fx.profile_id, job, home_root=fx.home_root, target=fx.target)["result"] == "enqueued"


def _states(fx: PipelineFixture, job: str = JOB) -> dict[str, str]:
    store = PipelineStore(fx.db)
    try:
        return {step.name: step.state for step in store.steps(profile_id=fx.profile_id, job=job)}
    finally:
        store.close()


# --- (c) the server's runner thread and `--once` in two processes: no step twice -----------------------


@pytest.mark.skip(reason="needs the pipeline on: re-enable in 0.1.11.1 (pipeline off by default in 0.1.11)")
def test_a_server_runner_thread_and_a_once_runner_in_two_processes_never_run_a_step_twice(tmp_path: Path) -> None:
    db = tmp_path / "pipeline" / "pipeline.sqlite"
    store = PipelineStore(db)
    for index, job in enumerate(_JOBS):
        lane = "ollama" if index >= 14 else "claude_cli"
        assert store.enqueue(
            "profile_race", job, "tailor", input_digest=_digest(job, "resume-r1", "answers-r1"), trigger="process_now",
            lane=lane, model_target=lane, downstream_lanes={"reassess": ("codex_cli", "codex_cli")},
        ) == "enqueued"

    logs = tmp_path / "logs"
    (logs / "ran").mkdir(parents=True)
    go = tmp_path / "go"
    racers = [
        subprocess.Popen(
            [sys.executable, str(_WORKER), "runner", str(db), str(logs), label, mode, str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for label, mode in (("server", "server"), ("once", "once"))
    ]
    for racer in racers:
        assert racer.stdout.readline().strip() == "ready"  # both are up before either may claim
    go.write_text("go", encoding="utf-8")
    for racer in racers:
        _, stderr = racer.communicate(timeout=180)
        assert racer.returncode == 0, stderr

    expected = len(_JOBS) * len(STEPS)
    assert store.counts() == {"done": expected}
    ok = [run for run in store.runs() if run.outcome == "ok"]
    per_step = defaultdict(int)
    for run in ok:
        per_step[(run.job, run.name)] += 1
    assert len(per_step) == expected and set(per_step.values()) == {1}  # 80 of 80, none twice
    assert [run.outcome for run in store.runs() if run.outcome != "ok"] == []
    assert len(list((logs / "ran").iterdir())) == expected  # and by the exclusive-create marker of each step

    lines = [json.loads(line) for path in sorted(logs.glob("*.jsonl")) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == expected
    by_process = defaultdict(int)
    for path in sorted(logs.glob("*.jsonl")):
        by_process[path.stem] = len(path.read_text(encoding="utf-8").splitlines())
    assert set(by_process) == {"server", "once"} and all(by_process.values())  # both ran steps
    assert len({line["owner"].split(":")[0] for line in lines}) == 2
    by_lane: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for line in lines:
        by_lane[line["lane"]].append((line["start"], line["end"]))
    for lane, intervals in by_lane.items():
        assert _peak(intervals) <= LANE_CAPS[lane], lane  # the lane caps held across both processes
    model = [interval for lane, intervals in by_lane.items() if lane != "local" for interval in intervals]
    assert _peak(model) <= MODEL_TOTAL_CAP
    assert {lane: len(intervals) for lane, intervals in by_lane.items()} == {"claude_cli": 14, "ollama": 6, "codex_cli": 20, "local": 40}


# --- (d) a killed runner's step is reclaimed at once ---------------------------------------------------


def test_a_step_held_by_a_killed_runner_is_reclaimed_at_once_and_run_to_completion(fx: PipelineFixture) -> None:
    _enqueue(fx)
    crashed = subprocess.run(
        [sys.executable, str(_WORKER), "crash", str(fx.db), "ollama"], capture_output=True, text=True, timeout=120, check=True
    )
    held = json.loads(crashed.stdout)
    assert (held["job"], held["name"]) == (JOB, "tailor")
    store = PipelineStore(fx.db)
    step = store.step(fx.profile_id, JOB, "tailor")
    assert step.state == "running" and step.lease_until - time.time() > 500  # its lease is far from expiry
    store.close()

    result = _runner(fx).drain()

    assert {step["name"]: step["outcome"] for step in result.steps} == dict.fromkeys(STEPS, "done")
    store = PipelineStore(fx.db)
    attempts = [run for run in store.runs(profile_id=fx.profile_id, job=JOB) if run.name == "tailor"]
    assert [run.outcome for run in attempts] == ["interrupted", "ok"]
    assert attempts[0].owner == held["owner"] and attempts[1].owner.split(":")[0] != held["owner"].split(":")[0]
    assert store.step(fx.profile_id, JOB, "tailor").attempts == 2 and len(fx.model.tailor_prompts) == 1  # run once, by this runner
    store.close()


# --- (e) the runner yields to a live find-jobs run and resumes after it --------------------------------


def test_the_runner_claims_nothing_while_a_find_jobs_run_is_live_and_resumes_after(fx: PipelineFixture) -> None:
    _enqueue(fx)
    live: list[str | None] = [BUSY_FIND_JOBS_RUN]
    runner = _runner(fx, busy=lambda: live[0])

    waiting = runner.drain()

    assert (waiting.state, waiting.reason, waiting.steps, waiting.model_calls) == (DRAIN_YIELDED, BUSY_FIND_JOBS_RUN, [], 0)
    assert fx.model.calls == 0 and _states(fx) == {"tailor": "ready", "reassess": "blocked", "ats": "blocked", "label": "blocked"}
    status = pipeline_status(fx.home_root, fx.target, busy=lambda: live[0])
    assert status["yielding_to"] == BUSY_FIND_JOBS_RUN

    live[0] = None
    resumed = runner.drain()

    assert resumed.state == DRAIN_RAN and _states(fx) == dict.fromkeys(STEPS, "done")
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1)


def test_live_work_names_a_find_jobs_run_that_is_not_finished_and_wrote_recently(fx: PipelineFixture) -> None:
    assert live_work(fx.home_root, fx.target) is None
    run_dir = Path(fx.gig.resolved.path) / "runs" / "run_00000000-0000-4000-8000-00000000abcd"
    run_dir.mkdir(parents=True)
    details = run_dir / "run-details.json"

    details.write_text(json.dumps({"run_id": run_dir.name, "status": "running"}), encoding="utf-8")
    assert live_work(fx.home_root, fx.target) == BUSY_FIND_JOBS_RUN
    # The default signal, end to end: nothing is claimed while it says so.
    _enqueue(fx)
    assert PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root)).drain().state == DRAIN_YIELDED
    assert fx.model.calls == 0

    old = time.time() - runner_module.RUN_QUIET_SECONDS - 60
    os.utime(details, (old, old))  # its process is gone: it has written nothing for a quarter hour
    assert live_work(fx.home_root, fx.target) is None

    details.write_text(json.dumps({"run_id": run_dir.name, "status": "succeeded"}), encoding="utf-8")
    assert live_work(fx.home_root, fx.target) is None


# --- settings: off, and unreadable means off -----------------------------------------------------------


@pytest.mark.skip(reason="needs the pipeline on: re-enable in 0.1.11.1 (pipeline off by default in 0.1.11)")
def test_the_settings_default_to_on_with_the_caps_and_an_unreadable_file_turns_the_pipeline_off(fx: PipelineFixture) -> None:
    path = settings_path(fx.home_root, fx.target)
    assert pipeline_setting(fx.home_root, fx.target, environ={}).to_json() == {
        "enabled": True, "source": "default", "auto_jobs_per_trigger": 10, "max_model_calls_per_day": 40, "label_min_ats": 0,
        "models": {}, "rank": {"enabled": True, "source": "default", "max_calls_per_day": 100, "warn_calls_per_day": 60},
    }
    # 0.1.11.2: the variable set to off turns ranking off with the pipeline (the file does not name rank.enabled).
    assert pipeline_setting(fx.home_root, fx.target, environ={PIPELINE_ENV: "off"}) == PipelineSetting(
        enabled=False, source="environment", rank_enabled=False, rank_source="environment"
    )

    def write(block: object, **more: object) -> None:
        path.write_text(json.dumps({"schema_version": "scout-settings:1", "pipeline": block, **more}), encoding="utf-8")

    write({"max_model_calls_per_day": 5, "models": {"tailor": "codex_cli"}}, rank={"max_calls_per_day": 50})
    setting = pipeline_setting(fx.home_root, fx.target, environ={})
    assert (setting.enabled, setting.source, setting.max_model_calls_per_day, setting.rank_max_calls_per_day) == (True, "setting", 5, 50)
    # pipeline.models.<step>: the tailor step runs on the named adapter, the re-assessment on the configured one.
    _enqueue(fx)
    store = PipelineStore(fx.db)
    tailor, reassess = store.step(fx.profile_id, JOB, "tailor"), store.step(fx.profile_id, JOB, "reassess")
    assert (tailor.lane, tailor.model_target, reassess.lane, reassess.model_target) == ("codex_cli", "codex_cli", "ollama", "ollama_local")
    assert store.cancel(fx.profile_id, JOB) == 4
    store.close()

    write({"enabled": False})
    off = _runner(fx).drain()
    assert (off.state, off.reason, off.steps) == (DRAIN_DISABLED, "setting", [])
    assert pipeline_setting(fx.home_root, fx.target, environ={PIPELINE_ENV: "1"}).enabled is True

    for bad in ({"enabled": "yes"}, {"max_model_calls_per_day": -1}, {"max_model_calls_per_day": "40"}, {"models": {"ats": "codex_cli"}},
                {"models": {"tailor": "gpt"}}, {"label_min_ats": 101}, ["enabled"]):
        write(bad)
        assert pipeline_setting(fx.home_root, fx.target, environ={}).to_json()["source"] == "settings_unreadable", bad
        assert pipeline_setting(fx.home_root, fx.target, environ={}).enabled is False
    path.write_text("{not json", encoding="utf-8")
    assert pipeline_setting(fx.home_root, fx.target, environ={}) == PipelineSetting(
        enabled=False, source="settings_unreadable", rank_enabled=False, rank_source="settings_unreadable"
    )
    # Never guessed on, not even by the environment or by an explicit process.
    assert pipeline_setting(fx.home_root, fx.target, environ={PIPELINE_ENV: "1"}).enabled is False


@pytest.mark.skip(reason="needs the pipeline on: re-enable in 0.1.11.1 (pipeline off by default in 0.1.11)")
def test_an_unreadable_settings_file_stops_every_claim_even_for_process(fx: PipelineFixture) -> None:
    _enqueue(fx)
    settings_path(fx.home_root, fx.target).write_text("{not json", encoding="utf-8")

    assert _runner(fx).drain().state == DRAIN_DISABLED
    forced = _runner(fx).drain(only=(fx.profile_id, JOB), force_enabled=True)
    assert (forced.state, forced.reason) == (DRAIN_DISABLED, "settings_unreadable")
    assert fx.model.calls == 0 and _states(fx)["tailor"] == "ready"

    # Switched off by the user: `process` still runs the one job that was asked for; the background does not.
    settings_path(fx.home_root, fx.target).write_text(json.dumps({"schema_version": "scout-settings:1", "pipeline": {"enabled": False}}), encoding="utf-8")
    assert _runner(fx).drain().state == DRAIN_DISABLED
    assert _runner(fx).drain(only=(fx.profile_id, JOB), force_enabled=True).state == DRAIN_RAN
    assert _states(fx) == dict.fromkeys(STEPS, "done")


# --- failures: a lane that cannot be called backs off; a timeout is retried later ---------------------


def test_model_target_unavailable_backs_the_lane_off_and_the_step_waits_for_it(fx: PipelineFixture) -> None:
    _enqueue(fx)
    clock = [time.time()]
    failing = [True]

    def run_step(ctx, store, claim):
        if failing[0]:
            raise steps.StepError("model_target_unavailable", "no such executable: /private/path/that/must/not/be/stored")
        return steps.run_step(ctx, store, claim)

    api = SimpleNamespace(input_digest=steps.input_digest, unchanged=steps.unchanged, run_step=run_step)
    first = _runner(fx, step_api=api, clock=lambda: clock[0]).drain()

    assert [(step["name"], step["outcome"], step["error_code"]) for step in first.steps] == [("tailor", "ready", "model_target_unavailable")]
    status = pipeline_status(fx.home_root, fx.target, clock=lambda: clock[0], busy=lambda: None)
    assert [(lane["lane"], lane["error_code"]) for lane in status["lanes"]] == [("ollama", "model_target_unavailable")]
    (tailor,) = [step for step in status["steps"] if step["name"] == "tailor"]
    assert (tailor["state"], tailor["waiting"], tailor["attempts"]) == ("ready", "lane_backoff", 0)
    store = PipelineStore(fx.db)
    assert store.used("pipeline_calls", runner_module.datetime.now().astimezone().date().isoformat()) == 0  # no call was made
    store.close()

    failing[0] = False
    assert _runner(fx, step_api=api, clock=lambda: clock[0]).drain().steps == []  # the lane is still backed off
    clock[0] += 301
    assert _runner(fx, step_api=api, clock=lambda: clock[0]).drain().state == DRAIN_RAN
    assert _states(fx) == dict.fromkeys(STEPS, "done")
    assert pipeline_status(fx.home_root, fx.target, clock=lambda: clock[0], busy=lambda: None)["lanes"] == []


def test_a_model_timeout_is_retried_after_a_backoff_and_no_message_is_stored(fx: PipelineFixture) -> None:
    _enqueue(fx)
    clock = [time.time()]
    fx.model.fail_next = TimeoutError("timed out reading /private/secret/path")

    first = _runner(fx, clock=lambda: clock[0]).drain()

    assert [(step["name"], step["outcome"], step["error_code"]) for step in first.steps] == [("tailor", "ready", "assess_timeout")]
    assert b"secret" not in fx.db.read_bytes()
    assert _runner(fx, clock=lambda: clock[0]).drain().steps == []  # 60 s backoff
    clock[0] += 61
    assert _runner(fx, clock=lambda: clock[0]).drain().state == DRAIN_RAN
    assert _states(fx) == dict.fromkeys(STEPS, "done")


# --- the server's thread -------------------------------------------------------------------------------


def test_the_runner_thread_runs_enqueued_work_on_a_kick_and_stops_cleanly(fx: PipelineFixture) -> None:
    runner = _runner(fx, poll_seconds=3600.0)
    runner.start()
    try:
        assert runner.alive and [thread.name for thread in threading.enumerate()].count("scout-pipeline") == 1
        _enqueue(fx)
        runner.kick()
        deadline = time.monotonic() + 60
        while _states(fx) != dict.fromkeys(STEPS, "done"):
            assert time.monotonic() < deadline, _states(fx)
            time.sleep(0.02)
    finally:
        assert runner.stop(timeout=30) is True
    assert runner.alive is False and "scout-pipeline" not in [thread.name for thread in threading.enumerate()]
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1)


def test_the_server_starts_the_pipeline_thread_beside_the_refresh_threads_and_shutdown_ends_it(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    backend = ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target)

    plain = serve(backend=backend, bind=("127.0.0.1", 0))
    try:
        assert plain.pipeline_runner is None
    finally:
        plain.server_close()

    server = serve(backend=backend, bind=("127.0.0.1", 0), background_refresh=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        runner = server.pipeline_runner
        assert runner is not None and runner.alive and server.refresh_ticker.alive
        assert (runner.home_root, runner.target) == (backend.home_root, backend.target)
        assert [thread.name for thread in threading.enumerate()].count("scout-pipeline") == 1
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
    assert runner.alive is False and "scout-pipeline" not in [thread.name for thread in threading.enumerate()]


def test_a_look_with_nothing_enqueued_creates_no_pipeline_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_pipeline_fixture(tmp_path, monkeypatch, base=False)

    assert PipelineRunner(home_root=fx.home_root, target=fx.target, busy=lambda: None).drain().state == DRAIN_IDLE
    status = pipeline_status(fx.home_root, fx.target, busy=lambda: None)
    assert (status["steps"], status["counts"], status["calls_today"]["used"]) == ([], {}, 0)
    assert list((fx.home_root / "scout").rglob("pipeline.sqlite")) == []


# --- the CLI ---------------------------------------------------------------------------------------------


def test_cli_run_once_cancel_retry_and_status(fx: PipelineFixture) -> None:
    cli = CliRunner()

    def call(*args: str) -> dict[str, object]:
        result = cli.invoke(scout_group, fx.cli(*args))
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    assert call("run", "--once")["state"] == DRAIN_IDLE  # nothing was ever enqueued
    needs_once = cli.invoke(scout_group, fx.cli("run"))
    assert needs_once.exit_code == 1 and json.loads(needs_once.output)["error"]["code"] == "invalid_value"

    _enqueue(fx)
    assert call("cancel", JOB) == {"ok": True, "action": "cancel", "profile_id": fx.profile_id, "job": JOB, "steps": 4}
    assert call("run", "--once")["steps"] == [] and fx.model.calls == 0
    assert {step["name"]: step["state"] for step in call("status", "--job", JOB)["steps"]} == dict.fromkeys(STEPS, "cancelled")

    assert call("retry", JOB, "--step", "tailor")["steps"] == 1
    assert call("retry", JOB)["steps"] == 3
    first = call("run", "--once", "--max-steps", "1")
    assert [(step["name"], step["outcome"]) for step in first["steps"]] == [("tailor", "done")] and first["model_calls"] == 1
    rest = call("run", "--once")
    assert rest["state"] == DRAIN_RAN and sorted(step["name"] for step in rest["steps"]) == ["ats", "label", "reassess"]

    status = call("status", "--job", JOB, "--profile", fx.profile_id)
    assert status["counts"] == {"done": 4} and status["outputs"]["label"]["name"] == "Scout label"
    assert [run["name"] for run in status["runs"] if run["model"] == "fixture-model"] == ["tailor", "reassess"]
    everything = call("status")
    assert "outputs" not in everything and len(everything["steps"]) == 4

    plain = cli.invoke(scout_group, [*fx.cli("run", "--once")[:-1]])
    assert plain.exit_code == 0 and plain.output.strip() == "Nothing to run."


def test_process_of_a_second_job_runs_only_that_job(fx: PipelineFixture) -> None:
    other = "https://jobs.example.test/acme/second"
    assess_base(fx, other)
    _enqueue(fx)  # waits: nobody ran the first job

    result = CliRunner().invoke(scout_group, fx.cli("process", other))

    assert result.exit_code == 0, result.output
    assert {step["job"] for step in json.loads(result.output)["drain"]["steps"]} == {other}
    assert _states(fx, other) == dict.fromkeys(STEPS, "done") and _states(fx)["tailor"] == "ready"
