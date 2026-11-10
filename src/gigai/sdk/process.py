"""Core's supervisor for gigs whose manifest says ``[server] mode = "process"`` (Scout keeps its own supervisor).

``start`` runs the manifest's ``[server] start`` argv (a list, never a shell) as a child that leads its own process group, on
a port core picks: the manifest's ``port`` when it is free, else a free one found by binding ``127.0.0.1:0``. ``{port}``,
``{home}`` and ``{data}`` in the argv become that port, the gigai home and the gig folder; a bare program name is looked up
next to this interpreter first, then on ``PATH``. The child's output goes to ``<home>/run/<gig>.log``. Once the health probe
passes (``GET`` of ``[server] health`` answering 2xx, or a TCP connect when the manifest names no health path) the server is
recorded in ``<home>/run/<gig>.json`` (one file per gig, written atomically, mode 0600): pid, pgid, port, gig version, start
time and argv. Scout's own state lives in ``<home>/run/scout/``; this module never starts or reads it.

``stop`` signals the whole process group (SIGTERM, then SIGKILL after a bounded wait), so a process the server started ends
with it. A group is signalled only when its id is the recorded pid (the server leads it), is above 1 and is not the caller's
own group, so a corrupt or hand-edited state file cannot aim signals elsewhere. ``status`` reports the recorded server and
probes its health. A state file whose process is gone, or whose pid now belongs to another program, is removed (liveness is
not identity: the pid must still lead the recorded group and ``ps`` must show the recorded argv, either exactly or after an
interpreter prefix, which is how a ``#!`` script such as a console script shows; where ``ps`` cannot run, leading the recorded
group is the only check). If ``start`` is interrupted while it waits for health (Ctrl-C, or
SIGTERM/SIGHUP when those have no handler of their own), the half-started server's group is stopped before the exception goes
on. A live server recorded under another version of the gig is stopped and started again by ``start`` and ``status`` (the
pattern of Scout's ``run_supervisor``, uat-bug-006; no code is shared with it). Servers bind 127.0.0.1 only: there is no bind
option, token or proxy here.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..gigs.home import check_name, resolve_gig_folder

RUN_DIRNAME = "run"
HOST = "127.0.0.1"
START_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 1.0
_POLL_SECONDS = 0.05
_PLACEHOLDER = re.compile(r"\{(port|home|data)\}")

# The servers this process started, by pid, so a stop from the same process reaps its own child (no zombie lingers in the
# group it waits on). Servers started by an earlier ``gigai`` run are reaped by init once that run has exited.
_STARTED: dict[int, subprocess.Popen[bytes]] = {}
_STARTED_LOCK = threading.Lock()


class ProcessServerError(Exception):
    """A process-mode server that cannot be started: an unusable ``[server]`` table, or a child that failed or never got healthy."""


def is_process_server(manifest: Mapping[str, Any] | None) -> bool:
    """Whether ``manifest`` declares ``[server] mode = "process"``."""

    server = manifest.get("server") if manifest else None
    return isinstance(server, dict) and server.get("mode") == "process"


@dataclass(frozen=True)
class ServerSpec:
    """One gig's process-mode server: the ``[server]`` table of its manifest and the installed gig's version."""

    gig: str
    start: tuple[str, ...]
    health: str | None
    port: int
    version: str

    @classmethod
    def from_manifest(cls, gig: str, manifest: Mapping[str, Any], version: str) -> ServerSpec:
        if not is_process_server(manifest):
            raise ProcessServerError('the manifest has no [server] mode = "process"')
        server = manifest["server"]
        start = server.get("start")
        if not isinstance(start, list) or not start or not all(isinstance(arg, str) and arg for arg in start):
            raise ProcessServerError("[server] start must be a non-empty list of non-empty strings")
        health = server.get("health")
        if health is not None and (not isinstance(health, str) or not health.startswith("/")):
            raise ProcessServerError("[server] health must be a path that starts with '/'")
        port = server.get("port", 0)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ProcessServerError("[server] port must be an integer from 0 to 65535")
        return cls(check_name("gig", gig), tuple(start), health, port, version)


@dataclass(frozen=True)
class ServerState:
    """What ``<home>/run/<gig>.json`` records about a running server."""

    gig: str
    pid: int
    pgid: int
    port: int
    version: str
    started_at: str
    argv: tuple[str, ...]
    log: str

    def to_json(self) -> dict[str, Any]:
        return {
            "gig": self.gig,
            "pid": self.pid,
            "pgid": self.pgid,
            "port": self.port,
            "version": self.version,
            "started_at": self.started_at,
            "argv": list(self.argv),
            "log": self.log,
        }

    @classmethod
    def from_json(cls, document: object) -> ServerState | None:
        """The state a file records; ``None`` when it is not a state document."""

        if not isinstance(document, dict):
            return None
        ints = [document.get(key) for key in ("pid", "pgid", "port")]
        texts = [document.get(key) for key in ("gig", "version", "started_at", "log")]
        argv = document.get("argv")
        if not all(type(value) is int and value > 0 for value in ints) or not all(isinstance(value, str) for value in texts):
            return None
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            return None
        pid, pgid, port = ints
        if pgid != pid or pgid <= 1:  # core only ever records a group its server leads
            return None
        gig, version, started_at, log = texts
        return cls(gig, pid, pgid, port, version, started_at, tuple(argv), log)


@dataclass(frozen=True)
class ServerStatus:
    """``start`` / ``status`` result: ``state`` is ``None`` when no server runs; ``health`` is ``ok`` or ``failing``."""

    gig: str
    state: ServerState | None
    health: str | None = None
    reused: bool = False
    restarted_from: str | None = None
    cleaned_stale: bool = False

    def to_json(self) -> dict[str, Any]:
        state = self.state
        return {
            "gig": self.gig,
            "status": "running" if state is not None else "stopped",
            "pid": state.pid if state else None,
            "pgid": state.pgid if state else None,
            "port": state.port if state else None,
            "health": self.health,
            "version": state.version if state else None,
            "started_at": state.started_at if state else None,
            "argv": list(state.argv) if state else None,
            "log": state.log if state else None,
            "restarted_from": self.restarted_from,
            "cleaned_stale": self.cleaned_stale,
        }


@dataclass(frozen=True)
class StopResult:
    """``stop`` result: the server that was stopped (``None`` when none ran) and whether a stale state file was removed."""

    stopped: ServerState | None
    cleaned_stale: bool = False


def state_path(home: Path, gig: str) -> Path:
    return Path(home) / RUN_DIRNAME / f"{check_name('gig', gig)}.json"


def log_path(home: Path, gig: str) -> Path:
    return Path(home) / RUN_DIRNAME / f"{check_name('gig', gig)}.log"


def start(home: Path, spec: ServerSpec, *, timeout: float = START_TIMEOUT_SECONDS) -> ServerStatus:
    """Start ``spec``'s server, or reuse the one running at the same version; restart one running at another version."""

    with _locked(home, spec.gig):
        live, cleaned = _live_state(home, spec.gig)
        restarted_from = None
        if live is not None:
            if live.version == spec.version:
                return ServerStatus(spec.gig, live, _health(spec.health, live.port), reused=True)
            restarted_from = live.version
            _stop_group(live.pgid, live.pid, STOP_TIMEOUT_SECONDS)
            _remove(state_path(home, spec.gig))
        state = _spawn(home, spec, timeout)
        return ServerStatus(spec.gig, state, "ok", restarted_from=restarted_from, cleaned_stale=cleaned)


