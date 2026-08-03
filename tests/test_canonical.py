from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from gigai.canonical import (
    DigestMismatchError,
    DuplicateJsonMemberError,
    EntityPrefix,
    ExactBytesRequiredError,
    IdentifierCollisionError,
    InconsistentActiveVersionError,
    InvalidCanonicalValueError,
    InvalidFrontMatterError,
    InvalidIdentifierError,
    InvalidJsonError,
    InvalidOwnedTextError,
    InvalidVersionError,
    UnsupportedSchemaVersionError,
    VersionNotApprovedError,
    canonical_json_bytes,
    canonical_json_digest,
    canonicalize_owned_text,
    digest_imported_bytes,
    digest_owned_text,
    generate_entity_id,
    parse_json_bytes,
    parse_json_front_matter,
    render_json_front_matter,
    require_supported_schema_version,
    resolve_gig_version,
    validate_entity_id,
)


VECTORS = (
    Path(__file__).parents[1]
    / "research"
    / "contract_spike"
    / "fixtures"
    / "canonical-vectors.json"
)


def test_frozen_vectors_match_production_bytes_and_digests() -> None:
    fixture = json.loads(VECTORS.read_text(encoding="utf-8"))
    for vector in fixture["vectors"]:
        assert canonical_json_bytes(vector["input"]) == vector[
            "canonical_utf8"
        ].encode("utf-8")
        assert canonical_json_digest(vector["input"]) == vector["sha256"]


def test_ascii_member_order_matches_restricted_jcs_order() -> None:
    value = {"a": 4, "_name": 3, "A": 2, "$name": 1}
    assert canonical_json_bytes(value) == b'{"$name":1,"A":2,"_name":3,"a":4}'


def test_unicode_is_preserved_without_normalization() -> None:
    composed = canonical_json_digest({"text": "é"})
    decomposed = canonical_json_digest({"text": "e\u0301"})
    assert composed != decomposed


@pytest.mark.parametrize(
    "value",
    [
        {"amount": 1.5},
        {"amount": 9_007_199_254_740_992},
        {"amount": -9_007_199_254_740_992},
        {"not-ascii-é": "value"},
        {"bad key": "value"},
        {1: "value"},
        {"tuple": (1, 2)},
    ],
)
def test_noncanonical_values_fail_with_stable_type(value: object) -> None:
    with pytest.raises(InvalidCanonicalValueError) as raised:
        canonical_json_bytes(value)
    assert raised.value.code == "invalid_canonical_value"


def test_cyclic_json_value_fails_with_stable_type() -> None:
    value: list[object] = []
    value.append(value)
    with pytest.raises(InvalidCanonicalValueError, match="cycles") as raised:
        canonical_json_bytes(value)
    assert raised.value.code == "invalid_canonical_value"


def test_duplicate_members_are_rejected_at_any_depth() -> None:
    with pytest.raises(DuplicateJsonMemberError) as raised:
        parse_json_bytes(b'{"outer":{"a":1,"a":2}}')
    assert raised.value.code == "duplicate_json_member"


@pytest.mark.parametrize("data", [b"\xff", b"{", b"{\"amount\":NaN}"])
def test_invalid_json_fails_closed(data: bytes) -> None:
    error = InvalidJsonError if data != b'{"amount":NaN}' else InvalidCanonicalValueError
    with pytest.raises(error):
        parse_json_bytes(data)


def test_json_parser_requires_exact_bytes() -> None:
    with pytest.raises(ExactBytesRequiredError) as raised:
        parse_json_bytes('{"a":1}')  # type: ignore[arg-type]
    assert raised.value.code == "exact_bytes_required"


def test_owned_text_has_one_canonical_rendering_and_digest() -> None:
    assert canonicalize_owned_text("line one\r\nline two\n\n") == (
        b"line one\nline two\n"
    )
    assert digest_owned_text("line one\rline two") == digest_imported_bytes(
        b"line one\nline two\n"
    )


