from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.project_binding import load_project_binding
from tests.behaviors.scout_proposals_tools.test_scout05_bundled_tools import _create_args, _run
from tests.behaviors.scout_proposals_tools.test_scout05_capability_cli import _args, _input, _payload, _reviewable_candidate


_CAPABILITY = "cap_00000000-0000-4000-8000-000000000071"


def _head(workpad: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _review_and_prepare_args(
    *, home: Path, target: Path, instance: object, manifest_id: str, operation_key: str
) -> list[str]:
    return [
        "capability",
        "prepare-successor",
        "--gig",
        str(instance.gig_id),
        "--base-version",
        "1",
        "--base-proposal-id",
        str(instance.proposal_id),
        "--capability-id",
        _CAPABILITY,
        "--reviewed-manifest-id",
        manifest_id,
        "--operation-key",
        operation_key,
        "--home",
        str(home),
        "--target",
        str(target),
        "--json",
    ]


def _reviewed_candidate(tmp_path: Path):
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    reviewed = CliRunner().invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=_input(tmp_path / "review.json"),
            operation_key="cli-successor-review",
        ),
    )
    assert reviewed.exit_code == 0, reviewed.output
    payload = _payload(reviewed)
    manifest_ref = payload["reviewed_manifest_ref"]
    assert isinstance(manifest_ref, dict)
    return home, target, instance, workpad, Path(str(manifest_ref["path"])).stem


def test_public_prepare_replay_then_separate_approve_and_fresh_wrapper_crud(tmp_path: Path) -> None:
    home, target, instance, workpad, manifest_id = _reviewed_candidate(tmp_path)
    runner = CliRunner()
    pointer_before = (workpad / "manifests/active-gig-version.json").read_bytes()
    prepare_args = _review_and_prepare_args(
        home=home,
        target=target,
        instance=instance,
        manifest_id=manifest_id,
        operation_key="cli-successor-prepare",
    )
    first = runner.invoke(cli, prepare_args)
    assert first.exit_code == 0, first.output
    prepared = _payload(first)
    assert prepared["status"] == "successor_pending"
    assert prepared["replayed"] is False
    assert prepared["reviewed_manifest_ref"]["path"].endswith(manifest_id + ".json")
    assert (workpad / "manifests/active-gig-version.json").read_bytes() == pointer_before
    assert load_project_binding(target).active_gig_id is None
    head_before_replay = _head(workpad)

    replay = runner.invoke(cli, prepare_args)
    assert replay.exit_code == 0, replay.output
    replayed = _payload(replay)
    assert replayed["replayed"] is True
    assert replayed["proposal"] == prepared["proposal"]
    assert replayed["binding_ref"] == prepared["binding_ref"]
    assert _head(workpad) == head_before_replay
    assert (workpad / "manifests/active-gig-version.json").read_bytes() == pointer_before

    approval_argv = prepared["approval_argv"]
    assert isinstance(approval_argv, list)
    assert approval_argv[:2] == ["gigai", "approve"]
    approved = runner.invoke(cli, approval_argv[1:])
    assert approved.exit_code == 0, approved.output
    approval_payload = _payload(approved)
    assert approval_payload["version"] == 2
    assert load_project_binding(target).active_gig_id is None

    created = _run(workpad / "gig.py", home, target, *_create_args("cli-successor-create"))
    assert created.returncode == 0, created.stderr
    created_payload = json.loads(created.stdout)
    record = created_payload["result"]["result"]
    receipt = record["receipt"]
    assert receipt["tool_binding"]["gig_version"] == 2
    assert Path(receipt["tool_binding"]["manifest_ref"]["path"]).stem == manifest_id


def test_prepare_normal_output_is_pending_only_and_invalid_identity_precedes_resolution(
    tmp_path: Path,
) -> None:
    home, target, instance, _workpad, manifest_id = _reviewed_candidate(tmp_path)
    normal_args = _review_and_prepare_args(
        home=home,
        target=target,
        instance=instance,
        manifest_id=manifest_id,
        operation_key="cli-successor-normal",
    )[:-1]
    normal = CliRunner().invoke(cli, normal_args)
    assert normal.exit_code == 0, normal.output
    assert "pending successor" in normal.output
    assert "approve" in normal.output

    invalid = CliRunner().invoke(
        cli,
        [
            "capability",
            "prepare-successor",
            "--gig",
            "not-a-gig",
            "--base-version",
            "1",
            "--base-proposal-id",
            str(instance.proposal_id),
            "--capability-id",
            _CAPABILITY,
            "--reviewed-manifest-id",
            manifest_id,
            "--operation-key",
            "invalid-id-before-path",
            "--home",
            str(tmp_path / "missing-home"),
            "--target",
            str(tmp_path / "missing-target"),
            "--json",
        ],
    )
    assert invalid.exit_code != 0
    assert _payload(invalid)["error"]["code"] == "capability_successor_identity_invalid"


def test_prepare_refuses_stale_missing_and_changed_review_authority_without_new_publication(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad, manifest_id = _reviewed_candidate(tmp_path)
    runner = CliRunner()
    base_args = _review_and_prepare_args(
        home=home,
        target=target,
        instance=instance,
        manifest_id=manifest_id,
        operation_key="cli-successor-refusal",
    )
    stale = list(base_args)
    stale[stale.index("--base-version") + 1] = "2"
    stale_result = runner.invoke(cli, stale)
    assert stale_result.exit_code != 0
    assert _payload(stale_result)["error"]["code"] == "capability_successor_review_invalid"
    before = _head(workpad)

    reviewed_path = workpad / "manifests/capabilities" / f"{manifest_id}.json"
    reviewed_path.unlink()
    missing = runner.invoke(cli, base_args)
    assert missing.exit_code != 0
    assert _payload(missing)["error"]["code"] == "capability_successor_authority_unavailable"
    assert _head(workpad) == before


def test_prepare_refuses_changed_working_review_manifest_without_publication(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad, manifest_id = _reviewed_candidate(tmp_path)
    base_args = _review_and_prepare_args(
        home=home,
        target=target,
        instance=instance,
        manifest_id=manifest_id,
        operation_key="cli-successor-changed-review",
    )
    reviewed_path = workpad / "manifests/capabilities" / f"{manifest_id}.json"
    reviewed_path.write_bytes(reviewed_path.read_bytes() + b"\n")
    before = _head(workpad)
    result = CliRunner().invoke(cli, base_args)
    assert result.exit_code != 0
    assert _payload(result)["error"]["code"] == "capability_successor_authority_unavailable"
    assert _head(workpad) == before


@pytest.mark.parametrize(
    "manifest_id",
    ["capmanifest_00000000-0000-4000-8000-000000000099"],
)
def test_prepare_refuses_foreign_manifest_key_without_publication(
    tmp_path: Path, manifest_id: str
) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    before = _head(workpad)
    result = CliRunner().invoke(
        cli,
        _review_and_prepare_args(
            home=home,
            target=target,
            instance=instance,
            manifest_id=manifest_id,
            operation_key="cli-successor-foreign-review",
        ),
    )
    assert result.exit_code != 0
    assert _payload(result)["error"]["code"] == "capability_successor_review_unavailable"
    assert _head(workpad) == before
