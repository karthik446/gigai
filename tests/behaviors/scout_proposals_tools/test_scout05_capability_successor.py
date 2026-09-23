from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from gigai.capability_review import review_local_tool
from gigai.capability_successor import (
    CapabilitySuccessorError,
    prepare_capability_successor,
)
from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.journal import read_committed_artifact
from gigai.lifecycle import LifecycleError, approve_offline, create_offline
from gigai.project_binding import load_project_binding
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from tests.behaviors.scout_proposals_tools.test_scout05_bundled_tools import _create_args, _run, _candidate
from tests.behaviors.scout_proposals_tools.test_scout05_capability_review import _review_kwargs
from tests.behaviors.scout_proposals_tools.test_scout04_input_integration import _profile


def _fixture(tmp_path: Path):
    home, target, instance, workpad = _candidate(tmp_path)
    approved = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=str(instance.proposal_id),
        gig_id=instance.gig_id,
    )
    result = review_local_tool(
        **_review_kwargs(instance, workpad, operation_key="successor-review")
    )
    assert result.reviewed_manifest_ref is not None
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    return home, target, instance, workpad, approved, result, str(layout["project_id"])


def _prepare(
    home: Path, target: Path, instance: object, workpad: Path, review: object,
    *, operation_key: str = "successor-prepare",
):
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    result = prepare_capability_successor(
        workpad=workpad,
        project_id=str(layout["project_id"]),
        gig_id=str(instance.gig_id),
        base_version=1,
        base_proposal_id=str(instance.proposal_id),
        capability_id="cap_00000000-0000-4000-8000-000000000071",
        decision_ref=review.decision_ref,
        reviewed_manifest_ref=review.reviewed_manifest_ref,
        operation_key=operation_key,
    )
    return result


def _head(workpad: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _successor_paths(workpad: Path) -> dict[str, bytes]:
    root = workpad / "manifests/capability-successors"
    return {
        path.relative_to(workpad).as_posix(): path.read_bytes()
        for path in root.rglob("*.json")
    }


def _legacy_first_approval(tmp_path: Path):
    home, target = tmp_path / "legacy-home", tmp_path / "legacy-target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "legacy-workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target)
    return create_offline(
        home_root=home,
        requested_target=target,
        name="legacy-first-approval",
        open_editor=False,
    ), home, target


