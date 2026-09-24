from __future__ import annotations

import io
import json
import threading
import time
from email.message import Message
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from gigai.scout.find_jobs.contracts import (
    FindJobsConfig,
    FindJobsContractError,
    PinnedResume,
    RunRequest,
    RunResultsResponse,
    RunStatusResponse,
)
from gigai.scout.find_jobs.present_api import (
    Backend,
    ConfigMissingError,
    NotWiredBackend,
    _make_handler,
    main,
    serve,
)

from .conftest import load_fixture


def _config() -> FindJobsConfig:
    return FindJobsConfig.from_json(load_fixture("fixture-api-config-response-v1.json")["config"])


def _resume() -> PinnedResume:
    return PinnedResume.from_json(load_fixture("fixture-api-config-response-v1.json")["resume_preview"])


def _run_status_response(run_id: str) -> RunStatusResponse:
    fixture = load_fixture("fixture-api-run-status-response-v1.json")
    fixture = dict(fixture, run_id=run_id)
    return RunStatusResponse.from_json(fixture)


def _run_results_response(run_id: str) -> RunResultsResponse:
    fixture = json.loads(json.dumps(load_fixture("fixture-api-run-results-response-v1.json")))
    fixture["run_id"] = run_id
    fixture["payload"]["run_id"] = run_id
    return RunResultsResponse.from_json(fixture)


class FakeBackend:
    """A fully in-memory Backend double; no network, no provider, no model calls.

    ``start_run`` models I-2/I-3's real shape: it runs the "whole traversal" (here,
    an optional sleep to stand in for acquire+assess taking a while) and calls
    ``on_run_allocated`` partway through, once a run_id exists. Failure timing is
    controlled by ``pre_allocation_error`` (raised before the callback — the POST
    caller should see it) vs. ``post_allocation_error`` (raised after — recorded
    onto ``self.failed_run_id`` so ``run_status`` can reflect it, since the POST
    caller can no longer be reached).
    """

    def __init__(
        self,
        *,
        config: FindJobsConfig | None = None,
        resume: PinnedResume | None = None,
        known_run_id: str = "run_123e4567-e89b-42d3-a456-426614174002",
        pre_allocation_error: Exception | None = None,
        post_allocation_error: Exception | None = None,
        sleep_after_allocation_seconds: float = 0.0,
    ) -> None:
        self.config = config if config is not None else _config()
        self.resume = resume if resume is not None else _resume()
        self.known_run_id = known_run_id
        self.pre_allocation_error = pre_allocation_error
        self.post_allocation_error = post_allocation_error
        self.sleep_after_allocation_seconds = sleep_after_allocation_seconds
        self.started_requests: list[RunRequest] = []
        self.failed_run_id: str | None = None

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        return self.config, self.config.digest().encode("utf-8")

    def resume_preview(self) -> PinnedResume | None:
        return self.resume

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        if self.pre_allocation_error is not None:
            raise self.pre_allocation_error
        self.started_requests.append(run_request)
        on_run_allocated(self.known_run_id)
        if self.sleep_after_allocation_seconds > 0:
            time.sleep(self.sleep_after_allocation_seconds)
        if self.post_allocation_error is not None:
            self.failed_run_id = self.known_run_id

    def run_status(self, run_id: str) -> RunStatusResponse:
        if run_id != self.known_run_id:
            raise LookupError(run_id)
        if run_id == self.failed_run_id:
            fixture = load_fixture("fixture-api-run-status-response-v1.json")
            fixture = dict(fixture, run_id=run_id, status="failed")
            return RunStatusResponse.from_json(fixture)
        return _run_status_response(run_id)

    def run_results(self, run_id: str) -> RunResultsResponse:
        if run_id != self.known_run_id:
            raise LookupError(run_id)
        return _run_results_response(run_id)


@pytest.fixture
def running_server(request: pytest.FixtureRequest):
    backend: Backend = getattr(request, "param", None) or FakeBackend()
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            yield client, backend
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_get_config_happy_path(running_server) -> None:
    client, backend = running_server
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "scout-find-jobs-config-response:1"
    assert body["config"] == backend.config.to_json()
    assert body["resume_preview"] == backend.resume.to_json()
    assert body["config_digest"] == backend.config.digest()


