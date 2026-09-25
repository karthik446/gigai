"""uat-bug-005 part 2: an assess failure must reach the run status the UI reads.

The operator's screenshot showed Acquire still "running" while the assess log
already had a ``ScoutProposalExecutionError`` (the duplicate-codex-target
bug fixed in ``test_assess_model_policy.py``). This file isolates the
progress-writing side of that gap: ``assess_node``'s own failure path
(``proposal_execution.py``, the try/finally wrapper around
``_assess_node_body``) marks ``steps["assess"]`` ``"failed"`` today, but
carries no error message -- so ``GET /api/runs/{id}/progress`` (which folds
``read_progress(...).steps`` straight into its response, see
``present_api.run_progress``) has a failed step with nothing for the UI to
show the operator.

This only tests what ``assess_node``/``ProgressWriter`` (owned by this
worker) can control. Whether ``GET /api/runs/{id}`` (``run_status``, backed
by ``run.py``'s scheduler + node receipts) already carries the message is a
separate, run.py-owned question -- see the worker report.

``TestAssessFailureReachesRealRunStatus`` below is the run-status-and-logs
follow-up: it drives a real find-jobs run through the HTTP API exactly the
way the UI does (spawned child process, ``POST /api/run`` then polling
``GET /api/runs/{id}`` and ``GET /api/runs/{id}/progress``), the same
scaffolding as ``test_m1_end_to_end.py``, but with a sealed config that
reproduces uat-bug-005's adapter-resolution ambiguity for real (two enabled
``ollama_local``-adapter targets, neither named exactly ``ollama_local``) --
a genuine, fully offline assess failure (no live provider call, no
``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL``/``_TEST_HTTP`` stand-in needed, since
the error fires at adapter-name resolution before any HTTP call) that
reaches every layer the UI reads, not just ``assess_node`` called directly.
"""

from __future__ import annotations

from pathlib import Path
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from gigai.config import Endpoint, Profile
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME
from gigai.scout.find_jobs.contracts import ModelTarget, NodeContext
from gigai.scout.find_jobs.present_api import RUN_START_TIMEOUT_SECONDS, ScoutFindJobsBackend, serve
from gigai.scout.find_jobs.progress import read_progress
from gigai.scout.proposal_execution import ScoutProposalExecutionError, assess_node
from gigai.setup import build_config

from .test_m1_end_to_end import _fixture, _run_request


def _context(tmp_path: Path) -> NodeContext:
    return NodeContext(
        run_id="run_01",
        project_id="project_01",
        gig_id="gig_01",
        graph_id="find-jobs:functional",
        graph_version=1,
        goal_slug="assess",
        manifest_digest="sha256:" + "a" * 64,
        operation_key="assess-001",
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(tmp_path),
        redeemed_consent_ref="consent",
        model_target=ModelTarget.CODEX_CLI,
    )


def _ambiguous_codex_config(tmp_path: Path):
    """uat-bug-005 repro config: codex-default (setup) + codex_cli (0.1.8.x
    README workaround), both enabled on the same codex endpoint."""
    return build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="codex", adapter="codex_cli"),
            Endpoint(name="codex-alt", adapter="codex_cli"),
        ),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget("codex-default", "codex", "default", ("text",), 512),
            ConfigModelTarget("codex-alt-default", "codex-alt", "default", ("text",), 512),
        ),
    )


