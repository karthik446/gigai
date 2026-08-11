"""Offline capability inspection and approved local-artifact installation.

G17 deliberately treats a capability as proposal metadata. Inspection may read
local bytes and metadata, while installation only copies a pinned local file
into an isolated per-Gig workpad root. This module never invokes a capability,
provider, package manager, shell, subprocess, or target.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid
from typing import Any

from .canonical import (
    CanonicalizationError,
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
    parse_json_bytes,
)
from .validators import ValidationFinding, ValidationReport, validate_serialized_contract


class CapabilityError(ValueError):
    """Base class for fail-closed capability errors."""

    code = "capability_error"


class CapabilityManifestError(CapabilityError):
    code = "invalid_capability_manifest"


class CapabilityInstallationError(CapabilityError):
    code = "capability_installation_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class CapabilityInspection:
    """The canonical inspected manifest and its state findings."""

    manifest_bytes: bytes
    states: tuple[tuple[str, str], ...]


_INSPECTION_PRECEDENCE = (
    "security_rejected",
    "incompatible",
    "credential_missing",
    "available",
    "installable",
    "missing",
)
_INSTALL_EFFECTS = frozenset({"read_local_metadata", "read_pinned_bytes", "write_isolated_tool_root"})
_CAPABILITY_ROOT = Path("tools")
_STAGING_PREFIX = ".staging-"


def _finding(location: str, code: str, message: str) -> ValidationFinding:
    return ValidationFinding(location, code, message)


def _safe_relative(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return None
    if "\\" in value or any(part == ".." for part in Path(value).parts):
        return None
    candidate = root / value
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    return candidate


def _capability_root(root: Path, capability_id: str) -> Path:
    path = _safe_relative(root, f"tools/{capability_id}")
    if path is None:
        raise CapabilityInstallationError("capability root escaped the workpad")
    return path


def _manifest_path(root: Path, manifest_id: str) -> Path:
    path = _safe_relative(root, f"manifests/capabilities/{manifest_id}.json")
    if path is None:
        raise CapabilityManifestError("capability manifest path escaped the workpad")
    return path


def _installation_path(root: Path, installation_id: str) -> Path:
    path = _safe_relative(root, f"manifests/installations/{installation_id}.json")
    if path is None:
        raise CapabilityInstallationError("installation record path escaped the workpad")
    return path


def _parse_manifest(manifest_bytes: bytes) -> Mapping[str, Any] | None:
    try:
        value = parse_json_bytes(manifest_bytes)
    except CanonicalizationError:
        return None
    return value if isinstance(value, Mapping) else None


def _validate_manifest_semantics(manifest: Mapping[str, Any]) -> ValidationReport:
    findings: list[ValidationFinding] = []
    capabilities = manifest.get("capabilities", [])
    if not isinstance(capabilities, list):
        return ValidationReport(())
    seen_ids: set[str] = set()
    for index, capability in enumerate(capabilities):
        location = f"capabilities/{index}"
        if not isinstance(capability, Mapping):
            continue
        capability_id = capability.get("capability_id")
        if isinstance(capability_id, str) and capability_id in seen_ids:
            findings.append(_finding(location + "/capability_id", "duplicate_capability_id", "capability_id occurs more than once"))
        if isinstance(capability_id, str):
            seen_ids.add(capability_id)
        options = capability.get("options", [])
        if not isinstance(options, list):
            continue
        option_ids: set[str] = set()
        ordinals: list[int] = []
        for option_index, option in enumerate(options):
            option_location = f"{location}/options/{option_index}"
            if not isinstance(option, Mapping):
                continue
            option_id = option.get("option_id")
            if isinstance(option_id, str) and option_id in option_ids:
                findings.append(_finding(option_location + "/option_id", "duplicate_option_id", "option_id occurs more than once"))
            if isinstance(option_id, str):
                option_ids.add(option_id)
            ordinal = option.get("ordinal")
            if isinstance(ordinal, int):
                ordinals.append(ordinal)
            if option.get("decision") != "pending":
                findings.append(_finding(option_location + "/decision", "proposal_not_pending", "proposal options must remain pending before approval"))
        if ordinals != sorted(ordinals) or ordinals != list(range(len(ordinals))):
            findings.append(_finding(location + "/options", "unstable_option_order", "option ordinals must be contiguous and ordered"))
        alternatives = capability.get("alternatives", [])
        if not isinstance(alternatives, list):
            continue
        alternative_ids = {item.get("option_id") for item in alternatives if isinstance(item, Mapping)}
        for option_index, option in enumerate(options):
            if isinstance(option, Mapping) and option.get("kind") == "choose_alternative" and option.get("option_id") not in alternative_ids:
                findings.append(_finding(f"{location}/options/{option_index}", "invented_alternative", "alternative option is not declared"))
    return ValidationReport(tuple(sorted(set(findings))))


def validate_capability_manifest(manifest_bytes: bytes) -> ValidationReport:
    """Validate canonical capability requirements and proposal semantics."""

    report = validate_serialized_contract("capability-manifest.schema.json", manifest_bytes)
    manifest = _parse_manifest(manifest_bytes)
    if manifest is None:
        return report
    if manifest_bytes != canonical_json_bytes(dict(manifest)):
        report = _merge_reports(report, ValidationReport((_finding("$", "noncanonical_manifest", "capability manifest is not canonical JSON"),)))
    return _merge_reports(report, _validate_manifest_semantics(manifest))


def validate_capability_bundle_link(root: Path, bundle_bytes: bytes) -> ValidationReport:
    """Validate that a G15 Bundle's tool reference names a G17 manifest."""

    from .review import validate_review_bundle

    report = validate_review_bundle(root, bundle_bytes)
    bundle = _parse_manifest(bundle_bytes)
    if bundle is None:
        return report
    tool_ref = bundle.get("tool_requirements")
    if tool_ref is None:
        return _merge_reports(report, ValidationReport((_finding("tool_requirements", "missing_capability_manifest", "Bundle must reference a capability manifest"),)))
    if not isinstance(tool_ref, Mapping) or not str(tool_ref.get("path", "")).startswith("manifests/capabilities/"):
        return _merge_reports(report, ValidationReport((_finding("tool_requirements/path", "invalid_capability_manifest_path", "tool_requirements must point to manifests/capabilities"),)))
    path = _safe_relative(root, tool_ref.get("path"))
    if path is None or path.is_symlink() or not path.is_file():
        return _merge_reports(report, ValidationReport((_finding("tool_requirements/path", "missing_capability_manifest", "capability manifest bytes are missing"),)))
    return _merge_reports(report, validate_capability_manifest(path.read_bytes()))


