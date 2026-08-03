# GigAI Phase 0 serialized-contract closure spike

- **Date:** 2026-08-02
- **Status:** complete; binding input to implementation-plan revision 14
- **Scope:** serialization, identity, CLI resolution, journal ordering, and
  contract fixtures only
- **Runtime used:** macOS, Python 3.11.9, `jsonschema` 4.23.0

## 1. Why this spike exists

The implementation plan correctly defined authority and lifecycle but still
left several independent implementations free to produce incompatible bytes:

1. schemas were prose field lists rather than executable contracts;
2. hash-bearing JSON had no exact canonical encoding;
3. identifiers and approved-version resolution were examples rather than
   rules;
4. `rehearse`, `eval`, and `--wait` had ambiguous scope or terminal behavior;
5. a process-local writer lock could not serialize the CLI and worker, which
   are separate processes.

Those are Phase 0 contract questions, not implementation preferences. This
spike resolves them before repository creation.

## 2. Decisions

### 2.1 Schemas: JSON Schema Draft 2020-12

GigAI uses [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)
for serialized boundary validation. The executable schemas are in
`src/gigai/schemas/` and cover:

- Gig Proposal;
- active approved Gig version;
- Goal Graph;
- Run Brief JSON front matter;
- sealed Run manifest;
- `RunDetails`;
- handoff JSON front matter;
- shared IDs, digests, actors, budgets, effects, usage, errors, and artifact
  references.

All boundary objects list required fields and reject unknown fields. Schema
validation is necessary but not sufficient: a named semantic validator must
also prove graph acyclicity, reachability, edge references, join validity,
effect compatibility, budgets, digest correctness, and journal continuity.

Why: JSON Schema makes missing fields, cardinality, enums, formats, references,
and compatibility policy executable. It deliberately does not pretend to be a
graph or journal-consistency language.

### 2.2 Canonical bytes: restricted RFC 8785 JCS

Hash-bearing logical JSON uses the
[RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)
encoded as UTF-8, narrowed to this v1 domain:

- ASCII identifier object member names matching
  `[A-Za-z_$][A-Za-z0-9_$]*`;
- no duplicate members;
- no floating-point numbers;
- integers only within `-(2^53)+1..(2^53)-1`;
- decimal values such as money represented as normalized decimal strings;
- Unicode string values preserved exactly, with no normalization;
- no insignificant whitespace or trailing LF in canonical JSON;
- `sha256:<lowercase-hex>` digests.

The ASCII-name restriction makes Python's lexical key ordering identical to
JCS's required member ordering for GigAI contracts. Rejecting floats avoids
cross-language number-rendering differences while keeping interoperable JSON
integers. The composed `é` and decomposed `e` plus combining acute accent
intentionally produce different hashes because JCS preserves Unicode as
received.

GigAI-owned text uses UTF-8 without BOM, LF line endings, no NUL, and exactly
one final LF. Imported user files are not normalized silently; their digest is
over the exact bytes read.

Run Brief and handoff text begins with canonical JSON front matter:

```text
---gigai-json
<one canonical JSON object>
---
<human-readable body ending in one LF>
```

The metadata carries the body digest. The parent manifest carries the full-file
digest, avoiding self-reference.

### 2.3 IDs and version selection

