"""``gigai scout run|stop|status`` (packet C of `gigai scout run`, U13/U23).

These tests really start and stop the supervised child process (a real
``python -m gigai.scout.find_jobs.present_api``) against a temp GIGAI_HOME, a
temp non-git target, and an ephemeral port -- never the operator's real
``~/.gigai`` and never a live Exa/ATS/provider call (the server only serves
config/UI; no run is ever started against a provider).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import run_supervisor


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _setup_and_init(tmp_path: Path) -> tuple[Path, Path]:
    """Same non-interactive ``gigai setup`` + ``gigai init`` flow packet B used."""

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)

    runner = CliRunner()
    setup_result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint",
            "remote=openai_api:provider:https://api.example.test",
            "--model-target",
            "remote=remote:smoke-test",
            "--create-model-target",
            "remote",
            "--json",
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output

    init_result = runner.invoke(
        cli,
        ["init", "--home", str(home), "--target", str(target), "--username", "scout-run-test", "--json"],
    )
    assert init_result.exit_code == 0, init_result.output
    return home, target


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_gone(pid: int, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def bound_project(tmp_path: Path) -> tuple[Path, Path]:
    return _setup_and_init(tmp_path)


@pytest.fixture
def stop_after(bound_project):
    """Ensure the supervised child is always stopped, even if a test fails."""

    home, target = bound_project
    yield home, target
    run_supervisor.stop(home_root=home, requested_target=target)


def _run_cli(home: Path, target: Path, *args: str) -> dict[str, object]:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["scout", *args, "--home", str(home), "--target", str(target), "--json"],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_run_then_status_running_then_ui_and_config_then_stop(stop_after) -> None:
    home, target = stop_after
    port = _free_port()

    run_payload = _run_cli(home, target, "run", "--port", str(port), "--no-browser")
    assert run_payload["ok"] is True
    assert run_payload["reused"] is False
    assert run_payload["port"] == port
    pid = int(run_payload["pid"])  # type: ignore[arg-type]
    assert _process_is_alive(pid)

    status_payload = _run_cli(home, target, "status")
    assert status_payload["state"] == "running"
    assert status_payload["pid"] == pid
    assert status_payload["url"] == f"http://127.0.0.1:{port}"

    response = httpx.get(f"http://127.0.0.1:{port}/")
    assert response.status_code == 200
    assert "<div id=\"root\">" in response.text or "<!doctype html>" in response.text.lower()

    config_response = httpx.get(f"http://127.0.0.1:{port}/api/config")
    assert config_response.status_code == 200
    body = config_response.json()
    assert body["schema_version"] == "scout-find-jobs-config-response:1"
    assert body["resume_preview"] is None
    assert body["resume_missing_hint"] == "gigai scout resume add <file>"

    health_response = httpx.get(f"http://127.0.0.1:{port}/api/health")
    assert health_response.status_code == 200

    # find-jobs.json was written as a starter (never overwritten, per install_scout).
    assert (target / "find-jobs.json").is_file()

    stop_payload = _run_cli(home, target, "stop")
    assert stop_payload["stopped"] is True
    assert _wait_until_gone(pid)

    final_status = _run_cli(home, target, "status")
    assert final_status["state"] == "stopped"


def test_second_run_reuses_the_running_instance(stop_after) -> None:
    home, target = stop_after
    port = _free_port()

    first = _run_cli(home, target, "run", "--port", str(port), "--no-browser")
    assert first["reused"] is False
    pid = int(first["pid"])  # type: ignore[arg-type]

    second = _run_cli(home, target, "run", "--port", str(port), "--no-browser")
    assert second["reused"] is True
    assert second["pid"] == pid
    assert second["port"] == port
    assert _process_is_alive(pid)


def test_stop_is_idempotent_when_not_running(bound_project) -> None:
    home, target = bound_project
    payload = _run_cli(home, target, "stop")
    assert payload["stopped"] is False


def test_status_is_stopped_from_a_clean_project(bound_project) -> None:
    home, target = bound_project
    payload = _run_cli(home, target, "status")
    assert payload["state"] == "stopped"
    assert payload["pid"] is None


def _setup_only(tmp_path: Path) -> Path:
    """Non-interactive ``gigai setup`` with NO ``gigai init`` and no bound target."""

    home = tmp_path / "home"
    runner = CliRunner()
    setup_result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint",
            "remote=openai_api:provider:https://api.example.test",
            "--model-target",
            "remote=remote:smoke-test",
            "--create-model-target",
            "remote",
            "--json",
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output
    return home


def test_scout_run_from_an_unregistered_folder_with_no_target_creates_home_scout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uat-bug-002: no ``--target``, cwd an empty unregistered folder, no prior
    ``gigai init`` -- ``scout install`` -> ``resume add`` -> ``run --no-browser``
    must succeed by creating and binding ``<home>/scout``, not by demanding
    ``--target`` for an implicit non-Git cwd (958306c's behavior).
    """

    home = _setup_only(tmp_path)
    cwd = tmp_path / "empty-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    resume_source = tmp_path / "resume.md"
    resume_source.write_text("Software engineer with Python service experience.\n", encoding="utf-8")

    runner = CliRunner()

    install_result = runner.invoke(cli, ["scout", "install", "--home", str(home), "--json"])
    assert install_result.exit_code == 0, install_result.output
    install_payload = json.loads(install_result.output)
    assert install_payload["bound"] is True

    scout_home_target = home / "scout"
    assert scout_home_target.is_dir()

    resume_result = runner.invoke(
        cli, ["scout", "resume", "add", str(resume_source), "--home", str(home), "--json"]
    )
    assert resume_result.exit_code == 0, resume_result.output

    # Disable every live source so `run` never makes a network call.
    config_path = scout_home_target / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["sources"] = {"exa": False, "ats": False, "hiringcafe": False}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    port = _free_port()
    try:
        run_result = runner.invoke(
            cli, ["scout", "run", "--no-browser", "--port", str(port), "--home", str(home), "--json"]
        )
        assert run_result.exit_code == 0, run_result.output
        run_payload = json.loads(run_result.output)
        assert run_payload["ok"] is True
        pid = int(run_payload["pid"])  # type: ignore[arg-type]
        assert _process_is_alive(pid)
    finally:
        stop_result = runner.invoke(cli, ["scout", "stop", "--home", str(home), "--json"])
        assert stop_result.exit_code == 0, stop_result.output


