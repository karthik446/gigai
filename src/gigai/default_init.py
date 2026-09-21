"""Explicit, recoverable provisioning of release-eligible default instances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence

from .canonical import EntityPrefix, canonical_json_bytes, canonical_json_digest, digest_imported_bytes, generate_entity_id, parse_json_bytes, validate_entity_id
from .catalog import CatalogEntry, catalog_entries
from .journal import (
    JournalArtifact,
    JournalArtifactMissingError,
    JournalConflictError,
    JournalReconciliationRequired,
    JournalUnbornError,
    JournalWriter,
    read_committed_artifact,
    record_transition,
    run_with_journal_writer,
)
from .package import PackageInitResult, initialize_project_package
from .private_records import PrivateRecordError, migrate_workpad_layout
from .scout_materialization import (
    ScoutMaterializationError,
    is_scout_candidate,
    materialize_scout_candidate,
    repair_prepared_scout_capability_manifest,
)
from .validators import validate_serialized_contract
from .project_binding import MissingProjectBindingError, ProjectBindingError, load_project_binding
from .lifecycle import allocate_gig_id
from .registry import (
    RegistryError,
    RegistryMigrationRequired,
    ProjectRegistry,
    TemplateInstanceRecord,
    WorkspaceOwnerRecord,
    open_project_registry,
)
from .target_binding import TargetInitLock, resolve_target
from .workpad import provision_workpad


class DefaultInitError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DefaultInstanceResult:
    template_id: str
    gig_id: str
    status: str
    next_action: str
    proposal_id: str | None = None
    approval_state: str = "unavailable"


@dataclass(frozen=True)
class DefaultInitResult:
    package: PackageInitResult
    owner_id: str
    username: str
    instances: tuple[DefaultInstanceResult, ...]
    scout_status: str
    setup_steps: tuple[str, ...]


@dataclass(frozen=True)
class _BindingAuthority:
    template_id: str
    gig_id: str
    package_id: str
    package_digest: str
    binding_bytes: bytes
    binding_commit: str
    workpad: Path
    proposal_id: str | None = None
    approval_state: str | None = None
    source_digest: str | None = None


def normalize_username(value: str | None) -> str:
    if value is None:
        raise DefaultInitError("username_required", "--username is required for non-interactive initialization")
    normalized = value.strip()
    if not 1 <= len(normalized) <= 64 or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise DefaultInitError("username_invalid", "username must contain 1-64 trimmed Unicode code points without controls")
    return normalized


def default_inventory() -> tuple[CatalogEntry, ...]:
    """The current release-eligible inventory; Scout remains authoring-only."""
    return catalog_entries()


def _intent_path(target_root: Path) -> Path:
    return target_root / ".gigai" / "local" / "default-init.json"


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _owner_id(factory: Callable[[], uuid.UUID]) -> str:
    value = factory()
    if type(value) is not uuid.UUID or value.version != 4:
        raise DefaultInitError("owner_id_invalid", "owner ID factory must return UUIDv4")
    return f"owner_{value}"


def _saved_username(home_root: Path, target_root: Path) -> str | None:
    """Read an already-bound owner without creating package or registry state."""
    try:
        binding = load_project_binding(target_root)
    except MissingProjectBindingError:
        return None
    except ProjectBindingError as exc:
        raise DefaultInitError("workspace_owner_invalid", str(exc)) from exc
    try:
        registry, _created = open_project_registry(home_root, create=False)
        with registry.transaction() as transaction:
            owner = transaction.find_workspace_owner(binding.project_id)
    except RegistryMigrationRequired:
        # A legacy registry cannot contain the v3 owner row.  Validate a
        # supplied username before init is allowed to perform that migration.
        return None
    except RegistryError as exc:
        raise DefaultInitError("workspace_owner_invalid", str(exc)) from exc
    return owner.username if owner is not None else None


def _binding_from_snapshot(
    *, workpad: Path, project_id: str, gig_id: str
) -> tuple[dict[str, object], str] | None:
    path = "manifests/template-instance-binding.json"

    def snapshot(writer: JournalWriter) -> tuple[bytes, str]:
        return read_committed_artifact(
            workpad=writer.root,
            project_id=project_id,
            gig_id=gig_id,
            path=path,
        )

    try:
        captured = run_with_journal_writer(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            operation=snapshot,
        )
    except JournalArtifactMissingError:
        return None
    except JournalUnbornError:
        return None
    except (JournalConflictError, JournalReconciliationRequired) as exc:
        raise DefaultInitError("template_reconciliation_required", str(exc)) from exc
    artifact, commit = captured
    candidate = workpad / path
    if candidate.is_symlink() or not candidate.is_file() or candidate.read_bytes() != artifact:
        raise DefaultInitError(
            "template_reconciliation_required",
            "committed template binding differs from its working copy",
        )
    try:
        payload = parse_json_bytes(artifact)
    except ValueError as exc:
        raise DefaultInitError("template_reconciliation_required", "committed template binding is malformed") from exc
    if not isinstance(payload, dict):
        raise DefaultInitError("template_reconciliation_required", "committed template binding is not an object")
    return payload, commit


def _validate_registered_scout_binding(binding_bytes: bytes) -> None:
    report = validate_serialized_contract(
        "template-instance-binding.schema.json", binding_bytes
    )
    if report.valid:
        return
    details = "; ".join(
        f"{finding.location}:{finding.code}" for finding in report.findings
    )
    raise DefaultInitError(
        "template_reconciliation_required",
        "Scout template-instance binding failed registered schema validation"
        + (f": {details}" if details else ""),
    )


def _owner_payload(owner: WorkspaceOwnerRecord) -> dict[str, str]:
    return {
        "project_id": owner.project_id,
        "owner_id": owner.owner_id,
        "username": owner.username,
    }


def _binding_authority(
    *, workpad: Path, project_id: str, owner: WorkspaceOwnerRecord, gig_id: str
) -> _BindingAuthority | None:
    """Authenticate one historical default binding without an incoming template."""

    binding = _binding_from_snapshot(
        workpad=workpad, project_id=project_id, gig_id=gig_id
    )
    if binding is None:
        return None
    payload, commit = binding
    if payload.get("schema_version") == "2.0":
        _validate_registered_scout_binding(canonical_json_bytes(payload))
        required_candidate = {
            "schema_version", "kind", "project_id", "gig_id", "owner_id",
            "username", "template_id", "instance_name", "package_id",
            "package_digest", "inventory_digest", "software_inventory",
            "source_digest", "proposal_id", "proposal_ref", "approval",
            "customization_parent",
        }
        approval = payload.get("approval")
        proposal_id = payload.get("proposal_id")
        source_digest = payload.get("source_digest")
        if (
            set(payload) != required_candidate
            or any(
                payload.get(field) != expected
                for field, expected in {
                    "kind": "template_instance_binding",
                    "project_id": project_id,
                    "gig_id": gig_id,
                    "owner_id": owner.owner_id,
                    "username": owner.username,
                    "template_id": "scout",
                    "instance_name": "default",
                    "customization_parent": None,
                }.items()
            )
            or approval != {"state": "unapproved", "approved_version": None}
            or not isinstance(proposal_id, str)
            or not isinstance(source_digest, str)
            or not isinstance(payload.get("software_inventory"), dict)
            or not isinstance(payload.get("proposal_ref"), dict)
        ):
            raise DefaultInitError(
                "template_reconciliation_required",
                "committed Scout binding has incompatible identity or unapproved proposal state",
            )
        try:
            validate_entity_id(proposal_id, expected_prefix=EntityPrefix.GIG_PROPOSAL)
        except Exception as exc:
            raise DefaultInitError(
                "template_reconciliation_required",
                "committed Scout binding has an invalid proposal identity",
            ) from exc
        package_id = payload.get("package_id")
        package_digest = payload.get("package_digest")
        inventory_digest = payload.get("inventory_digest")
        if not all(
            isinstance(value, str)
            and len(value) == 71
            and value.startswith("sha256:")
            and all(character in "0123456789abcdef" for character in value[7:])
            for value in (package_digest, inventory_digest, source_digest)
        ) or not isinstance(package_id, str):
            raise DefaultInitError(
                "template_reconciliation_required",
                "committed Scout binding has invalid pinned digests",
            )
        return _BindingAuthority(
            template_id="scout",
            gig_id=gig_id,
            package_id=package_id,
            package_digest=package_digest,
            binding_bytes=canonical_json_bytes(payload),
            binding_commit=commit,
            workpad=workpad,
            proposal_id=proposal_id,
            approval_state="unapproved",
            source_digest=source_digest,
        )
    required = {
        "schema_version",
        "kind",
        "project_id",
        "gig_id",
        "owner_id",
        "username",
        "template_id",
        "instance_name",
        "package_id",
        "package_digest",
        "inventory_digest",
        "approval",
    }
    if set(payload) != required or any(
        payload.get(field) != expected
        for field, expected in {
            "schema_version": "1.0",
            "kind": "template_instance_binding",
            "project_id": project_id,
            "gig_id": gig_id,
            "owner_id": owner.owner_id,
            "username": owner.username,
            "instance_name": "default",
            "approval": None,
        }.items()
    ):
        raise DefaultInitError(
            "template_reconciliation_required",
            "committed template binding has incompatible identity or owner scope",
        )
    template_id = payload.get("template_id")
    package_id = payload.get("package_id")
    package_digest = payload.get("package_digest")
    inventory_digest = payload.get("inventory_digest")
    if not all(
        isinstance(value, str) and value
        for value in (template_id, package_id, package_digest, inventory_digest)
    ) or not (
        isinstance(package_digest, str)
        and isinstance(inventory_digest, str)
        and all(
            len(value) == 71
            and value.startswith("sha256:")
            and all(character in "0123456789abcdef" for character in value[7:])
            for value in (package_digest, inventory_digest)
        )
    ):
        raise DefaultInitError(
            "template_reconciliation_required",
            "committed template binding has invalid historical package provenance",
        )
    return _BindingAuthority(
        template_id=template_id,
        gig_id=gig_id,
        package_id=package_id,
        package_digest=package_digest,
        binding_bytes=canonical_json_bytes(payload),
        binding_commit=commit,
        workpad=workpad,
    )


def _has_committed_active_version(*, workpad: Path, project_id: str, gig_id: str) -> bool:
    """Check only for an existing authoritative active pointer; never write it."""

    try:
        read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path="manifests/active-gig-version.json",
        )
    except JournalArtifactMissingError:
        return False
    except (JournalConflictError, JournalReconciliationRequired) as exc:
        raise DefaultInitError("template_reconciliation_required", str(exc)) from exc
    return True


def _validate_cached_binding(
    *, cached: TemplateInstanceRecord, authority: _BindingAuthority, project_id: str
) -> None:
    """Treat the registry row strictly as a cache of its historical binding."""

    if (
        cached.project_id != project_id
        or cached.template_id != authority.template_id
        or cached.gig_id != authority.gig_id
        or cached.package_id != authority.package_id
        or cached.original_package_digest != authority.package_digest
        or cached.binding_artifact_ref != "manifests/template-instance-binding.json"
        or cached.binding_sha256 != digest_imported_bytes(authority.binding_bytes)
        or cached.journal_commit != authority.binding_commit
    ):
        raise DefaultInitError(
            "template_reconciliation_required",
            "template instance cache differs from its committed binding authority",
        )


def _authoritative_bindings(
    *, registry: ProjectRegistry, project_id: str, owner: WorkspaceOwnerRecord
) -> dict[str, _BindingAuthority]:
    """Discover default bindings from every validated project workpad locator."""

    records = registry.workpad_records()
    found: dict[str, _BindingAuthority] = {}
    for record in records:
        if record.project_id != project_id:
            continue
        authority = _binding_authority(
            workpad=Path(record.workpad_locator),
            project_id=project_id,
            owner=owner,
            gig_id=record.gig_id,
        )
        if authority is None:
            continue
        existing = found.get(authority.template_id)
        if existing is not None:
            raise DefaultInitError(
                "template_reconciliation_required",
                "multiple committed default bindings claim the same template",
            )
        found[authority.template_id] = authority
    return found


def _cache_record(project_id: str, authority: _BindingAuthority) -> TemplateInstanceRecord:
    return TemplateInstanceRecord(
        project_id,
        authority.template_id,
        "default",
        authority.gig_id,
        authority.package_id,
        authority.package_digest,
        "manifests/template-instance-binding.json",
        digest_imported_bytes(authority.binding_bytes),
        authority.binding_commit,
    )


def _validate_intent(
    *, intent: Mapping[str, object], owner: WorkspaceOwnerRecord, project_id: str
) -> None:
    """Refuse malformed or repurposed durable batch state before publication."""

    required = {
        "schema_version",
        "status",
        "owner_id",
        "project_id",
        "username",
        "owner_row_sha256",
        "inventory",
        "inventory_digest",
        "reserved_gig_ids",
    }
    if set(intent) != required or intent.get("schema_version") != "1.0":
        raise DefaultInitError(
            "template_reconciliation_required", "default initialization intent has invalid shape"
        )
    if intent.get("status") not in {"pending", "bound"}:
        raise DefaultInitError(
            "template_reconciliation_required", "default initialization intent has invalid status"
        )
    if (
        intent.get("project_id") != project_id
        or intent.get("owner_id") != owner.owner_id
        or intent.get("username") != owner.username
        or intent.get("owner_row_sha256") != canonical_json_digest(_owner_payload(owner))
    ):
        raise DefaultInitError(
            "workspace_owner_conflict", "default initialization intent conflicts with the pinned workspace owner"
        )
    inventory = intent.get("inventory")
    reserved = intent.get("reserved_gig_ids")
    if not isinstance(inventory, list) or not isinstance(reserved, list):
        raise DefaultInitError(
            "template_reconciliation_required", "default initialization intent has invalid inventory reservations"
        )
    if intent.get("inventory_digest") != canonical_json_digest(inventory):
        raise DefaultInitError(
            "template_reconciliation_required", "default initialization intent inventory digest differs"
        )
    template_ids: set[str] = set()
    for row in inventory:
        if not isinstance(row, dict) or set(row) != {"template_id", "package_id", "digest"}:
            raise DefaultInitError(
                "template_reconciliation_required", "default initialization intent inventory row is invalid"
            )
        if not all(isinstance(row.get(field), str) and row[field] for field in row):
            raise DefaultInitError(
                "template_reconciliation_required", "default initialization intent inventory row is invalid"
            )
        template_id = row["template_id"]
        if template_id in template_ids:
            raise DefaultInitError(
                "template_reconciliation_required", "default initialization intent repeats a template"
            )
        template_ids.add(template_id)
    reserved_ids: set[str] = set()
    reserved_templates: set[str] = set()
    for row in reserved:
        if not isinstance(row, dict) or set(row) != {"template_id", "gig_id"}:
            raise DefaultInitError(
                "template_reconciliation_required", "default initialization intent reservation is invalid"
            )
        template_id = row.get("template_id")
        gig_id = row.get("gig_id")
        if not isinstance(template_id, str) or not isinstance(gig_id, str):
            raise DefaultInitError(
                "template_reconciliation_required", "default initialization intent reservation is invalid"
            )
        try:
            validate_entity_id(gig_id, expected_prefix=EntityPrefix.GIG)
        except Exception as exc:
            raise DefaultInitError(
                "template_reconciliation_required", "default initialization intent reservation has invalid Gig ID"
            ) from exc
        if template_id not in template_ids or template_id in reserved_templates or gig_id in reserved_ids:
            raise DefaultInitError(
                "template_reconciliation_required", "default initialization intent reservations are ambiguous"
            )
        reserved_templates.add(template_id)
        reserved_ids.add(gig_id)
    if reserved_templates != template_ids:
        raise DefaultInitError(
            "template_reconciliation_required", "default initialization intent does not reserve every template"
        )


def _validate_existing_binding(
    *, payload: Mapping[str, object], project_id: str, gig_id: str,
    owner_id: str, username: str, entry: CatalogEntry, inventory_digest: str | None,
) -> None:
    expected = {
        "schema_version": "1.0",
        "kind": "template_instance_binding",
        "project_id": project_id,
        "gig_id": gig_id,
        "owner_id": owner_id,
        "username": username,
        "template_id": entry.catalog_id,
        "instance_name": "default",
        "package_id": entry.package_id,
        "package_digest": entry.entry_content_digest,
        "inventory_digest": inventory_digest,
        "approval": None,
    }
    if set(payload) != set(expected) or any(
        payload.get(field) != value
        for field, value in expected.items()
        if field != "inventory_digest" or value is not None
    ):
        raise DefaultInitError("template_instance_conflict", "committed template binding does not match the pinned batch intent")
    if inventory_digest is None:
        digest = payload.get("inventory_digest")
        if not (
            isinstance(digest, str)
            and len(digest) == 71
            and digest.startswith("sha256:")
            and all(character in "0123456789abcdef" for character in digest[7:])
        ):
            raise DefaultInitError(
                "template_reconciliation_required",
                "committed template binding has an invalid historical inventory digest",
            )


def _reserved_gig_id(intent: Mapping[str, object], template_id: str) -> str:
    rows = intent.get("reserved_gig_ids")
    if not isinstance(rows, list):
        raise DefaultInitError("template_reconciliation_required", "batch intent has invalid reserved IDs")
    matches = [
        row.get("gig_id")
        for row in rows
        if isinstance(row, dict) and row.get("template_id") == template_id
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise DefaultInitError("template_reconciliation_required", "batch intent does not reserve this template")
    return matches[0]


def initialize_defaults(
    *,
    home_root: Path,
    requested_target: Path | None,
    username: str | None,
    inventory: Sequence[CatalogEntry] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: Callable[[str], None] | None = None,
    adopt_package: bool = False,
    confirmed: bool = False,
) -> DefaultInitResult:
    """Bind an owner and journal one reserved default instance per template.

    This function performs no source execution, provider probe, import, active
    selection, or approval.  It is intentionally an explicit init operation.
    """
    home_root = home_root.expanduser().resolve(strict=False)
    target = resolve_target(requested_target)
    saved_username = _saved_username(home_root, target.root)
    supplied_username = normalize_username(username) if username is not None else saved_username
    if supplied_username is None:
        raise DefaultInitError("username_required", "--username is required for non-interactive initialization")
    if saved_username is not None and supplied_username != saved_username:
        raise DefaultInitError("workspace_owner_conflict", "provided username conflicts with the saved workspace owner")
    migration_steps: list[str] = []
    if (home_root / "registry.sqlite").exists():
        try:
            open_project_registry(
                home_root,
                create=False,
                allow_migration=True,
                migration_observer=migration_steps.append,
            )
        except RegistryError as exc:
            raise DefaultInitError("workspace_owner_invalid", str(exc)) from exc
    entries = tuple(inventory if inventory is not None else default_inventory())
    if not entries or len({entry.catalog_id for entry in entries}) != len(entries):
        raise DefaultInitError("default_inventory_invalid", "default inventory must contain unique release-eligible templates")
    observer = observer or (lambda _step: None)
    package = initialize_project_package(
        home_root=home_root,
        requested_target=requested_target,
        adopt_package=adopt_package,
        confirmed=confirmed,
        uuid_factory=uuid_factory,
    )
    target = resolve_target(requested_target)
    intent_path = _intent_path(target.root)
    intent_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = target.root / ".gigai" / "locks" / "default-init.lock"
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = TargetInitLock(lock_path)
    with lock:
        registry, _created = open_project_registry(home_root, create=False)
        with registry.transaction() as transaction:
            owner = transaction.find_workspace_owner(package.project_id)
            if owner is None:
                owner = WorkspaceOwnerRecord(package.project_id, _owner_id(uuid_factory), supplied_username)
                transaction.insert_workspace_owner(owner)
            elif owner.username != supplied_username:
                raise DefaultInitError("workspace_owner_conflict", "provided username conflicts with the saved workspace owner")
        inventory_rows = [{"template_id": entry.catalog_id, "package_id": entry.package_id, "digest": entry.entry_content_digest} for entry in entries]
        inventory_digest = canonical_json_digest(inventory_rows)
        owner_row_sha256 = canonical_json_digest(_owner_payload(owner))
        if intent_path.exists():
            try:
                intent = parse_json_bytes(intent_path.read_bytes())
            except ValueError as exc:
                raise DefaultInitError("template_reconciliation_required", "default initialization intent is malformed") from exc
            if not isinstance(intent, dict):
                raise DefaultInitError("template_reconciliation_required", "default initialization intent has invalid shape")
            status = intent.get("status", "pending")
            _validate_intent(
                intent=intent,
                owner=owner,
                project_id=package.project_id,
            )
            if status == "pending" and (
                intent.get("owner_row_sha256") != owner_row_sha256
                or intent.get("owner_id") != owner.owner_id
                or intent.get("username") != owner.username
            ):
                raise DefaultInitError("workspace_owner_conflict", "pending default initialization conflicts with the pinned workspace owner")
            if status == "pending" and intent.get("inventory_digest") != inventory_digest:
                raise DefaultInitError(
                    "template_instance_conflict",
                    "pending default initialization pins a different inventory; "
                    "re-run gigai init with the package that started this batch",
                )
            if status == "bound":
                intent = {}
        else:
            intent = {}
        authoritative = _authoritative_bindings(
            registry=registry, project_id=package.project_id, owner=owner
        )
        if not intent:
            reserved: list[dict[str, str]] = []
            with registry.transaction() as transaction:
                for entry in entries:
                    existing = transaction.find_template_instance(package.project_id, entry.catalog_id)
                    authority = authoritative.get(entry.catalog_id)
                    if existing is not None and authority is None:
                        raise DefaultInitError(
                            "template_reconciliation_required",
                            "template instance cache has no committed binding authority",
                        )
                    gig_id = (
                        authority.gig_id
                        if authority is not None
                        else allocate_gig_id(
                            is_persisted=lambda candidate: transaction.find_workpad(candidate)
                            is not None,
                            uuid_factory=uuid_factory,
                        )
                    )
                    reserved.append({"template_id": entry.catalog_id, "gig_id": gig_id})
            intent = {"schema_version": "1.0", "status": "pending", "owner_id": owner.owner_id, "project_id": package.project_id, "username": owner.username, "owner_row_sha256": owner_row_sha256, "inventory": inventory_rows, "inventory_digest": inventory_digest, "reserved_gig_ids": reserved}
            _atomic(intent_path, canonical_json_bytes(intent))
            observer("intent_prepared")
        instances: list[DefaultInstanceResult] = []
        for entry in entries:
            reserved_id = _reserved_gig_id(intent, entry.catalog_id)
            with registry.transaction() as transaction:
                existing = transaction.find_template_instance(package.project_id, entry.catalog_id)
            authority = authoritative.get(entry.catalog_id)
            if authority is not None:
                if authority.gig_id != reserved_id:
                    raise DefaultInitError(
                        "template_reconciliation_required",
                        "pinned default instance ID differs from its committed binding",
                    )
                if existing is not None:
                    _validate_cached_binding(
                        cached=existing,
                        authority=authority,
                        project_id=package.project_id,
                    )
                else:
                    with registry.transaction() as transaction:
                        transaction.insert_template_instance(
                            _cache_record(package.project_id, authority)
                        )
                update_available = (
                    authority.package_id != entry.package_id
                    or authority.package_digest != entry.entry_content_digest
                )
                active_version_exists = _has_committed_active_version(
                    workpad=authority.workpad,
                    project_id=package.project_id,
                    gig_id=authority.gig_id,
                )
                if is_scout_candidate(entry) and authority.proposal_id is None:
                    raise DefaultInitError(
                        "template_reconciliation_required",
                        "legacy binding-only Scout instance cannot claim prepared proposal authority",
                    )
                if is_scout_candidate(entry) and not update_available and not active_version_exists:
                    binding_payload = parse_json_bytes(authority.binding_bytes)
                    if not isinstance(binding_payload, dict) or not isinstance(
                        binding_payload.get("software_inventory"), dict
                    ):
                        raise DefaultInitError(
                            "template_reconciliation_required",
                            "committed Scout binding has no repairable software inventory",
                        )
                    try:
                        repair_prepared_scout_capability_manifest(
                            workpad=authority.workpad,
                            project_id=package.project_id,
                            gig_id=authority.gig_id,
                            inventory_ref=binding_payload["software_inventory"],
                            source_digest=str(authority.source_digest),
                        )
                    except ScoutMaterializationError as exc:
                        raise DefaultInitError("template_reconciliation_required", str(exc)) from exc
                instances.append(
                    DefaultInstanceResult(
                        entry.catalog_id,
                        authority.gig_id,
                        "update_available" if update_available else (
                            "capability_review_required"
                            if is_scout_candidate(entry) and active_version_exists
                            else (
                            "approval_required"
                            if authority.approval_state == "unapproved"
                            else "existing"
                            )
                        ),
                        (
                            "upstream template update is available; existing source and "
                            "proposal remain unchanged"
                            if update_available
                            else (
                                "Graph Set approval is retained; explicit capability review and "
                                "write_workpad effect consent remain pending"
                                if is_scout_candidate(entry) and active_version_exists
                                else (
                                f"pending proposal {authority.proposal_id} requires direct approval; "
                                f"use gigai approve {authority.proposal_id} --gig {authority.gig_id}"
                                if authority.approval_state == "unapproved"
                                else "binding recorded; source and proposal preparation remain pending"
                                )
                            )
                        ),
                        authority.proposal_id,
                        authority.approval_state or "unavailable",
                    )
                )
                continue
            if existing is not None:
                raise DefaultInitError(
                    "template_reconciliation_required",
                    "template instance cache has no committed binding authority",
                )
            provisioned = provision_workpad(
                home_root=home_root,
                project_id=package.project_id,
                gig_id=reserved_id,
                reconcile_existing_journal=True,
            )
            observer("workpad_published")
            try:
                migrate_workpad_layout(
                    workpad=provisioned.path,
                    project_id=package.project_id,
                    gig_id=reserved_id,
                    uuid_factory=uuid_factory,
                )
            except PrivateRecordError as exc:
                raise DefaultInitError("template_reconciliation_required", str(exc)) from exc
            candidate_material = None
            if is_scout_candidate(entry):
                try:
                    candidate_material = materialize_scout_candidate(
                        home_root=home_root,
                        requested_target=requested_target,
                        workpad=provisioned.path,
                        project_id=package.project_id,
                        gig_id=reserved_id,
                        entry=entry,
                        uuid_factory=uuid_factory,
                        observer=observer,
                    )
                except ScoutMaterializationError as exc:
                    raise DefaultInitError("template_reconciliation_required", str(exc)) from exc
                binding = {
                    "schema_version": "2.0",
                    "kind": "template_instance_binding",
                    "project_id": package.project_id,
                    "gig_id": reserved_id,
                    "owner_id": owner.owner_id,
                    "username": owner.username,
                    "template_id": "scout",
                    "instance_name": "default",
                    "package_id": entry.package_id,
                    "package_digest": entry.entry_content_digest,
                    "inventory_digest": inventory_digest,
                    "software_inventory": candidate_material.inventory_ref,
                    "source_digest": candidate_material.source_digest,
                    "proposal_id": candidate_material.proposal_id,
                    "proposal_ref": candidate_material.proposal_ref,
                    "approval": {"state": "unapproved", "approved_version": None},
                    "customization_parent": None,
                }
            else:
                binding = {"schema_version": "1.0", "kind": "template_instance_binding", "project_id": package.project_id, "gig_id": reserved_id, "owner_id": owner.owner_id, "username": owner.username, "template_id": entry.catalog_id, "instance_name": "default", "package_id": entry.package_id, "package_digest": entry.entry_content_digest, "inventory_digest": inventory_digest, "approval": None}
                _validate_existing_binding(
                    payload=binding,
                    project_id=package.project_id,
                    gig_id=reserved_id,
                    owner_id=owner.owner_id,
                    username=owner.username,
                    entry=entry,
                    inventory_digest=inventory_digest,
                )
            existing_binding = _binding_from_snapshot(
                workpad=provisioned.path,
                project_id=package.project_id,
                gig_id=reserved_id,
            )
            binding_path = "manifests/template-instance-binding.json"
            if existing_binding is None:
                binding_bytes = canonical_json_bytes(binding)
                if is_scout_candidate(entry):
                    _validate_registered_scout_binding(binding_bytes)
                journal = record_transition(
                    workpad=provisioned.path,
                    project_id=package.project_id,
                    gig_id=reserved_id,
                    handoff_id=generate_entity_id(
                        EntityPrefix.HANDOFF,
                        is_persisted=lambda _candidate: False,
                        uuid_factory=uuid_factory,
                    ),
                    transition="template_instance_bound",
                    body=(
                        "Default template instance is prepared; explicit review and "
                        "approval remain required."
                    ),
                    artifacts=(JournalArtifact(binding_path, binding_bytes),),
                    front_matter={
                        "artifact_refs": [
                            {
                                "path": binding_path,
                                "content_sha256": digest_imported_bytes(binding_bytes),
                                "media_type": "application/json",
                                "size_bytes": len(binding_bytes),
                            }
                        ]
                    },
                )
                binding_commit = journal.commit
                observer("binding_published")
            else:
                existing_payload, existing_commit = existing_binding
                if not is_scout_candidate(entry):
                    _validate_existing_binding(
                        payload=existing_payload,
                        project_id=package.project_id,
                        gig_id=reserved_id,
                        owner_id=owner.owner_id,
                        username=owner.username,
                        entry=entry,
                        inventory_digest=inventory_digest,
                    )
                binding_bytes = canonical_json_bytes(existing_payload)
                binding_commit = existing_commit
            with registry.transaction() as transaction:
                transaction.insert_template_instance(TemplateInstanceRecord(package.project_id, entry.catalog_id, "default", reserved_id, entry.package_id, entry.entry_content_digest, binding_path, digest_imported_bytes(binding_bytes), binding_commit))
            instances.append(
                DefaultInstanceResult(
                    entry.catalog_id,
                    reserved_id,
                    "approval_required" if candidate_material is not None else "binding_recorded",
                    (
                        f"pending proposal {candidate_material.proposal_id} requires direct approval; "
                        f"use gigai approve {candidate_material.proposal_id} --gig {reserved_id}"
                        if candidate_material is not None
                        else "binding recorded; source and proposal preparation remain pending"
                    ),
                    candidate_material.proposal_id if candidate_material is not None else None,
                    candidate_material.approval_state if candidate_material is not None else "unavailable",
                )
            )
        intent["status"] = "bound"
        _atomic(intent_path, canonical_json_bytes(intent))
        scout_status = (
            "capability_review_required"
            if any(
                row.template_id == "scout" and row.status == "capability_review_required"
                for row in instances
            )
            else (
                "approval_required"
                if any(
                    row.template_id == "scout" and row.approval_state == "unapproved"
                    for row in instances
                )
                else "binding_only_unready"
            )
        )
        return DefaultInitResult(
            package,
            owner.owner_id,
            owner.username,
            tuple(instances),
            scout_status,
            tuple(sorted(set(migration_steps))),
        )


__all__ = ["DefaultInitError", "DefaultInitResult", "DefaultInstanceResult", "default_inventory", "initialize_defaults", "normalize_username"]