def status(home: Path, spec: ServerSpec, *, timeout: float = START_TIMEOUT_SECONDS) -> ServerStatus:
    """The recorded server and its health; a stale state file is removed, a server at another version is restarted."""

    if not os.path.lexists(state_path(home, spec.gig)):
        return ServerStatus(spec.gig, None)
    with _locked(home, spec.gig):
        live, cleaned = _live_state(home, spec.gig)
        if live is None:
            return ServerStatus(spec.gig, None, cleaned_stale=cleaned)
        if live.version != spec.version:
            _stop_group(live.pgid, live.pid, STOP_TIMEOUT_SECONDS)
            _remove(state_path(home, spec.gig))
            return ServerStatus(spec.gig, _spawn(home, spec, timeout), "ok", restarted_from=live.version)
        return ServerStatus(spec.gig, live, _health(spec.health, live.port))


def stop(home: Path, gig: str, *, grace_seconds: float = STOP_TIMEOUT_SECONDS) -> StopResult:
    """Stop ``gig``'s server: SIGTERM to its process group, SIGKILL after ``grace_seconds``. Idempotent."""

    if not os.path.lexists(state_path(home, gig)):
        return StopResult(None)
    with _locked(home, gig):
        live, cleaned = _live_state(home, gig)
        if live is None:
            return StopResult(None, cleaned_stale=cleaned)
        _stop_group(live.pgid, live.pid, grace_seconds)
        _remove(state_path(home, gig))
        return StopResult(live)


