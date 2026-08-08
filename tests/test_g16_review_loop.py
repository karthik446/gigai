from __future__ import annotations

from pathlib import Path
import ast
import hashlib
import tempfile
import uuid

import pytest

from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.lifecycle import approve_offline, create_offline
from gigai.review import (
    validate_finding,
    validate_review_bundle,
    validate_review_loop,
    validate_review_loop_artifacts,
)
from gigai.review_loop import ReviewLoopError, run_review_loop
from gigai.run import launch_run
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad
from gigai.validators import SCHEMA_NAMES


_G15_SCHEMA_DIGESTS = {
    "adjudication.schema.json": "9b6d3f489dcff15b510e4c041be5bcef0afcd10d6e9d584a21ef79c9842f49fe",
    "active-gig-version.schema.json": "77a2f9df0928a8cfe60b496f63b981ab941268cdfc8c557902549f645e4a76f6",
    "common.schema.json": "825a15da8f61348cc16afe315c2aca0e3218c78c0bf0f93394f74fe78cb7b53a",
    "feedback.schema.json": "c89cda74feb86d34448a3e8afbfcded2554e3c077622ecfaa64e525951502461",
    "finding.schema.json": "4444e0cfec3a32bb83b016172331072a369afb2699b093ad670dc36dfbcdd8f7",
    "gig-proposal.schema.json": "515f16368059c7d8d4bf88cb47d8fc0df63afc50a51e13c8c75601c013f134b3",
    "goal-graph.schema.json": "669115492bfed52f4738cb9cbbac626a10f80f6965da3d1f70eb20e4c2e264cf",
    "handoff-frontmatter.schema.json": "de27d69529ae7cce07063fb67dcecc48aff79012ef72c66f3ed077367b9bd09e",
    "report.schema.json": "dc012ee13f66d45e3bdaab857c82a152a66be46cf98a8d50ffd21a4e581cac8c",
    "review-bundle.schema.json": "ab60331eaf6095aa2c70690592f1b66769012aa6973a03e6cb4a1d36f904b531",
    "review-contract.schema.json": "d7cc23e267ce07e071138e62c65accba9fc0b64ff967880fa05bf5cc5a4626f1",
    "run-brief-frontmatter.schema.json": "481118d7c49f97d00c389f8f4d4216cc1baf6ff96e8c16c3343006ad019369e3",
    "run-details.schema.json": "c2388d917e08cfcc0860ecd3a20b389be4f434aadde6b21ffa18ee4d6457111f",
    "run-manifest.schema.json": "a14126ac4943e71980371eb215fbc191434cfb0fb2f2761259a0faabb36af24f",
    "trace.schema.json": "d1b5a8970e26b753fbbb8275cd30321a3fe0bc2bb56c4443c6d6306b42ca29ef",
}


def test_g16_additive_schema_inventory_preserves_g15_baseline() -> None:
    assert len(SCHEMA_NAMES) == 17
    root = Path(__file__).parents[1] / "src/gigai/schemas"
    for name, expected in _G15_SCHEMA_DIGESTS.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 100))
    created = create_offline(home_root=home, requested_target=target, name="g16-review", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    return home, target, created.gig_id


def _workpad(home: Path, target: Path, gig_id: str):
    return resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)


def _sealed_run(home: Path, target: Path, gig_id: str, seed: int) -> str:
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(seed, seed + 20))
    result = launch_run(home_root=home, requested_target=target, gig_id=gig_id, wait=True, uuid_factory=lambda: next(values))
    assert result.status == "succeeded"
    return result.run_id


@pytest.mark.parametrize("profile", ["research", "climate", "pull-request", "repository", "spreadsheet"])
def test_all_profiles_materialize_replayable_complete_loop(tmp_path: Path, profile: str) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 100 + len(profile))
    result = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id, profile=profile)
    assert result.state == "complete"
    loop = parse_json_bytes((resolved.path / "manifests/review-loop.json").read_bytes())
    assert loop["stage_sequence"][-1]["state"] == "complete"
    assert validate_review_loop((resolved.path / "manifests/review-loop.json").read_bytes()).valid
    assert result.addressed_artifact_id is not None
    assert (
        resolved.path
        / "findings"
        / result.finding_ids[0]
        / "v3-resolved.json"
    ).is_file()
    report = parse_json_bytes(
        (resolved.path / "reports" / f"{result.report_id}.json").read_bytes()
    )
    assert report["status"] == "complete"
    assert len(result.journal_entries) == 10


