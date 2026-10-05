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
from tests.support.scout_servers import scout_test_servers, stop_test_servers

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
    # 0110-028: several tests here delete a server's state file on purpose
    # (the "moved home"), so the supervisor's stop above cannot find that
    # server. When such a test fails before its takeover, the server stayed
    # up for days. Whatever Scout server of this test's home is still alive
    # is stopped by its pid, found by its command line.
    stop_test_servers(scout_test_servers(under=tmp_path))


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
        assert f"port {port} is in use by pid " in result.output
        assert "which is not a Scout server; stop it or pass --port" in result.output
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
        assert f"port {port} is in use by pid " in result.output
        assert "which is not a Scout server; stop it or pass --port" in result.output
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


# --- 0.1.10 item 3: an older Scout server from a moved home holds the port ----


def _spawn_listener(port: int, *, argv_tail: tuple[str, ...] = ()) -> subprocess.Popen[bytes]:
    """A stranger that listens on ``port`` (plain sockets, never answers HTTP)."""

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket, time\n"
            "listener = socket.socket()\n"
            "listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"listener.bind(('127.0.0.1', {port}))\n"
            "listener.listen(5)\n"
            "print('listening', flush=True)\n"
            "time.sleep(300)\n",
            *argv_tail,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == b"listening"
    return process


def test_run_stops_a_verified_older_scout_server_from_a_moved_home(projects) -> None:
    """The operator's case: a real Scout server whose home no longer records it."""

    home, project_a, project_b = projects
    port = _free_port()
    first = _scout_json(home, project_a, "run", "--port", str(port), "--no-browser")
    old_pid = int(first["pid"])  # type: ignore[arg-type]
    _state_path(home, project_a).unlink()  # the moved home: nothing records this server any more
    assert run_supervisor._other_live_servers(home, "someone-else") == ()

    result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

    assert result.exit_code == 0, result.output
    assert STOPPED_OPENING not in result.output
    assert (
        f"Stopped an older Scout server (pid {old_pid}, started with --home {home} "
        f"--target {project_a}) that was using port {port}"
    ) in result.output
    assert f"Scout is running at http://127.0.0.1:{port}" in result.output
    assert _wait_until_gone(old_pid)
    assert _scout_json(home, project_b, "status")["state"] == "running"


def test_json_reports_stopped_server_when_stopped_and_null_when_not(projects) -> None:
    home, project_a, project_b = projects
    port = _free_port()
    first = _scout_json(home, project_a, "run", "--port", str(port), "--no-browser")
    assert first["stopped_server"] is None
    old_pid = int(first["pid"])  # type: ignore[arg-type]
    _state_path(home, project_a).unlink()

    raw = _scout(home, project_b, "run", "--port", str(port), "--no-browser", "--json")
    assert raw.exit_code == 0, raw.output
    # The human line goes to stderr (CliRunner mixes it in); stdout is the JSON object.
    assert "Stopped an older Scout server" in raw.output
    second = json.loads([line for line in raw.output.splitlines() if line.startswith("{")][-1])

    assert second["stopped_server"] == {"pid": old_pid, "home": str(home), "target": str(project_a)}
    assert second["stopped_other"] is None
    assert _scout_json(home, project_b, "run", "--port", str(port), "--no-browser")["stopped_server"] is None
    assert _scout_json(home, project_b, "install")["stopped_server"] is None


def test_a_non_scout_listener_is_not_killed_and_the_message_names_it(projects) -> None:
    home, _, project_b = projects
    port = _free_port()
    stranger = _spawn_listener(port)
    try:
        result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

        assert result.exit_code != 0
        assert f"port {port} is in use by pid {stranger.pid} (" in result.output
        assert "which is not a Scout server; stop it or pass --port" in result.output
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait(timeout=5.0)


def test_a_process_naming_present_api_that_fails_the_identity_check_is_not_killed(projects) -> None:
    home, _, project_b = projects
    port = _free_port()
    # Its argv reads `-m gigai.scout.find_jobs.present_api` but it never answers Scout's identity.
    impostor = _spawn_listener(port, argv_tail=("-m", "gigai.scout.find_jobs.present_api"))
    try:
        assert "-m gigai.scout.find_jobs.present_api" in (run_supervisor._command_line_for_pid(impostor.pid) or "")

        result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

        assert result.exit_code != 0
        assert "which is not a Scout server; stop it or pass --port" in result.output
        assert impostor.poll() is None
    finally:
        impostor.kill()
        impostor.wait(timeout=5.0)


