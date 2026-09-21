from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from click.testing import CliRunner

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.canonical import EntityPrefix, canonical_json_bytes, canonical_json_digest, digest_imported_bytes, generate_entity_id, parse_json_bytes
from gigai.cli import _parse_endpoint_spec, _parse_model_target_spec, cli
from gigai.config import Endpoint, ModelTarget, Profile, load_config
from gigai.journal import JournalArtifact, read_committed_artifact, record_transition
from gigai.lifecycle import LifecycleError, approve_offline, create_offline, propose_first_graph_set_offline, propose_graph_set_offline
from gigai.model_execution import ModelInvocationExecution
from gigai.private_records import import_run_input, migrate_workpad_layout
from gigai.runtime_comparison import (
    RuntimeComparisonError,
    _comparison_prompt,
    comparison_status,
    grade_output,
    load_evaluation_pack,
    resume_comparison,
    run_comparison,
    show_comparison,
    validate_evaluation_pack,
    validate_grader,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target

from tests.test_scout02_graph_set_flow import _write_definition
from tests.test_scout05_first_proposal import _bound_defaults, _write_first_definition


def test_pack_and_grader_are_frozen_and_source_grounded() -> None:
    pack = load_evaluation_pack()
    assert pack.payload["pack_version"] == "1.0"
    assert validate_grader(pack)["status"] == "pass"
    rejected = grade_output(
        pack,
        "posting_supported",
        {"verdict": "pass", "criteria": [{"criterion_id": "location", "status": "supported", "evidence_ids": ["not-supplied"]}], "unsupported_claims": []},
    )
    assert rejected["status"] == "fail"


def test_pack_reload_preserves_canonical_identity_for_pretty_and_whitespace_sources(tmp_path: Path) -> None:
    pack = load_evaluation_pack()
    pretty = tmp_path / "pretty.json"
    whitespace = tmp_path / "whitespace.json"
    pretty.write_text(json.dumps(pack.payload, indent=2), encoding="utf-8")
    whitespace.write_text(" \n" + json.dumps(pack.payload, separators=(",", ":")) + "\n", encoding="utf-8")
    assert load_evaluation_pack(pretty).digest == pack.digest
    assert load_evaluation_pack(whitespace).digest == pack.digest


def test_grader_rejects_empty_wrong_kind_and_duplicate_criterion_evidence() -> None:
    pack = load_evaluation_pack()
    empty = grade_output(pack, "posting_supported", {"verdict": "pass", "criteria": [{"criterion_id": "location", "status": "supported", "evidence_ids": []}], "unsupported_claims": []})
    wrong_kind = grade_output(pack, "posting_supported", {"verdict": "pass", "criteria": [{"criterion_id": "location", "status": "supported", "evidence_ids": ["E2"]}], "unsupported_claims": []})
    duplicate = grade_output(pack, "posting_supported", {"verdict": "pass", "criteria": [{"criterion_id": "location", "status": "unsupported", "evidence_ids": ["E1"]}, {"criterion_id": "location", "status": "supported", "evidence_ids": ["E1"]}], "unsupported_claims": []})
    assert empty["status"] == wrong_kind["status"] == duplicate["status"] == "fail"


def _fixture(tmp_path: Path, *, pack_mutator=None, evaluation_mutator=None, canonical_output=False, output_ref_mutator=None):
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 500))
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(Endpoint("offline", "deterministic"), Endpoint("loop", "ollama_local", base_url="http://127.0.0.1:11434"), Endpoint("codex", "codex_cli")),
        model_targets=(ModelTarget("offline-default", "offline", "fixture", ("text",), 128), ModelTarget("qwen", "loop", "qwen:latest", ("text",), 128, model_digest="a" * 64), ModelTarget("luna", "codex", "gpt-luna", ("text",), 128)),
        profiles=(Profile("default", "offline-default", "offline-default", "offline-default"),),
    )
    run_setup(config)
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: next(values))
    created = create_offline(home_root=home, requested_target=target, name="comparison-test", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    workpad = next(path for path in (tmp_path / "workpads").glob("projects/*/gigs/*") if path.name == created.gig_id)
    migrate_workpad_layout(workpad=workpad, project_id=created.project_id, gig_id=created.gig_id, uuid_factory=lambda: next(values))
    source_root = tmp_path / "comparison-definition"
    definition = _write_definition(source_root, workpad, created.gig_id)
    definition_payload = json.loads(definition.read_text())
    descriptor = next(item for item in definition_payload["graphs"] if item["graph_id"] == "career")
    graph_path = source_root / descriptor["goal_graph"]["path"]
    graph_payload = json.loads(graph_path.read_text())
    pack_payload = dict(load_evaluation_pack().payload)
    pack_payload["owner"] = created.gig_id
    pack_payload["selected_graph"] = {**pack_payload["selected_graph"], "graph_id": graph_payload["graph_id"], "graph_version": graph_payload["graph_version"]}
    pack_payload["goal_id"] = graph_payload["goals"][0]["goal_id"]
    if pack_mutator is not None:
        pack_mutator(pack_payload)
    # Keep the journaled source human-readable.  The published pack identity
    # remains canonical, so reload must authenticate both representations.
    pack_bytes = json.dumps(pack_payload, indent=2, ensure_ascii=False).encode()
    (source_root / "evaluation-pack.json").write_bytes(pack_bytes)
    if canonical_output or output_ref_mutator is not None:
        for item in definition_payload["graphs"]:
            output_ref = item["output_contract"]
            output_data = (source_root / output_ref["path"]).read_bytes()
            if canonical_output:
                output_ref["canonical_sha256"] = canonical_json_digest(parse_json_bytes(output_data))
            if output_ref_mutator is not None:
                output_ref_mutator(output_ref)
    imported = import_run_input(home_root=home, requested_target=target, gig_id=created.gig_id, data=pack_bytes, label="synthetic evaluation pack", uuid_factory=lambda: next(values))
    local_output_ref = dict(descriptor["output_contract"])
    evaluation_payload = {"schema_version": "1.0", "kind": "evaluation_contract", "gig_id": created.gig_id, "fields": ["deterministic"], "runtime_comparison": {"output_contract_version": pack_payload["selected_graph"]["output_contract_version"], "output_contract_ref": local_output_ref}, "pack_ref": {"path": f"run-inputs/{imported.item_id}/source.txt", "content_sha256": digest_imported_bytes(pack_bytes), "media_type": "text/plain", "size_bytes": len(pack_bytes)}}
    if evaluation_mutator is not None:
        evaluation_mutator(evaluation_payload)
    evaluation_bytes = canonical_json_bytes(evaluation_payload)
    career_evaluation_path = source_root / "career-evaluation.json"
    career_evaluation_path.write_bytes(evaluation_bytes)
    for item in definition_payload["graphs"]:
        item["evaluation_contract"] = {"path": career_evaluation_path.name, "content_sha256": digest_imported_bytes(evaluation_bytes), "media_type": "application/json", "size_bytes": len(evaluation_bytes)}
    definition.write_bytes(canonical_json_bytes(definition_payload))
    proposed = propose_graph_set_offline(home_root=home, requested_target=target, gig_id=created.gig_id, definition_path=definition, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=proposed.proposal_id, uuid_factory=lambda: next(values))
    return home, target, config, created.gig_id, values


@pytest.mark.parametrize(
    "mutator",
    (
        lambda pack: pack["selected_graph"].update(output_contract_version="r6-output:1"),
        lambda pack: pack["cases"][0]["output_contract"]["criteria"][0].update(criterion_id="not_the_grader_key"),
        lambda pack: pack["cases"][0]["output_contract"]["criteria"].append({"criterion_id": "extra", "description": "Unexpected public criterion."}),
        lambda pack: pack["cases"][0]["output_contract"]["criteria"].clear(),
    ),
)
def test_public_comparison_refuses_incoherent_approved_pack_before_invocation(tmp_path: Path, mutator) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path, pack_mutator=mutator)
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    before_runs = sorted(path.relative_to(workpad).as_posix() for path in (workpad / "runs").rglob("*") if path.exists())
    before_comparisons = sorted(path.relative_to(workpad).as_posix() for path in (workpad / "comparisons").rglob("*") if path.exists())
    calls: list[str] = []

    def invoke(**kwargs):
        calls.append(kwargs["model_target"])
        raise AssertionError("incoherent pack reached invocation")

    with pytest.raises(RuntimeComparisonError, match="output contract|expected grader criteria|Gig-owned evaluation pack is invalid"):
        run_comparison(
            home_root=home, requested_target=target, gig_id=gig_id, config=config,
            local_target="qwen", luna_target="luna", graph_selector="career",
            operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}},
            invocation_service=invoke, uuid_factory=lambda: next(values),
        )
    after_runs = sorted(path.relative_to(workpad).as_posix() for path in (workpad / "runs").rglob("*") if path.exists())
    after_comparisons = sorted(path.relative_to(workpad).as_posix() for path in (workpad / "comparisons").rglob("*") if path.exists())
    assert calls == []
    assert before_runs == after_runs
    assert before_comparisons == after_comparisons


