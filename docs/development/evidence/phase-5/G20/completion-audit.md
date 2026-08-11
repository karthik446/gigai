# G20 Completion Audit

- Status: Complete; independently verified
- Goal: Local improve and evaluator learning
- Completion date: 2026-08-10
- Implementation branch: `goal/g20-local-improve`

## Acceptance-criteria audit

| # | Criterion | Evidence and result |
|---:|---|---|
| 1 | Accepted additive contract | Commit `563886e`; two new packaged resources bring the inventory from 23 to 25 while preserving prior resource bytes and hashes. |
| 2 | Exact learning observations | `learning-record.schema.json`, source-byte verification, active-pointer digest/version verification, and contract/runtime tests. |
| 3 | Provenance and duplicate handling | All four provenance values have valid fixtures; source identity and duplicate-observation refusal are tested. |
| 4 | Derived local storage boundary | Records use `home_root / "learning"`; symlinked roots and redirected paths fail closed. No config override or second version ledger exists. |
| 5 | Authoring recovery | Atomic temporary-write/rename/journal flow, failpoints, orphan reconciliation, and interruption tests are recorded in [recovery evidence](recovery-record.md). |
| 6 | Typed improvement manifest | `improvement-manifest.schema.json` restricts targets and carries explicit before/after artifact identities. Forbidden effect, provider, credential, budget, recovery, parallelism, and target-authority paths are rejected. |
| 7 | Evidence sufficiency gate | At least one `observed_outcome` or `evaluator_judgment` record is required; feedback-only evidence fails. |
| 8 | Improvement quality gate | Baseline/candidate replay checks all three fixed corpus splits, final held-out performance, and no regression. Reported values are recomputed before approval. |
| 9 | Load-bearing gates | The mutation harness kills both independent gate mutations: `2/2`. |
| 10 | Explicit G22 improve path | `start_interview(..., improve=True)` opens an improve request and approval emits `gig-proposal.kind == "improve"` through the existing lifecycle. |
| 11 | Stale-base refusal | Manifest staging and approval revalidate `base_gig_version` against the active pointer and fail closed on stale state. |
| 12 | Immutable history and idempotence | Prior learning records and Runs are not rewritten; approval advances version 1 to 2 once, and replay returns the existing proposal without another advance. |
| 13 | Effect boundary | G20 reads existing evidence only. It performs no provider, credential, network, shell, Git-target, or automatic-commit operation. |
| 14 | Packaged replay | Fresh-wheel schema verification and G20 installed lifecycle replay pass; see [installed replay](installed-replay.md). |
| 15 | Closeout records | This audit and the [terminal handoff](terminal-handoff.md) are committed with the implementation evidence. |

## Verification summary

The final repository verification reported:

```text
482 passed, 56 subtests passed in 202.41s
mutation_killed=2/2
verified 25 installed GigAI schemas
verified installed GigAI G20 improve lifecycle
```

The G20 implementation was delivered in commits `67603d3`, `8d6eb2c`,
`17bcd5d`, and `deea796`, following the accepted amendment in `563886e`.
No live provider or external evaluator was contacted. The quality replay is
deterministic contract plumbing and is not evidence of candidate-judge
accuracy.

## Limitations preserved

G20 does not automatically self-modify, schedule learning, execute providers,
mutate a target, roll back an accepted version, or propose recovery-policy or
bounded-parallelism changes. Those remain outside this Goal's authority.

