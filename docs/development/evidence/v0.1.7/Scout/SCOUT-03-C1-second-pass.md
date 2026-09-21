# SCOUT-03 C1 — Second storage correction pass

**Status:** Ready for bounded implementation; C2 remains blocked.  
**Inputs:** [C1 contract](SCOUT-03-correction-plan.md),
[coordinator probes](SCOUT-03-C1-coordinator-verification.md),
[independent Claude review](SCOUT-03-C1-review.md).

The coordinator read the full independent review and source. Fix the existing
accepted C1 behavior; this is not a new product-design gate. Preserve the first
pass's useful locked CAS, authenticated-byte and in-place database primitives.

## Additional confirmed recovery regressions

Two further tests are now in `tests/test_scout03_c1_acceptance.py`:

```text
.venv/bin/python -m pytest -q tests/test_scout03_c1_acceptance.py \
  -k 'legacy_mutable or interrupted_layout'
2 failed, 6 deselected in 1.87s
```

- A previously supported mutable journal artifact update interrupted at
  `after_transaction_prepare` cannot recover: `_restore_transaction` now
  applies Scout no-clobber semantics to every non-migration transition.
- Migration interrupted at `after_artifact_replace` cannot reach recovery:
  `reconcile_journal` calls normal layout validation first, which rejects the
  uncommitted v2 marker before reading the prepared migration transaction.

The first exploratory migration fixture accidentally reused an existing v2
Gig and did not hit its failpoint. That fixture was corrected to use a fresh
v1 Gig before the result above; only the corrected result proves this defect.

There are now eight coordinator regression tests, all with reproduced failures
in the initial C1 handoff. Keep the assertions; do not skip/xfail or weaken them.

## Required implementation order

1. **One committed snapshot of the relevant private families.** Under the
   journal writer lock, capture HEAD, enumerate committed imports, operations
   and revisions, and resolve the parent chain from that set. Reuse this for
   listing, equivalent imports, key lookup, CAS and rebuild. Authenticate exact
   publication, canonical identities/paths, complete content references and
   scope. Check every working path component and its bytes against the pinned
   evidence; diagnose missing, redirected, changed or extra uncommitted private
   evidence rather than silently omitting it. Do not require unrelated editable
   Gig source/drafts to be globally Git-clean.
2. **Original-request replay.** Hash caller-supplied semantic intent before
   allocating IDs. Explicit record IDs remain part of intent; omitted IDs do
   not become random conflict inputs. Matching requests return the original
   IDs and strict receipt; changed actor/origin/media/content/parent conflict.
   Verify equivalent-import handling across distinct keys does not forget a
   previously accepted key or permit duplicate content publication. Keep all
   decision/publication checks inside the critical section.
3. **Exact content read.** Validate the wrapper's complete selected record and
   snapshot references against canonical committed originals, then return those
   authenticated bytes. Never validate Git bytes and subsequently return an
   unchecked working-file read. Reject tampered working evidence as C1 requires.
4. **Coherent managed views.** Build the Scout records AND operation-receipt
   projection at one pinned HEAD. Serialize the database and context publication
   in documented journal-then-database lock order. Validate a closed database
   object inventory and full table shape under that lock: unexpected triggers,
   views, indexes/constraints or malformed tables cannot execute supplied SQL
   or delete G22 history. Preserve supported internal indexes and current G22
   connections. Do not replace the database inode. Use safe unique context
   staging and validate all parent components. Cursor mismatch must repair or
   report staleness, not remain a decorative metadata field.
5. **Post-commit result.** Once publication succeeds, a projection exception
   yields committed plus pending/rebuild metadata on the operation result.
   The receipt object and its committed bytes stay unchanged and schema-valid.
   Retry/rebuild repairs without another domain event. Do not swallow process
   termination or hide the underlying projection diagnostic.
6. **Recoverable layout and legacy writes.** Normal admission remains strict.
   Only explicit recovery may validate a prepared, scope-bound migration intent
   against the committed v1 parent and exact marker/ignore artifacts before
   completing it. Never turn an arbitrary uncommitted marker into authority.
   Restore the original supported replacement behavior for legacy mutable
   transactions while retaining no-clobber for immutable private records.
   Test interruption before and after artifact publication and ambiguity/refusal
   paths. Preserve ignored drafts and refuse new-root collisions safely.

## Review disposition and non-goals

- Claude F1–F6 are accepted, with the coordinator's stricter divergence check
  and actual failing tests. Generated revision IDs are not currently included
  in `create_record`'s hash; generated **record** ID is the confirmed replay bug.
- R1 (unpinned HEAD), R6 (validation before lock) and R7 (empty operation table)
  are C1 obligations, not C2 deferrals. The reproduced trigger-loss case and
  post-commit result failures also remain mandatory despite their omission
  from the independent report.
- The independent report's recovery-branch praise is superseded by the two
  concrete recovery probes above. Do not assume the presence of recovery code
  proves the public recovery path can reach it.
- R3 must compare actual v1/v2 roots: `tools/` and `reports/` already exist in
  the v1 semantic allowlist. Do not reject legitimate legacy state or apply a
  blanket collision ban to all v2 roots.
- R2 does not justify breaking standalone/in-memory interview traces. They do
  not share a workpad database. Prove locking and preservation for actual
  file-backed shared G22/HTTP connections; do not invent a lock requirement
  for an unrelated in-memory database.
- R4 byte-mode Git reads are appropriate for exact authentication. R5 caching
  optimization is not required in this pass.
- No C2 native/default/archive behavior, C3 approved tools/Plan inputs, new
  initialization, external executor, provider calls, UI, release or private
  user-data changes. No broad schema redesign.

## Evidence required before handoff

Run all eight coordinator tests and existing focused C1 suites. Add tests for
missing/hidden revision CAS, exact reference mismatches and nested symlinks,
equivalent import concurrency/key reuse, coherent HEAD/cursor under concurrent
publication, unknown schema objects, long-lived G22 trace preservation, and
migration interruption/recovery without relaxing ordinary admission. Assert
commit counts and preserved bytes. Scoped lint/compile/schema inventory must
pass; report exact commands/results in `SCOUT-03-C1-second-implementation.md`.
Do not run the full suite; the coordinator will schedule it once writers stop.

One runtime writer. Finish code, tests and evidence before reporting completion.
If an obligation remains unimplemented, report it explicitly rather than
claiming the entire C1 contract is complete.
