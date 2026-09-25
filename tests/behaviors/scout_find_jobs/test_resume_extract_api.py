"""P9b (A3): ``POST /api/resume/extract`` over the real ``ScoutFindJobsBackend``
+ ``serve()`` (in-process server thread), against a real gig
(``build_gig_with_resume``), mirroring ``test_present_assess_api.py``.

The model is a scripted binding installed by patching
``proposal_execution.resolve_model_adapter`` (C1); the home's GigAI config
is swapped for one with an ``ollama_local``-adapter target by patching
``extract.load_config`` (the fixture home's own config has only the
deterministic endpoint -- that unpatched state is what the 503 test relies
on).  Every response is checked for the no-leak rule: never the resume text.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai.adapters.ollama_local import OllamaLocalAdapterError
from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.api import extract
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.profile_records import selected_profile
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services on Kubernetes for six years. (fixture only.)\n"
_PASTED = "Pasted resume: nine years of Go and Kafka, staff level, led a platform team."
_REPLY = json.dumps(
    {
        "stack": ["Python", "python", "Kubernetes", "  PostgreSQL  ", 7, ""],
        "seniority": "Staff",
        "titles": ["Staff Software Engineer", "Staff Backend Engineer", "staff software engineer"],
    }
)


class _ScriptedPort:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []
        self.name = "fixture-port"

    def invoke(self, request):
        self.prompts.append(request.prompt)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return InvocationResult(
            status="success", output_text=item, resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object]) -> None:
        self.port = _ScriptedPort(outputs)
        self.closed = False

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        self.closed = True


def _config_with_ollama(home: Path):
    return build_config(
        home_root=home, workpad_root=home.parent / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="ollama", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(name="ollama-default", endpoint="ollama", model="fixture-model", capabilities=("text",), max_output_tokens=512, model_digest="sha256:" + "c" * 64),
        ),
    )


def _install_model(monkeypatch: pytest.MonkeyPatch, outputs: list[object]) -> _ScriptedBinding:
    binding = _ScriptedBinding(outputs)

    def resolve(config, adapter_target, **_kwargs):
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture
def ollama_config(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config_with_ollama(fx.home_root)
    monkeypatch.setattr(extract, "load_config", lambda home_root: config)


def _serve(backend: ScoutFindJobsBackend):
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    return server, thread, host, port


@pytest.fixture
def running_server(fx: ProfileFixtureGig):
    server, thread, host, port = _serve(ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target))
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=20.0) as client:
            yield client, fx, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _assert_error(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


# --- the fixture contract -------------------------------------------------------------------------


def test_prompt_header_matches_the_fixture_marker() -> None:
    """``bindings._test_model_handler`` keys its extraction reply on this literal."""

    assert extract.EXTRACT_PROMPT_HEADER == bindings.TEST_MODEL_EXTRACT_MARKER
    assert extract.render_prompt("x").startswith(extract.EXTRACT_PROMPT_HEADER)


# --- happy paths --------------------------------------------------------------------------------------


def test_extract_from_the_selected_profile(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    client, fx, _port = running_server
    binding = _install_model(monkeypatch, [_REPLY])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    with caplog.at_level(logging.INFO, logger="gigai.scout.server"):
        response = client.post("/api/resume/extract", json={"profile_id": selected.profile_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "schema_version": "scout-resume-extract-response:1",
        # dedupe (case-insensitive), whitespace trimmed, non-strings dropped, order kept
        "stack": ["Python", "Kubernetes", "PostgreSQL"],
        "seniority": "staff",
        "titles": ["Staff Software Engineer", "Staff Backend Engineer"],
        "extractor": "model",
        "model_target": "ollama_local",
        "resolved_target": "fixture-port",
        "resume": {"profile_id": selected.profile_id, "content_sha256": selected.resume_ref.content_sha256},
    }
    # The resume reached the prompt, and never the wire or the log.
    assert "six years" in binding.port.prompts[0] and binding.closed
    assert "six years" not in response.text and "Fixture Resume" not in response.text
    assert "six years" not in caplog.text and "Fixture Resume" not in caplog.text
    assert "Python" not in caplog.text  # counts only
    assert "resume extraction: source=profile target=ollama_local resolved=fixture-port stack=3 titles=2" in caplog.text


def test_extract_from_pasted_text_never_stores_it(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.private_records import list_imports

    client, fx, _port = running_server
    binding = _install_model(monkeypatch, [_REPLY])
    before = list_imports(home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id)

    response = client.post("/api/resume/extract", json={"resume_text": _PASTED, "model_target": "ollama_local"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resume"]["profile_id"] is None and body["resume"]["content_sha256"].startswith("sha256:")
    assert body["stack"] == ["Python", "Kubernetes", "PostgreSQL"]
    assert _PASTED not in response.text and "nine years" not in response.text
    assert "nine years" in binding.port.prompts[0]
    after = list_imports(home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id)
    assert after == before


def test_garbage_then_good_output_is_retried_once(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    binding = _install_model(monkeypatch, ["Sorry, no JSON here.", "```json\n" + _REPLY + "\n```"])
    response = client.post("/api/resume/extract", json={"resume_text": _PASTED})
    assert response.status_code == 200, response.text
    assert response.json()["titles"] == ["Staff Software Engineer", "Staff Backend Engineer"]
    assert len(binding.port.prompts) == 2


def test_unknown_seniority_and_partial_output_are_tolerated(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    _install_model(monkeypatch, [json.dumps({"stack": ["Go"], "seniority": "unknown", "titles": "not a list"})])
    response = client.post("/api/resume/extract", json={"resume_text": _PASTED})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stack"] == ["Go"] and body["seniority"] is None and body["titles"] == []


# --- request validation --------------------------------------------------------------------------------


def test_bad_requests_are_422_with_typed_codes(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, fx, _port = running_server
    binding = _install_model(monkeypatch, [_REPLY])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    _assert_error(client.post("/api/resume/extract", json={}), status=422, code="resume_input_invalid")
    _assert_error(
        client.post("/api/resume/extract", json={"resume_text": _PASTED, "profile_id": selected.profile_id}),
        status=422, code="resume_input_invalid",
    )
    _assert_error(client.post("/api/resume/extract", json={"resume_text": "   "}), status=422, code="resume_input_invalid")
    _assert_error(client.post("/api/resume/extract", json={"resume_text": 12}), status=422, code="wrong_type")
    _assert_error(client.post("/api/resume/extract", json={"resume_text": _PASTED, "extra": 1}), status=422, code="unknown_key")
    _assert_error(client.post("/api/resume/extract", json={"resume_text": _PASTED, "model_target": "gpt"}), status=422, code="bad_enum")
    _assert_error(client.post("/api/resume/extract", json=["not", "an", "object"]), status=422, code="wrong_type")
    _assert_error(
        client.post("/api/resume/extract", content=b"{not json", headers={"Content-Type": "application/json"}),
        status=422, code="wrong_type",
    )
    assert binding.port.prompts == []  # none of these reached the model


def test_unknown_profile_is_404(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    binding = _install_model(monkeypatch, [_REPLY])
    response = client.post("/api/resume/extract", json={"profile_id": "profile_00000000-0000-4000-8000-00000000dead"})
    _assert_error(response, status=404, code="profile_not_found")
    assert binding.port.prompts == []


# --- model-side errors ---------------------------------------------------------------------------------


def test_no_configured_model_target_is_503(running_server, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture home's real config has no ollama_local target: 503, typed, no 500."""

    client, _fx, _port = running_server
    _install_model(monkeypatch, [_REPLY])
    _assert_error(client.post("/api/resume/extract", json={"resume_text": _PASTED}), status=503, code="model_target_unavailable")


