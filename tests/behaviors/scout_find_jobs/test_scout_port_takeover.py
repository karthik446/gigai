"""uat-bug-019: an older project's Scout server holds the port.

After uat-bug-017 moved Scout to ``<home>/scout`` (a new project), the
earlier project's live server still held the default port and
``gigai scout run`` failed with "port ... is already in use". ``run`` now
stops a live Scout server recorded for ANOTHER project when it holds the
requested port, and says so in one line; anything else on the port keeps the
error. ``status`` only reports such a server, it never stops one.

Like ``test_scout_run_supervisor.py`` these tests start and stop real
supervised child processes against a temp GigAI home, temp targets, a temp
``HOME`` and ephemeral ports -- never the operator's ``~/.gigai``, their live
Scout, or port 8765 -- and make no provider or network call.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner, Result

from gigai.cli import cli
from gigai.scout import run_supervisor, target_resolution
from gigai.workpad import resolve_bound_project
from tests.support.latency import latency_bound

STOPPED_OPENING = "Stopped the Scout server for"


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _setup(user_home: Path) -> Path:
    """Non-interactive ``gigai setup`` with GigAI's home at ``<user home>/.gigai``."""

    home = user_home / ".gigai"
    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(user_home / "workpads"),
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
    assert result.exit_code == 0, result.output
    return home


def _init(home: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    result = CliRunner().invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--username", "takeover-test", "--json"]
    )
    assert result.exit_code == 0, result.output
    return target


def _scout(home: Path, target: Path, *args: str) -> Result:
    """One ``gigai scout ...`` command, plain text, as the operator runs it."""

    return CliRunner().invoke(cli, ["scout", *args, "--home", str(home), "--target", str(target)])


def _scout_json(home: Path, target: Path, *args: str) -> dict[str, object]:
    result = _scout(home, target, *args, "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_gone(pid: int, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + latency_bound(timeout)
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return True
        time.sleep(0.05)
    return False


def _state_path(home: Path, target: Path) -> Path:
    bound = resolve_bound_project(home_root=home, requested_target=target)
    return run_supervisor._state_path(home, bound.project_id)


def _record_server(home: Path, target: Path, *, pid: int, port: int) -> Path:
    """A state file saying ``pid`` is ``target``'s Scout server on ``port``."""

    run_supervisor.ensure_scout_ready(home_root=home, requested_target=target)
    bound = resolve_bound_project(home_root=home, requested_target=target)
    run_supervisor._write_state(
        home,
        run_supervisor.ScoutRunState(
            project_id=bound.project_id,
            pid=pid,
            port=port,
            url=f"http://127.0.0.1:{port}",
            log_path=str(home / "logs" / "recorded.log"),
            started_at="2020-01-01T00:00:00+00:00",
            gigai_version=run_supervisor._installed_gigai_version(),
            package_path=run_supervisor._installed_package_path(),
            build_id=run_supervisor._installed_build_id(),
        ),
    )
    return run_supervisor._state_path(home, bound.project_id)


@pytest.fixture
def projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """GigAI home plus two bound projects: A is the operator's earlier ``~/scout``."""

    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    home = _setup(user_home)
    project_a = _init(home, user_home / "scout")
    project_b = _init(home, user_home / "project-b")
    yield home, project_a, project_b
    for target in (project_a, project_b):
        run_supervisor.stop(home_root=home, requested_target=target)


def test_run_stops_another_projects_live_server_that_holds_the_port(projects) -> None:
    home, project_a, project_b = projects
    port = _free_port()

    first = _scout_json(home, project_a, "run", "--port", str(port), "--no-browser")
    pid_a = int(first["pid"])  # type: ignore[arg-type]
    state_a = _state_path(home, project_a)
    assert _process_is_alive(pid_a)
    assert state_a.is_file()

    result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

    assert result.exit_code == 0, result.output
    assert result.output.count(STOPPED_OPENING) == 1
    assert (
        f"Stopped the Scout server for ~/scout (pid {pid_a}) so this one can use port {port}."
        in result.output.splitlines()
    )
    assert f"Scout is running at http://127.0.0.1:{port}" in result.output

    assert _wait_until_gone(pid_a), "project A's server must have been stopped"
    assert not state_a.exists(), "project A's state file must be removed, as `scout stop` does"

    status_b = _scout_json(home, project_b, "status")
    assert status_b["state"] == "running"
    assert status_b["url"] == f"http://127.0.0.1:{port}"
    assert status_b["pid"] != pid_a
    assert _process_is_alive(int(status_b["pid"]))  # type: ignore[arg-type]
    health = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=latency_bound(5.0))
    assert health.status_code == 200

    assert _scout_json(home, project_a, "status")["state"] == "stopped"


