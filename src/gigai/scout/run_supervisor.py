"""``gigai scout run|stop|status`` — supervise the Scout find-jobs API+UI process.

Packet C of the ``gigai scout run`` UAT fix (U13/U23): a background-by-default
supervisor around ``present_api``'s ``ThreadingHTTPServer`` entry, one per
project, with a state file + log file under the GigAI home so a second
``gigai scout run`` reuses the running instance instead of starting a
duplicate, and ``gigai scout stop``/``status`` can find it again.

State and logs are keyed by ``BoundProject.project_id`` (not the target path)
so they never live inside the operator's project directory and stay stable
across renames of the target. Everything here is local process supervision;
no network calls other than the loopback health check against the child we
just started.
"""

from __future__ import annotations

import importlib.metadata
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import gigai

from ..canonical import canonical_json_bytes, parse_json_bytes
from ..workpad import resolve_bound_project
from .find_jobs.contracts import API_BIND
from .template import install_scout


def _installed_gigai_version() -> str:
    """The version ``gigai --version`` prints (``importlib.metadata``, same
    source ``click.version_option(package_name="gigai")`` uses)."""

    return importlib.metadata.version("gigai")


def _installed_package_path() -> str:
    """The directory the running ``gigai`` package was imported from."""

    return str(Path(gigai.__file__).resolve().parent)

# Deferred import: scout_cli imports this module (to build the `run`/`stop`/
# `status` commands), so importing scout_cli at module load time here would
# be circular. write_starter_find_jobs_config lives in scout_cli.
def _write_starter_find_jobs_config(target_root: Path) -> bool:
    from .scout_cli import write_starter_find_jobs_config

    return write_starter_find_jobs_config(target_root)


DEFAULT_PORT = API_BIND[1]
HEALTH_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 5.0