def probe(port: int, health: str | None) -> bool:
    """Whether the server on ``127.0.0.1:port`` answers: ``GET health`` gives 2xx, or (no health path) a TCP connect works."""

    if health is None:
        try:
            with socket.create_connection((HOST, port), timeout=PROBE_TIMEOUT_SECONDS):
                return True
        except OSError:
            return False
    connection = http.client.HTTPConnection(HOST, port, timeout=PROBE_TIMEOUT_SECONDS)
    try:
        connection.request("GET", health)
        response = connection.getresponse()
        response.read()
        return 200 <= response.status < 300
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _health(health: str | None, port: int) -> str:
    return "ok" if probe(port, health) else "failing"


def _spawn(home: Path, spec: ServerSpec, timeout: float) -> ServerState:
    data = resolve_gig_folder(home, spec.gig)
    port = _pick_port(spec.port)
    argv = _server_argv(spec.start, port=port, home=Path(home), data=data)
    log = log_path(home, spec.gig)
    output = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen(
            [*argv],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            cwd=data,
            start_new_session=True,
        )
    except OSError as exc:
        raise ProcessServerError(f"cannot start {argv[0]} ({exc})") from exc
    finally:
        os.close(output)
    with _STARTED_LOCK:
        _STARTED[process.pid] = process
    try:
        with _terminating_signals_raise():
            _wait_healthy(process, port, spec.health, timeout, log)
    except BaseException:
        _stop_group(process.pid, process.pid, STOP_TIMEOUT_SECONDS)
        raise
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    state = ServerState(spec.gig, process.pid, process.pid, port, spec.version, started_at, argv, str(log))
    _write_state(state_path(home, spec.gig), state)
    return state


def _wait_healthy(process: subprocess.Popen[bytes], port: int, health: str | None, timeout: float, log: Path) -> None:
    deadline = time.monotonic() + timeout
    while True:
        healthy = probe(port, health)
        code = process.poll()  # after the probe: a 2xx from something else on the port must not pass for a dead child
        if code is not None:
            raise ProcessServerError(f"the server exited with code {code} before it was healthy (log: {log})")
        if healthy:
            return
        if time.monotonic() >= deadline:
            raise ProcessServerError(f"the server was not healthy within {timeout:g}s (log: {log})")
        time.sleep(_POLL_SECONDS)


