# SCOUT-02 — Review correction brief

**Date:** 2026-09-08  
**Status:** Accepted; see [completion evidence](SCOUT-02-completion.md).  
**Owner:** Terra implementation; Claude independent re-review; Luna verification.

Orca task `task_6ad58e659829` / dispatch `ctx_f79e69f40791` completed with a
[correction handoff](SCOUT-02-review-corrections-implementation.md). Its report
was read and completion acknowledged; release retained the user-owned terminal
without process action. Claude re-review (`ctx_5059c2d9347d`) and Luna independent
verification (`ctx_4c67b1df3595`) subsequently completed successfully. The
coordinator's additional negative-path and two-graph execution checks also
passed; the linked completion record states the acceptance limits.

The operator requested continuing review corrections after recording schema
simplification as [v0.1.8 Spike 5](../../../v0.1.8/spikes/README.md#spike-5--schema-simplification-and-redesign).
That redesign is deferred; these are corrections within accepted A02, not a
replacement contract. SCOUT-03 remains waiting on SCOUT-02 acceptance.

## Inputs and ownership

Read the [independent review](SCOUT-02-independent-review.md),
[implementation plan](SCOUT-02-implementation-plan.md),
[caller audit](SCOUT-02-caller-audit.md), accepted SCOUT-00 contracts/amendment,
and Luna's `SCOUT-02-verification.md` when available. Reproduce findings rather
than treating source-derived review examples as executed tests.

Terra owns bounded source/schema/caller fixes, its new regression tests, schema
inventories, and `SCOUT-02-review-corrections-implementation.md`. Preserve old
schema bytes, existing dirty G43.1 work, and coordinator-owned
`tests/test_scout02_independent.py`. Do not edit independent reports, baseline
approvals, private Gigs, v0.1.8 docs, or the coordinator ledger. No provider calls,
commits, tags, publication, new schema family, or general storage redesign.

## Required corrections

1. Restore strict nested validation for v2 Plans and Run manifests using the
   existing v1 definitions where compatible. Preserve v1 bytes/identity. Test
   unknown keys, missing required fields, wrong types/enums, and malformed array
   members in each affected nested shape. Validate both serialized schemas and
   public read paths; synchronize packaged checksums and verifier inventories.
2. Reproduce v2 listing, portability, comparison, and occurrence behavior with
   disposable approved two-graph Gigs. Make listing show valid identity/title/status;
   use version-aware readers where supported and deliberate typed unsupported-version
   diagnostics where support is outside scope. Do not mislabel a valid sealed
   pointer as unsealed. Cover every named reader, including occurrence comparison,
   and guard against comparing different selected graphs as equivalent implicitly.
3. Add the seven existing new schemas to the schema README, with purpose and
   version relationships. Keep catalogue, registry, checksums, fixtures, and
   installed-package verification consistent; do not invent more schemas to fix it.
4. Resolve the agent-explicit provenance gap at the actual selection acceptance
   boundary. A syntactically valid artifact reference or path prefix alone is not
   authenticated invocation evidence. Bind exact journaled invocation bytes and
   actor, with tamper/missing/mismatch tests, using existing authority primitives.
   If the accepted contract cannot be met without expanding scope, report the
   precise blocker; do not claim unsupported provenance is verified or silently
   change the accepted selection modes. Selection remains separate from consent.
5. Correct the v1-only manifest check in provider-review recovery at the v2 seam,
   preserving existing G43.1 behavior. Add an offline/fake-adapter regression for
   abandoned v2 provider-review recovery; this is not authorization for live calls.

## Verification and handoff

Luna's [completed verification](SCOUT-02-verification.md) reported 74 focused
passes and genuine isolated-wheel success. The full run returned 729 passes,
15 failures, six errors, and one skip. Five socket-related failures subsequently
passed in an 18-test permission-enabled rerun. The remaining deterministic
findings are nine stale 37-resource assertions, six contract-spike schema setup
errors, and one bare-command diagnostic expectation mismatch.

Include those inventory/fixture/CLI expectation corrections in this task after
checking actual accepted command behavior. Preserve legacy hash and semantic
assertions; do not merely delete tests or loosen validation to make the suite pass.
The schema spike needs valid examples for new boundaries, not just a changed count.
Distinguish original results from post-correction results and environment failures.

Run focused regressions, compile/Ruff, schema inventory checks, and the complete
offline suite (`GIGAI_G30_UAT=0 .venv/bin/pytest`). Verify a genuinely installed,
newly built wheel in a disposable environment without replacing the project venv.
Report exact results, failures, environment limits, and changed files. Invoke GigAI
directly, never through RTK. Do not classify editable-source checks as wheel proof.

After the implementation handoff, Claude re-reviews the changes and Luna independently
verifies them. The coordinator waits for each worker's handoff before examining its
in-flight work or running competing tests. No acceptance is implied by this brief.
