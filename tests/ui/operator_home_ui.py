"""The operator-sized synthetic home for a browser: built once, served by the real Scout server process.

`tests/support/operator_home.py` builds the home (290,000 postings, 10,350 companies, 2 profiles, the
setup preferences a browser needs); this module only runs it and serves it:

* the home is built by a child process (`python -m tests.support.operator_home <root>`), so nothing
  of the build leaks into the pytest process, in a fresh temporary HOME (refused otherwise);
* the server is the one `make operator-ui-check` starts (`tools.media.operator_ui_check.start_server`:
  `python -m gigai.scout.find_jobs.present_api`, a free port, the fixture transports, its background
  threads running), and it is COLD: nothing has read the list since the home was built.

`GIGAI_UI_SERVER_ROOT=<checkout>` builds and serves with the product code of ANOTHER checkout (its
`src/` goes first on PYTHONPATH of both child processes; the generator and the tests stay this
tree's). That is how a test is shown red on an older release without editing that checkout.

`GIGAI_OPERATOR_HOME_PREBUILT=<temporary HOME>/op` (the release pre-check's `operator-home` job):
the home was built once, for the timing gate too; this module takes a fresh, never-read copy of it
(`operator_home.take_prebuilt`) instead of building a second one. HOME is then the folder it sits in.

No Playwright and no psutil here; nothing is imported from the product in this process.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from tests.support import operator_home
from tests.ui import support

REPO = Path(__file__).resolve().parents[2]
SERVER_ROOT_ENV = "GIGAI_UI_SERVER_ROOT"
#: The build is about 30 s on a 14-core laptop and 2 to 5 minutes on a small runner; this is a patience, not a budget.
BUILD_PATIENCE_SECONDS = 1800
HEALTH_PATIENCE_SECONDS = 60
#: The ceiling on the server's peak resident memory while a flow runs on this home. 200 to 330 MB measured on the
#: fixed build (2026-10-04, macOS); 0.1.10.8 peaked at 845 MB here and 1,182 to 1,271 MB in the spike.
SERVER_RSS_MB = 600


class OperatorHomeUiError(RuntimeError):
    pass


@dataclass(frozen=True)
class OperatorServer:
    url: str
    pid: int
    log_path: str
    home: Path  # the temporary HOME
    built: operator_home.OperatorHome
    build_seconds: float  # wall, the child process included (a prebuilt home: the time to take the copy)
    start_seconds: float  # until /api/health answered
    server_root: Path | None
    process: subprocess.Popen
    prebuilt: bool = False  # the home was built once by an earlier step of the job, not by this run

    @property
    def target(self) -> Path:
        return self.built.target_path

    def log_text(self) -> str:
        return Path(self.log_path).read_text(encoding="utf-8", errors="replace")


def server_root(environ: Mapping[str, str] | None = None) -> Path | None:
    """The other checkout whose product code is built with and served (`GIGAI_UI_SERVER_ROOT`), or None for this tree."""

    raw = ((os.environ if environ is None else environ).get(SERVER_ROOT_ENV) or "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    if not (root / "src" / "gigai" / "__init__.py").is_file():
        raise OperatorHomeUiError(f"{SERVER_ROOT_ENV}={raw} is not a GigAI checkout (no src/gigai)")
    return root


def child_environment(home: Path, *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """What the build and the server inherit: HOME is `home`, no GIGAI_HOME and no GIGAI_SCOUT_* of the developer's shell."""

    home = support.refuse_real_home(home, environ={})
    source = os.environ if environ is None else environ
    env = {name: value for name, value in source.items() if name != "GIGAI_HOME" and not name.startswith("GIGAI_SCOUT_")}
    env["HOME"] = str(home)
    root = server_root(source)
    if root is not None:
        env["PYTHONPATH"] = os.pathsep.join(part for part in (str(root / "src"), env.get("PYTHONPATH", "")) if part)
        env["PYTHONPYCACHEPREFIX"] = str(home / "pycache")  # nothing is written into the other checkout, not even bytecode
    return env


def build_home(root: Path, *, env: dict[str, str], log=lambda _line: None) -> operator_home.OperatorHome:
    """Run the generator in a child process; return what it built (`<root>/operator-home.json`)."""

    operator_home.assert_synthetic_root(root)
    done = subprocess.run(
        [sys.executable, "-m", "tests.support.operator_home", str(root)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=BUILD_PATIENCE_SECONDS, check=False,
    )
    for line in done.stderr.splitlines():
        log(line)
    if done.returncode != 0:
        raise OperatorHomeUiError(f"the operator-sized home was not built (exit {done.returncode}):\n{done.stderr[-2000:]}")
    return operator_home.OperatorHome(**json.loads((root / "operator-home.json").read_text(encoding="utf-8")))


def prebuilt_home(environ: Mapping[str, str] | None = None) -> Path | None:
    """The temporary HOME of a prebuilt home (the folder `GIGAI_OPERATOR_HOME_PREBUILT` sits in), or None."""

    root = operator_home.prebuilt_root(environ)
    return None if root is None else root.resolve().parent


def take_home(home: Path, *, env: dict[str, str], log=lambda _line: None) -> operator_home.OperatorHome:
    """The home under the temporary HOME `home`: a fresh copy of the prebuilt one when the job built one, else built here."""

    root = operator_home.prebuilt_root()
    if root is None:
        return build_home(home / "op", env=env, log=log)
    if server_root() is not None:
        raise OperatorHomeUiError(f"{operator_home.PREBUILT_ENV} cannot be combined with {SERVER_ROOT_ENV}: the prebuilt home was built with this tree's code")
    if root.resolve().parent != Path(home).resolve():
        raise OperatorHomeUiError(f"{operator_home.PREBUILT_ENV}={root} is not under the temporary HOME {home}")
    built = operator_home.take_prebuilt(log=log)
    assert built is not None
    return built


def wait_healthy(process: subprocess.Popen, url: str, log_path: Path) -> None:
    """`/api/health` answers (it reads nothing of the postings: the list stays cold)."""

    deadline = time.monotonic() + HEALTH_PATIENCE_SECONDS
    while True:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        if process.poll() is not None or time.monotonic() > deadline:
            raise OperatorHomeUiError(f"the Scout server did not start on {url}:\n{support.tail_text(log_path, 40)}")
        time.sleep(0.05)


def start(home: Path, *, log=lambda _line: None) -> OperatorServer:
    """Build the home under the temporary HOME `home` and start the real server on it, cold."""

    from tools.media.operator_ui_check import start_server

    env = child_environment(home)
    started = time.monotonic()
    built = take_home(home, env=env, log=log)
    build_seconds = time.monotonic() - started
    log_path = home / "server.log"
    started = time.monotonic()
    process, url = start_server(built.home, built.target, log_path, env=env)
    try:
        wait_healthy(process, url, log_path)
    except BaseException:
        stop(process)
        raise
    return OperatorServer(url, process.pid, str(log_path), home, built, build_seconds, time.monotonic() - started, server_root(), process, operator_home.prebuilt_root() is not None)


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(20)
