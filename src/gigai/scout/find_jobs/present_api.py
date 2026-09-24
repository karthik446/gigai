"""C-2: the localhost API server the Scout find-jobs UI talks to.

Stdlib-only (``http.server.ThreadingHTTPServer``); binds loopback only and
refuses any non-loopback peer with 403.  All business logic is injected
through the ``Backend`` protocol below; this module owns routing, JSON
(de)serialization against the frozen contracts, and the loopback guard.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
import traceback
from importlib import resources
from pathlib import Path
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Protocol
from urllib.parse import urlsplit

from ...canonical import canonical_json_bytes
from ...run import RunError
from .contracts import (
    API_BIND,
    AggregateStatus,
    FindJobsConfig,
    FindJobsContractError,
    PinnedResume,
    RunRequest,
    RunResultsResponse,
    RunStatusResponse,
)

_TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"
_TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"


class Backend(Protocol):
    """Injected dependencies; I-3 wires the real one to ``run.launch_find_jobs_run``."""

    def read_config(self) -> tuple[FindJobsConfig, bytes]: ...

    def resume_preview(self) -> PinnedResume | None: ...

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        """Run the whole traversal; call ``on_run_allocated(run_id)`` once the run exists.

        Any exception raised before ``on_run_allocated`` is called is surfaced to the
        POST caller as an error response. Any exception raised after allocation cannot
        reach the (already-answered) POST caller; the backend must record the failure
        so it shows up in ``run_status``/``run_results`` instead.
        """
        ...

    def run_status(self, run_id: str) -> RunStatusResponse: ...

    def run_results(self, run_id: str) -> RunResultsResponse: ...


class NotWiredBackend:
    """Explicit test fallback; ``__main__`` installs ``ScoutFindJobsBackend``."""

    def _not_wired(self) -> None:
        raise NotImplementedError("scout_present_api backend is not wired yet")

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        self._not_wired()
        raise AssertionError("unreachable")

    def resume_preview(self) -> PinnedResume | None:
        self._not_wired()
        raise AssertionError("unreachable")

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        self._not_wired()

    def run_status(self, run_id: str) -> RunStatusResponse:
        self._not_wired()
        raise AssertionError("unreachable")

    def run_results(self, run_id: str) -> RunResultsResponse:
        self._not_wired()
        raise AssertionError("unreachable")


class _RunBoundaryError(FindJobsContractError):
    """A typed pre-allocation error with its route-level HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code, message)
        self.status = status


