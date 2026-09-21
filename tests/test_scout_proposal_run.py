from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import uuid

import pytest

from gigai import run as run_module
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_front_matter
from gigai.journal import JournalArtifact, JournalConflictError, record_transition
from gigai.lifecycle import approve_offline, propose_graph_set_offline
from gigai.native_records import create_native_record
from gigai.private_records import import_reference
from gigai.run import ProposalRunRequest, RunError, launch_run, read_run_details
from gigai.scout_proposal_execution import (
    ScoutProposalExecutionError,
    execute_local_proposal,
    read_proposal_invocation_attempts,
)

from tests.test_scout07_posting_inputs import _completed_find_jobs
from tests.test_scout03_native_records import _experience, _profile
from tests.test_scout_proposal_execution import _Transport, _config


def _request_inputs(tmp_path: Path):
    resolved, posting, _snapshot, _metadata = _completed_find_jobs(tmp_path)
    home, target = tmp_path / "home", tmp_path / "target"
    preference_path = tmp_path / "preference.txt"
    preference_path.write_text("Minimum salary $120000; remote preferred.\n", encoding="utf-8")
    preference = import_reference(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        kind="resume",
        source=preference_path,
        label="synthetic proposal preference",
    )
    experience = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        content=_experience(answered=True),
        actor={"kind": "operator", "id": "synthetic-user"},
        origin="user_reported",
        operation_key="proposal-run-experience",
    )
    profile = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        content=_profile(),
        actor={"kind": "operator", "id": "synthetic-user"},
        origin="user_reported",
        operation_key="proposal-run-profile",
    )
    selectors = (
        {"purpose": "experience", "selector": {"family": "g45_reference", "id": preference.item_id}},
        {
            "purpose": "experience",
            "selector": {
                "family": "scout_record",
                "record_id": experience.record_id,
                "revision_id": experience.revision_id,
                "scope": {"mode": "saved_default", "task_context_id": None},
            },
        },
        {
            "purpose": "preferences",
            "selector": {
                "family": "scout_record",
                "record_id": profile.record_id,
                "revision_id": profile.revision_id,
                "scope": {"mode": "saved_default", "task_context_id": None},
            },
        },
    )
    subprocess.run(["git", "-C", str(target), "config", "user.name", "Proposal Run Test"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "proposal-run-test@gigai.invalid"], check=True)
    (target / "README.md").write_text("proposal run fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "--quiet", "-m", "proposal run fixture"], check=True)
    return resolved, posting, selectors


def _consent() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "operator_run_consent",
        "action": "run",
        "actor": {"kind": "operator", "id": "local-user"},
        "source": "direct_cli_confirm",
    }


