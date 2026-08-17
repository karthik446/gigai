"""Verify G23 portability behavior from an installed GigAI wheel."""

from __future__ import annotations

from pathlib import Path
import tempfile
import uuid

from gigai.capabilities import install_local_capability, materialize_capability_manifest
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.lifecycle import approve_offline, create_offline
from gigai.portability import resolve_proposal_lineage, verify_active_version_portability
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import SCHEMA_NAMES
from gigai.workpad import resolve_workpad


CAP = "cap_00000000-0000-4000-8000-000000000003"
MANIFEST = "capmanifest_00000000-0000-4000-8000-000000000004"


def _manifest(digest: str, *, gig_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_id": MANIFEST,
        "manifest_version": 1,
        "gig_id": gig_id,
        "created_at": "2026-08-11T00:00:00Z",
        "created_by": {"kind": "gigai", "id": "installed-g23", "model_target": None},
        "capabilities": [
            {
                "capability_id": CAP,
                "goal_ids": ["goal_00000000-0000-4000-8000-000000000002"],
                "kind": "local_capability",
                "name": "installed-fixture-tool",
                "requested_version": "1.0.0",
                "source_constraints": {
                    "allowed_source_kinds": ["local_artifact"],
                    "required_digest": digest,
                    "required_identity": f"{CAP}.artifact",
                },
                "declared_effects": ["read_local_metadata"],
                "permissions": {"filesystem": "write_isolated", "network": "none", "credentials": "none"},
                "credential_requirements": [],
                "network_requirement": "none",
                "availability_state": "missing",
                "compatibility": {"status": "compatible", "reason": None},
                "security_review": {"status": "passed", "checks": ["path_containment"], "reason": None},
                "alternatives": [],
                "options": [
                    {"option_id": "A", "kind": "install_local", "label": "Install pinned local artifact", "ordinal": 0, "decision": "pending"},
                    {"option_id": "B", "kind": "continue_without", "label": "Continue without capability", "ordinal": 1, "decision": "pending"},
                ],
            }
        ],
    }


def main() -> int:
    if len(SCHEMA_NAMES) != 29:
        raise SystemExit(f"installed G23 schema inventory is {len(SCHEMA_NAMES)}, expected 29")
    with tempfile.TemporaryDirectory(prefix="gigai-g23-installed-") as directory:
        root = Path(directory)
        runtime_home = root / "runtime-home"
        runtime_target = root / "runtime-target"
        runtime_target.mkdir()
        run_setup(
            build_config(
                home_root=runtime_home,
                workpad_root=root / "runtime-workpads",
                editor_argv=("/usr/bin/true",),
                open_with_target=False,
            )
        )
        values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 100))
        initialize_target(home_root=runtime_home, requested_target=runtime_target, uuid_factory=lambda: next(values))
        created = create_offline(
            home_root=runtime_home,
            requested_target=runtime_target,
            name="installed-g23-runtime",
            open_editor=False,
            uuid_factory=lambda: next(values),
        )
        runtime_source = created.workpad / "tools/.sources" / f"{CAP}.artifact"
        runtime_source.parent.mkdir(parents=True)
        runtime_payload = b"installed G23 runtime source\n"
        runtime_source.write_bytes(runtime_payload)
        runtime_manifest = canonical_json_bytes(
            _manifest(digest_imported_bytes(runtime_payload), gig_id=created.gig_id)
        )
        materialize_capability_manifest(created.workpad, parse_json_bytes(runtime_manifest))
        approved = approve_offline(
            home_root=runtime_home,
            requested_target=runtime_target,
            proposal_id=created.proposal_id,
            capability_manifest_id=MANIFEST,
            uuid_factory=lambda: next(values),
        )
        resolved = resolve_workpad(
            home_root=runtime_home,
            requested_target=runtime_target,
            gig_id=created.gig_id,
            allow_semantic_state=True,
        )
        portability = verify_active_version_portability(resolved.path)
        if portability.outcome != "verified_portable":
            raise SystemExit(f"installed G23 pointer replay returned {portability.outcome}")
        lineage = resolve_proposal_lineage(
            resolved.path,
            approved_proposal_id=approved.proposal_id,
            gig_id=created.gig_id,
            sealed_commit=approved.sealed_commit,
        )
        if lineage.proposal_ids != (created.proposal_id,):
            raise SystemExit("installed G23 lineage replay was not single-hop")

        first = root / "machine-a"
        second = root / "machine-b"
        first_source = first / "tools/.sources" / f"{CAP}.artifact"
        first_source.parent.mkdir(parents=True)
        source = b"installed G23 source\n"
        first_source.write_bytes(source)
        manifest_bytes = canonical_json_bytes(
            _manifest(
                digest_imported_bytes(source),
                gig_id="gig_00000000-0000-4000-8000-000000000001",
            )
        )
        materialize_capability_manifest(first, parse_json_bytes(manifest_bytes))

        second_source = second / "tools/.sources" / f"{CAP}.artifact"
        second_source.parent.mkdir(parents=True)
        second_source.write_bytes(first_source.read_bytes())
        materialize_capability_manifest(second, parse_json_bytes(manifest_bytes))
        if (second / f"tools/{CAP}").exists():
            raise SystemExit("installed tool bytes were transported before replay")
        record = install_local_capability(
            second,
            manifest_bytes,
            capability_id=CAP,
            option_id="A",
            approving_actor={"kind": "operator", "id": "installed-g23", "model_target": None},
            now="2026-08-11T00:00:00Z",
            installation_id="capinstall_00000000-0000-4000-8000-000000000005",
        )
        if parse_json_bytes(record)["outcome"] != "installed":
            raise SystemExit("installed G23 replay did not install the pinned capability")
        if (second / f"tools/{CAP}/artifact").read_bytes() != source:
            raise SystemExit("installed G23 replay produced different tool bytes")
    print("verified installed GigAI G23 portability replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
