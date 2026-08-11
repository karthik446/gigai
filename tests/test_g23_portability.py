from __future__ import annotations

import json

import pytest

from gigai.capabilities import materialize_capability_manifest
from gigai.lifecycle import approve_offline, create_offline
from gigai.portability import (
    PortabilityError,
    resolve_proposal_lineage,
    verify_active_version_portability,
)
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