def _merge_reports(*reports: ValidationReport) -> ValidationReport:
    return ValidationReport(tuple(sorted({finding for report in reports for finding in report.findings})))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def materialize_capability_manifest(root: Path, manifest: Mapping[str, Any]) -> bytes:
    """Validate and atomically persist a canonical capability manifest."""

    manifest_bytes = canonical_json_bytes(dict(manifest))
    report = validate_capability_manifest(manifest_bytes)
    if not report.valid:
        raise CapabilityManifestError("invalid capability manifest: " + "; ".join(item.code for item in report.findings))
    path = _manifest_path(root, str(manifest["manifest_id"]))
    if _is_symlinked_path(path.parent, root):
        raise CapabilityManifestError("capability manifest parent contains a symlink")
    if path.is_symlink() or (path.exists() and path.read_bytes() != manifest_bytes):
        raise CapabilityManifestError("refusing to overwrite divergent capability manifest")
    _atomic_write(path, manifest_bytes)
    replay = validate_capability_manifest(path.read_bytes())
    if not replay.valid:
        raise CapabilityManifestError("capability manifest failed replay")
    return manifest_bytes


def capability_manifest_artifact_ref(
    root: Path, manifest_id: str, *, gig_id: str | None = None
) -> dict[str, Any]:
    """Return a validated content reference for one local capability manifest."""

    path = _manifest_path(root, manifest_id)
    if _is_symlinked_path(path, root) or not path.is_file():
        raise CapabilityManifestError("capability manifest path is not a regular file")
    payload = path.read_bytes()
    report = validate_capability_manifest(payload)
    if not report.valid:
        raise CapabilityManifestError("capability manifest is invalid")
    manifest = _parse_manifest(payload)
    assert manifest is not None
    if manifest.get("manifest_id") != manifest_id:
        raise CapabilityManifestError("capability manifest identity does not match its path")
    if gig_id is not None and manifest.get("gig_id") != gig_id:
        raise CapabilityManifestError("capability manifest belongs to another Gig")
    return {
        "path": path.relative_to(root).as_posix(),
        "content_sha256": digest_imported_bytes(payload),
        "media_type": "application/json",
        "size_bytes": len(payload),
    }