@pytest.mark.parametrize(
    "mutator",
    (
        lambda reference: reference.update(path="forged/output.json"),
        lambda reference: reference.update(content_sha256="sha256:" + "0" * 64),
        lambda reference: reference.update(size_bytes=reference["size_bytes"] + 1),
        lambda reference: reference.update(media_type="text/plain"),
        lambda reference: reference.update(forged=True),
        lambda reference: reference.pop("path"),
    ),
)
def test_public_graph_proposal_rejects_malformed_local_output_binding_before_publication(tmp_path: Path, mutator) -> None:
    with pytest.raises(LifecycleError, match="reference shape or identity"):
        _fixture(tmp_path, evaluation_mutator=lambda payload: mutator(payload["runtime_comparison"]["output_contract_ref"]))
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    active = parse_json_bytes((workpad / "manifests/active-gig-version.json").read_bytes())
    assert isinstance(active, dict) and active["active_version"] == 1
    assert not (workpad / "manifests/graph-sets").exists()


def test_v2_first_graph_proposal_check_and_reject_selects_exact_gig(tmp_path: Path) -> None:
    home, target, project_id, gig_id, workpad, _other = _bound_defaults(tmp_path)
    definition = _write_first_definition(tmp_path / "first-definition", gig_id=gig_id, project_id=project_id)
    proposal = propose_first_graph_set_offline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        definition_path=definition,
        uuid_factory=uuid.uuid4,
    )
    runner = CliRunner()
    checked = runner.invoke(cli, ["check", gig_id, "--home", str(home), "--target", str(target), "--json"])
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.output)["valid"] is True
    wrong_proposal = runner.invoke(cli, ["reject", "gp_00000000-0000-4000-8000-000000000099", "--gig", gig_id, "--reason", "wrong proposal", "--home", str(home), "--target", str(target)])
    assert wrong_proposal.exit_code != 0
    wrong_gig = runner.invoke(cli, ["reject", proposal.proposal_id, "--gig", _other.name, "--reason", "wrong gig", "--home", str(home), "--target", str(target)])
    assert wrong_gig.exit_code != 0
    rejected = runner.invoke(cli, ["reject", proposal.proposal_id, "--gig", gig_id, "--reason", "synthetic v2 rejection", "--home", str(home), "--target", str(target)])
    assert rejected.exit_code == 0, rejected.output
    proposal_payload = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(proposal_payload, dict) and proposal_payload["status"] == "rejected"
    assert not (workpad / "manifests" / "active-gig-version.json").exists()
    assert not (_other / "manifests" / "active-gig-version.json").exists()


