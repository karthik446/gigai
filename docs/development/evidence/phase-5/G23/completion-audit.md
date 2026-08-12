# G23 Completion Audit

- Status: Complete after post-closeout repair
- Goal: [G23 Gig Self-Containment and Portability](../../../../goals/phase-5/G23-gig-self-containment-and-portability.md)
- Accepted amendment: [contract amendment](gig-self-containment-and-portability-contract-amendment.md)
- Activation commit: `85c2baa`
- Final implementation commits: `67d9d9c`, `268edf4`, `a9ba756`, `f2fa8c0`, `f74e6bd`, `2ccb490`, `0345d73`, `1e563e0`, `e8d6328`

## Delivered

- Added optional `capability_manifest` to the existing active-version schema;
  the packaged inventory remains 27 resources and the prior schema bytes are
  unchanged except for this accepted amendment.
- Bound the reference during approval and preserved it through later approval
  paths, including G20 improvement approvals.
- Added read-only sealed-publication resolution from the unique
  `gig_accepted` child of `journal_commit`; no arbitrary `HEAD` is trusted.
- Added exact canonical pointer comparison, legacy `reported_non_portable`,
  path/digest/Gig identity checks, and named refusal outcomes.
- Added sealed-history proposal-lineage resolution with cycle, missing-parent,
  non-terminal-root, malformed-history, and cross-Gig refusal. Refusal codes
  now match the accepted amendment: `refused_cycle`,
  `refused_missing_parent`, `refused_cross_gig_lineage`, and
  `refused_lineage_authority`.
- Publication children now revalidate handoff parentage, sealed proposal and
  Gig identity, pointer identity, and tag resolution; forged children return
  `refused_ambiguous_publication`.
- Implicitly carried capability-manifest references are re-derived and compared
  against current manifest bytes before a later approval can seal.
- Reused G17's installer for out-of-band two-home source transport. No Run
  authority, provider execution, network access, credentials, or installed-byte
  transport was added.

## Verification

- Focused G23 suite: 25 passed; focused regression set: 48 passed.
- Broad suite: 526 passed and 60 subtests; four pre-existing G22 loopback tests
  were blocked by the sandbox's local socket restriction and then independently
  rerun with local-bind permission: 4 passed. Combined repository verification:
  530 passing tests.
- Mutation harness: 13/13 killed, including the exact publication-child
  cardinality, sealed-commit, and tag guards plus all other amendment-named
  semantic, lineage, and historical-schema guards.
- Fresh wheel: 27 installed schemas and the installed G23 pointer/lineage/
  two-home replay passed in a disposable environment.
- `git diff --check`: clean before this closeout commit.

The four broad-suite socket failures were environmental (`PermissionError` on
loopback bind in G22 HTTP tests), not failures in G23 or its dependencies.
The elevated rerun passed all four.

## Boundary decision

G23 does not declare alpha or public release. G24 remains the separate
release-lane goal responsible for alpha readiness and final repository cleanup.