def test_adapter_failure_is_503_model_unavailable(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    _install_model(monkeypatch, [OllamaLocalAdapterError("local Ollama endpoint returned HTTP 500 for POST /api/chat")])
    _assert_error(client.post("/api/resume/extract", json={"resume_text": _PASTED}), status=503, code="model_unavailable")


def test_garbage_model_output_twice_is_502(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    binding = _install_model(monkeypatch, ["nope", "{\"stack\": [], \"titles\": []}"])
    _assert_error(client.post("/api/resume/extract", json={"resume_text": _PASTED}), status=502, code="model_output_invalid")
    assert len(binding.port.prompts) == 2


def test_adapter_timeout_is_504(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    timed_out = OllamaLocalAdapterError("local Ollama request timed out")
    timed_out.__cause__ = httpx.ReadTimeout("read timed out")
    _install_model(monkeypatch, [timed_out])
    _assert_error(client.post("/api/resume/extract", json={"resume_text": _PASTED}), status=504, code="extract_timeout")


# --- guards -----------------------------------------------------------------------------------------------


def test_post_extract_goes_through_the_csrf_guard(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, port = running_server
    binding = _install_model(monkeypatch, [_REPLY])
    base = str(client.base_url)
    payload = {"resume_text": _PASTED}

    wrong_type = httpx.post(f"{base}/api/resume/extract", content=json.dumps(payload).encode(), headers={"Content-Type": "text/plain"})
    _assert_error(wrong_type, status=415, code="unsupported_media_type")
    wrong_origin = httpx.post(f"{base}/api/resume/extract", json=payload, headers={"Origin": "http://evil.example.test"})
    _assert_error(wrong_origin, status=403, code="forbidden_origin")
    wrong_host = httpx.post(f"{base}/api/resume/extract", json=payload, headers={"Host": "rebinder.example.test:80"})
    _assert_error(wrong_host, status=403, code="forbidden_origin")
    assert binding.port.prompts == []

    same_origin = client.post("/api/resume/extract", json=payload, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert same_origin.status_code == 200, same_origin.text


def test_get_is_not_a_route(running_server) -> None:
    client, _fx, _port = running_server
    response = client.get("/api/resume/extract")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


def test_backend_without_a_target_is_404_target_unavailable(fx: ProfileFixtureGig) -> None:
    server, thread, host, port = _serve(ScoutFindJobsBackend(home_root=fx.home_root, target=None))
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            _assert_error(client.post("/api/resume/extract", json={"resume_text": _PASTED}), status=404, code="target_unavailable")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