def test_reject_preserves_active_version_refusal(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)
    workpad = next(path for path in (tmp_path / "workpads").glob("projects/*/gigs/*") if path.name == gig_id)
    source_root = tmp_path / "amendment-definition"
    definition = _write_definition(source_root, workpad, gig_id, suffix="active")
    proposal = propose_graph_set_offline(home_root=home, requested_target=target, gig_id=gig_id, definition_path=definition, uuid_factory=lambda: next(values))
    rejected = CliRunner().invoke(cli, ["reject", proposal.proposal_id, "--gig", gig_id, "--reason", "active refusal", "--home", str(home), "--target", str(target)])
    assert rejected.exit_code != 0
    assert "active Gig version" in rejected.output
    payload = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(payload, dict) and payload["status"] == "proposed"


def test_valid_optional_output_canonical_digest_survives_staging_and_comparison(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path, canonical_output=True)
    captured: list[str] = []

    def invoke(**kwargs):
        captured.append(kwargs["prompt"])
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        expected = {"posting_supported": ("pass", "location", "supported", "E1", []), "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]), "finalized_not_applied": ("pass", "application_state", "supported", "E3", [])}[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        output = json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})
        return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000999", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output, "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    result = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke, uuid_factory=lambda: next(values))
    assert result["status"] == "succeeded" and len(captured) == 6
    workpad = next(path for path in (tmp_path / "workpads").glob("projects/*/gigs/*") if path.name == gig_id)
    graph_set_ref = result["authority"]["graph_set_ref"]
    graph_set = parse_json_bytes((workpad / graph_set_ref["path"]).read_bytes())
    assert isinstance(graph_set, dict)
    descriptor = next(item for item in graph_set["graphs"] if item["graph_id"] == "career")
    evaluation = parse_json_bytes((workpad / descriptor["evaluation_contract"]["path"]).read_bytes())
    assert isinstance(evaluation, dict)
    assert descriptor["output_contract"].get("canonical_sha256")
    assert evaluation["runtime_comparison"]["output_contract_ref"] == descriptor["output_contract"]


