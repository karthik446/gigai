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
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import gigai

from ..canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ..registry import RegistryError, open_project_registry
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


def _installed_build_id() -> str:
    """An identity for *this* install of gigai that changes on every rebuild
    (uat-bug-006-r2): ``gigai_version``/``package_path`` alone are not enough
    -- every dev branch build lands on the same ``0.1.9.dev0`` version at the
    same uv tool path, so a stale reinstall looked identical to a fresh one.

    Preference order, all read through ``importlib.metadata`` (never a
    hard-coded dist-info path, so this keeps working across uv/pip layouts):

    1. ``direct_url.json``'s ``vcs_info.commit_id`` -- present for a VCS
       install (``uv tool install git+...``, the branch-install dev channel
       every operator UAT actually uses). Exact and human-legible (a commit
       sha), and changes on every real reinstall from a new commit.
    2. A digest of the installed dist-info ``RECORD`` -- covers a non-VCS
       wheel/sdist install (e.g. from a built artifact, no ``vcs_info``).
       ``RECORD`` lists every installed file with a content hash, so it
       changes on every rebuild that actually changes what got installed.
    3. A fallback stable for the lifetime of *this* source tree, for a
       source checkout / editable install where neither of the above pins a
       build: an editable install's ``direct_url.json`` has no ``vcs_info``
       (just ``{"url": "file://...", "dir_info": {"editable": true}}``) and
       its ``RECORD`` never changes on a source edit (it lists the editable
       ``.pth``/dist-info shim files, not the project's source files, and
       carries no hash for ``RECORD`` itself). This never raises: an
       operator's editable/dev checkout must never crash ``scout run``.
    """

    try:
        dist = importlib.metadata.distribution("gigai")
    except importlib.metadata.PackageNotFoundError:
        return "source-checkout:no-dist-info"

    direct_url_text = dist.read_text("direct_url.json")
    if direct_url_text:
        try:
            direct_url = json.loads(direct_url_text)
        except ValueError:
            direct_url = None
        if isinstance(direct_url, dict):
            commit_id = direct_url.get("vcs_info", {}).get("commit_id")
            if isinstance(commit_id, str) and commit_id:
                return f"commit:{commit_id}"

    record_text = dist.read_text("RECORD")
    if record_text:
        digest = digest_imported_bytes(record_text.encode("utf-8"))
        return f"record:{digest}"

    # Neither file is present (a layout ``importlib.metadata`` can still
    # resolve a distribution for but without dist-info files, e.g. an
    # unusual finder) -- fall back to something stable per install location
    # rather than crash.
    origin = getattr(dist, "_path", None) or _installed_package_path()
    return f"path:{origin}"


# Deferred import: scout_cli imports this module (to build the `run`/`stop`/
# `status` commands), so importing scout_cli at module load time here would
# be circular. write_starter_find_jobs_config lives in scout_cli.
def _write_starter_find_jobs_config(target_root: Path) -> bool:
    from .scout_cli import write_starter_find_jobs_config

    return write_starter_find_jobs_config(target_root)


DEFAULT_PORT = API_BIND[1]
HEALTH_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 5.0
# How long a port may stay bound after the server that held it was stopped.
PORT_RELEASE_TIMEOUT_SECONDS = 2.0
# The test suite's CI latency scale (tests/support/latency.py). Read here
# because the health wait runs inside the product, where a test-side bound
# cannot reach it; it can only widen the wait, never shorten it.
LATENCY_SCALE_ENV = "GIGAI_TEST_LATENCY_SCALE"


