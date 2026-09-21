from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import uuid

import pytest
from click.testing import CliRunner

from gigai.capability_review import review_local_tool
from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.cli import cli
from gigai.lifecycle import LifecycleError, approve_offline, propose_graph_set_offline
from tests.test_scout05_bundled_tools import _candidate
from tests.test_scout05_capability_review import _review_kwargs


def _head(workpad: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _tags(workpad: Path) -> tuple[str, ...]:
    return tuple(
        subprocess.run(
            ["git", "-C", str(workpad), "tag", "--points-at", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )


def _all_tags(workpad: Path) -> tuple[str, ...]:
    return tuple(
        subprocess.run(
            ["git", "-C", str(workpad), "show-ref", "--tags"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )


def _approval_state(workpad: Path) -> tuple[str, tuple[str, ...], bytes]:
    pointer_path = workpad / "manifests/active-gig-version.json"
    return _head(workpad), _all_tags(workpad), pointer_path.read_bytes()


def _assert_approval_state(workpad: Path, before: tuple[str, tuple[str, ...], bytes]) -> None:
    assert _approval_state(workpad) == before


def _ordinary_definition(tmp_path: Path, workpad: Path) -> Path:
    first = next(workpad.glob("manifests/software/*/compiled/first-graph-set-definition.json"))
    source_root = tmp_path / "ordinary-source"
    shutil.copytree(first.parent, source_root)
    definition = parse_json_bytes((source_root / first.name).read_bytes())
    assert isinstance(definition, dict)
    ordinary = {
        key: definition[key]
        for key in ("schema_version", "gig_id", "graphs", "shared_policy")
    }
    path = source_root / "ordinary-amendment.json"
    path.write_bytes(canonical_json_bytes(ordinary))
    return path


def _reviewed_ordinary_amendment(tmp_path: Path):
    home, target, instance, workpad = _candidate(tmp_path)
    assert instance.proposal_id is not None
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=instance.proposal_id,
        gig_id=instance.gig_id,
    )
    review = review_local_tool(
        **_review_kwargs(instance, workpad, operation_key="reviewed-manifest-guard")
    )
    assert review.reviewed_manifest_ref is not None
    ordinary = propose_graph_set_offline(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        definition_path=_ordinary_definition(tmp_path, workpad),
    )
    manifest_id = Path(str(review.reviewed_manifest_ref["path"])).stem
    return home, target, instance, workpad, ordinary, manifest_id, review


def _renamed_reviewed_manifest(
    workpad: Path,
    manifest_id: str,
    *,
    changed_creator: bool = False,
    omit_creator: bool = False,
    decoy_position: str | None = None,
) -> str:
    original_path = workpad / f"manifests/capabilities/{manifest_id}.json"
    copied = parse_json_bytes(original_path.read_bytes())
    assert isinstance(copied, dict)
    rogue_id = f"capmanifest_{uuid.uuid4()}"
    copied["manifest_id"] = rogue_id
    if changed_creator:
        copied["created_by"] = {
            "kind": "operator",
            "id": "local-user",
            "model_target": None,
        }
        copied["created_at"] = "2026-09-10T12:00:00Z"
        copied["manifest_version"] = 999
        capability = copied["capabilities"][0]
        assert isinstance(capability, dict)
        capability["compatibility"] = {
            "status": "compatible",
            "reason": "cosmetic copied metadata",
        }
        capability["security_review"] = {
            "status": "passed",
            "checks": capability["security_review"]["checks"],
            "reason": "cosmetic copied metadata",
        }
    if omit_creator:
        copied.pop("created_by")
    if decoy_position is not None:
        capabilities = copied["capabilities"]
        assert isinstance(capabilities, list) and capabilities
        reviewed_capability = parse_json_bytes(canonical_json_bytes(capabilities[0]))
        assert isinstance(reviewed_capability, dict)
        reviewed_capability["capability_id"] = f"cap_{uuid.uuid4()}"
        if decoy_position == "append":
            capabilities.append(reviewed_capability)
        elif decoy_position == "prepend":
            capabilities.insert(0, reviewed_capability)
        else:
            raise AssertionError(f"unexpected decoy position: {decoy_position}")
    (workpad / f"manifests/capabilities/{rogue_id}.json").write_bytes(
        canonical_json_bytes(copied)
    )
    return rogue_id


def test_reviewed_manifest_cannot_bind_through_ordinary_proposal_or_mutated_decision(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad, ordinary, manifest_id, review = _reviewed_ordinary_amendment(tmp_path)
    pointer_path = workpad / "manifests/active-gig-version.json"
    before_head, before_tags, before_pointer = _head(workpad), _tags(workpad), pointer_path.read_bytes()

    decision_path = workpad / str(review.decision_ref["path"])
    edited = parse_json_bytes(decision_path.read_bytes())
    assert isinstance(edited, dict)
    edited["reviewer_rationale"] = "working bytes cannot hide committed review provenance"
    decision_path.write_bytes(canonical_json_bytes(edited))
    source = workpad / "tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py"
    source.write_bytes(source.read_bytes() + b"\n# tampered tool bytes\n")

    with pytest.raises(LifecycleError, match="requires its authenticated successor"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            capability_manifest_id=manifest_id,
        )
    assert _head(workpad) == before_head
    assert _tags(workpad) == before_tags
    assert pointer_path.read_bytes() == before_pointer


@pytest.mark.parametrize(
    ("changed_creator", "omit_creator"),
    [(False, False), (True, False), (False, True)],
    ids=["exact-copy", "creator-and-cosmetics-changed", "creator-removed"],
)
def test_renamed_reviewed_manifest_refuses_before_normal_approval(
    tmp_path: Path, changed_creator: bool, omit_creator: bool
) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)
    rogue_id = _renamed_reviewed_manifest(
        workpad,
        manifest_id,
        changed_creator=changed_creator,
        omit_creator=omit_creator,
    )
    source = workpad / "tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py"
    source.write_bytes(source.read_bytes() + b"\n# reviewed source tampered before approval\n")
    before = _approval_state(workpad)

    with pytest.raises(LifecycleError):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            capability_manifest_id=rogue_id,
        )
    _assert_approval_state(workpad, before)


def test_public_cli_refuses_renamed_reviewed_manifest_without_publication(tmp_path: Path) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)
    rogue_id = _renamed_reviewed_manifest(workpad, manifest_id)
    before = _approval_state(workpad)

    result = CliRunner().invoke(
        cli,
        [
            "approve",
            ordinary.proposal_id,
            "--gig",
            instance.gig_id,
            "--capability-manifest-id",
            rogue_id,
            "--home",
            str(home),
            "--target",
            str(target),
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert "authenticated successor" in result.output
    _assert_approval_state(workpad, before)


@pytest.mark.parametrize("decoy_position", ["append", "prepend"])
def test_renamed_reviewed_capability_containment_refuses_normal_approval(
    tmp_path: Path, decoy_position: str
) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)
    rogue_id = _renamed_reviewed_manifest(
        workpad,
        manifest_id,
        changed_creator=True,
        decoy_position=decoy_position,
    )
    source = workpad / "tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py"
    source.write_bytes(source.read_bytes() + b"\n# containment source tampered before approval\n")
    before = _approval_state(workpad)

    with pytest.raises(LifecycleError, match="authenticated successor"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            capability_manifest_id=rogue_id,
        )
    _assert_approval_state(workpad, before)


@pytest.mark.parametrize("decoy_position", ["append", "prepend"])
def test_public_cli_refuses_contained_reviewed_capability(tmp_path: Path, decoy_position: str) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)
    rogue_id = _renamed_reviewed_manifest(
        workpad,
        manifest_id,
        changed_creator=True,
        decoy_position=decoy_position,
    )
    before = _approval_state(workpad)

    result = CliRunner().invoke(
        cli,
        [
            "approve",
            ordinary.proposal_id,
            "--gig",
            instance.gig_id,
            "--capability-manifest-id",
            rogue_id,
            "--home",
            str(home),
            "--target",
            str(target),
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert "authenticated successor" in result.output
    _assert_approval_state(workpad, before)


@pytest.mark.parametrize("unsafe", ["source-deleted", "manifest-deleted", "manifest-symlink"])
def test_renamed_reviewed_manifest_unsafe_working_paths_refuse_before_publication(
    tmp_path: Path, unsafe: str
) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)
    rogue_id = _renamed_reviewed_manifest(workpad, manifest_id)
    rogue_path = workpad / f"manifests/capabilities/{rogue_id}.json"
    source = workpad / "tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py"
    if unsafe == "source-deleted":
        source.unlink()
    elif unsafe == "manifest-deleted":
        rogue_path.unlink()
    else:
        rogue_path.unlink()
        rogue_path.symlink_to(workpad / f"manifests/capabilities/{manifest_id}.json")
    before = _approval_state(workpad)

    with pytest.raises(LifecycleError):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            capability_manifest_id=rogue_id,
        )
    _assert_approval_state(workpad, before)


def test_reviewed_manifest_is_refused_during_missing_commit_b_recovery(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)

    def crash(step: str) -> None:
        if step == "after_approval_tag":
            raise RuntimeError("disposable ordinary approval crash")

    # This is a legitimate old crash shape: Commit A approved an ordinary
    # no-manifest amendment. Recovery must not newly attach reviewed authority.
    with pytest.raises(RuntimeError, match="disposable ordinary approval crash"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            observer=crash,
        )
    pointer_path = workpad / "manifests/active-gig-version.json"
    pointer_before = pointer_path.read_bytes()
    before_head, before_tags = _head(workpad), _tags(workpad)

    with pytest.raises(LifecycleError, match="requires its authenticated successor"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            capability_manifest_id=manifest_id,
        )
    assert _head(workpad) == before_head
    assert _tags(workpad) == before_tags
    assert pointer_path.read_bytes() == pointer_before

    recovered = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=ordinary.proposal_id,
        gig_id=instance.gig_id,
    )
    assert recovered.version == 2


def test_renamed_reviewed_manifest_is_refused_during_missing_commit_b_recovery(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)
    rogue_id = _renamed_reviewed_manifest(workpad, manifest_id, changed_creator=True)
    source = workpad / "tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py"
    source.write_bytes(source.read_bytes() + b"\n# reviewed source tampered before recovery\n")

    def crash(step: str) -> None:
        if step == "after_approval_tag":
            raise RuntimeError("disposable ordinary approval crash")

    with pytest.raises(RuntimeError, match="disposable ordinary approval crash"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            observer=crash,
        )
    before = _approval_state(workpad)

    with pytest.raises(LifecycleError, match="authenticated successor"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            capability_manifest_id=rogue_id,
        )
    _assert_approval_state(workpad, before)


@pytest.mark.parametrize("decoy_position", ["append", "prepend"])
def test_contained_reviewed_capability_is_refused_during_missing_commit_b_recovery(
    tmp_path: Path, decoy_position: str
) -> None:
    home, target, instance, workpad, ordinary, manifest_id, _review = _reviewed_ordinary_amendment(tmp_path)
    rogue_id = _renamed_reviewed_manifest(
        workpad,
        manifest_id,
        changed_creator=True,
        decoy_position=decoy_position,
    )

    def crash(step: str) -> None:
        if step == "after_approval_tag":
            raise RuntimeError("disposable ordinary approval crash")

    with pytest.raises(RuntimeError, match="disposable ordinary approval crash"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            observer=crash,
        )
    before = _approval_state(workpad)

    with pytest.raises(LifecycleError, match="authenticated successor"):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=ordinary.proposal_id,
            gig_id=instance.gig_id,
            capability_manifest_id=rogue_id,
        )
    _assert_approval_state(workpad, before)
