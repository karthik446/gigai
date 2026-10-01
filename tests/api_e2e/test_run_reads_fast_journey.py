"""run-reads-fast (uat-bug-022, P0): after a full-catalog run the Jobs page

stayed empty for about a minute. On the operator's server ``GET /api/runs``
took 16 s on every call, ``.../results`` 44.6 s (3.9 MB) and ``.../progress``
1.9 s (3.7 MB).

This journey builds that size and reads it over HTTP from the real
supervised server, like every journey in this suite:

* a watchlist of 10,000 boards, seeded the way the catalog seeds it (no
  preferences are saved, so nothing else is seeded);
* 50 of them indexed by an "Update sources" pass over a fixture transport:
  500 postings, each with a description of about 3.7 KB;
* two find-jobs runs with a cap of 50: the first imports the 500 postings
  and assesses 50 of them; the second finds them unchanged, carries those
  50 assessments forward and assesses 50 more.

What it pins:

1. each of the four reads a page makes answers in under a second (median
   of 3, scaled by ``GIGAI_TEST_LATENCY_SCALE``): ``GET /api/runs``,
   ``GET /api/runs/{run_id}``, the first results page and the progress
   summary; so do the next page and one posting;
2. their size is bounded: a page of 100 rows is under 256 KB and the
   progress summary under 512 KB (1 KB a posting, at the 500-posting import
   bound), where the full reads are megabytes;
3. the first page is the top of the grid (the assessed postings first) and
   carries no posting text; the pages cover the run once; a posting read on
   its own is complete, text and assessment; every row the run did not
   assess says why (``over_cap``, ``duplicate``) on the page that lists it
   (0110-037);
4. the no-query reads still answer every row with its text.

The model and the boards are the suite's fixture transports; Jev has no key
here, so no page carries a score (``test_run_reads.py`` covers the scores).
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

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

WATCHLIST_BOARDS = 10_000
INDEXED_BOARDS = 50
POSTINGS_PER_BOARD = 10
POSTINGS = INDEXED_BOARDS * POSTINGS_PER_BOARD
ASSESSED = 50  # the run request's cap, and its maximum
DUPLICATES = POSTINGS - 2 * INDEXED_BOARDS  # a board's ten postings are two titles, five times each
OVER_CAP = 2 * INDEXED_BOARDS - ASSESSED  # the kept postings the cap left out
DESCRIPTION = "Build reliable Python services. " * 115  # about 3.7 KB

PAGE = 100
ROUTE_BUDGET_SECONDS = 1.0
PAGE_BYTES_MAX = 256 * 1024
PROGRESS_SUMMARY_BYTES_MAX = 512 * 1024
SMALL_BYTES_MAX = 16 * 1024  # the runs list, a run's status, one posting
FULL_READ_BYTES_MIN = 1024 * 1024  # what the page reads are measured against


def _jobs(slug: str, updated_at: str) -> list[dict[str, object]]:
    return [
        {
            "id": 1000 + index,
            "title": "Software Engineer" if index % 2 == 0 else "Senior Software Engineer",
            "absolute_url": f"https://boards.greenhouse.io/{slug}/jobs/{1000 + index}",
            "location": {"name": "Denver, CO"},
            "updated_at": updated_at,
            "content": f"&lt;p&gt;{slug} {1000 + index}. {DESCRIPTION}&lt;/p&gt;",
        }
        for index in range(POSTINGS_PER_BOARD)
    ]


def _board_transport() -> httpx.MockTransport:
    updated_at = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")  # v1/boards/<slug>/jobs[/<id>]
        if len(parts) >= 4 and parts[:2] == ["v1", "boards"] and parts[3] == "jobs":
            jobs = _jobs(parts[2], updated_at)
            if len(parts) == 4:
                return httpx.Response(200, json={"jobs": jobs}, headers={"etag": f'W/"{parts[2]}"'})
            for job in jobs:
                if str(job["id"]) == parts[4]:
                    return httpx.Response(200, json=job)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _catalog_size_project(tmp_path: Path) -> tuple[Path, Path]:
    """A project with 10,000 watched boards, 50 of them indexed with 500 postings."""

    from gigai.scout.find_jobs.company_index import CompanyIndex
    from gigai.scout.find_jobs.contracts import ATSProvider
    from gigai.scout.find_jobs.market_acquisition import AcquireLimits
    from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources
    from gigai.scout.find_jobs.watchlist import list_active, seed_watchlist_from_catalog
    from tests.behaviors.scout_find_jobs.test_read_routes_lock_free import _NO_FILTER
    from tests.behaviors.scout_find_jobs.test_watchlist_seed import _catalog, _record

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    config_path = target / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["sources"] = {"exa": False, "ats": True, "hiringcafe": False}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    catalog = _catalog(
        *(_record(f"Company {index}", ATSProvider.GREENHOUSE, f"board{index:05d}") for index in range(WATCHLIST_BOARDS))
    )
    seeded = seed_watchlist_from_catalog(home, target, prefs=_NO_FILTER, catalog=catalog)
    assert seeded.added == WATCHLIST_BOARDS

    indexed = [entry for entry in list_active(home, target) if int(entry.board_token.removeprefix("board")) < INDEXED_BOARDS]
    assert len(indexed) == INDEXED_BOARDS
    with httpx.Client(transport=_board_transport()) as client:
        updated = update_sources(
            indexed,
            cache=board_cache_for_home(home),
            index=CompanyIndex.for_home(home),
            client=client,
            limits=AcquireLimits(concurrency_per_provider=4, min_request_interval_seconds=0.0, time_budget_seconds=None),
        )
    assert updated.status == "succeeded", updated.to_json()
    return home, target


def _run(client: httpx.Client) -> str:
    digest = client.get("/api/config").json()["config_digest"]
    response = client.post("/api/run", json=run_request_body(digest, selection_cap=ASSESSED))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    status = poll_until_terminal(client, run_id, deadline_seconds=300.0)
    assert status["status"] == "succeeded", status
    return run_id


def _read(client: httpx.Client, path: str, *, bytes_max: int) -> dict:
    """``path``'s response, read three times: under a second (median) and under ``bytes_max``."""

    elapsed: list[float] = []
    response = None
    for _ in range(3):
        started = time.monotonic()
        response = client.get(path)
        elapsed.append(time.monotonic() - started)
        assert response.status_code == 200, (path, response.text[:400])
    median = statistics.median(elapsed)
    bound = latency_bound(ROUTE_BUDGET_SECONDS)
    assert median < bound, (
        f"GET {path} took {median:.3f}s (median of {[round(item, 3) for item in elapsed]}), "
        f"over its {bound:.1f}s budget"
    )
    assert len(response.content) < bytes_max, f"GET {path} answered {len(response.content)} bytes, over {bytes_max}"
    return response.json()


