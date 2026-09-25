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

from .api.common import RUN_START_TIMEOUT_SECONDS
from .api.server import (
    API_BIND,
    Backend,
    ConfigMissingError,
    DiscoveryConflictError,
    DiscoveryUnavailableError,
    LOGGER_NAME,
    NotWiredBackend,
    ScoutFindJobsBackend,
    SetupPrefsMissingError,
    SetupValidationError,
    _RunBoundaryError,
    _atomic_write_json,
    _configure_logging,
    _gigai_version,
    _logger,
    _make_handler,
    serve,
)
from .api.config import _prefs_prefill_from_config
from .api.setup import _validate_setup_body
from .api.static import _UI_DIST_RELATIVE_PARTS

_TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"
_TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"
_TEST_JEV_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_JEV"


def _run_forever(bind: tuple[str, int], *, backend: Backend | None = None) -> None:
    import threading

    server = serve(backend=backend, bind=bind)
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
        help="allow starting with GIGAI_SCOUT_FIND_JOBS_TEST_HTTP/_MODEL/_JEV test seams active",
    )
    args = parser.parse_args(argv)

    active_seam_vars = [name for name in (_TEST_HTTP_ENV, _TEST_MODEL_ENV, _TEST_JEV_ENV) if os.environ.get(name)]
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

    target = Path(args.target).expanduser().resolve(strict=False) if args.target else None
    home_root = Path(args.home).expanduser().resolve(strict=False) if args.home else default_home_root()
    bind = (API_BIND[0], args.port) if args.port is not None else API_BIND
    backend = ScoutFindJobsBackend(home_root=home_root, target=target)
    _run_forever(bind, backend=backend)


if __name__ == "__main__":
    main()
