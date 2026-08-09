from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from gigai.capabilities import (
    install_local_capability,
    inspect_capability_manifest,
    materialize_capability_manifest,
    validate_capability_bundle_link,
    validate_capability_manifest,
)
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


NOW = "2026-08-08T00:00:00Z"
GIG = "gig_00000000-0000-4000-8000-000000000001"
GOAL = "goal_00000000-0000-4000-8000-000000000002"
CAP = "cap_00000000-0000-4000-8000-000000000003"
MANIFEST = "capmanifest_00000000-0000-4000-8000-000000000004"
INSTALL = "capinstall_00000000-0000-4000-8000-000000000005"
ACTOR = {"kind": "operator", "id": "test-user", "model_target": None}


def _capability(*, digest: str | None, state: str = "missing", credentials: list[str] | None = None, filesystem: str = "write_isolated") -> dict[str, object]:
    return {
        "capability_id": CAP,
        "goal_ids": [GOAL],
        "kind": "local_capability",
        "name": "fixture-tool",
        "requested_version": "1.0.0",
        "source_constraints": {
            "allowed_source_kinds": ["local_artifact"],
            "required_digest": digest,
            "required_identity": f"{CAP}.artifact",
        },
        "declared_effects": ["read_local_metadata"],
        "permissions": {"filesystem": filesystem, "network": "none", "credentials": "none"},
        "credential_requirements": credentials or [],
        "network_requirement": "none",
        "availability_state": state,
        "compatibility": {"status": "compatible", "reason": None},
        "security_review": {"status": "passed", "checks": ["path_containment"], "reason": None},
        "alternatives": [],
        "options": [
            {"option_id": "A", "kind": "install_local", "label": "Install pinned local artifact", "ordinal": 0, "decision": "pending"},
            {"option_id": "B", "kind": "continue_without", "label": "Continue without capability", "ordinal": 1, "decision": "pending"},
        ],
    }


def _manifest(capability: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_id": MANIFEST,
        "manifest_version": 1,
        "gig_id": GIG,
        "created_at": NOW,
        "created_by": {"kind": "gigai", "id": "g17-test", "model_target": None},
        "capabilities": [capability],
    }


def _stage_source(root: Path, payload: bytes = b"fixture bytes\n") -> str:
    path = root / "tools/.sources/" / f"{CAP}.artifact"
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    return digest_imported_bytes(payload)


def test_g17_additive_schema_inventory_and_baseline_hashes() -> None:
    assert len(SCHEMA_NAMES) == 21
    root = Path(__file__).parents[1] / "src/gigai/schemas"
    manifest = root / "capability-manifest.schema.json"
    installation = root / "capability-installation.schema.json"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == "17844fd06a4a905ebcd12cff9994c86ad83dfd941e774ff539beb6e4429ec4cd"
    assert hashlib.sha256(installation.read_bytes()).hexdigest() == "c21641988e728cd94a8617a994ec1e3f5ffa9ae38ca020a2f7406916d6d083c0"


def test_manifest_materializes_and_inspects_installable_state(tmp_path: Path) -> None:
    digest = _stage_source(tmp_path)
    manifest = _manifest(_capability(digest=digest))
    payload = materialize_capability_manifest(tmp_path, manifest)
    assert validate_capability_manifest(payload).valid
    inspected = inspect_capability_manifest(tmp_path, payload)
    assert inspected.states == ((CAP, "installable"),)
    assert parse_json_bytes(inspected.manifest_bytes)["capabilities"][0]["availability_state"] == "installable"


def test_capability_manifest_is_linked_by_the_g15_bundle(tmp_path: Path) -> None:
    from tests.test_g15_review_substrate import _bundle

    digest = _stage_source(tmp_path)
    manifest = canonical_json_bytes(_manifest(_capability(digest=digest)))
    bundle, objects = _bundle()
    manifest_path = f"manifests/capabilities/{MANIFEST}.json"
    bundle["tool_requirements"] = {
        "path": manifest_path,
        "content_sha256": digest_imported_bytes(manifest),
        "media_type": "application/json",
        "size_bytes": len(manifest),
    }
    objects[manifest_path] = manifest
    from gigai.review import materialize_review_bundle

    bundle_bytes = materialize_review_bundle(tmp_path, bundle, objects)
    assert validate_capability_bundle_link(tmp_path, bundle_bytes).valid