def test_assess_node_failure_marks_the_progress_step_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline (already true today): an assess_node failure marks
    steps["assess"] "failed" in progress/steps.json, which /progress folds
    straight into its response -- so the UI does not stay stuck on
    "running" for the *step* itself."""

    config = _ambiguous_codex_config(tmp_path)
    # Force the ambiguous-adapter-scan path (no exact-name target) so this
    # is a real ScoutProposalExecutionError from _resolve_configured_target_name_for_adapter,
    # not a scripted stand-in.
    monkeypatch.setattr(
        "gigai.scout.proposal_execution._read_pinned_resume",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must fail before reading resume")),
    )
    context = _context(tmp_path)
    assess_input = SimpleNamespace(model_target=ModelTarget.CODEX_CLI)

    with pytest.raises(ScoutProposalExecutionError, match="multiple configured model targets use adapter 'codex_cli'"):
        assess_node(context, assess_input, home_root=tmp_path / "home", target=None, config=config)

    snapshot = read_progress(tmp_path / "runs" / "run_01")
    assert snapshot.steps["assess"]["status"] == "failed"


def test_assess_node_failure_message_reaches_progress_steps(tmp_path: Path) -> None:
    """The fix: an assess_node failure's own message now reaches
    progress/steps.json (``entry["message"]``), which /progress folds
    straight into its response (``present_api.run_progress`` returns
    ``dict(snapshot.steps)`` verbatim) -- so a UI reading /progress for the
    failed step's status also has the "why" without needing a second call.

    Before this fix, `steps["assess"]` only ever had "status"/"started_at"/
    "finished_at" -- no message field at all."""

    config = _ambiguous_codex_config(tmp_path)
    context = _context(tmp_path)
    assess_input = SimpleNamespace(model_target=ModelTarget.CODEX_CLI)

    with pytest.raises(ScoutProposalExecutionError) as excinfo:
        assess_node(context, assess_input, home_root=tmp_path / "home", target=None, config=config)

    snapshot = read_progress(tmp_path / "runs" / "run_01")
    failed_step = snapshot.steps["assess"]
    assert failed_step["status"] == "failed"
    assert failed_step["message"] == str(excinfo.value)
    assert "multiple configured model targets use adapter 'codex_cli'" in failed_step["message"]


def _ambiguous_ollama_fixture_config() -> tuple[
    tuple[Endpoint, ...], tuple[ConfigModelTarget, ...], tuple[Profile, ...]
]:
    """Two enabled targets on ``ollama_local``-adapter endpoints, neither
    named exactly ``ollama_local`` (the sealed enum value) -- the real,
    end-to-end shape of uat-bug-005's adapter-resolution ambiguity
    (``_resolve_configured_target_name_for_adapter``), applied to the
    ``ollama_local`` adapter instead of ``codex_cli`` so this fixture never
    needs a real `codex` binary. Both targets' endpoints point at the same
    closed local port (nothing must ever actually connect: the error fires
    before any HTTP call, inside adapter-name resolution itself)."""

    endpoints = (
        Endpoint("local-a", "ollama_local", base_url="http://127.0.0.1:1"),
        Endpoint("local-b", "ollama_local", base_url="http://127.0.0.1:1"),
    )
    model_targets = (
        ConfigModelTarget(
            name="ollama-a",
            endpoint="local-a",
            model=TEST_MODEL_NAME,
            capabilities=("text",),
            max_output_tokens=512,
            reasoning_effort=None,
            model_digest=TEST_MODEL_DIGEST,
            context_tokens=2048,
            max_response_bytes=65536,
        ),
        ConfigModelTarget(
            name="ollama-b",
            endpoint="local-b",
            model=TEST_MODEL_NAME,
            capabilities=("text",),
            max_output_tokens=512,
            reasoning_effort=None,
            model_digest=TEST_MODEL_DIGEST,
            context_tokens=2048,
            max_response_bytes=65536,
        ),
    )
    profiles = (Profile("default", "ollama-a", "ollama-a", "ollama-a"),)
    return endpoints, model_targets, profiles


def _poll_terminal(client: httpx.Client, run_id: str, *, deadline_seconds: float) -> dict[str, object]:
    deadline = time.monotonic() + deadline_seconds
    last_body: object = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        last_body = response.text
        if response.status_code == 200:
            body = response.json()
            if body["status"] in {"succeeded", "failed", "blocked", "cancelled", "interrupted"}:
                return body
        elif response.status_code != 200:
            pytest.fail(f"GET /api/runs/{run_id} returned {response.status_code}: {response.text}")
        time.sleep(0.05)
    pytest.fail(f"run {run_id} did not terminalize before timeout; last response={last_body!r}")


class TestAssessFailureReachesRealRunStatus:
    """run-status-and-logs (A): the operator's screenshot showed Acquire stuck
    "running" while the server log already had the assess error. This drives
    a real find-jobs run through the HTTP API exactly the way the UI does --
    a spawned child process, ``POST /api/run``, then polling
    ``GET /api/runs/{id}`` and ``GET /api/runs/{id}/progress`` -- with a
    sealed config that reproduces uat-bug-005's adapter-resolution
    ambiguity for real (two enabled ``ollama_local``-adapter targets,
    neither named exactly ``ollama_local``; see
    ``_ambiguous_ollama_fixture_config``), so ``assess_node`` raises
    unconditionally, before any HTTP call to a model -- fully offline, no
    live provider call, no ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL``/``_TEST_HTTP``
    stand-in needed (acquire finds nothing, which is fine: this reproduces
    the failure at adapter resolution, before acquire's rows even matter).

    Before any run.py/present_api.py change this asserts, the acceptance
    bar is: the run's status is "failed", the "assess" step/receipt is
    "failed" with a message, and no step is left "running". This test is
    the fail-before/pass-after proof for that bar; see the worker report
    for what (if anything) had to change to satisfy it.
    """

    _POLL_DEADLINE_SECONDS = RUN_START_TIMEOUT_SECONDS * 6

    def test_real_child_process_run_reports_failed_not_stuck_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoints, model_targets, profiles = _ambiguous_ollama_fixture_config()
        home, target, _workpad = _fixture(
            tmp_path, endpoints=endpoints, model_targets=model_targets, profiles=profiles
        )
        monkeypatch.setenv("EXA_API_KEY", "m1-test-key")
        # Acquire must still never make a live network call, even though its
        # result doesn't matter to this repro (assess fails at adapter-name
        # resolution regardless of what acquire finds) -- the same offline
        # Exa/Greenhouse fixture transport test_m1_end_to_end.py uses.
        monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")

        backend = ScoutFindJobsBackend(home_root=home, target=target)
        server = serve(backend=backend, bind=("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[0], server.server_address[1]
        try:
            with httpx.Client(base_url=f"http://{host}:{port}", timeout=RUN_START_TIMEOUT_SECONDS * 3) as client:
                config_response = client.get("/api/config")
                assert config_response.status_code == 200, config_response.text
                config_digest = config_response.json()["config_digest"]

                response = client.post("/api/run", json=_run_request(config_digest))
                assert response.status_code == 202, response.text
                run_id = response.json()["run_id"]

                status_body = _poll_terminal(client, run_id, deadline_seconds=self._POLL_DEADLINE_SECONDS)

                # The acceptance bar, in order: overall status is "failed"
                # (never left at "running"/"pending" once the child
                # terminalizes), the assess node's own receipt is "failed"
                # with a message, and -- the operator's exact screenshot --
                # no node receipt is left at "running".
                assert status_body["status"] == "failed", status_body
                receipts_by_slug = {
                    item["node_slug"]: item for item in status_body["node_receipts"]
                }
                assert "assess" in receipts_by_slug, status_body
                assess_receipt = receipts_by_slug["assess"]
                assert assess_receipt["status"] == "failed", assess_receipt
                failure = assess_receipt.get("failure") or {}
                assert failure.get("message"), assess_receipt
                for slug, receipt in receipts_by_slug.items():
                    assert receipt["status"] != "running", f"{slug} was left running: {receipt}"

                # The non-authoritative /progress view (what the UI actually
                # polls while a run is in flight) must agree: acquire done,
                # assess failed with the same message, never "running".
                progress_response = client.get(f"/api/runs/{run_id}/progress")
                assert progress_response.status_code == 200, progress_response.text
                steps = progress_response.json()["steps"]
                assert steps["acquire"]["status"] == "done", steps
                assert steps["assess"]["status"] == "failed", steps
                assert steps["assess"].get("message"), steps
                for slug, step in steps.items():
                    assert step.get("status") != "running", f"{slug} was left running: {step}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