def test_a_plain_listener_on_the_port_keeps_todays_error(projects) -> None:
    home, _, project_b = projects
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

        assert result.exit_code != 0
        assert f"Error: port {port} is already in use; pass --port to choose a different one" in result.output
        assert STOPPED_OPENING not in result.output
        # The listener was not touched: it still accepts a connection.
        with socket.create_connection(("127.0.0.1", port), timeout=latency_bound(2.0)):
            pass
    finally:
        listener.close()
    assert _scout_json(home, project_b, "status")["state"] == "stopped"


def test_a_recorded_pid_that_is_not_our_server_keeps_todays_error_and_is_not_killed(projects) -> None:
    """A state file for project A names a live process that holds the port but is not Scout."""

    home, project_a, project_b = projects
    port = _free_port()
    stranger = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket, sys, time\n"
            "listener = socket.socket()\n"
            "listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"listener.bind(('127.0.0.1', {port}))\n"
            "listener.listen(1)\n"
            "print('listening', flush=True)\n"
            "time.sleep(300)\n",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert stranger.stdout is not None
        assert stranger.stdout.readline().strip() == b"listening"
        state_a = _record_server(home, project_a, pid=stranger.pid, port=port)

        result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

        assert result.exit_code != 0
        assert f"Error: port {port} is already in use; pass --port to choose a different one" in result.output
        assert STOPPED_OPENING not in result.output
        assert stranger.poll() is None, "a process that is not our Scout server must never be signalled"
        assert state_a.is_file(), "another project's state file is that project's to clean up"
    finally:
        stranger.kill()
        stranger.wait(timeout=5.0)


def test_target_of_the_live_project_reuses_its_server_and_leaves_the_others_alone(projects) -> None:
    home, project_a, project_b = projects
    port_a = _free_port()
    port_b = _free_port()

    first_a = _scout_json(home, project_a, "run", "--port", str(port_a), "--no-browser")
    first_b = _scout_json(home, project_b, "run", "--port", str(port_b), "--no-browser")
    assert first_b["stopped_other"] is None

    again = _scout(home, project_a, "run", "--port", str(port_a), "--no-browser")

    assert again.exit_code == 0, again.output
    assert STOPPED_OPENING not in again.output
    assert f"Scout is already running at http://127.0.0.1:{port_a}" in again.output
    again_json = _scout_json(home, project_a, "run", "--port", str(port_a), "--no-browser")
    assert again_json["reused"] is True
    assert again_json["pid"] == first_a["pid"]
    assert again_json["stopped_other"] is None
    assert _process_is_alive(int(first_a["pid"]))  # type: ignore[arg-type]
    assert _process_is_alive(int(first_b["pid"]))  # type: ignore[arg-type]
    assert _scout_json(home, project_b, "status")["pid"] == first_b["pid"]


def test_a_stale_state_file_of_another_project_is_ignored(projects) -> None:
    home, project_a, project_b = projects
    port = _free_port()
    state_a = _record_server(home, project_a, pid=999_999_999, port=port)
    assert not _process_is_alive(999_999_999)

    result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

    assert result.exit_code == 0, result.output
    assert STOPPED_OPENING not in result.output
    assert f"Scout is running at http://127.0.0.1:{port}" in result.output
    assert _scout_json(home, project_b, "status")["state"] == "running"
    # Project A's own commands still see and clean their state, as before.
    assert state_a.is_file()
    assert _scout_json(home, project_a, "status")["state"] == "crashed"


