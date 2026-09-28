"""N11-C: the "Update sources" journey (``POST``/``GET /api/sources/update``).

Through the real supervised server (``run_supervisor.start``) and the
fixture transport only (``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP=1``: the ``acme``
Greenhouse board answers with one posting and its detail; an Ashby board has
no fixture route and 404s), like every journey in this suite:

1. ``GET /api/sources/update`` on a fresh home: no update yet, the index is
   ``empty`` and carries the 'Run Update sources' message.
2. Two companies are added to the watchlist (``POST /api/watchlist``).
3. ``POST /api/sources/update`` answers 202 with an id at once; polling
   ``GET`` ends in ``succeeded``: the live board is indexed (one JSON file
   per company under ``<home>/cache/scout/companies``), the dead one is
   counted as a board that did not answer, never an update failure.
4. A second update changes nothing: the board is served from the cache and
   the company file only moves ``checked_at``.
5. A foreign ``Origin`` is 403 before anything starts.

No setup preferences are saved, so nothing is seeded from the company
catalog: the update covers exactly the two user-added boards (the
full-catalog cost is measured separately, not journeyed).
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
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

ACME_BOARD = "https://boards.greenhouse.io/acme"
KONG_BOARD = "https://jobs.ashbyhq.com/kong"


def _poll_until_settled(client: httpx.Client, *, deadline_seconds: float = 60.0) -> dict:
    deadline = time.monotonic() + deadline_seconds
    while True:
        response = client.get("/api/sources/update")
        assert response.status_code == 200, response.text
        body = response.json()
        if not body["running"]:
            return body
        assert time.monotonic() < deadline, f"sources update did not finish before the deadline: {body}"
        time.sleep(0.2)


def test_update_sources_indexes_the_watchlist_and_a_second_update_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        companies = home / "cache" / "scout" / "companies"

        before, before_latency = timed_request("GET /api/sources/update", lambda: client.get("/api/sources/update"))
        assert before.status_code == 200, before.text
        assert before.json() == {
            "schema_version": "scout-sources-update-status:1",
            "running": False,
            "update": None,
            "index": {
                "status": "empty",
                "needs_update": True,
                "message": "No company postings are stored on this machine yet. Run Update sources, then search again.",
                "companies_indexed": 0,
                "last_checked_at": None,
                "stale_after_hours": 24.0,
            },
        }
        before_latency.assert_within_budget()

        for url in (ACME_BOARD, KONG_BOARD):
            added = client.post("/api/watchlist", json={"url": url})
            assert added.status_code == 201, added.text

        evil = client.post("/api/sources/update", json={}, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403, evil.text
        assert not companies.exists()

        # -- first update: the live board is indexed ------------------------
        started, start_latency = timed_request(
            "POST /api/sources/update", lambda: client.post("/api/sources/update", json={})
        )
        assert started.status_code == 202, started.text
        start_body = started.json()
        assert start_body["schema_version"] == "scout-sources-update-start-response:1"
        assert start_body["status"] == "running"
        start_latency.assert_within_budget()

        first = _poll_until_settled(client)
        update = first["update"]
        assert update["update_id"] == start_body["update_id"]
        assert update["status"] == "succeeded", update
        assert update["boards"] == {"total": 2, "done": 2, "fetched": 1, "cached": 0, "failed": 1, "skipped": 0}
        assert update["companies"]["checked"] == 1 and update["companies"]["indexed"] == 1
        assert update["postings"] == {"new": 1, "changed": 0, "removed": 0, "live": 1}
        assert update["summary"] == "1 company with new postings: 1 new, 0 changed, 0 removed"
        assert update["remaining"] == 0 and update["error"] is None
        assert update["roles"] == ["software engineer"]
        assert update["watchlist_seed"] == {"status": "skipped", "reason": "prefs_missing"}
        assert first["index"]["status"] == "ready" and first["index"]["needs_update"] is False
        assert first["index"]["message"] is None and first["index"]["companies_indexed"] == 1
        assert first["index"]["last_checked_at"] == update["finished_at"]

        assert sorted(item.name for item in companies.iterdir()) == ["greenhouse:acme.json", "last-update.json"]
        acme = json.loads((companies / "greenhouse:acme.json").read_text(encoding="utf-8"))
        assert acme["schema_version"] == "scout-company-index:1"
        assert (acme["ats"], acme["slug"], acme["company"]) == ("greenhouse", "acme", "acme")
        assert acme["body_sha256"].startswith("sha256:") and acme["checked_at"]
        assert list(acme["postings"]) == ["101"]
        posting = acme["postings"]["101"]
        assert posting["title"] == "Software Engineer" and posting["location"] == "Denver, CO"
        assert posting["url"] == "https://boards.greenhouse.io/acme/jobs/101"
        assert posting["updated_at"] == "2026-09-22T00:00:00Z"
        assert posting["content_sha256"].startswith("sha256:")
        assert posting["first_seen"] == posting["last_seen"] == acme["checked_at"]
        assert "removed_at" not in posting

        # -- second update: nothing changed ----------------------------------
        again = client.post("/api/sources/update", json={})
        assert again.status_code == 202, again.text
        assert again.json()["update_id"] != start_body["update_id"]
        second = _poll_until_settled(client)["update"]
        assert second["status"] == "succeeded", second
        assert second["boards"] == {"total": 2, "done": 2, "fetched": 0, "cached": 1, "failed": 1, "skipped": 0}
        assert second["companies"]["untouched"] == 1
        assert second["summary"] == "0 companies with new postings: 0 new, 0 changed, 0 removed"
        unchanged = json.loads((companies / "greenhouse:acme.json").read_text(encoding="utf-8"))
        assert unchanged["checked_at"] > acme["checked_at"]
        assert {**unchanged, "checked_at": acme["checked_at"]} == acme

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    # The index and the status snapshot live under the home's cache: the
    # journaled workpad holds the two watchlist adds and nothing else new.
    assert not list(workpad.rglob("greenhouse:acme.json"))
    assert_clean_and_healthy(workpad, home)
