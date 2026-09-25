"""P5: the quick-assess journey over HTTP against the real supervised server.

(a) job_url (single-job fixture) + selected profile -> 200 with a verdict;
(b) job_text + resume_text -> 200 and no new private record; (c) neither job
input -> 422; (d) JS-shell URL -> board-listing fallback -> 200; (e) fetch
failure host -> 502 ``job_fetch_failed``; (f) fake model returns garbage
twice -> 502 ``model_output_invalid``; (g) fake model sleeps past a 1 s test
timeout -> 504 ``assess_timeout`` (own server: the deadline env is read
only while the model seam is on); (h) ``GET /api/assessments`` lists (a)+(b);
(i) CSRF 403.  Fixtures: ``bindings._test_provider_handler`` (HTTP) and
``bindings._test_model_handler`` (model) through the two existing seams --
no live network or model call.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.private_records import list_imports
from gigai.scout.find_jobs.bindings import TEST_MODEL_GARBAGE_MARKER, TEST_MODEL_SLEEP_MARKER

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_POSTING = (
    "Acme is hiring a Software Engineer to build reliable Python services. "
    "Requirements: Python in production; GCP experience is a plus. Remote within the US."
)


def _assert_error(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def _gig_id(home: Path, target: Path) -> str:
    from gigai.workpad import resolve_workpad

    return resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True).gig_id


def test_assess_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        gig_id = _gig_id(home, target)
        imports_before = list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id)

        # (a) job_url -> Greenhouse single-job fixture + the selected profile.
        single, single_latency = timed_request(
            "POST /api/assess (job_url)",
            lambda: client.post("/api/assess", json={"job": {"job_url": "https://boards.greenhouse.io/acme/jobs/101"}}),
        )
        assert single.status_code == 200, single.text
        a = single.json()
        assert a["schema_version"] == "scout-assess-response:1"
        assert a["job"]["fetch_kind"] == "ats_single"
        assert a["job"]["title"] == "Software Engineer" and a["job"]["company"] == "Acme"
        assert a["job"]["normalized_url"] == "https://boards.greenhouse.io/acme/jobs/101"
        assert "text" not in a["job"]
        assert a["result"]["verdict"] == "pending_user_answers"
        assert a["result"]["structured_questions"][0]["question_id"] == "cloud:gcp"
        assert a["resume"]["profile_id"], "the selected profile's resume must be the default"
        assert a["preferences"]["titles"] == ["software engineer"]
        assert a["producer"]["model_target"] == "ollama_local"
        single_latency.assert_within_budget()

        # (b) job_text + resume_text -> 200, and no new private record.
        pasted, pasted_latency = timed_request(
            "POST /api/assess (job_text+resume_text)",
            lambda: client.post(
                "/api/assess",
                json={"job": {"job_text": _POSTING}, "resume": {"resume_text": "Pasted resume: Python services for six years."}},
            ),
        )
        assert pasted.status_code == 200, pasted.text
        b = pasted.json()
        assert b["job"]["fetch_kind"] == "pasted" and b["resume"]["profile_id"] is None
        assert "Pasted resume" not in pasted.text and _POSTING not in pasted.text
        imports_after = list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id)
        assert imports_after == imports_before
        pasted_latency.assert_within_budget()

        # (c) neither job input -> 422.
        _assert_error(client.post("/api/assess", json={"job": {}}), status=422, code="job_input_invalid")

        # (d) JavaScript-shell URL -> board-listing fallback -> 200.
        shell = client.post("/api/assess", json={"job": {"job_url": "https://boards.greenhouse.io/shell/jobs/303"}})
        assert shell.status_code == 200, shell.text
        d = shell.json()
        assert d["job"]["fetch_kind"] == "ats_board"
        assert d["job"]["title"] == "Platform Engineer"

        # (e) a host the fixture does not know -> 502 job_fetch_failed.
        _assert_error(
            client.post("/api/assess", json={"job": {"job_url": "https://careers.unreachable.test/jobs/1"}}),
            status=502, code="job_fetch_failed",
        )

        # (f) the fake model returns garbage on both attempts -> 502.
        _assert_error(
            client.post("/api/assess", json={"job": {"job_text": _POSTING + " " + TEST_MODEL_GARBAGE_MARKER}}),
            status=502, code="model_output_invalid",
        )

        # (h) the list carries (a)+(b) (+(d)), newest first, never job text.
        listing, listing_latency = timed_request("GET /api/assessments", lambda: client.get("/api/assessments"))
        assert listing.status_code == 200, listing.text
        items = listing.json()["items"]
        assert {item["stored_path"] for item in items} == {a["stored_path"], b["stored_path"], d["stored_path"]}
        assert all("text" not in item["job"] for item in items)
        by_profile = client.get("/api/assessments", params={"profile_id": a["resume"]["profile_id"]}).json()["items"]
        assert {item["stored_path"] for item in by_profile} == {a["stored_path"], d["stored_path"]}
        listing_latency.assert_within_budget()

        # (i) CSRF: wrong Origin -> 403, wrong Content-Type -> 415.
        _assert_error(
            httpx.post(f"{server.base_url}/api/assess", json={"job": {"job_text": _POSTING}}, headers={"Origin": "http://evil.example.test"}),
            status=403, code="forbidden_origin",
        )
        _assert_error(
            httpx.post(f"{server.base_url}/api/assess", content=b"{}", headers={"Content-Type": "text/plain"}),
            status=415, code="unsupported_media_type",
        )

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_assess_timeout_is_a_typed_504(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(g): the fake model sleeps past a 1 s deadline -> 504 ``assess_timeout``.

    Its own server: ``GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS`` is inherited by
    the spawned server child and read only while the model seam is on, so
    it must never be set for the main journey's ordinary calls.
    """

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    monkeypatch.setenv("GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS", "1")
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        slow, slow_latency = timed_request(
            "POST /api/assess (timeout)",
            lambda: client.post("/api/assess", json={"job": {"job_text": _POSTING + " " + TEST_MODEL_SLEEP_MARKER}}),
        )
        _assert_error(slow, status=504, code="assess_timeout")
        slow_latency.assert_within_budget()
        assert client.get("/api/assessments").json()["items"] == []

        # The same server still answers a fast call normally afterwards.
        fine = client.post("/api/assess", json={"job": {"job_text": _POSTING}})
        assert fine.status_code == 200, fine.text
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