@pytest.mark.parametrize("text", ["\ufeffnot allowed", "not\x00allowed", "\ud800"])
def test_invalid_owned_text_has_stable_error(text: str) -> None:
    with pytest.raises(InvalidOwnedTextError) as raised:
        canonicalize_owned_text(text)
    assert raised.value.code == "invalid_owned_text"


def test_owned_text_requires_text_not_bytes() -> None:
    with pytest.raises(InvalidOwnedTextError):
        canonicalize_owned_text(b"already bytes")  # type: ignore[arg-type]


def test_imported_bytes_are_hashed_without_normalization() -> None:
    crlf = b"line\r\n"
    lf = b"line\n"
    assert digest_imported_bytes(crlf) == f"sha256:{hashlib.sha256(crlf).hexdigest()}"
    assert digest_imported_bytes(lf) == f"sha256:{hashlib.sha256(lf).hexdigest()}"
    assert digest_imported_bytes(crlf) != digest_imported_bytes(lf)


def test_imported_digest_rejects_implicit_text_encoding() -> None:
    with pytest.raises(ExactBytesRequiredError) as raised:
        digest_imported_bytes("line\n")  # type: ignore[arg-type]
    assert raised.value.code == "exact_bytes_required"


def test_front_matter_round_trip_uses_canonical_body_digest() -> None:
    body = "Run paused for operator review.\r\n"
    body_digest = digest_owned_text(body)
    metadata = {
        "body_sha256": body_digest,
        "run_id": "run_77777777-7777-4777-8777-777777777777",
        "schema_version": "1.0",
    }
    document = render_json_front_matter(metadata, body)
    parsed_metadata, parsed_body = parse_json_front_matter(document)
    assert parsed_metadata == metadata
    assert parsed_body == b"Run paused for operator review.\n"


def test_front_matter_render_rejects_body_digest_mismatch() -> None:
    with pytest.raises(DigestMismatchError) as raised:
        render_json_front_matter({"body_sha256": "sha256:" + "0" * 64}, "body")
    assert raised.value.code == "digest_mismatch"


@pytest.mark.parametrize(
    ("document", "error"),
    [
        (b"body\n", InvalidFrontMatterError),
        (b"---gigai-json\n{}\nbody\n", InvalidFrontMatterError),
        (b"---gigai-json\n[]\n---\nbody\n", InvalidFrontMatterError),
        (
            b'---gigai-json\n{"schema_version": "1.0"}\n---\nbody\n',
            InvalidFrontMatterError,
        ),
        (
            b'---gigai-json\n{"body_sha256":"sha256:'
            + b"0" * 64
            + b'","schema_version":"1.0"}\n---\nbody\r\n',
            InvalidFrontMatterError,
        ),
        (
            b'---gigai-json\n{"body_sha256":"sha256:'
            + b"0" * 64
            + b'","schema_version":"1.0"}\n---\n\xff',
            InvalidFrontMatterError,
        ),
        (
            b'---gigai-json\n{"body_sha256":"sha256:'
            + b"0" * 64
            + b'","schema_version":"1.0"}\n---\nbody\n',
            DigestMismatchError,
        ),
    ],
)
def test_malformed_front_matter_fails_with_typed_error(
    document: bytes,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        parse_json_front_matter(document)


def test_front_matter_parser_requires_exact_bytes() -> None:
    with pytest.raises(ExactBytesRequiredError):
        parse_json_front_matter("document")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("prefix", "identifier"),
    [
        (EntityPrefix.PROJECT, "project_123e4567-e89b-42d3-a456-426614174000"),
        (EntityPrefix.GIG, "gig_123e4567-e89b-42d3-a456-426614174001"),
        (EntityPrefix.GIG_PROPOSAL, "gp_123e4567-e89b-42d3-a456-426614174002"),
        (EntityPrefix.GRAPH, "graph_123e4567-e89b-42d3-a456-426614174003"),
        (EntityPrefix.GOAL, "goal_123e4567-e89b-42d3-a456-426614174004"),
        (EntityPrefix.EDGE, "edge_123e4567-e89b-42d3-a456-426614174005"),
        (EntityPrefix.RUN, "run_123e4567-e89b-42d3-a456-426614174006"),
        (EntityPrefix.HANDOFF, "handoff_123e4567-e89b-42d3-a456-426614174007"),
        (EntityPrefix.INVOCATION, "inv_123e4567-e89b-42d3-a456-426614174008"),
    ],
)
def test_all_documented_entity_id_prefixes_are_accepted(
    prefix: EntityPrefix,
    identifier: str,
) -> None:
    assert validate_entity_id(identifier, expected_prefix=prefix) == identifier