def _repropose_graph_set(
    tmp_path: Path,
    workpad: Path,
    gig_id: str,
    *,
    tailor_alias: bool = False,
    extra_proposal_goal: bool = False,
) -> None:
    """Approve a disposable, real Graph Set variant for authority negatives."""

    current_ref = json.loads(
        (workpad / "manifests" / "active-gig-version.json").read_text()
    )["graph_set"]
    current = json.loads(
        (workpad / str(current_ref["path"])).read_text()
    )
    source_root = tmp_path / "graph-set-definition"
    source_root.mkdir()
    descriptors = []
    for index, raw in enumerate(current["graphs"]):
        if raw.get("graph_id") == "proposal-assessment" and tailor_alias:
            # Remove the canonical member so the Tailor alias is the only
            # resolver match for the requested proposal selector.
            continue
        descriptor = dict(raw)
        graph_ref = raw["goal_graph"]
        graph_data = json.loads((workpad / str(graph_ref["path"])).read_text())
        if raw.get("graph_id") == "proposal-assessment" and extra_proposal_goal:
            extra_id = "goal_00000000-0000-4000-8000-000000009999"
            extra = dict(graph_data["goals"][0])
            extra["goal_id"] = extra_id
            extra["slug"] = "proposal-assessment-extra"
            extra["required"] = False
            extra["display_ordinal"] = "G99"
            extra["budget"] = {
                **extra["budget"],
                "max_model_calls": 0,
                "max_tokens": 0,
                "max_wall_time_ms": 0,
                "max_cost": "0.00",
            }
            graph_data["goals"] = [*graph_data["goals"], extra]
            original_id = graph_data["goals"][0]["goal_id"]
            graph_data["edges"] = [
                *graph_data["edges"],
                {
                    "edge_id": "edge_00000000-0000-4000-8000-000000009999",
                    "from_goal_id": original_id,
                    "to_goal_id": extra_id,
                    "kind": "dependency",
                    "on_outcomes": ["COMPLETE"],
                    "automatic": True,
                },
            ]
            graph_data["terminal_goal_ids"] = [extra_id]
        for goal_index, goal in enumerate(graph_data["goals"]):
            contract_ref = goal["contract"]
            contract_data = (workpad / str(contract_ref["path"])).read_bytes()
            contract_file = source_root / f"goal-contract-{index}-{goal_index}.md"
            contract_file.write_bytes(contract_data)
            goal["contract"] = {
                "path": contract_file.name,
                "content_sha256": digest_imported_bytes(contract_data),
                "size_bytes": len(contract_data),
            }
        graph_file = source_root / f"graph-{index}.json"
        graph_file.write_bytes(canonical_json_bytes(graph_data))
        descriptor["goal_graph"] = {
            "path": graph_file.name,
            "content_sha256": digest_imported_bytes(graph_file.read_bytes()),
            "size_bytes": graph_file.stat().st_size,
        }
        if raw.get("graph_id") == "tailor-application" and tailor_alias:
            descriptor["aliases"] = ["proposal-assessment"]
        for field in (
            "input_contract", "output_contract", "permitted_reference_contract",
            "review_contract", "evaluation_contract", "completion_evidence_contract",
        ):
            ref = raw[field]
            data = (workpad / str(ref["path"])).read_bytes()
            target = source_root / f"{field}-{index}.json"
            target.write_bytes(data)
            descriptor[field] = {
                "path": target.name,
                "content_sha256": digest_imported_bytes(data),
                "size_bytes": len(data),
            }
        descriptors.append(descriptor)
    definition = {
        "schema_version": "1.0",
        "gig_id": gig_id,
        "graphs": descriptors,
        "shared_policy": current["shared_policy"],
    }
    definition_path = source_root / "definition.json"
    definition_path.write_bytes(canonical_json_bytes(definition))
    proposed = propose_graph_set_offline(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=gig_id,
        definition_path=definition_path,
    )
    approve_offline(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=gig_id,
        proposal_id=proposed.proposal_id,
    )


def test_supported_proposal_entry_allocates_and_terminalizes_real_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: __import__(
            "gigai.adapters.factory", fromlist=["resolve_model_adapter"]
        ).resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
        ),
    )
    assert result.status == "succeeded"
    assert transport.closed
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    assert details["status"] == "succeeded"
    assert details["goal_sets"]["complete"]
    assert not details["goal_sets"]["active"]
    calls = list(transport.calls)
    attempts = read_proposal_invocation_attempts(
        resolved, result.run_id, str(details["goal_sets"]["complete"][0])
    )
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "succeeded"
    head = subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(ScoutProposalExecutionError) as repeated:
        execute_local_proposal(
            resolved=resolved,
            config=config,
            run_id=result.run_id,
            goal_id=str(details["goal_sets"]["complete"][0]),
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
            local_allowed=True,
        )
    assert repeated.value.code == "execution_authority_refused"
    assert transport.calls == calls
    assert subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip() == head
    transitions = []
    for handoff in sorted((resolved.path / "handoffs").glob("*.txt")):
        front, _body = parse_json_front_matter(handoff.read_bytes())
        if front.get("run_id") == result.run_id:
            transitions.append(front.get("transition"))
    assert transitions == [
        "run_started", "goal_started", "proposal_invocation_recorded",
        "goal_completed", "private_record_revised", "run_succeeded",
    ]
    manifest = json.loads((result.run_path / "run-manifest.json").read_text())
    assert manifest["selected_graph_id"] == "proposal-assessment"
    assert manifest["graph_set"]["content_sha256"].startswith("sha256:")
    assert manifest["selection_record"]["path"].endswith("proposal-graph-selection.json")
    assert any(
        ref.get("path") == f"runs/{result.run_id}/sealed/proposal-execution-request.json"
        for ref in manifest["sealed_sources"]
    )
    before_race_head = subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()
    stale_failure = run_module._mark_proposal_goal_failed(
        resolved=resolved,
        run_id=result.run_id,
        gig_version=result.gig_version,
        graph=json.loads((result.run_path / "goal-graph.json").read_text()),
        manifest_digest=digest_imported_bytes((result.run_path / "run-manifest.json").read_bytes()),
        parent_handoff_id=result.terminal.handoff_id if result.terminal else "handoff_missing",
    )
    assert stale_failure is None
    assert subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip() == before_race_head


