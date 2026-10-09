"""Q3: the tailored-resume journey over HTTP against the real supervised server.

(a) job_url (single-job fixture) + selected profile -> 200 with the
structured output: no header (tailored output is headerless, 0110-003 P1),
a skills copy line that is the resume line verbatim, every rewritten line
carrying refs with the cited source text, non-empty
``markdown`` and a sibling ``.md`` on disk; (b) ``POST /api/answers
cloud:gcp`` then tailor again -> a line cites ``answer cloud:gcp`` and
``created_at`` survives; (c) the fabricate marker -> 502
``model_output_invalid`` naming the line, nothing stored; (d) ``GET
/api/tailored-resumes`` with the ``profile_id`` / ``job_identity`` filters;
(e) ``{"job": {}}`` -> 422; (f) CSRF 403 / 415; (g) the posting text is
never echoed (pasted job + ephemeral resume, stored under ``ephemeral/``);
(h) 0110-006: the fixture model's lossy marker returns the operator's
weaker Staff and DSAR rewrites and the stored result and the markdown show
the ORIGINAL lines, the rejected rewrites kept with what they dropped.
A second test: the fake model sleeps past a 1 s deadline -> 504
``tailor_timeout``.  Fixtures: ``bindings._test_provider_handler`` (HTTP)
and ``bindings._test_model_handler`` (model) through the two existing seams
-- no live network or model call.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.private_records import list_imports
from gigai.scout.find_jobs.bindings import (
    TEST_MODEL_FABRICATE_MARKER,
    TEST_MODEL_LOSSY_MARKER,
    TEST_MODEL_LOSSY_REWRITES,
    TEST_MODEL_SLEEP_MARKER,
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

_RESUME_LINE = "Software engineer with Python service experience."  # harness.add_resume's one line
_POSTING = (
    "Acme is hiring a Software Engineer to build reliable Python services on Kubernetes and Terraform. "
    "Requirements: Python in production; Kubernetes; Terraform; GCP experience is a plus. Remote within the US."
)

_STAFF = "- Managed a group of 4 analysts supporting scheduling and billing systems end to end, owning the release calendar, vendor contact, and staff training across 2 hospitals."
_DSAR = "- Built the medication reconciliation (MRX) workflow handling admission and discharge lists end to end under HIPAA and OH state requirements."
_EKS = "- Owns the shared Kubernetes cluster (AKS) for 12 billing applications."
_LOSSY_RESUME = f"## Experience\n**Clinical Applications Manager — Example Corp** (2020–present)\n{_STAFF}\n{_DSAR}\n{_EKS}\n"
_LOSSY_POSTING = "Acme is hiring a Staff Engineer to lead scheduling and billing systems and medication reconciliation. Requirements: Python; Kubernetes."


def _assert_error(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def _gig_id(home: Path, target: Path) -> str:
    from gigai.workpad import resolve_workpad

    return resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True).gig_id


def _assert_structured(payload: dict) -> None:
    """The structured-output and copy-line checks every 200 must satisfy."""

    assert payload["schema_version"] == "scout-tailor-response:1"
    assert "text" not in payload["job"]
    result = payload["result"]
    assert result["schema_version"] == "scout-tailored-resume:1"
    assert result["header"] == [], "tailored output is headerless (0110-003 P1)"
    copies = [line for section in result["sections"] for line in section.get("lines", []) if line["kind"] == "copy"]
    assert copies, "a copy line is expected"
    for line in copies:
        assert line["refs"][0]["kind"] == "resume"
        assert line["text"] == line["refs"][0]["text"], "a copy line is the resume line verbatim"
    headings = [section["heading"] for section in result["sections"]]
    assert headings and len(set(headings)) == len(headings)
    for section in result["sections"]:
        lines = section.get("lines", []) + [
            item for entry in section.get("entries", []) for item in entry["heading"] + entry["bullets"]
        ]
        for line in lines:
            assert line["kind"] in {"copy", "rewritten"}
            assert line["refs"], "every line keeps its refs"
            for ref in line["refs"]:
                assert ref["kind"] in {"resume", "answer"} and isinstance(ref["text"], str) and ref["text"]
            if line["kind"] == "copy":
                assert line["text"] == line["refs"][0]["text"]
        for entry in section.get("entries", []):
            assert all(item["kind"] == "copy" for item in entry["heading"])
    assert isinstance(payload["markdown"], str) and payload["markdown"].strip()
    assert payload["markdown_path"].endswith(".md")
    assert payload["sources"]["resume_line_count"] >= 1


def test_tailored_resumes_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        gig_id = _gig_id(home, target)
        imports_before = list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id)

        # (a) job_url -> Greenhouse single-job fixture + the selected profile.
        first, first_latency = timed_request(
            "POST /api/tailored-resumes (job_url)",
            lambda: client.post("/api/tailored-resumes", json={"job": {"job_url": "https://boards.greenhouse.io/acme/jobs/101"}}),
        )
        assert first.status_code == 200, first.text
        a = first.json()
        _assert_structured(a)
        assert a["job"]["fetch_kind"] == "ats_single" and a["job"]["title"] == "Software Engineer" and a["job"]["company"] == "Acme"
        assert a["resume"]["profile_id"], "the selected profile's resume is the default"
        skills = next(section for section in a["result"]["sections"] if section["heading"] == "skills")
        assert skills["lines"][0]["text"] == _RESUME_LINE
        assert skills["lines"][0]["refs"] == [{"kind": "resume", "line": 1, "text": _RESUME_LINE}]
        summary = a["result"]["sections"][0]
        assert summary["heading"] == "summary" and summary["lines"][0]["kind"] == "rewritten"
        assert summary["lines"][0]["refs"] == [{"kind": "resume", "line": 1, "text": _RESUME_LINE}]
        assert a["sources"] == {"resume_content_sha256": a["resume"]["content_sha256"], "resume_line_count": 1, "answers": [], "assessment_stored_path": None}
        assert a["markdown"].startswith(f"## Summary\n\n- {_RESUME_LINE} <!-- R1 -->\n")
        assert a["producer"]["model_target"] == "ollama_local" and a["producer"]["callable"] == "scout.tailor"
        assert a["created_at"] == a["updated_at"]
        stored = Path(a["stored_path"])
        markdown = Path(a["markdown_path"])
        assert stored.is_file() and markdown.is_file() and markdown == stored.with_suffix(".md")
        # 0.1.11.9: the job's own folder; the role that asked is recorded inside.
        assert stored.parent.name == "job" and stored.parent.parent.name == "resumes" and a["resume"]["profile_id"]
        assert markdown.read_text(encoding="utf-8") == a["markdown"]
        first_latency.assert_within_budget()

        # (b) answer cloud:gcp, tailor again -> a line cites the answer; created_at kept.
        answered = client.post("/api/answers", json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": None})
        assert answered.status_code == 201, answered.text
        second, second_latency = timed_request(
            "POST /api/tailored-resumes (with answer)",
            lambda: client.post("/api/tailored-resumes", json={"job": {"job_url": "https://boards.greenhouse.io/acme/jobs/101"}}),
        )
        assert second.status_code == 200, second.text
        b = second.json()
        _assert_structured(b)
        assert b["stored_path"] == a["stored_path"] and b["created_at"] == a["created_at"] and b["updated_at"] >= a["updated_at"]
        assert [item["question_id"] for item in b["sources"]["answers"]] == ["cloud:gcp"]
        assert b["sources"]["answers"][0]["revision_id"].startswith("revision_")
        cited = [ref for section in b["result"]["sections"] for line in section.get("lines", []) for ref in line["refs"]]
        answer_refs = [ref for ref in cited if ref["kind"] == "answer"]
        assert answer_refs == [{"kind": "answer", "question_id": "cloud:gcp", "text": "cloud gcp Yes, two years on GCP."}]
        assert "<!-- A cloud:gcp -->" in b["markdown"]
        second_latency.assert_within_budget()

        # (c) planted fabrications on both attempts -> 502 naming the line; nothing new stored.
        listing_before = client.get("/api/tailored-resumes").json()["items"]
        fabricated = client.post(
            "/api/tailored-resumes",
            json={"job": {"job_text": _POSTING + " " + TEST_MODEL_FABRICATE_MARKER, "title": "Software Engineer", "company": "Acme"}},
        )
        _assert_error(fabricated, status=502, code="model_output_invalid")
        assert 'summary line 1 contains the number "8" that appears in none of its cited sources (R1)' in fabricated.json()["error"]["message"]
        assert client.get("/api/tailored-resumes").json()["items"] == listing_before
        assert TEST_MODEL_FABRICATE_MARKER not in fabricated.text

        # (g) pasted job + ephemeral resume: 200, stored under ephemeral/, no posting text echoed, no new record.
        pasted, pasted_latency = timed_request(
            "POST /api/tailored-resumes (job_text+resume_text)",
            lambda: client.post(
                "/api/tailored-resumes",
                json={"job": {"job_text": _POSTING}, "resume": {"resume_text": "Pasted resume: Python services for six years.\nRan k8s."}},
            ),
        )
        assert pasted.status_code == 200, pasted.text
        g = pasted.json()
        _assert_structured(g)
        assert g["job"]["fetch_kind"] == "pasted" and g["resume"]["profile_id"] is None
        assert Path(g["stored_path"]).parent.name == "ephemeral"
        assert _POSTING not in pasted.text and "Kubernetes and Terraform" not in pasted.text
        assert "Pasted resume: Python services for six years." in pasted.text  # resume-derived text is the product
        imports_after = list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id)
        assert imports_after == imports_before
        pasted_latency.assert_within_budget()

        # (d) the list: newest first, never job text; profile_id and job_identity filters.
        listing, listing_latency = timed_request("GET /api/tailored-resumes", lambda: client.get("/api/tailored-resumes"))
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["schema_version"] == "scout-tailored-resumes-response:1"
        items = body["items"]
        assert [item["stored_path"] for item in items] == [g["stored_path"], b["stored_path"]]
        assert all("text" not in item["job"] for item in items)
        by_profile = client.get("/api/tailored-resumes", params={"profile_id": a["resume"]["profile_id"]}).json()["items"]
        assert [item["stored_path"] for item in by_profile] == [b["stored_path"]]
        ephemeral = client.get("/api/tailored-resumes", params={"profile_id": "ephemeral"}).json()["items"]
        assert [item["stored_path"] for item in ephemeral] == [g["stored_path"]]
        by_job = client.get("/api/tailored-resumes", params={"job_identity": a["job"]["job_identity"]}).json()["items"]
        assert [item["stored_path"] for item in by_job] == [b["stored_path"]]
        _assert_error(client.get("/api/tailored-resumes", params={"profile_id": "../x"}), status=422, code="invalid_value")
        listing_latency.assert_within_budget()

        # (e) neither job input -> 422.
        _assert_error(client.post("/api/tailored-resumes", json={"job": {}}), status=422, code="job_input_invalid")
        _assert_error(client.post("/api/tailored-resumes", json={"job": {"job_text": _POSTING}, "preferences": {}}), status=422, code="unknown_key")

        # (f) CSRF: wrong Origin -> 403, wrong Content-Type -> 415.
        _assert_error(
            httpx.post(f"{server.base_url}/api/tailored-resumes", json={"job": {"job_text": _POSTING}}, headers={"Origin": "http://evil.example.test"}),
            status=403, code="forbidden_origin",
        )
        _assert_error(
            httpx.post(f"{server.base_url}/api/tailored-resumes", content=b"{}", headers={"Content-Type": "text/plain"}),
            status=415, code="unsupported_media_type",
        )

        # (h) 0110-006: weaker rewrites fall back to the original lines, per line, with no retry.
        lossy, lossy_latency = timed_request(
            "POST /api/tailored-resumes (lossy rewrites)",
            lambda: client.post(
                "/api/tailored-resumes",
                json={
                    "job": {"job_text": _LOSSY_POSTING + " " + TEST_MODEL_LOSSY_MARKER, "title": "Staff Engineer", "company": "Acme"},
                    "resume": {"resume_text": _LOSSY_RESUME},
                },
            ),
        )
        assert lossy.status_code == 200, lossy.text
        h = lossy.json()
        _assert_structured(h)
        experience = next(section for section in h["result"]["sections"] if section["heading"] == "experience")
        bullets = experience["entries"][0]["bullets"]
        # The stored result shows the originals; the third bullet is the fixture's own copy line.
        assert [bullet["text"] for bullet in bullets] == [_STAFF, _DSAR, _EKS]
        assert [bullet["origin"] for bullet in bullets] == ["fallback", "fallback", "model"]
        assert [bullet["id"] for bullet in bullets] == ["L3", "L4", "L5"]  # L1 summary, L2 the role heading
        staff, dsar = bullets[0]["alternative"], bullets[1]["alternative"]
        assert staff["text"] == TEST_MODEL_LOSSY_REWRITES[_STAFF[2:].rstrip(".")] and staff["lost"] == {"ownership": ["own"], "scope": ["end to end"]}
        assert dsar["text"] == TEST_MODEL_LOSSY_REWRITES[_DSAR[2:].rstrip(".")] and dsar["lost"] == {"entities": ["mrx", "oh"], "scope": ["end to end"]}
        assert staff["reason"] == {"kind": "surface", "requirement": None, "posting_phrase": "scheduling"}
        # The markdown (response and file) prints the originals once, never the weaker text.
        assert f"- {_STAFF[2:]} <!-- R3 -->\n" in h["markdown"] and f"- {_DSAR[2:]} <!-- R4 -->\n" in h["markdown"]
        assert staff["text"] not in h["markdown"] and dsar["text"] not in h["markdown"] and "- - " not in h["markdown"]
        assert Path(h["markdown_path"]).read_text(encoding="utf-8") == h["markdown"]
        assert TEST_MODEL_LOSSY_MARKER not in lossy.text and _LOSSY_POSTING not in lossy.text
        lossy_latency.assert_within_budget()

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_tailor_timeout_is_a_typed_504(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake model sleeps past a 1 s deadline -> 504 ``tailor_timeout`` (own server, as the assess journey)."""

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    monkeypatch.setenv("GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS", "1")
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        slow, slow_latency = timed_request(
            "POST /api/tailored-resumes (timeout)",
            lambda: client.post("/api/tailored-resumes", json={"job": {"job_text": _POSTING + " " + TEST_MODEL_SLEEP_MARKER}}),
        )
        _assert_error(slow, status=504, code="tailor_timeout")
        slow_latency.assert_within_budget()
        assert client.get("/api/tailored-resumes").json()["items"] == []

        fine = client.post("/api/tailored-resumes", json={"job": {"job_text": _POSTING}})
        assert fine.status_code == 200, fine.text
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
