from __future__ import annotations

import json
import ast
from pathlib import Path
import subprocess

import pytest

from gigai.capabilities import install_local_capability, materialize_capability_manifest
from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.lifecycle import LifecycleError, approve_offline, create_offline
from gigai.portability import (
    PortabilityError,
    resolve_proposal_lineage,
    verify_active_version_portability,
)
import gigai.portability as portability
from tests.test_g08_offline_create_lifecycle import _configured_target, _uuids
from tests.test_g17_capabilities import _capability, _manifest, _stage_source


def test_g23_binds_manifest_and_resolves_sealed_lineage(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="portable-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    digest = _stage_source(created.workpad)
    materialize_capability_manifest(
        created.workpad, _manifest(_capability(digest=digest))
    )
    approved = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        capability_manifest_id="capmanifest_00000000-0000-4000-8000-000000000004",
        uuid_factory=_uuids(),
    )

    pointer_path = created.workpad / "manifests/active-gig-version.json"
    pointer_before = pointer_path.read_bytes()
    head_before = __import__("subprocess").run(
        ["git", "-C", str(created.workpad), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    result = verify_active_version_portability(created.workpad)
    assert result.outcome == "verified_portable"
    assert result.publication_commit == approved.publication_commit
    lineage = resolve_proposal_lineage(
        created.workpad,
        approved_proposal_id=created.proposal_id,
        gig_id=created.gig_id,
        sealed_commit=approved.sealed_commit,
    )
    assert lineage.proposal_ids == (created.proposal_id,)
    assert pointer_path.read_bytes() == pointer_before
    assert __import__("subprocess").run(
        ["git", "-C", str(created.workpad), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout == head_before


def test_g23_legacy_pointer_reports_non_portable(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="legacy-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=_uuids(),
    )
    assert verify_active_version_portability(created.workpad).outcome == "reported_non_portable"


def test_g23_unpublished_pointer_is_recoverable_refusal(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="unpublished-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )

    def crash(step):
        if step == "after_approval_tag":
            raise RuntimeError(step)

    with pytest.raises(RuntimeError):
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            uuid_factory=_uuids(),
            observer=crash,
        )
    with pytest.raises(PortabilityError) as error:
        verify_active_version_portability(created.workpad)
    assert error.value.code == "refused_unpublished_pointer"


def test_g23_manifest_digest_is_revalidated(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="digest-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    digest = _stage_source(created.workpad)
    materialize_capability_manifest(created.workpad, _manifest(_capability(digest=digest)))
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        capability_manifest_id="capmanifest_00000000-0000-4000-8000-000000000004",
        uuid_factory=_uuids(),
    )
    manifest_path = created.workpad / "manifests/capabilities/capmanifest_00000000-0000-4000-8000-000000000004.json"
    tampered = parse_json_bytes(manifest_path.read_bytes())
    assert isinstance(tampered, dict)
    tampered["created_by"] = {"kind": "gigai", "id": "tampered", "model_target": None}
    manifest_path.write_bytes(canonical_json_bytes(tampered))
    with pytest.raises(PortabilityError) as error:
        verify_active_version_portability(created.workpad)
    assert error.value.code == "refused_digest_mismatch"


def test_g23_semantic_manifest_refusals_are_independent(tmp_path):
    manifest = _manifest(_capability(digest="sha256:" + "a" * 64))
    payload = canonical_json_bytes(manifest)
    path = tmp_path / "manifests/capabilities/manifest.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    reference = {
        "path": "manifests/capabilities/manifest.json",
        "content_sha256": "sha256:" + "b" * 64,
        "media_type": "application/json",
        "size_bytes": len(payload),
    }
    with pytest.raises(PortabilityError) as digest_error:
        portability._read_referenced_manifest(tmp_path, reference)
    assert digest_error.value.code == "refused_digest_mismatch"

    outside = tmp_path / "outside.json"
    outside.write_bytes(payload)
    unsafe = dict(reference, path="../outside.json")
    with pytest.raises(PortabilityError) as path_error:
        portability._read_referenced_manifest(tmp_path, unsafe)
    assert path_error.value.code == "refused_unsafe_path"

    other = dict(manifest, gig_id="gig_00000000-0000-4000-8000-000000000099")
    other_path = tmp_path / "manifests/capabilities/other.json"
    other_bytes = canonical_json_bytes(other)
    other_path.write_bytes(other_bytes)
    with pytest.raises(PortabilityError) as binding_error:
        parsed = parse_json_bytes(other_bytes)
        portability._validate_manifest_binding(parsed, manifest["gig_id"])
    assert binding_error.value.code == "refused_unbound_manifest"


def test_g23_three_hop_lineage_is_ordered(monkeypatch, tmp_path):
    proposals = {
        "gp_current": {"proposal_id": "gp_current", "gig_id": "gig_a", "parent_proposal_id": "gp_middle", "kind": "improve"},
        "gp_middle": {"proposal_id": "gp_middle", "gig_id": "gig_a", "parent_proposal_id": "gp_root", "kind": "amend"},
        "gp_root": {"proposal_id": "gp_root", "gig_id": "gig_a", "parent_proposal_id": None, "kind": "create"},
    }
    monkeypatch.setattr(portability, "_historical_proposals", lambda _root, _sealed: proposals)
    lineage = resolve_proposal_lineage(
        tmp_path,
        approved_proposal_id="gp_current",
        gig_id="gig_a",
        sealed_commit="a" * 40,
    )
    assert lineage.proposal_ids == ("gp_current", "gp_middle", "gp_root")


def _real_lineage_fixture(tmp_path, revisions):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="real-lineage-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    workpad = created.workpad
    base = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(base, dict)
    for proposal_id, parent_id, kind, gig_id in revisions:
        if parent_id == "$ROOT":
            parent_id = created.proposal_id
        if gig_id == "$GIG":
            gig_id = created.gig_id
        proposal = dict(base)
        proposal.update(
            {
                "proposal_id": proposal_id,
                "parent_proposal_id": parent_id,
                "kind": kind,
                "status": "approved",
                "gig_id": gig_id,
            }
        )
        (workpad / "manifests/gig-proposal.json").write_bytes(canonical_json_bytes(proposal))
        subprocess.run(["git", "-C", str(workpad), "add", "manifests/gig-proposal.json"], check=True)
        subprocess.run(
            ["git", "-C", str(workpad), "commit", "-m", f"fixture {proposal_id}"],
            check=True,
            capture_output=True,
            text=True,
        )
    sealed = subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return workpad, created.gig_id, sealed, created.proposal_id


def test_g23_three_hop_lineage_walks_real_git_history(tmp_path):
    p2 = "gp_11111111-1111-4111-8111-111111111111"
    p3 = "gp_22222222-2222-4222-8222-222222222222"
    workpad, gig_id, sealed, root_id = _real_lineage_fixture(
        tmp_path,
        ((p2, "$ROOT", "amend", "$GIG"), (p3, p2, "improve", "$GIG")),
    )
    lineage = resolve_proposal_lineage(
        workpad, approved_proposal_id=p3, gig_id=gig_id, sealed_commit=sealed
    )
    assert lineage.proposal_ids == (p3, p2, root_id)


@pytest.mark.parametrize("case", ["cycle", "missing", "cross_gig"])
def test_g23_lineage_refusals_walk_real_git_history(tmp_path, case):
    root_id = "gp_00000000-0000-4000-8000-000000000003"
    gig_id = "gig_00000000-0000-4000-8000-000000000001"
    p2 = "gp_11111111-1111-4111-8111-111111111111"
    p3 = "gp_22222222-2222-4222-8222-222222222222"
    if case == "cycle":
        revisions = ((p2, p3, "amend", "$GIG"), (p3, p2, "improve", "$GIG"))
        expected = "refused_cycle"
        current = p3
    elif case == "missing":
        revisions = ((p2, "gp_33333333-3333-4333-8333-333333333333", "amend", "$GIG"),)
        expected = "refused_missing_parent"
        current = p2
    else:
        revisions = ((p2, None, "create", "gig_00000000-0000-4000-8000-000000000099"),)
        expected = "refused_cross_gig_lineage"
        current = p2
    workpad, actual_gig_id, sealed, _root = _real_lineage_fixture(tmp_path, revisions)
    with pytest.raises(PortabilityError) as error:
        resolve_proposal_lineage(
            workpad,
            approved_proposal_id=current,
            gig_id=actual_gig_id,
            sealed_commit=sealed,
        )
    assert error.value.code == expected


def test_g23_forged_publication_child_is_ambiguous(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="ambiguous-publication-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    approved = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=_uuids(),
    )
    base_branch = subprocess.run(
        ["git", "-C", str(created.workpad), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    names = subprocess.run(
        ["git", "-C", str(created.workpad), "show", "--format=", "--name-only", approved.publication_commit],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    handoff = next(name for name in names if name.startswith("handoffs/") and name.endswith(".txt"))
    handoff_bytes = subprocess.run(
        ["git", "-C", str(created.workpad), "show", f"{approved.publication_commit}:{handoff}"],
        check=True,
        capture_output=True,
    ).stdout
    pointer = parse_json_bytes(
        subprocess.run(
            ["git", "-C", str(created.workpad), "show", f"{approved.publication_commit}:manifests/active-gig-version.json"],
            check=True,
            capture_output=True,
        ).stdout
    )
    assert isinstance(pointer, dict)
    pointer["approved_proposal_id"] = "gp_99999999-9999-4999-8999-999999999999"
    subprocess.run(["git", "-C", str(created.workpad), "checkout", "-b", "g23-forged", approved.sealed_commit], check=True, capture_output=True)
    (created.workpad / handoff).parent.mkdir(parents=True, exist_ok=True)
    (created.workpad / handoff).write_bytes(handoff_bytes)
    (created.workpad / "manifests/active-gig-version.json").write_bytes(canonical_json_bytes(pointer))
    subprocess.run(["git", "-C", str(created.workpad), "add", handoff, "manifests/active-gig-version.json"], check=True)
    subprocess.run(["git", "-C", str(created.workpad), "commit", "-m", "forged publication"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(created.workpad), "checkout", "--detach"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(created.workpad), "update-ref", "-d", f"refs/heads/{base_branch}"], check=True)
    with pytest.raises(PortabilityError) as error:
        verify_active_version_portability(created.workpad)
    assert error.value.code == "refused_ambiguous_publication"


def test_g23_tag_resolution_is_independently_refused(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="tag-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    approved = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=_uuids(),
    )
    with pytest.raises(PortabilityError) as error:
        portability._require_approval_tag(created.workpad, "gig-v999999", approved.sealed_commit)
    assert error.value.code == "refused_unsealed_pointer"


def test_g23_readers_do_not_touch_installed_tool_bytes():
    source = portability.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    assert "tools/" not in text


def test_g23_effect_boundary_has_no_network_or_write_imports():
    tree = ast.parse(Path(portability.__file__).read_text(encoding="utf-8"))
    imported = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported.intersection({"socket", "httpx", "urllib", "requests"})
    assert '"commit"' not in Path(portability.__file__).read_text(encoding="utf-8")


def test_g23_pointer_substitution_is_refused(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="tamper-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=_uuids(),
    )
    pointer_path = created.workpad / "manifests/active-gig-version.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["approved_proposal_id"] = "gp_00000000-0000-4000-8000-000000000099"
    pointer_path.write_text(json.dumps(pointer, sort_keys=True, separators=(",", ":")))
    with pytest.raises(PortabilityError, match="sealed publication") as error:
        verify_active_version_portability(created.workpad)
    assert error.value.code == "refused_unsealed_pointer"


def test_g23_reinstalls_from_manifest_and_source_on_second_home(tmp_path):
    first_home = tmp_path / "machine-a"
    second_home = tmp_path / "machine-b"
    first_home.mkdir()
    second_home.mkdir()
    digest = _stage_source(first_home, payload=b"portable source bytes\n")
    manifest_bytes = canonical_json_bytes(_manifest(_capability(digest=digest)))
    materialize_capability_manifest(
        first_home, parse_json_bytes(manifest_bytes)
    )
    # The transport boundary copies only the pinned source and manifest bytes;
    # it does not copy the installed tools/<capability-id> directory.
    (second_home / "tools/.sources").mkdir(parents=True)
    (second_home / "tools/.sources" / "cap_00000000-0000-4000-8000-000000000003.artifact").write_bytes(
        (first_home / "tools/.sources" / "cap_00000000-0000-4000-8000-000000000003.artifact").read_bytes()
    )
    materialize_capability_manifest(second_home, parse_json_bytes(manifest_bytes))
    assert not (second_home / "tools/cap_00000000-0000-4000-8000-000000000003").exists()
    record = install_local_capability(
        second_home,
        manifest_bytes,
        capability_id="cap_00000000-0000-4000-8000-000000000003",
        option_id="A",
        approving_actor={"kind": "operator", "id": "portable-test", "model_target": None},
        now="2026-08-11T00:00:00Z",
        installation_id="capinstall_00000000-0000-4000-8000-000000000005",
    )
    assert parse_json_bytes(record)["outcome"] == "installed"
    assert (second_home / "tools/cap_00000000-0000-4000-8000-000000000003/artifact").read_bytes() == b"portable source bytes\n"
    assert not (first_home / "tools/cap_00000000-0000-4000-8000-000000000003").exists()


@pytest.mark.parametrize(
    ("proposals", "code"),
    [
        (
            {
                "gp_current": {"proposal_id": "gp_current", "gig_id": "gig_a", "parent_proposal_id": "gp_parent", "kind": "improve"},
                "gp_parent": {"proposal_id": "gp_parent", "gig_id": "gig_a", "parent_proposal_id": "gp_current", "kind": "amend"},
            },
            "refused_cycle",
        ),
        (
            {"gp_current": {"proposal_id": "gp_current", "gig_id": "gig_a", "parent_proposal_id": None, "kind": "amend"}},
            "refused_lineage_authority",
        ),
        (
            {"gp_current": {"proposal_id": "gp_current", "gig_id": "gig_a", "parent_proposal_id": "gp_missing", "kind": "improve"}},
            "refused_missing_parent",
        ),
        (
            {"gp_current": {"proposal_id": "gp_current", "gig_id": "gig_b", "parent_proposal_id": None, "kind": "create"}},
            "refused_cross_gig_lineage",
        ),
    ],
)
def test_g23_lineage_refusals_are_closed(monkeypatch, tmp_path, proposals, code):
    monkeypatch.setattr(portability, "_historical_proposals", lambda _root, _sealed: proposals)
    with pytest.raises(PortabilityError) as error:
        resolve_proposal_lineage(
            tmp_path,
            approved_proposal_id="gp_current",
            gig_id="gig_a",
            sealed_commit="a" * 40,
        )
    assert error.value.code == code


def test_g23_historical_proposal_schema_is_real_git_guarded(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="malformed-lineage-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    proposal_path = created.workpad / "manifests/gig-proposal.json"
    proposal_path.write_text('{"proposal_id":"' + created.proposal_id + '"}')
    subprocess.run(["git", "-C", str(created.workpad), "add", str(proposal_path.relative_to(created.workpad))], check=True)
    subprocess.run(["git", "-C", str(created.workpad), "commit", "-m", "malformed lineage fixture"], check=True, capture_output=True, text=True)
    sealed = subprocess.run(["git", "-C", str(created.workpad), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    with pytest.raises(PortabilityError) as error:
        resolve_proposal_lineage(created.workpad, approved_proposal_id=created.proposal_id, gig_id=created.gig_id, sealed_commit=sealed)
    assert error.value.code == "refused_lineage_authority"


def test_g23_implicit_manifest_carry_forward_revalidates_bytes(tmp_path):
    home, target = _configured_target(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="carry-forward-gig",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    digest = _stage_source(created.workpad)
    materialize_capability_manifest(created.workpad, _manifest(_capability(digest=digest)))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, capability_manifest_id="capmanifest_00000000-0000-4000-8000-000000000004", uuid_factory=_uuids())
    proposal_path = created.workpad / "manifests/gig-proposal.json"
    proposal = parse_json_bytes(proposal_path.read_bytes())
    assert isinstance(proposal, dict)
    proposal.update({"proposal_id": "gp_99999999-9999-4999-8999-999999999999", "parent_proposal_id": created.proposal_id, "status": "proposed", "kind": "amend"})
    proposal_path.write_bytes(canonical_json_bytes(proposal))
    manifest_path = created.workpad / "manifests/capabilities/capmanifest_00000000-0000-4000-8000-000000000004.json"
    manifest = parse_json_bytes(manifest_path.read_bytes())
    assert isinstance(manifest, dict)
    manifest["created_by"] = {"kind": "gigai", "id": "changed", "model_target": None}
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(LifecycleError, match="stale or invalid"):
        approve_offline(home_root=home, requested_target=target, proposal_id=proposal["proposal_id"], uuid_factory=_uuids())