class _ConfigMissingBackend(FakeBackend):
    """Models an unwritten ``find-jobs.json`` (0.1.8.1 UAT addendum)."""

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        raise ConfigMissingError(Path("/tmp/fixture-target/find-jobs.json"))


class _NoResumeBackend(FakeBackend):
    """Models a project with a config but no saved resume yet (U15)."""

    def resume_preview(self) -> PinnedResume | None:
        return None


def test_get_health_ok(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("running_server", [_ConfigMissingBackend()], indirect=True)
def test_get_config_missing_names_the_file_and_the_fix(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/config")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "config_missing"
    message = body["error"]["message"]
    # The old blanket 404 text ("That run could not be found.") is a UI-side
    # fallback keyed off status code; the backend message here must be
    # specific enough that the UI no longer needs that fallback for this case.
    assert "find-jobs.json" in message
    assert "gigai scout install" in message or "gigai scout run" in message


@pytest.mark.parametrize("running_server", [_ConfigMissingBackend()], indirect=True)
def test_post_run_with_missing_config_is_also_config_missing(running_server) -> None:
    client, _backend = running_server
    request_payload = load_fixture("fixture-api-run-request-v1.json")
    response = client.post("/api/run", json=request_payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "config_missing"


@pytest.mark.parametrize("running_server", [_NoResumeBackend()], indirect=True)
def test_get_config_with_no_resume_carries_an_explicit_hint(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert body["resume_preview"] is None
    assert body["resume_missing_hint"] == "gigai scout resume add <file>"


def test_get_config_with_a_resume_has_no_hint(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["resume_missing_hint"] is None


def test_post_run_happy_path_returns_202(running_server) -> None:
    client, backend = running_server
    request_payload = load_fixture("fixture-api-run-request-v1.json")
    request_payload = dict(request_payload, config_digest=backend.config.digest())
    response = client.post("/api/run", json=request_payload)
    assert response.status_code == 202
    body = response.json()
    assert body["schema_version"] == "scout-find-jobs-run-response:1"
    assert body["run_id"] == backend.known_run_id
    assert body["status"] == "pending"
    assert body["node_receipts"] == []
    assert len(backend.started_requests) == 1
    assert backend.started_requests[0].config_digest == backend.config.digest()


def test_get_run_status_happy_path(running_server) -> None:
    client, backend = running_server
    response = client.get(f"/api/runs/{backend.known_run_id}")
    assert response.status_code == 200
    assert response.json() == _run_status_response(backend.known_run_id).to_json()


def test_get_run_results_happy_path(running_server) -> None:
    client, backend = running_server
    response = client.get(f"/api/runs/{backend.known_run_id}/results")
    assert response.status_code == 200
    assert response.json() == _run_results_response(backend.known_run_id).to_json()


def test_get_run_status_404_for_unknown_run(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/runs/run_00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_get_run_results_404_for_unknown_run(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/runs/run_00000000-0000-4000-8000-000000000000/results")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_route_is_404(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


class _BlowsUpBackend(FakeBackend):
    """Raises an unexpected, undocumented exception from every GET route."""

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        raise RuntimeError("boom: read_config exploded with a secret path /Users/nope/config.json")

    def run_status(self, run_id: str) -> RunStatusResponse:
        raise RuntimeError("boom: run_status exploded with a secret path /Users/nope/run-details.json")

    def run_results(self, run_id: str) -> RunResultsResponse:
        raise RuntimeError("boom: run_results exploded with a secret path /Users/nope/results.json")


@pytest.mark.parametrize(
    "running_server",
    [_BlowsUpBackend()],
    indirect=True,
)
def test_get_config_500_on_unexpected_backend_exception(running_server) -> None:
    client, _backend = running_server
    response = client.get("/api/config")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "boom" not in body["error"]["message"]
    assert "/Users/nope" not in body["error"]["message"]
    assert "Traceback" not in json.dumps(body)


@pytest.mark.parametrize(
    "running_server",
    [_BlowsUpBackend()],
    indirect=True,
)
def test_get_run_status_500_on_unexpected_backend_exception(running_server) -> None:
    client, backend = running_server
    response = client.get(f"/api/runs/{backend.known_run_id}")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "boom" not in body["error"]["message"]
    assert "/Users/nope" not in body["error"]["message"]
    assert "Traceback" not in json.dumps(body)


@pytest.mark.parametrize(
    "running_server",
    [_BlowsUpBackend()],
    indirect=True,
)
def test_get_run_results_500_on_unexpected_backend_exception(running_server) -> None:
    client, backend = running_server
    response = client.get(f"/api/runs/{backend.known_run_id}/results")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "boom" not in body["error"]["message"]
    assert "/Users/nope" not in body["error"]["message"]
    assert "Traceback" not in json.dumps(body)


def test_post_run_config_digest_mismatch_is_409(running_server) -> None:
    client, backend = running_server
    request_payload = load_fixture("fixture-api-run-request-v1.json")
    assert request_payload["config_digest"] != backend.config.digest()
    response = client.post("/api/run", json=request_payload)
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "config_digest_mismatch"
    assert backend.started_requests == []


def test_post_run_malformed_json_is_422(running_server) -> None:
    client, backend = running_server
    response = client.post(
        "/api/run",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert backend.started_requests == []


def test_post_run_schema_violation_is_422(running_server) -> None:
    client, backend = running_server
    request_payload = load_fixture("fixture-api-run-request-v1.json")
    request_payload = dict(request_payload, config_digest=backend.config.digest())
    del request_payload["selection_cap"]
    response = client.post("/api/run", json=request_payload)
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "missing_key"
    assert backend.started_requests == []


def test_post_run_does_not_block_on_the_whole_traversal() -> None:
    """A backend that allocates promptly then keeps running for 2s must not make POST wait."""

    backend = FakeBackend(sleep_after_allocation_seconds=2.0)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            request_payload = dict(load_fixture("fixture-api-run-request-v1.json"), config_digest=backend.config.digest())
            started = time.monotonic()
            response = client.post("/api/run", json=request_payload)
            elapsed = time.monotonic() - started
        assert response.status_code == 202
        assert response.json()["run_id"] == backend.known_run_id
        assert elapsed < 1.0, f"POST /api/run took {elapsed}s; must return promptly after allocation"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_run_raise_before_allocation_is_mapped_to_422() -> None:
    backend = FakeBackend(pre_allocation_error=FindJobsContractError("invalid_value", "consent is stale"))
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            request_payload = dict(load_fixture("fixture-api-run-request-v1.json"), config_digest=backend.config.digest())
            response = client.post("/api/run", json=request_payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_value"
        assert backend.started_requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_run_raise_after_allocation_still_returns_202_and_status_reflects_failure() -> None:
    backend = FakeBackend(post_allocation_error=RuntimeError("assess node blew up"))
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            request_payload = dict(load_fixture("fixture-api-run-request-v1.json"), config_digest=backend.config.digest())
            response = client.post("/api/run", json=request_payload)
            assert response.status_code == 202
            run_id = response.json()["run_id"]

            deadline = time.monotonic() + 5.0
            status_body: dict[str, Any] = {}
            while time.monotonic() < deadline:
                status_response = client.get(f"/api/runs/{run_id}")
                assert status_response.status_code == 200
                status_body = status_response.json()
                if status_body["status"] == "failed":
                    break
                time.sleep(0.01)
            assert status_body.get("status") == "failed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_run_allocation_timeout_is_504() -> None:
    """A backend that never calls on_run_allocated within the timeout gets a 504."""

    class NeverAllocatesBackend(FakeBackend):
        def start_run(self, run_request: RunRequest, config_bytes: bytes, on_run_allocated: Callable[[str], None]) -> None:
            self.started_requests.append(run_request)
            time.sleep(1.0)  # longer than the injected 0.05s test timeout; never calls back

    backend = NeverAllocatesBackend()
    server = serve(backend=backend, bind=("127.0.0.1", 0), run_start_timeout_seconds=0.05)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=5.0) as client:
            request_payload = dict(load_fixture("fixture-api-run-request-v1.json"), config_digest=backend.config.digest())
            response = client.post("/api/run", json=request_payload)
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "run_start_timeout"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _invoke_handler(backend: Backend, *, client_address: tuple[str, int], method: str, path: str, body: bytes = b"") -> tuple[int, dict[str, Any]]:
    handler_cls = _make_handler(backend)
    handler = handler_cls.__new__(handler_cls)
    handler.client_address = client_address
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    headers = Message()
    if body:
        headers["Content-Length"] = str(len(body))
    handler.headers = headers
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.server = None
    if method == "GET":
        handler.do_GET()
    else:
        handler.do_POST()
    handler.wfile.seek(0)
    raw = handler.wfile.read()
    status_line, _, rest = raw.partition(b"\r\n")
    status_code = int(status_line.split(b" ")[1])
    _headers_blob, _, response_body = rest.partition(b"\r\n\r\n")
    parsed_body = json.loads(response_body) if response_body else {}
    return status_code, parsed_body


def test_non_loopback_peer_is_refused_on_get() -> None:
    backend = FakeBackend()
    status_code, body = _invoke_handler(
        backend,
        client_address=("8.8.8.8", 55555),
        method="GET",
        path="/api/config",
    )
    assert status_code == 403
    assert body["error"]["code"] == "forbidden"


def test_non_loopback_peer_is_refused_on_post() -> None:
    backend = FakeBackend()
    request_payload = json.dumps(dict(load_fixture("fixture-api-run-request-v1.json"), config_digest=backend.config.digest())).encode("utf-8")
    status_code, body = _invoke_handler(
        backend,
        client_address=("10.0.0.5", 55555),
        method="POST",
        path="/api/run",
        body=request_payload,
    )
    assert status_code == 403
    assert body["error"]["code"] == "forbidden"
    assert backend.started_requests == []


def test_ipv6_loopback_peer_is_accepted() -> None:
    backend = FakeBackend()
    status_code, body = _invoke_handler(
        backend,
        client_address=("::1", 55555),
        method="GET",
        path="/api/config",
    )
    assert status_code == 200
    assert body["config_digest"] == backend.config.digest()


def test_default_main_backend_raises_not_wired_yet() -> None:
    backend = NotWiredBackend()
    with pytest.raises(NotImplementedError, match="not wired yet"):
        backend.read_config()


def test_main_refuses_to_start_when_test_http_seam_is_active(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", raising=False)

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("must not start the server when a test seam is active without the flag")

    monkeypatch.setattr("gigai.scout.find_jobs.present_api._run_forever", _must_not_run)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP" in stderr
    assert "--allow-test-seams" in stderr


def test_main_refuses_to_start_when_test_model_seam_is_active(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", raising=False)
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("must not start the server when a test seam is active without the flag")

    monkeypatch.setattr("gigai.scout.find_jobs.present_api._run_forever", _must_not_run)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL" in stderr


def test_main_starts_with_loud_warning_when_test_seams_allowed(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")

    calls = []
    monkeypatch.setattr("gigai.scout.find_jobs.present_api._run_forever", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("gigai.setup.default_home_root", lambda: tmp_path)

    main(["--allow-test-seams"])

    assert len(calls) == 1
    stderr = capsys.readouterr().err
    assert "TEST SEAMS ACTIVE" in stderr
    assert "fixture data" in stderr


def test_main_starts_normally_when_no_test_seam_is_active(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", raising=False)
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", raising=False)

    calls = []
    monkeypatch.setattr("gigai.scout.find_jobs.present_api._run_forever", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("gigai.setup.default_home_root", lambda: tmp_path)

    main([])

    assert len(calls) == 1
    stderr = capsys.readouterr().err
    assert "TEST SEAMS ACTIVE" not in stderr


def test_main_home_flag_overrides_default_home_root(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", raising=False)
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", raising=False)

    custom_home = tmp_path / "custom-gigai-home"
    custom_home.mkdir()

    def _fail_if_called():
        raise AssertionError("--home should override default_home_root(), which must not be called")

    monkeypatch.setattr("gigai.setup.default_home_root", _fail_if_called)

    captured_backends = []

    def _capture_run_forever(bind, *, backend=None):
        captured_backends.append(backend)

    monkeypatch.setattr("gigai.scout.find_jobs.present_api._run_forever", _capture_run_forever)

    main(["--home", str(custom_home)])

    assert len(captured_backends) == 1
    assert captured_backends[0].home_root == custom_home.resolve()
