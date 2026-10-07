"""0.1.11.5 ASSESS-01 part 1: a stopped Scout server ends the model calls it started, and nothing else.

The operator stopped the server during an assess batch (``gigai scout stop``: SIGTERM) and four `claude -p` calls
stayed, under pid 1, until they were killed by hand: each model call is the leader of its own session, so the signal
to the server never reached it.

A real child Python process runs the server's own loop (``present_api._run_forever``) with two model calls in flight
(the real ``adapters.process.run_json_process``, each a process that would wait a minute) and one process it started
some other way. It gets the SIGTERM ``run_supervisor._stop_pid`` sends. Pinned: the server ends, both model
processes are gone, the other process is untouched. ``terminate_children`` is pinned alone too: it signals only what
this module started.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from tests.support.slow_model_child import alive

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups and SIGTERM are POSIX")

REPO = Path(__file__).resolve().parents[3]
SLEEPER = "import sys, time; sys.stdin.read(); print('started', flush=True); time.sleep(60)"

SERVER = """
import json, subprocess, sys, threading, time
from pathlib import Path
from gigai.adapters.process import run_json_process
from gigai.scout.find_jobs import present_api

folder = Path(sys.argv[1])
child = "import os, sys, time; sys.stdin.read(); open(sys.argv[1] + '/model-' + str(os.getpid()), 'w').close(); time.sleep(60)"

def call():
    try:
        run_json_process([sys.executable, "-c", child, str(folder)], prompt="x", cwd=folder, timeout_seconds=120)
    except Exception:
        pass

for _ in range(2):
    threading.Thread(target=call, daemon=True).start()
other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
while len(list(folder.glob("model-*"))) < 2:
    time.sleep(0.02)
(folder / "ready.json").write_text(json.dumps({"other": other.pid}))
present_api._run_forever(("127.0.0.1", 0), backend=None)
"""


def _wait(check, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_a_stopped_server_ends_its_own_model_calls_and_nothing_else(tmp_path: Path) -> None:
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO / "src"), os.environ.get("PYTHONPATH")])), "HOME": str(tmp_path), "GIGAI_SCOUT_AUTO_REFRESH": "0"}
    server = subprocess.Popen([sys.executable, "-c", SERVER, str(tmp_path)], env=environment, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    other = None
    models: list[int] = []
    try:
        assert _wait(lambda: (tmp_path / "ready.json").exists(), 60), "the server did not start its model calls"
        other = json.loads((tmp_path / "ready.json").read_text())["other"]
        models = [int(path.name.split("-")[1]) for path in tmp_path.glob("model-*")]
        assert len(models) == 2 and all(alive(pid) for pid in models) and alive(other)
        time.sleep(0.5)  # the serve loop is up

        os.kill(server.pid, signal.SIGTERM)  # what `gigai scout stop` sends

        assert server.wait(timeout=15) == -signal.SIGTERM, server.stderr.read() if server.stderr else ""
        assert _wait(lambda: not any(alive(pid) for pid in models)), f"model calls left running after the server stopped: {[pid for pid in models if alive(pid)]}"
        assert alive(other), "a process the model adapter did not start was ended"
    finally:
        for pid in [*models, *([other] if other else [])]:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if server.poll() is None:
            server.kill()


def test_terminate_children_ends_what_the_adapter_started_and_only_that(tmp_path: Path) -> None:
    from gigai.adapters.port import ModelInvocationError
    from gigai.adapters.process import live_children, run_json_process, terminate_children

    errors: list[BaseException] = []

    def call() -> None:
        try:
            run_json_process([sys.executable, "-c", SLEEPER], prompt="x", cwd=tmp_path, timeout_seconds=120)
        except ModelInvocationError as error:
            errors.append(error)

    threads = [threading.Thread(target=call) for _ in range(3)]
    for thread in threads:
        thread.start()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        assert _wait(lambda: len(live_children()) == 3)
        pids = live_children()
        assert other.pid not in pids
        assert terminate_children(grace_seconds=2.0) == 3
        for thread in threads:
            thread.join(15)
        assert not any(thread.is_alive() for thread in threads)
        assert len(errors) == 3, "a call whose process was ended is a failed call, not an answer"
        assert live_children() == () and not any(alive(pid) for pid in pids)
        assert other.poll() is None, "a process the adapter did not start was signalled"
        assert terminate_children() == 0  # nothing left: nothing signalled
    finally:
        other.kill()
        other.wait()
