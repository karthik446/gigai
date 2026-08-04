# G10 — Phase 1 Completion Audit

- Status: Approved; blocked by G09
- Depends on: G09
- Unblocks: Phase 2 planning gate

## Outcome

Prove the complete Phase 1 offline local spine across macOS, Ubuntu, and the
Debian offline-container lane, reconcile every requirement to durable evidence,
and make an explicit go/no-go decision for Phase 2.

## In scope

- Rerun the complete source, installed-wheel, installed-CLI, contract,
  concurrency, crash, idempotency, and offline scenario suites.
- Prove supported behavior on macOS and Ubuntu plus the declared Debian
  offline container.
- Audit exact target deltas, private workpad locality, Git remotes, semantic
  handoffs, rebuildable index identity, and command effects.
- Reconcile G00–G11 completion audits and unresolved findings.
- Publish one Phase 1 completion audit and terminal handoff with explicit
  limitations and deferred work.

## Out of scope

- Fixing a failed upstream goal inside the audit change set.
- Live provider proof, deliberative model authoring, Run execution, or target
  mutation.
- Weakening acceptance criteria to obtain a passing audit.
- Declaring Phase 2 ready with missing or ambiguous evidence.

## Acceptance criteria

1. Python and non-Python targets remain visibly unchanged after initialization
   except for `.gigai/project.toml` and one idempotent `/.gigai/` exclude entry.
2. Offline read commands produce no target delta.
3. The complete Gig exists only on the configured workpad mount.
4. `gigai open` resolves the active private workpad and `--with-target` resolves
   both paths through structured argv.
5. Every creation transition has its durable text handoff and local commit.
6. Setup and `doctor` prove two-process exclusion and atomic replacement on the
   configured workpad filesystem.
7. Every workpad has no remote.
8. Deleting and rebuilding `state.sqlite` preserves the canonical
   `status --json` projection.
9. No Phase 1 scenario uses network access or tokens. G11's separately
   evidenced, local-only, opt-in live checks are redacted operator verification
   and are explicitly outside the audited scenario set.
10. The full matrix passes on macOS, Ubuntu, and the Debian offline container,
    or the audit records a blocking failure and stops.
11. Every Phase 1 plan requirement maps to a passing test, concrete artifact,
    explicit non-applicability rationale, or blocking finding.

## Verification and evidence

- Platform and Python-version matrix results with exact commands and versions.
- Installed package and CLI scenario reports.
- Cross-goal target/workpad manifests, journal graphs, remote inspection, and
  index rebuild digest comparison.
- Network-denial and credential-canary evidence.
- Consolidated requirement-to-evidence audit and terminal Phase 1 handoff.

## Stop boundary

Stop on any missing, contradictory, or platform-specific evidence and route the
failure back to the owning goal. Phase 2 begins only after an explicit approved
completion decision; G10 itself implements no Phase 2 behavior.