def test_run_json_names_the_server_it_stopped(projects) -> None:
    home, project_a, project_b = projects
    port = _free_port()
    first = _scout_json(home, project_a, "run", "--port", str(port), "--no-browser")

    second = _scout_json(home, project_b, "run", "--port", str(port), "--no-browser")

    assert second["port"] == port
    assert second["stopped_other"] == {
        "project_id": first["project_id"],
        "pid": first["pid"],
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "target": str(project_a),
    }
    assert _wait_until_gone(int(first["pid"]))  # type: ignore[arg-type]


def test_status_reports_another_projects_live_server_and_never_stops_it(projects) -> None:
    home, project_a, project_b = projects
    port = _free_port()
    first = _scout_json(home, project_a, "run", "--port", str(port), "--no-browser")
    pid_a = int(first["pid"])  # type: ignore[arg-type]

    result = _scout(home, project_b, "status")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "stopped" in lines
    assert [line for line in lines if "~/scout" in line] == [
        f"The Scout server for ~/scout is running at http://127.0.0.1:{port} (pid {pid_a}); "
        "`gigai scout run` stops it when it holds the port this one needs."
    ]
    assert STOPPED_OPENING not in result.output

    status_json = _scout_json(home, project_b, "status")
    assert status_json["state"] == "stopped"
    assert [other["pid"] for other in status_json["other_servers"]] == [pid_a]  # type: ignore[union-attr]
    assert _process_is_alive(pid_a)
    assert _state_path(home, project_a).is_file()
    # Project A's own status does not list itself as another project's server.
    assert _scout_json(home, project_a, "status")["other_servers"] == []


# --- The earlier-project notice: `use --target` keeps its space -----------------

NOTICE = (
    "Scout now lives in ~/.gigai/scout; your earlier data in ~/scout is untouched; "
    "use --target ~/scout to open it."
)


def _as_copied_from_a_terminal(printed: str, columns: int) -> str:
    """``printed`` the way a terminal ``columns`` wide gives it back when copied.

    The terminal breaks every line longer than its width into rows and, on
    copy, joins the rows of one line again with each row's trailing spaces
    dropped.
    """

    copied = []
    for line in printed.splitlines():
        rows = [line[start : start + columns] for start in range(0, len(line), columns)] or [""]
        copied.append("".join(row.rstrip() for row in rows))
    return "\n".join(copied)


@pytest.mark.parametrize("columns", [None, 40, 80, 81, 82, 200])
def test_the_printed_notice_keeps_the_space_in_use_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, columns: int | None
) -> None:
    """``columns`` is the terminal's width; ``None`` is output that is not a terminal.

    82 is the width the operator's line broke at: the space after ``use`` was
    the last cell of the first row.
    """

    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    home = _setup(user_home)
    earlier = _init(home, user_home / "scout")
    installed = CliRunner().invoke(
        cli, ["scout", "install", "--home", str(home), "--target", str(earlier), "--json"]
    )
    assert installed.exit_code == 0, installed.output
    monkeypatch.setattr(target_resolution, "_terminal_columns", lambda: columns, raising=False)

    result = CliRunner().invoke(cli, ["scout", "status", "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert result.output.count("Scout now lives in") == 1
    assert "use --target ~/scout" in result.output
    assert "use--target" not in result.output
    assert " ".join(result.output.split()).count(NOTICE) == 1
    if columns is None or columns > len(NOTICE):
        assert NOTICE in result.output.splitlines()
    else:
        start = result.output.index("Scout now lives in")
        end = result.output.index("to open it.") + len("to open it.")
        notice = result.output[start:end]
        assert all(len(line) <= columns for line in notice.splitlines()), notice
        copied = _as_copied_from_a_terminal(notice, columns)
        assert "use --target ~/scout" in copied
        assert "use--target" not in copied
