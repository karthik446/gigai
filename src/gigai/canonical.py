"""Canonical bytes, digests, identifiers, and version selection for GigAI.

GigAI uses a deliberately restricted RFC 8785 JSON domain. Object member names
are ASCII identifiers, so Python code-point ordering and JCS UTF-16 code-unit
ordering are identical for every accepted name. Floats are forbidden, integers
are restricted to the interoperable range, and Unicode string values are
preserved without normalization.

The API names make byte ownership explicit:

* :func:`canonicalize_owned_text` normalizes text created by GigAI.
* :func:`digest_imported_bytes` hashes imported user bytes without changing
  them.

Callers must never decode and re-encode imported content before passing it to
``digest_imported_bytes``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Collection
from enum import StrEnum
from typing import Any


SAFE_INTEGER_MAX = 9_007_199_254_740_991
ID_COLLISION_RETRIES = 3
MEMBER_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
ENTITY_ID = re.compile(
    r"^(?P<prefix>project|gig|gp|graph|goal|edge|run|handoff|inv|learning|improve_manifest|draft_manifest|occurrence|comparison)_"
    r"(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
FRONT_MATTER_OPEN = b"---gigai-json\n"
FRONT_MATTER_CLOSE = b"---\n"


class CanonicalizationError(ValueError):
    """Base class for stable canonical-contract failures."""

    code = "canonicalization_error"


class InvalidCanonicalValueError(CanonicalizationError):
    code = "invalid_canonical_value"


class DuplicateJsonMemberError(CanonicalizationError):
    code = "duplicate_json_member"


class InvalidJsonError(CanonicalizationError):
    code = "invalid_json"


class InvalidOwnedTextError(CanonicalizationError):
    code = "invalid_owned_text"


class InvalidFrontMatterError(CanonicalizationError):
    code = "invalid_front_matter"


class DigestMismatchError(CanonicalizationError):
    code = "digest_mismatch"


class ExactBytesRequiredError(CanonicalizationError):
    code = "exact_bytes_required"


class InvalidIdentifierError(CanonicalizationError):
    code = "invalid_identifier"


class IdentifierCollisionError(InvalidIdentifierError):
    code = "identifier_collision"


class InvalidVersionError(CanonicalizationError):
    code = "invalid_version"


class VersionNotApprovedError(InvalidVersionError):
    code = "version_not_approved"


class InconsistentActiveVersionError(InvalidVersionError):
    code = "inconsistent_active_version"


class UnsupportedSchemaVersionError(InvalidVersionError):
    code = "unsupported_schema_version"


class EntityPrefix(StrEnum):
    PROJECT = "project"
    GIG = "gig"
    GIG_PROPOSAL = "gp"
    GRAPH = "graph"
    GOAL = "goal"
    EDGE = "edge"
    RUN = "run"
    HANDOFF = "handoff"
    INVOCATION = "inv"
    LEARNING = "learning"
    IMPROVEMENT_MANIFEST = "improve_manifest"
    DRAFT_MANIFEST = "draft_manifest"
    DISCOVERY_MANIFEST = "discovery_manifest"
    OCCURRENCE = "occurrence"
    COMPARISON = "comparison"


def _validate_canonical_value(
    value: Any,
    active_containers: set[int] | None = None,
) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise InvalidCanonicalValueError(
                "string contains an invalid Unicode surrogate"
            ) from exc
        return
    if type(value) is int:
        if not -SAFE_INTEGER_MAX <= value <= SAFE_INTEGER_MAX:
            raise InvalidCanonicalValueError(
                "integer is outside the JCS interoperable range"
            )
        return
    if type(value) is float:
        raise InvalidCanonicalValueError(
            "identity-bearing GigAI JSON forbids floating-point numbers"
        )

    if type(value) not in (list, dict):
        raise InvalidCanonicalValueError(
            f"unsupported canonical JSON value: {type(value).__name__}"
        )

    if active_containers is None:
        active_containers = set()
    identity = id(value)
    if identity in active_containers:
        raise InvalidCanonicalValueError("canonical JSON cannot contain cycles")
    active_containers.add(identity)
    try:
        if type(value) is list:
            for item in value:
                _validate_canonical_value(item, active_containers)
            return
        for key, item in value.items():
            if type(key) is not str or not MEMBER_NAME.fullmatch(key):
                raise InvalidCanonicalValueError(
                    f"invalid canonical member name: {key!r}"
                )
            _validate_canonical_value(item, active_containers)
    finally:
        active_containers.remove(identity)


def canonical_json_bytes(value: Any) -> bytes:
    """Render restricted-JCS UTF-8 bytes with no trailing newline."""

    _validate_canonical_value(value)
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return rendered.encode("utf-8")


def parse_json_bytes(data: bytes) -> Any:
    """Parse UTF-8 JSON while rejecting duplicate object member names."""

    if type(data) is not bytes:
        raise ExactBytesRequiredError("JSON input must be exact bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJsonMemberError(
                    f"duplicate JSON member name: {key!r}"
                )
            result[key] = value
        return result

    try:
        decoded = data.decode("utf-8", errors="strict")
        value = json.loads(decoded, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidJsonError("input is not valid UTF-8 JSON") from exc
    _validate_canonical_value(value)
    return value


def _sha256_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def canonical_json_digest(value: Any) -> str:
    """Digest the canonical logical identity of a GigAI JSON value."""

    return _sha256_digest(canonical_json_bytes(value))


def canonicalize_owned_text(text: str) -> bytes:
    """Normalize GigAI-owned text to UTF-8, LF, and exactly one final LF."""

    if type(text) is not str:
        raise InvalidOwnedTextError("GigAI-owned text must be a string")
    if "\x00" in text:
        raise InvalidOwnedTextError("GigAI-owned text cannot contain NUL")
    if text.startswith("\ufeff"):
        raise InvalidOwnedTextError("GigAI-owned text cannot begin with a UTF-8 BOM")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n") + "\n"
    try:
        return normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise InvalidOwnedTextError(
            "GigAI-owned text contains an invalid Unicode surrogate"
        ) from exc


def digest_owned_text(text: str) -> str:
    """Digest text after the GigAI-owned canonical text transformation."""

    return _sha256_digest(canonicalize_owned_text(text))


def digest_imported_bytes(data: bytes) -> str:
    """Digest imported user bytes exactly as read, without normalization."""

    if type(data) is not bytes:
        raise ExactBytesRequiredError(
            "imported content must be bytes; text encoding is not implicit"
        )
    return _sha256_digest(data)


def render_json_front_matter(metadata: dict[str, Any], body: str) -> bytes:
    """Render canonical JSON front matter and a normalized owned-text body."""

    if type(metadata) is not dict:
        raise InvalidFrontMatterError("front matter metadata must be a JSON object")
    body_bytes = canonicalize_owned_text(body)
    expected = _sha256_digest(body_bytes)
    if metadata.get("body_sha256") != expected:
        raise DigestMismatchError(
            "body_sha256 does not match canonical GigAI-owned body bytes"
        )
    return (
        FRONT_MATTER_OPEN
        + canonical_json_bytes(metadata)
        + b"\n"
        + FRONT_MATTER_CLOSE
        + body_bytes
    )


def parse_json_front_matter(document: bytes) -> tuple[dict[str, Any], bytes]:
    """Parse front matter and require exact canonical metadata and body bytes."""

    if type(document) is not bytes:
        raise ExactBytesRequiredError("front-matter document must be exact bytes")
    if not document.startswith(FRONT_MATTER_OPEN):
        raise InvalidFrontMatterError("missing GigAI JSON front-matter opener")
    metadata_start = len(FRONT_MATTER_OPEN)
    metadata_end = document.find(b"\n" + FRONT_MATTER_CLOSE, metadata_start)
    if metadata_end < 0:
        raise InvalidFrontMatterError("missing GigAI JSON front-matter closer")

    metadata_bytes = document[metadata_start:metadata_end]
    metadata = parse_json_bytes(metadata_bytes)
    if type(metadata) is not dict:
        raise InvalidFrontMatterError("front matter must be a JSON object")
    if canonical_json_bytes(metadata) != metadata_bytes:
        raise InvalidFrontMatterError("front matter is not canonical JSON")

    body = document[metadata_end + 1 + len(FRONT_MATTER_CLOSE) :]
    try:
        body_text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidFrontMatterError("front-matter body is not UTF-8") from exc
    if canonicalize_owned_text(body_text) != body:
        raise InvalidFrontMatterError(
            "front-matter body is not canonical GigAI-owned text"
        )
    if metadata.get("body_sha256") != _sha256_digest(body):
        raise DigestMismatchError("front-matter body digest does not match")
    return metadata, body


def validate_entity_id(
    value: str,
    *,
    expected_prefix: EntityPrefix | None = None,
) -> str:
    """Require a known prefix and canonical lowercase RFC 9562 UUIDv4 text."""

    if type(value) is not str:
        raise InvalidIdentifierError("entity ID must be a string")
    match = ENTITY_ID.fullmatch(value)
    if match is None:
        raise InvalidIdentifierError(
            "entity ID must use a known prefix and lowercase UUIDv4"
        )
    actual_prefix = EntityPrefix(match.group("prefix"))
    if expected_prefix is not None and actual_prefix is not expected_prefix:
        raise InvalidIdentifierError(
            f"expected {expected_prefix.value!r} entity ID, got {actual_prefix.value!r}"
        )
    parsed = uuid.UUID(match.group("uuid"))
    if parsed.version != 4 or str(parsed) != match.group("uuid"):
        raise InvalidIdentifierError("entity ID UUID must be canonical lowercase UUIDv4")
    return value


def generate_entity_id(
    prefix: EntityPrefix,
    *,
    is_persisted: Callable[[str], bool],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> str:
    """Generate an ID, allowing three collision regenerations before failure.

    ``is_persisted`` must check both the authoritative workpad and the local
    registry before first persistence.
    """

    if type(prefix) is not EntityPrefix:
        raise InvalidIdentifierError("entity prefix must be an EntityPrefix")
    for _ in range(ID_COLLISION_RETRIES + 1):
        generated = uuid_factory()
        if type(generated) is not uuid.UUID or generated.version != 4:
            raise InvalidIdentifierError("UUID factory must return UUIDv4 values")
        candidate = f"{prefix.value}_{generated}"
        validate_entity_id(candidate, expected_prefix=prefix)
        if not is_persisted(candidate):
            return candidate
    raise IdentifierCollisionError(
        "entity ID collided after three regeneration attempts"
    )


def _positive_version(value: Any, *, field: str) -> int:
    if type(value) is not int or not 1 <= value <= SAFE_INTEGER_MAX:
        raise InvalidVersionError(
            f"{field} must be a positive interoperable-range integer"
        )
    return value


def resolve_gig_version(
    requested_version: int | None,
    *,
    active_version: int,
    approved_versions: Collection[int],
) -> int:
    """Resolve an explicit approved version or the explicit active pointer.

    Strings such as ``"latest"`` are invalid. Ordering of filenames,
    timestamps, IDs, or collection members never selects authority.
    """

    active = _positive_version(active_version, field="active_version")
    approved = frozenset(
        _positive_version(version, field="approved version")
        for version in approved_versions
    )
    if active not in approved:
        raise InconsistentActiveVersionError(
            "active_version does not name an approved Gig version"
        )
    if requested_version is None:
        return active
    requested = _positive_version(requested_version, field="requested_version")
    if requested not in approved:
        raise VersionNotApprovedError(
            "requested_version does not name an approved Gig version"
        )
    return requested


def require_supported_schema_version(
    version: str,
    *,
    supported_versions: Collection[str],
) -> str:
    """Accept only an exact schema version explicitly supported by a reader."""

    if type(version) is not str or version not in frozenset(supported_versions):
        raise UnsupportedSchemaVersionError(
            "serialized contract uses an unsupported schema version"
        )
    return version


__all__ = [
    "CanonicalizationError",
    "DigestMismatchError",
    "DuplicateJsonMemberError",
    "EntityPrefix",
    "ExactBytesRequiredError",
    "IdentifierCollisionError",
    "InconsistentActiveVersionError",
    "InvalidCanonicalValueError",
    "InvalidFrontMatterError",
    "InvalidIdentifierError",
    "InvalidJsonError",
    "InvalidOwnedTextError",
    "InvalidVersionError",
    "UnsupportedSchemaVersionError",
    "VersionNotApprovedError",
    "canonical_json_bytes",
    "canonical_json_digest",
    "canonicalize_owned_text",
    "digest_imported_bytes",
    "digest_owned_text",
    "generate_entity_id",
    "parse_json_bytes",
    "parse_json_front_matter",
    "render_json_front_matter",
    "require_supported_schema_version",
    "resolve_gig_version",
    "validate_entity_id",
]
