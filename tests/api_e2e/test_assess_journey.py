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

uat-bug-014/015: a posting assessed by URL carries its fetched text
(``posting_text``; pasted text never) and, with a Jev key, one Jev score
(``rank_score``, the fake Jev of ``test_rank_journey.py``); with no key the
assessment is unchanged and ``rank_skip_reason`` says ``no_key``.
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
    # No Jev key in this journey (the server child inherits the environment).
    monkeypatch.delenv("JEV_API_KEY", raising=False)
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
        # uat-bug-014: the fetched public posting text is served; uat-bug-015:
        # no Jev key -> no score, and the response says why.
        assert a["posting_text"] == "Build reliable Python services."
        assert "rank_score" not in a and a["rank_skip_reason"] == "no_key"
        # assess-origin-field: a POST that names no origin is a quick assessment.
        assert a["origin"] == "quick_assess"
        single_latency.assert_within_budget()

        # (b) job_text + resume_text -> 200, and no new private record.
        pasted, pasted_latency = timed_request(
            "POST /api/assess (job_text+resume_text)",
            lambda: client.post(
                "/api/assess",
                json={
                    "job": {"job_text": _POSTING},
                    "resume": {"resume_text": "Pasted resume: Python services for six years."},
                    "origin": "quick_assess",  # what the Assess page sends
                },
            ),
        )
        assert pasted.status_code == 200, pasted.text
        b = pasted.json()
        assert b["job"]["fetch_kind"] == "pasted" and b["resume"]["profile_id"] is None
        assert b["origin"] == "quick_assess"
        assert "Pasted resume" not in pasted.text and _POSTING not in pasted.text
        assert "posting_text" not in b and "rank_score" not in b
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
        by_path = {item["stored_path"]: item for item in items}
        assert by_path[a["stored_path"]]["posting_text"] == a["posting_text"]
        assert by_path[d["stored_path"]]["posting_text"] == d["posting_text"] != ""
        assert "posting_text" not in by_path[b["stored_path"]]
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


def test_assess_journey_with_a_jev_key_stores_text_and_jev_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """uat-bug-014 + uat-bug-015: the operator's flow -- a Jev key exists,
    "+ Assess a job" with a public URL -> the job page's data
    (``GET /api/assessments``) carries the posting text AND a Jev score in
    the run rows' ``rank_scores`` shape.  Fake Jev
    (``GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1``); no live Jev call."""

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch, test_jev=True)
    try:
        client = server.client
        url = "https://boards.greenhouse.io/acme/jobs/101"

        first, first_latency = timed_request(
            "POST /api/assess (job_url, Jev key)", lambda: client.post("/api/assess", json={"job": {"job_url": url}})
        )
        assert first.status_code == 200, first.text
        a = first.json()
        assert a["result"]["verdict"] == "pending_user_answers"
        assert a["posting_text"] == "Build reliable Python services." and "text" not in a["job"]
        assert a["rank_score"] == {
            "normalized_url": url,
            "content_sha256": a["job"]["text_sha256"],
            "fit": "strong",
            "score": 89,
            "reasons": ["stack_match"],
            "mismatch_flags": [],
            "hidden_by_default": False,
            "cost_usd": "0.000500",
            "cached": False,
        }
        assert "rank_skip_reason" not in a
        first_latency.assert_within_budget()

        # The job page reads the stored result: text + score are both there.
        items = client.get("/api/assessments").json()["items"]
        assert len(items) == 1
        assert items[0]["job"]["job_identity"] == url
        assert items[0]["posting_text"] == a["posting_text"] and items[0]["rank_score"] == a["rank_score"]

        # Re-assess: the Jev cache answers (no second spend), history appends.
        again = client.post("/api/assess", json={"job": {"job_url": url}})
        assert again.status_code == 200, again.text
        assert again.json()["rank_score"]["cached"] is True
        assert again.json()["rank_score"]["score"] == 89
        assert [entry["trigger"] for entry in again.json()["history"]] == ["assess", "reassess"]

        # A pasted resume is never sent to Jev; pasted job text is never stored.
        pasted = client.post(
            "/api/assess",
            json={"job": {"job_text": _POSTING, "title": "Software Engineer"}, "resume": {"resume_text": "Pasted resume: Python services."}},
        )
        assert pasted.status_code == 200, pasted.text
        assert "rank_score" not in pasted.json() and pasted.json()["rank_skip_reason"] == "ephemeral_resume"
        assert "posting_text" not in pasted.json() and _POSTING not in pasted.text

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
