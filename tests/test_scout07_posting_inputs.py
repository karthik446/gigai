"""SCOUT-07 explicit completed-discovery posting resolution."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from gigai import external_recording
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.journal import JournalArtifact, JournalSnapshot, JournalTransition, run_with_journal_writer
from gigai.scout_posting_inputs import (
    ScoutPostingInputError,
    hydrate_discovery_posting_input_snapshot,
    resolve_discovery_posting_input,
    resolve_discovery_posting_input_from_journal,
)
from gigai.workpad import ResolvedWorkpad, resolve_workpad
from tests.test_scout07_discovery_run_flow import (
    _envelope,
    _fixture,
    _packet,
    _profile,
)
from gigai.native_records import create_native_record


def _completed_find_jobs(tmp_path: Path) -> tuple[ResolvedWorkpad, dict[str, object], object, dict[str, object]]:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    native = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="posting-resolver-profile",
    )
    plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "posting-resolver-find-plan",
            {
                "graph_selector": "find-jobs",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{
                    "family": "scout_record",
                    "record_id": native.record_id,
                    "revision_id": native.revision_id,
                    "scope": {"mode": "saved_default", "task_context_id": None},
                }],
                "output_kinds": ["discovery"],
                "predecessor": None,
            },
        ),
    )
    started = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("posting-resolver-find-start", {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id)
    snapshot = external_recording._snapshot(resolved)
    selected = plan.payload["inputs"][0]
    preference = snapshot.artifacts[selected["content"]["blob_ref"]["path"]]
    artifact, check = _packet(plan.payload, started.payload, preference, outcome="no_match")
    checkpoint = external_recording.checkpoint_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "posting-resolver-find-checkpoint",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": None,
                "questions": [],
                "artifact_refs": [artifact, check],
                "reason": "public discovery posting resolver fixture",
            },
        ),
    )
    output, recorded_check = checkpoint.payload["artifacts"]
    submitted = external_recording.submit_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "posting-resolver-find-submit",
            {
                "run_id": started.payload["run_id"],
                "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                "output_refs": [output],
                "check_refs": [recorded_check["sidecar"]],
                "disclosure": {"execution": "unobserved", "actor_report": "declared"},
            },
        ),
    )
    posting = artifact["domain_sidecar"]["value"]["discovery"]["postings"][0]
    selector = {
        "family": "scout_discovery",
        "run_id": started.payload["run_id"],
        "receipt_id": submitted.payload["receipt_id"],
        "output_kind": "discovery",
        "opportunity_id": posting["opportunity_id"],
        "snapshot_id": posting["snapshot_id"],
    }
    return resolved, selector, snapshot, {"posting": posting, "submitted": submitted.payload}


def _cancelled_find_jobs(tmp_path: Path) -> tuple[ResolvedWorkpad, dict[str, object]]:
    """Build an incomplete public Run and terminate it through the public API."""
    home, target, gig_id, _workpad = _fixture(tmp_path)
    native = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="posting-resolver-cancelled-profile",
    )
    plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "posting-resolver-cancelled-plan",
            {
                "graph_selector": "find-jobs",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{
                    "family": "scout_record",
                    "record_id": native.record_id,
                    "revision_id": native.revision_id,
                    "scope": {"mode": "saved_default", "task_context_id": None},
                }],
                "output_kinds": ["discovery"],
                "predecessor": None,
            },
        ),
    )
    started = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("posting-resolver-cancelled-start", {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    cancelled = external_recording.cancel_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "posting-resolver-cancelled-run",
            {"run_id": started.payload["run_id"], "reason": "test-only incomplete discovery"},
        ),
    )
    selector = {
        "family": "scout_discovery",
        "run_id": started.payload["run_id"],
        "receipt_id": cancelled.payload["receipt_id"],
        "output_kind": "discovery",
        "opportunity_id": "opportunity_" + "1" * 32,
        "snapshot_id": "snapshot_" + "1" * 32,
    }
    return resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id), selector


def _publish_second_terminal_receipt(resolved: ResolvedWorkpad, selector: dict[str, object]) -> None:
    """Publish a schema-valid second terminal receipt only for adversarial testing.

    The public recorder prevents a second terminal transition. This fixture
    uses the journal writer directly so resolver semantics, rather than a
    missing-artifact or schema failure, are exercised.
    """
    snapshot = external_recording._snapshot(resolved)
    receipt_path = f"runs/{selector['run_id']}/receipts/{selector['receipt_id']}.json"
    original = parse_json_bytes(snapshot.artifacts[receipt_path])
    receipt_id = f"receipt_{uuid.uuid4()}"
    invocation = dict(original["invocation"])
    invocation["operation_key"] = "posting-resolver-adversarial-second-terminal"
    invocation["payload_sha256"] = digest_imported_bytes(canonical_json_bytes(invocation["input"]))
    receipt = dict(original)
    receipt.update({
        "receipt_id": receipt_id,
        "invocation": invocation,
        "operation_key": invocation["operation_key"],
        "payload_sha256": digest_imported_bytes(canonical_json_bytes(invocation)),
        "outcome": "cancelled",
        "outputs": [],
        "checks": [],
    })
    data = canonical_json_bytes(receipt)

    def publish(writer: object) -> None:
        path = f"runs/{selector['run_id']}/receipts/{receipt_id}.json"
        writer.record(JournalTransition(
            handoff_id=f"handoff_{uuid.uuid4()}",
            transition="external_recording_cancelled",
            body="Test-only second terminal receipt fixture.",
            artifacts=(JournalArtifact(path, data),),
            front_matter={
                "actor": {"kind": "agent", "id": "test-scout07"},
                "outcome": "RECORDED",
                "artifact_refs": [{
                    "path": path,
                    "content_sha256": digest_imported_bytes(data),
                    "media_type": "application/json",
                    "size_bytes": len(data),
                }],
            },
        ))

    run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=publish,
    )


def test_resolve_completed_public_discovery_posting_exact_bytes_and_provenance(tmp_path: Path) -> None:
    resolved, selector, _before_snapshot, expected = _completed_find_jobs(tmp_path)
    before_full = external_recording._snapshot(resolved)
    result = resolve_discovery_posting_input_from_journal(resolved, selector)
    after = external_recording._snapshot(resolved)

    assert result["posting_bytes"] == b"Synthetic job capture."
    assert result["posting"]["opportunity_id"] == selector["opportunity_id"]
    assert result["posting"]["snapshot_id"] == selector["snapshot_id"]
    assert result["posting"]["source"]["locator"] == "https://jobs.example.test/fde-074"
    assert result["posting"]["source"]["status"] == "independently_verified"
    assert result["receipt_ref"]["path"].endswith(f"/{selector['receipt_id']}.json")
    assert result["checkpoint_ref"]["path"].startswith(f"runs/{selector['run_id']}/checkpoints/")
    assert result["domain_binding"]["schema_id"] == "urn:gigai:scout:discovery-packet:2"
    assert result["posting_ref"]["artifact_id"] == "job_capture"
    assert expected["submitted"]["outcome"] == "succeeded"
    assert before_full == after


def test_changed_snapshot_and_no_match_refuse_without_writes(tmp_path: Path) -> None:
    resolved, selector, _snapshot, _expected = _completed_find_jobs(tmp_path)
    before_full = external_recording._snapshot(resolved)
    before = hydrate_discovery_posting_input_snapshot(resolved, selector)
    for opportunity_id, snapshot_id in (
        (selector["opportunity_id"], "snapshot_" + "0" * 32),
        ("opportunity_" + "0" * 32, selector["snapshot_id"]),
    ):
        changed = dict(selector, opportunity_id=opportunity_id, snapshot_id=snapshot_id)
        with pytest.raises(ScoutPostingInputError) as refused:
            resolve_discovery_posting_input(resolved, before, changed)
        assert refused.value.code == "posting_input_not_found"
        assert external_recording._snapshot(resolved) == before_full


def test_completed_no_match_packet_refuses_unlisted_selection(tmp_path: Path) -> None:
    """Normal public lifecycle: succeeded receipt contains a genuine no_match packet."""
    resolved, selector, _snapshot, expected = _completed_find_jobs(tmp_path)
    assert expected["posting"]["opportunity_id"] == selector["opportunity_id"]
    before = external_recording._snapshot(resolved)
    absent = dict(selector, opportunity_id="opportunity_" + "2" * 32, snapshot_id="snapshot_" + "2" * 32)
    with pytest.raises(ScoutPostingInputError) as refused:
        resolve_discovery_posting_input_from_journal(resolved, absent)
    assert refused.value.code == "posting_input_not_found"
    assert external_recording._snapshot(resolved) == before


def test_foreign_completed_run_stays_outside_selected_gig(tmp_path: Path) -> None:
    """Normal public lifecycle: a second disposable Gig/Run cannot cross-resolve."""
    resolved, _selector, _snapshot, _expected = _completed_find_jobs(tmp_path)
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    _foreign_resolved, foreign_selector, _foreign_snapshot, _foreign_expected = _completed_find_jobs(foreign_root)
    before = external_recording._snapshot(resolved)
    with pytest.raises(ScoutPostingInputError) as refused:
        resolve_discovery_posting_input_from_journal(resolved, foreign_selector)
    assert refused.value.code == "posting_input_not_found"
    assert external_recording._snapshot(resolved) == before


def test_cancelled_incomplete_run_refuses_before_packet_lookup(tmp_path: Path) -> None:
    """Normal public lifecycle: an incomplete Run is explicitly cancelled."""
    resolved, selector = _cancelled_find_jobs(tmp_path)
    before = external_recording._snapshot(resolved)
    with pytest.raises(ScoutPostingInputError) as refused:
        resolve_discovery_posting_input_from_journal(resolved, selector)
    assert refused.value.code == "posting_input_not_terminal"
    assert external_recording._snapshot(resolved) == before


def test_two_terminal_receipts_refuse_without_selecting_one(tmp_path: Path) -> None:
    """Adversarial test-only journal publication: public APIs prevent this state."""
    resolved, selector, _snapshot, _expected = _completed_find_jobs(tmp_path)
    _publish_second_terminal_receipt(resolved, selector)
    before = external_recording._snapshot(resolved)
    with pytest.raises(ScoutPostingInputError) as refused:
        resolve_discovery_posting_input_from_journal(resolved, selector)
    assert refused.value.code == "posting_input_refused"
    assert external_recording._snapshot(resolved) == before


@pytest.mark.parametrize(
    "selector",
    [
        {},
        {"family": "scout_research"},
        {"family": "scout_discovery", "run_id": "bad"},
    ],
)
def test_selector_shape_refuses_before_journal_read(selector: object) -> None:
    resolved = ResolvedWorkpad(
        project_id="project_00000000-0000-4000-8000-000000000001",
        gig_id="gig_00000000-0000-4000-8000-000000000001",
        path=Path("/unopened"),
        target_root=Path("/unopened-target"),
        target_kind="git",
    )
    with pytest.raises(ScoutPostingInputError) as refused:
        resolve_discovery_posting_input(resolved, JournalSnapshot("head", {}), selector)
    assert refused.value.code == "posting_input_invalid"


def test_caller_pinned_snapshot_tamper_refuses_without_writing(tmp_path: Path) -> None:
    """Raw primitive boundary: copied snapshot tamper is not committed-journal evidence."""
    resolved, selector, _snapshot, _expected = _completed_find_jobs(tmp_path)
    before_full = external_recording._snapshot(resolved)
    before = hydrate_discovery_posting_input_snapshot(resolved, selector)
    path = next(path for path in before.artifacts if path.endswith(".domain.json"))
    tampered = dict(before.artifacts)
    tampered[path] = tampered[path].replace(b"job_discovery", b"tampered")
    with pytest.raises(ScoutPostingInputError) as refused:
        resolve_discovery_posting_input(resolved, JournalSnapshot(before.head, tampered), selector)
    assert refused.value.code in {"posting_input_refused", "posting_input_unsupported"}
    assert external_recording._snapshot(resolved) == before_full


def test_posting_digest_is_returned_from_committed_supporting_member(tmp_path: Path) -> None:
    resolved, selector, _snapshot, _expected = _completed_find_jobs(tmp_path)
    result = resolve_discovery_posting_input(resolved, hydrate_discovery_posting_input_snapshot(resolved, selector), selector)
    assert result["posting_ref"]["ref"]["content_sha256"] == digest_imported_bytes(result["posting_bytes"])
