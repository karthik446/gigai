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
5. 0110-032: the documented way to change a line and render a new PDF works as documented (the
   spec's ``use: custom`` + ``text``, then the PDF link; the resume's markdown through ``POST /api/resume/pdf``).
"""

from __future__ import annotations

import json
from pathlib import Path
import time

import httpx
import pytest

from gigai.scout.find_jobs.api import openapi
from gigai.scout.pipeline import busy
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
from tests.support.latency import latency_bound

_URL = "https://boards.greenhouse.io/acme/jobs/101"


def _job(client: httpx.Client, url: str = _URL) -> httpx.Response:
    return client.get("/api/jobs", params={"url": url})


def _workpad_files(workpad: Path) -> set[str]:
    return {str(path.relative_to(workpad)) for path in workpad.rglob("*") if path.is_file() and ".git" not in path.parts}


def _pipeline_job(client: httpx.Client) -> dict[str, object] | None:
    return next((item for item in client.get("/api/pipeline").json()["jobs"] if item["job_identity"] == _URL), None)


def _wait_for_pipeline(client: httpx.Client, *, deadline_seconds: float = 90.0) -> dict[str, object]:
    """Poll ``GET /api/pipeline`` until the job's background pipeline is over (the server's runner does the work)."""

    deadline = time.monotonic() + latency_bound(deadline_seconds)
    seen: dict[str, object] | None = None
    while time.monotonic() < deadline:
        seen = _pipeline_job(client)
        if seen is not None and seen["state"] in ("done", "failed"):
            return seen
        time.sleep(0.2)
    raise AssertionError(f"the job's pipeline never finished: {seen}")


def test_agent_api_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    # 0.1.10.7 PL5 + fix1: the pipeline is ON. Answering the job's question queues its background pipeline, and the
    # agent then tailors and edits that job's resume by hand: the background tailoring must keep it (no 409).
    # The order is made certain, not left to timing: the server's runner yields while an assess batch is live
    # (``busy.assess_batch``, the marker `scout new` leaves), so the background tailoring runs AFTER the agent's.
    monkeypatch.delenv("GIGAI_SCOUT_PIPELINE", raising=False)
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
        # 0110-032: editing a line and rendering markdown are local (no model), and say how.
        line_op, md_op = document["paths"]["/api/tailored-resumes/lines"]["put"], document["paths"]["/api/resume/pdf"]["post"]
        line_props = line_op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert line_props["use"]["enum"] == ["original", "rewritten", "custom"] and "text" in line_props
        assert line_op["x-gigai-external"] == md_op["x-gigai-external"] == "none" and md_op["x-gigai-effect"] == "read"
        assert "application/pdf" in md_op["responses"]["200"]["content"] and "422" in md_op["responses"]
        assert "personal_info_refused" in line_op["description"] and "not sent to a model" in md_op["description"]

        llms = client.get("/llms.txt")
        assert llms.status_code == 200 and llms.headers["content-type"].startswith("text/plain")
        assert "/api/openapi.json" in llms.text and "/api/jobs?url=" in llms.text and "application/json" in llms.text
        assert 'use: "custom", text' in llms.text and "POST /api/resume/pdf {markdown}" in llms.text and "no model call" in llms.text
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
        with busy.assess_batch(home, target):  # the runner yields: the job's background pipeline waits, queued
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
            queued = _pipeline_job(client)
            assert queued is not None and queued["trigger"] == "answer_saved" and queued["steps"]["tailor"] == "ready", queued
        # 0.1.10.7 fix1: now the background pipeline the answer queued runs, after the agent tailored. Its tailor step
        # keeps the agent's resume: same revision, so the edit below (made with the revision the agent holds) gets no 409.
        assert client.post("/api/pipeline/process", json={"job_identity": _URL}).status_code == 202
        finished = _wait_for_pipeline(client)
        assert (finished["state"], finished["error_code"]) == ("done", None), finished
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
        # 0110-032: change one line with the link's body (use custom + text), then the same PDF link prints it;
        # the resume's own markdown renders through POST /api/resume/pdf. No model call in any of it.
        body_lines = [line for section in tailored.json()["result"]["sections"] for line in (section.get("lines") or [bullet for entry in section.get("entries", []) for bullet in entry["bullets"]])]
        wording = "Agent journey wording for one resume line."
        held = {**line_link["body"], "updated_at": tailored.json()["updated_at"]}  # the revision the agent got when it tailored
        edited = client.put(line_link["path"], json={**held, "line_id": body_lines[0]["id"], "use": "custom", "text": wording})
        assert edited.status_code == 200, edited.text
        assert f"- {wording} <!-- edited -->" in edited.json()["markdown"]
        assert client.post(link["path"], json=link["body"]).status_code == 200
        # 0.1.10.7 fix1: the job's pipeline runs again from its tailoring (forced) and the resume is still the
        # agent's: the edited line survives, under the revision the agent holds (its next choice below is not refused).
        forced = client.post("/api/pipeline/process", json={"job_identity": _URL, "force": True})
        assert forced.status_code == 202 and forced.json()["result"] == "enqueued", forced.text
        finished = _wait_for_pipeline(client)
        assert (finished["state"], finished["error_code"]) == ("done", None), finished
        (kept,) = client.get("/api/tailored-resumes", params={"job_identity": _URL}).json()["items"]
        assert f"- {wording} <!-- edited -->" in kept["markdown"] and kept["updated_at"] == tailored.json()["updated_at"]
        from_markdown = client.post("/api/resume/pdf", json={"markdown": edited.json()["markdown"]})
        assert from_markdown.status_code == 200 and from_markdown.headers["content-type"] == "application/pdf" and from_markdown.content.startswith(b"%PDF")
        undone = client.put(line_link["path"], json={**line_link["body"], "line_id": body_lines[0]["id"], "use": "original" if body_lines[0]["kind"] == "copy" else "rewritten"})
        assert undone.status_code == 200 and undone.json()["result"] == tailored.json()["result"]

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
        assert allowed(client.post("/api/answers", json={"question_id": "a:b", "answer": "x", "bogus": 1})) == [
                # 0.1.10.7 C: an answer is the user's (no profile_id); it may carry a tag and the revision read.
                "actor", "answer", "from_bank", "question", "question_id", "reassess", "revision", "source", "tag",
            ]
        assert allowed(client.post("/api/sources/update", json={"bogus": 1})) == ["force", "full_refresh"]
        # 0110-025: the sources status carries the documented `background` block. A journey server
        # runs with the background refresh off (the harness says so), and the block says that.
        documented = next(r for r in openapi.ROUTES if r.key == ("GET", "/api/sources/update")).example
        sources_status = client.get("/api/sources/update").json()
        assert set(sources_status) == set(documented)
        background = sources_status["background"]
        assert set(background) == set(documented["background"])
        assert {name: set(background[name]) for name in ("auto_refresh", "tags", "text")} == {name: set(documented["background"][name]) for name in ("auto_refresh", "tags", "text")}
        assert background["auto_refresh"] == {"enabled": False, "source": "environment", "active": True}
        assert (background["state"], background["next_tick_at"]) == ("disabled", None)
        # 0110-026: and the documented `snapshot` block. Reading it asks nobody: nothing was imported here.
        snapshot_block = sources_status["snapshot"]
        assert set(snapshot_block) == set(documented["snapshot"])
        assert (snapshot_block["as_of"], snapshot_block["last_attempt_at"], snapshot_block["counts"]) == (None, None, None)
        # 0110-026f: and the documented `tags`, `text_index` and `refresh` blocks, key for key.
        for block in ("tags", "text_index", "refresh"):
            assert set(sources_status[block]) == set(documented[block]), block
        tags_block, documented_queue = sources_status["tags"], documented["tags"]["queue"]
        assert set(tags_block["setting"]) == set(documented["tags"]["setting"]) and set(tags_block["models"]) == set(documented["tags"]["models"])
        assert set(tags_block["queue"]) == set(documented_queue)
        assert {lane: set(tags_block["queue"][lane]) for lane in ("demand", "backfill")} == {lane: set(documented_queue[lane]) for lane in ("demand", "backfill")}
        assert (sources_status["refresh"]["enabled"], sources_status["refresh"]["state"], sources_status["refresh"]["next_tick_at"]) == (False, "disabled", None)
        # 0110-026f: the background settings, documented key for key; a bad body names what is allowed.
        settings_documented = next(r for r in openapi.ROUTES if r.key == ("GET", "/api/settings/background")).example
        settings_body = client.get("/api/settings/background").json()
        assert set(settings_body) == set(settings_documented)
        for view in ("settings", "effective"):
            assert {name: set(settings_body[view][name]) for name in settings_body[view]} == {name: set(settings_documented[view][name]) for name in settings_documented[view]}, view
        assert allowed(client.put("/api/settings/background", json={"bogus": 1})) == ["pipeline", "rank", "snapshot", "sources", "tagging"]
        nested_setting = client.put("/api/settings/background", json={"tagging": {"bogus": 1}})
        assert nested_setting.status_code == 422 and nested_setting.json()["error"]["allowed_keys"] == ["backfill_enabled", "model_enabled", "tag_backfill_model"]
        assert allowed(client.post("/api/applications", json={"bogus": 1})) == ["event_kind", "job_identity", "normalized_url", "notes", "occurred_at"]
        assert allowed(client.put("/api/resume-display", json={"bogus": 1})) == ["auto_fit", "contact", "name", "spacing_scale", "titles"]
        assert allowed(client.post("/api/resumes", json={"bogus": 1})) == ["content_base64", "file_name", "text"]
        assert allowed(client.post("/api/resume/pdf", json={"markdown": "## Summary\n- x\n", "bogus": 1})) == ["auto_fit", "header", "markdown", "profile_id", "spacing_scale"]
        profiles = client.post("/api/profiles", json={"label": "x", "titles": ["a"], "bogus": 1})
        assert "bogus" in profiles.json()["error"]["field_errors"]["_"] and "allowed: " in profiles.json()["error"]["field_errors"]["_"]
        assert "label" in allowed(profiles)
        top = client.post("/api/assess", json={"job": {"job_url": _URL}, "bogus": 1})
        assert top.status_code == 422 and "job" in top.json()["error"]["allowed_keys"] and "model_target" in top.json()["error"]["allowed_keys"]
        nested = client.post("/api/assess", json={"job": {"job_url": _URL, "bogus": 1}})
        assert nested.status_code == 422 and nested.json()["error"]["allowed_keys"] == ["company", "job_text", "job_url", "title"], "a nested-object error lists that object's own keys, not the route's"

        assert client.get("/api/health").json() == {"status": "ok"}, "existing response shapes are unchanged"
        assert client.get("/api/nope").json() == {"error": {"code": "not_found", "message": "no such route"}}

        assert_clean_and_healthy(workpad, home)
    finally:
        stop_server(server)
