# G21 Completion Audit

- Status: Complete
- Goal: [G21 recurring and comparative Gigs](../../../../goals/phase-5/G21-recurring-and-comparative-gigs.md)
- Accepted amendment: [occurrence/comparison contract amendment](occurrence-comparison-contract-amendment.md)
- Original implementation range: `fade42f..87840da`
- Corrective review commits: `702ff1c`, `e8e5269`

## Acceptance evidence

1. The accepted two-resource amendment defines `gig-occurrence` and
   `gig-comparison` without changing prior schema meaning or authority.
2. The packaged schema inventory is additive: 25 resources became 27, with
   prior hashes preserved and golden fixtures updated for both new resources.
3. Manual daily, weekly, and monthly occurrence commands are implemented with
   exact snapshot binding, one-Run linkage, explicit terminal outcomes, and
   no scheduler or automatic retry.
4. Comparisons consume sealed Run outputs and complete contract sets; they
   preserve disagreement by leaving `selected_winner` null and fail closed on
   incomparable or missing evidence.
5. Interruption, recovery, terminal replay, missed-state, unavailable, and
   cancellation fixtures are covered in the focused lifecycle suite. Refusal
   terminals preserve explicit outcome actors and outcomes, while prepared
   Runs cannot be manually terminalized before reconciliation.
6. The implementation boundary has no provider, network, credential,
   scheduler, daemon, subprocess, or target-effect authority.
7. Mutation evidence killed all nine named semantic mutations, including the
   new schema, in-flight terminalization, and version-mismatch guards.
8. The fresh-wheel installed replay passed for all three cadence fixtures, and
   the G20 replay remained green from the same wheel.

## Verification record

- Corrective focused G21 suite: `22 passed in 35.47s`.
- Affected schema/contract suite: `50 passed, 53 subtests passed in 52.27s`.
- Full suite after corrective fixes: `504 passed, 60 subtests passed in 244.00s`.
- Mutation harness: `mutation_killed=9/9`.
- `git diff --check`: passed before closeout.

The earlier full-suite run exposed stale pre-G21 schema-count expectations;
those were corrected in `87840da`. A subsequent review then identified five
G21 enforcement gaps: refusal actor attribution, in-flight terminalization,
schema conditionals, refusal outcomes, and explicit version mismatch handling.
Those were corrected in `702ff1c` and `e8e5269`; the clean full-suite and
fresh-wheel results above were rerun afterward.

## Scope conclusion

G21 is complete for manually triggered recurring and comparative Gigs. It does
not ship a scheduler, recurring background authority, provider execution, or
target mutation. G23 remains independent, and G24 remains the later alpha
readiness/release-lane goal.