def test_the_jobs_page_reads_a_full_catalog_run_in_under_a_second_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = _catalog_size_project(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        client.timeout = httpx.Timeout(latency_bound(120.0))
        first_run = _run(client)
        second_run = _run(client)

        # -- 1. the four reads of a page load, timed and sized -----------------
        listed = _read(client, "/api/runs", bytes_max=SMALL_BYTES_MAX)
        status = _read(client, f"/api/runs/{first_run}", bytes_max=SMALL_BYTES_MAX)
        page = _read(client, f"/api/runs/{first_run}/results?limit={PAGE}", bytes_max=PAGE_BYTES_MAX)
        progress = _read(client, f"/api/runs/{first_run}/progress?summary=1", bytes_max=PROGRESS_SUMMARY_BYTES_MAX)

        assert [entry["run_id"] for entry in listed["runs"]] == [second_run, first_run]
        assert listed["runs"][1]["counts"] == {"found": POSTINGS, "new": POSTINGS, "assessed": ASSESSED, "matched": 0}
        assert listed["runs"][0]["counts"] == {"found": POSTINGS, "new": 0, "assessed": ASSESSED, "matched": 0}
        assert all(entry["status"] == "succeeded" and entry["created_at"] for entry in listed["runs"])
        assert status["status"] == "succeeded"
        assert [item["node_slug"] for item in status["node_receipts"]] == ["acquire", "assess", "present"]

        # -- 2. the first page is the top of the grid, without the text --------
        assert (page["total"], page["limit"], page["offset"]) == (POSTINGS, PAGE, 0)
        assert page["counts"] == listed["runs"][1]["counts"] and page["created_at"] == listed["runs"][1]["created_at"]
        rows = page["payload"]["rows"]
        assert len(rows) == PAGE
        assert all("text" not in row["posting"] for row in rows)
        # The fake model's rank branch scored the run (C1): every row carries its stored score and its rank.
        assert all(row["outcome"] == "new" and isinstance(row["rank"]["score"], int) for row in rows)
        assert all(row["rank_score"] is not None and row["rank_score"]["score"] == row["rank"]["score"] for row in rows)
        assessed = [item["posting"]["normalized_url"] for item in page["payload"]["assessments"]]
        assert len(assessed) == ASSESSED
        # Every assessed posting is on the first page, above the others.
        assert [row["posting"]["normalized_url"] for row in rows[:ASSESSED]] == assessed
        assert all(item["verdict"] == "pending_user_answers" and item["matrix"] for item in page["payload"]["assessments"])
        # 0110-037: the page says why each of its other rows was not assessed. Before, a launched run
        # sealed no reason at all (its assess node looked for the run's config in the wrong folder), and
        # this line pinned that as an empty list. The rows are real: 500 postings and a cap of 50, so the
        # 50 rows under the assessed ones were left out by the cap or as a copy of a posting the run kept.
        reasons = {item["posting"]["normalized_url"]: item["reason"] for item in page["payload"]["not_assessed"]}
        assert list(reasons) == [row["posting"]["normalized_url"] for row in rows[ASSESSED:]]
        assert set(reasons.values()) <= {"over_cap", "duplicate"}
        assert all("text" not in item["posting"] for item in page["payload"]["not_assessed"])
        assert page["carried_forward_assessments"] == []
        assert page["payload"]["status"] == "succeeded" and page["payload"]["failures"] == []

        # One progress entry a posting: the assessed ones, and (0110-037) each of the others with its reason.
        assert len(progress["postings"]) == POSTINGS == len(progress["assessments"])
        assert sum(1 for item in progress["assessments"] if item["status"] == "assessed") == ASSESSED
        assert all("text" not in posting for posting in progress["postings"])
        assert progress["boards"]["source"] == "index" and progress["boards"]["requests"] == 0
        assert "skipped_boards" not in progress["boards"]
        assert progress["not_imported_count"] == 0
        # Each board lists the same two titles five times: two postings a board are kept (100), the
        # other 400 are copies; the cap takes 50 of the 100.
        assert progress["not_assessed_counts"] == {"duplicate": DUPLICATES, "over_cap": OVER_CAP}

        # -- 3. the next pages, and one posting complete -----------------------
        seen = [row["posting"]["normalized_url"] for row in rows]
        for offset in range(PAGE, POSTINGS, PAGE):
            following = _read(
                client, f"/api/runs/{first_run}/results?limit={PAGE}&offset={offset}", bytes_max=PAGE_BYTES_MAX
            )
            assert (following["total"], following["offset"]) == (POSTINGS, offset)
            assert following["payload"]["assessments"] == []
            reasons.update((item["posting"]["normalized_url"], item["reason"]) for item in following["payload"]["not_assessed"])
            seen.extend(row["posting"]["normalized_url"] for row in following["payload"]["rows"])
        assert len(seen) == POSTINGS == len(set(seen)), "the pages cover the run's postings once each"
        # Every row the run did not assess has its reason, on the page that lists the row.
        assert sorted(reasons) == sorted(set(seen) - set(assessed))
        assert sorted(reasons.values()).count("duplicate") == DUPLICATES and sorted(reasons.values()).count("over_cap") == OVER_CAP
        past = client.get(f"/api/runs/{first_run}/results", params={"limit": PAGE, "offset": POSTINGS}).json()
        assert past["payload"]["rows"] == []

        detail = _read(
            client,
            str(httpx.URL(f"/api/runs/{first_run}/posting", params={"url": assessed[0]})),
            bytes_max=SMALL_BYTES_MAX,
        )
        assert detail["row"]["posting"]["normalized_url"] == assessed[0]
        assert len(detail["row"]["posting"]["text"]) > 3000
        assert detail["assessment"]["posting"]["normalized_url"] == assessed[0] and detail["assessment"]["matrix"]
        assert detail["not_assessed_reason"] is None and detail["carried_forward"] is None
        unassessed = _read(
            client,
            str(httpx.URL(f"/api/runs/{first_run}/posting", params={"url": seen[-1]})),
            bytes_max=SMALL_BYTES_MAX,
        )
        assert unassessed["row"]["posting"]["text"] and unassessed["assessment"] is None
        assert unassessed["not_assessed_reason"] == reasons[seen[-1]]

        # -- the second run: unchanged rows; 50 assessments carried forward, 50 new ones
        carried_page = _read(client, f"/api/runs/{second_run}/results?limit={PAGE}", bytes_max=PAGE_BYTES_MAX)
        assert carried_page["total"] == POSTINGS
        carried = [item["normalized_url"] for item in carried_page["carried_forward_assessments"]]
        newly_assessed = [item["posting"]["normalized_url"] for item in carried_page["payload"]["assessments"]]
        assert sorted(carried) == sorted(assessed) and len(newly_assessed) == ASSESSED
        assert not set(carried) & set(newly_assessed)
        # Both kinds have a verdict, so both are the top of the grid: the first page is exactly them.
        assert {row["posting"]["normalized_url"] for row in carried_page["payload"]["rows"]} == set(carried) | set(newly_assessed)
        assert all(row["outcome"] == "unchanged" for row in carried_page["payload"]["rows"])
        # A carried-forward row is "unchanged", as before; a newly assessed one has no reason.
        assert {item["posting"]["normalized_url"]: item["reason"] for item in carried_page["payload"]["not_assessed"]} == dict.fromkeys(carried, "unchanged")
        carried_detail = client.get(f"/api/runs/{second_run}/posting", params={"url": carried[0]}).json()
        assert carried_detail["carried_forward"]["result"]["verdict"] == "pending_user_answers"
        assert carried_detail["assessment"] is None and carried_detail["row"]["posting"]["text"]

        # -- 4. the no-query reads answer what they did -------------------------
        full = client.get(f"/api/runs/{first_run}/results")
        assert full.status_code == 200 and len(full.content) > FULL_READ_BYTES_MIN
        full_rows = full.json()["payload"]["rows"]
        assert len(full_rows) == POSTINGS and all(row["posting"]["text"] for row in full_rows)
        assert sorted(row["posting"]["normalized_url"] for row in full_rows) == sorted(seen)
        full_progress = client.get(f"/api/runs/{first_run}/progress")
        assert full_progress.status_code == 200 and len(full_progress.content) > FULL_READ_BYTES_MIN
        assert all(posting["text"] for posting in full_progress.json()["postings"])

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
