"""ui-pass (uat-bug-021 decision a): "Rank with Jev: on/off" and the daily
budget, through the real server.

The real supervisor, a real find-jobs run in its child process, a stored
resume, a Jev key and the fake Jev transport
(``GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1``); no live call. The fake charges for
every call, so every Jev call is a line in the home's spend ledger: an empty
ledger is zero Jev calls.

1. ``GET /api/jev/settings`` answers the defaults (on, $0.50, key set) and
   ``GET /api/jev/usage`` today's usage.
2. ``PUT /api/jev/settings`` saves one setting and keeps the other; a bad
   value or an unknown key is refused and changes nothing.
3. With ranking OFF: a run records ``rank_status`` ``skipped: disabled``
   and asks Jev nothing; a "Score with Jev" click (``POST /rank {"start":
   true}``) starts no pass. Turned back ON, the same click scores every
   posting (so the fake Jev was there to be asked all along).
4. A preferences save (``PUT /api/setup``, what the wizard sends) leaves
   both settings as they were: they are not preferences.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gigai.scout.find_jobs.jev_rank import RankStatus
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

JEV_CALL_USD = 0.0005  # bindings._test_jev_handler

_SETUP_BODY = {
    "roles": ["software engineer"],
    "titles_to_avoid": [],
    "countries": ["US"],
    "work_mode": "remote",
    "city": "Denver, CO",
    "visa_sponsorship_required": False,
    "exclude_companies": [],
    "watch_companies": [],
    "company_stage_size": None,
    "industries_include": [],
    "industries_exclude": [],
    "must_have_stack": [],
    "dealbreaker_stack": [],
    "cadence_days": 7,
    "budget_usd_per_session": 0.50,
}


def _run(client) -> str:
    config_digest = client.get("/api/config").json()["config_digest"]
    response = client.post("/api/run", json=run_request_body(config_digest))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    status = poll_until_terminal(client, run_id)
    assert status["status"] == "succeeded", status
    return run_id


def _ledger(home: Path) -> list[dict]:
    lines: list[dict] = []
    for path in sorted((home / "cache" / "scout" / "jev" / "spend").glob("*.jsonl")):
        lines.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return lines


def _server_log(home: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((home / "logs").glob("scout-*.log")))


def test_rank_with_jev_off_asks_jev_nothing_and_the_settings_survive_a_preferences_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIGAI_JEV_COST_CAP_USD", raising=False)
    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch, test_jev=True)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)

        # -- 1. the defaults, and today's usage ---------------------------------
        settings, settings_latency = timed_request("GET /api/jev/settings", lambda: client.get("/api/jev/settings"))
        assert settings.status_code == 200, settings.text
        settings_latency.assert_within_budget()
        body = settings.json()
        assert {key: body[key] for key in ("jev_daily_budget_usd", "jev_rank_enabled", "daily_budget_env", "has_key")} == {
            "jev_daily_budget_usd": 0.5,
            "jev_rank_enabled": True,
            "daily_budget_env": None,
            "has_key": True,
        }
        usage, usage_latency = timed_request("GET /api/jev/usage", lambda: client.get("/api/jev/usage"))
        assert usage.status_code == 200, usage.text
        usage_latency.assert_within_budget()
        assert usage.json()["spent_today_usd"] == "0.000000" and usage.json()["daily_budget_usd"] == "0.50"
        assert usage.json()["line"] == "Jev: $0.00 of $0.50 today"
        assert body["usage"] == usage.json()
        assert not (home / "local" / "scout" / "jev-settings.json").exists(), "a read wrote the settings"

        # -- 2. each save keeps the other setting; bad input changes nothing ----
        off, off_latency = timed_request(
            "PUT /api/jev/settings", lambda: client.put("/api/jev/settings", json={"jev_rank_enabled": False})
        )
        assert off.status_code == 200, off.text
        off_latency.assert_within_budget()
        assert off.json()["jev_rank_enabled"] is False and off.json()["jev_daily_budget_usd"] == 0.5
        budget = client.put("/api/jev/settings", json={"jev_daily_budget_usd": 0.3})
        assert budget.status_code == 200, budget.text
        assert budget.json()["jev_rank_enabled"] is False and budget.json()["jev_daily_budget_usd"] == 0.3
        assert budget.json()["usage"]["daily_budget_usd"] == "0.30"
        for bad, code in (
            ({"jev_daily_budget_usd": -1}, "invalid_value"),
            ({"jev_daily_budget_usd": "0.10"}, "invalid_value"),
            ({"jev_rank_enabled": "no"}, "wrong_type"),
            ({"jev_rank_enabled": True, "cost_cap_usd": 1}, "unknown_field"),
        ):
            refused = client.put("/api/jev/settings", json=bad)
            assert refused.status_code == 422, (bad, refused.text)
            assert refused.json()["error"]["code"] == code, refused.text
        stored = json.loads((home / "local" / "scout" / "jev-settings.json").read_text(encoding="utf-8"))
        assert stored == {"schema_version": "scout-jev-settings:1", "jev_daily_budget_usd": 0.3, "jev_rank_enabled": False}

        # -- 3. ranking off: the run and a click ask Jev nothing ------------------
        run_id = _run(client)
        progress = client.get(f"/api/runs/{run_id}/progress").json()
        postings = len(progress["postings"])
        assert postings >= 1
        assert progress["rank_status"] == RankStatus("skipped", 0, postings, "disabled", None, 0.0, None, 0.0, 0.3).to_json()
        assert progress["rank_status"]["text"] == "skipped: disabled"
        sealed = json.loads((workpad / "runs" / run_id / "outputs" / "acquire.json").read_text(encoding="utf-8"))
        assert "rank_scores" not in sealed
        clicked = client.post(f"/api/runs/{run_id}/rank", json={"start": True})
        assert clicked.status_code == 200, clicked.text
        assert clicked.json()["rank_status"]["status"] == "skipped" and clicked.json()["rank_status"]["reason"] == "disabled"
        assert [item["score"] for item in clicked.json()["scores"]] == [None] * postings
        time.sleep(0.5)
        read = client.post(f"/api/runs/{run_id}/rank", json={}).json()
        assert read["rank_status"]["reason"] == "disabled" and read["rank_status"]["status"] == "skipped"
        assert _ledger(home) == [], "Jev was asked with Rank with Jev off"
        log = _server_log(home)
        assert f"scout acquire: Jev: skipped (" in log and f"[run_id={run_id} reason=disabled " in log

        # -- ... and ON again: the same click scores every posting ---------------
        on = client.put("/api/jev/settings", json={"jev_rank_enabled": True})
        assert on.status_code == 200 and on.json()["jev_rank_enabled"] is True and on.json()["jev_daily_budget_usd"] == 0.3
        answer = client.post(f"/api/runs/{run_id}/rank", json={"start": True}).json()
        deadline = time.monotonic() + 30.0
        while answer["rank_status"]["status"] == "running":
            assert time.monotonic() < deadline, answer
            time.sleep(0.05)
            answer = client.post(f"/api/runs/{run_id}/rank", json={}).json()
        assert [item["score"] for item in answer["scores"]] == [89] * postings
        assert [(line["where"], line["run_id"]) for line in _ledger(home)] == [("rank", run_id)] * postings
        assert answer["usage"]["daily_budget_usd"] == "0.30"
        assert answer["usage"]["spent_today_usd"] == f"{postings * JEV_CALL_USD:.6f}"

        # -- 4. a preferences save is not a Jev settings save ---------------------
        client.put("/api/jev/settings", json={"jev_rank_enabled": False})
        saved = client.put("/api/setup", json=_SETUP_BODY)
        assert saved.status_code == 200, saved.text
        after = client.get("/api/jev/settings").json()
        assert after["jev_rank_enabled"] is False and after["jev_daily_budget_usd"] == 0.3
        assert "jev_daily_budget_usd" not in saved.json()["prefs"] and "jev_rank_enabled" not in saved.json()["prefs"]
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_the_environment_budget_wins_over_the_stored_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_JEV_DAILY_BUDGET_USD", "0.7")
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        saved = client.put("/api/jev/settings", json={"jev_daily_budget_usd": 0.2})
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["jev_daily_budget_usd"] == 0.2 and body["daily_budget_env"] == "0.70"
        assert body["usage"]["daily_budget_usd"] == "0.70" and body["has_key"] is False
        assert client.get("/api/jev/usage").json()["line"] == "Jev: $0.00 of $0.70 today"
    finally:
        stop_server(server)