def test_inspection_states_are_distinct_and_security_precedes_credentials(tmp_path: Path) -> None:
    source_digest = _stage_source(tmp_path)
    credential_manifest = canonical_json_bytes(_manifest(_capability(digest=source_digest, credentials=["api_key"])))
    assert inspect_capability_manifest(tmp_path, credential_manifest).states == ((CAP, "credential_missing"),)
    rejected = _capability(digest=source_digest, credentials=["api_key"], filesystem="none")
    rejected["security_review"] = {"status": "rejected", "checks": ["effect_allowlist"], "reason": "installer policy"}
    assert inspect_capability_manifest(tmp_path, canonical_json_bytes(_manifest(rejected))).states == ((CAP, "security_rejected"),)
    missing = canonical_json_bytes(_manifest(_capability(digest=source_digest)))
    (tmp_path / "tools/.sources" / f"{CAP}.artifact").unlink()
    assert inspect_capability_manifest(tmp_path, missing).states == ((CAP, "missing"),)


def test_incompatible_and_malformed_manifest_findings_are_deterministic() -> None:
    incompatible = _capability(digest=None)
    incompatible["compatibility"] = {"status": "incompatible", "reason": "unsupported platform"}
    report = validate_capability_manifest(canonical_json_bytes(_manifest(incompatible)))
    assert report.valid
    assert inspect_capability_manifest(Path("/tmp"), canonical_json_bytes(_manifest(incompatible))).states == ((CAP, "incompatible"),)
    duplicate = _manifest(_capability(digest=None))
    duplicate["capabilities"] = [duplicate["capabilities"][0], duplicate["capabilities"][0]]
    codes = {finding.code for finding in validate_capability_manifest(canonical_json_bytes(duplicate)).findings}
    assert "duplicate_capability_id" in codes
    invented = _capability(digest=None)
    invented["options"] = [{"option_id": "C", "kind": "choose_alternative", "label": "Invented", "ordinal": 0, "decision": "pending"}]
    codes = {finding.code for finding in validate_capability_manifest(canonical_json_bytes(_manifest(invented))).findings}
    assert "invented_alternative" in codes
    malformed = dict(_manifest(_capability(digest=None)))
    malformed["unexpected"] = True
    assert "schema_invalid" in {finding.code for finding in validate_capability_manifest(canonical_json_bytes(malformed)).findings}


def test_source_symlink_and_target_symlink_fail_closed(tmp_path: Path) -> None:
    digest = _stage_source(tmp_path)
    manifest = canonical_json_bytes(_manifest(_capability(digest=digest)))
    source = tmp_path / "tools/.sources" / f"{CAP}.artifact"
    source.unlink()
    source.symlink_to(tmp_path / "outside-artifact")
    (tmp_path / "outside-artifact").write_bytes(b"fixture bytes\n")
    assert inspect_capability_manifest(tmp_path, manifest).states == ((CAP, "security_rejected"),)
    try:
        install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id=INSTALL)
    except Exception as exc:
        assert getattr(exc, "code", None) == "unsafe_source_path"
    else:
        raise AssertionError("source symlink unexpectedly installed")
    source.unlink()
    source.write_bytes(b"fixture bytes\n")
    target = tmp_path / f"tools/{CAP}"
    target.symlink_to(tmp_path / "outside-tool", target_is_directory=False)
    try:
        install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id=INSTALL)
    except Exception as exc:
        assert getattr(exc, "code", None) == "unsafe_target_path"
    else:
        raise AssertionError("target symlink unexpectedly installed")


