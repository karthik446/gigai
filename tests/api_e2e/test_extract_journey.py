"""P9b (A3): the resume-extraction journey over HTTP against the real
supervised server -- the setup wizard's first screen.

(a) ``profile_id`` (the migrated default profile) -> 200 with the fixture's
stack/seniority/titles and ``extractor: "model"``; (b) ``resume_text`` ->
200, nothing stored, nothing echoed; (c) neither input -> 422; (d) both
inputs -> 422; (e) the fixture's unavailable marker inside the pasted resume -> 503
``model_unavailable`` (the fake Ollama answers HTTP 503); (f) the fake model's garbage marker inside
the pasted resume -> 502 ``model_output_invalid`` through the SAME existing
fixture branch the assess journeys use; (g) CSRF 403 / 415.  Fixture:
``bindings._test_model_handler``'s additive extraction branch through the
existing model seam -- no live model call.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.private_records import list_imports
from gigai.scout.find_jobs.bindings import (
    TEST_MODEL_EXTRACT_REPLY,
    TEST_MODEL_GARBAGE_MARKER,
    TEST_MODEL_UNAVAILABLE_MARKER,
)

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_PASTED = "Pasted resume: staff engineer, nine years of Go and Kafka on Kubernetes."


def _assert_error(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def _gig_id(home: Path, target: Path) -> str:
    from gigai.workpad import resolve_workpad

    return resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True).gig_id


def test_extract_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        gig_id = _gig_id(home, target)
        imports_before = list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id)

        profiles = client.get("/api/profiles").json()
        selected_id = profiles["selected_profile_id"]
        assert selected_id, profiles
        selected = next(item for item in profiles["profiles"] if item["profile_id"] == selected_id)

        # (a) the selected profile's pinned resume -> the fixture extraction.
        by_profile, by_profile_latency = timed_request(
            "POST /api/resume/extract (profile_id)",
            lambda: client.post("/api/resume/extract", json={"profile_id": selected_id}),
        )
        assert by_profile.status_code == 200, by_profile.text
        a = by_profile.json()
        assert a["schema_version"] == "scout-resume-extract-response:1"
        assert a["stack"] == TEST_MODEL_EXTRACT_REPLY["stack"]
        assert a["seniority"] == TEST_MODEL_EXTRACT_REPLY["seniority"]
        assert a["titles"] == TEST_MODEL_EXTRACT_REPLY["titles"]
        assert a["extractor"] == "model" and a["model_target"] == "ollama_local"
        assert a["resume"] == {"profile_id": selected_id, "content_sha256": selected["resume_ref"]["content_sha256"]}
        # harness.add_resume's own text never comes back.
        assert "Python service experience" not in by_profile.text
        by_profile_latency.assert_within_budget()

        # (b) pasted text -> 200, never stored, never echoed.
        pasted, pasted_latency = timed_request(
            "POST /api/resume/extract (resume_text)",
            lambda: client.post("/api/resume/extract", json={"resume_text": _PASTED}),
        )
        assert pasted.status_code == 200, pasted.text
        b = pasted.json()
        assert b["resume"]["profile_id"] is None and b["resume"]["content_sha256"].startswith("sha256:")
        assert b["titles"] == TEST_MODEL_EXTRACT_REPLY["titles"]
        assert _PASTED not in pasted.text and "nine years" not in pasted.text
        assert list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id) == imports_before
        pasted_latency.assert_within_budget()

        # (c) neither input, (d) both inputs -> 422.
        _assert_error(client.post("/api/resume/extract", json={}), status=422, code="resume_input_invalid")
        _assert_error(
            client.post("/api/resume/extract", json={"resume_text": _PASTED, "profile_id": selected_id}),
            status=422, code="resume_input_invalid",
        )

        # (e) model unavailable: the fixture answers HTTP 503 when the
        # unavailable marker rides inside the pasted resume -> a typed 503.
        # (Never an unconfigured adapter kind here: `gigai setup` also
        # registers a Codex/Claude CLI it finds on the host, so
        # `model_target: "codex_cli"` would make a LIVE call on a dev box.)
        unavailable, unavailable_latency = timed_request(
            "POST /api/resume/extract (model unavailable)",
            lambda: client.post("/api/resume/extract", json={"resume_text": _PASTED + " " + TEST_MODEL_UNAVAILABLE_MARKER}),
        )
        _assert_error(unavailable, status=503, code="model_unavailable")
        unavailable_latency.assert_within_budget()

        # (f) garbage on both attempts (the existing fixture marker, carried
        # inside the pasted resume) -> 502.
        _assert_error(
            client.post("/api/resume/extract", json={"resume_text": _PASTED + " " + TEST_MODEL_GARBAGE_MARKER}),
            status=502, code="model_output_invalid",
        )

        # (g) CSRF: wrong Origin -> 403, wrong Content-Type -> 415.
        _assert_error(
            httpx.post(f"{server.base_url}/api/resume/extract", json={"resume_text": _PASTED}, headers={"Origin": "http://evil.example.test"}),
            status=403, code="forbidden_origin",
        )
        _assert_error(
            httpx.post(f"{server.base_url}/api/resume/extract", content=b"{}", headers={"Content-Type": "text/plain"}),
            status=415, code="unsupported_media_type",
        )

        # The same server still answers normally afterwards.
        again = client.post("/api/resume/extract", json={"profile_id": selected_id})
        assert again.status_code == 200, again.text

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
