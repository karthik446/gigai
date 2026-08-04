from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from gigai.canonical import parse_json_front_matter
from gigai.lifecycle import (
    approve_offline,
    create_offline,
    record_feedback,
    reject_offline,
    revise_offline,
)
from gigai.registry import open_project_registry
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_proposal_workpad


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
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


def test_create_orders_first_commit_active_selection_and_valid_proposal(
    tmp_path: Path,
) -> None:
    home, target = _configured_target(tmp_path)

    result = create_offline(
        home_root=home,
        requested_target=target,
        name="offline-proof",
        commission="Create an offline reviewable proof.",
        open_editor=False,
        uuid_factory=_uuids(),
    )

    assert result.project_id == PROJECT_ID
    assert result.creation_started.sequence == 1
    assert result.proposal_ready.sequence == 2
    assert result.resumed is False
    assert validate_proposal_workpad(result.workpad).valid
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        active = transaction.find_active_workpad(PROJECT_ID)
    assert active is not None and active.gig_id == result.gig_id
    assert _git(result.workpad, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    first_files = _git(
        result.workpad, "show", "--format=", "--name-only", "HEAD~1"
    ).stdout.splitlines()
    assert first_files == [".gitignore", "handoffs/000000000001-creation-started.txt"]
    second_files = _git(
        result.workpad, "show", "--format=", "--name-only", "HEAD"
    ).stdout.splitlines()
    assert "manifests/gig-proposal.json" in second_files
    assert "handoffs/000000000002-gig-proposal-ready.txt" in second_files
    assert "gigai-offline-ok" in (
        result.workpad / "manifests/creation-manifest.json"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("failpoint", "expected_commits"),
    (
        ("after_provisioning", "2"),
        ("after_creation_started", "2"),
        ("after_active_selection", "2"),
        ("after_proposal_ready", "2"),
    ),
)
def test_create_recovers_one_exact_workpad_across_lifecycle_boundaries(
    tmp_path: Path, failpoint: str, expected_commits: str
) -> None:
    home, target = _configured_target(tmp_path)
    values = _uuids()

    def crash(step: str) -> None:
        if step == failpoint:
            raise RuntimeError(step)

    try:
        create_offline(
            home_root=home,
            requested_target=target,
            name="resume-proof",
            open_editor=False,
            uuid_factory=values,
            observer=crash,
        )
    except RuntimeError as error:
        assert str(error) == failpoint
    else:
        raise AssertionError("create did not stop at the injected recovery boundary")

    resumed = create_offline(
        home_root=home,
        requested_target=target,
        name="resume-proof",
        open_editor=False,
        uuid_factory=values,
    )

    assert resumed.resumed is True
    assert resumed.gig_id == "gig_00000000-0000-4000-8000-000000000001"
    assert (
        _git(resumed.workpad, "rev-list", "--count", "HEAD").stdout.strip()
        == expected_commits
    )


def test_approval_seals_then_publishes_a_pointer_to_the_prior_commit(
    tmp_path: Path,
) -> None:
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="approve-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    approved = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=_uuids(),
    )

    assert approved.version == 1
    assert approved.tag == "gig-v000001"
    assert (
        _git(created.workpad, "rev-parse", "gig-v000001").stdout.strip()
        == approved.sealed_commit
    )
    assert (
        _git(created.workpad, "rev-parse", "HEAD").stdout.strip()
        == approved.publication_commit
    )
    pointer = (created.workpad / "manifests" / "active-gig-version.json").read_text(
        encoding="utf-8"
    )
    assert approved.sealed_commit in pointer
    assert _git(created.workpad, "rev-list", "--count", "HEAD").stdout.strip() == "4"


def test_approval_recovers_the_missing_pointer_after_the_sealed_commit(
    tmp_path: Path,
) -> None:
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="approval-recovery-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )

    def crash(step: str) -> None:
        if step == "after_approval_tag":
            raise RuntimeError(step)

    try:
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            uuid_factory=_uuids(),
            observer=crash,
        )
    except RuntimeError as error:
        assert str(error) == "after_approval_tag"
    else:
        raise AssertionError("approval did not stop after sealing Commit A")

    assert _git(created.workpad, "rev-list", "--count", "HEAD").stdout.strip() == "3"
    assert not (created.workpad / "manifests" / "active-gig-version.json").exists()
    recovered = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=_uuids(),
    )

    assert (
        recovered.sealed_commit
        == _git(created.workpad, "rev-parse", "HEAD~1").stdout.strip()
    )
    assert _git(created.workpad, "rev-list", "--count", "HEAD").stdout.strip() == "4"
    assert (created.workpad / "manifests" / "active-gig-version.json").is_file()


def test_feedback_is_verbatim_and_revision_preserves_prior_proposal_bytes(
    tmp_path: Path,
) -> None:
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="feedback-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    original = (created.workpad / "manifests" / "gig-proposal.json").read_bytes()
    feedback = "Keep the scope local.\nDo not start a Run.\n"
    entry = record_feedback(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        feedback=feedback,
        uuid_factory=_uuids(),
    )
    _metadata, body = parse_json_front_matter(entry.path.read_bytes())
    assert body == feedback.encode("utf-8")
    revised = revise_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        change_request="Add the explicit local-only stop boundary.",
        uuid_factory=_uuids(),
    )
    proposal = (created.workpad / "manifests" / "gig-proposal.json").read_bytes()
    assert proposal != original
    assert created.proposal_id in proposal.decode("utf-8")
    assert revised.proposal_id in proposal.decode("utf-8")
    assert validate_proposal_workpad(created.workpad).valid
    historical = _git(
        created.workpad, "show", "HEAD~2:manifests/gig-proposal.json"
    ).stdout.encode("utf-8")
    assert historical == original


def test_rejection_is_terminal_and_does_not_create_an_active_version(
    tmp_path: Path,
) -> None:
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="reject-proof",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    entry = reject_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        reason="The operator rejected this offline proposal.",
        uuid_factory=_uuids(),
    )
    metadata, body = parse_json_front_matter(entry.path.read_bytes())
    assert metadata["transition"] == "gig_proposal_rejected"
    assert body == b"The operator rejected this offline proposal.\n"
    proposal = (created.workpad / "manifests" / "gig-proposal.json").read_text(
        encoding="utf-8"
    )
    assert '"status":"rejected"' in proposal
    assert not (created.workpad / "manifests" / "active-gig-version.json").exists()


def test_g08_is_the_only_lifecycle_owner_of_gig_identity_generation() -> None:
    root = Path(__file__).parents[1] / "src" / "gigai"
    lifecycle = root / "lifecycle.py"
    tree = ast.parse(lifecycle.read_text(encoding="utf-8"), filename=str(lifecycle))
    generated = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "generate_entity_id"
    ]
    assert generated
    for path in root.glob("*.py"):
        if path == lifecycle:
            continue
        other_tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        gig_calls = [
            node
            for node in ast.walk(other_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "generate_entity_id"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "GIG"
        ]
        assert not gig_calls
