"""Core's supervisor for `[server] mode = "process"` gigs (`gigai.sdk.process`) and the `gigai <gig> run|stop|status` verbs.

The demo fixture gig (`tests/fixtures/gigs/demo`) serves stdlib HTTP on 127.0.0.1 and starts one grandchild, so stopping the
process group is checked to end both. Every test kills whatever it left under its own tmp folder.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import os
import signal
import stat
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from importlib.metadata import EntryPoint
from pathlib import Path

import click
import pytest

from gigai.gigs.loader import LazyGig
from gigai.sdk import process
from tests.behaviors.gigs.test_loader import CLI, FIXTURES, _add_dist, _add_fixture, _run

GRANDCHILD_MARKER = "gig-demo-grandchild"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    _add_fixture(directory, "demo")
    return directory


@pytest.fixture
def home(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "home"
    for pid in _pids(str(tmp_path)):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _pids(pattern: str) -> list[int]:
    completed = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, check=False)
    return [int(line) for line in completed.stdout.split()]


def _gigai(tmp_path: Path, site: Path, home: Path, *argv: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return _run(tmp_path, site, *argv, env_extra={"GIGAI_HOME": str(home), **(env_extra or {})})


def _status(tmp_path: Path, site: Path, home: Path) -> dict:
    result = _gigai(tmp_path, site, home, "demo", "status", "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _get(port: int, path: str = "/") -> str:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        return connection.getresponse().read().decode()
    finally:
        connection.close()


def _gone(pattern: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while _pids(pattern):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _flip_version(site: Path, version: str) -> None:
    """Build A -> B without reinstalling: the installed dist now reports ``version``."""

    metadata = site / "gigai_test_gig_demo-0.1.0.dist-info" / "METADATA"
    metadata.write_text(metadata.read_text(encoding="utf-8").replace("Version: 0.1.0", f"Version: {version}"), encoding="utf-8")


def test_run_status_stop_supervise_the_server_and_its_grandchild(tmp_path: Path, site: Path, home: Path) -> None:
    started = _gigai(tmp_path, site, home, "demo", "run")
    assert started.returncode == 0, started.stderr
    assert started.stdout.startswith("running: pid ")

    status = _status(tmp_path, site, home)
    assert status["status"] == "running" and status["health"] == "ok" and status["version"] == "0.1.0"
    pid, port = status["pid"], status["port"]
    assert status["pgid"] == pid and isinstance(port, int)
    assert status["argv"][-4:-2] == ["--port", str(port)]
    assert _get(port) == "demo 0.1.0\n"
    data = str(home / "gigs" / "demo")
    assert len(_pids(f"{GRANDCHILD_MARKER} {data}")) == 1

    state = process.state_path(home, "demo")
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert json.loads(state.read_text(encoding="utf-8"))["port"] == port

    stopped = _gigai(tmp_path, site, home, "demo", "stop")
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout == f"stopped: pid {pid}\n"
    assert _gone(data), "the server or its grandchild outlived stop"
    assert not state.exists()
    assert _gigai(tmp_path, site, home, "demo", "status").stdout == "stopped\n"
    assert _gigai(tmp_path, site, home, "demo", "stop").stdout == "not running\n"


def test_a_second_run_reuses_the_running_server(tmp_path: Path, site: Path, home: Path) -> None:
    first = _gigai(tmp_path, site, home, "demo", "run")
    assert first.returncode == 0, first.stderr
    pid = _status(tmp_path, site, home)["pid"]

    second = _gigai(tmp_path, site, home, "demo", "run")

    assert second.returncode == 0, second.stderr
    assert second.stdout.startswith(f"already running: pid {pid}, ")
    assert _pids(f"gig_demo.server .*{home}") == [pid]
    assert _gigai(tmp_path, site, home, "demo", "stop").returncode == 0


def test_a_new_version_of_the_gig_restarts_the_server(tmp_path: Path, site: Path, home: Path) -> None:
    assert _gigai(tmp_path, site, home, "demo", "run").returncode == 0
    before = _status(tmp_path, site, home)

    _flip_version(site, "0.2.0")
    result = _gigai(tmp_path, site, home, "demo", "status")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "restarted: version 0.1.0 -> 0.2.0"
    assert lines[1].startswith("running: ") and lines[1].endswith("health ok, version 0.2.0")
    after = _status(tmp_path, site, home)
    assert after["pid"] != before["pid"] and after["version"] == "0.2.0" and after["restarted_from"] is None
    assert _pids(f"gig_demo.server .*{home}") == [after["pid"]], "the build A server outlived the restart"
    assert _get(after["port"]) == "demo 0.2.0\n"
    assert len(_pids(f"{GRANDCHILD_MARKER} {home}")) == 1
    assert _gigai(tmp_path, site, home, "demo", "run").stdout.startswith(f"already running: pid {after['pid']}, ")
    assert _gigai(tmp_path, site, home, "demo", "stop").returncode == 0


def test_a_stale_state_file_is_removed_and_an_unrelated_process_is_left_alone(tmp_path: Path, site: Path, home: Path) -> None:
    state = process.state_path(home, "demo")
    state.parent.mkdir(parents=True)
    record = _record(home, _dead_pid(), 0)
    record["pgid"] = record["pid"]
    state.write_text(json.dumps(record), encoding="utf-8")
    assert _gigai(tmp_path, site, home, "demo", "status").stdout == "stopped (removed a stale state file)\n"
    assert not state.exists()

    state.write_text("{not json", encoding="utf-8")
    assert _gigai(tmp_path, site, home, "demo", "stop").stdout == "not running (removed a stale state file)\n"
    assert not state.exists()

    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(tmp_path)], shell=False, start_new_session=True)
    try:
        state.write_text(json.dumps({**record, "pid": unrelated.pid, "pgid": unrelated.pid}), encoding="utf-8")
        assert _status(tmp_path, site, home)["cleaned_stale"] is True
        assert not state.exists()
        assert unrelated.poll() is None, "a pid that is no longer the server must not be signalled"
    finally:
        unrelated.kill()
        unrelated.wait()


def _record(home: Path, pid: int, pgid: int) -> dict:
    return {
        "gig": "demo",
        "pid": pid,
        "pgid": pgid,
        "port": 9,
        "version": "0.1.0",
        "started_at": "2026-01-01T00:00:00+00:00",
        "argv": ["python", "-m", "gig_demo.server", "--port", "9"],
        "log": str(home / "run" / "demo.log"),
    }


def _dead_pid() -> int:
    dead = subprocess.Popen([sys.executable, "-c", "pass"], shell=False)
    dead.wait()
    return dead.pid


def test_a_doctored_state_file_never_signals_a_group_the_server_does_not_lead(tmp_path: Path, site: Path, home: Path) -> None:
    state = process.state_path(home, "demo")
    state.parent.mkdir(parents=True)
    marker = f"p5a-victim {tmp_path}"
    victim = subprocess.Popen(
        [sys.executable, "-c", f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', {marker!r}]); time.sleep(60)", marker],
        shell=False,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while len(_pids(marker)) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(_pids(marker)) == 2
        dead = _dead_pid()
        records = [_record(home, dead, victim.pid), _record(home, dead, 1), _record(home, victim.pid + 1, victim.pid)]
        for record in records:
            state.write_text(json.dumps(record), encoding="utf-8")
            assert _gigai(tmp_path, site, home, "demo", "status").stdout == "stopped (removed a stale state file)\n"
            assert not state.exists()
            state.write_text(json.dumps(record), encoding="utf-8")
            assert _gigai(tmp_path, site, home, "demo", "stop").stdout == "not running (removed a stale state file)\n"
            assert not state.exists()
        assert victim.poll() is None and len(_pids(marker)) == 2, "an unrelated group was signalled"
    finally:
        os.killpg(victim.pid, signal.SIGKILL)
        victim.wait()


# Runs one gigai verb in a fresh session after writing a state file that names that very session's group: either with a
# dead pid, or with the caller's own pid and exact argv (so only the "never the caller's own group" rule protects it).
OWN_GROUP_CLI = (
    "import json, os, sys; from pathlib import Path; from gigai.cli import cli; "
    "state = Path(os.environ['GIGAI_HOME']) / 'run' / 'demo.json'; state.parent.mkdir(parents=True, exist_ok=True); "
    "record = json.loads(os.environ['P5A_RECORD']); own = os.getpgrp(); "
    "record.update(pgid=own, pid=record['pid'] or own, argv=record['argv'] if record['pid'] else sys.orig_argv); "
    "state.write_text(json.dumps(record)); cli(sys.argv[1:], prog_name='gigai')"
)


@pytest.mark.parametrize("pid", ["dead", "own"])
def test_a_state_file_naming_the_callers_own_group_is_removed_without_a_signal(tmp_path: Path, site: Path, home: Path, pid: str) -> None:
    record = _record(home, _dead_pid() if pid == "dead" else 0, 0)
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(site), "GIGAI_HOME": str(home), "P5A_RECORD": json.dumps(record)}
    for verb, expected in (("status", "stopped (removed a stale state file)\n"), ("stop", "not running (removed a stale state file)\n")):
        result = subprocess.run(
            [sys.executable, "-c", OWN_GROUP_CLI, "demo", verb],
            capture_output=True, text=True, env=env, timeout=60, check=False, shell=False, start_new_session=True,
        )
        assert result.returncode == 0, f"returncode {result.returncode} (negative: the caller signalled itself): {result.stderr}"
        assert result.stdout == expected
        assert not process.state_path(home, "demo").exists()


def _spec() -> process.ServerSpec:
    manifest = tomllib.loads((FIXTURES / "demo" / "gig_demo" / "gig.toml").read_text(encoding="utf-8"))
    return process.ServerSpec.from_manifest("demo", manifest, "0.1.0")


def test_a_server_that_exits_at_once_is_reported_and_leaves_nothing(home: Path, site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(site))
    monkeypatch.setenv("GIG_DEMO_EXIT_CODE", "3")

    with pytest.raises(process.ProcessServerError, match=r"the server exited with code 3 before it was healthy \(log: "):
        process.start(home, _spec())

    assert not process.state_path(home, "demo").exists()
    assert process.log_path(home, "demo").exists()
    assert _gone(str(home), timeout=2), "the grandchild of a server that exited outlived it"


def test_a_server_that_never_gets_healthy_is_stopped_after_the_timeout(home: Path, site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(site))
    monkeypatch.setenv("GIG_DEMO_NEVER_HEALTHY", "1")

    began = time.monotonic()
    with pytest.raises(process.ProcessServerError, match=r"the server was not healthy within 1s"):
        process.start(home, _spec(), timeout=1.0)

    assert time.monotonic() - began < 5
    assert not process.state_path(home, "demo").exists()
    assert _gone(str(home), timeout=2), "the never-healthy server or its grandchild outlived the timeout"


@pytest.mark.parametrize(("sig", "returncode"), [(signal.SIGINT, 1), (signal.SIGTERM, 128 + signal.SIGTERM)])
def test_interrupting_run_during_the_health_wait_stops_the_server(
    tmp_path: Path, site: Path, home: Path, sig: signal.Signals, returncode: int
) -> None:
    # A terminal's Ctrl-C reaches gigai with Python's default SIGINT handler; a background runner may have ignored SIGINT.
    code = "import signal; signal.signal(signal.SIGINT, signal.default_int_handler); " + CLI
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(site), "GIGAI_HOME": str(home), "GIG_DEMO_NEVER_HEALTHY": "1"}
    run = subprocess.Popen(
        [sys.executable, "-c", code, "demo", "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False
    )
    try:
        deadline = time.monotonic() + 15
        while not _pids(f"{GRANDCHILD_MARKER} {home}") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _pids(f"{GRANDCHILD_MARKER} {home}"), "the server never started"
        run.send_signal(sig)
        _, stderr = run.communicate(timeout=15)
    finally:
        run.kill()
        run.wait()

    assert run.returncode == returncode, stderr
    assert _gone(str(home), timeout=2), "an interrupted run left its server running"
    assert not process.state_path(home, "demo").exists()


def test_stop_escalates_to_sigkill_for_a_group_that_ignores_sigterm(
    tmp_path: Path, site: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(site))
    monkeypatch.setenv("GIG_DEMO_IGNORE_TERM", "1")
    spec = _spec()

    started = process.start(home, spec)
    assert started.state is not None and started.health == "ok"
    assert len(_pids(f"{GRANDCHILD_MARKER} {home}")) == 1

    began = time.monotonic()
    result = process.stop(home, "demo", grace_seconds=0.3)

    assert result.stopped == started.state
    assert time.monotonic() - began < 5
    assert _gone(str(home), timeout=1), "SIGKILL did not end the group"
    assert process.status(home, spec).state is None


def test_a_hashbang_script_server_is_recognised_by_status_stop_and_a_second_run(
    tmp_path: Path, site: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Like a console script (marimo, uvicorn): ps shows "<python> <script> <args>", not the argv core recorded.
    script = tmp_path / "bin" / "demo-serve"
    script.parent.mkdir()
    script.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_module('gig_demo.server', run_name='__main__')\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PYTHONPATH", str(site))
    spec = dataclasses.replace(_spec(), start=(str(script), "--port", "{port}", "--data", "{data}"))

    started = process.start(home, spec)
    assert started.state is not None and started.health == "ok"
    assert started.state.argv[0] == str(script)

    status = process.status(home, spec)
    assert status.state == started.state and status.health == "ok" and not status.cleaned_stale
    assert process.state_path(home, "demo").exists()

    again = process.start(home, spec)
    assert again.reused and again.state == started.state
    assert len(_pids(f"{GRANDCHILD_MARKER} {home}")) == 1, "a second run started a duplicate server"

    assert process.stop(home, "demo").stopped == started.state
    assert _gone(str(home), timeout=2)


def test_core_verbs_are_added_only_to_process_gigs_and_a_gigs_own_verb_wins(
    tmp_path: Path, site: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_fixture(site, "storegig")
    own = (
        "import click\n\n@click.group()\ndef owngig():\n    'test gig: owngig'\n\n"
        "@owngig.command('status')\ndef status():\n    click.echo('owngig status of its own')\n"
    )
    manifest = (
        '[gig]\nmanifest_format = 1\nname = "owngig"\ndescription = "test gig"\ncore = ">=0.2.0a1"\n\n'
        '[commands]\ngroup = "owngig:owngig"\nprovides = ["status"]\n\n'
        '[server]\nmode = "process"\nstart = ["python", "-c", "pass"]\nhealth = "/health"\nmount_path = "/gigs/owngig/"\n'
    )
    _add_dist(site, "gigai-test-gig-owngig", "0.1.0", {"owngig": "owngig:owngig"}, {"owngig/__init__.py": own, "owngig/gig.toml": manifest})

    help_text = _gigai(tmp_path, site, home, "demo", "--help").stdout
    assert all(f"\n  {verb} " in help_text for verb in ("hello", "run", "status", "stop"))
    assert _gigai(tmp_path, site, home, "demo", "hello").stdout == "hello from demo\n"
    store_help = _gigai(tmp_path, site, home, "storegig", "--help").stdout
    assert not any(f"\n  {verb} " in store_help for verb in ("run", "stop"))
    assert _gigai(tmp_path, site, home, "owngig", "status").stdout == "owngig status of its own\n"
    assert _gigai(tmp_path, site, home, "owngig", "stop").stdout == "not running\n"

    monkeypatch.syspath_prepend(str(site))
    entry_point = EntryPoint("demo", "gig_demo:demo", "gigai.gigs")
    loaded = LazyGig("demo", entry_point, tomllib.loads((site / "gig_demo" / "gig.toml").read_text(encoding="utf-8"))).load()
    group = entry_point.load()
    assert loaded is not group and loaded.name == group.name
    # V1: every in-process gig another dist ships also gets core's `export`; BK2: and `steps`.
    assert loaded.list_commands(click.Context(loaded)) == ["hello", "export", "run", "status", "steps", "stop"]
    assert sorted(group.commands) == ["hello"], "the gig's own group must not be changed"
