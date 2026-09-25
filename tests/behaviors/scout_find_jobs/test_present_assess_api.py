"""P5: ``POST /api/assess`` / ``GET /api/assessments`` over the real
``ScoutFindJobsBackend`` + ``serve()`` (in-process server thread), against a
real gig (``build_gig_with_resume``), mirroring ``test_present_profiles_api.py``.

The model is a scripted binding installed by patching
``proposal_execution.resolve_model_adapter`` (plan constraint C1); the home's
GigAI config is swapped for one with an ``ollama_local``-adapter target by
patching ``quick_assess.load_config`` (the fixture home's own ``build_config``
default has only the deterministic endpoint -- that unpatched state is what
the 503 test relies on).  The CSRF/loopback guard is proven on the new POST
too (C8).  Responses are checked for the no-leak rule: never resume text,
never the job text.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai.adapters.ollama_local import OllamaLocalAdapterError
from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout import quick_assess
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.profile_records import selected_profile
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"
_POSTING = "Acme is hiring a Staff AI Engineer: 5+ years of Python in production; GCP a plus. Remote within the US."
_PENDING = json.dumps(
    {
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
            {"requirement": "GCP", "class": "askable", "status": "unclear", "resume_evidence": []},
        ],
        "suggestions": [],
        "questions": [{"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    }
)
_MATCH = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)


class _ScriptedPort:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []

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

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


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
    monkeypatch.setattr(quick_assess, "load_config", lambda home_root: config)


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


# --- happy path + list ------------------------------------------------------------------------


def test_post_assess_then_get_assessments(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, fx, _port = running_server
    binding = _install_model(monkeypatch, [_PENDING, _MATCH])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    response = client.post("/api/assess", json={"job": {"job_text": _POSTING, "title": "Staff AI Engineer", "company": "Acme"}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "scout-assess-response:1"
    assert body["result"]["verdict"] == "pending_user_answers"
    assert body["result"]["structured_questions"] == [
        {"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}
    ]
    assert body["resume"]["profile_id"] == selected.profile_id
    assert set(body["resume"]) == {"schema_version", "profile_id", "pinned", "content_sha256"}
    assert body["job"]["fetch_kind"] == "pasted" and body["job"]["title"] == "Staff AI Engineer"
    assert "text" not in body["job"] and body["job"]["text_sha256"].startswith("sha256:")
    assert body["preferences"] == {
        "visa_sponsorship_required": False,
        "titles": ["staff ai engineer", "principal machine learning engineer"],
        "countries": ["US"],
    }
    assert body["producer"]["callable"] == "scout.assess" and body["producer"]["model_target"] == "ollama_local"
    assert body["usage"]["input_tokens"] == 1
    assert Path(body["stored_path"]).is_file()
    # Never the resume or job text on the wire.
    assert "six years" not in response.text.replace('"six years"', "")  # the model's evidence quote is allowed
    assert _POSTING not in response.text and "Fixture Resume" not in response.text
    # The MUST: countries (find-jobs.json) and titles (profile) reached the prompt.
    assert "US" in binding.port.prompts[0] and "principal machine learning engineer" in binding.port.prompts[0]

    second = client.post(
        "/api/assess",
        json={"job": {"job_text": "Second posting: Python platform engineer."}, "resume": {"resume_text": "Pasted resume text."}},
    )
    assert second.status_code == 200, second.text
    assert second.json()["resume"]["profile_id"] is None
    assert "Pasted resume text" not in second.text

    listing = client.get("/api/assessments")
    assert listing.status_code == 200, listing.text
    listed = listing.json()
    assert listed["schema_version"] == "scout-assessments-response:1"
    assert {item["stored_path"] for item in listed["items"]} == {body["stored_path"], second.json()["stored_path"]}
    assert all("text" not in item["job"] for item in listed["items"])

    by_profile = client.get("/api/assessments", params={"profile_id": selected.profile_id}).json()
    assert [item["stored_path"] for item in by_profile["items"]] == [body["stored_path"]]
    ephemeral = client.get("/api/assessments", params={"profile_id": "ephemeral"}).json()
    assert [item["stored_path"] for item in ephemeral["items"]] == [second.json()["stored_path"]]
    matched = client.get("/api/assessments", params={"verdict": "matched_above_threshold"}).json()
    assert [item["stored_path"] for item in matched["items"]] == [second.json()["stored_path"]]
    assert client.get("/api/assessments", params={"profile_id": "profile_nobody"}).json()["items"] == []
    _assert_error(client.get("/api/assessments", params={"profile_id": "../../etc"}), status=422, code="invalid_value")


def test_get_assessments_is_empty_before_any_assessment(running_server) -> None:
    client, _fx, _port = running_server
    response = client.get("/api/assessments")
    assert response.status_code == 200, response.text
    assert response.json() == {"schema_version": "scout-assessments-response:1", "items": []}
    _assert_error(client.get("/api/assessments", params={"verdict": "maybe"}), status=422, code="bad_enum")


# --- request validation ---------------------------------------------------------------------------


def test_bad_requests_are_422_with_typed_codes(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    binding = _install_model(monkeypatch, [_MATCH])

    _assert_error(client.post("/api/assess", json={"job": {}}), status=422, code="job_input_invalid")
    _assert_error(
        client.post("/api/assess", json={"job": {"job_url": "https://x.test/1", "job_text": "both"}}),
        status=422, code="job_input_invalid",
    )
    _assert_error(
        client.post("/api/assess", json={"job": {"job_text": _POSTING}, "resume": {"profile_id": "p", "resume_text": "r"}}),
        status=422, code="resume_input_invalid",
    )
    _assert_error(client.post("/api/assess", json={"job": {"job_text": _POSTING}, "extra": 1}), status=422, code="unknown_key")
    _assert_error(client.post("/api/assess", json={}), status=422, code="missing_key")
    _assert_error(
        client.post("/api/assess", json={"job": {"job_text": _POSTING}, "model_target": "gpt"}), status=422, code="bad_enum"
    )
    _assert_error(
        client.post("/api/assess", json={"job": {"job_text": _POSTING}, "preferences": {"countries": ["usa"]}}),
        status=422, code="invalid_value",
    )
    _assert_error(client.post("/api/assess", json={"job": {"job_text": "  "}}), status=422, code="job_text_unavailable")
    _assert_error(
        client.post("/api/assess", content=b"{not json", headers={"Content-Type": "application/json"}),
        status=422, code="wrong_type",
    )
    assert binding.port.prompts == []  # none of these reached the model


def test_unknown_profile_is_404(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    _install_model(monkeypatch, [_MATCH])
    response = client.post(
        "/api/assess",
        json={"job": {"job_text": _POSTING}, "resume": {"profile_id": "profile_00000000-0000-4000-8000-00000000dead"}},
    )
    _assert_error(response, status=404, code="profile_not_found")


# --- model-side errors ------------------------------------------------------------------------------


def test_no_configured_model_target_is_503(running_server, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture home's real config has no ollama_local target: 503, typed, no 500."""

    client, _fx, _port = running_server
    _install_model(monkeypatch, [_MATCH])
    _assert_error(client.post("/api/assess", json={"job": {"job_text": _POSTING}}), status=503, code="model_target_unavailable")


