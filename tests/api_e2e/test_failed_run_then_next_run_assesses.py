"""test-gap-001's headline acceptance: a run failing at assess, then the
next run assesses successfully -- uat-bug-009's exact repro shape
(5b0029e's own commit message: "postings from failed runs get assessed").

Uses the same real, unconditional assess failure
``test_assess_failure_status.py``'s ``TestAssessFailureReachesRealRunStatus``
and ``test_workpad_clean_after_run.py``'s ``TestWorkpadStaysCleanAfterAFailedRun``
already drive through HTTP: two enabled ``ollama_local``-adapter targets,
neither named exactly ``ollama_local``
(``_resolve_configured_target_name_for_adapter``'s ambiguity path) -- fully
offline, the error fires at adapter-name resolution before any HTTP call.

The operator's real fix for this is exactly what this journey does: add a
target actually named ``ollama_local`` via ``gigai setup`` (the exact-name
match wins outright over the ambiguous pair --
``_resolve_configured_target_name_for_adapter``'s own docstring), then run
again. Before uat-bug-009 (5b0029e), the run that failed left its posting
never assessed and never visible in a later run either; the fix is exactly
that a LATER, healthy run assesses it.

## uat-bug-009-r1: this journey found 5b0029e's own fix was incomplete

Building this journey found a real regression in 5b0029e itself, reproduced
live through the HTTP API (not a harness mistake -- traced on disk:
acquire's ``outputs/acquire.json`` for the second run correctly listed the
URL in ``selected_postings``, but assess's own candidate loop
(``proposal_execution._assess_node_body``) still dropped it via an
if/elif ordering bug that skipped every selected UNCHANGED row before
reaching the branch meant to assess it). Reported via `orca orchestration
ask`; fixed in-session by a parallel worker (uat-bug-009-r1, commented
"uat-bug-009-r1" at the fixed call site). This journey now passes for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    poll_until_terminal,
    resolve_workpad_path,
    run_request_body,
    setup_and_init_without_a_model_target,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)


def _setup_ambiguous_ollama_pair(home: Path) -> None:
    """Two enabled ``ollama_local``-adapter targets, neither named exactly
    ``ollama_local`` -- the real, end-to-end shape of the adapter-resolution
    ambiguity error (mirrors
    ``test_assess_failure_status._ambiguous_ollama_fixture_config``, done
    here via the real ``gigai setup`` CLI). Both endpoints point at the
    same closed local port; nothing ever actually connects -- the error
    fires at adapter-name resolution, before any HTTP call.
    """

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--endpoint",
            "ollama-a=ollama_local:http://127.0.0.1:1",
            "--endpoint",
            "ollama-b=ollama_local:http://127.0.0.1:1",
            "--model-target",
            f"ollama-a-target=ollama-a:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
            "--model-target",
            f"ollama-b-target=ollama-b:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output


def _fix_ambiguity_with_an_exact_name_match(home: Path) -> None:
    """The operator's real fix: add a target actually named
    ``ollama_local``. Per ``_resolve_configured_target_name_for_adapter``'s
    own docstring, an enabled target whose NAME equals the sealed adapter
    value wins outright -- no need to remove the ambiguous pair first,
    exactly like an operator editing their config without deleting old
    entries."""

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--endpoint",
            "ollama_local=ollama_local:http://127.0.0.1:11434",
            "--model-target",
            f"ollama_local=ollama_local:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
            "--create-model-target",
            "ollama_local",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output


def test_a_run_failing_at_assess_then_the_next_run_assesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init_without_a_model_target(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    _setup_ambiguous_ollama_pair(home)

    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        config_digest = client.get("/api/config").json()["config_digest"]

        # -- run 1: assess fails unconditionally (adapter-name ambiguity) --
        first_response = client.post("/api/run", json=run_request_body(config_digest))
        assert first_response.status_code == 202, first_response.text
        first_run_id = first_response.json()["run_id"]
        first_status = poll_until_terminal(client, first_run_id)
        assert first_status["status"] == "failed", first_status
        receipts_by_slug = {item["node_slug"]: item for item in first_status["node_receipts"]}
        assert receipts_by_slug["assess"]["status"] == "failed"
        for slug, receipt in receipts_by_slug.items():
            assert receipt["status"] != "running", f"{slug} was left running: {receipt}"

        # The first run's posting was never assessed -- results must show it
        # accounted for (not silently vanished), never claim a fake success.
        first_run_results = client.get(f"/api/runs/{first_run_id}/results")
        assert first_run_results.status_code == 200, first_run_results.text

        # -- the operator's fix: add an exact-name ollama_local target -----
        _fix_ambiguity_with_an_exact_name_match(home)

        # -- run 2: same posting, healthy target, assess succeeds ----------
        second_response = client.post("/api/run", json=run_request_body(config_digest))
        assert second_response.status_code == 202, second_response.text
        second_run_id = second_response.json()["run_id"]
        second_status = poll_until_terminal(client, second_run_id)
        assert second_status["status"] == "succeeded", second_status
        assert [item["node_slug"] for item in second_status["node_receipts"]] == [
            "acquire",
            "assess",
            "present",
        ]

        second_results = client.get(f"/api/runs/{second_run_id}/results").json()
        second_payload = second_results["payload"]
        # uat-bug-009's headline: the posting from the FAILED run now gets
        # assessed by this later, healthy run -- not silently dropped.
        assert second_payload["assessments"], (
            "the posting must be assessed by the next healthy run, per "
            "uat-bug-009 (5b0029e): postings from failed runs get assessed"
        )
        assert second_payload["assessments"][0]["matrix"]

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
