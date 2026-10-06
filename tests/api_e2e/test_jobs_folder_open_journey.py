"""0.1.11.4 J3: ``POST /api/jobs-folder/open`` over HTTP against the real supervised server.

The job page's "Open folder" button. Scout's own page only (a request carrying this server's own ``Origin``); the
folder is found by the server from the stored job, never taken from the request; one that is not inside the jobs
folder is refused. The opener is the test seam (``GIGAI_SCOUT_OPEN_FOLDER_TEST=1``: the folder is written to
``<home>/open-folder-test.log`` instead of a file manager window), so nothing opens during the run.

1. No Origin, another site's Origin, another port's Origin: 403 ``forbidden_origin``, nothing opened.
2. A job with a folder: the answer names the folder as the user types it and the opener was given exactly that
   folder; the jobs folder itself (``{}``) opens the root; a job GigAI made no folder for is 404 ``folder_missing``.
3. A path in the request is an unknown key (422); a company folder that is a link out of the jobs folder is 422
   ``outside_jobs_folder`` and nothing is opened.
4. The route is not an agent route: its description says so, and ``llms.txt`` does not list it.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil

import httpx
import pytest

from tests.api_e2e.harness import add_resume, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_URL = "https://boards.greenhouse.io/acme/jobs/101"
ROUTE = "/api/jobs-folder/open"


def _error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status and response.json()["error"]["code"] == code, response.text


def _opened(home: Path) -> list[str]:
    log = home / "open-folder-test.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_open_folder_opens_only_a_folder_the_server_found_and_only_for_scouts_own_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    monkeypatch.setenv("GIGAI_SCOUT_OPEN_FOLDER_TEST", "1")
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        page = {"Origin": server.base_url.rstrip("/")}
        assert client.post("/api/assess", json={"job": {"job_url": _URL}}).status_code == 200
        tailored = client.post("/api/tailored-resumes", json={"job": {"job_url": _URL}}).json()
        key = {"profile_id": tailored["resume"]["profile_id"], "job_identity": tailored["job"]["job_identity"]}
        jobs, job_dir = home / "jobs", home / "jobs" / "acme" / "software-engineer"
        assert job_dir.is_dir()

        # ---- 1. nobody but Scout's own page -----------------------------------------------------
        for name, headers in (("no Origin", {}), ("another site", {"Origin": "http://evil.example.invalid"}), ("another port", {"Origin": "http://127.0.0.1:1"})):
            refused = client.post(ROUTE, json=key, headers=headers)
            _error(refused, 403, "forbidden_origin")
            assert not _opened(home), name

        # ---- 2. the job's folder, the root, a job with no folder ---------------------------------------
        done = client.post(ROUTE, json=key, headers=page)
        assert done.status_code == 200, done.text
        shown = client.get("/api/jobs-folder", params=key).json()["job"]["shown"]
        assert done.json() == {"schema_version": "scout-jobs-folder-open:1", "opened": True, "shown": shown, "message": f"Opened {shown}."}
        assert _opened(home) == [str(job_dir.resolve())]
        root = client.post(ROUTE, json={}, headers=page)
        assert root.status_code == 200 and root.json()["shown"] == client.get("/api/jobs-folder").json()["shown"]
        assert _opened(home)[-1] == str(jobs.resolve())
        before = _opened(home)
        _error(client.post(ROUTE, json={**key, "job_identity": "https://boards.greenhouse.io/acme/jobs/999"}, headers=page), 404, "folder_missing")

        # ---- 3. never a path from the request; never a folder outside the jobs folder ------------------
        _error(client.post(ROUTE, json={**key, "path": str(tmp_path)}, headers=page), 422, "unknown_key")
        _error(client.post(ROUTE, json={"path": "/"}, headers=page), 422, "unknown_key")
        _error(client.post(ROUTE, json={"profile_id": key["profile_id"]}, headers=page), 422, "invalid_value")
        _error(client.post(ROUTE, json=[1], headers=page), 422, "wrong_type")
        outside = tmp_path / "somewhere-else"
        shutil.move(str(jobs / "acme"), str(outside))
        os.symlink(outside, jobs / "acme")
        _error(client.post(ROUTE, json=key, headers=page), 422, "outside_jobs_folder")
        assert _opened(home) == before, "nothing was opened for any refusal"

        # ---- 4. not an agent route ---------------------------------------------------------------------
        document = client.get("/api/openapi.json").json()
        assert "NOT for agents" in document["paths"][ROUTE]["post"]["description"]
        assert ROUTE not in client.get("/llms.txt").text
    finally:
        stop_server(server)