def test_false_optional_output_canonical_digest_rejected_before_publication(tmp_path: Path) -> None:
    with pytest.raises(LifecycleError, match="canonical artifact digest is not authenticated"):
        _fixture(tmp_path, output_ref_mutator=lambda reference: reference.update(canonical_sha256="sha256:" + "0" * 64))


def test_historical_v1_pending_proposal_check_and_reject_remain_readable(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 30))
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: next(values))
    created = create_offline(home_root=home, requested_target=target, name="legacy-pending", open_editor=False, uuid_factory=lambda: next(values))
    checked = CliRunner().invoke(cli, ["check", created.gig_id, "--home", str(home), "--target", str(target), "--json"])
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.output)["valid"] is True
    rejected = CliRunner().invoke(cli, ["reject", created.proposal_id, "--gig", created.gig_id, "--reason", "legacy v1 rejection", "--home", str(home), "--target", str(target)])
    assert rejected.exit_code == 0, rejected.output
    workpad = next(path for path in (tmp_path / "workpads").glob("projects/*/gigs/*") if path.name == created.gig_id)
    payload = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(payload, dict) and payload["status"] == "rejected"


def test_historical_pack_without_output_contract_remains_readable() -> None:
    pack = load_evaluation_pack()
    legacy = deepcopy(pack.payload)
    legacy["selected_graph"] = {**legacy["selected_graph"], "output_contract_version": "r6-output:1"}
    for case in legacy["cases"]:
        case.pop("output_contract", None)
    # Validate through the production pack validator without writing or
    # rewriting any historical bytes.
    loaded = validate_evaluation_pack(legacy)
    assert loaded.payload["selected_graph"]["output_contract_version"] == "r6-output:1"
    assert _comparison_prompt(legacy["cases"][0]) == legacy["cases"][0]["prompt"]


def test_comparison_has_two_durable_runs_and_reload_is_exact(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)
    captured_prompts: list[str] = []

    def invoke(**kwargs):
        captured_prompts.append(kwargs["prompt"])
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        expected = {"posting_supported": ("pass", "location", "supported", "E1", []), "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]), "finalized_not_applied": ("pass", "application_state", "supported", "E3", [])}[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        output = json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})
        execution = ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000999", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output, "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())
        return execution

    result = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke, uuid_factory=lambda: next(values))
    assert result["status"] == "succeeded"
    assert len({item["run_id"] for item in result["attempts"]}) == 2
    status = comparison_status(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=result["comparison_id"])
    assert all(len(item["completed_cases"]) == 3 for item in status["attempts"])
    assert show_comparison(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=result["comparison_id"]) == result
    fresh_show = subprocess.run([sys.executable, "-c", "from gigai.cli import cli; cli()", "comparison", "show", result["comparison_id"], "--gig", gig_id, "--home", str(home), "--target", str(target), "--json"], check=True, capture_output=True, text=True)
    fresh_status = subprocess.run([sys.executable, "-c", "from gigai.cli import cli; cli()", "comparison", "status", result["comparison_id"], "--gig", gig_id, "--home", str(home), "--target", str(target), "--json"], check=True, capture_output=True, text=True)
    assert json.loads(fresh_show.stdout)["comparison_id"] == result["comparison_id"]
    assert json.loads(fresh_status.stdout)["status"] == "succeeded"
    assert len(captured_prompts) == 6
    assert all('Top-level fields: "verdict", "criteria", "unsupported_claims".' in prompt for prompt in captured_prompts)
    assert all('"criterion_evidence"' not in prompt and '"expected"' not in prompt for prompt in captured_prompts)
    assert any("location" in prompt for prompt in captured_prompts)
    assert any("candidate_duration" in prompt for prompt in captured_prompts)


