# CONTRACT-01: Decide the handoff front-matter contract, then align schema and writer

**Status:** Recorded 2026-09-22 from the S14 follow-up. Not started, and no
implementation is authorized.
**Owner/system:** GigAI journal (`journal.py`) and the packaged
`handoff-frontmatter.schema.json`.
**Priority:** Contract correctness. This isn't a release gate: nothing
validates handoffs against the file today, so the mismatch has no runtime
effect yet.

## Ticket

**Problem:** The packaged `handoff-frontmatter.schema.json` doesn't describe
what the journal writes. In one test run's workpads, **0 of 28** handoffs
conformed:

- `artifact_refs` is present on all 28, and the schema doesn't allow it.
- Several transition names the journal writes aren't in the schema's list
  (for example `gig_graph_set_proposed` and `workpad_layout_migrated`).
- External recording writes `actor.kind: "agent"`, which the schema doesn't
  allow.
- Keys such as `operation`, `operation_key`, `layout_marker` and
  `layout_version` appear, and the schema doesn't list them.

**Intended behavior:** One authoritative handoff contract that both the
schema and the writer follow. **First decide which fields, transitions and
actor kinds are intended.** Then align the schema and the writer to that
decision. **Don't blindly regenerate the schema from every current output**:
some current keys or values may be accidental and should be removed from the
writer rather than legitimized in the schema.

**Tasks:**

1. Inventory every transition name and front-matter key the journal can
   emit. Collect them from all `record_transition` call sites, not from one
   test sample.
2. For each key, transition and actor kind, record whether it is intended,
   and why. `artifact_refs` in particular is filled in by the journal itself
   (`journal.py:407-417`), so it is probably intended.
3. Decide whether the file should be enforced at write time, only in tests,
   or not at all. That decision determines what "aligned" means.
4. Propose the schema and writer changes. Name any change that affects
   hash-pinned tests (`test_g16_review_loop.py:34`,
   `test_scout02_independent.py:32`).

**Acceptance:** A reviewed intended-contract table. After alignment, the
chosen enforcement point validates handoffs produced by the full test suite,
not a single sample. Any key removed from the writer is listed.

**Evidence:** [S14 audit, F3 and F6](../v0.1.8/spikes/evidence/S14-schema-inventory-audit.md).
This is Claude's reported, executed result from one test file's scratch
workpads. **The operator hasn't independently reviewed it yet.**
