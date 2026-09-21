"""Adversarial journal and authority checks for the real Scout v2 Run path."""

from __future__ import annotations

from copy import deepcopy
import base64
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from gigai import external_recording
from gigai.canonical import digest_imported_bytes

_FLOW_SPEC = spec_from_file_location(
    "_scout06_research_run_flow_helpers", Path(__file__).with_name("test_scout06_research_run_flow.py")
)
assert _FLOW_SPEC and _FLOW_SPEC.loader
_FLOW = module_from_spec(_FLOW_SPEC)
_FLOW_SPEC.loader.exec_module(_FLOW)
_envelope = _FLOW._envelope
_started_v2_run = _FLOW._started_v2_run
_valid_v2_checkpoint = _FLOW._valid_v2_checkpoint


def _state(home: Path, target: Path, gig_id: str) -> dict[str, bytes]:
    resolved = external_recording._resolved(
        home_root=home, requested_target=target, gig_id=gig_id
    )
    return dict(external_recording._snapshot(resolved).artifacts)


def _record_checkpoint(
    home: Path, target: Path, gig_id: str, request: dict[str, object]
) -> external_recording.ExternalResult:
    return external_recording.checkpoint_v2(
        home_root=home, requested_target=target, gig_id=gig_id, envelope=request
    )