def test_cycle_limit_blocks_without_successful_address(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 200)
    result = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id, cycle_limit_case=True)
    loop = parse_json_bytes((resolved.path / "manifests/review-loop.json").read_bytes())
    assert result.state == loop["state"] == "blocked"
    assert loop["terminal_decision"]["reason"] == "cycle limit exhausted"
    report = parse_json_bytes(
        (resolved.path / "reports" / f"{result.report_id}.json").read_bytes()
    )
    assert report["status"] == "blocked"
    assert not list((resolved.path / "addressed").glob("*.json"))


def test_clarification_feedback_blocks_before_addressing(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 210)
    result = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id, feedback_decision="clarification_requested")
    loop = parse_json_bytes((resolved.path / "manifests/review-loop.json").read_bytes())
    assert result.state == loop["state"] == "blocked"
    assert [stage["state"] for stage in loop["stage_sequence"]] == ["reviewing", "verifying", "feedback_pending", "blocked"]
    assert not list((resolved.path / "addressed").glob("*.json"))


def test_unresolved_disagreement_blocks_before_addressing(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 211)
    result = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id, unresolved_disagreement=True)
    loop = parse_json_bytes((resolved.path / "manifests/review-loop.json").read_bytes())
    assert result.state == loop["state"] == "blocked"
    assert loop["terminal_decision"]["reason"] == "unresolved evaluator disagreement"
    assert len(loop["adjudication_ids"]) == 1
    adjudication = resolved.path / "review" / "adjudications" / f"{loop['adjudication_ids'][0]}.json"
    assert adjudication.is_file()


def test_deferred_feedback_is_distinct_and_blocks_addressing(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 213)
    result = run_review_loop(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=gig_id,
        run_id=run_id,
        feedback_decision="deferred",
    )
    assert result.state == "blocked"
    assert (resolved.path / "findings" / result.finding_ids[0] / "v2-deferred.json").is_file()
    feedback = parse_json_bytes(
        next((resolved.path / "feedback").glob("*.json")).read_bytes()
    )
    assert feedback["decision"] == "deferred"
    assert not list((resolved.path / "addressed").glob("*.json"))


def test_partial_address_blocks_closure(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 212)
    result = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id, partial_address_case=True)
    loop = parse_json_bytes((resolved.path / "manifests/review-loop.json").read_bytes())
    assert result.state == loop["state"] == "blocked"
    addressed = parse_json_bytes((resolved.path / "addressed" / f"{result.addressed_artifact_id}.json").read_bytes())
    assert addressed["status"] == "partial"


def test_loop_preserves_target_bytes(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 215)
    run_details = parse_json_bytes(
        (resolved.path / "runs" / run_id / "run-details.json").read_bytes()
    )
    graph_path = resolved.path / "manifests" / "goal-graph.json"
    graph_before = graph_path.read_bytes()
    assert run_details["goal_graph_sha256"]
    before = {path.relative_to(target).as_posix(): path.read_bytes() for path in target.rglob("*") if path.is_file() and ".git" not in path.parts}
    run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id)
    after = {path.relative_to(target).as_posix(): path.read_bytes() for path in target.rglob("*") if path.is_file() and ".git" not in path.parts}
    assert before == after
    assert graph_path.read_bytes() == graph_before


def test_addressed_artifact_parent_mismatch_fails_closed(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 216)
    result = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id)
    artifact_path = resolved.path / "addressed" / f"{result.addressed_artifact_id}.json"
    artifact = parse_json_bytes(artifact_path.read_bytes())
    artifact["loop_id"] = "loop_99999999-9999-4999-8999-999999999999"
    artifact_path.write_bytes(canonical_json_bytes(artifact))
    report = validate_review_loop_artifacts(resolved.path, (resolved.path / "manifests/review-loop.json").read_bytes())
    assert not report.valid
    assert any(item.code == "addressed_parent_mismatch" for item in report.findings)


def test_report_tampering_fails_before_a_repeated_loop_starts(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 217)
    result = run_review_loop(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=gig_id,
        run_id=run_id,
    )
    report_path = resolved.path / "reports" / f"{result.report_id}.json"
    report = parse_json_bytes(report_path.read_bytes())
    report["status"] = "blocked"
    report_path.write_bytes(canonical_json_bytes(report))
    with pytest.raises(ReviewLoopError):
        run_review_loop(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=gig_id,
            run_id=run_id,
        )