@contextmanager
def _terminating_signals_raise() -> Iterator[None]:
    """SIGTERM and SIGHUP raise ``SystemExit`` meanwhile (as Ctrl-C raises ``KeyboardInterrupt``), so cleanup can run.

    Only signals still at their default action are touched, and only in the main thread (the only one that may set handlers).
    """

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def raise_exit(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(sig) is signal.SIG_DFL:
            previous[sig] = signal.signal(sig, raise_exit)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _server_argv(start: tuple[str, ...], *, port: int, home: Path, data: Path) -> tuple[str, ...]:
    values = {"port": str(port), "home": str(home), "data": str(data)}
    argv = [_PLACEHOLDER.sub(lambda match: values[match[1]], arg) for arg in start]
    if os.sep not in argv[0]:
        search = os.pathsep.join([os.path.dirname(sys.executable), os.environ.get("PATH", "")])
        found = shutil.which(argv[0], path=search)
        if found is None:
            raise ProcessServerError(f"executable {argv[0]!r} not found")
        argv[0] = found
    return tuple(argv)


def _pick_port(preferred: int) -> int:
    for port in ((preferred, 0) if preferred else (0,)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            try:
                probe_socket.bind((HOST, port))
            except OSError:
                continue
            return probe_socket.getsockname()[1]
    raise ProcessServerError(f"no free port on {HOST}")


def _live_state(home: Path, gig: str) -> tuple[ServerState | None, bool]:
    """``(state, cleaned_stale)``: the recorded server when it is still ours, else ``None`` with any stale file removed."""

    path = state_path(home, gig)
    try:
        state = ServerState.from_json(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None, False
    except (OSError, ValueError):
        state = None
    if state is not None and _is_ours(state):
        return state, False
    if state is not None and not _pid_exists(state.pid):
        # The leader is gone but a process it started may still hold the group; while it does, no other group can have
        # that id (``from_json`` only accepts pgid == pid), so the group is still ours to end.
        _stop_group(state.pgid, state.pid, STOP_TIMEOUT_SECONDS)
    _remove(path)
    return None, True


def _is_ours(state: ServerState) -> bool:
    if state.pgid == os.getpgrp():  # the caller is never its own server
        return False
    _reap(state.pid)
    try:
        if os.getpgid(state.pid) != state.pgid:
            return False
    except ProcessLookupError:
        return False
    command = _command_line(state.pid)
    if command is None:  # no ps: the pid still leading the recorded group is all there is to go on
        return True
    recorded = " ".join(state.argv)
    # A #! script runs as "<interpreter> [<shebang arg>] <script path> <args>", and the script path is argv[0] as executed.
    return command == recorded or command.endswith(" " + recorded)


def _command_line(pid: int) -> str | None:
    """The full command line of ``pid`` (``""`` when there is no such process); ``None`` when ``ps`` cannot run."""

    try:
        completed = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _pid_exists(pid: int) -> bool:
    _reap(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_group(pgid: int, pid: int, grace_seconds: float) -> None:
    """SIGTERM to the group, SIGKILL to what is left after ``grace_seconds``; returns once the group is gone (bounded).

    Does nothing unless ``pgid`` is ``pid`` (a group the server leads), above 1 and not this process's own group.
    """

    if pgid != pid or pgid <= 1 or pgid == os.getpgrp():
        return
    for sig, wait in ((signal.SIGTERM, grace_seconds), (signal.SIGKILL, STOP_TIMEOUT_SECONDS)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            # macOS refuses a group signal while a member is a zombie not yet reaped: signal the live members one by one.
            for member in _live_members(pgid) or ():
                try:
                    os.kill(member, sig)
                except (ProcessLookupError, PermissionError):
                    pass
        if _group_gone(pgid, pid, time.monotonic() + wait):
            break
    _reap(pid)
    with _STARTED_LOCK:
        _STARTED.pop(pid, None)


def _group_gone(pgid: int, pid: int, deadline: float) -> bool:
    while True:
        _reap(pid)
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # macOS, zombies only: gone once no live member is left
            if _live_members(pgid) == []:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)


def _live_members(pgid: int) -> list[int] | None:
    """The pids in process group ``pgid`` that are not zombies; ``None`` when ``ps`` cannot run."""

    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "pid=,pgid=,stat="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    members = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == str(pgid) and not fields[2].startswith("Z"):
            members.append(int(fields[0]))
    return members


def _reap(pid: int) -> None:
    """Collect ``pid``'s exit status when this process started it, so a dead server does not linger as a zombie."""

    with _STARTED_LOCK:
        process = _STARTED.get(pid)
    if process is not None:
        process.poll()


@contextmanager
def _locked(home: Path, gig: str) -> Iterator[None]:
    """One supervisor at a time per gig (``<home>/run/<gig>.lock``), so two ``run``\\s cannot start two servers."""

    directory = Path(home) / RUN_DIRNAME
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(directory / f"{check_name('gig', gig)}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write_state(path: Path, state: ServerState) -> None:
    """Replace ``path`` with ``state`` atomically (a 0600 temporary file in the same folder, fsynced, then renamed)."""

    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        _remove(Path(temporary))
        raise


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
