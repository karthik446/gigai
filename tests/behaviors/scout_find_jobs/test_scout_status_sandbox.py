"""0110-10-13 (5): ``gigai scout status`` keeps process evidence apart from API reachability.

The operator's agent ran ``gigai scout status`` inside its sandbox while Scout
was running, and was told "stopped". Two things a sandbox takes away: the
process list (the recorded pid's command line cannot be read) and localhost
(the health check gets no answer). Neither proves Scout is stopped, and a
run-state file with a live pid does not prove it is healthy.

The END outcomes, through the CLI (JSON and text), with a live process and a
port nothing listens on:

* a live recorded process whose API does not answer is ``unreachable``: never
  just "running" (a pid and a run-state file alone), never "stopped";
* a pid whose command line cannot be read is not called a stranger: the run
  state is kept (it used to be deleted) and what is known is said;
* the two kinds of evidence are separate fields (``process``, ``api``), with
  how the check failed (``refused``; ``not_permitted`` for a sandbox that
  blocks localhost);
* ``running`` needs both: the process alive and the API answering.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
from urllib.error import URLError

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import run_supervisor
from gigai.workpad import resolve_bound_project

from .test_scout_run_supervisor import _free_port, _setup_and_init


@pytest.fixture
def recorded(tmp_path: Path):
    """A bound project whose run state names a LIVE process that looks like the Scout server, on a port nothing listens on."""

    home, target = _setup_and_init(tmp_path)
    run_supervisor.ensure_scout_ready(home_root=home, requested_target=target)
    port = _free_port()
    # The detached server's own shape on a command line (the module marker), doing nothing: no socket is opened.
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)", "gigai.scout.find_jobs.present_api", "--port", str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    bound = resolve_bound_project(home_root=home, requested_target=target)
    state = run_supervisor.ScoutRunState(
        project_id=bound.project_id, pid=process.pid, port=port, url=f"http://127.0.0.1:{port}",
        log_path=str(home / "logs" / "scout.log"), started_at="2026-10-04T00:00:00+00:00",
        gigai_version=run_supervisor._installed_gigai_version(),
    )
    run_supervisor._write_state(home, state)
    try:
        yield home, target, state
    finally:
        process.kill()
        process.wait(timeout=5)


def _status(home: Path, target: Path, *, as_json: bool = True):
    result = CliRunner().invoke(cli, ["scout", "status", "--home", str(home), "--target", str(target), *(["--json"] if as_json else [])])
    assert result.exit_code == 0, result.output
    return json.loads(result.output) if as_json else result.output.splitlines()


def test_a_live_process_whose_socket_refuses_is_unreachable_never_running_and_never_stopped(recorded) -> None:
    home, target, state = recorded

    payload = _status(home, target)

    assert payload["state"] == "unreachable"
    assert payload["process"] == {"recorded": True, "pid": state.pid, "alive": True, "identity": "scout"}
    assert payload["api"] == {"checked": True, "reachable": False, "url": state.url, "error": "refused"}
    assert (payload["pid"], payload["url"]) == (state.pid, state.url)
    lines = _status(home, target, as_json=False)
    assert lines[0] == f"process: running (pid {state.pid}); API: not reachable from here (connection refused) at {state.url}"
    assert lines[1] == (
        "Inside an agent sandbox a localhost check can fail while Scout is fine. Check from outside the sandbox "
        f"(or reload the page) before you restart anything. Log: {state.log_path}"
    )
    assert "stopped" not in lines and not lines[0].startswith("running")
    assert run_supervisor._read_state(home, state.project_id) is not None  # status changed nothing


def test_a_sandbox_that_hides_the_process_list_and_blocks_localhost_is_not_told_stopped(recorded, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, state = recorded
    # What an agent sandbox does: `ps` answers nothing for the pid, and connect() to localhost is not permitted.
    monkeypatch.setattr(run_supervisor, "_command_line_for_pid", lambda _pid: None)

    def blocked(*_args, **_kwargs):
        raise URLError(PermissionError(1, "Operation not permitted"))

    monkeypatch.setattr(run_supervisor, "urlopen", blocked)

    payload = _status(home, target)

    assert payload["state"] == "unreachable"
    assert payload["process"] == {"recorded": True, "pid": state.pid, "alive": True, "identity": "unknown"}
    assert payload["api"] == {"checked": True, "reachable": False, "url": state.url, "error": "not_permitted"}
    lines = _status(home, target, as_json=False)
    assert lines[0] == (
        f"process: pid {state.pid} is alive (could not confirm it is Scout: its command line is not readable from here); "
        f"API: not reachable from here (the connection was not permitted: this sandbox blocks localhost) at {state.url}"
    )
    assert "stopped" not in lines
    # The run state of the running server is still there: a status call inside a sandbox deletes nothing.
    assert run_supervisor._read_state(home, state.project_id) is not None


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the handler's own name
        self.send_response(200 if self.path == "/api/health" else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args) -> None:
        pass


def test_running_needs_both_the_live_process_and_an_api_that_answers(recorded) -> None:
    home, target, state = recorded
    assert _status(home, target)["state"] == "unreachable"  # the process and the run state alone

    server = HTTPServer(("127.0.0.1", state.port), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = _status(home, target)
        lines = _status(home, target, as_json=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["state"] == "running"
    assert payload["process"] == {"recorded": True, "pid": state.pid, "alive": True, "identity": "scout"}
    assert payload["api"] == {"checked": True, "reachable": True, "url": state.url, "error": None}
    assert lines[0] == f"running: {state.url} (pid {state.pid}, log: {state.log_path})"


def test_stopped_and_crashed_say_what_the_process_evidence_is(recorded) -> None:
    home, target, state = recorded
    run_supervisor._remove_state(home, state.project_id)
    stopped = _status(home, target)
    assert stopped["state"] == "stopped"
    assert stopped["process"] == {"recorded": False, "pid": None, "alive": None, "identity": None}
    assert stopped["api"] == {"checked": False, "reachable": None, "url": None, "error": None}

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=10)
    run_supervisor._write_state(home, run_supervisor.ScoutRunState(
        project_id=state.project_id, pid=gone.pid, port=state.port, url=state.url, log_path=state.log_path, started_at=state.started_at,
    ))
    crashed = _status(home, target)
    assert crashed["state"] == "crashed"
    assert crashed["process"] == {"recorded": True, "pid": gone.pid, "alive": False, "identity": None}
    assert crashed["api"]["checked"] is False
