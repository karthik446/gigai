"""C-2: the localhost API server the Scout find-jobs UI talks to.

R0: this module is now an import-compat shim over the ``api/`` package
(``config.py``, ``setup.py``, ``discover.py``, ``runs.py``, ``static.py``,
``server.py``, plus an empty ``profiles.py`` reserved for F1-c) -- a pure
move, no behaviour change. It exists so every name code or tests already
import or monkeypatch from ``gigai.scout.find_jobs.present_api`` keeps
resolving, and so ``python -m gigai.scout.find_jobs.present_api`` (the
server entry ``run_supervisor.py`` spawns, including with
``--allow-test-seams``) keeps working unchanged. The actual route/handler
code lives in ``api/`` now; this file owns only ``main()``/``_run_forever``
(see the MONKEYPATCH TRAP note below) and the re-exports.

Stdlib-only (``http.server.ThreadingHTTPServer``); binds loopback only and
refuses any non-loopback peer with 403.  Every state-changing route (POST
/PUT/PATCH/DELETE) also runs a same-origin CSRF guard (``_check_csrf``):
loopback alone doesn't stop a malicious web page open in the operator's own
browser, so writes additionally require ``Content-Type: application/json``,
a matching ``Origin`` when present, and a matching ``Host``.  All business
logic is injected through the ``Backend`` protocol below; this module owns
routing, JSON (de)serialization against the frozen contracts, and both
guards.

MONKEYPATCH TRAP: ``main()`` and ``_run_forever`` live here, not in
``api/server.py``, even though ``serve()`` (which they call) lives there.
``test_present_ui.py`` patches
``"gigai.scout.find_jobs.present_api._run_forever"`` by string path, and
``main()`` calls the bare name ``_run_forever`` -- Python resolves a bare
name against the *defining* module's own globals, not the caller's, so if
``main`` lived in ``api/server.py`` instead, a patch on the shim's
``_run_forever`` would silently stop taking effect. Keeping both here means
``main``'s own lookup of ``_run_forever`` is this module's globals, exactly
where the patch lands.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .api.common import RUN_START_TIMEOUT_SECONDS  # noqa: F401 - re-export: tests import it from this shim
from .api.server import (
    API_BIND,
    Backend,
    ConfigMissingError,  # noqa: F401 - re-export: tests import it from this shim
    DiscoveryConflictError,  # noqa: F401 - re-export: tests import it from this shim
    DiscoveryUnavailableError,  # noqa: F401 - re-export: tests import it from this shim
    LOGGER_NAME,  # noqa: F401 - re-export: tests import it from this shim
    NotWiredBackend,  # noqa: F401 - re-export: tests import it from this shim
    ScoutFindJobsBackend,
    SetupPrefsMissingError,  # noqa: F401 - re-export: tests import it from this shim
    SetupValidationError,  # noqa: F401 - re-export: tests import it from this shim
    _logger,
    _make_handler,  # noqa: F401 - re-export: tests import it from this shim
    serve,
)
from .api.setup import _validate_setup_body  # noqa: F401 - re-export: tests import it from this shim
from .api.static import _UI_DIST_RELATIVE_PARTS  # noqa: F401 - re-export: api/static.py reads it here, tests patch it here

_TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"
_TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"


def _end_model_calls_on_stop() -> None:
    """0.1.11.5 (ASSESS-01): a server that is stopped ends the model processes it started.

    ``gigai scout stop`` sends SIGTERM. Each model call (``adapters.process``) is the leader of its own session, so
    the signal never reached it: four `claude -p` calls were left running under pid 1 after a stop. The handler ends
    this server's own model processes (only those it started and has not reaped), then lets the signal end the
    server as it always did. Installed by ``_run_forever`` (the real server process), on its main thread only.
    """

    import signal

    def on_stop(signum: int, _frame: object) -> None:
        from ...adapters.process import terminate_children

        try:
            ended = terminate_children(grace_seconds=1.0)
            if ended:
                _logger.info("scout server stopping: ended %d model call(s) it had started", ended)
        finally:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, on_stop)


def _run_forever(bind: tuple[str, int], *, backend: Backend | None = None) -> None:
    import threading

    if threading.current_thread() is threading.main_thread():
        _end_model_calls_on_stop()
    # 0110-025: the real server also runs the hourly sources refresh thread.
    server = serve(backend=backend, bind=bind, background_refresh=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m gigai.scout.find_jobs.present_api")
    parser.add_argument("--target", dest="target", default=None, help="target root path")
    parser.add_argument("--home", dest="home", default=None, help="GigAI home root path")
    parser.add_argument(
        "--port",
        dest="port",
        type=int,
        default=None,
        help=f"loopback port to bind (default: {API_BIND[1]})",
    )
    parser.add_argument(
        "--allow-test-seams",
        dest="allow_test_seams",
        action="store_true",
        default=False,
        help="allow starting with GIGAI_SCOUT_FIND_JOBS_TEST_HTTP/_MODEL test seams active",
    )
    args = parser.parse_args(argv)

    active_seam_vars = [name for name in (_TEST_HTTP_ENV, _TEST_MODEL_ENV) if os.environ.get(name)]
    if active_seam_vars and not args.allow_test_seams:
        joined = " and ".join(active_seam_vars)
        print(
            f"refusing to start: {joined} is set in the environment; pass --allow-test-seams to start anyway",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if active_seam_vars:
        print("TEST SEAMS ACTIVE: results are fixture data", file=sys.stderr)

    from ...setup import default_home_root
    from ..target_resolution import home_scout_target

    home_root = Path(args.home).expanduser().resolve(strict=False) if args.home else default_home_root()
    # uat-bug-017: no --target means <home>/scout, as for every `gigai scout`
    # command; the folder this was started from never matters.
    target = Path(args.target) if args.target else home_scout_target(home_root)
    target = target.expanduser().resolve(strict=False)
    bind = (API_BIND[0], args.port) if args.port is not None else API_BIND
    backend = ScoutFindJobsBackend(home_root=home_root, target=target)
    _start_contact_cleanup(home_root, target)
    _run_forever(bind, backend=backend)


def _start_contact_cleanup(home_root: Path, target: Path) -> None:
    """0110-046: the one-time contact cleanup, off the startup path (``gigai scout run`` has usually run it
    already, then this is one small file read). Never raises; logs the status only, never a value."""

    import threading

    from ..contact_cleanup import run_cleanup

    def work() -> None:
        report = run_cleanup(home_root=home_root, target=target if target.is_dir() else None)
        _logger.info("contact cleanup: status=%s removed_any=%s", report.get("status"), report.get("removed_any"))

    threading.Thread(target=work, name="gigai-contact-cleanup", daemon=True).start()


if __name__ == "__main__":
    main()
