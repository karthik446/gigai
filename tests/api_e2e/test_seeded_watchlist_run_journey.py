"""Q2 + N11-C: a watchlist seeded from the bundled catalog, updated, then searched.

setup (``PUT /api/setup`` with ``countries: ["US"]``) -> Update sources ->
run -> poll -> results, the run twice, through the real supervised server
and the fixture transports only (``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1``:
every Greenhouse board answers with the ``acme`` fixture job; Lever/Ashby
boards 404).

N11-C moved the board fetch out of the run: "Update sources" (``POST
/api/sources/update``) seeds every catalog board the prefs admit and fetches
them all under the concurrency/pacing limits, writing the company index. The
runs then read that index. The only board a run asks is the ``acme`` board
the Exa fixture discovers in the first run (a company Exa found in that run
and the index does not hold: fetched once, indexed); the second run asks
none. The existing journey assertions (run succeeds, acquire/assess/present
receipts, results rows + assessments, workpad clean + doctor PASS) all still
hold over the seeded watchlist.

Pacing is turned off for the journey (``GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS=0``,
an operator-tunable knob, not a test seam): ~200 boards at the production
8 requests/s would make this a minute-long test for no extra coverage.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from gigai.scout.find_jobs.company_catalog import load_company_catalog
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.market_acquisition import ATS_CONCURRENCY_ENV, ATS_MIN_INTERVAL_ENV
from gigai.scout.find_jobs.progress import read_progress
from gigai.scout.find_jobs.watchlist import list_active

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
from tests.api_e2e.test_discover_fake_provider import _SETUP_BODY
from tests.support.latency import latency_bound


def test_run_over_a_seeded_watchlist_passes_the_existing_journeys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATS_MIN_INTERVAL_ENV, "0")
    monkeypatch.setenv(ATS_CONCURRENCY_ENV, "8")
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    catalog = load_company_catalog()
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # setup: the prefs the seeding filters by (US only, no excludes). Work
        # mode Any: every fixture posting is "Denver, CO" (a plain city), which
        # uat-bug-028 correctly drops for a Remote-only operator; this journey
        # is about the seeded watchlist, not the work-mode filter.
        setup_response = client.put("/api/setup", json={**_SETUP_BODY, "work_mode": "any"})
        assert setup_response.status_code == 200, setup_response.text
        assert setup_response.json()["prefs"]["countries"] == ["US"]
        assert list_active(home, target) == (), "seeding must not happen at setup time"

        # -- Update sources: seeds + fetches every admitted catalog board ----
        started = client.post("/api/sources/update", json={})
        assert started.status_code == 202, started.text
        update_deadline = time.monotonic() + latency_bound(120.0)
        while True:
            sources = client.get("/api/sources/update").json()
            if not sources["running"]:
                break
            assert time.monotonic() < update_deadline, sources
            time.sleep(0.2)
        update = sources["update"]
        assert update["status"] == "succeeded", update
        seed = update["watchlist_seed"]
        assert seed is not None and seed["status"] == "seeded", seed
        assert seed["catalog_revision"] == catalog.revision
        assert seed["catalog_digest"] == catalog.digest
        assert seed["added"] == seed["eligible"] > 0
        seeded_ids = {entry.watchlist_id for entry in list_active(home, target)}
        assert len(seeded_ids) >= seed["added"]
        boards = update["boards"]
        assert boards["total"] == boards["done"] == len(seeded_ids)
        assert boards["skipped"] == 0
        assert boards["fetched"] + boards["failed"] == boards["total"]
        assert boards["fetched"] > 0
        # Lever/Ashby catalog boards have no fixture route (404): each is a
        # board that did not answer, never an update failure.
        assert boards["failed"] == sum(1 for entry in list_active(home, target) if entry.provider.value != "greenhouse")
        assert update["companies"]["indexed"] == boards["fetched"]
        assert sources["index"]["status"] == "ready" and sources["index"]["companies_indexed"] == boards["fetched"]

        config_digest = client.get("/api/config").json()["config_digest"]

        # -- first run: reads the index; only Exa's new company is fetched ----
        run_response, run_latency = timed_request(
            "POST /api/run", lambda: client.post("/api/run", json=run_request_body(config_digest))
        )
        assert run_response.status_code == 202, run_response.text
        first_run_id = run_response.json()["run_id"]
        run_latency.assert_within_budget()
        first_status = poll_until_terminal(client, first_run_id, deadline_seconds=120.0)
        assert first_status["status"] == "succeeded", first_status
        assert [item["node_slug"] for item in first_status["node_receipts"]] == ["acquire", "assess", "present"]

        progress_response, progress_latency = timed_request(
            "GET /api/runs/{run_id}/progress", lambda: client.get(f"/api/runs/{first_run_id}/progress")
        )
        assert progress_response.status_code == 200, progress_response.text
        assert progress_response.json()["steps"]["acquire"]["status"] == "done"
        progress_latency.assert_within_budget()

        results_response, results_latency = timed_request(
            "GET /api/runs/{run_id}/results", lambda: client.get(f"/api/runs/{first_run_id}/results")
        )
        assert results_response.status_code == 200, results_response.text
        first_payload = results_response.json()["payload"]
        assert first_payload["rows"], "acquire must still find the fixture Greenhouse posting"
        assert first_payload["assessments"], "assess must still produce an assessment"
        results_latency.assert_within_budget()

        workpad = resolve_workpad_path(home, target)
        first_progress = read_progress(workpad / "runs" / first_run_id)
        run_seed = first_progress.watchlist_seed
        # The update already seeded: the run finds nothing new to add.
        assert run_seed is not None and run_seed["status"] == "seeded", run_seed
        assert run_seed["added"] == 0 and run_seed["already_present"] == seed["eligible"]
        # Q4b-data (Q2 flag 1): the route serves the same two Q2 keys the
        # progress files hold, not just the hand-built subset.
        progress_body = progress_response.json()
        assert progress_body["watchlist_seed"] == run_seed
        assert progress_body["boards"] == first_progress.boards
        # N11-C: the run read the index. The one board it asked is the
        # company the Exa fixture found in this run (acme, not in the catalog).
        run_boards = first_progress.boards
        assert run_boards["status"] == "done" and run_boards["source"] == "index"
        assert run_boards["cached"] == boards["fetched"]
        assert run_boards["exa_new"]["found"] == 1 and run_boards["exa_new"]["fetched"] == 1
        assert run_boards["exa_new"]["waiting"] == 0
        assert run_boards["requests"] == run_boards["exa_new"]["requests"] <= 2
        # Q4b-data: the H-1B join. Every served row agrees with the shipped
        # catalog: ``h1b`` present iff its board is a catalog record whose
        # h1b is an object, and then exactly that aggregate; never ``null``.
        h1b_index = catalog.h1b_by_board()
        for row in first_payload["rows"]:
            posting = row["posting"]
            key = (ATSProvider(posting["provider"]), (posting["board_token"] or "").lower())
            expected = h1b_index.get(key)
            if expected is None:
                assert "h1b" not in row, row
            else:
                assert row["h1b"] == expected.to_json(), row
        # Exa's find is on the watchlist from this run on.
        after_first = {entry.watchlist_id for entry in list_active(home, target)}
        assert after_first == seeded_ids | {"scout_watchlist:greenhouse:acme"}

        # -- second run: nothing new to seed, nothing to fetch ---------------
        second_response = client.post("/api/run", json=run_request_body(config_digest))
        assert second_response.status_code == 202, second_response.text
        second_run_id = second_response.json()["run_id"]
        second_status = poll_until_terminal(client, second_run_id, deadline_seconds=120.0)
        assert second_status["status"] == "succeeded", second_status
        second_payload = client.get(f"/api/runs/{second_run_id}/results").json()["payload"]
        assert second_payload["rows"]

        second_progress = read_progress(workpad / "runs" / second_run_id)
        again = second_progress.watchlist_seed
        assert again is not None and again["status"] == "seeded" and again["added"] == 0
        assert again["already_present"] == seed["eligible"]
        assert second_progress.boards["source"] == "index"
        assert second_progress.boards["total"] == boards["total"] + 1
        assert second_progress.boards["cached"] == boards["fetched"] + 1, second_progress.boards
        assert second_progress.boards["requests"] == 0
        assert second_progress.boards["exa_new"]["found"] == 0
        assert {entry.watchlist_id for entry in list_active(home, target)} == after_first
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
