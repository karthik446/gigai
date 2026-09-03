from __future__ import annotations

import json
from pathlib import Path
import uuid

from click.testing import CliRunner

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.cli import cli
from gigai.lifecycle import approve_offline, create_offline
from gigai.model_execution import ModelInvocationExecution
from gigai.review import validate_review_bundle, validate_review_loop_artifacts
from gigai.run_plan import create_run_plan
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


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
