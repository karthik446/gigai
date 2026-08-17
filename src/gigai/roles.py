"""Namespaced GigAI roles and explicit compatibility decoding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class RoleError(ValueError):
    """A role reference is malformed, unknown, or used in the wrong namespace."""


NAMESPACES = frozenset({"model_invocation", "reference", "executor", "occurrence", "protocol"})

_BUILT_INS: dict[str, frozenset[str]] = {
    "model_invocation": frozenset(
        {
            "gig-builder",
            "proposal-questioner",
            "live-diagnostic",
            "offline-diagnostic",
            "create",
            "reviewer",
            "researcher",
        }
    ),
    "reference": frozenset({"primary", "primary-source", "subject-repository", "comparison-data", "source"}),
    "executor": frozenset({"local-capability", "researcher"}),
    "occurrence": frozenset({"input"}),
    "protocol": frozenset({"user", "assistant"}),
}

_LEGACY_ALIASES = {
    ("model_invocation", "diagnostic"): "legacy_unresolved",
    ("model_invocation", "doctor"): "legacy_unresolved",
    ("model_invocation", "test"): "legacy_unresolved",
}


@dataclass(frozen=True)
class RoleReference:
    namespace: str
    id: str
    version: int = 1

    def __post_init__(self) -> None:
        if self.namespace not in NAMESPACES:
            raise RoleError(f"unknown role namespace: {self.namespace}")
        if not self.id or self.version <= 0:
            raise RoleError("role id must be non-empty and version must be positive")

    def as_dict(self) -> dict[str, object]:
        return {"namespace": self.namespace, "id": self.id, "version": self.version}


@dataclass(frozen=True)
class RoleResolution:
    reference: RoleReference | None
    legacy_value: str | None
    status: str


@dataclass(frozen=True)
class RoleExtension:
    """An owner-declared role definition without capability authority."""

    reference: RoleReference
    owner: str
    purpose: str
    allowed_callers: tuple[str, ...]
    required_evidence: tuple[str, ...]
    deprecated: bool = False


_EXTENSIONS: dict[tuple[str, str], RoleExtension] = {}


def register_extension(
    reference: RoleReference,
    *,
    owner: str,
    purpose: str,
    allowed_callers: tuple[str, ...],
    required_evidence: tuple[str, ...],
    deprecated: bool = False,
) -> RoleExtension:
    """Register an explicit namespace extension; no capability field is accepted."""

    if reference.id in _BUILT_INS[reference.namespace]:
        raise RoleError(f"cannot replace built-in role: {reference.namespace}/{reference.id}")
    if not owner or not purpose or not allowed_callers or not required_evidence:
        raise RoleError("role extensions require owner, purpose, callers, and evidence")
    key = (reference.namespace, reference.id)
    existing = _EXTENSIONS.get(key)
    if existing is not None and existing != RoleExtension(
        reference, owner, purpose, allowed_callers, required_evidence, deprecated
    ):
        raise RoleError(f"role extension is already registered: {reference.namespace}/{reference.id}")
    extension = RoleExtension(
        reference, owner, purpose, allowed_callers, required_evidence, deprecated
    )
    _EXTENSIONS[key] = extension
    return extension


def _structured(value: Mapping[str, object]) -> RoleReference:
    if set(value) != {"namespace", "id", "version"}:
        raise RoleError("structured role must contain exactly namespace, id, and version")
    namespace = value["namespace"]
    role_id = value["id"]
    version = value["version"]
    if not isinstance(namespace, str) or not isinstance(role_id, str) or type(version) is not int:
        raise RoleError("structured role fields have invalid types")
    reference = RoleReference(namespace, role_id, version)
    if reference.id not in _BUILT_INS[reference.namespace] and (
        reference.namespace,
        reference.id,
    ) not in _EXTENSIONS:
        raise RoleError(f"unknown role id in {reference.namespace}: {reference.id}")
    return reference


def resolve_role(value: str | Mapping[str, object], *, namespace: str) -> RoleResolution:
    """Resolve a structured role or classify an old string without guessing."""

    if namespace not in NAMESPACES:
        raise RoleError(f"unknown role namespace: {namespace}")
    if isinstance(value, Mapping):
        reference = _structured(value)
        if reference.namespace != namespace:
            raise RoleError(f"role namespace {reference.namespace} does not match {namespace}")
        return RoleResolution(reference, None, "registered")
    if not isinstance(value, str) or not value or "\0" in value:
        raise RoleError("legacy role must be a non-empty NUL-free string")
    if value in _BUILT_INS[namespace]:
        return RoleResolution(RoleReference(namespace, value), value, "registered")
    extension = _EXTENSIONS.get((namespace, value))
    if extension is not None:
        status = "deprecated" if extension.deprecated else "registered"
        return RoleResolution(extension.reference, value, status)
    if (namespace, value) in _LEGACY_ALIASES:
        return RoleResolution(None, value, _LEGACY_ALIASES[(namespace, value)])
    return RoleResolution(None, value, "legacy_unresolved")


def require_registered(value: str | Mapping[str, object], *, namespace: str) -> RoleReference:
    resolution = resolve_role(value, namespace=namespace)
    if resolution.status != "registered" or resolution.reference is None:
        raise RoleError(f"role is not registered in {namespace}: {resolution.legacy_value}")
    return resolution.reference


def registered_roles(namespace: str) -> tuple[RoleReference, ...]:
    if namespace not in NAMESPACES:
        raise RoleError(f"unknown role namespace: {namespace}")
    extensions = (
        extension.reference
        for (extension_namespace, _), extension in _EXTENSIONS.items()
        if extension_namespace == namespace
    )
    return tuple(
        sorted(
            (RoleReference(namespace, role_id) for role_id in _BUILT_INS[namespace]),
            key=lambda item: item.id,
        )
    ) + tuple(sorted(extensions, key=lambda item: item.id))


__all__ = [
    "NAMESPACES",
    "RoleError",
    "RoleExtension",
    "RoleReference",
    "RoleResolution",
    "register_extension",
    "registered_roles",
    "require_registered",
    "resolve_role",
]
