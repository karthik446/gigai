"""0110-007: an agent finds its way around the API cold, over HTTP against the real supervised server.

1. ``GET /api`` (and ``/api/``) lists every route of the table; ``GET /api/openapi.json`` is the
   generated OpenAPI 3.1 document and validates; ``GET /llms.txt`` is plain text.
2. ``GET /api/jobs?url=`` for a job only a quick assessment knows (no run yet), then after a run,
   after answering the open question, after tailoring, after applying: one aggregate each time
   (posting, runs, assessments + matrix, open questions, tailored ids + links, job_state + the
   events it accepts next, action links); read only.
3. typed errors: 404 for an unknown job, 422 for no/blank/bad ``url`` and unknown query keys
   (which name the allowed ones), 403 for a foreign Host.
4. ``unknown_key`` 422s from the other routes list the allowed keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.api import openapi
from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    poll_until_terminal,
    resolve_workpad_path,
    run_request_body,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_URL = "https://boards.greenhouse.io/acme/jobs/101"


def _job(client: httpx.Client, url: str = _URL) -> httpx.Response:
    return client.get("/api/jobs", params={"url": url})


def _workpad_files(workpad: Path) -> set[str]:
    return {str(path.relative_to(workpad)) for path in workpad.rglob("*") if path.is_file() and ".git" not in path.parts}


def test_agent_api_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # ---- 1. discovery ------------------------------------------------------------
        for path in ("/api", "/api/"):
            index = client.get(path)
            assert index.status_code == 200 and index.headers["content-type"] == "application/json", path
            body = index.json()
            assert body["schema_version"] == "scout-api-index:1" and body["openapi"] == "/api/openapi.json" and body["llms"] == "/llms.txt"
            listed = {(row["method"], row["path"]) for row in body["routes"]}
            assert listed == {route.key for route in openapi.ROUTES}
            for row in body["routes"]:
                assert row["summary"] and row["effect"] in ("read", "write") and row["external"] in ("none", "model", "network")
        print("GET /api ->", json.dumps({**body, "routes": body["routes"][:2] + ["... %d more" % (len(body["routes"]) - 2)]}))

        spec = client.get("/api/openapi.json")
        assert spec.status_code == 200
        document = spec.json()
        assert openapi.validate_document(document) == []
        assert document["openapi"] == "3.1.0" and set(document["paths"]) == {route.path for route in openapi.ROUTES}
        job_op = document["paths"]["/api/jobs"]["get"]
        assert job_op["x-gigai-effect"] == "read" and job_op["x-gigai-external"] == "none"
        assert [p["name"] for p in job_op["parameters"]] == ["url"] and "#/jobs/" in job_op["description"]
        assert document["paths"]["/api/assess"]["post"]["x-gigai-external"] == "model"

        llms = client.get("/llms.txt")
        assert llms.status_code == 200 and llms.headers["content-type"].startswith("text/plain")
        assert "/api/openapi.json" in llms.text and "/api/jobs?url=" in llms.text and "application/json" in llms.text
        assert client.get("/llms.txt", headers={"Host": "evil.example"}).status_code == 403

        # ---- 2. one job ----------------------------------------------------------------
        assert _job(client).status_code == 404 and _job(client).json()["error"]["code"] == "not_found"

        assessed = client.post("/api/assess", json={"job": {"job_url": _URL}})
        assert assessed.status_code == 200, assessed.text
        workpad = resolve_workpad_path(home, target)
        before = _workpad_files(workpad)
        quick_only = _job(client)
        assert quick_only.status_code == 200, quick_only.text
        job = quick_only.json()
        assert job["schema_version"] == "scout-job-response:1" and job["job_identity"] == _URL
        assert job["runs"] == [] and job["posting"]["title"] == "Software Engineer" and job["posting"]["text"] == "Build reliable Python services."
        assert [item["source"] for item in job["assessments"]] == ["quick"]
        assert job["assessments"][0]["verdict"] == "pending_user_answers" and "matrix" in job["assessments"][0]
        assert [q["question_id"] for q in job["open_questions"]] == ["cloud:gcp"]
        assert job["tailored_resumes"] == [] and job["links"]["pdf"] is None
        assert job["job_state"]["state"] == "needs_answers" and job["job_state"]["next_events"] == ["applied"]
        assert job["links"]["assess"] == {"method": "POST", "path": "/api/assess", "body": {"job": {"job_url": _URL}}}
        assert job["links"]["mark_applied"]["body"] == {"normalized_url": _URL, "event_kind": "applied"}
        assert _workpad_files(workpad) == before, "GET /api/jobs must not write"

        config_digest = client.get("/api/config").json()["config_digest"]
        started = client.post("/api/run", json=run_request_body(config_digest))
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        assert poll_until_terminal(client, run_id)["status"] == "succeeded"

        # raw and tracking-parameter URLs land on the same job
        after_run = _job(client, _URL + "?utm_source=agent")
        assert after_run.status_code == 200, after_run.text
        job = after_run.json()
        assert job["job_identity"] == _URL and [run["run_id"] for run in job["runs"]] == [run_id]
        assert job["posting"]["normalized_url"] == _URL and job["posting"]["text"]
        assert {item["source"] for item in job["assessments"]} >= {"run", "quick"}
        run_entry = next(item for item in job["assessments"] if item["source"] == "run")
        assert run_entry["run_id"] == run_id and run_entry["profile_id"] == next(a for a in job["assessments"] if a["source"] == "quick")["profile_id"] and "posting" not in run_entry and run_entry["verdict"] == "pending_user_answers"
        assert job["links"]["run_posting"]["path"] == f"/api/runs/{run_id}/posting?url=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F101"
        assert "rank" in job and "work_mode_fit" in job
        ids = [q["question_id"] for q in job["open_questions"]]
        assert ids == ["cloud:gcp"], "the run's and the quick assessment's identical question is listed once"
        print("JOBJSON", json.dumps({**job, "posting": {**job["posting"], "text": job["posting"]["text"][:40] + "..."}}))
        print("GET /api/jobs?url= keys ->", sorted(job), "| assessments:", [(a["source"], a.get("verdict")) for a in job["assessments"]], "| state:", job["job_state"])

        # answering closes the question; the answer is listed with it
        answered = client.post("/api/answers", json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": {"job_identity": _URL}})
        assert answered.status_code == 201, answered.text
        job = _job(client).json()
        assert job["open_questions"] == []
        assert [a["answer"] for a in job["answers"]] == ["Yes, two years on GCP."]
        assert next(a for a in job["assessments"] if a["source"] == "quick")["verdict"] == "matched_above_threshold"
        assert job["job_state"]["state"] == "matched"

        # tailoring adds the resume with its links; the PDF link works as given
        tailored = client.post("/api/tailored-resumes", json={"job": {"job_url": _URL}})
        assert tailored.status_code == 200, tailored.text
        job = _job(client).json()
        assert len(job["tailored_resumes"]) == 1 and job["job_state"]["state"] == "tailored"
        link = job["links"]["pdf"]
        assert link == job["tailored_resumes"][0]["links"]["pdf"] and link["method"] == "POST"
        pdf = client.post(link["path"], json=link["body"])
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
        assert "markdown" not in job["tailored_resumes"][0], "ids and links, not the resume text"
        line_link = job["tailored_resumes"][0]["links"]["line"]  # 0110-006: the per-line choice, beside pdf
        assert line_link["method"] == "PUT" and line_link["path"] == "/api/tailored-resumes/lines"
        assert set(line_link["body"]) == {"profile_id", "job_identity", "updated_at", "line_id", "use"}
        assert line_link["body"]["updated_at"] == tailored.json()["updated_at"]

        # the mark-applied link works as given, and the job then accepts the pipeline events
        mark = job["links"]["mark_applied"]
        applied = client.post(mark["path"], json=mark["body"])
        assert applied.status_code == 201, applied.text
        job = _job(client).json()
        assert job["job_state"]["state"] == "applied" and "interview_scheduled" in job["job_state"]["next_events"]
        assert job["links"]["mark_applied"] is None
        assert [event["event_kind"] for event in job["application_events"]] == ["applied"]

        # ---- 3. typed errors -----------------------------------------------------------
        for bad, code in ((client.get("/api/jobs"), "invalid_value"), (client.get("/api/jobs?url="), "invalid_value"), (_job(client, "not a url"), "invalid_value")):
            assert bad.status_code == 422 and bad.json()["error"]["code"] == code, bad.text
        unknown = client.get("/api/jobs", params={"posting_url": _URL})
        assert unknown.status_code == 422
        error = unknown.json()["error"]
        assert error["code"] == "unknown_key" and error["allowed_keys"] == ["url"] and "allowed: url" in error["message"]
        assert _job(client, "https://boards.greenhouse.io/acme/jobs/999").status_code == 404
        assert client.get("/api/jobs", params={"url": _URL}, headers={"Host": "evil.example"}).status_code == 403

        # ---- 4. unknown_key everywhere names the allowed keys ----------------------------
        def allowed(response: httpx.Response) -> list[str]:
            assert response.status_code in (400, 422), response.text
            return response.json()["error"]["allowed_keys"]

        assert allowed(client.get(f"/api/runs/{run_id}/results?bogus=1")) == ["limit", "offset"]
        assert allowed(client.get(f"/api/runs/{run_id}/posting?bogus=1")) == ["url"]
        assert allowed(client.get(f"/api/runs/{run_id}/progress?bogus=1")) == ["summary"]
        assert allowed(client.get("/api/watchlist?bogus=1")) == ["limit", "offset", "summary"]
        assert allowed(client.post("/api/watchlist", json={"url": "x", "bogus": 1})) == ["url"]
        assert allowed(client.post("/api/answers", json={"question_id": "a:b", "answer": "x", "bogus": 1})) == ["answer", "question_id", "reassess"]
        assert allowed(client.post("/api/sources/update", json={"bogus": 1})) == ["force"]
        assert allowed(client.post("/api/applications", json={"bogus": 1})) == ["event_kind", "job_identity", "normalized_url", "notes", "occurred_at"]
        assert allowed(client.put("/api/resume-display", json={"bogus": 1})) == ["contact", "name", "titles"]
        assert allowed(client.post("/api/resumes", json={"bogus": 1})) == ["content_base64", "file_name", "text"]
        profiles = client.post("/api/profiles", json={"label": "x", "titles": ["a"], "bogus": 1})
        assert "bogus" in profiles.json()["error"]["field_errors"]["_"] and "allowed: " in profiles.json()["error"]["field_errors"]["_"]
        assert "label" in allowed(profiles)
        top = client.post("/api/assess", json={"job": {"job_url": _URL}, "bogus": 1})
        assert top.status_code == 422 and "job" in top.json()["error"]["allowed_keys"] and "model_target" in top.json()["error"]["allowed_keys"]
        nested = client.post("/api/assess", json={"job": {"job_url": _URL, "bogus": 1}})
        assert nested.status_code == 422 and "allowed_keys" not in nested.json()["error"], "a nested-object error keeps its own message"

        assert client.get("/api/health").json() == {"status": "ok"}, "existing response shapes are unchanged"
        assert client.get("/api/nope").json() == {"error": {"code": "not_found", "message": "no such route"}}

        assert_clean_and_healthy(workpad, home)
    finally:
        stop_server(server)
