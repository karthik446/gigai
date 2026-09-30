"""N11-C: a find-jobs search reads the company index and asks no board.

Through the real supervised server and the fixture transport only, like
every journey in this suite. Exa is off here, so every request a run could
make would be a board request: the run's own raw capture (every response
its HTTP client received, ``runs/<id>/raw/index.json``) is the proof that it
made none.

1. A company is on the watchlist but "Update sources" has never run: the
   search does not fetch. The run fails at acquire with
   ``sources_update_required`` and the message the UI shows; its progress
   carries ``boards.index.needs_update``.
2. "Update sources" runs (``POST /api/sources/update``): the board is
   fetched once and indexed.
3. The search now succeeds from the index: rows with their descriptions,
   an assessment, ``boards.source == "index"``, ``boards.requests == 0``,
   and no raw capture at all.
4. A second search: the same rows, UNCHANGED, still zero requests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
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

ACME_BOARD = "https://boards.greenhouse.io/acme"
EMPTY_MESSAGE = "No company postings are stored on this machine yet. Run Update sources, then search again."


def _boards_only_config(target: Path) -> None:
    write_offline_find_jobs_config(target, sources_live=True)
    path = target / "find-jobs.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["sources"] = {"exa": False, "ats": True, "hiringcafe": False}
    path.write_text(json.dumps(config), encoding="utf-8")


def _update_sources(client: httpx.Client) -> dict:
    started = client.post("/api/sources/update", json={})
    assert started.status_code == 202, started.text
    deadline = time.monotonic() + latency_bound(60.0)
    while True:
        body = client.get("/api/sources/update").json()
        if not body["running"]:
            assert body["update"]["status"] == "succeeded", body
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.2)


def _run(client: httpx.Client) -> tuple[str, dict]:
    config_digest = client.get("/api/config").json()["config_digest"]
    response = client.post("/api/run", json=run_request_body(config_digest))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    return run_id, poll_until_terminal(client, run_id, deadline_seconds=120.0)


def test_a_search_never_fetches_a_board_and_says_when_to_update_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    _boards_only_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        added = client.post("/api/watchlist", json={"url": ACME_BOARD})
        assert added.status_code == 201, added.text

        # -- 1. nothing stored: the search does not fetch, it says what to do --
        empty_run, empty_status = _run(client)
        assert empty_status["status"] == "failed", empty_status
        acquire = {item["node_slug"]: item for item in empty_status["node_receipts"]}["acquire"]
        assert acquire["status"] == "failed"
        assert EMPTY_MESSAGE in json.dumps(empty_status)
        progress = client.get(f"/api/runs/{empty_run}/progress").json()
        assert progress["boards"]["source"] == "index" and progress["boards"]["requests"] == 0
        assert progress["boards"]["index"]["status"] == "empty"
        assert progress["boards"]["index"]["needs_update"] is True
        assert progress["boards"]["index"]["message"] == EMPTY_MESSAGE
        assert not (workpad / "runs" / empty_run / "raw").exists(), "the search made a request"
        assert not (home / "cache" / "scout" / "companies").exists()
        assert client.get("/api/sources/update").json()["index"]["needs_update"] is True

        # -- 2. Update sources: the one place boards are fetched ---------------
        update = _update_sources(client)["update"]
        assert update["boards"] == {"total": 1, "done": 1, "checked": 1, "fetched": 1, "cached": 0, "failed": 0, "skipped": 0, "never_checked": 0}
        assert update["summary"] == "1 company with new postings: 1 new, 0 changed, 0 removed"

        # -- 3. the search reads the index: zero board requests -----------------
        first_run, first_status = _run(client)
        assert first_status["status"] == "succeeded", first_status
        assert [item["node_slug"] for item in first_status["node_receipts"]] == ["acquire", "assess", "present"]
        progress_response, progress_latency = timed_request(
            "GET /api/runs/{run_id}/progress", lambda: client.get(f"/api/runs/{first_run}/progress")
        )
        boards = progress_response.json()["boards"]
        progress_latency.assert_within_budget()
        assert boards["source"] == "index" and boards["status"] == "done"
        assert boards["requests"] == 0 and boards["fetched"] == 0
        assert boards["total"] == 1 and boards["cached"] == 1 and boards["matched"] == 1
        assert boards["touched_since_last_search"] == 1
        assert boards["index"]["status"] == "ready" and boards["index"]["needs_update"] is False
        assert boards["exa_new"] == {"cap": 20, "found": 0, "fetched": 0, "failed": 0, "waiting": 0, "requests": 0}
        assert progress_response.json()["rotation"] is None
        # Every response the run's HTTP client received is captured under
        # raw/: there is none, so the run asked nothing.
        assert not (workpad / "runs" / first_run / "raw").exists(), "the search made a request"

        payload = client.get(f"/api/runs/{first_run}/results").json()["payload"]
        assert [row["posting"]["url"] for row in payload["rows"]] == ["https://boards.greenhouse.io/acme/jobs/101"]
        assert payload["rows"][0]["outcome"] == "new"
        assert payload["rows"][0]["posting"]["text"], "the posting's description comes from the stored board"
        assert payload["assessments"], "the posting is assessed in the same run"
        assert payload["failures"] == []

        # -- 4. again: unchanged, still zero requests ---------------------------
        second_run, second_status = _run(client)
        assert second_status["status"] == "succeeded", second_status
        again = client.get(f"/api/runs/{second_run}/progress").json()["boards"]
        assert again["requests"] == 0 and again["touched_since_last_search"] == 0
        assert not (workpad / "runs" / second_run / "raw").exists()
        second_payload = client.get(f"/api/runs/{second_run}/results").json()["payload"]
        assert [row["outcome"] for row in second_payload["rows"]] == ["unchanged"]
        assert [row["posting"] for row in second_payload["rows"]] == [row["posting"] for row in payload["rows"]]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