def test_supported_proposal_entry_invalid_result_finishes_run_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    transport = _Transport({"not": "a bounded proposal"})
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: __import__(
            "gigai.adapters.factory", fromlist=["resolve_model_adapter"]
        ).resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
        ),
    )
    assert result.status == "failed"
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    assert details["status"] == "failed"
    goal = details["goals"][0]
    assert (goal["status"], goal["outcome"]) == ("failed", "FAILED")
    attempts = read_proposal_invocation_attempts(resolved, result.run_id, str(goal["goal_id"]))
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "succeeded"
    transitions = []
    for handoff in sorted((resolved.path / "handoffs").glob("*.txt")):
        front, _body = parse_json_front_matter(handoff.read_bytes())
        if front.get("run_id") == result.run_id:
            transitions.append(front.get("transition"))
    assert transitions.count("goal_failed") == 1
    assert transitions.count("run_failed") == 1
    assert transport.closed


def test_proposal_run_redeems_sealed_inputs_after_request_and_config_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    loads = 0

    def pinned_config(_home: Path):
        nonlocal loads
        loads += 1
        if loads > 1:
            raise AssertionError("proposal execution must not reload mutable config")
        return config

    monkeypatch.setattr(run_module, "load_config", pinned_config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: __import__(
            "gigai.adapters.factory", fromlist=["resolve_model_adapter"]
        ).resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    request = ProposalRunRequest(
        graph_selector="proposal-assessment",
        model_target="local-proposal",
        posting_selector=dict(posting),
        private_selectors=tuple(dict(item) for item in selectors),
    )

    def observer(step: str) -> None:
        if step == "after_run_started_commit":
            request.posting_selector["snapshot_id"] = "tampered-after-seal"
            request.private_selectors[0]["purpose"] = "tampered-after-seal"

    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=request,
        observer=observer,
    )
    assert result.status == "succeeded"
    assert loads == 1
    assert transport.closed


def test_proposal_result_survives_target_mutation_and_run_recovers_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: __import__(
            "gigai.adapters.factory", fromlist=["resolve_model_adapter"]
        ).resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    original_observation = run_module._target_observation
    observations = 0

    def mutate_after_result(workpad):
        nonlocal observations
        observations += 1
        if observations == 2:
            (workpad.target_root / "README.md").write_text("changed after result\n", encoding="utf-8")
        return original_observation(workpad)

    monkeypatch.setattr(run_module, "_target_observation", mutate_after_result)
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
        ),
    )
    assert result.status == "interrupted"
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    assert details["status"] == "interrupted"
    assert details["goal_sets"]["complete"]
    transitions = []
    for handoff in sorted((resolved.path / "handoffs").glob("*.txt")):
        front, _body = parse_json_front_matter(handoff.read_bytes())
        if front.get("run_id") == result.run_id:
            transitions.append(front.get("transition"))
    assert transitions.count("goal_completed") == 1
    assert transitions.count("run_interrupted") == 1
    assert len(read_proposal_invocation_attempts(
        resolved, result.run_id, str(details["goal_sets"]["complete"][0])
    )) == 1


def test_injected_final_journal_conflict_recovers_terminal_run_without_duplicate_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: __import__(
            "gigai.adapters.factory", fromlist=["resolve_model_adapter"]
        ).resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    final_calls = 0

    def conflict_after_result(*_args: object, **_kwargs: object) -> None:
        nonlocal final_calls
        final_calls += 1
        raise JournalConflictError("injected final proposal journal conflict")

    monkeypatch.setattr(run_module, "_finish_run", conflict_after_result)
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
        ),
    )
    assert final_calls == 1
    assert result.status == "interrupted"
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    assert details["status"] == "interrupted"
    goal_id = str(details["goal_sets"]["complete"][0])
    assert len(read_proposal_invocation_attempts(resolved, result.run_id, goal_id)) == 1
    transitions = []
    for handoff in sorted((resolved.path / "handoffs").glob("*.txt")):
        front, _body = parse_json_front_matter(handoff.read_bytes())
        if front.get("run_id") == result.run_id:
            transitions.append(front.get("transition"))
    assert transitions.count("goal_completed") == 1
    assert transitions.count("run_interrupted") == 1
    assert transitions.count("goal_failed") == 0
    assert transport.closed