class ScoutRunError(RuntimeError):
    """Raised for a supervisor failure that already has a stable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScoutRunState:
    project_id: str
    pid: int
    port: int
    url: str
    log_path: str
    started_at: str
    # ``None`` means "written by a build before uat-bug-006" -- treated as a
    # version mismatch (an upgrade must never be masked by an old state file
    # that predates recording a version at all).
    gigai_version: str | None = None
    package_path: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": "scout-run-state:1",
            "project_id": self.project_id,
            "pid": self.pid,
            "port": self.port,
            "url": self.url,
            "log_path": self.log_path,
            "started_at": self.started_at,
            "gigai_version": self.gigai_version,
            "package_path": self.package_path,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "ScoutRunState":
        gigai_version = data.get("gigai_version")
        package_path = data.get("package_path")
        return cls(
            project_id=str(data["project_id"]),
            pid=int(data["pid"]),  # type: ignore[arg-type]
            port=int(data["port"]),  # type: ignore[arg-type]
            url=str(data["url"]),
            log_path=str(data["log_path"]),
            started_at=str(data["started_at"]),
            gigai_version=str(gigai_version) if gigai_version is not None else None,
            package_path=str(package_path) if package_path is not None else None,
        )

    def is_outdated(self) -> bool:
        """True if this state predates the installed gigai, or lacks a
        recorded version entirely (older builds never wrote one)."""

        return (
            self.gigai_version != _installed_gigai_version()
            or self.package_path != _installed_package_path()
        )


def _state_path(home_root: Path, project_id: str) -> Path:
    return home_root / "run" / "scout" / f"{project_id}.json"


def _log_path(home_root: Path, project_id: str) -> Path:
    return home_root / "logs" / f"scout-{project_id}.log"


def _reap_if_child(pid: int) -> None:
    """Best-effort non-blocking reap.

    If this process is the pid's parent (true when the same process both
    started and is now stopping it, e.g. in tests or --foreground), a dead
    child stays a zombie -- and ``os.kill(pid, 0)`` keeps succeeding on a
    zombie's pid -- until something calls ``waitpid`` on it. When we aren't
    the parent (the common case: a separate `gigai scout stop` invocation),
    this just raises ChildProcessError, which is fine to ignore.
    """

    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def _process_is_alive(pid: int) -> bool:
    _reap_if_child(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# Command-line markers that identify a pid as *our* Scout server. There are
# two shapes: the detached child `start()` spawns (runs the present_api
# module directly -- see the `argv` built below) and, in --foreground mode,
# the pid *is* the `gigai scout run --foreground` CLI process itself, which
# never mentions present_api. Matching either marker is enough; a stranger
# process is vanishingly unlikely to contain both "scout" and "run"/
# "present_api" together on its command line. Used so a pid the OS reused
# for an unrelated process is never mistaken for a live Scout server --
# liveness alone (``_process_is_alive``) is not identity. Portable in the two
# ways we actually run: /proc on Linux, ``ps -o command=`` on macOS/BSD;
# psutil is deliberately not a dependency.
_SERVER_MODULE_MARKER = "gigai.scout.find_jobs.present_api"
_FOREGROUND_MARKERS = ("scout", "run", "--foreground")


def _command_line_for_pid(pid: int) -> str | None:
    """Best-effort full command line for ``pid``, or ``None`` if unavailable.

    Returns ``None`` (never raises) when the pid is gone, permission is
    denied, or the platform has neither ``/proc`` nor ``ps`` -- callers must
    treat that as "identity unknown", not as "identity confirmed".
    """

    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    if proc_cmdline.exists():
        try:
            raw = proc_cmdline.read_bytes()
        except OSError:
            return None
        if not raw:
            return None
        parts = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        return " ".join(parts)

    try:
        result = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
            ["ps", "-o", "command=", "-p", str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.decode(errors="replace").strip()
    return output or None


def _pid_is_our_server(pid: int) -> bool:
    """True only if ``pid`` is alive *and* its command line is our server.

    Liveness (``_process_is_alive``) is necessary but not sufficient: after
    pid reuse an unrelated process can hold the same pid. When the command
    line can't be determined (permission denied, unsupported platform) this
    conservatively returns ``False`` rather than risk signalling a stranger.
    """

    if not _process_is_alive(pid):
        return False
    command_line = _command_line_for_pid(pid)
    if command_line is None:
        return False
    if _SERVER_MODULE_MARKER in command_line:
        return True
    return all(marker in command_line for marker in _FOREGROUND_MARKERS)


def _read_state(home_root: Path, project_id: str) -> ScoutRunState | None:
    path = _state_path(home_root, project_id)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return ScoutRunState.from_json(parse_json_bytes(path.read_bytes()))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_state(home_root: Path, state: ScoutRunState) -> None:
    path = _state_path(home_root, state.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(state.to_json()))


def _remove_state(home_root: Path, project_id: str) -> None:
    path = _state_path(home_root, project_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _health_ok(port: int, *, timeout: float = 1.0) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            return response.status == 200
    except (URLError, OSError, TimeoutError):
        return False


def _port_is_free(port: int) -> bool:
    # uat-bug-007: match how the real server binds. ``HTTPServer`` (via
    # ``socketserver.TCPServer``) sets ``SO_REUSEADDR`` before binding, so a
    # port left in TIME_WAIT by a since-closed connection binds fine there.
    # A plain bind here without it fails on macOS for the ~30s TIME_WAIT
    # window even though the server itself would start cleanly, producing a
    # false "port already in use" right after stopping/restarting Scout.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _tail(path: Path, *, lines: int = 40) -> str:
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-lines:])


@dataclass(frozen=True)
class ScoutRunResult:
    state: ScoutRunState
    reused: bool
    cleaned_stale: bool
    # Set to the old build's recorded version (or "unknown" if it had none)
    # when a live server was stopped and replaced because it predated the
    # installed gigai (uat-bug-006). ``None`` otherwise.
    restarted_from_version: str | None = None


def ensure_scout_ready(*, home_root: Path, requested_target: Path | None) -> None:
    """Install/approve/activate Scout and write the starter config if missing.

    Idempotent; safe to call on every ``gigai scout run``.
    """

    install_scout(home_root=home_root, requested_target=requested_target)
    bound_project = resolve_bound_project(home_root=home_root, requested_target=requested_target)
    _write_starter_find_jobs_config(bound_project.target_root)


def _existing_live_state(home_root: Path, project_id: str) -> tuple[ScoutRunState | None, bool]:
    """Return (state, cleaned_stale). ``state`` is ``None`` unless it is live.

    A live server recorded with an outdated (or missing) version is *not*
    returned here -- callers that need to reuse-if-current must check
    ``ScoutRunState.is_outdated()`` themselves, since an outdated-but-live
    server is neither "reusable" nor "stale" in the cleanup sense; it needs
    an explicit stop-and-restart (uat-bug-006), not silent removal.
    """

    state = _read_state(home_root, project_id)
    if state is None:
        return None, False
    if not _pid_is_our_server(state.pid) or not _health_ok(state.port):
        _remove_state(home_root, project_id)
        return None, True
    return state, False


def start(
    *,
    home_root: Path,
    requested_target: Path | None,
    port: int | None,
    foreground: bool,
    open_browser: bool,
) -> ScoutRunResult:
    """Ensure Scout is set up, then reuse or start the supervised API+UI.

    In ``foreground`` mode this call blocks running the server in-process
    (Ctrl-C stops it) after writing the state file; the state file is removed
    when the foreground server exits.
    """

    home_root = home_root.expanduser().resolve(strict=False)
    ensure_scout_ready(home_root=home_root, requested_target=requested_target)
    bound_project = resolve_bound_project(home_root=home_root, requested_target=requested_target)
    project_id = bound_project.project_id

    live_state, cleaned_stale = _existing_live_state(home_root, project_id)
    restarted_from_version: str | None = None
    if live_state is not None:
        if not live_state.is_outdated():
            return ScoutRunResult(state=live_state, reused=True, cleaned_stale=cleaned_stale)
        # uat-bug-006: a live server that is genuinely ours but predates the
        # installed gigai (or has no recorded version at all -- older
        # builds). Reusing it would silently keep serving pre-upgrade code,
        # so stop it (identity-checked; `_stop_pid` only signals a pid we
        # already confirmed is our server) and fall through to start fresh.
        restarted_from_version = live_state.gigai_version or "an earlier build"
        _stop_pid(live_state.pid)
        _remove_state(home_root, project_id)

    requested_port = port if port is not None else DEFAULT_PORT
    if not _port_is_free(requested_port):
        raise ScoutRunError(
            "scout_run_port_in_use",
            f"port {requested_port} is already in use; pass --port to choose a different one",
        )

    log_path = _log_path(home_root, project_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    argv = [
        sys.executable,
        "-m",
        "gigai.scout.find_jobs.present_api",
        "--home",
        str(home_root),
        "--target",
        str(bound_project.target_root),
        "--port",
        str(requested_port),
    ]

    if foreground:
        with log_path.open("ab") as log_file:
            log_file.write(
                f"--- gigai scout run --foreground starting at port {requested_port} ---\n".encode()
            )
            log_file.flush()
        state = ScoutRunState(
            project_id=project_id,
            pid=os.getpid(),
            port=requested_port,
            url=f"http://127.0.0.1:{requested_port}",
            log_path=str(log_path),
            started_at=_now_iso(),
            gigai_version=_installed_gigai_version(),
            package_path=_installed_package_path(),
        )
        _write_state(home_root, state)
        try:
            _run_foreground(bound_project.target_root, home_root, requested_port, open_browser)
        finally:
            _remove_state(home_root, project_id)
        return ScoutRunResult(
            state=state,
            reused=False,
            cleaned_stale=cleaned_stale,
            restarted_from_version=restarted_from_version,
        )

    with log_path.open("ab") as log_file:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, loopback-only server
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    state = ScoutRunState(
        project_id=project_id,
        pid=process.pid,
        port=requested_port,
        url=f"http://127.0.0.1:{requested_port}",
        log_path=str(log_path),
        started_at=_now_iso(),
        gigai_version=_installed_gigai_version(),
        package_path=_installed_package_path(),
    )

    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    healthy = False
    while time.monotonic() < deadline:
        if not _process_is_alive(process.pid):
            break
        if _health_ok(requested_port):
            healthy = True
            break
        time.sleep(0.2)

    if not healthy:
        _stop_pid(process.pid)
        tail = _tail(log_path)
        raise ScoutRunError(
            "scout_run_health_check_failed",
            f"Scout's API did not become healthy within {HEALTH_TIMEOUT_SECONDS:.0f}s. "
            f"Log: {log_path}\n--- last lines ---\n{tail}",
        )

    _write_state(home_root, state)
    if open_browser:
        webbrowser.open(state.url)
    return ScoutRunResult(
        state=state,
        reused=False,
        cleaned_stale=cleaned_stale,
        restarted_from_version=restarted_from_version,
    )


def _run_foreground(target: Path, home_root: Path, port: int, open_browser: bool) -> None:
    from .find_jobs.present_api import ScoutFindJobsBackend, _run_forever

    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}")
    backend = ScoutFindJobsBackend(home_root=home_root, target=target)
    try:
        _run_forever(("127.0.0.1", port), backend=backend)
    except KeyboardInterrupt:
        pass


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _stop_pid(pid: int) -> None:
    if not _process_is_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def stop(*, home_root: Path, requested_target: Path | None) -> bool:
    """Stop the running instance for this project, if any. Idempotent.

    Returns ``True`` if something was actually stopped, ``False`` if nothing
    was running (state absent or already stale).
    """

    home_root = home_root.expanduser().resolve(strict=False)
    bound_project = resolve_bound_project(home_root=home_root, requested_target=requested_target)
    state = _read_state(home_root, bound_project.project_id)
    if state is None:
        return False
    # Identity, not just liveness: never signal a pid the OS may have reused
    # for an unrelated process since our server last held it (P1 #9,
    # pr37-review-findings.md).
    is_ours = _pid_is_our_server(state.pid)
    if is_ours:
        _stop_pid(state.pid)
    _remove_state(home_root, bound_project.project_id)
    return is_ours


@dataclass(frozen=True)
class ScoutStatus:
    state: str  # "running" | "stopped" | "crashed"
    project_id: str
    url: str | None = None
    pid: int | None = None
    log_path: str | None = None
    started_at: str | None = None
    # True only when state == "running" and the live server predates the
    # installed gigai (or has no recorded version -- older builds).
    outdated: bool = False
    outdated_version: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "project_id": self.project_id,
            "url": self.url,
            "pid": self.pid,
            "log_path": self.log_path,
            "started_at": self.started_at,
            "outdated": self.outdated,
        }


def status(*, home_root: Path, requested_target: Path | None) -> ScoutStatus:
    home_root = home_root.expanduser().resolve(strict=False)
    bound_project = resolve_bound_project(home_root=home_root, requested_target=requested_target)
    project_id = bound_project.project_id
    saved = _read_state(home_root, project_id)
    if saved is None:
        return ScoutStatus(state="stopped", project_id=project_id)
    if not _process_is_alive(saved.pid):
        return ScoutStatus(
            state="crashed",
            project_id=project_id,
            url=saved.url,
            pid=saved.pid,
            log_path=saved.log_path,
            started_at=saved.started_at,
        )
    if not _pid_is_our_server(saved.pid):
        # The pid is alive but isn't our server -- the OS reused it for an
        # unrelated process since we last held it. Clean up the stale state
        # rather than reporting "running" (P1 #9, pr37-review-findings.md).
        _remove_state(home_root, project_id)
        return ScoutStatus(state="stopped", project_id=project_id)
    return ScoutStatus(
        state="running",
        project_id=project_id,
        url=saved.url,
        pid=saved.pid,
        log_path=saved.log_path,
        started_at=saved.started_at,
        outdated=saved.is_outdated(),
        outdated_version=saved.gigai_version,
    )


__all__ = [
    "DEFAULT_PORT",
    "ScoutRunError",
    "ScoutRunResult",
    "ScoutRunState",
    "ScoutStatus",
    "ensure_scout_ready",
    "start",
    "status",
    "stop",
]
