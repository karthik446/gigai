"""``POST /api/resume/extract`` reads a stored resume that no profile uses.

A resume stored with ``gigai scout resume add`` before any profile exists is
offered by the wizard ("Choose existing"), and "Analyze resume" had nothing
to send for it: the route took pasted text or a profile. It now also takes
``resume_ref`` (``{"record_id", "revision_id"}``, optionally with
``content_sha256``).

Same fixtures as ``test_resume_extract_api.py`` (a real gig, the real
``ScoutFindJobsBackend`` + ``serve()``, a scripted model binding). The
stored resume of these tests is a SECOND one, pinned by no profile. Checked
on every answer and on the log: never the resume's text.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from gigai.private_records import list_imports
from gigai.scout.find_jobs.api import extract
from gigai.scout.find_jobs.api.extract import ResumeExtractError, StoredResumeRef, parse_request
from gigai.scout.find_jobs.assess_contracts import AssessResumeInput
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend
from gigai.scout.profile_records import list_profiles
from gigai.scout.resume_import import ImportedResume, import_resume_bytes

from tests.behaviors.scout_find_jobs.test_resume_extract_api import (
    _REPLY,
    _assert_error,
    _config_with_ollama,
    _install_model,
    _serve,
)
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

STORED = "Jordan Rivera\nStaff engineer: eleven years of Rust and Postgres.\n".encode("utf-8")
UNKNOWN_RECORD = "record_00000000-0000-4000-8000-000000000000"
UNKNOWN_REVISION = "revision_00000000-0000-4000-8000-000000000000"


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


@pytest.fixture
def stored(fx: ProfileFixtureGig) -> ImportedResume:
    """A stored resume no profile pins (the file name has a space: uat-bug-023)."""

    resume = import_resume_bytes(
        home_root=fx.home_root, requested_target=fx.target, data=STORED, file_name="Jordan Rivera.md"
    )
    assert resume.created
    assert all(item.resume_ref.record_id != resume.record_id for item in list_profiles(fx.resolved))
    return resume


@pytest.fixture
def client(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch):
    config = _config_with_ollama(fx.home_root)
    monkeypatch.setattr(extract, "load_config", lambda home_root: config)
    server, thread, host, port = _serve(ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target))
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=20.0) as http:
            yield http
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _ref(resume: ImportedResume, **extra: object) -> dict[str, object]:
    return {"record_id": resume.record_id, "revision_id": resume.revision_id, **extra}


def _imports(fx: ProfileFixtureGig) -> list[dict[str, object]]:
    return list_imports(home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id)


# --- the request ---------------------------------------------------------------------------


def test_a_resume_ref_is_one_of_the_three_inputs() -> None:
    ref = {"record_id": "record_x", "revision_id": "revision_x"}

    assert parse_request({"resume_ref": ref}) == (StoredResumeRef("record_x", "revision_x"), None)
    assert parse_request({"resume_ref": {**ref, "content_sha256": "sha256:abc"}})[0] == StoredResumeRef(
        "record_x", "revision_x", "sha256:abc"
    )
    assert parse_request({"resume_ref": {**ref, "content_sha256": None}})[0] == StoredResumeRef("record_x", "revision_x")
    # The inputs the route already took come back as they did.
    assert parse_request({"resume_text": "text", "resume_ref": None})[0] == AssessResumeInput(resume_text="text")
    assert parse_request({"profile_id": "profile_x"})[0] == AssessResumeInput(profile_id="profile_x")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "resume_input_invalid"),
        ({"resume_ref": None}, "resume_input_invalid"),
        ({"resume_ref": "record_x"}, "wrong_type"),
        ({"resume_ref": ["record_x", "revision_x"]}, "wrong_type"),
        ({"resume_ref": {}}, "resume_input_invalid"),
        ({"resume_ref": {"record_id": "record_x"}}, "resume_input_invalid"),
        ({"resume_ref": {"revision_id": "revision_x"}}, "resume_input_invalid"),
        ({"resume_ref": {"record_id": "", "revision_id": "revision_x"}}, "resume_input_invalid"),
        ({"resume_ref": {"record_id": 7, "revision_id": "revision_x"}}, "resume_input_invalid"),
        ({"resume_ref": {"record_id": "record_x", "revision_id": "revision_x", "content_sha256": 7}}, "resume_input_invalid"),
        ({"resume_ref": {"record_id": "record_x", "revision_id": "revision_x", "text": "x"}}, "unknown_key"),
        ({"resume_ref": {"record_id": "record_x", "revision_id": "revision_x"}, "resume_text": "text"}, "resume_input_invalid"),
        ({"resume_ref": {"record_id": "record_x", "revision_id": "revision_x"}, "profile_id": "profile_x"}, "resume_input_invalid"),
    ],
)
def test_a_bad_request_is_refused_with_a_typed_code(body: dict[str, object], code: str) -> None:
    with pytest.raises(ResumeExtractError) as refused:
        parse_request(body)
    assert refused.value.code == code


# --- the route -----------------------------------------------------------------------------


def test_a_stored_resume_no_profile_uses_is_analysed(
    fx: ProfileFixtureGig,
    stored: ImportedResume,
    client: httpx.Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    binding = _install_model(monkeypatch, [_REPLY])
    imports_before = _imports(fx)
    profiles_before = list_profiles(fx.resolved)

    with caplog.at_level(logging.INFO, logger="gigai.scout.server"):
        response = client.post("/api/resume/extract", json={"resume_ref": _ref(stored), "model_target": "ollama_local"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "schema_version": "scout-resume-extract-response:1",
        "stack": ["Python", "Kubernetes", "PostgreSQL"],
        "seniority": "staff",
        "titles": ["Staff Software Engineer", "Staff Backend Engineer"],
        "extractor": "model",
        "model_target": "ollama_local",
        "resolved_target": "fixture-port",
        "resume": {"profile_id": None, "content_sha256": stored.content_sha256},
    }
    # The stored text reached the model once, and nothing else.
    assert len(binding.port.prompts) == 1 and binding.closed
    assert "eleven years of Rust" in binding.port.prompts[0]
    for leak in ("eleven years", "Jordan Rivera", "Rust"):
        assert leak not in response.text
        assert leak not in caplog.text
    assert stored.record_id not in caplog.text
    assert "resume extraction: source=stored target=ollama_local resolved=fixture-port stack=3 titles=2" in caplog.text
    # Reading it stored nothing and touched no profile.
    assert _imports(fx) == imports_before
    assert list_profiles(fx.resolved) == profiles_before


def test_a_named_digest_must_be_the_stored_resumes(
    stored: ImportedResume, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = _install_model(monkeypatch, [_REPLY])

    same = client.post("/api/resume/extract", json={"resume_ref": _ref(stored, content_sha256=stored.content_sha256)})
    assert same.status_code == 200, same.text
    assert same.json()["resume"]["content_sha256"] == stored.content_sha256

    other = client.post("/api/resume/extract", json={"resume_ref": _ref(stored, content_sha256="sha256:" + "0" * 64)})
    _assert_error(other, status=404, code="resume_digest_mismatch")
    assert len(binding.port.prompts) == 1  # the refused request called no model


def test_a_resume_that_is_not_stored_is_404_and_calls_no_model(
    stored: ImportedResume, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = _install_model(monkeypatch, [_REPLY])

    for ref in (
        {"record_id": UNKNOWN_RECORD, "revision_id": UNKNOWN_REVISION},
        {"record_id": stored.record_id, "revision_id": UNKNOWN_REVISION},
        {"record_id": "../../etc/passwd", "revision_id": stored.revision_id},
    ):
        _assert_error(client.post("/api/resume/extract", json={"resume_ref": ref}), status=404, code="resume_unavailable")
    assert binding.port.prompts == []


def test_a_failed_model_call_logs_the_source_kind_only(
    stored: ImportedResume, client: httpx.Client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_model(monkeypatch, ["not json", "still not json"])

    with caplog.at_level(logging.INFO, logger="gigai.scout.server"):
        response = client.post("/api/resume/extract", json={"resume_ref": _ref(stored)})

    _assert_error(response, status=502, code="model_output_invalid")
    assert "resume extraction failed: source=stored target=ollama_local code=model_output_invalid" in caplog.text
    assert "eleven years" not in caplog.text and "eleven years" not in response.text


def test_a_target_with_no_gig_is_404(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    home.mkdir()
    target.mkdir()
    server, thread, host, port = _serve(ScoutFindJobsBackend(home_root=home, target=target))
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=20.0) as http:
            response = http.post(
                "/api/resume/extract",
                json={"resume_ref": {"record_id": UNKNOWN_RECORD, "revision_id": UNKNOWN_REVISION}},
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    _assert_error(response, status=404, code="resume_unavailable")