def test_reader_rejects_changed_pack_bytes_forged_refs_and_foreign_authority(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)

    def invoke(**kwargs):
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        expected = {
            "posting_supported": ("pass", "location", "supported", "E1", []),
            "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]),
            "finalized_not_applied": ("pass", "application_state", "supported", "E3", []),
        }[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        output = json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})
        return ModelInvocationExecution(
            {"invocation_id": "inv_00000000-0000-4000-8000-000000000991", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None},
            InvocationResult("success", output, "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, (),
        )

    result = run_comparison(
        home_root=home, requested_target=target, gig_id=gig_id, config=config,
        local_target="qwen", luna_target="luna", graph_selector="career",
        operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}},
        invocation_service=invoke, uuid_factory=lambda: next(values),
    )
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    mutations = (
        ("changed raw bytes", lambda item: item["pack"]["source_ref"].update(content_sha256="sha256:" + "0" * 64)),
        ("forged canonical ref", lambda item: item["pack"].update(content_sha256="sha256:" + "0" * 64)),
        ("foreign graph", lambda item: item["authority"].update(selected_graph={"graph_id": "graph_foreign", "graph_version": 1})),
        ("foreign goal", lambda item: item["authority"].update(goal_id="goal_foreign")),
        ("foreign consent", lambda item: item["authority"]["consent"]["scope"].update(pack_sha256="sha256:" + "0" * 64)),
    )
    for label, mutate in mutations:
        forged = deepcopy(result)
        forged["comparison_id"] = generate_entity_id(EntityPrefix.COMPARISON, is_persisted=lambda _value: False, uuid_factory=lambda: next(values))
        mutate(forged)
        record_transition(
            workpad=workpad, project_id=result["project_id"], gig_id=gig_id,
            handoff_id=generate_entity_id(EntityPrefix.HANDOFF, is_persisted=lambda _value: False, uuid_factory=lambda: next(values)),
            transition="comparison_published", body=f"Malformed {label} fixture.",
            artifacts=(JournalArtifact(f"comparisons/{forged['comparison_id']}.json", canonical_json_bytes(forged)),),
            front_matter={"comparison_id": forged["comparison_id"]},
        )
        with pytest.raises(RuntimeComparisonError):
            show_comparison(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=forged["comparison_id"])


def test_public_setup_roundtrip_and_local_parser_boundaries(tmp_path: Path) -> None:
    digest = "a" * 64
    runner = CliRunner()
    home = tmp_path / "home"
    workpad = tmp_path / "workpads"
    result = runner.invoke(cli, [
        "setup", "--non-interactive", "--json", "--home", str(home),
        "--workpad-root", str(workpad), "--editor", "/usr/bin/true",
        "--endpoint", "local=ollama_local:http://127.0.0.1:11434",
        "--model-target", f"qwen=local:qwen3.8:latest@sha256:{digest}",
        "--create-model-target", "qwen",
    ])
    assert result.exit_code == 0, result.output
    configured = load_config(home)
    endpoint = next(item for item in configured.endpoints if item.name == "local")
    target = next(item for item in configured.model_targets if item.name == "qwen")
    assert (endpoint.adapter, endpoint.base_url) == ("ollama_local", "http://127.0.0.1:11434")
    assert target.model_digest == f"sha256:{digest}"
    assert _parse_endpoint_spec("remote=openai_api:credential").adapter == "openai_api"
    assert _parse_model_target_spec("remote=remote:model", {}, {}).model_digest is None
    for endpoint_spec in ("local=ollama_local:http://localhost:11434", "local=ollama_local:https://example.com:11434"):
        rejected = runner.invoke(cli, ["setup", "--non-interactive", "--home", str(tmp_path / endpoint_spec.replace('/', '_')), "--endpoint", endpoint_spec])
        assert rejected.exit_code != 0
    missing_digest = runner.invoke(cli, [
        "setup", "--non-interactive", "--home", str(tmp_path / "missing-digest"),
        "--editor", "/usr/bin/true", "--endpoint", "local=ollama_local:http://127.0.0.1:11434",
        "--model-target", "qwen=local:qwen3.8:latest", "--create-model-target", "qwen",
    ])
    assert missing_digest.exit_code != 0