def test_garbage_model_output_twice_is_502(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    binding = _install_model(monkeypatch, ["nope", "still nope"])
    _assert_error(client.post("/api/assess", json={"job": {"job_text": _POSTING}}), status=502, code="model_output_invalid")
    assert len(binding.port.prompts) == 2
    assert client.get("/api/assessments").json()["items"] == []


def test_adapter_timeout_is_504(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, _port = running_server
    timed_out = OllamaLocalAdapterError("local Ollama request timed out")
    timed_out.__cause__ = httpx.ReadTimeout("read timed out")
    _install_model(monkeypatch, [timed_out])
    _assert_error(client.post("/api/assess", json={"job": {"job_text": _POSTING}}), status=504, code="assess_timeout")


def test_job_fetch_failure_is_502(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    client, _fx, _port = running_server
    _install_model(monkeypatch, [_MATCH])
    _assert_error(
        client.post("/api/assess", json={"job": {"job_url": "https://nowhere.example.test/jobs/1"}}),
        status=502, code="job_fetch_failed",
    )


# --- guards ------------------------------------------------------------------------------------------


def test_post_assess_goes_through_the_csrf_guard(running_server, ollama_config, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _fx, port = running_server
    binding = _install_model(monkeypatch, [_MATCH])
    base = str(client.base_url)

    wrong_type = httpx.post(f"{base}/api/assess", content=json.dumps({"job": {"job_text": _POSTING}}).encode(), headers={"Content-Type": "text/plain"})
    _assert_error(wrong_type, status=415, code="unsupported_media_type")
    wrong_origin = httpx.post(f"{base}/api/assess", json={"job": {"job_text": _POSTING}}, headers={"Origin": "http://evil.example.test"})
    _assert_error(wrong_origin, status=403, code="forbidden_origin")
    wrong_host = httpx.post(f"{base}/api/assess", json={"job": {"job_text": _POSTING}}, headers={"Host": "rebinder.example.test:80"})
    _assert_error(wrong_host, status=403, code="forbidden_origin")
    assert binding.port.prompts == []

    same_origin = client.post("/api/assess", json={"job": {"job_text": _POSTING}}, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert same_origin.status_code == 200, same_origin.text


def test_backend_without_a_target_is_404_target_unavailable(fx: ProfileFixtureGig) -> None:
    server, thread, host, port = _serve(ScoutFindJobsBackend(home_root=fx.home_root, target=None))
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            _assert_error(client.post("/api/assess", json={"job": {"job_text": _POSTING}}), status=404, code="target_unavailable")
            _assert_error(client.get("/api/assessments"), status=404, code="target_unavailable")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