def test_stale_state_file_is_cleaned_and_a_fresh_instance_starts(stop_after) -> None:
    home, target = stop_after
    port = _free_port()

    # Plant a state file that points at a pid that can't be running.
    run_supervisor.ensure_scout_ready(home_root=home, requested_target=target)
    from gigai.workpad import resolve_bound_project

    bound = resolve_bound_project(home_root=home, requested_target=target)
    stale = run_supervisor.ScoutRunState(
        project_id=bound.project_id,
        pid=999_999_999,
        port=port,
        url=f"http://127.0.0.1:{port}",
        log_path=str(home / "logs" / "stale.log"),
        started_at="2020-01-01T00:00:00+00:00",
    )
    run_supervisor._write_state(home, stale)
    assert not _process_is_alive(stale.pid)

    status_before = _run_cli(home, target, "status")
    assert status_before["state"] == "crashed"

    run_payload = _run_cli(home, target, "run", "--port", str(port), "--no-browser")
    assert run_payload["cleaned_stale"] is True
    assert run_payload["reused"] is False
    assert run_payload["pid"] != stale.pid
    assert _process_is_alive(int(run_payload["pid"]))  # type: ignore[arg-type]


def test_stop_and_status_do_not_trust_a_pid_reused_by_an_unrelated_process(bound_project) -> None:
    """P1 (pr37-review-findings.md #9): a state file's pid can outlive our
    server and be reused by the OS for an unrelated process. stop/status must
    confirm the pid is actually our Scout server (identity, not just
    liveness) before treating it as running or signalling it.
    """

    home, target = bound_project
    port = _free_port()

    # A harmless long-lived process standing in for "OS reused our old pid".
    unrelated = subprocess.Popen(
        ["sleep", "300"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        run_supervisor.ensure_scout_ready(home_root=home, requested_target=target)
        from gigai.workpad import resolve_bound_project

        bound = resolve_bound_project(home_root=home, requested_target=target)
        fake = run_supervisor.ScoutRunState(
            project_id=bound.project_id,
            pid=unrelated.pid,
            port=port,
            url=f"http://127.0.0.1:{port}",
            log_path=str(home / "logs" / "fake.log"),
            started_at="2020-01-01T00:00:00+00:00",
        )
        run_supervisor._write_state(home, fake)
        assert _process_is_alive(unrelated.pid)

        status_payload = _run_cli(home, target, "status")
        assert status_payload["state"] != "running"

        stop_payload = _run_cli(home, target, "stop")
        assert stop_payload["stopped"] is False

        # The unrelated process must still be alive -- never signalled.
        assert _process_is_alive(unrelated.pid)

        # The stale state must have been cleaned up.
        final_status = _run_cli(home, target, "status")
        assert final_status["state"] == "stopped"
        assert final_status["pid"] is None
    finally:
        unrelated.terminate()
        try:
            unrelated.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            unrelated.kill()
            unrelated.wait(timeout=5.0)


def test_health_check_failure_reports_log_and_exits_nonzero(bound_project, monkeypatch) -> None:
    home, target = bound_project
    port = _free_port()

    # Force the health probe to always fail so start() gives up and reports
    # the log path/tail instead of hanging for the full timeout.
    monkeypatch.setattr(run_supervisor, "HEALTH_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(run_supervisor, "_health_ok", lambda *_a, **_k: False)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["scout", "run", "--home", str(home), "--target", str(target), "--port", str(port), "--no-browser"],
    )
    assert result.exit_code != 0

    status_after = run_supervisor.status(home_root=home, requested_target=target)
    assert status_after.state == "stopped"


def test_no_orphan_process_left_after_health_failure(bound_project, monkeypatch) -> None:
    home, target = bound_project
    port = _free_port()

    started_pids: list[int] = []
    real_popen = subprocess.Popen

    def _tracking_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started_pids.append(process.pid)
        return process

    monkeypatch.setattr(run_supervisor.subprocess, "Popen", _tracking_popen)
    monkeypatch.setattr(run_supervisor, "_health_ok", lambda *_a, **_k: False)
    monkeypatch.setattr(run_supervisor, "HEALTH_TIMEOUT_SECONDS", 1.0)

    with pytest.raises(run_supervisor.ScoutRunError) as excinfo:
        run_supervisor.start(
            home_root=home,
            requested_target=target,
            port=port,
            foreground=False,
            open_browser=False,
        )
    assert "scout_run_health_check_failed" == excinfo.value.code

    assert started_pids, "expected the supervisor to have started a child"
    assert _wait_until_gone(started_pids[0])
