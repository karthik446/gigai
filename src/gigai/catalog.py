"""Offline built-in Gig catalog and G42 project-package materialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import uuid
from typing import Any, Mapping

from .canonical import canonical_json_bytes, canonical_json_digest, digest_imported_bytes
from .package import (
    PackageError,
    PackageInspection,
    _reject_symlink_components,
    inspect_package,
)


CATALOG_REVISION = "v0.1.7"
CATALOG_NAMESPACE = uuid.UUID("6d4f3a9e-3f76-4a2b-9d17-5c8e2f1a4b63")
CATALOG_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class CatalogEntry:
    catalog_id: str
    definition_version: str
    title: str
    summary: str
    capabilities: tuple[str, ...]
    files: Mapping[str, bytes]

    @property
    def source_revision(self) -> str:
        return f"builtin:{CATALOG_REVISION}"

    @property
    def entry_content_digest(self) -> str:
        inventory = [
            {"path": path, "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)}
            for path, data in sorted(self.files.items())
        ]
        return canonical_json_digest(inventory)

    @property
    def package_id(self) -> str:
        digest = digest_imported_bytes(
            CATALOG_NAMESPACE.bytes
            + f"{self.catalog_id}@{self.definition_version}".encode("utf-8")
        )
        raw = bytearray(bytes.fromhex(digest.removeprefix("sha256:")[:32]))
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        return f"package_{uuid.UUID(bytes=bytes(raw))}"

    def entry_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_id": self.catalog_id,
            "definition_version": self.definition_version,
            "catalog_revision": CATALOG_REVISION,
            "source_revision": self.source_revision,
            "title": self.title,
            "summary": self.summary,
            "capabilities": list(self.capabilities),
            "entry_content_digest": self.entry_content_digest,
        }

    def package_files(self) -> dict[str, bytes]:
        files = dict(self.files)
        files["catalog-entry.json"] = canonical_json_bytes(self.entry_metadata())
        files["provenance.json"] = canonical_json_bytes(
            {
                "schema_version": "1.0",
                "source": self.source_revision,
                "catalog_id": self.catalog_id,
                "definition_version": self.definition_version,
            }
        )
        return files


def _json(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(value))


def _entry(catalog_id: str, title: str, summary: str, *, question: str, capability: str) -> CatalogEntry:
    files = {
        "definition/gig.md": (
            f"# {title}\n\n{summary}\n\n"
            "This definition is portable authoring material. Approval and execution "
            "remain under GigAI's normal lifecycle authorities.\n"
        ).encode("utf-8"),
        "definition/goal-graph.json": _json(
            {
                "schema_version": "1.0",
                "catalog_id": catalog_id,
                "definition_version": "1.0",
                "question": question,
                "entry_goal": "prepare",
                "terminal_outcomes": ["READY", "BLOCKED", "INVALID"],
                "stopping_rules": ["bounded_inputs", "required_evidence", "no_implicit_network"],
            }
        ),
        "definition/review-contract.json": _json(
            {
                "schema_version": "1.0",
                "contract_id": f"catalog:{catalog_id}:review",
                "criteria": ["scope_is_declared", "evidence_is_identified", "authority_is_preserved"],
                "output_shape": "typed_findings",
                "failure_outcomes": ["BLOCKED", "INVALID"],
            }
        ),
        "definition/evaluation.json": _json(
            {
                "schema_version": "1.0",
                "fixture": f"offline:{catalog_id}:1.0",
                "deterministic": True,
                "network": "disallowed",
                "credential_access": "disallowed",
                "required_invariants": ["portable", "authority_free", "reproducible"],
            }
        ),
    }
    return CatalogEntry(
        catalog_id=catalog_id,
        definition_version="1.0",
        title=title,
        summary=summary,
        capabilities=(capability,),
        files=files,
    )


CATALOG: tuple[CatalogEntry, ...] = (
    _entry(
        "sync-references",
        "Sync References",
        "Reconcile an explicitly supplied reference set into a bounded snapshot.",
        question="Which supplied references are in scope and what provenance is required?",
        capability="references.classify",
    ),
    _entry(
        "plan-a-research",
        "Plan a Research Task",
        "Turn a declared research question into a bounded, evidence-oriented plan.",
        question="What question, scope, constraints, and stopping rules define the research?",
        capability="research.plan",
    ),
    _entry(
        "review-plan",
        "Review a Plan",
        "Inspect a candidate plan against declared requirements and produce typed findings.",
        question="Does the candidate plan satisfy its declared scope and evidence contract?",
        capability="review.findings",
    ),
)


def catalog_entries() -> tuple[CatalogEntry, ...]:
    return CATALOG


def get_catalog_entry(catalog_id: str, definition_version: str = "1.0") -> CatalogEntry:
    for entry in CATALOG:
        if entry.catalog_id == catalog_id and entry.definition_version == definition_version:
            return entry
    raise PackageError("catalog entry is unknown", code="catalog_unknown")


def package_bytes(entry: CatalogEntry) -> dict[str, bytes]:
    files = entry.package_files()
    inventory = [
        {"path": path, "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)}
        for path, data in sorted(files.items())
    ]
    files["package.json"] = canonical_json_bytes(
        {
            "schema_version": "1.0",
            "package_id": entry.package_id,
            "package_version": 1,
            "project_scope": "repository",
            "content_digest": canonical_json_digest(inventory),
            "files": inventory,
        }
    )
    return files


def validate_catalog_entry(entry: CatalogEntry) -> None:
    metadata = canonical_json_bytes(entry.entry_metadata())
    expected_keys = {
        "schema_version", "catalog_id", "definition_version", "catalog_revision",
        "source_revision", "title", "summary", "capabilities", "entry_content_digest",
    }
    parsed = json.loads(metadata)
    if set(parsed) != expected_keys or parsed["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise PackageError("catalog entry failed schema validation", code="catalog_invalid")
    if entry.entry_content_digest != entry.entry_metadata()["entry_content_digest"]:
        raise PackageError("catalog entry content digest is inconsistent", code="catalog_digest_invalid")
    files = package_bytes(entry)
    if files["package.json"] != package_bytes(entry)["package.json"]:
        raise PackageError("catalog package bytes are not deterministic", code="catalog_nondeterministic")


def materialize_catalog_package(entry: CatalogEntry, target_root: Path) -> PackageInspection:
    """Materialize one validated catalog package into an unbound target."""

    validate_catalog_entry(entry)
    destination = target_root / ".gigai" / "packages" / entry.package_id
    _reject_symlink_components(destination, label="catalog package destination")
    packages_root = destination.parent
    existing_roots = tuple(path for path in packages_root.glob("*") if path.is_dir()) if packages_root.is_dir() else ()
    if existing_roots and destination not in existing_roots:
        raise PackageError("project already has a different package", code="project_package_exists")
    if destination.exists():
        inspection = inspect_package(destination)
        if inspection.content_digest != _content_digest(package_bytes(entry)):
            raise PackageError("project package conflicts with catalog entry", code="project_package_exists")
        return inspection
    package_files = package_bytes(entry)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for relative, data in package_files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return inspect_package(destination)
    except Exception:
        import shutil

        shutil.rmtree(destination, ignore_errors=True)
        raise


def _content_digest(files: Mapping[str, bytes]) -> str:
    return canonical_json_digest(
        [
            {"path": path, "content_sha256": digest_imported_bytes(data), "size_bytes": len(data)}
            for path, data in sorted(files.items())
            if path != "package.json"
        ]
    )


__all__ = [
    "CATALOG_NAMESPACE",
    "CATALOG_REVISION",
    "CatalogEntry",
    "catalog_entries",
    "get_catalog_entry",
    "materialize_catalog_package",
    "package_bytes",
    "validate_catalog_entry",
]
