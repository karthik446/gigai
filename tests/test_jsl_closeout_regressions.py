from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from queue import Empty

import pytest
from click.testing import CliRunner

from gigai import journal, provider_review
from gigai.cli import cli
from gigai.model_targets import resolve_model_target
from gigai.adapters.factory import ModelAdapterBinding
from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.run_plan import approve_requirements_baseline, create_run_plan
from gigai.workpad import provision_workpad, resolve_workpad

from tests.test_g43_provider_review import _fixture


def _clean_closeout_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    home, target, gig_id, source = _fixture(tmp_path)
    baseline = tmp_path / "requirements.md"
    baseline.write_text("# Requirements\n\nKeep scope explicit.\n", encoding="utf-8")
    approval = approve_requirements_baseline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        baseline_path=baseline,
        direct_operator_confirmed=True,
    )
    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        review_subject=source,
        requirements_baseline_approval_id=approval.approval_id,
        profile_id="standard",
    )

    class FakeAdapter:
        def invoke(self, request):
            return InvocationResult(
                "success",
                '{"findings":[]}',
                "fixture",
                {},
                NormalizedUsage(1, 1, 2),
                "provider_reported",
            )

    def fake_resolve(config, target_name):
        return ModelAdapterBinding(resolve_model_target(config, target_name), FakeAdapter())

    # This keeps the real model_execution journal/evidence path and only replaces
    # the provider port, matching the existing G43 closeout fixture seam.
    monkeypatch.setattr("gigai.model_execution.resolve_model_adapter", fake_resolve)
    run = CliRunner().invoke(
        cli,
        [
            "run",
            "--plan",
            plan.run_plan_id,
            "--execute-review",
            "--confirm",
            "--wait",
            "--home",
            str(home),
            "--target",
            str(target),
            "--json",
        ],
    )
    assert run.exit_code == 0, run.output
    run_id = json.loads(run.output)["run_id"]
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    root = plan.workpad / "runs" / run_id / "provider-reviews" / plan.run_plan_id
    return {
        "home": home,
        "target": target,
        "gig_id": gig_id,
        "plan": plan,
        "run_id": run_id,
        "project_id": resolved.project_id,
        "root": root,
    }


def _closeout_command(fixture: dict[str, object], *, gig_id: str | None = None, run_id: str | None = None) -> list[str]:
    plan = fixture["plan"]
    return [
        "provider-review",
        "closeout",
        "--run",
        str(run_id or fixture["run_id"]),
        "--plan",
        plan.run_plan_id,
        "--gig",
        str(gig_id or fixture["gig_id"]),
        "--confirm",
        "--home",
        str(fixture["home"]),
        "--target",
        str(fixture["target"]),
        "--json",
    ]


def _invocation_artifacts(root: Path) -> dict[str, Path]:
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    invocation = result["reviewer_invocations"][0]
    workpad = root.parents[3]
    record_path = workpad / "runs" / root.parents[1].name / "model-invocations" / invocation["invocation_id"] / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    request_path = workpad / record["request"]["request_artifact"]["path"]
    response_ref = next(
        item["value"]
        for item in record["extensions"]
        if item.get("namespace") == "gigai.g18" and item.get("name") == "response_artifact"
    )
    return {
        "request": request_path,
        "response": workpad / response_ref["path"],
        "record": record_path,
        "result": root / "result.json",
        "loop": root / "review-loop.json",
    }


@pytest.mark.parametrize(
    ("artifact", "mutation", "expected_code"),
    (
        ("request", "missing", "provider_review_closeout_evidence_missing"),
        ("request", "tampered", "provider_review_closeout_invocation_invalid"),
        ("response", "missing", "provider_review_closeout_evidence_missing"),
        ("response", "tampered", "provider_review_closeout_invocation_invalid"),
        ("record", "missing", "provider_review_closeout_evidence_missing"),
        ("record", "tampered", "provider_review_closeout_invocation_invalid"),
        ("result", "missing", "provider_review_closeout_evidence_missing"),
        ("result", "tampered", "provider_review_closeout_not_clean"),
        ("loop", "missing", "provider_review_closeout_evidence_missing"),
        ("loop", "tampered", "provider_review_closeout_not_clean"),
    ),
)
def test_closeout_refuses_missing_or_tampered_authenticated_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
    mutation: str,
    expected_code: str,
) -> None:
    fixture = _clean_closeout_fixture(tmp_path, monkeypatch)
    path = _invocation_artifacts(fixture["root"])
    target = path[artifact]
    if mutation == "missing":
        target.unlink()
    else:
        target.write_bytes(b"{}")

    refused = CliRunner().invoke(cli, _closeout_command(fixture))
    assert refused.exit_code != 0, refused.output
    payload = json.loads(refused.output)
    assert payload["error"]["code"] == expected_code
    assert not (fixture["root"] / "closeout" / "no-fix-required.json").exists()