@pytest.mark.parametrize(
    "identifier",
    [
        "G00",
        "run_123E4567-E89B-42D3-A456-426614174006",
        "run_123e4567-e89b-72d3-a456-426614174006",
        "unknown_123e4567-e89b-42d3-a456-426614174006",
    ],
)
def test_noncanonical_entity_ids_are_rejected(identifier: str) -> None:
    with pytest.raises(InvalidIdentifierError) as raised:
        validate_entity_id(identifier)
    assert raised.value.code == "invalid_identifier"


def test_entity_id_expected_prefix_is_enforced() -> None:
    with pytest.raises(InvalidIdentifierError):
        validate_entity_id(
            "run_123e4567-e89b-42d3-a456-426614174006",
            expected_prefix=EntityPrefix.GOAL,
        )


def test_entity_id_generation_retries_three_collisions() -> None:
    values = iter(
        [
            uuid.UUID("123e4567-e89b-42d3-a456-426614174000"),
            uuid.UUID("123e4567-e89b-42d3-a456-426614174001"),
            uuid.UUID("123e4567-e89b-42d3-a456-426614174002"),
            uuid.UUID("123e4567-e89b-42d3-a456-426614174003"),
        ]
    )
    checked: list[str] = []

    def is_persisted(candidate: str) -> bool:
        checked.append(candidate)
        return len(checked) <= 3

    generated = generate_entity_id(
        EntityPrefix.RUN,
        is_persisted=is_persisted,
        uuid_factory=lambda: next(values),
    )
    assert generated == "run_123e4567-e89b-42d3-a456-426614174003"
    assert len(checked) == 4


def test_entity_id_generation_fails_after_three_regenerations() -> None:
    with pytest.raises(IdentifierCollisionError) as raised:
        generate_entity_id(EntityPrefix.RUN, is_persisted=lambda _: True)
    assert raised.value.code == "identifier_collision"


def test_version_resolution_uses_explicit_active_pointer_or_requested_version() -> None:
    assert resolve_gig_version(None, active_version=2, approved_versions={1, 2}) == 2
    assert resolve_gig_version(1, active_version=2, approved_versions={1, 2}) == 1


@pytest.mark.parametrize("requested", ["latest", 0, -1, True])
def test_version_resolution_rejects_lexical_latest_and_invalid_values(
    requested: object,
) -> None:
    with pytest.raises(InvalidVersionError):
        resolve_gig_version(
            requested,  # type: ignore[arg-type]
            active_version=2,
            approved_versions={1, 2},
        )


def test_version_resolution_rejects_unapproved_explicit_version() -> None:
    with pytest.raises(VersionNotApprovedError) as raised:
        resolve_gig_version(3, active_version=2, approved_versions={1, 2})
    assert raised.value.code == "version_not_approved"


def test_version_resolution_rejects_inconsistent_active_pointer() -> None:
    with pytest.raises(InconsistentActiveVersionError) as raised:
        resolve_gig_version(None, active_version=3, approved_versions={1, 2})
    assert raised.value.code == "inconsistent_active_version"


def test_schema_version_support_is_exact_not_range_based() -> None:
    assert require_supported_schema_version("1.0", supported_versions={"1.0"}) == "1.0"
    for unsupported in ("1.1", "2.0", "latest"):
        with pytest.raises(UnsupportedSchemaVersionError) as raised:
            require_supported_schema_version(
                unsupported,
                supported_versions={"1.0"},
            )
        assert raised.value.code == "unsupported_schema_version"
