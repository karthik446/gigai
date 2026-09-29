"""uat-bug-021 / SCOPE-ADD-3 C1: a run says what its ranking step did, through the real server.

The lesson of uat-bug-021 stands: drive a real supervised run, not the
ranker in isolation, and fail when ranking silently fails open. Since C1 the
ranker is the run's own model target (here the suite's fake Ollama,
``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``, whose rank branch answers a rank-v1
prompt with valid scores), so the journey pins the SCORED path end to end:

* ``GET /progress`` carries ``rank_status`` (the model ranker, ``scored N of
  N``, no failure reason), the additive ``rank`` (``Ranked N of N``) and
  ``assess_counts`` (``Assessed X of Y``) keys, and every posting's ``rank``
  entry with an integer score;
* the server log carries no ``rank (<run>, acquire)`` warning: the pass did not fail open;
* no Jev: no Jev key, no Jev test seam, and no Jev spend ledger appears in
  the home.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout.find_jobs import bindings
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


def _server_log(home: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((home / "logs").glob("scout-*.log")))


def test_a_run_says_what_its_ranking_step_did(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        config_digest = client.get("/api/config").json()["config_digest"]
        response = client.post("/api/run", json=run_request_body(config_digest))
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        assert poll_until_terminal(client, run_id)["status"] == "succeeded"

        progress = client.get(f"/api/runs/{run_id}/progress").json()
        status = progress["rank_status"]
        total = status["total"]
        assert total >= 1
        # A fail-open pass (scored 0) fails here: the fake model was asked and answered.
        assert (status["status"], status["ranker"], status["scored"], status["ranked"]) == ("scored", "model", total, total)
        assert status["text"] == f"scored {total} of {total}" and status["reason"] is None
        assert status["model_target"] == "ollama_local" and status["pass_status"] == "complete"
        ranked = [item for item in progress["postings"] if item.get("rank") is not None]
        assert len(ranked) == total and all(isinstance(item["rank"]["score"], int) for item in ranked)
        assert {item["rank"]["score"] for item in ranked} == {bindings.TEST_MODEL_RANK_DEFAULT_SCORE}

        # The additive keys: the folded rank.jsonl and "Assessing X of Y", from the server.
        rank = progress["rank"]
        assert (rank["status"], rank["ranked"], rank["scored"], rank["total"]) == ("complete", total, total, total)
        assert rank["text"] == f"Ranked {total:,} of {total:,}" and rank["model_target"] == "ollama_local"
        counts = progress["assess_counts"]
        assert counts["selected"] >= 1 and counts["status"] == "done" and counts["finished"] >= 1
        assert counts["text"] == f"Assessed {counts['finished']} of {counts['selected']}"
        summary = client.get(f"/api/runs/{run_id}/progress", params={"summary": "1"}).json()
        assert summary["rank"] == rank and summary["assess_counts"] == counts

        log = _server_log(home)
        # A complete pass logs at INFO (not kept by the child's log); any WARNING line means it failed open.
        lines = [line for line in log.splitlines() if f"rank ({run_id}, acquire):" in line]
        assert lines == [], lines
        assert "Jev" not in log
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert not (home / "cache" / "scout" / "jev").exists()
    assert_clean_and_healthy(workpad, home)
