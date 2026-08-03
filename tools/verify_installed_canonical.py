from __future__ import annotations

from gigai.canonical import (
    EntityPrefix,
    ExactBytesRequiredError,
    canonical_json_bytes,
    canonical_json_digest,
    canonicalize_owned_text,
    digest_imported_bytes,
    resolve_gig_version,
    validate_entity_id,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    value = {"b": 1, "a": "€", "array": [3, 2, 1]}
    require(
        canonical_json_bytes(value) == b'{"a":"\xe2\x82\xac","array":[3,2,1],"b":1}',
        "installed canonical JSON bytes differ from the frozen vector",
    )
    require(
        canonical_json_digest(value)
        == "sha256:c13c4a77bd771168d7df43be9fe4694550d33e3577af6353ef69863b2f3d7b34",
        "installed canonical JSON digest differs from the frozen vector",
    )
    require(
        canonicalize_owned_text("line one\r\nline two\n\n")
        == b"line one\nline two\n",
        "installed owned-text canonicalization differs from the contract",
    )
    require(
        digest_imported_bytes(b"line\r\n")
        == "sha256:893e89e669b5a4f9e5136d565f51e341a0c5e5531816c9c1a806d90df66a45f4",
        "installed imported-byte digest normalized or changed its input",
    )
    try:
        digest_imported_bytes("line\n")  # type: ignore[arg-type]
    except ExactBytesRequiredError:
        pass
    else:
        raise SystemExit("installed imported-byte digest accepted text")

    run_id = "run_123e4567-e89b-42d3-a456-426614174006"
    require(
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN) == run_id,
        "installed entity-ID validation differs from the contract",
    )
    require(
        resolve_gig_version(None, active_version=2, approved_versions={1, 2}) == 2,
        "installed version resolution ignored the explicit active pointer",
    )
    require(
        resolve_gig_version(1, active_version=2, approved_versions={1, 2}) == 1,
        "installed version resolution ignored an explicit approved version",
    )
    print("verified installed GigAI canonical identity API")


if __name__ == "__main__":
    main()