def test_per_gig_provenance_does_not_leak_between_roots(tmp_path: Path) -> None:
    first_root = tmp_path / "gig-a"
    second_root = tmp_path / "gig-b"
    digest = _stage_source(first_root)
    manifest = canonical_json_bytes(_manifest(_capability(digest=digest)))
    install_local_capability(first_root, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id=INSTALL)
    (second_root / "tools/.sources").mkdir(parents=True)
    (second_root / "tools/.sources" / f"{CAP}.artifact").write_bytes(b"fixture bytes\n")
    assert inspect_capability_manifest(second_root, manifest).states == ((CAP, "installable"),)
    assert not (second_root / f"tools/{CAP}").exists()


def test_capability_module_has_no_effectful_imports() -> None:
    tree = ast.parse(Path(__file__).parents[1].joinpath("src/gigai/capabilities.py").read_text())
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
    assert not imported.intersection({"socket", "subprocess", "httpx", "urllib", "requests"})


def test_adversarial_runtime_effects_remain_metadata_and_target_is_untouched(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "sentinel.txt").write_text("unchanged\n")
    digest = _stage_source(tmp_path)
    capability = _capability(digest=digest)
    capability["declared_effects"] = [
        "network_read",
        "credential_value_access",
        "execute_capability",
        "subprocess",
        "shell",
        "write_target",
        "global_installation",
    ]
    manifest = canonical_json_bytes(_manifest(capability))
    assert inspect_capability_manifest(tmp_path, manifest).states == ((CAP, "installable"),)
    install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id=INSTALL)
    assert (target / "sentinel.txt").read_text() == "unchanged\n"


def test_install_is_idempotent_and_records_exact_snapshots(tmp_path: Path) -> None:
    digest = _stage_source(tmp_path)
    manifest = canonical_json_bytes(_manifest(_capability(digest=digest)))
    first = install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id=INSTALL)
    first_record = parse_json_bytes(first)
    assert first_record["outcome"] == "installed"
    assert first_record["provenance"]["installed_root"] == f"tools/{CAP}"
    assert validate_serialized_contract("capability-installation.schema.json", first).valid
    assert (tmp_path / f"tools/{CAP}/artifact").read_bytes() == b"fixture bytes\n"
    repeat = install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id="capinstall_00000000-0000-4000-8000-000000000006")
    assert parse_json_bytes(repeat)["outcome"] == "already_available"


def test_refusal_and_interruption_rollback_are_durable(tmp_path: Path) -> None:
    digest = _stage_source(tmp_path)
    manifest = canonical_json_bytes(_manifest(_capability(digest=digest)))
    refused = install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id=INSTALL, approved=False)
    assert parse_json_bytes(refused)["outcome"] == "refused"
    failed = install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id="capinstall_00000000-0000-4000-8000-000000000006", failpoint="before_rename")
    assert parse_json_bytes(failed)["outcome"] == "failed"
    rolled = install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id="capinstall_00000000-0000-4000-8000-000000000007", failpoint="after_rename")
    record = parse_json_bytes(rolled)
    assert record["outcome"] == "rolled_back"
    assert record["rollback"]["restored_before"] is True
    assert not (tmp_path / f"tools/{CAP}").exists()


def test_digest_drift_fails_before_tool_root_write(tmp_path: Path) -> None:
    digest = _stage_source(tmp_path)
    manifest = canonical_json_bytes(_manifest(_capability(digest=digest)))
    (tmp_path / "tools/.sources" / f"{CAP}.artifact").write_bytes(b"drifted\n")
    try:
        install_local_capability(tmp_path, manifest, capability_id=CAP, option_id="A", approving_actor=ACTOR, now=NOW, installation_id=INSTALL)
    except Exception as exc:
        assert getattr(exc, "code", None) == "source_digest_mismatch"
        assert "source_digest_mismatch" in str(exc)
    else:
        raise AssertionError("digest drift unexpectedly installed")
    assert not (tmp_path / f"tools/{CAP}").exists()
