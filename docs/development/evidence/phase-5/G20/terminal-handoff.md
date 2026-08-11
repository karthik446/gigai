# G20 Terminal Handoff

- Status: Complete; handoff accepted
- Next consumer: G21 recurring and comparative Gigs
- Recorded: 2026-08-10

G20 is complete. The repository now has a bounded local learning path that
turns exact, provenance-tagged observations into typed improvement manifests,
checks evidence sufficiency and held-out quality, and routes an explicitly
opened improve request through the existing G22 approval and active-version
lifecycle.

## Handoff invariants

- `gig-proposal` remains the only proposal/version authority.
- `active-gig-version.json` and the workpad journal remain the only active
  version authority.
- Learning records are append-only under derived `home_root / "learning"` and
  never sync outside the local root.
- G20 never treats operator feedback or model confidence alone as sufficient
  evidence for an improvement.
- G20 never expands target-effect authority, provider access, credentials,
  budgets, recovery policy, or parallelism.
- Approval is explicit, stale-base checked, and idempotent.

## G21 entry condition

G21 may begin its own goal-definition and contract work. It must not infer
recurrence authority merely because G20 can produce a new active Gig version.
Before runtime scheduling or comparative history work, G21 needs its own
reviewed contract for occurrences, reference snapshots, prior-output identity,
missed-run behavior, and operator-visible recovery.

## Evidence index

- [G20 goal contract](../../goals/phase-5/G20-local-improve-and-evaluator-learning.md)
- [Accepted contract amendment](learning-contract-amendment.md)
- [Corpus evidence](learning-corpus.md)
- [Recovery evidence](recovery-record.md)
- [Mutation report](mutation-report.md)
- [Installed replay](installed-replay.md)
- [Completion audit](completion-audit.md)

