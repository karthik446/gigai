# ADR 0002: Restrict canonical JSON and make byte ownership explicit

- Status: Accepted
- Date: 2026-08-03

## Context

GigAI derives identities and integrity evidence from JSON and text bytes. A
general-purpose JSON canonicalization profile would require broad,
cross-language agreement on member ordering, number rendering, and text
handling. That is more surface area than the V14 contract needs and makes a
small implementation disagreement an identity disagreement.

Imported content has a different risk: decoding and re-encoding it before
hashing can silently alter the user artifact the digest is meant to attest.

## Decision

GigAI accepts a restricted RFC 8785/JCS-compatible JSON domain for
identity-bearing values:

- Object names are ASCII identifiers. Python code-point ordering therefore
  equals JCS UTF-16 ordering for every accepted name.
- Duplicate names and floats are rejected. Integers are limited to the
  interoperable safe range; decimal quantities are normalized strings.
- Unicode string values are preserved exactly, without normalization.
- Canonical JSON is compact UTF-8 with no trailing line feed.

The API distinguishes who owns the bytes:

- `canonical_json_bytes` and `canonical_json_digest` handle accepted logical
  JSON.
- `canonicalize_owned_text` and `digest_owned_text` normalize text created by
  GigAI.
- `digest_imported_bytes` hashes imported `bytes` directly. Callers must not
  decode or re-encode an imported artifact first.

## Consequences

- GigAI gets deterministic cross-language identity within its stated domain,
  not a general-purpose JCS implementation.
- Unsupported numbers and member names fail explicitly instead of acquiring
  ambiguous canonical bytes.
- Visually equivalent but differently encoded Unicode strings intentionally
  have different identities.
- Callers must choose the correct ownership boundary before hashing. This
  preserves imported artifacts as evidence while allowing GigAI-owned text to
  have stable formatting.
- Broadening this domain or changing its byte rules requires an explicit
  contract decision and review of frozen schemas and canonical vectors.
