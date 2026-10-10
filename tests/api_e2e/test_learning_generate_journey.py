"""0.1.11.10 Part B, packet G5: generating a course over HTTP against the real supervised server.

``POST /api/learning/pathways`` (estimate first, ``approve`` starts the job), ``GET /api/learning/pathways`` and
``.../{pathway_id}`` while it runs, ``POST .../cancel`` and ``POST .../resume``.

The server is another process, so the fakes reach it the way the assess journeys' do: through the environment,
double-gated. With BOTH of ``bindings.py``'s seams on (a server only starts with them when it was started with
``--allow-test-seams``), ``learning_job`` reads the script file ``GIGAI_SCOUT_LEARNING_TEST_SCRIPT`` names: model
answers by what the prompt holds, and a table of pages. No model is called, no name is resolved, no request leaves.
The postings are SYNTHETIC boards seeded into the home's board cache; the corpus step, the course steps, the
renderer, the gates and the store are the real ones. The practice-paths step is a stand-in that writes none.

1. Without ``approve`` the answer is the estimate and nothing is stored or started; a bad body is 422.
2. ``approve`` stores the request and answers 202; a second approval is 409 ``learning_running`` while it runs.
3. Polling shows the job running with its progress, then ``done``: the course is listed with what it took.
4. The course is served under ``/learning/<id>/`` with the exact Content-Security-Policy.
5. ``cancel`` stops a second job (``failed`` / ``cancelled``, what was written kept); ``resume`` finishes it.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time

import httpx
import pytest

from gigai.scout import learning_job, learning_store
from gigai.scout.find_jobs import search_index

from tests.api_e2e.harness import setup_and_init, start_server, stop_server
from tests.behaviors.scout_learning.test_learning_course import lesson_answer, lesson_id
from tests.behaviors.scout_learning.test_learning_generate_cli import corpus_answers, course_answer, seed_boards
from tests.support.latency import latency_bound

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

ROLE = "MLOps engineer"
PATHWAYS = "/api/learning/pathways"
STEPS = ["corpus", "course", "paths", "render", "audit", "import"]
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; "
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox"
)
ROW_KEYS = {"id", "role_text", "requested_at", "status", "cost", "course", "url", "source", "imported_from", "error", "error_code", "progress"}


def write_script(path: Path, *, delay_seconds: float) -> dict[str, object]:
    """The script the server's job reads: an answer per kind of prompt, and every candidate page of the curriculum."""

    titles, vocabulary, follow_up = corpus_answers()
    course = course_answer()
    rules: list[dict[str, object]] = [
        {
            "when": ["You are writing one MODULE", f"LESSON id={lesson_id(module, 1)} "],
            "answer": {"module_intro": "Two sentences. About the module.", "lessons": [lesson_answer(lesson_id(module, n)) for n in (1, 2, 3, 4)]},
        }
        for module in range(1, 8)
    ]
    rules += [
        {"when": ["FOLLOW-UP:"], "answer": follow_up},
        {"when": ["You are naming what a set of real job postings"], "answer": vocabulary},
        {"when": ["You are planning a COURSE"], "answer": course},
        {"when": ["You are turning one job role"], "answer": titles},
    ]
    pages = {
        source["url"]: f'<!doctype html><html><head><title>{lesson["title"]} page</title></head><body><h1 id="top">{lesson["title"]}</h1>'
        f'<h2 id="setup">Setup</h2><p>{"A sentence about this lesson. " * 20}</p></body></html>'
        for module in course["modules"] for lesson in module["lessons"] for source in lesson["sources"]  # type: ignore[union-attr]
    }
    script = {"delay_seconds": delay_seconds, "answers": rules, "pages": pages}
    path.write_text(json.dumps(script), encoding="utf-8")
    return script


def _error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status and response.json()["error"]["code"] == code, response.text


def _poll(client: httpx.Client, pathway_id: str, until: tuple[str, ...]) -> list[dict[str, object]]:
    """Every answer of ``GET .../{pathway_id}`` until its status is one of ``until`` (the last one is that answer)."""

    seen: list[dict[str, object]] = []
    deadline = time.monotonic() + latency_bound(120.0)
    while time.monotonic() < deadline:
        answer = client.get(f"{PATHWAYS}/{pathway_id}")
        assert answer.status_code == 200, answer.text
        seen.append(answer.json()["pathway"])
        if seen[-1]["status"] in until:
            return seen
        time.sleep(0.05)
    raise AssertionError(f"the job did not reach {until}: {seen[-1]}")