class ConfigMissingError(LookupError):
    """``find-jobs.json`` doesn't exist for the target yet.

    Distinct from a generic ``LookupError`` (used elsewhere for "not found"
    routes like an unknown run id) so ``/api/config`` can return a message
    that names the actual missing file and the commands that create it,
    instead of the generic "not found" text the UI used to show for every
    404 (see api.js's old blanket "That run could not be found.").
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(str(path))


class ScoutFindJobsBackend:
    """The production localhost backend for the Scout find-jobs API."""

    def __init__(self, *, home_root: Path, target: Path | None) -> None:
        self.home_root = Path(home_root).expanduser().resolve(strict=False)
        self.target = (
            Path(target).expanduser().resolve(strict=False)
            if target is not None
            else None
        )

    def _target_root(self) -> Path:
        if self.target is None:
            raise LookupError("a target path is required")
        return self.target

    def _resolved_run(self, run_id: str):
        from ...workpad import resolve_workpad

        return resolve_workpad(
            home_root=self.home_root,
            requested_target=self._target_root(),
            gig_id=None,
            allow_semantic_state=True,
        )

    def _require_run(self, run_id: str):
        resolved = self._resolved_run(run_id)
        run_path = resolved.path / "runs" / run_id / "run-details.json"
        if run_path.is_symlink() or not run_path.is_file():
            raise LookupError(run_id)
        return resolved

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        path = self._target_root() / "find-jobs.json"
        if path.is_symlink() or not path.is_file():
            raise ConfigMissingError(path)
        try:
            from ...canonical import parse_json_bytes

            config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
        except FindJobsContractError:
            raise
        except (OSError, ValueError) as exc:
            raise FindJobsContractError("invalid_value", "find-jobs.json is not valid JSON") from exc
        return config, canonical_json_bytes(config.to_json())

    def resume_preview(self) -> PinnedResume | None:
        from ... import run

        try:
            return run.resolve_newest_resume(self.home_root, self._target_root())
        except RunError as exc:
            if str(exc).startswith("find_jobs_resume_required:"):
                return None
            raise

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        from ... import run
        from .bindings import register_find_jobs_nodes

        target = self._target_root()
        # This call binds the parent (for the launch hook) and causes the
        # spawned child to bind itself before executing any Goal.
        register_find_jobs_nodes(home_root=self.home_root, target=target)
        try:
            run_id = run.launch_find_jobs_run(
                home_root=self.home_root,
                target=target,
                run_request=run_request,
                config_bytes=config_bytes,
                ui_loopback_verified=True,
            )
        except RunError as exc:
            message = str(exc)
            lowered = message.casefold()
            if "digest_mismatch" in lowered:
                raise _RunBoundaryError(HTTPStatus.CONFLICT, "config_digest_mismatch", message) from exc
            if "consent" in lowered or "loopback" in lowered:
                raise _RunBoundaryError(HTTPStatus.FORBIDDEN, "consent_required", message) from exc
            raise _RunBoundaryError(HTTPStatus.UNPROCESSABLE_ENTITY, "run_start_failed", message) from exc
        on_run_allocated(run_id)

    def _payload(self, run_id: str):
        from ..projection import build_present_payload

        self._require_run(run_id)
        return build_present_payload(
            home_root=self.home_root,
            target=self._target_root(),
            run_id=run_id,
        )

    def run_status(self, run_id: str) -> RunStatusResponse:
        from ... import run

        self._require_run(run_id)
        details = run.read_run_details(
            home_root=self.home_root,
            requested_target=self._target_root(),
            run_id=run_id,
        )
        payload = self._payload(run_id)
        detail_status = str(details.get("status", "running"))
        if detail_status == "preparing":
            status = AggregateStatus.PENDING
        elif detail_status in {"running", "verifying"}:
            status = AggregateStatus.RUNNING
        else:
            try:
                status = AggregateStatus(detail_status)
            except ValueError:
                status = payload.status
        receipts = payload.node_receipts
        # A partial receipt set is not enough to satisfy the DTO invariant
        # while the scheduler is still running; terminal reads expose all
        # receipts and the C-1 payload remains the result authority.
        if status in {AggregateStatus.PENDING, AggregateStatus.RUNNING}:
            receipts = ()
        return RunStatusResponse(run_id, status, receipts)

    def run_results(self, run_id: str) -> RunResultsResponse:
        payload = self._payload(run_id)
        return RunResultsResponse(run_id, payload)


RUN_START_TIMEOUT_SECONDS = 30.0


def _error_body(code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message}}


# The packaged UI: built by `yarn build` in src/gigai/scout/ui and committed
# under ui/dist (pyproject.toml package-data ships it in the wheel/sdist).
# Read via importlib.resources, anchored on the real "gigai.scout" package
# and joined onto "ui/dist" (ui/ itself has no __init__.py, so it isn't an
# importable package/resource anchor of its own) so this works from an
# installed wheel, not just a source checkout.
_UI_DIST_ANCHOR_PACKAGE = "gigai.scout"
_UI_DIST_RELATIVE_PARTS = ("ui", "dist")
_UI_MISSING_MESSAGE = (
    "the Scout UI is not built: run `yarn build` in src/gigai/scout/ui "
    "(this is a source checkout without a built UI)"
)

_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _static_content_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _STATIC_CONTENT_TYPES.get(suffix, "application/octet-stream")


def _ui_dist_root():
    """Return the packaged ``ui/dist`` resource root, or ``None`` if unbuilt.

    A source checkout without a build has no ``ui/dist`` directory at all
    (it's gitignored-turned-tracked only once built); an installed wheel
    always has it because package-data ships it. Either way, a missing or
    empty root is treated the same: not built.
    """

    try:
        root = resources.files(_UI_DIST_ANCHOR_PACKAGE)
    except ModuleNotFoundError:
        return None
    for part in _UI_DIST_RELATIVE_PARTS:
        root = root.joinpath(part)
    if not root.is_dir():
        return None
    return root


def _resolve_static_resource(path: str):
    """Resolve a URL path to a traversal-safe resource under ``ui/dist``.

    Returns ``None`` if the dist root is missing, the path escapes the
    dist root, or the resource doesn't exist as a file. ``path`` is the
    already-percent-decoded, query-stripped request path (e.g. ``/`` or
    ``/assets/index-abc123.js``).
    """

    root = _ui_dist_root()
    if root is None:
        return None
    normalized = posixpath.normpath(path)
    if normalized in ("/", ".", ""):
        relative = "index.html"
    else:
        relative = normalized.lstrip("/")
    # normpath collapses ".." segments together, so any remaining ".."
    # component means the request tried to climb out of dist/; reject it
    # rather than resolve it away, to fail closed on traversal attempts.
    parts = relative.split("/")
    if ".." in parts or any(not part for part in parts):
        return None
    resource = root
    for part in parts:
        resource = resource.joinpath(part)
    if not resource.is_file():
        return None
    return resource


def _make_handler(
    backend: Backend,
    *,
    run_start_timeout_seconds: float = RUN_START_TIMEOUT_SECONDS,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _write_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str, message: str) -> None:
            self._write_json(status, _error_body(code, message))

        def _check_loopback(self) -> bool:
            peer_host = self.client_address[0]
            if peer_host not in {"127.0.0.1", "::1"}:
                self._error(HTTPStatus.FORBIDDEN, "forbidden", "peer must be loopback")
                return False
            return True

        def _read_json_body(self) -> object | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "Content-Length is invalid")
                return None
            if length < 0:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "Content-Length is invalid")
                return None
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body is not valid JSON")
                return None

        def do_GET(self) -> None:  # noqa: N802
            if not self._check_loopback():
                return
            path = urlsplit(self.path).path
            try:
                if path.startswith("/api/"):
                    if path == "/api/health":
                        self._write_json(HTTPStatus.OK, {"status": "ok"})
                        return
                    if path == "/api/config":
                        self._handle_get_config()
                        return
                    run_id = _match_run_id(path, suffix="")
                    if run_id is not None:
                        self._handle_get_run_status(run_id)
                        return
                    run_id = _match_run_id(path, suffix="/results")
                    if run_id is not None:
                        self._handle_get_run_results(run_id)
                        return
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
                    return
                self._handle_get_static(path)
            except Exception:  # noqa: BLE001 - last-resort boundary so the connection never just drops
                traceback.print_exc(file=sys.stderr)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "an internal error occurred")

        def _handle_get_static(self, path: str) -> None:
            if _ui_dist_root() is None:
                if path in ("/", ""):
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "ui_not_built", _UI_MISSING_MESSAGE)
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
                return
            resource = _resolve_static_resource(path)
            if resource is None:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
                return
            body = resource.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _static_content_type(resource.name))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if not self._check_loopback():
                return
            path = urlsplit(self.path).path
            if path == "/api/run":
                self._handle_post_run()
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")

        def _handle_get_config(self) -> None:
            try:
                config, _config_bytes = backend.read_config()
            except ConfigMissingError as exc:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "config_missing",
                    f"{exc.path} does not exist yet. Run `gigai scout install` or "
                    "`gigai scout run` to write a starter find-jobs.json, then edit it.",
                )
                return
            except FindJobsContractError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
                return
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "config not found")
                return
            resume_preview = backend.resume_preview()
            config_digest = config.digest()
            payload = {
                "schema_version": "scout-find-jobs-config-response:1",
                "config": config.to_json(),
                "resume_preview": resume_preview.to_json() if resume_preview is not None else None,
                "resume_missing_hint": (
                    None if resume_preview is not None else "gigai scout resume add <file>"
                ),
                "config_digest": config_digest,
            }
            self._write_json(HTTPStatus.OK, payload)

        def _handle_post_run(self) -> None:
            body = self._read_json_body()
            if body is None:
                return
            try:
                run_request = RunRequest.from_json(body)
            except FindJobsContractError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
                return
            try:
                config, config_bytes = backend.read_config()
            except ConfigMissingError as exc:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "config_missing",
                    f"{exc.path} does not exist yet. Run `gigai scout install` or "
                    "`gigai scout run` to write a starter find-jobs.json, then edit it.",
                )
                return
            except FindJobsContractError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
                return
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "config not found")
                return
            if run_request.config_digest != config.digest():
                self._error(HTTPStatus.CONFLICT, "config_digest_mismatch", "config_digest does not match the current config")
                return

            allocated = threading.Event()
            allocation: dict[str, str] = {}
            pre_allocation_error: BaseException | None = None
            post_allocation_error: BaseException | None = None

            def _on_run_allocated(run_id: str) -> None:
                allocation["run_id"] = run_id
                allocated.set()

            def _run() -> None:
                nonlocal pre_allocation_error, post_allocation_error
                try:
                    backend.start_run(run_request, config_bytes, _on_run_allocated)
                except BaseException as exc:  # noqa: BLE001 - routed to the right side of allocation, not swallowed
                    if allocated.is_set():
                        post_allocation_error = exc
                    else:
                        pre_allocation_error = exc
                        allocated.set()

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            reached = allocated.wait(run_start_timeout_seconds)
            if not reached:
                self._error(HTTPStatus.GATEWAY_TIMEOUT, "run_start_timeout", "run did not allocate a run_id in time")
                return
            if pre_allocation_error is not None:
                if isinstance(pre_allocation_error, _RunBoundaryError):
                    self._error(
                        pre_allocation_error.status,
                        pre_allocation_error.code,
                        str(pre_allocation_error),
                    )
                    return
                if isinstance(pre_allocation_error, FindJobsContractError):
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, pre_allocation_error.code, str(pre_allocation_error))
                    return
                raise pre_allocation_error
            # post_allocation_error (if any) surfaces via run_status, not here: the POST
            # response is already committed to a run_id once allocation happened.
            run_id = allocation["run_id"]
            payload = {
                "schema_version": "scout-find-jobs-run-response:1",
                "run_id": run_id,
                "status": "pending",
                "node_receipts": [],
            }
            self._write_json(HTTPStatus.ACCEPTED, payload)

        def _handle_get_run_status(self, run_id: str) -> None:
            try:
                status_response = backend.run_status(run_id)
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
                return
            self._write_json(HTTPStatus.OK, status_response.to_json())

        def _handle_get_run_results(self, run_id: str) -> None:
            try:
                results_response = backend.run_results(run_id)
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
                return
            self._write_json(HTTPStatus.OK, results_response.to_json())

    return Handler


def _match_run_id(path: str, *, suffix: str) -> str | None:
    prefix = "/api/runs/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    if suffix:
        if not remainder.endswith(suffix):
            return None
        run_id = remainder[: -len(suffix)]
    else:
        run_id = remainder
    if not run_id or "/" in run_id:
        return None
    return run_id


def serve(
    *,
    backend: Backend | None = None,
    bind: tuple[str, int] = API_BIND,
    run_start_timeout_seconds: float = RUN_START_TIMEOUT_SECONDS,
) -> ThreadingHTTPServer:
    """Build and start a ``ThreadingHTTPServer`` bound to ``bind``.

    Callers (including tests) own the returned server's lifecycle: start its
    serve loop on a thread and call ``shutdown()``/``server_close()`` when done.
    ``run_start_timeout_seconds`` is exposed for tests that need a short
    ``POST /api/run`` allocation timeout; production callers should leave it
    at the default.
    """

    if backend is None:
        backend = NotWiredBackend()
    handler = _make_handler(backend, run_start_timeout_seconds=run_start_timeout_seconds)
    server = ThreadingHTTPServer(bind, handler)
    server.daemon_threads = True
    return server


def _run_forever(bind: tuple[str, int], *, backend: Backend | None = None) -> None:
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

    target = Path(args.target).expanduser().resolve(strict=False) if args.target else None
    home_root = Path(args.home).expanduser().resolve(strict=False) if args.home else default_home_root()
    bind = (API_BIND[0], args.port) if args.port is not None else API_BIND
    backend = ScoutFindJobsBackend(home_root=home_root, target=target)
    _run_forever(bind, backend=backend)


if __name__ == "__main__":
    main()
