"""0.1.11.5 (ASSESS-01): a model call that takes as long as the test says, as a REAL child process.

``gate_model_calls(monkeypatch, folder)`` wraps the job page's Assess (``quick_assess.run_quick_assessment``, what an
assess batch calls for each posting) so each call first runs a child through the real ``adapters.process.run_json_process``
(its own session, registered like a `claude -p` call) and only then the fixture model. The child writes
``<folder>/started/<pid>`` and waits for ``<folder>/release`` (or ``<folder>/release-<n>`` for the n-th call alone):
the test decides when a call "in flight" ends. No model, no network.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

CHILD = (
    "import os, sys, time\n"
    "folder, number = sys.argv[1], sys.argv[2]\n"
    "sys.stdin.read()\n"
    "open(os.path.join(folder, 'started', str(os.getpid())), 'w').close()\n"
    "while not (os.path.exists(os.path.join(folder, 'release')) or os.path.exists(os.path.join(folder, 'release-' + number))):\n"
    "    time.sleep(0.02)\n"
    "print('{}')\n"
)


class Gate:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        (folder / "started").mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def started(self) -> list[int]:
        return sorted(int(path.name) for path in (self.folder / "started").iterdir())

    def wait_started(self, count: int, timeout: float = 30.0) -> list[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.started()) >= count:
                return self.started()
            time.sleep(0.02)
        raise AssertionError(f"{len(self.started())} model call(s) started, {count} expected")

    def release(self, number: int | None = None) -> None:
        (self.folder / ("release" if number is None else f"release-{number}")).write_text("", encoding="utf-8")


def alive(pid: int) -> bool:
    """Whether ``pid`` still runs (a zombie waiting to be reaped counts as gone)."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        import subprocess

        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return True
    return bool(state) and not state.startswith("Z")


def gate_model_calls(monkeypatch, folder: Path) -> Gate:
    from gigai.adapters.process import run_json_process
    from gigai.scout import quick_assess

    gate = Gate(Path(folder))
    real = quick_assess.run_quick_assessment

    def slow(request, **kwargs):
        gate.calls += 1
        run_json_process([sys.executable, "-c", CHILD, str(gate.folder), str(gate.calls)], prompt="x", cwd=gate.folder, timeout_seconds=120)
        return real(request, **kwargs)

    monkeypatch.setattr(quick_assess, "run_quick_assessment", slow)
    return gate


__all__ = ["CHILD", "Gate", "alive", "gate_model_calls"]
