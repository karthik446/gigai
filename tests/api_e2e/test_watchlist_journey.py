"""Q1 (v0.1.9, SCOPE-ADD-2): the "Add company" + rolling-window journey.

Through the real supervised server (``run_supervisor.start``), like every
journey in this suite:

1. ``GET /api/watchlist`` is empty on a fresh gig.
2. ``POST /api/watchlist`` with an Ashby JOB URL adds the ``kong`` board
   (201); the same board's bare URL again is 200 ``created: false``; a
   foreign host is 422; a foreign ``Origin`` is 403 before the body runs.
3. A real find-jobs run with ``max_age_days`` set in find-jobs.json still
   succeeds and its acquire polls the added board (the fixture transport
   has no ``kong`` board, so that poll is recorded as a per-board failure,
   never a run failure) while the fixture's ``acme`` posting (2026-09-22)
   is inside the window.
4. ``PUT /api/setup`` carrying ``max_age_days`` writes the rolling window
   into find-jobs.json (``GET /api/config`` shows it, ``published_after``
   cleared); a later save WITHOUT it keeps the value (the silent-drop case).

Order matters: the run comes BEFORE any prefs save. Q2's acquire seeds the
watchlist from the company catalog once setup preferences exist, and a run
over hundreds of fixture-404 boards would not fit the poll deadline -- the
window/run proof here needs only the hand-written config field.
"""

from __future__ import annotations

import json
from pathlib import Path

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

KONG_JOB = "https://jobs.ashbyhq.com/kong/ea7b507b-1111-2222-3333-444455556666"


def _setup_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "roles": ["software engineer"],
        "titles_to_avoid": [],
        "countries": ["US"],
        "work_mode": "remote",
        "city": None,
        "visa_sponsorship_required": False,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.5,
    }
    body.update(overrides)
    return body


def test_add_company_then_window_then_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # -- 1. empty watchlist --------------------------------------------
        empty_response, empty_latency = timed_request("GET /api/watchlist", lambda: client.get("/api/watchlist"))
        assert empty_response.status_code == 200, empty_response.text
        assert empty_response.json() == {"schema_version": "scout-watchlist-response:1", "entries": []}
        empty_latency.assert_within_budget()

        # -- 2. add company by a JOB url; idempotent; typed refusals --------
        bad_origin = client.post("/api/watchlist", json={"url": KONG_JOB}, headers={"Origin": "http://evil.example.com"})
        assert bad_origin.status_code == 403, bad_origin.text
        assert bad_origin.json()["error"]["code"] == "forbidden_origin"

        other_host = client.post("/api/watchlist", json={"url": "https://example.com/careers"})
        assert other_host.status_code == 422, other_host.text
        assert other_host.json()["error"]["code"] == "unsupported_board_host"

        add_response, add_latency = timed_request("POST /api/watchlist", lambda: client.post("/api/watchlist", json={"url": KONG_JOB}))
        assert add_response.status_code == 201, add_response.text
        added = add_response.json()
        assert added["schema_version"] == "scout-watchlist-add-response:1"
        assert added["created"] is True
        assert added["entry"]["provider"] == "ashby"
        assert added["entry"]["board_token"] == "kong"
        assert added["entry"]["company"] == "kong"
        add_latency.assert_within_budget()

        again = client.post("/api/watchlist", json={"url": "https://jobs.ashbyhq.com/kong"})
        assert again.status_code == 200, again.text
        assert again.json()["created"] is False
        assert again.json()["entry"] == added["entry"]

        listed = client.get("/api/watchlist").json()
        assert [item["watchlist_id"] for item in listed["entries"]] == ["scout_watchlist:ashby:kong"]

        # -- 3. a real run with the rolling window set ----------------------
        config_path = target / "find-jobs.json"
        windowed = json.loads(config_path.read_text(encoding="utf-8"))
        windowed["max_age_days"] = 45
        windowed["published_after"] = None
        config_path.write_text(json.dumps(windowed), encoding="utf-8")
        config_before_run = client.get("/api/config").json()
        assert config_before_run["config"]["max_age_days"] == 45
        run_response = client.post("/api/run", json=run_request_body(config_before_run["config_digest"]))
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["run_id"]
        status_body = poll_until_terminal(client, run_id)
        assert status_body["status"] == "succeeded", status_body
        results = client.get(f"/api/runs/{run_id}/results").json()
        rows = results["payload"]["rows"]
        assert rows, "the fixture's acme posting (2026-09-22) must be inside a 45-day window"
        assert all(row["posting"]["board_token"] != "kong" for row in rows)  # no fixture board for kong

        # -- 4. the wizard's save writes the rolling window -----------------
        fixed = json.loads(config_path.read_text(encoding="utf-8"))
        fixed["published_after"] = "2026-09-10"
        fixed.pop("max_age_days", None)
        config_path.write_text(json.dumps(fixed), encoding="utf-8")

        saved = client.put("/api/setup", json=_setup_body(max_age_days=45))
        assert saved.status_code == 200, saved.text
        assert "max_age_days" not in saved.json()["prefs"]  # never a discovery pref
        config_after = client.get("/api/config").json()["config"]
        assert config_after["max_age_days"] == 45
        assert config_after["published_after"] is None

        bad_window = client.put("/api/setup", json=_setup_body(max_age_days=0))
        assert bad_window.status_code == 400, bad_window.text
        assert "max_age_days" in bad_window.json()["error"]["field_errors"]

        # A Preferences save that doesn't mention the window keeps it.
        kept = client.put("/api/setup", json=_setup_body())
        assert kept.status_code == 200, kept.text
        assert client.get("/api/config").json()["config"]["max_age_days"] == 45

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
