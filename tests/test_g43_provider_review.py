from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from click.testing import CliRunner

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.adapters.factory import ModelAdapterBinding
from gigai.cli import cli
from gigai.lifecycle import approve_offline, create_offline
from gigai.model_execution import ModelInvocationExecution
from gigai.review import validate_review_bundle, validate_review_loop_artifacts
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.journal import JournalArtifact, record_transition
from gigai.run_plan import (
    RunPlanError,
    approve_requirements_baseline,
    create_run_plan,
    read_run_plan,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad
from gigai.model_targets import resolve_model_target


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 40))
    created = create_offline(home_root=home, requested_target=target, name="provider-review", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    source = tmp_path / "input.md"
    source.write_text("# Contract\n\nA direct existing-Gig request asks no questions.\n", encoding="utf-8")
    return home, target, created.gig_id, source


def test_execute_review_requires_a_sealed_plan(tmp_path: Path) -> None:
    home, target, _gig_id, _source = _fixture(tmp_path)
    result = CliRunner().invoke(cli, ["run", "--execute-review", "--confirm", "--home", str(home), "--target", str(target)])
    assert result.exit_code != 0
    assert "requires one sealed --plan" in result.output


def test_execute_review_records_typed_findings_and_verification(tmp_path: Path, monkeypatch) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,), profile_id="standard")
    finding_id = "finding_00000000-0000-4000-8000-000000000001"
    monkeypatch.setattr("gigai.provider_review._uuid_id", lambda prefix: f"{prefix}_00000000-0000-4000-8000-000000000001")
    calls = iter((
        '{"findings":[{"criterion_id":"criterion_requirements","severity":"medium","title":"Missing route boundary","description":"The contract needs an explicit candidate boundary.","reference_id":"REF","locator":"line 3","confidence":"0.8"}]}',
        '{"findings":[]}',
        f'{{"outcomes":[{{"finding_id":"{finding_id}","status":"verified","reason":"The supplied document supports this concern."}}]}}',
        f'{{"decisions":[{{"finding_id":"{finding_id}","decision":"accepted","rationale":"The verified finding needs an operator decision."}}]}}',
    ))

    def fake_invocation(**kwargs):
        output = next(calls)
        if "reference_id\":\"REF" in output:
            output = output.replace("REF", kwargs["selected_reference_ids"][0])
        return ModelInvocationExecution(
            record={"outcome": "succeeded", "invocation_id": f"inv_{uuid.uuid4()}"},
            result=InvocationResult("success", output, "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported"),
            journal_entry=None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr("gigai.provider_review.run_model_invocation", fake_invocation)
    result = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--execute-review", "--confirm", "--wait", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.output)["run_id"]
    root = plan.workpad / "runs" / run_id / "provider-reviews" / plan.run_plan_id
    terminal = json.loads((root / "result.json").read_text(encoding="utf-8"))
    assert terminal["finding_count"] == 1
    assert terminal["status"] == "complete"
    finding = next((root / "review" / "findings").glob("*/v1-open.json"))
    assert json.loads(finding.read_text(encoding="utf-8"))["evaluator"]["stage"] == "model"
    verification = next((root / "review" / "verification").glob("*.json"))
    assert json.loads(verification.read_text(encoding="utf-8"))["outcomes"][0]["status"] == "verified"
    adjudication = next((root / "review" / "adjudications").glob("*.json"))
    assert json.loads(adjudication.read_text(encoding="utf-8"))["decisions"][0]["decision"] == "accepted"
    assert validate_review_bundle(root, (root / "review" / "bundle.json").read_bytes()).valid
    assert validate_review_loop_artifacts(root, (root / "review-loop.json").read_bytes()).valid
    consent = json.loads((plan.workpad / "runs" / run_id / "operator-consent.json").read_text(encoding="utf-8"))
    assert consent["scope"]["provider_review_requested"] is True


def test_invalid_reviewer_json_is_terminally_blocked_without_a_finding(tmp_path: Path, monkeypatch) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))

    def fake_invocation(**_kwargs):
        return ModelInvocationExecution(
            record={"outcome": "succeeded", "invocation_id": f"inv_{uuid.uuid4()}"},
            result=InvocationResult("success", "not JSON", "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported"),
            journal_entry=None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr("gigai.provider_review.run_model_invocation", fake_invocation)
    result = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--execute-review", "--confirm", "--wait", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.output)["run_id"]
    root = plan.workpad / "runs" / run_id / "provider-reviews" / plan.run_plan_id
    terminal = json.loads((root / "result.json").read_text(encoding="utf-8"))
    assert terminal["status"] == "blocked"
    assert terminal["finding_count"] == 0
    assert not list((root / "review" / "findings").glob("*/v1-open.json"))


def test_closure_review_requires_directly_confirmed_baseline_approval(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    baseline = tmp_path / "requirements.md"
    baseline.write_text("# Requirements\n\nThe review must name its scope.\n", encoding="utf-8")

    missing = CliRunner().invoke(
        cli,
        ["run-plan", "approve-baseline", "--gig", gig_id, "--input", str(baseline), "--home", str(home), "--target", str(target), "--json"],
    )
    assert missing.exit_code != 0
    assert json.loads(missing.output)["error"]["code"] == "requirements_baseline_confirmation_required"

    approved = CliRunner().invoke(
        cli,
        ["run-plan", "approve-baseline", "--gig", gig_id, "--input", str(baseline), "--confirm", "--home", str(home), "--target", str(target), "--json"],
    )
    assert approved.exit_code == 0, approved.output
    approval_id = json.loads(approved.output)["approval"]["approval_id"]
    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        review_subject=source,
        requirements_baseline_approval_id=approval_id,
    )
    assert [item["role"] for item in plan.plan["inputs"]] == ["review_subject", "requirements_baseline"]
    assert plan.plan["review_contract"]["path"].endswith("review-contract.json")
    assert {item["role"] for item in plan.plan["inputs"]} == {"review_subject", "requirements_baseline"}
    assert any("requirements-baseline-approvals" in item["path"] for item in plan.plan["sealed_sources"])


def test_closure_review_refuses_an_approval_shaped_file_without_direct_journal_proof(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    approval_id = "requirements_baseline_approval_00000000-0000-4000-8000-000000000123"
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    workpad = resolved.path
    base = workpad / "review-inputs" / "requirements-baseline-approvals" / approval_id
    snapshot = b"# Requirements\n"
    snapshot_path = base / "baseline.bin"
    snapshot_ref = {
        "path": snapshot_path.relative_to(workpad).as_posix(),
        "content_sha256": digest_imported_bytes(snapshot),
        "media_type": "text/markdown",
        "size_bytes": len(snapshot),
    }
    approval = {
        "schema_version": "1.0",
        "approval_id": approval_id,
        "project_id": "project_12345678-1234-4234-9234-123456789abc",
        "gig_id": gig_id,
        "baseline_snapshot_ref": snapshot_ref,
        "approved_by": {"kind": "operator", "id": "local-user", "model_target": None},
        "approved_at": "2026-01-01T00:00:00Z",
    }
    approval_path = base / "approval.json"
    approval_bytes = canonical_json_bytes(approval)
    record_transition(
        workpad=workpad,
        project_id=resolved.project_id,
        gig_id=gig_id,
        handoff_id="handoff_00000000-0000-4000-8000-000000000123",
        transition="goal_completed",
        body="A fixture wrote approval-shaped bytes without direct baseline approval.",
        artifacts=(
            JournalArtifact(snapshot_path.relative_to(workpad).as_posix(), snapshot),
            JournalArtifact(approval_path.relative_to(workpad).as_posix(), approval_bytes),
        ),
    )

    try:
        create_run_plan(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            review_subject=source,
            requirements_baseline_approval_id=approval_id,
        )
    except RunPlanError as exc:
        assert exc.code == "requirements_baseline_approval_invalid"
    else:
        raise AssertionError("approval-shaped unjournaled file was accepted")


def test_typed_closure_review_requires_both_role_locators_and_labels_prompts(tmp_path: Path, monkeypatch) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    baseline = tmp_path / "requirements.md"
    baseline.write_text("# Requirements\n\nDefine a candidate boundary.\n", encoding="utf-8")
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
    )
    prompts: list[str] = []
    calls = iter(("reviewer", "verifier"))

    def fake_invocation(**kwargs):
        prompts.append(kwargs["prompt"])
        stage = next(calls)
        if stage == "reviewer":
            refs = kwargs["selected_reference_ids"]
            output = json.dumps({"findings": [{
                "criterion_id": "criterion_requirements", "severity": "medium", "title": "Boundary missing",
                "description": "The subject omits a requirement from the baseline.",
                "evidence": [
                    {"reference_id": refs[0], "locator": "line 1"},
                    {"reference_id": refs[1], "locator": "line 3"},
                ],
                "confidence": "0.8",
            }]})
        else:
            match = re.search(r"finding_[0-9a-f-]{36}", kwargs["prompt"])
            assert match is not None
            finding_id = match.group(0)
            output = json.dumps({"outcomes": [{"finding_id": finding_id, "status": "verified", "reason": "Both labelled sources support it."}]})
        return ModelInvocationExecution(
            record={"outcome": "succeeded", "invocation_id": f"inv_{uuid.uuid4()}"},
            result=InvocationResult("success", output, "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported"),
            journal_entry=None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr("gigai.provider_review.run_model_invocation", fake_invocation)
    result = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--execute-review", "--confirm", "--wait", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 0, result.output
    assert all("Review subject" in prompt and "Requirements baseline" in prompt for prompt in prompts)
    run_id = json.loads(result.output)["run_id"]
    root = plan.workpad / "runs" / run_id / "provider-reviews" / plan.run_plan_id
    finding = json.loads(next((root / "review" / "findings").glob("*/v1-open.json")).read_text(encoding="utf-8"))
    assert len(finding["evidence"]) == 2
    bundle = json.loads((root / "review" / "bundle.json").read_text(encoding="utf-8"))
    assert {item["role"] for item in bundle["references"]} == {"review_subject", "requirements_baseline"}


def test_changed_approved_baseline_refuses_before_run_allocation(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    baseline = tmp_path / "requirements.md"
    baseline.write_text("# Requirements\n\nKeep this exact baseline.\n", encoding="utf-8")
    approval = approve_requirements_baseline(home_root=home, requested_target=target, gig_id=gig_id, baseline_path=baseline, direct_operator_confirmed=True)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, review_subject=source, requirements_baseline_approval_id=approval.approval_id)
    snapshot_ref = approval.approval["baseline_snapshot_ref"]
    assert isinstance(snapshot_ref, dict)
    (plan.workpad / snapshot_ref["path"]).write_bytes(b"# Mutated requirements\n")

    result = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--confirm", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code != 0
    assert result.output.startswith("Error: requirements_baseline_changed:")
    assert not list(plan.workpad.glob("runs/run_*/run-manifest.json"))


def test_typed_closure_re_review_requires_the_identical_baseline_digest(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    baseline = tmp_path / "requirements.md"
    baseline.write_text("# Requirements\n\nName the scope.\n", encoding="utf-8")
    first_approval = approve_requirements_baseline(home_root=home, requested_target=target, gig_id=gig_id, baseline_path=baseline, direct_operator_confirmed=True)
    original = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, review_subject=source, requirements_baseline_approval_id=first_approval.approval_id)
    rereview = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, review_subject=source, requirements_baseline_approval_id=first_approval.approval_id, re_review_of=original.run_plan_id)
    record_path = rereview.plan["inputs"][1]["record_ref"]["path"]
    record = json.loads((rereview.workpad / record_path).read_text(encoding="utf-8"))
    assert record["re_review_of"]["original_run_plan_sha256"] == original.content_sha256
    assert read_run_plan(home_root=home, requested_target=target, gig_id=gig_id, run_plan_id=rereview.run_plan_id).run_plan_id == rereview.run_plan_id

    changed = tmp_path / "changed-requirements.md"
    changed.write_text("# Requirements\n\nA different requirement.\n", encoding="utf-8")
    changed_approval = approve_requirements_baseline(home_root=home, requested_target=target, gig_id=gig_id, baseline_path=changed, direct_operator_confirmed=True)
    try:
        create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, review_subject=source, requirements_baseline_approval_id=changed_approval.approval_id, re_review_of=original.run_plan_id)
    except RunPlanError as exc:
        assert exc.code == "requirements_baseline_changed"
    else:
        raise AssertionError("changed baseline was accepted for a re-review")