def _tags(workpad: Path) -> tuple[str, ...]:
    return tuple(
        subprocess.run(
            ["git", "-C", str(workpad), "tag", "--points-at", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )


@pytest.mark.parametrize("proposal_family", ["legacy", "v2"])
def test_first_approval_crash_after_tag_recovers_once_without_prior_pointer(
    tmp_path: Path, proposal_family: str
) -> None:
    if proposal_family == "legacy":
        created, home, target = _legacy_first_approval(tmp_path)
        workpad, proposal_id, gig_id = created.workpad, created.proposal_id, created.gig_id
    else:
        home, target, instance, workpad = _candidate(tmp_path)
        proposal_id, gig_id = str(instance.proposal_id), instance.gig_id

    def crash(step: str) -> None:
        if step == "after_approval_tag":
            raise RuntimeError("disposable first-approval crash")

    with pytest.raises(RuntimeError, match="disposable first-approval crash"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=proposal_id,
            gig_id=gig_id,
            observer=crash,
        )
    assert not (workpad / "manifests/active-gig-version.json").exists()
    sealed_head = _head(workpad)
    sealed_tag = _tags(workpad)
    assert sealed_tag == ("gig-v000001",)

    recovered = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=proposal_id,
        gig_id=gig_id,
    )
    published_head = _head(workpad)
    replay = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=proposal_id,
        gig_id=gig_id,
    )
    assert recovered.version == 1
    assert recovered.sealed_commit == sealed_head
    assert replay.publication_commit == recovered.publication_commit == published_head
    assert _head(workpad) == published_head
    accepted = subprocess.run(
        ["git", "-C", str(workpad), "log", "--format=%s", "--all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert accepted.count("journal: gig accepted") == 1


@pytest.mark.parametrize("recovery", [False, True])
def test_committed_successor_binding_refuses_mutable_bypass_before_publication(
    tmp_path: Path, recovery: bool
) -> None:
    home, target, instance, workpad, _base, review, _project_id = _fixture(tmp_path)
    first = _prepare(home, target, instance, workpad, review, operation_key=f"successor-committed-binding-{recovery}")
    if recovery:
        def crash(step: str) -> None:
            if step == "after_approval_tag":
                raise RuntimeError("disposable successor recovery crash")

        with pytest.raises(RuntimeError, match="disposable successor recovery crash"):
            approve_offline(
                home_root=home,
                requested_target=target,
                proposal_id=str(first.proposal["proposal_id"]),
                gig_id=instance.gig_id,
                observer=crash,
            )

    proposal_path = workpad / "manifests/gig-proposal.json"
    sidecar_path = workpad / str(first.binding_ref["path"])
    source_path = workpad / "tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py"
    original_proposal = proposal_path.read_bytes()
    original_sidecar = sidecar_path.read_bytes()
    original_source = source_path.read_bytes()
    pointer_path = workpad / "manifests/active-gig-version.json"
    pointer_before = pointer_path.read_bytes() if pointer_path.exists() else None
    head_before, tags_before = _head(workpad), _tags(workpad)

    edited = parse_json_bytes(original_proposal)
    assert isinstance(edited, dict)
    edited["created_by"] = {"kind": "operator", "id": "local-user", "model_target": None}
    proposal_path.write_bytes(canonical_json_bytes(edited))
    sidecar_path.unlink()
    source_path.write_bytes(original_source + b"\n# tampered before approval\n")

    with pytest.raises(LifecycleError, match="successor binding is unavailable"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
        )
    assert _head(workpad) == head_before
    assert _tags(workpad) == tags_before
    assert (pointer_path.read_bytes() if pointer_path.exists() else None) == pointer_before

    # Restoring ordinary working bytes is not an approval: no pointer, tag, or
    # journal state changes until a later explicit supported retry.
    proposal_path.write_bytes(original_proposal)
    sidecar_path.write_bytes(original_sidecar)
    assert _head(workpad) == head_before
    assert _tags(workpad) == tags_before
    assert (pointer_path.read_bytes() if pointer_path.exists() else None) == pointer_before

    with pytest.raises(LifecycleError, match="source"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
        )
    assert _head(workpad) == head_before
    assert _tags(workpad) == tags_before
    assert (pointer_path.read_bytes() if pointer_path.exists() else None) == pointer_before

    source_path.write_bytes(original_source)

    accepted = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=str(first.proposal["proposal_id"]),
        gig_id=instance.gig_id,
    )
    assert accepted.version == 2


def test_prepare_then_approve_and_run_actual_bundled_wrapper(tmp_path: Path) -> None:
    home, target, instance, workpad, base, review, project_id = _fixture(tmp_path)
    prepared = _prepare(home, target, instance, workpad, review)
    assert prepared.replayed is False
    assert prepared.proposal["status"] == "proposed"
    assert prepared.binding["parent_proposal_id"] == instance.proposal_id
    assert prepared.binding["reviewed_manifest_ref"] == review.reviewed_manifest_ref
    assert _head(workpad)
    accepted = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=str(prepared.proposal["proposal_id"]),
        gig_id=instance.gig_id,
        capability_manifest_id=str(Path(str(review.reviewed_manifest_ref["path"])).stem),
    )
    assert accepted.version == base.version + 1
    pointer = parse_json_bytes((workpad / "manifests/active-gig-version.json").read_bytes())
    assert isinstance(pointer, dict)
    assert pointer["active_version"] == accepted.version
    assert pointer["approved_proposal_id"] == prepared.proposal["proposal_id"]
    assert pointer["capability_manifest"] == review.reviewed_manifest_ref
    result = _run(
        workpad / "gig.py", home, target, *_create_args("successor-create")
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["result"]["result"]["created"] is True
    record_result = payload["result"]["result"]
    receipt = record_result["receipt"]
    assert receipt["tool_binding"]["gig_version"] == accepted.version
    assert receipt["tool_binding"]["manifest_ref"] == review.reviewed_manifest_ref
    record_id, revision_id = record_result["record_id"], record_result["revision_id"]
    listed = _run(workpad / "gig.py", home, target, "list")
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout and record_id in listed.stdout
    read = _run(workpad / "gig.py", home, target, "read", record_id)
    assert read.returncode == 0, read.stderr
    assert json.loads(read.stdout)["result"]["record"]["record_id"] == record_id
    updated = _run(
        workpad / "gig.py", home, target, "update", record_id,
        "--parent-revision", revision_id, "--capability-id",
        "cap_00000000-0000-4000-8000-000000000071", "--actor-id", "bundled-tools-agent",
        "--operation-key", "successor-update", "--input-json", json.dumps({"content": _profile()}),
    )
    assert updated.returncode == 0, updated.stderr
    new_revision = json.loads(updated.stdout)["result"]["result"]["revision_id"]
    archived = _run(
        workpad / "gig.py", home, target, "archive", record_id,
        "--parent-revision", new_revision, "--capability-id",
        "cap_00000000-0000-4000-8000-000000000071", "--actor-id", "bundled-tools-agent",
        "--operation-key", "successor-archive",
    )
    assert archived.returncode == 0, archived.stderr
    context = _run(workpad / "gig.py", home, target, "context")
    assert context.returncode == 0, context.stderr
    assert json.loads(context.stdout)["result"]["operation"] == "context"
    assert read_committed_artifact(
        workpad=workpad,
        project_id=project_id,
        gig_id=instance.gig_id,
        path=str(review.reviewed_manifest_ref["path"]),
    )[0]


def test_prepare_preserves_v1_and_replays_exactly_without_publication(tmp_path: Path) -> None:
    home, target, instance, workpad, base, review, _project_id = _fixture(tmp_path)
    pointer_before = (workpad / "manifests/active-gig-version.json").read_bytes()
    parent_before = (workpad / "manifests/gig-proposal.json").read_bytes()
    first = _prepare(home, target, instance, workpad, review, operation_key="successor-replay")
    paths_before = _successor_paths(workpad)
    head_before = _head(workpad)
    replay = _prepare(home, target, instance, workpad, review, operation_key="successor-replay")
    assert replay.replayed is True
    assert replay.proposal == first.proposal
    assert replay.binding == first.binding
    assert _head(workpad) == head_before
    assert _successor_paths(workpad) == paths_before
    assert (workpad / "manifests/active-gig-version.json").read_bytes() == pointer_before
    assert (workpad / "manifests/gig-proposal.json").read_bytes() != parent_before
    current = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(current, dict)
    assert current["base_gig_version"] == 1
    assert current["parent_proposal_id"] == instance.proposal_id
    assert current["graph_set"] == parse_json_bytes(parent_before)["graph_set"]
    assert load_project_binding(target).active_gig_id is None
    old_pointer = subprocess.run(
        ["git", "-C", str(workpad), "show", f"{base.publication_commit}:manifests/active-gig-version.json"],
        check=True,
        capture_output=True,
    ).stdout
    assert old_pointer == pointer_before


def test_prepare_refuses_second_pending_and_missing_or_foreign_review(tmp_path: Path) -> None:
    home, target, instance, workpad, _base, review, _project_id = _fixture(tmp_path)
    first = _prepare(home, target, instance, workpad, review)
    with pytest.raises(CapabilitySuccessorError) as refused:
        _prepare(home, target, instance, workpad, review, operation_key="successor-second")
    assert refused.value.code == "capability_successor_pending_conflict"
    sidecar = workpad / str(first.binding_ref["path"])
    binding = parse_json_bytes(sidecar.read_bytes())
    assert isinstance(binding, dict)
    binding["gig_id"] = "gig_00000000-0000-4000-8000-000000000099"
    sidecar.write_bytes(canonical_json_bytes(binding))
    with pytest.raises(LifecycleError) as foreign:
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
        )
    assert "successor binding is unavailable" in str(foreign.value)