@pytest.mark.parametrize("foreign", ("run", "gig"))
def test_closeout_refuses_foreign_run_or_gig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign: str
) -> None:
    fixture = _clean_closeout_fixture(tmp_path, monkeypatch)
    if foreign == "run":
        refused = CliRunner().invoke(
            cli,
            _closeout_command(
                fixture,
                run_id="run_87654321-4321-4876-8876-543210fedcba",
            ),
        )
        expected_code = "provider_review_consent_missing"
    else:
        foreign_gig = "gig_87654321-4321-4876-8876-543210fedcba"
        provision_workpad(
            home_root=fixture["home"],
            project_id=fixture["project_id"],
            gig_id=foreign_gig,
        )
        refused = CliRunner().invoke(cli, _closeout_command(fixture, gig_id=foreign_gig))
        expected_code = "run_plan_not_found"
    assert refused.exit_code != 0, refused.output
    assert json.loads(refused.output)["error"]["code"] == expected_code


def test_closeout_refuses_symlinked_evidence_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _clean_closeout_fixture(tmp_path, monkeypatch)
    evidence_parent = fixture["root"]
    real_parent = evidence_parent.with_name(".provider-review-evidence")
    evidence_parent.rename(real_parent)
    evidence_parent.symlink_to(real_parent, target_is_directory=True)

    refused = CliRunner().invoke(cli, _closeout_command(fixture))
    assert refused.exit_code != 0, refused.output
    assert json.loads(refused.output)["error"]["code"] == "provider_review_plan_invalid"
    assert not (real_parent / "closeout" / "no-fix-required.json").exists()


def test_interrupted_closeout_is_recovered_then_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _clean_closeout_fixture(tmp_path, monkeypatch)
    original_record_transition = provider_review.record_transition

    def interrupted_record_transition(**kwargs):
        def crash(step: str) -> None:
            if step == "after_artifact_replace":
                raise RuntimeError("injected closeout interruption")

        return journal.record_transition(**kwargs, observer=crash)

    monkeypatch.setattr(provider_review, "record_transition", interrupted_record_transition)
    interrupted = CliRunner().invoke(cli, _closeout_command(fixture))
    assert interrupted.exit_code != 0, interrupted.output
    assert "provider_review_closeout_journal_failed" in interrupted.output
    assert (fixture["root"] / "closeout" / "no-fix-required.json").exists()
    assert tuple(fixture["plan"].workpad.glob("scratch/.gigai-journal-*.json"))

    monkeypatch.setattr(provider_review, "record_transition", original_record_transition)
    recovered = journal.reconcile_journal(
        workpad=fixture["plan"].workpad,
        project_id=fixture["project_id"],
        gig_id=fixture["gig_id"],
    )
    assert recovered.reconciled is True
    replay = CliRunner().invoke(cli, _closeout_command(fixture))
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output)["closeout"]["replayed"] is True
    assert not tuple(fixture["plan"].workpad.glob("scratch/.gigai-journal-*.json"))


def _concurrent_closeout_worker(
    home: str,
    target: str,
    gig_id: str,
    run_id: str,
    run_plan_id: str,
    output,
) -> None:
    try:
        result = provider_review.close_provider_review_no_fix_required(
            home_root=Path(home),
            requested_target=Path(target),
            gig_id=gig_id,
            run_id=run_id,
            run_plan_id=run_plan_id,
            direct_operator_confirmed=True,
        )
    except BaseException as exc:  # pragma: no cover - surfaced through the parent assertion
        output.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    else:
        output.put({"ok": True, "replayed": result.replayed, "closeout_id": result.closeout_id})


def test_concurrent_identical_closeout_publishes_once_and_replays_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("the journal lock contract requires a POSIX fork-capable test process")
    fixture = _clean_closeout_fixture(tmp_path, monkeypatch)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    output = context.Queue()
    original_record_transition = provider_review.record_transition

    def gated_record_transition(**kwargs):
        barrier.wait(timeout=30)
        return original_record_transition(**kwargs)

    # Both processes authenticate the exact same clean evidence before this gate;
    # the real journal writer lock decides publication order after the gate opens.
    monkeypatch.setattr(provider_review, "record_transition", gated_record_transition)
    processes = [
        context.Process(
            target=_concurrent_closeout_worker,
            args=(
                str(fixture["home"]),
                str(fixture["target"]),
                str(fixture["gig_id"]),
                str(fixture["run_id"]),
                fixture["plan"].run_plan_id,
                output,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    reports = []
    for _ in processes:
        try:
            reports.append(output.get(timeout=10))
        except Empty as exc:  # pragma: no cover - process assertions above should catch this
            raise AssertionError("closeout worker did not report") from exc
    assert all(report["ok"] for report in reports), reports
    assert sorted(report["replayed"] for report in reports) == [False, True]
    assert len({report["closeout_id"] for report in reports}) == 1
    assert len(tuple((fixture["plan"].workpad / "runs" / fixture["run_id"] / "provider-reviews" / fixture["plan"].run_plan_id / "closeout").glob("no-fix-required.json"))) == 1
    closeout_handoffs = tuple(
        fixture["plan"].workpad.glob("handoffs/*-provider-review-no-fix-required.txt")
    )
    assert len(closeout_handoffs) == 1
    assert not tuple(fixture["plan"].workpad.glob("scratch/.gigai-journal-*.json"))
