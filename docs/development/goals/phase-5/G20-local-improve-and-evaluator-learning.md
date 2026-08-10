# G20 — Local Improve and Evaluator Learning

- Status: Proposed for review; not activated
- Depends on: G16 completion and terminal handoff, accepted S16-EVAL
  methodology and improvement-evaluation extension, G18 completion and
  terminal handoff, G19 completion and terminal handoff, and G22 completion
  audit and terminal handoff
- Unblocks: G21 recurring and comparative Gigs

## Outcome

G20 turns completed local evidence into an evidence-backed proposal for a new
Gig version. It records one normalized observation at a time, preserves exact
provenance into an existing Finding, Feedback, Adjudication, Report, or G19
target-effect artifact, evaluates a candidate improvement against the accepted
S16-EVAL methodology, and presents the resulting proposal for explicit
operator approval.

The first implementation may propose changes only to the review contract,
rubric, or verifier. Recovery-policy and bounded-parallelism proposals remain
deferred until a later contract establishes their evidence and authority
boundaries.

G20 never edits an approved Gig, Run, learning record, or prior evidence in
place. Approval creates a normal new `gig-proposal` with `kind: "improve"` and
advances the existing active-Gig-version authority exactly once. G20 is local,
on-demand, and proposal-producing; it is not automatic self-modification.

## Contract gate

Before runtime implementation, G20 must read and cite:

- [G16's completion audit](../../evidence/phase-3/G16/completion-audit.md) and
  [terminal handoff](../../evidence/phase-3/G16/terminal-handoff.md);
- [S16-EVAL's accepted methodology](../../evidence/phase-3/S16-EVAL/completion-audit.md),
  including an accepted improvement-evaluation extension that preserves its
  existing corpus split and normative bar;
- [G18's completion audit](../../evidence/phase-3/G18/completion-audit.md) and
  [terminal handoff](../../evidence/phase-3/G18/terminal-handoff.md);
- [G19's completion audit](../../evidence/phase-4/G19/completion-audit.md) and
  [terminal handoff](../../evidence/phase-4/G19/terminal-handoff.md); and
- [G22's completion audit](../../evidence/phase-2/G22/completion-audit.md) and
  [terminal handoff](../../evidence/phase-2/G22/terminal-handoff.md).

Before runtime code, an accepted additive amendment must define the
`learning-record.schema.json` and `improvement-manifest.schema.json` resources,
their ownership, the learning-root journal, the typed improvement diff, the
two proposal gates, the G22 improve approval path, and the recovery states.
The amendment must preserve all 23 existing schema resources and hashes. These
two resources are subordinate evidence/manifest resources; neither creates a
second proposal identity or active-version authority.

The existing `gig-proposal.schema.json` remains the sole proposal and version
authority. Its existing `kind: "improve"`, `base_gig_version`, and
`parent_proposal_id` fields are used rather than introducing a parallel
improvement proposal identity. The improvement manifest is referenced through
the proposal's existing `creation_manifest` artifact slot.

The accepted amendment must also record that `learning_root` is derived as
`home_root / "learning"`. It must not add a second configurable path or change
the current configuration schema. The learning-root journal owns learning
record publication only; the workpad journal and
`active-gig-version.json` remain the sole authority for proposal approval and
active-version advancement.

## First implementation boundary

The first implementation is intentionally narrow:

- one bound local Gig and one active base version;
- one on-demand local `improve` operation;
- completed evidence already present in the workpad or accepted G19 record;
- one immutable learning record per normalized observation;
- one typed improvement manifest containing an allowlisted change set;
- one candidate replay against the accepted S16-EVAL improvement corpus;
- one ordinary `gig-proposal` with `kind: "improve"`;
- one short-lived G22 loopback approval session; and
- one existing workpad journal/version advancement after explicit approval.

The implementation produces a proposal and, after approval, a new immutable
Gig version. It does not execute the improved Gig, call a provider, mutate a
target, install a capability, create a Git commit in the target, or schedule a
future Run.

## In scope

- Read completed Run, Goal, Review Loop, Finding, Feedback, Adjudication,
  Report, addressed-artifact, and G19 target-effect evidence from their
  authoritative locations.
- Capture one normalized observation per source artifact with exactly one
  provenance value: `observed_outcome`, `evaluator_judgment`,
  `operator_feedback`, or `accepted_outcome`.
- Preserve the source artifact's exact identity and capture the active Gig
  version value and pointer digest observed at record creation time.
- Publish learning records under the derived local `home_root / "learning"`
  root through an append-only learning journal and atomic artifact writes.
- Reject duplicate observations, incomplete records, path escapes, symlinks,
  malformed citations, changed source bytes, and orphaned partial artifacts.
- Compose multiple learning records into one improvement manifest without
  copying evidence into free-text summaries.
- Propose only typed changes to explicitly allowlisted review-contract, rubric,
  or verifier paths. The manifest carries before/after artifact identities and
  the exact change operations.
- Run two independent proposal gates:
  1. **Evidence sufficiency:** cited learning records are valid, complete,
     provenance-tagged, and include at least one
     `observed_outcome` or `evaluator_judgment` record supporting the target
     change.
  2. **Improvement quality:** baseline and candidate behavior are replayed
     against the accepted S16-EVAL development, calibration, and final
     held-out acceptance sets. The candidate must meet the fixed normative bar
     and must not regress the baseline on any normative metric.
- Mutation-test both gates so removing either gate permits a fixture that must
  fail to pass.
- Open the existing G22 local approval surface in an explicit `improve` mode.
  The resulting proposal must retain `kind: "improve"`, the base version, the
  parent proposal, the improvement manifest, and the learning citations.
- Revalidate the base active version and proposal identity at approval time.
  A stale base, changed source evidence, or changed improvement manifest fails
  closed.
- Advance the existing active-Gig-version pointer and journal exactly once
  through the existing lifecycle path. Repeated approval or replay must not
  create a second version.
- Recover only authoring-time interruptions: temporary-file cleanup, missing
  journal/artifact reconciliation, orphan discard, and idempotent retry of a
  complete learning-record publication.
- Produce sanitized learning-record fixtures, improvement manifests, baseline
  versus candidate quality reports, refusal records, mutation evidence,
  installed replay, a completion audit, and a terminal handoff for G21.

## Out of scope

- Automatic self-modification, automatic approval, automatic proposal
  revision, or automatic application of a candidate improvement.
- Any change to approved Gig or Run history, prior evidence, prior learning
  records, or an already-accepted active version.
- Changes to `allowed_effects`, redaction or network policy, credentials,
  provider selection, budgets, cycle caps, recovery policy, parallelism, or
  target-effect authority.
- Recovery or rollback of an already-accepted improvement version.
- Recurring or scheduled learning, daemon behavior, background workers, or
  comparative history; those belong to G21 or a later contract.
- Provider, model, tool, capability, credential, shell, arbitrary subprocess,
  network, or target execution by G20 itself.
- New target mutation authority, multi-file mutation, automatic Git commits,
  pushes, branches, merges, or history rewriting.
- A second proposal identity, version ledger, or active-version pointer.
- Syncing learning records outside the configured local machine or the
  derived learning root.

## State and authority contract

Learning-record publication has its own local authoring lifecycle:

```text
drafting -> written
    |          |
    v          v
 discarded   reconciled
```

`written` means the exact learning-record bytes and its learning-journal entry
both exist and validate. A crash before that point leaves no accepted record.
Reconciliation discards an orphaned temporary file, an artifact without its
journal entry, or a journal entry whose artifact is missing; it never silently
completes a partial record. A duplicate source observation is rejected rather
than merged by inference.

The improvement flow is separate from learning-record publication:

```text
learning_records_written
        |
        v
evidence_checked -> quality_checked -> proposal_ready
                                      |
                         +------------+------------+
                         v                         v
                    approved                   rejected
                         |
                         v
             new active Gig version
```

The states above are G20 process states, not a replacement for the existing
`gig-proposal` or `active-gig-version` schemas. The proposal remains pending
until the operator approves it through the G22 loopback flow. Only the
existing workpad journal may publish the approval and advance
`active-gig-version.json`.

The authority rules are non-negotiable:

1. A learning record is an immutable observation of one exact source artifact;
   it is not an evaluator verdict merely because its provenance is present.
2. `operator_feedback` and `accepted_outcome` alone cannot satisfy the
   evidence-sufficiency gate.
3. Evidence pointers identify exact existing artifacts; free-text explanation
   is descriptive and never authoritative.
4. The active version captured at observation time is evidence metadata. The
   base version revalidated at approval time controls whether the proposal may
   advance the Gig.
5. The improvement manifest is subordinate to `gig-proposal`; it cannot
   allocate proposal identity, approve itself, or advance a version.
6. A candidate that fails the fixed S16-EVAL bar or regresses the baseline
   cannot become approval-ready, regardless of operator feedback or model
   confidence.
7. G20 never turns a target-effect outcome into new target authority. It may
   cite G19's terminal evidence as an observed outcome only.

## Acceptance criteria

1. **Contract gate.** G20 cites the completed G16, S16-EVAL, G18, G19, and
   G22 evidence above, records an accepted additive amendment, and preserves
   all 23 existing schema resources and hashes. The amendment adds exactly
   `learning-record.schema.json` and `improvement-manifest.schema.json` as
   subordinate resources, raising the packaged inventory to 25.
2. **Learning-record contract.** A valid record contains one subject
   observation, one Gig identity, one active version value and pointer digest,
   exactly one source artifact identity, one provenance enum value, an
   observation timestamp, and no authoritative free-text evidence summary.
   Missing, malformed, changed, or cross-Gig citations fail closed.
3. **Source grain and duplicate policy.** Fixtures cover each provenance
   value and every accepted source artifact family. Re-citing the same source
   artifact for the same observation is deterministically rejected; distinct
   observations remain separately attributable.
4. **Learning-root boundary.** Records and the append-only learning journal
   are written only below `home_root / "learning"`. Path traversal, symlink,
   outside-root, unwritable-root, and malformed-root fixtures fail closed.
   Setup remains idempotent and no configuration schema change is introduced.
5. **Authoring recovery.** Crash fixtures cover before temporary write,
   after temporary write, after atomic rename, and before/after journal
   publication. Reconciliation discards incomplete/orphaned state and never
   promotes it to `written`.
6. **Improvement-manifest contract.** A manifest binds one base Gig/version,
   one parent proposal, one typed allowlisted change set, exact before/after
   artifact identities, and one or more learning-record IDs. It rejects every
   forbidden path and cannot allocate proposal or version authority.
7. **Evidence sufficiency gate.** A proposal with only operator-feedback or
   accepted-outcome records fails. A proposal with a valid supporting observed
   outcome or evaluator judgment passes this gate only when all citations and
   identities validate.
8. **Improvement-quality gate.** Baseline and candidate replay use the fixed
   S16-EVAL split and bar, including the final held-out acceptance set. A
   candidate that regresses any normative metric or fails the bar cannot reach
   `proposal_ready`. The quality report preserves baseline and candidate
   identities, corpus split, metrics, and evaluator versions.
9. **Gate mutation evidence.** Mutation tests prove that removing either the
   evidence-sufficiency gate or the improvement-quality gate allows the
   corresponding under-evidenced or regressing fixture to pass; the correct
   tests must fail against each mutant.
10. **G22 improve path.** The reused loopback session is explicitly marked as
    `improve`; the approved result produces a `gig-proposal` with
    `kind: "improve"`, the correct base version, parent proposal, and manifest
    citation. A create session cannot be relabeled as improve by inference.
11. **Approval and stale-base safety.** Approval revalidates the current
    active pointer, base version, proposal identity, learning citations, and
    manifest digest. A stale or changed base fails closed. A valid approval
    advances the existing active version exactly once and uses the existing
    `gig-vNNNNNN` journal/tag mechanism.
12. **Immutable history and replay.** Prior Gig versions, Runs, evidence, and
    learning records remain byte-identical. Replaying an approved improvement
    returns the existing version result without a second advancement; a
    different base, manifest, or evidence set is refused.
13. **Effect boundary.** G20 performs no provider, credential, capability,
    tool, shell, subprocess, network, target, Git-commit, push, branch, merge,
    schedule, or background effect. The local learning root is the only new
    write surface; the approved Gig version is published through the existing
    workpad journal.
14. **Installed replay.** A freshly built wheel, installed into a disposable
    environment, verifies 25 schemas and replays the learning-record,
    evidence-gate, quality-gate, stale-base, recovery, and approved-improvement
    fixtures without a source checkout or external network.
15. **Closeout evidence.** Evidence under
    `docs/development/evidence/phase-5/G20/` includes the accepted amendment,
    learning corpus, improvement manifests, baseline/candidate quality report,
    recovery records, mutation report, installed replay, completion audit, and
    terminal handoff. The handoff names G21 and explicitly preserves the G19
    target-effect boundary.

## Verification and evidence

The implementation must produce:

- `learning-record.schema.json` and `improvement-manifest.schema.json` golden
  vectors with preserved prior-resource hashes;
- a fixed G20 improvement corpus extending S16-EVAL without changing its
  normative bar or contamination rules;
- one valid and one invalid learning record for each provenance value;
- exact source-citation and active-version pointer fixtures;
- learning-root path, symlink, duplicate, atomic-write, and reconciliation
  fixtures;
- typed allowed/forbidden improvement-manifest fixtures;
- baseline/candidate quality reports over development, calibration, and final
  held-out acceptance sets;
- G22 improve-session and explicit approval black-box fixtures;
- stale-base, idempotent replay, immutable-history, and no-second-advance
  fixtures;
- mutation evidence for both proposal gates, source validation, stale-base
  validation, and version advancement; and
- a fresh-wheel installed replay with sanitized evidence.

Evidence lives under
`docs/development/evidence/phase-5/G20/`. Raw model outputs, credentials,
ambient paths, temporary learning roots, and workstation-specific logs do not
ship as evidence.

## Stop boundary

Stop before runtime implementation if the additive amendment cannot represent
one exact observation, its provenance, its source artifact, the active-version
snapshot, the typed improvement diff, both proposal gates, or authoring
recovery without a second authority.

Stop before proposal readiness if any citation is stale, the base version has
changed, the candidate has not passed the fixed S16-EVAL final held-out bar,
the candidate regresses the baseline, a forbidden field appears in the change
set, or an incomplete learning-root artifact cannot be reconciled
deterministically.

Stop before approval if the G22 improve flow does not produce the existing
`gig-proposal` shape, if the workpad journal cannot advance the active version
exactly once, or if a learning-root record can be mistaken for proposal or
version authority.

G20 stops at an operator-approved new Gig version. It does not execute that
version, revert it, schedule it, compare it across recurring Runs, or mutate a
target. Those behaviors require later contracts and evidence.