def test_approval_rejects_manifest_mismatch_without_new_publication(tmp_path: Path) -> None:
    home, target, instance, workpad, _base, review, _project_id = _fixture(tmp_path)
    first = _prepare(home, target, instance, workpad, review)
    before = _head(workpad)
    before_handoffs = {
        path.name for path in (workpad / "handoffs").glob("*")
    }
    with pytest.raises(LifecycleError, match="differs from successor"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
            capability_manifest_id="capmanifest_00000000-0000-4000-8000-000000000099",
        )
    assert _head(workpad) == before
    assert {path.name for path in (workpad / "handoffs").glob("*")} == before_handoffs
    assert parse_json_bytes((workpad / "manifests/active-gig-version.json").read_bytes())["active_version"] == 1


def test_approval_requires_successor_binding_without_new_publication(tmp_path: Path) -> None:
    home, target, instance, workpad, _base, review, _project_id = _fixture(tmp_path)
    first = _prepare(home, target, instance, workpad, review, operation_key="successor-missing-binding")
    Path(workpad / str(first.binding_ref["path"])).unlink()
    before = _head(workpad)
    with pytest.raises(LifecycleError, match="successor binding is unavailable"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
        )
    assert _head(workpad) == before
    assert parse_json_bytes((workpad / "manifests/active-gig-version.json").read_bytes())["active_version"] == 1


