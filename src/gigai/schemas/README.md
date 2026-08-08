# GigAI v1 serialized contracts

**Status:** Phase 0 contract baseline
**Dialect:** JSON Schema Draft 2020-12
**Identity profile:** RFC 8785 JSON Canonicalization Scheme, restricted as
described below

These schemas close the serialized boundaries required before Phase 1. They are
the contract source for runtime models, validators, generated reference docs,
fixtures, and compatibility tests. Prose in the implementation plan explains
intent; these files define field names, types, required values, cardinality,
and enums.

The original eight Phase 0/1 resources remain byte-identical. The additive G15
resources define review bundles, contracts, findings, feedback, adjudications,
traces, and machine reports; their hashes are pinned separately in
`SHA256SUMS` and verified as a seventeen-resource set. The two additive G16
resources are `review-loop.schema.json` and `addressed-artifact.schema.json`.

## Production identity API

`gigai.canonical` is the sole shipped implementation of canonical JSON,
SHA-256 identity, GigAI-owned text normalization, canonical JSON front matter,
prefixed UUIDv4 IDs, and explicit version selection.

The naming boundary is deliberate:

- `canonical_json_bytes()` and `canonical_json_digest()` identify logical
  GigAI JSON values;
- `canonicalize_owned_text()` and `digest_owned_text()` apply the GigAI-owned
  UTF-8/LF/final-newline contract; and
- `digest_imported_bytes()` accepts bytes only and hashes them exactly as read,
  without implicit decoding, encoding, or normalization.

No other product module implements canonical rendering or SHA-256 identity.

## Files

- `common.schema.json` contains shared identifiers, digests, artifact
  references, actors, budgets, effects, usage, and errors.
- `gig-proposal.schema.json` defines the non-executable output of `create` and
  `improve`.
- `goal-graph.schema.json` defines the fixed DAG representation consumed by the
  scheduler.
- `active-gig-version.schema.json` defines the authoritative approved-version
  pointer advanced by proposal approval.
- `run-brief-frontmatter.schema.json` defines the JSON front matter embedded in
  `run-brief.md`.
- `run-manifest.schema.json` defines the sealed authority for one Run.
- `run-details.schema.json` defines the small materialized Run status/result
  record.
- `handoff-frontmatter.schema.json` defines the JSON front matter embedded in
  every text handoff.
- `review-bundle.schema.json` defines exact-byte review references and the
  redaction/tool-requirement envelope.
- `review-contract.schema.json` defines criteria, evidence, evaluator plans,
  and bounded review policy.
- `finding.schema.json`, `feedback.schema.json`, and
  `adjudication.schema.json` define evaluator findings and operator decisions.
- `trace.schema.json` and `report.schema.json` define replay identity and the
  machine report projection.

All top-level objects reject unknown fields. `schema_version` names one exact
contract version. An additive optional field creates a new minor schema version;
a breaking change creates a new major schema version. A reader accepts only the
exact versions it explicitly supports and returns `unsupported_schema_version`
for any other version. It never accepts, drops, and rewrites unknown fields.

## Canonical JSON and digests

Hash-bearing logical JSON values use RFC 8785 JCS encoded as UTF-8. GigAI narrows
the accepted domain so the baseline implementation does not depend on
cross-language floating-point formatting:

- object member names are ASCII identifiers matching
  `[A-Za-z_$][A-Za-z0-9_$]*` and are case-sensitive;
- duplicate member names, lone Unicode surrogates, NaN, and Infinity are
  invalid;
- JSON numbers in identity-bearing contracts are integers in the interoperable
  range `-9007199254740991..9007199254740991`;
- money, rates, ratios, and other decimal values are normalized decimal strings
  matching `0|[1-9][0-9]*` with an optional fractional part;
- JCS does not normalize Unicode; string values are preserved exactly;
- arrays preserve declared order;
- canonical JSON emits no insignificant whitespace and no trailing newline.

The digest of logical JSON is:

```text
sha256:<lowercase hexadecimal SHA-256 of canonical JCS UTF-8 bytes>
```

GigAI-owned Markdown and text use UTF-8 without BOM, LF line endings, no NUL,
and exactly one final LF. Their artifact digest is over those exact stored
bytes. Imported user files are never normalized silently; their digest is over
the exact bytes read.

JSON artifacts may be pretty-printed for inspection, but their `canonical_sha256`
is always computed from the parsed logical value. `content_sha256` always means
the exact stored bytes. A schema or field must say which identity it carries;
the two are never interchangeable.

## Markdown JSON front matter

`run-brief.md` and handoff `.txt` files begin with JSON front matter:

```text
---gigai-json
<one RFC 8785 canonical JSON object>
---
<human-readable body ending in one LF>
```

The opening delimiter must be the first bytes in the file. Delimiters use LF.
The metadata object contains `body_sha256`, which hashes only the normalized
human-readable body bytes. The sealed manifest or parent handoff carries the
whole-file `content_sha256`, avoiding a self-referential digest.

## Identifier contract

Persistent entity identifiers are an ASCII prefix, underscore, and canonical
lowercase RFC 9562 UUIDv4 text:

```text
project_123e4567-e89b-42d3-a456-426614174000
gig_123e4567-e89b-42d3-a456-426614174001
gp_123e4567-e89b-42d3-a456-426614174002
graph_123e4567-e89b-42d3-a456-426614174003
goal_123e4567-e89b-42d3-a456-426614174004
edge_123e4567-e89b-42d3-a456-426614174005
run_123e4567-e89b-42d3-a456-426614174006
handoff_123e4567-e89b-42d3-a456-426614174007
inv_123e4567-e89b-42d3-a456-426614174008
```

IDs are opaque, globally collision-resistant, and intentionally not sortable.
Creation timestamps and the journal sequence define ordering. Before first
persistence, a generated ID is checked against the authoritative workpad and
local registry; a collision is regenerated up to three times, then fails as an
entropy/runtime error. An ID is never reused after persistence.

Goal identity is stable across Gig versions. An unchanged Goal keeps its
`goal_id` and `goal_version`; a changed Goal keeps `goal_id` and increments
`goal_version`; a split or semantically new Goal receives a new `goal_id`.
Human labels such as `G00` are display ordinals and never identity.

Gig versions are positive integers allocated while holding the per-Gig writer
lock. Proposal approval writes `active-gig-version.json` and tags the same
private journal commit as `gig-v000001`, `gig-v000002`, and so on. `run` without
`--version` resolves this explicit active pointer, never a timestamp or lexical
"latest" guess.

## Journal ordering and locking

Every writer acquires one exclusive interprocess lock on
`.git/gigai-writer.lock` before reading the journal head, allocating a handoff
sequence, writing stable files, committing Git, and updating the rebuildable
SQLite index. The lock spans processes, not only threads in one process.

Lock acquisition has a monotonic 10-second default deadline and fails with
`interprocess_lock_unavailable` on timeout. Under the lock, normal allocation is
O(1): the writer reads the current commit's `GigAI-Handoff-Sequence` and
`GigAI-Handoff` trailers, validates that predecessor, and allocates the next
integer. SQLite is never the allocator. Directory scanning belongs only to
explicit journal reconciliation after inconsistent or interrupted state.

Handoff sequence is an unsigned per-Gig integer starting at 1 and formatted as
12 zero-padded decimal digits in filenames. It is allocated at durable journal
commit time. Concurrent Goal completions therefore receive strict commit order;
GigAI does not claim that repeated Runs will assign the same order to genuinely
simultaneous events. `parent_handoff_ids`, Run/Goal IDs, timestamps, and
invocation IDs preserve causal meaning.

The v1 POSIX backend uses `fcntl.flock`. Locks are advisory, so every GigAI
writer must participate. Setup/doctor must prove mutual exclusion and atomic
replacement on the selected workpad mount with two processes. If this cannot be
proved, writes and Runs fail closed with `interprocess_lock_unavailable`.
Windows is explicitly unsupported in v1; a future backend requires its own live
evidence before the platform claim changes.

## CLI resolution bound to these contracts

- `run <gig-id>` uses `active-gig-version.json`; `--version <positive-int>`
  selects an older approved version explicitly.
- `--goal <goal-id>` always resolves inside a selected Gig version or a sealed
  Run. A display ordinal such as `G00` is rejected in noninteractive use.
- `rehearse` and `eval` require Gig scope:
  `gigai rehearse <gig-id> --goal <goal-id> --case <name>` and
  `gigai eval <gig-id> --goal <goal-id> [--suite <name>]`.
- `run --wait`, `wait`, and `continue --wait` return when the Run is terminal or
  reaches `waiting_for_gate`. A gate pause returns command exit 0 because the
  wait operation succeeded; structured status and `next_actions` distinguish it
  from terminal success. Failed, cancelled, interrupted, or blocked Runs return
  exit 1; invalid usage returns 2; noninteractive `needs_input` returns 3.

## Validation beyond JSON Schema

JSON Schema cannot prove graph reachability, acyclicity, edge referential
integrity, compatible parallel effects, digest correctness, valid Git tags, or
journal continuity. The Phase 0 conformance script performs those semantic
checks for the fixtures, and Phase 1 must implement them as named validators.
