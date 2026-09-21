from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import re
import subprocess
import uuid

import pytest
from click.testing import CliRunner

from gigai.adapters.factory import ModelAdapterBinding
from gigai.adapters.port import InvocationResult, ModelInvocationError, NormalizedUsage
from gigai.cli import cli
from gigai.lifecycle import approve_offline, create_offline
from gigai.model_targets import resolve_model_target
from gigai.run import launch_run, read_run_details
from gigai.run_plan import create_run_plan
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _fixture(
    tmp_path: Path, *, git_target: bool = False
) -> tuple[Path, Path, str, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    if git_target:
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main", target], check=True
        )
        subprocess.run(
            ["git", "-C", str(target), "config", "user.name", "G43 Test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(target), "config", "user.email", "g43@gigai.invalid"],
            check=True,
        )
        (target / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(target), "commit", "--quiet", "-m", "fixture"], check=True
        )
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
        for index in range(1, 40)
    )
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="provider-run-status",
        open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    source = tmp_path / "review.md"
    source.write_text("# Review subject\n\nKeep the boundary explicit.\n", encoding="utf-8")
    return home, target, created.gig_id, source


def _review_command(home: Path, target: Path, plan_id: str, *, wait: bool) -> list[str]:
    return [
        "run",
        "--plan",
        plan_id,
        "--execute-review",
        "--confirm",
        *(["--wait"] if wait else []),
        "--home",
        str(home),
        "--target",
        str(target),
        "--json",
    ]


@pytest.mark.parametrize("wait", [False, True])
def test_clean_provider_review_is_its_own_terminal_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait: bool
) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(
        home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,)
    )
    observed_statuses: list[str] = []

    class CleanAdapter:
        def invoke(self, request):  # type: ignore[no-untyped-def]
            workpad = plan.workpad
            run_dirs = sorted((workpad / "runs").glob("run_*"))
            assert len(run_dirs) == 1
            observed_statuses.append(
                read_run_details(home_root=home, requested_target=target, gig_id=gig_id, run_id=run_dirs[0].name)["status"]
            )
            return InvocationResult(
                "success", '{"findings":[]}', "fixture", {},
                NormalizedUsage(2, 3, 5), "provider_reported",
            )

    def fake_resolve(config, target_name):  # type: ignore[no-untyped-def]
        return ModelAdapterBinding(resolve_model_target(config, target_name), CleanAdapter())

    monkeypatch.setattr("gigai.model_execution.resolve_model_adapter", fake_resolve)
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=wait))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "succeeded"
    run_id = payload["run_id"]
    details = read_run_details(
        home_root=home, requested_target=target, gig_id=gig_id, run_id=run_id
    )
    assert observed_statuses and set(observed_statuses) == {"running"}
    assert details["status"] == "succeeded"
    assert details["aggregate_usage"] == {
        "input_tokens": 2 * len(observed_statuses),
        "output_tokens": 3 * len(observed_statuses),
        "total_tokens": 5 * len(observed_statuses),
        "cost": None,
        "currency": None,
        "cost_status": "unavailable",
    }
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}
    assert not (plan.workpad / "runs" / run_id / "evidence").exists()
    terminal = sorted((plan.workpad / "handoffs").glob("*-run-succeeded.txt"))[-1]
    handoff = terminal.read_text(encoding="utf-8")
    assert "g43.1-provider-review" in handoff
    assert f"runs/{run_id}/provider-reviews/{plan.run_plan_id}/result.json" in handoff


def test_malformed_provider_review_blocks_without_executing_offline_goals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(
        home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,)
    )

    class MalformedAdapter:
        def invoke(self, request):  # type: ignore[no-untyped-def]
            return InvocationResult(
                "success", "not JSON", "fixture", {},
                NormalizedUsage(1, 1, 2), "provider_reported",
            )

    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda config, target_name: ModelAdapterBinding(
            resolve_model_target(config, target_name), MalformedAdapter()
        ),
    )
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    details = read_run_details(
        home_root=home, requested_target=target, gig_id=gig_id, run_id=payload["run_id"]
    )
    assert details["status"] == "blocked"
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}
    assert not (plan.workpad / "runs" / payload["run_id"] / "evidence").exists()