def test_addressed_artifact_tampering_fails_before_a_repeated_loop_starts(
    tmp_path: Path,
) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 219)
    result = run_review_loop(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=gig_id,
        run_id=run_id,
    )
    artifact_path = resolved.path / "addressed" / f"{result.addressed_artifact_id}.json"
    artifact = parse_json_bytes(artifact_path.read_bytes())
    artifact["status"] = "partial"
    artifact_path.write_bytes(canonical_json_bytes(artifact))
    with pytest.raises(ReviewLoopError):
        run_review_loop(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=gig_id,
            run_id=run_id,
        )


def test_missing_reference_and_invented_citation_fail_closed(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 218)
    result = run_review_loop(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=gig_id,
        run_id=run_id,
    )
    bundle_path = resolved.path / "manifests" / "review-bundles" / "research.json"
    bundle = parse_json_bytes(bundle_path.read_bytes())
    bundle["references"][0]["content_sha256"] = "sha256:" + "0" * 64
    bundle_path.write_bytes(canonical_json_bytes(bundle))
    report = validate_review_bundle(resolved.path, bundle_path.read_bytes())
    assert not report.valid
    assert any(item.code == "reference_digest_mismatch" for item in report.findings)

    finding_path = resolved.path / "findings" / result.finding_ids[0] / "v2-accepted.json"
    finding = parse_json_bytes(finding_path.read_bytes())
    finding["evidence"][0]["reference_id"] = "ref_99999999-9999-4999-8999-999999999999"
    finding_report = validate_finding(canonical_json_bytes(finding), bundle)
    assert not finding_report.valid
    assert any(item.code == "unknown_reference" for item in finding_report.findings)


def test_divergent_bundle_fails_closed_before_loop(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    run_id = _sealed_run(home, target, gig_id, 220)
    tampered = resolved.path / "manifests/review-bundles/research.json"
    tampered.parent.mkdir(parents=True)
    tampered.write_text("{}")
    with pytest.raises(ReviewLoopError):
        run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=run_id)


def test_loop_requires_a_sealed_run(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    with pytest.raises(ReviewLoopError):
        run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id="run_00000000-0000-4000-8000-000000000202")


def test_repeated_runs_preserve_profile_and_stage_order(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    resolved = _workpad(home, target, gig_id)
    first_id = _sealed_run(home, target, gig_id, 230)
    second_id = _sealed_run(home, target, gig_id, 250)
    first = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=first_id, profile="research")
    second = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=gig_id, run_id=second_id, profile="research")
    assert first.state == second.state == "complete"
    assert [entry.path.name.split("-", 1)[1] for entry in first.journal_entries] == [entry.path.name.split("-", 1)[1] for entry in second.journal_entries]


def test_review_loop_effect_boundary_has_no_network_or_subprocess_imports() -> None:
    source = (Path(__file__).parents[1] / "src/gigai/review_loop.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import) and node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported.intersection({"socket", "subprocess", "urllib", "httpx", "requests"})


def test_review_loop_rejects_skipped_state_transition() -> None:
    payload = {
        "schema_version": "1.0",
        "loop_id": "loop_99999999-9999-4999-8999-999999999999",
        "loop_version": 1,
        "run_id": "run_99999999-9999-4999-8999-999999999999",
        "gig_id": "gig_99999999-9999-4999-8999-999999999999",
        "bundle_id": "bundle_99999999-9999-4999-8999-999999999999",
        "contract_id": "contract_99999999-9999-4999-8999-999999999999",
        "state": "complete",
        "cycle_cap": 1,
        "cycle_count": 0,
        "stage_sequence": [{"state": "reviewing", "sequence": 1}, {"state": "complete", "sequence": 2}],
        "finding_ids": [], "report_ids": [], "feedback_ids": [], "adjudication_ids": [], "trace_ids": [], "addressed_artifact_ids": [],
        "terminal_decision": {"state": "complete", "reason": "done", "next_action": None},
        "created_at": "2026-08-08T00:00:00Z", "updated_at": "2026-08-08T00:00:00Z",
    }
    report = validate_review_loop(canonical_json_bytes(payload))
    assert not report.valid
    assert any(item.code == "invalid_loop_transition" for item in report.findings)


def test_malformed_loop_state_fails_closed() -> None:
    report = validate_review_loop(b"{not-json")
    assert not report.valid