def _health_timeout_seconds() -> float:
    """``HEALTH_TIMEOUT_SECONDS``, widened by ``GIGAI_TEST_LATENCY_SCALE``."""

    # GIGAI_TEST_LATENCY_SCALE only ever WIDENS the 15 s health wait (never shortens it).
    try:
        scale = float(os.environ.get(LATENCY_SCALE_ENV, "1.0"))
    except ValueError:
        scale = 1.0
    # ``not scale >= 1.0`` also rejects NaN, which compares false to everything.
    if not scale >= 1.0 or scale == float("inf"):
        scale = 1.0
    return HEALTH_TIMEOUT_SECONDS * scale


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
    # ``None`` means "written by a build before uat-bug-006-r2" -- also
    # treated as outdated, same reasoning: a state file that predates
    # recording a build identity at all must never be trusted as current.
    build_id: str | None = None

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
            "build_id": self.build_id,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "ScoutRunState":
        gigai_version = data.get("gigai_version")
        package_path = data.get("package_path")
        build_id = data.get("build_id")
        return cls(
            project_id=str(data["project_id"]),
            pid=int(data["pid"]),  # type: ignore[arg-type]
            port=int(data["port"]),  # type: ignore[arg-type]
            url=str(data["url"]),
            log_path=str(data["log_path"]),
            started_at=str(data["started_at"]),
            gigai_version=str(gigai_version) if gigai_version is not None else None,
            package_path=str(package_path) if package_path is not None else None,
            build_id=str(build_id) if build_id is not None else None,
        )

    def is_outdated(self) -> bool:
        """True if this state predates the installed gigai, lacks a recorded
        version/build identity entirely (older builds never wrote one), or
        was written by a different build of the same version at the same
        package path (uat-bug-006-r2: every dev branch reinstall lands on
        the same ``0.1.9.dev0`` version and uv tool path, so version+path
        alone can't tell a stale reinstall from a fresh one)."""

        return (
            self.gigai_version != _installed_gigai_version()
            or self.package_path != _installed_package_path()
            or self.build_id != _installed_build_id()
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
    return command_line is not None and _is_server_command_line(command_line)


def _is_server_command_line(command_line: str) -> bool:
    if _SERVER_MODULE_MARKER in command_line:
        return True
    return all(marker in command_line for marker in _FOREGROUND_MARKERS)


def _read_state(home_root: Path, project_id: str) -> ScoutRunState | None:
    return _read_state_file(_state_path(home_root, project_id))


def _read_state_file(path: Path) -> ScoutRunState | None:
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


#: 0110-10-13: why the health check of ``status`` got no answer. ``not_permitted`` is what a sandbox that blocks
#: localhost answers (EPERM on connect); ``refused`` is no listener on the port as seen from here.
API_NOT_PERMITTED = "not_permitted"
API_REFUSED = "refused"
API_TIMEOUT = "timeout"
API_FAILED = "failed"


def _health_probe(port: int, *, timeout: float = 1.0) -> str | None:
    """``None`` when ``/api/health`` answers 200 on ``port``; else why not, as one of the ``API_*`` names."""

    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            return None if response.status == 200 else API_FAILED
    except (URLError, OSError, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, URLError) and isinstance(exc.reason, BaseException) else exc
        if isinstance(reason, ConnectionRefusedError):
            return API_REFUSED
        if isinstance(reason, PermissionError):
            return API_NOT_PERMITTED
        if isinstance(reason, TimeoutError):
            return API_TIMEOUT
        return API_FAILED


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
class OtherScoutServer:
    """A live Scout server recorded for another project under the same GigAI home."""

    project_id: str
    pid: int
    port: int
    url: str
    # The folder that project is registered at; ``None`` when the registry
    # no longer knows the project.
    target: str | None

    def to_json(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "pid": self.pid,
            "port": self.port,
            "url": self.url,
            "target": self.target,
        }


@dataclass(frozen=True)
class ScoutRunResult:
    state: ScoutRunState
    reused: bool
    cleaned_stale: bool
    # Set to the old build's recorded version (or "unknown" if it had none)
    # when a live server was stopped and replaced because it predated the
    # installed gigai (uat-bug-006). ``None`` otherwise.
    restarted_from_version: str | None = None
    # Set when another project's live Scout server held the requested port
    # and was stopped so this one could start (uat-bug-019).
    stopped_other: OtherScoutServer | None = None
    # Set when a verified Scout server NOT recorded in this home held the port
    # and was stopped (0.1.10 item 3).
    stopped_server: "StoppedOlderServer | None" = None


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


def _project_target(home_root: Path, project_id: str) -> str | None:
    try:
        registry, _created = open_project_registry(home_root, create=False)
        record = registry.find_project(project_id)
    except RegistryError:
        return None
    return record.target_locator if record is not None else None


def _pid_serves_port(pid: int, port: int) -> bool:
    """Whether our server at ``pid`` is the one recorded for ``port``.

    The detached child names its port on its command line, so a pid the OS
    reused for a *different* project's Scout server is not mistaken for the
    one a state file recorded. A ``--foreground`` run may leave the port to
    its default and then has nothing to compare; the recorded port stands.
    """

    parts = (_command_line_for_pid(pid) or "").split()
    named = [parts[index + 1] for index, part in enumerate(parts[:-1]) if part == "--port"]
    return not named or named[-1] == str(port)


def _other_live_servers(
    home_root: Path, project_id: str
) -> tuple[tuple[Path, OtherScoutServer], ...]:
    """Live Scout servers recorded for every project except ``project_id``.

    Same identity check the same-project restart uses (uat-bug-006-r2): the
    pid is alive *and* is our server, never a pid the OS reused. A state file
    that fails it belongs to its own project's ``run``/``status``/``stop`` to
    clean up and is left alone here.
    """

    found: list[tuple[Path, OtherScoutServer]] = []
    for path in sorted((home_root / "run" / "scout").glob("*.json")):
        state = _read_state_file(path)
        if state is None or project_id in (state.project_id, path.stem):
            continue
        if not _pid_is_our_server(state.pid) or not _pid_serves_port(state.pid, state.port):
            continue
        found.append(
            (
                path,
                OtherScoutServer(
                    project_id=state.project_id,
                    pid=state.pid,
                    port=state.port,
                    url=state.url,
                    target=_project_target(home_root, state.project_id),
                ),
            )
        )
    return tuple(found)


def _stop_other_server_on_port(
    home_root: Path, project_id: str, port: int
) -> OtherScoutServer | None:
    """Stop another project's live Scout server holding ``port``, the way ``stop`` does."""

    for path, other in _other_live_servers(home_root, project_id):
        if other.port != port:
            continue
        _stop_pid(other.pid)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return other
    return None


# 0.1.10 item 3: the holder of the port is not recorded in the current home
# (the home was moved or deleted while its server kept running). Stop it only
# when it is verifiably a Scout server: its listening pid, a `-m
# gigai.scout.find_jobs.present_api` command line, AND the Scout identity
# answer on loopback (GET /api returning Scout's "scout-api-index:1" index, or, for 0.1.9.x, GET /api/secrets/status;
# /api/health alone is a generic {"status": "ok"}). Anything else is message-only.
_OLDER_SERVER_CMD = re.compile(r"(?:^|\s)-m\s+" + re.escape(_SERVER_MODULE_MARKER) + r"(?:\s|$)")
_SCOUT_API_INDEX_SCHEMA = "scout-api-index:"
_SCOUT_LEGACY_SCHEMA = "scout-secrets-status:"


@dataclass(frozen=True)
class StoppedOlderServer:
    pid: int
    port: int
    home: str | None
    target: str | None

    def to_json(self) -> dict[str, object]:
        return {"pid": self.pid, "home": self.home, "target": self.target}


def _listening_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = result.stdout.decode(errors="replace").splitlines()
    return sorted({int(line[1:]) for line in lines if re.fullmatch(r"p\d+", line)})


def _scout_schema_at(port: int, path: str, prefix: str, *, timeout: float) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
            if response.status != 200:
                return False
            document = parse_json_bytes(response.read(1_000_000))
    except (URLError, OSError, TimeoutError, ValueError):
        return False
    schema = document.get("schema_version") if isinstance(document, dict) else None
    return isinstance(schema, str) and schema.startswith(prefix)


def _answers_scout_identity(port: int, *, timeout: float = 2.0) -> bool:
    # Either answer is Scout-specific (never the generic /api/health): 0.1.10+ answers
    # GET /api; 0.1.9.x servers 404 there but answer GET /api/secrets/status (read-only,
    # booleans only) with "scout-secrets-status:1" (v0.1.9.1 api/secrets_status.py).
    return _scout_schema_at(port, "/api", _SCOUT_API_INDEX_SCHEMA, timeout=timeout) or _scout_schema_at(
        port, "/api/secrets/status", _SCOUT_LEGACY_SCHEMA, timeout=timeout
    )


def _flag_value(command_line: str, flag: str) -> str | None:
    try:
        parts = shlex.split(command_line)
    except ValueError:
        parts = command_line.split()
    values = [parts[i + 1] for i in range(len(parts) - 1) if parts[i] == flag]
    return values[-1] if values else None


def _stop_verified_older_scout(port: int) -> StoppedOlderServer | None:
    """Stop the Scout server holding ``port`` if verifiably Scout; raise if the holder is not.

    ``None`` means the holder could not be resolved (nothing was touched).
    """

    pids = _listening_pids(port)
    if not pids:
        return None
    pid = pids[0]
    command_line = _command_line_for_pid(pid)
    if (
        len(pids) != 1
        or command_line is None
        or _OLDER_SERVER_CMD.search(command_line) is None
        or not _answers_scout_identity(port)
    ):
        raise ScoutRunError(
            "scout_run_port_in_use",
            f"port {port} is in use by pid {pid} ({command_line or 'command unknown'}), "
            "which is not a Scout server; stop it or pass --port",
        )

    def _still_it() -> bool:
        return _command_line_for_pid(pid) == command_line and pid in _listening_pids(port)

    if not _still_it():  # re-verified immediately before signalling
        raise ScoutRunError(
            "scout_run_port_in_use",
            f"port {port} is already in use; pass --port to choose a different one",
        )
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and _process_is_alive(pid):
        time.sleep(0.1)
    if _process_is_alive(pid) and _still_it():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    stopped = StoppedOlderServer(
        pid=pid,
        port=port,
        home=_flag_value(command_line, "--home"),
        target=_flag_value(command_line, "--target"),
    )
    sys.stderr.write(
        f"Stopped an older Scout server (pid {pid}, started with --home {stopped.home} "
        f"--target {stopped.target}) that was using port {port}\n"
    )
    return stopped


def _port_is_released(port: int) -> bool:
    deadline = time.monotonic() + PORT_RELEASE_TIMEOUT_SECONDS
    while not _port_is_free(port):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def start(
    *,
    home_root: Path,
    requested_target: Path | None,
    port: int | None,
    foreground: bool,
    open_browser: bool,
    allow_test_seams: bool = False,
    on_stopped_other: Callable[[OtherScoutServer], None] | None = None,
) -> ScoutRunResult:
    """Ensure Scout is set up, then reuse or start the supervised API+UI.

    In ``foreground`` mode this call blocks running the server in-process
    (Ctrl-C stops it) after writing the state file; the state file is removed
    when the foreground server exits.

    ``allow_test_seams`` is test-only: no CLI flag or env var reaches it (see
    ``scout_cli.py``'s ``run`` command, which never passes it), so
    ``gigai scout run`` can never set it. When ``True``, the spawned child's
    argv gets ``--allow-test-seams`` appended, letting present_api.py's own
    ``main()`` start with ``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP``/``_TEST_MODEL``
    active in the child's environment (double-gated: both the env var and
    this flag are required) -- test-gap-001's API e2e suite is the only
    caller that ever sets it.

    ``on_stopped_other`` is called as soon as another project's Scout server
    was stopped to free the port (uat-bug-019), before this one starts, so
    the caller can say so even when the start then fails.
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
    stopped_other: OtherScoutServer | None = None
    older: StoppedOlderServer | None = None
    if not _port_is_free(requested_port):
        # uat-bug-019: the port may be held by a Scout server recorded for
        # another project (after uat-bug-017 moved Scout to <home>/scout,
        # the earlier project's server still holds the default port). Only
        # a recorded, identity-checked Scout server is ever stopped; anything
        # else on the port keeps the error below.
        stopped_other = _stop_other_server_on_port(home_root, project_id, requested_port)
        if stopped_other is not None and on_stopped_other is not None:
            on_stopped_other(stopped_other)
        if stopped_other is None:
            older = _stop_verified_older_scout(requested_port)
        if (stopped_other is None and older is None) or not _port_is_released(requested_port):
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
    if allow_test_seams:
        argv.append("--allow-test-seams")

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
            build_id=_installed_build_id(),
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
            stopped_other=stopped_other,
            stopped_server=older,
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
        build_id=_installed_build_id(),
    )

    health_timeout = _health_timeout_seconds()
    deadline = time.monotonic() + health_timeout
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
            f"Scout's API did not become healthy within {health_timeout:.0f}s. "
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
        stopped_other=stopped_other,
        stopped_server=older,
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


#: 0110-10-13: who the recorded pid is. ``unknown``: alive, but its command line cannot be read from here (a sandbox
#: that hides the process list), so it is neither confirmed as Scout nor shown to be something else.
IDENTITY_SCOUT = "scout"
IDENTITY_UNKNOWN = "unknown"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"
STATE_CRASHED = "crashed"
#: The recorded process is alive and its API did not answer from here. Never "running": a pid and a run-state file
#: do not prove a healthy server. Never "stopped": not reaching the socket does not prove there is none.
STATE_UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ScoutStatus:
    """What ``gigai scout status`` knows, with the two kinds of evidence kept apart (0110-10-13).

    PROCESS evidence is the run-state file (``recorded``), whether its pid is
    alive and whether that pid is Scout. API evidence is whether
    ``/api/health`` answered on the recorded port from HERE. ``state`` is
    ``running`` only when both hold; a live process whose API did not answer
    is ``unreachable``, with ``api_error`` saying how the check failed.
    """

    state: str  # "running" | "stopped" | "crashed" | "unreachable"
    project_id: str
    url: str | None = None
    pid: int | None = None
    log_path: str | None = None
    started_at: str | None = None
    # True only when state == "running" and the live server predates the
    # installed gigai (or has no recorded version -- older builds).
    outdated: bool = False
    outdated_version: str | None = None
    # Live Scout servers recorded for other projects (uat-bug-019). Reported
    # only: ``status`` never stops anything.
    other_servers: tuple[OtherScoutServer, ...] = ()
    # 0110-10-13: process evidence, apart from API reachability. ``None``: not known / not checked.
    process_alive: bool | None = None
    process_identity: str | None = None
    api_reachable: bool | None = None
    api_error: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "project_id": self.project_id,
            "url": self.url,
            "pid": self.pid,
            "log_path": self.log_path,
            "started_at": self.started_at,
            "outdated": self.outdated,
            "other_servers": [other.to_json() for other in self.other_servers],
            "process": {
                "recorded": self.pid is not None, "pid": self.pid, "alive": self.process_alive, "identity": self.process_identity,
            },
            "api": {"checked": self.api_reachable is not None, "reachable": self.api_reachable, "url": self.url, "error": self.api_error},
        }


def status(*, home_root: Path, requested_target: Path | None) -> ScoutStatus:
    home_root = home_root.expanduser().resolve(strict=False)
    bound_project = resolve_bound_project(home_root=home_root, requested_target=requested_target)
    project_id = bound_project.project_id
    other_servers = tuple(other for _path, other in _other_live_servers(home_root, project_id))
    saved = _read_state(home_root, project_id)
    if saved is None:
        return ScoutStatus(state=STATE_STOPPED, project_id=project_id, other_servers=other_servers)
    if not _process_is_alive(saved.pid):
        return ScoutStatus(
            state=STATE_CRASHED,
            project_id=project_id,
            url=saved.url,
            pid=saved.pid,
            log_path=saved.log_path,
            started_at=saved.started_at,
            other_servers=other_servers,
            process_alive=False,
        )
    command_line = _command_line_for_pid(saved.pid)
    if command_line is not None and not _is_server_command_line(command_line):
        # The pid is alive but isn't our server -- the OS reused it for an
        # unrelated process since we last held it. Clean up the stale state
        # rather than reporting "running" (P1 #9, pr37-review-findings.md).
        _remove_state(home_root, project_id)
        return ScoutStatus(state=STATE_STOPPED, project_id=project_id, other_servers=other_servers)
    # 0110-10-13: a pid whose command line cannot be read from here (a sandbox that hides the process list) is NOT
    # shown to be a stranger: the run state stays, and what is known is said. Whether the server is healthy is the
    # API's to say, and that is checked apart from the process.
    api_error = _health_probe(saved.port)
    return ScoutStatus(
        state=STATE_RUNNING if api_error is None else STATE_UNREACHABLE,
        project_id=project_id,
        url=saved.url,
        pid=saved.pid,
        log_path=saved.log_path,
        started_at=saved.started_at,
        outdated=saved.is_outdated(),
        outdated_version=saved.gigai_version,
        other_servers=other_servers,
        process_alive=True,
        process_identity=IDENTITY_SCOUT if command_line is not None else IDENTITY_UNKNOWN,
        api_reachable=api_error is None,
        api_error=api_error,
    )


__all__ = [
    "DEFAULT_PORT",
    "OtherScoutServer",
    "ScoutRunError",
    "ScoutRunResult",
    "ScoutRunState",
    "ScoutStatus",
    "ensure_scout_ready",
    "start",
    "status",
    "stop",
]
