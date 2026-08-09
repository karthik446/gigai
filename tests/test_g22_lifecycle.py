from __future__ import annotations

import json
from pathlib import Path
import subprocess
import uuid

import pytest

from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.lifecycle import LifecycleError, persist_interview_session, start_interview
from gigai.proposal_interview import answer_question, session_record
from gigai.registry import open_project_registry
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_serialized_contract


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )


def _configured_target(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    binding = initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    assert binding.project_id == PROJECT_ID
    return home, target


def _uuids() -> callable:
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 32)
    )
    return lambda: next(values)


def test_start_interview_commits_exact_request_and_reference_bytes(tmp_path: Path) -> None:
    home, target = _configured_target(tmp_path)
    reference = tmp_path / "notes.txt"
    reference.write_bytes(b"exact reference bytes\n")

    result = start_interview(
        home_root=home,
        requested_target=target,
        name="interview-proof",
        request="Review this selected reference.",
        reference_paths=(reference,),
        uuid_factory=_uuids(),
    )

    snapshot_path = result.workpad / "manifests/proposal-interview.json"
    assert result.session.state == "questions_pending"
    assert snapshot_path.is_file()
    assert validate_serialized_contract("proposal-interview.schema.json", snapshot_path.read_bytes()).valid
    assert result.reference_bytes[result.session.references[0].reference_id] == reference.read_bytes()
    assert _git(result.workpad, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    committed = _git(result.workpad, "show", "HEAD:review/interviews/session_00000000-0000-4000-8000-000000000003/request.txt").stdout
    assert committed == "Review this selected reference."


def test_interview_snapshot_recovers_from_workpad_not_sqlite(tmp_path: Path) -> None:
    home, target = _configured_target(tmp_path)
    reference = tmp_path / "notes.txt"
    reference.write_bytes(b"recoverable bytes\n")
    values = _uuids()
    created = start_interview(
        home_root=home,
        requested_target=target,
        name="recover-proof",
        request="Recover this interview.",
        reference_paths=(reference,),
        uuid_factory=values,
    )
    (created.workpad / "state.sqlite").unlink()

    recovered = start_interview(
        home_root=home,
        requested_target=target,
        name="recover-proof",
        request="ignored after recovery",
        reference_paths=(),
        uuid_factory=values,
    )

    assert recovered.resumed is True
    assert recovered.session.session_id == created.session.session_id
    assert recovered.reference_bytes == created.reference_bytes
    assert recovered.session.state == "questions_pending"


def test_interview_recovery_rejects_changed_reference_bytes(tmp_path: Path) -> None:
    home, target = _configured_target(tmp_path)
    reference = tmp_path / "notes.txt"
    reference.write_bytes(b"original bytes\n")
    values = _uuids()
    created = start_interview(
        home_root=home,
        requested_target=target,
        name="digest-proof",
        request="Recover only if the reference is unchanged.",
        reference_paths=(reference,),
        uuid_factory=values,
    )
    stored = next((created.workpad / "review/interviews").rglob("*.bin"))
    stored.write_bytes(b"tampered committed bytes\n")
    with pytest.raises(LifecycleError, match="digest"):
        start_interview(
            home_root=home,
            requested_target=target,
            name="digest-proof",
            request="ignored after recovery",
            reference_paths=(),
            uuid_factory=values,
        )


def test_interview_rejects_symlink_reference_before_capture(tmp_path: Path) -> None:
    home, target = _configured_target(tmp_path)
    source = tmp_path / "source.txt"
    source.write_bytes(b"source bytes\n")
    link = tmp_path / "link.txt"
    link.symlink_to(source)
    with pytest.raises(LifecycleError, match="non-symlink"):
        start_interview(
            home_root=home,
            requested_target=target,
            name="symlink-proof",
            request="Reject redirected references.",
            reference_paths=(link,),
            uuid_factory=_uuids(),
        )


def test_persist_interview_session_is_schema_validated_and_journaled(tmp_path: Path) -> None:
    home, target = _configured_target(tmp_path)
    reference = tmp_path / "notes.txt"
    reference.write_bytes(b"selected bytes\n")
    result = start_interview(
        home_root=home,
        requested_target=target,
        name="persist-proof",
        request="Persist the answer.",
        reference_paths=(reference,),
        uuid_factory=_uuids(),
    )
    selected = result.session.references[0].reference_id
    session = answer_question(result.session, "references", [selected])
    entry = persist_interview_session(
        workpad=result.workpad,
        project_id=result.project_id,
        gig_id=result.gig_id,
        session=session,
        uuid_factory=_uuids(),
    )

    assert entry.sequence == 3
    snapshot = parse_json_bytes((result.workpad / "manifests/proposal-interview.json").read_bytes())
    assert snapshot["selected_reference_ids"] == [selected]
    assert _git(result.workpad, "rev-list", "--count", "HEAD").stdout.strip() == "3"