def test_a_course_is_generated_only_on_approval_shows_its_progress_and_is_served_locked_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    seed_boards(home)
    search_index.close(home)
    script = write_script(tmp_path / "learning-script.json", delay_seconds=0.15)
    monkeypatch.setenv("GIGAI_SCOUT_LEARNING_TEST_SCRIPT", str(tmp_path / "learning-script.json"))
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # ---- 1. the estimate first: nothing is stored, nothing starts ------------------------------------------
        asked = client.post(PATHWAYS, json={"role_text": f"  {ROLE} "})
        assert asked.status_code == 200 and asked.headers["x-gigai-labels"] == "user-private", asked.text
        assert asked.json() == {
            "schema_version": "scout-learning-generate:1", "status": "ask", "role_text": ROLE,
            "estimate": {"calls": 60, "fetches": 1000, "minutes": 45, "tokens": None, "basis": "none"},
            "caps": {"calls": 120, "fetches": 1500}, "warning": "learning_higher_usage",
        }
        assert client.post(PATHWAYS, json={"role_text": ROLE, "approve": False}).json()["status"] == "ask"
        _error(client.post(PATHWAYS, json={"role_text": ROLE, "yes": True}), 422, "unknown_key")
        _error(client.post(PATHWAYS, json={"role_text": ROLE, "approve": "yes"}), 422, "wrong_type")
        _error(client.post(PATHWAYS, json={"approve": True}), 422, "wrong_type")
        _error(client.post(PATHWAYS, json=[ROLE]), 422, "wrong_type")
        _error(client.post(PATHWAYS, json={"role_text": "   ", "approve": True}), 422, "invalid_value")
        _error(client.post(PATHWAYS, json={"role_text": "write to someone@example.com", "approve": True}), 422, "personal_info_refused")
        assert client.get(PATHWAYS).json() == {"schema_version": "scout-learning-pathways-response:1", "pathways": [], "total": 0}
        assert learning_store.list_pathways(home, target) == [] and not (learning_store.learning_dir(home, target) / "work").exists()

        # ---- 2. approval starts the job; one course at a time ---------------------------------------------------
        started = client.post(PATHWAYS, json={"role_text": ROLE, "approve": True})
        assert started.status_code == 202, started.text
        first = started.json()
        assert first["schema_version"] == "scout-learning-pathway-response:1"
        pathway_id = first["pathway"]["id"]
        assert first["pathway"]["status"] in ("queued", "running") and first["pathway"]["role_text"] == ROLE
        assert (first["pathway"]["source"], first["pathway"]["course"], first["pathway"]["url"]) == ("requested", None, None)
        _error(client.post(PATHWAYS, json={"role_text": "Platform engineer", "approve": True}), 409, "learning_running")
        _error(client.post(f"{PATHWAYS}/{pathway_id}/resume", json={}), 409, "learning_running")
        assert client.get(f"/learning/{pathway_id}/index.html").status_code == 404, "no course is served before it is done"

        # ---- 3. polling: running with its progress, then done ---------------------------------------------------
        seen = _poll(client, pathway_id, ("done", "failed"))
        done = seen[-1]
        assert done["status"] == "done", done
        running = [item for item in seen if item["status"] == "running"]
        assert running, "the job was seen running"
        assert all(item["progress"]["step"] in STEPS and [step["id"] for step in item["progress"]["steps"]] == STEPS for item in running)  # type: ignore[index]
        assert all(item["course"] is None and item["url"] is None for item in running)
        calls = [item["progress"]["calls"] for item in seen if item["progress"]]  # type: ignore[index]
        assert calls == sorted(calls) and calls[-1] == 11, "the calls only go up: 3 for the corpus, the curriculum, 7 modules"
        details = {step["detail"] for item in seen if item["progress"] for step in item["progress"]["steps"] if step["detail"]}  # type: ignore[index, union-attr]
        assert any(str(detail).startswith("Writing module ") for detail in details), details
        # every plain line the job said is in its progress file (a poll only sees some of them)
        lines = [json.loads(line)["text"] for line in (learning_job.work_dir(home, target, pathway_id) / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(re.fullmatch(r"Reading \d+ postings", line) for line in lines) and "Writing module 3 of 7" in lines and details <= set(lines)

        assert (done["source"], done["imported_from"], done["error"], done["error_code"]) == ("requested", None, None, None)
        assert done["url"] == f"/learning/{pathway_id}/index.html" and done["course"]["lessons"] == 28 and done["course"]["generated_at"]  # type: ignore[index]
        assert done["cost"] == {  # type: ignore[index]
            "cli_model_calls": 11, "worker_minutes": done["cost"]["worker_minutes"], "web_fetches": 84, "web_searches": 0, "tokens": None, "note": None,  # type: ignore[index]
        }
        progress = done["progress"]
        assert isinstance(progress, dict) and progress["step"] is None and {step["status"] for step in progress["steps"]} == {"done"}
        assert (progress["calls"], progress["fetches"], progress["max_calls"], progress["max_fetches"], progress["dropped"]) == (11, 84, 120, 1500, [])

        listed = client.get(PATHWAYS).json()
        assert listed["total"] == 1 and set(listed["pathways"][0]) == ROW_KEYS
        assert {key: listed["pathways"][0][key] for key in ("id", "status", "url", "cost", "progress")} == {
            "id": pathway_id, "status": "done", "url": done["url"], "cost": done["cost"], "progress": progress,
        }
        # no route answered posting text or resume text: only counts, codes and plain lines
        everything = json.dumps(seen) + json.dumps(listed)
        assert "Experience with Terraform is required" not in everything and "jobs.lever.co" not in everything

        # ---- 4. the course is served, locked down -----------------------------------------------------------------
        index = client.get(str(done["url"]))
        assert index.status_code == 200 and index.headers["content-security-policy"] == CSP
        assert index.headers["content-type"] == "text/html; charset=utf-8" and "MLOps engineer" in index.text
        lesson = client.get(f"/learning/{pathway_id}/concept-lesson-1-1.html")
        assert lesson.status_code == 200 and lesson.headers["content-security-policy"] == CSP
        assert next(iter(script["pages"])) in lesson.text, "a lesson links to a page this job verified"  # type: ignore[arg-type, call-overload]
        assert "<script>" not in lesson.text and client.get(f"/learning/{pathway_id}/assets/course.js").status_code == 200

        # a finished course is not stopped and not resumed; an unknown id is 404
        stop = client.post(f"{PATHWAYS}/{pathway_id}/cancel", json={})
        assert stop.status_code == 200 and stop.json()["cancel_requested"] is False and stop.json()["pathway"]["status"] == "done"
        _error(client.post(f"{PATHWAYS}/{pathway_id}/resume", json={}), 409, "not_resumable")
        for action in ("cancel", "resume"):
            _error(client.post(f"{PATHWAYS}/lp-00000000/{action}", json={}), 404, "not_found")
            _error(client.post(f"{PATHWAYS}/nope/{action}", json={}), 404, "not_found")
            _error(client.post(f"{PATHWAYS}/{pathway_id}/{action}", json={"force": True}), 422, "unknown_key")
        assert client.post(f"{PATHWAYS}/{pathway_id}/again", json={}).status_code == 404

        # ---- 5. cancel a second job, then resume it ------------------------------------------------------------
        second = client.post(PATHWAYS, json={"role_text": "ML platform engineer", "approve": True})
        assert second.status_code == 202, second.text
        second_id = second.json()["pathway"]["id"]
        _poll(client, second_id, ("running",))
        stop = client.post(f"{PATHWAYS}/{second_id}/cancel", json={})
        assert stop.status_code == 200 and stop.json()["cancel_requested"] is True, stop.text
        stopped = _poll(client, second_id, ("done", "failed"))[-1]
        assert (stopped["status"], stopped["error_code"]) == ("failed", "cancelled"), stopped
        assert "Stopped" in str(stopped["error"]) and stopped["course"] is None and stopped["progress"]["calls"] < 11  # type: ignore[index]
        assert client.get(f"/learning/{second_id}/index.html").status_code == 404
        spent = stopped["progress"]["calls"]  # type: ignore[index]

        resumed = client.post(f"{PATHWAYS}/{second_id}/resume", json={})
        assert resumed.status_code == 202 and resumed.json()["pathway"]["id"] == second_id, resumed.text
        finished = _poll(client, second_id, ("done", "failed"))[-1]
        assert finished["status"] == "done" and finished["course"]["lessons"] == 28, finished  # type: ignore[index]
        # the calls of both runs are counted; a step that had its result was not asked for again
        assert finished["cost"]["cli_model_calls"] == finished["progress"]["calls"] and spent < finished["progress"]["calls"] <= spent + 11  # type: ignore[index]
        assert client.get(f"/learning/{second_id}/index.html").headers["content-security-policy"] == CSP
        assert [row["id"] for row in client.get(PATHWAYS).json()["pathways"]] == [second_id, pathway_id], "the newest request first"
        # G7d: the estimate is now sized from the two done courses' own cost records (not call_metrics, which this
        # script's model never writes rows to), the average of their recorded calls and fetches
        third = client.post(PATHWAYS, json={"role_text": ROLE}).json()["estimate"]
        assert third["basis"] == "history" and third["fetches"] == 84
        assert third["calls"] == round((done["cost"]["cli_model_calls"] + finished["cost"]["cli_model_calls"]) / 2)  # type: ignore[index]
    finally:
        stop_server(server)
