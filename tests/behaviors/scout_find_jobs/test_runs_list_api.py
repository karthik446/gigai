"""P9c: ``GET /api/runs`` -- every find-jobs run for this target, newest
first, with per-run counts and the profile it ran against.

Drives a real, offline run (the same ``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP``/
``_MODEL`` seams ``test_present_logging.py``'s own real-backend tests use,
via ``test_m1_end_to_end.py``'s ``_fixture``/``_run_request``) against a
real ``ScoutFindJobsBackend``/``serve()`` server -- in-process, no spawned
subprocess -- so this route's counts are read back from genuinely committed
sealed outputs, never hand-faked fixtures.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

from .test_m1_end_to_end import _fixture, _run_request

_POLL_DEADLINE_SECONDS = 30.0


def _poll_succeeded(client: httpx.Client, run_id: str) -> dict[str, object]:
    deadline = time.monotonic() + _POLL_DEADLINE_SECONDS
    last_body: object = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        last_body = response.text
        if response.status_code == 200:
            body = response.json()
            if body["status"] in {"succeeded", "failed", "blocked", "cancelled", "interrupted"}:
                return body
        time.sleep(0.05)
    pytest.fail(f"run {run_id} did not terminalize before timeout; last response={last_body!r}")
    raise AssertionError("unreachable")


@pytest.fixture
def running_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home, target, _workpad = _fixture(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "p9c-test-key")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")

    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=60.0) as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_runs_list_is_empty_before_any_run(running_server) -> None:
    response = running_server.get("/api/runs")
    assert response.status_code == 200, response.text
    assert response.json() == {"schema_version": "scout-runs-list-response:1", "runs": []}


def test_runs_list_shows_a_finished_run_with_real_counts(running_server) -> None:
    client = running_server
    profile_id = client.get("/api/profiles").json()["selected_profile_id"]
    assert profile_id

    config_digest = client.get("/api/config").json()["config_digest"]
    run_response = client.post("/api/run", json=_run_request(config_digest))
    assert run_response.status_code == 202, run_response.text
    run_id = run_response.json()["run_id"]
    status_body = _poll_succeeded(client, run_id)
    assert status_body["status"] == "succeeded", status_body

    results_body = client.get(f"/api/runs/{run_id}/results").json()
    payload = results_body["payload"]

    list_response = client.get("/api/runs")
    assert list_response.status_code == 200, list_response.text
    list_body = list_response.json()
    assert list_body["schema_version"] == "scout-runs-list-response:1"
    assert len(list_body["runs"]) == 1
    entry = list_body["runs"][0]
    assert entry["run_id"] == run_id
    assert entry["status"] == "succeeded"
    assert entry["profile_id"] == profile_id
    assert entry["created_at"]
    assert entry["counts"] == {
        "found": len(payload["rows"]),
        "new": sum(1 for row in payload["rows"] if row["outcome"] == "new"),
        "assessed": len(payload["assessments"]),
        "matched": sum(1 for item in payload["assessments"] if item.get("verdict") == "matched_above_threshold"),
    }


def test_runs_list_profile_filter_narrows_to_that_profile_only(running_server) -> None:
    client = running_server
    profile_id = client.get("/api/profiles").json()["selected_profile_id"]

    config_digest = client.get("/api/config").json()["config_digest"]
    run_response = client.post("/api/run", json=_run_request(config_digest))
    run_id = run_response.json()["run_id"]
    _poll_succeeded(client, run_id)

    matching = client.get("/api/runs", params={"profile_id": profile_id})
    assert matching.status_code == 200, matching.text
    assert len(matching.json()["runs"]) == 1

    other = client.get("/api/runs", params={"profile_id": "profile_does_not_exist"})
    assert other.status_code == 200, other.text
    assert other.json()["runs"] == []


def test_runs_list_is_newest_first(running_server) -> None:
    client = running_server
    config_digest = client.get("/api/config").json()["config_digest"]

    first_run = client.post("/api/run", json=_run_request(config_digest)).json()["run_id"]
    _poll_succeeded(client, first_run)

    config_digest_2 = client.get("/api/config").json()["config_digest"]
    second_run = client.post("/api/run", json=_run_request(config_digest_2)).json()["run_id"]
    _poll_succeeded(client, second_run)

    list_body = client.get("/api/runs").json()
    run_ids = [item["run_id"] for item in list_body["runs"]]
    assert run_ids == [second_run, first_run]
