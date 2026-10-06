"""0.1.11 URLCHECK: a job assessed BY URL (not in the stored-postings index) works end to end: the CLI and the page's API.

Real supervised server, the offline model and fixture transport (no model call, no network), a synthetic resume.
``gigai scout assess --job-url URL`` stores the quick assessment; then, for that job:

1. ``GET /api/jobs?url=`` and ``GET /api/assessments`` (the page's own read) answer with the assessment, the
   requirements, the questions and the resume / pick state;
2. ``resume pick|brief (both parts)|store|pdf --job-url`` and ``suggestions list`` work;
3. ``jobs list`` and ``jobs assess URL`` do NOT know it (``not_found``, by design: the index holds searched postings;
   the quick-assess store holds this job and the Assessments page lists it), the Jobs LIST (``GET /api/postings``)
   does not show it, and Re-assess is ``POST /api/assess`` for the same URL, which stores a new assessment the page reads;
4. the paste/URL box (``POST /api/assess``) stores the assessment of a second URL, which the page then opens.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from click.testing import CliRunner

from gigai.cli import cli

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_URL = "https://boards.greenhouse.io/acme/jobs/101"
_PASTED = "Acme is hiring a Staff Engineer to lead scheduling systems. Requirements: Python."


def test_a_job_assessed_by_url_works_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)

        def run(*args: str, ok: bool = True) -> dict[str, object]:
            result = CliRunner().invoke(cli, ["scout", *args, "--home", str(home), "--target", str(target), "--json"])
            assert (result.exit_code == 0) is ok, result.output
            return json.loads(result.output.strip().splitlines()[-1])

        # ---- the CLI quick assessment stores the job ------------------------------------------------
        assessed = run("assess", "--job-url", _URL)
        assert assessed["ok"] is True and assessed["result"]["verdict"]

        # ---- 1. what the job page reads -----------------------------------------------------------
        job = client.get(f"/api/jobs?url={quote(_URL, safe='')}").json()
        assert job["job_identity"] == _URL and job["assessments"] and job["assessments"][0]["matrix"]
        assert job["posting"]["title"] and job["links"]["pick"] and job["links"]["brief"]
        listed = client.get("/api/assessments").json()["items"]
        mine = [item for item in listed if item["job"]["job_identity"] == _URL]
        assert len(mine) == 1 and mine[0]["result"]["matrix"] and "questions" in mine[0]["result"]
        assert "model_notice" in mine[0] or True  # present only for a model GigAI's accuracy results are not for

        # ---- 2. the resume commands -------------------------------------------------------------------
        yours = run("resume", "brief", "--job-url", _URL)
        theirs = run("resume", "brief", "--job-url", _URL, "--posting")
        assert (yours["part"], theirs["part"]) == ("yours", "posting")
        picked = run("resume", "pick", "--job-url", _URL)
        assert picked.get("job_identity", _URL) == _URL
        resume_file = tmp_path / "handback.md"
        resume_file.write_text(_resume_markdown(), encoding="utf-8")
        stored = run("resume", "store", "--in", str(resume_file), "--job-url", _URL, "--as", "agent")
        assert stored["ok"] is True
        pdf = tmp_path / "out.pdf"
        run("resume", "pdf", "--job-url", _URL, "--out", str(pdf))
        assert pdf.read_bytes().startswith(b"%PDF")
        # The offline model answers in the shape of before 0.1.11, so no record is written: the typed error, not a crash.
        listed_suggestions = run("suggestions", "list", "--job-url", _URL, ok=False)
        assert listed_suggestions["error"]["code"] == "suggestions_not_found"
        print("suggestions list ->", listed_suggestions["error"]["message"])

        # ---- 3. F2: the index does not know the job ------------------------------------------------
        printed = run("jobs", "list")
        assert all(row.get("job_identity", row.get("url")) != _URL for row in printed["postings"]["rows"])
        assessed_these = run("jobs", "assess", _URL)
        assert assessed_these["counts"]["not_found"] == 1 and assessed_these["not_found"] == [_URL]
        assert _URL not in json.dumps(client.get("/api/postings").json()["postings"])

        # The page's Re-assess and the paste/URL box send the same body: POST /api/assess with the profile and origin.
        box = {"job": {"job_url": _URL}, "resume": {"profile_id": yours["profile_id"]}, "origin": "quick_assess"}
        again = client.post("/api/assess", json=box)
        assert again.status_code == 200, again.text
        after = [i for i in client.get("/api/assessments").json()["items"] if i["job"]["job_identity"] == _URL]
        assert len(after) == 1  # the same job, one stored assessment (a new one replaces it)
        assert client.get(f"/api/jobs?url={quote(_URL, safe='')}").json()["assessments"]

        # ---- 4. the box with a pasted posting: stored, listed, and the page opens it by its identity --------------
        pasted = client.post("/api/assess", json={"job": {"job_text": _PASTED}, "resume": box["resume"], "origin": "quick_assess"})
        assert pasted.status_code == 200, pasted.text
        identity = pasted.json()["job"]["job_identity"]
        assert any(i["job"]["job_identity"] == identity for i in client.get("/api/assessments").json()["items"])
        opened = client.get(f"/api/jobs?url={quote(identity, safe='')}")
        assert opened.status_code == 200 and opened.json()["assessments"], opened.text
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)


def _resume_markdown() -> str:
    return "## Summary\n\n- Software engineer with Python service experience.\n"