def test_direct_no_fix_closeout_requires_authenticated_clean_standard_evidence_and_replays(tmp_path: Path, monkeypatch) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    baseline = tmp_path / "requirements.md"
    baseline.write_text("# Requirements\n\nKeep scope explicit.\n", encoding="utf-8")
    approval = approve_requirements_baseline(home_root=home, requested_target=target, gig_id=gig_id, baseline_path=baseline, direct_operator_confirmed=True)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, review_subject=source, requirements_baseline_approval_id=approval.approval_id, profile_id="standard")
    foreign_subject = tmp_path / "foreign.md"
    foreign_subject.write_text("# Different contract\n", encoding="utf-8")
    foreign_plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, review_subject=foreign_subject, requirements_baseline_approval_id=approval.approval_id, profile_id="standard")

    calls: list[str] = []
    class FakeAdapter:
        def invoke(self, request):
            calls.append(request.role)
            return InvocationResult("success", '{"findings":[]}', "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported")

    def fake_resolve(config, target_name):
        return ModelAdapterBinding(resolve_model_target(config, target_name), FakeAdapter())

    monkeypatch.setattr("gigai.model_execution.resolve_model_adapter", fake_resolve)
    run = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--execute-review", "--confirm", "--wait", "--home", str(home), "--target", str(target), "--json"])
    assert run.exit_code == 0, run.output
    assert calls == ["reviewer", "reviewer"]
    run_id = json.loads(run.output)["run_id"]
    command = ["provider-review", "closeout", "--run", run_id, "--plan", plan.run_plan_id, "--gig", gig_id, "--home", str(home), "--target", str(target), "--json"]
    missing_confirmation = CliRunner().invoke(cli, command)
    assert missing_confirmation.exit_code != 0
    assert "provider_review_closeout_confirmation_required" in missing_confirmation.output
    foreign = CliRunner().invoke(cli, ["provider-review", "closeout", "--run", run_id, "--plan", foreign_plan.run_plan_id, "--gig", gig_id, "--confirm", "--home", str(home), "--target", str(target), "--json"])
    assert foreign.exit_code != 0
    assert "provider_review_consent_mismatch" in foreign.output

    closed = CliRunner().invoke(cli, [*command, "--confirm"])
    assert closed.exit_code == 0, closed.output
    payload = json.loads(closed.output)
    assert payload["closeout"]["replayed"] is False
    receipt_path = plan.workpad / payload["closeout"]["receipt_path"]
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["decision"] == "no_fix_required"
    assert receipt["reviewer_participant_ids"] == ["participant_p1", "participant_p2"]
    assert not {"approval_id", "baseline_snapshot_ref", "state", "operator_consent", "run_manifest"}.intersection(receipt)

    replay = CliRunner().invoke(cli, [*command, "--confirm"])
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output)["closeout"]["replayed"] is True

    receipt["confirmed_at"] = "2030-01-01T00:00:00Z"
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    forged = CliRunner().invoke(cli, [*command, "--confirm"])
    assert forged.exit_code != 0
    assert "provider_review_closeout_conflict" in forged.output
    receipt_path.write_bytes(receipt_bytes)

    report_path = plan.workpad / "runs" / run_id / "provider-reviews" / plan.run_plan_id / "report.json"
    report_path.write_bytes(b"{}")
    tampered = CliRunner().invoke(cli, [*command, "--confirm"])
    assert tampered.exit_code != 0
    assert "provider_review_closeout_not_clean" in tampered.output


