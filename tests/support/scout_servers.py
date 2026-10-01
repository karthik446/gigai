"""0110-028: Scout servers a test started and did not stop.

The supervisor starts the server detached (its own session), so a test that
loses track of it (a state file it removed, a stop that did not take, a test
that failed before its own ``stop``) leaves a ``present_api`` process running
for days. This module finds such processes by what only a test's server has
in its command line (the server module AND a pytest temp directory) and
stops them. It never looks at, or signals, anything else: the operator's own
Scout server has no pytest path in its command line.

Used by the API e2e harness (a per-test finalizer over the servers it
started), the port-takeover fixture, and the session-finish guard in
``tests/conftest.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time

SERVER_MARKER = "present_api"
SERVER_INVOCATION = f"-m gigai.scout.find_jobs.{SERVER_MARKER}"
PYTEST_TMP_MARKER = "pytest-of-"


@dataclass(frozen=True)
class LeftoverServer:
    pid: int
    ppid: int
    command: str

    def line(self) -> str:
        return f"pid {self.pid} (parent {self.ppid}): {self.command}"


def _processes() -> list[LeftoverServer]:
    try:
        done = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return []  # no `ps` here (Windows): nothing can be listed, so nothing is ever signalled
    found: list[LeftoverServer] = []
    for row in done.stdout.splitlines():
        parts = row.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            found.append(LeftoverServer(int(parts[0]), int(parts[1]), parts[2]))
    return found


def _server_home(command: str) -> str | None:
    """The ``--home`` of a supervised Scout server's command line, or ``None`` when it is not one.

    Only the exact shape the supervisor starts (``-m ...present_api --home
    <home> --target ...``) counts: a shell or an editor whose command line
    merely mentions both words is never taken for a server.
    """

    _, found, rest = command.partition(f"{SERVER_INVOCATION} --home ")
    if not found:
        return None
    home, target, _ = rest.partition(" --target ")
    return home if target and home else None


def scout_test_servers(under: Path | str | None = None) -> list[LeftoverServer]:
    """Live Scout servers whose ``--home`` is inside a pytest temp directory.

    ``under``: only those whose home is inside that directory (a test's
    ``tmp_path``, a session's base temp); ``None``: any pytest temp.
    """

    roots: tuple[str, ...] = ()
    if under is not None:
        path = Path(under)
        roots = tuple(dict.fromkeys((str(path), str(path.resolve()))))
    servers = []
    for process in _processes():
        home = _server_home(process.command)
        if home is None or process.pid == os.getpid() or PYTEST_TMP_MARKER not in home:
            continue
        if roots and not any(home == root or home.startswith(f"{root}/") for root in roots):
            continue
        servers.append(process)
    return servers


def orphaned_test_servers() -> list[LeftoverServer]:
    """Test servers whose pytest process is gone (re-parented to init): left by an EARLIER session."""

    return [server for server in scout_test_servers() if server.ppid == 1]


def _alive(pid: int) -> bool:
    try:
        os.waitpid(pid, os.WNOHANG)  # reap it if it is our own child, or it stays a zombie that "exists"
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def stop_test_servers(servers: list[LeftoverServer], *, timeout: float = 5.0) -> list[LeftoverServer]:
    """SIGTERM, then SIGKILL after ``timeout``. Returns the ones that were signalled.

    Each pid is checked again right before the signal: only a process whose
    command line still names the server module and a pytest temp is touched.
    """

    current = {server.pid: server for server in scout_test_servers()}
    stopped = [server for server in servers if server.pid in current and current[server.pid].command == server.command]
    for server in stopped:
        try:
            os.kill(server.pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_alive(server.pid) for server in stopped):
        time.sleep(0.05)
    for server in stopped:
        if _alive(server.pid):
            try:
                os.kill(server.pid, signal.SIGKILL)
            except OSError:
                pass
    return stopped


__all__ = ["PYTEST_TMP_MARKER", "SERVER_MARKER", "LeftoverServer", "orphaned_test_servers", "stop_test_servers", "scout_test_servers"]
