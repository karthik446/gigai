"""Ledger 32: ``job_state.assessment_stale`` over HTTP against the real server.

A stored quick assessment of a posting read from an ATS board records the text
it was made on. When the run's row for that posting now hashes differently,
``job_state`` carries ``assessment_stale: {reason: "posting_changed"}`` on the
run results rows, the run posting route and ``GET /api/jobs?url=``; when the
text is the same, or the stored record cannot say (a record from before the
text was stored, or a posting not read from a board), the field is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout.quick_assess import quick_assess_path
from tests.api_e2e.harness import (
    add_resume,
    poll_until_terminal,
    run_request_body,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)


def _states(client: httpx.Client, run_id: str, identity: str) -> dict[str, dict[str, object]]:
    rows = client.get(f"/api/runs/{run_id}/results").json()["payload"]["rows"]
    results = next(row["job_state"] for row in rows if row["posting"]["normalized_url"] == identity)
    posting = client.get(f"/api/runs/{run_id}/posting", params={"url": identity})
    assert posting.status_code == 200, posting.text
    job = client.get("/api/jobs", params={"url": identity})
    assert job.status_code == 200, job.text
    return {"results": results, "posting": posting.json()["row"]["job_state"], "job": job.json()["job_state"]}


def test_a_changed_posting_marks_the_stored_assessment_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        config_digest = client.get("/api/config").json()["config_digest"]
        run_id = client.post("/api/run", json=run_request_body(config_digest)).json()["run_id"]
        assert poll_until_terminal(client, run_id)["status"] == "succeeded"
        results = client.get(f"/api/runs/{run_id}/results").json()["payload"]
        identity = results["assessments"][0]["posting"]["normalized_url"]
        answered = client.post(
            "/api/answers",
            json={"question_id": "cloud:gcp", "answer": "Yes, two years on GCP.", "reassess": {"job_identity": identity}},
        )
        assert answered.status_code == 201, answered.text
        item = answered.json()["reassessed"]
        assert item["result"]["verdict"] == "matched_above_threshold"
        # A fresh assessment carries no marker: its run row is the text it was made on or the record cannot say.
        for state in _states(client, run_id, identity).values():
            assert state["state"] == "matched" and "assessment_stale" not in state

        row = client.get(f"/api/runs/{run_id}/posting", params={"url": identity}).json()["row"]["posting"]
        path = quick_assess_path(home, target, item["resume"].get("profile_id"), identity)
        stored = json.loads(path.read_text(encoding="utf-8"))

        def store(*, fetch_kind: str, title: str, text: str | None) -> None:
            edited = json.loads(json.dumps(stored))
            edited["job"]["fetch_kind"] = fetch_kind
            edited["job"]["title"] = title
            if text is None:
                edited.pop("posting_text", None)
            else:
                edited["posting_text"] = text
            path.write_text(json.dumps(edited), encoding="utf-8")

        # The text the assessment was made on differs from the run row's now.
        store(fetch_kind="ats_board", title=row["title"], text="An earlier, shorter version of this posting.")
        for state in _states(client, run_id, identity).values():
            assert state["state"] == "matched"
            assert state["assessment_stale"] == {"reason": "posting_changed"}

        # Same title and text as the row hashes to the row's own digest: no marker.
        same = "\n".join(part for part in (row["title"], row["text"]) if part)
        assert digest_imported_bytes(same.encode("utf-8")) == row["content_sha256"], "fixture rows hash like ATS board rows"
        store(fetch_kind="ats_board", title=row["title"], text=row["text"])
        for state in _states(client, run_id, identity).values():
            assert "assessment_stale" not in state

        # A record that stored no text (older), or a posting not read from a board, cannot be compared.
        store(fetch_kind="ats_board", title=row["title"], text=None)
        for state in _states(client, run_id, identity).values():
            assert "assessment_stale" not in state
        store(fetch_kind="generic", title=row["title"], text="An earlier, shorter version of this posting.")
        for state in _states(client, run_id, identity).values():
            assert "assessment_stale" not in state
    finally:
        stop_server(server)