def test_retry_records_original_failure_and_status_is_durable(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)
    calls: dict[str, int] = {}

    def invoke(**kwargs):
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        calls[case_id] = calls.get(case_id, 0) + 1
        if kwargs["model_target"] == "qwen" and case_id == "posting_supported" and calls[case_id] == 1:
            return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000998", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": {"code": "temporary_transport", "message": "injected retryable transport", "retryable": True}}, InvocationResult("success", "retry placeholder", "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())
        expected = {"posting_supported": ("pass", "location", "supported", "E1", []), "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]), "finalized_not_applied": ("pass", "application_state", "supported", "E3", [])}[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        output = json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})
        return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000997", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output, "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    result = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", retry_failed=True, operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke, uuid_factory=lambda: next(values))
    local_case = next(case for case in result["attempts"][0]["cases"] if case["case_id"] == "posting_supported")
    assert local_case["retries"] == 1
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    stored, _ = read_committed_artifact(workpad=workpad, project_id=result["project_id"], gig_id=gig_id, path=local_case["result"]["path"])
    assert parse_json_bytes(stored)["errors"][0]["code"] == "temporary_transport"
    assert result["attempts"][0]["status"] == "succeeded"
    assert comparison_status(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=result["comparison_id"])["attempts"][0]["retry_counts"]["posting_supported"] == 1


def test_reader_rejects_forged_nested_result_reference(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)

    def invoke(**kwargs):
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        expected = {"posting_supported": ("pass", "location", "supported", "E1", []), "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]), "finalized_not_applied": ("pass", "application_state", "supported", "E3", [])}[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        output = json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})
        return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000996", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output, "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    result = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke, uuid_factory=lambda: next(values))
    forged = deepcopy(result)
    forged["comparison_id"] = generate_entity_id(EntityPrefix.COMPARISON, is_persisted=lambda _value: False, uuid_factory=lambda: next(values))
    forged["attempts"][0]["cases"][0]["result"]["content_sha256"] = "sha256:" + "0" * 64
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    record_transition(workpad=workpad, project_id=result["project_id"], gig_id=gig_id, handoff_id=generate_entity_id(EntityPrefix.HANDOFF, is_persisted=lambda _value: False, uuid_factory=lambda: next(values)), transition="comparison_published", body="Malformed nested comparison reader fixture.", artifacts=(JournalArtifact(f"comparisons/{forged['comparison_id']}.json", canonical_json_bytes(forged)),), front_matter={"comparison_id": forged["comparison_id"]})
    try:
        show_comparison(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=forged["comparison_id"])
    except RuntimeComparisonError as exc:
        assert "comparison case result identity" in str(exc)
    else:
        raise AssertionError("forged nested result reference was accepted")


def test_setup_failure_preserves_the_other_attempt(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)

    def invoke(**kwargs):
        if kwargs["model_target"] == "luna":
            raise RuntimeError("injected codex transport failure")
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        expected = {"posting_supported": ("pass", "location", "supported", "E1", []), "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]), "finalized_not_applied": ("pass", "application_state", "supported", "E3", [])}[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        output = json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})
        return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000999", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output, "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    result = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke, uuid_factory=lambda: next(values))
    assert result["status"] == "partial"
    assert result["attempts"][0]["status"] == "succeeded"
    assert result["attempts"][1]["status"] == "failed"
    assert result["attempts"][1]["cases"][0]["result"]["path"].startswith("runs/")


def test_interrupted_comparison_resumes_same_runs_and_reuses_terminal_cases(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)
    calls: dict[tuple[str, str], int] = {}
    interrupted = {"done": False}

    def output_for(case_id: str) -> str:
        expected = {"posting_supported": ("pass", "location", "supported", "E1", []), "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]), "finalized_not_applied": ("pass", "application_state", "supported", "E3", [])}[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        return json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})

    def invoke_first(**kwargs):
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        setup = kwargs["model_target"]
        key = (setup, case_id)
        calls[key] = calls.get(key, 0) + 1
        if setup == "qwen" and case_id == "unsupported_duration" and not interrupted["done"]:
            interrupted["done"] = True
            raise KeyboardInterrupt()
        return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000995", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output_for(case_id), "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    initial = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke_first, uuid_factory=lambda: next(values))
    assert initial["status"] == "interrupted"
    first_run_ids = {item["setup_id"]: item["run_id"] for item in initial["attempts"]}
    checkpoint = comparison_status(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=initial["comparison_id"])
    local_status = next(item for item in checkpoint["attempts"] if item["setup_id"] == "qwen_ollama")
    assert local_status["run_id"] == first_run_ids["qwen_ollama"]
    assert local_status["completed_cases"] == ["posting_supported"]

    def invoke_resume(**kwargs):
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        setup = kwargs["model_target"]
        calls[(setup, case_id)] = calls.get((setup, case_id), 0) + 1
        return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000994", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output_for(case_id), "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    with pytest.raises(RuntimeComparisonError, match="target or settings differ"):
        resume_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, comparison_id=initial["comparison_id"], local_target="qwen", luna_target="luna", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke_resume, retry_failed=True, uuid_factory=lambda: next(values))
    resumed = resume_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, comparison_id=initial["comparison_id"], local_target="qwen", luna_target="luna", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke_resume, uuid_factory=lambda: next(values))
    assert resumed["status"] == "succeeded"
    assert {item["setup_id"]: item["run_id"] for item in resumed["attempts"]} == first_run_ids
    assert calls[("qwen", "posting_supported")] == 1
    assert calls[("qwen", "unsupported_duration")] == 2
    assert show_comparison(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=initial["comparison_id"])["comparison_id"] == initial["comparison_id"]


