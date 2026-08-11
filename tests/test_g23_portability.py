from __future__ import annotations

import json

import pytest

from gigai.capabilities import install_local_capability, materialize_capability_manifest
from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.lifecycle import approve_offline, create_offline
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
            "refused_lineage_cycle",
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