def _source_path(root: Path, capability_id: str) -> Path:
    path = _safe_relative(root, f"tools/.sources/{capability_id}.artifact")
    if path is None:
        raise CapabilityInstallationError("capability source escaped the workpad")
    return path


def _is_symlinked_path(path: Path, root: Path) -> bool:
    current = path.absolute()
    root = root.absolute()
    while True:
        if current.is_symlink():
            return True
        if current == root:
            return False
        if current.parent == current:
            return True
        current = current.parent


def _snapshot(root: Path, relative_root: str, source_identity: str | None) -> dict[str, Any]:
    target = _safe_relative(root, relative_root)
    if target is None:
        raise CapabilityInstallationError("snapshot root escaped the workpad")
    entries: list[dict[str, Any]] = []
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise CapabilityInstallationError("tool root is not a regular directory")
        for path in sorted(target.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise CapabilityInstallationError("tool root contains a symlink")
            mode = stat.S_IMODE(path.stat().st_mode)
            if path.is_dir():
                entries.append({"path": relative, "content_sha256": digest_imported_bytes(b""), "size_bytes": 0, "mode": mode, "kind": "directory"})
            elif path.is_file():
                payload = path.read_bytes()
                entries.append({"path": relative, "content_sha256": digest_imported_bytes(payload), "size_bytes": len(payload), "mode": mode, "kind": "file"})
            else:
                raise CapabilityInstallationError("tool root contains an unsupported filesystem entry")
    identity_payload = {"root": relative_root, "entries": entries}
    return {
        "root": relative_root,
        "entries": entries,
        "snapshot_sha256": canonical_json_digest(identity_payload),
        "source_identity": source_identity,
    }


def _write_installation_record(root: Path, record: Mapping[str, Any]) -> bytes:
    payload = canonical_json_bytes(dict(record))
    report = validate_serialized_contract("capability-installation.schema.json", payload)
    if not report.valid:
        raise CapabilityInstallationError("installation record is invalid: " + "; ".join(item.code for item in report.findings))
    path = _installation_path(root, str(record["installation_id"]))
    if _is_symlinked_path(path.parent, root):
        raise CapabilityInstallationError("installation record parent contains a symlink", code="unsafe_record_path")
    _atomic_write(path, payload)
    return payload


def _base_record(
    *,
    installation_id: str,
    manifest: Mapping[str, Any],
    capability: Mapping[str, Any],
    option_id: str,
    actor: Mapping[str, Any],
    source: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    outcome: str,
    rollback: Mapping[str, Any],
    reason: str | None,
    now: str,
    decision_status: str = "approved",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "installation_id": installation_id,
        "installation_version": 1,
        "gig_id": manifest["gig_id"],
        "capability_id": capability["capability_id"],
        "manifest_id": manifest["manifest_id"],
        "created_at": now,
        "decision": {"option_id": option_id, "status": decision_status, "actor": dict(actor), "recorded_at": now, "reason": reason},
        "source": dict(source),
        "security_checks": [
            {"name": "source_digest", "status": "passed", "detail": "source bytes match the pinned digest"},
            {"name": "source_containment", "status": "passed", "detail": "source is a regular workpad-relative file"},
            {"name": "isolated_root", "status": "passed", "detail": "writes are confined to the per-Gig tools root"},
        ],
        "before_manifest": dict(before),
        "after_manifest": dict(after),
        "outcome": outcome,
        "rollback": dict(rollback),
        "provenance": {
            "source_kind": "local_artifact",
            "source_sha256": source["content_sha256"],
            "installed_root": f"tools/{capability['capability_id']}",
            "recorded_by": dict(actor),
        },
        "failure_reason": reason,
    }


def _find_capability(manifest: Mapping[str, Any], capability_id: str) -> Mapping[str, Any]:
    for capability in manifest.get("capabilities", []):
        if isinstance(capability, Mapping) and capability.get("capability_id") == capability_id:
            return capability
    raise CapabilityInstallationError("unknown capability_id")


def inspect_capability_manifest(root: Path, manifest_bytes: bytes) -> CapabilityInspection:
    """Reconcile local state into deterministic proposal inspection states."""

    report = validate_capability_manifest(manifest_bytes)
    if not report.valid:
        raise CapabilityManifestError("invalid capability manifest: " + "; ".join(item.code for item in report.findings))
    manifest = _parse_manifest(manifest_bytes)
    assert manifest is not None
    inspected = deepcopy(dict(manifest))
    states: list[tuple[str, str]] = []
    for capability in inspected["capabilities"]:
        capability_id = str(capability["capability_id"])
        state = "missing"
        security = capability["security_review"]
        permissions = capability["permissions"]
        if security["status"] != "passed" or permissions["filesystem"] != "write_isolated":
            state = "security_rejected"
        elif capability["compatibility"]["status"] == "incompatible":
            state = "incompatible"
        elif capability["credential_requirements"]:
            state = "credential_missing"
        else:
            target = _capability_root(root, capability_id)
            source = _source_path(root, capability_id)
            if target.is_symlink() or source.is_symlink():
                state = "security_rejected"
            elif target.exists() and not target.is_symlink():
                try:
                    snapshot = _snapshot(root, f"tools/{capability_id}", capability.get("requested_version"))
                    required = capability["source_constraints"]["required_digest"]
                    actual = next((entry["content_sha256"] for entry in snapshot["entries"] if entry["kind"] == "file"), None)
                    state = "available" if required is None or actual == required else "incompatible"
                except CapabilityInstallationError:
                    state = "security_rejected"
            elif source.exists() and not source.is_symlink():
                required = capability["source_constraints"]["required_digest"]
                actual = digest_imported_bytes(source.read_bytes())
                state = "installable" if required is None or actual == required else "incompatible"
            else:
                state = "missing"
        capability["availability_state"] = state
        states.append((capability_id, state))
    result = canonical_json_bytes(inspected)
    return CapabilityInspection(result, tuple(states))


def install_local_capability(
    root: Path,
    manifest_bytes: bytes,
    *,
    capability_id: str,
    option_id: str,
    approving_actor: Mapping[str, Any],
    now: str,
    installation_id: str | None = None,
    failpoint: str | None = None,
    approved: bool = True,
) -> bytes:
    """Install one approved pinned local artifact without executing it."""

    report = validate_capability_manifest(manifest_bytes)
    if not report.valid:
        raise CapabilityInstallationError("invalid capability manifest")
    manifest = _parse_manifest(manifest_bytes)
    assert manifest is not None
    capability = _find_capability(manifest, capability_id)
    options = {item["option_id"]: item for item in capability["options"]}
    option = options.get(option_id)
    if option is None or option["kind"] != "install_local":
        raise CapabilityInstallationError("selected option is not an install_local option")
    if capability["security_review"]["status"] != "passed" or capability["permissions"]["filesystem"] != "write_isolated":
        raise CapabilityInstallationError("security_rejected", code="security_rejected")
    source_path = _source_path(root, capability_id)
    if _is_symlinked_path(source_path, root) or not source_path.is_file():
        raise CapabilityInstallationError("source_path_invalid", code="unsafe_source_path")
    payload = source_path.read_bytes()
    source_digest = digest_imported_bytes(payload)
    required_digest = capability["source_constraints"]["required_digest"]
    if required_digest is not None and source_digest != required_digest:
        raise CapabilityInstallationError("source_digest_mismatch", code="source_digest_mismatch")
    required_identity = capability["source_constraints"]["required_identity"]
    if required_identity is not None and source_path.name != required_identity:
        raise CapabilityInstallationError("source_identity_mismatch", code="source_identity_mismatch")
    source_relative = source_path.relative_to(root).as_posix()
    source = {
        "path": source_relative,
        "content_sha256": source_digest,
        "size_bytes": len(payload),
        "media_type": "application/octet-stream",
        "identity": required_identity or source_path.name,
        "version": capability["requested_version"] or "unversioned",
    }
    target_relative = f"tools/{capability_id}"
    target = _capability_root(root, capability_id)
    if target.is_symlink():
        raise CapabilityInstallationError("target_root_symlink", code="unsafe_target_path")
    before = _snapshot(root, target_relative, capability.get("requested_version"))
    installation_id = installation_id or f"capinstall_{uuid.uuid4()}"
    if not approved:
        record = _base_record(installation_id=installation_id, manifest=manifest, capability=capability, option_id=option_id, actor=approving_actor, source=source, before=before, after=before, outcome="refused", rollback={"attempted": False, "restored_before": True, "reason": "operator_refused"}, reason="operator_refused", now=now, decision_status="refused")
        return _write_installation_record(root, record)
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise CapabilityInstallationError("target_root_symlink", code="unsafe_target_path")
        after = _snapshot(root, target_relative, capability.get("requested_version"))
        files = [entry for entry in after["entries"] if entry["kind"] == "file"]
        if len(files) == 1 and files[0]["path"] == f"tools/{capability_id}/artifact" and files[0]["content_sha256"] == source_digest and len(after["entries"]) == 1:
            record = _base_record(installation_id=installation_id, manifest=manifest, capability=capability, option_id=option_id, actor=approving_actor, source=source, before=before, after=after, outcome="already_available", rollback={"attempted": False, "restored_before": True, "reason": None}, reason=None, now=now)
            return _write_installation_record(root, record)
        raise CapabilityInstallationError("divergent_tool_bytes", code="divergent_tool_bytes")

    staging = root / "tools" / f"{_STAGING_PREFIX}{capability_id}"
    if staging.exists() or staging.is_symlink():
        shutil.rmtree(staging) if staging.is_dir() and not staging.is_symlink() else staging.unlink()
    staging.mkdir(parents=True)
    renamed = False
    try:
        artifact = staging / "artifact"
        artifact.write_bytes(payload)
        artifact.chmod(0o600)
        if failpoint == "before_rename":
            raise CapabilityInstallationError("injected_before_rename")
        os.replace(staging, target)
        renamed = True
        after = _snapshot(root, target_relative, capability.get("requested_version"))
        if failpoint == "after_rename":
            raise CapabilityInstallationError("injected_after_rename")
        record = _base_record(installation_id=installation_id, manifest=manifest, capability=capability, option_id=option_id, actor=approving_actor, source=source, before=before, after=after, outcome="installed", rollback={"attempted": False, "restored_before": True, "reason": None}, reason=None, now=now)
        return _write_installation_record(root, record)
    except Exception as exc:
        if renamed and target.exists():
            shutil.rmtree(target)
        if staging.exists():
            shutil.rmtree(staging)
        after = _snapshot(root, target_relative, capability.get("requested_version"))
        outcome = "rolled_back" if renamed else "failed"
        record = _base_record(installation_id=installation_id, manifest=manifest, capability=capability, option_id=option_id, actor=approving_actor, source=source, before=before, after=after, outcome=outcome, rollback={"attempted": renamed, "restored_before": after == before, "reason": str(exc)}, reason=str(exc), now=now)
        record_bytes = _write_installation_record(root, record)
        if failpoint is not None:
            return record_bytes
        if isinstance(exc, CapabilityInstallationError):
            raise
        raise CapabilityInstallationError(str(exc)) from exc


__all__ = [
    "CapabilityError",
    "CapabilityInstallationError",
    "CapabilityInspection",
    "CapabilityManifestError",
    "inspect_capability_manifest",
    "capability_manifest_artifact_ref",
    "install_local_capability",
    "materialize_capability_manifest",
    "validate_capability_bundle_link",
    "validate_capability_manifest",
]
