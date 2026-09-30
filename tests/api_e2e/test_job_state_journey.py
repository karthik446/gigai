"""uat-bug-018: a job's derived state, over HTTP against the real server.

``job_state`` ``{state, since, next_events}`` is additive on the three
responses the UI already reads: ``GET /api/runs/{id}/results`` rows, ``GET
/api/assessments`` items and ``GET /api/applications`` rows. Nothing is
stored for it; these journeys move one job through every source the state
is derived from and read it back after each step:

1. a run posting: the run's verdict -> the quick store's newer verdict ->
   a tailored resume -> applied -> interview -> offer -> withdrawn, with
   the refused transitions in between (409) and the pipeline's end;
2. a posting whose text was pasted (identity ``text:sha256:<hex>``): it
   reaches the pipeline through ``job_identity``, which ``normalized_url``
   still refuses; a ``saved`` event is never a state.

Same fixtures as the job-page journey: the fixture model answers pending
with a ``cloud:gcp`` question until that answer reaches the prompt.
"""

from __future__ import annotations

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

_PIPELINE = ["interview_scheduled", "offer_received", "rejected", "withdrawn"]
_PASTED_POSTING = (
    "Acme is hiring a Software Engineer to build reliable Python services. "
    "Requirements: Python in production; GCP experience is a plus. Remote within the US."
)


def _assert_state_shape(value: object) -> None:
    assert isinstance(value, dict) and set(value) == {"state", "since", "next_events"}, value
    assert isinstance(value["state"], str) and isinstance(value["next_events"], list)
    assert value["since"] is None or isinstance(value["since"], str)


def _row_state(client: httpx.Client, run_id: str, job_identity: str) -> dict[str, object]:
    response = client.get(f"/api/runs/{run_id}/results")
    assert response.status_code == 200, response.text
    rows = response.json()["payload"]["rows"]
    for row in rows:
        _assert_state_shape(row["job_state"])
    return next(row["job_state"] for row in rows if row["posting"]["normalized_url"] == job_identity)


def _item_state(client: httpx.Client, job_identity: str) -> dict[str, object]:
    response = client.get("/api/assessments")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    for item in items:
        _assert_state_shape(item["job_state"])
    return next(item["job_state"] for item in items if item["job"]["job_identity"] == job_identity)


def _application_rows(client: httpx.Client, job_identity: str) -> list[dict[str, object]]:
    response = client.get("/api/applications")
    assert response.status_code == 200, response.text
    return [row for row in response.json()["applications"] if row.get("external_ref") == job_identity]


