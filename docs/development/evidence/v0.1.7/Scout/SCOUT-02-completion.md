# SCOUT-02 — Multi-graph foundation acceptance

**Date:** 2026-09-08  
**Coordinator verdict:** Accepted for SCOUT-02 / G43.2's amended A02 foundation.  
**Release status:** Scout domain workflows and v0.1.7 release remain incomplete.

## Evidence

- Terra's [correction handoff](SCOUT-02-review-corrections-implementation.md)
  addresses the five original findings without rewriting v1 schema bytes.
- Claude's [independent re-review](SCOUT-02-corrections-rereview.md) accepts
  A02/the five corrections. Its remaining non-blocking notes have explicit
  [dispositions and G44 owners](SCOUT-02-corrections-review-disposition.md).
- Luna's [independent verification](SCOUT-02-corrections-verification.md)
  reports 209 focused passes; full suite 747 passed, five loopback-permission
  failures, one deliberate provider skip, 94 subtests. The affected group
  passed all 18 tests with permission. A new isolated wheel passed installed
  schema, canonical, and CLI checks from temporary `site-packages`.
- After both workers finished, the coordinator added 15 independent acceptance
  cases in `tests/test_scout02_acceptance_negative_paths.py`. They cover altered,
  missing, foreign, symlinked, and traversal member references; alias collision;
  descriptor and actual-graph budget widening; missing/unknown selection; Run
  consent refusal; unjournaled invocation refusal; foreign Gig/version/Graph Set/
  selected-graph binding; and independent Career and Stock Runs under one v2 Gig.
  Public refusal tests assert the journal HEAD, proposal, active pointer, and
  Run directory identities remain unchanged. All fixtures are disposable.

Final coordinator command:

```text
.venv/bin/pytest -q tests/test_scout02_acceptance_negative_paths.py tests/test_scout02_independent.py tests/test_scout02_graph_set_flow.py tests/test_scout02_review_corrections.py
33 passed in 23.42s

ruff check tests/test_scout02_acceptance_negative_paths.py
All checks passed!
```

The first new-test collection attempt used an incorrect helper import and
failed before execution; correcting it to the repository's `tests.*` package
resolved collection. Intermediate new-file checks passed 10 cases and then
32 combined cases before adding the second-graph Run proof. The final result
above supersedes those partial runs.

Only coordinator tests/docs were added after the independent source/package
verification; production code and packaged schemas stayed unchanged. The full
suite was not rerun after adding this test-only file. The 33-case run includes
all new cases and the existing focused Scout tests. Do not sum overlapping
review slices or describe the restricted full-suite run as zero failures.

## Accepted behavior and limits

One approved Gig version pins an immutable Graph Set. Plans select one graph,
seal its inputs, and remain usable against their original version after a new
version is approved. Missing or invalid selection refuses; selection does not
authorize a Run. Existing single-graph records retain their bytes and identities.
The two structural graphs execute independently; this proves infrastructure,
not job research, tailoring, application tracking, or stock-market functionality.

V2 listing is supported; v2 portability presently returns an explicit
`unsupported_schema_version` inspection-only diagnostic, as A02 permits.
Full user-owned-Gig portability remains downstream scope. G44 invocation/handoff
content linkage and full actor-session binding remain the explicitly tracked
producer-side residuals; no stronger provenance claim is made here.

The wheel still reports version 0.1.6 from current package metadata. No provider
UAT, private operator Gig execution, release publication, commit, tag, or new
operator approval occurred. The next ready goal is SCOUT-03: private revisioned
records and resumable context.

## Worker settlement

Terra completion was received and acknowledged; release retained its user-owned
terminal without process action. Claude completion was acknowledged after its
report was read and its terminal released with transcript captured. Luna
completion was acknowledged after reading its report; release returned
`retained / external_terminal` because its custom `--approve-for-me` session
was attached via `worker-start --terminal`. No force-close or app restart was
attempted. All three tasks are settled, not waiting for a missing notification.
