"""Read-only, human-readable registered Gig listing."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import unicodedata
from typing import Any

from .canonical import canonical_json_bytes, parse_json_bytes
from .config import ConfigurationError, GigAIConfig, load_config
from .index import JournalIndexError, read_authoritative_index
from .registry import (
    ProjectRecord,
    RegistryError,
    WorkpadRecord,
    open_project_registry,
)
from .validators import validate_serialized_contract
from .workpad import (
    WorkpadConflictError,
    WorkpadError,
    _validate_workpad_repository,
    resolve_bound_project,
)


@dataclass(frozen=True)
class GigListingDiagnostic:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": "warning", "message": self.message}


@dataclass(frozen=True)
class GigListingEntry:
    gig_id: str
    project_id: str
    title: str
    status: str
    version: str
    project_label: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {
            "gig_id": self.gig_id,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status,
            "version": self.version,
        }
        if self.project_label is not None:
            payload["project_label"] = self.project_label
        return payload


@dataclass(frozen=True)
class GigListingResult:
    scope: dict[str, str]
    entries: tuple[GigListingEntry, ...]
    diagnostics: tuple[GigListingDiagnostic, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "scope": self.scope,
            "entries": [entry.as_dict() for entry in self.entries],
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }


class GigListingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_STATUS_LABELS = {
    "drafting": "Drafting",
    "proposed": "Proposed",
    "rejected": "Rejected",
    "superseded": "Superseded",
}


def list_gigs(
    *,
    home_root: Path,
    requested_target: Path | None,
    all_projects: bool,
    cwd: Path | None = None,
) -> GigListingResult:
    if all_projects and requested_target is not None:
        raise GigListingError(
            "gigs_scope_conflict", "--all cannot be combined with --target"
        )

    home = home_root.expanduser().resolve(strict=False)
    try:
        config = load_config(home)
        bound = None
        if not all_projects:
            bound = resolve_bound_project(
                home_root=home,
                requested_target=requested_target,
                cwd=cwd,
                tolerate_invalid_registry_rows=True,
            )
        registry, _ = open_project_registry(
            home, create=False, tolerate_invalid_rows=True
        )
        project_id = bound.project_id if bound is not None else None
        listing = registry.list_workpad_records(project_id)
    except GigListingError:
        raise
    except WorkpadError as exc:
        if "not bound" in str(exc):
            code = "gigs_target_unbound" if requested_target is not None else "gigs_project_unbound"
        else:
            code = "registry_unavailable"
        raise GigListingError(code, str(exc)) from exc
    except RegistryError as exc:
        raise GigListingError("registry_unavailable", str(exc)) from exc
    except ConfigurationError as exc:
        raise GigListingError("registry_unavailable", str(exc)) from exc
    except OSError as exc:
        raise GigListingError("registry_unavailable", str(exc)) from exc

    diagnostics = [
        GigListingDiagnostic(item.code, item.message)
        for item in listing.diagnostics
    ]
    project_records: dict[str, ProjectRecord] = {}
    if all_projects:
        for record in listing.records:
            if record.project_id in project_records:
                continue
            try:
                project = registry.find_project(record.project_id)
            except RegistryError:
                project = None
            if project is None:
                diagnostics.append(
                    GigListingDiagnostic(
                        "registry_row_invalid",
                        "one registry workpad row was omitted because its project binding is invalid",
                    )
                )
            else:
                project_records[record.project_id] = project
    elif bound is not None:
        try:
            project = registry.find_project(bound.project_id)
        except RegistryError as exc:
            raise GigListingError("registry_unavailable", str(exc)) from exc
        if project is None:
            raise GigListingError(
                "registry_unavailable", "the bound project is missing from the registry"
            )
        project_records[bound.project_id] = project

    labels = _project_labels(project_records)
    entries: list[GigListingEntry] = []
    for record in listing.records:
        project = project_records.get(record.project_id)
        if all_projects and project is None:
            continue
        project_label = labels.get(record.project_id) if all_projects else None
        entry, entry_diagnostics = _read_entry(
            config=config,
            record=record,
            project_label=project_label,
        )
        entries.append(entry)
        diagnostics.extend(entry_diagnostics)

    entries.sort(key=lambda item: _entry_sort_key(item, all_projects))
    diagnostics.sort(key=lambda item: (item.code, item.message))
    if all_projects:
        scope = {"kind": "all"}
    else:
        assert bound is not None
        scope = {
            "kind": "project",
            "project_id": bound.project_id,
            "label": labels.get(
                bound.project_id,
                _fallback_label(bound.target_root.name, bound.target_kind),
            ),
        }
    return GigListingResult(scope, tuple(entries), tuple(diagnostics))


def _read_entry(
    *, config: GigAIConfig, record: WorkpadRecord, project_label: str | None
) -> tuple[GigListingEntry, tuple[GigListingDiagnostic, ...]]:
    diagnostics: list[GigListingDiagnostic] = []
    path, path_code = _safe_workpad_path(config, record)
    if path is None:
        diagnostics.append(
            GigListingDiagnostic(path_code, "registered workpad is unavailable or outside its configured authority")
        )
        return (
            GigListingEntry(record.gig_id, record.project_id, "N/A Gig", "N/A", "N/A", project_label),
            tuple(diagnostics),
        )
    try:
        _validate_workpad_repository(
            path,
            record.project_id,
            record.gig_id,
            allow_journal=True,
            allow_semantic_state=True,
        )
    except WorkpadConflictError as exc:
        message = str(exc)
        code = "journal_project_id_mismatch" if "gigai.project-id" in message else (
            "journal_gig_id_mismatch" if "gigai.gig-id" in message else "workpad_path_unsafe"
        )
        diagnostics.append(GigListingDiagnostic(code, message_safe(code)))
        return (
            GigListingEntry(record.gig_id, record.project_id, "N/A Gig", "N/A", "N/A", project_label),
            tuple(diagnostics),
        )
    except OSError:
        diagnostics.append(
            GigListingDiagnostic("workpad_unavailable", "registered workpad could not be read")
        )
        return (
            GigListingEntry(record.gig_id, record.project_id, "N/A Gig", "N/A", "N/A", project_label),
            tuple(diagnostics),
        )
    try:
        projection = read_authoritative_index(
            workpad=path,
            project_id=record.project_id,
            gig_id=record.gig_id,
            tolerate_manifest_errors=True,
        )
    except (JournalIndexError, OSError):
        diagnostics.append(
            GigListingDiagnostic("journal_unreadable", "committed journal could not be read")
        )
        return (
            GigListingEntry(record.gig_id, record.project_id, "N/A Gig", "N/A", "N/A", project_label),
            tuple(diagnostics),
        )

    proposal_bytes = _git_file(path, projection.head, "manifests/gig-proposal.json")
    active_bytes = _git_file(path, projection.head, "manifests/active-gig-version.json")
    proposal = _valid_manifest(proposal_bytes, "gig-proposal.schema.json")
    active = _valid_manifest(active_bytes, "active-gig-version.schema.json")
    proposal_invalid = proposal_bytes is not None and (
        proposal is None
        or proposal.get("gig_id") != record.gig_id
        or proposal.get("project_id") != record.project_id
    )
    active_invalid = active_bytes is not None and active is None
    if proposal_invalid:
        diagnostics.append(GigListingDiagnostic("proposal_metadata_invalid", "Gig proposal metadata is invalid"))
    if active_invalid:
        diagnostics.append(GigListingDiagnostic("active_version_metadata_invalid", "active version metadata is invalid"))

    title = "Untitled Gig"
    if proposal_invalid:
        title = "N/A Gig"
    elif proposal is not None and isinstance(proposal.get("name"), str) and proposal["name"].strip():
        title = proposal["name"].strip()
    pointer_valid, historical_proposal = _valid_active_pointer(
        path, active, record
    )
    if active is not None and not pointer_valid and not active_invalid:
        diagnostics.append(GigListingDiagnostic("active_version_metadata_invalid", "active version metadata is invalid"))
        active_invalid = True
    status = "Initializing"
    if proposal_invalid or active_invalid:
        status = "N/A"
    elif proposal is not None:
        raw_status = proposal.get("status")
        if raw_status == "approved":
            if pointer_valid and active is not None and active.get("approved_proposal_id") == proposal.get("proposal_id"):
                status = "Approved"
            else:
                status = "N/A"
        else:
            status = _STATUS_LABELS.get(str(raw_status), "N/A")

    if pointer_valid and active is not None:
        version = f"v{active['active_version']}"
    elif active is None and proposal is not None and not proposal_invalid:
        version = "Proposed"
    elif active is None and proposal is None:
        version = "—"
    else:
        version = "N/A"
    if historical_proposal is not None and historical_proposal.get("status") != "approved":
        version = "N/A"
    return (
        GigListingEntry(record.gig_id, record.project_id, title, status, version, project_label),
        tuple(diagnostics),
    )


def _safe_workpad_path(
    config: GigAIConfig, record: WorkpadRecord
) -> tuple[Path | None, str]:
    configured = config.workpad_root
    if configured.is_symlink():
        return None, "workpad_path_unsafe"
    try:
        root = configured.resolve(strict=True)
        expected = root / "projects" / record.project_id / "gigs" / record.gig_id
        resolved = expected.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "workpad_unavailable"
    if root != configured or resolved != expected or not resolved.is_relative_to(root):
        return None, "workpad_path_unsafe"
    if record.workpad_locator != os.fspath(expected):
        return None, "workpad_path_unsafe"
    return expected, ""


def _valid_manifest(raw: bytes | None, schema: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        payload = parse_json_bytes(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        serialized = canonical_json_bytes(payload)
    except (TypeError, ValueError):
        return None
    if not validate_serialized_contract(schema, serialized).valid:
        return None
    return payload


def _valid_active_pointer(
    workpad: Path, active: dict[str, Any] | None, record: WorkpadRecord
) -> tuple[bool, dict[str, Any] | None]:
    if active is None:
        return False, None
    if active.get("gig_id") != record.gig_id:
        return False, None
    commit = active.get("journal_commit")
    approved_id = active.get("approved_proposal_id")
    if not isinstance(commit, str) or not isinstance(approved_id, str):
        return False, None
    raw = _git_file(workpad, commit, "manifests/gig-proposal.json")
    proposal = _valid_manifest(raw, "gig-proposal.schema.json")
    if proposal is None or proposal.get("proposal_id") != approved_id:
        return False, proposal
    if proposal.get("gig_id") != record.gig_id or proposal.get("project_id") != record.project_id:
        return False, proposal
    if proposal.get("status") != "approved":
        return False, proposal
    return True, proposal


def _git_file(workpad: Path, commit: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "-C", os.fspath(workpad), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
        shell=False,
    )
    return result.stdout if result.returncode == 0 else None


def _project_labels(projects: dict[str, ProjectRecord]) -> dict[str, str]:
    bases = {project_id: _fallback_label(project.target_locator, project.target_kind, include_kind=False) for project_id, project in projects.items()}
    groups: dict[str, list[str]] = {}
    for project_id, base in bases.items():
        groups.setdefault(base, []).append(project_id)
    result: dict[str, str] = {}
    for base, project_ids in groups.items():
        for index, project_id in enumerate(sorted(project_ids), start=1):
            suffix = f" #{index}" if len(project_ids) > 1 else ""
            result[project_id] = (
                f"{base}{suffix} [{_kind_label(projects[project_id].target_kind)}]"
            )
    return result


def _fallback_label(value: str, kind: str, *, include_kind: bool = True) -> str:
    name = unicodedata.normalize("NFC", Path(value).name)
    name = "".join("\ufffd" if unicodedata.category(char)[0] == "C" else char for char in name)
    name = " ".join(name.split())[:48] or "Project"
    if include_kind:
        return f"{name} [{_kind_label(kind)}]"
    return name


def _kind_label(kind: str) -> str:
    return "git" if kind == "git" else "directory"


def _entry_sort_key(entry: GigListingEntry, all_projects: bool) -> tuple[str, str, str]:
    title = unicodedata.normalize("NFC", entry.title).casefold().encode("utf-8")
    label = entry.project_label or ""
    return (label, title, entry.gig_id)


def message_safe(code: str) -> str:
    return {
        "journal_project_id_mismatch": "workpad journal project ownership does not match the registry",
        "journal_gig_id_mismatch": "workpad journal Gig ownership does not match the registry",
        "workpad_path_unsafe": "registered workpad path is unsafe",
    }.get(code, "registered workpad could not be read")


__all__ = [
    "GigListingDiagnostic",
    "GigListingEntry",
    "GigListingError",
    "GigListingResult",
    "list_gigs",
]