def test_approval_revalidates_source_before_publication(tmp_path: Path) -> None:
    home, target, instance, workpad, _base, review, _project_id = _fixture(tmp_path)
    first = _prepare(home, target, instance, workpad, review, operation_key="successor-source-race")
    source = workpad / "tools" / "cap_00000000-0000-4000-8000-000000000071" / "record_tool.py"
    original = source.read_bytes()
    source.write_bytes(original + b"\n# changed before explicit approval\n")
    before = _head(workpad)
    with pytest.raises(LifecycleError, match="source"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
        )
    assert _head(workpad) == before
    assert parse_json_bytes((workpad / "manifests/active-gig-version.json").read_bytes())["active_version"] == 1


def test_approval_crash_after_tag_recovers_without_duplicate_review_manifest(tmp_path: Path) -> None:
    home, target, instance, workpad, _base, review, project_id = _fixture(tmp_path)
    first = _prepare(home, target, instance, workpad, review, operation_key="successor-recovery")

    class Crash(RuntimeError):
        pass

    def crash(step: str) -> None:
        if step == "after_approval_tag":
            raise Crash("disposable crash")

    with pytest.raises(Crash):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
            capability_manifest_id=str(Path(str(review.reviewed_manifest_ref["path"])).stem),
            observer=crash,
        )
    recovered = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=str(first.proposal["proposal_id"]),
        gig_id=instance.gig_id,
        capability_manifest_id=str(Path(str(review.reviewed_manifest_ref["path"])).stem),
    )
    assert recovered.version == 2
    assert read_committed_artifact(
        workpad=workpad,
        project_id=project_id,
        gig_id=instance.gig_id,
        path=str(review.reviewed_manifest_ref["path"]),
    )[0]
    replay = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=str(first.proposal["proposal_id"]),
        gig_id=instance.gig_id,
        capability_manifest_id=str(Path(str(review.reviewed_manifest_ref["path"])).stem),
    )
    assert replay.publication_commit == recovered.publication_commit


def test_recovery_refuses_swapped_binding_before_commit_b(tmp_path: Path) -> None:
    home, target, instance, workpad, _base, review, _project_id = _fixture(tmp_path)
    first = _prepare(home, target, instance, workpad, review, operation_key="successor-swapped-recovery")

    def crash(step: str) -> None:
        if step == "after_approval_tag":
            raise RuntimeError("disposable crash after sealed approval tag")

    with pytest.raises(RuntimeError):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
            observer=crash,
        )
    before_head = _head(workpad)
    sidecar = workpad / str(first.binding_ref["path"])
    binding = parse_json_bytes(sidecar.read_bytes())
    assert isinstance(binding, dict)
    binding["reviewed_manifest_ref"] = {
        **binding["reviewed_manifest_ref"],
        "path": "manifests/capabilities/capmanifest_00000000-0000-4000-8000-000000000099.json",
    }
    sidecar.write_bytes(canonical_json_bytes(binding))
    with pytest.raises(LifecycleError, match="binding is unavailable"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=str(first.proposal["proposal_id"]),
            gig_id=instance.gig_id,
        )
    assert _head(workpad) == before_head
