from __future__ import annotations

from pathlib import Path
import subprocess
import time
import uuid

import pytest
import gigai.occurrence as occurrence_module
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.lifecycle import approve_offline, create_offline
from gigai.journal import JournalArtifact, record_transition
from gigai.occurrence import (
    OccurrenceError,
    declare_occurrence,
    mark_occurrence,
    trigger_occurrence,
    close_occurrence,
    reconcile_occurrence,
)
from gigai.review import materialize_review_bundle
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad


def _fixture(tmp_path: Path, *, git_target: bool = False) -> tuple[Path, Path, str, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    if git_target:
        subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
        subprocess.run(["git", "-C", str(target), "config", "user.name", "G21 Test"], check=True)
        subprocess.run(["git", "-C", str(target), "config", "user.email", "g21-test@gigai.invalid"], check=True)
        (target / "README.md").write_text("fixture\n")
        subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(target), "commit", "--quiet", "-m", "fixture"], check=True)
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
        for index in range(1, 80)
    )
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="g21-proof",
        open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        allow_semantic_state=True,
    )
    reference = {
        "reference_id": "ref_12345678-1234-4234-9234-123456789abc",
        "role": "input",
        "kind": "csv",
        "path": "occurrences/inputs/market.csv",
        "media_type": "text/csv",
        "content_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        "size_bytes": 0,
        "canonical_sha256": None,
        "provenance": {
            "source_kind": "local_file",
            "locator": "fixture://market.csv",
            "acquired_at": "2026-08-10T12:00:00Z",
            "acquisition_method": "fixture",
            "source_revision": None,
        },
        "sensitivity": "public",
        "redaction_status": "not_required",
    }
    source = b"date,value\n2026-08-10,10\n"

    reference["content_sha256"] = digest_imported_bytes(source)
    reference["size_bytes"] = len(source)
    bundle = {
        "schema_version": "1.0",
        "bundle_id": "bundle_22345678-1234-4234-9234-123456789abc",
        "bundle_version": 1,
        "created_at": "2026-08-10T12:00:00Z",
        "created_by": {"kind": "operator", "id": "g21-test"},
        "name": "g21-market",
        "question": "Compare market state.",
        "references": [reference],
        "tool_requirements": None,
        "redaction_policy": {
            "mode": "local_only",
            "allowed_reference_ids": [reference["reference_id"]],
            "policy_version": "g21-test-v1",
            "detector_version": None,
        },
    }
    bundle_path = resolved.path / "manifests" / "review-bundles" / "daily.json"
    manifest_bytes = materialize_review_bundle(
        resolved.path,
        bundle,
        {"occurrences/inputs/market.csv": source},
        manifest_relative_path="manifests/review-bundles/daily.json",
    )
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id="handoff_22345678-1234-4234-9234-123456789abc",
        transition="creation_started",
        body="G21 fixture Review Bundle snapshot prepared.",
        artifacts=(
            JournalArtifact("occurrences/inputs/market.csv", source),
            JournalArtifact("manifests/review-bundles/daily.json", manifest_bytes),
        ),
    )
    return home, target, created.gig_id, bundle_path


def _uuids() -> callable:
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
        for index in range(100, 160)
    )
    return lambda: next(values)


def test_manual_occurrence_runs_once_and_preserves_active_pointer(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path, git_target=True)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    before_pointer = (resolved.path / "manifests/active-gig-version.json").read_bytes()
    first = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="daily",
        occurrence_key="2026-08-10",
        snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )
    replay = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="daily",
        occurrence_key="2026-08-10",
        snapshot_path="manifests/review-bundles/daily.json",
        uuid_factory=_uuids(),
    )
    assert first.occurrence_id == replay.occurrence_id
    result = trigger_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=first.occurrence_id,
        wait=True,
        uuid_factory=_uuids(),
    )
    assert result.state == "run_terminal"
    assert result.run_id is not None
    closed = close_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=first.occurrence_id,
    )
    assert closed.state == "closed"
    assert (resolved.path / "manifests/active-gig-version.json").read_bytes() == before_pointer
    assert len(list((resolved.path / "runs").glob("run_*/run-manifest.json"))) == 1


def test_changed_snapshot_blocks_before_run_allocation(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    occurrence = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="weekly",
        occurrence_key="2026-w33",
        snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )
    (resolved.path / "occurrences/inputs/market.csv").write_bytes(b"tampered\n")
    result = trigger_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id,
        wait=True,
        uuid_factory=_uuids(),
    )
    assert result.state == "blocked"
    assert not list((resolved.path / "runs").glob("run_*/run-manifest.json"))


def test_valid_replacement_snapshot_is_blocked_by_identity_digest(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    occurrence = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="daily",
        occurrence_key="2026-08-12",
        snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )
    replacement = parse_json_bytes(bundle_path.read_bytes())
    replacement["question"] = "A different valid Bundle at the same path."
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id="handoff_32345678-1234-4234-9234-123456789abc",
        transition="creation_started",
        body="G21 fixture replaces the snapshot manifest.",
        artifacts=(
            JournalArtifact(
                "manifests/review-bundles/daily.json",
                canonical_json_bytes(replacement),
            ),
        ),
    )
    result = trigger_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id,
        wait=True,
        uuid_factory=_uuids(),
    )
    assert result.state == "blocked"
    assert not list((resolved.path / "runs").glob("run_*/run-manifest.json"))


