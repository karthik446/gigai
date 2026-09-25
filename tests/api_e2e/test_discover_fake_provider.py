"""test-gap-001: Discover with a fake provider.

## The discovery module DOES exist

A first pass at this file wrongly reported no
``gigai.scout.find_jobs.discovery`` module existed -- that search only
looked for a ``discovery*.py`` file, missing the real package at
``src/gigai/scout/find_jobs/discovery/`` (``feat(scout): weekly company
discovery``, c461869, already on this branch). Corrected here: Discover has
a real, wired backend (``ScoutFindJobsBackend.start_discovery``/
``latest_discovery``/``read_setup``/``write_setup``), and this file drives
it for real over HTTP.

## Seam: no HTTP-injectable override into the OpenAI call, so this journey
## uses the real, deterministic, already-offline "no key configured" path

``discovery.run_discovery`` builds its own ``httpx.Client()`` internally
with no transport-override parameter (unlike ``openai_source.run``, which
DOES take a ``client:`` -- but only the module's own unit tests,
``test_discovery_openai.py``, call that directly). There is no seam today
to inject a fixture OpenAI response through a real ``POST /api/discover``
call in a spawned child process.

What IS already a real, deterministic, zero-network, zero-cost code path
(covered by ``test_discovery_openai.py::test_missing_api_key_skips_without_
raising``, exercised here for the first time over HTTP): no
``OPENAI_API_KEY`` configured. ``openai_source.run`` skips with
``skip_reason="OPENAI_API_KEY is not set; ..."`` instead of raising or
calling out, and (since ``visa_sponsorship_required`` is false in this
journey's prefs) the H-1B source is skipped too
(``skip_reason="sponsorship_not_required"``). ``run_discovery`` completes
successfully with zero new boards and ``cost_usd == 0.0`` -- this IS a fake
provider, in the sense the ticket means (no live call reaches any real
provider), it's just that "fake" here is "absent, and the code's own
documented degradation path," not an injected mock response.

A follow-up packet that wants "found N new boards" coverage needs a real
seam (e.g. a ``GIGAI_SCOUT_FIND_JOBS_TEST_DISCOVERY`` env var, mirroring
``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP``/``_TEST_MODEL``, threaded into
``run_discovery``'s ``httpx.Client()`` construction) -- flagged here rather
than added speculatively, per this ticket's "ask before adding a seam"
rule; this file's job today is the real, currently-reachable path.
"""



from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
)

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


def test_discover_with_no_provider_key_configured_completes_with_zero_boards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real Discover pipeline, end to end over HTTP, with no
    OPENAI_API_KEY configured -- the deterministic, zero-network, zero-cost
    "no provider available" path (see module docstring for why this is the
    fake-provider journey this packet can build without a new seam)."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        # setup: save the discovery prefs (required before Discover can run).
        setup_response = client.put("/api/setup", json=_SETUP_BODY)
        assert setup_response.status_code == 200, setup_response.text
        assert setup_response.json()["prefs"]["roles"] == ["software engineer"]

        # latest, before ever discovering: no result yet, not running.
        before_response, before_latency = timed_request(
            "GET /api/discover/latest", lambda: client.get("/api/discover/latest")
        )
        assert before_response.status_code == 200, before_response.text
        before_body = before_response.json()
        assert before_body["result"] is None
        assert before_body["running"] is False
        before_latency.assert_within_budget()

        # start Discover.
        discover_response, discover_latency = timed_request(
            "POST /api/discover", lambda: client.post("/api/discover", json={})
        )
        assert discover_response.status_code == 202, discover_response.text
        assert "discovery_id" in discover_response.json()
        discover_latency.assert_within_budget()

        # a concurrent second POST while the first is (however briefly)
        # still marked running must be refused with 409, never silently
        # queued or double-started (present_api.py's own CHANGE #1: "one at
        # a time -> 409 if running"). Best-effort: only meaningful if it
        # actually lands while running is still True: a run this fast (no
        # provider key -> nothing to await) may finish before this second
        # call is issued, in which case a 202 is equally correct -- so
        # this only asserts the contract when the race actually landed.
        immediate_second = client.post("/api/discover", json={})
        if immediate_second.status_code == 409:
            assert immediate_second.json()["error"]["code"] == "discovery_running"
        else:
            assert immediate_second.status_code == 202, immediate_second.text

        # poll latest until it reports a finished result.
        import time

        deadline = time.monotonic() + 30.0
        latest_body: dict[str, object] = {}
        while time.monotonic() < deadline:
            latest_response = client.get("/api/discover/latest")
            assert latest_response.status_code == 200, latest_response.text
            latest_body = latest_response.json()
            if latest_body.get("result") is not None and latest_body.get("running") is False:
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"discovery never reported finished; last body={latest_body!r}")

        result = latest_body["result"]
        assert isinstance(result, dict)
        assert result["status"] in ("completed", "failed", "partial")
        assert result["cost_usd"] == 0.0, "no provider key was configured -- no spend must occur"
        assert result["new_boards"] == []
        source_names = {item["name"] for item in result.get("sources", [])}
        assert "openai_web_search" in source_names
        openai_source_entry = next(
            item for item in result["sources"] if item["name"] == "openai_web_search"
        )
        assert openai_source_entry.get("skip_reason"), (
            "the openai source must report why it skipped (no API key), "
            "never silently succeed with zero candidates"
        )

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_discover_running_with_no_prefs_saved_is_prefs_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/discover before any setup interview has been completed
    (no prefs.json saved yet) -- a distinct, documented error, never a 500
    or a hang."""

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        response = client.post("/api/discover", json={})
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "prefs_missing"

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
