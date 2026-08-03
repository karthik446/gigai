# G01 — Canonical Serialization

- Status: Approved; blocked by G00
- Depends on: G00
- Unblocks: G03, G07

## Outcome

Provide the single production implementation for canonical GigAI-owned bytes,
imported exact-byte digests, front matter, IDs, version selection, and
compatibility checks without changing any frozen contract.

## In scope

- Port the proven canonicalization behavior from
  `research/contract_spike/canonical.py` into `src/gigai/canonical.py`.
- Preserve validate-before-render, duplicate-member rejection, exact front
  matter byte comparison, and the approved newline and text rules.
- Expose differently named APIs for canonicalizing GigAI-owned text and hashing
  imported exact bytes.
- Route every product canonical JSON rendering and exact-byte digest through
  the named canonical module.
- Implement the approved identifier and explicit-version selection rules.
- Document why ASCII member-name restrictions make Python code-point ordering
  equivalent to JCS UTF-16 ordering for the accepted domain.

## Out of scope

- Broadening or tightening the accepted member-name domain.
- Editing schemas or canonical vectors to fit an implementation.
- Workpad persistence, installed CLI scenarios, or schema-graph semantics.
- General-purpose JCS claims outside the accepted GigAI contract.

## Acceptance criteria

1. All existing canonical vectors pass byte-for-byte through production APIs.
2. Duplicate JSON members, invalid member names, non-canonical values, malformed
   front matter, and byte mismatches fail with typed, stable errors.
3. Imported bytes are never normalized before hashing.
4. GigAI-owned text and JSON use one documented canonical rendering path.
5. IDs and explicit version selection conform to the approved V14 rules and do
   not rely on lexical “latest.”
6. No second product module independently implements canonicalization or
   digest behavior.
7. Frozen schema and vector hashes remain unchanged.

## Verification and evidence

- Golden-vector compatibility tests using the shipped production APIs.
- Negative tests for duplicate members, invalid text, invalid front matter,
  normalization mistakes, and explicit-version failures.
- A source scan proving digest and canonical rendering ownership.
- Before/after frozen-contract hashes and a completion audit.

## Stop boundary

Stop and raise a contract-change decision if implementation suggests a frozen
schema or vector is wrong. Do not repair the evidence or expand into G03, G07,
or persistence work.