def test_finding_bearing_provider_review_succeeds_without_offline_goal_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        input_paths=(source,),
        profile_id="standard",
    )

    class FindingAdapter:
        def invoke(self, request):  # type: ignore[no-untyped-def]
            if request.role == "reviewer":
                reference_id = re.search(r"ref_[0-9a-f-]{36}", request.prompt)
                assert reference_id is not None
                output = json.dumps({"findings": [{
                    "criterion_id": "criterion_requirements",
                    "severity": "low",
                    "title": "Boundary missing",
                    "description": "The sealed text needs a clearer boundary.",
                    "reference_id": reference_id.group(0),
                    "locator": "line 1",
                    "confidence": "0.8",
                }]})
            elif request.role == "verifier":
                finding_ids = re.findall(r"finding_[0-9a-f-]{36}", request.prompt)
                output = json.dumps({"outcomes": [
                    {"finding_id": finding_id, "status": "verified", "reason": "Supported."}
                    for finding_id in finding_ids
                ]})
            else:
                finding_ids = re.findall(r"finding_[0-9a-f-]{36}", request.prompt)
                output = json.dumps({"decisions": [
                    {"finding_id": finding_id, "decision": "accepted", "rationale": "Keep it visible."}
                    for finding_id in finding_ids
                ]})
            return InvocationResult(
                "success", output, "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported"
            )

    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda config, target_name: ModelAdapterBinding(
            resolve_model_target(config, target_name), FindingAdapter()
        ),
    )
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "succeeded"
    root = plan.workpad / "runs" / payload["run_id"] / "provider-reviews" / plan.run_plan_id
    assert json.loads((root / "result.json").read_text())["finding_count"] > 0
    details = read_run_details(
        home_root=home, requested_target=target, gig_id=gig_id, run_id=payload["run_id"]
    )
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}


def test_failed_provider_invocation_blocks_without_false_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(
        home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,)
    )

    class FailedAdapter:
        def invoke(self, request):  # type: ignore[no-untyped-def]
            raise ModelInvocationError("provider unavailable")

    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda config, target_name: ModelAdapterBinding(
            resolve_model_target(config, target_name), FailedAdapter()
        ),
    )
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    details = read_run_details(
        home_root=home, requested_target=target, gig_id=gig_id, run_id=payload["run_id"]
    )
    assert details["status"] == "blocked"
    assert details["aggregate_usage"]["total_tokens"] is None
    assert details["aggregate_usage"]["cost"] is None
    assert details["aggregate_usage"]["cost_status"] == "unavailable"
    root = plan.workpad / "runs" / payload["run_id"] / "provider-reviews" / plan.run_plan_id
    assert json.loads((root / "result.json").read_text())["status"] == "blocked"
    invocation = json.loads(
        next((plan.workpad / "runs" / payload["run_id"] / "model-invocations").glob("*/record.json")).read_text()
    )
    assert invocation["outcome"] == "unavailable"
    assert invocation["error"]["code"] == "provider_unavailable"
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}


def test_provider_review_exception_is_durably_failed_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(
        home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,)
    )

    def explode(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr("gigai.provider_review.execute_provider_review", explode)
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=False))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    details = read_run_details(
        home_root=home, requested_target=target, gig_id=gig_id, run_id=payload["run_id"]
    )
    assert details["status"] == "failed"
    assert details["model_errors"][-1]["code"] == "provider_review_exception"
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}


def test_changed_target_interrupts_provider_review_without_offline_goal_fabrication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target, gig_id, source = _fixture(tmp_path, git_target=True)
    plan = create_run_plan(
        home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,)
    )

    class MutatingAdapter:
        def invoke(self, request):  # type: ignore[no-untyped-def]
            (target / "README.md").write_text("changed during review\n", encoding="utf-8")
            return InvocationResult(
                "success", '{"findings":[]}', "fixture", {},
                NormalizedUsage(1, 1, 2), "provider_reported",
            )

    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda config, target_name: ModelAdapterBinding(
            resolve_model_target(config, target_name), MutatingAdapter()
        ),
    )
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "interrupted"
    details = read_run_details(
        home_root=home, requested_target=target, gig_id=gig_id, run_id=payload["run_id"]
    )
    assert details["status"] == "interrupted"
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}
    assert len(list((plan.workpad / "handoffs").glob("*-run-interrupted.txt"))) == 1


def test_ordinary_offline_nonwait_still_launches_the_worker(tmp_path: Path) -> None:
    home, target, gig_id, _source = _fixture(tmp_path)
    result = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        wait=False,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000030"),
    )
    assert result.status == "running"


