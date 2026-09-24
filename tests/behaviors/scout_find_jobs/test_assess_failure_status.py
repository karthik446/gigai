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
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs.contracts import ModelTarget, NodeContext
from gigai.scout.find_jobs.progress import read_progress
from gigai.scout.proposal_execution import ScoutProposalExecutionError, assess_node
from gigai.setup import build_config


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