def test_missed_state_requires_explicit_reason_and_has_no_run(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    occurrence = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="monthly",
        occurrence_key="2026-08",
        snapshot_path=bundle_path.relative_to(
            resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True).path
        ).as_posix(),
        uuid_factory=_uuids(),
    )
    result = mark_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id,
        state="missed",
        reason="operator reconciliation: no external trigger arrived",
    )
    assert result.state == "missed"
    assert result.run_id is None


def test_refusal_terminal_records_outcome_and_actor(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    occurrence = declare_occurrence(
        home_root=home, requested_target=target, gig_id=gig_id,
        cadence="monthly", occurrence_key="2026-08", snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )
    result = mark_occurrence(
        home_root=home, requested_target=target, gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id, state="missed", reason="no trigger",
        outcome_actor={"kind": "operator", "id": "scheduler-reviewer"},
    )
    record = parse_json_bytes(result.record_path.read_bytes())
    assert record["outcome"] == "missed"
    assert record["outcome_actor"] == {"kind": "operator", "id": "scheduler-reviewer"}


def test_mark_refuses_inflight_prepared_run_until_reconciled(tmp_path: Path, monkeypatch) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    occurrence = declare_occurrence(
        home_root=home, requested_target=target, gig_id=gig_id,
        cadence="daily", occurrence_key="2026-08-14", snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )
    prepared = trigger_occurrence(
        home_root=home, requested_target=target, gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id, wait=False, uuid_factory=_uuids(),
    )
    assert prepared.state == "run_prepared"
    monkeypatch.setattr(occurrence_module, "read_run_details", lambda **_kwargs: {"status": "running"})
    with pytest.raises(OccurrenceError, match="still in flight"):
        mark_occurrence(
            home_root=home, requested_target=target, gig_id=gig_id,
            occurrence_id=occurrence.occurrence_id, state="cancelled", reason="operator cancellation",
        )
    assert parse_json_bytes(prepared.record_path.read_bytes())["state"] == "run_prepared"


def test_mark_refuses_completed_prepared_run_until_reconciled(tmp_path: Path, monkeypatch) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    occurrence = declare_occurrence(
        home_root=home, requested_target=target, gig_id=gig_id,
        cadence="daily", occurrence_key="2026-08-15", snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )
    prepared = trigger_occurrence(
        home_root=home, requested_target=target, gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id, wait=False, uuid_factory=_uuids(),
    )
    monkeypatch.setattr(occurrence_module, "read_run_details", lambda **_kwargs: {"status": "succeeded"})
    with pytest.raises(OccurrenceError, match="complete"):
        mark_occurrence(
            home_root=home, requested_target=target, gig_id=gig_id,
            occurrence_id=occurrence.occurrence_id, state="cancelled", reason="operator cancellation",
        )
    assert parse_json_bytes(prepared.record_path.read_bytes())["state"] == "run_prepared"


def test_prepared_occurrence_reconciles_without_relaunch(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    occurrence = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="daily",
        occurrence_key="2026-08-13",
        snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )
    prepared = trigger_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id,
        wait=False,
        uuid_factory=_uuids(),
    )
    assert prepared.state == "run_prepared"
    for _ in range(100):
        reconciled = reconcile_occurrence(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            occurrence_id=occurrence.occurrence_id,
        )
        if reconciled.state == "run_terminal":
            break
        time.sleep(0.05)
    assert reconciled.state == "run_terminal"
    assert close_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id,
    ).state == "closed"
    assert len(list((resolved.path / "runs").glob("run_*/run-manifest.json"))) == 1


def test_interruption_after_run_preparation_is_terminal_failure(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path, git_target=True)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    occurrence = declare_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        cadence="weekly",
        occurrence_key="2026-w34",
        snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
        uuid_factory=_uuids(),
    )

    def interrupt(step: str) -> None:
        if step == "after_run_started_commit":
            (target / "README.md").write_text("interrupted after preparation\n")

    result = trigger_occurrence(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        occurrence_id=occurrence.occurrence_id,
        wait=True,
        uuid_factory=_uuids(),
        observer=interrupt,
    )
    assert result.state == "failed"
    assert result.run_id is not None


def test_explicit_terminal_states_are_idempotent_and_never_run(tmp_path: Path) -> None:
    home, target, gig_id, bundle_path = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    for index, state in enumerate(("skipped", "cancelled", "unavailable", "missed"), start=20):
        occurrence = declare_occurrence(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            cadence="daily",
            occurrence_key=f"2026-08-{index}",
            snapshot_path=bundle_path.relative_to(resolved.path).as_posix(),
            uuid_factory=_uuids(),
        )
        first = mark_occurrence(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            occurrence_id=occurrence.occurrence_id,
            state=state,
            reason=f"explicit {state} fixture",
        )
        replay = mark_occurrence(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            occurrence_id=occurrence.occurrence_id,
            state=state,
            reason="different replay reason is ignored",
        )
        assert first.state == replay.state == state
        assert replay.run_id is None
    assert not list((resolved.path / "runs").glob("run_*/run-manifest.json"))