def test_provider_process_death_is_recovered_once_without_offline_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))

    def die(**kwargs):  # type: ignore[no-untyped-def]
        os._exit(17)

    monkeypatch.setattr("gigai.provider_review.execute_provider_review", die)
    worker = multiprocessing.get_context("fork").Process(
        target=lambda: CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    )
    worker.start()
    worker.join(30)
    assert worker.exitcode == 17
    run_root = next((plan.workpad / "runs").glob("run_*"))
    assert json.loads((run_root / "run-details.json").read_text())["status"] == "running"
    first = read_run_details(home_root=home, requested_target=target, gig_id=gig_id, run_id=run_root.name)
    second = read_run_details(home_root=home, requested_target=target, gig_id=gig_id, run_id=run_root.name)
    assert first == second
    assert first["status"] == "interrupted"
    assert first["model_errors"][-1]["code"] == "provider_review_abandoned"
    assert first["aggregate_usage"]["cost"] is None
    assert {goal["status"] for goal in first["goals"]} <= {"pending", "ready"}
    assert len(list((plan.workpad / "handoffs").glob("*-run-interrupted.txt"))) == 1
    assert not (run_root / "evidence").exists()


def test_terminal_authentication_failure_is_recoverably_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gigai.run import RunError

    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))

    class CleanAdapter:
        def invoke(self, request):  # type: ignore[no-untyped-def]
            return InvocationResult("success", '{"findings":[]}', "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported")

    monkeypatch.setattr("gigai.model_execution.resolve_model_adapter", lambda config, target_name: ModelAdapterBinding(resolve_model_target(config, target_name), CleanAdapter()))

    def reject(*args):  # type: ignore[no-untyped-def]
        raise RunError("injected evidence authentication failure")

    monkeypatch.setattr("gigai.run._require_journaled_provider_evidence", reject)
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    assert result.exit_code != 0
    run_root = next((plan.workpad / "runs").glob("run_*"))
    details = read_run_details(home_root=home, requested_target=target, gig_id=gig_id, run_id=run_root.name)
    assert details["status"] == "interrupted"
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}
    assert (run_root / "provider-reviews" / plan.run_plan_id / "result.json").is_file()
    assert not list((plan.workpad / "handoffs").glob("*-run-succeeded.txt"))


def test_missing_terminal_result_cannot_be_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))
    monkeypatch.setattr("gigai.provider_review.execute_provider_review", lambda **kwargs: SimpleNamespace(status="complete"))
    result = CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    assert result.exit_code != 0
    run_root = next((plan.workpad / "runs").glob("run_*"))
    details = read_run_details(home_root=home, requested_target=target, gig_id=gig_id, run_id=run_root.name)
    assert details["status"] == "interrupted"
    assert not list((plan.workpad / "handoffs").glob("*-run-succeeded.txt"))


@pytest.mark.parametrize("fault", ["after_artifact_replace", "after_replace", "before_commit", "after_commit"])
def test_terminal_publication_crash_never_exposes_uncommitted_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from gigai.journal import reconcile_journal, record_transition
    from gigai.run import RunError
    from gigai.workpad import resolve_workpad

    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))

    class CleanAdapter:
        def invoke(self, request):  # type: ignore[no-untyped-def]
            return InvocationResult("success", '{"findings":[]}', "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported")

    monkeypatch.setattr("gigai.model_execution.resolve_model_adapter", lambda config, target_name: ModelAdapterBinding(resolve_model_target(config, target_name), CleanAdapter()))

    def crash_terminal(**kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("transition") == "run_succeeded":
            def crash_at(step):  # type: ignore[no-untyped-def]
                if step == fault:
                    os._exit(17)
            kwargs["observer"] = crash_at
        return record_transition(**kwargs)

    monkeypatch.setattr("gigai.run.record_transition", crash_terminal)
    worker = multiprocessing.get_context("fork").Process(
        target=lambda: CliRunner().invoke(cli, _review_command(home, target, plan.run_plan_id, wait=True))
    )
    worker.start()
    worker.join(30)
    assert worker.exitcode == 17
    run_root = next((plan.workpad / "runs").glob("run_*"))
    assert json.loads((run_root / "run-details.json").read_text())["status"] == "succeeded"
    if fault != "after_commit":
        with pytest.raises(RunError, match="run_details_reconciliation_required"):
            read_run_details(home_root=home, requested_target=target, gig_id=gig_id, run_id=run_root.name)
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
        recovered = reconcile_journal(workpad=plan.workpad, project_id=resolved.project_id, gig_id=gig_id)
        assert recovered.reconciled is True
    details = read_run_details(home_root=home, requested_target=target, gig_id=gig_id, run_id=run_root.name)
    assert details["status"] == "succeeded"
    assert {goal["status"] for goal in details["goals"]} <= {"pending", "ready"}
    assert len(list((plan.workpad / "handoffs").glob("*-run-succeeded.txt"))) == 1
    committed = subprocess.run(["git", "-C", str(plan.workpad), "show", f"HEAD:runs/{run_root.name}/run-details.json"], check=True, capture_output=True).stdout
    assert committed == (run_root / "run-details.json").read_bytes()
