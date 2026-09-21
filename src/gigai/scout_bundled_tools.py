"""Closed preparation of bundled Scout CRUD capability source.

This module only describes the immutable package source that a candidate may
copy and later submit for explicit review and approval. It never executes
Python, marks a review passed, installs a capability, or publishes a receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from .canonical import canonical_json_bytes, digest_imported_bytes


SCOUT_CRUD_CAPABILITY_ID = "cap_00000000-0000-4000-8000-000000000071"
SCOUT_CRUD_MANIFEST_ID = "capmanifest_00000000-0000-4000-8000-000000000072"
SCOUT_CRUD_ENTRY_PATH = f"tools/{SCOUT_CRUD_CAPABILITY_ID}/record_tool.py"
SCOUT_CRUD_SCHEMA_PATH = f"tools/{SCOUT_CRUD_CAPABILITY_ID}/operation.schema.json"
_ROOT_WRAPPER = "gig.py"


def _member(path: str, data: bytes, media_type: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


def prepared_scout_crud_manifest(
    *, gig_id: str, goal_ids: Sequence[str], source: Mapping[str, bytes]
) -> dict[str, object]:
    """Build the schema-valid but deliberately unapproved Scout CRUD request."""

    required = {_ROOT_WRAPPER, SCOUT_CRUD_ENTRY_PATH, SCOUT_CRUD_SCHEMA_PATH}
    if not required.issubset(source):
        raise ValueError("bundled Scout CRUD source is incomplete")
    canonical_goals = sorted(set(goal_ids))
    if not canonical_goals:
        raise ValueError("bundled Scout CRUD capability requires compiled goal IDs")
    inventory = [
        _member(_ROOT_WRAPPER, source[_ROOT_WRAPPER], "text/x-python"),
        _member(SCOUT_CRUD_SCHEMA_PATH, source[SCOUT_CRUD_SCHEMA_PATH], "application/schema+json"),
        _member(SCOUT_CRUD_ENTRY_PATH, source[SCOUT_CRUD_ENTRY_PATH], "text/x-python"),
    ]
    inventory.sort(key=lambda item: str(item["path"]))
    wrapper_ref = next(item for item in inventory if item["path"] == _ROOT_WRAPPER)
    entry = next(item for item in inventory if item["path"] == SCOUT_CRUD_ENTRY_PATH)
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "1.0",
        "manifest_id": SCOUT_CRUD_MANIFEST_ID,
        "manifest_version": 1,
        "gig_id": gig_id,
        "created_at": created_at,
        "created_by": {
            "kind": "gigai",
            "id": "scout-bundled-tool-preparation",
            "model_target": None,
        },
        "capabilities": [
            {
                "capability_id": SCOUT_CRUD_CAPABILITY_ID,
                "goal_ids": canonical_goals,
                "kind": "tool",
                "name": "scout-native-records",
                "requested_version": "1.0",
                "source_constraints": {
                    "allowed_source_kinds": ["local_artifact"],
                    "required_digest": entry["content_sha256"],
                    "required_identity": "record_tool.py",
                },
                "declared_effects": ["write_workpad"],
                "permissions": {
                    "filesystem": "write_isolated",
                    "network": "none",
                    "credentials": "none",
                },
                "credential_requirements": [],
                "network_requirement": "none",
                "availability_state": "missing",
                "compatibility": {
                    "status": "unknown",
                    "reason": "Requires explicit local inspection and capability review.",
                },
                "security_review": {
                    "status": "pending",
                    "checks": [],
                    "reason": "Bundled source is prepared only; no security review or effect consent was recorded.",
                },
                "alternatives": [],
                "options": [
                    {
                        "option_id": "A",
                        "kind": "use_available",
                        "label": "Review and explicitly approve the pinned Scout CRUD source",
                        "ordinal": 0,
                        "decision": "pending",
                    }
                ],
                "tool_binding": {
                    "entry_path": SCOUT_CRUD_ENTRY_PATH,
                    "inventory": inventory,
                    "inventory_sha256": digest_imported_bytes(canonical_json_bytes(inventory)),
                    "operations": ["record_archive", "record_create", "record_update"],
                    "effects": ["write_workpad"],
                    "wrapper_ref": wrapper_ref,
                },
            }
        ],
    }


__all__ = [
    "SCOUT_CRUD_CAPABILITY_ID",
    "SCOUT_CRUD_ENTRY_PATH",
    "SCOUT_CRUD_MANIFEST_ID",
    "SCOUT_CRUD_SCHEMA_PATH",
    "prepared_scout_crud_manifest",
]
