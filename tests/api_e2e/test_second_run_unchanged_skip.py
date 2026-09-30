"""test-gap-001: a second run against the same config skips unchanged rows
and carries forward their earlier verdict (uat-bug-009's fix, 5b0029e).

Before 5b0029e, a posting acquire marked UNCHANGED that had never been
successfully assessed (e.g. its first attempt failed) was silently dropped:
never sent to assess, never shown in ``not_assessed`` either -- it just
vanished from the operator's results. The fix (``bindings._assess_bound``'s
reconciliation, and ``AcquireOutput.carried_forward_assessments``) makes any
UNCHANGED, already-successfully-assessed row show up in this run's results
via ``carried_forward_assessments`` -- additive to (never inside) the sealed
assessed/not-assessed partition (see present_api.py's own comment at the
``GET /api/runs/{id}/results`` handler). This journey proves that end to end
over HTTP: run twice against the identical config with no target-side
change, and assert the second run's row is ``unchanged``, never re-sent to
assess (the model is not re-invoked for it), and shows up in
``carried_forward_assessments`` with its earlier matrix/suggestions intact.
"""

from __future__ import annotations

from pathlib import Path

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


def test_second_run_carries_forward_the_unchanged_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        config_digest = client.get("/api/config").json()["config_digest"]

        first_response = client.post("/api/run", json=run_request_body(config_digest))
        assert first_response.status_code == 202, first_response.text
        first_run_id = first_response.json()["run_id"]
        first_status = poll_until_terminal(client, first_run_id)
        assert first_status["status"] == "succeeded", first_status

        first_payload = client.get(f"/api/runs/{first_run_id}/results").json()["payload"]
        assert first_payload["rows"], "the first run must have found the fixture posting"
        assert first_payload["assessments"], "the first run must have assessed it"
        first_normalized_url = first_payload["rows"][0]["posting"]["normalized_url"]
        first_matrix = first_payload["assessments"][0]["matrix"]
        first_suggestions = first_payload["assessments"][0]["suggestions"]

        # A second run, same config, nothing changed on the fixture side.
        second_response = client.post("/api/run", json=run_request_body(config_digest))
        assert second_response.status_code == 202, second_response.text
        second_run_id = second_response.json()["run_id"]
        second_status = poll_until_terminal(client, second_run_id)
        assert second_status["status"] == "succeeded", second_status

        second_results = client.get(f"/api/runs/{second_run_id}/results").json()
        second_payload = second_results["payload"]

        # The row itself: same posting, "unchanged" outcome, not re-sent to
        # assess (present in rows, but NOT in this run's own `assessments`
        # -- it was carried forward, not freshly assessed).
        assert len(second_payload["rows"]) == 1
        second_row = second_payload["rows"][0]
        assert second_row["posting"]["normalized_url"] == first_normalized_url
        assert second_row["outcome"] == "unchanged"
        assert not second_payload["assessments"], (
            "an unchanged, already-assessed posting must not be re-sent to "
            "assess -- the model must not be re-invoked for it"
        )

        # uat-bug-009's headline field: carried_forward_assessments -- the
        # SAME verdict the first run produced, not silently dropped and not
        # a fresh one.
        carried_forward = second_results["carried_forward_assessments"]
        assert carried_forward, (
            "an unchanged, previously-assessed posting must appear in "
            "carried_forward_assessments -- this is uat-bug-009's exact fix"
        )
        carried_entry = next(
            item for item in carried_forward
            if item["normalized_url"] == first_normalized_url
        )
        assert carried_entry["result"]["matrix"] == first_matrix
        assert carried_entry["result"]["suggestions"] == first_suggestions

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
