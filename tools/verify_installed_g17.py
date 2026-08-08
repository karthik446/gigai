"""Verify installed G17 capability inspection and local installation."""

from __future__ import annotations

from pathlib import Path
import tempfile

from gigai.capabilities import (
    install_local_capability,
    inspect_capability_manifest,
)
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.validators import validate_serialized_contract


NOW = "2026-08-08T00:00:00Z"
GIG = "gig_00000000-0000-4000-8000-000000000001"
GOAL = "goal_00000000-0000-4000-8000-000000000002"
CAP = "cap_00000000-0000-4000-8000-000000000003"
MANIFEST = "capmanifest_00000000-0000-4000-8000-000000000004"
ACTOR = {"kind": "operator", "id": "installed-g17", "model_target": None}


def _manifest(digest: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_id": MANIFEST,
        "manifest_version": 1,
        "gig_id": GIG,
        "created_at": NOW,
        "created_by": {"kind": "gigai", "id": "installed-g17", "model_target": None},
        "capabilities": [
            {
                "capability_id": CAP,
                "goal_ids": [GOAL],
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
    with tempfile.TemporaryDirectory(prefix="gigai-g17-wheel-") as temporary:
        root = Path(temporary)
        payload = b"installed G17 fixture\n"
        source = root / "tools/.sources" / f"{CAP}.artifact"
        source.parent.mkdir(parents=True)
        source.write_bytes(payload)
        manifest = canonical_json_bytes(_manifest(digest_imported_bytes(payload)))
        inspection = inspect_capability_manifest(root, manifest)
        if inspection.states != ((CAP, "installable"),):
            raise SystemExit("installed G17 inspection did not find installable fixture")
        record = install_local_capability(root, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id="capinstall_00000000-0000-4000-8000-000000000005")
        if parse_json_bytes(record)["outcome"] != "installed":
            raise SystemExit("installed G17 fixture did not install")
        if not validate_serialized_contract("capability-installation.schema.json", record).valid:
            raise SystemExit("installed G17 installation record failed schema validation")
        if inspect_capability_manifest(root, manifest).states != ((CAP, "available"),):
            raise SystemExit("installed G17 fixture did not become available")
        if (root / f"tools/{CAP}/artifact").read_bytes() != payload:
            raise SystemExit("installed G17 fixture bytes changed")
    print("verified installed GigAI G17 capability inspection and local installation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