def _submit_request(
    started: dict[str, object], checkpoint: dict[str, object], *, operation_key: str
) -> dict[str, object]:
    output, check = checkpoint["artifacts"]
    return _envelope(
        operation_key,
        {
            "run_id": started["run_id"],
            "parent_checkpoint": checkpoint["checkpoint_id"],
            "output_refs": [output],
            "check_refs": [check["sidecar"]],
            "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        },
    )


def test_checkpoint_replay_is_exact_and_does_not_republish(tmp_path: Path) -> None:
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    request = _valid_v2_checkpoint(plan=plan, started=started, operation_key="exact")
    first = _record_checkpoint(home, target, gig_id, request)
    committed = _state(home, target, gig_id)
    replay = _record_checkpoint(home, target, gig_id, deepcopy(request))
    assert first.created
    assert not replay.created
    assert replay.payload == first.payload
    assert _state(home, target, gig_id) == committed


def test_changed_operation_intent_and_checkpoint_parent_cas_refuse_without_mutation(
    tmp_path: Path,
) -> None:
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    request = _valid_v2_checkpoint(plan=plan, started=started, operation_key="same-key")
    first = _record_checkpoint(home, target, gig_id, request)
    committed = _state(home, target, gig_id)

    changed = deepcopy(request)
    changed["input"]["reason"] = "changed intent"
    with pytest.raises(external_recording.ExternalRecordingError) as intent:
        _record_checkpoint(home, target, gig_id, changed)
    assert intent.value.code == "external_operation_conflict"
    assert _state(home, target, gig_id) == committed

    cas = _valid_v2_checkpoint(plan=plan, started=started, operation_key="new-key")
    cas["input"]["parent_checkpoint"] = "checkpoint_00000000-0000-4000-8000-000000000099"
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        _record_checkpoint(home, target, gig_id, cas)
    assert refused.value.code == "external_checkpoint_conflict"
    assert first.payload["sequence"] == 1
    assert _state(home, target, gig_id) == committed


def _mutate_missing_domain(request: dict[str, object]) -> None:
    del request["input"]["artifact_refs"][0]["domain_sidecar"]


def _mutate_extra_domain(request: dict[str, object]) -> None:
    request["input"]["artifact_refs"][0]["domain_sidecar"]["value"]["unexpected"] = True


def _mutate_missing_supporting(request: dict[str, object]) -> None:
    request["input"]["artifact_refs"][0]["supporting_artifacts"].pop()


def _mutate_extra_supporting(request: dict[str, object]) -> None:
    extra = b"unused supporting evidence"
    request["input"]["artifact_refs"][0]["supporting_artifacts"].append(
        {
            "artifact_id": "unused",
            "media_type": "text/plain",
            "content_base64": base64.b64encode(extra).decode(),
            "content_sha256": digest_imported_bytes(extra),
            "size_bytes": len(extra),
        }
    )


def _mutate_corrupt_supporting(request: dict[str, object]) -> None:
    request["input"]["artifact_refs"][0]["supporting_artifacts"][0]["content_base64"] = base64.b64encode(b"tampered").decode()


@pytest.mark.parametrize(
    "mutation",
    [
        _mutate_missing_domain,
        _mutate_extra_domain,
        _mutate_missing_supporting,
        _mutate_extra_supporting,
        _mutate_corrupt_supporting,
    ],
    ids=["missing-domain", "extra-domain", "missing-supporting", "extra-supporting", "corrupt-supporting"],
)
def test_malformed_domain_evidence_is_atomic(
    tmp_path: Path, mutation
) -> None:
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    request = _valid_v2_checkpoint(plan=plan, started=started, operation_key="malformed")
    mutation(request)
    before = _state(home, target, gig_id)
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        _record_checkpoint(home, target, gig_id, request)
    assert refused.value.code in {
        "external_domain_invalid",
        "external_invocation_invalid",
        "research_domain_supporting_mismatch",
        "research_domain_invalid",
    }
    assert _state(home, target, gig_id) == before


@pytest.mark.parametrize("forgery", ["digest", "tuple"], ids=["forged-digest", "forged-tuple"])
def test_submit_rereads_committed_tuple_and_refuses_forged_refs(
    tmp_path: Path, forgery: str
) -> None:
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    checkpoint = _record_checkpoint(
        home,
        target,
        gig_id,
        _valid_v2_checkpoint(plan=plan, started=started, operation_key="checkpoint"),
    )
    request = _submit_request(started, checkpoint.payload, operation_key=f"submit-{forgery}")
    if forgery == "digest":
        request["input"]["output_refs"][0]["markdown"]["content_sha256"] = "sha256:" + "0" * 64
    else:
        request["input"]["output_refs"][0]["kind"] = "forged-research"
    before = _state(home, target, gig_id)
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        external_recording.submit_v2(
            home_root=home, requested_target=target, gig_id=gig_id, envelope=request
        )
    assert refused.value.code in {"external_output_invalid", "external_output_missing"}
    assert _state(home, target, gig_id) == before


def test_v1_checkpoint_and_submit_cannot_downgrade_v2_run(tmp_path: Path) -> None:
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    before = _state(home, target, gig_id)
    v1_checkpoint = _envelope(
        "v1-checkpoint",
        {
            "run_id": started["run_id"],
            "parent_checkpoint": None,
            "questions": [],
            "artifact_refs": [],
            "reason": "legacy downgrade probe",
        },
    )
    with pytest.raises(external_recording.ExternalRecordingError) as checkpoint_refused:
        external_recording.checkpoint(
            home_root=home, requested_target=target, gig_id=gig_id, envelope=v1_checkpoint
        )
    assert checkpoint_refused.value.code == "external_protocol_downgrade"
    v1_submit = _envelope(
        "v1-submit",
        {
            "run_id": started["run_id"],
            "parent_checkpoint": None,
            "output_refs": [],
            "check_refs": [],
            "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        },
    )
    with pytest.raises(external_recording.ExternalRecordingError) as submit_refused:
        external_recording.submit(
            home_root=home, requested_target=target, gig_id=gig_id, envelope=v1_submit
        )
    assert submit_refused.value.code == "external_protocol_downgrade"
    assert _state(home, target, gig_id) == before


def test_completed_run_history_is_stable_after_exact_submit_replay(tmp_path: Path) -> None:
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    checkpoint = _record_checkpoint(
        home,
        target,
        gig_id,
        _valid_v2_checkpoint(plan=plan, started=started, operation_key="checkpoint"),
    )
    request = _submit_request(started, checkpoint.payload, operation_key="submit")
    first = external_recording.submit_v2(
        home_root=home, requested_target=target, gig_id=gig_id, envelope=request
    )
    completed = _state(home, target, gig_id)
    replay = external_recording.submit_v2(
        home_root=home, requested_target=target, gig_id=gig_id, envelope=deepcopy(request)
    )
    assert first.created
    assert not replay.created
    assert replay.payload == first.payload
    assert _state(home, target, gig_id) == completed
    inspected = external_recording.inspect(
        home_root=home, requested_target=target, gig_id=gig_id, run_id=started["run_id"]
    )
    assert inspected["run"]["status"] == "succeeded"
    assert len(inspected["receipts"]) == 1


@pytest.mark.parametrize("operation", ["checkpoint", "submit"])
@pytest.mark.parametrize("binding_member", ["validator_source_ref", "schema_ref"])
def test_committed_domain_binding_change_before_publication_refuses_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, binding_member: str
) -> None:
    """Inject only a publication race after the real bridge has accepted input.

    The mutation changes the committed resource's working mirror, not Git
    history. A compliant competing journal writer is already excluded by flock.
    No validator result is replaced by a fabricated acceptance.
    """
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    request = _valid_v2_checkpoint(plan=plan, started=started, operation_key="race")
    if operation == "submit":
        checkpoint = _record_checkpoint(home, target, gig_id, request)
        request = _submit_request(started, checkpoint.payload, operation_key="submit-race")
    resolved = external_recording._resolved(
        home_root=home, requested_target=target, gig_id=gig_id
    )
    contract = external_recording._approved_contract(resolved, plan["output_contract"])["content"]
    source_ref = contract["domains"]["research"][binding_member]
    source_path = resolved.path / source_ref["path"]
    original_journaled = external_recording._journaled

    def mutate_after_validation(writer, *, transition, handoff_id, body, artifacts, **kwargs):
        source_path.write_bytes(source_path.read_bytes() + b"\n# injected race")
        return original_journaled(
            writer,
            transition=transition,
            handoff_id=handoff_id,
            body=body,
            artifacts=artifacts,
            **kwargs,
        )

    monkeypatch.setattr(external_recording, "_journaled", mutate_after_validation)
    before = _state(home, target, gig_id)
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        getattr(external_recording, f"{operation}_v2")(
            home_root=home, requested_target=target, gig_id=gig_id, envelope=request
        )
    assert refused.value.code == "external_authority_mismatch"
    assert _state(home, target, gig_id) == before


def test_completed_exact_replay_is_not_invalidated_by_later_source_mirror_edit(tmp_path: Path):
    home, target, gig_id, plan, started = _started_v2_run(tmp_path)
    checkpoint = _record_checkpoint(
        home, target, gig_id, _valid_v2_checkpoint(plan=plan, started=started),
    )
    request = _submit_request(started, checkpoint.payload, operation_key="completed")
    scope = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    receipt = external_recording.submit_v2(**scope, envelope=request)
    resolved = external_recording._resolved(**scope)
    contract = external_recording._approved_contract(resolved, plan["output_contract"])["content"]
    source_path = resolved.path / contract["domains"]["research"]["validator_source_ref"]["path"]
    source_path.write_bytes(source_path.read_bytes() + b"\n# later local edit")
    before = external_recording._snapshot(resolved)
    replay = external_recording.submit_v2(**scope, envelope=request)
    assert not replay.created and replay.payload == receipt.payload
    assert external_recording._snapshot(resolved) == before