def _publish_committed_receipt_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> tuple[object, str]:
    resolved, posting, selectors = _request_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: __import__(
            "gigai.adapters.factory", fromlist=["resolve_model_adapter"]
        ).resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
        ),
    )
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    goal_id = str(details["goal_sets"]["complete"][0])
    original = read_proposal_invocation_attempts(resolved, result.run_id, goal_id)[0]
    old_request = original["request"]["request_artifact"]
    old_response = next(
        item["value"]
        for item in original["extensions"]
        if item["name"] == "response_artifact"
    )
    invocation_id = "inv_00000000-0000-4000-8000-000000009998"
    request_path = f"runs/{result.run_id}/model-invocations/{invocation_id}/request.json"
    response_path = f"runs/{result.run_id}/model-invocations/{invocation_id}/response.json"
    record_path = f"runs/{result.run_id}/model-invocations/{invocation_id}/record.json"
    request_bytes = subprocess.check_output(
        ["git", "-C", str(resolved.path), "show", f"HEAD:{old_request['path']}"]
    )
    response_bytes = subprocess.check_output(
        ["git", "-C", str(resolved.path), "show", f"HEAD:{old_response['path']}"]
    )
    forged = deepcopy(original)
    forged["invocation_id"] = invocation_id
    actor = {
        "kind": "gigai",
        "id": "scout-proposal-execution",
        "model_target": "local-proposal",
    }
    if tamper == "target":
        forged["configured_selector"] = "not-the-receipt-target"
    elif tamper == "request":
        request_payload = json.loads(request_bytes)
        request_payload["input_sha256"] = "sha256:" + "0" * 64
        request_bytes = canonical_json_bytes(request_payload)
    elif tamper == "response":
        old_response = {**dict(old_response), "content_sha256": "sha256:" + "0" * 64}
    elif tamper == "record":
        forged["goal_id"] = "goal_00000000-0000-4000-8000-000000009996"
    elif tamper == "actor":
        actor["id"] = "untrusted-writer"
    elif tamper == "source":
        descriptor = dict(forged["request"]["selected_source_descriptors"][0])
        descriptor["identity_sha256"] = "sha256:" + "0" * 64
        forged["request"]["selected_source_descriptors"][0] = descriptor
    elif tamper == "ref":
        forged["request"]["request_artifact"] = {
            **dict(old_request),
            "path": f"runs/{result.run_id}/model-invocations/{invocation_id}/wrong.json",
        }
    else:
        raise AssertionError(f"unknown receipt tamper: {tamper}")
    forged["request"]["request_artifact"] = {
        **dict(old_request),
        "path": (
            request_path
            if tamper != "ref"
            else f"runs/{result.run_id}/model-invocations/{invocation_id}/wrong.json"
        ),
    }
    for extension in forged["extensions"]:
        if extension["name"] == "response_artifact":
            extension["value"] = {**dict(old_response), "path": response_path}
    record_bytes = canonical_json_bytes(forged)
    refs = [
        {
            "path": path,
            "content_sha256": digest_imported_bytes(data),
            "media_type": "application/json",
            "size_bytes": len(data),
        }
        for path, data in (
            (request_path, request_bytes),
            (record_path, record_bytes),
            (response_path, response_bytes),
        )
    ]
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id="handoff_00000000-0000-4000-8000-000000009997",
        transition="proposal_invocation_recorded",
        body=f"Test-only committed receipt {tamper} mismatch.",
        artifacts=tuple(
            JournalArtifact(path, data)
            for path, data in (
                (request_path, request_bytes),
                (record_path, record_bytes),
                (response_path, response_bytes),
            )
        ),
        front_matter={
            "run_id": result.run_id,
            "goal_id": goal_id,
            "invocation_id": invocation_id,
            "model_target": "local-proposal",
            "source": "scout-proposal-execution",
            "actor": actor,
            "outcome": "INVOCATION_EVIDENCE",
            "artifact_refs": refs,
        },
    )
    return resolved, result.run_id


@pytest.mark.parametrize(
    "tamper",
    ["target", "request", "response", "record", "actor", "source", "ref"],
)
def test_committed_receipt_tamper_variants_are_refused_by_pinned_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    resolved, run_id = _publish_committed_receipt_tamper(tmp_path, monkeypatch, tamper)
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=run_id,
    )
    goal_id = str(details["goal_sets"]["complete"][0])
    with pytest.raises(ScoutProposalExecutionError) as refused:
        read_proposal_invocation_attempts(resolved, run_id, goal_id)
    assert refused.value.code == "invocation_evidence_invalid"


def test_alias_only_proposal_selector_refuses_before_run_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    before = subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(RunError, match="graph_selection_invalid"):
        launch_run(
            home_root=tmp_path / "home",
            requested_target=tmp_path / "target",
            gig_id=resolved.gig_id,
            wait=True,
            operator_consent=_consent(),
            proposal_execution=ProposalRunRequest(
                graph_selector="assessment",
                model_target="local-proposal",
                posting_selector=posting,
                private_selectors=selectors,
            ),
        )
    assert subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip() == before
    assert not list((resolved.path / "runs").glob("run_*/sealed/proposal-execution-request.json"))