def test_reader_uses_comparison_publication_head_after_later_nested_replacement(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)

    def invoke(**kwargs):
        case_id = json.loads(kwargs["references"][0].content.decode())["case_id"]
        expected = {"posting_supported": ("pass", "location", "supported", "E1", []), "unsupported_duration": ("fail", "candidate_duration", "unsupported", "E2", ["unsupported"]), "finalized_not_applied": ("pass", "application_state", "supported", "E3", [])}[case_id]
        verdict, criterion, status, evidence, unsupported = expected
        output = json.dumps({"verdict": verdict, "criteria": [{"criterion_id": criterion, "status": status, "evidence_ids": [evidence]}], "unsupported_claims": unsupported})
        return ModelInvocationExecution({"invocation_id": "inv_00000000-0000-4000-8000-000000000993", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}, InvocationResult("success", output, "fixture", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    result = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke, uuid_factory=lambda: next(values))
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    case_ref = result["attempts"][0]["cases"][0]["result"]
    original, _ = read_committed_artifact(workpad=workpad, project_id=result["project_id"], gig_id=gig_id, path=case_ref["path"])
    changed = original.replace(b'"terminal":true', b'"terminal":false')
    record_transition(workpad=workpad, project_id=result["project_id"], gig_id=gig_id, handoff_id=generate_entity_id(EntityPrefix.HANDOFF, is_persisted=lambda _value: False, uuid_factory=lambda: next(values)), transition="goal_failed", body="Later nested replacement fixture.", artifacts=(JournalArtifact(case_ref["path"], changed),), front_matter={"run_id": result["attempts"][0]["run_id"], "outcome": "FAILED"})
    assert show_comparison(home_root=home, requested_target=target, gig_id=gig_id, comparison_id=result["comparison_id"]) == result


def test_real_path_rejects_wrong_observed_adapter_identity_as_structured_failure(tmp_path: Path) -> None:
    home, target, config, gig_id, values = _fixture(tmp_path)

    def invoke(**kwargs):
        output = json.dumps({"verdict": "pass", "criteria": [{"criterion_id": "location", "status": "supported", "evidence_ids": ["E1"]}], "unsupported_claims": []})
        record = {"invocation_id": "inv_00000000-0000-4000-8000-000000000992", "provider_family": "wrong_adapter", "endpoint_identity": "loop", "resolved_model": "qwen:latest", "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cost": None, "currency": None, "cost_status": "unavailable"}, "error": None}
        return ModelInvocationExecution(record, InvocationResult("success", output, "qwen:latest", {}, NormalizedUsage(None, None, None), "unavailable"), None, ())

    result = run_comparison(home_root=home, requested_target=target, gig_id=gig_id, config=config, local_target="qwen", luna_target="luna", graph_selector="career", operator_consent={"action": "runtime_comparison", "actor": {"kind": "operator", "id": "local-user"}}, invocation_service=invoke, uuid_factory=lambda: next(values))
    assert result["attempts"][0]["status"] == "failed"
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    stored, _ = read_committed_artifact(workpad=workpad, project_id=result["project_id"], gig_id=gig_id, path=result["attempts"][0]["cases"][0]["result"]["path"])
    assert parse_json_bytes(stored)["errors"][-1]["code"] == "observed_adapter_mismatch"
