# G21 Comparison Record

- Status: Accepted evidence
- Implementation commits: `48960cb`
- Corrective implementation commit: `e8e5269`
- Focused result: comparison tests pass, including explicit version mismatch
  refusal

Comparisons bind two occurrences to two distinct Runs of the same Gig, verify
their sealed Review Bundle snapshots, Goal Graphs, complete Review Contract
sets, and sealed `target-after.json` outputs, then classify the result as
`changed`, `unchanged`, `incomparable`, or `blocked`.

The comparison never selects a winner: `selected_winner` is always `null`.
Different Bundle identities are `incomparable`; missing sealed outputs are
`blocked`; different Run `gig_version` values are explicitly `incomparable`,
independent of Goal Graph digest comparison. The comparison path does not
retry, fall back, mutate a target, or advance the active Gig pointer.
