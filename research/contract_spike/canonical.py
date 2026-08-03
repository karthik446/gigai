from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SAFE_INTEGER_MAX = 9_007_199_254_740_991
MEMBER_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
FRONT_MATTER_OPEN = b"---gigai-json\n"
FRONT_MATTER_CLOSE = b"---\n"


class CanonicalizationError(ValueError):
    pass


def _validate(value: Any) -> None:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise CanonicalizationError("string contains an invalid Unicode surrogate") from exc
        return
    if isinstance(value, int):
        if not -SAFE_INTEGER_MAX <= value <= SAFE_INTEGER_MAX:
            raise CanonicalizationError("integer is outside the JCS interoperable range")
        return
    if isinstance(value, float):
        raise CanonicalizationError("identity-bearing GigAI JSON forbids floating-point numbers")
    if isinstance(value, list):
        for item in value:
            _validate(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not MEMBER_NAME.fullmatch(key):
                raise CanonicalizationError(f"invalid canonical member name: {key!r}")
            _validate(item)
        return
    raise CanonicalizationError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return RFC 8785-compatible bytes for GigAI's restricted JSON domain."""

    _validate(value)
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

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CanonicalizationError(f"duplicate JSON member name: {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalizationError("invalid UTF-8 JSON") from exc
    _validate(value)
    return value


def sha256_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def canonical_json_digest(value: Any) -> str:
    return sha256_digest(canonical_json_bytes(value))


def normalize_owned_text(text: str) -> bytes:
    if "\x00" in text:
        raise CanonicalizationError("GigAI-owned text cannot contain NUL")
    if text.startswith("\ufeff"):
        raise CanonicalizationError("GigAI-owned text cannot begin with a UTF-8 BOM")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n") + "\n"
    return normalized.encode("utf-8", errors="strict")


def render_json_front_matter(metadata: dict[str, Any], body: str) -> bytes:
    body_bytes = normalize_owned_text(body)
    expected = sha256_digest(body_bytes)
    if metadata.get("body_sha256") != expected:
        raise CanonicalizationError("body_sha256 does not match normalized body bytes")
    return FRONT_MATTER_OPEN + canonical_json_bytes(metadata) + b"\n" + FRONT_MATTER_CLOSE + body_bytes


def parse_json_front_matter(document: bytes) -> tuple[dict[str, Any], bytes]:
    if not document.startswith(FRONT_MATTER_OPEN):
        raise CanonicalizationError("missing GigAI JSON front-matter opener")
    metadata_start = len(FRONT_MATTER_OPEN)
    metadata_end = document.find(b"\n" + FRONT_MATTER_CLOSE, metadata_start)
    if metadata_end < 0:
        raise CanonicalizationError("missing GigAI JSON front-matter closer")
    metadata_bytes = document[metadata_start:metadata_end]
    metadata = parse_json_bytes(metadata_bytes)
    if not isinstance(metadata, dict):
        raise CanonicalizationError("front matter must be a JSON object")
    if canonical_json_bytes(metadata) != metadata_bytes:
        raise CanonicalizationError("front matter is not canonical JSON")
    body = document[metadata_end + 1 + len(FRONT_MATTER_CLOSE) :]
    try:
        body_text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CanonicalizationError("front-matter body is not UTF-8") from exc
    if normalize_owned_text(body_text) != body:
        raise CanonicalizationError("front-matter body is not normalized GigAI text")
    if metadata.get("body_sha256") != sha256_digest(body):
        raise CanonicalizationError("front-matter body digest does not match")
    return metadata, body
