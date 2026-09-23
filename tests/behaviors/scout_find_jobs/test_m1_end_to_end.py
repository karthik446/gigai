"""M1's real Scout acquire -> assess -> present API path."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
import time

import httpx
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.config import Endpoint, ModelTarget as ConfigModelTarget, Profile
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline
from gigai.private_records import create_record, import_reference
from gigai.run import resolve_newest_resume
from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles
from gigai.scout.find_jobs.present_api import RUN_START_TIMEOUT_SECONDS, ScoutFindJobsBackend, serve
from gigai.scout.template import scout_candidate_inventory
from gigai.setup import build_config, run_setup
from gigai.workpad import select_active_workpad

from .conftest import load_fixture


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Create and approve a fresh Scout candidate through the normal path."""

    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", target],
        check=True,
    )
    subprocess.run(["git", "-C", str(target), "config", "user.name", "M1 Test"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "config", "user.email", "m1-test@gigai.invalid"],
        check=True,
    )
    (target / "README.md").write_text("M1 Scout fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "--quiet", "-m", "M1 Scout fixture"],
        check=True,
    )
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(Endpoint("local-test", "ollama_local", base_url="http://127.0.0.1:11434"),),
        model_targets=(
            ConfigModelTarget(
                name="ollama_local",
                endpoint="local-test",
                model=TEST_MODEL_NAME,
                capabilities=("text",),
                max_output_tokens=512,
                reasoning_effort=None,
                model_digest=TEST_MODEL_DIGEST,
                context_tokens=2048,
                max_response_bytes=65536,
            ),
        ),
        profiles=(Profile("default", "ollama_local", "ollama_local", "ollama_local"),),
    )
    run_setup(config)
    initialized = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=scout_candidate_inventory(),
    )
    instance = initialized.instances[0]
    assert instance.proposal_id is not None
    approve_offline(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        proposal_id=str(instance.proposal_id),
    )
    select_active_workpad(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        allow_semantic_state=True,
    )

    resume_source = tmp_path / "resume.md"
    resume_source.write_text(
        "Software engineer with Python service experience.\n",
        encoding="utf-8",
    )
    imported = import_reference(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        kind="resume",
        source=resume_source,
        operation_key="m1-resume-import",
    )
    create_record(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key="m1-resume-record",
    )

    config_path = target / "find-jobs.json"
    find_jobs_config = FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location="Denver, CO",
        remote=True,
        published_after="2026-09-15T00:00:00Z",
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
    )
    config_path.write_bytes(canonical_json_bytes(find_jobs_config.to_json()))
    return home, target


# Generous headroom over the server's own documented allocation budget
# (RUN_START_TIMEOUT_SECONDS) so this test is robust under heavy xdist CPU
# contention (14-way parallel load can starve the child process well past a
# tight client-side timeout without anything actually being broken).
_CLIENT_TIMEOUT_SECONDS = RUN_START_TIMEOUT_SECONDS * 3
_POST_RUN_SANITY_SECONDS = RUN_START_TIMEOUT_SECONDS * 2
_POLL_DEADLINE_SECONDS = RUN_START_TIMEOUT_SECONDS * 4


def _run_request(config_digest: str) -> dict[str, object]:
    request = load_fixture("fixture-api-run-request-v1.json")
    return {**request, "config_digest": config_digest}


def _poll_succeeded(client: httpx.Client, run_id: str) -> dict[str, object]:
    deadline = time.monotonic() + _POLL_DEADLINE_SECONDS
    last_body: object = None
    while time.monotonic() < deadline:
        try:
            response = client.get(f"/api/runs/{run_id}")
            last_body = response.text
            if response.status_code == 200:
                body = response.json()
                if body["status"] in {"succeeded", "failed", "blocked", "cancelled", "interrupted"}:
                    assert body["status"] == "succeeded", body
                    return body
        except (httpx.HTTPError, json.JSONDecodeError):
            pass
        time.sleep(0.05)
    pytest.fail(f"run {run_id} did not succeed before timeout; last response={last_body!r}")


def test_m1_real_api_run_child_process_and_second_run_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child registry hook is proven by requiring all real node receipts."""

    home, target = _fixture(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "m1-test-key")
    # The bindings construct these MockTransports in the spawned child.  A
    # parent transport cannot be pickled through multiprocessing.spawn.
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")

    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=_CLIENT_TIMEOUT_SECONDS) as client:
            config_response = client.get("/api/config")
            assert config_response.status_code == 200, config_response.text
            config_body = config_response.json()
            assert config_body["resume_preview"] is not None
            request_body = _run_request(config_body["config_digest"])

            started = time.monotonic()
            response = client.post("/api/run", json=request_body)
            elapsed = time.monotonic() - started
            assert response.status_code == 202, response.text
            # This only proves the POST returns before the run finishes (an
            # allocation ack, not a synchronous wait for the whole traversal);
            # it is intentionally far looser than the server's own
            # RUN_START_TIMEOUT_SECONDS budget so heavy parallel CPU
            # contention cannot turn a correct allocation into a flaky
            # failure.
            assert elapsed < _POST_RUN_SANITY_SECONDS, f"POST /api/run waited {elapsed:.3f}s"
            run_id = response.json()["run_id"]

            status_body = _poll_succeeded(client, run_id)
            assert [item["node_slug"] for item in status_body["node_receipts"]] == [
                "acquire",
                "assess",
                "present",
            ]

            results_response = client.get(f"/api/runs/{run_id}/results")
            assert results_response.status_code == 200, results_response.text
            first_payload = results_response.json()["payload"]
            assert first_payload["rows"]
            assert first_payload["assessments"]
            assert first_payload["assessments"][0]["matrix"]
            assert first_payload["assessments"][0]["suggestions"]
            assert first_payload["assessments"][0]["questions"]
            assert len(first_payload["node_receipts"]) == 3
            assert first_payload["pinned_resume"] == config_body["resume_preview"]

            second_response = client.post("/api/run", json=request_body)
            assert second_response.status_code == 202, second_response.text
            second_run_id = second_response.json()["run_id"]
            _poll_succeeded(client, second_run_id)

            second_results = client.get(f"/api/runs/{second_run_id}/results")
            assert second_results.status_code == 200, second_results.text
            second_payload = second_results.json()["payload"]
            assert [item["posting"] for item in second_payload["rows"]] == [
                item["posting"] for item in first_payload["rows"]
            ]
            assert {item["outcome"] for item in second_payload["rows"]} == {"unchanged"}
            assert second_payload["not_assessed"]
            assert {item["reason"] for item in second_payload["not_assessed"]} == {"unchanged"}
            assert second_payload["pinned_resume"] == first_payload["pinned_resume"]
            assert len(second_payload["node_receipts"]) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    # The reference-add path pinned the exact committed revision that the run
    # sealed; this also prevents a successful run from hiding a mutable resume.
    assert backend.resume_preview() == resolve_newest_resume(home, target)