def test_a_command_line_that_changes_before_the_signal_is_not_signalled(
    projects, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, project_b = projects
    port = _free_port()
    # The supervised shape, naming this home: the same-home rule (0.1.10.11 NC) lets it through to the re-check.
    stranger = _spawn_listener(
        port, argv_tail=("-m", "gigai.scout.find_jobs.present_api", "--home", str(home), "--target", str(project_b))
    )
    real = run_supervisor._command_line_for_pid
    reads: list[int] = []

    def racing(pid: int) -> str | None:
        reads.append(pid)
        line = real(pid)
        return line if len(reads) == 1 else f"{line} (pid reused)"

    signals: list[tuple[int, int]] = []
    try:
        with monkeypatch.context() as patched:
            patched.setattr(run_supervisor, "_command_line_for_pid", racing)
            patched.setattr(run_supervisor, "_answers_scout_identity", lambda _port, **_kw: True)
            patched.setattr(run_supervisor.os, "kill", lambda pid, sig: signals.append((pid, sig)))
            with pytest.raises(run_supervisor.ScoutRunError):
                run_supervisor._stop_verified_older_scout(port, home_root=home.resolve())
        assert len(reads) >= 2, "the command line must be re-read before signalling"
        assert signals == []
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait(timeout=5.0)


def _serve_json(routes: dict[str, tuple[int, object]]):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            status, body = routes.get(self.path, (404, {"error": "not_found"}))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize(
    ("routes", "expected"),
    [
        # 0.1.9.x: /api is 404, /api/secrets/status answers Scout's legacy schema.
        ({"/api/secrets/status": (200, {"schema_version": "scout-secrets-status:1", "keys": {}})}, True),
        # 0.1.10+: the agent API index.
        ({"/api": (200, {"schema_version": "scout-api-index:1"})}, True),
        # Generic health, an unrelated schema, or an error status never counts.
        ({"/api/health": (200, {"status": "ok"})}, False),
        ({"/api/secrets/status": (200, {"schema_version": "other:1"})}, False),
        ({"/api/secrets/status": (500, {"schema_version": "scout-secrets-status:1"})}, False),
        ({}, False),
    ],
)
def test_identity_accepts_the_0110_index_or_the_legacy_0191_answer(routes, expected: bool) -> None:
    server = _serve_json(routes)
    try:
        assert run_supervisor._answers_scout_identity(server.server_address[1]) is expected
    finally:
        server.shutdown()
        server.server_close()


# --- 0.1.10.11 NC: `run` stops only a Scout of the SAME home ----------------
#
# The holder of the port can be a real Scout server that ANOTHER GigAI home
# started (a scratch home beside the operator's own). `run` never signals it:
# it refuses in one line. These tests use two scratch homes under ``tmp_path``
# and a free port in the 18xxx range.


def _free_port_18xxx() -> int:
    import random

    first = random.randrange(18100, 18900)
    for port in [*range(first, 19000), *range(18100, first)]:
        if run_supervisor._port_is_free(port):
            return port
    raise AssertionError("no free port between 18100 and 18999")


def _scratch_env(user_home: Path) -> dict[str, str]:
    """The environment of a real ``gigai`` process whose user home is a scratch folder."""

    env = {name: value for name, value in os.environ.items() if not name.startswith("GIGAI_HOME")}
    env["HOME"] = str(user_home)
    return env


def _gigai_process(user_home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """A real ``gigai ...`` process (not the in-process runner): its exit code and stderr as a terminal gets them."""

    return subprocess.run(
        [sys.executable, "-c", "from gigai.cli import cli; cli(prog_name='gigai')", *args],
        cwd=user_home,
        env=_scratch_env(user_home),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=latency_bound(60.0),
        check=False,
    )


@pytest.fixture
def two_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two scratch GigAI homes, each under its own scratch user home, both as new as a first ``gigai scout run``
    finds them (no ``gigai setup``, no ``gigai init``): A's Scout is the one already running."""

    user_a = tmp_path / "user-a"
    user_b = tmp_path / "user-b"
    user_a.mkdir()
    user_b.mkdir()
    monkeypatch.delenv("GIGAI_HOME", raising=False)
    monkeypatch.setenv("HOME", str(user_a))
    yield user_a / ".gigai", user_b / ".gigai", user_b
    # Found by command line (the server module AND a --home inside this test's tmp_path), then stopped by pid.
    stop_test_servers(scout_test_servers(under=tmp_path))


def _bare(home: Path, command: str, *args: str) -> Result:
    """``gigai scout <command>`` with only --home: no --target, the way the starter prompt writes ``run``."""

    return CliRunner().invoke(cli, ["scout", command, *args, "--home", str(home)])


def _bare_json(home: Path, command: str, *args: str) -> dict[str, object]:
    result = _bare(home, command, *args, "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_run_never_signals_another_homes_scout_and_refuses_in_one_line(two_homes) -> None:
    """Home A's Scout holds port P; a bare ``gigai scout run --port P`` of home B leaves it alone."""

    home_a, home_b, user_b = two_homes
    port = _free_port_18xxx()
    first = _bare_json(home_a, "run", "--port", str(port), "--no-browser")
    pid_a = int(first["pid"])  # type: ignore[arg-type]
    state_a = _state_path(home_a, home_a / "scout")
    assert state_a.is_file()

    # A real second process, as the starter prompt writes it (no --target), from the other home.
    done = _gigai_process(user_b, "scout", "run", "--no-browser", "--port", str(port), "--home", str(home_b))

    # run_supervisor's check, not os.kill(pid, 0) alone: A's server is this test process's child, and a stopped
    # child that nobody has waited for still "exists".
    assert run_supervisor._process_is_alive(pid_a), f"home B's run stopped home A's Scout (pid {pid_a}):\n{done.stderr}"
    assert run_supervisor._listening_pids(port) == [pid_a], "the port is still home A's"
    assert httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=latency_bound(5.0)).status_code == 200
    assert state_a.is_file(), "home A's run state is home A's"
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert "Scout is running" not in done.stdout  # stdout: only the new home's own setup lines
    assert done.stderr.splitlines() == [
        f"Error: port {port} is held by the Scout of another GigAI home ({home_a}, pid {pid_a}), "
        "which `gigai scout run` never stops; pass --port to choose a different one"
    ]
    assert "Stopped" not in done.stderr
    assert not list((home_b / "run" / "scout").glob("*.json")), "home B started nothing"

    # --json: the same refusal as the error object, same code as every other "port in use".
    raw = _bare(home_b, "run", "--port", str(port), "--no-browser", "--json")
    assert raw.exit_code == 1, raw.output
    error = json.loads(raw.output)
    assert error["status"] == "error"
    assert error["error"]["code"] == "scout_run_port_in_use"
    assert error["error"]["message"] == done.stderr.strip().removeprefix("Error: ")
    assert run_supervisor._process_is_alive(pid_a)

    # What the line says to do works, and both homes' Scouts then run side by side.
    other_port = _free_port_18xxx()
    second = _bare_json(home_b, "run", "--port", str(other_port), "--no-browser")
    assert second["stopped_server"] is None and second["stopped_other"] is None
    assert run_supervisor._process_is_alive(pid_a)
    assert run_supervisor._process_is_alive(int(second["pid"]))  # type: ignore[arg-type]
    assert run_supervisor._listening_pids(port) == [pid_a]
    assert _bare_json(home_a, "status")["state"] == "running"


def test_a_scout_whose_command_line_does_not_say_its_home_is_never_signalled(projects, tmp_path: Path) -> None:
    """Unknown home is never "the same home": a Scout started by hand, without --home, is left running."""

    home, project_a, project_b = projects
    port = _free_port_18xxx()
    run_supervisor.ensure_scout_ready(home_root=home, requested_target=project_a)
    by_hand = subprocess.Popen(
        [sys.executable, "-m", "gigai.scout.find_jobs.present_api", "--target", str(project_a), "--port", str(port)],
        env=_scratch_env(home.parent),  # no --home: the server takes <scratch user home>/.gigai
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + latency_bound(15.0)
        while not run_supervisor._answers_scout_identity(port) and time.monotonic() < deadline:
            assert by_hand.poll() is None, "the hand-started Scout exited"
            time.sleep(0.1)
        assert run_supervisor._answers_scout_identity(port)

        result = _scout(home, project_b, "run", "--port", str(port), "--no-browser")

        assert by_hand.poll() is None, result.output
        assert result.exit_code == 1, result.output
        assert result.output.splitlines() == [
            f"Error: port {port} is held by a Scout server (pid {by_hand.pid}) whose command line does not say "
            "which GigAI home it serves, so `gigai scout run` does not stop it; stop it yourself or pass --port "
            "to choose a different one"
        ]
    finally:
        by_hand.kill()
        by_hand.wait(timeout=5.0)


@pytest.mark.parametrize(
    ("command_line", "expected"),
    [
        ("python -m gigai.scout.find_jobs.present_api --home /h/.gigai --target /h/scout --port 18765", "/h/.gigai"),
        # A path with spaces is read whole (the process list joins argv with spaces).
        ("py -m gigai.scout.find_jobs.present_api --home /h/My Home/.gigai --target /t --port 1", "/h/My Home/.gigai"),
        ("py -m gigai.scout.find_jobs.present_api --home /h/.gigai copy --target /t --port 1", "/h/.gigai copy"),
        # Not the shape the supervisor starts: the home is unknown.
        ("python -m gigai.scout.find_jobs.present_api --port 18765", None),
        ("python -m gigai.scout.find_jobs.present_api --target /h/scout --port 18765", None),
        ("python -m gigai.scout.find_jobs.present_api --home /h/.gigai --port 18765", None),
        ("gigai scout run --foreground --home /h/.gigai --target /h/scout", None),
    ],
)
def test_the_home_of_a_server_is_read_from_the_supervised_command_line(command_line: str, expected: str | None) -> None:
    assert run_supervisor._server_home(command_line) == expected


def test_same_home_means_the_same_folder_never_a_name_that_starts_alike(tmp_path: Path) -> None:
    home = (tmp_path / "user" / ".gigai").resolve()
    home.mkdir(parents=True)
    (tmp_path / "link").symlink_to(home, target_is_directory=True)

    assert run_supervisor._is_same_home(str(home), home)
    assert run_supervisor._is_same_home(str(tmp_path / "link"), home), "a path that resolves to this home is this home"
    assert not run_supervisor._is_same_home(f"{home} copy", home)
    assert not run_supervisor._is_same_home(str(home.parent), home)
    assert not run_supervisor._is_same_home(str(home / "scout"), home)
    assert not run_supervisor._is_same_home(".gigai", home), "a relative path says nothing about whose home it is"
    assert not run_supervisor._is_same_home(None, home)