def _assert_refused(response: httpx.Response, *, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body["error"]["message"]


def test_a_run_posting_moves_through_every_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        config_digest = client.get("/api/config").json()["config_digest"]
        run_response = client.post("/api/run", json=run_request_body(config_digest))
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["run_id"]
        assert poll_until_terminal(client, run_id)["status"] == "succeeded"

        results, results_latency = timed_request("GET /api/runs/{id}/results", lambda: client.get(f"/api/runs/{run_id}/results"))
        assert results.status_code == 200, results.text
        payload = results.json()["payload"]
        run_assessment = payload["assessments"][0]
        assert run_assessment["verdict"] == "pending_user_answers"
        job_identity = run_assessment["posting"]["normalized_url"]
        run_created_at = next(run["created_at"] for run in client.get("/api/runs").json()["runs"] if run["run_id"] == run_id)
        results_latency.assert_within_budget()

        # -- the run's own verdict; a row the run did not assess is not assessed
        assert _row_state(client, run_id, job_identity) == {"state": "needs_answers", "since": run_created_at, "next_events": ["applied"]}
        assessed = {item["posting"]["normalized_url"] for item in payload["assessments"]}
        for row in payload["rows"]:
            if row["posting"]["normalized_url"] not in assessed:
                assert row["job_state"] == {"state": "not_assessed", "since": None, "next_events": ["applied"]}

        # -- nothing may follow a job that was never applied to ----------------
        for event_kind in _PIPELINE:
            message = _assert_refused(
                client.post("/api/applications", json={"normalized_url": job_identity, "event_kind": event_kind}),
                status=409,
                code="application_transition_refused",
            )
            assert "accepted next: applied" in message
        assert _application_rows(client, job_identity) == []

        # -- a newer assessment in the quick store decides the verdict ---------
        answered = client.post(
            "/api/answers",
            json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": {"job_identity": job_identity}},
        )
        assert answered.status_code == 201, answered.text
        reassessed = answered.json()["reassessed"]
        assert reassessed["result"]["verdict"] == "matched_above_threshold"
        matched = {"state": "matched", "since": reassessed["created_at"], "next_events": ["applied"]}
        assert _row_state(client, run_id, job_identity) == matched
        assert _item_state(client, job_identity) == matched

        # ... and ``since`` stays at the first time the verdict was given.
        again = client.post("/api/assess", json={"job": {"job_url": run_assessment["posting"]["url"]}})
        assert again.status_code == 200, again.text
        assert again.json()["result"]["verdict"] == "matched_above_threshold"
        assert [entry["trigger"] for entry in again.json()["history"]] == ["answer:cloud:gcp", "reassess"]
        assert _item_state(client, job_identity) == matched
        assert _row_state(client, run_id, job_identity) == matched

        # -- a tailored resume wins over the verdict ---------------------------
        tailored = client.post("/api/tailored-resumes", json={"job": {"job_url": run_assessment["posting"]["url"]}})
        assert tailored.status_code == 200, tailored.text
        assert tailored.json()["job"]["job_identity"] == job_identity
        tailored_state = {"state": "tailored", "since": tailored.json()["created_at"], "next_events": ["applied"]}
        assert _row_state(client, run_id, job_identity) == tailored_state
        assert _item_state(client, job_identity) == tailored_state

        # -- applied: application events win over both -------------------------
        _assert_refused(
            client.post(
                "/api/applications",
                json={"normalized_url": job_identity, "job_identity": job_identity, "event_kind": "applied"},
            ),
            status=422,
            code="invalid_value",
        )
        applied, applied_latency = timed_request(
            "POST /api/applications (applied)",
            lambda: client.post(
                "/api/applications",
                json={"job_identity": job_identity + "/?utm_source=job-page", "event_kind": "applied", "occurred_at": "2026-09-01T10:00:00Z"},
            ),
        )
        assert applied.status_code == 201, applied.text
        assert applied.json()["event"]["external_ref"] == job_identity  # a URL identity is normalized
        applied_state = {"state": "applied", "since": "2026-09-01T10:00:00Z", "next_events": _PIPELINE}
        assert applied.json()["job_state"] == applied_state
        applied_latency.assert_within_budget()
        assert _row_state(client, run_id, job_identity) == applied_state
        assert _item_state(client, job_identity) == applied_state
        rows = _application_rows(client, job_identity)
        assert [row["job_state"] for row in rows] == [applied_state]
        assert rows[0]["linked_posting"]["normalized_url"] == job_identity

        # -- refused: applied twice, saved after applied, an earlier date ------
        for event_kind in ("applied", "saved"):
            _assert_refused(
                client.post("/api/applications", json={"normalized_url": job_identity, "event_kind": event_kind}),
                status=409,
                code="application_transition_refused",
            )
        _assert_refused(
            client.post(
                "/api/applications",
                json={"normalized_url": job_identity, "event_kind": "interview_scheduled", "occurred_at": "2026-08-31T10:00:00Z"},
            ),
            status=409,
            code="application_event_out_of_order",
        )
        assert len(_application_rows(client, job_identity)) == 1  # a refused event records nothing

        # -- interview -> offer -> withdrawn -----------------------------------
        steps = [
            ("interview_scheduled", "2026-09-05T10:00:00Z", ["offer_received", "rejected", "withdrawn"]),
            ("offer_received", "2026-09-10T10:00:00Z", ["rejected", "withdrawn"]),
            ("withdrawn", "2026-09-12T10:00:00Z", []),
        ]
        for event_kind, occurred_at, following in steps:
            response = client.post(
                "/api/applications",
                json={"normalized_url": job_identity, "event_kind": event_kind, "occurred_at": occurred_at},
            )
            assert response.status_code == 201, response.text
            expected = {"state": event_kind, "since": occurred_at, "next_events": following}
            assert response.json()["job_state"] == expected
            assert _row_state(client, run_id, job_identity) == expected
            if event_kind == "interview_scheduled":
                # No second interview, and no going back to applied.
                for refused in ("interview_scheduled", "applied"):
                    _assert_refused(
                        client.post("/api/applications", json={"normalized_url": job_identity, "event_kind": refused}),
                        status=409,
                        code="application_transition_refused",
                    )

        # -- withdrawn ends the pipeline ---------------------------------------
        withdrawn = {"state": "withdrawn", "since": "2026-09-12T10:00:00Z", "next_events": []}
        for event_kind in ("applied", *_PIPELINE, "saved"):
            message = _assert_refused(
                client.post("/api/applications", json={"normalized_url": job_identity, "event_kind": event_kind}),
                status=409,
                code="application_transition_refused",
            )
            assert "withdrawn" in message
        rows = _application_rows(client, job_identity)
        assert [row["event_kind"] for row in rows] == ["applied", "interview_scheduled", "offer_received", "withdrawn"]
        assert [row["job_state"] for row in rows] == [withdrawn] * 4  # every row of a job says the same
        assert _item_state(client, job_identity) == withdrawn

        # An unknown event kind is still a 422, never a 409.
        _assert_refused(
            client.post("/api/applications", json={"normalized_url": job_identity, "event_kind": "accepted"}),
            status=422,
            code="invalid_value",
        )

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_a_pasted_job_reaches_the_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        assessed = client.post("/api/assess", json={"job": {"job_text": _PASTED_POSTING, "title": "Software Engineer", "company": "Acme"}})
        assert assessed.status_code == 200, assessed.text
        body = assessed.json()
        job_identity = body["job"]["job_identity"]
        assert job_identity.startswith("text:sha256:") and body["job"]["normalized_url"] is None
        assert body["result"]["verdict"] == "pending_user_answers"
        assert _item_state(client, job_identity) == {"state": "needs_answers", "since": body["created_at"], "next_events": ["applied"]}

        # ``normalized_url`` is a URL; a pasted job is named by ``job_identity``.
        _assert_refused(
            client.post("/api/applications", json={"normalized_url": job_identity, "event_kind": "applied"}),
            status=422,
            code="invalid_value",
        )
        for malformed in (job_identity[:-1], job_identity.upper(), "text:md5:abc", "not a url", ""):
            _assert_refused(
                client.post("/api/applications", json={"job_identity": malformed, "event_kind": "applied"}),
                status=422,
                code="invalid_value",
            )
        _assert_refused(
            client.post("/api/applications", json={"job_identity": job_identity, "event_kind": "interview_scheduled"}),
            status=409,
            code="application_transition_refused",
        )

        applied = client.post(
            "/api/applications",
            json={"job_identity": job_identity, "event_kind": "applied", "occurred_at": "2026-09-01T10:00:00Z"},
        )
        assert applied.status_code == 201, applied.text
        assert applied.json()["event"]["external_ref"] == job_identity  # recorded verbatim
        assert "opportunity_ref" not in applied.json()["event"]
        applied_state = {"state": "applied", "since": "2026-09-01T10:00:00Z", "next_events": _PIPELINE}
        assert applied.json()["job_state"] == applied_state
        assert _item_state(client, job_identity) == applied_state

        rejected = client.post(
            "/api/applications",
            json={"job_identity": job_identity, "event_kind": "rejected", "occurred_at": "2026-09-08T10:00:00Z"},
        )
        assert rejected.status_code == 201, rejected.text
        rejected_state = {"state": "rejected", "since": "2026-09-08T10:00:00Z", "next_events": []}
        assert _item_state(client, job_identity) == rejected_state
        rows = _application_rows(client, job_identity)
        assert [row["job_state"] for row in rows] == [rejected_state, rejected_state]
        assert all(row["linked_posting"] is None for row in rows)  # no run posting has this identity

        # -- a saved job has no application state; its events are still listed
        saved_url = "https://boards.greenhouse.io/nowhere/jobs/999999"
        for _ in range(2):  # saving twice is fine: it changes nothing
            saved = client.post("/api/applications", json={"normalized_url": saved_url, "event_kind": "saved"})
            assert saved.status_code == 201, saved.text
            assert saved.json()["job_state"] is None
        rows = _application_rows(client, saved_url)
        assert [row["event_kind"] for row in rows] == ["saved", "saved"]
        assert [row["job_state"] for row in rows] == [None, None]
        # ... and it can still be applied to.
        then_applied = client.post("/api/applications", json={"normalized_url": saved_url, "event_kind": "applied"})
        assert then_applied.status_code == 201, then_applied.text
        assert then_applied.json()["job_state"]["state"] == "applied"
        assert {row["job_state"]["state"] for row in _application_rows(client, saved_url)} == {"applied"}

        # One job's events never change another's state.
        assert _item_state(client, job_identity) == rejected_state

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