Persistent IDs use an entity prefix plus canonical lowercase RFC 9562 UUIDv4,
for example `run_77777777-7777-4777-8777-777777777777`. UUIDv4 is deliberately
opaque and non-sortable. [RFC 9562](https://www.rfc-editor.org/rfc/rfc9562.html)
also defines time-ordered UUIDv7, but Python added `uuid.uuid7()` only in 3.14;
the current spike baseline is Python 3.11.9. UUIDv4 therefore avoids a new
runtime or dependency merely to encode order. Ordering belongs to timestamps
and the journal sequence, not identity.

ID generation checks the authoritative workpad and local registry before first
persistence. A collision is regenerated up to three times, then fails closed.
Persisted IDs are never reused.

Goal IDs are stable across Gig versions. A changed Goal increments
`goal_version`; a semantically new or split Goal receives a new ID. `G00` is a
display ordinal, not an ID.

Proposal approval allocates a positive Gig version under the per-Gig writer
lock, writes `active-gig-version.json`, and tags the same journal commit
`gig-v000001`. `gigai run <gig-id>` resolves that explicit active pointer.
`--version <positive-int>` is required to run another approved version; neither
timestamp nor filename sorting selects authority.

### 2.4 CLI scope and wait behavior

Goal-only commands require Gig scope:

```text
gigai rehearse <gig-id> --goal <goal-id> --case <name> [--version <version>]
gigai eval <gig-id> --goal <goal-id> [--suite <name>] [--version <version>]
```

`run --wait`, `wait`, and `continue --wait` return when the selected Run is
terminal or reaches `waiting_for_gate`. A healthy gate pause returns exit 0
because waiting succeeded; structured status and `next_actions` distinguish it
from terminal success. Failed, blocked, cancelled, or interrupted Runs return
1; invalid command use returns 2; unresolved noninteractive questions return
3.

Capability input and output models are capability-local public models imported
from the pack's `.models` module. They are not undeclared root exports. The
plan's capability example now makes those imports explicit.

### 2.5 Journal lock and handoff ordering

The CLI, background worker, and recovery process are independent writers, so a
thread or process-local mutex is insufficient. Every writer takes an exclusive
interprocess advisory lock at `.git/gigai-writer.lock` before reading the head,
allocating a handoff sequence, replacing stable files, committing, and updating
the rebuildable index.

The POSIX proof uses Python's
[`fcntl.flock`](https://docs.python.org/3.12/library/fcntl.html#fcntl.flock).
V1 supports the proven POSIX backend and explicitly defers Windows rather than
shipping an untested equivalent. The lock is advisory, so every GigAI writer
participates. Production acquisition adds a bounded timeout and reads the
committed journal-head trailers for O(1) allocation; directory scanning remains
reconciliation-only. Stable file replacement follows the same mutual-exclusion
and atomic-replacement principle documented by [Git's lockfile
API](https://git-scm.com/docs/api-lockfile).

Handoff sequence is per Gig, starts at 1, and is rendered as 12 zero-padded
digits. Sequence allocation occurs at durable journal commit time. Parallel
Goals therefore receive a strict commit order, but GigAI does not claim that
genuinely simultaneous completions will receive the same sequence across
repeated Runs. IDs and parent handoffs preserve causality.

`setup` and `doctor` must run a two-process mutual-exclusion and atomic-replace
probe on the selected mount. A mount that cannot prove the required behavior
fails writes and Runs with `interprocess_lock_unavailable`.

## 3. Executable evidence

The proof package is `research/contract_spike/`.

| Evidence | What it proves |
|---|---|
| `fixtures/canonical-vectors.json` | member ordering, UTF-8 bytes, stable SHA-256, and no Unicode normalization |
| `canonical.py` | restricted canonical JSON, duplicate-key rejection, owned-text normalization, and JSON front-matter round trip |
| `graph_validation.py` | reference, outcome, cycle, reachability, and basic join checks beyond JSON Schema |
| `journal_lock.py` | POSIX interprocess exclusion, 12-digit allocation, fsync, and atomic replacement |
| `tests/test_schemas.py` | all eight schema documents are valid and every serialized boundary has a valid canonical instance |
| `tests/test_journal_lock.py` | eight processes concurrently create 40 distinct handoffs numbered exactly 1 through 40 |

Current standalone-repository reproduction from the repository root:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

The original contract-only run on 2026-08-02 reported:

```text
Ran 14 tests in 0.117s

OK
```

The unified standalone source gate now includes those 14 contract tests and 17
Phase 0 tests.

Negative cases prove rejection of duplicate members, floats, unsafe integers,
non-ASCII member names, BOM/NUL text, noncanonical front matter, malformed IDs,
missing required fields, unknown fields, graph cycles, and unreachable required
Goals.

## 4. Implementation consequences

Phase 1 must use these schema files and golden vectors as compatibility tests,
not rewrite them from prose. Generated runtime models may add convenience
methods but cannot reinterpret field identity or defaults.

The lock spike proves the POSIX mechanism on this local filesystem. It does not
prove every network filesystem, bounded timeout behavior, or the production
committed-head allocator. Those are explicit G06 requirements. Mount capability
remains a live `setup`/`doctor` gate rather than a portability claim.

UUID collision checking is defense in depth, not a claim that random UUIDs are
sequential. Handoff sequence and explicit active-version records remain the
only ordering and version-authority mechanisms.

## 5. Exit verdict

The previously blocking serialized contracts are now implementable to the same
bytes by two independent builders. The final revision-14 harness commit records
operator approval. Phase 1 starts only after the contract package is copied to
the standalone repository and materialized as its verifiable Goal Graph.