def test_no_fix_closeout_refuses_finding_bearing_evidence(tmp_path: Path, monkeypatch) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    baseline = tmp_path / "requirements.md"
    baseline.write_text("# Requirements\n\nKeep scope explicit.\n", encoding="utf-8")
    approval = approve_requirements_baseline(home_root=home, requested_target=target, gig_id=gig_id, baseline_path=baseline, direct_operator_confirmed=True)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, review_subject=source, requirements_baseline_approval_id=approval.approval_id, profile_id="standard")

    def fake_invocation(**kwargs):
        refs = kwargs["selected_reference_ids"]
        output = json.dumps({"findings": [{"criterion_id": "criterion_requirements", "severity": "low", "title": "Missing", "description": "Missing requirement.", "evidence": [{"reference_id": refs[0], "locator": "line 1"}, {"reference_id": refs[1], "locator": "line 1"}], "confidence": "0.8"}]})
        return ModelInvocationExecution(record={"outcome": "succeeded", "invocation_id": f"inv_{uuid.uuid4()}"}, result=InvocationResult("success", output, "fixture", {}, NormalizedUsage(1, 1, 2), "provider_reported"), journal_entry=None)  # type: ignore[arg-type]

    monkeypatch.setattr("gigai.provider_review.run_model_invocation", fake_invocation)
    run = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--execute-review", "--confirm", "--wait", "--home", str(home), "--target", str(target), "--json"])
    assert run.exit_code == 0, run.output
    run_id = json.loads(run.output)["run_id"]
    refused = CliRunner().invoke(cli, ["provider-review", "closeout", "--run", run_id, "--plan", plan.run_plan_id, "--gig", gig_id, "--confirm", "--home", str(home), "--target", str(target), "--json"])
    assert refused.exit_code != 0
    assert "provider_review_closeout_not_clean" in refused.output
