from __future__ import annotations

import json
from pathlib import Path
import uuid

from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import Endpoint, ModelTarget, Profile
from gigai.lifecycle import approve_offline, create_offline
from gigai.review import validate_finding, validate_report_artifact, validate_review_bundle, validate_review_loop_artifacts, validate_trace
from gigai.run_plan import create_run_plan, read_run_plan
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 40))
    created = create_offline(home_root=home, requested_target=target, name="g43-proof", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    source = tmp_path / "input.md"
    source.write_text("# explicit input\n", encoding="utf-8")
    return home, target, created.gig_id, source


def test_run_plan_seals_idempotently_and_is_readable(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    first = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))
    second = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))
    read = read_run_plan(home_root=home, requested_target=target, gig_id=gig_id, run_plan_id=first.run_plan_id)

    assert first.created is True
    assert second.created is False
    assert second.run_plan_id == first.run_plan_id
    assert read.content_sha256 == first.content_sha256
    assert read.plan["state"] == "sealed"
    assert read.plan["profile"]["profile_id"] == "focused"
    assert (first.workpad / "run-plans" / first.run_plan_id / "run-plan.json").is_file()
    listed = CliRunner().invoke(cli, ["run-plan", "list", "--gig", gig_id, "--home", str(home), "--target", str(target), "--json"])
    shown = CliRunner().invoke(cli, ["run-plan", "show", first.run_plan_id, "--gig", gig_id, "--home", str(home), "--target", str(target), "--json"])
    assert listed.exit_code == 0, listed.output
    assert shown.exit_code == 0, shown.output
    assert json.loads(listed.output)["plans"][0]["run_plan_id"] == first.run_plan_id
    assert json.loads(shown.output)["plan"]["content_sha256"] == first.content_sha256


def test_standard_plan_seals_explicit_distinct_participant_targets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    targets = tuple(
        ModelTarget(name=name, endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=4096)
        for name in ("claude-review", "codex-luna-review", "codex-verify", "claude-adjudicate")
    )
    run_setup(build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(Endpoint(name="offline", adapter="deterministic"),),
        model_targets=targets,
        profiles=(Profile(name="default", planner="claude-review", critic="claude-review", adjudicator="claude-adjudicate"),),
    ))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="explicit-review-targets", model_target="claude-review", open_editor=False)
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id)
    source = tmp_path / "input.md"
    source.write_text("# explicit input\n", encoding="utf-8")

    plan = create_run_plan(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        profile_id="standard",
        input_paths=(source,),
        reviewer_targets=("claude-review", "codex-luna-review"),
        verifier_targets=("codex-verify",),
        adjudicator_targets=("claude-adjudicate",),
    )

    assert [item["model_target_id"] for item in plan.plan["participants"]] == [
        "claude-review", "codex-luna-review", "codex-verify", "claude-adjudicate",
    ]
    assert plan.plan["participants"][0]["target_reuse_disclosure"] is None


def test_plan_run_requires_fresh_consent_and_pins_plan_in_manifest(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))
    missing = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--home", str(home), "--target", str(target)])
    assert missing.exit_code != 0
    assert "--confirm" in missing.output

    completed = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--confirm", "--wait", "--home", str(home), "--target", str(target), "--json"])
    assert completed.exit_code == 0, completed.output
    payload = json.loads(completed.output)
    manifest_path = plan.workpad / "runs" / payload["run_id"] / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    consent = json.loads((manifest_path.parent / "operator-consent.json").read_text(encoding="utf-8"))
    assert any(item["path"] == f"run-plans/{plan.run_plan_id}/run-plan.json" and item["content_sha256"] == plan.content_sha256 for item in manifest["sealed_sources"])
    assert consent["scope"]["run_plan_id"] == plan.run_plan_id
    assert consent["scope"]["run_plan_content_sha256"] == plan.content_sha256
    run_path = manifest_path.parent
    loop_path = run_path / "review" / "review-loop.json"
    loop = json.loads(loop_path.read_text(encoding="utf-8"))
    assert loop["run_id"] == payload["run_id"]
    assert loop["schema_version"] == "1.1"
    assert loop["verification_ids"]
    assert validate_review_bundle(run_path, (run_path / "review" / "bundle.json").read_bytes()).valid
    assert validate_review_loop_artifacts(run_path, loop_path.read_bytes()).valid
    assert validate_trace((run_path / "review" / "traces" / f"{loop['trace_ids'][0]}.json").read_bytes()).valid
    assert validate_finding((run_path / "review" / "findings" / loop["finding_ids"][0] / "v1-open.json").read_bytes()).valid
    report_path = run_path / "review" / "reports" / f"{loop['report_ids'][0]}.json"
    assert json.loads(report_path.read_text(encoding="utf-8"))["schema_version"] == "1.1"
    assert validate_report_artifact(run_path, report_path.read_bytes()).valid

    replay = CliRunner().invoke(cli, ["run", "--plan", plan.run_plan_id, "--confirm", "--home", str(home), "--target", str(target)])
    assert replay.exit_code != 0
    assert "run_plan_already_handed_off" in replay.output


def test_deep_profile_and_explicit_classification_require_recorded_reasons(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    result = CliRunner().invoke(cli, ["run-plan", "create", "--gig", gig_id, "--class", "code_review", "--input", str(source), "--profile", "deep", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "classification_ambiguous"

    valid = CliRunner().invoke(cli, ["run-plan", "create", "--gig", gig_id, "--class", "code_review", "--reason", "operator classified the supplied source", "--input", str(source), "--profile", "deep", "--profile-opt-in-reason", "higher-risk review", "--home", str(home), "--target", str(target), "--json"])
    assert valid.exit_code == 0, valid.output
    assert json.loads(valid.output)["plan"]["profile"]["profile_id"] == "deep"
    assert all("Target reuse disclosed" in item["target_reuse_disclosure"] for item in json.loads(valid.output)["plan"]["participants"])


def test_plan_handoff_rejects_changed_sealed_source_before_run_allocation(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))
    sealed_input = plan.workpad / "run-plans" / plan.run_plan_id / "inputs" / "input_a.bin"
    sealed_input.write_bytes(b"mutated after sealing\n")

    result = CliRunner().invoke(
        cli,
        ["run", "--plan", plan.run_plan_id, "--confirm", "--home", str(home), "--target", str(target), "--json"],
    )

    assert result.exit_code != 0
    assert result.output.startswith("Error: run_plan_input_mismatch:")
    assert not list(plan.workpad.glob("runs/run_*/run-manifest.json"))


def test_plan_reads_reject_symlinked_parent_components(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))
    plan_root = plan.workpad / "run-plans"
    real_root = tmp_path / "run-plans-real"
    plan_root.rename(real_root)
    plan_root.symlink_to(real_root, target_is_directory=True)
    result = CliRunner().invoke(cli, ["run-plan", "show", plan.run_plan_id, "--gig", gig_id, "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code != 0
    assert "run_plan_not_found" in result.output


def test_sealed_source_parent_symlink_is_a_stable_input_refusal(tmp_path: Path) -> None:
    home, target, gig_id, source = _fixture(tmp_path)
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=gig_id, input_paths=(source,))
    plan_dir = plan.workpad / "run-plans" / plan.run_plan_id
    inputs = plan_dir / "inputs"
    real_inputs = plan_dir / "inputs-real"
    inputs.rename(real_inputs)
    inputs.symlink_to(real_inputs, target_is_directory=True)
    result = CliRunner().invoke(cli, ["run-plan", "show", plan.run_plan_id, "--gig", gig_id, "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code != 0
    assert "run_plan_input_mismatch" in result.output