def test_real_tailor_descriptor_alias_is_not_proposal_authority(
    tmp_path: Path,
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    _repropose_graph_set(
        tmp_path,
        resolved.path,
        resolved.gig_id,
        tailor_alias=True,
    )
    before = subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(RunError, match="proposal_run_authority_refused"):
        launch_run(
            home_root=tmp_path / "home",
            requested_target=tmp_path / "target",
            gig_id=resolved.gig_id,
            wait=True,
            operator_consent=_consent(),
            proposal_execution=ProposalRunRequest(
                graph_selector="proposal-assessment",
                model_target="local-proposal",
                posting_selector=posting,
                private_selectors=selectors,
            ),
        )
    assert subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip() == before
    assert not list((resolved.path / "runs").glob("run_*/sealed/proposal-execution-request.json"))


def test_extra_proposal_goal_refuses_before_run_allocation(
    tmp_path: Path,
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    _repropose_graph_set(
        tmp_path,
        resolved.path,
        resolved.gig_id,
        extra_proposal_goal=True,
    )
    before = subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(RunError, match="proposal_run_authority_refused"):
        launch_run(
            home_root=tmp_path / "home",
            requested_target=tmp_path / "target",
            gig_id=resolved.gig_id,
            wait=True,
            operator_consent=_consent(),
            proposal_execution=ProposalRunRequest(
                graph_selector="proposal-assessment",
                model_target="local-proposal",
                posting_selector=posting,
                private_selectors=selectors,
            ),
        )
    assert subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip() == before
    assert not list((resolved.path / "runs").glob("run_*/sealed/proposal-execution-request.json"))


def test_competing_cancel_preserves_invocation_evidence_without_goal_terminal_duplication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors = _request_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _home: config)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: __import__(
            "gigai.adapters.factory", fromlist=["resolve_model_adapter"]
        ).resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    import gigai.scout_proposal_execution as proposal_module

    def cancel_before_domain_publication(**kwargs):
        run_id = str(kwargs["run_id"])
        details_path = resolved.path / "runs" / run_id / "run-details.json"
        details = json.loads(details_path.read_text())
        goal = details["goals"][0]
        goal.update({"status": "cancelled", "outcome": "CANCELLED", "finished_at": run_module._now()})
        run_module._refresh_details(
            details,
            {item["goal_id"]: item for item in details["goals"]},
            json.loads((resolved.path / "runs" / run_id / "goal-graph.json").read_text()),
            "cancelled",
        )
        details["status"] = "cancelled"
        details["finished_at"] = run_module._now()
        terminal_path = f"runs/{run_id}/terminal-handoff.md"
        terminal_bytes = f"Run {run_id} cancelled by competing owner.\n".encode("utf-8")
        details["terminal_handoff"] = {
            "path": terminal_path,
            "content_sha256": digest_imported_bytes(terminal_bytes),
            "media_type": "text/markdown",
            "size_bytes": len(terminal_bytes),
        }
        details["workpad_commit"] = subprocess.check_output(
            ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
        ).strip()
        record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=run_module._new_id(run_module.EntityPrefix.HANDOFF, uuid.uuid4),
            transition="run_cancelled",
            body=f"Run {run_id} cancelled by competing owner.",
            artifacts=(
                JournalArtifact(f"runs/{run_id}/run-details.json", canonical_json_bytes(details)),
                JournalArtifact(terminal_path, terminal_bytes),
            ),
            front_matter={"run_id": run_id, "outcome": "CANCELLED", "actor": {"kind": "operator", "id": "local-user"}},
        )
        raise ScoutProposalExecutionError(
            "execution_publication_refused", "competing cancellation won before domain publication"
        )

    monkeypatch.setattr(proposal_module, "_publish_result", cancel_before_domain_publication)
    result = launch_run(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        wait=True,
        operator_consent=_consent(),
        proposal_execution=ProposalRunRequest(
            graph_selector="proposal-assessment",
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
        ),
    )
    assert result.status == "cancelled"
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=result.run_id,
    )
    goal_id = str(details["goals"][0]["goal_id"])
    assert details["status"] == "cancelled"
    assert not details["goal_sets"]["complete"]
    transitions = []
    for handoff in sorted((resolved.path / "handoffs").glob("*.txt")):
        front, _body = parse_json_front_matter(handoff.read_bytes())
        if front.get("run_id") == result.run_id:
            transitions.append(front.get("transition"))
    assert transitions.count("run_cancelled") == 1
    assert transitions.count("goal_completed") == 0
    assert transitions.count("goal_failed") == 0
    assert len(read_proposal_invocation_attempts(resolved, result.run_id, goal_id)) == 1
    assert transport.closed
