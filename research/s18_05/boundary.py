"""Executable, provider-free S18-05 redaction and boundary evidence.

This module models the pre-invocation decision only. It never opens a socket,
resolves a credential value, starts a process, or invokes an adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from gigai.canonical import digest_imported_bytes
from gigai.credentials import CredentialReference, validate_reference
from gigai.review import redact_text


@dataclass(frozen=True)
class BoundaryDecision:
    status: str
    reason: str
    selected_reference_ids: tuple[str, ...]
    provider_input: str | None
    credential_reference: Mapping[str, str] | None
    network_allowed: bool


_SAFE_RELATIVE = re.compile(r"^(?!/)(?!.*\\)(?!.*(^|/)\.\.(/|$)).+$")


def build_fixture_bundle() -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return a small local bundle with public, private, and repository bytes."""

    objects = {
        "references/public.txt": b"Public source claim.\n",
        "references/private.txt": b"Private unselected note.\n",
        "references/repository.txt": b"Repository snapshot bytes.\n",
    }
    references = [
        _reference("ref-public", "references/public.txt", objects["references/public.txt"]),
        _reference("ref-private", "references/private.txt", objects["references/private.txt"]),
        _reference("ref-repository", "references/repository.txt", objects["references/repository.txt"]),
    ]
    return (
        {
            "bundle_id": "bundle-s18-05-fixture",
            "references": references,
            "redaction_policy": {
                "mode": "local_only",
                "allowed_reference_ids": ["ref-public", "ref-private", "ref-repository"],
            },
        },
        objects,
    )


def _reference(reference_id: str, path: str, payload: bytes) -> dict[str, Any]:
    return {
        "reference_id": reference_id,
        "path": path,
        "content_sha256": digest_imported_bytes(payload),
        "size_bytes": len(payload),
    }


def prepare_provider_boundary(
    bundle: Mapping[str, Any],
    objects: Mapping[str, bytes],
    *,
    selected_reference_ids: tuple[str, ...],
    credential: CredentialReference,
    credential_values: tuple[str, ...] = (),
    redaction_values: tuple[str, ...] = (),
    network_allowed: bool,
    offline: bool,
) -> BoundaryDecision:
    """Evaluate the pre-invocation boundary without invoking a provider."""

    allowed = set(bundle.get("redaction_policy", {}).get("allowed_reference_ids", []))
    references = {
        str(item.get("reference_id")): item
        for item in bundle.get("references", [])
        if isinstance(item, Mapping)
    }
    selected = tuple(selected_reference_ids)
    if not selected:
        return _blocked("reference_selection_required", selected, network_allowed)
    if any(reference_id not in allowed for reference_id in selected):
        return _blocked("reference_not_allowed", selected, network_allowed)
    if any(reference_id not in references for reference_id in selected):
        return _blocked("reference_missing", selected, network_allowed)

    try:
        validate_reference(credential)
    except ValueError:
        return _blocked("credential_reference_invalid", selected, network_allowed)

    selected_parts: list[str] = []
    for reference_id in selected:
        path = references[reference_id].get("path")
        if not isinstance(path, str) or not _SAFE_RELATIVE.fullmatch(path):
            return _blocked("unsafe_reference_path", selected, network_allowed)
        payload = objects.get(str(path))
        if payload is None:
            return _blocked("selected_reference_bytes_missing", selected, network_allowed)
        if digest_imported_bytes(payload) != references[reference_id].get("content_sha256"):
            return _blocked("reference_digest_mismatch", selected, network_allowed)
        if len(payload) != references[reference_id].get("size_bytes"):
            return _blocked("reference_size_mismatch", selected, network_allowed)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return _blocked("selected_reference_not_text", selected, network_allowed)
        selected_parts.append(f"[{reference_id}]\n{text}")

    provider_input = redact_text("\n".join(selected_parts), redaction_values)
    if any(value and value in provider_input for value in credential_values):
        return _blocked("redaction_failed", selected, network_allowed)
    if offline or not network_allowed:
        return _blocked("network_denied", selected, network_allowed)

    return BoundaryDecision(
        status="eligible",
        reason="pre_invocation_checks_passed",
        selected_reference_ids=selected,
        provider_input=provider_input,
        credential_reference={
            "name": credential.name,
            "kind": credential.kind,
            "reference": credential.reference,
        },
        network_allowed=network_allowed,
    )


def _blocked(reason: str, selected: tuple[str, ...], network_allowed: bool) -> BoundaryDecision:
    return BoundaryDecision(
        status="blocked",
        reason=reason,
        selected_reference_ids=selected,
        provider_input=None,
        credential_reference=None,
        network_allowed=network_allowed,
    )


__all__ = ["BoundaryDecision", "build_fixture_bundle", "prepare_provider_boundary"]
