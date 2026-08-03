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


__all__ = [
    "CredentialReferenceError",
    "reference_is_available",
    "validate_reference",
]
