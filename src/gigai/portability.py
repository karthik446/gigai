"""Read-only authority and portability checks for G23.

This module never writes a workpad, installs a capability, or consults a
network.  It verifies that the live active-version pointer is the pointer
published by the one journal child that accepted the Gig, then validates the
optional capability-manifest reference and proposal lineage from sealed Git
history.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .canonical import (
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
    parse_json_front_matter,
)
from .validators import validate_serialized_contract


class PortabilityError(ValueError):
    """A G23 authority or portability invariant failed closed."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PortabilityResult:
    outcome: str
    sealed_commit: str
    publication_commit: str
    pointer: Mapping[str, Any]
    capability_manifest: Mapping[str, Any] | None


@dataclass(frozen=True)
class ProposalLineage:
    gig_id: str
    proposal_ids: tuple[str, ...]
    proposals: tuple[Mapping[str, Any], ...]


def verify_active_version_portability(workpad: Path) -> PortabilityResult:
    """Verify the live pointer against its sealed publication and manifest."""

    root = workpad.expanduser().resolve(strict=False)
    pointer_path = root / "manifests" / "active-gig-version.json"
    if pointer_path.is_symlink() or not pointer_path.is_file():
        raise PortabilityError("active-version pointer is not a regular file", code="refused_unsealed_pointer")
    live_bytes = pointer_path.read_bytes()
    _require_schema("active-gig-version.schema.json", live_bytes, "refused_unsealed_pointer")
    live = parse_json_bytes(live_bytes)
    if not isinstance(live, dict):
        raise PortabilityError("active-version pointer is not an object", code="refused_unsealed_pointer")
    sealed_commit = _required_string(live, "journal_commit", "refused_unsealed_pointer")
    tag = _required_string(live, "journal_tag", "refused_unsealed_pointer")
    resolved_tag = _git(root, "rev-parse", "--verify", tag, check=False)
    if resolved_tag.returncode != 0 or resolved_tag.stdout.strip() != sealed_commit:
        raise PortabilityError("active-version tag does not resolve to journal_commit", code="refused_unsealed_pointer")

    publication_commits = _publication_children(root, sealed_commit)
    if not publication_commits:
        raise PortabilityError("active-version pointer has not been published", code="refused_unpublished_pointer")
    if len(publication_commits) != 1:
        raise PortabilityError("active-version pointer has ambiguous publication", code="refused_ambiguous_publication")
    publication_commit = publication_commits[0]
    sealed_pointer = _git_bytes(root, "show", f"{publication_commit}:manifests/active-gig-version.json")
    _require_schema("active-gig-version.schema.json", sealed_pointer, "refused_unsealed_pointer")
    if canonical_json_bytes(live) != canonical_json_bytes(parse_json_bytes(sealed_pointer)):
        raise PortabilityError("live active-version pointer differs from sealed publication", code="refused_unsealed_pointer")

    reference = live.get("capability_manifest")
    if reference is None:
        return PortabilityResult("reported_non_portable", sealed_commit, publication_commit, live, None)
    manifest_bytes = _read_referenced_manifest(root, reference)
    _require_schema("capability-manifest.schema.json", manifest_bytes, "refused_digest_mismatch")
    manifest = parse_json_bytes(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("gig_id") != live.get("gig_id"):
        raise PortabilityError("capability manifest belongs to another Gig", code="refused_unbound_manifest")
    return PortabilityResult("verified_portable", sealed_commit, publication_commit, live, manifest)


def resolve_proposal_lineage(
    workpad: Path, *, approved_proposal_id: str, gig_id: str, sealed_commit: str
) -> ProposalLineage:
    """Resolve the complete proposal chain using only ancestors of sealed_commit."""

    root = workpad.expanduser().resolve(strict=False)
    proposals = _historical_proposals(root, sealed_commit)
    current = approved_proposal_id
    seen: set[str] = set()
    ordered: list[Mapping[str, Any]] = []
    while True:
        if current in seen:
            raise PortabilityError("proposal lineage contains a cycle", code="refused_lineage_cycle")
        seen.add(current)
        proposal = proposals.get(current)
        if proposal is None:
            raise PortabilityError("proposal lineage parent is missing from sealed history", code="refused_missing_parent")
        if proposal.get("gig_id") != gig_id:
            raise PortabilityError("proposal lineage crosses Gig identities", code="refused_cross_gig_lineage")
        ordered.append(proposal)
        parent = proposal.get("parent_proposal_id")
        if parent is None:
            if proposal.get("kind") != "create":
                raise PortabilityError("proposal lineage does not terminate at a create proposal", code="refused_unsealed_lineage")
            break
        if not isinstance(parent, str):
            raise PortabilityError("proposal lineage parent is malformed", code="refused_missing_parent")
        current = parent
    return ProposalLineage(gig_id, tuple(item["proposal_id"] for item in ordered), tuple(ordered))


def _publication_children(root: Path, sealed_commit: str) -> tuple[str, ...]:
    result = _git(root, "rev-list", "--all", "--children")
    matches: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts or parts[0] != sealed_commit:
            continue
        for child in parts[1:]:
            names = _git(root, "show", "--format=", "--name-only", child).stdout.splitlines()
            handoffs = [name for name in names if name.startswith("handoffs/") and name.endswith(".txt")]
            if len(handoffs) != 1 or "manifests/active-gig-version.json" not in names:
                continue
            metadata, _body = parse_json_front_matter(_git_bytes(root, "show", f"{child}:{handoffs[0]}"))
            if metadata.get("transition") == "gig_accepted" and metadata.get("previous_journal_commit") == sealed_commit:
                matches.append(child)
    return tuple(sorted(set(matches)))


def _historical_proposals(root: Path, sealed_commit: str) -> dict[str, Mapping[str, Any]]:
    result = _git(root, "rev-list", "--reverse", sealed_commit)
    found: dict[str, Mapping[str, Any]] = {}
    for commit in result.stdout.splitlines():
        probe = _git(root, "cat-file", "-e", f"{commit}:manifests/gig-proposal.json", check=False)
        if probe.returncode != 0:
            continue
        payload = parse_json_bytes(_git_bytes(root, "show", f"{commit}:manifests/gig-proposal.json"))
        if not isinstance(payload, dict) or not isinstance(payload.get("proposal_id"), str):
            raise PortabilityError("historical proposal is malformed", code="refused_unsealed_lineage")
        _require_schema("gig-proposal.schema.json", canonical_json_bytes(payload), "refused_unsealed_lineage")
        found[payload["proposal_id"]] = payload
    return found


def _read_referenced_manifest(root: Path, reference: object) -> bytes:
    if not isinstance(reference, Mapping):
        raise PortabilityError("capability_manifest is not an artifact reference", code="refused_unbound_manifest")
    path_value = reference.get("path")
    if not isinstance(path_value, str) or path_value.startswith("/") or "\\" in path_value or ".." in Path(path_value).parts:
        raise PortabilityError("capability manifest path is unsafe", code="refused_unsafe_path")
    path = root / path_value
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise PortabilityError("capability manifest path escapes workpad", code="refused_unsafe_path") from exc
    if path.is_symlink() or not path.is_file():
        raise PortabilityError("capability manifest path is not a regular file", code="refused_unsafe_path")
    payload = path.read_bytes()
    if reference.get("content_sha256") != digest_imported_bytes(payload) or reference.get("size_bytes") != len(payload):
        raise PortabilityError("capability manifest bytes differ from the pointer", code="refused_digest_mismatch")
    return payload


def _require_schema(schema_name: str, payload: bytes, code: str) -> None:
    if not validate_serialized_contract(schema_name, payload).valid:
        raise PortabilityError(f"{schema_name} validation failed", code=code)


def _required_string(payload: Mapping[str, Any], key: str, code: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PortabilityError(f"{key} is missing", code=code)
    return value


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        text=True,
        check=check,
        shell=False,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )


def _git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        check=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout


__all__ = ["PortabilityError", "PortabilityResult", "ProposalLineage", "resolve_proposal_lineage", "verify_active_version_portability"]
