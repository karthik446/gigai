# G01 Completion Audit

- Goal: [G01 — Canonical Serialization](../../../goals/phase-1/G01-canonical-serialization.md)
- Date: 2026-08-03
- Result: Pass
- Verification host: macOS 26.5.2, arm64
- Package version: 0.0.0
- uv version: 0.5.25

## Outcome

GigAI now has one shipped implementation for restricted canonical JSON,
GigAI-owned text, exact imported-byte digests, canonical JSON front matter,
prefixed lowercase UUIDv4 entity IDs, exact schema-version compatibility, and
explicit active or requested Gig-version selection.

The implementation lives in `src/gigai/canonical.py`. Research code remains
evidence, not a supported product API.

## Acceptance reconciliation

### 1. Frozen canonical vectors pass through production APIs

Pass. `tests/test_canonical.py` loads the unchanged
`research/contract_spike/fixtures/canonical-vectors.json` and passes every
input through the shipped `canonical_json_bytes()` and
`canonical_json_digest()` APIs. All canonical UTF-8 bytes and prefixed SHA-256
digests match byte-for-byte.

The installed-wheel verifier independently checks the first frozen vector
through the wheel-installed module.

### 2. Invalid inputs fail with typed, stable errors

Pass. `CanonicalizationError` has specific subclasses with stable `code`
values for:

- invalid canonical values and member names;
- duplicate JSON members and invalid UTF-8 JSON;
- invalid GigAI-owned text;
- malformed front matter and digest mismatches;
- non-byte imported input;
- invalid identifiers and exhausted collision retries;
- invalid, unapproved, or inconsistent Gig versions; and
- unsupported exact schema versions.

Negative tests cover floats, out-of-range integers, invalid member names,
unsupported Python values, recursive containers, duplicate nested members,
malformed JSON, BOM/NUL/surrogate text, malformed and noncanonical front matter,
body-byte mismatches, invalid entity IDs, and invalid version selection.

### 3. Imported bytes are never normalized

Pass. `digest_imported_bytes()` accepts `bytes` only and hashes them directly.
It rejects text rather than choosing an implicit encoding. Tests prove CRLF and
LF inputs have different identities and compare the CRLF result to an
independent `hashlib.sha256` digest.

The installed-wheel verifier confirms the exact digest of `b"line\r\n"` and
confirms that text input is rejected.

### 4. GigAI-owned text and JSON have one documented path

Pass. `gigai.canonical` documents the restricted JCS profile and the reason
Python key ordering is equivalent to JCS UTF-16 ordering for the accepted ASCII
member-name domain.

The root README and packaged schema README document these distinct APIs:

- `canonical_json_bytes()` and `canonical_json_digest()` for logical JSON;
- `canonicalize_owned_text()` and `digest_owned_text()` for GigAI-owned text;
  and
- `digest_imported_bytes()` for exact imported bytes.

Canonical JSON renders without insignificant whitespace or a trailing newline.
Owned text renders as strict UTF-8, LF, no BOM or NUL, and exactly one final LF.

### 5. IDs and explicit version selection conform to V14

Pass within the G01 boundary.

- All nine documented entity prefixes validate only canonical lowercase RFC
  9562 UUIDv4 text.
- Display ordinal `G00`, uppercase UUID text, UUIDv7 text, unknown prefixes, and
  prefix mismatches are rejected.
- Generation checks the caller-supplied authoritative persistence predicate,
  permits three collision regenerations, and then fails closed.
- `resolve_gig_version()` selects only the explicit active pointer or an
  explicit approved positive integer.
- Lexical `"latest"`, booleans, nonpositive values, unapproved versions, and an
  inconsistent active pointer are rejected.
- `require_supported_schema_version()` accepts only versions explicitly named
  by the reader and reports `unsupported_schema_version` otherwise.

G01 provides lexical identity, collision, and selection primitives. G06 owns
version allocation under the per-Gig writer lock. G07 owns cross-artifact Goal
identity and graph semantics; those behaviors are explicitly outside G01's
schema-graph boundary.

### 6. Canonicalization and SHA-256 have one product owner

Pass. Product-source tests parse every Python module under `src/gigai/` and
prove that no module other than `canonical.py` imports `hashlib`, calls SHA-256,
or defines a canonical API entry point.

The final source scan found:

- one `hashlib` import;
- one SHA-256 call;
- one `json.dumps` call; and
- one definition of every canonical API function;

all in `src/gigai/canonical.py`.

### 7. Frozen schema and vector hashes are unchanged

Pass. `shasum -a 256 -c SHA256SUMS`, executed from
`src/gigai/schemas/`, reported `OK` for all eight frozen schema JSON files.

The canonical-vector digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

No frozen schema or vector was edited.

## Additional verification

### Locked source matrix

| Interpreter | pytest | Collected | Result |
|---|---:|---:|---|
| CPython 3.11.9 | 9.1.1 | 90 | 90 passed |
| CPython 3.12.8 | 9.1.1 | 90 | 90 passed |
| CPython 3.13.1 | 9.1.1 | 90 | 90 passed |

Each run used the committed lockfile and the CI command shape:

```text
uv sync --locked --extra test --python <python-version>
uv run --locked --python <python-version> pytest
```

### Built artifact

`uv build` produced the wheel and source distribution. The wheel contains 18
entries: the prior G00 package inventory plus only `gigai/canonical.py`. It
contains no tests or research and declares no runtime dependency.

Installed without dependencies into a fresh CPython 3.11.9 environment, it
reported:

```text
verified 8 installed GigAI schemas
verified installed GigAI canonical identity API
```

`uv lock --check` also passed with the existing 24-package development lock.

## Completion decision

G01 is complete. No acceptance criterion is waived, no frozen contract byte is
changed, and the implementation does not expand into CLI, persistence, journal,
or graph-validation behavior.

Hosted CI on the exact G01 commit remains the publication confirmation gate.
No downstream dependency is operationally released until that pushed commit
passes all source-matrix and wheel jobs.
