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
        assert update["boards"] == {"total": 1, "done": 1, "checked": 1, "fetched": 1, "cached": 0, "failed": 0, "skipped": 0, "never_checked": 0, "up_to_date": 0}
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


def _run_with(client: httpx.Client, **body: object) -> httpx.Response:
    config_digest = client.get("/api/config").json()["config_digest"]
    return client.post("/api/run", json=run_request_body(config_digest, **body))


def test_search_keywords_filter_through_the_text_index_and_the_sealed_config_shows_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0110-026 (F2): ``POST /api/run`` takes optional ``keywords``.

    The posting's stored description is "Build reliable Python services."
    1. Keywords before any Update sources: no text index, so they are
       ignored with a reason; the run fails for the empty index, as without them.
    2. After Update sources, a keyword found only in the description: the
       posting is kept and assessed; the run's sealed config holds the
       keywords and ``boards.keywords`` counts the match.
    3. A keyword the text does not hold: the posting is dropped, counted.
    4. A bad ``keywords`` value is 422 and starts nothing; the form's
       ``config_digest`` (which never includes keywords) is still checked.
    """

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    _boards_only_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        assert client.post("/api/watchlist", json={"url": ACME_BOARD}).status_code == 201
        plain_config = client.get("/api/config").json()
        assert "keywords" not in plain_config["config"]

        # -- 1. no text index yet: ignored with a reason, never an error ----------
        early = _run_with(client, keywords=["python services"])
        assert early.status_code == 202, early.text
        early_id = early.json()["run_id"]
        assert poll_until_terminal(client, early_id, deadline_seconds=120.0)["status"] == "failed"  # the empty index, as in the journey above
        early_keywords = client.get(f"/api/runs/{early_id}/progress").json()["boards"]["keywords"]
        assert (early_keywords["applied"], early_keywords["reason"], early_keywords["terms"]) == (False, "no_text_index", ["python services"])
        assert not (home / "cache" / "scout" / "text.sqlite").exists()

        _update_sources(client)

        # -- 2. a keyword only the description holds: kept, assessed, sealed --------
        found = _run_with(client, keywords=["  Python   services ", "kubernetes"])
        assert found.status_code == 202, found.text
        found_id = found.json()["run_id"]
        found_status = poll_until_terminal(client, found_id, deadline_seconds=120.0)
        assert found_status["status"] == "succeeded", found_status
        sealed = json.loads((workpad / "runs" / found_id / "sealed" / "find-jobs-config.json").read_text(encoding="utf-8"))
        assert sealed["keywords"] == ["Python services", "kubernetes"]
        assert {key: value for key, value in sealed.items() if key != "keywords"} == plain_config["config"]
        boards = client.get(f"/api/runs/{found_id}/progress").json()["boards"]
        assert boards["keywords"] == {
            "terms": ["Python services", "kubernetes"],
            "mode": "filter",
            "applied": True,
            "reason": None,
            "message": None,
            "matched": 1,
            "dropped": 0,
            "text_not_checked": 0,
        }
        assert boards["requests"] == 0 and boards["matched"] == 1
        payload = client.get(f"/api/runs/{found_id}/results").json()["payload"]
        assert [row["posting"]["url"] for row in payload["rows"]] == ["https://boards.greenhouse.io/acme/jobs/101"]
        assert payload["assessments"], "the keyword-only match reached assess"
        # The stored config and its digest are what they were: keywords belong to the one search.
        assert client.get("/api/config").json() == plain_config

        # -- 3. a keyword the stored text does not hold: dropped and counted --------
        missed = _run_with(client, keywords=["kubernetes"])
        assert missed.status_code == 202, missed.text
        missed_id = missed.json()["run_id"]
        poll_until_terminal(client, missed_id, deadline_seconds=120.0)
        missed_boards = client.get(f"/api/runs/{missed_id}/progress").json()["boards"]
        assert (missed_boards["keywords"]["matched"], missed_boards["keywords"]["dropped"], missed_boards["keywords"]["text_not_checked"]) == (0, 1, 0)
        assert missed_boards["matched"] == 0

        # A run without keywords has no keywords block and an unchanged sealed config.
        plain_id, plain_status = _run(client)
        assert plain_status["status"] == "succeeded", plain_status
        assert "keywords" not in client.get(f"/api/runs/{plain_id}/progress").json()["boards"]
        assert json.loads((workpad / "runs" / plain_id / "sealed" / "find-jobs-config.json").read_text(encoding="utf-8")) == plain_config["config"]

        # -- 4. validation ------------------------------------------------------------
        runs_before = len(client.get("/api/runs").json()["runs"])
        for bad, code in ((["   "], "invalid_value"), ("python", "wrong_type"), ([7], "wrong_type"), (["x" * 101], "invalid_value")):
            refused = _run_with(client, keywords=bad)
            assert refused.status_code == 422 and refused.json()["error"]["code"] == code, (bad, refused.text)
        stale = client.post("/api/run", json=run_request_body("sha256:" + "0" * 64, keywords=["python"]))
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "config_digest_mismatch", stale.text
        unknown = _run_with(client, keyword=["python"])
        assert unknown.status_code == 422 and "keywords" in unknown.json()["error"]["allowed_keys"], unknown.text
        assert len(client.get("/api/runs").json()["runs"]) == runs_before
        # An empty list is "no keywords": the plain run.
        empty = _run_with(client, keywords=[])
        assert empty.status_code == 202, empty.text
        empty_id = empty.json()["run_id"]
        poll_until_terminal(client, empty_id, deadline_seconds=120.0)
        assert "keywords" not in client.get(f"/api/runs/{empty_id}/progress").json()["boards"]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
