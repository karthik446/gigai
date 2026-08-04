"""Credential-reference validation without secret-value access."""

from __future__ import annotations

import os
import re

from .config import CredentialReference


ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
REFERENCE_NAME = re.compile(r"^(?:keychain|op|vault)://[A-Za-z0-9][A-Za-z0-9._:/-]*$")
LOGICAL_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


class CredentialReferenceError(ValueError):
    code = "credential_reference_invalid"


class CredentialUnavailableError(CredentialReferenceError):
    code = "credential_unavailable"


def validate_reference(reference: CredentialReference) -> None:
    if not LOGICAL_NAME.fullmatch(reference.name):
        raise CredentialReferenceError(
            "credential names must use lowercase letters, digits, '_' or '-'"
        )
    if reference.kind == "environment":
        if not ENVIRONMENT_NAME.fullmatch(reference.reference):
            raise CredentialReferenceError(
                f"credential {reference.name!r} must name an environment variable"
            )
        return
    if reference.kind == "secret-manager":
        if not REFERENCE_NAME.fullmatch(reference.reference):
            raise CredentialReferenceError(
                f"credential {reference.name!r} has an invalid secret-manager reference"
            )
        return
    raise CredentialReferenceError(
        f"credential {reference.name!r} has unsupported kind {reference.kind!r}"
    )


def reference_is_available(reference: CredentialReference) -> bool | None:
    """Report presence only; never return or serialize a credential value."""

    validate_reference(reference)
    if reference.kind == "environment":
        return reference.reference in os.environ
    return None


def resolve_reference_value(reference: CredentialReference) -> str:
    """Resolve one credential only at the runtime adapter boundary.

    The value must remain transient and must never be returned to a domain,
    configuration, diagnostic, or serialization caller.
    """

    validate_reference(reference)
    if reference.kind != "environment":
        raise CredentialUnavailableError(
            f"credential {reference.name!r} uses {reference.kind!r}, which is not "
            "available to the local G11 runtime resolver"
        )
    value = os.environ.get(reference.reference)
    if not value:
        raise CredentialUnavailableError(
            f"credential {reference.name!r} is not available in its configured environment reference"
        )
    return value


__all__ = [
    "CredentialReferenceError",
    "CredentialUnavailableError",
    "reference_is_available",
    "resolve_reference_value",
    "validate_reference",
]
